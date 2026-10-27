"""Spotify 畅联状态收集服务"""

from __future__ import annotations


def collect_spotify_speaker_status(orchestrator, did: str) -> dict:
    config = orchestrator.config
    enabled = bool(getattr(config, "spotify_enabled", False))
    configured = bool((getattr(config, "spotify_api_key", "") or "").strip())

    info = {
        "enabled": enabled,
        "configured": configured,
        "is_target": False,
        "active": False,
        "offline": False,
        "running": False,
        "paired": False,
        "playback": "idle",
        "device_name": "",
        "track": None,
        "volume": None,
        "last_error": "",
    }
    if not enabled:
        return info

    receiver = orchestrator.get_spotify_receiver()
    if receiver is None:
        # 接收器未运行时依据配置判断目标音箱
        target_did = (getattr(config, "spotify_did", "") or "").strip()
        info["is_target"] = bool(target_did) and did == target_did
        info["offline"] = bool(getattr(orchestrator, "spotify_target_offline", False))
        return info

    status = receiver.status()
    track = status.get("track")
    info.update(
        {
            "is_target": did == status.get("target_did"),
            "active": bool(status.get("active", status.get("is_active", False))),
            "offline": bool(status.get("offline", False)),
            "running": bool(status.get("running")),
            "paired": bool(status.get("paired")),
            "playback": status.get("playback") or "idle",
            "device_name": status.get("device_name") or "",
            "track": track,
            "volume": status.get("volume"),
            "last_error": status.get("last_error") or "",
        }
    )
    return info
