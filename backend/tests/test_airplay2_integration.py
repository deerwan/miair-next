"""Regressions at the shared AirPlay 2 / Spotify integration boundaries."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest

from app.engine.airplay.shairport_server import ShairportSyncServer
from app.engine.config import Speaker
from app.engine.spotify.receiver import SpotifyReceiver
from app.services.orchestrator import Orchestrator


def test_airplay_session_retirement_leaves_spotify_http_stream_running():
    async def scenario():
        airplay = ShairportSyncServer("127.0.0.1")
        spotify = SpotifyReceiver(
            SimpleNamespace(), SimpleNamespace(speaker=Speaker(name="Spotify")), "127.0.0.1"
        )
        ap_stream, sp_stream = airplay._stream_server, spotify._stream
        await ap_stream.start()
        try:
            await sp_stream.start()
            airplay._begin_session()
            sp_stream.start_streaming()
            ap_stream.write_pcm(b"old!" * 480)
            sp_stream.write_pcm(b"spot" * 480)
            async with aiohttp.ClientSession() as client:
                async with client.get(sp_stream.stream_url) as sp_response:
                    assert "/spotify/stream.wav?" in sp_stream.stream_url
                    await sp_response.content.readexactly(44)
                    assert await asyncio.wait_for(
                        sp_response.content.readexactly(480 * 4), 1
                    ) == b"spot" * 480
                    old_url = ap_stream.stream_url
                    async with client.get(old_url) as old_response:
                        assert "/airplay/stream.wav?" in old_url
                        await old_response.content.readexactly(44)
                        await old_response.content.readexactly(480 * 4)
                        airplay._end_session()
                        airplay._begin_session()
                        ap_stream.write_pcm(b"new!" * 480)
                        async with client.get(old_url) as stale:
                            assert stale.status == 410
                        async with client.get(ap_stream.stream_url) as fresh:
                            await fresh.content.readexactly(44)
                            assert await asyncio.wait_for(
                                fresh.content.readexactly(480 * 4), 1
                            ) == b"new!" * 480
                        # Closing the AP2 stream must not retire Spotify's
                        # connection, replace its queue, or change its routing.
                        airplay._end_session()
                        sp_stream.write_pcm(b"next" * 480)
                        assert await asyncio.wait_for(
                            sp_response.content.readexactly(480 * 4), 1
                        ) == b"next" * 480
                        assert sp_stream.has_clients
        finally:
            airplay._end_session()
            await ap_stream.stop()
            await sp_stream.stop()
        assert not ap_stream.has_clients
        assert not sp_stream.has_clients

    asyncio.run(scenario())


@pytest.mark.parametrize("fail_recovery", [False, True])
def test_visibility_recovery_preserves_airplay2_target_and_fallback(monkeypatch, fail_recovery):
    from app.engine.airplay import speaker_airplay as module

    attempts = []
    fail = [False]

    class Discovery:
        def __init__(self, **kwargs):
            pass

        def close(self):
            pass

    async def start(wrapper):
        attempts.append((wrapper.speaker.did, wrapper.airplay2))
        if wrapper.airplay2 and fail[0]:
            raise RuntimeError("clock unavailable")
        wrapper.airplay_server = SimpleNamespace(rtsp_port=7000, running=True)

    async def stop(wrapper):
        wrapper.airplay_server = None

    monkeypatch.setattr(module, "Zeroconf", Discovery)
    monkeypatch.setattr(module.SpeakerAirPlay, "start", start)
    monkeypatch.setattr(module.SpeakerAirPlay, "stop", stop)

    async def scenario():
        target_online = [True]

        async def status():
            if not target_online[0]:
                raise RuntimeError("offline")
            return {"status": 1}

        controllers = {
            "legacy": SimpleNamespace(
                speaker=Speaker(did="legacy", name="Legacy"),
                get_status=AsyncMock(return_value={"status": 1}),
            ),
            "target": SimpleNamespace(
                speaker=Speaker(did="target", name="Target"), get_status=status
            ),
        }
        manager = module.AirPlayManager(
            "127.0.0.1",
            config=SimpleNamespace(airplay_mode="airplay2", airplay2_target_did="target"),
        )
        await manager.start_for_speakers(controllers)
        legacy = manager.speaker_airplays["legacy"]
        assert manager.airplay2_running

        # Exercise upstream visibility calls with the real AP2-aware manager.
        orch = Orchestrator.__new__(Orchestrator)
        orch.dlna_running = True
        orch.speaker_manager = SimpleNamespace(controllers=controllers)
        orch._offline_speakers = set()
        orch._did_to_udn = {}
        orch.airplay_manager = manager
        try:
            target_online[0] = False
            await orch._refresh_speaker_visibility()
            assert not manager.airplay2_running
            assert "target" not in manager.speaker_airplays
            assert manager.airplay2_target_did == "target"
            target_online[0] = True
            fail[0] = fail_recovery
            await orch._refresh_speaker_visibility()
            assert manager.speaker_airplays["legacy"] is legacy
            assert manager.speaker_airplays["target"].airplay2 is not fail_recovery
            assert manager.airplay2_running is not fail_recovery
            if fail_recovery:
                assert manager.airplay2_error == "clock unavailable"
                target_online[0] = False
                await orch._refresh_speaker_visibility()
                fail[0] = False
                target_online[0] = True
                await orch._refresh_speaker_visibility()
                assert manager.airplay2_running
                assert manager.airplay2_error == ""
            assert attempts.count(("legacy", False)) == 1
            assert not orch._offline_speakers
        finally:
            await manager.stop()

    asyncio.run(scenario())
