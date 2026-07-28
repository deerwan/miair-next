"""日志配置: 控制台 + 滚动文件 + 内存环形缓冲 (供 API / WebSocket 查询推送)"""

import asyncio
import collections
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

LOG_NAME = "miair"


class RingBufferHandler(logging.Handler):
    """内存环形缓冲, 保留最近 N 条日志, 并向 WebSocket 订阅者广播"""

    def __init__(self, capacity: int = 500):
        super().__init__()
        self.buffer: collections.deque[str] = collections.deque(maxlen=capacity)
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self._subscribers.discard(q)

    def emit(self, record: logging.LogRecord):
        try:
            line = self.format(record)
        except Exception:
            return
        self.buffer.append(line)
        if self._loop and self._subscribers:
            for q in list(self._subscribers):
                try:
                    self._loop.call_soon_threadsafe(q.put_nowait, line)
                except Exception:
                    pass


ring_handler = RingBufferHandler()


def setup_logging(verbose: bool, log_file: str | None = None):
    logger = logging.getLogger(LOG_NAME)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)

    # 抑制 asyncio / aiohttp 内部的连接断开噪音日志
    logging.getLogger("asyncio").setLevel(logging.CRITICAL)
    logging.getLogger("aiohttp.server").setLevel(logging.WARNING)

    if logger.handlers:
        return

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d: %(message)s",
        datefmt="[%Y-%m-%d %H:%M:%S]",
    )

    # 控制台 - Windows 下用 UTF-8 流避免 GBK 编码异常
    if sys.platform == "win32":
        import io
        stream = io.TextIOWrapper(
            sys.stderr.buffer, encoding="utf-8", errors="replace",
            line_buffering=True,
        )
        console = logging.StreamHandler(stream)
    else:
        console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    ring_handler.setFormatter(formatter)
    logger.addHandler(ring_handler)

    # 文件 — 每次启动清空, 大小上限 500KB (超过自动清空重写)
    if log_file:
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        try:
            open(log_file, "w", encoding="utf-8").close()
        except OSError:
            pass
        file_handler = RotatingFileHandler(
            log_file, maxBytes=500 * 1024, backupCount=0, encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
