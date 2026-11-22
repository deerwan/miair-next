"""SpeakerController 状态轮询与连续失败计数测试 (不触网)"""

import asyncio

import pytest

from app.engine.config import Config, Speaker
from app.engine.speaker import SpeakerController


class FakeMina:
    """替代 MiNAService: player_get_status 可编程成功/登录失效/空闲"""

    def __init__(self, fail: bool = False, idle: bool = False):
        self.fail = fail
        self.idle = idle

    async def player_get_status(self, device_id):
        if self.fail:
            raise Exception("Error https://api2.mina.mi.com: Login failed")
        if self.idle:
            # 实测: 设备未加载任何媒体时只返回 data.code, 不含 info
            # (暂停/停止但保有曲目时仍有 info, status=2/0)
            return {"code": 0, "message": "Msg has been successfully proxy...",
                    "data": {"code": 0}}
        return {"code": 0, "data": {"info": '{"status":1,"volume":30}'}}


class FakeAuth:
    """替代 AuthManager: ensure_login 空操作, call_api 直接执行 factory"""

    def __init__(self, fail: bool = False, config=None, idle: bool = False):
        self.mina_service = FakeMina(fail, idle)
        self.config = config

    async def ensure_login(self):
        return None

    async def call_api(self, factory, label=""):
        return await factory()


def _make_controller(tmp_path, fail: bool = False, idle: bool = False) -> SpeakerController:
    config = Config(conf_path=str(tmp_path))
    return SpeakerController(
        Speaker(did="123", device_id="dev-1", hardware="LX06"),
        auth=FakeAuth(fail, config, idle),
        config=config,
    )


class RecorderMina:
    """记录 play 调用参数的 Mina 替身"""

    def __init__(self):
        self.calls = []

    async def player_get_status(self, device_id):
        return {"code": 0, "data": {"info": '{"status":1,"volume":30}'}}

    async def play_by_music_url(self, device_id, url, **kwargs):
        self.calls.append(("music", url, kwargs))
        return {"code": 0, "data": {"code": 0}}

    async def play_by_url(self, device_id, url):
        self.calls.append(("url", url, {}))
        return {"code": 0, "data": {"code": 0}}


class TestPlayUrlMusicType:
    """触屏歌词开启时 play_by_music_url 需携带 _type=1 (audio_type=MUSIC)"""

    def _make(self, tmp_path, hardware, lyrics, compat=None):
        from app.engine.auth import AuthManager  # noqa: F401 -- 占位避免误用

        class RecAuth:
            def __init__(self, mina):
                self.mina_service = mina

            async def ensure_login(self):
                return None

            async def call_api(self, factory, label=""):
                return await factory()

        mina = RecorderMina()
        speaker = Speaker(
            did="123", device_id="dev-1", hardware=hardware,
            compatibility_mode=compat,
        )
        config = Config(conf_path=str(tmp_path))
        config.touchscreen_lyrics = lyrics
        controller = SpeakerController(speaker, auth=RecAuth(mina), config=config)
        return controller, mina

    def test_lyrics_on_sends_music_type(self, tmp_path):
        """歌词开启: _type=1 (audio_type=MUSIC), audio_id 透传"""
        controller, mina = self._make(tmp_path, "X08C", lyrics=True)
        ok = asyncio.run(controller.play_url("http://x/stream.mp3", "999"))
        assert ok is True
        kind, url, kwargs = mina.calls[0]
        assert kind == "music"
        assert kwargs["_type"] == 1
        assert kwargs["audio_id"] == "999"

    def test_lyrics_off_keeps_default_type(self, tmp_path):
        """歌词关闭: 不带 _type, 保持既有行为 (audio_type 为空)"""
        controller, mina = self._make(tmp_path, "X08C", lyrics=False)
        asyncio.run(controller.play_url("http://x/stream.mp3"))
        kind, _, kwargs = mina.calls[0]
        assert kind == "music"
        assert "_type" not in kwargs

    def test_compat_mode_uses_play_by_url(self, tmp_path):
        """显式兼容模式: 走 play_by_url, 不涉 _type"""
        controller, mina = self._make(tmp_path, "X08C", lyrics=True, compat=True)
        asyncio.run(controller.play_url("http://x/stream.mp3"))
        kind, _, kwargs = mina.calls[0]
        assert kind == "url"


class TestFailureCounter:
    def test_success_resets_counter(self, tmp_path):
        """获取状态成功后必须重置实例级连续失败计数

        回归: 曾误写成 SpeakerController._consecutive_login_failures = 0
        (从未被读取的类属性), 实例计数只增不减, 长期运行中偶发失败累积
        会触发毫无必要的自动重启。
        """
        controller = _make_controller(tmp_path)
        controller.consecutive_login_failures = 5

        async def _main():
            status = await controller.get_status()
            return status, controller.consecutive_login_failures

        status, failures = asyncio.run(_main())
        assert status == {"status": 1, "volume": 30}
        assert failures == 0

    def test_login_failure_accumulates_below_threshold(self, tmp_path):
        """登录失效类错误累计实例计数 (未达重启阈值), 并向调用方抛出"""
        controller = _make_controller(tmp_path, fail=True)

        async def _main():
            for _ in range(2):
                with pytest.raises(Exception, match="get_status 失败"):
                    await controller.get_status()
            return controller.consecutive_login_failures

        failures = asyncio.run(_main())
        assert failures == 2


class TestIdleStatus:
    """空闲设备只返回 data.code 没有 info —— 这是正常状态, 不是错误

    回归: 曾把缺少 info 当成 API 异常抛出, 导致在线但空闲的音箱被判离线,
    DLNA/AirPlay 服务被隐藏, Spotify 也一直等待"目标音箱上线"。
    """

    def test_idle_is_not_an_error(self, tmp_path):
        controller = _make_controller(tmp_path, idle=True)
        controller._last_volume = 42

        async def _main():
            return await controller.get_status()

        status = asyncio.run(_main())
        assert status == {"status": 0, "volume": 42, "idle": True}

    def test_idle_keeps_last_volume(self, tmp_path):
        controller = _make_controller(tmp_path, idle=True)
        controller._last_volume = 37

        async def _main():
            return await controller.get_volume()

        assert asyncio.run(_main()) == 37
