"""账号扫码登录 (auth handler)

安全边界: passToken/cookie 全程留在后端, 不返回前端。扫码成功后直接写入
config.cookie 并热重启 DLNA/AirPlay 服务使登录生效。
"""

import json
import logging
import os
import time
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
            # 注意: 这里不再清空 config.account / config.password。
            # 账号密码是三级凭证降级链的兜底凭证 : passToken 
            # 约 24h 硬有效期且换发不会延长它, 一旦过期只有账密重登能重新签发。
            # 保留账密后, 扫码登录的账号也能在 passToken 失效时自动恢复。
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


@router.get("/account/status")
async def account_status(
    orch: Orchestrator = Depends(get_orchestrator),
    config=Depends(get_engine_config),
):
    """当前小米账号登录状态 (供前端状态卡展示)

    状态等级: offline(未登录) / expired(已过期) / expiring(即将过期<3h)
             / healthy(正常)。serviceToken 由后端定时续期, 剩余有效期可
    直观提示用户何时可能掉线, 解决「过期静默失败」不可见的问题。
    """
    auth = orch.auth
    user_id = ""
    if config.cookie:
        try:
            parsed = parse_qs(config.cookie.replace(";", "&"))
            user_id = (parsed.get("userId") or [""])[0]
        except Exception:
            pass

    remaining = None
    if config.token_expires_at > 0:
        remaining = config.token_expires_at - time.time()

    logged_in = auth.is_logged_in()
    if not logged_in:
        status = "offline"
    elif remaining is None:
        # 已登录但无过期时间戳 (如纯 cookie 登录未记录): 视为正常
        status = "healthy"
    elif remaining < 0:
        status = "expired"
    elif remaining < 3 * 3600:
        status = "expiring"
    else:
        status = "healthy"

    return {
        "user_id": user_id,
        "logged_in": logged_in,
        "status": status,
        "service_token_remaining_hours": round(remaining / 3600, 1) if remaining is not None else None,
        "has_password_fallback": bool(config.account and config.password),
        "token_refresh_running": bool(auth._refresh_task and not auth._refresh_task.done()),
        "has_account": bool(config.account or config.cookie),
    }


@router.delete("/account")
async def delete_account(
    background: BackgroundTasks,
    orch: Orchestrator = Depends(get_orchestrator),
    config=Depends(get_engine_config),
):
    """删除账号: 清空所有登录凭证并热重启服务, 回到未配置状态

    用途: 换绑小米账号、或清除失效凭证重新扫码。清空后 DLNA/AirPlay
    会停止 (无凭证无法投送), 前端可重新扫码或用账号密码登录。
    """
    config.cookie = ""
    config.account = ""
    config.password = ""
    config.token_expires_at = 0.0
    config.save()
    try:
        os.remove(config.mi_token_home)
    except FileNotFoundError:
        pass
    log.info("账号凭证已清空, 正在热重启服务 ...")
    background.add_task(orch.restart_dlna_services)
    return {"ok": True, "message": "账号已删除, 服务已重置"}
