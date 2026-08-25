"""登录失败的递增退避:纯函数 + 显式时钟,便于测试与跨存储复用。

连续失败达到阈值后开始锁定,时长 min(2^(超出次数) 分钟, 上限);登录成功即清零。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ThrottleState:
    """某账号当前的失败计数与锁定截止时间(0.0 = 未锁定)。"""

    fail_count: int = 0
    locked_until: float = 0.0


CLEARED = ThrottleState()


def is_locked(state: ThrottleState, *, now: float) -> bool:
    """当前是否处于锁定期。"""
    return state.locked_until > now


def register_failure(state: ThrottleState, *, now: float, max_failures: int = 5,
                     lock_max_minutes: int = 30) -> ThrottleState:
    """记一次失败并算出新的锁定截止时间。"""
    fail_count = state.fail_count + 1
    if fail_count < max_failures:
        return ThrottleState(fail_count=fail_count, locked_until=state.locked_until)
    exponent = min(fail_count - max_failures, 16)   # 封顶前先夹住指数,避免溢出
    minutes = min(2 ** exponent, lock_max_minutes)
    return ThrottleState(fail_count=fail_count, locked_until=now + minutes * 60)
