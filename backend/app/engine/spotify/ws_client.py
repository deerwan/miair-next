"""Spotify Soloist 本地 WebSocket 通信客户端"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

log = logging.getLogger("miair")

EventCallback = Callable[[dict], Awaitable[None]]


def _first_cover(visual: dict) -> str:
    covers = visual.get("cover") or []
    if isinstance(covers, list):
        for item in covers:
            if isinstance(item, dict) and item.get("url"):
                return str(item["url"])
    return ""


def _identity_name(entity: dict | None) -> str:
    if not isinstance(entity, dict):
        return ""
    decorations = entity.get("decorations") or {}
    identity = decorations.get("identity") or {}
    name = identity.get("name")
    return str(name) if name else ""


def parse_entity(item: dict | None) -> dict | None:
    """解析 Soloist entity 结构为音频元数据字典"""
    if not isinstance(item, dict):
        return None
    decorations = item.get("decorations") or {}
    identity = decorations.get("identity") or {}
    visual = decorations.get("visual_identity") or {}
    playback = decorations.get("playback") or {}

    artists: list[str] = []
    for creator in decorations.get("creators") or []:
        if not isinstance(creator, dict):
            continue
        name = _identity_name(creator.get("entity"))
        if name:
            artists.append(name)

    parent = decorations.get("parent") or {}
    album = _identity_name((parent or {}).get("entity"))

    title = identity.get("name") or ""
    uri = item.get("uri") or ""
    entity_type = item.get("entity_type") or ""
    if not title and not uri:
        return None
    return {
        "title": str(title),
        "artist": ", ".join(artists),
        "album": album,
        "cover": _first_cover(visual),
        "duration_ms": playback.get("duration_ms") or 0,
        "uri": str(uri),
        "entity_type": str(entity_type),
    }


class SpotifyWsClient:
    """带自动重连的 Soloist WebSocket 客户端"""

    def __init__(self, port: int, on_event: EventCallback):
        self.url = f"ws://127.0.0.1:{port}"
        self.on_event = on_event
        self._ws = None
        self._task: asyncio.Task | None = None
        self._running = False
        self.connected = asyncio.Event()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        backoff = 1.0
        while self._running:
            try:
                import websockets

                async with websockets.connect(
                    self.url, open_timeout=5, ping_interval=20, ping_timeout=20
                ) as ws:
                    self._ws = ws
                    backoff = 1.0
                    self.connected.set()
                    log.info(f"已连接 Spotify Soloist WebSocket: {self.url}")
                    async for raw in ws:
                        if not self._running:
                            break
                        try:
                            data = json.loads(raw)
                        except (TypeError, ValueError):
                            continue
                        if not isinstance(data, dict):
                            continue
                        try:
                            await self.on_event(data)
                        except Exception as e:  # noqa: BLE001
                            log.warning(f"处理 Soloist 事件失败: {e}")
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self.connected.clear()
                self._ws = None
                if not self._running:
                    break
                log.warning(f"Spotify Soloist WebSocket 断开: {e} ({backoff:.0f}s 后重连)")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10.0)
            finally:
                self._ws = None
                self.connected.clear()

    async def send(self, command: str, **fields) -> bool:
        ws = self._ws
        if ws is None:
            return False
        payload = {"type": "command", "command": command}
        payload.update(fields)
        try:
            await ws.send(json.dumps(payload))
            return True
        except Exception as e:  # noqa: BLE001
            log.warning(f"发送 Soloist 命令失败 ({command}): {e}")
            return False

    async def stop(self) -> None:
        self._running = False
        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        self.connected.clear()
