"""DLNA HTTP 服务 - 处理设备描述、SOAP 控制和事件订阅"""

import asyncio
import logging
import os
import re
import secrets
import shutil
import tempfile
import time
from typing import TYPE_CHECKING

import aiohttp
from aiohttp import web

from app.engine.const import (
    TRANSPORT_STATE_PAUSED,
    TRANSPORT_STATE_PLAYING,
    TRANSPORT_STATE_STOPPED,
    TRANSPORT_STATE_TRANSITIONING,
    UPNP_ERROR_ACTION_FAILED,
)
from app.engine.dlna.eventing import EventManager, build_last_change_event, clamp_timeout
from app.engine.dlna.duration import probe_duration as probe_audio_duration
from app.engine.dlna.media_buffer import MediaBuffer, _MAX_DOWNLOAD_SIZE
from app.engine.dlna.cover import (
    default_cover_asset_path,
)
from app.engine.dlna.renderer import DLNARenderer
from app.engine.dlna.soap_handler import (
    handle_soap_request,
    parse_soap_action,
    parse_soap_body,
)
from app.engine.dlna.templates import (
    AVTRANSPORT_SCPD,
    CONNECTION_MANAGER_SCPD,
    RENDERING_CONTROL_SCPD,
    device_description_xml,
    soap_fault,
)
from app.engine.net_guard import SafeResolver

log = logging.getLogger("miair")

if TYPE_CHECKING:
    # 仅供类型标注使用 (运行时不导入, 避免 engine.config <-> dlna 循环依赖)
    from app.engine.config import Config

# 最多同时保留的音频缓冲数（含 seek 缓冲）
_MAX_BUFFERS = 10

# 时长嗅探: 等待缓冲就绪的最少数据量 (容器头部探测足够)
_PROBE_MIN_BYTES = 64 * 1024

# 音箱状态轮询周期 (秒)。提为模块常量以便回归测试压缩等待时间。
_POLL_INTERVAL_SECONDS = 5.0

# 缓冲清理周期 (秒)
_BUFFER_CLEANUP_INTERVAL_SECONDS = 60.0


class DeviceServer:
    """DLNA HTTP 服务器"""

    def __init__(self, hostname: str, dlna_port: int, config: "Config | None" = None):
        self.hostname = hostname
        self.dlna_port = dlna_port
        self.config = config
        self.renderers: dict[str, DLNARenderer] = {}  # udn -> DLNARenderer
        self.event_managers: dict[str, EventManager] = {}  # udn -> EventManager
        self.app = web.Application()
        self._runner = None
        self._poll_task: asyncio.Task | None = None
        # 音频缓冲系统
        # 并发模型: 这三个字典由事件循环单线程访问, 读写在无 await 的同步段内
        # 完成即可保证原子性 (无需额外锁)。迭代它们时必须先取快照, 否则
        # await 期间被回收/注销会导致 RuntimeError。
        self._media_buffers: dict[str, MediaBuffer] = {}  # buffer_id -> MediaBuffer
        self._proxy_tokens: dict[str, tuple[str, int, str]] = {}  # token -> (buffer_id, start_byte, udn)
        self._url_to_buffer: dict[str, str] = {}  # remote_url -> buffer_id (最新的)
        self._proxy_session: aiohttp.ClientSession | None = None
        self._ffmpeg_path: str | None = None
        self._ffmpeg_checked: bool = False
        # seek 缓冲的创建时间戳，用于 TTL 清理
        self._buffer_created_time: dict[str, float] = {}  # buffer_id -> time.time()
        # 缓冲最后被代理访问的时间，保护正在被流式传输的缓冲不被清理
        self._buffer_last_accessed: dict[str, float] = {}  # buffer_id -> time.time()
        # 实验性功能：打断后续播
        self._resume_tasks: dict[str, asyncio.Task] = {}  # udn -> resume task
        # 追踪活跃的代理任务，用于强制中止
        self._active_proxy_tasks: dict[str, set[asyncio.Task]] = {} # udn -> set of tasks
        self._active_proxy_buffers: dict[asyncio.Task, str] = {}
        # 暂停中屏蔽集合：暂停时加入，播放/停止时移除，阻止音箱重连拉取音频
        self._paused_proxy_udns: set[str] = set()
        # 周期性缓冲清理任务
        self._buffer_cleanup_task: asyncio.Task | None = None
        # spool 大文件写入配置数据目录，避免 OpenWrt 等系统的 /tmp tmpfs
        # 让“落盘”缓冲仍消耗大量 RAM。总预算上限 200MB，并尽量保留 32MB 空间。
        self._buffer_temp_dir = getattr(config, "conf_path", None)
        self._max_buffer_memory = 200 * 1024 * 1024  # 名称保留兼容旧调用方
        if self._buffer_temp_dir:
            try:
                os.makedirs(self._buffer_temp_dir, exist_ok=True)
                free_bytes = shutil.disk_usage(self._buffer_temp_dir).free
                self._max_buffer_memory = min(
                    self._max_buffer_memory, max(0, free_bytes - 32 * 1024 * 1024)
                )
            except OSError as e:
                log.warning(f"无法读取缓冲目录剩余空间，使用默认缓冲预算: {e}")
        # fire-and-forget 任务集合
        self._background_tasks: set[asyncio.Task] = set()
        self._setup_routes()

    def _setup_routes(self):
        """配置路由"""
        self.app.router.add_get(
            "/device/{udn}/description.xml", self._handle_description
        )
        self.app.router.add_get(
            "/device/{udn}/AVTransport.xml", self._handle_avtransport_scpd
        )
        self.app.router.add_get(
            "/device/{udn}/RenderingControl.xml", self._handle_rendering_control_scpd
        )
        self.app.router.add_get(
            "/device/{udn}/ConnectionManager.xml", self._handle_connection_manager_scpd
        )
        self.app.router.add_post(
            "/device/{udn}/AVTransport/control", self._handle_control
        )
        self.app.router.add_post(
            "/device/{udn}/RenderingControl/control", self._handle_control
        )
        self.app.router.add_post(
            "/device/{udn}/ConnectionManager/control", self._handle_control
        )
        # 事件订阅 - 使用通配路由处理 SUBSCRIBE/UNSUBSCRIBE
        self.app.router.add_route(
            "SUBSCRIBE", "/device/{udn}/{service}/event", self._handle_subscribe
        )
        self.app.router.add_route(
            "UNSUBSCRIBE", "/device/{udn}/{service}/event", self._handle_unsubscribe
        )
        # 媒体代理 (音箱从此拉取音频流)
        self.app.router.add_get("/media/{token}", self._handle_media_proxy)
        # 通用默认封面 (无封面投送时，DLNA/AirPlay 触屏兜底显示)
        self.app.router.add_get("/default-cover", self._handle_default_cover)

    def _get_proxy_session(self) -> aiohttp.ClientSession:
        """获取/创建用于下载的持久 session，带连接池限制和超时"""
        if not self._proxy_session or self._proxy_session.closed:
            connector = aiohttp.TCPConnector(
                limit=50,  # 连接池上限（默认100太大，10太小会卡住多线程下载）
                ttl_dns_cache=300,  # DNS 缓存 5 分钟
                resolver=SafeResolver(),
            )
            self._proxy_session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=120, connect=10, sock_read=60)
            )
        return self._proxy_session

    def register_renderer(self, renderer: DLNARenderer):
        """注册渲染器"""
        self.renderers[renderer.udn] = renderer
        em = EventManager()
        self.event_managers[renderer.udn] = em
        renderer.event_manager = em
        renderer.proxy_url_func = self.create_proxy_url
        renderer.seek_url_func = self.create_seek_url
        renderer.probe_duration_func = self.probe_duration
        renderer.pre_buffer_func = self.start_buffering
        renderer.abort_proxy_func = self.abort_proxy_for_renderer
        renderer.resume_proxy_func = self.resume_proxy_for_renderer

    async def unregister_renderer(self, udn: str):
        """注销渲染器并停止事件管理器"""
        self.renderers.pop(udn, None)
        em = self.event_managers.pop(udn, None)
        if em is not None:
            try:
                await em.stop()
            except Exception as e:  # noqa: BLE001
                log.warning(f"停止 DLNA EventManager 失败: {e}")

    # ---- 音频缓冲/代理系统 ----

    def _new_media_buffer(self, remote_url: str) -> MediaBuffer:
        """在数据目录创建有界 spool 缓冲，避免临时文件写入 RAM 文件系统。"""
        return MediaBuffer(remote_url, temp_dir=self._buffer_temp_dir)

    def start_buffering(self, remote_url: str):
        """预缓冲：在 SetAVTransportURI 时提前开始下载"""
        if remote_url in self._url_to_buffer:
            buf = self._media_buffers.get(self._url_to_buffer[remote_url])
            if buf and not buf.error:
                return  # 已在缓冲
        if not self._cleanup_old_buffers():
            log.warning(f"缓冲资源不足，跳过预缓冲: {remote_url[:80]}...")
            return
        buffer_id = secrets.token_urlsafe(16)
        buf = self._new_media_buffer(remote_url)
        self._media_buffers[buffer_id] = buf
        self._url_to_buffer[remote_url] = buffer_id
        self._buffer_created_time[buffer_id] = time.time()
        asyncio.get_running_loop().create_task(buf.start_download(self._get_proxy_session()))
        log.info(f"预缓冲已启动: {remote_url[:80]}...")

    def create_proxy_url(self, remote_url: str, udn: str = "") -> str:
        """为远端 URL 创建本地代理 URL（复用已有缓冲或新建）"""
        buffer_id = self._url_to_buffer.get(remote_url)
        if buffer_id and buffer_id in self._media_buffers:
            buf = self._media_buffers[buffer_id]
            if not buf.error:
                token = secrets.token_urlsafe(16)
                self._proxy_tokens[token] = (buffer_id, 0, udn)
                url = f"http://{self.hostname}:{self.dlna_port}/media/{token}"
                return url

        # 创建新缓冲
        if not self._cleanup_old_buffers():
            # 代理容量不足时保留现有播放，音箱尝试直接读取原始 URL。
            log.warning(f"缓冲资源不足，回退到原始媒体 URL: {remote_url[:80]}...")
            return remote_url
        buffer_id = secrets.token_urlsafe(16)
        buf = self._new_media_buffer(remote_url)
        self._media_buffers[buffer_id] = buf
        self._url_to_buffer[remote_url] = buffer_id
        asyncio.get_running_loop().create_task(buf.start_download(self._get_proxy_session()))

        token = secrets.token_urlsafe(16)
        self._proxy_tokens[token] = (buffer_id, 0, udn)
        url = f"http://{self.hostname}:{self.dlna_port}/media/{token}"
        return url

    async def probe_duration(self, remote_url: str, timeout: float = 20.0) -> float:
        """从远端 URL 对应的缓冲数据嗅探音频时长（秒），无缓冲或失败返回 0

        元数据缺失 duration 时的兜底：等待预缓冲头部就绪后读取连续前缀，
        探测无需将完整音频载入内存。
        """
        buffer_id = self._url_to_buffer.get(remote_url)
        buf = self._media_buffers.get(buffer_id) if buffer_id else None
        if buf is None or buf.error:
            return 0.0
        try:
            await asyncio.wait_for(buf._headers_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        deadline = time.monotonic() + timeout
        while not buf.download_complete and not buf.error:
            if buf.contiguous_size >= _PROBE_MIN_BYTES or time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.25)
        available = buf.downloaded_size if buf.download_complete else buf.contiguous_size
        if buf.error or available == 0:
            return 0.0
        probe_data = await asyncio.to_thread(
            buf.read_range, 0, min(available, 2 * 1024 * 1024)
        )
        return await probe_audio_duration(probe_data)

    async def create_seek_url(
        self, original_url: str, seek_seconds: float, duration: float, udn: str = ""
    ) -> str | None:
        """为 Seek 创建格式正确的音频代理 URL（支持 FLAC/MP3 等）"""
        buffer_id = self._url_to_buffer.get(original_url)
        buf = None
        
        # 如果找不到缓冲，尝试创建新缓冲
        if not buffer_id:
            log.info("Seek: 未找到缓冲，尝试创建新缓冲...")
            if not self._cleanup_old_buffers():
                log.warning("缓冲资源不足，跳过 Seek 缓冲创建")
                return None
            buffer_id = secrets.token_urlsafe(16)
            buf = self._new_media_buffer(original_url)
            self._media_buffers[buffer_id] = buf
            self._url_to_buffer[original_url] = buffer_id
            # 启动下载
            asyncio.get_running_loop().create_task(buf.start_download(self._get_proxy_session()))
            log.info("Seek: 已启动新缓冲下载")
        else:
            buf = self._media_buffers.get(buffer_id)
        
        if not buf or buf.error:
            log.warning("Seek: 缓冲无效或出错")
            return None
        if buf.total_size <= 0:
            log.warning("Seek: 缓冲大小为0")
            return None
        if seek_seconds < 0:
            log.warning(f"Seek: 无效的时间参数 {seek_seconds}/{duration}")
            return None

        # 等待缓冲完成（最多等待15秒）
        if not buf.download_complete:
            log.info("Seek: 等待缓冲完成...")
            try:
                await asyncio.wait_for(buf._complete_event.wait(), timeout=15.0)
            except asyncio.TimeoutError:
                log.warning("Seek: 缓冲等待超时，尝试使用当前可用数据")
                # 如果缓冲未完成但有足够数据（至少2MB），继续处理
                if buf.contiguous_size < 2 * 1024 * 1024:
                    log.warning(f"Seek: 缓冲数据不足 ({buf.contiguous_size} bytes)")
                    return None

        # Seek / 格式回退算法需要随机访问源音频。只在触发 Seek 时将其短暂
        # 读入内存，普通播放与代理 Range 响应始终按块从 spool 读取。
        available = buf.downloaded_size if buf.download_complete else buf.contiguous_size
        data = await asyncio.to_thread(buf.read_range, 0, available)
        if not data:
            log.warning("Seek: 缓冲数据为空")
            return None

        # 元数据缺失 duration 时从缓冲数据嗅探兜底, 避免静默回退软 Seek
        if duration <= 0:
            duration = await probe_audio_duration(data)
            if duration <= 0:
                log.warning("Seek: 无法确定音轨时长 (元数据缺失且嗅探失败)")
                return None
            log.info(f"Seek: 元数据缺失时长, 已从缓冲嗅探: {duration:.1f}s")

        if seek_seconds >= duration:
            log.warning(f"Seek: 时间超出范围 {seek_seconds}/{duration}")
            return None

        seek_ratio = seek_seconds / duration
        fmt = self._detect_audio_format(data)
        log.info(
            f"Seek 开始: {seek_seconds:.1f}/{duration:.1f}s, "
            f"格式={fmt}, content_type={buf.content_type}, "
            f"大小={buf.total_size}, 缓冲完成={buf.download_complete}"
        )

        # 1) 尝试 ffmpeg（最可靠）
        seeked_data = await self._ffmpeg_seek(
            data, seek_seconds, buf.content_type
        )

        # 2) 回退: 格式感知的纯 Python seek
        if seeked_data is None:
            seeked_data = self._format_seek(data, seek_ratio, buf.content_type)

        if seeked_data is None or len(seeked_data) == 0:
            log.warning(f"Seek: 无法生成有效的 seeked 音频数据 (格式={fmt})")
            return None

        # 将 seeked 数据存为新缓冲
        seek_buf = self._new_media_buffer(original_url)
        seek_buf.data = seeked_data
        seek_buf.total_size = len(seeked_data)
        seek_buf.content_type = buf.content_type
        seek_buf.download_complete = True
        seek_buf._complete_event.set()

        if not self._cleanup_old_buffers():
            log.warning("缓冲资源不足，无法保留 Seek 结果")
            return None
        seek_bid = secrets.token_urlsafe(16)
        self._media_buffers[seek_bid] = seek_buf
        self._buffer_created_time[seek_bid] = time.time()

        token = secrets.token_urlsafe(16)
        self._proxy_tokens[token] = (seek_bid, 0, udn)
        url = f"http://{self.hostname}:{self.dlna_port}/media/{token}"
        log.info(
            f"Seek 音频就绪: {len(seeked_data)} bytes "
            f"(原 {buf.total_size}, seek {seek_seconds:.1f}/{duration:.1f}s)"
        )
        return url

    # ---- 音频格式检测 ----

    @staticmethod
    def _detect_audio_format(data: bytes | bytearray) -> str:
        """通过文件魔数 (magic bytes) 检测音频格式"""
        if len(data) < 12:
            return "unknown"
        # FLAC: 'fLaC'
        if data[:4] == b"fLaC":
            return "flac"
        # RIFF/WAV: 'RIFF' + size + 'WAVE'
        if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
            return "wav"
        # OGG: 'OggS'
        if data[:4] == b"OggS":
            return "ogg"
        # ID3 tag (MP3 with ID3v2 header)
        if data[:3] == b"ID3":
            return "mp3"
        # M4A/MP4: 'ftyp' box at offset 4
        if data[4:8] == b"ftyp":
            return "m4a"
        # AAC ADTS: 0xFFF with specific bits (layer=00)
        if len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xF6) == 0xF0:
            return "aac"
        # MP3 sync frame: 0xFF + 0xE0 mask (but not AAC)
        if len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:
            return "mp3"
        # WMA/ASF header GUID
        if data[:4] == b"\x30\x26\xb2\x75":
            return "wma"
        return "unknown"

    # ---- ffmpeg seek ----

    def _check_ffmpeg(self) -> str | None:
        """检测 ffmpeg（优先项目目录，其次系统 PATH）"""
        if self._ffmpeg_checked:
            return self._ffmpeg_path
        self._ffmpeg_checked = True
        # 1) 项目目录下的 ffmpeg
        project_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        for name in ("ffmpeg.exe", "ffmpeg"):
            local = os.path.join(project_dir, name)
            if os.path.isfile(local):
                self._ffmpeg_path = local
                log.info(f"ffmpeg 可用 (项目目录): {self._ffmpeg_path}")
                return self._ffmpeg_path
        # 2) 系统 PATH
        self._ffmpeg_path = shutil.which("ffmpeg")
        if self._ffmpeg_path:
            log.info(f"ffmpeg 可用 (系统): {self._ffmpeg_path}")
        else:
            log.info("ffmpeg 未找到，Seek 将使用内置格式处理")
        return self._ffmpeg_path

    async def _ffmpeg_seek(
        self, data: bytearray, seek_seconds: float, content_type: str
    ) -> bytearray | None:
        """用 ffmpeg 生成从 seek 位置开始的有效音频流"""
        ffmpeg = self._check_ffmpeg()
        if not ffmpeg:
            return None

        # 用魔数检测实际格式，不依赖 content_type
        fmt = self._detect_audio_format(data)
        fmt_map = {
            "flac": ("flac", ".flac"),
            "mp3": ("mp3", ".mp3"),
            "ogg": ("ogg", ".ogg"),
            "aac": ("adts", ".aac"),
            "wav": ("wav", ".wav"),
            "m4a": ("ipod", ".m4a"),
            "wma": ("asf", ".wma"),
        }
        out_fmt, suffix = fmt_map.get(fmt, ("mp3", ".mp3"))

        in_fd, in_path = tempfile.mkstemp(suffix=suffix, prefix="miair_si_")
        out_fd, out_path = tempfile.mkstemp(suffix=suffix, prefix="miair_so_")
        proc = None
        try:
            with os.fdopen(in_fd, "wb") as f:
                f.write(data)
            os.close(out_fd)

            proc = await asyncio.create_subprocess_exec(
                ffmpeg, "-y",
                "-ss", str(seek_seconds),
                "-i", in_path,
                "-c", "copy",
                "-f", out_fmt,
                out_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

            if proc.returncode != 0:
                return None

            with open(out_path, "rb") as f:
                result = bytearray(f.read())
            return result if len(result) > 0 else None

        except asyncio.TimeoutError:
            log.warning("ffmpeg seek 超时")
            if proc:
                try:
                    proc.kill()
                except Exception:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except (asyncio.TimeoutError, Exception):
                    log.warning("ffmpeg seek 进程杀死后等待超时")
            return None
        except Exception:
            return None
        finally:
            # 确保进程已终止
            if proc and proc.returncode is None:
                try:
                    proc.kill()
                    await asyncio.wait_for(proc.wait(), timeout=3)
                except Exception:
                    pass
            for p in (in_path, out_path):
                try:
                    if os.path.exists(p):
                        os.unlink(p)
                except OSError:
                    pass

    # ---- 纯 Python 格式感知 seek ----

    @staticmethod
    def _format_seek(
        data: bytearray, seek_ratio: float, content_type: str
    ) -> bytearray | None:
        """不依赖 ffmpeg 的格式感知 seek（通过魔数检测格式）"""
        fmt = DeviceServer._detect_audio_format(data)
        if fmt == "flac":
            return DeviceServer._seek_flac(data, seek_ratio)
        if fmt == "mp3":
            return DeviceServer._seek_mp3(data, seek_ratio)
        if fmt == "wav":
            return DeviceServer._seek_wav(data, seek_ratio)
        if fmt == "aac":
            return DeviceServer._seek_aac(data, seek_ratio)
        # ogg, m4a, wma 等复杂容器格式需要 ffmpeg
        return None

    @staticmethod
    def _seek_flac(data: bytearray, seek_ratio: float) -> bytearray | None:
        """FLAC seek: 提取元数据头 + 从目标位置的帧边界开始的音频数据"""
        if len(data) < 42 or data[:4] != b"fLaC":
            return None

        # 解析元数据块，找到音频数据的起始位置
        pos = 4
        while pos + 4 <= len(data):
            header_byte = data[pos]
            is_last = (header_byte & 0x80) != 0
            block_len = (data[pos + 1] << 16) | (data[pos + 2] << 8) | data[pos + 3]
            pos += 4 + block_len
            if is_last:
                break
        metadata_end = pos

        if metadata_end >= len(data):
            return None

        # 复制元数据并修补 STREAMINFO
        metadata = bytearray(data[:metadata_end])
        # STREAMINFO 数据从 file byte 8 开始（4 fLaC + 4 block header）
        # 将 total_samples 设为 0（"未知"），清除 MD5
        si = 8  # STREAMINFO data offset in file
        if len(metadata) >= si + 34:
            # total_samples 高 4 位在 byte si+13 的低 4 位
            metadata[si + 13] &= 0xF0
            # total_samples 低 32 位在 bytes si+14 ~ si+17
            metadata[si + 14] = metadata[si + 15] = 0
            metadata[si + 16] = metadata[si + 17] = 0
            # 清除 MD5 签名 (bytes si+18 ~ si+33)
            for i in range(si + 18, si + 34):
                metadata[i] = 0

        # 在音频数据区定位目标帧
        audio_start = metadata_end
        audio_len = len(data) - audio_start
        target = int(seek_ratio * audio_len)

        # 从 target 附近搜索 FLAC 帧同步码 (0xFF 0xF8 或 0xFF 0xF9)
        search_lo = max(0, target - 65536)
        search_hi = min(audio_len - 1, target + 65536)
        frame_pos = None
        for i in range(search_lo, search_hi):
            if data[audio_start + i] == 0xFF and (data[audio_start + i + 1] & 0xFE) == 0xF8:
                frame_pos = i
                if i >= target:
                    break  # 优先取目标之后的第一个帧

        if frame_pos is None:
            log.warning("FLAC seek: 未找到帧同步码")
            return None

        result = metadata + data[audio_start + frame_pos :]
        return result

    @staticmethod
    def _seek_mp3(data: bytearray, seek_ratio: float) -> bytearray | None:
        """MP3 seek: 跳过 ID3 tag 后从目标字节位置找到最近的同步帧"""
        audio_start = 0
        # 跳过 ID3v2 头
        if len(data) >= 10 and data[:3] == b"ID3":
            tag_size = (
                (data[6] & 0x7F) << 21
                | (data[7] & 0x7F) << 14
                | (data[8] & 0x7F) << 7
                | (data[9] & 0x7F)
            )
            audio_start = 10 + tag_size

        audio_len = len(data) - audio_start
        if audio_len <= 0:
            return None
        target = audio_start + int(seek_ratio * audio_len)
        # 从 target 向后扫描 MP3 帧同步 (0xFF + 0xE0 mask)
        for i in range(max(audio_start, target), min(len(data) - 1, target + 8192)):
            if data[i] == 0xFF and (data[i + 1] & 0xE0) == 0xE0:
                return bytearray(data[i:])
        return None

    @staticmethod
    def _seek_wav(data: bytearray, seek_ratio: float) -> bytearray | None:
        """WAV seek: 保留文件头，从 data chunk 中按块对齐偏移"""
        if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
            return None

        # 找到 'data' chunk
        pos = 12
        data_offset = -1
        data_size = 0
        while pos + 8 <= len(data):
            chunk_id = data[pos : pos + 4]
            chunk_size = int.from_bytes(data[pos + 4 : pos + 8], "little")
            if chunk_id == b"data":
                data_offset = pos + 8
                data_size = chunk_size
                break
            pos += 8 + chunk_size
            if chunk_size % 2:
                pos += 1  # 对齐

        if data_offset < 0:
            return None

        # 获取块对齐信息（从 fmt chunk）
        block_align = 4  # 默认
        fmt_pos = 12
        while fmt_pos + 8 <= len(data):
            cid = data[fmt_pos : fmt_pos + 4]
            csz = int.from_bytes(data[fmt_pos + 4 : fmt_pos + 8], "little")
            if cid == b"fmt ":
                if csz >= 16:
                    block_align = int.from_bytes(
                        data[fmt_pos + 8 + 12 : fmt_pos + 8 + 14], "little"
                    )
                break
            fmt_pos += 8 + csz
            if csz % 2:
                fmt_pos += 1

        if block_align <= 0:
            block_align = 4

        # 计算 seek 偏移（块对齐）
        target_in_data = int(seek_ratio * data_size)
        target_in_data = (target_in_data // block_align) * block_align

        new_data_size = data_size - target_in_data
        if new_data_size <= 0:
            return None

        # 构造新 WAV: 原始头（到 data chunk 头）+ 修改后的 data
        header = bytearray(data[:data_offset])
        # 更新 RIFF size
        new_riff_size = len(header) - 8 + new_data_size
        header[4:8] = new_riff_size.to_bytes(4, "little")
        # 更新 data chunk size
        header[-4:] = new_data_size.to_bytes(4, "little")

        result = header + data[data_offset + target_in_data : data_offset + data_size]
        return result

    @staticmethod
    def _seek_aac(data: bytearray, seek_ratio: float) -> bytearray | None:
        """AAC ADTS seek: 从目标位置找到最近的 ADTS 帧同步"""
        target = int(seek_ratio * len(data))
        for i in range(max(0, target), min(len(data) - 1, target + 8192)):
            # ADTS sync: 0xFFF with layer=00
            if data[i] == 0xFF and (data[i + 1] & 0xF6) == 0xF0:
                return bytearray(data[i:])
        return None

    def _cleanup_old_buffers(self) -> bool:
        """先淘汰闲置缓冲，再判断新下载是否有预留空间。"""
        while len(self._media_buffers) >= _MAX_BUFFERS:
            oldest_id = next(
                (bid for bid in self._media_buffers if not self._buffer_is_in_use(bid)),
                None,
            )
            if oldest_id is None:
                log.warning("缓冲数量达到上限，所有缓冲都正在使用；暂时保留活跃缓冲")
                break
            self._remove_buffer(oldest_id)
        # 按缓冲存储上限清理
        self._cleanup_by_storage()
        if len(self._media_buffers) >= _MAX_BUFFERS:
            return False
        # 未知文件大小按单文件上限预留，避免多个并发下载突破总缓冲预算。
        return self._reserved_buffer_memory() + _MAX_DOWNLOAD_SIZE <= self._max_buffer_memory

    def _reserved_buffer_memory(self) -> int:
        """已完成缓冲按实际存储量计，进行中的下载按单文件上限预留。"""
        return sum(
            buf.downloaded_size
            if buf.download_complete or buf.error
            else max(buf.downloaded_size, _MAX_DOWNLOAD_SIZE)
            for buf in self._media_buffers.values()
        )

    def _buffer_is_in_use(self, buffer_id: str) -> bool:
        """当前播放/下一首或 HTTP 代理请求持有的缓冲不能被淘汰。"""
        if buffer_id in self._active_proxy_buffers.values():
            return True
        buf = self._media_buffers.get(buffer_id)
        if buf is None:
            return False
        source_url = buf.remote_url
        if self._url_to_buffer.get(source_url) != buffer_id:
            return False
        return any(
            renderer.current_uri == source_url or renderer.next_uri == source_url
            for renderer in self.renderers.values()
        )

    def _cleanup_by_storage(
        self, required_extra: int = 0, protected_id: str | None = None
    ) -> bool:
        """清理闲置缓冲，确保当前存储量加预留空间不超过上限。"""
        total_storage = sum(buf.downloaded_size for buf in self._media_buffers.values())
        while total_storage + required_extra > self._max_buffer_memory and self._media_buffers:
            oldest_id = next(
                (
                    bid for bid in self._media_buffers
                    if bid != protected_id and not self._buffer_is_in_use(bid)
                ),
                None,
            )
            if oldest_id is None:
                log.warning(
                    "缓冲存储达到上限，活跃缓冲无法回收；拒绝扩容以避免资源耗尽"
                )
                break
            buf = self._media_buffers.get(oldest_id)
            freed = buf.downloaded_size if buf else 0
            self._remove_buffer(oldest_id)
            total_storage -= freed
        return total_storage + required_extra <= self._max_buffer_memory

    def _cleanup_by_memory(
        self, required_extra: int = 0, protected_id: str | None = None
    ):
        """兼容旧私有调用名；缓冲现在按磁盘存储预算回收。"""
        return self._cleanup_by_storage(required_extra, protected_id)

    def _remove_buffer(self, buffer_id: str):
        """移除一个缓冲及其关联的 token 和时间戳"""
        buf = self._media_buffers.pop(buffer_id, None)
        if buf:
            buf.cleanup()
        self._buffer_created_time.pop(buffer_id, None)
        self._buffer_last_accessed.pop(buffer_id, None)
        tokens_to_remove = [
            t for t, (bid, _, _) in self._proxy_tokens.items() if bid == buffer_id
        ]
        for t in tokens_to_remove:
            del self._proxy_tokens[t]
        urls_to_remove = [
            u for u, bid in self._url_to_buffer.items() if bid == buffer_id
        ]
        for u in urls_to_remove:
            del self._url_to_buffer[u]

    def _cleanup_all_buffers(self):
        """清理全部缓冲"""
        for buf in self._media_buffers.values():
            buf.cleanup()
        self._media_buffers.clear()
        self._proxy_tokens.clear()
        self._url_to_buffer.clear()
        self._buffer_created_time.clear()
        self._buffer_last_accessed.clear()

    async def _periodic_buffer_cleanup(self):
        """周期性清理已完成且长时间未访问的缓冲

        并发安全: 清理期间有 await (虽然主要在事后), 且 renderers /
        _media_buffers 会被 HTTP 请求与 unregister_renderer 并发修改,
        故迭代前统一取快照, 避免字典变动中断清理任务。
        """
        try:
            while True:
                await asyncio.sleep(_BUFFER_CLEANUP_INTERVAL_SECONDS)
                now = time.time()

                # 收集当前正在播放的 URL 集合（包括 next_uri）
                active_urls = set()
                for renderer in list(self.renderers.values()):
                    if renderer.current_uri:
                        active_urls.add(renderer.current_uri)
                    if renderer.next_uri:
                        active_urls.add(renderer.next_uri)

                # 清理过时缓冲（创建超过 120 秒且已完成下载的）
                to_remove = []
                for bid in list(self._media_buffers):
                    buf = self._media_buffers.get(bid)
                    if buf is None or not buf.download_complete:
                        continue
                    # 跳过最近 120 秒内被代理访问过的缓冲（正在被流式传输）
                    last_access = self._buffer_last_accessed.get(bid, 0)
                    if last_access > 0 and (now - last_access) < 120:
                        continue
                    created = self._buffer_created_time.get(bid, 0)
                    if created > 0 and (now - created) > 120:
                        # 检查是否是当前播放的源 URL
                        is_active = False
                        source_url = buf.remote_url
                        if source_url in active_urls:
                            # 检查这个 buffer_id 是否是该 URL 的最新缓冲
                            if self._url_to_buffer.get(source_url) == bid:
                                is_active = True
                        if not is_active:
                            to_remove.append(bid)
                
                for bid in to_remove:
                    self._remove_buffer(bid)
                if to_remove:
                    log.info(f"周期清理: 移除 {len(to_remove)} 个过时缓冲")
                
                # 原有的数量限制清理（也要跳过最近访问过的缓冲）
                if len(self._media_buffers) > _MAX_BUFFERS // 2:
                    ids = list(self._media_buffers.keys())
                    to_remove = ids[:len(ids) - _MAX_BUFFERS // 2]
                    for bid in to_remove:
                        buf = self._media_buffers.get(bid)
                        if not buf or not buf.download_complete:
                            continue
                        # 跳过最近被访问的缓冲
                        la = self._buffer_last_accessed.get(bid, 0)
                        if la > 0 and (now - la) < 120:
                            continue
                        if self._buffer_is_in_use(bid):
                            continue
                        self._remove_buffer(bid)
                # 缓冲存储上限检查
                self._cleanup_by_storage()
                # 清理已完成的 background tasks
                self._background_tasks = {t for t in self._background_tasks if not t.done()}
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            # 清理任务异常不得静默终止 (否则缓冲只增不减, 最终突破存储预算)
            log.error(f"缓冲清理任务异常退出: {e}", exc_info=True)

    def abort_proxy_for_renderer(self, udn: str):
        """立即中止指定渲染器的所有活跃代理连接，并屏蔽后续重连请求"""
        self._paused_proxy_udns.add(udn)
        tasks = self._active_proxy_tasks.get(udn)
        if tasks:
            log.info(f"[{udn}] 正在中止 {len(tasks)} 个活跃的媒体代理连接...")
            for task in list(tasks):
                if not task.done():
                    task.cancel()
            tasks.clear()

    def resume_proxy_for_renderer(self, udn: str):
        """恢复渲染器的代理访问（播放/停止时调用）"""
        self._paused_proxy_udns.discard(udn)

    async def _handle_default_cover(self, request: web.Request) -> web.Response:
        """通用默认封面处理器。

        当投送方未提供封面时，DLNA/AirPlay 触屏回退到这张内置默认图。
        若用户在 Web 配置 default_cover_url，则优先返回该地址 (由解析逻辑决定，
        此处始终返回内置资源；可配置 URL 由 renderer/airplay 注入元数据时使用)。
        """
        path = default_cover_asset_path()
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            return web.Response(status=404, text="Default cover not found")
        return web.Response(body=data, content_type="image/png")

    async def _handle_media_proxy(self, request: web.Request) -> web.StreamResponse:
        """媒体代理处理器 - 从内存缓冲提供音频，支持 Range/Seek"""
        token = request.match_info.get("token", "")
        entry = self._proxy_tokens.get(token)
        if not entry:
            log.warning(f"代理请求无效 token: {token}")
            return web.Response(status=404, text="Not Found")

        buffer_id, base_offset, udn = entry

        # 暂停中的渲染器，拒绝代理请求（防止音箱重连拉取音频）
        if udn and udn in self._paused_proxy_udns:
            log.info(f"[{udn}] 暂停中，拒绝代理请求")
            return web.Response(status=403, text="Paused")

        buf = self._media_buffers.get(buffer_id)
        if not buf:
            log.warning(f"代理请求缓冲不存在: {buffer_id}")
            return web.Response(status=404, text="Buffer Not Found")

        # 更新最后访问时间，保护缓冲不被周期清理误删
        self._buffer_last_accessed[buffer_id] = time.time()

        # 注册任务到 udn 追踪列表
        if udn:
            current_task = asyncio.current_task()
            # 闭包捕获 set 本身而非按 udn 回查: 设备注销后 key 可能已被移除,
            # 回查会 KeyError; 同时在集合空时删掉 key, 避免每台播放过的音箱
            # 永久留下一个空 set (原实现只 discard 不删 key, 属缓慢泄漏)。
            tasks = self._active_proxy_tasks.setdefault(udn, set())
            tasks.add(current_task)
            self._active_proxy_buffers[current_task] = buffer_id

            def _untrack(t, _udn=udn, _tasks=tasks):
                _tasks.discard(t)
                self._active_proxy_buffers.pop(t, None)
                if not _tasks and self._active_proxy_tasks.get(_udn) is _tasks:
                    self._active_proxy_tasks.pop(_udn, None)

            current_task.add_done_callback(_untrack)

        log.info(
            f"代理请求: base_offset={base_offset}, "
            f"下载进度={buf.downloaded_size}/{buf.total_size or '?'}")

        # 检查是否需要转码（不支持无损格式的音箱）
        needs_conversion = False
        if udn and not buf._converted:
            renderer = self.renderers.get(udn)
            if renderer and renderer.speaker:
                speaker = renderer.speaker.speaker
                needs_conversion = speaker.needs_audio_conversion(buf.content_type)

        # 等待下载完成
        await buf.wait_for_completion(timeout=120)
        
        # 如果需要转码，一次性转为 WAV 再提供服务（WAV转码极快，几乎无感）
        if needs_conversion:
            if buf.error or not buf.download_complete:
                log.error(f"音频缓冲未就绪（转码前）: {buf.error}")
                return web.Response(status=502, text="Buffer Error")
            ok = await self._convert_buffer_to_wav(buf)
            if not ok:
                log.warning("WAV 转码失败，尝试原始格式提供服务")
        if buf.error or not buf.download_complete:
            log.error(f"音频缓冲未就绪: {buf.error}")
            return web.Response(status=502, text="Buffer Error")

        total_file_size = buf.downloaded_size
        content_type = buf.content_type

        # base_offset 之后的 "虚拟" 内容
        virtual_size = total_file_size - base_offset
        if virtual_size <= 0:
            return web.Response(status=416, text="Range Not Satisfiable")

        # 解析 Range 请求头
        range_header = request.headers.get("Range")
        if range_header:
            parsed = self._parse_range_header(range_header, virtual_size)
            if parsed is None:
                return web.Response(status=416, text="Range Not Satisfiable")
            range_start, range_end = parsed
            actual_start = base_offset + range_start
            actual_end = base_offset + range_end
            content_length = actual_end - actual_start + 1

            resp_headers = {
                "Content-Type": content_type,
                "Content-Length": str(content_length),
                "Content-Range": f"bytes {range_start}-{range_end}/{virtual_size}",
                "Accept-Ranges": "bytes",
            }
            response = web.StreamResponse(status=206, headers=resp_headers)
        else:
            actual_start = base_offset
            content_length = virtual_size

            resp_headers = {
                "Content-Type": content_type,
                "Content-Length": str(content_length),
                "Accept-Ranges": "bytes",
            }
            response = web.StreamResponse(status=200, headers=resp_headers)

        bytes_sent = 0
        try:
            await response.prepare(request)
            pos = actual_start
            end = actual_start + content_length
            while pos < end:
                chunk_end = min(pos + 256 * 1024, end)
                chunk = await asyncio.to_thread(
                    buf.read_range, pos, chunk_end - pos
                )
                if not chunk:
                    raise ConnectionError(
                        f"缓冲文件读取提前结束 ({pos}/{end})"
                    )
                await response.write(chunk)
                bytes_sent += len(chunk)
                pos = chunk_end
            await response.write_eof()
            return response
        except asyncio.CancelledError:
            if bytes_sent >= content_length:
                return response
            raise
        except ConnectionError:
            if bytes_sent >= content_length:
                pass
            else:
                log.warning(f"代理连接中断: {bytes_sent}/{content_length} bytes")
            return response
        except Exception as e:
            if bytes_sent >= content_length:
                pass
            else:
                log.error(f"代理响应失败 ({bytes_sent}/{content_length} bytes): {e}")
            return response

    async def _convert_buffer_to_wav(self, buf: MediaBuffer) -> bool:
        """将缓冲中的音频一次性转换为 WAV (PCM) 格式
        
        WAV 转码极快（纯解码+写PCM），一首5分钟的歌通常1-2秒内完成。
        转完后直接替换缓冲数据，后续当普通文件提供服务。
        """
        if buf._converted:
            return True
        if buf._converting:
            # 另一个请求正在转码，等待完成
            try:
                await asyncio.wait_for(buf._convert_event.wait(), timeout=60)
            except asyncio.TimeoutError:
                pass
            return buf._converted
        
        # 已经是可直接播放的格式
        ct = buf.content_type.lower()
        if "wav" in ct or "x-wav" in ct or "mp3" in ct or "mpeg" in ct:
            buf._converted = True
            return True
        
        buf._converting = True
        input_path = None
        output_path = None
        
        try:
            import tempfile

            original_size = buf.downloaded_size
            # 写入临时输入文件（异步避免大文件写入阻塞事件循环）
            with tempfile.NamedTemporaryFile(
                suffix=".input", dir=self._buffer_temp_dir, delete=False
            ) as f:
                input_path = f.name
            await asyncio.to_thread(buf.copy_to_file, input_path)
            output_path = input_path + '.wav'

            # 转码期间同时存在原缓冲、输入副本和输出文件。限制 FFmpeg 输出，
            # 让这几份临时数据也计入缓冲预算，并保留文件系统安全余量。
            buffered_bytes = sum(
                candidate.downloaded_size
                for candidate in self._media_buffers.values()
            )
            reserved_bytes = self._reserved_buffer_memory()
            other_reserved = max(0, reserved_bytes - original_size)
            storage_limit = min(
                self._max_buffer_memory - other_reserved,
                self._max_buffer_memory - buffered_bytes - original_size,
            )
            temp_root = os.path.dirname(input_path) or "."
            try:
                disk_limit = shutil.disk_usage(temp_root).free - 32 * 1024 * 1024
            except OSError:
                disk_limit = storage_limit
            output_limit = min(storage_limit, disk_limit)
            if output_limit <= 0:
                log.warning("可用缓冲空间不足，跳过 WAV 转码")
                return False

            ffmpeg_path = self._check_ffmpeg() or 'ffmpeg'
            cmd = [
                ffmpeg_path, '-y',
                '-hide_banner',
                '-loglevel', 'error',
                '-threads', '2',          # 限制解码并行度，避免小型主机被 FFmpeg 占满
                '-i', input_path,
                '-vn',                    # 禁用视频
                '-codec:a', 'pcm_s16le',  # 16-bit PCM
                '-ar', '44100',           # 44.1kHz 采样率
                '-ac', '2',               # 立体声
                '-fs', str(output_limit), # 约束临时 WAV 文件的最大空间
                output_path
            ]

            log.info(f"WAV 转码开始: {buf.content_type} ({original_size} bytes)")
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=30  # WAV 转码很快，30 秒绰绰有余
                )
            except asyncio.TimeoutError:
                try:
                    process.kill()
                except Exception:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except Exception:
                    pass
                log.error("WAV 转码超时")
                return False
            
            if process.returncode != 0:
                stderr_str = stderr.decode('utf-8', errors='ignore') if stderr else ""
                log.error(f"WAV 转码失败 (rc={process.returncode}): {stderr_str[:200]}")
                return False

            # ffmpeg 已结束，尽早释放输入副本，避免复制输出时多占一份磁盘空间。
            try:
                os.unlink(input_path)
                input_path = None
            except OSError:
                pass
            
            # 读取转码结果
            if not os.path.isfile(output_path):
                log.error("WAV 转码未产生输出文件")
                return False
            
            output_size = os.path.getsize(output_path)
            if output_size <= 0:
                log.error("WAV 转码输出文件为空")
                return False
            if output_size >= output_limit:
                log.error(
                    f"WAV 转码输出达到空间上限，拒绝使用可能不完整的文件: "
                    f"{output_size}/{output_limit} bytes"
                )
                return False
            os.chmod(output_path, 0o600)

            buffer_id = next(
                (bid for bid, candidate in self._media_buffers.items() if candidate is buf),
                None,
            )
            required_extra = max(0, output_size - original_size)
            if not self._cleanup_by_storage(required_extra, protected_id=buffer_id):
                log.error(
                    f"WAV 转码结果超出缓冲存储预算: {output_size} bytes"
                )
                return False
            
            # 直接接管输出文件为缓冲存储，避免把大 WAV 读入内存或再复制一份。
            replaced = await asyncio.to_thread(
                buf.replace_from_file,
                output_path,
                max_size=self._max_buffer_memory,
            )
            if not replaced:
                log.error(f"WAV 转码结果超出单缓冲上限: {output_size} bytes")
                return False
            output_path = None  # 所有权已转移给 MediaBuffer，cleanup 会删除文件。
            buf.content_type = "audio/wav"
            buf._converted = True
            
            log.info(
                f"WAV 转码完成: {buf.total_size} bytes "
                f"(原 {original_size} bytes, {buf.total_size / max(original_size, 1):.1f}x)"
            )
            return True
            
        except Exception as e:
            log.error(f"WAV 转码异常: {e}")
            return False
        finally:
            # 清理临时文件和状态
            for p in (input_path, output_path):
                if p:
                    try:
                        os.unlink(p)
                    except OSError:
                        pass
            buf._converting = False
            buf._convert_event.set()

    def _get_ffmpeg_input_format(self, content_type: str, remote_url: str = "") -> str:
        """根据Content-Type和URL获取ffmpeg输入格式"""
        content_type_lower = content_type.lower()
        
        # 首先检查URL后缀
        if remote_url:
            url_lower = remote_url.lower()
            if ".flac" in url_lower:
                return "flac"
            elif ".ogg" in url_lower or ".opus" in url_lower:
                return "ogg"
            elif ".wav" in url_lower:
                return "wav"
            elif ".m4a" in url_lower:
                return "aac"
            elif ".mp3" in url_lower:
                return "mp3"
        
        # 根据Content-Type判断
        if "flac" in content_type_lower:
            return "flac"
        elif "ogg" in content_type_lower or "opus" in content_type_lower:
            return "ogg"
        elif "wav" in content_type_lower:
            return "wav"
        elif "m4a" in content_type_lower or "aac" in content_type_lower:
            return "aac"
        elif "mp3" in content_type_lower or "mpeg" in content_type_lower:
            return "mp3"
        else:
            # 对于未知格式，尝试使用原始数据
            return "data"
    
    @staticmethod
    def _parse_range_header(
        header: str, total: int
    ) -> tuple[int, int] | None:
        """解析 Range 头 (bytes=start-end)"""
        m = re.match(r"bytes=(\d*)-(\d*)", header)
        if not m:
            return None
        start_str, end_str = m.group(1), m.group(2)
        if start_str:
            start = int(start_str)
            end = int(end_str) if end_str else total - 1
        elif end_str:
            suffix = int(end_str)
            start = max(total - suffix, 0)
            end = total - 1
        else:
            return None
        if start > end or start >= total:
            return None
        end = min(end, total - 1)
        return start, end

    def _get_renderer(self, request: web.Request) -> DLNARenderer | None:
        """从请求中提取 UDN 并获取对应的渲染器"""
        udn = request.match_info.get("udn", "")
        return self.renderers.get(udn)

    async def _handle_description(self, request: web.Request) -> web.Response:
        """处理设备描述请求"""
        renderer = self._get_renderer(request)
        if not renderer:
            return web.Response(status=404, text="Device not found")

        base_url = f"http://{self.hostname}:{self.dlna_port}"
        xml = device_description_xml(renderer.udn, renderer.friendly_name, base_url)
        return web.Response(
            text=xml,
            content_type="text/xml",
            charset="utf-8",
        )

    async def _handle_avtransport_scpd(self, request: web.Request) -> web.Response:
        """返回 AVTransport SCPD"""
        return web.Response(text=AVTRANSPORT_SCPD, content_type="text/xml", charset="utf-8")

    async def _handle_rendering_control_scpd(self, request: web.Request) -> web.Response:
        """返回 RenderingControl SCPD"""
        return web.Response(
            text=RENDERING_CONTROL_SCPD, content_type="text/xml", charset="utf-8"
        )

    async def _handle_connection_manager_scpd(self, request: web.Request) -> web.Response:
        """返回 ConnectionManager SCPD"""
        return web.Response(
            text=CONNECTION_MANAGER_SCPD, content_type="text/xml", charset="utf-8"
        )

    async def _handle_control(self, request: web.Request) -> web.Response:
        """处理 SOAP 控制请求"""
        renderer = self._get_renderer(request)
        if not renderer:
            return web.Response(status=404, text="Device not found")

        # 记录最后活动时间
        renderer._last_control_time = time.time()

        # 解析 SOAPAction
        soap_action = request.headers.get("SOAPAction", "")
        if not soap_action:
            soap_action = request.headers.get("SOAPACTION", "")
        service_urn, action = parse_soap_action(soap_action)

        # 解析请求体
        body = await request.text()
        params = parse_soap_body(body)

        # 处理请求 (handle_soap_request 内部已把参数错误/执行异常转成规范 Fault;
        # 这里再兜一层, 确保任何意外都不会变成 HTTP 500 空响应)
        try:
            response_xml, status_code = await handle_soap_request(
                renderer, service_urn, action, params
            )
        except Exception as e:  # noqa: BLE001
            log.error(
                f"[{renderer.friendly_name}] SOAP 请求处理异常 ({action}): {e}",
                exc_info=True,
            )
            response_xml = soap_fault(UPNP_ERROR_ACTION_FAILED, "Internal error")
            status_code = 500

        return web.Response(
            text=response_xml,
            status=status_code,
            content_type="text/xml",
            charset="utf-8",
        )

    async def _handle_subscribe(self, request: web.Request) -> web.Response:
        """处理 SUBSCRIBE 请求"""
        udn = request.match_info.get("udn", "")
        service = request.match_info.get("service", "")
        event_manager = self.event_managers.get(udn)
        if not event_manager:
            return web.Response(status=404)

        # 记录活动时间（续订或新订阅都说明控制端仍在连接）
        renderer = self.renderers.get(udn)
        if renderer:
            renderer._last_control_time = time.time()

        # 检查是否是续订 (有 SID header)
        sid = request.headers.get("SID", "")
        if sid:
            timeout = self._parse_timeout(request.headers.get("TIMEOUT", ""))
            if event_manager.renew(sid, timeout):
                return web.Response(
                    status=200,
                    headers={
                        "SID": sid,
                        "TIMEOUT": f"Second-{timeout}",
                    },
                )
            return web.Response(status=412)  # Precondition Failed

        # 新订阅
        callback = request.headers.get("CALLBACK", "")
        if not callback:
            return web.Response(status=400)

        # 提取 URL (格式: <http://...>); CALLBACK 校验与订阅上限由
        # EventManager.subscribe 负责, 拒绝时回 400
        callback_url = callback.strip("<>")
        timeout = self._parse_timeout(request.headers.get("TIMEOUT", ""))

        sid = await event_manager.subscribe(callback_url, timeout)
        if sid is None:
            return web.Response(status=400)

        # 发送初始事件 (参照 MaCast: 在后台发送完整状态，不阻塞 SUBSCRIBE 响应)
        renderer = self.renderers.get(udn)
        if renderer:
            # 客户端建立新订阅，视为新会话或重连，重置音量初始化标志
            # 以便在接下来的播放中重新应用默认音量
            renderer._volume_initialized = False
            task = asyncio.get_running_loop().create_task(
                self._send_initial_event(event_manager, sid, renderer, service)
            )
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

        return web.Response(
            status=200,
            headers={
                "SID": sid,
                "TIMEOUT": f"Second-{timeout}",
            },
        )

    async def _send_initial_event(
        self, em: EventManager, sid: str, renderer: DLNARenderer, service: str
    ):
        """发送初始事件 (后台执行，包含完整状态变量)"""
        sub = em._subscriptions.get(sid)
        if not sub:
            return
        # 参照 MaCast: 初始事件发送该服务的所有 observed 状态变量
        event_xml = build_last_change_event(
            transport_state=renderer.transport_state,
            volume=renderer.volume,
        )
        try:
            await em._send_notify(sub, event_xml)
        except Exception:
            pass

    async def _handle_unsubscribe(self, request: web.Request) -> web.Response:
        """处理 UNSUBSCRIBE 请求"""
        udn = request.match_info.get("udn", "")
        event_manager = self.event_managers.get(udn)
        if not event_manager:
            return web.Response(status=404)

        sid = request.headers.get("SID", "")
        if sid and event_manager.unsubscribe(sid):
            return web.Response(status=200)
        return web.Response(status=412)

    @staticmethod
    def _parse_timeout(timeout_header: str) -> int:
        """解析 TIMEOUT header (e.g., 'Second-1800'), 收敛到合理区间"""
        if timeout_header.startswith("Second-"):
            try:
                return clamp_timeout(int(timeout_header[7:]))
            except ValueError:
                pass
        return 1800

    async def _poll_speaker_states(self):
        """定期轮询音箱实际播放状态，同步渲染器状态并发送事件通知

        并发安全: 循环体每轮都要 await 云端查状态 (单台最多 10s), 期间
        Orchestrator 的在线巡检可能调用 unregister_renderer() 从
        self.renderers 里 pop 掉离线音箱。若直接迭代 self.renderers.items(),
        字典在迭代中被修改会抛 RuntimeError 并直接杀死本任务 —— 而
        stop() 与 Orchestrator._stop_dlna_services 都未兜住该异常, 后果是
        停止链路中断: SSDP 已停、device_server 仍在监听、dlna_running 仍为
        True, 之后每次「热重启」都撞同一具死任务, 只能重启进程才能恢复。
        故每轮先对键取快照, 再按键取当前渲染器 (已注销的键自然跳过)。
        """
        try:
            while True:
                await asyncio.sleep(_POLL_INTERVAL_SECONDS)
                try:
                    await self._poll_once()
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    # 巡检是状态同步的唯一来源, 单轮失败必须只丢掉这一轮,
                    # 不能让任务退出 (退出后 DLNA 状态永不更新且无法自愈)
                    log.warning(f"本轮音箱状态轮询失败, 跳过本轮: {e}", exc_info=True)
        except asyncio.CancelledError:
            raise

    async def _poll_once(self) -> None:
        """执行一轮音箱状态巡检 (单台/单步失败不外溢)"""
        for udn in list(self.renderers):
            # 快照之后该设备可能已被注销 (离线隐藏), 跳过即可
            renderer = self.renderers.get(udn)
            if renderer is None or not renderer.speaker:
                continue

            # 检测控制端断开：渲染器处于终端状态（PAUSED/STOPPED）且有媒体URI
            # 1. PAUSED 振荡：音箱 STOP 但被强制保持 PAUSED 超过 30 秒
            #    语音打断场景（_user_stopped=False）：直接重置
            #    用户手动暂停（_user_stopped=True）：需确认无订阅者后重置
            # 2. STOPPED 持久：有 URI 但无控制活动超过 60 秒且无订阅者
            idle = False
            if (
                renderer.current_uri
                and renderer.transport_state == TRANSPORT_STATE_PAUSED
                and renderer._stuck_paused_since > 0
                and (time.time() - renderer._stuck_paused_since) > 30
            ):
                if not renderer._user_stopped:
                    idle = True
                elif (
                    renderer.event_manager
                    and not renderer.event_manager.has_subscribers()
                ):
                    idle = True
            elif (
                renderer.current_uri
                and renderer.transport_state == TRANSPORT_STATE_STOPPED
                and renderer._last_control_time > 0
                and (time.time() - renderer._last_control_time) > 60
                and renderer.event_manager
                and not renderer.event_manager.has_subscribers()
            ):
                idle = True

            if idle:
                # 单台重置失败不应拖垮整轮巡检 (reset 内部有锁与通知)
                try:
                    await renderer.reset_to_idle()
                except Exception as e:  # noqa: BLE001
                    log.warning(
                        f"[{renderer.friendly_name}] 重置为空闲失败: {e}"
                    )
                renderer._stuck_paused_since = 0.0
                continue

            if not renderer.current_uri:
                continue
            try:
                status = await asyncio.wait_for(
                    renderer.speaker.get_status(), timeout=10
                )
                await self._sync_renderer_state(udn, renderer, status)
            except Exception as e:
                log.warning(
                    f"[{renderer.friendly_name}] 轮询状态暂时失败 (网络或 API 超时): {e}"
                )

    async def _sync_renderer_state(
        self, udn: str, renderer: DLNARenderer, status: dict
    ):
        """同步单个渲染器状态"""
        speaker_status = status.get("status", 0)
        new_state = {
            0: TRANSPORT_STATE_STOPPED,
            1: TRANSPORT_STATE_PLAYING,
            2: TRANSPORT_STATE_PAUSED,
        }.get(speaker_status, TRANSPORT_STATE_STOPPED)

        if renderer.transport_state == new_state:
            return

        # music-api 机型 pause=player_stop: 音箱在暂停期间持续上报 STOPPED,
        # 而「保持位置」改写会把渲染器维持在 PAUSED。不拦截的话每轮轮询都会
        # 重复状态改写、日志与 GENA 通知 (refs #18)。已处于保持位置暂停时视为无变化。
        if (
            new_state == TRANSPORT_STATE_STOPPED
            and renderer.transport_state == TRANSPORT_STATE_PAUSED
            and renderer._stuck_paused_since > 0
        ):
            return

        old_state = renderer.transport_state

        # TRANSITIONING 保护：play()/seek() 正在执行中，轮询结果是过时的，跳过同步
        if old_state == TRANSPORT_STATE_TRANSITIONING:
            return

        # 宽限期内：音箱还没真正开始播放（转码中），不覆盖 PLAYING 状态
        if (
            renderer._play_grace_until > 0
            and time.time() < renderer._play_grace_until
            and old_state == TRANSPORT_STATE_PLAYING
            and new_state != TRANSPORT_STATE_PLAYING
        ):
            return

        # 跳过从 PAUSED 到 PLAYING 的状态更新
        if old_state == TRANSPORT_STATE_PAUSED and new_state == TRANSPORT_STATE_PLAYING:
            return

        renderer.transport_state = new_state
        log_message = self._handle_state_transition(
            udn, renderer, old_state, new_state
        )

        await renderer.notify_state_change()
        log.info(f"[{renderer.friendly_name}] 状态同步: {log_message}")

    def _handle_state_transition(
        self, udn: str, renderer: DLNARenderer,
        old_state: str, new_state: str
    ) -> str:
        """处理状态变迁的副作用，返回日志消息"""
        if new_state == TRANSPORT_STATE_PAUSED and old_state == TRANSPORT_STATE_PLAYING:
            if renderer._play_start_time > 0:
                renderer._accumulated_time += time.time() - renderer._play_start_time
                renderer._play_start_time = 0.0
            return f"{old_state} -> {new_state}"

        if new_state == TRANSPORT_STATE_PLAYING and old_state == TRANSPORT_STATE_PAUSED:
            renderer._play_start_time = time.time()
            return f"{old_state} -> {new_state}"

        if new_state == TRANSPORT_STATE_STOPPED and old_state in (
            TRANSPORT_STATE_PLAYING, TRANSPORT_STATE_PAUSED
        ):
            if renderer._play_start_time > 0:
                renderer._accumulated_time += time.time() - renderer._play_start_time
            renderer._play_start_time = 0.0
            renderer.transport_state = TRANSPORT_STATE_PAUSED

            if renderer._stuck_paused_since == 0.0:
                renderer._stuck_paused_since = time.time()

            if (
                self.config
                and self.config.auto_resume_on_interrupt
                and not renderer._user_stopped
                and (time.time() - renderer._stuck_paused_since) < 15
            ):
                if udn in self._resume_tasks:
                    self._resume_tasks[udn].cancel()
                delay = self.config.resume_delay_seconds
                self._resume_tasks[udn] = asyncio.create_task(
                    self._auto_resume_after_delay(udn, delay)
                )
                log.info(
                    f"[{renderer.friendly_name}] 将在 {delay} 秒后自动恢复播放"
                )
            return f"{old_state} -> PAUSED (保持位置)"

        if new_state == TRANSPORT_STATE_STOPPED:
            renderer._accumulated_time = 0.0
            renderer._play_start_time = 0.0

        return f"{old_state} -> {new_state}"

    async def _auto_resume_after_delay(self, udn: str, delay: int):
        """实验性功能：延迟后自动恢复播放"""
        try:
            await asyncio.sleep(delay)
            renderer = self.renderers.get(udn)
            if not renderer:
                return
            
            # 检查当前状态是否仍然是PAUSED
            if renderer.transport_state == TRANSPORT_STATE_PAUSED:
                log.info(f"[{renderer.friendly_name}] 自动恢复播放")
                await renderer.play()
            
            # 清除任务引用
            if udn in self._resume_tasks:
                del self._resume_tasks[udn]
        except asyncio.CancelledError:
            # 任务被取消，正常情况
            pass
        except Exception as e:
            log.error(f"自动恢复播放失败: {e}")

    async def start(self):
        """启动 HTTP 服务"""
        # 启动所有事件管理器
        for em in self.event_managers.values():
            em.start_cleanup()

        self._runner = web.AppRunner(self.app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self.dlna_port)
        await site.start()
        log.info(f"DLNA HTTP 服务已启动: http://0.0.0.0:{self.dlna_port}")

        # 启动状态轮询
        self._poll_task = asyncio.create_task(self._poll_speaker_states())

        # 启动周期性缓冲清理
        self._buffer_cleanup_task = asyncio.create_task(self._periodic_buffer_cleanup())

    async def stop(self):
        """停止 HTTP 服务

        每一步独立兜底: 任何一步失败都不能中断后续清理, 否则会留下
        "SSDP 已停但 HTTP 仍在监听 / 会话未关闭" 的半死状态。
        尤其是轮询任务的 await —— 历史缺陷下它会带着 RuntimeError 抛出。
        """
        # 停止轮询
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            except Exception as e:  # noqa: BLE001
                log.warning(f"停止状态轮询任务时异常: {e}")
            self._poll_task = None

        # 停止周期性缓冲清理
        if self._buffer_cleanup_task:
            self._buffer_cleanup_task.cancel()
            try:
                await self._buffer_cleanup_task
            except asyncio.CancelledError:
                pass
            except Exception as e:  # noqa: BLE001
                log.warning(f"停止缓冲清理任务时异常: {e}")
            self._buffer_cleanup_task = None

        # 取消所有 background tasks
        for task in list(self._background_tasks):
            task.cancel()
        self._background_tasks.clear()

        # 取消所有恢复任务（添加超时）
        if self._resume_tasks:
            for task in self._resume_tasks.values():
                task.cancel()
            # 等待所有任务完成或超时
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._resume_tasks.values(), return_exceptions=True),
                    timeout=2.0
                )
            except asyncio.TimeoutError:
                log.warning("恢复任务取消超时")
            self._resume_tasks.clear()

        for em in list(self.event_managers.values()):
            try:
                await em.stop()
            except Exception as e:  # noqa: BLE001
                log.warning(f"停止 DLNA EventManager 失败: {e}")
        self._cleanup_all_buffers()
        self._active_proxy_buffers.clear()
        if self._proxy_session and not self._proxy_session.closed:
            await self._proxy_session.close()
        if self._runner:
            # 添加超时，避免卡住
            try:
                await asyncio.wait_for(self._runner.cleanup(), timeout=3.0)
            except asyncio.TimeoutError:
                log.warning("DLNA HTTP 服务关闭超时")
        log.info("DLNA HTTP 服务已停止")
