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
TOKEN_RECHECK_INTERVAL = 60  # token 过期复验间隔 (秒)


def _collect_status(orch) -> dict:
    speakers = []
    for did, controller in orch.speaker_manager.controllers.items():
        renderer = orch.get_renderer_by_did(did)
        airplay_active = False
        airplay_client = ""
        title = artist = source = ""
        if renderer and renderer.transport_state == "PLAYING":
            # DLNA 路线: 投送元数据里的歌名/歌手 (控制点投送时自带, 准确)
            title, artist = renderer._track_title, renderer._track_artist
            source = "dlna"
        if orch.airplay_manager:
            sap = orch.airplay_manager.speaker_airplays.get(did)
            if sap and sap.airplay_server and sap.airplay_server.is_playing:
                airplay_active = True
                airplay_client = sap.airplay_server.client_name
                # AirPlay 不展示 DAAP 歌名/歌手: 元数据由发送端 App 可选下发,
                # 不少 App 切歌时不更新, 展示出来常与实际内容不符
                # (设备名与"播放中"状态是可靠的, 保留)
                source = "airplay"
        if airplay_active:
            # AirPlay 实际发声时清掉可能残留的 DLNA 标题, 避免串台
            title = artist = ""
        speakers.append({
            "did": did,
            "dlna_name": controller.speaker.get_dlna_name(),
            "transport_state": renderer.transport_state if renderer else "UNKNOWN",
            "current_uri": renderer.current_uri if renderer else "",
            "airplay_active": airplay_active,
            # 正在播放摘要 (总览首行展示): 空串表示无元数据/未播放
            "now_playing": {
                "playing": bool(renderer and renderer.transport_state == "PLAYING") or airplay_active,
                "source": source,
                "title": title,
                "artist": artist,
                "client": airplay_client,
            },
        })
    return {
        "type": "status",
        "dlna_running": orch.dlna_running,
        "renderers_count": len(orch.renderers),
        "speakers": speakers,
    }


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = ""):
    # WebSocket 无法带 Header, 通过查询参数校验 token。
    # 必须 accept 之后再校验关闭: pre-accept 的 close 会被 uvicorn 转成
    # HTTP 403 拒绝握手, 浏览器侧只能看到 1006, 前端的 4401 登出分支
    # 永远不会触发 (token 过期场景退化为无限重连)。
    await websocket.accept()
    if not decode_access_token(token):
        await websocket.close(code=4401)
        return

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

    async def check_token():
        # 连接建立后 token 仍可能过期, 周期复验; 失效则主动关闭 (4401)
        while True:
            await asyncio.sleep(TOKEN_RECHECK_INTERVAL)
            if not decode_access_token(token):
                await websocket.close(code=4401)
                return

    tasks = [
        asyncio.create_task(push_logs()),
        asyncio.create_task(push_status()),
        asyncio.create_task(check_token()),
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
