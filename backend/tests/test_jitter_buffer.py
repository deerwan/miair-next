"""JitterBuffer 行为测试 (对应审查 P2: _order 无界增长内存泄漏)"""

from app.engine.airplay.server import JitterBuffer


class TestOrderBounded:
    def test_pop_removes_from_order(self):
        """pop 后 _order 必须同步收缩 (回归: 曾只清 _packets, _order 以包速率
        无界增长, 长时间播放每天泄漏数百 MB)"""
        buf = JitterBuffer()
        for i in range(1000):
            buf.insert(i, i * 352, b"\x00" * 32)
        for i in range(1000):
            buf.pop(i)
        assert len(buf._packets) == 0
        assert len(buf._order) == 0

    def test_drain_removes_from_order(self):
        buf = JitterBuffer()
        for i in range(400):  # 保持在 512 淘汰上限内, 专注验证 _order 清理
            buf.insert(i, i * 352, b"\x00" * 32)
        drained = buf.drain(0)
        assert len(drained) == 400
        assert len(buf._order) == 0
        assert len(buf._packets) == 0

    def test_eviction_still_works_after_fix(self):
        """不 pop 纯灌包时, 512 上限淘汰仍然生效且 _order 同步有界"""
        buf = JitterBuffer()
        for i in range(2000):
            buf.insert(i, i * 352, b"\x00" * 32)
        assert len(buf._packets) <= 512
        assert len(buf._order) <= 512

    def test_duplicate_insert_keeps_order_single_entry(self):
        """重复 seq 覆盖不得向 _order 追加第二条"""
        buf = JitterBuffer()
        buf.insert(7, 100, b"a")
        buf.insert(7, 200, b"b")
        assert len(buf._order) == 1
        ts, payload = buf.pop(7)
        assert ts == 200 and payload == b"b"
        assert len(buf._order) == 0
