"""M6 加固:页面运行时的总超时 + 并发上限 + 驱动回收(离线,零浏览器)。"""
from __future__ import annotations

import asyncio
from uuid import uuid4

from dano.execution.page import FakePageDriver, PageActionRuntime
from dano.shared.asset_bodies import PageAction, PageScriptBody
from dano.shared.enums import Outcome

_SCRIPT = PageScriptBody(actions=[PageAction(op="verify", locator="css=#x")],
                         dom_fingerprint="", action="probe")


class _HangDriver(FakePageDriver):
    """fingerprint 卡死,用于触发运行时总超时。"""
    closed = False

    async def fingerprint(self) -> str:
        await asyncio.sleep(10)
        return await super().fingerprint()

    async def close(self) -> None:
        type(self).closed = True
        await super().close()


async def test_run_times_out_and_recycles_driver() -> None:
    d = _HangDriver()
    _HangDriver.closed = False
    res = await PageActionRuntime(lambda: d, timeout_s=0.05).run(
        uuid4(), _SCRIPT, {}, confirm=lambda f: True)
    assert res.outcome == Outcome.FAILED
    assert res.structured_output.get("timeout") is True
    assert _HangDriver.closed is True            # 超时也回收浏览器,杜绝僵尸


class _SlowDriver(FakePageDriver):
    """持有一段时间(fingerprint 慢),用于观测并发占用窗口。"""

    def __init__(self, *, live: list[int], peak: list[int], delay: float) -> None:
        super().__init__()
        self._live, self._peak, self._delay = live, peak, delay
        live[0] += 1
        peak[0] = max(peak[0], live[0])           # 占用窗口从创建开始

    async def fingerprint(self) -> str:
        await asyncio.sleep(self._delay)
        return await super().fingerprint()

    async def close(self) -> None:
        self._live[0] -= 1
        await super().close()


async def _run_n(n: int, max_concurrency: int) -> int:
    live, peak = [0], [0]
    rt = PageActionRuntime(lambda: _SlowDriver(live=live, peak=peak, delay=0.05),
                           timeout_s=5, max_concurrency=max_concurrency)
    await asyncio.gather(*[rt.run(uuid4(), _SCRIPT, {}, confirm=lambda f: True) for _ in range(n)])
    return peak[0]


async def test_concurrency_capped() -> None:
    assert await _run_n(4, max_concurrency=1) == 1     # 上限 1 → 任一时刻最多 1 个浏览器
    assert await _run_n(4, max_concurrency=2) <= 2     # 上限 2 → 不超过 2


async def test_concurrency_unbounded_without_cap() -> None:
    assert await _run_n(4, max_concurrency=0) > 1      # 不设上限 → 会并发多个(印证上限有意义)
