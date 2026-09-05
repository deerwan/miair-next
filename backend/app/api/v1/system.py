"""系统状态 / 健康检查 / 服务重启 / 日志"""

import asyncio
import logging
import os
import platform
import resource
import sys
import time

import aiohttp
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import __version__
from app.api.deps import get_engine_config, get_orchestrator
from app.core.config import get_settings
from app.core.logging import LOG_NAME, ring_handler
from app.engine.notify import send_notification
from app.engine.restart import _restart_process
from app.services.orchestrator import Orchestrator

log = logging.getLogger("miair")

router = APIRouter()

# 进程启动时刻 (模块导入时记录, 用于计算运行时长)
_START_TIME = time.time()

# 版本检查结果缓存 (result, timestamp), TTL 内直接返回, 避免 GitHub 匿名限流
_UPDATE_CACHE_TTL = 600
_update_cache: tuple[dict, float] | None = None


def _memory_mb() -> tuple[float | None, str]:
    """当前内存占用 (MB) 与口径标注, 仅用标准库不引入重依赖。

    业界标准的分层回退 (口径与所处环境的 OOM 威胁模型对齐):
    1. 容器 (cgroup v2/v1): memory.current —— 与 docker stats / OOM killer
       同一口径, 给容器设了内存限额时这个数字才有意义;
    2. Linux 裸机: PSS (/proc/self/smaps_rollup) —— 共享库按比例分摊,
       比 VmRSS 更接近真实物理占用;
    3. 其它平台 (macOS 开发): resource.ru_maxrss (峰值口径, 尽力而为)。
    """
    # 1) cgroup v2 (Docker 默认) / v1
    for path in (
        "/sys/fs/cgroup/memory.current",                # cgroup v2
        "/sys/fs/cgroup/memory/memory.usage_in_bytes",  # cgroup v1
    ):
        try:
            with open(path, encoding="utf-8") as f:
                return round(int(f.read().strip()) / 1024 / 1024, 1), "cgroup"
        except (OSError, ValueError):
            continue

    # 2) Linux 裸机: PSS (smaps_rollup 自内核 4.14 起可用)
    try:
        with open("/proc/self/smaps_rollup", encoding="utf-8") as f:
            for line in f:
                if line.startswith("Pss:"):
                    return round(int(line.split()[1]) / 1024, 1), "pss"
    except OSError:
        pass

    # 3) 回退: Linux VmRSS, 再退 macOS ru_maxrss (峰值)
    try:
        with open("/proc/self/status", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1), "rss"
    except OSError:
        pass
    try:
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
        return round(rss / divisor, 1), "peak"
    except Exception:
        return None, "unknown"


# 进程 CPU 采样基线: (墙钟时间, 累计 CPU 秒), 用于相邻两次 /status 间算百分比
_last_cpu_sample: tuple[float, float] | None = None


def _read_process_cpu_seconds() -> float | None:
    """进程累计 CPU 秒: Linux 读 /proc/self/stat, 其它平台退回 getrusage"""
    try:
        with open("/proc/self/stat", encoding="utf-8") as f:
            fields = f.read().rsplit(")", 1)[1].split()
        # utime=第11字段, stime=第12字段 (index 11/12 after state), 单位: 时钟滴答
        clock_ticks = os.sysconf("SC_CLK_TCK")
        utime, stime = int(fields[11]), int(fields[12])
        return (utime + stime) / clock_ticks
    except (OSError, IndexError, ValueError):
        pass
    try:
        ru = resource.getrusage(resource.RUSAGE_SELF)
        return ru.ru_utime + ru.ru_stime
    except Exception:
        return None


def _cpu_percent() -> float | None:
    """进程 CPU 占用率 (%): 相邻两次调用的 CPU 时间差 / 墙钟差。

    前端每 30s 轮询一次, 返回的是该窗口的平均占用——与任务管理器的
    刷新语义一致。仅统计本进程 (容器内即容器主进程), 不含宿主机整体。
    """
    global _last_cpu_sample
    cpu = _read_process_cpu_seconds()
    if cpu is None:
        return None
    now = time.time()
    if _last_cpu_sample is None:
        _last_cpu_sample = (now, cpu)
        return None
    prev_t, prev_cpu = _last_cpu_sample
    _last_cpu_sample = (now, cpu)
    wall = now - prev_t
    if wall <= 0:
        return None
    return round(min((cpu - prev_cpu) / wall * 100, 100.0), 1)


def _disk_free_gb(config) -> float | None:
    """数据目录所在文件系统的剩余空间 (GB): 日志/数据库/转码临时文件都在这"""
    try:
        import shutil

        return round(shutil.disk_usage(config.conf_path).free / 1024**3, 1)
    except Exception:
        return None


@router.get("/status")
async def system_status(
    orch: Orchestrator = Depends(get_orchestrator),
    config=Depends(get_engine_config),
):
    """系统状态"""
    auth = orch.auth
    # serviceToken 已使用时长 (小时): 供前端/运维判断续期状态; 未登录时为 None
    token_age_hours = (
        round((time.time() - auth._service_token_issued_at) / 3600, 1)
        if auth._service_token_issued_at > 0
        else None
    )
    # serviceToken 剩余有效期 (小时): 负数表示已过期, 由三级降级链兜底恢复
    token_remaining_hours = (
        round((config.token_expires_at - time.time()) / 3600, 1)
        if config.token_expires_at > 0
        else None
    )
    memory_mb, memory_source = _memory_mb()
    return {
        "version": __version__,
        "dlna_running": orch.dlna_running,
        "renderers_count": len(orch.renderers),
        "hostname": config.hostname,
        "dlna_port": config.dlna_port,
        "has_account": bool(config.account or config.cookie),
        "logged_in": orch.auth.is_logged_in(),
        "service_token_age_hours": token_age_hours,
        "service_token_remaining_hours": token_remaining_hours,
        # 是否配置了账号密码: passToken 过期时的自动恢复兜底凭证
        "has_password_fallback": bool(config.account and config.password),
        "token_refresh_running": bool(auth._refresh_task and not auth._refresh_task.done()),
        "uptime_seconds": int(time.time() - _START_TIME),
        "memory_mb": memory_mb,
        # 内存口径: cgroup(容器, 与 docker stats 一致) / pss / rss / peak
        "memory_source": memory_source,
        # 进程 CPU 占用率 (%): 两次轮询窗口的平均值, 首次调用无基线为 None
        "cpu_percent": _cpu_percent(),
        # 数据目录所在文件系统剩余空间 (GB)
        "disk_free_gb": _disk_free_gb(config),
        # 运行环境 (排障用): python 版本 + CPU 架构
        "python_version": sys.version.split()[0],
        "arch": platform.machine(),
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


@router.post("/system/notify/test")
async def notify_test(config=Depends(get_engine_config)):
    """向已配置的通知渠道发送测试消息 (跳过节流)"""
    results = await send_notification(
        config,
        "test",
        "[MiAir Next] 测试消息",
        "如果你收到这条消息, 说明通知渠道配置成功。",
        force=True,
    )
    if not results:
        raise HTTPException(status_code=400, detail="未配置通知方式, 请先选择通知方式并保存对应凭证")
    return {"ok": any(results.values()), "results": results}


@router.get("/logs")
async def recent_logs(limit: int = 200):
    """最近日志 (来自内存环形缓冲)"""
    lines = list(ring_handler.buffer)
    if limit > 0:
        lines = lines[-limit:]
    return {"lines": lines}


@router.get("/logs/download")
async def download_logs(config=Depends(get_engine_config)):
    """下载落盘的完整日志文件 (miair.log)。

    内存环形缓冲仅留最近若干行, 文件则保留本次运行期全量日志。
    """
    log_file = config.log_file
    if not os.path.isfile(log_file):
        raise HTTPException(status_code=404, detail="日志文件不存在")
    return FileResponse(
        log_file,
        media_type="text/plain",
        filename="miair.log",
    )


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
async def check_update(force: bool = False):
    """检查 GitHub 最新 Release, 返回是否有新版本。

    适配 Docker 镜像发布模式: 仅提示新版本与 Release 链接, 不自动覆盖代码;
    用户自行重新拉取镜像升级。

    成功结果缓存 10 分钟, 避免频繁请求触发 GitHub 匿名限流 (60 次/小时);
    force=true 可强制绕过缓存。
    """
    global _update_cache
    if not force and _update_cache is not None:
        cached, ts = _update_cache
        if time.time() - ts < _UPDATE_CACHE_TTL:
            return cached

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
    result = {
        "current": current,
        "latest": latest or None,
        "update_available": update_available,
        "release_url": data.get("html_url"),
        "release_name": data.get("name") or tag,
        "published_at": data.get("published_at"),
        "notes": (data.get("body") or "")[:2000],
    }
    # 仅缓存成功结果 (错误不缓存, 便于下次重试)
    _update_cache = (result, time.time())
    return result
