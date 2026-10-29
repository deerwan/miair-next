"""Seek regression: real control messages, HTTP generations and MiNA ordering."""

import asyncio
from types import SimpleNamespace

import aiohttp
import pytest

from app.engine.airplay.shairport_server import ShairportSyncServer
from app.engine.airplay.speaker_airplay import SpeakerAirPlay
from app.engine.audio_stream import AudioStreamServer
from app.engine.config import Speaker

PAUSE = '0.0002 "rtsp.c:1481" Connection 1: SETRATEANCHORI Pause playing.'
RESUME = '0.0002 "rtsp.c:1470" Connection 1: SETRATEANCHORI Start playing, with anchor clock 5c1bf4b920640008.'
FLUSH = '0.0002 "rtsp.c:1351" Connection 1: immediate flush request created: flushUntilTS: 3403981999, flushUntilSeq: 7658032.'


def test_seek_invalidates_queue_and_starts_a_unique_url_on_resume():
    server = ShairportSyncServer("127.0.0.1")
    starts, stops = [], []
    server.on_play_start = starts.append
    server.on_play_stop = lambda: stops.append(True)
    server._begin_session()
    old_queue = server._stream_server._audio_queue
    server._stream_server.write_pcm(b"\x01\x00\x02\x00")
    server._on_shairport_message(PAUSE)
    server._on_shairport_message(FLUSH)
    assert not server.is_playing
    assert not server._play_allowed
    assert server._stream_server._audio_queue is not old_queue
    assert server._stream_server._audio_queue.empty()
    server._begin_session()  # stray stdout while paused cannot restart playback
    assert len(starts) == 1
    server._on_shairport_message(RESUME)
    server._begin_session()
    assert len(starts) == 2
    assert starts[0] != starts[1]
    assert stops == [True]


@pytest.mark.parametrize("line", [
    '0.01 "rtsp.c:1295" Connection 1: FLUSHBUFFERED rtsp://example : Content-Length 87',
    '0.01 "rtsp.c:1389" Connection 1: deferred flush request created: flushFromSeq: 12, flushUntilSeq: 32.',
    '<key>flushUntilSeq</key>',
    '0.01 "player.c:4909" play frame 155251552.',
])
def test_non_immediate_control_messages_do_not_reset_playback(line):
    server = ShairportSyncServer("127.0.0.1")
    server._begin_session()
    url = server._stream_server.stream_url
    server._on_shairport_message(line)
    assert server.is_playing
    assert server._stream_server.stream_url == url


def test_classic_flush_inside_shairport_allows_next_pcm_to_restart():
    server = ShairportSyncServer("127.0.0.1")
    server._begin_session()
    old_url = server._stream_server.stream_url
    server._on_shairport_message('0.01 "rtsp.c:2338" RTSP Flush Requested: 12345.')
    assert not server.is_playing
    assert server._play_allowed
    server._begin_session()
    assert server._stream_server.stream_url != old_url


def test_periodic_anchor_update_does_not_discard_current_pcm():
    server = ShairportSyncServer("127.0.0.1")
    server._begin_session()
    generation = server._timeline_generation
    url = server._stream_server.stream_url
    server._on_shairport_message(RESUME)
    assert server._timeline_generation == generation
    assert server._stream_server.stream_url == url


def test_wrapper_shutdown_waits_for_its_inflight_cloud_stop():
    async def scenario():
        release = asyncio.Event()
        started = asyncio.Event()

        class Controller:
            speaker = Speaker(name="Test")

            async def stop(self):
                started.set()
                await release.wait()

        wrapper = SpeakerAirPlay("127.0.0.1", Controller(), airplay2=True)
        wrapper._loop = asyncio.get_running_loop()
        wrapper._on_play_stop()
        await asyncio.wait_for(started.wait(), 1)
        shutdown = asyncio.create_task(wrapper.stop())
        await asyncio.sleep(0)
        assert not shutdown.done()
        release.set()
        await asyncio.wait_for(shutdown, 1)
        assert not wrapper._pending_commands

    asyncio.run(scenario())


def test_fragmented_pcm_straddling_seek_is_discarded_without_frame_shift():
    server = ShairportSyncServer("127.0.0.1")
    fragments = iter([
        (None, b"\x01"),
        (PAUSE, b"\x00"),
        (None, b"\x02"),
        (RESUME, b"\x00\x03\x00"),
        (None, b"\x04\x00"),
        (None, b"\x05\x00\x06\x00"),
        (None, b""),
    ])

    class Pipe:
        def read(self, size):
            event, data = next(fragments)
            if event:
                server._on_shairport_message(event)
            return data

    server._shairport_process = SimpleNamespace(stdout=Pipe(), poll=lambda: None)
    server._running = True
    output = []
    server._stream_server.write_pcm = output.append
    server._read_audio()
    assert b"".join(output) == b"\x05\x00\x06\x00"
    assert server.last_error == "AirPlay 2 PCM stream closed unexpectedly"


def test_session_ids_are_unique_even_when_clock_does_not_advance(monkeypatch):
    monkeypatch.setattr("app.engine.audio_stream.time.time_ns", lambda: 1)
    stream = AudioStreamServer("127.0.0.1", isolate_sessions=True)
    urls = []
    for _ in range(10):
        stream.start_streaming()
        assert len(stream._session_id) >= 40
        urls.append(stream.stream_url)
        stream.stop_streaming()
    assert len(set(urls)) == 10


def test_invalid_tokens_head_and_client_limit_preserve_active_airplay2_consumer(monkeypatch):
    monkeypatch.setattr("app.engine.audio_stream._MAX_STREAM_CLIENTS", 1)
    async def scenario():
        stream = AudioStreamServer("127.0.0.1", isolate_sessions=True)
        await stream.start()
        try:
            stream.start_streaming()
            async with aiohttp.ClientSession() as client:
                active = await client.get(stream.stream_url)
                await active.content.readexactly(44)
                assert stream._client_count == 1
                for method in ("GET", "HEAD"):
                    for suffix in ("", "?sid=guessable-timestamp", "?sid=无效"):
                        async with client.request(method, stream.stream_url.split("?")[0] + suffix) as denied:
                            assert denied.status == 410
                        assert stream._client_count == 1
                async with client.head(stream.stream_url) as head:
                    assert head.status == 200
                assert stream._client_count == 1
                async with client.get(stream.stream_url) as limited:
                    assert limited.status == 429
                assert stream._client_count == 1
                pcm = b"\x01\x00\x02\x00" * 48
                stream.write_pcm(pcm)
                assert await asyncio.wait_for(active.content.readexactly(len(pcm)), 1) == pcm
                active.close()
        finally:
            await stream.stop()
        assert stream._client_count == 0

    asyncio.run(scenario())


async def _expect_aborted(response):
    try:
        await asyncio.wait_for(response.content.read(), 1)
    except (aiohttp.ClientPayloadError, aiohttp.ClientConnectionError):
        pass


def test_http_seek_closes_old_connection_and_rejects_old_sid():
    async def scenario():
        stream = AudioStreamServer("127.0.0.1", isolate_sessions=True)
        await stream.start()
        try:
            stream.start_streaming()
            old_url = stream.stream_url
            async with aiohttp.ClientSession() as client:
                old = await client.get(old_url)
                assert (await old.content.readexactly(44))[:4] == b"RIFF"
                stream.stop_streaming()
                stream.start_streaming()
                new_pcm = b"\x03\x00\x04\x00" * 960
                stream.write_pcm(new_pcm)
                await _expect_aborted(old)
                async with client.get(old_url) as stale:
                    assert stale.status == 410
                async with client.get(stream.stream_url.split("?")[0]) as no_sid:
                    assert no_sid.status == 410
                async with client.get(stream.stream_url) as new:
                    assert new.status == 200
                    await new.content.readexactly(44)
                    assert await new.content.readexactly(len(new_pcm)) == new_pcm
        finally:
            await stream.stop()
        assert not stream.has_clients

    asyncio.run(scenario())


def test_http_head_does_not_steal_pcm_or_count_as_a_client():
    async def scenario():
        stream = AudioStreamServer("127.0.0.1", isolate_sessions=True)
        await stream.start()
        try:
            stream.start_streaming()
            pcm = b"\x01\x00\x02\x00" * 48
            stream.write_pcm(pcm)
            async with aiohttp.ClientSession() as client:
                async with client.head(stream.stream_url) as head:
                    assert head.status == 200
                    assert await head.read() == b""
                assert not stream.has_clients
                assert stream._audio_queue.get_nowait() == pcm
        finally:
            await stream.stop()

    asyncio.run(scenario())


def test_overlapping_http_retry_has_only_one_pcm_consumer():
    async def scenario():
        stream = AudioStreamServer("127.0.0.1", isolate_sessions=True)
        await stream.start()
        try:
            stream.start_streaming()
            async with aiohttp.ClientSession() as client:
                old = await client.get(stream.stream_url)
                await old.content.readexactly(44)
                new = await client.get(stream.stream_url)
                await new.content.readexactly(44)
                pcm = b"\x05\x00\x06\x00" * 960
                stream.write_pcm(pcm)
                assert await asyncio.wait_for(new.content.readexactly(len(pcm)), 1) == pcm
                await _expect_aborted(old)
                new.close()
        finally:
            await stream.stop()

    asyncio.run(scenario())


def test_isolated_http_does_not_insert_silence_during_pcm_gap():
    async def scenario():
        stream = AudioStreamServer("127.0.0.1", isolate_sessions=True)
        await stream.start()
        try:
            stream.start_streaming()
            async with aiohttp.ClientSession() as client:
                response = await client.get(stream.stream_url)
                await response.content.readexactly(44)
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(response.content.readexactly(4), 2.2)
                pcm = b"\x07\x00\x08\x00"
                stream.write_pcm(pcm)
                assert await asyncio.wait_for(response.content.readexactly(4), 1) == pcm
                response.close()
        finally:
            await stream.stop()

    asyncio.run(scenario())


def test_superseded_cloud_stop_cannot_run_after_new_play():
    async def scenario():
        release = asyncio.Event()
        started = asyncio.Event()
        calls = []

        class Controller:
            speaker = Speaker(name="Test")

            async def play_url(self, url, audio_id):
                calls.append(("play", url))
                if url == "old":
                    started.set()
                    await release.wait()
                return True

            async def stop(self):
                calls.append(("stop", None))

        wrapper = SpeakerAirPlay("127.0.0.1", Controller(), airplay2=True)
        wrapper._command_revision = 1
        old = asyncio.create_task(wrapper._apply_airplay2_command(1, "old"))
        await started.wait()
        wrapper._command_revision = 2
        stop = asyncio.create_task(wrapper._apply_airplay2_command(2, None))
        wrapper._command_revision = 3
        new = asyncio.create_task(wrapper._apply_airplay2_command(3, "new"))
        release.set()
        await asyncio.gather(old, stop, new)
        assert calls == [("play", "old"), ("play", "new")]
        assert wrapper._stream_url == "new"
        assert wrapper._airplay_active

    asyncio.run(scenario())


def test_inflight_stop_finishes_before_new_cloud_play():
    async def scenario():
        release = asyncio.Event()
        started = asyncio.Event()
        calls = []

        class Controller:
            speaker = Speaker(name="Test")

            async def stop(self):
                calls.append("stop-start")
                started.set()
                await release.wait()
                calls.append("stop-end")

            async def play_url(self, url, audio_id):
                calls.append("play-new")
                return True

        wrapper = SpeakerAirPlay("127.0.0.1", Controller(), airplay2=True)
        wrapper._command_revision = 1
        stop = asyncio.create_task(wrapper._apply_airplay2_command(1, None))
        await started.wait()
        wrapper._command_revision = 2
        new = asyncio.create_task(wrapper._apply_airplay2_command(2, "new"))
        await asyncio.sleep(0)
        assert calls == ["stop-start"]
        release.set()
        await asyncio.gather(stop, new)
        assert calls == ["stop-start", "stop-end", "play-new"]
        assert wrapper._stream_url == "new"
        assert wrapper._airplay_active

    asyncio.run(scenario())
