"""PTP-preserving native transport and HTTP scheduling regressions."""
import asyncio
import io
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import pytest

from app.engine import audio_stream
from app.engine.airplay.shairport_server import ShairportSyncServer
from app.engine.airplay.timed_audio import HEADER, MAGIC, TimedPCM, read_record
from app.engine.config import Config
from app.models.schemas import SettingsPayload


def packet(data=b"\x11\x00\x22\x00", pts=10_000_000_000, epoch=1, kind=1):
    return HEADER.pack(MAGIC, len(data) // 4, 0xffffffff, kind, pts, epoch) + data


def test_native_transport_survives_short_writes_and_eintr(tmp_path):
    compiler = shutil.which("cc")
    if not compiler:
        pytest.skip("C compiler unavailable")
    backend = Path(__file__).resolve().parents[1]
    executable = tmp_path / "stdout-test"
    subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-pthread",
                    "-I", str(backend / "native/shairport-sync"),
                    str(backend / "tests/fixtures/timed_stdout_harness.c"),
                    "-o", str(executable)], check=True, capture_output=True)
    result = subprocess.run([str(executable)], env={**os.environ, "MIAIR_TIMED_STDOUT": "1"},
                            check=True, capture_output=True)
    stream = io.BytesIO(result.stdout)
    start, first, second, flush, untimed, stop = [read_record(stream) for _ in range(6)]
    assert start == (2, 0, 0, 1, b"")
    assert first[:4] == (1, 0xffffffff, 10_000_000_000, 1)
    assert first[4] == b"\x53" * (4096 * 4)
    assert second[2] == 10_000_000_000 + 4096 * 1_000_000_000 // 48000
    assert len(second[4]) == (5000 - 4096) * 4
    assert flush == (2, 0, 0, 2, b"")
    assert untimed == (0, 0, 0, 2, b"\x53" * 16)
    assert stop == (2, 0, 0, 3, b"")
    assert read_record(stream) is None
    raw = subprocess.run([str(executable)], env={**os.environ, "MIAIR_TIMED_STDOUT": "0"},
                         check=True, capture_output=True)
    assert raw.stdout == b"\x53" * 8


def test_reader_handles_arbitrary_pipe_fragmentation():
    class Fragmented(io.BytesIO):
        def read(self, count=-1):
            return super().read(min(count, 3))
    assert read_record(Fragmented(packet())) == (1, 0xffffffff, 10_000_000_000, 1,
                                               b"\x11\x00\x22\x00")


@pytest.mark.parametrize("payload", [b"bad", packet()[:-1],
    HEADER.pack(b"oops", 1, 0, 1, 1, 1), HEADER.pack(MAGIC, 4097, 0, 1, 1, 1),
    HEADER.pack(MAGIC, 1, 0, 2, 0, 1), HEADER.pack(MAGIC, 1, 0, 4, 0, 1)])
def test_reader_rejects_corrupt_transport(payload):
    with pytest.raises(ValueError):
        read_record(io.BytesIO(payload))


def test_timestamp_trim_keeps_frame_alignment_and_updates_deadline():
    frame = TimedPCM(b"abcd" * 480, 1_000_000_000)
    trimmed, count = frame.trim_before(1_005_000_000)
    assert count == 240
    assert trimmed == TimedPCM(b"abcd" * 240, 1_005_000_000)
    assert frame.trim_before(1_010_000_000) == (None, 480)
    assert frame.trim_before(999_000_000) == (frame, 0)


def test_clock_config_uses_lookahead_not_double_latency_offset(monkeypatch, tmp_path):
    monkeypatch.setenv("MIAIR_AIRPLAY2_TIMED_STDOUT", "1")
    server = ShairportSyncServer("127.0.0.1", output_latency_ms=2400)
    config = server._make_config()
    assert "audio_backend_buffer_desired_length_in_seconds = 2.550;" in config
    assert "audio_decoded_buffer_desired_length_in_seconds = 2.750;" in config
    assert "audio_backend_latency_offset_in_seconds = 0.0;" in config
    Config(conf_path=str(tmp_path), airplay2_output_latency_ms=2400).save()
    assert Config.load(str(tmp_path)).airplay2_output_latency_ms == 2400
    for value in (-1, 4001):
        with pytest.raises(ValueError):
            SettingsPayload(airplay2_output_latency_ms=value)


def test_in_band_epoch_retires_old_timed_queue(monkeypatch):
    monkeypatch.setenv("MIAIR_AIRPLAY2_TIMED_STDOUT", "1")
    server = ShairportSyncServer("127.0.0.1")
    server._running = True
    starts = []
    server.on_play_start = starts.append
    old = packet(b"aaaa" * 100)
    boundary = HEADER.pack(MAGIC, 0, 0, 2, 0, 2)
    fresh = packet(b"bbbb" * 100, epoch=2)
    process = SimpleNamespace(stdout=io.BytesIO(old + boundary + fresh), poll=lambda: None)
    server._read_timed_audio(process)
    assert len(starts) == 2 and starts[0] != starts[1]
    assert server._stream_server._audio_queue.get_nowait().data == b"bbbb" * 100


def test_http_startup_catches_up_to_ptp_instead_of_playing_backlog(monkeypatch):
    now = [10_000_000_000]
    monkeypatch.setattr(audio_stream, "clock_ns", lambda: now[0])

    async def scenario():
        server = audio_stream.AudioStreamServer("127.0.0.1", isolate_sessions=True,
                                               output_latency_ms=1400)
        server.set_audio_params(48000, 2, 2)
        await server.start()
        try:
            server.start_streaming()
            server.write_timed_pcm(b"old!" * 480, now[0] + 1_000_000_000)
            server.write_timed_pcm(b"good" * 480, now[0] + 1_400_000_000)
            async with aiohttp.ClientSession() as client:
                async with client.get(server.stream_url) as response:
                    await response.content.readexactly(44)
                    assert await asyncio.wait_for(response.content.readexactly(480 * 4), 1) == b"good" * 480
                    server.write_timed_pcm(b"next" * 480, now[0] + 1_450_000_000)
                    read = asyncio.create_task(response.content.readexactly(480 * 4))
                    try:
                        await asyncio.wait_for(asyncio.shield(read), 0.03)
                        pytest.fail("future PCM was sent before its PTP deadline")
                    except asyncio.TimeoutError:
                        pass
                    now[0] += 50_000_000
                    assert await asyncio.wait_for(read, 1) == b"next" * 480
                    server.stop_streaming()
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_partial_startup_catchup_does_not_report_false_shortfall(monkeypatch, caplog):
    import threading
    import time
    now = 10_000_000_000
    monkeypatch.setattr(audio_stream, "clock_ns", lambda: now)
    server = audio_stream.AudioStreamServer("127.0.0.1", isolate_sessions=True,
                                           output_latency_ms=1400)
    server.set_audio_params(48000, 2, 2)
    server.start_streaming()
    server.write_timed_pcm(b"trim" * 4800, now + 1_390_000_000)
    server.write_timed_pcm(b"next" * 480, now + 1_490_000_000)
    abort = threading.Event()

    class Response:
        async def write(self, data):
            assert data == b"trim" * 4320
            abort.set()

    assert asyncio.run(server._send_timed_wav(Response(), abort, server._generation,
                                              server._audio_queue, time.monotonic())) == 4320 * 4
    assert "提前量不足" not in caplog.text


def test_insufficient_upstream_lead_does_not_silence_live_stream(monkeypatch, caplog):
    now = [10_000_000_000]
    monkeypatch.setattr(audio_stream, "clock_ns", lambda: now[0])

    async def scenario():
        server = audio_stream.AudioStreamServer("127.0.0.1", isolate_sessions=True,
                                               output_latency_ms=2400)
        server.set_audio_params(48000, 2, 2)
        await server.start()
        try:
            server.start_streaming()
            # Never replay expired PCM, but keep future PCM even if a realtime
            # sender cannot provide the full requested 2.4 seconds of lead.
            server.write_timed_pcm(b"old!" * 480, now[0] - 100_000_000)
            server.write_timed_pcm(b"live" * 480, now[0] + 800_000_000)
            async with aiohttp.ClientSession() as client:
                async with client.get(server.stream_url) as response:
                    await response.content.readexactly(44)
                    assert await asyncio.wait_for(response.content.readexactly(480 * 4), 1) == b"live" * 480
                    server.write_timed_pcm(b"next" * 480, now[0] + 810_000_000)
                    assert await asyncio.wait_for(response.content.readexactly(480 * 4), 1) == b"next" * 480
                    server.stop_streaming()
        finally:
            await server.stop()

    asyncio.run(scenario())
    assert "提前量不足" in caplog.text


def test_timed_pcm_fails_closed_without_clock_or_frame_alignment():
    server = audio_stream.AudioStreamServer("127.0.0.1", isolate_sessions=True)
    with pytest.raises(RuntimeError):
        server.write_timed_pcm(b"abcd", 1)
    timed = audio_stream.AudioStreamServer("127.0.0.1", isolate_sessions=True,
                                         output_latency_ms=1000)
    with pytest.raises(ValueError):
        timed.write_timed_pcm(b"abc", 1)
