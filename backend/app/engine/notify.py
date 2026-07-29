"""登录异常推送通知

通知方式单选 (notify_type), 当前支持:
- feishu: 飞书自定义机器人 Webhook;
- wxpusher: WxPusher 极简推送, 仅需 SPT (Simple Push Token), 微信直接收消息。

同一事件默认 1 小时内只推送一次, 避免轮询循环中重复失败导致消息轰炸。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import time

import aiohttp

log = logging.getLogger("miair")

WXPUSHER_SIMPLE_API = "https://wxpusher.zjiecode.com/api/send/message/simple-push"

# 同一事件的最小推送间隔 (秒)
THROTTLE_SECONDS = 3600

# 事件 -> 上次推送时间戳 (进程内存, 重启后清零)
_last_sent: dict[str, float] = {}


def _active_channel(config) -> str | None:
    """当前生效的通知渠道; 未选择或所需凭证为空时返回 None"""
    ntype = getattr(config, "notify_type", "")
    if ntype == "feishu" and getattr(config, "notify_feishu_webhook", ""):
        return "feishu"
    if ntype == "wxpusher" and getattr(config, "notify_wxpusher_spt", ""):
        return "wxpusher"
    return None


async def send_notification(
    config,
    event: str,
    title: str,
    content: str,
    force: bool = False,
    throttle: int | None = None,
) -> dict[str, bool]:
    """按所选通知方式推送, 返回 {channel: ok}。

    force=True 跳过节流 (用于测试消息); throttle 可按事件覆盖默认节流时长 (秒);
    未配置渠道或被节流时返回空 dict。"""
    channel = _active_channel(config)
    if channel is None:
        return {}
    if not force:
        window = THROTTLE_SECONDS if throttle is None else throttle
        now = time.time()
        if now - _last_sent.get(event, 0) < window:
            return {}
        _last_sent[event] = now

    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        if channel == "feishu":
            ok = await _send_feishu(
                session,
                config.notify_feishu_webhook,
                getattr(config, "notify_feishu_secret", ""),
                title,
                content,
            )
        else:
            ok = await _send_wxpusher(
                session, config.notify_wxpusher_spt, title, content
            )
    return {channel: ok}


def notify_async(config, event: str, title: str, content: str, throttle: int | None = None):
    """后台推送 (fire-and-forget), 供同步/异步触发点调用, 不阻塞调用方。

    无运行中的事件循环时静默跳过 (例如单测同步环境)。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(send_notification(config, event, title, content, throttle=throttle))
    # 吞掉后台任务异常, 避免 "Task exception was never retrieved" 噪音
    task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)


def _normalize_feishu_url(key: str) -> str:
    """兼容只填 hook 后缀 key 的写法, 自动补全为完整 Webhook 地址"""
    if not key.startswith("http"):
        return f"https://open.feishu.cn/open-apis/bot/v2/hook/{key}"
    return key


def _build_feishu_payload(secret: str, title: str, content: str) -> dict:
    """飞书消息体; 配置了加签密钥时附加签名。

    飞书签名算法较特殊: 以 timestamp+"\n"+secret 作为 HMAC-SHA256 密钥对空消息签名。"""
    payload: dict = {"msg_type": "text", "content": {"text": f"{title}\n{content}"}}
    if secret:
        timestamp = str(int(time.time()))
        string_to_sign = f"{timestamp}\n{secret}"
        sign = base64.b64encode(
            hmac.new(string_to_sign.encode(), digestmod=hashlib.sha256).digest()
        ).decode()
        payload["timestamp"] = timestamp
        payload["sign"] = sign
    return payload


async def _send_feishu(
    session: aiohttp.ClientSession, url: str, secret: str, title: str, content: str
) -> bool:
    """飞书自定义机器人: HTTP 200 时仍需校验业务码, 成功 code=0 (旧版 StatusCode=0)"""
    payload = _build_feishu_payload(secret, title, content)
    try:
        async with session.post(_normalize_feishu_url(url), json=payload) as resp:
            if not (200 <= resp.status < 300):
                log.warning(f"[Notify] 飞书推送失败 HTTP {resp.status}: {title}")
                return False
            result = await resp.json(content_type=None)
            if result.get("code", result.get("StatusCode")) != 0:
                log.warning(f"[Notify] 飞书推送被拒绝: {result}")
                return False
            log.info(f"[Notify] 飞书推送成功: {title}")
            return True
    except asyncio.CancelledError:
        return False
    except Exception as e:
        log.warning(f"[Notify] 飞书推送异常: {e}")
        return False


async def _send_wxpusher(
    session: aiohttp.ClientSession, spt: str, title: str, content: str
) -> bool:
    """WxPusher 极简推送: 成功时接口返回 code=1000"""
    payload = {
        "spt": spt,
        "summary": title[:100],
        "content": f"{title}\n{content}",
        "contentType": 1,
    }
    try:
        async with session.post(WXPUSHER_SIMPLE_API, json=payload) as resp:
            result = await resp.json(content_type=None)
            if result.get("code") == 1000:
                log.info(f"[Notify] WxPusher 推送成功: {title}")
                return True
            log.warning(f"[Notify] WxPusher 推送失败: {result}")
            return False
    except asyncio.CancelledError:
        return False
    except Exception as e:
        log.warning(f"[Notify] WxPusher 推送异常: {e}")
        return False
