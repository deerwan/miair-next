from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field

log = logging.getLogger("miair")


@dataclass
class Speaker:
    """单个小爱音箱的配置"""

    did: str = ""
    device_id: str = ""
    hardware: str = ""
    name: str = ""
    dlna_name: str = ""
    udn: str = ""
    use_music_api: bool = False
    compatibility_mode: bool | None = None
    enabled: bool = True

    # 不支持无损格式的音箱型号列表
    _NON_LOSSLESS_HARDWARE = {"L05B", "L05C", "LX06", "L16A"}

    def is_compatibility_mode(self) -> bool:
        if self.compatibility_mode is not None:
            return self.compatibility_mode
        # 默认：如果 hardware 在 NEED_USE_PLAY_MUSIC_API 中，则为 False，否则为 True
        from app.engine.const import NEED_USE_PLAY_MUSIC_API
        for model in NEED_USE_PLAY_MUSIC_API:
            if model in self.hardware:
                return False
        return True

    def get_dlna_name(self) -> str:
        return self.dlna_name or self.name or f"XiaoAI-{self.did}"

    def ensure_udn(self):
        if not self.udn:
            self.udn = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"miair-{self.did}"))

    def needs_audio_conversion(self, content_type: str = "") -> bool:
        """检查是否需要转换音频格式
        
        部分音箱不支持无损格式，需要转换为 WAV (PCM) 播放
        """
        if self.hardware not in self._NON_LOSSLESS_HARDWARE:
            return False
        
        # 已经是可直接播放的格式则不需要转换
        if content_type:
            ct = content_type.lower()
            if "mp3" in ct or "mpeg" in ct or "wav" in ct or "x-wav" in ct:
                return False
        
        return True


@dataclass
class Config:
    """MiAir 全局配置"""

    account: str = ""
    password: str = ""
    mi_did: str = ""
    cookie: str = ""
    hostname: str = ""
    dlna_port: int = 8200
    web_port: int = 8300
    conf_path: str = "conf"
    verbose: bool = False
    # log_file 不存储，动态计算相对于 conf_path
    proxy_enabled: bool = False
    auto_play_on_set_uri: bool = False
    # 实验性功能：打断后续播
    auto_resume_on_interrupt: bool = False
    resume_delay_seconds: int = 5
    # 默认音量 (1-100)
    default_volume: int = 38
    # 实验性功能：跟随设备当前音量
    follow_device_volume: bool = True
    # 通用默认封面 (DLNA 路线)：用户在 Web 配置的封面图片 URL（可选）。
    # 为空时回退到后端内置默认封面 (/default-cover)，保证开箱即用。
    default_cover_url: str = ""
    # 小米云默认封面 audioID (play_by_music_url 路线)：小米曲库中某首歌的 audioID，
    # 用于触屏/带屏音箱显示封面与歌词。为空时回退到内置默认值 (const.DEFAULT_AUDIO_ID)。
    default_audio_id: str = ""
    # 触屏歌词匹配 (DLNA 路线)：每首歌按投送元数据中的歌名/歌手搜小米曲库，
    # 命中则用真实 audioID 使触屏音箱显示该曲歌词与封面；未命中回退 default_audio_id。
    touchscreen_lyrics: bool = False
    # 语音控制
    enable_voice_control: bool = False
    # 自动重启（当登录失败或服务异常时）
    auto_restart: bool = False
    # serviceToken 过期时间戳(秒), 0 表示未知/未续期
    token_expires_at: float = 0.0
    voice_poll_interval: int = 1
    # 通知推送 (登录过期/失败提醒): notify_type 单选 (""=关闭 / feishu / wxpusher)
    notify_type: str = ""
    notify_feishu_webhook: str = ""
    notify_feishu_secret: str = ""
    notify_wxpusher_spt: str = ""
    speakers: dict = field(default_factory=dict)

    # 保存配置的线程锁（类级别共享）
    _save_lock = threading.Lock()

    @property
    def log_file(self) -> str:
        """日志文件路径，动态计算"""
        return os.path.join(self.conf_path, "miair.log")

    def __post_init__(self):
        self.resume_delay_seconds = max(1, min(15, self.resume_delay_seconds))
        if not self.account:
            self.account = os.getenv("MI_USER", "")
        if not self.password:
            self.password = os.getenv("MI_PASS", "")
        if not self.mi_did:
            self.mi_did = os.getenv("MI_DID", "")
        # MIAIR_HOSTNAME 环境变量优先级最高 (覆盖持久化配置),
        # 用于纠正多网卡/容器下自动探测到的错误 IP 导致 AirPlay 不可连接
        env_hostname = os.getenv("MIAIR_HOSTNAME", "")
        if env_hostname:
            self.hostname = env_hostname
            if self._is_loopback_hostname(env_hostname):
                log.warning(
                    f"MIAIR_HOSTNAME={env_hostname} 是回环地址, 音箱无法访问该地址 (拉流将无声)!"
                )
        # 持久化配置中的回环地址 (历史版本探测失败写入的 127.0.0.1) 不可信,
        # 必须重新探测纠正, 否则音箱永远拉不到音频流且无法自愈
        if not self.hostname or self._is_loopback_hostname(self.hostname):
            detected = self._detect_local_ip()
            if self.hostname and self.hostname != detected:
                log.warning(
                    f"配置中的主机名 {self.hostname} 是回环地址, 已重新探测为 {detected}"
                    " (如仍不正确, 请设置 MIAIR_HOSTNAME=<本机局域网IP>)"
                )
            self.hostname = detected

    @staticmethod
    def _is_loopback_hostname(hostname: str) -> bool:
        """判断 hostname 是否为回环地址 (音箱无法访问)。"""
        import ipaddress

        if not hostname:
            return False
        if hostname.lower() in ("localhost", "::1"):
            return True
        try:
            return ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            return hostname.startswith("127.")

    @staticmethod
    def _is_lan_ip(ip: str) -> bool:
        """判断是否为可用的局域网 IP。

        排除: 回环、链路本地 (169.254)、公网、常见虚拟网卡网段
        (docker0 172.17.0.0/16 / tailscale-CGNAT 100.64.0.0/10)。
        """
        import ipaddress

        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        if addr.is_loopback or addr.is_link_local:
            return False
        if not addr.is_private:
            return False
        for net in ("172.17.0.0/16", "100.64.0.0/10"):
            if addr in ipaddress.ip_network(net):
                return False
        return True

    @staticmethod
    def _score_local_ipv4(ip: str, source: str = "") -> int:
        """对候选 IP 评分: 私网段优先 (192.168 > 10 > 172.16-31), 虚拟网卡接口名与 default-route 来源降权。"""
        import ipaddress

        addr = ipaddress.ip_address(ip)
        source_lower = source.lower()
        score = 0
        if ip.startswith("192.168."):
            score += 50
        elif ip.startswith("10."):
            score += 40
        elif ipaddress.ip_address("172.16.0.0") <= addr <= ipaddress.ip_address("172.31.255.255"):
            score += 30
        virtual_markers = (
            "docker", "veth", "br-", "vmware", "virtualbox", "vbox", "hyper-v",
            "wsl", "tailscale", "zerotier", "vpn", "tun", "tap", "wg", "ppp",
            "utun", "awdl", "llw", "anpi",
        )
        if any(marker in source_lower for marker in virtual_markers):
            score -= 120
        if ip.startswith("172.17.") or ip.startswith("172.18."):
            score -= 30
        if source_lower == "default-route":
            score -= 20
        return score

    @staticmethod
    def _detect_local_ip() -> str:
        """自动检测本机局域网 IP。

        多候选 + 评分排序:
        1. 枚举真实网卡地址: `ip -o -4 addr show scope global` (Linux/OpenWrt 最可靠,
           可拿接口名降权 Docker/VPN 虚拟网卡), `ifconfig` 兜底 (macOS)
        2. UDP connect 探测 (默认路由 + 私网段广播) 仅作最后候选
        3. 评分取最高; 公网/回环/链路本地一律丢弃
        解决软路由形态 (WAN 口直接获得公网 IP) 下 UDP connect 全部返回 WAN 公网 IP
        导致探测失败、fallback 127.0.0.1 使 AirPlay/DLNA 拉流无声的问题
        (见 issue #23/#24)。
        """
        import re
        import socket
        import subprocess

        candidates: dict[str, str] = {}

        # 1) 枚举真实网卡地址
        for cmd in (["ip", "-o", "-4", "addr", "show", "scope", "global"], ["ifconfig"]):
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=2, check=False)
            except (OSError, subprocess.SubprocessError):
                continue
            for line in proc.stdout.splitlines():
                ip = ""
                iface = ""
                if cmd[0] == "ip":
                    parts = line.split()
                    if len(parts) >= 4:
                        ip = parts[3].split("/", 1)[0]
                        iface = parts[1]
                else:
                    if line and not line[0].isspace():
                        iface = line.split(":", 1)[0].strip()
                    match = re.search(r"\binet\s+(\d{1,3}(?:\.\d{1,3}){3})", line)
                    if match:
                        ip = match.group(1)
                if ip and Config._is_lan_ip(ip):
                    candidates.setdefault(ip, f"iface:{iface}" if iface else "iface")

        # 2) UDP connect 探测作为最后候选 (default-route 来源, 评分降权)
        for target in ("8.8.8.8", "10.255.255.255", "192.168.255.255", "172.31.255.255"):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect((target, 80))
                ip = s.getsockname()[0]
                s.close()
                if Config._is_lan_ip(ip):
                    candidates.setdefault(ip, "default-route")
            except Exception:
                pass

        if candidates:
            scored = sorted(candidates.items(), key=lambda kv: Config._score_local_ipv4(kv[0], kv[1]), reverse=True)
            selected = scored[0][0]
            summary = ", ".join(f"{ip}({src})" for ip, src in scored[:6])
            log.info(f"自动检测局域网 IP: {selected}; 候选: {summary}")
            return selected

        log.warning(
            "未能检测到有效的局域网 IP, 回退使用 127.0.0.1。"
            "音箱将无法拉取音频流 (AirPlay/DLNA 无声)! "
            "请通过环境变量 MIAIR_HOSTNAME=<本机局域网IP> 指定正确地址。"
        )
        return "127.0.0.1"

    @property
    def mi_token_home(self) -> str:
        return os.path.join(self.conf_path, ".mi.token")

    @property
    def config_file(self) -> str:
        return os.path.join(self.conf_path, "config.json")

    def get_did_list(self) -> list[str]:
        """获取配置的设备 DID 列表"""
        if not self.mi_did:
            return []
        return [d.strip() for d in self.mi_did.split(",") if d.strip()]

    def get_speaker(self, did: str) -> Speaker:
        """获取或创建指定 DID 的 Speaker 配置"""
        if did not in self.speakers:
            self.speakers[did] = Speaker(did=did)
        speaker = self.speakers[did]
        if isinstance(speaker, dict):
            speaker = Speaker(**speaker)
            self.speakers[did] = speaker
        speaker.ensure_udn()
        return speaker

    def get_enabled_speakers(self) -> list[Speaker]:
        """获取所有已启用的 Speaker"""
        result = []
        for did in self.get_did_list():
            speaker = self.get_speaker(did)
            if speaker.enabled:
                result.append(speaker)
        return result

    def save(self):
        """保存配置到文件（线程安全，原子写入防损坏）"""
        with self._save_lock:
            os.makedirs(self.conf_path, exist_ok=True)
            data = asdict(self)
            # 回环 hostname 不持久化, 避免固化污染 (下次启动将重新探测)
            if self._is_loopback_hostname(data.get("hostname", "")):
                data["hostname"] = ""
            # speakers 中的 Speaker 对象转为 dict
            speakers_data = {}
            for did, speaker in data.get("speakers", {}).items():
                if isinstance(speaker, Speaker):
                    speakers_data[did] = asdict(speaker)
                else:
                    speakers_data[did] = speaker
            data["speakers"] = speakers_data

            tmp_file = self.config_file + ".tmp"
            try:
                with open(tmp_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(tmp_file, self.config_file)  # 原子替换
            except Exception:
                # 清理临时文件，避免残留
                if os.path.exists(tmp_file):
                    os.remove(tmp_file)
                raise
            self._restrict_sensitive_file_perms()

    def _restrict_sensitive_file_perms(self) -> None:
        """收紧敏感文件权限 (尽力而为): config.json 含明文账号密码 / Cookie,
        .mi.token 含 serviceToken。miservice 的 save_token() 会删除重建
        .mi.token 导致权限回落, 故每次保存配置时都重新收紧;
        Windows / 特殊文件系统上 chmod 可能无效, 忽略失败。
        """
        for path in (self.config_file, self.mi_token_home):
            try:
                if os.path.exists(path):
                    os.chmod(path, 0o600)
            except OSError as e:
                log.warning(f"收紧文件权限失败 {path}: {e}")

    @classmethod
    def load(cls, conf_path: str = "conf") -> "Config":
        """从文件加载配置"""
        # 标准化路径为绝对路径，确保无论从哪里运行都能正确定位
        if not os.path.isabs(conf_path):
            conf_path = os.path.abspath(conf_path)
        config_file = os.path.join(conf_path, "config.json")
        if os.path.exists(config_file):
            with open(config_file, encoding="utf-8") as f:
                data = json.load(f)
            data["conf_path"] = conf_path
            # 过滤掉不存在的字段，避免TypeError
            import inspect
            sig = inspect.signature(cls.__init__)
            valid_params = list(sig.parameters.keys())
            filtered_data = {k: v for k, v in data.items() if k in valid_params}
            return cls(**filtered_data)
        return cls(conf_path=conf_path)
