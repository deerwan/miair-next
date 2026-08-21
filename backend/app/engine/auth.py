"""小米账号认证管理"""

import asyncio
import logging
import os
import re
import json

import aiohttp
from miservice import MiAccount, MiIOService, MiNAService

from app.engine.config import Config

log = logging.getLogger("miair")


def parse_cookie_string(cookie_str: str) -> dict:
    """解析 cookie 字符串，提取 userId 和 passToken"""
    result = {}
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" in item:
            key, value = item.split("=", 1)
            key = key.strip()
            value = value.strip()
            if key in ("userId", "passToken"):
                result[key] = value
    return result


class AuthManager:
    """管理小米账号认证和设备服务"""

    def __init__(self, config: Config):
        self.config = config
        self.session: aiohttp.ClientSession | None = None
        self.account: MiAccount | None = None
        self.mina_service: MiNAService | None = None
        self.miio_service: MiIOService | None = None
        self._logged_in = False
        # 登录串行化: 防止扫码热重启 / AirPlay 停止回调 / 设备轮询并发触发 login,
        # 共享 aiohttp session 与 .mi.token 文件造成竞态, 导致 serviceLogin 偶发失败
        # (miservice login() 失败时 save_token() 会删除 .mi.token 文件, 引发连锁失败)
        self._login_lock = asyncio.Lock()
        # 关闭标记: close() 后旧实例不再执行登录, 避免旧 AirPlay 停止回调
        # 触发并发登录与新建实例竞争
        self._closed = False

    async def login(self):
        """登录小米账号并初始化服务 (串行化, 防并发竞态)"""
        if self._closed:
            log.warning("AuthManager 已关闭, 跳过登录")
            return
        async with self._login_lock:
            await self._do_login()

    async def _do_login(self):
        """执行登录逻辑 (受 _login_lock 保护)"""
        os.makedirs(self.config.conf_path, exist_ok=True)

        # 创建 aiohttp session（必须设置超时，否则 miservice HTTP 调用可能无限挂起导致卡死）
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15, connect=5, sock_read=10)
            )
        else:
            # 清空 cookie jar, 避免并发/失败后残留污染导致 Login failed
            # (参考 xiaomusic auth.py 的 _patch_account)
            self.session.cookie_jar.clear()

        token_store = self.config.mi_token_home

        # 如果有 cookie，使用 cookie 中的信息创建 MiAccount
        token_data = {}
        if self.config.cookie:
            token_data = parse_cookie_string(self.config.cookie)
        
        # 创建 MiAccount，如果使用 cookie 登录，传入空的账号密码
        if token_data.get("userId") and token_data.get("passToken"):
            # 使用 cookie 登录，传入空的账号密码，避免触发密码登录流程
            #
            # 关键修复: miservice 的 MiAccount 在 __init__ 时会从 token_store (即
            # config.mi_token_home 指向的 .mi.token 文件) 加载 token。但 QR 登录只把
            # "userId=...; passToken=..." 写进了 config.cookie 字符串, .mi.token 文件为空,
            # 导致 miservice 读不到 passToken, login() 时走空 passToken 分支 →
            # 小米返回 code 70016 "登录验证失败"。
            #
            # 因此这里把 cookie 中的 userId/passToken/deviceId 预写入 .mi.token 文件,
            # 让 miservice 的 MiAccount 能正常加载, 并交由 miservice.login() 用 passToken
            # 自动换发 serviceToken 并 save_token() 回盘 (完整自愈链路)。
            token_home = token_store
            try:
                os.makedirs(os.path.dirname(token_home), exist_ok=True)
                with open(token_home, "w") as f:
                    json.dump(
                        {
                            "userId": token_data["userId"],
                            "passToken": token_data["passToken"],
                            "deviceId": "miair_device",
                        },
                        f,
                        indent=2,
                    )
            except Exception as e:
                log.warning(f"预写 .mi.token 失败: {e}")

            self.account = MiAccount(
                self.session,
                "",  # 空账号
                "",  # 空密码
                token_store=token_store,
            )
            log.info("使用 cookie 登录")
        else:
            # 使用账号密码登录
            self.account = MiAccount(
                self.session,
                self.config.account,
                self.config.password,
                token_store=token_store,
            )
            # 确保 token 不为 None，避免后续操作出错
            if not hasattr(self.account, 'token') or self.account.token is None:
                self.account.token = {"deviceId": "miair_device"}

        # 显式调用 login
        # cookie 登录: 已把 userId/passToken 预写入 .mi.token 文件, 这里让 miservice
        # 用 passToken 真正换发 serviceToken (并 save_token 回盘)。不再盲目置
        # _logged_in=True, 避免 "使用 cookie 登录成功" 却实际 Login failed 的伪成功。
        if token_data.get("userId") and token_data.get("passToken"):
            try:
                ok = await self.account.login("micoapi")
                if not ok:
                    # 同 session 登录失败, 可能是 session 状态损坏 / cookie jar 污染。
                    # 参考 xiaomusic _try_fresh_session_and_relogin: 丢弃旧 session,
                    # 用全新 session + 全新 MiAccount 重登一次, 避免"同一个损坏
                    # session 无限重试导致连锁失败"。
                    ok = await self._relogin_with_fresh_session()
                if ok:
                    self._logged_in = True
                    log.info("使用 cookie 登录成功 (已换发 serviceToken)")
                else:
                    self._logged_in = False
                    log.error(
                        "Cookie 登录失败 (passToken 可能已过期或被吊销), 请重新扫码"
                    )
            except Exception as e:
                self._logged_in = False
                log.error(f"Cookie 登录异常: {e}")
                # miservice 在 serviceLogin 失败后会把 account.token 置为 None,
                # 导致下次 login() 时 self.token["deviceId"] 抛 TypeError。这里重建
                # MiAccount 并预置最小 token, 避免 None 残留。
                self.account = MiAccount(
                    self.session, "", "", token_store=token_store
                )
                self.account.token = {"deviceId": "miair_device"}
        else:
            try:
                await self.account.login("micoapi")
                self._logged_in = True
                log.info("小米账号登录成功")
            except Exception as e:
                self._logged_in = False
                # 确保 token 不为 None，避免后续操作出错
                if not hasattr(self.account, 'token') or self.account.token is None:
                    self.account.token = {"deviceId": "miair_device"}
                err_msg = str(e)
                err_code = self._extract_error_code(err_msg)
                if err_code == "87001" or "captcha" in err_msg.lower():
                    log.error(
                        "登录需要验证码! 请在浏览器访问 https://account.xiaomi.com 完成验证后重试，"
                        "或使用 cookie 方式登录"
                    )
                elif err_code == "70016":
                    log.error(
                        "登录验证失败! 可能原因：密码错误、需要关闭二次验证、"
                        "或需要在 https://www.mi.com 完成人机验证。"
                        "建议使用 cookie 方式登录。"
                    )
                elif "userId" in err_msg:
                    log.error(
                        "登录失败(缺少userId)! 小米账号可能需要额外验证。"
                        "请尝试以下方法：\n"
                        "  1. 在浏览器登录 https://account.xiaomi.com 完成验证\n"
                        "  2. 使用 cookie 方式登录（在设置中填入 cookie）\n"
                        "  3. 确保关闭了代理/VPN"
                    )
                else:
                    log.error(f"登录失败: {e}")

                # 推送登录失败通知 (同一事件 1 小时内只推一次)
                from app.engine.notify import notify_async
                notify_async(
                    self.config,
                    "login_failed",
                    "[MiAir Next] 小米账号登录失败",
                    f"错误: {e}\n请到管理后台检查账号配置, 或改用扫码/Cookie 方式登录。",
                )

                # 如果开启了自动重启，则在严重错误时尝试重启程序
                if self.config.auto_restart:
                    log.warning("检测到登录失败，正在尝试自动重启程序以恢复服务...")
                    from app.engine.restart import _restart_process
                    import asyncio
                    try:
                        loop = asyncio.get_running_loop()
                        loop.call_later(5, _restart_process)
                    except RuntimeError:
                        # 如果没有正在运行的 loop，则直接重启
                        _restart_process()

        # 无论是否登录成功，都设置 service (方便后续重试)
        self.mina_service = MiNAService(self.account)
        self.miio_service = MiIOService(self.account)

    async def _relogin_with_fresh_session(self) -> bool:
        """cookie 登录失败时, 丢弃可能损坏的 session, 用全新 session 重登一次

        参考 xiaomusic auth.py 的 _try_fresh_session_and_relogin: 同一个 session
        在并发登录 / 失败后可能残留损坏状态 (cookie jar 污染等), 继续用它重试
        会连锁失败, 换全新 session 后通常能立即恢复。
        """
        try:
            old_session = self.session
            new_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15, connect=5, sock_read=10)
            )
            token_data = parse_cookie_string(self.config.cookie or "")
            if not token_data.get("userId") or not token_data.get("passToken"):
                await new_session.close()
                return False
            account = MiAccount(
                new_session, "", "", token_store=self.config.mi_token_home
            )
            account.token = {
                "userId": token_data["userId"],
                "passToken": token_data["passToken"],
                "deviceId": "miair_device",
            }
            ok = await account.login("micoapi")
            if ok:
                if old_session and not old_session.closed:
                    await old_session.close()
                self.session = new_session
                self.account = account
                self._logged_in = True
                log.info("使用全新 session 重新登录成功")
                return True
            await new_session.close()
            return False
        except Exception as e:
            log.warning(f"全新 session 重新登录失败: {e}")
            return False

    async def ensure_login(self):
        """确保已登录，未登录则尝试登录"""
        if self._closed:
            return
        if self.mina_service is None or not self._logged_in:
            await self.login()

    @staticmethod
    def _extract_error_code(err_msg: str) -> str:
        """从异常消息中提取数字错误码"""
        m = re.search(r'\b(\d{4,6})\b', err_msg)
        return m.group(1) if m else ""

    async def get_device_list(self) -> list[dict]:
        """获取账号下所有设备列表"""
        await self.ensure_login()
        if not self._logged_in:
            log.warning("未成功登录，无法获取设备列表")
            return []
        try:
            devices = await self.mina_service.device_list()
            return devices or []
        except Exception as e:
            log.warning(f"获取设备列表失败: {e}")
            # 可能 token 过期，尝试重新登录
            if self.config.cookie:
                # 并发登录 / 换发 serviceToken 竞态可能导致 device_list 偶发
                # "Login failed", 先串行重新登录一次再重试, 而非直接放弃
                log.info("Cookie 场景 device_list 失败, 重新登录后重试一次...")
                await self.login()
                if self._logged_in:
                    try:
                        devices = await self.mina_service.device_list()
                        if devices:
                            return devices
                    except Exception as e2:
                        log.warning(f"重试 device_list 仍然失败: {e2}")
                log.error(f"Cookie 可能已过期，请重新获取: {e}")
                # 推送登录失效通知 (同一事件 1 小时内只推一次)
                from app.engine.notify import notify_async
                notify_async(
                    self.config,
                    "login_expired",
                    "[MiAir Next] 小米登录已失效",
                    "Cookie 可能已过期, 请到管理后台重新扫码登录。",
                )
                return []
            await self.close()
            await self.login()
            if not self._logged_in:
                return []
            try:
                devices = await self.mina_service.device_list()
                return devices or []
            except Exception as e2:
                log.error(f"重新登录后仍然失败: {e2}")
                return []

    async def update_speakers_info(self):
        """从云端获取设备信息，更新 speakers 配置"""
        devices = await self.get_device_list()
        did_list = self.config.get_did_list()

        for device in devices:
            miot_did = device.get("miotDID", "")
            if miot_did in did_list:
                speaker = self.config.get_speaker(miot_did)
                speaker.device_id = device.get("deviceID", "")
                speaker.hardware = device.get("hardware", "")
                if not speaker.name:
                    speaker.name = device.get("name", "")
                speaker.ensure_udn()
                log.info(
                    f"已更新设备信息: {speaker.name} "
                    f"(did={miot_did}, device_id={speaker.device_id}, "
                    f"hardware={speaker.hardware})"
                )

    def is_logged_in(self) -> bool:
        """是否已成功登录"""
        return self._logged_in

    async def close(self):
        """关闭 session"""
        # 置关闭标记: 防止旧实例被 AirPlay 停止回调等触发重新登录 (与新建实例并发竞态)
        self._closed = True
        if self.session and not self.session.closed:
            await self.session.close()
        self.session = None
        self.account = None
        self.mina_service = None
        self.miio_service = None
        self._logged_in = False
