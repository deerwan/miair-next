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

    # 抑制 asyncio / aiohttp 内部的连接断开噪音日志。
    # 注意: asyncio logger 只压到 ERROR 而非静音——"Task exception was
    # never retrieved" 等未捕获任务异常是排查后台任务崩溃的关键信号
    # (曾借此定位过登录自愈假死), 全局关掉会让任务静默死亡不可见。
    logging.getLogger("asyncio").setLevel(logging.ERROR)
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
    ring_handler.setFormatter(formatter)

    # 文件 — 每次启动清空, 大小上限 500KB (超过自动清空重写)
    file_handler = None
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

    logger.addHandler(console)
    logger.addHandler(ring_handler)
    if file_handler:
        logger.addHandler(file_handler)

    # miservice 的登录响应码 (如 70016 风控、passToken 失效) 是排查续期失败的
    # 唯一证据, 但它默认只输出到 stderr、不进 miair.log, 导致生产环境完全
    # 看不到失败原因。这里让它复用同一批 handler (只收 WARNING 及以上, 避免
    # 它的 DEBUG 请求日志淹没文件)。
    miservice_logger = logging.getLogger("miservice")
    miservice_logger.setLevel(logging.WARNING)
    miservice_logger.propagate = False
    for handler in (console, ring_handler, file_handler):
        if handler is not None:
            miservice_logger.addHandler(handler)
