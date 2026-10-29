import asyncio
import io
import threading
import time
from types import SimpleNamespace

import aiohttp
import pytest

from app.engine import audio_stream
from app.engine.airplay.shairport_server import ShairportSyncServer
from app.engine.airplay.timed_audio import HEADER, MAGIC, TimedPCM


def record(data, pts):
    return HEADER.pack(MAGIC, len(data) // 4, 0, 1, pts, 1) + data


def receiver(monkeypatch):
    monkeypatch.setenv("MIAIR_AIRPLAY2_TIMED_STDOUT", "1")
    server = ShairportSyncServer("127.0.0.1", output_latency_ms=1400)
    server._running = True
    starts, stops = [], []
    server.on_play_start = starts.append
    server.on_play_stop = lambda: stops.append(True)
    return server, starts, stops


@pytest.mark.parametrize("shift", [130_000_000, -130_000_000])
def test_pcm_anchor_step_rebuilds_downstream_only(monkeypatch, shift):
    server, starts, stops = receiver(monkeypatch)
    pcm = record(b"old!" * 480, 10_000_000_000)
    pcm += record(b"old!" * 480, 10_010_000_000)
    pcm += record(b"new!" * 480, 10_020_000_000 + shift)
    pcm += record(b"next" * 480, 10_030_000_000 + shift)
    process = SimpleNamespace(stdout=io.BytesIO(pcm), poll=lambda: None)
    server._read_timed_audio(process)
    assert len(starts) == 2 and starts[0] != starts[1]
    assert stops == [True]
    assert server._running and server._play_allowed and server.is_playing
    assert server.output_latency_ms == 1400
    assert server._stream_server._audio_queue.get_nowait().data == b"new!" * 480
    assert server._stream_server._audio_queue.get_nowait().data == b"next" * 480


def test_contiguous_pts_and_tiny_jitter_do_not_rebuild(monkeypatch):
    server, starts, stops = receiver(monkeypatch)
    pcm = b"".join(record(b"fine" * 480, 10_000_000_000 + i * 10_000_000 + (i % 2) * 10_000)
                   for i in range(50))
    server._read_timed_audio(SimpleNamespace(stdout=io.BytesIO(pcm), poll=lambda: None))
    assert len(starts) == 1 and not stops


def test_obsolete_http_generation_cannot_retire_new_session(monkeypatch):
    server, starts, stops = receiver(monkeypatch)
    server._begin_session()
    generation = server._stream_server._generation
    assert not server._on_stream_resync(generation - 1, "obsolete-write-timeout")
    assert len(starts) == 1 and not stops
    assert server._on_stream_resync(generation, "current-write-timeout")
    assert len(starts) == 2 and len(stops) == 1
    assert not server._on_stream_resync(generation, "old-handler-finally")
    assert not server._on_stream_resync(server._stream_server._generation, "rate-limit")


def test_resync_cooldown_expires_and_pause_blocks_recovery(monkeypatch):
    server, starts, stops = receiver(monkeypatch)
    now = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    server._begin_session()
    assert server._on_stream_resync(server._stream_server._generation, "first")
    now[0] += 3.1
    assert server._on_stream_resync(server._stream_server._generation, "persistent")
    server._play_allowed = False
    now[0] += 10
    assert not server._on_stream_resync(server._stream_server._generation, "paused")
    assert len(starts) == 3 and len(stops) == 2


def test_contiguous_pcm_after_delivery_stall_reanchors(monkeypatch):
    now = [10_000_000_000]
    monkeypatch.setattr(audio_stream, "clock_ns", lambda: now[0])
    stream = audio_stream.AudioStreamServer("127.0.0.1", isolate_sessions=True, output_latency_ms=1400)
    stream.set_audio_params(48000, 2, 2)
    stream.start_streaming()
    stream.write_timed_pcm(b"old!" * 480, 11_400_000_000)
    stream.write_timed_pcm(b"new!" * 480, 11_410_000_000)
    abort = threading.Event()
    recoveries = []

    def recover(generation, reason, seed):
        recoveries.append((reason, seed))
        abort.set()
        return True

    class Response:
        async def write(self, data):
            now[0] += 210_000_000

    stream.on_clock_resync = recover
    assert asyncio.run(stream._send_timed_wav(Response(), abort, stream._generation,
                                              stream._audio_queue, time.monotonic())) == 480 * 4
    assert recoveries == [("wav-send-late:200.0ms", TimedPCM(b"new!" * 480, 11_410_000_000))]


def test_http_failure_during_cooldown_is_deferred_not_lost(monkeypatch):
    server, starts, stops = receiver(monkeypatch)
    now = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    timers = []

    class Timer:
        def __init__(self, delay, callback, args):
            self.delay, self.callback, self.args = delay, callback, args
            timers.append(self)

        def start(self):
            pass

        def cancel(self):
            pass

    monkeypatch.setattr(threading, "Timer", Timer)
    server._begin_session()
    assert server._on_stream_resync(server._stream_server._generation, "first")
    now[0] += 1
    assert not server._on_stream_resync(server._stream_server._generation, "http-disconnected-or-write-timeout")
    assert len(timers) == 1 and timers[0].delay == pytest.approx(2.01)
    now[0] += 2.01
    timers[0].callback(*timers[0].args)
    assert len(starts) == 3 and len(stops) == 2
    assert server._clock_resync_timer is None


def test_http_write_timeout_rebuilds_stream_without_sender_disconnect(monkeypatch):
    now = [10_000_000_000]
    monkeypatch.setattr(audio_stream, "clock_ns", lambda: now[0])

    async def scenario():
        server, starts, stops = receiver(monkeypatch)
        stream = server._stream_server
        server._begin_session()
        generation = stream._generation

        class Response:
            async def prepare(self, request):
                pass

            async def write(self, data):
                raise asyncio.TimeoutError

        monkeypatch.setattr(audio_stream.web, "StreamResponse", lambda **kw: Response())
        request = SimpleNamespace(transport=None, query={"sid": str(stream._session_id)},
                                  method="GET", remote="127.0.0.1")
        await stream._handle_isolated_wav(request)
        assert stream._generation != generation
        assert len(starts) == 2 and len(stops) == 1
        assert server._running and server._play_allowed
        assert stream._client_count == 0

    asyncio.run(scenario())


def test_small_wav_pts_gaps_cannot_accumulate_unnoticed(monkeypatch):
    now = [10_000_000_000]
    monkeypatch.setattr(audio_stream, "clock_ns", lambda: now[0])
    stream = audio_stream.AudioStreamServer("127.0.0.1", isolate_sessions=True, output_latency_ms=1400)
    stream.set_audio_params(48000, 2, 2)
    stream.start_streaming()
    for i in range(4):
        stream.write_timed_pcm(b"abcd" * 480, now[0] + 1_400_000_000 + i * 50_000_000)
    abort = threading.Event()
    recoveries = []

    def recover(generation, reason, seed):
        recoveries.append((generation, reason, seed))
        abort.set()
        return True

    class Response:
        async def write(self, data):
            now[0] += 50_000_000

    stream.on_clock_resync = recover
    assert asyncio.run(stream._send_timed_wav(Response(), abort, stream._generation,
                                              stream._audio_queue, time.monotonic())) == 3 * 480 * 4
    assert len(recoveries) == 1
    assert recoveries[0][1] == "wav-timeline-shift:120.0ms"
    assert recoveries[0][2] == TimedPCM(b"abcd" * 480, 11_550_000_000)


def test_http_recovery_aborts_old_url_and_seeds_new_stream(monkeypatch):
    now = [10_000_000_000]
    monkeypatch.setattr(audio_stream, "clock_ns", lambda: now[0])

    async def scenario():
        server, starts, stops = receiver(monkeypatch)
        stream = server._stream_server
        await stream.start()
        try:
            server._begin_session()
            old_url = stream.stream_url
            stream.write_timed_pcm(b"old!" * 480, now[0] + 1_400_000_000)
            async with aiohttp.ClientSession() as client:
                old = await client.get(old_url)
                await old.content.readexactly(44)
                assert await old.content.readexactly(480 * 4) == b"old!" * 480
                stream.write_timed_pcm(b"new!" * 480, now[0] + 1_600_000_000)
                now[0] += 200_000_000
                try:
                    await asyncio.wait_for(old.content.read(), 1)
                except (aiohttp.ClientPayloadError, aiohttp.ClientConnectionError):
                    pass
                assert len(starts) == 2 and len(stops) == 1
                assert stream.stream_url != old_url
                async with client.get(old_url) as retired:
                    assert retired.status == 410
                async with client.get(stream.stream_url) as fresh:
                    await fresh.content.readexactly(44)
                    assert await asyncio.wait_for(fresh.content.readexactly(480 * 4), 1) == b"new!" * 480
                    stream.stop_streaming()
        finally:
            await stream.stop()

    asyncio.run(scenario())
