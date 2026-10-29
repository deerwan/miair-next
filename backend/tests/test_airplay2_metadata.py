"""Metadata framing, real FIFO lifecycle, protected artwork and lyric ordering."""

import asyncio
import base64
import os
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user, get_orchestrator
from app.api.v1.ws import _collect_status
from app.engine.airplay import shairport_metadata as metadata_module
from app.engine.airplay.shairport_metadata import ShairportMetadata
from app.engine.airplay.shairport_server import ShairportSyncServer
from app.engine.airplay.speaker_airplay import SpeakerAirPlay
from app.engine.config import Speaker
from app.main import create_app

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWLsAAAAASUVORK5CYII=")


def item(kind, code, data=b""):
    return (f"<item><type>{kind.encode().hex()}</type><code>{code.encode().hex()}</code>"
            f"<length>{len(data)}</length><data encoding=\"base64\">\n"
            f"{base64.b64encode(data).decode()}</data></item>\n").encode()


def track(title="Song", artist="Artist", album="Album", token=b"1"):
    return b"".join([
        item("ssnc", "mdst", token), item("core", "minm", title.encode()),
        item("core", "asar", artist.encode()), item("core", "asal", album.encode()),
        item("ssnc", "mden", token),
    ])


def picture(token=b"1", data=PNG):
    return item("ssnc", "pcst", token) + item("ssnc", "PICT", data) + item("ssnc", "pcen", token)


@pytest.mark.parametrize("chunk_size", [1, 13, 65536])
def test_fragmented_unicode_metadata_and_picture(chunk_size):
    metadata = ShairportMetadata()
    payload = item("ssnc", "snam", b"Sender") + track("青花瓷 - 周杰伦", "周杰伦") + picture()
    for pos in range(0, len(payload), chunk_size):
        metadata.feed(payload[pos:pos + chunk_size])
    assert metadata.meta == {"title": "青花瓷", "artist": "周杰伦", "album": "Album", "derived": ""}
    assert metadata.client_name == "Sender"
    assert metadata.artwork[1:] == ("image/png", PNG)
    assert metadata.snapshot()["artwork_id"] == metadata.artwork[0]
    assert len(metadata.events) == 1


def test_transactions_are_atomic_and_track_changes_discard_old_artwork():
    metadata = ShairportMetadata()
    metadata.feed(track() + picture())
    metadata.feed(item("ssnc", "mdst", b"2") + item("core", "minm", b"Next"))
    assert metadata.meta["title"] == "Song"
    metadata.feed(item("ssnc", "mden", b"2"))
    assert metadata.meta == {"title": "Next", "artist": "", "album": "", "derived": ""}
    assert metadata.artwork is None
    metadata.feed(picture(b"1"))  # late previous cover must not reappear
    assert metadata.artwork is None
    metadata.feed(picture(b"3") + track("Third", token=b"3"))
    assert metadata.artwork[2] == PNG
    metadata.feed(picture(b"3", b""))
    assert metadata.artwork is None


def test_bad_items_and_oversized_frames_recover_without_poisoning_next_track(monkeypatch):
    metadata = ShairportMetadata()
    monkeypatch.setattr(metadata_module, "MAX_ITEM_BYTES", 1024)
    metadata.feed(b"<item>" + b"x" * 2000)
    assert len(metadata._buffer) <= 5
    metadata.feed(b"<item>broken</item>" + item("core", "minm", b"wrong length").replace(
        b"<length>12</length>", b"<length>9</length>"))
    metadata.feed(b"<item>incomplete" + track("Recovered"))
    assert metadata.meta["title"] == "Recovered"
    metadata.feed(picture(data=b"<svg onload='alert(1)'/>"))
    assert metadata.artwork is None
    metadata.feed(item("core", "minm", b"orphan"))
    assert metadata.meta["title"] == "Recovered"


def test_artwork_size_limit_rejects_large_images_and_accepts_later_jpeg(monkeypatch):
    metadata = ShairportMetadata()
    metadata.feed(track())
    monkeypatch.setattr(metadata_module, "MAX_ARTWORK_BYTES", 16)
    metadata.feed(picture())
    assert metadata.artwork is None
    jpeg = b"\xff\xd8\xff\xe0fixture"
    metadata.feed(picture(data=jpeg))
    assert metadata.artwork[1:] == ("image/jpeg", jpeg)


def test_event_snapshots_are_bounded_copied_and_reset_on_sender_end():
    metadata = ShairportMetadata()
    for index in range(140):
        metadata.feed(track(str(index)))
    assert len(metadata.events) == 128
    metadata.events[-1][1]["title"] = "mutated"
    assert metadata.meta["title"] == "139"
    metadata.feed(item("ssnc", "pend"))
    assert metadata.meta is None
    assert metadata.events == []
    metadata.feed(track("New sender"))
    assert metadata.events[-1][0] > 140


def test_real_metadata_fifo_startup_reader_and_stop_cleanup():
    async def scenario():
        server = ShairportSyncServer("127.0.0.1")
        try:
            server._start_metadata()
            directory = server._metadata_dir.name
            pipe = os.path.join(directory, "metadata")
            assert os.stat(directory).st_mode & 0o777 == 0o700
            assert os.stat(pipe).st_mode & 0o777 == 0o600
            assert 'include_cover_art = "yes"' in server._make_config()
            with open(pipe, "wb", buffering=0) as writer:
                writer.write(track() + picture())
            deadline = time.monotonic() + 2
            while server.artwork is None and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            assert server.daap_meta["title"] == "Song"
            assert server.artwork[2] == PNG
            thread = server._metadata_thread
        finally:
            await server.stop()
        assert not thread.is_alive()
        assert not os.path.exists(directory)
        assert server._metadata_fd is None
        assert server.daap_meta is None
        await server.stop()

    asyncio.run(scenario())


def orchestrator(server):
    return SimpleNamespace(
        config=SimpleNamespace(spotify_enabled=False),
        airplay_manager=SimpleNamespace(speaker_airplays={"s1": SimpleNamespace(airplay_server=server)}),
        speaker_manager=SimpleNamespace(controllers={"s1": SimpleNamespace(
            speaker=Speaker(did="s1", name="Living room"))}),
        get_renderer_by_did=lambda did: None, dlna_running=True, renderers={},
    )


def test_websocket_status_and_artwork_route_require_auth_and_current_active_cover():
    server = ShairportSyncServer("127.0.0.1")
    server._metadata.feed(track() + picture())
    server._begin_session()
    orch = orchestrator(server)
    status = _collect_status(orch)["speakers"][0]["now_playing"]
    assert status["title"] == "Song"
    assert status["artist"] == "Artist"
    artwork_id = status["artwork_id"]
    assert artwork_id
    app = create_app()
    app.dependency_overrides[get_orchestrator] = lambda: orch
    client = TestClient(app)
    url = f"/api/v1/speakers/s1/airplay/artwork/{artwork_id}"
    assert client.get(url).status_code == 401
    app.dependency_overrides[get_current_user] = lambda: "reviewer"
    response = client.get(url)
    assert response.status_code == 200
    assert response.content == PNG
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "no-store"
    assert client.get(url.replace("s1", "other")).status_code == 404
    server._metadata.feed(track("Next", token=b"2"))
    assert client.get(url).status_code == 404
    server._end_session()
    assert _collect_status(orch)["speakers"][0]["now_playing"]["title"] == ""
    assert client.get(url).status_code == 404


def test_websocket_airplay2_diagnostics_report_bridge_metrics_without_native_counters():
    server = ShairportSyncServer("127.0.0.1")
    orch = orchestrator(server)
    server._begin_session()
    server._stream_server.write_pcm(b"\x00" * 1920)
    diagnostic = _collect_status(orch)["speakers"][0]["airplay_audio"]
    assert diagnostic == {
        "protocol": "airplay2", "playing": True, "sample_rate": 48000, "channels": 2,
        "stream_clients": 0, "stream_queue_depth": 1, "stream_pending_dropped_bytes": 0,
    }
    server._end_session()
    diagnostic = _collect_status(orch)["speakers"][0]["airplay_audio"]
    assert diagnostic["playing"] is False
    assert diagnostic["stream_queue_depth"] == 0


class LyricsController:
    speaker = Speaker(name="Test", hardware="LX06")

    def __init__(self):
        self.commands = []
        self.searches = []
        self.changed = asyncio.Event()
        self.search_started = asyncio.Event()
        self.release_search = None

    async def play_url(self, url, audio_id=None):
        self.commands.append((url, audio_id))
        self.changed.set()
        return True

    async def search_audio_id(self, title, artist, fuzzy_fallback):
        self.searches.append((title, artist, fuzzy_fallback))
        self.search_started.set()
        if self.release_search:
            await self.release_search.wait()
        return "" if title == "Unknown" else artist

    async def stop(self):
        self.commands.append(("stop", None))


def wrapper(controller):
    result = SpeakerAirPlay("127.0.0.1", controller, airplay2=True,
                            config=SimpleNamespace(touchscreen_lyrics=True,
                                                   auto_resume_on_interrupt=False))
    result.airplay_server = SimpleNamespace(daap_events=[], stream_has_clients=True)
    result._loop = asyncio.get_running_loop()
    return result


def test_ap2_lyrics_follow_artist_changes_return_to_cached_tracks_and_clear_misses():
    async def scenario():
        controller = LyricsController()
        sap = wrapper(controller)
        watcher = None
        try:
            await sap._play_on_speaker("http://speaker/stream?sid=1")
            watcher = sap._backfill_task
            assert controller.commands == [("http://speaker/stream?sid=1", None)]
            for seq, artist, title in [(1, "Artist A", "Song"), (2, "Artist B", "Song"),
                                       (3, "Artist A", "Song"), (4, "Missing", "Unknown")]:
                controller.changed.clear()
                sap.airplay_server.daap_events.append((seq, {"title": title, "artist": artist}))
                await asyncio.wait_for(controller.changed.wait(), 3)
                assert controller.commands[-1][1] == (None if title == "Unknown" else artist)
            assert controller.searches == [("Song", "Artist A", False),
                                           ("Song", "Artist B", False), ("Unknown", "Missing", False)]
        finally:
            await sap._stop_speaker()
            if watcher:
                await watcher

    asyncio.run(scenario())


def test_shutdown_waits_for_inflight_lyrics_cloud_play_then_stops_last():
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        class Controller(LyricsController):
            async def play_url(self, url, audio_id=None):
                if audio_id:
                    started.set()
                    await release.wait()
                return await super().play_url(url, audio_id)

        controller = Controller()
        sap = wrapper(controller)

        async def receiver_stop():
            sap._on_play_stop()

        sap.airplay_server.stop = receiver_stop
        await sap._play_on_speaker("http://speaker/stream?sid=1")
        sap.airplay_server.daap_events.append((1, {"title": "Song", "artist": "Artist"}))
        await asyncio.wait_for(started.wait(), 3)
        shutdown = asyncio.create_task(sap.stop())
        await asyncio.sleep(0.01)
        assert not shutdown.done()
        release.set()
        await asyncio.wait_for(shutdown, 3)
        assert controller.commands[-2:] == [("http://speaker/stream?sid=1", "Artist"), ("stop", None)]
        assert not sap._pending_commands

    asyncio.run(scenario())


@pytest.mark.parametrize("interrupt", ["stop", "track"])
def test_late_lyrics_search_cannot_restore_stopped_audio_or_previous_track(interrupt):
    async def scenario():
        controller = LyricsController()
        controller.release_search = asyncio.Event()
        sap = wrapper(controller)
        await sap._play_on_speaker("http://speaker/stream?sid=1")
        watcher = sap._backfill_task
        sap.airplay_server.daap_events.append((1, {"title": "Old", "artist": "Old artist"}))
        await asyncio.wait_for(controller.search_started.wait(), 3)
        if interrupt == "stop":
            sap._command_revision += 1
            await sap._apply_airplay2_command(sap._command_revision, None)
        else:
            sap.airplay_server.daap_events.append((2, {"title": "New", "artist": "New artist"}))
        controller.changed.clear()
        controller.release_search.set()
        if interrupt == "track":
            await asyncio.wait_for(controller.changed.wait(), 3)
            assert controller.commands[-1][1] == "New artist"
            await sap._stop_speaker()
        await watcher
        assert not any(audio_id == "Old artist" for _, audio_id in controller.commands)
        if interrupt == "stop":
            assert controller.commands[-1] == ("stop", None)

    asyncio.run(scenario())
