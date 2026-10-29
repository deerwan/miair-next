"""AirPlay 2 receiver backed by Shairport Sync.

Shairport Sync and NQPTP handle the undocumented AirPlay 2 pairing,
buffered-audio and PTP timing layers.  This adapter fixes their stdout backend
to 48 kHz / signed 16-bit / stereo PCM and feeds it into MiAir's existing HTTP
audio stream, so Xiaomi speakers can pull the result in the same way as the
native AirPlay 1 receiver.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import select
import shutil
import subprocess
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable

from app.engine.airplay.shairport_metadata import ShairportMetadata
from app.engine.airplay.timed_audio import TimedPCM, read_record
from app.engine.audio_stream import AudioStreamServer

log = logging.getLogger("miair")

_SAMPLE_RATE = 48000
_CHANNELS = 2
_SAMPLE_WIDTH = 2
_FRAME_SIZE = _CHANNELS * _SAMPLE_WIDTH
_READ_SIZE = _SAMPLE_RATE * _FRAME_SIZE // 100  # 10 ms, ~1 s in the 100-chunk queue
_SESSION_IDLE_SECONDS = 4.0
_NQPTP_SHM_PATH = "/dev/shm/nqptp"


def _libconfig_string(value: str) -> str:
    """Escape a string for a quoted libconfig value."""
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


class ShairportSyncServer:
    """AirPlay 2 server exposing the subset used by ``SpeakerAirPlay``."""

    def __init__(
        self,
        hostname: str,
        device_name: str = "MiAir",
        shared_zeroconf=None,
        speaker_hardware: str = "",
        *,
        port: int = 7000,
        output_latency_ms: int = 1000,
    ):
        del shared_zeroconf, speaker_hardware
        self.hostname = hostname
        self.device_name = device_name
        self.rtsp_port = port
        self.stream_port = 0
        self._timed_stdout = os.getenv("MIAIR_AIRPLAY2_TIMED_STDOUT", "") == "1"
        self.output_latency_ms = max(0, min(4000, output_latency_ms))

        self.on_play_start: Callable[[str], None] | None = None
        self.on_play_stop: Callable[[], None] | None = None
        self.on_volume_change: Callable[[float], None] | None = None

        self._stream_server = AudioStreamServer(
            hostname, 0, audio_format="wav", isolate_sessions=True,
            output_latency_ms=self.output_latency_ms if self._timed_stdout else None,
        )
        self._stream_server.set_audio_params(_SAMPLE_RATE, _CHANNELS, _SAMPLE_WIDTH)
        self._stream_server.on_clock_resync = self._on_stream_resync
        self._last_clock_resync_at = float('-inf')
        self._clock_resync_timer: threading.Timer | None = None
        self._shairport_process: subprocess.Popen | None = None
        self._nqptp_process: subprocess.Popen | None = None
        self._nqptp_shm_identity: tuple[int, int] | None = None
        self._config_path = ""
        self._metadata = ShairportMetadata()
        self._metadata_dir = None
        self._metadata_fd = None
        self._metadata_thread = None
        self._metadata_stop = threading.Event()
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._nqptp_stderr_thread: threading.Thread | None = None
        self._shairport_messages: deque[str] = deque(maxlen=12)
        self._nqptp_messages: deque[str] = deque(maxlen=12)
        self._monitor_task: asyncio.Task | None = None
        self._stop_lock = asyncio.Lock()
        self._running = False
        self._is_playing = False
        self._last_audio_at = 0.0
        self._state_lock = threading.RLock()
        self._timeline_generation = 0
        self._play_allowed = True
        self.last_error = ""

    @staticmethod
    def available() -> bool:
        return bool(shutil.which("shairport-sync") and shutil.which("nqptp"))

    @property
    def is_playing(self) -> bool:
        return self._is_playing

    @property
    def running(self) -> bool:
        process = self._shairport_process
        clock = self._nqptp_process
        return bool(
            self._running and process and process.poll() is None
            and clock and clock.poll() is None
        )

    @property
    def stream_has_clients(self) -> bool:
        return self._stream_server.has_clients

    def get_audio_diagnostics(self) -> dict:
        """Expose bridge metrics; native RTP/decoder counters are unavailable."""
        return {
            "protocol": "airplay2",
            "playing": self.is_playing,
            "sample_rate": _SAMPLE_RATE,
            "channels": _CHANNELS,
            "stream_clients": self._stream_server.client_count,
            "stream_queue_depth": self._stream_server.queue_depth,
            "stream_pending_dropped_bytes": self._stream_server.pending_drop_bytes,
        }

    @property
    def client_name(self) -> str:
        return self._metadata.client_name

    @property
    def daap_meta(self):
        return self._metadata.meta

    @property
    def daap_candidates(self) -> list:
        return [meta for _, meta in self._metadata.events]

    @property
    def daap_events(self) -> list:
        return self._metadata.events

    @property
    def track_metadata(self):
        return self._metadata.snapshot()

    @property
    def artwork(self):
        return self._metadata.artwork

    def _start_metadata(self):
        self._metadata = ShairportMetadata()
        self._metadata_stop.clear()
        self._metadata_dir = tempfile.TemporaryDirectory(prefix="miair-metadata-")
        pipe_path = os.path.join(self._metadata_dir.name, "metadata")
        os.mkfifo(pipe_path, 0o600)
        # Keep the reader present before Shairport opens its writer, including
        # startup (when _running is still false) and pauses with no events.
        self._metadata_fd = os.open(pipe_path, os.O_RDWR | os.O_NONBLOCK)
        self._metadata_thread = threading.Thread(
            target=self._read_metadata, name="miair-shairport-metadata", daemon=True)
        self._metadata_thread.start()

    def _read_metadata(self):
        fd = self._metadata_fd
        try:
            while not self._metadata_stop.is_set():
                if select.select([fd], [], [], 0.2)[0]:
                    chunk = os.read(fd, 65536)
                    if chunk:
                        self._metadata.feed(chunk)
        except (OSError, ValueError):
            if not self._metadata_stop.is_set():
                log.exception("AirPlay 2 metadata reader failed")

    def _make_config(self) -> str:
        name = _libconfig_string(self.device_name[:50])
        interface = os.getenv("MIAIR_AIRPLAY_INTERFACE", "").strip()
        interface_setting = f'    interface = "{_libconfig_string(interface)}";\n' if interface else ""
        timing_setting = ""
        if self._timed_stdout:
            # Supply future PCM before its send deadline; never double-shift the
            # PTP anchor with audio_backend_latency_offset as well.
            timing_setting = (
                # AP2's decoder otherwise withholds future packets until their
                # PTS is within the default 0.75 + 0.1 s window, regardless of
                # the stdout backend's requested lead. Both gates must open.
                f"    audio_decoded_buffer_desired_length_in_seconds = {self.output_latency_ms / 1000 + 0.35:.3f};\n"
                f"    audio_backend_buffer_desired_length_in_seconds = {self.output_latency_ms / 1000 + 0.15:.3f};\n"
                "    audio_backend_latency_offset_in_seconds = 0.0;\n"
            )
        return f'''general =
{{
    name = "{name}";
    service_type = "airplay2";
    output_backend = "stdout";
    port = {self.rtsp_port};
    mdns_backend = "avahi";
    ignore_volume_control = "no";
    playback_mode = "stereo";
{interface_setting}{timing_setting}}};

stdout =
{{
    output_rate = {_SAMPLE_RATE};
    output_format = "S16_LE";
    output_channels = {_CHANNELS};
}};

metadata =
{{
    enabled = "yes";
    include_cover_art = "yes";
    pipe_name = "{_libconfig_string(os.path.join(self._metadata_dir.name, 'metadata')) if self._metadata_dir else ''}";
}};

diagnostics =
{{
    // Level 2 emits timeline control boundaries independently of metadata.
    log_verbosity = 2;
}};
'''

    async def start(self):
        if self._running:
            return
        shairport_bin = shutil.which("shairport-sync")
        nqptp_bin = shutil.which("nqptp")
        if not shairport_bin or not nqptp_bin:
            missing = []
            if not shairport_bin:
                missing.append("shairport-sync")
            if not nqptp_bin:
                missing.append("nqptp")
            raise RuntimeError(f"AirPlay 2 组件缺失: {', '.join(missing)}")

        self.last_error = ""
        self._shairport_messages.clear()
        self._nqptp_messages.clear()
        with self._state_lock:
            self._timeline_generation += 1
            self._play_allowed = True
            self._last_audio_at = 0.0
        try:
            await self._stream_server.start()
            self.stream_port = self._stream_server.port
            self._start_metadata()

            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", prefix="miair-shairport-",
                suffix=".conf", delete=False,
            ) as config_file:
                self._config_path = config_file.name
                config_file.write(self._make_config())

            await self._start_nqptp(nqptp_bin)
            self._shairport_process = subprocess.Popen(
                [shairport_bin, "-c", self._config_path],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                start_new_session=True,
                env={**os.environ, "MIAIR_TIMED_STDOUT": "1" if self._timed_stdout else "0"},
            )
            self._stderr_thread = self._start_stderr_reader(
                self._shairport_process, "shairport-sync", self._shairport_messages,
                self._on_shairport_message,
            )
            await asyncio.sleep(1.0)
            if self._shairport_process.poll() is not None:
                await asyncio.to_thread(self._stderr_thread.join, 0.5)
                detail = self._process_error(self._shairport_process, self._shairport_messages)
                raise RuntimeError(f"shairport-sync 启动失败: {detail}")

            self._running = True
            if self._timed_stdout:
                log.info("AirPlay 2 PTP 时间戳桥已启用: clock=CLOCK_MONOTONIC_RAW output_latency_ms=%s (校准估计，非音箱时钟反馈)",
                         self.output_latency_ms)
            self._reader_thread = threading.Thread(target=self._read_audio, daemon=True)
            self._reader_thread.start()
            self._monitor_task = asyncio.create_task(self._monitor())
            log.info(
                "AirPlay 2 服务已启动: %s (RTSP %s, PCM %s Hz/S16LE/stereo)",
                self.device_name,
                self.rtsp_port,
                _SAMPLE_RATE,
            )
        except BaseException:
            # Cancellation during a settings restart must also release ports/processes.
            await self.stop()
            raise

    async def _start_nqptp(self, nqptp_bin: str):
        # NQPTP 1.2.8 has a single-client shared clock. A shared-memory file is
        # not proof of a live/compatible daemon, so never silently reuse it.
        # The test container must keep its default, private IPC namespace.
        if os.path.exists(_NQPTP_SHM_PATH):
            raise RuntimeError("检测到已有 NQPTP 共享时钟；请使用独立容器 IPC，勿使用 --ipc=host")

        self._nqptp_process = subprocess.Popen(
            [nqptp_bin],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            bufsize=0,
            start_new_session=True,
        )
        self._nqptp_stderr_thread = self._start_stderr_reader(
            self._nqptp_process, "nqptp", self._nqptp_messages
        )
        try:
            await asyncio.sleep(0.25)
        finally:
            # Remember the object created by our child even if startup is
            # cancelled or fails. Never remove a pre-existing/replaced clock.
            self._remember_nqptp_clock()
        if self._nqptp_process.poll() is not None:
            await asyncio.to_thread(self._nqptp_stderr_thread.join, 0.5)
            detail = self._process_error(self._nqptp_process, self._nqptp_messages)
            raise RuntimeError(
                f"NQPTP 启动失败 ({detail})；请确认 UDP 319/320 未被 PTP 服务占用"
            )
        log.info("NQPTP 时钟同步服务已启动 (UDP 319/320)")

    def _remember_nqptp_clock(self):
        if self._nqptp_process is None or self._nqptp_shm_identity is not None:
            return
        try:
            stat = os.stat(_NQPTP_SHM_PATH)
            self._nqptp_shm_identity = (stat.st_dev, stat.st_ino)
        except FileNotFoundError:
            pass

    def _remove_owned_nqptp_clock(self):
        identity = self._nqptp_shm_identity
        self._nqptp_shm_identity = None
        if identity is None:
            return
        try:
            stat = os.stat(_NQPTP_SHM_PATH)
            if (stat.st_dev, stat.st_ino) == identity:
                os.unlink(_NQPTP_SHM_PATH)
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.warning("Could not remove the stopped NQPTP clock: %s", exc)

    @staticmethod
    def _process_error(process: subprocess.Popen, messages: deque[str]) -> str:
        return " | ".join(messages) or f"exit {process.returncode}"

    def _begin_session(self):
        with self._state_lock:
            if self._is_playing or not self._play_allowed:
                return
            self._is_playing = True
            self._stream_server.start_streaming()
        log.info("AirPlay 2 开始输出音频: %s", self.device_name)
        if self.on_play_start:
            self.on_play_start(self._stream_server.stream_url)

    def _end_session(self):
        with self._state_lock:
            if not self._is_playing:
                return
            self._is_playing = False
            self._stream_server.stop_streaming()
        log.info("AirPlay 2 停止输出音频: %s", self.device_name)
        if self.on_play_stop:
            self.on_play_stop()

    def _on_shairport_message(self, line: str):
        """Control-only messages from the pinned Shairport Sync 5.5.2 build.

        Do not match the raw FLUSHBUFFERED request: deferred flushes are not
        immediate seeks. Only the accepted immediate request retires the stream.
        The sample-level stdout pipe cannot itself carry these boundaries.
        """
        reason = ""
        resume = False
        classic_flush = False
        if re.search(r"Connection \d+: SETRATEANCHORI Pause playing\.$", line):
            reason = "pause"
        elif re.search(r"Connection \d+: immediate flush request created:", line):
            reason = "flushbuffered"
        elif re.search(r"RTSP Flush Requested: \d+\.$", line):
            reason = "classic-flush"
            classic_flush = True
        elif re.search(r"Connection \d+: SETRATEANCHORI Start playing, with anchor clock [0-9a-f]+\.$", line):
            resume = True
        elif re.search(r"Connection \d+: player_stop\.$", line):
            reason = "stop"
        else:
            return
        with self._state_lock:
            if resume:
                if not self._play_allowed:
                    self._timeline_generation += 1
                self._play_allowed = True
                log.info("AirPlay 2 播放时间锚点更新: generation=%s", self._timeline_generation)
            else:
                self._timeline_generation += 1
                self._play_allowed = classic_flush
                self._end_session()
                log.info("AirPlay 2 时间线重置: reason=%s generation=%s", reason, self._timeline_generation)

    def _on_stream_resync(self, generation, reason, seed: TimedPCM | None = None):
        """Re-anchor the official Xiaomi stream, not the sender connection.

        Protect against obsolete HTTP handlers and rate-limit pathological
        jitter. A persistent byte/PTS mismatch retries after the cooldown.
        """
        with self._state_lock:
            if (not self._running or not self._is_playing or not self._play_allowed or
                    generation != self._stream_server._generation):
                return False
            now = time.monotonic()
            if now - self._last_clock_resync_at < 3:
                # A disconnected response cannot request again on its next PCM
                # block. Defer its recovery instead of losing it to cooldown.
                if reason.startswith("http-") and self._clock_resync_timer is None:
                    self._clock_resync_timer = threading.Timer(
                        3 - (now - self._last_clock_resync_at) + 0.01,
                        self._retry_stream_resync, args=(generation, reason))
                    self._clock_resync_timer.daemon = True
                    self._clock_resync_timer.start()
                return False
            if self._clock_resync_timer is not None:
                self._clock_resync_timer.cancel()
                self._clock_resync_timer = None
            self._last_clock_resync_at = now
            self._timeline_generation += 1
            log.warning("AirPlay 2 自动重新对齐官方播放流: reason=%s old_generation=%s; 保留发送端连接及延迟校准值",
                        reason, generation)
            self._end_session()
            self._begin_session()
            if seed is not None:
                self._stream_server.write_timed_pcm(seed.data, seed.playtime_ns)
            return True

    def _retry_stream_resync(self, generation, reason):
        with self._state_lock:
            self._clock_resync_timer = None
            self._on_stream_resync(generation, reason)

    def _read_audio(self):
        process = self._shairport_process
        if not process or not process.stdout:
            return
        try:
            if self._timed_stdout:
                self._read_timed_audio(process)
                return
            pending = b""
            pending_stale = False
            while self._running and process.poll() is None:
                with self._state_lock:
                    generation = self._timeline_generation
                chunk = process.stdout.read(_READ_SIZE)
                if not chunk or not self._running:
                    break
                # FileIO reads may split a stereo frame. Never send a partial
                # frame to the WAV bridge, or left/right samples become shifted.
                with self._state_lock:
                    pending += chunk
                    aligned_size = len(pending) - len(pending) % _FRAME_SIZE
                    if generation != self._timeline_generation or not self._play_allowed:
                        # Drain/discard stdout while paused. Never append a chunk
                        # that straddles a control boundary to the new session.
                        # Keep only the incomplete frame so the continuous
                        # stdout byte stream remains stereo-frame aligned.
                        pending = pending[aligned_size:]
                        pending_stale = bool(pending)
                        continue
                    if not aligned_size:
                        continue
                    data = pending[:aligned_size]
                    pending = pending[aligned_size:]
                    if pending_stale:
                        data = data[_FRAME_SIZE:]
                        pending_stale = False
                    if not data:
                        continue
                    self._last_audio_at = time.monotonic()
                    self._begin_session()
                    self._stream_server.write_pcm(data)
        except Exception as exc:
            if self._running:
                self.last_error = f"AirPlay 2 PCM 读取失败: {exc}"
                log.error(self.last_error)
        finally:
            # EOF is permanent: a live child with a closed stdout pipe cannot
            # deliver more audio. Let the monitor release both native processes.
            if self._running and process.poll() is None and not self.last_error:
                self.last_error = "AirPlay 2 PCM stream closed unexpectedly"
                log.error(self.last_error)

    def _read_timed_audio(self, process):
        epoch = None
        expected_pts = None
        pts_generation = None
        while self._running and process.poll() is None:
            with self._state_lock:
                generation = self._timeline_generation
            record = read_record(process.stdout)
            if record is None or not self._running:
                break
            kind, rtp, playtime, new_epoch, data = record
            with self._state_lock:
                # Boundaries are also carried in-band, so old stdout bytes
                # cannot be mistaken for a resumed session after a seek.
                if new_epoch != epoch:
                    if epoch is not None:
                        self._timeline_generation += 1
                        self._end_session()
                    epoch = new_epoch
                if kind != 1 or not playtime or not data:
                    continue  # no synthetic untimed lead-in on the WAV stream
                if generation != self._timeline_generation or not self._play_allowed:
                    continue
                if pts_generation != self._timeline_generation:
                    expected_pts = None
                if expected_pts is not None and abs(playtime - expected_pts) > 80_000_000:
                    self._on_stream_resync(self._stream_server._generation,
                                          f"pcm-pts-shift:{(playtime - expected_pts) / 1_000_000:.1f}ms")
                expected_pts = playtime + (len(data) // _FRAME_SIZE) * 1_000_000_000 // _SAMPLE_RATE
                pts_generation = self._timeline_generation
                self._last_audio_at = time.monotonic()
                self._begin_session()
                self._stream_server.write_timed_pcm(data, playtime)

    @staticmethod
    def _start_stderr_reader(process, name: str, messages: deque[str], on_line=None) -> threading.Thread:
        def read():
            if not process.stderr:
                return
            try:
                for raw in iter(process.stderr.readline, b""):
                    line = raw.decode("utf-8", errors="replace").strip()
                    if line:
                        messages.append(line)
                        if on_line:
                            on_line(line)
                        log.debug("%s: %s", name, line)
            except (OSError, ValueError):
                pass

        thread = threading.Thread(target=read, name=f"miair-{name}-stderr", daemon=True)
        thread.start()
        return thread

    async def _monitor(self):
        try:
            while self._running:
                await asyncio.sleep(0.5)
                process = self._shairport_process
                clock = self._nqptp_process
                if process and process.poll() is not None:
                    detail = self._process_error(process, self._shairport_messages)
                    self.last_error = f"shairport-sync 异常退出: {detail}"
                elif clock and clock.poll() is not None:
                    detail = self._process_error(clock, self._nqptp_messages)
                    self.last_error = f"NQPTP 异常退出: {detail}"
                if self.last_error:
                    log.error(self.last_error)
                    await self.stop()
                    return
                if (
                    self._is_playing
                    and self._last_audio_at
                    and time.monotonic() - self._last_audio_at > _SESSION_IDLE_SECONDS
                ):
                    self._end_session()
        except asyncio.CancelledError:
            pass

    async def stop(self):
        async with self._stop_lock:
            await self._stop_locked()

    async def _stop_locked(self):
        self._running = False
        with self._state_lock:
            if self._clock_resync_timer is not None:
                self._clock_resync_timer.cancel()
                self._clock_resync_timer = None
        if self._monitor_task:
            if self._monitor_task is not asyncio.current_task():
                self._monitor_task.cancel()
                try:
                    await self._monitor_task
                except asyncio.CancelledError:
                    pass
            self._monitor_task = None

        self._remember_nqptp_clock()
        await self._terminate(self._shairport_process)
        await self._terminate(self._nqptp_process)
        self._metadata_stop.set()
        if self._metadata_thread:
            await asyncio.to_thread(self._metadata_thread.join)
            self._metadata_thread = None
        if self._metadata_fd is not None:
            os.close(self._metadata_fd)
            self._metadata_fd = None
        if self._metadata_dir:
            self._metadata_dir.cleanup()
            self._metadata_dir = None
        self._metadata.reset()
        # SIGKILL cannot run NQPTP's shm_unlink handler. A stale owned clock
        # would otherwise make every subsequent settings restart fall back.
        self._remove_owned_nqptp_clock()
        for thread in (self._reader_thread, self._stderr_thread, self._nqptp_stderr_thread):
            if thread:
                await asyncio.to_thread(thread.join, 1)
        self._reader_thread = self._stderr_thread = self._nqptp_stderr_thread = None
        self._end_session()
        for process in (self._shairport_process, self._nqptp_process):
            if process:
                for pipe in (process.stdout, process.stderr):
                    if pipe:
                        pipe.close()
        self._shairport_process = None
        self._nqptp_process = None

        await self._stream_server.stop()
        if self._config_path:
            try:
                os.unlink(self._config_path)
            except OSError:
                pass
            self._config_path = ""

    @staticmethod
    async def _terminate(process: subprocess.Popen | None):
        if not process or process.poll() is not None:
            return
        process.terminate()
        try:
            await asyncio.to_thread(process.wait, 3)
        except subprocess.TimeoutExpired:
            process.kill()
            await asyncio.to_thread(process.wait)
