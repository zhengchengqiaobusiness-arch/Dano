"""Phase A2 验收:评审委员会工程化(重试退避 / 结果缓存 / 降级急停)。

纯单元:注入 fake client,不需 PG/key/mock。
- 瞬时错误退避重试:失败 N 次后成功 → 整体通过;重试用尽 → 判不通过(安全默认)。
- 结果缓存:同输入第二次评审不再调模型。
- 降级急停:review_enabled=false → 发布评审闸门旁路放行(运维急停)。
"""

from __future__ import annotations

import os

import pytest

from dano.review import board as B


@pytest.fixture(autouse=True)
def _clear_cache():
    B._VERDICT_CACHE.clear()
    yield
    B._VERDICT_CACHE.clear()


_MODELS = {"acceptance": "m-a", "security": "m-b", "compliance": "m-c"}


class FlakyClient:
    """每个模型前 fail_n 次调用抛错,之后返回通过。按模型计数(= 按角色)。"""
    def __init__(self, fail_n: int):
        self.fail_n = fail_n
        self.calls: dict[str, int] = {}

    async def complete_json(self, *, model, system, user, timeout_s):  # noqa: ANN001
        self.calls[model] = self.calls.get(model, 0) + 1
        if self.calls[model] <= self.fail_n:
            raise RuntimeError("transient boom")
        return {"passed": True, "reasons": []}


class CountingClient:
    """总是通过,记录总调用次数(验证缓存命中)。"""
    def __init__(self):
        self.total = 0

    async def complete_json(self, *, model, system, user, timeout_s):  # noqa: ANN001
        self.total += 1
        return {"passed": True, "reasons": []}


async def test_retry_recovers_from_transient():
    c = FlakyClient(fail_n=2)
    board = B.ReviewBoard(client=c, models=_MODELS, max_retries=2, backoff_s=0)
    verdicts = await board.review(asset_type="connector", asset_key="x", body={"a": 1})
    assert all(v.passed for v in verdicts), verdicts
    assert all(c.calls[m] == 3 for m in _MODELS.values()), c.calls   # 2 失败 + 1 成功


async def test_retry_exhausted_marks_failed():
    c = FlakyClient(fail_n=3)            # 超过 max_retries+1 次,始终失败
    board = B.ReviewBoard(client=c, models=_MODELS, max_retries=2, backoff_s=0)
    verdicts = await board.review(asset_type="connector", asset_key="x", body={"a": 1})
    assert all(not v.passed for v in verdicts), verdicts
    assert all("重试2次" in v.reasons[0] for v in verdicts), verdicts


async def test_cache_avoids_repeat_calls():
    c = CountingClient()
    board = B.ReviewBoard(client=c, models=_MODELS, max_retries=0, backoff_s=0)
    body = {"endpoint": "/x", "method": "GET"}
    await board.review(asset_type="connector", asset_key="x", body=body)
    assert c.total == 3                  # 三审各一次
    await board.review(asset_type="connector", asset_key="x", body=body)   # 同输入
    assert c.total == 3                  # 命中缓存,未再调用


async def test_degrade_bypasses_review_gate():
    """review_enabled=false → verify_reviewed 直接放行(运维急停),不碰 DB。"""
    from uuid import uuid4

    from dano.assets.drafts import DraftStore
    from dano.config import get_settings
    os.environ["DANO_REVIEW_ENABLED"] = "0"
    get_settings.cache_clear()
    try:
        ok, reason = await DraftStore().verify_reviewed(uuid4(), [])
        assert ok and "临时关闭" in reason, reason
    finally:
        os.environ["DANO_REVIEW_ENABLED"] = "1"
        get_settings.cache_clear()
