"""局域网 IP 自动检测相关单元测试（不依赖真实网络拓扑）

覆盖 issue #23/#24 修复的关键逻辑：
- _is_lan_ip 正确区分局域网 IP / 公网 / 回环 / 链路本地 / 虚拟网卡网段
- _score_local_ipv4 评分排序: 192.168 > 10 > 172.16-31, 虚拟网卡与 default-route 降权
- _is_loopback_hostname 识别回环地址
- Config.__post_init__: 持久化配置中的回环 hostname 必须被重新探测纠正,
  MIAIR_HOSTNAME 环境变量保持最高优先级
- Config.save(): 回环 hostname 不持久化, 避免固化污染
"""

import json

from app.engine.config import Config


# ---------------------------------------------------------------------------
# _is_lan_ip
# ---------------------------------------------------------------------------

class TestIsLanIp:
    def test_private_ips(self):
        assert Config._is_lan_ip("192.168.1.10") is True
        assert Config._is_lan_ip("10.0.0.1") is True
        assert Config._is_lan_ip("172.20.1.1") is True

    def test_excluded_ips(self):
        assert Config._is_lan_ip("127.0.0.1") is False          # 回环
        assert Config._is_lan_ip("8.8.8.8") is False            # 公网
        assert Config._is_lan_ip("169.254.1.1") is False        # 链路本地
        assert Config._is_lan_ip("172.17.0.1") is False         # docker0
        assert Config._is_lan_ip("100.64.1.1") is False         # tailscale/CGNAT
        assert Config._is_lan_ip("not-an-ip") is False
        assert Config._is_lan_ip("") is False

    def test_docker_bridge_subnet_demoted_not_excluded(self):
        """172.18 可能是真实 LAN, 不做网段级排除, 依赖 docker 网桥接口名降权"""
        assert Config._is_lan_ip("172.18.0.5") is True
        assert Config._score_local_ipv4("172.18.0.5", "iface:br-abc123") < \
            Config._score_local_ipv4("172.18.0.5", "iface:eth0")


# ---------------------------------------------------------------------------
# _score_local_ipv4
# ---------------------------------------------------------------------------

class TestScoreLocalIpv4:
    def test_segment_priority(self):
        assert Config._score_local_ipv4("192.168.1.10", "iface:eth0") > \
            Config._score_local_ipv4("10.0.0.1", "iface:eth0")
        assert Config._score_local_ipv4("10.0.0.1", "iface:eth0") > \
            Config._score_local_ipv4("172.20.1.1", "iface:eth0")

    def test_virtual_iface_demoted(self):
        normal = Config._score_local_ipv4("192.168.1.10", "iface:eth0")
        docker = Config._score_local_ipv4("192.168.1.10", "iface:docker0")
        tun = Config._score_local_ipv4("192.168.1.10", "iface:utun3")
        assert docker < normal - 100
        assert tun < normal - 100

    def test_docker_subnet_demoted(self):
        normal = Config._score_local_ipv4("192.168.1.10", "iface:eth0")
        docker_subnet = Config._score_local_ipv4("172.17.0.2", "iface:eth0")
        assert docker_subnet < normal

    def test_default_route_demoted(self):
        assert Config._score_local_ipv4("192.168.1.10", "default-route") < \
            Config._score_local_ipv4("192.168.1.10", "iface:eth0")


# ---------------------------------------------------------------------------
# _is_loopback_hostname
# ---------------------------------------------------------------------------

class TestIsLoopbackHostname:
    def test_loopback_forms(self):
        assert Config._is_loopback_hostname("127.0.0.1") is True
        assert Config._is_loopback_hostname("127.9.9.9") is True
        assert Config._is_loopback_hostname("localhost") is True
        assert Config._is_loopback_hostname("::1") is True

    def test_non_loopback(self):
        assert Config._is_loopback_hostname("192.168.1.10") is False
        assert Config._is_loopback_hostname("myhost.local") is False
        assert Config._is_loopback_hostname("") is False


# ---------------------------------------------------------------------------
# _detect_local_ip
# ---------------------------------------------------------------------------

class TestDetectLocalIp:
    def test_returns_valid_result(self):
        """结果必须是局域网 IP 或显式回退 127.0.0.1, 绝不能是其他无效值"""
        ip = Config._detect_local_ip()
        assert ip == "127.0.0.1" or Config._is_lan_ip(ip), f"非法探测结果: {ip}"

    def test_loopback_fallback_is_logged_loopback(self):
        """探测失败回退的 127.0.0.1 必须能被 _is_loopback_hostname 识别 (启动纠正的前提)"""
        assert Config._is_loopback_hostname("127.0.0.1") is True


# ---------------------------------------------------------------------------
# Config.__post_init__: hostname 纠正逻辑
# ---------------------------------------------------------------------------

class TestHostnamePostInit:
    def _detect_fixed(self, monkeypatch, ip: str):
        monkeypatch.setattr(Config, "_detect_local_ip", staticmethod(lambda: ip))

    def test_persisted_loopback_is_corrected(self, monkeypatch):
        """issue #23/#24 核心场景: config.json 固化的 127.0.0.1 必须重新探测纠正"""
        self._detect_fixed(monkeypatch, "192.168.50.99")
        cfg = Config(hostname="127.0.0.1")
        assert cfg.hostname == "192.168.50.99"

    def test_valid_hostname_kept(self, monkeypatch):
        self._detect_fixed(monkeypatch, "192.168.50.99")
        cfg = Config(hostname="192.168.1.5")
        assert cfg.hostname == "192.168.1.5"

    def test_env_overrides_persisted(self, monkeypatch):
        """MIAIR_HOSTNAME 保持最高优先级 (覆盖持久化配置与探测结果)"""
        self._detect_fixed(monkeypatch, "192.168.50.99")
        monkeypatch.setenv("MIAIR_HOSTNAME", "10.0.0.5")
        cfg = Config(hostname="127.0.0.1")
        assert cfg.hostname == "10.0.0.5"

    def test_empty_hostname_triggers_detect(self, monkeypatch):
        self._detect_fixed(monkeypatch, "192.168.50.99")
        cfg = Config(hostname="")
        assert cfg.hostname == "192.168.50.99"


# ---------------------------------------------------------------------------
# Config.save(): 回环 hostname 不持久化
# ---------------------------------------------------------------------------

class TestSaveLoopbackProtection:
    def test_loopback_hostname_not_persisted(self, tmp_path, monkeypatch):
        """探测失败时 hostname=127.0.0.1 不得写入 config.json (防止固化污染)"""
        monkeypatch.setattr(Config, "_detect_local_ip", staticmethod(lambda: "127.0.0.1"))
        cfg = Config(conf_path=str(tmp_path))
        assert cfg.hostname == "127.0.0.1"
        cfg.save()
        with open(tmp_path / "config.json", encoding="utf-8") as f:
            saved = json.load(f)
        assert saved["hostname"] == ""

    def test_valid_hostname_persisted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Config, "_detect_local_ip", staticmethod(lambda: "192.168.50.99"))
        cfg = Config(conf_path=str(tmp_path))
        cfg.save()
        with open(tmp_path / "config.json", encoding="utf-8") as f:
            saved = json.load(f)
        assert saved["hostname"] == "192.168.50.99"