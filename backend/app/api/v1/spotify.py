"""Spotify Connect (Soloist) 管理接口"""

from __future__ import annotations

import asyncio
import logging
import platform
import shutil

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_engine_config, get_orchestrator
from app.core.config import get_settings
from app.engine.spotify.soloist import inspect_binary
from app.services.orchestrator import Orchestrator

log = logging.getLogger("miair")

router = APIRouter()


def _target_name(config) -> str:
    did = (config.spotify_did or "").strip()
    if not did:
        return ""
    try:
        speaker = config.get_speaker(did)
        return speaker.get_dlna_name()
    except Exception:  # noqa: BLE001
        return ""


async def _status_payload(config, orchestrator: Orchestrator) -> dict:
    receiver = orchestrator.get_spotify_receiver()
    if receiver is not None:
        data = receiver.status()
    else:
        data = {
            "enabled": bool(getattr(config, "spotify_enabled", False)),
            "running": False,
            "paired": False,
            "active": False,
            "device_name": (config.spotify_device_name or "").strip(),
            "target_did": config.spotify_did or "",
            "target_name": _target_name(config),
            "ws_port": 0,
            "stream_url": "",
            "playback": "idle",
            "track": None,
            "volume": None,
            "binary": await asyncio.to_thread(inspect_binary, get_settings().data_dir),
            "expires_in_days": None,
            "last_error": "",
        }
    data.update(
        {
            "api_key_configured": bool((config.spotify_api_key or "").strip()),
            "arch": platform.machine(),
            "pulse_available": shutil.which("pulseaudio") is not None,
            "hostname": config.hostname,
        }
    )
    if not data.get("running"):
        reasons: list[str] = []
        if not data.get("enabled"):
            reasons.append("Spotify 畅联未启用")
        if not data.get("api_key_configured"):
            reasons.append("未配置 Soloist API Key")
        if not data.get("pulse_available"):
            reasons.append("当前环境没有 PulseAudio (需使用 Docker 镜像)")
        if not reasons and not data.get("last_error"):
            reasons.append("等待小米账号登录与可用音箱后自动启动")
        if reasons and not data.get("last_error"):
            data["idle_reason"] = "; ".join(reasons)
    return data


@router.get("/spotify/status")
async def spotify_status(
    orch: Orchestrator = Depends(get_orchestrator),
    config=Depends(get_engine_config),
):
    return await _status_payload(config, orch)


@router.post("/spotify/pair")
async def spotify_pair(
    orch: Orchestrator = Depends(get_orchestrator),
    config=Depends(get_engine_config),
):
    receiver = orch.get_spotify_receiver()
    if receiver is None or not receiver.running:
        raise HTTPException(
            status_code=400,
            detail="Spotify 接收器未运行, 请先在「系统设置 → Spotify 畅联」启用并保存",
        )
    return receiver.pair_hint()


@router.post("/spotify/restart")
async def spotify_restart(
    orch: Orchestrator = Depends(get_orchestrator),
    config=Depends(get_engine_config),
):
    receiver = orch.get_spotify_receiver()
    if receiver is None:
        raise HTTPException(status_code=400, detail="Spotify 接收器未启动")
    try:
        await receiver.restart()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"重启失败: {e}") from e
    return {"ok": True, "message": "Spotify 服务已重启"}


@router.post("/spotify/update")
async def spotify_update(
    orch: Orchestrator = Depends(get_orchestrator),
    config=Depends(get_engine_config),
):
    receiver = orch.get_spotify_receiver()
    if receiver is None:
        raise HTTPException(status_code=400, detail="Spotify 接收器未启动, 无法更新")
    # 异步下载最新二进制并重启服务
    if receiver.start_update():
        return {"ok": True, "message": "正在下载最新 Soloist 并重启, 请稍后刷新状态"}
    return {"ok": True, "message": "更新已在进行中, 请稍后刷新状态"}
