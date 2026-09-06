"""账号登录

支持三种登录方式:
- 扫码登录: /account/qrcode + /account/qrcode/poll (确认后立即换取 serviceToken)
- 密码登录: /account/login, 触发图形验证码/短信邮箱验证时分别以
  /account/login/captcha、/account/login/verify 续步完成 (交互式会话)
- 手动 Token: /account/token (passToken + userId 立即验证并换取 serviceToken)

安全边界: passToken/serviceToken 全程留在后端, 不返回前端。登录成功后直接写入
config.cookie + .mi.token 并热重启 DLNA/AirPlay 服务使登录生效。
"""

import json
import logging
import os
import time

from fastapi import APIRouter, BackgroundTasks, Depends

from app.api.deps import get_engine_config, get_orchestrator
from app.engine.auth import parse_cookie_string
from app.engine.mi_account import new_device_id, normalize_device_id
from app.engine.mina_auth import MINA_SID, MinaAuth, PasswordLoginService
from app.engine.qr_login import QRLoginManager
from app.models.schemas import (
    LoginCaptchaRequest,
    LoginVerifyRequest,
    ManualTokenRequest,
    PasswordLoginRequest,
)
from app.services.orchestrator import Orchestrator

log = logging.getLogger("miair")

router = APIRouter()

# 扫码会话管理器 (单进程内存态)
_qr_manager = QRLoginManager()
# 密码登录交互会话 (验证码/短信验证多步续接, 单进程内存态)
_login_manager = PasswordLoginService()


def _persist_login(
    background: BackgroundTasks,
    orch: Orchestrator,
    config,
    *,
    user_id: str,
    pass_token: str,
    account: str | None = None,
    password: str | None = None,
    service_token: str = "",
    ssecurity: str = "",
    device_id: str = "",
):
    """登录成功后的统一落盘: config.cookie + .mi.token + 热重启服务

    .mi.token 携带 micoapi 缓存 (serviceToken/ssecurity) 时, 服务重启后可直接
    复用缓存, 不必再触发 serviceLogin 换发 (换发过于频繁会触发小米风控 70016)。
    account/password 非 None 时一并写入, 作为三级凭证降级链的兜底凭证
    """
    config.cookie = f"userId={user_id}; passToken={pass_token}"
    if account is not None:
        config.account = account
    if password is not None:
        config.password = password

    token_home = config.mi_token_home
    os.makedirs(os.path.dirname(token_home), exist_ok=True)
    existing = {}
    try:
        with open(token_home) as f:
            existing = json.load(f)
    except Exception:
        pass
    # deviceId: 新登录会话的真实标识 > 既有合法值 > 随机新生成 (保证跨重启稳定)
    resolved_device_id = (
        normalize_device_id(device_id)
        or normalize_device_id(existing.get("deviceId"))
        or new_device_id()
    )
    token = {
        "userId": str(user_id),
        "passToken": pass_token,
        "deviceId": resolved_device_id,
    }
    if service_token and ssecurity:
        token["micoapi"] = [ssecurity, service_token]
    with open(token_home, "w") as f:
        json.dump(token, f, indent=2)
    try:
        os.chmod(token_home, 0o600)
    except OSError:
        pass

    config.save()
    log.info("登录凭证已保存, 正在热重启服务...")
    background.add_task(orch.restart_dlna_services)


def _login_response(
    result: dict,
    session_id: str,
    background: BackgroundTasks,
    orch: Orchestrator,
    config,
):
    """把 PasswordLoginService 的结果转为 API 响应; 成功时落盘并热重启"""
    state = result.get("state")

    if state == "success":
        # 与扫码路径同款防御: 凭证不完整时拒绝落盘, 避免空 passToken 引发
        # 「登录失败→缓存复用→恢复重启」循环 (2026-09-06 事故)
        if not (result.get("user_id") and result.get("pass_token")):
            log.error("密码登录成功但凭证不完整 (userId/passToken 缺失), 跳过落盘")
            return {"success": False, "state": "failed", "error": "登录凭证异常, 请重新登录"}
        _persist_login(
            background,
            orch,
            config,
            user_id=result.get("user_id", ""),
            pass_token=result.get("pass_token", ""),
            # 密码登录成功后把账密一并存为三级链的兜底凭证
            account=result.get("username") or None,
            password=result.get("password") or None,
            service_token=result.get("service_token", ""),
            ssecurity=result.get("ssecurity", ""),
            device_id=result.get("device_id", ""),
        )
        return {"success": True, "state": "success", "message": "登录成功, 服务正在重启"}

    if state == "need_captcha":
        return {
            "success": True,
            "state": "need_captcha",
            "session_id": session_id,
            "captcha_image": result.get("captcha_image", ""),
        }

    if state == "need_verify":
        return {
            "success": True,
            "state": "need_verify",
            "session_id": session_id,
            "verify_type": result.get("verify_type", "phone"),
        }

    return {"success": False, "state": "failed", "error": result.get("error") or "登录失败"}


# ===== 扫码登录 =====


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
    """轮询扫码状态; 成功后写入 cookie 并热重启服务 (token 不返回前端)

    扫码确认后已立即用 passToken 换取 micoapi serviceToken (qr_login 内部完成),
    落盘时一并写入 .mi.token 缓存。
    """
    result = await _qr_manager.poll(session_id)

    if result["state"] == "confirmed":
        cookie = result.pop("cookie", "")
        token_info = result.pop("token_info", None) or {}
        if cookie:
            # 必须用按 ";" 切分的健壮解析: parse_qs(cookie.replace(";", "&"))
            # 会把分号后的键名解析成 " passToken" (带前导空格), passToken 丢失成
            # 空串, 落盘后登录永远失败并触发「恢复→重启」风暴 (2026-09-06 事故)。
            creds = parse_cookie_string(cookie)
            user_id = creds.get("userId", "")
            pass_token = creds.get("passToken", "")
            if not (user_id and pass_token):
                log.error(
                    f"扫码回调凭证解析异常 (userId={'有' if user_id else '缺'}, "
                    f"passToken={'有' if pass_token else '缺'}), 跳过落盘"
                )
                return {
                    "success": False,
                    "state": "confirmed",
                    "message": "登录凭证异常, 请重新扫码",
                }
            services = token_info.get("services") or {}
            micoapi = services.get(MINA_SID, {})
            _persist_login(
                background,
                orch,
                config,
                user_id=user_id,
                pass_token=pass_token,
                service_token=micoapi.get("service_token", ""),
                ssecurity=micoapi.get("ssecurity", ""),
                device_id=token_info.get("device_id", ""),
            )
        return {
            "success": True,
            "state": "confirmed",
            "message": "登录成功, 服务正在重启",
            "user_id": result.get("user_id", ""),
        }

    return {"success": True, "state": result["state"], "message": result["message"]}


# ===== 密码登录 (支持图形验证码 / 短信邮箱验证续步) =====


@router.post("/account/login")
async def password_login(
    payload: PasswordLoginRequest,
    background: BackgroundTasks,
    orch: Orchestrator = Depends(get_orchestrator),
    config=Depends(get_engine_config),
):
    """密码登录: 成功 / 需要图形验证码 / 需要短信邮箱验证 / 失败"""
    _login_manager.cleanup_expired()
    result, session_id = await _login_manager.login(payload.username, payload.password)
    return _login_response(result, session_id, background, orch, config)


@router.post("/account/login/captcha")
async def submit_login_captcha(
    payload: LoginCaptchaRequest,
    background: BackgroundTasks,
    orch: Orchestrator = Depends(get_orchestrator),
    config=Depends(get_engine_config),
):
    """提交图形验证码, 继续密码登录流程"""
    result, session_id = await _login_manager.submit_captcha(
        payload.session_id, payload.captcha
    )
    return _login_response(result, session_id, background, orch, config)


@router.post("/account/login/verify")
async def submit_login_verify(
    payload: LoginVerifyRequest,
    background: BackgroundTasks,
    orch: Orchestrator = Depends(get_orchestrator),
    config=Depends(get_engine_config),
):
    """提交短信/邮箱验证码, 完成密码登录流程"""
    result, session_id = await _login_manager.submit_verify_code(
        payload.session_id, payload.code
    )
    return _login_response(result, session_id, background, orch, config)


# ===== 手动 Token (passToken + userId 立即验证) =====


@router.post("/account/token")
async def set_manual_token(
    payload: ManualTokenRequest,
    background: BackgroundTasks,
    orch: Orchestrator = Depends(get_orchestrator),
    config=Depends(get_engine_config),
):
    """手动设置 Token: 立即用 passToken 换取 serviceToken 验证有效性"""
    auth = MinaAuth()
    try:
        result = await auth.refresh_by_pass_token(
            payload.pass_token, payload.user_id, MINA_SID
        )
    finally:
        await auth.close()

    if result.get("state") != "success":
        return {
            "success": False,
            "error": f"passToken 换取 serviceToken 失败: {result.get('error') or '未知错误'}",
        }

    token_info = result.get("token_info") or {}
    micoapi = (token_info.get("services") or {}).get(MINA_SID, {})
    _persist_login(
        background,
        orch,
        config,
        user_id=payload.user_id,
        pass_token=payload.pass_token,
        service_token=micoapi.get("service_token", ""),
        ssecurity=micoapi.get("ssecurity", ""),
        device_id=token_info.get("device_id", ""),
    )
    return {"success": True, "message": "令牌验证成功, 服务正在重启"}


# ===== 账号状态管理 =====


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
            user_id = parse_cookie_string(config.cookie).get("userId", "")
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
