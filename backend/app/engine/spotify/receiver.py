"""Spotify Connect (Soloist) 接收器"""

from __future__ import annotations

import asyncio
import logging
import socket
import time

from app.core.config import get_settings
from app.engine.audio_stream import AudioStreamServer
from app.engine.speaker import SpeakerController
from app.engine.spotify.pulse import PulseAudio
from app.engine.spotify.soloist import (
    SoloistBinary,
    SoloistProcess,
    inspect_binary,
)
from app.engine.spotify.ws_client import SpotifyWsClient, parse_entity

log = logging.getLogger("miair")

STOP_DEBOUNCE_SECONDS = 3.0
PLAY_GRACE_SECONDS = 10.0
INTERRUPT_POLL_SECONDS = 5.0
# 音箱离线连续失败判定阈值
SPEAKER_OFFLINE_FAILURES = 3
# 音箱在线检查轮询周期倍率
SPEAKER_ONLINE_CHECK_TICKS = 1


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _fresh_stream_url(url: str) -> str:
    """附加时间戳防止音箱拉取旧缓存"""
    if not url:
        return url
    return f"{url.split('?')[0]}?sid={int(time.time())}"


class SpotifyReceiver:
    def __init__(self, config, controller: SpeakerController, hostname: str):
        self.config = config
        self.controller = controller
        self.speaker = controller.speaker
        self.hostname = hostname
        self.data_dir = get_settings().data_dir

        self._stream = AudioStreamServer(
            hostname, 0, audio_format="wav", label="Spotify", route_prefix="spotify"
        )
        self._pulse = PulseAudio(self.data_dir)
        self._binary: SoloistBinary | None = None
        self._proc: SoloistProcess | None = None
        self._ws: SpotifyWsClient | None = None

        self._running = False
        self._paired = False
        self._is_active = False
        self._last_cluster_status = "idle"
        self._playback_status = "idle"
        self._track: dict | None = None
        self._volume: int | None = None
        self._start_volume_applied = False
        self._volume_boosted = False
        self._audio_id = ""
        self._speaker_active = False
        self._speaker_offline = False
        self._speaker_status_failures = 0
        self._online_check_tick = 0
        self.on_target_offline = None
        self._last_push_at = 0.0
        self._stream_url = ""
        self._ws_port = 0
        self._last_error = ""
        self._expiry_days: int | None = None
        self._binary_info = inspect_binary(self.data_dir)

        self._lifecycle_lock = asyncio.Lock()
        self._play_lock = asyncio.Lock()
        self._play_task: asyncio.Task | None = None
        self._stop_task: asyncio.Task | None = None
        self._poll_task: asyncio.Task | None = None
        self._lyrics_task: asyncio.Task | None = None
        self._restart_task: asyncio.Task | None = None
        self._update_task: asyncio.Task | None = None
        self._active_poll_task: asyncio.Task | None = None
        self._updating = False

    @property
    def device_name(self) -> str:
        name = (getattr(self.config, "spotify_device_name", "") or "").strip()
        return name or self.speaker.get_dlna_name()

    @property
    def running(self) -> bool:
        return self._running

    def status(self) -> dict:
        return {
            "enabled": bool(getattr(self.config, "spotify_enabled", False)),
            "running": self._running,
            "paired": self._paired,
            "active": self._is_active,
            "offline": self._speaker_offline,
            "device_name": self.device_name,
            "target_did": self.speaker.did,
            "target_name": self.speaker.get_dlna_name(),
            "ws_port": self._ws_port,
            "stream_url": self._stream_url,
            "playback": self._playback_status,
            "track": self._track,
            "volume": self._volume,
            "binary": (
                self._binary.info.to_dict() if self._binary is not None else self._binary_info
            ),
            "expires_in_days": self._expiry_days,
            "last_error": self._last_error,
        }

    def pair_hint(self) -> dict:
        return {
            "ok": True,
            "device_name": self.device_name,
            "message": (
                f"请打开手机 Spotify App → 设备列表 → 选择「{self.device_name}」完成配对；"
                "配对成功后凭据会保存在数据目录, 重启自动恢复。"
            ),
        }

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._running:
                return
            self._last_error = ""
            if not (getattr(self.config, "spotify_api_key", "") or "").strip():
                self._last_error = "未配置 Spotify Soloist API Key"
                raise RuntimeError(self._last_error)
            try:
                self._binary = SoloistBinary(self.data_dir)
                info = await self._binary.ensure()
                self._binary_info = info.to_dict()

                await self._stream.start()
                self._stream_url = self._stream.stream_url
                log.info(f"Spotify 音频流服务器: {self._stream_url}")

                await self._pulse.ensure()
                await self._pulse.start_capture(self._on_pcm)

                self._ws_port = _free_port()
                self._proc = SoloistProcess(
                    self._binary,
                    self.data_dir,
                    self.config.spotify_api_key.strip(),
                    self.device_name,
                    self._ws_port,
                    env=self._pulse.child_env,
                )
                self._proc.on_exit = self._on_soloist_exit
                self._proc.on_active_changed = self._on_soloist_active_changed
                await self._proc.start()
                if not await self._proc.wait_ready(timeout=20):
                    log.warning("Spotify Soloist 启动日志未就绪, 继续尝试连接 WebSocket")

                self._ws = SpotifyWsClient(self._ws_port, self._on_ws_event)
                await self._ws.start()
                for _ in range(30):
                    if self._ws.connected.is_set():
                        break
                    await asyncio.sleep(0.1)
                await self._ws.send("get_auth_state")
                await self._ws.send("get_state")

                self._running = True
                self._start_volume_applied = False
                self._last_cluster_status = "idle"
                self._expiry_days = self._proc.expiry_days
                # 周期轮询活动设备状态
                self._active_poll_task = asyncio.create_task(self._poll_active_state())
                log.info(
                    f"Spotify 接收器已启动: device={self.device_name!r} "
                    f"→ 音箱 {self.speaker.get_dlna_name()!r}"
                )
            except Exception as e:  # noqa: BLE001
                self._last_error = str(e)
                log.error(f"启动 Spotify 接收器失败: {e}")
                await self._cleanup()
                raise

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            self._running = False
            self._paired = False
            self._is_active = False
            self._last_cluster_status = "idle"
            play_in_flight = self._play_task is not None and not self._play_task.done()
            await self._stop_speaker()
            await self._cleanup()
            if play_in_flight:
                # 若推流请求在取消瞬间下发，执行停止音箱兜底
                try:
                    await self.controller.stop()
                except Exception as e:  # noqa: BLE001
                    log.debug(f"停止音箱兜底失败: {e}")
            log.info("Spotify 接收器已停止")

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    def start_update(self) -> bool:
        """启动后台异步更新任务"""
        if self._updating:
            return False
        if self._update_task is not None and not self._update_task.done():
            return False
        self._update_task = asyncio.create_task(self.request_update())
        return True

    async def request_update(self) -> bool:
        """执行更新 Soloist 二进制并重启服务"""
        if self._updating:
            return False
        self._updating = True
        try:
            await self.update_binary()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            self._last_error = f"更新 Soloist 失败: {e}"
            log.error(self._last_error)
        finally:
            self._updating = False
        return True

    async def update_binary(self) -> None:
        """强制重新下载 Soloist (用于构建过期或手动更新)"""
        try:
            if self._binary is None:
                self._binary = SoloistBinary(self.data_dir)
            await self._binary.ensure(force=True)
            self._binary_info = self._binary.info.to_dict()
            if self._running:
                await self.restart()
        except Exception as e:  # noqa: BLE001
            self._last_error = f"更新 Soloist 失败: {e}"
            log.error(self._last_error)
            raise

    async def _cleanup(self) -> None:
        self._cancel_tasks()
        if self._ws is not None:
            await self._ws.stop()
            self._ws = None
        if self._proc is not None:
            await self._proc.stop()
            self._proc = None
        await self._pulse.stop_capture()
        await self._pulse.close()
        try:
            await self._stream.stop()
        except Exception:  # noqa: BLE001
            pass
        self._stream_url = ""

    def _cancel_tasks(self) -> None:
        current = asyncio.current_task()
        for attr in (
            "_play_task",
            "_stop_task",
            "_poll_task",
            "_lyrics_task",
            "_restart_task",
            "_update_task",
            "_active_poll_task",
        ):
            task = getattr(self, attr)
            if task is None:
                continue
            # 避免在重启流程中取消当前正在执行的任务
            if task is not current and not task.done():
                task.cancel()
            if task is not current:
                setattr(self, attr, None)

    def _on_pcm(self, chunk: bytes) -> None:
        self._stream.write_pcm(chunk)

    async def _do_play(self) -> None:
        if (
            not self._is_active
            or self._speaker_offline
            or self._playback_status not in ("playing", "buffering")
        ):
            return
        async with self._play_lock:
            if not self._running or self._speaker_active:
                return
            audio_id = await self._resolve_audio_id()
            if self._playback_status not in ("playing", "buffering"):
                return
            await self._prepare_start_volume()
            await self._apply_start_volume()
            self._stream.start_streaming()
            url = self._stream.stream_url
            self._stream_url = url
            ok = await self.controller.play_url(url, audio_id or None)
            if not ok:
                self._stream.stop_streaming()
                await self._restore_idle_volume()
                log.warning("Spotify 音频推送到音箱失败, 等待状态轮询重试")
                return
            self._speaker_active = True
            self._last_push_at = time.time()
            self._audio_id = audio_id
            log.info(
                f"Spotify 开始在 {self.speaker.get_dlna_name()!r} 播放: "
                f"{(self._track or {}).get('title', '未知曲目')}"
            )
            # 推流下发期间若播放状态已变更为暂停或停止，补发停止指令
            if self._playback_status not in ("playing", "buffering"):
                log.info("Spotify 在推流期间已暂停/停止, 立即停止音箱")
                await self._stop_speaker()
                # 补停期间若再次恢复播放，重置任务引用并重新触发推流
                if self._playback_status in ("playing", "buffering"):
                    log.info("补停期间恢复播放, 重新调度播放任务")
                    self._play_task = None
                    self._request_play()
                return
            self._start_interrupt_poll()

    async def _stop_speaker(self) -> None:
        self._cancel_interrupt_poll()
        if not self._speaker_active:
            await self._restore_idle_volume()
            return
        self._speaker_active = False
        try:
            await self.controller.stop()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning(f"停止音箱失败: {e}")
        finally:
            try:
                self._stream.stop_streaming()
            except Exception:  # noqa: BLE001
                pass
            await self._restore_idle_volume()

    async def _restore_idle_volume(self) -> None:
        """将音箱硬件音量还原为配置的默认音量"""
        if not self._volume_boosted:
            return
        self._volume_boosted = False
        volume = int(getattr(self.config, "default_volume", 0) or 0)
        if volume <= 0:
            return
        try:
            ok = await self.controller.set_volume(max(1, min(100, volume)))
            if ok:
                log.info(f"Spotify 停止播放, 音箱音量已恢复默认: {volume}%")
            else:
                log.warning("恢复默认音量失败: 设备无响应")
        except Exception as e:  # noqa: BLE001
            log.warning(f"恢复默认音量失败: {e}")

    async def _prepare_start_volume(self) -> None:
        """开播初始化：设置 Spotify 软件音量为默认音量"""
        if self._start_volume_applied:
            return
        target = int(getattr(self.config, "default_volume", 0) or 0)
        if target <= 0:
            return
        target = max(1, min(100, target))
        if self._ws is not None:
            ok = await self._ws.send("set_volume", volume=target)
            log.info(f"Spotify 开播音量设为默认音量: {target}% (发送成功={ok})")
            self._volume = target
        self._start_volume_applied = True

    async def _apply_start_volume(self) -> None:
        """开播时将音箱硬件音量置为 100%，由客户端控制软件输出音量"""
        if not self._start_volume_applied:
            return
        try:
            await asyncio.sleep(0.3)
            await self.controller.set_volume(100)
            self._volume_boosted = True
            log.info("Spotify 音箱硬件音量已置 100%, 之后跟随 Spotify App 音量")
        except Exception as e:  # noqa: BLE001
            log.warning(f"设置音箱硬件音量失败: {e}")

    async def _resolve_audio_id(self) -> str:
        """触屏歌词/封面: 用 Soloist 元数据搜小米曲库换 audioID"""
        if not getattr(self.config, "touchscreen_lyrics", False):
            return ""
        track = self._track or {}
        title = (track.get("title") or "").strip()
        if not title:
            return ""
        try:
            return await asyncio.wait_for(
                self.controller.search_audio_id(
                    title, track.get("artist", ""), fuzzy_fallback=False
                ),
                timeout=4.0,
            )
        except Exception:  # noqa: BLE001
            return ""

    async def _on_ws_event(self, data: dict) -> None:
        event_type = data.get("type")
        if event_type == "auth_state":
            prev_active = self._is_active
            self._paired = bool(data.get("logged_in"))
            self._is_active = bool(data.get("is_active")) and not self._speaker_offline
            if (
                self._paired != bool(data.get("logged_in"))
                or self._is_active != bool(data.get("is_active"))
            ):
                log.info(
                    f"Spotify 配对状态: logged_in={self._paired}, "
                    f"is_active={self._is_active}, device={data.get('device_name')}"
                )
            if not self._paired:
                # 凭据失效或退出登录时停止推流
                if self._speaker_active:
                    log.info("Spotify 已退出登录, 停止向音箱推流")
                    self._request_stop(0.5)
            elif prev_active and not self._is_active and self._speaker_active:
                # 播放设备转移至其他端时停止推流
                log.info("Spotify 播放已转移到其它设备, 停止向音箱推流")
                self._request_stop(1.0)
            elif not prev_active and self._is_active:
                # 切换为本机活动设备时，恢复当前播放
                log.info(
                    f"Spotify 重新成为活动设备, 最近播放状态={self._last_cluster_status}"
                )
                if self._last_cluster_status in ("playing", "buffering"):
                    self._playback_status = self._last_cluster_status
                    self._request_play()
                if self._ws is not None:
                    asyncio.create_task(self._ws.send("get_state"))
        elif event_type == "playback_state":
            # 仅在收到确切激活信号时置为活动状态，避免历史状态覆盖
            if data.get("is_active") is True and not self._speaker_offline:
                self._is_active = True
            await self._apply_playback(
                data.get("status"), data.get("item"), data.get("volume")
            )
        elif event_type == "track_changed":
            item = parse_entity(data.get("item") or data.get("entity"))
            if item:
                if self._is_active:
                    await self._handle_track(item)
                else:
                    self._track = item
        elif event_type == "playback_changed":
            await self._apply_playback(data.get("status"), None, None)
        elif event_type == "device_changed":
            # 处理播放设备切换通知
            is_active = data.get("is_active")
            if isinstance(is_active, bool):
                prev_active = self._is_active
                self._is_active = is_active and not self._speaker_offline
                if not self._is_active:
                    self._playback_status = "idle"
                    if self._speaker_active:
                        log.info("Spotify 活动设备已变更, 停止向音箱推流")
                        self._request_stop(1.0)
                elif not prev_active:
                    # 从其他设备切回本机时补偿触发推流
                    log.info(
                        f"Spotify 切换为本机活动设备, 最近播放状态={self._last_cluster_status}"
                    )
                    if self._last_cluster_status in ("playing", "buffering"):
                        self._playback_status = self._last_cluster_status
                        self._request_play()
                    if self._ws is not None:
                        asyncio.create_task(self._ws.send("get_state"))
        elif event_type == "volume_changed":
            volume = data.get("volume")
            if isinstance(volume, int):
                self._volume = volume

    async def _apply_playback(self, status: str | None, item, volume) -> None:
        if status:
            self._last_cluster_status = status
        if item is not None:
            parsed = parse_entity(item)
            if parsed:
                self._track = parsed
        if isinstance(volume, int):
            self._volume = volume
        if not self._is_active:
            # 仅当本机为当前活动播放设备时才向音箱推流
            self._playback_status = "idle"
            if self._speaker_active:
                self._request_stop(0.5)
            return
        if status:
            self._playback_status = status
        if status in ("playing", "buffering"):
            self._request_play()
        elif status in ("paused", "idle", "stopped"):
            self._request_stop(STOP_DEBOUNCE_SECONDS)

    async def _handle_track(self, item: dict) -> None:
        self._track = item
        if self._playback_status in ("playing", "buffering"):
            self._request_play()
        if self._speaker_active and getattr(self.config, "touchscreen_lyrics", False):
            self._schedule_lyrics_refresh()

    def _request_play(self) -> None:
        if not self._is_active or self._speaker_offline:
            return
        if self._stop_task is not None and not self._stop_task.done():
            self._stop_task.cancel()
            self._stop_task = None
        if self._play_task is not None and not self._play_task.done():
            return
        self._play_task = asyncio.create_task(self._do_play())

    def _request_stop(self, delay: float) -> None:
        if self._stop_task is not None and not self._stop_task.done():
            return
        if not self._speaker_active:
            return

        async def run() -> None:
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                return
            if self._playback_status in ("playing", "buffering"):
                return
            await self._stop_speaker()

        self._stop_task = asyncio.create_task(run())

    def _schedule_lyrics_refresh(self) -> None:
        if self._lyrics_task is not None and not self._lyrics_task.done():
            return
        self._lyrics_task = asyncio.create_task(self._refresh_audio_id())

    async def _refresh_audio_id(self) -> None:
        audio_id = await self._resolve_audio_id()
        if not audio_id or audio_id == self._audio_id:
            return
        if not self._speaker_active or self._playback_status not in ("playing", "buffering"):
            return
        self._audio_id = audio_id
        url = _fresh_stream_url(self._stream_url)
        self._stream_url = url
        log.info(
            f"Spotify 触屏歌词切换: {(self._track or {}).get('title', '')} "
            f"→ audioID={audio_id}"
        )
        ok = await self.controller.play_url(url, audio_id)
        if ok:
            self._last_push_at = time.time()

    def _start_interrupt_poll(self) -> None:
        """启动推流中断检测轮询"""
        self._cancel_interrupt_poll()
        self._poll_task = asyncio.create_task(self._poll_interrupts())

    def _cancel_interrupt_poll(self) -> None:
        task = self._poll_task
        self._poll_task = None
        if task is not None and not task.done():
            task.cancel()

    async def _poll_interrupts(self) -> None:
        try:
            while self._speaker_active and self._playback_status in ("playing", "buffering"):
                await asyncio.sleep(INTERRUPT_POLL_SECONDS)
                # 推流宽限期内跳过打断检测
                if time.time() - self._last_push_at < PLAY_GRACE_SECONDS:
                    continue
                if not self._speaker_active:
                    return
                # 优先检测拉流断开，其次采样云端播放状态
                interrupted = await self._probe_stream_lost()
                if not interrupted:
                    interrupted = await self._probe_interrupted()
                if not interrupted:
                    continue
                if not self._is_active or not self._paired:
                    log.info("Spotify 已不是活动设备, 停止本地推流")
                    await self._stop_speaker()
                    return
                if getattr(self.config, "auto_resume_on_interrupt", False):
                    await self._resume_after_interrupt()
                else:
                    await self._pause_after_interrupt()
                    return
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.error(f"Spotify 打断轮询异常退出: {e}")

    def _interrupt_check_seconds(self) -> float:
        """获取中断确认等待时长 (秒)"""
        return float(getattr(self.config, "resume_delay_seconds", 5) or 5)

    async def _probe_stream_lost(self) -> bool:
        """检测音箱拉流连接是否已断开"""
        if self._stream.has_clients:
            return False
        log.info("Spotify 检测到音箱停止拉流, 确认是否被打断 ...")
        await asyncio.sleep(self._interrupt_check_seconds())
        return not self._stream.has_clients

    async def _probe_interrupted(self) -> bool:
        """采样音箱状态检测是否被打断"""
        for attempt in range(2):
            try:
                state = await asyncio.wait_for(self.controller.get_status(), timeout=10)
            except Exception:  # noqa: BLE001
                return False
            if int(state.get("status") or 0) == 1:
                return False
            if attempt == 0:
                if self._ws is not None:
                    await self._ws.send("get_auth_state")
                await asyncio.sleep(self._interrupt_check_seconds())
        return True

    async def _resume_after_interrupt(self) -> None:
        """打断后自动重新推流"""
        log.info("Spotify 检测到音箱播放被打断, 准备自动续播")
        if (
            not self._is_active
            or self._speaker_offline
            or not self._speaker_active
            or self._playback_status not in ("playing", "buffering")
        ):
            return
        url = _fresh_stream_url(self._stream_url)
        self._stream_url = url
        try:
            ok = await self.controller.play_url(url, self._audio_id or None)
        except Exception as e:  # noqa: BLE001
            log.warning(f"Spotify 自动续播失败: {e}")
            ok = False
        self._last_push_at = time.time()
        log.info(f"Spotify 自动续播{'成功' if ok else '失败'}")

    async def _send_pause(self) -> bool:
        """向 Soloist 下发暂停指令"""
        ws = self._ws
        if ws is None:
            return False
        for attempt in range(2):
            if await ws.send("pause"):
                return True
            if attempt == 0:
                try:
                    await asyncio.wait_for(ws.connected.wait(), timeout=10)
                except asyncio.TimeoutError:
                    return False
        return False

    async def _pause_after_interrupt(self) -> None:
        """打断后自动暂停 Spotify 播放并停止音箱推流"""
        if not self._is_active or not self._paired:
            return
        log.info("Spotify 检测到音箱播放被打断, 自动暂停 Spotify 播放")
        prev_status = self._playback_status
        paused = await self._send_pause()
        if not paused:
            log.error("Spotify 自动暂停失败 (WS 未连接或发送失败), 停止音箱推流")
        self._poll_task = None
        async with self._play_lock:
            await self._stop_speaker()
        if paused:
            pending_play = self._play_task is not None and not self._play_task.done()
            if pending_play or (
                self._playback_status in ("playing", "buffering")
                and self._playback_status != prev_status
            ):
                log.info("Spotify 暂停期间已恢复播放, 重新推流")
                self._request_play()
            else:
                self._last_cluster_status = "paused"
                self._playback_status = "paused"

    def _on_soloist_active_changed(self, active: bool) -> None:
        """响应 Soloist 进程日志中的活动设备变更信号"""
        asyncio.create_task(self._handle_active_changed(active))

    async def _poll_active_state(self) -> None:
        """定时查询活动设备状态以校准本地状态"""
        try:
            while self._running:
                await asyncio.sleep(5)
                if self._ws is not None:
                    await self._ws.send("get_auth_state")
                self._online_check_tick += 1
                if self._online_check_tick >= SPEAKER_ONLINE_CHECK_TICKS:
                    self._online_check_tick = 0
                    await self._check_speaker_online()
        except asyncio.CancelledError:
            pass

    async def _check_speaker_online(self) -> None:
        """检查音箱在线状态并同步更新活动状态"""
        try:
            await asyncio.wait_for(self.controller.get_status(), timeout=10)
        except Exception as e:  # noqa: BLE001
            self._speaker_status_failures += 1
            log.debug(
                f"Spotify 音箱在线检查失败 "
                f"({self._speaker_status_failures}/{SPEAKER_OFFLINE_FAILURES}): {e}"
            )
            if self._speaker_status_failures < SPEAKER_OFFLINE_FAILURES:
                return
            self._speaker_status_failures = 0
            if self._speaker_offline:
                return
            self._speaker_offline = True
            log.warning(f"音箱离线, 解除 Spotify 活动设备状态: {e}")
            if self._ws is not None:
                paused = await self._ws.send("pause")
                deactivated = await self._ws.send("deactivate")
                log.info(
                    f"音箱离线: 已请求 Spotify pause={paused}, deactivate={deactivated}"
                )
            self._is_active = False
            self._playback_status = "idle"
            self._last_cluster_status = "idle"
            if self._speaker_active:
                await self._stop_speaker()
            if self.on_target_offline is not None:
                asyncio.create_task(self._notify_target_offline())
            return
        self._speaker_status_failures = 0
        if self._speaker_offline:
            self._speaker_offline = False
            log.info("音箱已恢复在线, 不主动成为活动设备, 等待用户重新选择")
            if self._is_active and self._last_cluster_status in ("playing", "buffering"):
                self._playback_status = self._last_cluster_status
                self._request_play()

    async def _notify_target_offline(self) -> None:
        """触发目标音箱离线回调"""
        cb = self.on_target_offline
        if cb is None:
            return
        try:
            await cb()
        except Exception as e:  # noqa: BLE001
            log.warning(f"通知目标音箱离线失败: {e}")

    async def _handle_active_changed(self, active: bool) -> None:
        if self._speaker_offline:
            self._is_active = False
            if active:
                log.info("音箱离线, 忽略 Soloist 活动设备信号")
            return
        if active == self._is_active:
            return
        self._is_active = active
        if active:
            log.info("Soloist: 已成为活动设备 (日志信号)")
            if self._last_cluster_status in ("playing", "buffering"):
                self._playback_status = self._last_cluster_status
                self._request_play()
            elif self._ws is not None:
                await self._ws.send("get_state")
        else:
            log.info("Soloist: 已不是活动设备 (日志信号), 停止推流")
            self._playback_status = "idle"
            if self._speaker_active:
                self._request_stop(1.0)

    def _on_soloist_exit(self, code: int) -> None:
        if not self._running:
            return
        self._last_error = f"Spotify Soloist 进程退出 (code={code})"
        # Soloist 退出时停止推流并恢复默认音量
        if self._speaker_active or self._volume_boosted:
            asyncio.create_task(self._stop_speaker())
        if code == 10:
            log.warning("Spotify Soloist 构建已过期, 自动下载新版本...")
            self._schedule_restart(0.0, update=True)
        else:
            log.warning("Spotify Soloist 异常退出, 5 秒后重启...")
            self._schedule_restart(5.0, update=False)

    def _schedule_restart(self, delay: float, update: bool) -> None:
        if self._restart_task is not None and not self._restart_task.done():
            return

        async def run() -> None:
            try:
                await asyncio.sleep(delay)
                if not self._running:
                    return
                if update and self._binary is not None:
                    # 下载期间保持任务引用，使上层 stop() 能正常取消重启
                    await self._binary.ensure(force=True)
                    self._binary_info = self._binary.info.to_dict()
                    if not self._running:
                        log.info("Spotify 自动更新期间接收器已被停止, 取消重启")
                        return
                await self.restart()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self._last_error = f"Spotify 自动恢复失败: {e}"
                self._running = False
                self._paired = False
                log.error(self._last_error)
            finally:
                if self._restart_task is asyncio.current_task():
                    self._restart_task = None

        self._restart_task = asyncio.create_task(run())
