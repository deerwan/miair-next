"""PulseAudio 虚拟输出与 PCM 音频采集服务"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable

log = logging.getLogger("miair")

SINK_NAME = "miair_spotify"
SAMPLE_RATE = 44100
CHANNELS = 2
SAMPLE_WIDTH = 2


class PulseAudio:
    def __init__(self, data_dir: str, sink_name: str = SINK_NAME):
        # 运行时文件放置于系统临时目录，避免容器降权后影响宿主数据目录权限
        self.base_dir = os.path.join(tempfile.gettempdir(), "miair-spotify-pulse")
        self.socket_path = os.path.join(self.base_dir, "native")
        self.conf_path = os.path.join(self.base_dir, "pa.conf")
        self.log_path = os.path.join(self.base_dir, "pulse.log")
        self.sink_name = sink_name
        self.monitor_name = f"{sink_name}.monitor"
        self._env = dict(os.environ, PULSE_SERVER=f"unix:{self.socket_path}")
        self._started_by_us = False
        self._capture_task: asyncio.Task | None = None
        self._capturing = False
        self._loop_running = False

    @property
    def child_env(self) -> dict:
        return dict(self._env)

    async def ensure(self) -> None:
        """确保 PulseAudio 可用 (优先复用已有实例)"""
        if await self._pactl_ok():
            log.info("PulseAudio 已可用, 复用现有实例")
            return
        if shutil.which("pulseaudio") is None:
            raise RuntimeError(
                "未找到 pulseaudio 命令。Spotify Soloist 需要 PipeWire/PulseAudio 输出, "
                "请使用官方 Docker 镜像或在宿主机安装 PulseAudio。"
            )
        os.makedirs(self.base_dir, exist_ok=True)
        try:
            if os.geteuid() == 0:
                import pwd

                pw = pwd.getpwnam("pulse")
                os.chown(self.base_dir, pw.pw_uid, pw.pw_gid)
            os.chmod(self.base_dir, 0o700)
        except (KeyError, OSError):
            pass
        try:
            if os.path.exists(self.socket_path):
                os.remove(self.socket_path)
        except OSError:
            pass
        self._prepare_system_runtime()
        with open(self.conf_path, "w", encoding="utf-8") as f:
            f.write(
                "load-module module-native-protocol-unix "
                f"auth-anonymous=1 socket={self.socket_path}\n"
                f"load-module module-null-sink sink_name={self.sink_name} "
                "sink_properties=device.description=MiAirSpotify\n"
                f"set-default-sink {self.sink_name}\n"
            )
        cmd = [
            "pulseaudio",
            "-n",
            "--file",
            self.conf_path,
            "--exit-idle-time=-1",
            "--daemonize=yes",
        ]
        if os.geteuid() == 0:
            cmd += ["--system", "--disallow-exit", "--disallow-module-loading=no"]
        try:
            # 重定向输出至日志文件，防止守护进程阻塞管道
            os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
            with open(self.log_path, "ab") as log_file:
                proc = subprocess.run(
                    cmd,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    timeout=20,
                    env=self._env,
                )
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"启动 PulseAudio 失败: {e}") from e
        if proc.returncode != 0:
            raise RuntimeError(
                f"启动 PulseAudio 失败 (code={proc.returncode}), 详见 {self.log_path}"
            )
        self._started_by_us = True
        for _ in range(50):
            if await self._pactl_ok():
                log.info(f"PulseAudio 已启动: sink={self.sink_name} socket={self.socket_path}")
                return
            await asyncio.sleep(0.2)
        raise RuntimeError("PulseAudio 启动后仍无法连接 (pactl info 超时)")

    def _prepare_system_runtime(self) -> None:
        """准备 system 模式下的运行时目录与权限"""
        if os.geteuid() != 0:
            return
        try:
            import pwd

            pw = pwd.getpwnam("pulse")
            os.makedirs("/run/pulse", exist_ok=True)
            os.chown("/run/pulse", pw.pw_uid, pw.pw_gid)
        except (KeyError, OSError):
            pass

    async def _pactl_ok(self) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                "pactl",
                "info",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=self._env,
            )
            return await proc.wait() == 0
        except Exception:  # noqa: BLE001
            return False

    async def close(self) -> None:
        await self.stop_capture()
        if self._started_by_us:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "pactl",
                    "exit",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    env=self._env,
                )
                await proc.wait()
            except Exception:  # noqa: BLE001
                pass
            self._started_by_us = False

    async def start_capture(self, on_chunk: Callable[[bytes], None]) -> None:
        if self._capturing:
            return
        self._capturing = True
        self._loop_running = True
        self._capture_task = asyncio.create_task(self._capture_loop(on_chunk))
        log.info(f"开始采集 PulseAudio monitor: {self.monitor_name} (s16le/{SAMPLE_RATE}/{CHANNELS}ch)")

    async def stop_capture(self) -> None:
        self._capturing = False
        self._loop_running = False
        if self._capture_task is not None:
            self._capture_task.cancel()
            try:
                await self._capture_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._capture_task = None

    async def _capture_loop(self, on_chunk: Callable[[bytes], None]) -> None:
        cmd = [
            "parec",
            f"--device={self.monitor_name}",
            "--format=s16le",
            f"--rate={SAMPLE_RATE}",
            f"--channels={CHANNELS}",
        ]
        restarts = 0
        while self._loop_running:
            proc = None
            try:
                if restarts:
                    log.warning(f"parec 已重启 {restarts} 次, 继续采集")
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    env=self._env,
                )
                assert proc.stdout is not None
                while self._loop_running:
                    chunk = await proc.stdout.read(4096)
                    if not chunk:
                        break
                    try:
                        on_chunk(chunk)
                    except Exception as e:  # noqa: BLE001
                        log.warning(f"处理 PCM 数据失败: {e}")
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning(f"parec 采集异常: {e}")
            finally:
                if proc is not None and proc.returncode is None:
                    try:
                        proc.terminate()
                        await asyncio.wait_for(proc.wait(), timeout=3)
                    except Exception:  # noqa: BLE001
                        try:
                            proc.kill()
                        except Exception:  # noqa: BLE001
                            pass
            if self._loop_running:
                restarts += 1
                await asyncio.sleep(0.5)
