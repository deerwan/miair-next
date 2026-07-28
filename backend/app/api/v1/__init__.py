"""v1 路由汇总"""

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.api.v1 import account, login, settings, speakers, system, ws

# 无需鉴权: 登录相关
public_router = APIRouter()
public_router.include_router(login.router, tags=["auth"])

# 需要鉴权: 业务接口统一挂 get_current_user 依赖
protected_router = APIRouter(dependencies=[Depends(get_current_user)])
protected_router.include_router(settings.router, tags=["settings"])
protected_router.include_router(account.router, tags=["account"])
protected_router.include_router(speakers.router, tags=["speakers"])
protected_router.include_router(system.router, tags=["system"])

# WebSocket 单独注册 (自行校验 token)
ws_router = ws.router
