"""小米账号认证管理"""

import asyncio
import logging
import os
import re
import json
import time

import aiohttp
from miservice import MiAccount, MiIOService, MiNAService

from app.engine.config import Config

log = logging.getLogger("miair")

# ---------- serviceToken 生命周期  ----------
# serviceToken 假定有效期 (小时),  constants.SERVICE_TOKEN_VALID_HOURS
SERVICE_TOKEN_VALID_HOURS = 12
# 主动刷新阈值 (小时): 剩余有效期低于此值才换发
# TOKEN_REFRESH_THRESHOLD_HOURS (即第 9 小时触发, 过期前留足余量)
TOKEN_REFRESH_THRESHOLD_HOURS = 3
# 续期检查间隔 (秒),  TOKEN_REFRESH_INTERVAL_MS
TOKEN_REFRESH_CHECK_INTERVAL = 2 * 3600
# 被动恢复最小间隔 (秒): 401 / "Login failed" 触发的恢复走这个闸门, 对齐
# RELOGIN_MIN_INTERVAL_MS。防止 set_volume/play_url/stop 等并发调用
# 在同一失效事件上各触发一次真实登录 (生产 issue: 短时间多次 serviceLogin
# 被小米风控返回 70016, 且失败的登录会互相踩踏导致永久起不来)。
RELOGIN_MIN_INTERVAL = 60
# 登录成功后的宽限期 (秒): 期间 API 失败判定为临时问题 (冷却容忍),
# 不推送 "登录失效" 通知, 避免网络抖动误报
TRANSIENT_FAILURE_GRACE = 60


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
        # 最近一次登录成功的时间戳: 宽限期内 API 失败按临时问题处理, 不误判失效
        self._last_login_ok_time = 0.0
        # serviceToken 最近换发/载入时间: 供主动续期判断年龄
        self._service_token_issued_at = 0.0
        # 最近一次"被动恢复"尝试时间: 401/Login failed 触发的重登冷却闸门
        self._last_relogin_time = 0.0
        # 凭据由失效恢复为有效时的回调 (由 Orchestrator 注入, 用于重建服务)
        self.on_credentials_recovered = None
        self._refresh_task: asyncio.Task | None = None

    async def login(self):
        """登录小米账号并初始化服务 (串行化, 防并发竞态)"""
        if self._closed:
            log.warning("AuthManager 已关闭, 跳过登录")
            return
        async with self._login_lock:
            # 幂等: 已登录则跳过。扫码热重启 / 设备轮询可能并发触发多次 login,
            # 若每次都换发 serviceToken, 短时间多次 serviceLogin 会被小米风控
            # (返回 70016 登录验证失败), 导致服务起不来。
            if self._logged_in and self.mina_service is not None:
                return
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
                # 若旧 .mi.token 已含 micoapi 缓存 (miservice 以 sid 为 key 保存
                # [ssecurity, serviceToken]), 保留它: 避免预写覆盖导致每次登录都
                # 强制用 passToken 换发。短时间多次 serviceLogin 会被小米风控返回
                # 70016 (生产 issue #1 的真正根因); 保留缓存后, device_list 等
                # 调用可直接复用 serviceToken, 无需换发。
                existing_token = {}
                try:
                    with open(token_home) as f:
                        existing_token = json.load(f)
                except Exception:
                    pass
                new_token = {
                    "userId": token_data["userId"],
                    "passToken": token_data["passToken"],
                    "deviceId": "miair_device",
                }
                if existing_token.get("micoapi"):
                    new_token["micoapi"] = existing_token["micoapi"]
                with open(token_home, "w") as f:
                    json.dump(new_token, f, indent=2)
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
            # 优先复用 .mi.token 中已缓存的 serviceToken (miservice 以 sid 为 key):
            # 直接发 device_list 验证, 命中则零 serviceLogin; 401 时 miservice 的
            # mi_request 内部会自动 relogin (仅换发一次)。避免每次启动/热重启都
            # 强制换发, 短时间多次 serviceLogin 会被小米风控返回 70016, 导致
            # 服务起不来 (生产 issue #1 的真正根因)。
            reused = False
            if self.account.token and self.account.token.get("micoapi"):
                try:
                    await MiNAService(self.account).device_list()
                    reused = True
                except Exception as e:
                    log.warning(f"缓存 serviceToken 失效, 转用 passToken 换发: {e}")
            if reused:
                # 缓存的 serviceToken 年龄未知, 以 .mi.token 文件修改时间近似,
                # 供主动续期判断是否临近过期
                try:
                    issued_at = os.path.getmtime(token_store)
                except OSError:
                    issued_at = time.time()
                self._mark_login_ok(issued_at=issued_at)
                log.info("使用 cookie 登录成功 (复用缓存 serviceToken, 未触发换发)")
            else:
                try:
                    ok = await self.account.login("micoapi")
                    if not ok:
                        # 同 session 登录失败, 可能是 session 状态损坏 / cookie jar 污染。
                        # 参考 xiaomusic _try_fresh_session_and_relogin: 丢弃旧 session,
                        # 用全新 session + 全新 MiAccount 重登一次, 避免"同一个损坏
                        # session 无限重试导致连锁失败"。
                        ok = await self._relogin_with_fresh_session()
                    if ok:
                        self._mark_login_ok()
                        # 小米可能在换发时轮换了 passToken, 同步回写配置避免重启后旧值覆盖
                        self._sync_pass_token_to_config()
                        log.info("使用 cookie 登录成功 (已换发 serviceToken)")
                    else:
                        self._logged_in = False
                        log.error(
                            "Cookie 登录失败 (passToken 可能已过期或被吊销), 请重新扫码"
                        )
                        # 与账密登录失败分支对齐: 推送失效通知, 让用户及时重新扫码,
                        # 而非只能靠翻日志发现服务没起来 (同一事件 1 小时内只推一次)
                        from app.engine.notify import notify_async
                        notify_async(
                            self.config,
                            "login_expired",
                            "[MiAir Next] 小米登录已失效",
                            "Cookie (passToken) 已过期或被吊销, 投送服务无法启动。"
                            "请到管理后台重新扫码登录。",
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
                self._mark_login_ok()
                # 账密登录会签发一份新的 passToken, 回写配置后: 后续续期可走
                # passToken 级别, 且三级链的兜底凭证在重启后依然有效
                self._sync_pass_token_to_config()
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
                # 宽限期内刚登录成功: 判定为临时问题 (网络抖动/风控窗口),
                # 不推送失效通知, 由后续周期检查兜底 (冷却容忍)
                if time.time() - self._last_login_ok_time < TRANSIENT_FAILURE_GRACE:
                    log.warning("device_list 失败发生在登录宽限期内, 暂不判定登录失效")
                    return []
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

    # ---------- 凭证有效性校验 ----------

    @staticmethod
    def _is_login_error(exc: Exception) -> bool:
        """判断异常是否属于「登录失效」(401 / Login failed / 70016)

        MinaHTTPClient 对 401 的判定: 只有登录失效才触发恢复链,
        网络超时等临时问题直接上抛交给调用方处理。
        """
        msg = str(exc)
        # 不按 "401" 字面匹配: 设备 ID / 响应体里的数字可能恰好包含 401,
        # 误判会让普通业务错误也走一遍重登流程。miservice 最终抛出的就是
        # "Error {url}: Login failed", 前三个条件已完全覆盖。
        return (
            "Login failed" in msg
            or "登录验证失败" in msg
            or "Auth error" in msg
        )

    async def validate_token(self) -> bool:
        """真实调用一次云端 API, 校验 serviceToken 是否仍有效

        MinaHTTPClient.validateToken(): 必须直接判定底层响应,
        不能复用 get_device_list() —— 后者把 401 / 网络失败兜底成 [] 返回,
        会让失效的 token 被误判为有效, 造成「刷新链条持续假成功、既不提示
        重登又持续 401」空列表同样代表请求成功, 属有效。
        """
        if self._closed or not self.mina_service or not self.account:
            return False
        try:
            result = await self.mina_service.device_list()
            return result is not None
        except Exception as e:
            log.info(f"serviceToken 校验未通过: {e}")
            return False

    def _has_credentials(self) -> bool:
        """是否持有任一可用于恢复登录态的凭证"""
        return bool(self.config.cookie or (self.config.account and self.config.password))

    # ---------- 三级凭证降级链 (AuthService.refreshToken) ----------

    async def refresh_token(self, reason: str = "定时续期") -> bool:
        """按 passToken → serviceToken → 账号密码 的优先级恢复登录态

        - 级别 1 passToken 换发: 只能换 serviceToken, 不会延长 passToken 自身,
          因此它无法解决 passToken 过期 (生产 issue 的根因);
        - 级别 2 复用缓存 serviceToken: 换发失败但旧 token 仍可用时继续使用,
          避免一次网络抖动就把服务判死;
        - 级别 3 账号密码重登: 唯一能重新签发 passToken 的手段, 是长期免人工
          维护的关键 (autoLoginWithPassword)。

        每级成功后都会真实校验一次, 杜绝「假成功」。

        整体串行化: 定时器续期与 API 401 恢复可能同时发生, 共用一个锁避免
        并发换发 (短时间多次 serviceLogin 会触发小米风控 70016)。
        """
        async with self._login_lock:
            was_logged_in = self._logged_in

            if await self._refresh_by_pass_token():
                log.info(f"登录态已恢复 ({reason}): passToken 换发 serviceToken 成功")
                self._fire_recovered_callback(was_logged_in)
                return True

            if await self._reuse_service_token():
                log.info(f"登录态已恢复 ({reason}): 缓存 serviceToken 仍有效, 直接复用")
                self._fire_recovered_callback(was_logged_in)
                return True

            if await self._login_by_password():
                log.info(f"登录态已恢复 ({reason}): 账号密码重登成功 (已重新签发 passToken)")
                self._fire_recovered_callback(was_logged_in)
                return True

            log.error(
                f"登录态恢复失败 ({reason}): "
                "passToken / serviceToken / 账号密码 三种方式均失败"
            )
            self._notify_login_expired(
                "passToken、缓存 serviceToken 与账号密码三种恢复方式均已失败"
            )
            return False

    def _fire_recovered_callback(self, was_logged_in: bool):
        """凭据从失效恢复为有效时通知上层 (供重建 DLNA/AirPlay 服务)

        AuthManager 只管凭据; 由 Orchestrator 注入回调决定是否重建服务。
        """
        if was_logged_in or self.on_credentials_recovered is None:
            return
        try:
            asyncio.create_task(self._call_recovered_callback())
        except RuntimeError:
            log.warning("凭据恢复回调未启动: 当前无事件循环")

    async def _call_recovered_callback(self):
        try:
            await self.on_credentials_recovered()
        except Exception as e:
            log.warning(f"凭据恢复回调执行异常: {e}")

    async def _refresh_by_pass_token(self) -> bool:
        """级别 1: 用 passToken 换发 serviceToken (换发后强制校验)"""
        credentials = self._load_pass_token()
        if not credentials:
            log.info("无可用 passToken, 跳过 passToken 换发")
            return False
        user_id, pass_token = credentials

        # miservice login() 失败时会删除 .mi.token 文件, 先备份以便失败时还原
        backup = self._backup_token_file()
        session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15, connect=5, sock_read=10)
        )
        try:
            account = MiAccount(session, "", "", token_store=self.config.mi_token_home)
            # 直接注入凭据而非依赖 .mi.token 文件: 文件可能在上一次失败登录中
            # 被 miservice 删除, 仅靠文件加载会导致失败一次后永远换发不了。
            account.token = {
                "userId": user_id,
                "passToken": pass_token,
                "deviceId": "miair_device",
            }
            ok = await account.login("micoapi")
        except Exception as e:
            log.warning(f"passToken 换发异常: {e}")
            ok = False

        if not ok:
            log.warning("passToken 换发失败 (passToken 可能已过期或被吊销), 转入下一级")
            self._restore_token_file(backup)
            await self._close_session_quietly(session)
            return False

        self._install_account(session, account)
        # 换发返回成功 ≠ token 可用, 必须真调一次云端校验
        if not await self.validate_token():
            log.warning("passToken 换发后校验未通过, 判定为无效凭据")
            self._restore_token_file(backup)
            return False

        self._mark_login_ok()
        self._sync_pass_token_to_config()
        return True

    async def _reuse_service_token(self) -> bool:
        """级别 2: 缓存的 serviceToken 仍有效则直接复用 (autoLoginWithToken)"""
        if not (self.account and self.account.token and self.account.token.get("micoapi")):
            return False
        if not await self.validate_token():
            return False
        self._logged_in = True
        self._last_login_ok_time = time.time()
        return True

    async def _login_by_password(self) -> bool:
        """级别 3: 用账号密码重新登录, 重新签发 passToken + serviceToken

        这是唯一能打破「passToken 自身过期」的手段: passToken 换发只换
        serviceToken, 不会延长 passToken; 账密登录会让小米重新下发一份。
        扫码登录的账号若未配置账号密码, 这一级不可用 (此时仍需用户重新扫码)。
        """
        if not (self.config.account and self.config.password):
            log.warning(
                "未配置小米账号密码, 无法用账号密码兜底重登。"
                "扫码登录的账号请在管理后台「账号配置」补填账号密码作为兜底凭证"
            )
            return False

        log.info("passToken 与缓存 serviceToken 均失效, 尝试用账号密码重新登录 ...")
        session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15, connect=5, sock_read=10)
        )
        try:
            account = MiAccount(
                session,
                self.config.account,
                self.config.password,
                token_store=self.config.mi_token_home,
            )
            ok = await account.login("micoapi")
        except Exception as e:
            log.warning(f"账号密码重登异常: {e}")
            ok = False

        if not ok:
            log.warning("账号密码登录失败 (可能需要滑块/短信验证码, 或账号密码已变更)")
            await self._close_session_quietly(session)
            return False

        self._install_account(session, account)
        if not await self.validate_token():
            log.warning("账号密码登录后校验未通过")
            return False

        self._mark_login_ok()
        # 重新签发的 passToken 必须回写配置, 否则重启后仍用旧值
        self._sync_pass_token_to_config()
        return True

    async def handle_token_expired(self) -> bool:
        """401 / Login failed 的统一恢复入口 (handleTokenExpired)

        带 60s 冷却: 同一次失效事件上并发的 set_volume / play_url / stop 只会
        触发一次真实恢复动作, 其余直接跳过, 避免重登风暴触发小米风控 70016。
        """
        elapsed = time.time() - self._last_relogin_time
        if self._last_relogin_time and elapsed < RELOGIN_MIN_INTERVAL:
            log.info(
                f"凭据恢复跳过: 距上次尝试仅 {elapsed:.0f}s "
                f"(冷却 {RELOGIN_MIN_INTERVAL}s, 防止重登风暴)"
            )
            return False
        self._last_relogin_time = time.time()
        return await self.refresh_token(reason="检测到登录失效")

    async def call_api(self, factory, label: str = ""):
        """执行小米云 API, 遇登录失效自动恢复凭据并重试一次

        MinaHTTPClient.doPostRequest()
        401 → onTokenExpired → 刷新成功则重试原请求。

        factory 是无参可调用对象 (返回协程): 重试时需要重新构造调用,
        保证拿到刷新后重建的 mina_service。
        """
        try:
            return await factory()
        except Exception as e:
            if not self._is_login_error(e):
                raise
            log.warning(f"{label or '小米云 API'} 返回登录失效 ({e}), 尝试恢复凭据后重试")
            if not await self.handle_token_expired():
                raise
            return await factory()

    # ---------- 内部工具 ----------

    def _load_pass_token(self) -> tuple[str, str] | None:
        """取得可用的 (userId, passToken): 优先 config.cookie, 回退 .mi.token

        账密登录场景 passToken 可能只落在 .mi.token 里 (config.cookie 为空)。
        """
        token_data = parse_cookie_string(self.config.cookie or "")
        if token_data.get("userId") and token_data.get("passToken"):
            return token_data["userId"], token_data["passToken"]
        try:
            with open(self.config.mi_token_home, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return None
        if data.get("userId") and data.get("passToken"):
            return str(data["userId"]), str(data["passToken"])
        return None

    def _install_account(self, session, account):
        """装载新的 session/account 并重建服务句柄

        调用方全部通过 self.mina_service / self.miio_service 动态访问, 替换后
        对所有持有 auth 引用的 controller 立即生效 (刷新后
        client.updateTokenInfo 的同步语义)。
        """
        old_session = self.session
        self.session = session
        self.account = account
        self.mina_service = MiNAService(account)
        self.miio_service = MiIOService(account)
        if (
            old_session is not None
            and old_session is not session
            and not old_session.closed
        ):
            asyncio.create_task(self._close_session_quietly(old_session))

    def _mark_login_ok(self, issued_at: float | None = None):
        """记录登录/换发成功状态与 serviceToken 过期时间

        issued_at 允许传入: 复用缓存 serviceToken 时真实签发时间未知, 以
        .mi.token 文件修改时间近似, 否则按当前时间计算。
        """
        now = time.time()
        issued = issued_at if issued_at else now
        self._logged_in = True
        self._last_login_ok_time = now
        self._service_token_issued_at = issued
        self.config.token_expires_at = issued + SERVICE_TOKEN_VALID_HOURS * 3600
        try:
            self.config.save()
        except Exception as e:
            log.warning(f"保存 token 过期时间失败: {e}")

    @staticmethod
    async def _close_session_quietly(session):
        try:
            await session.close()
        except Exception:
            pass

    def _notify_login_expired(self, detail: str):
        """推送登录失效通知 (notify 内部同事件 1 小时节流)"""
        try:
            from app.engine.notify import notify_async

            notify_async(
                self.config,
                "login_expired",
                "[MiAir Next] 小米登录已失效",
                f"{detail}。\n请到管理后台重新扫码登录, "
                "或配置小米账号密码让服务自动恢复。",
            )
        except Exception as e:
            log.warning(f"推送登录失效通知失败: {e}")

    # ---------- serviceToken 主动续期 ----------

    def start_token_refresh(self):
        """启动 serviceToken 主动续期任务 (登录成功后调用, 幂等)"""
        if self._closed:
            return
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = asyncio.create_task(self._token_refresh_loop())

    async def stop_token_refresh(self):
        """停止续期任务"""
        if self._refresh_task:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
            self._refresh_task = None

    async def _token_refresh_loop(self):
        """定期检查 serviceToken 剩余有效期, 临近过期时走三级降级链恢复

        AuthService.startTokenRefresh + refreshToken: 每 2 小时
        检查一次, 剩余有效期低于 3 小时才动作; 成功后更新 config.token_expires_at
        落盘, 进程重启也能接着计算剩余时间。

        注意这里**不**以「当前已登录」为前置条件: 一旦 serviceToken 过期且
        尚未被任何调用发现, 定时器仍会持续尝试恢复(账密兜底), 避免像旧实现
        那样因 _logged_in=false 而让续期任务永久空转。
        """
        threshold = TOKEN_REFRESH_THRESHOLD_HOURS * 3600
        try:
            while not self._closed:
                await asyncio.sleep(TOKEN_REFRESH_CHECK_INTERVAL)
                if self._closed or not self._has_credentials():
                    continue  # 无任何凭证时不打扰用户

                remaining = self.config.token_expires_at - time.time()
                if remaining > threshold:
                    continue

                if remaining <= 0:
                    log.info("serviceToken 已过期, 主动恢复登录态 ...")
                else:
                    log.info(
                        f"serviceToken 剩余 {remaining / 3600:.1f}h "
                        f"(低于 {TOKEN_REFRESH_THRESHOLD_HOURS}h 阈值), 主动续期 ..."
                    )
                # 锁由 refresh_token 内部持有 (它与 API 401 恢复共用一把锁),
                # 这里不能再加锁, 否则 asyncio.Lock 不可重入会死锁
                if self._closed or not self._has_credentials():
                    continue
                await self.refresh_token(reason="定时续期")
        except asyncio.CancelledError:
            pass

    def _sync_pass_token_to_config(self):
        """回写轮换后的 passToken 到 config.cookie, 保持凭据始终最新

        miservice 换发成功后会把小米返回的 (可能轮换过的) passToken 更新到
        account.token 并落盘 .mi.token; 但启动路径预写 .mi.token 用的是
        config.cookie。若小米轮换了 passToken 并作废旧值, config.cookie 里的
        旧值会在下次重启时覆盖新值, 导致登录失败。回写后配置始终持有最新凭据。
        """
        token = self.account.token if self.account else None
        if not token:
            return
        new_pass = token.get("passToken")
        if not new_pass:
            return
        current = parse_cookie_string(self.config.cookie or "")
        if current.get("passToken") == new_pass:
            return  # 未轮换, 无需回写
        self.config.cookie = f"userId={token.get('userId', '')}; passToken={new_pass}"
        try:
            self.config.save()
            log.info("检测到 passToken 已轮换, 已同步回写配置")
        except Exception as e:
            log.warning(f"passToken 回写配置失败: {e}")

    def _backup_token_file(self) -> bytes | None:
        """备份 .mi.token 内容, 供续期失败时恢复"""
        try:
            with open(self.config.mi_token_home, "rb") as f:
                return f.read()
        except OSError:
            return None

    def _restore_token_file(self, backup: bytes | None):
        """换发失败后还原 .mi.token 文件

        miservice login() 失败时会把 token 置 None 并删除该文件; 若磁盘上仍
        有一份可能有效的旧 serviceToken, 就不该让它陪葬 —— 还原后进程重启
        仍有机会直接复用。内存态由三级降级链负责重建, 这里只管文件。
        """
        if backup is None:
            return
        token_home = self.config.mi_token_home
        try:
            os.makedirs(os.path.dirname(token_home), exist_ok=True)
            with open(token_home, "wb") as f:
                f.write(backup)
        except OSError as e:
            log.warning(f"恢复 .mi.token 失败: {e}")

    async def close(self):
        """关闭 session"""
        # 置关闭标记: 防止旧实例被 AirPlay 停止回调等触发重新登录 (与新建实例并发竞态)
        self._closed = True
        await self.stop_token_refresh()
        if self.session and not self.session.closed:
            await self.session.close()
        self.session = None
        self.account = None
        self.mina_service = None
        self.miio_service = None
        self._logged_in = False
