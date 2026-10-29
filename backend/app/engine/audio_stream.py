"""音频 HTTP 流输出

将解码后的 PCM 音频数据转换为 HTTP 音频流，供小爱音箱播放。
支持两种输出格式:
- MP3: 兼容性好，适用于 L05B/L05C/LX06/L16A 等不支持 WAV 的音箱
- WAV: 零编码延迟，直接输出 PCM，适用于大多数音箱
"""

import asyncio
import logging
import queue
import secrets
import struct
import subprocess
import threading
import time
from collections import deque
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from aiohttp import web

from app.engine.airplay.timed_audio import TimedPCM, clock_ns

log = logging.getLogger("miair")

# 缓冲队列上限（约 800ms，兼顾抗抖动与实时性）
_QUEUE_MAXSIZE = 100
# HTTP 写入受阻时保留的待发送 PCM 上限（约 1.3 秒立体声 44.1 kHz）。
_PENDING_MAX_BYTES = 256 * 1024
_MAX_STREAM_CLIENTS = 4


def with_cache_buster(url: str) -> str:
    """Append a cache-busting value while preserving the stream access token."""
    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query) if key != "_"]
    query.append(("_", str(time.time_ns())))
    return urlunsplit(parts._replace(query=urlencode(query)))


class AudioStreamServer:
    """HTTP 音频流服务器

    接收 PCM 音频数据，通过 HTTP 提供给小爱音箱播放。
    根据音箱型号选择 MP3 (ffmpeg 转码) 或 WAV (直接输出) 格式。
    """

    _label = "AirPlay"

    def __init__(
        self,
        hostname: str,
        port: int = 0,
        audio_format: str = "wav",
        label: str = "AirPlay",
        route_prefix: str = "airplay",
        *,
        isolate_sessions: bool = False,
        output_latency_ms: int | None = None,
    ):
        self.hostname = hostname
        self._label = label
        self._route_prefix = route_prefix.strip("/") or "airplay"
        self.port = port
        self._audio_format = audio_format
        self._app = web.Application()
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

        self._audio_queue: queue.Queue[bytes | None] = queue.Queue(maxsize=_QUEUE_MAXSIZE)
        self._sample_rate = 44100
        self._channels = 2
        self._sample_width = 2
        self._active = False
        self._abort = False
        self._session_id = secrets.token_urlsafe(32)
        self._client_count = 0
        self._client_lock = threading.Lock()
        self._pending_drop_bytes = 0
        self._pending_drop_lock = threading.Lock()
        self._last_pending_drop_log = 0.0
        # AirPlay 2 control boundaries must retire both queued PCM and the
        # downstream HTTP connection. Keep the legacy AirPlay 1 path unchanged.
        self._isolate_sessions = isolate_sessions
        self._output_latency_ns = (output_latency_ms * 1_000_000
                                   if output_latency_ms is not None else None)
        self._queue_capacity = 1000 if output_latency_ms is not None else _QUEUE_MAXSIZE
        self._stream_lock = threading.RLock()
        self._generation = 0
        self._isolated_clients: dict[object, tuple] = {}
        # Called outside _stream_lock; the receiver retires the HTTP session
        # and reissues an official MiNA URL without disconnecting AirPlay.
        self.on_clock_resync = None

        self._setup_routes()

    def _setup_routes(self):
        if self._audio_format == "mp3":
            self._app.router.add_get(
                f"/{self._route_prefix}/stream.mp3", self._handle_stream_mp3
            )
        else:
            self._app.router.add_get(
                f"/{self._route_prefix}/stream.wav", self._handle_stream_wav
            )

    @property
    def stream_url(self) -> str:
        ext = "mp3" if self._audio_format == "mp3" else "wav"
        return (
            f"http://{self.hostname}:{self.port}/"
            f"{self._route_prefix}/stream.{ext}?sid={self._session_id}"
        )

    @property
    def has_clients(self) -> bool:
        """是否有音箱正在拉取音频流"""
        with self._client_lock:
            return self._client_count > 0

    @property
    def client_count(self) -> int:
        """当前拉取音频流的客户端数量"""
        with self._client_lock:
            return self._client_count

    @property
    def queue_depth(self) -> int:
        """待发送 PCM 块数量，供运行诊断查看"""
        return self._audio_queue.qsize()

    @property
    def pending_drop_bytes(self) -> int:
        """慢速 HTTP 客户端导致丢弃的过期 PCM 字节数"""
        with self._pending_drop_lock:
            return self._pending_drop_bytes

    async def start(self):
        self._runner = web.AppRunner(self._app, access_log=None)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "0.0.0.0", self.port)
        await self._site.start()
        self.port = self._site._server.sockets[0].getsockname()[1]
        log.info(f"{self._label} 音频流服务器: http://{self.hostname}:{self.port} (格式: {self._audio_format})")

    async def stop(self):
        if self._isolate_sessions:
            self.stop_streaming()
        else:
            self._active = False
            try:
                self._audio_queue.put_nowait(None)
            except queue.Full:
                pass
        if self._runner:
            await self._runner.cleanup()

    def set_audio_params(self, sample_rate: int, channels: int, sample_width: int = 2):
        self._sample_rate = sample_rate
        self._channels = channels
        self._sample_width = sample_width

    def start_streaming(self, *, new_session: bool = True):
        """Start or reset PCM reception, rotating the token for a new session."""
        if self._isolate_sessions:
            with self._stream_lock:
                self._retire_isolated_clients()
                self._generation += 1
                if new_session:
                    self._session_id = secrets.token_urlsafe(32)
                    with self._pending_drop_lock:
                        self._pending_drop_bytes = 0
                self._audio_queue = queue.Queue(maxsize=self._queue_capacity)
                self._active = True
                self._abort = False
            log.info("AirPlay 2 新音频会话: generation=%s", self._generation)
            return
        self._active = True
        self._abort = False
        if new_session:
            self._session_id = secrets.token_urlsafe(32)
            with self._pending_drop_lock:
                self._pending_drop_bytes = 0
        while True:
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break
        log.info(f"音频流: 开始接收 PCM 数据 (格式: {self._audio_format})")

    def stop_streaming(self):
        if self._isolate_sessions:
            with self._stream_lock:
                self._active = False
                self._abort = True
                self._generation += 1
                self._retire_isolated_clients()
                # Old handlers retain only their old queue; they cannot consume
                # any data from a subsequent session, even after a fast restart.
                self._audio_queue = queue.Queue(maxsize=self._queue_capacity)
            log.info("AirPlay 2 音频会话作废: generation=%s", self._generation)
            return
        self._active = False
        self._abort = True
        while True:
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break
        try:
            self._audio_queue.put_nowait(None)
        except queue.Full:
            pass
        log.info("音频流: 停止接收 PCM 数据")

    _DROP_LOG_INTERVAL = 10.0

    def write_pcm(self, data: bytes):
        """Write PCM without blocking, dropping only the oldest block on overflow."""
        if not self._isolate_sessions:
            self._write_pcm_locked(data)
            return
        with self._stream_lock:
            self._write_pcm_locked(data)

    def write_timed_pcm(self, data: bytes, playtime_ns: int):
        if self._output_latency_ns is None:
            raise RuntimeError("timed PCM requires a clock-aware stream")
        if not data or len(data) % (self._channels * self._sample_width):
            raise ValueError("timed PCM must contain complete stereo frames")
        with self._stream_lock:
            self._write_pcm_locked(TimedPCM(data, playtime_ns))

    def _write_pcm_locked(self, data: bytes):
        if not self._active:
            return
        try:
            self._audio_queue.put_nowait(data)
        except queue.Full:
            dropped = 0
            try:
                while True:
                    self._audio_queue.get_nowait()
                    dropped += 1
                    self._audio_queue.put_nowait(data)
                    break
            except queue.Empty:
                return
            except queue.Full:
                pass
            now = time.monotonic()
            if now - getattr(self, "_last_drop_log", 0.0) > self._DROP_LOG_INTERVAL:
                self._last_drop_log = now
                log.warning(f"{self._label} 音频队列溢出: 丢弃最旧 {dropped} 块 (~{dropped * 8}ms)")

    def _authorize_stream_request(self, request: web.Request) -> web.Response | None:
        """验证流会话令牌，并限制同时拉流数。"""
        if not self._active:
            log.info(f"{self._label}: 流已停止，拒绝拉取 (404)")
            return web.Response(status=404, headers={"Connection": "close"})
        supplied = request.query.get("sid", "")
        if not supplied.isascii() or not supplied or not secrets.compare_digest(supplied, self._session_id):
            log.warning(f"{self._label}: 拒绝无效音频流令牌")
            return web.Response(status=404, headers={"Connection": "close"})
        with self._client_lock:
            if self._client_count >= _MAX_STREAM_CLIENTS:
                return web.Response(status=429, headers={"Connection": "close"})
            self._client_count += 1
        return None

    def _release_stream_client(self) -> None:
        with self._client_lock:
            self._client_count = max(0, self._client_count - 1)

    def _build_wav_header(self, data_size: int = 0x7FFFFF00) -> bytes:
        byte_rate = self._sample_rate * self._channels * self._sample_width
        block_align = self._channels * self._sample_width
        bits_per_sample = self._sample_width * 8
        return struct.pack(
            '<4sI4s4sIHHIIHH4sI',
            b'RIFF', data_size + 36, b'WAVE',
            b'fmt ', 16, 1, self._channels,
            self._sample_rate, byte_rate, block_align, bits_per_sample,
            b'data', data_size,
        )

    async def _handle_stream_wav(self, request: web.Request) -> web.StreamResponse:
        if self._isolate_sessions:
            return await self._handle_isolated_wav(request)
        rejected = self._authorize_stream_request(request)
        if rejected is not None:
            return rejected
        try:
            return await self._stream_wav_authorized(request)
        finally:
            self._release_stream_client()

    async def _stream_wav_authorized(self, request: web.Request) -> web.StreamResponse:
        """WAV 模式: 直接输出 PCM 数据，零编码延迟

        使用专用写入线程从队列批量读取数据，通过 asyncio 事件写回 HTTP 响应，
        避免每个包都经过 asyncio.to_thread 的调度开销。
        """
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "audio/wav",
                "Cache-Control": "no-cache, no-store",
                "Pragma": "no-cache",
                "Connection": "close",
                "Transfer-Encoding": "chunked",
            },
        )
        await response.prepare(request)

        # 禁用 Nagle 算法以降低传输延迟
        transport = request.transport
        if transport is not None:
            sock = transport.get_extra_info('socket')
            if sock is not None:
                import socket as _sock
                sock.setsockopt(_sock.IPPROTO_TCP, _sock.TCP_NODELAY, 1)

        self._abort = False

        log.info(f"{self._label}: 音箱开始拉取 WAV 音频流 (零编码延迟)")

        # 使用连接私有的中断事件，避免重连时被旧连接清理逻辑误置位
        loop = asyncio.get_event_loop()
        data_ready = asyncio.Event()
        pending_data: deque[bytes] = deque()
        pending_bytes = 0
        data_lock = threading.Lock()
        writer_done = False
        conn_abort = threading.Event()

        def _append_pending(chunks: list[bytes]) -> None:
            """写入连接待发送区，并在慢客户端时丢弃过期 PCM。"""
            nonlocal pending_bytes
            dropped_bytes = 0
            with data_lock:
                for chunk in chunks:
                    pending_data.append(chunk)
                    pending_bytes += len(chunk)
                while pending_bytes > _PENDING_MAX_BYTES and pending_data:
                    dropped = len(pending_data.popleft())
                    dropped_bytes += dropped
                    pending_bytes -= dropped
            if dropped_bytes:
                # 读取线程可能同时服务多个拉流客户端；集中计数并限频记录。
                with self._pending_drop_lock:
                    self._pending_drop_bytes += dropped_bytes
                    now = time.monotonic()
                    should_log = now - self._last_pending_drop_log > 10.0
                    if should_log:
                        self._last_pending_drop_log = now
                if should_log:
                    log.warning(
                        f"{self._label} HTTP 音频客户端写入滞后，丢弃过期 PCM "
                        f"({dropped_bytes} bytes)"
                    )

        def _reader_thread():
            """专用线程：从队列批量读取 PCM 并通知事件循环"""
            nonlocal writer_done
            empty_streak = 0
            try:
                while self._active and not conn_abort.is_set():
                    try:
                        chunk = self._audio_queue.get(timeout=0.02)
                        if chunk is None:
                            break
                        local_batch = [chunk]
                        for _ in range(31):
                            try:
                                extra = self._audio_queue.get_nowait()
                                if extra is None:
                                    break
                                local_batch.append(extra)
                            except queue.Empty:
                                break
                        _append_pending(local_batch)
                        loop.call_soon_threadsafe(data_ready.set)
                        empty_streak = 0
                    except queue.Empty:
                        empty_streak += 1
                        # 持续无数据时填充静音帧以维持 HTTP 连接
                        if empty_streak > 100:
                            silence = b'\x00' * (self._sample_rate * self._channels * self._sample_width // 50)
                            _append_pending([silence])
                            loop.call_soon_threadsafe(data_ready.set)
                        continue
            except Exception as e:
                log.error(f"WAV reader 异常: {e}")
            finally:
                writer_done = True
                loop.call_soon_threadsafe(data_ready.set)

        reader = threading.Thread(target=_reader_thread, daemon=True)
        reader.start()

        try:
            await response.write(self._build_wav_header())

            while not writer_done:
                await data_ready.wait()
                data_ready.clear()
                with data_lock:
                    chunks = list(pending_data)
                    pending_data.clear()
                    pending_bytes = 0
                if chunks:
                    await response.write(b"".join(chunks))
        except (ConnectionResetError, BrokenPipeError):
            log.info(f"{self._label}: 音箱断开 WAV 音频流连接")
        except Exception:
            pass
        finally:
            conn_abort.set()

        try:
            await response.write_eof()
        except Exception:
            pass
        log.info(f"{self._label}: WAV 音频流结束")
        return response

    def _retire_isolated_clients(self):
        """Called under _stream_lock, including from the PCM/control threads."""
        for abort, loop, transport in self._isolated_clients.values():
            abort.set()
            if transport is not None and not loop.is_closed():
                # Force-close instead of waiting for a stalled write or sending
                # an EOF that the speaker could cache as a completed WAV file.
                loop.call_soon_threadsafe(transport.abort)

    def _request_clock_resync(self, generation, reason, seed=None):
        callback = self.on_clock_resync
        return bool(callback and callback(generation, reason, seed))

    async def _handle_isolated_wav(self, request: web.Request) -> web.StreamResponse:
        headers = {"Content-Type": "audio/wav", "Cache-Control": "no-cache, no-store",
                   "Connection": "close"}
        loop = asyncio.get_running_loop()
        abort = threading.Event()
        token = object()
        transport = request.transport
        with self._stream_lock:
            if not self._active:
                return web.Response(status=404, headers=headers)
            supplied = request.query.get("sid", "")
            if not supplied.isascii() or not supplied or not secrets.compare_digest(supplied, self._session_id):
                log.info("AirPlay 2 拒绝无效或过期的音频流令牌")
                return web.Response(status=410, headers=headers)
            if request.method == "HEAD":
                return web.Response(headers=headers)
            with self._client_lock:
                if self._client_count >= _MAX_STREAM_CLIENTS:
                    return web.Response(status=429, headers=headers)
                self._client_count += 1
            # A retry may arrive before the previous GET disconnects. There is
            # exactly one consumer, not two readers sharing a destructive queue.
            self._retire_isolated_clients()
            self._isolated_clients[token] = (abort, loop, transport)
            generation = self._generation
            audio_queue = self._audio_queue
        started = last_report = time.monotonic()
        written = 0
        response = web.StreamResponse(headers=headers)
        log.info("AirPlay 2 拉流开始: generation=%s peer=%s", generation, request.remote)
        try:
            await response.prepare(request)
            await response.write(self._build_wav_header())
            if self._output_latency_ns is not None:
                written = await self._send_timed_wav(response, abort, generation, audio_queue, started)
                return response
            while not abort.is_set():
                with self._stream_lock:
                    if not self._active or generation != self._generation:
                        break
                    chunks = []
                    for _ in range(32):
                        try:
                            chunk = audio_queue.get_nowait()
                        except queue.Empty:
                            break
                        if chunk:
                            chunks.append(chunk)
                if not chunks:
                    # Do not fabricate silence during a seek/pause. Session
                    # termination comes from the control event, not this timer.
                    await asyncio.sleep(0.01)
                    continue
                if abort.is_set():
                    break
                data = b"".join(chunks)
                # No unbounded pending_data list. A slow client can retain only
                # this bounded batch plus the bounded PCM queue.
                await asyncio.wait_for(response.write(data), timeout=2.0)
                written += len(data)
                now = time.monotonic()
                if now - last_report >= 1.0:
                    log.info("AirPlay 2 拉流进度: generation=%s elapsed_ms=%.1f written_bytes=%s queue_chunks=%s",
                             generation, (now - started) * 1000, written, audio_queue.qsize())
                    last_report = now
        except (ConnectionResetError, BrokenPipeError, asyncio.TimeoutError):
            pass
        finally:
            unexpected_disconnect = not abort.is_set()
            abort.set()
            with self._stream_lock:
                self._isolated_clients.pop(token, None)
            self._release_stream_client()
            if transport is not None:
                transport.abort()
            log.info("AirPlay 2 拉流结束: generation=%s elapsed_ms=%.1f written_bytes=%s",
                     generation, (time.monotonic() - started) * 1000, written)
            if self._output_latency_ns is not None and unexpected_disconnect:
                self._request_clock_resync(generation, "http-disconnected-or-write-timeout")
        return response

    async def _send_timed_wav(self, response, abort, generation, audio_queue, started):
        """Schedule each block against PTP, not HTTP connection age.

        The Xiaomi DAC delay remains a calibrated estimate: this does NOT claim
        device-clock feedback. Startup/cloud delay must never become PCM backlog.
        """
        pending = None
        written = dropped = 0
        last_report = time.monotonic()
        last_shortfall_report = float('-inf')
        first_pts_ns = None
        first_lead_ns = None
        frame_bytes = self._channels * self._sample_width
        while not abort.is_set():
            with self._stream_lock:
                if not self._active or generation != self._generation:
                    break
                if pending is None:
                    try:
                        pending = audio_queue.get_nowait()
                    except queue.Empty:
                        pass
            if pending is None:
                await asyncio.sleep(0.002)
                continue
            if not isinstance(pending, TimedPCM):
                raise RuntimeError("untimed PCM on the PTP bridge")
            now_ns = clock_ns()
            target_ns = now_ns + self._output_latency_ns
            lateness_ns = target_ns - pending.playtime_ns
            # Exact catch-up at startup. During steady playback tolerate 80 ms
            # scheduling/network jitter rather than cutting every late packet.
            if written == 0 or lateness_ns > 80_000_000:
                # A live sender may not supply enough future audio to meet the
                # estimated Xiaomi latency. Do not discard every arriving block
                # in that case: only catch up to the estimate when a replacement
                # at that deadline is already queued. Otherwise discard actual
                # expired audio, preserving future samples and reporting the
                # unattainable lead rather than claiming clock lock.
                with audio_queue.mutex:
                    future_available = any(isinstance(item, TimedPCM) and
                                           item.playtime_ns >= target_ns
                                           for item in audio_queue.queue)
                trim_ns = target_ns if future_available else now_ns
                pending, trimmed = pending.trim_before(trim_ns)
                dropped += trimmed
                if pending is None:
                    continue
            wait_ns = pending.playtime_ns - self._output_latency_ns - clock_ns()
            if wait_ns > 1_000_000:
                await asyncio.sleep(min(wait_ns / 1_000_000_000, 0.01))
                continue
            if abort.is_set():
                break
            first = written == 0
            lead_ns = pending.playtime_ns - clock_ns()
            lead_ms = lead_ns / 1_000_000
            # A WAV carries only sample count, not each block's PTP timestamp.
            # Skipping PCM or changing the anchor in an existing response breaks
            # that mapping even when future HTTP writes again meet their PTS.
            # Compare against cumulative emitted frames (not prior packet only)
            # so small discontinuities cannot accumulate unnoticed.
            if first_pts_ns is not None:
                expected_pts_ns = first_pts_ns + (written // frame_bytes) * 1_000_000_000 // self._sample_rate
                shift_ns = pending.playtime_ns - expected_pts_ns
                if abs(shift_ns) > 80_000_000:
                    if self._request_clock_resync(generation, f"wav-timeline-shift:{shift_ns / 1_000_000:.1f}ms", pending):
                        break
            else:
                first_pts_ns = pending.playtime_ns
                first_lead_ns = lead_ns
            # Continuous PTS does not imply continuous delivery: an underrun
            # can leave the native WAV player permanently behind. Use this
            # response's initial lead, not the configured estimate (a live
            # sender may consistently provide less look-ahead).
            if not first and first_lead_ns - lead_ns > 80_000_000:
                if self._request_clock_resync(generation,
                                              f"wav-send-late:{(first_lead_ns - lead_ns) / 1_000_000:.1f}ms", pending):
                    break
            if (self._output_latency_ns / 1_000_000 - lead_ms > 80 and
                    time.monotonic() - last_shortfall_report >= 1):
                log.warning("AirPlay 2 提前量不足: generation=%s available_lead_ms=%.1f required_lead_ms=%.1f; 保留未过期音频，尚未同步",
                            generation, lead_ms, self._output_latency_ns / 1_000_000)
                last_shortfall_report = time.monotonic()
            await asyncio.wait_for(response.write(pending.data), timeout=0.25)
            written += len(pending.data)
            if first:
                log.info("AirPlay 2 时钟首帧: generation=%s pts_lead_ms=%.1f output_latency_ms=%.1f skipped_ms=%.1f",
                         generation, lead_ms, self._output_latency_ns / 1_000_000,
                         dropped * 1000 / self._sample_rate)
            pending = None
            now = time.monotonic()
            if now - last_report >= 1:
                log.info("AirPlay 2 时钟拉流: generation=%s elapsed_ms=%.1f written_bytes=%s queue_chunks=%s pts_lead_ms=%.1f skipped_ms=%.1f",
                         generation, (now - started) * 1000, written, audio_queue.qsize(),
                         lead_ms, dropped * 1000 / self._sample_rate)
                last_report = now
        log.info("AirPlay 2 时钟拉流结束: generation=%s written_bytes=%s skipped_ms=%.1f",
                 generation, written, dropped * 1000 / self._sample_rate)
        return written

    async def _handle_stream_mp3(self, request: web.Request) -> web.StreamResponse:
        rejected = self._authorize_stream_request(request)
        if rejected is not None:
            return rejected
        try:
            return await self._stream_mp3_authorized(request)
        finally:
            self._release_stream_client()

    async def _stream_mp3_authorized(self, request: web.Request) -> web.StreamResponse:

        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "audio/mpeg",
                "Cache-Control": "no-cache, no-store",
                "Pragma": "no-cache",
                "Connection": "close",
            },
        )
        await response.prepare(request)

        # 禁用 Nagle 算法
        transport = request.transport
        if transport is not None:
            sock = transport.get_extra_info('socket')
            if sock is not None:
                import socket as _sock
                sock.setsockopt(_sock.IPPROTO_TCP, _sock.TCP_NODELAY, 1)

        self._abort = False
        conn_abort = threading.Event()

        log.info(f"{self._label}: 音箱开始拉取 MP3 音频流")

        import os
        ffmpeg_bin = "ffmpeg"
        if os.path.exists("ffmpeg.exe"):
            ffmpeg_bin = os.path.abspath("ffmpeg.exe")
        elif os.path.exists("bin/ffmpeg.exe"):
            ffmpeg_bin = os.path.abspath("bin/ffmpeg.exe")

        ffmpeg_cmd = [
            ffmpeg_bin,
            "-loglevel", "error",
            "-fflags", "+nobuffer+flush_packets",
            "-flags", "low_delay",
            "-f", "s16le",
            "-ar", str(self._sample_rate),
            "-ac", str(self._channels),
            "-i", "pipe:0",
            "-acodec", "libmp3lame",
            "-compression_level", "0",
            "-ab", "128k",
            "-ac", "2",
            "-ar", "44100",
            "-flush_packets", "1",
            "-id3v2_version", "0",
            "-f", "mp3",
            "pipe:1"
        ]

        try:
            proc = subprocess.Popen(
                ffmpeg_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except FileNotFoundError:
            log.error("ffmpeg 未找到，无法转码音频流")
            await response.write_eof()
            return response

        def drain_stderr():
            try:
                while True:
                    line = proc.stderr.readline()
                    if not line:
                        break
                    line_str = line.decode('utf-8', errors='ignore').strip()
                    if line_str and 'rror' in line_str:
                        pass
            except Exception:
                pass

        threading.Thread(target=drain_stderr, daemon=True).start()

        def feed_ffmpeg():
            empty_streak = 0
            try:
                while True:
                    try:
                        chunk = self._audio_queue.get(timeout=0.05)
                        if chunk is None:
                            break
                        chunks = [chunk]
                        for _ in range(15):
                            try:
                                extra = self._audio_queue.get_nowait()
                                if extra is None:
                                    proc.stdin.close()
                                    return
                                chunks.append(extra)
                            except queue.Empty:
                                break
                        proc.stdin.write(b"".join(chunks))
                        empty_streak = 0
                    except queue.Empty:
                        empty_streak += 1
                        if empty_streak > 40:
                            silence = b'\x00' * (self._sample_rate * self._channels * self._sample_width // 50)
                            try:
                                proc.stdin.write(silence)
                            except Exception:
                                break
                        continue
                    except (BrokenPipeError, OSError):
                        break
            except Exception:
                pass
            finally:
                try:
                    proc.stdin.close()
                except Exception:
                    pass

        threading.Thread(target=feed_ffmpeg, daemon=True).start()

        try:
            while self._active and not conn_abort.is_set():
                audio_data = await asyncio.to_thread(proc.stdout.read, 8192)
                if not audio_data or self._abort:
                    break
                await response.write(audio_data)
        except (ConnectionResetError, BrokenPipeError):
            log.info(f"{self._label}: 音箱断开 MP3 音频流连接")
        except Exception:
            pass
        finally:
            conn_abort.set()
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=2)
                except Exception:
                    pass

        try:
            await response.write_eof()
        except Exception:
            pass
        log.info(f"{self._label}: MP3 音频流结束")
        return response
