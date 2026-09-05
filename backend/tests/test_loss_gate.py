"""丢包恢复三道闸门 + 音频队列溢出策略测试

对应优化: 参照 shairport-sync 把「一见缺口就插静音」改为
宽限 → 限频重传 → 放弃填坑; 队列满从一次丢 25% 改为只丢最旧最少数量。
"""

import logging
import queue

from app.engine.airplay.audio_stream import AudioStreamServer
from app.engine.airplay.server import loss_gate_action


class TestLossGate:
    def test_within_grace_waits(self):
        """宽限期内: 只等, 不插静音不重发 (乱序/迟到的包大概率到达)"""
        assert loss_gate_action(0.01, grace=0.05, give_up=0.6) == "wait"
        assert loss_gate_action(0.049, grace=0.05, give_up=0.6) == "wait"

    def test_after_grace_before_give_up_retries(self):
        assert loss_gate_action(0.05, grace=0.05, give_up=0.6) == "retry"
        assert loss_gate_action(0.3, grace=0.05, give_up=0.6) == "retry"

    def test_after_give_up_skips(self):
        """超过放弃窗口: 填静音跳过缺口, 不无限等待"""
        assert loss_gate_action(0.61, grace=0.05, give_up=0.6) == "skip"

    def test_boundaries(self):
        assert loss_gate_action(0.0, 0.05, 0.6) == "wait"
        assert loss_gate_action(1.0, 0.05, 0.6) == "skip"


def _make_stream(maxsize: int = 5) -> AudioStreamServer:
    """绕过 __init__ 直接构造最小可用的 AudioStreamServer"""
    s = object.__new__(AudioStreamServer)
    s._active = True
    s._audio_queue = queue.Queue(maxsize=maxsize)
    s._last_drop_log = 0.0
    return s


class TestWritePcmOverflow:
    def _drain(self, q):
        items = []
        while True:
            try:
                items.append(q.get_nowait())
            except queue.Empty:
                return items

    def test_normal_write_no_drop(self):
        s = _make_stream()
        s.write_pcm(b"a")
        assert self._drain(s._audio_queue) == [b"a"]

    def test_overflow_drops_minimum(self, caplog):
        """队列满时只丢到放下为止 (1 块), 而非一次丢 25%"""
        s = _make_stream(maxsize=2)
        s.write_pcm(b"1")
        s.write_pcm(b"2")
        s.write_pcm(b"3")  # 满, 应丢 b"1" 放入 b"3"

        assert self._drain(s._audio_queue) == [b"2", b"3"]

    def test_overflow_logs_warning(self, caplog):
        s = _make_stream(maxsize=1)
        s.write_pcm(b"1")
        with caplog.at_level(logging.WARNING, logger="miair"):
            s.write_pcm(b"2")
        assert any("音频队列溢出" in r.message for r in caplog.records)

    def test_inactive_ignores(self):
        s = _make_stream()
        s._active = False
        s.write_pcm(b"x")
        assert self._drain(s._audio_queue) == []
