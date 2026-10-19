"""Spotify Connect (Soloist) 集成单元测试

覆盖: 架构映射 / 版本与过期解析 / entity 元数据解析 / 配置持久化 / 密钥脱敏 /
AudioStreamServer 泛化后的 label 与 WAV 头。
"""

from __future__ import annotations

import asyncio
import io
import os
import tarfile
from datetime import date, datetime, timedelta

import pytest

from app.core.masking import MASKED_TOKEN, mask_secret, unmask_secret
from app.engine.audio_stream import AudioStreamServer
from app.engine.config import Config
from app.engine.spotify import soloist as soloist_mod
from app.engine.spotify.soloist import (
    BUILD_TTL_DAYS,
    detect_arch,
    download_url_for,
    inspect_binary,
    parse_expiry_days,
    parse_version_output,
)
from app.engine.spotify.ws_client import parse_entity

VERSION_OUTPUT = "soloist 1.3.9.34 build 1792389689 (20261019) (g440165f200) (linux/x86_64)"


class TestArch:
    @pytest.mark.parametrize(
        "machine,arch",
        [
            ("x86_64", "x86_64"),
            ("amd64", "x86_64"),
            ("aarch64", "arm64"),
            ("arm64", "arm64"),
            ("armv7l", "arm32"),
            ("armv6l", "arm32"),
            ("ARM64", "arm64"),
        ],
    )
    def test_map(self, machine, arch):
        assert detect_arch(machine) == arch

    def test_unsupported(self):
        with pytest.raises(ValueError):
            detect_arch("mips64")

    def test_download_url(self):
        assert (
            download_url_for("arm64")
            == "https://soloist-builds.spotifycdn.com/soloist_release_arm64.tar.gz"
        )


class TestVersionParsing:
    def test_parse_real_output(self):
        info = parse_version_output(VERSION_OUTPUT)
        assert info["version"] == "1.3.9.34"
        assert info["build_epoch"] == 1792389689
        assert info["build_date"] == "20261019"
        expires = datetime.strptime("20261019", "%Y%m%d").date() + timedelta(
            days=BUILD_TTL_DAYS
        )
        assert info["expires_on"] == expires.isoformat()
        assert isinstance(info["days_left"], int)

    def test_parse_invalid(self):
        info = parse_version_output("not a version string")
        assert info["version"] == ""
        assert info["days_left"] is None

    def test_parse_expiry_days(self):
        assert parse_expiry_days("2026-10-19 09:26:58.492: client expires in 89 days") == 89
        assert parse_expiry_days("hello") is None

    def test_inspect_binary_missing(self, tmp_path):
        info = inspect_binary(str(tmp_path))
        assert info["installed"] is False
        assert info["path"].endswith("soloist")


class TestEntityParsing:
    ENTITY = {
        "uri": "spotify:track:2JRo0gjbX4GrCqBYdRohoo",
        "entity_type": "track",
        "decorations": {
            "identity": {"name": "My Song"},
            "visual_identity": {
                "cover": [
                    {"url": "https://i.scdn.co/image/abc", "size": "large"},
                    {"url": "https://i.scdn.co/image/small", "size": "small"},
                ]
            },
            "parent": {
                "entity": {
                    "uri": "spotify:album:4aawyAB9vmqN3uQ7FjRGTy",
                    "entity_type": "album",
                    "decorations": {"identity": {"name": "Album Name"}},
                }
            },
            "creators": [
                {
                    "entity": {
                        "uri": "spotify:artist:1",
                        "entity_type": "artist",
                        "decorations": {"identity": {"name": "Artist A"}},
                    }
                },
                {
                    "entity": {
                        "uri": "spotify:artist:2",
                        "entity_type": "artist",
                        "decorations": {"identity": {"name": "Artist B"}},
                    }
                },
            ],
            "playback": {"duration_ms": 210000, "content_ratings": []},
        },
    }

    def test_parse_entity(self):
        parsed = parse_entity(self.ENTITY)
        assert parsed is not None
        assert parsed["title"] == "My Song"
        assert parsed["artist"] == "Artist A, Artist B"
        assert parsed["album"] == "Album Name"
        assert parsed["cover"] == "https://i.scdn.co/image/abc"
        assert parsed["duration_ms"] == 210000
        assert parsed["uri"] == "spotify:track:2JRo0gjbX4GrCqBYdRohoo"
        assert parsed["entity_type"] == "track"

    def test_parse_empty(self):
        assert parse_entity(None) is None
        assert parse_entity({}) is None

    def test_parse_minimal(self):
        parsed = parse_entity({"uri": "spotify:episode:1", "entity_type": "episode"})
        assert parsed is not None
        assert parsed["uri"] == "spotify:episode:1"
        assert parsed["title"] == ""
        assert parsed["artist"] == ""


class TestConfigPersistence:
    def test_defaults(self):
        cfg = Config()
        assert cfg.spotify_enabled is False
        assert cfg.spotify_device_name == ""
        assert cfg.spotify_did == ""
        assert cfg.spotify_api_key == ""

    def test_save_load_roundtrip(self, tmp_path):
        cfg = Config(conf_path=str(tmp_path))
        cfg.spotify_enabled = True
        cfg.spotify_device_name = "Living Room"
        cfg.spotify_did = "did-123"
        cfg.spotify_api_key = "secret-key"
        cfg.save()
        loaded = Config.load(str(tmp_path))
        assert loaded.spotify_enabled is True
        assert loaded.spotify_device_name == "Living Room"
        assert loaded.spotify_did == "did-123"
        assert loaded.spotify_api_key == "secret-key"


class TestSecretMasking:
    def test_mask_and_unmask(self):
        masked = mask_secret("abcdef123456")
        assert masked.endswith("456")
        assert "abcdef123" not in masked
        assert unmask_secret(masked, "abcdef123456") == "abcdef123456"

    def test_new_value_kept(self):
        assert unmask_secret("new-key", "old-key") == "new-key"

    def test_short_secret(self):
        assert mask_secret("ab") == MASKED_TOKEN


class TestAudioStreamLabel:
    def test_default_label(self):
        stream = AudioStreamServer("127.0.0.1", 0)
        assert stream._label == "AirPlay"

    def test_spotify_label_and_header(self):
        stream = AudioStreamServer("127.0.0.1", 0, label="Spotify")
        assert stream._label == "Spotify"
        header = stream._build_wav_header()
        assert header[:4] == b"RIFF"
        assert header[8:12] == b"WAVE"
        assert int.from_bytes(header[22:24], "little") == 2  # channels
        assert int.from_bytes(header[24:28], "little") == 44100


def test_build_ttl_is_90_days():
    assert BUILD_TTL_DAYS == 90
    assert date(2026, 10, 19) + timedelta(days=90) == date(2027, 1, 17)


class _FakeSpeaker:
    did = "test-did"

    def get_dlna_name(self) -> str:
        return "Test Speaker"


class _FakeController:
    def __init__(self):
        self.speaker = _FakeSpeaker()
        self.played: list[str] = []
        self.stopped = 0
        self.volumes: list[int] = []
        self._on_play = None

    async def play_url(self, url: str, audio_id: str | None = None) -> bool:
        self.played.append(url)
        if self._on_play is not None:
            self._on_play()
        return True

    async def stop(self) -> bool:
        self.stopped += 1
        return True

    async def set_volume(self, volume: int) -> bool:
        self.volumes.append(volume)
        return True

    async def get_status(self) -> dict:
        return {"status": 1}

    async def search_audio_id(self, *args, **kwargs) -> str:
        return ""


class _FakeConfig:
    spotify_enabled = True
    spotify_device_name = "Test Device"
    spotify_api_key = "dummy"
    spotify_did = "test-did"
    touchscreen_lyrics = False
    default_volume = 0
    auto_resume_on_interrupt = False
    resume_delay_seconds = 5
    hostname = "127.0.0.1"


def _make_receiver(controller=None):
    from app.engine.spotify.receiver import SpotifyReceiver

    return SpotifyReceiver(_FakeConfig(), controller or _FakeController(), "127.0.0.1")


class TestReceiverRecovery:
    def test_cancel_tasks_skips_current_task(self):
        """restart 由 _restart_task 自身触发时, _cancel_tasks 不能取消自己

        否则 _cleanup 在第一个 await 处抛 CancelledError, WS/PA/流全部泄漏,
        自动恢复 (退出码 10 更新 / 崩溃重启) 永远失败。
        """
        receiver = _make_receiver()

        async def scenario():
            async def long_task():
                await asyncio.sleep(60)

            play_task = asyncio.create_task(long_task())
            receiver._play_task = play_task
            receiver._restart_task = asyncio.current_task()
            await asyncio.sleep(0)
            receiver._cancel_tasks()
            await asyncio.sleep(0.05)
            assert play_task.cancelled() is True
            assert receiver._play_task is None
            # 当前任务不能被取消, 引用也保留 (由调用方在进入 restart 前自行摘除)
            assert receiver._restart_task is asyncio.current_task()

        asyncio.run(scenario())

    def test_pause_during_play_url_stops_speaker(self):
        """play_url 进行中收到暂停: 播放完成后必须立即停止音箱"""
        receiver = _make_receiver()

        async def scenario():
            receiver._running = True
            receiver._is_active = True
            receiver._playback_status = "playing"
            controller = receiver.controller
            controller._on_play = lambda: setattr(receiver, "_playback_status", "paused")
            await receiver._do_play()
            assert controller.played, "应已触发 play_url"
            assert controller.stopped == 1, "暂停后应在 play_url 返回时补停音箱"
            assert receiver._speaker_active is False

        asyncio.run(scenario())


class _BlockingBinary:
    """模拟 update 时 ensure(force=True) 长时间下载"""

    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.info = type("Info", (), {"to_dict": lambda self: {"installed": True}})()

    async def ensure(self, force: bool = False):
        self.started.set()
        await self.release.wait()
        return self.info


class TestRecoveryRaces:
    def test_stop_during_update_cancels_restart(self):
        """update 下载窗口内 stop() 必须能取消重启, 不能拉起孤儿接收器"""
        receiver = _make_receiver()

        async def scenario():
            receiver._running = True
            binary = _BlockingBinary()
            receiver._binary = binary
            receiver._schedule_restart(0, update=True)
            await asyncio.wait_for(binary.started.wait(), timeout=2)
            await asyncio.wait_for(receiver.stop(), timeout=5)
            await asyncio.sleep(0.2)
            assert receiver._running is False
            assert receiver._restart_task is None
            assert receiver._proc is None and receiver._ws is None, "不应重新启动 Soloist/WS"

        asyncio.run(scenario())

    def test_resume_during_compensating_stop_replays(self):
        """补停期间用户恢复播放 -> 应重新调度播放, 不能静音"""
        controller = _FakeController()
        receiver = _make_receiver(controller)
        calls = {"n": 0}

        async def play_url(url, audio_id=None):
            calls["n"] += 1
            controller.played.append(url)
            if calls["n"] == 1:
                receiver._playback_status = "paused"
            return True

        async def stop_speaker():
            controller.stopped += 1
            receiver._playback_status = "playing"
            return True

        controller.play_url = play_url
        controller.stop = stop_speaker

        async def scenario():
            receiver._running = True
            receiver._is_active = True
            receiver._playback_status = "playing"
            await receiver._do_play()
            for _ in range(100):
                await asyncio.sleep(0.02)
                if len(controller.played) >= 2 and receiver._speaker_active:
                    break
            assert len(controller.played) >= 2, "恢复播放后应重新 play_url"
            assert receiver._speaker_active is True
            await receiver.stop()

        asyncio.run(scenario())


class TestSoloistDownload:
    @staticmethod
    def _archive_bytes() -> bytes:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            payload = b"#!/bin/sh\necho fake soloist\n"
            info = tarfile.TarInfo("soloist")
            info.size = len(payload)
            info.mode = 0o755
            tar.addfile(info, io.BytesIO(payload))
        return buf.getvalue()

    def test_retry_prefers_ipv4_then_succeeds(self, tmp_path, monkeypatch):
        from app.engine.spotify.soloist import SoloistBinary

        binary = SoloistBinary(str(tmp_path))
        calls: list[bool] = []
        archive = self._archive_bytes()

        def fake_download(url, dest, prefer_ipv4=True):
            calls.append(prefer_ipv4)
            if len(calls) == 1:
                raise OSError("handshake operation timed out")
            with open(dest, "wb") as f:
                f.write(archive)

        monkeypatch.setattr(binary, "_download_to", fake_download)
        monkeypatch.setattr(soloist_mod.time, "sleep", lambda _s: None)
        binary._download_sync()

        assert calls == [True, True], "前两次应强制 IPv4"
        assert os.path.isfile(binary.path)
        assert os.access(binary.path, os.X_OK)

    def test_retry_exhausted_raises(self, tmp_path, monkeypatch):
        from app.engine.spotify.soloist import SoloistBinary

        binary = SoloistBinary(str(tmp_path))
        calls: list[bool] = []

        def always_fail(url, dest, prefer_ipv4=True):
            calls.append(prefer_ipv4)
            raise OSError("handshake operation timed out")

        monkeypatch.setattr(binary, "_download_to", always_fail)
        monkeypatch.setattr(soloist_mod.time, "sleep", lambda _s: None)

        with pytest.raises(RuntimeError):
            binary._download_sync()
        assert calls == [True, True, False], "最后一次应回退默认 IPv4+IPv6"


class TestPulseEnvPropagation:
    def test_child_env_contains_pulse_server(self, tmp_path):
        from app.engine.spotify.pulse import PulseAudio

        pa = PulseAudio(str(tmp_path))
        assert pa.child_env["PULSE_SERVER"] == f"unix:{pa.socket_path}"

    def test_soloist_process_keeps_pulse_env(self, tmp_path):
        from types import SimpleNamespace

        from app.engine.spotify.soloist import SoloistProcess

        proc = SoloistProcess(
            SimpleNamespace(path="/bin/true"),
            str(tmp_path),
            "dummy-key",
            "Test Device",
            12345,
            env={"PULSE_SERVER": "unix:/tmp/miair-spotify-pulse/native"},
        )
        assert proc.env["PULSE_SERVER"] == "unix:/tmp/miair-spotify-pulse/native"


ENTITY = {
    "uri": "spotify:track:test",
    "entity_type": "track",
    "decorations": {
        "identity": {"name": "Test Song"},
        "playback": {"duration_ms": 180000},
    },
}


class _FakePulse:
    socket_path = "/tmp/fake-pulse/native"

    def __init__(self):
        self.sink_volumes: list[int] = []

    async def set_sink_input_volume(self, percent: int = 100) -> bool:
        self.sink_volumes.append(percent)
        return True

    async def stop_capture(self) -> None:
        return None

    async def close(self) -> None:
        return None


class _FakeWs:
    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    async def send(self, command: str, **fields) -> bool:
        self.sent.append((command, fields))
        return True

    async def stop(self) -> None:
        return None


class TestSpotifyVolumeBehavior:
    def test_start_volume_uses_default_and_hardware_100(self):
        """开播: Spotify 软件音量=default_volume, 小爱硬件音量=100 (之后 App 控音量)"""
        controller = _FakeController()
        receiver = _make_receiver(controller)
        receiver.config.default_volume = 38
        receiver._pulse = _FakePulse()
        fake_ws = _FakeWs()
        receiver._ws = fake_ws

        async def scenario():
            receiver._running = True
            receiver._is_active = True
            await receiver._on_ws_event(
                {
                    "type": "playback_state",
                    "status": "playing",
                    "item": ENTITY,
                    "volume": 100,
                }
            )
            for _ in range(80):
                await asyncio.sleep(0.05)
                if fake_ws.sent and 100 in controller.volumes:
                    break
            assert fake_ws.sent == [("set_volume", {"volume": 38})], fake_ws.sent
            assert 100 in controller.volumes, f"硬件音量应为 100: {controller.volumes}"
            await receiver.stop()

        asyncio.run(scenario())

    def test_spotify_volume_change_does_not_touch_hardware(self):
        """App 调音量只改软件音量, 不再反复改硬件音量"""
        controller = _FakeController()
        receiver = _make_receiver(controller)
        receiver.config.default_volume = 38
        receiver._pulse = _FakePulse()
        fake_ws = _FakeWs()
        receiver._ws = fake_ws

        async def scenario():
            receiver._running = True
            receiver._speaker_active = True
            await receiver._on_ws_event({"type": "volume_changed", "volume": 65})
            await asyncio.sleep(0.4)
            assert controller.volumes == [], f"不应改硬件音量: {controller.volumes}"
            assert receiver._volume == 65
            await receiver.stop()

        asyncio.run(scenario())


# 接口状态汇总与状态映射测试

class _SpotifyCfg:
    def __init__(self, enabled=True, key="dummy", did="d1"):
        self.spotify_enabled = enabled
        self.spotify_api_key = key
        self.spotify_did = did
        self.spotify_device_name = ""


class _FakeSpotifyReceiver:
    def __init__(self, status):
        self._status = status

    def status(self):
        return self._status


class _FakeOrch:
    def __init__(self, cfg, receiver=None):
        self.config = cfg
        self._receiver = receiver

    def get_spotify_receiver(self):
        return self._receiver


class TestSpotifySpeakerStatus:
    def test_disabled(self):
        from app.services.spotify_status import collect_spotify_speaker_status

        info = collect_spotify_speaker_status(_FakeOrch(_SpotifyCfg(enabled=False)), "d1")
        assert info["enabled"] is False
        assert info["running"] is False

    def test_receiver_absent_target_inferred_from_config(self):
        from app.services.spotify_status import collect_spotify_speaker_status

        orch = _FakeOrch(_SpotifyCfg(did="d1"))
        assert collect_spotify_speaker_status(orch, "d1")["is_target"] is True
        assert collect_spotify_speaker_status(orch, "d2")["is_target"] is False

    def test_receiver_status_mapped(self):
        from app.services.spotify_status import collect_spotify_speaker_status

        receiver = _FakeSpotifyReceiver(
            {
                "active": True,
                "running": True,
                "paired": True,
                "playback": "playing",
                "target_did": "d1",
                "device_name": "Redmi小爱音箱Play",
                "track": {"title": "Song", "artist": "Artist"},
                "volume": 80,
                "last_error": "",
            }
        )
        orch = _FakeOrch(_SpotifyCfg(did="d1"), receiver)
        info = collect_spotify_speaker_status(orch, "d1")
        assert info["is_target"] is True
        assert info["active"] is True
        assert info["running"] is True
        assert info["paired"] is True
        assert info["playback"] == "playing"
        assert info["track"]["title"] == "Song"
        assert info["volume"] == 80
        assert collect_spotify_speaker_status(orch, "d2")["is_target"] is False


class _WsSpeaker:
    did = "d1"
    name = "Test Speaker"
    hardware = "LX06"
    enabled = True
    udn = "udn-1"

    def get_dlna_name(self) -> str:
        return "Test Speaker"

    def is_compatibility_mode(self) -> bool:
        return False


class _WsController:
    speaker = _WsSpeaker()


class _WsSpeakerManager:
    controllers = {"d1": _WsController()}


class _WsOrch:
    def __init__(self, cfg, receiver=None):
        self.config = cfg
        self.speaker_manager = _WsSpeakerManager()
        self.renderers = {"udn-1": object()}
        self.airplay_manager = None
        self.dlna_running = True
        self._receiver = receiver

    def get_spotify_receiver(self):
        return self._receiver

    def get_renderer_by_did(self, did):
        return None


class TestSpotifyStatusInApis:
    STATUS = {
        "active": True,
        "running": True,
        "paired": True,
        "playback": "playing",
        "target_did": "d1",
        "device_name": "Test Speaker",
        "track": {"title": "Song", "artist": "Artist", "album": "", "cover": ""},
        "volume": 80,
        "last_error": "",
    }

    def test_speakers_api_includes_spotify(self):
        from app.api.v1.speakers import list_speakers

        orch = _WsOrch(_SpotifyCfg(did="d1"), _FakeSpotifyReceiver(self.STATUS))
        data = asyncio.run(list_speakers(orch))
        assert data[0]["spotify"]["running"] is True
        assert data[0]["spotify"]["track"]["title"] == "Song"

    def test_websocket_status_includes_spotify_and_now_playing(self):
        from app.api.v1.ws import _collect_status

        orch = _WsOrch(_SpotifyCfg(did="d1"), _FakeSpotifyReceiver(self.STATUS))
        data = _collect_status(orch)
        sp = data["speakers"][0]
        assert sp["spotify"]["is_target"] is True
        assert sp["spotify"]["playback"] == "playing"
        # Spotify 播放时总览首行应展示曲目
        assert sp["now_playing"]["playing"] is True
        assert sp["now_playing"]["source"] == "spotify"
        assert sp["now_playing"]["title"] == "Song"

    def test_speakers_api_spotify_absent_when_disabled(self):
        from app.api.v1.speakers import list_speakers

        orch = _WsOrch(_SpotifyCfg(enabled=False), None)
        data = asyncio.run(list_speakers(orch))
        assert data[0]["spotify"]["enabled"] is False
        assert data[0]["spotify"]["running"] is False


class TestStopAndTransfer:
    @staticmethod
    def _playing_receiver(controller):
        from app.engine.spotify.receiver import SpotifyReceiver

        receiver = SpotifyReceiver(_FakeConfig(), controller, "127.0.0.1")
        receiver.config.default_volume = 38
        receiver._pulse = _FakePulse()
        receiver._ws = _FakeWs()
        receiver._running = True
        receiver._is_active = True
        return receiver

    def test_stop_restores_default_volume(self):
        """停止播放后硬件音量恢复到 default_volume"""
        controller = _FakeController()
        receiver = self._playing_receiver(controller)

        async def scenario():
            await receiver._on_ws_event(
                {"type": "playback_state", "status": "playing", "item": ENTITY, "volume": 100}
            )
            for _ in range(80):
                await asyncio.sleep(0.05)
                if 100 in controller.volumes:
                    break
            assert 100 in controller.volumes
            await receiver._stop_speaker()
            assert controller.volumes[-1] == 38, f"停止后应恢复 38: {controller.volumes}"
            assert receiver._volume_boosted is False
            await receiver.stop()

        asyncio.run(scenario())

    def test_transfer_to_other_device_stops_stream(self):
        """播放转移到其它设备 (auth_state is_active False) 时停止推流并恢复音量"""
        controller = _FakeController()
        receiver = self._playing_receiver(controller)
        receiver._speaker_active = True
        receiver._volume_boosted = True
        receiver._is_active = True

        async def scenario():
            await receiver._on_ws_event(
                {"type": "auth_state", "logged_in": True, "is_active": False}
            )
            for _ in range(40):
                await asyncio.sleep(0.05)
                if controller.stopped:
                    break
            assert controller.stopped >= 1, "转移后应停止音箱"
            assert controller.volumes and controller.volumes[-1] == 38
            await receiver.stop()

        asyncio.run(scenario())

    def test_logout_stops_stream(self):
        controller = _FakeController()
        receiver = self._playing_receiver(controller)
        receiver._speaker_active = True

        async def scenario():
            await receiver._on_ws_event(
                {"type": "auth_state", "logged_in": False, "is_active": False}
            )
            for _ in range(40):
                await asyncio.sleep(0.05)
                if controller.stopped:
                    break
            assert controller.stopped >= 1
            await receiver.stop()

        asyncio.run(scenario())

    def test_device_changed_inactive_stops_stream(self):
        controller = _FakeController()
        receiver = self._playing_receiver(controller)
        receiver._speaker_active = True

        async def scenario():
            await receiver._on_ws_event({"type": "device_changed", "is_active": False})
            for _ in range(40):
                await asyncio.sleep(0.05)
                if controller.stopped:
                    break
            assert controller.stopped >= 1
            await receiver.stop()

        asyncio.run(scenario())


class TestActiveDeviceGating:
    def test_inactive_playback_events_do_not_stream(self):
        """本机非活动设备时, 其它设备的 playing 事件不能触发推流"""
        controller = _FakeController()
        receiver = _make_receiver(controller)
        receiver._pulse = _FakePulse()
        receiver._ws = _FakeWs()

        async def scenario():
            receiver._running = True
            receiver._is_active = False
            await receiver._on_ws_event(
                {"type": "playback_state", "status": "playing", "item": ENTITY, "volume": 100}
            )
            await receiver._on_ws_event({"type": "playback_changed", "status": "playing"})
            await asyncio.sleep(0.4)
            assert controller.played == [], "非活动设备不应触发 play_url"
            assert receiver._playback_status == "idle"
            await receiver.stop()

        asyncio.run(scenario())

    def test_become_active_then_playing_triggers_stream(self):
        """设备切换为本机活动设备后, 播放事件才允许推流"""
        controller = _FakeController()
        receiver = _make_receiver(controller)
        receiver._pulse = _FakePulse()
        receiver._ws = _FakeWs()

        async def scenario():
            receiver._running = True
            receiver._is_active = False
            await receiver._on_ws_event({"type": "playback_changed", "status": "playing"})
            await asyncio.sleep(0.2)
            assert controller.played == []
            # 切换为本机活动设备
            await receiver._on_ws_event({"type": "device_changed", "is_active": True})
            await receiver._on_ws_event(
                {"type": "playback_state", "status": "playing", "item": ENTITY, "volume": 100}
            )
            for _ in range(80):
                await asyncio.sleep(0.05)
                if controller.played:
                    break
            assert controller.played, "成为活动设备后应触发推流"
            await receiver.stop()

        asyncio.run(scenario())


class TestActivationRecovery:
    def test_transfer_back_plays_without_new_playing_event(self):
        """从其它设备切回本机时, 即使没有新的 playing 事件也应补触发推流"""
        controller = _FakeController()
        receiver = _make_receiver(controller)
        receiver._pulse = _FakePulse()
        receiver._ws = _FakeWs()

        async def scenario():
            receiver._running = True
            receiver._is_active = False
            # 其它设备在播放: 只记录集群状态, 不推流
            await receiver._on_ws_event({"type": "playback_changed", "status": "playing"})
            await asyncio.sleep(0.2)
            assert controller.played == []
            # 切换回本机
            await receiver._on_ws_event({"type": "device_changed", "is_active": True})
            for _ in range(80):
                await asyncio.sleep(0.05)
                if controller.played:
                    break
            assert controller.played, "切回本机后应根据最近状态补触发推流"
            await receiver.stop()

        asyncio.run(scenario())

    def test_stale_inactive_playback_state_does_not_block(self):
        """playback_state 里过期的 is_active=false 不应把已激活状态降级"""
        controller = _FakeController()
        receiver = _make_receiver(controller)
        receiver._pulse = _FakePulse()
        receiver._ws = _FakeWs()

        async def scenario():
            receiver._running = True
            receiver._is_active = True
            await receiver._on_ws_event(
                {
                    "type": "playback_state",
                    "status": "playing",
                    "is_active": False,
                    "item": ENTITY,
                    "volume": 100,
                }
            )
            for _ in range(80):
                await asyncio.sleep(0.05)
                if controller.played:
                    break
            assert receiver._is_active is True, "过期的 false 不应降级活动状态"
            assert controller.played, "仍应正常推流"
            await receiver.stop()

        asyncio.run(scenario())


class TestActiveDeviceLogSignal:
    def test_parse_active_device_change(self):
        from app.engine.spotify.soloist import parse_active_device_change

        assert parse_active_device_change("2026-10-19 16:15:23.222: became active device") is True
        assert parse_active_device_change("2026-10-19 16:15:39.765: no longer active device") is False
        assert parse_active_device_change("playing spotify:track:x") is None
        assert parse_active_device_change("") is None

    def test_log_signal_active_triggers_playback(self):
        """Soloist 日志 'became active device' 且集群在播放时应补触发推流"""
        controller = _FakeController()
        receiver = _make_receiver(controller)
        receiver._pulse = _FakePulse()
        receiver._ws = _FakeWs()

        async def scenario():
            receiver._running = True
            receiver._is_active = False
            receiver._last_cluster_status = "playing"
            await receiver._handle_active_changed(True)
            for _ in range(80):
                await asyncio.sleep(0.05)
                if controller.played:
                    break
            assert receiver._is_active is True
            assert controller.played, "日志信号激活后应触发推流"
            await receiver.stop()

        asyncio.run(scenario())

    def test_log_signal_inactive_stops_stream(self):
        controller = _FakeController()
        receiver = _make_receiver(controller)
        receiver._pulse = _FakePulse()
        receiver._ws = _FakeWs()

        async def scenario():
            receiver._running = True
            receiver._is_active = True
            receiver._speaker_active = True
            await receiver._handle_active_changed(False)
            for _ in range(40):
                await asyncio.sleep(0.05)
                if controller.stopped:
                    break
            assert controller.stopped >= 1, "日志信号失活后应停止推流"
            await receiver.stop()

        asyncio.run(scenario())
