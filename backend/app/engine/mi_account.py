"""小米账号登录内核

替换 miservice-fork 的 MiAccount.login(), 其余请求层 (mi_request /
MiNAService / MiIOService) 原样继承, 对 AuthManager 完全 drop-in。

为什么不能用 miservice 原版 login():
1. GET serviceLogin 失败后, 原版**无条件**用 username/password 去 POST
   serviceLoginAuth2 —— cookie 模式下二者为空串, 等于定期向小米提交
   「空账号 + md5(空)」的密码登录, 必然返回 70016, 属于典型风控输入
   (生产 2026-09-06 事故: 每 2h 一次的定时续期持续触发该请求)。
2. 账号密码登录遇到「需要短信/邮箱二次验证」的响应 (code==0 但无
   userId / 带 notificationUrl) 时, 原版在 resp["userId"] 处直接
   KeyError 崩溃, 三级降级链的账密兜底永远走不通。
3. 登录失败会把 account.token 置 None 并删除 .mi.token 文件, 让仍然
   有效的缓存 serviceToken 陪葬。

本移植对齐 miot 插件的行为:
- 续期只走 GET serviceLogin (passToken 换发), 账号密码未配置时**绝不**
  发 serviceLoginAuth2;
- User-Agent 固定为米家 App 形态, deviceId 为持久化的 32 位 hex
  (对齐 qr_login.py 扫码时使用的设备标识), 整个登录会话稳定;
- 完整解析 serviceLoginAuth2 的 notificationUrl / captchaUrl /
  缺 userId 等验证类响应, 一律优雅返回 False 并留下可定位的日志;
- 登录失败不破坏内存 token 与 .mi.token 文件 (缓存 serviceToken 可继续
  被三级链的「复用缓存」级别使用)。
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import secrets
from http.cookies import SimpleCookie
from urllib.parse import quote

from miservice.miaccount import MiAccount as _MiserviceMiAccount

log = logging.getLogger("miair")

# ---- 常量 ----
ACCOUNT_BASE_URL = "https://account.xiaomi.com"
USER_AGENT_TEMPLATE = (
    "Android-7.1.1-1.0.0-ONEPLUS A3010-136-%s APP/xiaomi.smarthome APPV/62830"
)
# serviceLogin / serviceLoginAuth2 携带的 SDK 版本 cookie (与 miot 插件一致)
SDK_VERSION = "3.8.6"

# 合法 deviceId: 32 位小写 hex (secrets.token_hex(16) 的形态)
_DEVICE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def new_device_id() -> str:
    """生成 32 位 hex 设备标识 (与 miot 插件 generateDeviceId 一致)"""
    return secrets.token_hex(16)


def normalize_device_id(value) -> str | None:
    """校验并归一化 deviceId, 非法值 (如占位符 miair_device) 返回 None"""
    if isinstance(value, str) and _DEVICE_ID_RE.match(value):
        return value
    return None


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
    """读取响应 code, 缺失/异常时按 0 处理 (与 miot 插件 Number(code||0) 对齐)"""
    try:
        return int(obj.get("code", 0))
    except (TypeError, ValueError):
        return 0


class MiAccount(_MiserviceMiAccount):
    """miservice.MiAccount 的 drop-in 替换, 登录内核移植自 miot 插件"""

    def __init__(self, session, username, password, token_store=None):
        super().__init__(session, username, password, token_store=token_store)
        # 稳定设备标识: 优先沿用 .mi.token 中的合法值, 否则生成一次并立即落盘,
        # 保证跨进程/跨重试恒定 (频繁更换 deviceId 是小米风控的典型特征)
        token = self.token if isinstance(self.token, dict) else {}
        device_id = normalize_device_id(token.get("deviceId")) or new_device_id()
        if token.get("deviceId") != device_id:
            token["deviceId"] = device_id
            self.token = token
            if self.token_store:
                self.token_store.save_token(self.token)
        self._device_id = device_id
        # 固定 UA: 不再使用 fake_useragent 的每请求随机 UA
        self.now_ua = USER_AGENT_TEMPLATE % device_id

    async def login(self, sid) -> bool:
        """三级链共用的登录入口; 成功返回 True 并把 token 落盘, 失败返回 False

        与原版 miservice 的关键差异: 失败时**不**清空 self.token、**不**删除
        token 文件 —— 让仍然有效的缓存 serviceToken 留给降级链复用。
        """
        try:
            if not isinstance(self.token, dict):
                self.token = {}
            self.token["deviceId"] = self._device_id

            # ---- 第 1 步: GET serviceLogin ----
            # passToken 有效时这一步直接返回 code 0 + location, 无需账密;
            # passToken 无效时返回 70016 (与匿名请求一致的标准未登录响应)。
            resp = await self._service_login(sid)
            if _get_code(resp) == 0 and _get_str(resp, "location"):
                return await self._finish_login(resp, sid)

            # ---- 第 2 步: 账号密码登录 (仅在配置了账密时; 绝不发空账密请求) ----
            if not (self.username and self.password):
                log.info(
                    f"serviceLogin 换发失败 (code={_get_code(resp)}, "
                    f"desc={_get_str(resp, 'desc')}), 未配置账号密码, 不尝试密码登录"
                )
                return False

            log.info("passToken 换发未成功, 使用账号密码走 serviceLoginAuth2 ...")
            data = {
                "_json": "true",
                "qs": _get_str(resp, "qs"),
                "sid": _get_str(resp, "sid", sid) or sid,
                "_sign": _get_str(resp, "_sign"),
                "callback": _get_str(resp, "callback"),
                "user": self.username,
                "hash": hashlib.md5(self.password.encode()).hexdigest().upper(),
            }
            resp = await self._account_request(
                "POST", f"{ACCOUNT_BASE_URL}/pass/serviceLoginAuth2", data=data
            )

            # 需要短信/邮箱二次验证 (原版 miservice 不解析该字段导致 KeyError)
            notification_url = _get_str(resp, "notificationUrl")
            if notification_url:
                log.warning(
                    "账号密码登录需要短信/邮箱二次验证 (notificationUrl), "
                    "无法无人值守完成; 请到管理后台重新扫码登录"
                )
                return False

            # 需要图形验证码
            captcha_url = _get_str(resp, "captchaUrl")
            if captcha_url:
                log.warning(
                    "账号密码登录需要图形验证码 (captchaUrl), 无法无人值守完成; "
                    "请到管理后台重新扫码登录"
                )
                return False

            code = _get_code(resp)
            if code != 0:
                log.warning(
                    f"serviceLoginAuth2 失败: code={code}, "
                    f"desc={_get_str(resp, 'desc', _get_str(resp, 'description'))}"
                )
                return False

            # code==0 但缺 userId: 同样是要求二次验证的响应形态。
            # 原版 miservice 在此处 resp["userId"] KeyError 崩溃 (生产日志可见)。
            if not _get_str(resp, "userId"):
                log.warning(
                    "账号密码登录响应 code=0 但缺少 userId, "
                    "通常表示账号需要二次验证; 请到管理后台重新扫码登录"
                )
                return False

            return await self._finish_login(resp, sid)
        except Exception as e:
            # 不清 token / 不删文件, 只记录; 由降级链决定下一步
            log.warning(f"MiAccount.login 异常: {e}")
            return False

    # ---- 内部步骤 (对应 auth.ts 的 exchangeServiceToken / loginStep2 / loginStep3) ----

    async def _service_login(self, sid) -> dict:
        """GET serviceLogin?sid={sid}&_json=true, 携带 deviceId/sdkVersion/passToken"""
        cookies = {"sdkVersion": SDK_VERSION, "deviceId": self._device_id}
        if isinstance(self.token, dict):
            pass_token = self.token.get("passToken")
            user_id = self.token.get("userId")
            if pass_token and user_id is not None:
                cookies["passToken"] = str(pass_token)
                cookies["userId"] = str(user_id)
        return await self._account_request(
            "GET",
            f"{ACCOUNT_BASE_URL}/pass/serviceLogin",
            params={"sid": sid, "_json": "true"},
            cookies=cookies,
        )

    async def _finish_login(self, resp: dict, sid) -> bool:
        """登录成功响应 → 取 serviceToken → 写 token 并落盘"""
        location = _get_str(resp, "location")
        ssecurity = _get_str(resp, "ssecurity")
        if not (location and ssecurity):
            log.warning("登录响应缺少 location/ssecurity, 无法换取 serviceToken")
            return False

        service_token = await self._fetch_service_token(
            location, resp.get("nonce"), ssecurity
        )

        user_id = _get_str(resp, "userId") or _get_str(self.token, "userId")
        pass_token = _get_str(resp, "passToken") or _get_str(self.token, "passToken")
        if user_id:
            self.token["userId"] = user_id
        if pass_token:
            self.token["passToken"] = pass_token
        self.token["deviceId"] = self._device_id
        self.token[sid] = (ssecurity, service_token)
        if self.token_store:
            self.token_store.save_token(self.token)
        return True

    async def _fetch_service_token(self, location: str, nonce, ssecurity: str) -> str:
        """访问 STS location 跟随重定向, 取回 serviceToken (auth.ts loginStep3)

        clientSign = base64(sha1("nonce={nonce}&{ssecurity}"))
        """
        nsec = f"nonce={nonce}&{ssecurity}"
        client_sign = base64.b64encode(hashlib.sha1(nsec.encode()).digest()).decode()
        sep = "&" if "?" in location else "?"
        url = (
            f"{location}{sep}_userIdNeedEncrypt=true"
            f"&clientSign={quote(client_sign, safe='')}"
        )
        async with self.session.get(url) as r:
            service_token = self._cookie_value(r.cookies, "serviceToken")
            if not service_token:
                # 重定向链中 serviceToken 可能落在中间某一跳 (对齐 miot 插件
                # 「先取当前响应、再查 CookieJar」的取值顺序)
                jar_cookies = self.session.cookie_jar.filter_cookies(r.url)
                service_token = self._cookie_value(jar_cookies, "serviceToken")
            if not service_token:
                raise Exception(
                    f"STS 未返回 serviceToken (HTTP {r.status}): "
                    f"{(await r.text())[:200]}"
                )
        return service_token

    @staticmethod
    def _cookie_value(cookie: SimpleCookie, name: str) -> str:
        morsel = cookie.get(name)
        return morsel.value if morsel else ""

    async def _account_request(
        self, method, url, *, params=None, data=None, cookies=None
    ) -> dict:
        """account.xiaomi.com 的请求封装: 固定 UA + 忽略自签证书 + 剥离 JSON 前缀"""
        headers = {"User-Agent": self.now_ua}
        async with self.session.request(
            method, url, params=params, data=data, cookies=cookies,
            headers=headers, ssl=False,
        ) as r:
            raw = await r.read()
        return json.loads(_strip_json_prefix(raw.decode("utf-8", errors="replace")))
