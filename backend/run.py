#!/usr/bin/env python3
"""MiAir Next 入口点 — 自动检测并安装缺失依赖后启动 (uvicorn)"""

import os
import subprocess
import sys

REQUIRED_PACKAGES = {
    # import 名 -> pip 包名
    "fastapi": "fastapi>=0.110.0",
    "uvicorn": "uvicorn>=0.27.0",
    "aiohttp": "aiohttp>=3.9.0",
    "miservice": "miservice-fork",
    "zeroconf": "zeroconf>=0.38.0",
    "Crypto": "pycryptodome>=3.15.0",
    "av": "av>=10.0.0",
    "jose": "python-jose[cryptography]>=3.3.0",
    "bcrypt": "bcrypt>=4.0.0",
    # uvicorn 处理 WebSocket 升级所需的协议实现 (缺失时 WS 握手会返回 404)
    "websockets": "websockets>=12.0",
}


def ensure_dependencies():
    """检测缺失的依赖并一次性安装"""
    missing = []
    for import_name, pip_name in REQUIRED_PACKAGES.items():
        try:
            __import__(import_name)
        except ImportError:
            missing.append(pip_name)
    if missing:
        print(f"[MiAir Next] 正在安装缺失依赖: {', '.join(missing)}")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", *missing],
        )
        print("[MiAir Next] 依赖安装完成")


def main():
    ensure_dependencies()

    # 确保工作目录为 backend 目录, 使 app 包可导入
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.getcwd())

    import uvicorn

    host = os.environ.get("MIAIR_WEB_HOST", "0.0.0.0")
    port = int(os.environ.get("MIAIR_WEB_PORT", "8300"))
    uvicorn.run("app.main:app", host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
