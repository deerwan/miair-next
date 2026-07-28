"""进程重启工具

MiAir 原实现位于 web 层 (miair/web/api.py), 协议层反向依赖它。
新架构中独立为引擎层工具模块, 供 auth / speaker / orchestrator 调用。
"""

import logging
import os
import sys

log = logging.getLogger("miair")


def _is_docker() -> bool:
    """检测是否在 Docker 容器中运行"""
    if os.environ.get("MIAIR_DOCKER"):
        return True
    if os.path.exists("/.dockerenv"):
        return True
    try:
        with open("/proc/1/cgroup", "r") as f:
            content = f.read()
            return any(k in content for k in ("docker", "containerd", "kubepods"))
    except Exception:
        pass
    try:
        with open("/proc/self/mountinfo", "r") as f:
            content = f.read()
            return "docker" in content or "/docker/" in content
    except Exception:
        pass
    return False


def _restart_process():
    """重启当前 Python 进程"""
    log.info(f"重启进程: {sys.executable} {sys.argv}")

    if _is_docker():
        # Docker 环境下直接退出, 由 restart=unless-stopped 策略自动重启容器
        log.info("在 Docker 环境中, 退出进程, Docker 会自动重启容器")
        os._exit(0)
    elif sys.platform == "win32":
        import subprocess
        subprocess.Popen([sys.executable] + sys.argv)
        os._exit(0)
    else:
        os.execv(sys.executable, [sys.executable] + sys.argv)
