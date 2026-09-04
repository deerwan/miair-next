"""serviceToken 三级凭证降级链测试 (恢复语义)

覆盖点:
- 级别1 passToken 换发成功 / 换发成功但校验不通过 (杜绝「假成功」)
- 级别3 账号密码兜底: 只有它能重新签发 passToken
- 未配置账号密码时不做无谓尝试
- 60s 冷却: 同一次失效事件只触发一次真实恢复 (防重登风暴)
- call_api: 登录失效自动恢复并重试一次, 非登录错误直接上抛

全部用 FakeAccount 替代 miservice.MiAccount, 不触网。
"""

import asyncio
import time

import pytest

from app.engine import auth as auth_module
from app.engine.auth import SERVICE_TOKEN_VALID_HOURS, AuthManager
from app.engine.config import Config

COOKIE = "userId=100; passToken=old-pass"


class FakeAccount:
    """替代 miservice.MiAccount

    约定: username 为空 = cookie/passToken 换发; 非空 = 账号密码登录。
    由 `cookie_login_ok` / `password_login_ok` 分别控制两者的结果,
    以覆盖「passToken 已死但账密可用」这类关键场景。
    """

    created: list["FakeAccount"] = []
    login_calls: list[str] = []
    cookie_login_ok = True
    password_login_ok = True

    def __init__(self, session, username="", password="", token_store=None):
        self.session = session
        self.username = username
        self.password = password
        self.token_store = token_store
        self.token = None
        FakeAccount.created.append(self)

    async def login(self, sid):
        FakeAccount.login_calls.append(self.username or "<cookie>")
        ok = (
            self.password_login_ok if self.username else self.cookie_login_ok
        )
        if not ok:
            # 对齐 miservice: 登录失败会把 token 置 None
            self.token = None
            return False
        self.token = {
            "userId": "100",
            "passToken": "new-pass" if self.username else "old-pass",
            "micoapi": ("ssecurity", "service-token"),
        }
        return True

    @classmethod
    def reset(cls, cookie_login_ok=True, password_login_ok=True):
        cls.created.clear()
        cls.login_calls.clear()
        cls.cookie_login_ok = cookie_login_ok
        cls.password_login_ok = password_login_ok


@pytest.fixture(autouse=True)
def fake_account(monkeypatch):
    FakeAccount.reset()
    monkeypatch.setattr(auth_module, "MiAccount", FakeAccount)
    return FakeAccount


class FakeMiNA:
    """替代 miservice.MiNAService: device_list 可编程失败/返回"""

    fail_queue: list[Exception] = []
    result: list = []

    def __init__(self, account):
        self.account = account

    async def device_list(self):
        if FakeMiNA.fail_queue:
            raise FakeMiNA.fail_queue.pop(0)
        return FakeMiNA.result

    @classmethod
    def reset(cls, fail_queue=None, result=None):
        cls.fail_queue = list(fail_queue or [])
        cls.result = list(result or [])


def _make_auth(tmp_path, cookie=COOKIE, account="", password=""):
    cfg = Config(conf_path=str(tmp_path))
    cfg.cookie = cookie
    cfg.account = account
    cfg.password = password
    return AuthManager(cfg)


def _validate(result: bool):
    """替换 AuthManager.validate_token: 跳过真实云端调用"""

    async def _fn(self, *args, **kwargs):
        return result

    return _fn


def _run(main):
    """在独立事件循环里执行, 并在结束后关闭 auth 持有的 aiohttp session"""

    async def _wrapper(auth, coro_factory):
        try:
            return await coro_factory()
        finally:
            await auth.close()

    auth, coro_factory = main
    return asyncio.run(_wrapper(auth, coro_factory))


class TestLevel1PassToken:
    def test_pass_token_refresh_success(self, tmp_path, monkeypatch):
        monkeypatch.setattr(AuthManager, "validate_token", _validate(True))
        auth = _make_auth(tmp_path)

        async def _main():
            ok = await auth.refresh_token()
            assert ok is True
            assert auth.is_logged_in() is True
            # 级别1 直接命中: 只构造一个 MiAccount (cookie 模式, 未传账密)
            assert len(FakeAccount.created) == 1
            assert FakeAccount.created[0].username == ""
            assert FakeAccount.login_calls == ["<cookie>"]
            # 过期时间应落到配置里, 供定时器计算剩余有效期
            assert auth.config.token_expires_at > time.time()

        _run((auth, _main))

    def test_no_fake_success_when_validation_fails(self, tmp_path, monkeypatch):
        """换发返回成功但校验不通过 → 不得判定为恢复成功

        把失效 token 当有效, 会让续期链条持续「假成功」。
        """
        monkeypatch.setattr(AuthManager, "validate_token", _validate(False))
        auth = _make_auth(tmp_path)

        async def _main():
            ok = await auth.refresh_token()
            assert ok is False
            assert auth.is_logged_in() is False
            # cookie 换发尝试一次, 无账密故没有第三次尝试
            assert FakeAccount.login_calls == ["<cookie>"]

        _run((auth, _main))


class TestLevel3PasswordFallback:
    def test_password_used_when_pass_token_dead(self, tmp_path, monkeypatch):
        monkeypatch.setattr(AuthManager, "validate_token", _validate(True))
        FakeAccount.cookie_login_ok = False
        auth = _make_auth(tmp_path, account="13800000000", password="pwd")

        async def _main():
            ok = await auth.refresh_token()
            assert ok is True
            assert auth.is_logged_in() is True
            # 先 passToken 换发 (失败), 再用账号密码重登
            assert FakeAccount.login_calls == ["<cookie>", "13800000000"]
            # 重新签发的 passToken 必须回写配置, 否则重启后又用回旧值
            assert "new-pass" in auth.config.cookie

        _run((auth, _main))

    def test_without_password_no_extra_attempt(self, tmp_path, monkeypatch):
        """未配置账密时不做无谓尝试 (避免账密登录失败时触发重登)"""
        monkeypatch.setattr(AuthManager, "validate_token", _validate(True))
        FakeAccount.cookie_login_ok = False
        auth = _make_auth(tmp_path)  # 无 account/password

        async def _main():
            ok = await auth.refresh_token()
            assert ok is False
            assert auth.is_logged_in() is False
            assert FakeAccount.login_calls == ["<cookie>"]

        _run((auth, _main))


class TestReloginCooldown:
    def test_second_attempt_within_cooldown_is_skipped(self, tmp_path, monkeypatch):
        """同一次失效事件上并发的多个 API 调用只触发一次真实恢复"""
        monkeypatch.setattr(AuthManager, "validate_token", _validate(True))
        auth = _make_auth(tmp_path)

        async def _main():
            first = await auth.handle_token_expired()
            second = await auth.handle_token_expired()
            return first, second

        first, second = _run((auth, _main))

        assert first is True
        assert second is False  # 冷却窗口内直接跳过
        assert FakeAccount.login_calls == ["<cookie>"]  # 只有一次真实换发


class TestCallApi:
    def test_retries_once_after_recovery(self, tmp_path, monkeypatch):
        monkeypatch.setattr(AuthManager, "validate_token", _validate(True))
        auth = _make_auth(tmp_path)
        attempts = []

        async def factory():
            attempts.append(1)
            if len(attempts) == 1:
                raise Exception(
                    "Error https://api2.mina.mi.com/remote/ubus: Login failed"
                )
            return "ok"

        result = _run((auth, lambda: auth.call_api(factory, label="test")))

        assert result == "ok"
        assert len(attempts) == 2
        assert FakeAccount.login_calls == ["<cookie>"]

    def test_non_login_error_is_not_retried(self, tmp_path, monkeypatch):
        """网络超时等临时问题不该触发重登 (否则会放大成重登风暴)"""
        monkeypatch.setattr(AuthManager, "validate_token", _validate(True))
        auth = _make_auth(tmp_path)
        attempts = []

        async def factory():
            attempts.append(1)
            raise Exception("Connection timeout to host https://api2.mina.mi.com")

        with pytest.raises(Exception, match="Connection timeout"):
            _run((auth, lambda: auth.call_api(factory, label="test")))

        assert len(attempts) == 1
        assert FakeAccount.login_calls == []


class TestTokenExpiry:
    def test_expires_at_uses_configured_valid_hours(self, tmp_path, monkeypatch):
        monkeypatch.setattr(AuthManager, "validate_token", _validate(True))
        auth = _make_auth(tmp_path)

        async def _main():
            await auth.refresh_token()
            return auth.config.token_expires_at

        expires_at = _run((auth, _main))
        expected = SERVICE_TOKEN_VALID_HOURS * 3600
        assert expires_at == pytest.approx(time.time() + expected, abs=30)


class TestDoLogin:
    def test_password_login_false_is_not_pseudo_success(self, tmp_path, monkeypatch):
        """miservice 的 login() 失败时返回 False 而非抛异常, 不得误标为登录成功"""
        FakeAccount.password_login_ok = False
        monkeypatch.setattr(AuthManager, "validate_token", _validate(True))
        auth = _make_auth(tmp_path, cookie="", account="13800000000", password="pwd")

        async def _main():
            await auth.login()
            return auth.is_logged_in()

        assert _run((auth, _main)) is False


class TestDeviceListRecovery:
    """get_device_list 故障自愈路径 (账密模式, cookie 为空场景)"""

    def _make_password_auth(self, tmp_path, monkeypatch) -> AuthManager:
        monkeypatch.setattr(AuthManager, "validate_token", _validate(True))
        monkeypatch.setattr(auth_module, "MiNAService", FakeMiNA)
        # 阻断 _sync_pass_token_to_config 回写 cookie, 保持「cookie 为空」的
        # 账密分支 (真实场景它负责处理轮换回写, 与本测试无关)
        monkeypatch.setattr(
            AuthManager, "_sync_pass_token_to_config", lambda self: None
        )
        return _make_auth(tmp_path, cookie="", account="13800000000", password="pwd")

    def test_recovers_after_transient_device_list_failure(self, tmp_path, monkeypatch):
        """device_list 偶发网络失败 → 丢弃 session 重登 → 重试成功

        回归: 旧实现 close() 后直接 login(), 而 close() 置位的 _closed 标记
        会让 login() 直接跳过, AuthManager 从此假死直到进程重启。
        """
        FakeMiNA.reset(
            fail_queue=[Exception("Connection timeout to mina")], result=["dev1"]
        )
        auth = self._make_password_auth(tmp_path, monkeypatch)

        async def _main():
            devices = await auth.get_device_list()
            return (
                devices,
                auth._closed,
                auth.is_logged_in(),
                auth._refresh_task is not None and not auth._refresh_task.done(),
            )

        devices, was_closed, logged_in, refresh_running = _run((auth, _main))

        assert devices == ["dev1"]  # 重登后重试成功
        # 首次登录 + 自愈重登, 共两次真实登录
        assert FakeAccount.login_calls == ["13800000000", "13800000000"]
        assert was_closed is False  # 实例仍可用 (而非假死)
        assert logged_in is True
        assert refresh_running is True  # close() 停掉的续期任务已重新拉起
