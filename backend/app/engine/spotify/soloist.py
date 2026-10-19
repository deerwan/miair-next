"""Spotify Soloist 二进制管理与进程守护"""

from __future__ import annotations

import asyncio
import http.client
import logging
import os
import platform
import re
import shutil
import socket
import ssl
import subprocess
import tarfile
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit

log = logging.getLogger("miair")

CDN_BASE = "https://soloist-builds.spotifycdn.com"
DOWNLOAD_URL_TEMPLATE = CDN_BASE + "/soloist_release_{arch}.tar.gz"
BUILD_TTL_DAYS = 90  # 官方构建有效期 90 天
DOWNLOAD_TIMEOUT = 180
DOWNLOAD_RETRIES = 3

_ARCH_ALIASES = {
    "x86_64": "x86_64",
    "amd64": "x86_64",
    "aarch64": "arm64",
    "arm64": "arm64",
    "armv7l": "arm32",
    "armv7": "arm32",
    "armv6l": "arm32",
    "armv6": "arm32",
    "armv8l": "arm32",
}

_VERSION_RE = re.compile(r"soloist\s+(\S+)\s+build\s+(\d+)\s+\((\d{8})\)")
_EXPIRES_RE = re.compile(r"client expires in (\d+) days")


def parse_active_device_change(line: str) -> bool | None:
    """从 Soloist 日志解析活动设备变更信号，返回 True/False/None"""
    text = line or ""
    if "became active device" in text:
        return True
    if "no longer active device" in text:
        return False
    return None


class _IPv4HTTPSConnection(http.client.HTTPSConnection):
    """优先使用 IPv4 的 HTTPS 连接，避免容器环境 IPv6 不通时握手超时"""

    def connect(self) -> None:
        infos = socket.getaddrinfo(
            self.host, self.port, socket.AF_INET, socket.SOCK_STREAM
        )
        last_error: OSError | None = None
        for family, socktype, proto, _canon, addr in infos:
            sock = socket.socket(family, socktype, proto)
            try:
                sock.settimeout(self.timeout)
                sock.connect(addr)
                self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
                return
            except OSError as e:  # noqa: PERF203
                last_error = e
                sock.close()
        raise last_error or OSError(f"无法解析 {self.host} 的 IPv4 地址")


def detect_arch(machine: str | None = None) -> str:
    """映射系统架构为 Soloist 官方支持的架构名称"""
    raw = (machine or platform.machine() or "").strip().lower()
    arch = _ARCH_ALIASES.get(raw)
    if not arch:
        raise ValueError(f"Spotify Soloist 不支持当前架构: {raw or 'unknown'}")
    return arch


def download_url_for(arch: str) -> str:
    return DOWNLOAD_URL_TEMPLATE.format(arch=arch)


def parse_version_output(text: str) -> dict:
    """解析 soloist --version 输出"""
    result = {
        "version": "",
        "build_epoch": 0,
        "build_date": "",
        "expires_on": "",
        "days_left": None,
    }
    if not text:
        return result
    m = _VERSION_RE.search(text)
    if not m:
        return result
    version, build_epoch, build_date_str = m.group(1), int(m.group(2)), m.group(3)
    try:
        built = datetime.strptime(build_date_str, "%Y%m%d").date()
    except ValueError:
        return result
    expires = built + timedelta(days=BUILD_TTL_DAYS)
    result.update(
        version=version,
        build_epoch=build_epoch,
        build_date=build_date_str,
        expires_on=expires.isoformat(),
        days_left=(expires - date.today()).days,
    )
    return result


def parse_expiry_days(line: str) -> int | None:
    """从 Soloist 启动日志中提取剩余有效期天数"""
    m = _EXPIRES_RE.search(line or "")
    return int(m.group(1)) if m else None


@dataclass
class BinaryInfo:
    arch: str = ""
    path: str = ""
    installed: bool = False
    version: str = ""
    build_date: str = ""
    expires_on: str = ""
    days_left: int | None = None
    downloading: bool = False
    last_error: str = ""
    download_url: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class SoloistBinary:
    """Soloist 二进制文件下载与版本管理"""

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.spotify_dir = os.path.join(data_dir, "spotify")
        self.bin_dir = os.path.join(self.spotify_dir, "bin")
        self.arch = detect_arch()
        self.path = os.path.join(self.bin_dir, "soloist")
        self.info = BinaryInfo(
            arch=self.arch,
            path=self.path,
            download_url=download_url_for(self.arch),
        )
        self._lock = asyncio.Lock()
        self.refresh_info()

    def refresh_info(self) -> BinaryInfo:
        self.info.arch = self.arch
        self.info.path = self.path
        self.info.download_url = download_url_for(self.arch)
        if not os.path.isfile(self.path):
            self.info.installed = False
            self.info.version = ""
            self.info.build_date = ""
            self.info.expires_on = ""
            self.info.days_left = None
            return self.info
        text = self._run_version()
        parsed = parse_version_output(text)
        if not parsed["version"]:
            self.info.installed = False
            self.info.last_error = (
                "已存在 soloist 文件但无法执行 (可能架构/glibc 不兼容): "
                f"{(text or '').strip()[:200]}"
            )
            return self.info
        self.info.installed = True
        self.info.version = parsed["version"]
        self.info.build_date = parsed["build_date"]
        self.info.expires_on = parsed["expires_on"]
        self.info.days_left = parsed["days_left"]
        return self.info

    def is_expired(self) -> bool:
        if not self.info.installed:
            return True
        return self.info.days_left is not None and self.info.days_left <= 0

    def _run_version(self) -> str:
        try:
            proc = subprocess.run(
                [self.path, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return (proc.stdout or "") + (proc.stderr or "")
        except Exception as e:  # noqa: BLE001
            return str(e)

    async def ensure(self, force: bool = False) -> BinaryInfo:
        """确保可执行文件存在且未过期；force=True 强制重新下载"""
        async with self._lock:
            self.refresh_info()
            need = force or not self.info.installed or self.is_expired()
            if not need:
                return self.info
            self.info.downloading = True
            self.info.last_error = ""
            try:
                log.info(f"下载 Spotify Soloist ({self.arch}): {self.info.download_url}")
                await asyncio.to_thread(self._download_sync)
                self.refresh_info()
                if not self.info.installed:
                    raise RuntimeError(self.info.last_error or "下载后无法执行 soloist")
                log.info(
                    f"Spotify Soloist 已就绪: v{self.info.version} "
                    f"(构建 {self.info.build_date}, 剩余 {self.info.days_left} 天)"
                )
            except Exception as e:  # noqa: BLE001
                self.info.last_error = f"Spotify Soloist 下载失败: {e}"
                log.error(self.info.last_error)
                # 已存在旧版本时继续使用
                if not self.info.installed:
                    raise
            finally:
                self.info.downloading = False
            return self.info

    def _download_with_retry(self, url: str, dest: str) -> None:
        """带重试机制的下载流程，优先使用 IPv4 解析"""
        last_error: Exception | None = None
        for attempt in range(1, DOWNLOAD_RETRIES + 1):
            prefer_ipv4 = attempt < DOWNLOAD_RETRIES
            try:
                self._download_to(url, dest, prefer_ipv4=prefer_ipv4)
                return
            except Exception as e:  # noqa: BLE001
                last_error = e
                log.warning(
                    f"Spotify Soloist 下载失败 (第 {attempt}/{DOWNLOAD_RETRIES} 次, "
                    f"prefer_ipv4={prefer_ipv4}): {e}"
                )
                if attempt < DOWNLOAD_RETRIES:
                    time.sleep(min(2.0 * attempt, 5.0))
        raise RuntimeError(f"Spotify Soloist 下载失败: {last_error}")

    def _download_to(self, url: str, dest: str, prefer_ipv4: bool = True) -> None:
        parts = urlsplit(url)
        if not parts.hostname:
            raise ValueError(f"非法下载地址: {url}")
        context = ssl.create_default_context()
        conn_cls = _IPv4HTTPSConnection if prefer_ipv4 else http.client.HTTPSConnection
        conn = conn_cls(
            parts.hostname,
            parts.port or 443,
            timeout=DOWNLOAD_TIMEOUT,
            context=context,
        )
        try:
            conn.request(
                "GET",
                parts.path or "/",
                headers={"User-Agent": "miair-next"},
            )
            resp = conn.getresponse()
            if resp.status != 200:
                raise RuntimeError(f"下载失败: HTTP {resp.status} {resp.reason}")
            with open(dest, "wb") as out:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
        finally:
            conn.close()

    def _download_sync(self) -> None:
        os.makedirs(self.bin_dir, exist_ok=True)
        fd, archive_path = tempfile.mkstemp(prefix="soloist-", suffix=".tar.gz")
        os.close(fd)
        try:
            self._download_with_retry(self.info.download_url, archive_path)
            tmp_bin = self.path + ".download"
            with tarfile.open(archive_path, "r:gz") as tar:
                member = None
                for m in tar.getmembers():
                    if m.isfile() and m.name.rstrip("/").split("/")[-1] == "soloist":
                        member = m
                        break
                if member is None:
                    raise RuntimeError("压缩包中未找到 soloist 可执行文件")
                src = tar.extractfile(member)
                if src is None:
                    raise RuntimeError("无法读取压缩包中的 soloist")
                with open(tmp_bin, "wb") as out:
                    shutil.copyfileobj(src, out)
            os.chmod(tmp_bin, 0o755)
            os.replace(tmp_bin, self.path)
        finally:
            try:
                os.remove(archive_path)
            except OSError:
                pass


class SoloistProcess:
    """Soloist 进程管理与输出监控"""

    def __init__(
        self,
        binary: SoloistBinary,
        data_dir: str,
        api_key: str,
        device_name: str,
        ws_port: int,
        initial_volume: int = 100,
        env: dict | None = None,
    ):
        self.binary = binary
        self.data_dir = data_dir
        self.api_key = api_key
        self.device_name = device_name
        self.ws_port = ws_port
        self.initial_volume = initial_volume
        # 传递 PulseAudio UNIX Socket 环境变量
        self.env = dict(env) if env else dict(os.environ)
        self.state_dir = os.path.join(data_dir, "spotify", "state")
        self.cache_dir = os.path.join(data_dir, "spotify", "cache")
        self.ready = asyncio.Event()
        self.expiry_days: int | None = None
        self.exit_code: int | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._stopping = False
        self.on_exit: Callable[[int], None] | None = None
        self.on_active_changed: Callable[[bool], None] | None = None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> None:
        os.makedirs(self.state_dir, exist_ok=True)
        os.makedirs(self.cache_dir, exist_ok=True)
        cmd = [
            self.binary.path,
            "-n",
            self.device_name,
            "-k",
            self.api_key,
            "-D",
            self.state_dir,
            "-C",
            self.cache_dir,
            "-i",
            str(self.initial_volume),
            "-w",
            f"127.0.0.1:{self.ws_port}",
        ]
        # 调试开关：MIAIR_SPOTIFY_VERBOSE=1 时开启详细日志
        if os.environ.get("MIAIR_SPOTIFY_VERBOSE", "").lower() in ("1", "true", "yes"):
            cmd.append("-v")
        log.info(
            f"启动 Spotify Soloist: device={self.device_name!r} ws=127.0.0.1:{self.ws_port}"
        )
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=self.env,
        )
        self._reader_task = asyncio.create_task(self._read_output())

    async def _read_output(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            if not text:
                continue
            log.info(f"[soloist] {text}")
            if '"running"' in text or text.endswith(": running"):
                self.ready.set()
            days = parse_expiry_days(text)
            if days is not None:
                self.expiry_days = days
            active = parse_active_device_change(text)
            if active is not None:
                cb = self.on_active_changed
                if cb is not None:
                    try:
                        cb(active)
                    except Exception as e:  # noqa: BLE001
                        log.error(f"处理 Soloist 活动设备回调失败: {e}")
        code = await proc.wait()
        self.exit_code = code
        if not self._stopping:
            log.warning(f"Spotify Soloist 进程退出, code={code}")
            cb = self.on_exit
            if cb is not None:
                try:
                    result = cb(code)
                    if asyncio.iscoroutine(result):
                        asyncio.create_task(result)
                except Exception as e:  # noqa: BLE001
                    log.error(f"处理 Soloist 退出回调失败: {e}")

    async def wait_ready(self, timeout: float = 20.0) -> bool:
        try:
            await asyncio.wait_for(self.ready.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def stop(self) -> None:
        self._stopping = True
        proc = self._proc
        self._proc = None
        if proc is not None and proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=5)
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                    await asyncio.wait_for(proc.wait(), timeout=3)
                except Exception:  # noqa: BLE001
                    pass
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._reader_task = None


def inspect_binary(data_dir: str) -> dict:
    """只读检查二进制状态 (供 API 在接收器未运行时展示)"""
    try:
        binary = SoloistBinary(data_dir)
    except Exception as e:  # noqa: BLE001
        return {
            "installed": False,
            "arch": "",
            "path": "",
            "version": "",
            "build_date": "",
            "expires_on": "",
            "days_left": None,
            "downloading": False,
            "last_error": str(e),
            "download_url": "",
        }
    return binary.info.to_dict()
