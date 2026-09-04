import os
import time
from unittest.mock import MagicMock, AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.deps as deps
from app.api.v1.account import router as account_router


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(account_router, prefix="/api/v1")
    orch = MagicMock()
    orch.auth.is_logged_in.return_value = True
    orch.auth._refresh_task = None
    orch.restart_dlna_services = AsyncMock()
    config = MagicMock()
    config.cookie = "userId=test_user_001; passToken=test_token_abc"
    config.token_expires_at = time.time() + 7200
    config.account = "test_user_001"
    config.password = "test_password_xyz"

    app.dependency_overrides[deps.get_orchestrator] = lambda: orch
    app.dependency_overrides[deps.get_engine_config] = lambda: config
    with TestClient(app) as c:
        yield c, orch, config
    app.dependency_overrides.clear()


def test_account_status_healthy(client):
    c, orch, config = client
    config.token_expires_at = time.time() + 4 * 3600  # 4h > 3h 阈值
    body = c.get("/api/v1/account/status").json()
    assert body["user_id"] == "test_user_001"
    assert body["status"] == "healthy"
    assert body["has_password_fallback"] is True
    assert body["logged_in"] is True
    assert body["service_token_remaining_hours"] is not None


def test_account_status_expiring(client):
    c, orch, config = client
    config.token_expires_at = time.time() + 3600  # 1h < 3h 阈值
    body = c.get("/api/v1/account/status").json()
    assert body["status"] == "expiring"


def test_account_status_expired(client):
    c, orch, config = client
    config.token_expires_at = time.time() - 100
    body = c.get("/api/v1/account/status").json()
    assert body["status"] == "expired"


def test_account_status_offline(client):
    c, orch, config = client
    orch.auth.is_logged_in.return_value = False
    body = c.get("/api/v1/account/status").json()
    assert body["status"] == "offline"


def test_delete_account_clears_credentials(tmp_path):
    app = FastAPI()
    app.include_router(account_router, prefix="/api/v1")
    orch = MagicMock()
    orch.restart_dlna_services = AsyncMock()
    config = MagicMock()
    config.cookie = "userId=1; passToken=x"
    config.account = "1"
    config.password = "pw"
    config.token_expires_at = 123.0
    token_file = tmp_path / ".mi.token"
    token_file.write_text("{}")
    config.mi_token_home = str(token_file)

    app.dependency_overrides[deps.get_orchestrator] = lambda: orch
    app.dependency_overrides[deps.get_engine_config] = lambda: config
    with TestClient(app) as c:
        r = c.delete("/api/v1/account")
    app.dependency_overrides.clear()

    assert r.status_code == 200
    assert r.json()["ok"] is True
    # 所有凭证被清空
    assert config.cookie == ""
    assert config.account == ""
    assert config.password == ""
    assert config.token_expires_at == 0.0
    # 触发热重启
    orch.restart_dlna_services.assert_called_once()
    # token 文件被删除
    assert not os.path.exists(str(token_file))
