"""MiAccount 登录内核测试

覆盖生产事故 (2026-09-06 小米登录失效) 暴露的关键场景:
- passToken 有效: 一次 GET serviceLogin 换发成功, 不发 serviceLoginAuth2
- passToken 失效且未配置账密: 只发 GET, **绝不**发空账密密码请求 (70016 风控根因)
- 账密登录遇到二次验证 (notificationUrl / code==0 缺 userId): 优雅失败, 不再 KeyError
- 账密登录成功: serviceLoginAuth2 + STS 换取 serviceToken 并落盘
- 登录失败不破坏内存 token / token 文件 (缓存 serviceToken 留给降级链复用)
- deviceId 稳定: 非法占位值被替换为 32 位 hex 并跨实例保持

全部用 FakeSession 回放预设响应, 不触网。
"""

import asyncio
import hashlib
import json
import re
from http.cookies import SimpleCookie

from yarl import URL

from app.engine.mi_account import USER_AGENT_TEMPLATE, MiAccount

# 真实抓包的未登录响应 (生产日志与匿名实测一致)
BODY_SERVICE_LOGIN_70016 = (
    "&&&START&&&"
    '{"qs":"%3Fsid%3Dmicoapi%26_json%3Dtrue","code":70016,'
    '"description":"登录验证失败","securityStatus":0,'
    '"_sign":"58VY7HzpBxBcLsgTGFWDyJ7fEtw=","sid":"micoapi","result":"error",'
    '"miDemo":0,"captchaUrl":null,"callback":"https://api2.mina.mi.com/sts",'
    '"location":"","pwd":0,"child":0,"desc":"登录验证失败"}'
)

BODY_SERVICE_LOGIN_OK = (
    "&&&START&&&"
    '{"result":"ok","code":0,"location":"https://api2.mina.mi.com/sts?sid=micoapi",'
    '"userId":100,"passToken":"pt-1","nonce":1610098522385872896,'
    '"ssecurity":"sec-1"}'
)

# 账密通过但要求短信/邮箱二次验证的响应形态
BODY_AUTH2_NEED_VERIFY = (
    "&&&START&&&"
    '{"code":0,"result":"ok","securityStatus":7,'
    '"notificationUrl":"/fe/service/identity/authStart","location":"","pwd":1}'
)

# 生产日志中的崩溃形态: code==0 但无 userId (旧版 miservice 在此 KeyError)
BODY_AUTH2_CODE0_NO_USERID = (
    "&&&START&&&"
    '{"qs":"%3Fsid%3Dmicoapi%26_json%3Dtrue","code":0,"result":"ok",'
    '"sid":"micoapi","location":"","desc":"成功"}'
)

BODY_AUTH2_OK = (
    "&&&START&&&"
    '{"code":0,"result":"ok","location":"https://api2.mina.mi.com/sts?sid=micoapi",'
    '"userId":100,"passToken":"pt-new","nonce":12345,"ssecurity":"sec-2"}'
)

STS_URL = "https://api2.mina.mi.com/sts?sid=micoapi"


class FakeResponse:
    def __init__(self, body="", status=200, cookies=None, url=STS_URL):
        self._body = body
        self.status = status
        self.cookies = SimpleCookie()
        for key, value in (cookies or {}).items():
            self.cookies[key] = value
        self.url = URL(url)

    async def read(self):
        return self._body.encode("utf-8")

    async def text(self):
        return self._body


class _FakeCM:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *exc):
        return False


class _FakeCookieJar:
    """极简 cookie jar: 仅满足 _fetch_service_token 的兜底查询"""

    def filter_cookies(self, url):
        return SimpleCookie()


class FakeSession:
    """记录请求并按序回放预设响应"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.cookie_jar = _FakeCookieJar()

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": str(url), "kwargs": kwargs})
        remaining = self.responses.pop(0) if self.responses else FakeResponse()
        return _FakeCM(remaining)

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)


def _make_account(tmp_path, responses, username="", password=""):
    token_path = tmp_path / ".mi.token"
    session = FakeSession(responses)
    account = MiAccount(session, username, password, token_store=str(token_path))
    return account, session, token_path


def _run(coro):
    return asyncio.run(coro)


class TestPassTokenExchange:
    def test_exchange_success_single_get(self, tmp_path):
        """passToken 有效: 一次 GET 换发成功, 不碰 serviceLoginAuth2"""
        account, session, token_path = _make_account(
            tmp_path,
            [
                FakeResponse(body=BODY_SERVICE_LOGIN_OK),
                FakeResponse(body="ok", cookies={"serviceToken": "st-1"}),
            ],
        )
        account.token["userId"] = "100"
        account.token["passToken"] = "pt-old"

        ok = _run(account.login("micoapi"))

        assert ok is True
        assert account.token["micoapi"] == ("sec-1", "st-1")
        assert account.token["passToken"] == "pt-1"
        assert [c["method"] for c in session.calls] == ["GET", "GET"]
        assert "serviceLoginAuth2" not in session.calls[0]["url"]
        # 携带 sdkVersion/deviceId/passToken cookie
        cookies = session.calls[0]["kwargs"]["cookies"]
        assert cookies["sdkVersion"] == "3.8.6"
        assert cookies["passToken"] == "pt-old"
        assert cookies["deviceId"] == account._device_id
        # token 已落盘 (json 序列化后 tuple 变 list)
        saved = json.loads(token_path.read_text())
        assert saved["micoapi"] == ["sec-1", "st-1"]

    def test_dead_pass_token_without_password_never_posts_auth2(self, tmp_path):
        """passToken 失效且未配置账密: 只发 GET, 绝不发空账密密码请求

        回归: 原版 miservice 在此场景会 POST serviceLoginAuth2(user="", hash=md5("")),
        每 2h 触发一次 70016, 是小米风控的直接输入。
        """
        account, session, _ = _make_account(
            tmp_path, [FakeResponse(body=BODY_SERVICE_LOGIN_70016)]
        )
        account.token["userId"] = "100"
        account.token["passToken"] = "dead"

        ok = _run(account.login("micoapi"))

        assert ok is False
        assert len(session.calls) == 1
        assert session.calls[0]["method"] == "GET"

    def test_failure_keeps_cached_service_token(self, tmp_path):
        """登录失败不清空 token / 不删文件, 缓存 serviceToken 留给降级链复用"""
        account, session, _ = _make_account(
            tmp_path, [FakeResponse(body=BODY_SERVICE_LOGIN_70016)]
        )
        account.token.update(
            {"userId": "100", "passToken": "dead", "micoapi": ("sec", "st")}
        )

        ok = _run(account.login("micoapi"))

        assert ok is False
        assert account.token["micoapi"] == ("sec", "st")


class TestPasswordLogin:
    def test_needs_verify_is_graceful(self, tmp_path):
        """账密通过但要求短信/邮箱二次验证 → 优雅失败"""
        account, session, _ = _make_account(
            tmp_path,
            [
                FakeResponse(body=BODY_SERVICE_LOGIN_70016),
                FakeResponse(body=BODY_AUTH2_NEED_VERIFY),
            ],
            username="13800000000",
            password="pwd",
        )

        ok = _run(account.login("micoapi"))

        assert ok is False
        assert [c["method"] for c in session.calls] == ["GET", "POST"]
        data = session.calls[1]["kwargs"]["data"]
        assert data["user"] == "13800000000"
        assert data["hash"] == hashlib.md5(b"pwd").hexdigest().upper()

    def test_code0_without_user_id_no_crash(self, tmp_path):
        """code==0 但缺 userId 的响应形态 → 返回 False, 不再 KeyError 崩溃

        回归: 生产日志 2026-09-06 05:11:44 KeyError: 'userId' (miservice
        miaccount.py:74), 导致三级降级链的账密兜底永远走不通。
        """
        account, session, _ = _make_account(
            tmp_path,
            [
                FakeResponse(body=BODY_SERVICE_LOGIN_70016),
                FakeResponse(body=BODY_AUTH2_CODE0_NO_USERID),
            ],
            username="13800000000",
            password="pwd",
        )

        ok = _run(account.login("micoapi"))

        assert ok is False
        # 未发起 STS 请求
        assert [c["method"] for c in session.calls] == ["GET", "POST"]

    def test_password_login_success(self, tmp_path):
        """账密登录成功: Auth2 → STS 换取 serviceToken, 新 passToken 落盘"""
        account, session, token_path = _make_account(
            tmp_path,
            [
                FakeResponse(body=BODY_SERVICE_LOGIN_70016),
                FakeResponse(body=BODY_AUTH2_OK),
                FakeResponse(body="ok", cookies={"serviceToken": "st-2"}),
            ],
            username="13800000000",
            password="pwd",
        )

        ok = _run(account.login("micoapi"))

        assert ok is True
        assert account.token["micoapi"] == ("sec-2", "st-2")
        assert account.token["passToken"] == "pt-new"
        # STS 请求携带 _userIdNeedEncrypt + clientSign
        sts_url = session.calls[2]["url"]
        assert "_userIdNeedEncrypt=true" in sts_url
        assert "clientSign=" in sts_url
        saved = json.loads(token_path.read_text())
        assert saved["micoapi"] == ["sec-2", "st-2"]


class TestDeviceId:
    def test_invalid_device_id_replaced_and_stable(self, tmp_path):
        """非法占位 deviceId (miair_device) 被替换为 32 位 hex 并跨实例稳定"""
        token_path = tmp_path / ".mi.token"
        token_path.write_text(
            json.dumps({"userId": "100", "passToken": "pt", "deviceId": "miair_device"})
        )

        account1 = MiAccount(FakeSession([]), "", "", token_store=str(token_path))
        assert re.fullmatch(r"[0-9a-f]{32}", account1._device_id)
        # 已立即落盘, 下个实例沿用同一标识
        assert json.loads(token_path.read_text())["deviceId"] == account1._device_id
        account2 = MiAccount(FakeSession([]), "", "", token_store=str(token_path))
        assert account2._device_id == account1._device_id

    def test_user_agent_is_stable_mihome_shape(self, tmp_path):
        """UA 固定为米家 App 形态 (不再使用 fake_useragent 随机 UA)"""
        account, _, _ = _make_account(tmp_path, [])
        assert account.now_ua == USER_AGENT_TEMPLATE % account._device_id
        assert "APP/xiaomi.smarthome" in account.now_ua
