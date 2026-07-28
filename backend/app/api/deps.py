"""API 依赖注入"""

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import decode_access_token
from app.services.orchestrator import Orchestrator

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> str:
    """校验 Bearer token, 返回当前用户名"""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录"
        )
    username = decode_access_token(credentials.credentials)
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已过期"
        )
    return username


def get_orchestrator(request: Request) -> Orchestrator:
    return request.app.state.orchestrator


def get_engine_config(request: Request):
    return request.app.state.orchestrator.config
