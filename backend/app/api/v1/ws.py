"""WebSocket: 实时推送日志与音箱状态 (连接需带 ?token=)"""

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.logging import ring_handler
from app.core.security import decode_access_token

log = logging.getLogger("miair")

router = APIRouter()

STATUS_INTERVAL = 3  # 状态推送间隔 (秒)


def _collect_status(orch) -> dict:
    speakers = []
    for did, controller in orch.speaker_manager.controllers.items():
        renderer = orch.get_renderer_by_did(did)
        airplay_active = False
        if orch.airplay_manager:
            sap = orch.airplay_manager.speaker_airplays.get(did)
            if sap and sap.airplay_server and sap.airplay_server.is_playing:
                airplay_active = True
        speakers.append({
            "did": did,
            "dlna_name": controller.speaker.get_dlna_name(),
            "transport_state": renderer.transport_state if renderer else "UNKNOWN",
            "current_uri": renderer.current_uri if renderer else "",
            "airplay_active": airplay_active,
        })
    return {
        "type": "status",
        "dlna_running": orch.dlna_running,
        "renderers_count": len(orch.renderers),
        "speakers": speakers,
    }


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = ""):
    # WebSocket 无法带 Header, 通过查询参数校验 token
    if not decode_access_token(token):
        await websocket.close(code=4401)
        return

    await websocket.accept()
    orch = websocket.app.state.orchestrator
    log_queue = ring_handler.subscribe()

    async def push_logs():
        while True:
            line = await log_queue.get()
            await websocket.send_text(json.dumps({"type": "log", "line": line}, ensure_ascii=False))

    async def push_status():
        while True:
            await websocket.send_text(json.dumps(_collect_status(orch), ensure_ascii=False))
            await asyncio.sleep(STATUS_INTERVAL)

    tasks = [
        asyncio.create_task(push_logs()),
        asyncio.create_task(push_status()),
    ]
    try:
        # 保持接收循环以感知客户端断开
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        for t in tasks:
            t.cancel()
        ring_handler.unsubscribe(log_queue)
