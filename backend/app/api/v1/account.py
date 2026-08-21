"""账号扫码登录 (移植自 songloft-plugin-miot 的 auth handler)

安全边界: passToken/cookie 全程留在后端, 不返回前端。扫码成功后直接写入
config.cookie 并热重启 DLNA/AirPlay 服务使登录生效。
"""

import json
import logging
import os
from urllib.parse import parse_qs

from fastapi import APIRouter, BackgroundTasks, Depends

from app.api.deps import get_engine_config, get_orchestrator
from app.engine.qr_login import QRLoginManager
from app.services.orchestrator import Orchestrator

log = logging.getLogger("miair")

router = APIRouter()

# 扫码会话管理器 (单进程内存态)
_qr_manager = QRLoginManager()


@router.post("/account/qrcode")
async def start_qrcode():
    """启动扫码登录, 返回二维码与会话 ID"""
    result = await _qr_manager.start()
    if not result:
        return {"success": False, "error": "获取二维码失败, 请稍后重试"}
    session_id, info = result
    return {
        "success": True,
        "session_id": session_id,
        "qrcode_url": info["qrcode_url"],
        "login_url": info["login_url"],
    }


@router.get("/account/qrcode/poll")
async def poll_qrcode(
    session_id: str,
    background: BackgroundTasks,
    orch: Orchestrator = Depends(get_orchestrator),
    config=Depends(get_engine_config),
):
    """轮询扫码状态; 成功后写入 cookie 并热重启服务 (token 不返回前端)"""
    result = await _qr_manager.poll(session_id)

    if result["state"] == "confirmed":
        cookie = result.pop("cookie", "")
        if cookie:
            config.cookie = cookie
            # 扫码登录后清空账号密码, 避免与 cookie 冲突
            config.account = ""
            config.password = ""
            # 同步把 userId/passToken 写入 miservice 的 token 文件 (.mi.token),
            # 否则 MiAccount 在 login() 时读不到 passToken, 会触发 code 70016 登录失败
            try:
                parsed = parse_qs(cookie.replace(";", "&"))
                user_id = (parsed.get("userId") or [""])[0]
                pass_token = (parsed.get("passToken") or [""])[0]
                if user_id and pass_token:
                    token_home = config.mi_token_home
                    os.makedirs(os.path.dirname(token_home), exist_ok=True)
                    with open(token_home, "w") as f:
                        json.dump(
                            {
                                "userId": user_id,
                                "passToken": pass_token,
                                "deviceId": "miair_device",
                            },
                            f,
                            indent=2,
                        )
            except Exception as e:
                log.warning(f"写入 .mi.token 失败: {e}")
            config.save()
            log.info("扫码登录成功, 已保存 cookie, 正在热重启服务...")
            background.add_task(orch.restart_dlna_services)
        return {
            "success": True,
            "state": "confirmed",
            "message": "登录成功, 服务正在重启",
            "user_id": result.get("user_id", ""),
        }

    return {"success": True, "state": result["state"], "message": result["message"]}
