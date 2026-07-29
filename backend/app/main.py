"""FastAPI 主入口

- /api/v1/*         管理 API (登录接口公开, 业务接口需 JWT)
- /api/v1/health    健康检查 (Docker healthcheck, 无需鉴权)
- /                 前端静态资源 (SPA, 构建产物位于 app/static)
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.v1 import protected_router, public_router, ws_router
from app.core.config import get_settings
from app.core.logging import ring_handler, setup_logging
from app.db import store
from app.engine.config import Config
from app.services.orchestrator import Orchestrator

log = logging.getLogger("miair")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    # 引擎业务配置
    engine_config = Config.load(settings.conf_path)
    setup_logging(engine_config.verbose, engine_config.log_file)
    ring_handler.set_loop(asyncio.get_running_loop())

    # 用户数据库
    store.init_db()

    # 启动编排器 (DLNA / AirPlay / 小米云)
    orchestrator = Orchestrator(engine_config)
    app.state.orchestrator = orchestrator
    await orchestrator.start()

    web_port = int(os.environ.get("MIAIR_WEB_PORT", "8300"))
    log.info(f"Web 管理界面: http://{engine_config.hostname}:{web_port}")

    yield

    await orchestrator.stop()


def create_app() -> FastAPI:
    app = FastAPI(
        title="MiAir Next",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    # 健康检查 (无需鉴权, 供 Docker healthcheck 使用)
    @app.get("/api/v1/health")
    async def health():
        return {"status": "ok", "version": __version__}

    app.include_router(public_router, prefix="/api/v1")
    app.include_router(protected_router, prefix="/api/v1")
    app.include_router(ws_router, prefix="/api/v1")

    # 前端静态资源 (SPA fallback 到 index.html)
    if os.path.isdir(STATIC_DIR):
        assets_dir = os.path.join(STATIC_DIR, "assets")
        if os.path.isdir(assets_dir):
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/{full_path:path}")
        async def spa(full_path: str):
            candidate = os.path.normpath(os.path.join(STATIC_DIR, full_path))
            # 防目录穿越: 用 commonpath 严格判断 candidate 确实在静态目录内,
            # 避免同前缀兄弟目录 (如 static-backup) 被误放行
            try:
                in_static = os.path.commonpath([STATIC_DIR, candidate]) == STATIC_DIR
            except ValueError:
                in_static = False
            if in_static and os.path.isfile(candidate):
                return FileResponse(candidate)
            index = os.path.join(STATIC_DIR, "index.html")
            if os.path.isfile(index):
                return FileResponse(index)
            return PlainTextResponse("MiAir Next: 前端未构建", status_code=200)

    return app


app = create_app()
