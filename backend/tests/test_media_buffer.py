"""媒体缓冲下载大小上限测试 (全 fake, 不触网)

覆盖点 (对应审查 P1: 无鉴权代理端点可被单请求打爆内存):
- 远端声明超大 Content-Length → 预分配前被拒
- 无 Content-Length 的无限流式响应 → 循环内中止且不标记完成
- 服务器无视 Range 回全量响应 → 分块被拒 (而非整文件读进单分块)
- 正常分块下载行为不回归
"""

import asyncio

from app.engine.dlna import media_buffer
from app.engine.dlna.media_buffer import MediaBuffer


class FakeContent:
    """替代 aiohttp StreamResponse.content"""

    def __init__(self, chunks: list[bytes]):
        self._chunks = list(chunks)

    async def iter_chunked(self, n: int):
        for c in self._chunks:
            yield c

    async def read(self, n: int) -> bytes:
        """读取最多 n 字节, 按剩余数据返回"""
        data = b"".join(self._chunks)
        data, self._chunks = data[:n], [data[n:]]
        return data


class FakeResponse:
    def __init__(self, status=200, headers=None, content=None):
        self.status = status
        self.headers = headers or {}
        self.content = content or FakeContent([])

    async def read(self) -> bytes:
        return b"".join(self.content._chunks)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeSession:
    """替代 aiohttp.ClientSession: head/get 返回预置响应"""

    def __init__(self, head_response=None, get_response=None):
        self._head = head_response
        self._get = get_response

    def head(self, url, **kwargs):
        return self._ctx(self._head)

    def get(self, url, **kwargs):
        return self._ctx(self._get)

    class _ctx:
        def __init__(self, resp):
            self.resp = resp

        async def __aenter__(self):
            return self.resp

        async def __aexit__(self, *args):
            return False


def _run(main):
    return asyncio.run(main)


class TestSizeCap:
    def test_huge_content_length_rejected_before_alloc(self):
        """远端声明 10GB Content-Length → 预分配前被拒, data 保持为空"""
        buf = MediaBuffer("http://evil.example/song.flac")
        session = FakeSession(
            head_response=FakeResponse(
                status=200,
                headers={"Content-Length": str(10 * 1024**3), "Accept-Ranges": "bytes"},
            )
        )

        async def _main():
            await buf._do_download(session)
            return buf.error

        error = _run(_main())
        assert error is not None
        assert "过大" in error
        assert len(buf.data) == 0  # 未发生预分配
        assert buf.download_complete is False

    def test_unbounded_streaming_aborts_midway(self, monkeypatch):
        """无 Content-Length 的无限流: 超过上限时中止, 不标记下载完成"""
        monkeypatch.setattr(media_buffer, "_MAX_DOWNLOAD_SIZE", 3000)
        buf = MediaBuffer("http://evil.example/stream")
        session = FakeSession(
            head_response=FakeResponse(status=200, headers={}),
            get_response=FakeResponse(
                status=200, content=FakeContent([b"x" * 2000, b"y" * 2000])
            ),
        )

        async def _main():
            await buf._do_download(session)
            return len(buf.data), buf.error, buf.download_complete

        size, error, complete = _run(_main())
        assert size == 2000  # 第二块会越界, 第一块保留
        assert error is not None and "上限" in error
        assert complete is False

    def test_normal_download_unaffected(self):
        """正常小文件下载不受上限影响, 完整落盘"""
        buf = MediaBuffer("http://music.example/song.mp3")
        session = FakeSession(
            head_response=FakeResponse(
                status=200, headers={"Content-Length": "2048"}
            ),
            get_response=FakeResponse(status=200, content=FakeContent([b"x" * 2048])),
        )

        async def _main():
            await buf._do_download(session)
            return buf.download_complete, buf.error

        complete, error = _run(_main())
        assert complete is True
        assert error is None
        assert len(buf.data) == 2048


class TestRangeViolation:
    def test_range_ignoring_server_rejected_per_chunk(self, monkeypatch):
        """服务器无视 Range 回全量响应: 分块读取受限并拒绝, 最终数据量有界

        多线程分块全部被拒后会按设计回退单线程, 单线程同样被上限拦下,
        因此端到端断言: 内存不随全量响应体积增长。
        """
        monkeypatch.setattr(media_buffer, "_MAX_DOWNLOAD_SIZE", 3000)
        buf = MediaBuffer("http://evil.example/song.flac")
        buf.total_size = 6 * 1024 * 1024  # >5MB 触发多线程路径
        buf._supports_range = True
        full_body = b"z" * (6 * 1024 * 1024)

        # 每次 GET 返回全新响应 (真实服务器每次都回全量, 不共享读游标)
        class FreshBodySession(FakeSession):
            def get(self, url, **kwargs):
                return FakeSession._ctx(
                    FakeResponse(status=200, content=FakeContent([full_body]))
                )

        session = FreshBodySession()

        async def _main():
            await buf._do_multi_thread_download(session)
            return len(buf.data), buf.download_complete, buf.error

        size, complete, error = _run(_main())
        assert complete is False
        assert error is not None
        assert size <= 3000  # 6MB 全量响应没有进内存

    def test_normal_chunk_download_succeeds(self):
        """正确支持 Range 的服务器: 分块下载正常完成"""
        total = 6 * 1024 * 1024
        data = bytes(range(256)) * (total // 256)
        buf = MediaBuffer("http://music.example/song.flac")
        buf.total_size = total
        buf._supports_range = True

        class RangeAwareSession(FakeSession):
            def get(self, url, **kwargs):
                headers = kwargs.get("headers", {})
                rng = headers.get("Range", "")
                start = int(rng.split("=")[1].split("-")[0]) if rng else 0
                end = int(rng.split("=")[1].split("-")[1]) if rng else total - 1
                return FakeSession._ctx(
                    FakeResponse(
                        status=206, content=FakeContent([data[start:end + 1]])
                    )
                )

        async def _main():
            await buf._do_multi_thread_download(RangeAwareSession())
            return buf.download_complete

        assert _run(_main()) is True
        assert bytes(buf.data) == data
