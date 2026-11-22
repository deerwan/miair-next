"""AirPlay 音频流格式决策与兼容模式覆盖测试

背景: 触屏型号 (music-api 白名单) 的 ROM 播放器不接受无限 WAV 流,
拉流正常但解码失败 (无声 + 「播放服务出现错误」), 故 auto 模式下
白名单型号改走 ffmpeg MP3 转码; 并允许用户显式设置的兼容模式
覆盖型号白名单, 手动改走 play_by_url 兜底。
"""

import asyncio

import pytest

import app.engine.airplay  # noqa: F401 -- 先初始化 airplay 包, 单独运行本文件时避免 audio_stream 循环导入
from app.engine.audio_stream import AudioStreamServer
from app.engine.config import Config, Speaker, resolve_airplay_stream_format


def _has_ffmpeg() -> bool:
    import shutil
    return shutil.which("ffmpeg") is not None


class TestResolveStreamFormat:
    def test_auto_whitelist_model_uses_mp3(self):
        """auto: 白名单型号 (触屏等) 用 MP3 转码"""
        config = Config(conf_path="/tmp/miair-test-fmt")
        for hw in ("X08C", "X08E", "X4B", "LX06", "L05B"):
            assert resolve_airplay_stream_format(config, hw) == "mp3"

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

    def test_config_default_is_auto(self):
        """默认 auto, 支持环境变量覆盖"""
        config = Config(conf_path="/tmp/miair-test-fmt")
        assert config.airplay_stream_format == "auto"


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
