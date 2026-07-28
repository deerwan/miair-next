"""版本号解析与比较测试 (system.py::_parse_version)"""

from app.api.v1.system import _parse_version


class TestParseVersion:
    def test_plain(self):
        assert _parse_version("1.2.3") == (1, 2, 3)

    def test_v_prefix(self):
        assert _parse_version("v1.2.3") == (1, 2, 3)
        assert _parse_version("V1.2.3") == (1, 2, 3)

    def test_double_digit(self):
        # 字符串比较会错判 ("0.10.0" < "0.9.0"), 元组比较必须正确
        assert _parse_version("0.10.0") > _parse_version("0.9.0")

    def test_non_numeric_suffix_ignored(self):
        assert _parse_version("1.2.3-beta") == (1, 2, 3)
        assert _parse_version("1.2.3rc1") == (1, 2, 3)

    def test_empty_and_invalid(self):
        assert _parse_version("") == (0,)
        assert _parse_version(None) == (0,)
        assert _parse_version("abc") == (0,)

    def test_comparison(self):
        assert _parse_version("v0.2.0") > _parse_version("0.1.0")
        assert _parse_version("1.0.0") > _parse_version("0.99.99")
        assert _parse_version("0.1.0") == _parse_version("v0.1.0")
        assert not _parse_version("0.1.0") > _parse_version("0.1.0")
