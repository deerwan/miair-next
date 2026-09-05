"""AirPlay 音频 HTTP 流输出

将解码后的 PCM 音频数据转换为 HTTP 音频流，供小爱音箱播放。
支持两种输出格式:
- MP3: 兼容性好，适用于 L05B/L05C/LX06/L16A 等不支持 WAV 的音箱
- WAV: 零编码延迟，直接输出 PCM，适用于大多数音箱
"""

import asyncio
import logging
import queue
import struct
import subprocess
import threading
import time

from aiohttp import web

log = logging.getLogger("miair")

# --- 队列参数 ---
# 每个 ALAC 包约 8ms (352 samples @ 44100Hz)
# 100 个包 ≈ 800ms 的缓冲上限，兼顾低延迟与抗 WiFi 抖动
_QUEUE_MAXSIZE = 100


class AudioStreamServer:
    """HTTP 音频流服务器

    接收 PCM 音频数据，通过 HTTP 提供给小爱音箱播放。
    根据音箱型号选择 MP3 (ffmpeg 转码) 或 WAV (直接输出) 格式。
    """

    def __init__(self, hostname: str, port: int = 0, audio_format: str = "wav"):
        self.hostname = hostname
        self.port = port
        self._audio_format = audio_format  # "mp3" or "wav"
        self._app = web.Application()
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

        # 音频数据队列 - 小队列 = 低延迟
        self._audio_queue: queue.Queue[bytes | None] = queue.Queue(maxsize=_QUEUE_MAXSIZE)
        self._sample_rate = 44100
        self._channels = 2
        self._sample_width = 2  # 16-bit
        self._active = False
        self._abort = False
        self._session_id = int(time.time())
        self._client_count = 0  # 正在拉流的连接数（计数代替布尔，避免新旧连接交接时互相覆盖）
        self._client_lock = threading.Lock()

        self._setup_routes()

    def _setup_routes(self):
        if self._audio_format == "mp3":
            self._app.router.add_get("/airplay/stream.mp3", self._handle_stream_mp3)
        else:
            self._app.router.add_get("/airplay/stream.wav", self._handle_stream_wav)

    @property
    def stream_url(self) -> str:
        ext = "mp3" if self._audio_format == "mp3" else "wav"
        return f"http://{self.hostname}:{self.port}/airplay/stream.{ext}?sid={self._session_id}"

    @property
    def has_clients(self) -> bool:
        """是否有音箱正在拉取音频流"""
        with self._client_lock:
            return self._client_count > 0

    async def start(self):
        self._runner = web.AppRunner(self._app, access_log=None)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "0.0.0.0", self.port)
        await self._site.start()
        self.port = self._site._server.sockets[0].getsockname()[1]
        log.info(f"AirPlay 音频流服务器: http://{self.hostname}:{self.port} (格式: {self._audio_format})")

    async def stop(self):
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

    def start_streaming(self):
        self._active = True
        self._abort = False
        self._session_id = int(time.time())
        # 快速清空队列
        while True:
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break
        log.info(f"音频流: 开始接收 PCM 数据 (格式: {self._audio_format})")

    def stop_streaming(self):
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

    # 队列溢出日志节流: 溢出本不应发生 (丢包闸门已前移), 一旦发生要可见但不刷屏
    _DROP_LOG_INTERVAL = 10.0

    def write_pcm(self, data: bytes):
        """写入 PCM 音频数据 — 非阻塞，队列满时只丢最旧的最少数量"""
        if not self._active:
            return
        try:
            self._audio_queue.put_nowait(data)
        except queue.Full:
            # 只丢到能放下本块为止 (通常 1-2 块 ≈ 10-20ms), 原实现一次丢 25%
            # (~200ms) 会造成明显的整段跳顿
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
                log.warning(f"音频队列溢出: 丢弃最旧 {dropped} 块 (~{dropped * 8}ms)")

    # ============================================================
    # WAV 模式 — 直接输出 PCM，零编码延迟
    # ============================================================

    def _reject_if_inactive(self) -> web.Response | None:
        """流已停止（TEARDOWN 后）返回 404 拒绝拉取，避免音箱把空响应当成 EOF
        反复重连该 URL 造成死循环（氛围灯闪烁、固件崩溃）。未停止返回 None。
        """
        if not self._active:
            log.info("AirPlay: 流已停止，拒绝拉取 (404)")
            return web.Response(status=404, headers={"Connection": "close"})
        return None

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
        """WAV 模式: 直接输出 PCM 数据，零编码延迟

        使用专用写入线程从队列批量读取数据，通过 asyncio 事件写回 HTTP 响应，
        避免每个包都经过 asyncio.to_thread 的调度开销。
        """
        rejected = self._reject_if_inactive()
        if rejected is not None:
            return rejected

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

        # 关闭 Nagle 算法，让小包立即发送而不等待合并
        transport = request.transport
        if transport is not None:
            sock = transport.get_extra_info('socket')
            if sock is not None:
                import socket as _sock
                sock.setsockopt(_sock.IPPROTO_TCP, _sock.TCP_NODELAY, 1)

        with self._client_lock:
            self._client_count += 1
        self._abort = False  # 重置中断标志，允许续播

        log.info("AirPlay: 音箱开始拉取 WAV 音频流 (零编码延迟)")

        # 使用 asyncio.Event 在写入线程和事件循环间通信
        # 注意: 中断用连接私有的 Event 而非全局 _abort ——
        # 歌词补发/切歌时音箱断开旧连接后立即重连同一 URL，
        # 若旧 handler 的清理在新 handler 启动后才执行，全局标志会
        # 被旧连接置回 True，导致新连接 reader 误退出（有歌词无声）
        loop = asyncio.get_event_loop()
        data_ready = asyncio.Event()
        pending_data: list[bytes] = []
        data_lock = threading.Lock()
        writer_done = False
        conn_abort = threading.Event()

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
                        # 本地批量收集，避免每个 chunk 都加锁
                        local_batch = [chunk]
                        for _ in range(31):
                            try:
                                extra = self._audio_queue.get_nowait()
                                if extra is None:
                                    break
                                local_batch.append(extra)
                            except queue.Empty:
                                break
                        with data_lock:
                            pending_data.extend(local_batch)
                        loop.call_soon_threadsafe(data_ready.set)
                        empty_streak = 0
                    except queue.Empty:
                        empty_streak += 1
                        # 3 秒无数据写入静音保持连接
                        if empty_streak > 100:
                            silence = b'\x00' * (self._sample_rate * self._channels * self._sample_width // 50)
                            with data_lock:
                                pending_data.append(silence)
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
            # 发送 WAV 头
            await response.write(self._build_wav_header())

            while not writer_done:
                await data_ready.wait()
                data_ready.clear()
                with data_lock:
                    chunks = pending_data
                    pending_data = []
                if chunks:
                    # 批量写入：合并所有 chunk 一次性写出
                    await response.write(b"".join(chunks))
        except (ConnectionResetError, BrokenPipeError):
            log.info("AirPlay: 音箱断开 WAV 音频流连接")
        except Exception:
            pass
        finally:
            conn_abort.set()  # 只中断本连接的 reader，不影响后续重连
            with self._client_lock:
                self._client_count -= 1

        try:
            await response.write_eof()
        except Exception:
            pass
        log.info("AirPlay: WAV 音频流结束")
        return response

    # ============================================================
    # MP3 模式 — ffmpeg 实时转码 (用于不支持 WAV 的音箱)
    # ============================================================

    async def _handle_stream_mp3(self, request: web.Request) -> web.StreamResponse:
        rejected = self._reject_if_inactive()
        if rejected is not None:
            return rejected

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

        # 关闭 Nagle 算法
        transport = request.transport
        if transport is not None:
            sock = transport.get_extra_info('socket')
            if sock is not None:
                import socket as _sock
                sock.setsockopt(_sock.IPPROTO_TCP, _sock.TCP_NODELAY, 1)

        with self._client_lock:
            self._client_count += 1
        self._abort = False  # 重置中断标志，允许续播
        conn_abort = threading.Event()  # 连接私有中断标志（同 WAV 模式说明）

        log.info("AirPlay: 音箱开始拉取 MP3 音频流")

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
            "-compression_level", "0",  # 最快编码速度
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
            with self._client_lock:
                self._client_count -= 1
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
                        # 批量收集数据后一次性写入，减少 write 系统调用次数
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

        # 从 ffmpeg stdout 读取并写给客户端
        try:
            while self._active and not conn_abort.is_set():
                audio_data = await asyncio.to_thread(proc.stdout.read, 8192)
                if not audio_data or self._abort:
                    break
                await response.write(audio_data)
        except (ConnectionResetError, BrokenPipeError):
            log.info("AirPlay: 音箱断开 MP3 音频流连接")
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

        with self._client_lock:
            self._client_count -= 1

        try:
            await response.write_eof()
        except Exception:
            pass
        log.info("AirPlay: MP3 音频流结束")
        return response
