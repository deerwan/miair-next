"""SSRF 防护: 对外抓取 URL 前的 scheme 与目标 IP 校验

背景: DLNA SetAVTransportURI / 音频缓冲下载等入口的 URL 来自局域网
(无鉴权面), 服务端会主动发起 HTTP 请求。若不校验, 攻击者可让后端
请求 loopback (管理面板自身 / 本机其他服务)、链路本地 (云 metadata 169.254.169.254)
等危险地址。

策略: 家庭局域网的合法媒体源是 NAS / 媒体服务器 (10/8、172.16/12、
192.168/16、100.64/10 等), 因此仅拒绝 loopback / link-local / unspecified /
multicast, **不拒绝私网地址**, 避免破坏局域网投歌核心场景。
"""

import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import aiohttp

# 允许的 URL 协议
ALLOWED_SCHEMES = ("http", "https")

# URL 长度上限
_MAX_URL_LEN = 2048

# 手动跟随重定向的上限 (每一跳都做 SSRF 校验, 防止 302 绕过)
_MAX_REDIRECTS = 5
_REDIRECT_CODES = (301, 302, 303, 307, 308)


class UnsafeURLError(ValueError):
    """URL 未通过 SSRF 校验"""


def validate_url_scheme(url: str) -> None:
    """同步校验 scheme 与 hostname (纯字符串操作, 零 IO)"""
    if not url or len(url) > _MAX_URL_LEN:
        raise UnsafeURLError("URL 为空或超过长度上限")
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeURLError(f"仅允许 {ALLOWED_SCHEMES} 协议: {url[:80]}")
    if not parsed.hostname:
        raise UnsafeURLError(f"URL 缺少主机名: {url[:80]}")


def _is_blocked_ip(ip_str: str) -> bool:
    """判断 IP 是否属于禁止访问的地址段 (不拒绝私网, 见模块 docstring)"""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # 解析失败按危险处理
    return (
        ip.is_loopback        # 127.0.0.0/8, ::1
        or ip.is_link_local   # 169.254.0.0/16 (含云 metadata), fe80::/10
        or ip.is_unspecified  # 0.0.0.0, ::
        or ip.is_multicast    # 224.0.0.0/4
    )


def assert_host_safe(hostname: str) -> None:
    """校验主机名解析出的全部 IP (阻塞 DNS 调用, 谨慎在事件循环直调)

    IP 字面量零开销直判; 域名则解析出所有地址, 任一命中黑名单即拒绝。
    """
    try:
        ipaddress.ip_address(hostname)
        targets = [hostname]
    except ValueError:
        try:
            infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
        except (socket.gaierror, UnicodeError) as e:
            raise UnsafeURLError(f"主机解析失败: {hostname} ({e})") from e
        targets = [info[4][0] for info in infos]
        if not targets:
            raise UnsafeURLError(f"主机解析失败: {hostname}")
    for target in targets:
        if _is_blocked_ip(target):
            raise UnsafeURLError(f"目标地址不允许访问: {target}")


async def validate_url_safe(url: str) -> None:
    """完整 SSRF 校验 (scheme + 目标 IP), DNS 解析在线程池执行避免阻塞事件循环"""
    validate_url_scheme(url)
    hostname = urlparse(url).hostname
    await asyncio.to_thread(assert_host_safe, hostname)


async def safe_request(
    session: aiohttp.ClientSession, method: str, url: str, **kwargs
) -> aiohttp.ClientResponse:
    """带 SSRF 防护的请求: 每一跳 (含重定向) 都做校验

    用法与 aiohttp 原生一致 (返回最终非重定向响应, 由调用方 async with 管理):

        async with await safe_request(session, "GET", url, headers=h) as resp:
            ...
    """
    kwargs["allow_redirects"] = False
    current = url
    current_method = method
    for _ in range(_MAX_REDIRECTS + 1):
        await validate_url_safe(current)
        resp = await session.request(current_method, current, **kwargs)
        if resp.status in _REDIRECT_CODES:
            location = resp.headers.get("Location")
            resp.release()
            if not location:
                raise UnsafeURLError(f"重定向缺少 Location 头 (HTTP {resp.status})")
            current = urljoin(current, location)
            if resp.status == 303:
                current_method = "GET"
            continue
        return resp
    raise UnsafeURLError(f"重定向次数超过上限 ({_MAX_REDIRECTS})")
