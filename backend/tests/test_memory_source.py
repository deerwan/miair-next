"""内存口径分层回退测试 (cgroup → PSS → VmRSS → ru_maxrss)

Docker 容器内应命中 cgroup (与 docker stats / OOM killer 同口径);
macOS 开发机退回 ru_maxrss 峰值口径。
"""

from app.api.v1.system import _memory_mb


class TestMemoryMb:
    def test_returns_value_and_known_source(self):
        mb, source = _memory_mb()
        assert source in ("cgroup", "pss", "rss", "peak", "unknown")
        if source != "unknown":
            assert mb is not None and mb > 0

    def test_precedence_on_linux(self):
        """在 Linux 上: 有 cgroup 文件必命中 cgroup; 否则应有 PSS/RSS"""
        import os

        if os.path.exists("/sys/fs/cgroup/memory.current"):
            _, source = _memory_mb()
            assert source == "cgroup"
        elif os.path.exists("/proc/self/smaps_rollup"):
            _, source = _memory_mb()
            assert source in ("pss", "cgroup")

    def test_on_macos_falls_back_to_peak(self):
        import sys

        if sys.platform == "darwin":
            _, source = _memory_mb()
            assert source == "peak"
