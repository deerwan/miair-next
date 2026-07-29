"""登录 / 账号初始化 / 修改密码"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    hash_password,
    login_limiter,
    verify_password,
)
from app.db import store
from app.engine.notify import notify_async
from app.models.schemas import (
    LoginRequest,
    LoginStatusResponse,
    PasswordChangeRequest,
    SetupRequest,
    TokenResponse,
)

router = APIRouter()


def _client_ip(request: Request) -> str:
    """客户端 IP: 仅当 MIAIR_TRUST_PROXY 开启时才信任反代头

    直连部署下无条件信任 X-Forwarded-For 会被伪造头绕过登录限速,
    并污染登录日志中的 IP 记录。
    """
    if get_settings().trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        real_ip = request.headers.get("x-real-ip", "")
        if real_ip:
            return real_ip.strip()
    if request.client:
        return request.client.host
    return "unknown"


def _engine_config(request: Request):
    """引擎配置 (用于通知推送); 测试环境未启动 Orchestrator 时返回 None"""
    orch = getattr(request.app.state, "orchestrator", None)
    return orch.config if orch else None


@router.get("/login/status", response_model=LoginStatusResponse)
async def login_status():
    """是否已初始化管理员 (用于前端首次引导)"""
    return LoginStatusResponse(initialized=store.is_initialized())


@router.post("/login/setup", response_model=TokenResponse)
async def setup_admin(payload: SetupRequest):
    """首次设置管理员账号 (仅未初始化时可用)"""
    if store.is_initialized():
        raise HTTPException(status_code=400, detail="管理员已初始化")
    if not store.create_user(payload.username, hash_password(payload.password)):
        raise HTTPException(status_code=400, detail="创建管理员失败")
    return TokenResponse(access_token=create_access_token(payload.username))


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, request: Request):
    """登录, 带失败限速 (防暴力破解); 登录成功/触发锁定时推送提醒"""
    ip = _client_ip(request)
    config = _engine_config(request)
    locked = login_limiter.is_locked(ip)
    if locked:
        raise HTTPException(
            status_code=429, detail=f"失败次数过多, 请 {locked} 秒后重试"
        )

    hashed = store.get_password_hash(payload.username)
    if not hashed or not verify_password(payload.password, hashed):
        login_limiter.record_failure(ip)
        store.add_login_log(payload.username, ip, success=False)
        # 刚达到锁定阈值时告警 (而非每次失败都推, 避免爆破刷屏)
        if login_limiter.is_locked(ip):
            notify_async(
                config,
                "panel_login_failed",
                "[MiAir Next] 面板登录失败告警",
                f"IP {ip} 多次登录失败已被限速。\n"
                f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                "若非本人操作, 请检查面板是否暴露于公网并修改密码。",
            )
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    login_limiter.reset(ip)
    store.add_login_log(payload.username, ip, success=True)
    # 登录成功提醒 (短节流 60s, 避免 token 过期后频繁重登刷屏)
    notify_async(
        config,
        "panel_login",
        "[MiAir Next] 面板登录成功",
        f"账号 {payload.username} 于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 登录, IP: {ip}",
        throttle=60,
    )
    return TokenResponse(access_token=create_access_token(payload.username))


@router.post("/login/password")
async def change_password(
    payload: PasswordChangeRequest,
    username: str = Depends(get_current_user),
):
    """修改密码 (需登录)"""
    hashed = store.get_password_hash(username)
    if not hashed or not verify_password(payload.old_password, hashed):
        raise HTTPException(status_code=400, detail="原密码错误")
    store.update_password(username, hash_password(payload.new_password))
    return {"ok": True}


@router.get("/login/logs")
async def login_logs(
    limit: int = 50,
    _: str = Depends(get_current_user),
):
    """最近的面板登录日志 (需登录)"""
    return {"logs": store.get_login_logs(min(limit, 100))}


@router.get("/me")
async def me(username: str = Depends(get_current_user)):
    """当前用户信息 (前端用于校验 token 有效性)"""
    return {"username": username}
