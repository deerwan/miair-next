"""凭据恢复重启节流测试 (2026-09-06 重启风暴事故回归)

restart_dlna_services 每次都会新建 AuthManager; 当登录持续失败而「复用缓存
serviceToken」持续成功时, 每轮都会触发凭据恢复回调 → 重启 → 新 AuthManager
→ 又登录失败 → 回调……无节流时形成死循环并以每秒数次的频率请求小米接口。

节流状态必须放在 orchestrator 单例上 (跨 AuthManager 实例生效)。
"""

import asyncio

from app.services.orchestrator import RECOVERY_RESTART_MIN_INTERVAL, Orchestrator


def _bare_orchestrator() -> Orchestrator:
    """绕过 __init__ 构造最小化实例 (只设回调路径依赖的字段)"""
    orch = Orchestrator.__new__(Orchestrator)
    orch.dlna_running = False
    orch._last_recovery_restart = 0.0
    return orch


def _attach_recorder(orch: Orchestrator, calls: list):
    async def fake_restart():
        calls.append(1)

    orch.restart_dlna_services = fake_restart


def test_second_recovery_within_interval_is_skipped():
    """冷却窗口内的第二次恢复回调直接跳过 (切断 重启→失败→复用→再重启 循环)"""
    orch = _bare_orchestrator()
    calls: list = []
    _attach_recorder(orch, calls)

    async def run():
        await orch._on_auth_recovered()
        await orch._on_auth_recovered()
        return len(calls)

    assert asyncio.run(run()) == 1


def test_recovery_allowed_after_interval():
    """冷却期过后恢复回调正常放行 (真实凭据恢复场景不受影响)"""
    orch = _bare_orchestrator()
    calls: list = []
    _attach_recorder(orch, calls)

    async def run():
        await orch._on_auth_recovered()
        orch._last_recovery_restart -= RECOVERY_RESTART_MIN_INTERVAL + 1
        await orch._on_auth_recovered()
        return len(calls)

    assert asyncio.run(run()) == 2


def test_recovery_skipped_when_dlna_already_running():
    """DLNA 已在运行时不触发重启 (原有语义保持)"""
    orch = _bare_orchestrator()
    orch.dlna_running = True
    calls: list = []
    _attach_recorder(orch, calls)

    asyncio.run(orch._on_auth_recovered())

    assert calls == []
