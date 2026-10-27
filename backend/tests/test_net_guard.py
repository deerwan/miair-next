"""SSRF 防护测试 (net_guard): scheme 白名单 / 危险 IP 拒绝 / 私网放行

对应审查 P0-3: DLNA SetAVTransportURI / 音频缓冲下载等无鉴权入口,
服务端主动外呼前必须校验目标, 防止被引导访问 loopback (面板自身)、
链路本地 (云 metadata) 等地址; 私网 NAS 媒体源属核心场景必须放行。
"""

import pytest

from app.engine.net_guard import (
    UnsafeURLError,
    assert_host_safe,
    validate_url_scheme,
)


class TestValidateUrlScheme:
    def test_http_https_accepted(self):
        validate_url_scheme("http://192.168.1.5:8090/music.flac")
        validate_url_scheme("https://example.com/a.mp3")

    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "ftp://x/y",
            "gopher://x/y",
            "data:text/plain,hello",
            "javascript:alert(1)",
            "/etc/passwd",
            "",
        ],
    )
    def test_non_http_rejected(self, url):
        with pytest.raises(UnsafeURLError):
            validate_url_scheme(url)

    def test_no_hostname_rejected(self):
        with pytest.raises(UnsafeURLError):
            validate_url_scheme("http:///path/only")

    def test_oversize_rejected(self):
        with pytest.raises(UnsafeURLError):
            validate_url_scheme("http://" + "a" * 3000)


class TestHostSafety:
    def test_private_networks_allowed(self):
        """NAS/媒体服务器所在私网段必须放行 (投歌核心场景)"""
        for host in ("192.168.1.5", "10.0.0.2", "172.16.0.9", "100.64.0.1"):
            assert_host_safe(host)

    @pytest.mark.parametrize(
        "host",
        [
            "127.0.0.1",
            "127.8.8.8",
            "0.0.0.0",
            "169.254.169.254",  # 云厂商 metadata 服务
            "224.0.0.1",
        ],
    )
    def test_blocked_ipv4(self, host):
        with pytest.raises(UnsafeURLError):
            assert_host_safe(host)

    @pytest.mark.parametrize("host", ["::1", "fe80::1"])
    def test_blocked_ipv6(self, host):
        with pytest.raises(UnsafeURLError):
            assert_host_safe(host)

    def test_localhost_resolves_to_loopback(self):
        """域名解析到 loopback 必须拒绝 (防 DNS 绕过的第一步)"""
        with pytest.raises(UnsafeURLError):
            assert_host_safe("localhost")

    def test_domain_resolving_to_blocked_ip_rejected(self, monkeypatch):
        """域名解析到黑名单 IP 必须拒绝 (不依赖真实 DNS, mock 解析结果)"""
        import socket as socket_mod

        import app.engine.net_guard as ng

        def fake_getaddrinfo(host, port, proto=0):
            return [
                (socket_mod.AF_INET, socket_mod.SOCK_STREAM, 6, "", ("127.0.0.1", 0))
            ]

        monkeypatch.setattr(ng.socket, "getaddrinfo", fake_getaddrinfo)
        with pytest.raises(UnsafeURLError):
            ng.assert_host_safe("rebind.example.com")

    def test_domain_resolving_to_private_ip_allowed(self, monkeypatch):
        """NAS 域名解析到私网 IP 必须放行 (核心场景)"""
        import socket as socket_mod

        import app.engine.net_guard as ng

        def fake_getaddrinfo(host, port, proto=0):
            return [
                (
                    socket_mod.AF_INET,
                    socket_mod.SOCK_STREAM,
                    6,
                    "",
                    ("192.168.1.10", 0),
                )
            ]

        monkeypatch.setattr(ng.socket, "getaddrinfo", fake_getaddrinfo)
        ng.assert_host_safe("nas.lan")


class TestCallbackValidationIntegrated:
    """eventing.validate_callback_url 应复用 host 校验"""

    def test_loopback_callback_rejected(self):
        from app.engine.dlna.eventing import validate_callback_url

        assert validate_callback_url("<http://127.0.0.1:30000/cb>") is None

    def test_private_callback_accepted(self):
        from app.engine.dlna.eventing import validate_callback_url

        assert (
            validate_callback_url("<http://192.168.1.5:30000/callback>")
            == "http://192.168.1.5:30000/callback"
        )
