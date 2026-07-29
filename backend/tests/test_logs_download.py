"""日志下载接口测试 (system.py::download_logs)

download_logs 依赖 orchestrator.config.log_file, 这里注入一个最小 stub,
避免启动完整 lifespan (会拉起真实 DLNA/AirPlay 网络服务)。
"""

import os
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db import store
from app.main import create_app

app = create_app()
client = TestClient(app)


@pytest.fixture(autouse=True)
def fresh_state(tmp_path):
    """清空用户表, 并注入指向临时日志文件的 orchestrator stub"""
    store.init_db()
    with sqlite3.connect(get_settings().db_path) as conn:
        conn.execute("DELETE FROM users")
    log_file = tmp_path / "miair.log"
    app.state.orchestrator = SimpleNamespace(config=SimpleNamespace(log_file=str(log_file)))
    yield log_file
    if hasattr(app.state, "orchestrator"):
        delattr(app.state, "orchestrator")


def _auth_headers():
    resp = client.post(
        "/api/v1/login/setup", json={"username": "admin", "password": "pass1234"}
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


class TestLogDownload:
    def test_requires_auth(self):
        assert client.get("/api/v1/logs/download").status_code in (401, 403)

    def test_download_returns_file_content(self, fresh_state):
        log_file = fresh_state
        log_file.write_text("hello miair log\nsecond line\n", encoding="utf-8")
        resp = client.get("/api/v1/logs/download", headers=_auth_headers())
        assert resp.status_code == 200
        assert resp.text == "hello miair log\nsecond line\n"
        assert "miair.log" in resp.headers.get("content-disposition", "")

    def test_missing_file_returns_404(self, fresh_state):
        # 未写入日志文件 → 文件不存在
        assert not os.path.isfile(fresh_state)
        resp = client.get("/api/v1/logs/download", headers=_auth_headers())
        assert resp.status_code == 404
