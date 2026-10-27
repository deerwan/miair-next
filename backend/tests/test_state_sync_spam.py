"""状态同步刷屏修复测试 (refs #18)

music-api 机型 pause=player_stop: 音箱在暂停期间持续上报 STOPPED,
而「保持位置」改写会把渲染器维持在 PAUSED。修复前每轮轮询都会重复
状态改写、INFO 日志与 GENA 通知。
"""

import asyncio

from app.engine.const import TRANSPORT_STATE_PAUSED, TRANSPORT_STATE_PLAYING
from app.engine.dlna.device_server import DeviceServer


class _FakeRenderer:
    """仅覆盖 _sync_renderer_state / _handle_state_transition 触及的属性"""

    def __init__(self):
        self.friendly_name = "测试音箱"
        self.transport_state = TRANSPORT_STATE_PLAYING
        self._play_start_time = 100.0
        self._accumulated_time = 0.0
        self._stuck_paused_since = 0.0
        self._user_stopped = True
        self._play_grace_until = 0.0
        self.notify_calls = 0

    async def notify_state_change(self):
        self.notify_calls += 1


def _make_ds() -> DeviceServer:
    return DeviceServer("127.0.0.1", 8200)


def test_stuck_paused_polls_are_suppressed():
    """保持位置暂停期间, 轮询 STOPPED 不再重复改写/通知"""

    async def run():
        ds = _make_ds()
        r = _FakeRenderer()
        # 已处于「保持位置」暂停 (改写发生过)
        r.transport_state = TRANSPORT_STATE_PAUSED
        r._stuck_paused_since = 1000.0

        for _ in range(3):  # 模拟多轮轮询
            await ds._sync_renderer_state("udn", r, {"status": 0})

        assert r.transport_state == TRANSPORT_STATE_PAUSED
        assert r.notify_calls == 0, "暂停期间轮询不应重复通知"

    asyncio.run(run())


def test_first_stop_transition_still_notifies():
    """真正的播放->停止 (首次) 仍应正常通知并进入保持位置"""

    async def run():
        ds = _make_ds()
        r = _FakeRenderer()  # PLAYING, _stuck_paused_since=0

        await ds._sync_renderer_state("udn", r, {"status": 0})

        assert r.notify_calls == 1
        assert r.transport_state == TRANSPORT_STATE_PAUSED, "应改写为保持位置暂停"
        assert r._stuck_paused_since > 0
        assert r._accumulated_time > 0, "应累计播放时长"

    asyncio.run(run())


def test_stop_transition_when_guard_inactive_still_works():
    """守卫未激活 (从未标记保持位置) 时, STOPPED 同步行为不变"""

    async def run():
        ds = _make_ds()
        r = _FakeRenderer()
        r.transport_state = TRANSPORT_STATE_PAUSED
        r._stuck_paused_since = 0.0  # 守卫未激活

        await ds._sync_renderer_state("udn", r, {"status": 0})

        assert r.notify_calls == 1, "守卫未激活时保持原行为"
        assert r.transport_state == TRANSPORT_STATE_PAUSED
        assert r._stuck_paused_since > 0

    asyncio.run(run())
