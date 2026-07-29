"""小爱音箱控制模块"""

import asyncio
import json
import logging

from app.engine.auth import AuthManager
from app.engine.config import Config, Speaker
from app.engine.const import DEFAULT_AUDIO_ID, NEED_USE_PLAY_MUSIC_API

log = logging.getLogger("miair")


class SpeakerController:
    """单个小爱音箱的控制接口"""

    # 连续登录失败计数（所有实例共享，因为登录状态是全局的）
    _consecutive_login_failures: int = 0
    _LOGIN_FAILURE_RESTART_THRESHOLD = 6  # 连续失败 6 次后触发重启

    def __init__(self, speaker: Speaker, auth: AuthManager):
        self.speaker = speaker
        self.auth = auth
        self._last_volume: int = 50  # 用于 unmute 恢复

    @classmethod
    def _check_and_trigger_restart(cls, config=None):
        """检查连续登录失败次数，达到阈值时触发进程重启"""
        if cls._consecutive_login_failures >= cls._LOGIN_FAILURE_RESTART_THRESHOLD:
            log.error(
                f"连续 {cls._consecutive_login_failures} 次登录失败，正在重启程序以恢复服务..."
            )
            # 重启前推送通知, 让自愈动作对用户可见
            if config is not None:
                from app.engine.notify import notify_async
                notify_async(
                    config,
                    "auto_restart",
                    "[MiAir Next] 服务即将自动重启",
                    f"连续 {cls._consecutive_login_failures} 次登录失败, 服务将自动重启以尝试恢复。"
                    "若重启后仍收到此通知, 请到管理后台重新登录。",
                )
            from app.engine.restart import _restart_process
            try:
                asyncio.get_running_loop().call_soon(_restart_process)
            except RuntimeError:
                _restart_process()

    @property
    def device_id(self) -> str:
        return self.speaker.device_id

    @property
    def did(self) -> str:
        return self.speaker.did

    def _should_use_music_api(self) -> bool:
        if self.speaker.is_compatibility_mode():
            return False
        return True

    async def play_url(self, url: str) -> bool:
        """让音箱播放指定 URL"""
        try:
            await self.auth.ensure_login()
            if self._should_use_music_api():
                ret = await self.auth.mina_service.play_by_music_url(
                    self.device_id, url, audio_id=DEFAULT_AUDIO_ID
                )
                log.info(f"play_by_music_url device_id={self.device_id} ret={ret}")
            else:
                ret = await self.auth.mina_service.play_by_url(self.device_id, url)
                log.info(f"play_by_url device_id={self.device_id} ret={ret}")
            return ret is not None
        except Exception as e:
            log.error(f"play_url 失败: {e}")
            # 检查是否是登录失败的错误
            if "Login failed" in str(e) or "登录验证失败" in str(e):
                log.info("检测到登录失败，尝试重新登录...")
                # 重置登录状态并重新登录
                self.auth._logged_in = False
                await self.auth.login()
                # 重新尝试播放
                try:
                    await self.auth.ensure_login()
                    if self._should_use_music_api():
                        ret = await self.auth.mina_service.play_by_music_url(
                            self.device_id, url, audio_id=DEFAULT_AUDIO_ID
                        )
                    else:
                        ret = await self.auth.mina_service.play_by_url(self.device_id, url)
                    return ret is not None
                except Exception as e2:
                    log.error(f"重新登录后 play_url 仍然失败: {e2}")
                    return False
            return False

    async def pause(self) -> bool:
        """暂停播放"""
        try:
            await self.auth.ensure_login()
            if self._should_use_music_api():
                # 某些使用 play_by_music_url 的设备，调用 pause 后 API 状态不会
                # 正确更新为 paused (status=2)，需改用 stop 来实现暂停语义
                ret = await self.auth.mina_service.player_stop(self.device_id)
                log.info(f"player_stop(as pause) device_id={self.device_id} ret={ret}")
            else:
                ret = await self.auth.mina_service.player_pause(self.device_id)
                log.info(f"player_pause device_id={self.device_id} ret={ret}")
            return True
        except Exception as e:
            log.error(f"pause 失败: {e}")
            # 检查是否是登录失败的错误
            if "Login failed" in str(e) or "登录验证失败" in str(e):
                log.info("检测到登录失败，尝试重新登录...")
                # 重置登录状态并重新登录
                self.auth._logged_in = False
                await self.auth.login()
                # 重新尝试暂停
                try:
                    await self.auth.ensure_login()
                    if self._should_use_music_api():
                        await self.auth.mina_service.player_stop(self.device_id)
                    else:
                        await self.auth.mina_service.player_pause(self.device_id)
                    return True
                except Exception as e2:
                    log.error(f"重新登录后 pause 仍然失败: {e2}")
                    return False
            return False

    async def stop(self) -> bool:
        """停止播放"""
        try:
            await self.auth.ensure_login()
            # 某些型号的小爱音箱在 stop 后仍会残留缓存，
            # 连续调用 stop + pause 可以更彻底地清空播放状态。
            ret = await self.auth.mina_service.player_stop(self.device_id)
            await self.pause()
            log.info(f"player_stop device_id={self.device_id} ret={ret}")
            return True
        except Exception as e:
            log.error(f"stop 失败: {e}")
            # 检查是否是登录失败的错误
            if "Login failed" in str(e) or "登录验证失败" in str(e):
                log.info("检测到登录失败，尝试重新登录...")
                # 重置登录状态并重新登录
                self.auth._logged_in = False
                await self.auth.login()
                # 重新尝试停止
                try:
                    await self.auth.ensure_login()
                    await self.auth.mina_service.player_stop(self.device_id)
                    await self.pause()
                    return True
                except Exception as e2:
                    log.error(f"重新登录后 stop 仍然失败: {e2}")
                    SpeakerController._consecutive_login_failures += 1
                    SpeakerController._check_and_trigger_restart(self.auth.config)
                    return False
            return False

    async def set_volume(self, volume: int) -> bool:
        """设置音量 (0-100)"""
        volume = max(0, min(100, volume))
        try:
            await self.auth.ensure_login()
            await self.auth.mina_service.player_set_volume(self.device_id, volume)
            if volume > 0:
                self._last_volume = volume
            log.info(f"set_volume device_id={self.device_id} volume={volume}")
            return True
        except Exception as e:
            log.error(f"set_volume 失败: {e}")
            # 检查是否是登录失败的错误
            if "Login failed" in str(e) or "登录验证失败" in str(e):
                log.info("检测到登录失败，尝试重新登录...")
                # 重置登录状态并重新登录
                self.auth._logged_in = False
                await self.auth.login()
                # 重新尝试设置音量
                try:
                    await self.auth.ensure_login()
                    await self.auth.mina_service.player_set_volume(self.device_id, volume)
                    if volume > 0:
                        self._last_volume = volume
                    return True
                except Exception as e2:
                    log.error(f"重新登录后 set_volume 仍然失败: {e2}")
                    return False
            return False

    async def get_volume(self) -> int:
        """获取当前音量"""
        try:
            await self.auth.ensure_login()
            status = await self.auth.mina_service.player_get_status(self.device_id)
            info = json.loads(status.get("data", {}).get("info", "{}"))
            volume = int(info.get("volume", 0))
            if volume > 0:
                self._last_volume = volume
            return volume
        except Exception as e:
            log.error(f"get_volume 失败: {e}")
            # 检查是否是登录失败的错误
            if "Login failed" in str(e) or "登录验证失败" in str(e):
                log.info("检测到登录失败，尝试重新登录...")
                # 重置登录状态并重新登录
                self.auth._logged_in = False
                await self.auth.login()
                # 重新尝试获取音量
                try:
                    await self.auth.ensure_login()
                    status = await self.auth.mina_service.player_get_status(self.device_id)
                    info = json.loads(status.get("data", {}).get("info", "{}"))
                    volume = int(info.get("volume", 0))
                    if volume > 0:
                        self._last_volume = volume
                    return volume
                except Exception as e2:
                    log.error(f"重新登录后 get_volume 仍然失败: {e2}")
                    return self._last_volume
            return self._last_volume

    async def get_status(self) -> dict:
        """获取播放状态

        Returns:
            dict: {status: int, volume: int}
            status: 0=stopped, 1=playing, 2=paused
        """
        try:
            await self.auth.ensure_login()
            playing_info = await self.auth.mina_service.player_get_status(
                self.device_id
            )
            
            # 检查 API 响应码。如果 code != 0，说明请求失败（如超时 3012），
            # 此时绝不能返回 status=0，否则会触发“已停止”的错误逻辑导致自动续播误触发。
            if playing_info.get("code") != 0:
                raise Exception(f"Mina API Error: {playing_info}")
                
            data = playing_info.get("data", {})
            info_str = data.get("info")
            if not info_str:
                # 如果没有 info 字段，可能也是某种异常状态，但不代表停止
                raise Exception(f"Mina API response missing 'info': {playing_info}")
                
            info = json.loads(info_str)
            # 获取成功，重置连续登录失败计数
            SpeakerController._consecutive_login_failures = 0
            return {
                "status": info.get("status", 0),
                "volume": int(info.get("volume", 0)),
            }
        except Exception as e:
            # 检查是否是登录失败的错误
            if "Login failed" in str(e) or "登录验证失败" in str(e):
                log.info("检测到登录失败，尝试重新登录...")
                # 重置登录状态并重新登录
                self.auth._logged_in = False
                await self.auth.login()
                # 重新尝试获取状态
                try:
                    await self.auth.ensure_login()
                    playing_info = await self.auth.mina_service.player_get_status(
                        self.device_id
                    )
                    if playing_info.get("code") != 0:
                        raise Exception(f"Mina API Error: {playing_info}")
                    data = playing_info.get("data", {})
                    info_str = data.get("info")
                    if not info_str:
                        raise Exception(f"Mina API response missing 'info': {playing_info}")
                    info = json.loads(info_str)
                    # 重试成功，重置计数
                    SpeakerController._consecutive_login_failures = 0
                    return {
                        "status": info.get("status", 0),
                        "volume": int(info.get("volume", 0)),
                    }
                except Exception as e2:
                    log.error(f"重新登录后 get_status 仍然失败: {e2}")
                    SpeakerController._consecutive_login_failures += 1
                    SpeakerController._check_and_trigger_restart(self.auth.config)
            # 向上抛出异常，让调用者（如 DeviceServer 的轮询任务）捕获并忽略本次轮询
            raise Exception(f"get_status 失败: {e}")


class SpeakerManager:
    """管理所有音箱实例"""

    def __init__(self, config: Config, auth: AuthManager):
        self.config = config
        self.auth = auth
        self.controllers: dict[str, SpeakerController] = {}

    async def init_speakers(self):
        """初始化所有音箱控制器"""
        # 从云端获取设备详细信息
        await self.auth.update_speakers_info()

        # 为每个启用的音箱创建控制器
        for speaker in self.config.get_enabled_speakers():
            if speaker.device_id:
                self.controllers[speaker.did] = SpeakerController(speaker, self.auth)
                log.info(
                    f"已初始化音箱控制器: {speaker.get_dlna_name()} (did={speaker.did})"
                )
            else:
                log.warning(
                    f"音箱 did={speaker.did} 未找到 device_id，跳过"
                )

    def get_controller(self, did: str) -> SpeakerController | None:
        """根据 DID 获取控制器"""
        return self.controllers.get(did)

    def get_controller_by_udn(self, udn: str) -> SpeakerController | None:
        """根据 UDN 获取控制器"""
        for controller in self.controllers.values():
            if controller.speaker.udn == udn:
                return controller
        return None
