"""凭据恢复重启节流测试 (2026-09-06 重启风暴事故回归)

restart_dlna_services 每次都会新建 AuthManager; 当登录持续失败而「复用缓存
serviceToken」持续成功时, 每轮都会触发凭据恢复回调 → 重启 → 新 AuthManager
→ 又登录失败 → 回调……无节流时形成死循环并以每秒数次的频率请求小米接口。

节流状态必须放在 orchestrator 单例上 (跨 AuthManager 实例生效)。
"""

import asyncio
import logging
from types import SimpleNamespace

from app.services.orchestrator import RECOVERY_RESTART_MIN_INTERVAL, Orchestrator


def _bare_orchestrator() -> Orchestrator:
    """绕过 __init__ 构造最小化实例 (只设回调路径依赖的字段)"""
    orch = Orchestrator.__new__(Orchestrator)
    orch.dlna_running = False
    orch._last_recovery_restart = 0.0
    return orch


def _attach_recorder(orch: Orchestrator, calls: list):
    async def fake_restart():
        calls.append(1)

    orch.restart_dlna_services = fake_restart


def test_second_recovery_within_interval_is_skipped():
    """冷却窗口内的第二次恢复回调直接跳过 (切断 重启→失败→复用→再重启 循环)"""
    orch = _bare_orchestrator()
    calls: list = []
    _attach_recorder(orch, calls)

    async def run():
        await orch._on_auth_recovered()
        await orch._on_auth_recovered()
        return len(calls)

    assert asyncio.run(run()) == 1


def test_recovery_allowed_after_interval():
    """冷却期过后恢复回调正常放行 (真实凭据恢复场景不受影响)"""
    orch = _bare_orchestrator()
    calls: list = []
    _attach_recorder(orch, calls)

    async def run():
        await orch._on_auth_recovered()
        orch._last_recovery_restart -= RECOVERY_RESTART_MIN_INTERVAL + 1
        await orch._on_auth_recovered()
        return len(calls)

    assert asyncio.run(run()) == 2


def test_recovery_skipped_when_dlna_already_running():
    """DLNA 已在运行时不触发重启 (原有语义保持)"""
    orch = _bare_orchestrator()
    orch.dlna_running = True
    calls: list = []
    _attach_recorder(orch, calls)

    asyncio.run(orch._on_auth_recovered())

    assert calls == []


def test_start_spotify_defers_when_target_offline():
    """启动时目标音箱离线应等待上线"""
    orch = Orchestrator.__new__(Orchestrator)
    orch.config = SimpleNamespace(
        spotify_enabled=True,
        spotify_api_key="key",
        spotify_did="did-1",
        hostname="127.0.0.1",
    )
    orch.spotify = None
    orch.spotify_target_offline = False
    orch.spotify_offline_reason = ""
    orch._spotify_watch_task = None

    async def offline():
        raise RuntimeError("device offline")

    controller = SimpleNamespace(
        get_status=offline,
        speaker=SimpleNamespace(get_dlna_name=lambda: "Test Speaker"),
    )
    orch.speaker_manager = SimpleNamespace(controllers={"did-1": controller})

    async def run():
        await orch._start_spotify()
        assert orch.spotify is None
        assert orch.spotify_target_offline is True
        assert "离线" in orch.spotify_offline_reason
        assert orch._spotify_watch_task is not None
        task = orch._spotify_watch_task
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        orch._spotify_watch_task = None

    asyncio.run(run())


def test_online_watcher_starts_spotify(monkeypatch):
    """监听任务在音箱上线后启动 Spotify"""
    orch = Orchestrator.__new__(Orchestrator)
    orch.config = SimpleNamespace(
        spotify_enabled=True,
        spotify_api_key="key",
        spotify_did="did-1",
        hostname="127.0.0.1",
    )
    orch.spotify = None
    orch.spotify_target_offline = True
    orch.spotify_offline_reason = "目标音箱离线"
    orch._spotify_watch_task = None

    async def online():
        return {"status": 1}

    controller = SimpleNamespace(
        get_status=online,
        speaker=SimpleNamespace(get_dlna_name=lambda: "Test Speaker"),
    )
    orch.speaker_manager = SimpleNamespace(controllers={"did-1": controller})

    started: list[int] = []

    async def fake_start():
        started.append(1)

    orch._start_spotify = fake_start

    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    asyncio.run(orch._watch_spotify_online())
    assert started == [1]


def test_offline_info_logged_once_then_debug(caplog):
    """离线重试日志降级为 DEBUG"""
    orch = Orchestrator.__new__(Orchestrator)
    orch.spotify_target_offline = False

    async def offline():
        raise RuntimeError("device offline")

    controller = SimpleNamespace(get_status=offline)

    async def run():
        with caplog.at_level(logging.DEBUG, logger="miair"):
            await orch._is_controller_online(controller)
            orch.spotify_target_offline = True
            await orch._is_controller_online(controller)
            await orch._is_controller_online(controller)

    asyncio.run(run())
    info_records = [
        r for r in caplog.records
        if "Spotify 目标音箱离线" in r.message and r.levelno == logging.INFO
    ]
    debug_records = [
        r for r in caplog.records
        if "Spotify 目标音箱仍离线" in r.message and r.levelno == logging.DEBUG
    ]
    assert len(info_records) == 1
    assert len(debug_records) == 2


def test_offline_callback_stops_receiver_and_arms_watch():
    """运行中目标音箱离线应停止接收器并监听上线"""
    orch = Orchestrator.__new__(Orchestrator)
    orch.spotify_target_offline = False
    orch.spotify_offline_reason = ""
    orch._spotify_watch_task = None

    stopped: list[int] = []

    async def stop():
        stopped.append(1)

    receiver = SimpleNamespace(stop=stop)
    orch.spotify = receiver

    async def fake_watch():
        return None

    orch._watch_spotify_online = fake_watch

    async def run():
        await orch._on_spotify_target_offline(receiver)
        assert orch.spotify is None
        assert orch.spotify_target_offline is True
        assert orch.spotify_offline_reason == "目标音箱离线"
        assert stopped == [1]
        assert orch._spotify_watch_task is not None
        orch._spotify_watch_task.cancel()
        try:
            await orch._spotify_watch_task
        except asyncio.CancelledError:
            pass
        orch._spotify_watch_task = None

    asyncio.run(run())


def test_visibility_hides_and_shows_speaker():
    """音箱离线与上线时动态更新 AirPlay/DLNA 注册状态"""
    orch = Orchestrator.__new__(Orchestrator)
    orch.dlna_running = True
    orch._offline_speakers = set()
    orch._did_to_udn = {"did-1": "udn-1"}

    state = {"online": False}

    async def get_status():
        if not state["online"]:
            raise RuntimeError("offline")
        return {"status": 1}

    controller = SimpleNamespace(
        get_status=get_status,
        speaker=SimpleNamespace(get_dlna_name=lambda: "Test Speaker"),
    )
    orch.speaker_manager = SimpleNamespace(controllers={"did-1": controller})

    class Renderer:
        reset = 0

        async def reset_to_idle(self):
            self.reset += 1

    renderer = Renderer()
    orch.renderers = {"udn-1": renderer}

    class AirPlay:
        hidden: list[str] = []
        shown: list[str] = []

        async def hide_speaker(self, did):
            self.hidden.append(did)

        async def show_speaker(self, did, controller):
            self.shown.append(did)

    class SSDP:
        unregistered: list[str] = []
        registered: list[tuple[str, str]] = []
        alive: list[str] = []

        async def unregister_renderer(self, udn):
            self.unregistered.append(udn)

        def register_renderer(self, udn, name):
            self.registered.append((udn, name))

        async def send_alive_for(self, udn):
            self.alive.append(udn)

    class Device:
        unregistered: list[str] = []
        registered: list[int] = []

        async def unregister_renderer(self, udn):
            self.unregistered.append(udn)

        def register_renderer(self, renderer):
            self.registered.append(1)

    orch.airplay_manager = AirPlay()
    orch.ssdp_server = SSDP()
    orch.device_server = Device()

    async def run():
        await orch._refresh_speaker_visibility()
        assert orch._offline_speakers == {"did-1"}
        assert orch.airplay_manager.hidden == ["did-1"]
        assert orch.ssdp_server.unregistered == ["udn-1"]
        assert orch.device_server.unregistered == ["udn-1"]
        assert renderer.reset == 1

        state["online"] = True
        await orch._refresh_speaker_visibility()
        assert orch._offline_speakers == set()
        assert orch.airplay_manager.shown == ["did-1"]
        assert orch.ssdp_server.registered == [("udn-1", "Test Speaker")]
        assert orch.ssdp_server.alive == ["udn-1"]
        assert orch.device_server.registered == [1]

    asyncio.run(run())
