"""设备与音箱管理 / 播放控制"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_engine_config, get_orchestrator
from app.core.masking import mask_devices
from app.models.schemas import PlayUrlRequest, RenameRequest, VolumeRequest
from app.services.orchestrator import Orchestrator

log = logging.getLogger("miair")

router = APIRouter()


@router.get("/devices")
async def list_devices(orch: Orchestrator = Depends(get_orchestrator)):
    """获取小米账号下所有设备列表"""
    config = orch.config
    if not config.account and not config.cookie:
        raise HTTPException(status_code=400, detail="请先配置小米账号或 Cookie")

    devices = await orch.get_all_devices()
    if not devices and not orch.auth.is_logged_in():
        return {
            "devices": [],
            "error": "登录失败, 请检查账号密码或尝试使用 Cookie 登录",
        }
    return {"devices": mask_devices(devices)}


@router.get("/speakers")
async def list_speakers(orch: Orchestrator = Depends(get_orchestrator)):
    """获取当前运行中的渲染器状态"""
    speakers_info = []
    for did, controller in orch.speaker_manager.controllers.items():
        speaker = controller.speaker
        renderer = orch.get_renderer_by_did(did)
        transport_state = renderer.transport_state if renderer else "UNKNOWN"
        current_uri = renderer.current_uri if renderer else ""

        # 获取 AirPlay 状态
        airplay_active = False
        airplay_client = ""
        if orch.airplay_manager:
            sap = orch.airplay_manager.speaker_airplays.get(did)
            if sap and sap.airplay_server:
                if sap.airplay_server.is_playing:
                    airplay_active = True
                    airplay_client = sap.airplay_server.client_name

        speakers_info.append({
            "did": did,
            "name": speaker.name,
            "dlna_name": speaker.get_dlna_name(),
            "hardware": speaker.hardware,
            "enabled": speaker.enabled,
            "compatibility_mode": speaker.is_compatibility_mode(),
            "udn": speaker.udn,
            "transport_state": transport_state,
            "current_uri": current_uri,
            "airplay_active": airplay_active,
            "airplay_client": airplay_client,
        })
    return speakers_info


def _get_controller(orch: Orchestrator, did: str):
    controller = orch.speaker_manager.get_controller(did)
    if not controller:
        raise HTTPException(status_code=404, detail="音箱不存在或未启用")
    return controller


@router.post("/speakers/{did}/rename")
async def rename_speaker(
    did: str,
    payload: RenameRequest,
    orch: Orchestrator = Depends(get_orchestrator),
    config=Depends(get_engine_config),
):
    """重命名音箱的 DLNA 名称"""
    speaker = config.get_speaker(did)
    speaker.dlna_name = payload.dlna_name
    config.save()

    # 更新对应的 DLNA 渲染器名称
    for udn, renderer in orch.renderers.items():
        if renderer.did == did:
            renderer.friendly_name = payload.dlna_name
            log.info(f"已更新渲染器名称: {payload.dlna_name} (did={did})")
            break

    return {"ok": True, "dlna_name": payload.dlna_name}


@router.post("/speakers/{did}/play_url")
async def play_url(
    did: str,
    payload: PlayUrlRequest,
    orch: Orchestrator = Depends(get_orchestrator),
):
    """让指定音箱播放 URL"""
    controller = _get_controller(orch, did)
    ok = await controller.play_url(payload.url)
    return {"ok": ok}


@router.post("/speakers/{did}/pause")
async def pause(did: str, orch: Orchestrator = Depends(get_orchestrator)):
    controller = _get_controller(orch, did)
    ok = await controller.pause()
    return {"ok": ok}


@router.post("/speakers/{did}/stop")
async def stop(did: str, orch: Orchestrator = Depends(get_orchestrator)):
    controller = _get_controller(orch, did)
    ok = await controller.stop()
    return {"ok": ok}


@router.post("/speakers/{did}/volume")
async def set_volume(
    did: str,
    payload: VolumeRequest,
    orch: Orchestrator = Depends(get_orchestrator),
):
    controller = _get_controller(orch, did)
    ok = await controller.set_volume(payload.volume)
    return {"ok": ok}


@router.get("/speakers/{did}/volume")
async def get_volume(did: str, orch: Orchestrator = Depends(get_orchestrator)):
    controller = _get_controller(orch, did)
    volume = await controller.get_volume()
    return {"volume": volume}


@router.get("/speakers/{did}/status")
async def speaker_status(did: str, orch: Orchestrator = Depends(get_orchestrator)):
    controller = _get_controller(orch, did)
    status = await controller.get_status()
    return status
