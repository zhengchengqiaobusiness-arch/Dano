"""M6 收尾验收:goal-loop 运行可追溯(generation_runs 落库,审计自动写的代码)。

确定性(PG + FakeCoder):跑一次"首版 buggy→驳回→修复→发布",断言库里有一条
generation_runs,记录 ok、rejections≥1、迭代明细(第0轮失败原因 + 末轮发布)。
"""

from __future__ import annotations

import json
import os

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
        await c.execute("DELETE FROM assets WHERE tenant='gen6'")
        await c.execute("DELETE FROM generation_runs WHERE tenant='gen6'")
    yield
    await close_pool()


class _PassBoard:
    async def review(self, *, asset_type, asset_key, body, evidence=None):  # noqa: ANN001
        from dano.review.board import ReviewVerdict
        return [ReviewVerdict(r, m, True, []) for r, m in
                (("acceptance", "fa"), ("security", "fb"), ("compliance", "fc"))]


class _Coder:
    async def generate(self, *, plan, feedback):  # noqa: ANN001
        code = 200 if feedback else 500          # 首版 buggy,拿反馈后修复
        return {"action": plan.flow, "strategy": plan.strategy,
                "source": f"def run(inputs, creds):\n    return {{'code': {code}}}\n",
                "entry": "run", "success_rule": plan.success_rule}


async def test_generation_run_is_persisted():
    from dano.agent_tools import materials, tools as T
    from dano.generation import GenerationLoop, GoalBrief
    from dano.generation.strategies import select_strategy
    from dano.infra.db import get_pool

    run_id = "gen6-run"
    materials.register(materials.MaterialContext(
        run_id=run_id, tenant="gen6", system_instance_id="A-OA", subsystem="A-OA",
        openapi={}, deploy={"base_url": "http://x", "auth": {"kind": "token"}},
        credentials={"token": "t"}))
    goal = GoalBrief(run_id=run_id, system_instance_id="A-OA", flow="misc_flow",
                     actions=[{"name": "do", "method": "POST", "endpoint": "/misc/do"}])

    T.set_review_board(_PassBoard())
    try:
        result = await GenerationLoop(_Coder()).run(goal, select_strategy(goal.actions))
    finally:
        T.set_review_board(None)
    assert result.ok and result.rejections >= 1

    async with get_pool().acquire() as c:
        row = await c.fetchrow(
            "SELECT * FROM generation_runs WHERE tenant='gen6' AND flow='misc_flow'")
    assert row is not None
    assert row["ok"] is True
    assert row["rejections"] >= 1
    assert row["strategy"] == "simple_http"
    iters = json.loads(row["iterations"])
    assert iters[0]["passed"] is False and iters[0]["reasons"]      # 首轮失败有原因
    assert iters[-1]["passed"] is True                              # 末轮发布


async def test_generation_registers_into_lifecycle():
    from dano.agent_tools import materials, tools as T
    from dano.generation import GenerationLoop, GoalBrief
    from dano.generation.strategies import select_strategy
    from dano.lifecycle.state_machine import SkillLifecycle
    from dano.shared.enums import SkillState

    run_id = "gen6-lc"
    materials.register(materials.MaterialContext(
        run_id=run_id, tenant="gen6", system_instance_id="A-OA", subsystem="A-OA",
        openapi={}, deploy={"base_url": "http://x", "auth": {"kind": "token"}},
        credentials={"token": "t"}))
    goal = GoalBrief(run_id=run_id, system_instance_id="A-OA", flow="lc_flow",
                     actions=[{"name": "do", "method": "POST", "endpoint": "/misc/do"}])
    lc = SkillLifecycle()
    T.set_review_board(_PassBoard())
    try:
        result = await GenerationLoop(_Coder(), lifecycle=lc).run(goal, select_strategy(goal.actions))
    finally:
        T.set_review_board(None)
    assert result.ok
    rec = await lc.store.get("A-OA.lc_flow")
    assert rec is not None and rec.state == SkillState.PUBLISHED   # 已登记到「已发布」
