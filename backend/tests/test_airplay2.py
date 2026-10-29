"""AirPlay 2 tests: fake receivers/clocks, plus a loopback HTTP WAV bridge."""

import asyncio
import os
import struct
import subprocess
import sys
from types import SimpleNamespace

import aiohttp
import pytest
from pydantic import ValidationError

from app.engine.airplay.shairport_server import ShairportSyncServer, _libconfig_string
from app.engine.config import Config
from app.models.schemas import SettingsPayload


def test_airplay2_config_roundtrip(tmp_path):
    config = Config(
        conf_path=str(tmp_path),
        airplay_mode="airplay2",
        airplay2_target_did="speaker-2",
        airplay2_port=7100,
    )
    config.save()

    loaded = Config.load(str(tmp_path))
    assert loaded.airplay_mode == "airplay2"
    assert loaded.airplay2_target_did == "speaker-2"
    assert loaded.airplay2_port == 7100


def test_invalid_airplay2_config_is_normalized():
    config = Config(airplay_mode="unknown", airplay2_port=99999)
    assert config.airplay_mode == "airplay1"
    assert config.airplay2_port == 65535


def test_shairport_config_has_fixed_pcm_and_selected_port():
    server = ShairportSyncServer("192.168.1.2", 'Living "Room"', port=7123)
    rendered = server._make_config()

    assert 'service_type = "airplay2";' in rendered
    assert 'output_backend = "stdout";' in rendered
    assert "port = 7123;" in rendered
    assert "output_rate = 48000;" in rendered
    assert 'output_format = "S16_LE";' in rendered
    assert "output_channels = 2;" in rendered
    assert 'name = "Living \\"Room\\"";' in rendered


def test_libconfig_escape():
    assert _libconfig_string('a\\b"c') == 'a\\\\b\\"c'
    assert _libconfig_string("a\n\r\tb") == "a\\n\\r\\tb"


def test_new_config_environment_default_does_not_override_saved_mode(monkeypatch, tmp_path):
    Config(conf_path=str(tmp_path), airplay_mode="airplay1").save()
    monkeypatch.setenv("MIAIR_AIRPLAY_MODE", "airplay2")
    assert Config().airplay_mode == "airplay2"
    assert Config.load(str(tmp_path)).airplay_mode == "airplay1"


def test_optional_network_interface_is_escaped(monkeypatch):
    monkeypatch.setenv("MIAIR_AIRPLAY_INTERFACE", 'br-"lan')
    rendered = ShairportSyncServer("127.0.0.1")._make_config()
    assert 'interface = "br-\\"lan";' in rendered


@pytest.mark.parametrize("payload", [
    {"airplay_mode": "airplay3"},
    {"airplay2_port": 0},
    {"airplay2_port": 65536},
])
def test_settings_validate_protocol_and_port(payload):
    with pytest.raises(ValidationError):
        SettingsPayload(**payload)


@pytest.mark.parametrize("requested, expected, fails", [
    ("did-2", [("did-1", False), ("did-2", True)], False),
    ("", [("did-1", True), ("did-2", False)], False),
    ("deleted-device", [("did-1", False), ("did-2", False)], False),
    ("did-2", [("did-1", False), ("did-2", True), ("did-2", False)], True),
])
def test_manager_routing_and_startup_fallback(monkeypatch, requested, expected, fails):
    from app.engine.airplay import speaker_airplay as module

    created = []

    class FakeZeroconf:
        def __init__(self, **kwargs):
            del kwargs

        def close(self):
            pass

    async def fake_start(self):
        created.append((self.speaker.did, self.airplay2))
        if self.airplay2 and fails:
            raise RuntimeError("NQPTP port conflict")
        self.airplay_server = SimpleNamespace(rtsp_port=7000)

    async def fake_stop(self):
        self.airplay_server = None

    monkeypatch.setattr(module, "Zeroconf", FakeZeroconf)
    monkeypatch.setattr(module.SpeakerAirPlay, "start", fake_start)
    monkeypatch.setattr(module.SpeakerAirPlay, "stop", fake_stop)

    config = SimpleNamespace(
        airplay_mode="airplay2",
        airplay2_target_did=requested,
        airplay2_port=7000,
    )
    controllers = {
        "did-1": SimpleNamespace(speaker=SimpleNamespace(did="did-1")),
        "did-2": SimpleNamespace(speaker=SimpleNamespace(did="did-2")),
    }
    async def scenario():
        manager = module.AirPlayManager("192.168.1.2", config=config)
        await manager.start_for_speakers(controllers)

        assert created == expected
        assert manager.airplay2_running is (not fails and requested != "deleted-device")
        if requested == "deleted-device":
            assert "不可用" in manager.airplay2_error
            assert manager.airplay2_target_did == ""
        elif fails:
            assert manager.airplay2_error == "NQPTP port conflict"
        else:
            assert manager.airplay2_target_did == (requested or "did-1")

        await manager.stop()
        assert not manager.airplay2_running

        # Switching modes must not retain a previous AirPlay 2 failure.
        config.airplay_mode = "airplay1"
        await manager.start_for_speakers(controllers)
        assert manager.airplay2_error == ""
        assert not manager.airplay2_running
        await manager.stop()

    asyncio.run(scenario())


def test_pcm_reader_preserves_stereo_frame_alignment_and_single_session():
    class FragmentedPipe:
        def __init__(self):
            self.chunks = iter([b"\x01", b"\x00\x02", b"\x00\x03\x00", b"\x04\x00", b""])

        def read(self, size):
            return next(self.chunks)

    server = ShairportSyncServer("127.0.0.1")
    server._shairport_process = SimpleNamespace(stdout=FragmentedPipe(), poll=lambda: None)
    server._running = True
    chunks = []
    starts, stops = [], []
    server._stream_server.write_pcm = chunks.append
    server.on_play_start = starts.append
    server.on_play_stop = lambda: stops.append(True)

    server._read_audio()
    assert b"".join(chunks) == b"\x01\x00\x02\x00\x03\x00\x04\x00"
    assert all(len(chunk) % 4 == 0 for chunk in chunks)
    assert len(starts) == 1
    assert server.is_playing
    server._end_session()
    server._end_session()
    assert stops == [True]


def _fake_external_processes(
    monkeypatch, receiver_code="import time; time.sleep(60)",
    clock_code="import time; time.sleep(60)",
):
    """Use real local Python children instead of binding PTP/RTSP ports."""
    from app.engine.airplay import shairport_server as module

    real_popen = subprocess.Popen
    real_exists = os.path.exists
    processes = []
    monkeypatch.setattr(module.shutil, "which", lambda name: f"/fake/{name}")
    monkeypatch.setattr(module.os.path, "exists", lambda path: False if path == "/dev/shm/nqptp" else real_exists(path))

    def spawn(args, **kwargs):
        code = receiver_code if args[0].endswith("shairport-sync") else clock_code
        process = real_popen([sys.executable, "-u", "-c", code], **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(module.subprocess, "Popen", spawn)
    return processes


def test_receiver_pcm_reaches_http_wav_and_stop_releases_children(monkeypatch):
    processes = _fake_external_processes(
        monkeypatch,
        "import os, time; time.sleep(1.4); os.write(1, b'\\x01\\x00\\x02\\x00' * 960); time.sleep(60)",
    )

    async def scenario():
        server = ShairportSyncServer("127.0.0.1")
        starts, stops = [], []
        server.on_play_start = starts.append
        server.on_play_stop = lambda: stops.append(True)
        config_path = ""
        try:
            await server.start()
            config_path = server._config_path
            assert server.running
            for _ in range(100):
                if starts:
                    break
                await asyncio.sleep(0.02)
            assert starts == [server._stream_server.stream_url]
            async with aiohttp.ClientSession() as client:
                async with client.get(starts[0], timeout=aiohttp.ClientTimeout(total=3)) as response:
                    assert response.status == 200
                    header = await response.content.readexactly(44)
                    assert header[:4] == b"RIFF"
                    assert struct.unpack_from("<HHI", header, 20) == (1, 2, 48000)
                    pcm = await response.content.readexactly(3840)
                    assert pcm == b"\x01\x00\x02\x00" * 960
        finally:
            await server.stop()
            await server.stop()  # idempotent shutdown
        assert stops == [True]
        assert not server.running
        assert not os.path.exists(config_path)
        assert all(process.poll() is not None for process in processes)
        assert all(process.stderr.closed for process in processes)

    asyncio.run(scenario())


def test_startup_failure_cleans_clock_http_and_temp_config(monkeypatch):
    processes = _fake_external_processes(
        monkeypatch, "import sys; sys.stderr.write('Avahi unavailable\\n'); sys.exit(9)"
    )

    async def scenario():
        server = ShairportSyncServer("127.0.0.1")
        with pytest.raises(RuntimeError, match="Avahi unavailable"):
            await server.start()
        assert not server.running
        assert server._config_path == ""
        assert server._shairport_process is None
        assert server._nqptp_process is None
        assert all(process.poll() is not None for process in processes)

    asyncio.run(scenario())


@pytest.mark.parametrize("failed_process", ["_nqptp_process", "_shairport_process"])
def test_runtime_exit_is_reported_and_both_processes_are_reaped(monkeypatch, failed_process):
    processes = _fake_external_processes(monkeypatch)

    async def scenario():
        server = ShairportSyncServer("127.0.0.1")
        try:
            await server.start()
            monitor = server._monitor_task
            getattr(server, failed_process).kill()
            await asyncio.wait_for(monitor, timeout=5)
            assert not server.running
            assert "异常退出" in server.last_error
            assert server._config_path == ""
            assert all(process.poll() is not None for process in processes)
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_cancelled_start_releases_spawned_processes(monkeypatch):
    processes = _fake_external_processes(monkeypatch)

    async def scenario():
        server = ShairportSyncServer("127.0.0.1")
        task = asyncio.create_task(server.start())
        try:
            for _ in range(100):
                if server._shairport_process:
                    break
                await asyncio.sleep(0.02)
            assert server._shairport_process is not None
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert server._config_path == ""
            assert all(process.poll() is not None for process in processes)
        finally:
            if not task.done():
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            await server.stop()

    asyncio.run(scenario())


def test_shared_clock_is_not_silently_reused(monkeypatch):
    from app.engine.airplay import shairport_server as module

    monkeypatch.setattr(module.os.path, "exists", lambda path: path == "/dev/shm/nqptp")

    async def scenario():
        server = ShairportSyncServer("127.0.0.1")
        with pytest.raises(RuntimeError, match="独立容器 IPC"):
            await server._start_nqptp("/fake/nqptp")
        assert server._nqptp_process is None

    asyncio.run(scenario())


def test_live_receiver_with_closed_stdout_is_reported_and_stopped(monkeypatch):
    processes = _fake_external_processes(
        monkeypatch, "import os, time; os.close(1); time.sleep(60)"
    )

    async def scenario():
        server = ShairportSyncServer("127.0.0.1")
        try:
            await server.start()
            await asyncio.wait_for(server._monitor_task, timeout=5)
            assert server.last_error == "AirPlay 2 PCM stream closed unexpectedly"
            assert not server.running
            assert server._config_path == ""
            assert all(process.poll() is not None for process in processes)
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_killed_clock_does_not_block_a_subsequent_receiver_start(monkeypatch, tmp_path):
    from app.engine.airplay import shairport_server as module

    clock_file = tmp_path / "nqptp"
    monkeypatch.setattr(module, "_NQPTP_SHM_PATH", str(clock_file))
    _fake_external_processes(
        monkeypatch,
        clock_code=f"import pathlib, time; pathlib.Path({str(clock_file)!r}).touch(); time.sleep(60)",
    )

    async def scenario():
        first = ShairportSyncServer("127.0.0.1")
        try:
            await first.start()
            assert clock_file.exists()
            first._nqptp_process.kill()
            await asyncio.wait_for(first._monitor_task, timeout=5)
            assert not clock_file.exists()
        finally:
            await first.stop()
        second = ShairportSyncServer("127.0.0.1")
        try:
            await second.start()
            assert second.running
        finally:
            await second.stop()
        assert not clock_file.exists()

    asyncio.run(scenario())


def test_clock_cleanup_preserves_a_replacement_object(monkeypatch, tmp_path):
    from app.engine.airplay import shairport_server as module

    clock_file = tmp_path / "nqptp"
    clock_file.write_text("owned")
    monkeypatch.setattr(module, "_NQPTP_SHM_PATH", str(clock_file))
    server = ShairportSyncServer("127.0.0.1")
    server._nqptp_process = SimpleNamespace()
    server._remember_nqptp_clock()
    replacement = tmp_path / "replacement"
    replacement.write_text("external")
    replacement.replace(clock_file)
    server._remove_owned_nqptp_clock()
    assert clock_file.read_text() == "external"
