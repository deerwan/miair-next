"""登录 / 初始化 / 限速 / 改密码 接口测试 (不依赖真实音箱, 不触发 lifespan)"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.security import login_limiter
from app.db import store
from app.main import create_app

# TestClient 不使用 with 语句 → 不执行 lifespan → 不启动 Orchestrator
client = TestClient(create_app())

# TestClient 的模拟客户端 IP (限速按 IP 记录)
TEST_IP = "testclient"


@pytest.fixture(autouse=True)
def fresh_state():
    """每个测试前清空用户表与限速记录"""
    store.init_db()
    with sqlite3.connect(get_settings().db_path) as conn:
        conn.execute("DELETE FROM users")
    login_limiter.reset(TEST_IP)
    yield


def _setup_admin(username="admin", password="pass1234"):
    return client.post(
        "/api/v1/login/setup", json={"username": username, "password": password}
    )


class TestLoginStatus:
    def test_uninitialized(self):
        resp = client.get("/api/v1/login/status")
        assert resp.status_code == 200
        assert resp.json()["initialized"] is False

    def test_initialized_after_setup(self):
        _setup_admin()
        resp = client.get("/api/v1/login/status")
        assert resp.json()["initialized"] is True


class TestSetup:
    def test_setup_returns_token(self):
        resp = _setup_admin()
        assert resp.status_code == 200
        assert resp.json()["access_token"]

    def test_duplicate_setup_rejected(self):
        _setup_admin()
        resp = _setup_admin(username="hacker", password="evil12345")
        assert resp.status_code == 400


class TestLogin:
    def test_login_success(self):
        _setup_admin()
        resp = client.post(
            "/api/v1/login", json={"username": "admin", "password": "pass1234"}
        )
        assert resp.status_code == 200
        assert resp.json()["access_token"]

    def test_login_wrong_password(self):
        _setup_admin()
        resp = client.post(
            "/api/v1/login", json={"username": "admin", "password": "wrong"}
        )
        assert resp.status_code == 401

    def test_login_unknown_user(self):
        _setup_admin()
        resp = client.post(
            "/api/v1/login", json={"username": "nobody", "password": "pass1234"}
        )
        assert resp.status_code == 401

    def test_rate_limit_locks_after_max_failures(self):
        _setup_admin()
        max_failures = get_settings().login_max_failures
        for _ in range(max_failures):
            resp = client.post(
                "/api/v1/login", json={"username": "admin", "password": "wrong"}
            )
            assert resp.status_code == 401
        # 超过阈值后即使密码正确也被锁定
        resp = client.post(
            "/api/v1/login", json={"username": "admin", "password": "pass1234"}
        )
        assert resp.status_code == 429

    def test_success_resets_failures(self):
        _setup_admin()
        for _ in range(get_settings().login_max_failures - 1):
            client.post("/api/v1/login", json={"username": "admin", "password": "wrong"})
        resp = client.post(
            "/api/v1/login", json={"username": "admin", "password": "pass1234"}
        )
        assert resp.status_code == 200
        # 成功后计数清零, 再次失败不应立刻锁定
        resp = client.post(
            "/api/v1/login", json={"username": "admin", "password": "wrong"}
        )
        assert resp.status_code == 401


class TestMe:
    def test_me_with_valid_token(self):
        token = _setup_admin().json()["access_token"]
        resp = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["username"] == "admin"

    def test_me_without_token(self):
        resp = client.get("/api/v1/me")
        assert resp.status_code in (401, 403)

    def test_me_with_invalid_token(self):
        resp = client.get(
            "/api/v1/me", headers={"Authorization": "Bearer not-a-jwt"}
        )
        assert resp.status_code in (401, 403)


class TestChangePassword:
    def test_change_password_flow(self):
        token = _setup_admin().json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 原密码错误 → 400
        resp = client.post(
            "/api/v1/login/password",
            json={"old_password": "wrong", "new_password": "newpass123"},
            headers=headers,
        )
        assert resp.status_code == 400

        # 原密码正确 → 修改成功
        resp = client.post(
            "/api/v1/login/password",
            json={"old_password": "pass1234", "new_password": "newpass123"},
            headers=headers,
        )
        assert resp.status_code == 200

        # 旧密码失效, 新密码可登录
        resp = client.post(
            "/api/v1/login", json={"username": "admin", "password": "pass1234"}
        )
        assert resp.status_code == 401
        resp = client.post(
            "/api/v1/login", json={"username": "admin", "password": "newpass123"}
        )
        assert resp.status_code == 200
