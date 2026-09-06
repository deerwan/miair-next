"""小米交互式登录

三种登录方式的交互态都由本模块承载 (无人值守的静默续期/静默重登见 mi_account.py):
1. 密码登录: login() → serviceLogin → serviceLoginAuth2 → STS;
   触发图形验证码时前端展示 get_captcha_image() 取回的图片,
   用户输入后用 login_with_captcha() 续步 (captCode + ick);
   触发短信/邮箱验证时用 login_with_verify_code() 续步
   (verifyTicket → identity/list → 重定向收 passToken → serviceLogin 换 token);
2. 手动 Token: refresh_by_pass_token() (passToken + userId → micoapi serviceToken);
3. 扫码登录: qr_login.py 负责取码/轮询, 确认后同样走 refresh_by_pass_token()
   立即换取 serviceToken。

说明:
- HTTP 层用 aiohttp 自动重定向 + CookieJar(unsafe=True) 等价替代 fetchWithRedirects
  的手动重定向链 (登录链跨 account.xiaomi.com 与 api2.mina.mi.com, unsafe 保留跨域
  cookie, 与 qr_login.py 同一做法);
- 登录会话以生成的 session_id 为键, 适配本项目单账号
  与扫码 API 已有的 session_id 模式;
- 凭证的持久化部分由 API 层完成 (config.cookie / .mi.token /
  热重启), 本模块只负责登录状态机。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import secrets
import time

import aiohttp
from urllib.parse import quote

from yarl import URL

from app.engine.mi_account import (
    ACCOUNT_BASE_URL,
    SDK_VERSION,
    USER_AGENT_TEMPLATE,
    new_device_id,
)

log = logging.getLogger("miair")
# 目标服务: 小爱音箱 API 对应 micoapi
MINA_SID = "micoapi"
# serviceToken 假定有效期 (小时)
SERVICE_TOKEN_VALID_HOURS = 12
# 登录会话有效期 (秒)
SESSION_TTL_SECONDS = 3600

# 登录状态
STATE_SUCCESS = "success"
STATE_NEED_CAPTCHA = "need_captcha"
STATE_NEED_VERIFY = "need_verify"
STATE_FAILED = "failed"


def _strip_json_prefix(body: str) -> str:
    """去掉小米账号 API 响应的 JSON 前缀 &&&START&&&"""
    return body.replace("&&&START&&&", "").strip()


def _get_str(obj: dict, key: str, default: str = "") -> str:
    """兼容数字型 userId 等字段的取值"""
    v = obj.get(key)
    if v is None:
        return default
    if isinstance(v, str):
        return v
    if isinstance(v, (int, float)):
        return str(int(v))
    return str(v)


def _get_code(obj: dict) -> int:
    """读取响应 code, 缺失按 0 处理"""
    try:
        return int(obj.get("code", 0))
    except (TypeError, ValueError):
        return 0


def compute_client_sign(nonce, ssecurity: str) -> str:
    """clientSign = base64(sha1("nonce={nonce}&{ssecurity}"))"""
    nsec = f"nonce={nonce}&{ssecurity}"
    return base64.b64encode(hashlib.sha1(nsec.encode()).digest()).decode()


class MinaAuth:
    """单次交互式登录的认证器

    维护独立的 CookieJar / deviceId / UA 与登录中间态 (step1 签名、验证码 ick、
    二次验证 URL), 支持密码登录在验证码/短信验证之间多步续接。
    """

    def __init__(self, session: aiohttp.ClientSession | None = None):
        self.device_id = new_device_id()
        self.user_agent = USER_AGENT_TEMPLATE % self.device_id
        # 外部注入的 session 仅测试用; 生产环境懒创建独立 session
        self._external_session = session
        self._session: aiohttp.ClientSession | None = None

        # 登录过程中间态
        self.captcha_ick = ""
        self.verify_url = ""
        self.login_data: dict | None = None
        self.username = ""
        self.password = ""

        now = time.time()
        self.token_info: dict = {
            "user_id": "",
            "device_id": self.device_id,
            "services": {},
            "created_at": now,
            "expires_at": now + SERVICE_TOKEN_VALID_HOURS * 3600,
        }

    # ---- 公开方法 ----

    async def login(self, username: str, password: str) -> dict:
        """密码登录 (首次调用), 目标服务 micoapi"""
        self.username = username
        self.password = password
        return await self.login_service(MINA_SID)

    async def login_service(self, sid: str) -> dict:
        """Step1 取签名 → Step2 提交密码"""
        step1 = await self._login_step1(sid)
        if step1 is None:
            return {"state": STATE_FAILED, "error": "step1 failed: no response"}
        self.login_data = step1
        return await self._login_step2_with_password(step1, "", sid)

    async def login_with_captcha(self, captcha: str, sid: str = MINA_SID) -> dict:
        """使用图形验证码续步登录"""
        if not self.login_data:
            return {"state": STATE_FAILED, "error": "please call login first"}
        return await self._login_step2_with_password(self.login_data, captcha, sid)

    async def login_with_verify_code(self, verify_code: str, sid: str = MINA_SID) -> dict:
        """使用短信/邮箱验证码完成登录"""
        if not self.verify_url:
            return {"state": STATE_FAILED, "error": "no verify url, please call login first"}

        # 验证短信/邮箱验证码 → location
        location = await self._verify_ticket(verify_code)
        if not location:
            return {"state": STATE_FAILED, "error": "verify failed: no location returned"}

        # 跟随重定向链收集 passToken、userId 等 cookies
        await self._follow_redirects_for_cookies(location)

        # 用 passToken + userId 调 serviceLogin 换取 serviceToken
        pass_token = self.get_cookie("passToken")
        user_id = self.get_cookie("userId")
        if not pass_token:
            return {"state": STATE_FAILED, "error": "no passToken after verify"}

        return await self._exchange_service_token(pass_token, user_id, sid)

    async def get_captcha_image(self, captcha_url: str) -> dict:
        """获取验证码图片 (base64) 并记录 ick cookie"""
        async with self._client().get(
            captcha_url, headers={"User-Agent": self.user_agent}
        ) as r:
            data = await r.read()
        ick = self._cookie_from(r.cookies, "ick") or self.get_cookie("ick")
        self.captcha_ick = ick
        return {"image_base64": base64.b64encode(data).decode(), "ick": ick}

    async def submit_sms_code(self, notification_url: str, code: str) -> str:
        """提交短信验证码, 返回 location URL 或空字符串"""
        self.verify_url = notification_url
        location = await self._verify_ticket(code)
        return location or ""

    async def refresh_by_pass_token(
        self, pass_token: str, user_id: str, sid: str = MINA_SID
    ) -> dict:
        """通过 passToken 刷新 serviceToken (手动 Token / 扫码确认后共用)"""
        return await self._exchange_service_token(pass_token, user_id, sid)

    async def service_login(
        self, pass_token: str, user_id: str, sid: str = MINA_SID
    ) -> dict | None:
        """serviceLogin (passToken → serviceToken), 便捷封装"""
        result = await self._exchange_service_token(pass_token, user_id, sid)
        if result.get("state") != STATE_SUCCESS:
            return None
        svc = self.token_info["services"].get(sid)
        if not svc:
            return None
        return {"serviceToken": svc["service_token"], "ssecurity": svc["ssecurity"]}

    def get_cookie(self, name: str) -> str:
        """读取当前会话 cookie"""
        if self._session is None:
            return ""
        cookies = self._session.cookie_jar.filter_cookies(URL(ACCOUNT_BASE_URL))
        return self._cookie_from(cookies, name)

    def set_cookie(self, name: str, value: str):
        """注入 cookie (如扫码确认后注入 cUserId)"""
        self._client().cookie_jar.update_cookies(
            {name: value}, URL(ACCOUNT_BASE_URL)
        )

    async def close(self):
        """关闭内部 session (外部注入的 session 由调用方管理)"""
        if self._session is not None and not self._session.closed:
            try:
                await self._session.close()
            except Exception:
                pass
        self._session = None

    # ---- 内部步骤 ----

    def _client(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = self._external_session or aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                cookie_jar=aiohttp.CookieJar(unsafe=True),
            )
        return self._session

    async def _request_json(
        self, method, url, *, data=None, cookies=None
    ) -> dict | None:
        """账号 API 请求并解析 &&&START&&& JSON, 失败返回 None"""
        headers = {"User-Agent": self.user_agent}
        try:
            async with self._client().request(
                method, url, data=data, cookies=cookies, headers=headers, ssl=False
            ) as r:
                text = await r.text()
            return json.loads(_strip_json_prefix(text))
        except Exception as e:
            log.warning(f"[mina_auth] {method} {url} 解析失败: {e}")
            return None

    async def _login_step1(self, sid: str) -> dict | None:
        """GET serviceLogin 取登录签名 (_sign / qs / callback)"""
        url = f"{ACCOUNT_BASE_URL}/pass/serviceLogin?sid={sid}&_json=true"
        return await self._request_json(
            "GET", url, cookies={"sdkVersion": SDK_VERSION, "deviceId": self.device_id}
        )

    async def _login_step2_with_password(
        self, auth: dict, captcha: str, sid: str
    ) -> dict:
        """POST serviceLoginAuth2 提交密码 (可带图形验证码)"""
        login_url = f"{ACCOUNT_BASE_URL}/pass/serviceLoginAuth2?_json=true"

        form = {
            "user": self.username,
            "hash": hashlib.md5(self.password.encode()).hexdigest().upper(),
            "callback": _get_str(auth, "callback"),
            "sid": _get_str(auth, "sid", sid) or sid,
            "qs": _get_str(auth, "qs"),
            "_sign": _get_str(auth, "_sign"),
        }
        if captcha:
            form["captCode"] = captcha

        # 提交验证码时显式携带 ick cookie
        cookies = {"ick": self.captcha_ick} if captcha and self.captcha_ick else None
        result = await self._request_json("POST", login_url, data=form, cookies=cookies)
        if result is None:
            return {"state": STATE_FAILED, "error": "parse response failed"}

        # 响应检查顺序: 二次验证 → 图形验证码 → location
        notification_url = _get_str(result, "notificationUrl")
        if notification_url:
            if not notification_url.startswith("http"):
                notification_url = ACCOUNT_BASE_URL + notification_url
            self.verify_url = notification_url
            return {
                "state": STATE_NEED_VERIFY,
                "verify_url": notification_url,
                "verify_type": "email" if "email" in notification_url else "phone",
            }

        captcha_url = _get_str(result, "captchaUrl")
        if captcha_url:
            if not captcha_url.startswith("http"):
                captcha_url = ACCOUNT_BASE_URL + captcha_url
            captcha_result = await self.get_captcha_image(captcha_url)
            return {
                "state": STATE_NEED_CAPTCHA,
                "captcha_image": captcha_result["image_base64"],
            }

        location = _get_str(result, "location")
        if not location:
            return {
                "state": STATE_FAILED,
                "error": (
                    f"login failed, code: {result.get('code')}, "
                    f"desc: {result.get('description')}, sid: {sid}"
                ),
            }

        ssecurity = _get_str(result, "ssecurity")
        nonce = result.get("nonce", "")
        user_id = _get_str(result, "userId")

        step3_error = await self._login_step3(
            self._append_client_sign(location, nonce, ssecurity), sid, ssecurity
        )
        if step3_error:
            return {"state": STATE_FAILED, "error": f"step3 failed: {step3_error}"}

        if not self.token_info["user_id"] and user_id:
            self.token_info["user_id"] = user_id
        return {"state": STATE_SUCCESS, "token_info": self.token_info}

    async def _login_step3(
        self, location: str, sid: str, ssecurity_from_step2: str = ""
    ) -> str | None:
        """跟随 STS 重定向获取 serviceToken; 成功返回 None, 失败返回错误信息"""
        headers = {"User-Agent": self.user_agent}
        try:
            async with self._client().get(location, headers=headers) as r:
                service_token = self._cookie_from(r.cookies, "serviceToken")
                if not service_token:
                    # 重定向链中 serviceToken 可能落在中间某一跳, 回查 jar
                    # (先查当前响应、再查 CookieJar)
                    jar = self._client().cookie_jar.filter_cookies(r.url)
                    service_token = self._cookie_from(jar, "serviceToken")
                if not service_token:
                    try:
                        body = (await r.text())[:200]
                    except Exception:
                        body = ""
                    return (
                        f"failed to get serviceToken, status: {r.status}, body: {body}"
                    )
        except Exception as e:
            return f"STS request failed: {e}"

        user_id = self.get_cookie("userId")
        ssecurity = self.get_cookie("ssecurity") or ssecurity_from_step2
        if not self.token_info["user_id"] and user_id:
            self.token_info["user_id"] = user_id
        self.token_info["services"][sid] = {
            "service_token": service_token,
            "ssecurity": ssecurity,
            "expires_at": time.time() + SERVICE_TOKEN_VALID_HOURS * 3600,
        }
        return None

    async def _exchange_service_token(
        self, pass_token: str, user_id: str, sid: str
    ) -> dict:
        """用 passToken 通过 serviceLogin 换取指定服务的 serviceToken"""
        url = f"{ACCOUNT_BASE_URL}/pass/serviceLogin?sid={sid}&_json=true"
        cookies = {
            "passToken": pass_token,
            "userId": user_id,
            "deviceId": self.device_id,
            "sdkVersion": SDK_VERSION,
        }
        c_user_id = self.get_cookie("cUserId")
        if c_user_id:
            cookies["cUserId"] = c_user_id

        login_data = await self._request_json("GET", url, cookies=cookies)
        if login_data is None:
            return {
                "state": STATE_FAILED,
                "error": "parse serviceLogin response failed",
            }

        code = _get_code(login_data)
        if code != 0:
            return {
                "state": STATE_FAILED,
                "error": (
                    f"serviceLogin for {sid} failed: code={code}, "
                    f"desc={_get_str(login_data, 'desc', 'unknown')}"
                ),
            }

        location = _get_str(login_data, "location")
        ssecurity = _get_str(login_data, "ssecurity")
        if not location:
            return {
                "state": STATE_FAILED,
                "error": f"serviceLogin for {sid} returned no location URL",
            }

        new_user_id = _get_str(login_data, "userId")
        if new_user_id:
            self.token_info["user_id"] = new_user_id

        step3_error = await self._login_step3(
            self._append_client_sign(location, login_data.get("nonce", ""), ssecurity),
            sid,
            ssecurity,
        )
        if step3_error:
            return {"state": STATE_FAILED, "error": f"step3 failed: {step3_error}"}

        if not self.token_info["user_id"] and user_id:
            self.token_info["user_id"] = user_id
        return {"state": STATE_SUCCESS, "token_info": self.token_info}

    async def _verify_ticket(self, ticket: str) -> str | None:
        """验证短信/邮箱验证码, 成功返回 location URL"""
        # 身份验证类型 URL 需要先取 identity_session
        if "/fe/service/identity/authStart" in self.verify_url:
            await self._check_identity_list()

        if "email" in self.verify_url:
            verify_api, flag = "/identity/auth/verifyEmail", "8"
        else:
            verify_api, flag = "/identity/auth/verifyPhone", "4"

        url = f"{ACCOUNT_BASE_URL}{verify_api}?_dc={int(time.time() * 1000)}"
        form = {"ticket": ticket, "trust": "true", "_json": "true", "_flag": flag}
        result = await self._request_json("POST", url, data=form)
        if result is None or result.get("code") != 0:
            return None
        return result.get("location") or None

    async def _check_identity_list(self):
        """获取 identity_session (非关键步骤, 失败忽略)"""
        list_url = self.verify_url.replace(
            "/fe/service/identity/authStart", "/identity/list"
        )
        try:
            await self._client().get(
                list_url, headers={"User-Agent": self.user_agent}, ssl=False
            )
        except Exception:
            pass

    async def _follow_redirects_for_cookies(self, location: str):
        """跟随重定向链收集 passToken/userId cookie (失败不致命)"""
        try:
            await self._client().get(location, headers={"User-Agent": self.user_agent})
        except Exception as e:
            log.info(f"[mina_auth] 收集登录 cookie 重定向异常 (忽略): {e}")

    @staticmethod
    def _append_client_sign(location: str, nonce, ssecurity: str) -> str:
        """location 追加 _userIdNeedEncrypt 与 clientSign"""
        client_sign = compute_client_sign(nonce, ssecurity)
        sep = "&" if "?" in location else "?"
        return (
            f"{location}{sep}_userIdNeedEncrypt=true"
            f"&clientSign={quote(client_sign, safe='')}"
        )

    @staticmethod
    def _cookie_from(cookie_jar, name: str) -> str:
        morsel = cookie_jar.get(name) if cookie_jar is not None else None
        return morsel.value if morsel else ""


class LoginSession:
    """密码登录会话

    保存多步登录的上下文 (MinaAuth 实例持有 CookieJar / step1 签名 / ick),
    供验证码 / 短信验证续步使用。
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.state = "idle"
        self.username = ""
        self.auth: MinaAuth | None = None
        self.created_at = time.time()

    def is_expired(self) -> bool:
        return time.time() - self.created_at > SESSION_TTL_SECONDS


class PasswordLoginService:
    """密码登录编排 (login / submitCaptcha / submitVerifyCode)

    会话以生成的 session_id 为键保存在内存中, TTL 1 小时; 终态后自动清理。
    """

    def __init__(self):
        self._sessions: dict[str, LoginSession] = {}

    async def login(self, username: str, password: str) -> tuple[dict, str]:
        session_id = secrets.token_urlsafe(12)
        session = LoginSession(session_id)
        session.username = username
        session.auth = MinaAuth()
        self._sessions[session_id] = session
        result = await session.auth.login(username, password)
        return self._handle_result(session, result)

    async def submit_captcha(self, session_id: str, captcha: str) -> tuple[dict, str]:
        session = self._get_session(session_id)
        if session is None or session.auth is None:
            return {"state": STATE_FAILED, "error": "会话已过期, 请重新登录"}, ""
        result = await session.auth.login_with_captcha(captcha, MINA_SID)
        return self._handle_result(session, result)

    async def submit_verify_code(self, session_id: str, code: str) -> tuple[dict, str]:
        session = self._get_session(session_id)
        if session is None or session.auth is None:
            return {"state": STATE_FAILED, "error": "会话已过期, 请重新登录"}, ""
        result = await session.auth.login_with_verify_code(code, MINA_SID)
        return self._handle_result(session, result)

    def cleanup_expired(self):
        expired = [sid for sid, s in self._sessions.items() if s.is_expired()]
        for sid in expired:
            self._sessions.pop(sid, None)

    def _get_session(self, session_id: str) -> LoginSession | None:
        self.cleanup_expired()
        session = self._sessions.get(session_id)
        if session is not None and session.is_expired():
            self._sessions.pop(session_id, None)
            return None
        return session

    def _handle_result(
        self, session: LoginSession, result: dict
    ) -> tuple[dict, str]:
        session.state = result.get("state", STATE_FAILED)
        if result.get("state") == STATE_SUCCESS:
            # 成功: 从会话提取凭据信息一并返回, 随后清理会话
            # (username/password 供 API 层持久化为三级链的账密兜底凭证)
            auth = session.auth
            result = dict(result)
            result["pass_token"] = auth.get_cookie("passToken")
            result["user_id"] = auth.token_info.get("user_id", "")
            result["device_id"] = auth.token_info.get("device_id", "")
            result["username"] = session.username
            result["password"] = auth.password
            svc = auth.token_info["services"].get(MINA_SID, {})
            result["service_token"] = svc.get("service_token", "")
            result["ssecurity"] = svc.get("ssecurity", "")
            self._sessions.pop(session.session_id, None)
            asyncio.ensure_future(auth.close())
        elif result.get("state") == STATE_FAILED:
            self._sessions.pop(session.session_id, None)
            asyncio.ensure_future(session.auth.close())
        return result, session.session_id
