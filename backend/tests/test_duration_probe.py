"""曲长嗅探与元数据缺失 duration 兜底行为测试 (issue #27)

覆盖:
- duration.py: WAV/FLAC 头解析、PyAV 主路径、垃圾数据回退、异步包装
- renderer: SetAVTransportURI 缺时长时异步嗅探回填、带时长时不嗅探、
  过期结果丢弃、seek 嗅探兜底、elapsed clamp、未知时长超限模拟播完
- device_server: probe_duration 从缓冲嗅探、create_seek_url duration<=0 兜底
"""

import asyncio
import struct
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.engine.dlna import renderer as renderer_mod
from app.engine.dlna.device_server import DeviceServer
from app.engine.dlna.duration import (
    _probe_wav_flac_header,
    _probe_with_av,
    probe_duration,
    probe_duration_sync,
)
from app.engine.dlna.media_buffer import MediaBuffer
from app.engine.dlna.renderer import DLNARenderer

# ---------- 测试数据构造 ----------

def _make_wav(duration_s: float, sample_rate: int = 44100, channels: int = 2, bits: int = 16) -> bytes:
    block_align = channels * (bits // 8)
    byte_rate = sample_rate * block_align
    num_samples = int(duration_s * sample_rate)
    audio = bytearray(num_samples * block_align)
    fmt = struct.pack("<HHIIHH", 1, channels, sample_rate, byte_rate, block_align, bits)
    fmt_chunk = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    data_chunk = b"data" + struct.pack("<I", len(audio)) + bytes(audio)
    riff_size = 4 + len(fmt_chunk) + len(data_chunk)
    return b"RIFF" + struct.pack("<I", riff_size) + b"WAVE" + fmt_chunk + data_chunk


def _make_flac(total_samples: int, sample_rate: int = 44100) -> bytes:
    streaminfo = bytearray(34)
    streaminfo[0:2] = (4096).to_bytes(2, "big")
    streaminfo[2:4] = (4096).to_bytes(2, "big")
    val = (sample_rate << 12) | (1 << 9) | (15 << 4)
    streaminfo[10:14] = val.to_bytes(4, "big")
    streaminfo[14:18] = total_samples.to_bytes(4, "big")
    return b"fLaC" + bytes([0x80, 0x00, 0x00, 0x22]) + bytes(streaminfo) + b"\x00" * 64


_META_WITHOUT_DURATION = (
    '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
    "<item id=\"1\" parentID=\"0\" restricted=\"0\">"
    "<dc:title>song</dc:title><upnp:artist>artist</upnp:artist>"
    '<res protocolInfo="http-get:*:audio/mpeg:*">http://example.com/a.mp3</res>'
    "</item></DIDL-Lite>"
)

_META_WITH_DURATION = _META_WITHOUT_DURATION.replace(
    '<res protocolInfo="http-get:*:audio/mpeg:*">',
    '<res duration="0:03:30" protocolInfo="http-get:*:audio/mpeg:*">',
)


def _make_renderer() -> DLNARenderer:
    speaker = MagicMock()
    speaker.did = "test-did"
    speaker.speaker = None
    speaker.play_url = AsyncMock(return_value=True)
    speaker.stop = AsyncMock(return_value=True)
    speaker.pause = AsyncMock(return_value=True)
    speaker.set_volume = AsyncMock(return_value=True)
    speaker.get_volume = AsyncMock(return_value=50)
    speaker.search_audio_id = AsyncMock(return_value="")
    config = MagicMock()
    config.default_cover_url = ""
    config.hostname = "127.0.0.1"
    config.dlna_port = 8200
    config.touchscreen_lyrics = False
    config.follow_device_volume = False
    return DLNARenderer("uuid:test", "测试音箱", speaker, config=config)


# ---------- duration 探测 ----------

def test_probe_wav_duration():
    wav = _make_wav(10.0)
    assert abs(_probe_wav_flac_header(wav) - 10.0) < 0.01
    assert abs(probe_duration_sync(wav) - 10.0) < 0.1


def test_probe_flac_duration():
    flac = _make_flac(total_samples=44100 * 30)
    assert abs(_probe_wav_flac_header(flac) - 30.0) < 0.01
    assert abs(probe_duration_sync(flac) - 30.0) < 0.1


def test_probe_with_av_path():
    pytest.importorskip("av")
    wav = _make_wav(7.5)
    assert abs(_probe_with_av(wav) - 7.5) < 0.1


def test_probe_garbage_returns_zero():
    assert probe_duration_sync(b"") == 0.0
    assert probe_duration_sync(b"\x00" * 1000) == 0.0
    assert probe_duration_sync(b"ID3" + b"\x00" * 5000) == 0.0
    assert probe_duration_sync(b"\x30\x26\xb2\x75" + b"\x00" * 5000) == 0.0


def test_probe_duration_async_wrapper():
    assert abs(asyncio.run(probe_duration(_make_wav(3.0))) - 3.0) < 0.1


def test_probe_truncates_large_input():
    """超大输入仅取头部探测, 不报错"""
    big = _make_wav(6000.0) + b"\x00" * (3 * 1024 * 1024)
    assert probe_duration_sync(big) > 0


# ---------- renderer: 嗅探回填 ----------

def test_set_uri_without_duration_probes_and_backfills():
    async def run():
        r = _make_renderer()
        r.probe_duration_func = AsyncMock(return_value=180.0)
        await r.set_av_transport_uri("http://example.com/a.mp3", _META_WITHOUT_DURATION)
        assert r._track_duration == 0.0  # SOAP 响应不被阻塞, 先保持 0
        await asyncio.sleep(0.1)  # 等待嗅探任务完成
        assert r._track_duration == 180.0
        r.probe_duration_func.assert_awaited_once_with("http://example.com/a.mp3")

    asyncio.run(run())


def test_set_uri_with_duration_skips_probe():
    async def run():
        r = _make_renderer()
        r.probe_duration_func = AsyncMock(return_value=999.0)
        await r.set_av_transport_uri("http://example.com/a.mp3", _META_WITH_DURATION)
        await asyncio.sleep(0.05)
        assert r._track_duration == 210.0
        r.probe_duration_func.assert_not_awaited()

    asyncio.run(run())


def test_stale_probe_result_discarded_on_uri_change():
    async def run():
        r = _make_renderer()

        async def slow_probe(uri: str) -> float:
            await asyncio.sleep(0.15)
            return 999.0

        r.probe_duration_func = slow_probe
        await r.set_av_transport_uri("http://example.com/a.mp3", _META_WITHOUT_DURATION)
        # 立即切到带 duration 的新曲
        await r.set_av_transport_uri("http://example.com/b.mp3", _META_WITH_DURATION)
        await asyncio.sleep(0.3)
        assert r._track_duration == 210.0  # 未被过期嗅探结果覆盖

    asyncio.run(run())


def test_seek_probes_duration_when_missing():
    async def run():
        r = _make_renderer()
        await r.set_av_transport_uri("http://example.com/a.mp3", _META_WITHOUT_DURATION)
        r.probe_duration_func = AsyncMock(return_value=100.0)
        r.seek_url_func = AsyncMock(return_value="http://host/media/tok")
        r.transport_state = renderer_mod.TRANSPORT_STATE_PLAYING
        r._play_start_time = time.time()

        ok = await r.seek("REL_TIME", "0:01:30")
        assert ok is True
        r.probe_duration_func.assert_awaited_once_with("http://example.com/a.mp3")
        r.seek_url_func.assert_awaited_once()
        args = r.seek_url_func.await_args.args
        assert args[1] == pytest.approx(90.0)
        assert args[2] == pytest.approx(100.0)
        assert r._track_duration == pytest.approx(100.0)

    asyncio.run(run())


def test_seek_stays_soft_when_probe_fails():
    async def run():
        r = _make_renderer()
        await r.set_av_transport_uri("http://example.com/a.mp3", _META_WITHOUT_DURATION)
        r.probe_duration_func = AsyncMock(return_value=0.0)
        r.seek_url_func = AsyncMock(return_value="http://host/media/tok")
        r.transport_state = renderer_mod.TRANSPORT_STATE_PLAYING
        r._play_start_time = time.time()

        ok = await r.seek("REL_TIME", "0:01:00")
        assert ok is True
        r.seek_url_func.assert_not_awaited()  # 嗅探失败, 保持软 Seek
        assert r._accumulated_time == pytest.approx(60.0)

    asyncio.run(run())


# ---------- renderer: 位置与切歌兜底 ----------

def test_get_elapsed_time_clamped_to_duration():
    r = _make_renderer()
    r._track_duration = 60.0
    r._accumulated_time = 50.0
    r._play_start_time = time.time() - 100
    r.transport_state = renderer_mod.TRANSPORT_STATE_PLAYING
    assert r._get_elapsed_time() == 60.0


def test_get_elapsed_time_unclamped_when_duration_unknown():
    r = _make_renderer()
    r._track_duration = 0.0
    r._accumulated_time = 50.0
    r._play_start_time = time.time() - 10
    r.transport_state = renderer_mod.TRANSPORT_STATE_PLAYING
    assert abs(r._get_elapsed_time() - 60.0) < 0.5


def test_check_play_status_triggers_next_when_duration_unknown(monkeypatch):
    monkeypatch.setattr(renderer_mod, "_UNKNOWN_DURATION_PLAY_LIMIT", 1.0)

    async def run():
        r = _make_renderer()
        r.transport_state = renderer_mod.TRANSPORT_STATE_PLAYING
        r.current_uri = "http://example.com/a.mp3"
        r._track_duration = 0.0
        r._accumulated_time = 5.0  # 已超过 1.0s 上限
        called = []

        async def fake_next(*, automatic=False):
            called.append(automatic)

        r.next_track = fake_next
        task = asyncio.create_task(r._check_play_status())
        await asyncio.sleep(1.6)
        task.cancel()
        assert called == [True], "曲长未知且超限时应触发自动 next_track 模拟自然播完"

    asyncio.run(run())


def test_seek_hard_path_restarts_play_check_task():
    """回归: 硬 Seek 成功后必须重启播放状态检查任务, 否则 seek 后无法自动切歌"""

    async def run():
        r = _make_renderer()
        await r.set_av_transport_uri("http://example.com/a.mp3", _META_WITH_DURATION)
        r.seek_url_func = AsyncMock(return_value="http://host/media/tok")
        r.transport_state = renderer_mod.TRANSPORT_STATE_PLAYING
        r._play_start_time = time.time()

        ok = await r.seek("REL_TIME", "0:01:30")
        assert ok is True
        assert r._play_check_task is not None
        assert not r._play_check_task.done()
        r._play_check_task.cancel()

    asyncio.run(run())


def test_seek_keeps_paused_without_check_task():
    async def run():
        r = _make_renderer()
        await r.set_av_transport_uri("http://example.com/a.mp3", _META_WITH_DURATION)
        r.seek_url_func = AsyncMock(return_value="http://host/media/tok")
        r.transport_state = renderer_mod.TRANSPORT_STATE_PAUSED
        r._play_start_time = 0.0

        ok = await r.seek("REL_TIME", "0:01:30")
        assert ok is True
        assert r.transport_state == renderer_mod.TRANSPORT_STATE_PAUSED
        assert r._play_check_task is None  # 暂停态不启动检查任务

    asyncio.run(run())


def test_check_play_status_triggers_next_near_end():
    async def run():
        r = _make_renderer()
        r.transport_state = renderer_mod.TRANSPORT_STATE_PLAYING
        r.current_uri = "http://example.com/a.mp3"
        r._track_duration = 5.0
        r._accumulated_time = 4.9
        r._play_start_time = 0.0
        called = []

        async def fake_next(*, automatic=False):
            called.append(automatic)

        r.next_track = fake_next
        task = asyncio.create_task(r._check_play_status())
        await asyncio.sleep(2.2)
        task.cancel()
        assert called == [True], "接近曲尾时应触发自动 next_track (回归)"

    asyncio.run(run())


# ---------- device_server: 缓冲嗅探与 seek 兜底 ----------

def _completed_buffer(remote_url: str, data: bytes) -> MediaBuffer:
    buf = MediaBuffer(remote_url)
    buf.data = bytearray(data)
    buf.total_size = len(data)
    buf.download_complete = True
    buf._complete_event.set()
    buf._headers_event.set()
    return buf


def test_device_server_probe_duration():
    async def run():
        ds = DeviceServer("127.0.0.1", 8200)
        url = "http://example.com/a.wav"
        buf = _completed_buffer(url, _make_wav(10.0))
        ds._url_to_buffer[url] = "bid"
        ds._media_buffers["bid"] = buf

        duration = await ds.probe_duration(url)
        assert abs(duration - 10.0) < 0.5
        assert await ds.probe_duration("http://example.com/unknown") == 0.0

    asyncio.run(run())


def test_create_seek_url_probes_when_duration_missing():
    async def run():
        ds = DeviceServer("127.0.0.1", 8200)
        url = "http://example.com/a.wav"
        buf = _completed_buffer(url, _make_wav(10.0))
        ds._url_to_buffer[url] = "bid"
        ds._media_buffers["bid"] = buf

        # duration=0 (元数据缺失): 应从缓冲嗅探并成功生成 seek URL
        seek_url = await ds.create_seek_url(url, 5.0, 0.0)
        assert seek_url is not None
        assert seek_url.startswith("http://127.0.0.1:8200/media/")

    asyncio.run(run())


def test_create_seek_url_returns_none_when_probe_fails():
    async def run():
        ds = DeviceServer("127.0.0.1", 8200)
        url = "http://example.com/a.bin"
        buf = _completed_buffer(url, b"\x00" * (4 * 1024 * 1024))
        ds._url_to_buffer[url] = "bid"
        ds._media_buffers["bid"] = buf

        assert await ds.create_seek_url(url, 5.0, 0.0) is None

    asyncio.run(run())
