"""SpeakerController 状态轮询与连续失败计数测试 (不触网)"""

import asyncio

import pytest

from app.engine.config import Config, Speaker
from app.engine.speaker import SpeakerController


class FakeMina:
    """替代 MiNAService: player_get_status 可编程成功/登录失效失败"""

    def __init__(self, fail: bool = False):
        self.fail = fail

    async def player_get_status(self, device_id):
        if self.fail:
            raise Exception("Error https://api2.mina.mi.com: Login failed")
        return {"code": 0, "data": {"info": '{"status":1,"volume":30}'}}


class FakeAuth:
    """替代 AuthManager: ensure_login 空操作, call_api 直接执行 factory"""

    def __init__(self, fail: bool = False, config=None):
        self.mina_service = FakeMina(fail)
        self.config = config

    async def ensure_login(self):
        return None

    async def call_api(self, factory, label=""):
        return await factory()


def _make_controller(tmp_path, fail: bool = False) -> SpeakerController:
    config = Config(conf_path=str(tmp_path))
    return SpeakerController(
        Speaker(did="123", device_id="dev-1", hardware="LX06"),
        auth=FakeAuth(fail, config),
        config=config,
    )


class TestFailureCounter:
    def test_success_resets_counter(self, tmp_path):
        """获取状态成功后必须重置实例级连续失败计数

        回归: 曾误写成 SpeakerController._consecutive_login_failures = 0
        (从未被读取的类属性), 实例计数只增不减, 长期运行中偶发失败累积
        会触发毫无必要的自动重启。
        """
        controller = _make_controller(tmp_path)
        controller.consecutive_login_failures = 5

        async def _main():
            status = await controller.get_status()
            return status, controller.consecutive_login_failures

        status, failures = asyncio.run(_main())
        assert status == {"status": 1, "volume": 30}
        assert failures == 0

    def test_login_failure_accumulates_below_threshold(self, tmp_path):
        """登录失效类错误累计实例计数 (未达重启阈值), 并向调用方抛出"""
        controller = _make_controller(tmp_path, fail=True)

        async def _main():
            for _ in range(2):
                with pytest.raises(Exception, match="get_status 失败"):
                    await controller.get_status()
            return controller.consecutive_login_failures

        failures = asyncio.run(_main())
        assert failures == 2
