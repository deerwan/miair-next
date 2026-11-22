"""通知推送模块测试: 通知方式选择 / 事件节流 / 脱敏工具"""

import asyncio

import pytest

from app.core.masking import MASKED_TOKEN, mask_secret, unmask_secret
from app.engine import notify
from app.engine.config import Config


@pytest.fixture(autouse=True)
def _reset_throttle():
    """每个用例前清空节流状态, 避免互相影响"""
    notify._last_sent.clear()
    yield
    notify._last_sent.clear()


@pytest.fixture
def sent(monkeypatch):
    """屏蔽真实网络请求, 记录各渠道发送调用"""
    calls = []

    async def fake_feishu(session, url, secret, title, content):
        calls.append(("feishu", url, title, content))
        return True

    async def fake_wxpusher(session, spt, title, content):
        calls.append(("wxpusher", spt, title, content))
        return True

    async def fake_telegram(session, api_base, token, chat_id, title, content):
        calls.append(("telegram", api_base, token, chat_id, title, content))
        return True

    monkeypatch.setattr(notify, "_send_feishu", fake_feishu)
    monkeypatch.setattr(notify, "_send_wxpusher", fake_wxpusher)
    monkeypatch.setattr(notify, "_send_telegram", fake_telegram)
    return calls


def _make_config(ntype="", feishu="", spt="", tg_api_base="", tg_token="", tg_chat_id=""):
    config = Config(conf_path="/tmp/miair-test-notify")
    config.notify_type = ntype
    config.notify_feishu_webhook = feishu
    config.notify_wxpusher_spt = spt
    config.notify_tg_api_base = tg_api_base
    config.notify_tg_bot_token = tg_token
    config.notify_tg_chat_id = tg_chat_id
    return config


def test_normalize_feishu_url():
    """只填 hook key 时自动补全完整 Webhook 地址, 完整地址原样保留"""
    assert (
        notify._normalize_feishu_url("abc-def")
        == "https://open.feishu.cn/open-apis/bot/v2/hook/abc-def"
    )
    full = "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"
    assert notify._normalize_feishu_url(full) == full


def test_feishu_payload_signature():
    """未配密钥时不带签名字段; 配了密钥时附加 timestamp+sign (青龙同款算法)"""
    plain = notify._build_feishu_payload("", "t", "c")
    assert plain == {"msg_type": "text", "content": {"text": "t\nc"}}

    signed = notify._build_feishu_payload("my-secret", "t", "c")
    assert signed["msg_type"] == "text"
    assert signed["timestamp"].isdigit()
    # 签名可复现: 以 timestamp\nsecret 为 HMAC-SHA256 密钥对空消息签名后 base64
    import base64
    import hashlib
    import hmac

    expected = base64.b64encode(
        hmac.new(
            f"{signed['timestamp']}\nmy-secret".encode(), digestmod=hashlib.sha256
        ).digest()
    ).decode()
    assert signed["sign"] == expected


def test_no_channel_returns_empty(sent):
    """未选择通知方式时静默跳过, 且不占用节流额度"""
    config = _make_config()
    result = asyncio.run(notify.send_notification(config, "login_expired", "t", "c"))
    assert result == {}
    assert sent == []
    assert "login_expired" not in notify._last_sent


def test_type_without_credential_skipped(sent):
    """选择了通知方式但对应凭证为空时不推送"""
    config = _make_config(ntype="feishu")
    result = asyncio.run(notify.send_notification(config, "login_expired", "t", "c"))
    assert result == {}
    assert sent == []


def test_feishu_channel(sent):
    """飞书方式只走飞书渠道"""
    config = _make_config(
        ntype="feishu",
        feishu="https://open.feishu.cn/open-apis/bot/v2/hook/xxx",
        spt="SPT_should_be_ignored",
    )
    result = asyncio.run(notify.send_notification(config, "login_expired", "标题", "内容"))
    assert result == {"feishu": True}
    assert len(sent) == 1
    assert sent[0][0] == "feishu"


def test_wxpusher_channel(sent):
    """WxPusher 方式只走 WxPusher 渠道"""
    config = _make_config(ntype="wxpusher", spt="SPT_xxx")
    result = asyncio.run(notify.send_notification(config, "login_expired", "标题", "内容"))
    assert result == {"wxpusher": True}
    assert len(sent) == 1
    assert sent[0][0] == "wxpusher"


def test_telegram_channel(sent):
    """Telegram 方式只走 Telegram 渠道, 透传反代地址/Token/chat_id"""
    config = _make_config(
        ntype="telegram", tg_api_base="https://tg.example.workers.dev", tg_token="TOK", tg_chat_id="123"
    )
    result = asyncio.run(notify.send_notification(config, "login_expired", "标题", "内容"))
    assert result == {"telegram": True}
    assert len(sent) == 1
    assert sent[0][0] == "telegram"
    assert sent[0][1] == "https://tg.example.workers.dev"
    assert sent[0][2] == "TOK"
    assert sent[0][3] == "123"


def test_telegram_without_credentials_skipped(sent):
    """Telegram 渠道 Token 或 chat_id 任一为空时不推送"""
    config = _make_config(ntype="telegram", tg_token="TOK")
    assert asyncio.run(notify.send_notification(config, "e", "t", "c")) == {}
    config = _make_config(ntype="telegram", tg_chat_id="123")
    assert asyncio.run(notify.send_notification(config, "e", "t", "c")) == {}
    assert sent == []


def test_normalize_tg_api_base():
    """反代地址归一化: 空/纯空白回退官方 API, 去尾部斜杠, 保留自定义路径前缀"""
    assert notify._normalize_tg_api_base("") == notify.TG_DEFAULT_API_BASE
    assert notify._normalize_tg_api_base("   ") == notify.TG_DEFAULT_API_BASE
    assert notify._normalize_tg_api_base("https://tg.example.com/") == "https://tg.example.com"
    assert (
        notify._normalize_tg_api_base("https://tg.example.com/tg-proxy")
        == "https://tg.example.com/tg-proxy"
    )


class _FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status = status
        self._payload = payload if payload is not None else {"ok": True}

    async def json(self, content_type=None):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _FakeSession:
    def __init__(self, response=None, exc=None):
        self.calls = []
        self._response = response if response is not None else _FakeResponse()
        self._exc = exc

    def post(self, url, json=None):
        self.calls.append({"url": url, "json": json})
        if self._exc is not None:
            raise self._exc
        return self._response


def test_send_telegram_success():
    """成功: 请求打到官方 API, 消息体为纯文本且禁用链接预览"""
    session = _FakeSession()
    ok = asyncio.run(notify._send_telegram(session, "", "TOK", "123", "标题", "内容"))
    assert ok is True
    call = session.calls[0]
    assert call["url"] == "https://api.telegram.org/botTOK/sendMessage"
    assert call["json"] == {
        "chat_id": "123",
        "text": "标题\n内容",
        "disable_web_page_preview": True,
    }


def test_send_telegram_custom_api_base():
    """自建反代: 拼接在反代地址后, 去尾部斜杠"""
    session = _FakeSession()
    ok = asyncio.run(
        notify._send_telegram(session, "https://tg.example.com/", "TOK", "123", "t", "c")
    )
    assert ok is True
    assert session.calls[0]["url"] == "https://tg.example.com/botTOK/sendMessage"


def test_send_telegram_api_rejected():
    """HTTP 200 但业务 ok=false (如 Token 无效) 视为失败"""
    session = _FakeSession(_FakeResponse(payload={"ok": False, "description": "Unauthorized"}))
    assert asyncio.run(notify._send_telegram(session, "", "bad", "1", "t", "c")) is False


def test_send_telegram_http_error():
    """非 2xx 状态码视为失败"""
    session = _FakeSession(_FakeResponse(status=502))
    assert asyncio.run(notify._send_telegram(session, "", "TOK", "1", "t", "c")) is False


def test_send_telegram_exception():
    """网络异常 (如反代不可达) 视为失败, 不抛出"""
    session = _FakeSession(exc=RuntimeError("proxy unreachable"))
    assert asyncio.run(notify._send_telegram(session, "", "TOK", "1", "t", "c")) is False


def test_throttle_same_event(sent):
    """同一事件在节流窗口内只推一次, 不同事件互不影响"""
    config = _make_config(ntype="wxpusher", spt="SPT_xxx")
    first = asyncio.run(notify.send_notification(config, "login_expired", "t", "c"))
    second = asyncio.run(notify.send_notification(config, "login_expired", "t", "c"))
    other = asyncio.run(notify.send_notification(config, "login_failed", "t", "c"))
    assert first == {"wxpusher": True}
    assert second == {}
    assert other == {"wxpusher": True}
    assert len(sent) == 2


def test_force_bypasses_throttle(sent):
    """force=True (测试消息) 跳过节流"""
    config = _make_config(ntype="wxpusher", spt="SPT_xxx")
    asyncio.run(notify.send_notification(config, "test", "t", "c"))
    result = asyncio.run(notify.send_notification(config, "test", "t", "c", force=True))
    assert result == {"wxpusher": True}
    assert len(sent) == 2


def test_custom_throttle_window(sent):
    """throttle 参数可按事件覆盖默认节流时长"""
    config = _make_config(ntype="wxpusher", spt="SPT_xxx")
    asyncio.run(notify.send_notification(config, "panel_login", "t", "c", throttle=60))
    blocked = asyncio.run(
        notify.send_notification(config, "panel_login", "t", "c", throttle=60)
    )
    assert blocked == {}
    # 回拨上次发送时间至窗口外, 短节流窗口过后可再次推送
    notify._last_sent["panel_login"] -= 61
    again = asyncio.run(
        notify.send_notification(config, "panel_login", "t", "c", throttle=60)
    )
    assert again == {"wxpusher": True}
    assert len(sent) == 2


def test_notify_async_without_loop():
    """无运行中事件循环时 fire-and-forget 静默跳过, 不抛异常"""
    config = _make_config(ntype="wxpusher", spt="SPT_xxx")
    notify.notify_async(config, "login_expired", "t", "c")


def test_mask_secret():
    """SPT 脱敏: 仅保留最后 3 位明文"""
    assert mask_secret("") == ""
    assert mask_secret("abc") == MASKED_TOKEN
    assert mask_secret("SPT_1234567890") == MASKED_TOKEN + "890"


def test_mask_tg_bot_token_roundtrip():
    """Telegram Bot Token 脱敏往返: 掩码回写还原真实值, 新值直接保存"""
    real = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
    masked = mask_secret(real)
    assert "ABC-DEF" not in masked
    assert unmask_secret(masked, real) == real
    assert unmask_secret("999999:NEW-TOKEN", real) == "999999:NEW-TOKEN"


def test_unmask_secret():
    """前端回写含脱敏占位符时保留已存储的真实值"""
    real = "SPT_1234567890"
    assert unmask_secret(MASKED_TOKEN + "890", real) == real
    assert unmask_secret("SPT_new_value", real) == "SPT_new_value"
    assert unmask_secret("", real) == ""
