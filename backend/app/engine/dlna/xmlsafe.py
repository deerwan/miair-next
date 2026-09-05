"""安全 XML 解析: 拒绝含 DTD 的输入

SOAP 请求体与 DIDL-Lite 元数据来自无鉴权的局域网端点。ElementTree 对
外部实体 (XXE) 本身会直接报 ParseError, 但实体放大 (billion laughs)
依赖 DTD 内的实体定义——新版 libexpat (≥2.4) 有放大保护, 而 OpenWrt
等嵌入式平台常带的旧版没有。实体定义必须经由 DOCTYPE 引入, 因此
拒绝任何含 DOCTYPE/ENTITY 标记的输入即可封死这一类攻击, 且不影响
任何合法的 UPnP 控制点 (DIDL-Lite/SOAP 均不使用 DTD)。
"""

import logging
import xml.etree.ElementTree as ET

log = logging.getLogger("miair")

_DTD_MARKERS = ("<!doctype", "<!entity")


def safe_fromstring(text: str):
    """解析 XML, 含 DTD 的输入抛 ET.ParseError (调用方按解析失败处理)"""
    lowered = text.lower()
    if any(marker in lowered for marker in _DTD_MARKERS):
        log.warning("拒绝含 DTD 的 XML 输入 (可能的实体爆炸攻击)")
        raise ET.ParseError("DTD (DOCTYPE/ENTITY) is not allowed")
    return ET.fromstring(text)
