"""UPnP 事件订阅加固测试 (对应审查 P2: CALLBACK SSRF / 订阅无界 / 永生 TIMEOUT)"""

import time

from app.engine.dlna.eventing import (
    MAX_SUBSCRIPTIONS,
    EventManager,
    clamp_timeout,
    validate_callback_url,
)


class TestCallbackValidation:
    def test_valid_http_accepted(self):
        assert (
            validate_callback_url("<http://192.168.1.5:30000/callback>")
            == "http://192.168.1.5:30000/callback"
        )

    def test_scheme_must_be_http(self):
        assert validate_callback_url("file:///etc/passwd") is None
        assert validate_callback_url("ftp://x/y") is None
        assert validate_callback_url("/etc/passwd") is None
        assert validate_callback_url("") is None

    def test_host_required(self):
        assert validate_callback_url("http://") is None

    def test_oversize_rejected(self):
        assert validate_callback_url("http://" + "a" * 3000) is None


class TestTimeoutClamp:
    def test_huge_timeout_capped(self):
        assert clamp_timeout(99999999999) == 3600

    def test_tiny_or_negative_floored(self):
        assert clamp_timeout(5) == 60
        assert clamp_timeout(-10) == 60

    def test_normal_unchanged(self):
        assert clamp_timeout(1800) == 1800


class TestSubscribeHardening:
    def test_invalid_callback_returns_none(self):
        em = EventManager()
        assert em.subscribe("file:///etc/passwd") is None
        assert em._subscriptions == {}

    def test_subscription_cap(self):
        em = EventManager()
        for _ in range(MAX_SUBSCRIPTIONS):
            assert em.subscribe("http://10.0.0.1/cb") is not None
        # 表满后再订阅被拒
        assert em.subscribe("http://10.0.0.1/cb") is None
        assert len(em._subscriptions) == MAX_SUBSCRIPTIONS

    def test_expired_slots_freed_before_reject(self):
        """恰好满表时, 过期订阅先清理再判容量, 不误拒"""
        em = EventManager()
        for _ in range(MAX_SUBSCRIPTIONS):
            em.subscribe("http://10.0.0.1/cb", timeout=1)
        # 强制让全部订阅过期
        for sub in em._subscriptions.values():
            sub.created_at = time.time() - 3600
        assert em.subscribe("http://10.0.0.1/cb") is not None

    def test_stored_timeout_is_clamped(self):
        em = EventManager()
        sid = em.subscribe("http://10.0.0.1/cb", timeout=99999999999)
        assert em._subscriptions[sid].timeout == 3600
        assert em.renew(sid, timeout=1) is True
        assert em._subscriptions[sid].timeout == 60
