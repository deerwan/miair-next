"""每个音箱对应的 AirPlay 接收器

为每个小爱音箱创建一个独立的 AirPlay 接收服务，
手机连接后音频直接转发到对应音箱播放。
"""

import asyncio
import logging
import time

from zeroconf import Zeroconf, IPVersion

from app.engine.airplay.server import AirPlayServer
from app.engine.speaker import SpeakerController

log = logging.getLogger("miair")


class SpeakerAirPlay:
    """单个音箱的 AirPlay 接收器包装"""

    def __init__(self, hostname: str, controller: SpeakerController,
                 shared_zeroconf: Zeroconf | None = None, config=None):
        self.hostname = hostname
        self.controller = controller
        self.speaker = controller.speaker
        self.shared_zeroconf = shared_zeroconf
        self.config = config
        self.airplay_server: AirPlayServer | None = None
        self._loop: asyncio.AbstractEventLoop | None = None  # 保存事件循环引用
        # AirPlay 状态轮询（打断续播）
        self._stream_url: str = ""  # 当前播放的 HTTP 流 URL
        self._airplay_active: bool = False  # AirPlay 是否活跃
        self._poll_task: asyncio.Task | None = None  # 状态轮询任务
        self._play_grace_until: float = 0.0  # play 后宽限期
        self._session_audio_id: str | None = None  # 当前会话匹配到的曲库 audioID (续播复用)
        self._backfill_task: asyncio.Task | None = None  # 歌词迟到补发任务

    @property
    def device_name(self) -> str:
        """动态读取当前 AirPlay 广播名。

        设备名应始终跟随 dlna_name（用户可在 Web 后台修改），
        而非在构造时冻结。借鉴 airplay2-receiver 的 update_service 思路：
        名称是广播的属性，可在运行时被刷新后重新注册生效。
        """
        return self.speaker.get_dlna_name()

    async def rename(self, new_name: str):
        """重命名后重建该音箱的 AirPlay 广播。

        先停掉旧广播（mdns.stop 会显式 unregister_service 注销旧记录，
        避免 iOS 缓存里残留旧名），再按新名重新注册。
        相比单纯改 friendly_name，这样才能让手机搜到的名字真正更新。
        """
        log.info(f"AirPlay 重命名: {self.device_name} -> {new_name}")
        await self.stop()
        # 更新底层 dlna_name，使后续 device_name 动态属性返回新值
        self.speaker.dlna_name = new_name
        await self.start()

    async def start(self):
        """启动该音箱的 AirPlay 服务"""
        try:
            # 保存当前事件循环，以便在 RTSP 线程中安全调用异步函数
            self._loop = asyncio.get_running_loop()

            self.airplay_server = AirPlayServer(
                self.hostname, self.device_name, self.shared_zeroconf,
                speaker_hardware=self.speaker.hardware
            )

            # 设置回调：直接播放到这个音箱
            self.airplay_server.on_play_start = self._on_play_start
            self.airplay_server.on_play_stop = self._on_play_stop
            self.airplay_server.on_volume_change = self._on_volume_change

            await self.airplay_server.start()
            log.info(f"音箱 {self.device_name} 的 AirPlay 服务已启动，端口: {self.airplay_server.rtsp_port}")
        except Exception as e:
            log.error(f"启动音箱 {self.device_name} 的 AirPlay 服务失败: {e}")
            raise

    async def stop(self):
        """停止该音箱的 AirPlay 服务"""
        self._airplay_active = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        if self.airplay_server:
            await self.airplay_server.stop()
            self.airplay_server = None
            log.info(f"音箱 {self.device_name} 的 AirPlay 服务已停止")

    def _on_play_start(self, stream_url: str):
        """AirPlay 开始播放 - 直接推送到这个音箱

        注意: 这个回调从 RTSP 线程调用，不在 asyncio 事件循环中。
        必须使用 run_coroutine_threadsafe 安全调度异步任务。
        """
        log.info(f"AirPlay 音频推送到 {self.device_name}: {stream_url}")
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._play_on_speaker(stream_url), self._loop)
        else:
            log.warning(f"AirPlay: 事件循环未运行，无法播放到 {self.device_name}")

    async def _play_on_speaker(self, stream_url: str):
        """在对应音箱上播放"""
        try:
            self._stream_url = stream_url
            self._airplay_active = True
            self._play_grace_until = time.time() + 10.0  # 10秒宽限期
            audio_id = await self._resolve_audio_id()
            success = await self.controller.play_url(stream_url, audio_id)
            if success:
                log.info(f"AirPlay 音频已在 {self.device_name} 开始播放: {stream_url}")
                self._start_poll()
                # 快速路径未拿到 audioID 时启动歌词监听（网易云等发送端元数据迟到，切歌也靠它更新）
                if audio_id is None and self.config and getattr(self.config, "touchscreen_lyrics", False):
                    self._backfill_task = asyncio.create_task(self._lyrics_watcher(stream_url))
                if self.config:
                    default_vol = getattr(self.config, 'default_volume', 0)
                    follow_dev_vol = getattr(self.config, 'follow_device_volume', False)
                    if follow_dev_vol:
                        try:
                            current_vol = await self.controller.get_volume()
                            if self.airplay_server:
                                self.airplay_server._last_volume_db = self._vol_pct_to_db(current_vol)
                            log.info(f"AirPlay 已跟随设备当前音量到 {self.device_name}: {current_vol}%")
                        except Exception as e:
                            log.error(f"AirPlay 获取当前音量失败: {e}")
                    elif default_vol > 0:
                        await asyncio.sleep(0.5)
                        await self.controller.set_volume(default_vol)
                        if self.airplay_server:
                            self.airplay_server._last_volume_db = self._vol_pct_to_db(default_vol)
                        log.info(f"AirPlay 已应用默认音量到 {self.device_name}: {default_vol}%")
            else:
                log.warning(f"AirPlay 音频在 {self.device_name} 播放失败")
        except Exception as e:
            log.error(f"AirPlay 播放到 {self.device_name} 失败: {e}")

    async def _resolve_audio_id(self) -> str | None:
        """触屏歌词匹配: 用 DAAP 元数据搜小米曲库换 audioID

        发送端可能在 RECORD 前后才通过 SET_PARAMETER 下发 DAAP 元数据，
        此处短等最多 1 秒；超时也照常播放（无歌词）不阻塞。
        未命中返回 None，play_url 会回退到小米云默认封面。
        """
        if not (self.config and getattr(self.config, "touchscreen_lyrics", False)):
            return None
        meta = None
        deadline = time.time() + 1.0  # 短宽限等待 DAAP 元数据
        while time.time() < deadline:
            if self.airplay_server:
                meta = self.airplay_server.daap_meta
            if meta and meta.get("title"):
                break
            await asyncio.sleep(0.05)
        if not meta or not meta.get("title"):
            log.info(f"AirPlay 未收到 DAAP 歌曲元数据，跳过歌词匹配 ({self.device_name})")
            return None
        try:
            audio_id = await asyncio.wait_for(
                self.controller.search_audio_id(
                    meta.get("title", ""), meta.get("artist", ""),
                    fuzzy_fallback=False),  # DAAP 首条元数据可能是歌词行，不接受模糊回退
                timeout=5.0,
            )
        except asyncio.TimeoutError:
            log.warning(f"AirPlay 歌词匹配超时 ({self.device_name})")
            return None
        except Exception as e:
            log.warning(f"AirPlay 歌词匹配失败: {e}")
            return None
        if audio_id:
            self._session_audio_id = audio_id
            log.info(f"AirPlay 歌词匹配命中: {meta['title']} -> audioID={audio_id}")
        else:
            log.info(f"AirPlay 歌词匹配未命中: {meta.get('title')} - {meta.get('artist')}")
        return audio_id or None

    async def _lyrics_watcher(self, stream_url: str):
        """歌词会话监听：跟踪 DAAP 到达事件，切歌（含切回上一首）时补发播放指令

        网易云等发送端在播放后几秒才下发元数据（且滚动歌词也走同一通道），
        切歌/切回时新元数据同样迟到。因此基于不去重的到达事件逐个处理：
        - 未搜过的标题搜曲库（精确匹配，歌词行不会误命中）；
        - minm 是歌词行的发送端（如 Apple Music 真歌名拼在 artist 里），
          额外尝试 derived 候选（从 artist 提取）；
        - derived 候选按 songloft 的方式允许模糊回退首条结果（曲库无
          原版时至少能显示翻唱版的歌词，如周杰伦版权不在小米曲库）；
          minm 标题保持严格，否则滚动歌词行每行都会"命中"不同的歌，
          导致反复重发播放指令；
        - 已命中过的歌名缓存在 matched，切回上一首时发送端重发旧歌名，
          直接用缓存 audioID 补发，无需再搜。
        任务随会话存活，停止播放时取消。
        """
        tried: set[str] = set()       # 搜过未命中的标题（歌词行等），不重复搜索
        matched: dict[str, str] = {}  # 歌名 -> 命中的 audioID，切回上一首时复用
        # 制作名单行（网易云/Apple Music/QQ都会发），不是歌名，不搜
        credit_prefixes = ("作词", "作詞", "作曲", "编曲", "編曲", "演唱", "词：", "詞：", "曲：")
        last_seq = 0
        try:
            while self._airplay_active and self._stream_url == stream_url:
                await asyncio.sleep(1.0)
                if not self._airplay_active or self._stream_url != stream_url:
                    return
                events = list(self.airplay_server.daap_events) if self.airplay_server else []
                new_events = [e for e in events if e[0] > last_seq]
                if not new_events:
                    continue
                last_seq = new_events[-1][0]
                for _, meta in new_events:
                    title = meta.get("title", "")
                    if not title:
                        continue
                    # 候选歌名: minm 标题（非制作名单行）+ derived（从 artist 提取）
                    titles = [] if title.startswith(credit_prefixes) else [title]
                    derived = meta.get("derived") or ""
                    if derived and derived != title:
                        titles.append(derived)
                    hit_id = hit_title = ""
                    for cand in titles:
                        # 缓存命中（如歌名重复到达 = 切回上一首）：与当前不同则补发
                        audio_id = matched.get(cand)
                        if audio_id:
                            hit_id, hit_title = audio_id, cand
                            break
                        if cand in tried:
                            continue
                        tried.add(cand)
                        try:
                            audio_id = await asyncio.wait_for(
                                self.controller.search_audio_id(
                                    cand, meta.get("artist", ""),
                                    # derived 是真歌名，按 songloft 方式允许回退首条；
                                    # minm 可能是歌词行，模糊命中会误判切歌，保持严格
                                    fuzzy_fallback=(bool(derived) and cand == derived)),
                                timeout=5.0,
                            )
                        except Exception:
                            continue
                        if audio_id:
                            matched[cand] = audio_id
                            hit_id, hit_title = audio_id, cand
                            break
                    if not hit_id or hit_id == self._session_audio_id:
                        continue  # 未命中或与当前歌词同曲，无需重发
                    if not self._airplay_active or self._stream_url != stream_url:
                        return
                    self._session_audio_id = hit_id
                    self._play_grace_until = time.time() + 10.0
                    log.info(f"AirPlay 歌词切换: {hit_title} -> audioID={hit_id} ({self.device_name})")
                    await self._resend_play(stream_url, hit_id)
        except asyncio.CancelledError:
            pass

    async def _resend_play(self, stream_url: str, audio_id: str | None):
        """重发播放指令并确认音箱真的回来拉流（声音优先于歌词）

        补发后音箱会断开旧连接重拉新 URL，最多等 5 秒确认有拉流连接；
        若没有（偶尔音箱不重新拉取导致"有歌词封面但无声"），
        换新 sid 重试一次防缓存；仍失败不阻塞，打断续播轮询会兜底。
        """
        await self.controller.play_url(stream_url, audio_id)
        for _ in range(10):
            await asyncio.sleep(0.5)
            if not self._airplay_active or self._stream_url != stream_url:
                return
            if self.airplay_server and self.airplay_server.stream_has_clients:
                return
        if not self._airplay_active or self._stream_url != stream_url:
            return
        base_url = stream_url.split('?')[0]
        fresh_url = f"{base_url}?sid={int(time.time())}"
        log.warning(f"AirPlay 补发后音箱未拉流，换新 URL 重试: {fresh_url} ({self.device_name})")
        await self.controller.play_url(fresh_url, audio_id)

    @staticmethod
    def _vol_pct_to_db(volume: int) -> float:
        """音箱百分比 → AirPlay dB 值（线性映射逆运算）

        iOS 步骤 1-16 线性映射: -28.125 dB ~ 0 dB → 音箱 6% ~ 100%
        逆向: dB = (volume - 6) / 94 * 28.125 - 28.125
        """
        import math
        if volume <= 0:
            return -144.0
        if volume >= 100:
            return 0.0
        if volume <= 6:
            return -28.125
        return (volume - 6) / 94.0 * 28.125 - 28.125

    def _on_play_stop(self):
        """AirPlay 停止播放

        注意: 这个回调从 RTSP 线程调用，不在 asyncio 事件循环中。
        """
        log.info(f"AirPlay 停止播放到 {self.device_name}")
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._stop_speaker(), self._loop)
        else:
            log.warning(f"AirPlay: 事件循环未运行，无法停止 {self.device_name}")

    async def _stop_speaker(self):
        """停止音箱播放"""
        self._airplay_active = False
        self._stream_url = ""
        self._session_audio_id = None
        if self._backfill_task:
            self._backfill_task.cancel()
            self._backfill_task = None
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        try:
            await self.controller.stop()
        except Exception as e:
            pass

    def _start_poll(self):
        """启动 AirPlay 状态轮询任务（仅在 auto_resume_on_interrupt 开启时）"""
        # 未开启自动续播则不启动轮询，避免无意义的 API 调用
        if self.config and not getattr(self.config, 'auto_resume_on_interrupt', False):
            return
        if self._poll_task and not self._poll_task.done():
            return  # 已在运行
        self._poll_task = asyncio.create_task(self._poll_speaker_state())

    async def _poll_speaker_state(self):
        """轮询音箱状态，检测打断并自动续播

        当音箱被语音唤醒打断（status 从 playing 变成 stopped）时，
        只要 AirPlay 音频流仍然活跃，就自动重新 play_url 恢复播放。
        """
        try:
            while self._airplay_active and self._stream_url:
                await asyncio.sleep(3)  # 3秒轮询一次
                if not self._airplay_active or not self._stream_url:
                    break

                # 检查 AirPlay 音频流是否还在输出
                if self.airplay_server and not self.airplay_server.is_playing:
                    break

                # 宽限期内不轮询
                if time.time() < self._play_grace_until:
                    continue

                try:
                    status = await asyncio.wait_for(
                        self.controller.get_status(), timeout=10
                    )
                    speaker_status = status.get("status", 0)
                    # status: 0=stopped, 1=playing, 2=paused
                    if speaker_status == 1:
                        continue  # 正在播放，一切正常

                    # 音箱不在播放状态，但 AirPlay 流还在 → 被打断了
                    log.info(
                        f"[{self.device_name}] AirPlay 检测到播放中断 "
                        f"(speaker_status={speaker_status})，"
                        f"等待后自动续播..."
                    )
                    # 等待打断结束（如语音回复完毕）
                    resume_delay = 5
                    if self.config:
                        resume_delay = getattr(self.config, 'resume_delay_seconds', 5)
                    await asyncio.sleep(resume_delay)

                    # 再次检查 AirPlay 是否仍然活跃
                    if not self._airplay_active or not self._stream_url:
                        break
                    if self.airplay_server and not self.airplay_server.is_playing:
                        break

                    # 重新播放（使用新 URL 防止音箱缓存旧响应，复用本会话的 audioID）
                    base_url = self._stream_url.split('?')[0]
                    fresh_url = f"{base_url}?sid={int(time.time())}"
                    log.info(f"[{self.device_name}] AirPlay 自动续播: {fresh_url}")
                    self._play_grace_until = time.time() + 10.0
                    success = await self.controller.play_url(fresh_url, self._session_audio_id)
                    if success:
                        log.info(f"[{self.device_name}] AirPlay 续播成功")
                    else:
                        log.warning(f"[{self.device_name}] AirPlay 续播失败")

                except Exception as e:
                    pass
        except asyncio.CancelledError:
            pass
        except Exception as e:
            pass

    def _on_volume_change(self, vol_db: float):
        """处理音量改变

        注意: 这个回调从 RTSP 线程调用，不在 asyncio 事件循环中。

        iOS 步骤 1-16 线性映射 dB → 音箱百分比:
          步骤 0 (静音) → 0%
          步骤 1 (-28.125 dB) → 6%
          步骤 8 (-15.0 dB)  → 50%
          步骤 16 (0 dB)     → 100%
        """
        if vol_db <= -144:
            volume = 0
        elif vol_db >= 0:
            volume = 100
        else:
            # 线性映射: -28.125 dB ~ 0 dB → 6% ~ 100%
            volume = int(6 + (vol_db + 28.125) / 28.125 * 94)
            volume = max(0, min(100, volume))
            if volume == 0 and vol_db > -144:
                volume = 1

        log.info(f"AirPlay 音量同步到 {self.device_name}: {vol_db} dB -> {volume}%")
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self.controller.set_volume(volume), self._loop)


class AirPlayManager:
    """管理所有音箱的 AirPlay 接收器"""

    def __init__(self, hostname: str, config=None):
        self.hostname = hostname
        self.config = config
        self.speaker_airplays: dict[str, SpeakerAirPlay] = {}  # did -> SpeakerAirPlay
        self._shared_zeroconf: Zeroconf | None = None

    async def start_for_speakers(self, controllers: dict[str, SpeakerController]):
        """为所有音箱启动 AirPlay 服务"""
        # 创建一个共享的 Zeroconf 实例
        if not self._shared_zeroconf:
            self._shared_zeroconf = Zeroconf(ip_version=IPVersion.All)
            log.info("创建共享 Zeroconf 实例用于所有音箱")

        for did, controller in controllers.items():
            if did in self.speaker_airplays:
                # 已经存在，跳过
                continue

            try:
                speaker_airplay = SpeakerAirPlay(
                    self.hostname, controller, self._shared_zeroconf,
                    config=self.config
                )
                await speaker_airplay.start()
                self.speaker_airplays[did] = speaker_airplay
            except Exception as e:
                log.error(f"为音箱 {controller.speaker.get_dlna_name()} 启动 AirPlay 失败: {e}")

        log.info(f"共启动了 {len(self.speaker_airplays)} 个音箱的 AirPlay 服务")

    async def stop(self):
        """停止所有 AirPlay 服务"""
        for did, speaker_airplay in list(self.speaker_airplays.items()):
            try:
                await speaker_airplay.stop()
            except Exception as e:
                log.error(f"停止音箱 AirPlay 失败: {e}")
        self.speaker_airplays.clear()

        # 关闭共享的 zeroconf
        if self._shared_zeroconf:
            try:
                self._shared_zeroconf.close()
                log.info("共享 Zeroconf 已关闭")
            except Exception as e:
                log.error(f"关闭 Zeroconf 失败: {e}")
            self._shared_zeroconf = None

        log.info("所有 AirPlay 服务已停止")

    async def restart_for_speakers(self, controllers: dict[str, SpeakerController]):
        """重新为音箱启动 AirPlay 服务"""
        await self.stop()
        await self.start_for_speakers(controllers)
