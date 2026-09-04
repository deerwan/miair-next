"""服务编排器: 统一启停 DLNA / AirPlay / 小米云服务

移植自 MiAir 的 app.py (MiAir 类), 去除 Web 层耦合:
- 日志配置移至 core.logging
- 进程重启移至 engine.restart
- Web 服务由 FastAPI (main.py) 负责
"""

import asyncio
import logging
import time

from app.engine.airplay.speaker_airplay import AirPlayManager
from app.engine.auth import AuthManager
from app.engine.config import Config
from app.engine.dlna.device_server import DeviceServer
from app.engine.dlna.renderer import DLNARenderer
from app.engine.dlna.ssdp import SSDPServer
from app.engine.restart import _restart_process
from app.engine.speaker import SpeakerManager

log = logging.getLogger("miair")


class Orchestrator:
    """MiAir 服务编排器"""

    def __init__(self, config: Config):
        self.config = config
        self.auth = AuthManager(config)
        # 凭据经三级降级链自动恢复后重建 DLNA/AirPlay 服务的回调
        self.auth.on_credentials_recovered = self._on_auth_recovered
        self.speaker_manager = SpeakerManager(config, self.auth)
        self.renderers: dict[str, DLNARenderer] = {}  # udn -> DLNARenderer
        self._did_to_udn: dict[str, str] = {}  # did -> udn
        self.ssdp_server: SSDPServer | None = None
        self.device_server: DeviceServer | None = None
        self.dlna_running = False
        self.airplay_manager: AirPlayManager | None = None
        self._device_check_task: asyncio.Task | None = None
        # 重启串行化: 防止连续扫码/保存设置触发多个 restart 并发互相踩踏
        self._restart_lock = asyncio.Lock()

    def get_renderer_by_did(self, did: str) -> DLNARenderer | None:
        """根据 DID 获取渲染器"""
        udn = self._did_to_udn.get(did)
        if udn:
            return self.renderers.get(udn)
        return None

    async def get_all_devices(self) -> list[dict]:
        """获取小米账号下所有设备列表"""
        if not self.config.account and not self.config.cookie:
            return []
        try:
            await self.auth.ensure_login()
            devices = await self.auth.get_device_list()
            return devices
        except Exception as e:
            log.warning(f"获取设备列表失败: {e}")
            return []

    async def _periodic_device_check(self):
        """每分钟自主检查设备列表, 连续3次为空且启动超过5分钟才触发重启"""
        start_time = time.time()
        empty_count = 0
        while True:
            await asyncio.sleep(60)

            if not self.config.auto_restart:
                continue
            if not self.config.account and not self.config.cookie:
                continue

            uptime = time.time() - start_time
            if uptime < 300:
                continue

            try:
                devices = await self.get_all_devices()
                if not devices:
                    empty_count += 1
                    log.warning(f"定期检查设备列表为空 (连续第 {empty_count} 次)")
                    if empty_count >= 3:
                        log.error("设备列表连续3次为空, 判定为故障, 触发自动重启以恢复服务...")
                        try:
                            asyncio.get_running_loop().call_soon(_restart_process)
                        except RuntimeError:
                            _restart_process()
                else:
                    empty_count = 0
            except Exception as e:
                log.warning(f"定期检查设备列表异常: {e}")

    async def start(self):
        """启动所有服务"""
        log.info("MiAir Next 启动中...")
        log.info(f"主机名: {self.config.hostname}")
        log.info(f"DLNA 端口: {self.config.dlna_port}")

        if (self.config.account or self.config.cookie) and self.config.mi_did:
            await self._start_dlna_services()
        else:
            if not self.config.account and not self.config.cookie:
                log.info("未配置小米账号, 请打开 Web 管理界面进行配置")
            elif not self.config.mi_did:
                log.info("未选择音箱设备, 请打开 Web 管理界面选择设备")

        self._device_check_task = asyncio.create_task(self._periodic_device_check())

    async def _on_auth_recovered(self):
        """凭据由失效自动恢复后的回调: 重建 DLNA/AirPlay 服务

        三级降级链 (尤其账号密码重登) 可能在 DLNA 已停摆期间恢复凭据,
        此时必须重新拉起服务, 否则音箱在手机上仍然不可见。
        """
        if self.dlna_running:
            return
        log.info("检测到凭据已自动恢复, 正在重启 DLNA/AirPlay 服务 ...")
        await self.restart_dlna_services()

    async def _start_dlna_services(self):
        """启动 DLNA 相关服务 (登录、初始化音箱、SSDP、HTTP)"""
        try:
            # 登录小米
            await self.auth.login()

            # 登录失败时再走一次三级凭证降级链 (autoLoginAccount):
            # passToken 换发 → 复用缓存 serviceToken → 账号密码重登。
            # 旧实现在这里直接放弃, 导致 passToken 过期后唯有人工扫码才能恢复服务。
            if not self.auth.is_logged_in():
                log.warning("首次登录失败, 尝试用三级凭证降级链恢复 ...")
                await self.auth.refresh_token(reason="启动恢复")

            # 检查登录状态
            if not self.auth.is_logged_in():
                log.warning("登录失败, 无法启动 DLNA 服务")
                # 仍启动续期任务: 只要持有任一凭证, 三级降级链就会在后续周期
                # 继续尝试恢复, 成功后通过 on_credentials_recovered 回调自动
                # 拉起服务, 无需人工扫码。
                if self.auth._has_credentials():
                    self.auth.start_token_refresh()
                self._clear_runtime()
                return

            # 登录成功后启动 serviceToken 主动续期, 避免运行期过期 (幂等)
            self.auth.start_token_refresh()

            # 获取设备列表, 确保能正常获取新账号的设备
            device_list = await self.auth.get_device_list()
            if not device_list:
                log.warning("未获取到设备列表, 无法启动 DLNA 服务")
                self._clear_runtime()

                if self.config.auto_restart:
                    log.warning("未获取到设备列表, 正在尝试自动重启程序...")
                    asyncio.get_running_loop().call_later(5, _restart_process)
                return

            # 初始化音箱
            await self.speaker_manager.init_speakers()
            if not self.speaker_manager.controllers:
                log.warning("没有可用的音箱, 请检查配置或重新选择设备")
                self.renderers.clear()
                self._did_to_udn.clear()
                return

            # 为每个音箱创建 DLNA 渲染器
            self.ssdp_server = SSDPServer(self.config.hostname, self.config.dlna_port)
            self.device_server = DeviceServer(self.config.hostname, self.config.dlna_port, self.config)

            for did, controller in self.speaker_manager.controllers.items():
                speaker = controller.speaker
                udn = speaker.udn
                friendly_name = speaker.get_dlna_name()

                renderer = DLNARenderer(udn, friendly_name, controller, self.config.default_volume, config=self.config)
                self.renderers[udn] = renderer
                self._did_to_udn[did] = udn

                self.ssdp_server.register_renderer(udn, friendly_name)
                self.device_server.register_renderer(renderer)
                log.info(f"已创建渲染器: {friendly_name} (udn={udn})")

            # 启动 SSDP
            await self.ssdp_server.start()

            # 启动 DLNA HTTP 服务
            await self.device_server.start()

            self.dlna_running = True
            self.config.save()

            # 启动 AirPlay 服务 - 每个音箱一个
            await self._start_airplay_for_speakers()

            log.info(f"MiAir Next 服务启动完成! 共 {len(self.renderers)} 个音箱")
            log.info("手机 DLNA / AirPlay 现在应该能发现这些设备了")

        except Exception as e:
            log.error(f"启动 DLNA 服务失败: {e}")
            self.dlna_running = False
            self._clear_runtime()

    def _clear_runtime(self):
        """清空渲染器和控制器, 避免显示旧设备"""
        self.renderers.clear()
        self._did_to_udn.clear()
        if self.speaker_manager:
            self.speaker_manager.controllers.clear()

    async def _start_airplay_for_speakers(self):
        """为每个音箱启动独立的 AirPlay 接收服务"""
        try:
            # 先停掉上一次的 AirPlay 实例, 避免旧端口 / mDNS 注册泄漏
            if self.airplay_manager:
                await self.airplay_manager.stop()
                self.airplay_manager = None

            if not self.speaker_manager.controllers:
                log.warning("没有可用的音箱, 无法启动 AirPlay 服务")
                return

            self.airplay_manager = AirPlayManager(self.config.hostname, config=self.config)
            await self.airplay_manager.start_for_speakers(self.speaker_manager.controllers)
        except Exception as e:
            log.error(f"启动 AirPlay 服务失败: {e}")

    async def restart_dlna_services(self):
        """重启 DLNA 服务 (用户通过 Web 修改配置后调用)"""
        async with self._restart_lock:
            await self._stop_dlna_services()
            # 先停掉旧的 AirPlay, 避免重启失败时旧实例带着过期音箱残留
            if self.airplay_manager:
                await self.airplay_manager.stop()
                self.airplay_manager = None
            # 清空旧 controllers, 防止 AirPlay 停止回调触发旧 auth 重新登录
            # (旧 controller 持有旧 auth 引用, close() 后回调会触发并发登录)
            if self.speaker_manager:
                self.speaker_manager.controllers.clear()
            # 关闭并重新初始化 auth, 确保账号切换生效
            await self.auth.close()
            self.auth = AuthManager(self.config)
            self.auth.on_credentials_recovered = self._on_auth_recovered
            self.speaker_manager = SpeakerManager(self.config, self.auth)
            # _start_dlna_services 内部已重启 AirPlay (先停旧再启新), 无需再次重启
            await self._start_dlna_services()

    async def _stop_dlna_services(self):
        """停止 DLNA 服务"""
        if self.ssdp_server:
            await self.ssdp_server.stop()
            self.ssdp_server = None
        if self.device_server:
            await self.device_server.stop()
            self.device_server = None
        self.renderers.clear()
        self._did_to_udn.clear()
        self.dlna_running = False

    async def stop(self):
        """停止所有服务"""
        log.info("MiAir Next 正在关闭...")

        if self._device_check_task:
            self._device_check_task.cancel()
            self._device_check_task = None

        await self._stop_dlna_services()
        if self.airplay_manager:
            await self.airplay_manager.stop()
            self.airplay_manager = None
        await self.auth.close()

        log.info("MiAir Next 已关闭")
