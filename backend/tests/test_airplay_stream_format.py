"""AirPlay 音频流格式决策与兼容模式覆盖测试

背景: 带屏型号 (触屏音箱 / 智能家庭屏) 的 ROM
播放器不接受无限 WAV 流, 拉流正常但解码失败 (无声 + 「播放服务出现错误」),
故 auto 模式下这些型号改走 ffmpeg MP3 转码。MP3 集必须保持为带屏型号子集而
不能直接用 music-api 白名单: 白名单里的非触屏型号 (OH2 / LX06 / L16A ...)
收到 .mp3 流地址不会来拉流 (MiNA 回执 code 0 却无声)。允许用户显式设置的
兼容模式覆盖型号白名单, 手动改走 play_by_url 兜底。
"""

import asyncio

import pytest

import app.engine.airplay  # noqa: F401 -- 先初始化 airplay 包, 单独运行本文件时避免 audio_stream 循环导入
from app.engine.audio_stream import AudioStreamServer
from app.engine.config import Config, Speaker, resolve_airplay_stream_format
from app.engine.const import AIRPLAY_MP3_HARDWARE, NEED_USE_PLAY_MUSIC_API


def _has_ffmpeg() -> bool:
    import shutil
    return shutil.which("ffmpeg") is not None


class TestResolveStreamFormat:
    def test_auto_touchscreen_model_uses_mp3(self):
        """auto: 带屏型号 (ROM 不接受无限 WAV) 用 MP3 转码"""
        config = Config(conf_path="/tmp/miair-test-fmt")
        for hw in ("X08C", "X08E", "X8F", "X08A", "X10A", "OH11", "X6A", "X4B", "L04M", "X8S", "OH27", "OH8", "lx04"):
            assert resolve_airplay_stream_format(config, hw) == "mp3"

    def test_mp3_set_is_subset_of_music_api_whitelist(self):
        """MP3 型号集必须是 music-api 白名单的子集 (两者语义不同, 不能互相替代)"""
        assert AIRPLAY_MP3_HARDWARE <= set(NEED_USE_PLAY_MUSIC_API)
        # 白名单里存在非触屏型号, 这正是不能直接套用白名单的原因
        assert len(set(NEED_USE_PLAY_MUSIC_API) - AIRPLAY_MP3_HARDWARE) >= 10

    def test_auto_non_touch_whitelist_model_uses_wav(self):
        """auto: 白名单里的非触屏型号保持 WAV (回归: 收到 .mp3 地址不会来拉流, 切 wav 即恢复)

        OH2 = Xiaomi 智能音箱, 即本次回归报告的型号。
        """
        config = Config(conf_path="/tmp/miair-test-fmt")
        for hw in ("OH2", "OH2P", "LX05", "LX06", "L06A", "L05B", "L05C", "L15A", "L16A", "L17A"):
            assert hw in NEED_USE_PLAY_MUSIC_API
            assert resolve_airplay_stream_format(config, hw) == "wav"

    def test_official_hardware_codes_are_covered(self):
        """miot-spec 官方码与实机上报的历史写法并存, 三组别名都要能命中白名单且保持 WAV (回归: 收到 .mp3 不会拉流)"""
        config = Config(conf_path="/tmp/miair-test-fmt")
        for legacy, official in (("LX05A", "LX5A"), ("L07A", "L7A"), ("L16B", "16B")):
            for hw in (legacy, official):
                assert hw in NEED_USE_PLAY_MUSIC_API, hw
                assert resolve_airplay_stream_format(config, hw) == "wav", hw
                assert resolve_airplay_stream_format(config, hw.lower()) == "wav", hw

    def test_auto_other_model_uses_wav(self):
        """auto: 非白名单型号保持 WAV 零编码延迟"""
        config = Config(conf_path="/tmp/miair-test-fmt")
        for hw in ("L99X", "LX01", ""):
            assert resolve_airplay_stream_format(config, hw) == "wav"

    def test_explicit_format_overrides_auto(self):
        """显式 wav/mp3 无条件生效 (排查兼容问题时手动切换)"""
        config = Config(conf_path="/tmp/miair-test-fmt")
        config.airplay_stream_format = "wav"
        assert resolve_airplay_stream_format(config, "X08C") == "wav"
        config.airplay_stream_format = "mp3"
        assert resolve_airplay_stream_format(config, "LX06") == "mp3"

    def test_invalid_config_value_falls_back_to_auto(self):
        """非法配置值回退 auto"""
        config = Config(conf_path="/tmp/miair-test-fmt")
        config.airplay_stream_format = "flac"
        assert resolve_airplay_stream_format(config, "L99X") == "wav"
        assert resolve_airplay_stream_format(config, "X08C") == "mp3"
        assert resolve_airplay_stream_format(config, "LX06") == "wav"

    def test_config_default_is_auto(self):
        """默认 auto, 支持环境变量覆盖"""
        config = Config(conf_path="/tmp/miair-test-fmt")
        assert config.airplay_stream_format == "auto"

    def test_audio_stream_server_exposes_format(self):
        """流服务器对外暴露当前格式, 供无拉流告警输出"""
        assert AudioStreamServer("192.168.1.1", audio_format="mp3").audio_format == "mp3"
        assert AudioStreamServer("192.168.1.1", audio_format="wav").audio_format == "wav"


class TestAudioStreamServerMp3:
    def test_mp3_route_and_url(self):
        """audio_format=mp3 时路由与 URL 均为 stream.mp3"""
        server = AudioStreamServer("192.168.1.1", audio_format="mp3")
        assert "/airplay/stream.mp3?sid=" in server.stream_url
        assert server.stream_url.startswith("http://192.168.1.1:")
        # WAV 模式不受影响
        wav = AudioStreamServer("192.168.1.1", audio_format="wav")
        assert "/airplay/stream.wav?sid=" in wav.stream_url
        assert "/stream.mp3" not in wav.stream_url

    @pytest.mark.skipif(not _has_ffmpeg(), reason="本地无 ffmpeg")
    def test_mp3_stream_transcodes_pcm(self, tmp_path):
        """端到端: PCM 边喂边拉 -> 经 ffmpeg 转码得到合法 MP3 字节"""
        import math

        import aiohttp

        async def run():
            server = AudioStreamServer("127.0.0.1", audio_format="mp3")
            await server.start()
            server.start_streaming()
            # 1 秒 440Hz 正弦 PCM (s16le 44.1k 双声道)
            pcm = bytearray()
            for i in range(44100):
                v = int(12000 * math.sin(2 * math.pi * 440 * i / 44100))
                pcm += v.to_bytes(2, "little", signed=True) * 2

            async with aiohttp.ClientSession() as session:
                async with session.get(server.stream_url) as resp:
                    assert resp.status == 200
                    assert resp.headers["Content-Type"] == "audio/mpeg"
                    received = bytearray()

                    async def pull():
                        while True:
                            chunk = await resp.content.read(4096)
                            if not chunk:
                                break
                            received.extend(chunk)

                    pull_task = asyncio.create_task(pull())
                    for i in range(0, len(pcm), 4096):
                        server.write_pcm(bytes(pcm[i:i + 4096]))
                        await asyncio.sleep(0.005)
                    # 置 None 结束 PCM -> ffmpeg stdin 关闭 -> 输出 EOF
                    server.stop_streaming()
                    await asyncio.wait_for(pull_task, timeout=10)
            await server.stop()

            # MP3 帧头: ID3 标签或 0xFF Ex 同步字
            data = bytes(received)
            assert data[:3] == b"ID3" or (data[0] == 0xFF and (data[1] & 0xE0) == 0xE0), \
                f"非 MP3 数据: {data[:8]!r}"
            assert len(data) > 1000

        asyncio.run(run())


class TestCompatModeOverride:
    """兼容模式显式设置优先于型号白名单"""

    def _controller(self, tmp_path, hardware: str, compatibility_mode):
        from app.engine.speaker import SpeakerController

        class FakeAuth:
            mina_service = None

            async def ensure_login(self):
                return None

        speaker = Speaker(
            did="123", device_id="dev-1", hardware=hardware,
            compatibility_mode=compatibility_mode,
        )
        config = Config(conf_path=str(tmp_path))
        return SpeakerController(speaker, auth=FakeAuth(), config=config)

    def test_whitelist_default_music_api(self, tmp_path):
        """白名单型号未显式设置 -> music api (封面/触屏歌词生效)"""
        c = self._controller(tmp_path, "X08C", None)
        assert c._should_use_music_api() is True

    def test_explicit_compat_overrides_whitelist(self, tmp_path):
        """白名单型号显式开启兼容模式 -> play_by_url 兜底"""
        c = self._controller(tmp_path, "X08C", True)
        assert c._should_use_music_api() is False

    def test_explicit_not_compat_keeps_music_api(self, tmp_path):
        """白名单型号显式关闭兼容模式 -> 仍走 music api"""
        c = self._controller(tmp_path, "X08C", False)
        assert c._should_use_music_api() is True

    def test_non_whitelist_default_compat(self, tmp_path):
        """非白名单型号未显式设置 -> 维持原有默认 (兼容, play_by_url)"""
        c = self._controller(tmp_path, "L99X", None)
        assert c._should_use_music_api() is False
        c = self._controller(tmp_path, "", None)
        assert c._should_use_music_api() is False


class TestPullWatchdog:
    """下发播放指令后确认音箱确实来拉流 (MiNA code 0 只代表指令已转发)"""

    def _wrapper(self, monkeypatch, *, hardware="LX06", airplay2=False, has_clients=False):
        from app.engine.airplay import speaker_airplay as mod

        monkeypatch.setattr(mod, "_PULL_WATCH_SECONDS", 0.01)

        class Controller:
            speaker = Speaker(name="Test", hardware=hardware)

            async def stop(self):
                pass

        class FakeServer:
            stream_has_clients = has_clients
            stream_audio_format = "mp3"

            async def stop(self):
                pass

        wrapper = mod.SpeakerAirPlay("127.0.0.1", Controller(), airplay2=airplay2)
        wrapper.airplay_server = FakeServer()
        return wrapper

    def test_no_pull_after_grace_logs_warning(self, monkeypatch, caplog):
        """宽限期后仍无客户端 -> 告警并带出格式与型号, 不再误报「已开始播放」"""
        import logging

        wrapper = self._wrapper(monkeypatch)

        async def scenario():
            wrapper._arm_pull_watchdog(wrapper._command_revision)
            assert wrapper._pull_watch_task is not None
            await wrapper._pull_watch_task

        with caplog.at_level(logging.WARNING, logger="miair"):
            asyncio.run(scenario())
        assert "未拉取音频流" in caplog.text
        assert "mp3" in caplog.text and "LX06" in caplog.text
        assert "开始播放" not in caplog.text

    def test_clients_present_no_warning(self, monkeypatch, caplog):
        """音箱已来拉流 -> 不告警"""
        import logging

        wrapper = self._wrapper(monkeypatch, has_clients=True)

        async def scenario():
            wrapper._arm_pull_watchdog(wrapper._command_revision)
            await wrapper._pull_watch_task

        with caplog.at_level(logging.WARNING, logger="miair"):
            asyncio.run(scenario())
        assert "未拉取音频流" not in caplog.text

    def test_airplay2_never_arms(self, monkeypatch):
        """AirPlay 2 不走 HTTP 拉流路线, 不启用看门狗 (无需事件循环)"""
        wrapper = self._wrapper(monkeypatch, airplay2=True)
        wrapper._arm_pull_watchdog(wrapper._command_revision)
        assert wrapper._pull_watch_task is None

    def test_redispatch_resets_and_stop_cancels(self, monkeypatch):
        """重新下发会取消旧计时; stop() 不遗留任务"""
        wrapper = self._wrapper(monkeypatch)

        async def scenario():
            wrapper._arm_pull_watchdog(wrapper._command_revision)
            first = wrapper._pull_watch_task
            wrapper._arm_pull_watchdog(wrapper._command_revision)
            assert wrapper._pull_watch_task is not first
            await asyncio.sleep(0)  # 让取消传递到旧任务
            assert first.cancelled()
            await wrapper.stop()
            assert wrapper._pull_watch_task is None

        asyncio.run(scenario())

    def test_stale_revision_is_silent(self, monkeypatch, caplog):
        """下发后又有新指令 (停止/切歌) -> 旧看门狗静默退出, 不误报"""
        import logging

        wrapper = self._wrapper(monkeypatch)

        async def scenario():
            wrapper._command_revision = 5
            wrapper._arm_pull_watchdog(5)
            wrapper._command_revision = 6  # 模拟后续 stop/切歌
            await wrapper._pull_watch_task

        with caplog.at_level(logging.WARNING, logger="miair"):
            asyncio.run(scenario())
        assert "未拉取音频流" not in caplog.text
