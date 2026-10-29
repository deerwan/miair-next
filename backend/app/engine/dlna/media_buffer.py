"""DLNA 媒体缓冲：小文件留在内存，较大文件滚动到临时文件。

缓冲仍保留完整文件以支持 DLNA Range/Seek；HTTP 响应与转码文件复制按块读写，
避免把整首音频常驻 Python 堆内存。
"""

import asyncio
import logging
import os
import tempfile
import threading

import aiohttp

from app.engine.net_guard import safe_request, validate_url_safe

log = logging.getLogger("miair")

# 浏览器 UA，防止远端拒绝
_UA = (
    "Mozilla/5.0 (Linux; Android 12) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Mobile Safari/537.36"
)

# 多线程分块下载配置。限制并行请求和单块尺寸，降低并发响应体的内存峰值。
_DOWNLOAD_THREADS = 4
_CHUNK_SIZE = 1024 * 1024
_MIN_SIZE_FOR_MULTI_THREAD = 5 * 1024 * 1024
# 每个缓冲最多保留 1 MiB 在内存，超出后由 SpooledTemporaryFile 自动落盘。
_MEMORY_SPOOL_LIMIT = 1024 * 1024
# 单文件下载硬上限: 媒体代理端点无鉴权, 远端 Content-Length 不可信。
_MAX_DOWNLOAD_SIZE = 64 * 1024 * 1024


class MediaBuffer:
    """管理单个音频文件的有界缓冲；对小文件使用内存，对大文件使用临时磁盘。"""

    def __init__(self, remote_url: str, temp_dir: str | None = None):
        self.remote_url = remote_url
        self.content_type: str = "audio/mpeg"
        self.total_size: int = 0  # Content-Length（0 表示未知）
        self.download_complete: bool = False
        self.error: str | None = None
        self._headers_event = asyncio.Event()
        self._complete_event = asyncio.Event()
        self._download_task: asyncio.Task | None = None
        self._converting: bool = False
        self._converted: bool = False
        self._convert_event = asyncio.Event()
        self._supports_range: bool = False
        self._temp_dir = temp_dir
        if self._temp_dir:
            try:
                os.makedirs(self._temp_dir, exist_ok=True)
                with tempfile.TemporaryFile(dir=self._temp_dir):
                    pass
            except OSError as e:
                log.warning(f"无法创建媒体缓冲目录 {self._temp_dir}: {e}")
                self._temp_dir = None
        self._storage_lock = threading.RLock()
        self._storage = self._new_storage()
        self._owned_storage_path: str | None = None
        self._stored_bytes = 0
        self._contiguous_bytes = 0
        # 多线程 Range 下载时记录已落盘区间，用于只向探测器暴露连续前缀。
        self._written_ranges: list[tuple[int, int]] = []

    def _new_storage(self):
        try:
            return tempfile.SpooledTemporaryFile(
                max_size=_MEMORY_SPOOL_LIMIT, mode="w+b", dir=self._temp_dir
            )
        except OSError as e:
            if not self._temp_dir:
                raise
            log.warning(f"无法在数据目录创建媒体临时缓冲，回退系统临时目录: {e}")
            return tempfile.SpooledTemporaryFile(
                max_size=_MEMORY_SPOOL_LIMIT, mode="w+b"
            )

    @property
    def downloaded_size(self) -> int:
        """实际已写入的字节数（Range 并发下载期间可能不是连续前缀）。"""
        with self._storage_lock:
            return self._stored_bytes

    @property
    def contiguous_size(self) -> int:
        """从文件开头起连续可读的字节数。"""
        with self._storage_lock:
            return self._contiguous_bytes

    @property
    def data(self) -> bytearray:
        """兼容旧调用方的全量读取入口；生产代理路径应使用 read_range。"""
        return bytearray(self.read_range(0, self.downloaded_size))

    @data.setter
    def data(self, value: bytes | bytearray | memoryview) -> None:
        self.clear()
        if value:
            self.append_data(value)

    def read_range(self, start: int, size: int) -> bytes:
        """从缓冲中读取指定区间，不创建整文件副本。"""
        if start < 0 or size <= 0:
            return b""
        with self._storage_lock:
            if self._storage.closed or start >= self._stored_bytes:
                return b""
            self._storage.seek(start)
            return self._storage.read(min(size, self._stored_bytes - start))

    def append_data(self, data: bytes | bytearray | memoryview) -> None:
        """顺序追加下载数据。"""
        if not data:
            return
        with self._storage_lock:
            if self._storage.closed:
                raise ValueError("音频缓冲已关闭")
            self._storage.seek(self._stored_bytes)
            self._storage.write(data)
            self._stored_bytes += len(data)
            self._contiguous_bytes = self._stored_bytes
            self._written_ranges = [(0, self._stored_bytes)]

    def write_at(self, start: int, data: bytes | bytearray | memoryview) -> None:
        """写入 Range 下载分块，并更新连续前缀边界。"""
        if start < 0 or not data:
            return
        end = start + len(data)
        with self._storage_lock:
            if self._storage.closed:
                raise ValueError("音频缓冲已关闭")
            # Range 分块会随机写入文件偏移。先强制滚动到文件，避免 BytesIO
            # 因 seek 到较大偏移而在内存中填充整段空洞后才触发 rollover。
            rollover = getattr(self._storage, "rollover", None)
            if rollover:
                rollover()
            self._storage.seek(start)
            self._storage.write(data)
            self._stored_bytes += len(data)

            ranges = sorted((*self._written_ranges, (start, end)))
            merged: list[tuple[int, int]] = []
            for range_start, range_end in ranges:
                if merged and range_start <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], range_end))
                else:
                    merged.append((range_start, range_end))
            self._written_ranges = merged
            contiguous = 0
            for range_start, range_end in merged:
                if range_start > contiguous:
                    break
                contiguous = max(contiguous, range_end)
            self._contiguous_bytes = contiguous

    def clear(self) -> None:
        """清空缓冲内容，保留可复用的临时存储。"""
        with self._storage_lock:
            if self._storage.closed:
                self._storage = self._new_storage()
            self._storage.seek(0)
            self._storage.truncate(0)
            self._stored_bytes = 0
            self._contiguous_bytes = 0
            self._written_ranges.clear()

    def copy_to_file(self, path: str, chunk_size: int = 65536) -> None:
        """以固定块大小将缓冲复制到普通文件。"""
        with open(path, "wb") as output:
            for offset in range(0, self.downloaded_size, chunk_size):
                output.write(self.read_range(offset, chunk_size))

    def replace_from_file(self, path: str, *, max_size: int | None = None) -> bool:
        """接管文件作为缓冲存储；超限或无法打开时保留原数据。"""
        size = os.path.getsize(path)
        if max_size is not None and size > max_size:
            return False
        try:
            storage = open(path, "r+b")
        except OSError:
            return False
        with self._storage_lock:
            old_storage = self._storage
            old_path = self._owned_storage_path
            self._storage = storage
            self._owned_storage_path = path
            self._stored_bytes = size
            self._contiguous_bytes = size
            self._written_ranges = [(0, size)] if size else []
            if not old_storage.closed:
                old_storage.close()
        if old_path and old_path != path:
            try:
                os.unlink(old_path)
            except OSError:
                pass
        self.total_size = size
        return True

    async def start_download(self, session: aiohttp.ClientSession):
        """启动后台下载任务"""
        self._download_task = asyncio.create_task(self._do_download(session))

    async def _do_download(self, session: aiohttp.ClientSession):
        """执行受大小上限保护的下载，数据写入内存/临时文件缓冲。"""
        try:
            # SSRF 防护: URL 来自局域网无鉴权面 (SetAVTransportURI / play_url),
            # 禁止 loopback / link-local 等危险目标 (私网 NAS 媒体源不受影响)
            await validate_url_safe(self.remote_url)

            headers = {"User-Agent": _UA}
            async with await safe_request(
                session,
                "HEAD",
                self.remote_url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30, connect=10),
            ) as resp:
                if resp.status in (200, 206):
                    ct = resp.headers.get("Content-Type")
                    if ct:
                        self.content_type = ct.split(";")[0].strip()

                    cl = resp.headers.get("Content-Length")
                    if cl:
                        try:
                            self.total_size = int(cl)
                        except ValueError:
                            pass

                    accept_ranges = resp.headers.get("Accept-Ranges", "")
                    self._supports_range = "bytes" in accept_ranges.lower()

                self._headers_event.set()

            if not self._supports_range and self.total_size > _MIN_SIZE_FOR_MULTI_THREAD:
                self._supports_range = await self._test_range_support(session)

            if self.total_size > _MAX_DOWNLOAD_SIZE:
                self.error = (
                    f"文件过大 ({self.total_size} bytes > 上限 {_MAX_DOWNLOAD_SIZE})"
                )
                log.error(f"拒绝下载: {self.error}, url={self.remote_url[:80]}...")
                return

            if self._supports_range and self.total_size > _MIN_SIZE_FOR_MULTI_THREAD:
                log.info(f"使用多线程下载: {self.total_size} bytes, {_DOWNLOAD_THREADS} 线程")
                await self._do_multi_thread_download(session)
            else:
                if self.total_size > _MIN_SIZE_FOR_MULTI_THREAD:
                    log.info(f"服务器不支持Range，使用单线程下载: {self.total_size} bytes")
                await self._do_single_thread_download(session)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.error = str(e)
            log.error(f"音频缓冲下载失败: {e}")
        finally:
            self._headers_event.set()
            self._complete_event.set()

    async def _test_range_support(self, session: aiohttp.ClientSession) -> bool:
        """测试服务器是否实际支持 Range 请求。"""
        try:
            headers = {"User-Agent": _UA, "Range": "bytes=0-1023"}
            async with await safe_request(
                session,
                "GET",
                self.remote_url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10, connect=5),
            ) as resp:
                if resp.status == 206:
                    return True
                if resp.status == 200 and resp.headers.get("Content-Range"):
                    return True
        except Exception:
            pass
        return False

    async def _do_single_thread_download(self, session: aiohttp.ClientSession):
        """顺序下载，边收边写入 spool；未知长度也受硬上限约束。"""
        headers = {"User-Agent": _UA}
        async with await safe_request(
            session,
            "GET",
            self.remote_url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=300, connect=15),
        ) as resp:
            if resp.status not in (200, 206):
                self.error = f"HTTP {resp.status}"
                return

            async for chunk in resp.content.iter_chunked(256 * 1024):
                if self.downloaded_size + len(chunk) > _MAX_DOWNLOAD_SIZE:
                    self.error = (
                        f"下载超过大小上限 ({_MAX_DOWNLOAD_SIZE} bytes), 已中止"
                    )
                    log.error(f"{self.error}, url={self.remote_url[:80]}...")
                    return
                await asyncio.to_thread(self.append_data, chunk)

        if not self.total_size:
            self.total_size = self.downloaded_size
        self.download_complete = True
        log.info(
            f"音频缓冲完成(单线程): {self.total_size} bytes, "
            f"{self.remote_url[:80]}..."
        )

    async def _do_multi_thread_download(self, session: aiohttp.ClientSession):
        """并发 Range 下载；分块直接写入 spool，避免预分配整文件 bytearray。"""
        total_size = self.total_size
        chunk_size = max(_CHUNK_SIZE, total_size // (_DOWNLOAD_THREADS * 4))
        chunks = [
            (start, min(start + chunk_size - 1, total_size - 1))
            for start in range(0, total_size, chunk_size)
        ]
        semaphore = asyncio.Semaphore(_DOWNLOAD_THREADS)

        async def download_chunk(start: int, end: int):
            async with semaphore:
                headers = {
                    "User-Agent": _UA,
                    "Range": f"bytes={start}-{end}",
                }
                try:
                    async with await safe_request(
                        session,
                        "GET",
                        self.remote_url,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=120, connect=10),
                    ) as resp:
                        if resp.status not in (200, 206):
                            log.error(f"分块下载失败: HTTP {resp.status}")
                            return False

                        expected = end - start + 1
                        chunk_data = await resp.content.read(expected)
                        if len(chunk_data) < expected:
                            log.error(f"分块数据不足 ({len(chunk_data)}/{expected})")
                            return False
                        if await resp.content.read(1):
                            log.error("服务器未正确支持 Range (响应超出请求区间)，放弃该分块")
                            return False

                        await asyncio.to_thread(self.write_at, start, chunk_data)
                        return True
                except Exception as e:
                    log.error(f"分块下载失败 ({start}-{end}): {e}")
                    return False

        results = await asyncio.gather(
            *(download_chunk(start, end) for start, end in chunks),
            return_exceptions=True,
        )
        success_count = sum(1 for result in results if result is True)
        if success_count == len(chunks):
            self.download_complete = True
            log.info(
                f"音频缓冲完成(多线程): {self.total_size} bytes, "
                f"{len(chunks)} 个分块, {self.remote_url[:80]}..."
            )
        else:
            log.warning(
                f"多线程下载部分失败 ({success_count}/{len(chunks)})，回退到单线程"
            )
            self.clear()
            await self._do_single_thread_download(session)

    async def wait_for_headers(self, timeout: float = 30.0):
        """等待 HTTP 头就绪（content_type / total_size）"""
        try:
            await asyncio.wait_for(self._headers_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            self.error = "等待响应头超时"

    async def wait_for_completion(self, timeout: float = 120.0):
        """等待整个下载完成"""
        try:
            await asyncio.wait_for(self._complete_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            self.error = "下载超时"

    def cleanup(self):
        """取消下载并关闭临时存储。"""
        if self._download_task and not self._download_task.done():
            self._download_task.cancel()
        with self._storage_lock:
            if not self._storage.closed:
                self._storage.close()
            owned_path = self._owned_storage_path
            self._owned_storage_path = None
            self._stored_bytes = 0
            self._contiguous_bytes = 0
            self._written_ranges.clear()
        if owned_path:
            try:
                os.unlink(owned_path)
            except OSError:
                pass
        self.download_complete = False
