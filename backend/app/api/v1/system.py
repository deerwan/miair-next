"""系统状态 / 健康检查 / 服务重启 / 日志"""

import asyncio
import logging
import resource
import sys
import time

import aiohttp
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from app import __version__
from app.api.deps import get_engine_config, get_orchestrator
from app.core.config import get_settings
from app.core.logging import LOG_NAME, ring_handler
from app.engine.restart import _restart_process
from app.services.orchestrator import Orchestrator

log = logging.getLogger("miair")

router = APIRouter()

# 进程启动时刻 (模块导入时记录, 用于计算运行时长)
_START_TIME = time.time()


def _memory_mb() -> float | None:
    """当前进程内存占用 (MB), 仅用标准库不引入重依赖。

    Linux 优先读 /proc/self/status 的 VmRSS (实时值);
    其它平台退回 resource.ru_maxrss (峰值, Linux 单位 KB / macOS 单位字节)。
    """
    try:
        with open("/proc/self/status", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except OSError:
        pass
    try:
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
        return round(rss / divisor, 1)
    except Exception:
        return None


@router.get("/status")
async def system_status(
    orch: Orchestrator = Depends(get_orchestrator),
    config=Depends(get_engine_config),
):
    """系统状态"""
    return {
        "version": __version__,
        "dlna_running": orch.dlna_running,
        "renderers_count": len(orch.renderers),
        "hostname": config.hostname,
        "dlna_port": config.dlna_port,
        "has_account": bool(config.account or config.cookie),
        "logged_in": orch.auth.is_logged_in(),
        "uptime_seconds": int(time.time() - _START_TIME),
        "memory_mb": _memory_mb(),
    }


@router.post("/system/restart_services")
async def restart_services(
    background: BackgroundTasks,
    orch: Orchestrator = Depends(get_orchestrator),
):
    """热重启 DLNA/AirPlay 子服务"""
    log.info("收到服务重启请求")
    background.add_task(orch.restart_dlna_services)
    return {"ok": True, "message": "服务正在重启"}


@router.post("/system/restart_process")
async def restart_process():
    """重启整个进程 (Docker 下退出由容器策略拉起)"""
    log.info("收到进程重启请求")
    asyncio.get_running_loop().call_later(0.5, _restart_process)
    return {"ok": True, "message": "进程正在重启"}


@router.get("/logs")
async def recent_logs(limit: int = 200):
    """最近日志 (来自内存环形缓冲)"""
    lines = list(ring_handler.buffer)
    if limit > 0:
        lines = lines[-limit:]
    return {"lines": lines}


# 允许运行时切换的日志等级
_LOG_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


class LogLevelPayload(BaseModel):
    level: str


@router.get("/logs/level")
async def get_log_level():
    """当前日志等级"""
    level = logging.getLevelName(logging.getLogger(LOG_NAME).getEffectiveLevel()).lower()
    # 未配置 / 非标准等级时兜底为默认 info
    if level not in _LOG_LEVELS:
        level = "info"
    return {"level": level}


@router.post("/logs/level")
async def set_log_level(payload: LogLevelPayload):
    """运行时切换日志等级 (不持久化, 重启后恢复默认)"""
    level = payload.level.lower()
    if level not in _LOG_LEVELS:
        raise HTTPException(status_code=400, detail=f"不支持的日志等级: {payload.level}")
    logging.getLogger(LOG_NAME).setLevel(_LOG_LEVELS[level])
    # 用不低于新等级的级别记录, 确保切换动作本身总能出现在日志里
    log.log(max(_LOG_LEVELS[level], logging.INFO), f"日志等级已切换为 {level.upper()}")
    return {"ok": True, "level": level}


def _parse_version(v: str) -> tuple:
    """将 'v1.2.3' / '1.2.3' 解析为 (1, 2, 3) 以便比较, 非数字后缀忽略"""
    v = (v or "").strip().lstrip("vV")
    parts = []
    for seg in v.split("."):
        num = ""
        for ch in seg:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    return tuple(parts)


@router.get("/system/check_update")
async def check_update():
    """检查 GitHub 最新 Release, 返回是否有新版本。

    适配 Docker 镜像发布模式: 仅提示新版本与 Release 链接, 不自动覆盖代码;
    用户自行重新拉取镜像升级。
    """
    repo = get_settings().github_repo
    current = __version__
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "miair-next"}
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    return {
                        "current": current,
                        "latest": None,
                        "update_available": False,
                        "error": f"GitHub 返回 HTTP {resp.status}",
                    }
                data = await resp.json()
    except Exception as e:
        return {
            "current": current,
            "latest": None,
            "update_available": False,
            "error": f"检查更新失败: {e}",
        }

    tag = (data.get("tag_name") or "").strip()
    latest = tag.lstrip("vV")
    update_available = bool(latest) and _parse_version(latest) > _parse_version(current)
    return {
        "current": current,
        "latest": latest or None,
        "update_available": update_available,
        "release_url": data.get("html_url"),
        "release_name": data.get("name") or tag,
        "published_at": data.get("published_at"),
        "notes": (data.get("body") or "")[:2000],
    }
