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

    monkeypatch.setattr(notify, "_send_feishu", fake_feishu)
    monkeypatch.setattr(notify, "_send_wxpusher", fake_wxpusher)
    return calls


def _make_config(ntype="", feishu="", spt=""):
    config = Config(conf_path="/tmp/miair-test-notify")
    config.notify_type = ntype
    config.notify_feishu_webhook = feishu
    config.notify_wxpusher_spt = spt
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


def test_unmask_secret():
    """前端回写含脱敏占位符时保留已存储的真实值"""
    real = "SPT_1234567890"
    assert unmask_secret(MASKED_TOKEN + "890", real) == real
    assert unmask_secret("SPT_new_value", real) == "SPT_new_value"
    assert unmask_secret("", real) == ""
