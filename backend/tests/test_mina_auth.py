"""MinaAuth 交互式登录测试

覆盖三种登录方式的关键路径:
- 密码登录成功 (serviceLogin → serviceLoginAuth2 → STS)
- 图形验证码: 首次登录返回 need_captcha (自动取图+ick), 续步 captCode 成功
- 短信/邮箱验证: 返回 need_verify, verifyTicket → 收 passToken → 换 serviceToken
- 手动 Token / 扫码确认: refresh_by_pass_token 成功与失效场景
- PasswordLoginService 会话: 成功后清理, 过期会话提示重登

全部用 FakeSession 回放预设响应, 不触网。
"""

import asyncio
import base64
import json
from http.cookies import SimpleCookie

from yarl import URL

from app.engine import mina_auth as mina_auth_module
from app.engine.mina_auth import (
    MINA_SID,
    MinaAuth,
    PasswordLoginService,
)


def _body(obj: dict) -> str:
    return "&&&START&&&" + json.dumps(obj, ensure_ascii=False)


# serviceLogin 匿名/无效响应 (携带 step1 所需的签名参数)
BODY_STEP1 = _body({
    "qs": "%3Fsid%3Dmicoapi%26_json%3Dtrue",
    "code": 70016,
    "description": "登录验证失败",
    "_sign": "58VY7HzpBxBcLsgTGFWDyJ7fEtw=",
    "sid": "micoapi",
    "result": "error",
    "callback": "https://api2.mina.mi.com/sts",
    "location": "",
    "desc": "登录验证失败",
})

BODY_AUTH2_OK = _body({
    "code": 0,
    "result": "ok",
    "location": "https://api2.mina.mi.com/sts?sid=micoapi",
    "userId": 100,
    "passToken": "pt-new",
    "nonce": 12345,
    "ssecurity": "sec-2",
})

BODY_AUTH2_CAPTCHA = _body({
    "code": 70016,
    "description": "登录验证失败",
    "captchaUrl": "https://account.xiaomi.com/captcha/gen?ick=abc",
})

BODY_AUTH2_NEED_VERIFY = _body({
    "code": 0,
    "result": "ok",
    "securityStatus": 7,
    "notificationUrl": "/fe/service/identity/authStart",
    "location": "",
    "pwd": 1,
})

BODY_VERIFY_TICKET_OK = _body({
    "code": 0,
    "location": "https://account.xiaomi.com/fe/service/login?ticket=ok",
})

BODY_EXCHANGE_OK = _body({
    "result": "ok",
    "code": 0,
    "location": "https://api2.mina.mi.com/sts?sid=micoapi",
    "userId": 100,
    "passToken": "pt-1",
    "nonce": 1610098522385872896,
    "ssecurity": "sec-1",
})

BODY_EXCHANGE_DEAD = _body({
    "code": 70016,
    "description": "登录验证失败",
    "desc": "登录验证失败",
})

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
        if isinstance(self._body, bytes):
            return self._body
        return self._body.encode("utf-8")

    async def text(self):
        if isinstance(self._body, bytes):
            return self._body.decode("utf-8", "replace")
        return self._body


class _FakeCM:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *exc):
        return False


class FakeCookieJar:
    """极简 cookie jar: 预置 cookies + 收集响应 Set-Cookie"""

    def __init__(self, cookies=None):
        self._cookies = dict(cookies or {})

    def update_cookies(self, cookies, url=None):
        self._cookies.update(cookies)

    def filter_cookies(self, url):
        jar = SimpleCookie()
        for key, value in self._cookies.items():
            jar[key] = value
        return jar


class FakeSession:
    """记录请求并按序回放预设响应"""

    def __init__(self, responses, jar_cookies=None):
        self.responses = list(responses)
        self.calls = []
        self.cookie_jar = FakeCookieJar(jar_cookies)
        self.closed = False

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": str(url), "kwargs": kwargs})
        remaining = self.responses.pop(0) if self.responses else FakeResponse()
        return _FakeCM(remaining)

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    async def close(self):
        self.closed = True


def _make_auth(responses, jar_cookies=None) -> tuple[MinaAuth, FakeSession]:
    session = FakeSession(responses, jar_cookies)
    return MinaAuth(session=session), session


class TestPasswordLogin:
    def test_direct_success(self):
        """密码直登成功: Auth2 → STS, token_info 完整, 无 captCode"""
        auth, session = _make_auth([
            FakeResponse(body=BODY_STEP1),
            FakeResponse(body=BODY_AUTH2_OK),
            FakeResponse(body="ok", cookies={"serviceToken": "st-2"}),
        ])

        result = asyncio.run(auth.login("13800000000", "pwd"))

        assert result["state"] == "success"
        assert result["token_info"]["user_id"] == "100"
        assert result["token_info"]["services"][MINA_SID]["service_token"] == "st-2"
        assert result["token_info"]["services"][MINA_SID]["ssecurity"] == "sec-2"
        methods = [c["method"] for c in session.calls]
        assert methods == ["GET", "POST", "GET"]
        # Auth2 表单: md5 大写密码哈希 + step1 签名参数, 无 captCode
        form = session.calls[1]["kwargs"]["data"]
        assert form["user"] == "13800000000"
        assert "captCode" not in form
        assert form["_sign"] == "58VY7HzpBxBcLsgTGFWDyJ7fEtw="
        # STS 请求携带 clientSign
        assert "clientSign=" in session.calls[2]["url"]

    def test_captcha_flow(self):
        """图形验证码: 首登返回 need_captcha (含 base64 图片), 续步 captCode 成功"""
        png_bytes = b"\x89PNG-fake-image"
        auth, session = _make_auth([
            FakeResponse(body=BODY_STEP1),
            FakeResponse(body=BODY_AUTH2_CAPTCHA),
            # getCaptchaImage: 图片二进制 + ick cookie
            FakeResponse(body=png_bytes, cookies={"ick": "ICK1"}),
            FakeResponse(body=BODY_AUTH2_OK),
            FakeResponse(body="ok", cookies={"serviceToken": "st-2"}),
        ])

        first = asyncio.run(auth.login("13800000000", "pwd"))
        assert first["state"] == "need_captcha"
        assert base64.b64decode(first["captcha_image"]) == png_bytes
        assert auth.captcha_ick == "ICK1"

        second = asyncio.run(auth.login_with_captcha("1234"))
        assert second["state"] == "success"

        # 第二次 Auth2 携带 captCode 与 ick cookie
        assert len(session.calls) == 5
        retry = session.calls[3]
        assert retry["method"] == "POST"
        assert retry["kwargs"]["data"]["captCode"] == "1234"
        assert retry["kwargs"]["cookies"] == {"ick": "ICK1"}

    def test_verify_flow(self):
        """短信/邮箱验证: 首登返回 need_verify, verifyTicket → 收 passToken → 换 token"""
        auth, session = _make_auth(
            [
                FakeResponse(body=BODY_STEP1),
                FakeResponse(body=BODY_AUTH2_NEED_VERIFY),
                # checkIdentityList (authStart → identity/list)
                FakeResponse(body="ok"),
                # verifyTicket
                FakeResponse(body=BODY_VERIFY_TICKET_OK),
                # followRedirectsForCookies
                FakeResponse(body="ok"),
                # exchange serviceLogin
                FakeResponse(body=BODY_EXCHANGE_OK),
                # step3 STS
                FakeResponse(body="ok", cookies={"serviceToken": "st-1"}),
            ],
            # 重定向链收集到的 cookies (模拟真实 jar 累积)
            jar_cookies={"passToken": "pt-verify", "userId": "100"},
        )

        first = asyncio.run(auth.login("13800000000", "pwd"))
        assert first["state"] == "need_verify"
        assert first["verify_type"] == "phone"
        assert first["verify_url"].endswith("/fe/service/identity/authStart")

        second = asyncio.run(auth.login_with_verify_code("8848"))
        assert second["state"] == "success"
        assert second["token_info"]["services"][MINA_SID]["service_token"] == "st-1"

        # verifyTicket: verifyPhone + trust + _flag=4
        verify_call = session.calls[2]
        assert "/identity/list" in verify_call["url"]
        ticket_call = session.calls[3]
        assert "/identity/auth/verifyPhone" in ticket_call["url"]
        assert ticket_call["kwargs"]["data"] == {
            "ticket": "8848",
            "trust": "true",
            "_json": "true",
            "_flag": "4",
        }


class TestRefreshByPassToken:
    def test_exchange_success_with_cuserid(self):
        """手动 Token / 扫码确认: passToken → micoapi serviceToken, 携带 cUserId"""
        auth, session = _make_auth([
            FakeResponse(body=BODY_EXCHANGE_OK),
            FakeResponse(body="ok", cookies={"serviceToken": "st-1"}),
        ])
        auth.set_cookie("cUserId", "c-1")

        result = asyncio.run(auth.refresh_by_pass_token("pt-old", "100"))

        assert result["state"] == "success"
        assert result["token_info"]["services"][MINA_SID]["service_token"] == "st-1"
        cookies = session.calls[0]["kwargs"]["cookies"]
        assert cookies["passToken"] == "pt-old"
        assert cookies["userId"] == "100"
        assert cookies["cUserId"] == "c-1"
        assert cookies["sdkVersion"] == "3.8.6"

    def test_exchange_dead_pass_token(self):
        """passToken 失效: 返回 failed 并带出 code, 不抛异常"""
        auth, session = _make_auth([FakeResponse(body=BODY_EXCHANGE_DEAD)])

        result = asyncio.run(auth.refresh_by_pass_token("dead", "100"))

        assert result["state"] == "failed"
        assert "70016" in result["error"]
        assert auth.token_info["services"] == {}


class TestPasswordLoginService:
    def test_success_extracts_credentials_and_cleans_session(self, monkeypatch):
        """登录成功: 返回 passToken/账密 (供持久化), 会话被清理"""
        service = PasswordLoginService()
        monkeypatch.setattr(
            mina_auth_module, "MinaAuth", lambda: _ok_auth()
        )

        result, session_id = asyncio.run(service.login("13800000000", "pwd"))

        assert result["state"] == "success"
        assert result["user_id"] == "100"
        assert result["pass_token"] == "pt-verify"
        assert result["username"] == "13800000000"
        assert result["password"] == "pwd"
        assert result["service_token"] == "st-2"
        assert session_id  # 终态后仍返回原会话 ID, 便于前端核对
        assert service._sessions == {}  # 会话已清理

    def test_expired_session_is_rejected(self):
        """未知/过期会话: 返回 failed 并提示重新登录"""
        svc = PasswordLoginService()

        result, session_id = asyncio.run(svc.submit_captcha("no-such", "1234"))

        assert result["state"] == "failed"
        assert "重新登录" in result["error"]
        assert session_id == ""


def _ok_auth() -> MinaAuth:
    """构造一次性成功的 MinaAuth (注入 FakeSession)"""
    auth = MinaAuth(
        session=FakeSession([
            FakeResponse(body=BODY_STEP1),
            FakeResponse(body=BODY_AUTH2_OK),
            FakeResponse(body="ok", cookies={"serviceToken": "st-2"}),
        ])
    )
    auth._client().cookie_jar._cookies.update({"passToken": "pt-verify", "userId": "100"})
    return auth
