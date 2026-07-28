"""日志等级接口测试 (system.py::get_log_level / set_log_level)"""

import logging
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.logging import LOG_NAME
from app.db import store
from app.main import create_app

# TestClient 不使用 with 语句 → 不执行 lifespan → 不启动 Orchestrator
client = TestClient(create_app())


@pytest.fixture(autouse=True)
def fresh_state():
    """每个测试前清空用户表, 并在测试后恢复日志等级"""
    store.init_db()
    with sqlite3.connect(get_settings().db_path) as conn:
        conn.execute("DELETE FROM users")
    original_level = logging.getLogger(LOG_NAME).level
    yield
    logging.getLogger(LOG_NAME).setLevel(original_level)


def _auth_headers():
    resp = client.post(
        "/api/v1/login/setup", json={"username": "admin", "password": "pass1234"}
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


class TestLogLevel:
    def test_requires_auth(self):
        assert client.get("/api/v1/logs/level").status_code in (401, 403)
        assert client.post(
            "/api/v1/logs/level", json={"level": "debug"}
        ).status_code in (401, 403)

    def test_get_current_level(self):
        headers = _auth_headers()
        resp = client.get("/api/v1/logs/level", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["level"] in ("debug", "info", "warning", "error")

    def test_set_and_get_roundtrip(self):
        headers = _auth_headers()
        for level in ("debug", "warning", "error", "info"):
            resp = client.post(
                "/api/v1/logs/level", json={"level": level}, headers=headers
            )
            assert resp.status_code == 200
            assert resp.json() == {"ok": True, "level": level}
            # logger 实际级别已同步修改
            assert logging.getLogger(LOG_NAME).level == getattr(
                logging, level.upper()
            )
            resp = client.get("/api/v1/logs/level", headers=headers)
            assert resp.json()["level"] == level

    def test_case_insensitive(self):
        headers = _auth_headers()
        resp = client.post(
            "/api/v1/logs/level", json={"level": "DEBUG"}, headers=headers
        )
        assert resp.status_code == 200
        assert resp.json()["level"] == "debug"

    def test_invalid_level_rejected(self):
        headers = _auth_headers()
        resp = client.post(
            "/api/v1/logs/level", json={"level": "verbose"}, headers=headers
        )
        assert resp.status_code == 400
