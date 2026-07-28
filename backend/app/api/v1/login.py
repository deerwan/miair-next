"""登录 / 账号初始化 / 修改密码"""

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import get_current_user
from app.core.security import (
    create_access_token,
    hash_password,
    login_limiter,
    verify_password,
)
from app.db import store
from app.models.schemas import (
    LoginRequest,
    LoginStatusResponse,
    PasswordChangeRequest,
    SetupRequest,
    TokenResponse,
)

router = APIRouter()


def _client_ip(request: Request) -> str:
    if request.client:
        return request.client.host
    return "unknown"


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
    """登录, 带失败限速 (防暴力破解)"""
    ip = _client_ip(request)
    locked = login_limiter.is_locked(ip)
    if locked:
        raise HTTPException(
            status_code=429, detail=f"失败次数过多, 请 {locked} 秒后重试"
        )

    hashed = store.get_password_hash(payload.username)
    if not hashed or not verify_password(payload.password, hashed):
        login_limiter.record_failure(ip)
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    login_limiter.reset(ip)
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


@router.get("/me")
async def me(username: str = Depends(get_current_user)):
    """当前用户信息 (前端用于校验 token 有效性)"""
    return {"username": username}
