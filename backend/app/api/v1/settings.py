"""系统设置 (移植自 MiAir handle_get_setting / handle_save_setting)

改进: 保存设置后不再重启整个进程, 而是热重启 DLNA/AirPlay 子服务。
"""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends

from app import __version__
from app.api.deps import get_engine_config, get_orchestrator
from app.core.masking import (
    mask_cookie,
    mask_devices,
    mask_secret,
    unmask_cookie,
    unmask_secret,
)
from app.engine.const import NEED_USE_PLAY_MUSIC_API
from app.engine.const import VERSION as ENGINE_VERSION
from app.models.schemas import SettingsPayload
from app.services.orchestrator import Orchestrator

log = logging.getLogger("miair")

router = APIRouter()


@router.get("/settings")
async def get_settings_api(
    need_device_list: bool = False,
    orch: Orchestrator = Depends(get_orchestrator),
    config=Depends(get_engine_config),
):
    """获取当前设置和设备列表"""
    data = {
        "version": __version__,
        "engine_version": ENGINE_VERSION,
        "hostname": config.hostname,
        "dlna_port": config.dlna_port,
        "auto_play_on_set_uri": config.auto_play_on_set_uri,
        "mi_did": config.mi_did,
        "has_account": bool(config.account or config.cookie),
        "cookie": mask_cookie(config.cookie),
        "dlna_running": orch.dlna_running,
        "renderers_count": len(orch.renderers),
        "auto_resume_on_interrupt": config.auto_resume_on_interrupt,
        "resume_delay_seconds": config.resume_delay_seconds,
        "default_volume": config.default_volume,
        "follow_device_volume": config.follow_device_volume,
        "auto_restart": config.auto_restart,
        "notify_type": config.notify_type,
        "notify_feishu_webhook": config.notify_feishu_webhook,
        "notify_feishu_secret": mask_secret(config.notify_feishu_secret),
        "notify_wxpusher_spt": mask_secret(config.notify_wxpusher_spt),
        "need_use_play_music_api": NEED_USE_PLAY_MUSIC_API,
        "default_audio_id": config.default_audio_id,
        "touchscreen_lyrics": config.touchscreen_lyrics,
        "default_cover_url": config.default_cover_url,
    }

    speakers_info = {}
    for did in config.get_did_list():
        speaker = config.get_speaker(did)
        speakers_info[did] = {
            "did": did,
            "name": speaker.name,
            "dlna_name": speaker.get_dlna_name(),
            "hardware": speaker.hardware,
            "enabled": speaker.enabled,
            "compatibility_mode": speaker.is_compatibility_mode(),
        }
    data["speakers"] = speakers_info

    if need_device_list:
        device_list = await orch.get_all_devices()
        data["device_list"] = mask_devices(device_list)

    return data


@router.post("/settings")
async def save_settings_api(
    payload: SettingsPayload,
    background: BackgroundTasks,
    orch: Orchestrator = Depends(get_orchestrator),
    config=Depends(get_engine_config),
):
    """保存设置并热重启 DLNA/AirPlay 服务"""
    if payload.account is not None:
        config.account = payload.account
    if payload.password is not None:
        config.password = payload.password
    if payload.cookie is not None:
        # 若前端回写的是脱敏占位符 (未修改 passToken 等), 还原为已存储的真实值
        config.cookie = unmask_cookie(payload.cookie, config.cookie)
    if payload.mi_did is not None:
        config.mi_did = payload.mi_did
    if payload.dlna_port is not None:
        config.dlna_port = payload.dlna_port
    if payload.auto_play_on_set_uri is not None:
        config.auto_play_on_set_uri = payload.auto_play_on_set_uri
    if payload.auto_resume_on_interrupt is not None:
        config.auto_resume_on_interrupt = payload.auto_resume_on_interrupt
    if payload.resume_delay_seconds is not None:
        config.resume_delay_seconds = payload.resume_delay_seconds
    if payload.default_volume is not None:
        config.default_volume = payload.default_volume
    if payload.follow_device_volume is not None:
        config.follow_device_volume = payload.follow_device_volume
    if payload.auto_restart is not None:
        config.auto_restart = payload.auto_restart
    if payload.default_cover_url is not None:
        config.default_cover_url = payload.default_cover_url.strip()
    if payload.default_audio_id is not None:
        config.default_audio_id = payload.default_audio_id.strip()
    if payload.touchscreen_lyrics is not None:
        config.touchscreen_lyrics = payload.touchscreen_lyrics
    if payload.notify_type is not None:
        config.notify_type = payload.notify_type.strip()
    if payload.notify_feishu_webhook is not None:
        config.notify_feishu_webhook = payload.notify_feishu_webhook.strip()
    if payload.notify_feishu_secret is not None:
        # 回写的是脱敏占位符 (未修改) 时保留已存储的真实密钥
        config.notify_feishu_secret = unmask_secret(
            payload.notify_feishu_secret.strip(), config.notify_feishu_secret
        )
    if payload.notify_wxpusher_spt is not None:
        # 回写的是脱敏占位符 (未修改) 时保留已存储的真实 SPT
        config.notify_wxpusher_spt = unmask_secret(
            payload.notify_wxpusher_spt.strip(), config.notify_wxpusher_spt
        )

    if payload.speakers:
        for did, speaker_data in payload.speakers.items():
            speaker = config.get_speaker(did)
            if speaker_data.dlna_name is not None:
                speaker.dlna_name = speaker_data.dlna_name
            if speaker_data.compatibility_mode is not None:
                speaker.compatibility_mode = speaker_data.compatibility_mode

    config.save()

    # 响应返回后在后台热重启子服务 (无需重启进程)
    log.info("配置已保存, 正在热重启 DLNA/AirPlay 服务...")
    background.add_task(orch.restart_dlna_services)
    return {"ok": True, "message": "配置已保存, 服务正在重启"}
