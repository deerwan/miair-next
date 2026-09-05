"""XML 安全解析测试 (对应审查 P1: 无鉴权端点上的实体爆炸 DoS)"""

import xml.etree.ElementTree as ET

import pytest

from app.engine.dlna.xmlsafe import safe_fromstring

# 经典 billion laughs 载荷 (实体定义必须经由 DTD, 安全版直接拒绝)
_BOMB = """<?xml version="1.0"?>
<!DOCTYPE lolz [
  <!ENTITY lol "lol">
  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
  <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
]>
<lolz>&lol3;</lolz>"""

_DIDL = (
    '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/">'
    '<item id="0"><dc:title>晴天</dc:title></item></DIDL-Lite>'
)


class TestSafeFromstring:
    def test_rejects_billion_laughs(self):
        with pytest.raises(ET.ParseError, match="DTD"):
            safe_fromstring(_BOMB)

    def test_rejects_external_entity_dtd(self):
        payload = (
            '<?xml version="1.0"?><!DOCTYPE x SYSTEM "http://evil/x.dtd">'
            "<x/>"
        )
        with pytest.raises(ET.ParseError, match="DTD"):
            safe_fromstring(payload)

    def test_rejects_marker_in_any_case(self):
        with pytest.raises(ET.ParseError, match="DTD"):
            safe_fromstring("<?xml version='1.0'?><!DoCtYpE x [<!ENTITY a b>]><x/>")

    def test_normal_xml_ok(self):
        root = safe_fromstring("<root><a>1</a></root>")
        assert root.findtext("a") == "1"

    def test_didl_without_dtd_ok(self):
        root = safe_fromstring(_DIDL)
        assert root.findtext(".//{http://purl.org/dc/elements/1.1/}title") == "晴天"

    def test_malformed_xml_still_parseerror(self):
        with pytest.raises(ET.ParseError):
            safe_fromstring("<not-closed>")


class TestCallSites:
    def test_soap_body_with_dtd_returns_empty(self):
        """SOAP 入口: DTD 载荷按解析失败处理, 不崩溃不展开"""
        from app.engine.dlna.soap_handler import parse_soap_body

        assert parse_soap_body(_BOMB) == {}

    def test_soap_body_normal_ok(self):
        from app.engine.dlna.soap_handler import parse_soap_body

        body = (
            '<?xml version="1.0"?>'
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
            "<s:Body>"
            '<u:Play xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
            "<InstanceID>0</InstanceID><Speed>1</Speed></u:Play>"
            "</s:Body></s:Envelope>"
        )
        assert parse_soap_body(body) == {"InstanceID": "0", "Speed": "1"}

    def test_track_info_with_dtd_ignored(self):
        """投送元数据带 DTD: 歌名/歌手按未解析处理"""
        from app.engine.dlna.renderer import DLNARenderer

        assert DLNARenderer._parse_track_info(_BOMB) == ("", "")

    def test_duration_with_dtd_zero(self):
        from app.engine.dlna.renderer import DLNARenderer

        assert DLNARenderer._parse_duration_from_metadata(_BOMB) == 0.0

    def test_didl_cover_injection_still_works(self):
        """正常 DIDL 注入封面不受影响"""
        from app.engine.dlna.renderer import DLNARenderer

        out = DLNARenderer._build_didl_with_cover(_DIDL, "http://1.2.3.4/cover.png")
        assert "http://1.2.3.4/cover.png" in out
