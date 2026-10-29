import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks

from app.api.v1.settings import save_settings_api
from app.engine.config import Config
from app.models.schemas import SettingsPayload


@pytest.mark.parametrize("value", [0, 1, 2399, 2401, 2701, 3999, 4000])
def test_one_ms_calibration_roundtrip_preserves_other_settings(tmp_path, value):
    config = Config(conf_path=str(tmp_path), airplay_mode="airplay2",
                    airplay2_output_latency_ms=2400, dlna_port=8201,
                    default_volume=60, cookie="test-cookie-do-not-change",
                    spotify_enabled=True, spotify_did="spotify-target",
                    spotify_device_name="Spotify speaker", spotify_api_key="test-key")
    restart = AsyncMock()
    background = BackgroundTasks()
    result = asyncio.run(save_settings_api(
        SettingsPayload(airplay2_output_latency_ms=value), background,
        orch=SimpleNamespace(restart_dlna_services=restart), config=config))
    assert result["ok"]
    loaded = Config.load(str(tmp_path))
    assert loaded.airplay2_output_latency_ms == value
    assert loaded.airplay_mode == "airplay2"
    assert loaded.dlna_port == 8201
    assert loaded.default_volume == 60
    assert loaded.cookie == "test-cookie-do-not-change"
    assert loaded.spotify_enabled
    assert loaded.spotify_did == "spotify-target"
    assert loaded.spotify_device_name == "Spotify speaker"
    assert loaded.spotify_api_key == "test-key"
    assert len(background.tasks) == 1
    assert background.tasks[0].func == restart
    restart.assert_not_awaited()


@pytest.mark.parametrize("value", [-1, 4001, 2400.5])
def test_invalid_calibration_rejected(value):
    with pytest.raises(ValueError):
        SettingsPayload(airplay2_output_latency_ms=value)


def test_spotify_only_save_preserves_airplay2_configuration(tmp_path):
    config = Config(conf_path=str(tmp_path), airplay_mode="airplay2",
                    airplay2_target_did="airplay-target", airplay2_port=7012,
                    airplay2_output_latency_ms=2701)
    background = BackgroundTasks()
    result = asyncio.run(save_settings_api(
        SettingsPayload(spotify_enabled=True, spotify_did="spotify-target"), background,
        orch=SimpleNamespace(restart_dlna_services=AsyncMock()), config=config))
    assert result["ok"]
    loaded = Config.load(str(tmp_path))
    assert loaded.spotify_enabled
    assert loaded.spotify_did == "spotify-target"
    assert loaded.airplay_mode == "airplay2"
    assert loaded.airplay2_target_did == "airplay-target"
    assert loaded.airplay2_port == 7012
    assert loaded.airplay2_output_latency_ms == 2701
