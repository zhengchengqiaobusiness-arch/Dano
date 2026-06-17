"""M2 验收:把漏洞校验 + 三模型审核接进生成闭环(三关都能驳回)。

- 静态扫描单测:危险调用/命令注入/硬编码密钥能被抓到,干净代码放行;
- 漏洞关:代码能跑通沙箱但含 eval → 漏洞校验驳回 → 修复后通过 → 审核(fake)→ 发布;
- 审核关:代码干净过沙箱+漏洞,但三模型 security 驳回 → 始终不发布(闸门不放水)。
确定性:PG + 隔离 runner + 注入 fake 评审,无需 LLM/key。
"""

from __future__ import annotations

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
        await c.execute("DELETE FROM assets WHERE tenant='gen2'")
    yield
    await close_pool()


# ── 静态扫描单测(纯,无 PG)──
def test_scan_source_flags_dangerous_and_secrets():
    from dano.generation.vuln import scan_source
    assert any("eval" in f for f in scan_source("def run(i,c):\n eval('1')\n"))
    assert any("os.system" in f for f in scan_source("import os\ndef run(i,c):\n os.system('ls')\n"))
    assert any("shell=True" in f for f in
               scan_source("import subprocess\ndef run(i,c):\n subprocess.run('x', shell=True)\n"))
    assert any("Bearer" in f for f in
               scan_source("def run(i,c):\n h={'a':'Bearer abcdef0123456789ABCDEF'}\n return h\n"))
    assert any("密钥" in f or "口令" in f for f in
               scan_source("def run(i,c):\n token = 'super-secret-123'\n return {}\n"))


def test_scan_source_clean_passes():
    from dano.generation.vuln import scan_source
    clean = "def run(inputs, creds):\n    return {'code': 200, 'token_used': bool(creds.get('token'))}\n"
    assert scan_source(clean) == []


async def _materials(run_id: str):
    from dano.agent_tools import materials
    materials.register(materials.MaterialContext(
        run_id=run_id, tenant="gen2", system_instance_id="A-OA", subsystem="A-OA",
        openapi={}, deploy={"base_url": "http://localhost:9", "auth": {"kind": "token"}},
        credentials={"token": "test-token"}))


class _PassBoard:
    async def review(self, *, asset_type, asset_key, body, evidence=None):  # noqa: ANN001
        from dano.review.board import ReviewVerdict
        return [ReviewVerdict(r, m, True, []) for r, m in
                (("acceptance", "fa"), ("security", "fb"), ("compliance", "fc"))]


class _RejectBoard:
    async def review(self, *, asset_type, asset_key, body, evidence=None):  # noqa: ANN001
        from dano.review.board import ReviewVerdict
        return [ReviewVerdict("acceptance", "fa", True, []),
                ReviewVerdict("security", "fb", False, ["源码存在越权风险"]),
                ReviewVerdict("compliance", "fc", True, [])]


def _goal(run_id, flow, budget=4):
    from dano.generation import Budget, GoalBrief
    return GoalBrief(run_id=run_id, system_instance_id="A-OA", flow=flow,
                     actions=[{"name": "x", "method": "GET", "endpoint": "/x"}],
                     test_input={}, budget=Budget(max_iters=budget))


async def test_vuln_gate_rejects_then_fixes_then_publishes():
    from dano.agent_tools import tools as T
    from dano.assets.repository import AssetRepository
    from dano.generation import GenerationLoop
    from dano.generation.strategies import select_strategy
    from dano.shared.enums import AssetType, Subsystem
    from dano.shared.models import Scope

    run_id = "gen2-vuln"
    await _materials(run_id)

    class VulnThenClean:
        def __init__(self): self.n = 0
        async def generate(self, *, plan, feedback):  # noqa: ANN001
            self.n += 1
            # 都能跑通沙箱(返回 code 200);首轮含 eval(漏洞),拿反馈后去掉
            src = ("def run(inputs, creds):\n    return {'code': 200}\n" if feedback
                   else "def run(inputs, creds):\n    _ = eval('1+1')\n    return {'code': 200}\n")
            return {"action": plan.flow, "strategy": plan.strategy, "source": src,
                    "entry": "run", "success_rule": plan.success_rule}

    goal = _goal(run_id, "leave_query")
    T.set_review_board(_PassBoard())
    try:
        res = await GenerationLoop(VulnThenClean()).run(goal, select_strategy(goal.actions))
    finally:
        T.set_review_board(None)

    assert res.ok is True, res
    assert res.iterations[0].passed is False
    assert any("漏洞校验" in r for r in res.iterations[0].reasons), res.iterations[0].reasons
    repo = AssetRepository()
    pubs = await repo.list_published(AssetType.ADAPTER, Scope(tenant="gen2", subsystem=Subsystem.OA))
    assert [e.asset_key for e in pubs] == ["leave_query"], pubs


async def test_review_gate_blocks_publish_even_if_code_clean():
    from dano.agent_tools import tools as T
    from dano.assets.repository import AssetRepository
    from dano.generation import GenerationLoop
    from dano.generation.strategies import select_strategy
    from dano.shared.enums import AssetType, Subsystem
    from dano.shared.models import Scope

    run_id = "gen2-rev"
    await _materials(run_id)

    class Clean:
        async def generate(self, *, plan, feedback):  # noqa: ANN001
            return {"action": plan.flow, "strategy": plan.strategy,
                    "source": "def run(inputs, creds):\n    return {'code': 200}\n",
                    "entry": "run", "success_rule": plan.success_rule}

    goal = _goal(run_id, "blocked_flow", budget=2)
    T.set_review_board(_RejectBoard())                  # security 永远驳回
    try:
        res = await GenerationLoop(Clean()).run(goal, select_strategy(goal.actions))
    finally:
        T.set_review_board(None)

    assert res.ok is False                              # 评审挡住,始终不发布
    assert any("security" in r for it in res.iterations for r in it.reasons), res.iterations
    repo = AssetRepository()
    pubs = await repo.list_published(AssetType.ADAPTER, Scope(tenant="gen2", subsystem=Subsystem.OA))
    assert all(e.asset_key != "blocked_flow" for e in pubs)
