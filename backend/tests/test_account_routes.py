import os
import time
from unittest.mock import AsyncMock, MagicMock

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


def test_qr_poll_confirmed_persists_full_credentials(client, monkeypatch, tmp_path):
    """回归 (2026-09-06 事故): 扫码落盘必须整串解析 cookie, passToken 不能丢成空串

    parse_qs(cookie.replace(";", "&")) 会把分号后的键名解析成 " passToken"
    (带前导空格), 落盘 passToken 为空 → 重启登录永远失败 → 触发「恢复→重启」
    死循环。cookie 键值必须按 ";" 切分并去空格解析。
    """
    import json as _json
    from pathlib import Path

    from app.api.v1 import account as account_module

    c, orch, config = client
    token_path = tmp_path / ".mi.token"
    config.mi_token_home = str(token_path)

    confirmed = {
        "state": "confirmed",
        "message": "登录成功",
        "cookie": "userId=1992446; passToken=REAL_TOKEN_ABC",
        "user_id": "1992446",
        "token_info": {
            "user_id": "1992446",
            "device_id": "a" * 32,
            "services": {
                "micoapi": {"service_token": "ST-1", "ssecurity": "SEC-1", "expires_at": 0}
            },
        },
    }
    monkeypatch.setattr(
        account_module._qr_manager, "poll", AsyncMock(return_value=confirmed)
    )

    body = c.get("/api/v1/account/qrcode/poll", params={"session_id": "s1"}).json()

    assert body["success"] is True
    assert body["state"] == "confirmed"
    # config.cookie 携带真实 passToken (而非 "userId=xxx; passToken=")
    assert config.cookie == "userId=1992446; passToken=REAL_TOKEN_ABC"
    # .mi.token 同样落盘完整凭据 + micoapi 缓存
    saved = _json.loads(Path(token_path).read_text())
    assert saved["userId"] == "1992446"
    assert saved["passToken"] == "REAL_TOKEN_ABC"
    assert saved["deviceId"] == "a" * 32
    assert saved["micoapi"] == ["SEC-1", "ST-1"]


def test_account_status_parses_cookie_with_space_separator(client):
    """user_id 解析对 "; passToken=xxx" 形态同样健壮"""
    c, orch, config = client
    # fixture 里的 cookie 即 "userId=...; passToken=..." (分号后带空格)
    body = c.get("/api/v1/account/status").json()
    assert body["user_id"] == "test_user_001"
