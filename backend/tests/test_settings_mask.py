"""cookie 脱敏 / 还原逻辑测试 (core/masking.py)"""

from app.core.masking import MASKED_TOKEN, mask_cookie, mask_devices, unmask_cookie

REAL_COOKIE = "userId=123456789; passToken=SecretTokenValue; deviceId=dev01"


class TestMaskCookie:
    def test_pass_token_fully_masked(self):
        masked = mask_cookie(REAL_COOKIE)
        assert "SecretTokenValue" not in masked
        assert f"passToken={MASKED_TOKEN}" in masked

    def test_user_id_keeps_last_3(self):
        masked = mask_cookie(REAL_COOKIE)
        assert f"userId={MASKED_TOKEN}789" in masked
        assert "123456" not in masked

    def test_other_fields_untouched(self):
        masked = mask_cookie(REAL_COOKIE)
        assert "deviceId=dev01" in masked

    def test_empty_cookie(self):
        assert mask_cookie("") == ""

    def test_short_user_id_fully_masked(self):
        masked = mask_cookie("userId=12")
        assert masked == f"userId={MASKED_TOKEN}"


class TestUnmaskCookie:
    def test_masked_values_restored_from_current(self):
        """前端未修改, 回写的是脱敏占位符 → 还原真实值"""
        masked = mask_cookie(REAL_COOKIE)
        restored = unmask_cookie(masked, REAL_COOKIE)
        assert "passToken=SecretTokenValue" in restored
        assert "userId=123456789" in restored
        assert "deviceId=dev01" in restored

    def test_new_value_saved_as_is(self):
        """用户填入新值 (不含 *) → 按原样保存, 不被旧值覆盖"""
        new_cookie = "userId=999888777; passToken=BrandNewToken"
        restored = unmask_cookie(new_cookie, REAL_COOKIE)
        assert "passToken=BrandNewToken" in restored
        assert "userId=999888777" in restored
        assert "SecretTokenValue" not in restored

    def test_partial_update(self):
        """只改 passToken, userId 保持脱敏占位符 → 分别处理"""
        mixed = f"userId={MASKED_TOKEN}789; passToken=BrandNewToken"
        restored = unmask_cookie(mixed, REAL_COOKIE)
        assert "userId=123456789" in restored
        assert "passToken=BrandNewToken" in restored

    def test_empty_new_cookie(self):
        assert unmask_cookie("", REAL_COOKIE) == ""

    def test_masked_without_current_value_kept(self):
        """无已存储真实值时, 占位符原样保留 (不会崩溃)"""
        restored = unmask_cookie(f"passToken={MASKED_TOKEN}", "")
        assert restored == f"passToken={MASKED_TOKEN}"


class TestMaskDevices:
    DEVICE = {
        "miotDID": "12345",
        "hardware": "L05B",
        "name": "小爱音箱",
        "token": "should-be-removed",
        "mac": "AA:BB:CC",
    }

    def test_whitelist_only(self):
        masked = mask_devices([self.DEVICE])
        assert masked == [{"miotDID": "12345", "hardware": "L05B", "name": "小爱音箱"}]

    def test_single_device_dict(self):
        masked = mask_devices(self.DEVICE)
        assert masked["miotDID"] == "12345"
        assert "token" not in masked

    def test_missing_field_skipped(self):
        masked = mask_devices([{"name": "只有名字"}])
        assert masked == [{"name": "只有名字"}]
