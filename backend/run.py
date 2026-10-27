#!/usr/bin/env python3
"""MiAir Next 入口点 — 启动 uvicorn

依赖由安装阶段解决 (镜像构建时按 requirements.lock 安装, 本地开发用
`uv sync` 或 `pip install -e .[dev]`), 不在运行时动态安装。

开发模式 (代码改动自动热重载): python run.py --reload 或 MIAIR_RELOAD=1
"""

import os
import sys


def main():
    # 确保工作目录为 backend 目录, 使 app 包可导入
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.getcwd())

    import uvicorn

    host = os.environ.get("MIAIR_WEB_HOST", "0.0.0.0")
    port = int(os.environ.get("MIAIR_WEB_PORT", "8300"))
    # 开发模式: --reload 参数或 MIAIR_RELOAD=1 开启热重载;
    # reload_dirs 限定 app 目录, 避免监听 .venv / 数据目录引发频繁重启。
    # 注意: 热重启会重建 AirPlay/SSDP 等子服务, 仅限本地开发使用, 生产不开启。
    if "--reload" in sys.argv[1:] or os.environ.get("MIAIR_RELOAD") == "1":
        uvicorn.run(
            "app.main:app",
            host=host,
            port=port,
            log_level="info",
            reload=True,
            reload_dirs=["app"],
        )
    else:
        uvicorn.run("app.main:app", host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
