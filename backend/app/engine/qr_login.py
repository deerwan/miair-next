"""小米扫码登录 (移植自 songloft-plugin-miot 的 qrcode.ts)

流程 (仅需前 3 步, 产出 userId + passToken 即可写入 config.cookie):
  1. GET  serviceLogin        -> 取 _sign / qs / callback
  2. GET  longPolling/loginUrl -> 取 qr(二维码图) / loginUrl / lp(长轮询URL)
  3. GET  lp (服务端长轮询)     -> 用户米家 App 扫码确认后返回 passToken + userId

miservice-fork 的 cookie 登录只需 userId + passToken, 无需再换 serviceToken。
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time

import aiohttp

log = logging.getLogger("miair")

# ---- 常量 (对齐 songloft / xiaomusic 参考实现) ----
ACCOUNT_BASE_URL = "https://account.xiaomi.com"
LONG_POLLING_URL = "https://account.xiaomi.com/longPolling/loginUrl"
# 获取二维码使用 mijia SID (比 micoapi 稳定)
QR_LOGIN_SID = "mijia"
USER_AGENT_TEMPLATE = (
    "Android-7.1.1-1.0.0-ONEPLUS A3010-136-%s APP/xiaomi.smarthome APPV/62830"
)
# 单次长轮询阻塞上限 (服务端约 30s 挂起, 留余量)
POLL_TIMEOUT_SECONDS = 35
# 最大轮询次数 (防无限轮询, 约等于二维码有效期)
MAX_POLL_COUNT = 20
# 会话空闲过期 (秒)
SESSION_TTL_SECONDS = 300

# 扫码状态: waiting(等待扫码) / confirmed(成功) / expired(过期) / failed(失败)
STATE_WAITING = "waiting"
STATE_CONFIRMED = "confirmed"
STATE_EXPIRED = "expired"
STATE_FAILED = "failed"


def _strip_json_prefix(body: str) -> str:
    """去掉小米 API 响应的 JSON 前缀 &&&START&&&"""
    return body.replace("&&&START&&&", "").strip()


def _get_str(obj: dict, key: str, default: str = "") -> str:
    """兼容数字型 userId 的取值"""
    v = obj.get(key)
    if v is None:
        return default
    if isinstance(v, str):
        return v
    if isinstance(v, (int, float)):
        return str(int(v))
    return str(v)


class QRCodeLogin:
    """单次扫码登录会话 (维护独立的 cookie 与设备标识)"""

    def __init__(self):
        self.device_id = secrets.token_hex(16)
        self.user_agent = USER_AGENT_TEMPLATE % self.device_id
        self.state = "idle"
        self.poll_url = ""
        self.poll_count = 0
        self.created_at = time.time()
        # cookie_jar 需 unsafe=True 以保留跨请求 cookie
        self._session = aiohttp.ClientSession(
            cookie_jar=aiohttp.CookieJar(unsafe=True)
        )

    async def get_qrcode(self) -> dict | None:
        """获取二维码, 成功返回 {qrcode_url, login_url}"""
        try:
            # Step 1: serviceLogin 取签名参数
            url1 = f"{ACCOUNT_BASE_URL}/pass/serviceLogin?sid={QR_LOGIN_SID}&_json=true"
            headers1 = {
                "User-Agent": self.user_agent,
                "Cookie": f"sdkVersion=3.8.6; deviceId={self.device_id}",
            }
            async with self._session.get(url1, headers=headers1) as resp1:
                data1 = json.loads(_strip_json_prefix(await resp1.text()))

            sign = _get_str(data1, "_sign")
            qs = _get_str(data1, "qs")
            callback = _get_str(data1, "callback")
            if not (sign and qs and callback):
                log.warning("[qrcode] serviceLogin 缺少必要参数")
                return None

            # Step 2: longPolling/loginUrl 取二维码与轮询地址
            from urllib.parse import quote

            params = "&".join([
                "_qrsize=240",
                f"qs={quote(qs)}",
                f"sid={QR_LOGIN_SID}",
                f"_sign={quote(sign)}",
                f"callback={quote(callback)}",
                "_json=true",
                f"_dc={int(time.time() * 1000)}",
            ])
            url2 = f"{LONG_POLLING_URL}?{params}"
            headers2 = {
                "User-Agent": self.user_agent,
                "Content-Type": "application/x-www-form-urlencoded",
            }
            async with self._session.get(url2, headers=headers2) as resp2:
                data2 = json.loads(_strip_json_prefix(await resp2.text()))

            if int(data2.get("code", 0)) != 0:
                log.warning(f"[qrcode] 获取二维码失败: {data2.get('desc')}")
                return None

            qr = _get_str(data2, "qr")
            login_url = _get_str(data2, "loginUrl")
            lp = _get_str(data2, "lp")
            if not lp:
                log.warning("[qrcode] 响应缺少 lp (长轮询) 地址")
                return None

            self.poll_url = lp
            self.poll_count = 0
            self.state = STATE_WAITING
            return {"qrcode_url": qr or login_url, "login_url": login_url}
        except Exception as e:
            log.warning(f"[qrcode] get_qrcode 异常: {e}")
            self.state = STATE_FAILED
            return None

    async def poll(self) -> dict:
        """单次轮询 (长轮询, 服务端挂起约 30s)。返回 {state, message, [cookie]}"""
        if not self.poll_url:
            return {"state": STATE_FAILED, "message": "尚未获取二维码"}

        self.poll_count += 1
        if self.poll_count > MAX_POLL_COUNT:
            self.state = STATE_EXPIRED
            return {"state": STATE_EXPIRED, "message": "二维码已过期"}

        headers = {
            "User-Agent": self.user_agent,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        try:
            timeout = aiohttp.ClientTimeout(total=POLL_TIMEOUT_SECONDS)
            async with self._session.get(
                self.poll_url, headers=headers, timeout=timeout
            ) as resp:
                status = resp.status
                if status == 403:
                    self.state = STATE_EXPIRED
                    return {"state": STATE_EXPIRED, "message": "二维码已过期"}
                if status >= 400:
                    self.state = STATE_FAILED
                    return {"state": STATE_FAILED, "message": f"轮询失败: HTTP {status}"}
                body = _strip_json_prefix(await resp.text())
        except asyncio.TimeoutError:
            # 长轮询超时 = 仍在等待扫码
            return {"state": STATE_WAITING, "message": "等待扫码"}
        except Exception as e:
            self.state = STATE_FAILED
            return {"state": STATE_FAILED, "message": f"轮询异常: {e}"}

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            # 空响应, 继续等待
            return {"state": STATE_WAITING, "message": "等待扫码"}

        if int(data.get("code", 0)) != 0:
            self.state = STATE_EXPIRED
            return {"state": STATE_EXPIRED, "message": f"二维码失效: {data.get('desc')}"}

        pass_token = _get_str(data, "passToken")
        user_id = _get_str(data, "userId")
        if not (pass_token and user_id):
            # 已扫码但未确认, 继续等待
            return {"state": STATE_WAITING, "message": "已扫码, 等待确认"}

        # 扫码成功: 拼装 miservice 可用的 cookie
        self.state = STATE_CONFIRMED
        log.info(f"[qrcode] 扫码登录成功 userId={user_id}")
        return {
            "state": STATE_CONFIRMED,
            "message": "登录成功",
            "cookie": f"userId={user_id}; passToken={pass_token}",
            "user_id": user_id,
        }

    def is_expired(self) -> bool:
        return time.time() - self.created_at > SESSION_TTL_SECONDS

    async def close(self):
        try:
            await self._session.close()
        except Exception:
            pass


class QRLoginManager:
    """扫码会话管理器 (按 session_id 维护多个进行中的扫码流程)"""

    def __init__(self):
        self._sessions: dict[str, QRCodeLogin] = {}

    async def start(self) -> tuple[str, dict] | None:
        """新建会话并获取二维码, 返回 (session_id, qr_info)"""
        await self._cleanup_expired()
        qr = QRCodeLogin()
        info = await qr.get_qrcode()
        if not info:
            await qr.close()
            return None
        session_id = secrets.token_urlsafe(12)
        self._sessions[session_id] = qr
        return session_id, info

    async def poll(self, session_id: str) -> dict:
        qr = self._sessions.get(session_id)
        if not qr:
            return {"state": STATE_FAILED, "message": "没有进行中的扫码会话, 请重新获取二维码"}
        result = await qr.poll()
        # 终态时清理会话
        if result["state"] in (STATE_CONFIRMED, STATE_EXPIRED, STATE_FAILED):
            self._sessions.pop(session_id, None)
            await qr.close()
        return result

    async def _cleanup_expired(self):
        expired = [sid for sid, qr in self._sessions.items() if qr.is_expired()]
        for sid in expired:
            qr = self._sessions.pop(sid, None)
            if qr:
                await qr.close()

    async def close_all(self):
        for qr in self._sessions.values():
            await qr.close()
        self._sessions.clear()
