"""M1 验收:goal 模式代码生成的迭代闭环(非一次成型)。

确定性(PG + 隔离 runner + 注入 FakeCoder,无需 LLM/key):
- FakeCoder 第 1 轮产 buggy 代码(返回 code 500)→ 测试驳回;
- 拿到 reasons 后第 2 轮产修复代码(返回 code 200)→ 沙箱通过 → 发布。
证明:循环成立、闸门能驳回、驳回原因回灌后修复并发布(rejections ≥ 1),且发布走不可伪造闸门。
"""

from __future__ import annotations

import os
import socket

import pytest

BACK_DSN = os.environ.get("DANO_PG_DSN", "postgresql://postgres:111111@localhost:5432/dano_back")


@pytest.fixture(autouse=True)
async def _pg():
    os.environ["DANO_PG_DSN"] = BACK_DSN
    from dano.config import get_settings
    get_settings.cache_clear()
    from dano.infra.db import close_pool, get_pool, init_pool, run_migrations
    try:
        await init_pool()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"PostgreSQL 不可用: {e}")
    await run_migrations()
    async with get_pool().acquire() as c:
        await c.execute("DELETE FROM assets WHERE tenant='gen1'")
    yield
    await close_pool()


class FakeCoder:
    """确定性生成器:第 1 轮 buggy(code 500),收到 reasons 后修复(code 200)。"""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def generate(self, *, plan, feedback):  # noqa: ANN001
        self.calls.append(list(feedback))
        # 只有在收到驳回反馈后才"修复";首轮无反馈 → 故意 buggy
        fixed = bool(feedback)
        code = 200 if fixed else 500
        source = (
            "def run(inputs, creds):\n"
            f"    return {{'code': {code}, 'echo': inputs}}\n"
        )
        return {"action": plan.flow, "strategy": plan.strategy, "source": source,
                "entry": "run", "success_rule": plan.success_rule,
                "user_fields": plan.user_fields, "required_fields": plan.required_fields}


async def _register_materials(run_id: str):
    from dano.agent_tools import materials
    materials.register(materials.MaterialContext(
        run_id=run_id, tenant="gen1", system_instance_id="A-OA", subsystem="A-OA",
        openapi={}, deploy={"base_url": "http://localhost:9", "auth": {"kind": "token"}},
        credentials={"token": "test-token"}))


class _PassBoard:
    """三审全过的 fake 评审委员会(3 个不同 model_id),不烧 key。"""
    async def review(self, *, asset_type, asset_key, body, evidence=None):  # noqa: ANN001
        from dano.review.board import ReviewVerdict
        return [ReviewVerdict(role=r, model_id=m, passed=True, reasons=[])
                for r, m in (("acceptance", "fake-a"), ("security", "fake-b"), ("compliance", "fake-c"))]


async def test_generation_loop_rejects_then_fixes_then_publishes():
    from dano.assets.repository import AssetRepository
    from dano.generation import GenerationLoop, GoalBrief
    from dano.generation.strategies import select_strategy
    from dano.shared.enums import AssetType, Subsystem
    from dano.shared.models import Scope

    from dano.agent_tools import tools as T

    run_id = "gen1-run"
    await _register_materials(run_id)
    goal = GoalBrief(
        run_id=run_id, system_instance_id="A-OA", flow="list_done_items",
        actions=[{"name": "do_misc", "method": "POST", "endpoint": "/misc/do",
                  "required_in": []}],
        test_input={"pageNum": 1})
    strategy = select_strategy(goal.actions)        # 非 GET、非流程 → 兜底 simple_http
    assert strategy is not None and strategy.name == "simple_http"

    coder = FakeCoder()
    T.set_review_board(_PassBoard())                     # adapter 现需三模型评审(注入 fake)
    try:
        result = await GenerationLoop(coder).run(goal, strategy)
    finally:
        T.set_review_board(None)

    # 非一次成型:至少被驳回 1 次,且最终发布
    assert result.ok is True, result
    assert result.rejections >= 1, result.iterations
    assert result.iterations[0].passed is False        # 首轮 buggy 被驳回
    assert result.iterations[-1].passed is True         # 末轮修复通过
    # 第 2 轮 coder 确实收到了驳回原因(reasons 回灌)
    assert coder.calls[0] == [] and coder.calls[1], coder.calls

    # 发布走的是真实闸门:repo 里确有一个已发布 ADAPTER
    repo = AssetRepository()
    pubs = await repo.list_published(AssetType.ADAPTER, Scope(tenant="gen1", subsystem=Subsystem.OA))
    assert [e.asset_key for e in pubs] == ["list_done_items"], pubs


async def test_generation_loop_exhausts_budget_when_never_fixed():
    """coder 永远产 buggy → 耗尽预算,判失败、不发布(证明闸门不放水)。"""
    from dano.assets.repository import AssetRepository
    from dano.generation import Budget, GenerationLoop, GoalBrief
    from dano.generation.strategies import select_strategy
    from dano.shared.enums import AssetType, Subsystem
    from dano.shared.models import Scope

    run_id = "gen1-run2"
    await _register_materials(run_id)

    class AlwaysBuggy:
        async def generate(self, *, plan, feedback):  # noqa: ANN001
            return {"action": plan.flow, "strategy": plan.strategy,
                    "source": "def run(inputs, creds):\n    return {'code': 500}\n",
                    "entry": "run", "success_rule": plan.success_rule}

    goal = GoalBrief(run_id=run_id, system_instance_id="A-OA", flow="always_bad",
                     actions=[{"name": "x", "method": "GET", "endpoint": "/x"}],
                     budget=Budget(max_iters=3))
    result = await GenerationLoop(AlwaysBuggy()).run(goal, select_strategy(goal.actions))
    assert result.ok is False and len(result.iterations) == 3
    repo = AssetRepository()
    pubs = await repo.list_published(AssetType.ADAPTER, Scope(tenant="gen1", subsystem=Subsystem.OA))
    assert all(e.asset_key != "always_bad" for e in pubs)
