"""Phase B1 验收:制度规则生成 + 用例验证(流程4)。

确定性(只需 PG,无 key/mock):draft_policy → test_policy_cases(复用运行期闸门)→ publish
→ 运行期 PolicyGate 真按规则 拦截/转审批/放行。另验:无用例不可发布、用例不符不可发布。
"""

from __future__ import annotations

import os
from uuid import UUID

import pytest

_DSN = os.environ.get("DANO_PG_DSN", "postgresql://postgres:111111@localhost:5432/dano_back")


@pytest.fixture(autouse=True)
async def _pg():
    os.environ["DANO_PG_DSN"] = _DSN
    from dano.config import get_settings
    get_settings.cache_clear()
    from dano.infra.db import close_pool, get_pool, init_pool, run_migrations
    try:
        await init_pool()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"PostgreSQL 不可用: {e}")
    await run_migrations()
    async with get_pool().acquire() as c:
        await c.execute("DELETE FROM assets WHERE tenant='ph11'")
    yield
    await close_pool()


def _register(run_id: str):
    from dano.agent_tools import materials
    materials.register(materials.MaterialContext(
        run_id=run_id, tenant="ph11", system_instance_id="A-OA", subsystem="A-OA",
        policy_text="请假超过5天需部门经理审批;超过15天一律拦截。"))


# 规则顺序重要:先拦截(>15)后转审批(>5),闸门命中即返回
_RULES = [
    {"rule_id": "r_block", "description": "超15天拦截", "condition": "days > 15", "effect": "拦截"},
    {"rule_id": "r_approve", "description": "超5天转审批", "condition": "days > 5", "effect": "转审批"},
]


async def test_policy_generate_validate_publish_and_runtime():
    from dano.agent_tools import tools as T
    from dano.assets.repository import AssetRepository
    from dano.orchestrator.gate import GateAction, PolicyGate
    from dano.shared.asset_bodies import PolicyRuleBody
    from dano.shared.enums import AssetType, RiskLevel, Subsystem
    from dano.shared.models import Scope

    run_id = "ph11-ok"
    _register(run_id)
    d = await T.draft_policy(run_id, {"system_instance_id": "A-OA", "rules": _RULES})
    res = await T.test_policy_cases(run_id, {"asset_draft_id": d["asset_draft_id"], "cases": [
        {"fields": {"days": 3}, "expect": "放行"},
        {"fields": {"days": 8}, "expect": "转审批"},
        {"fields": {"days": 20}, "expect": "拦截"},
    ]})
    assert res["passed"], res["trace"]
    pub = await T.publish_asset(run_id, {"asset_draft_id": d["asset_draft_id"],
        "validation_run_ids": res["validation_run_ids"], "review_run_ids": []})
    assert pub["published"], pub      # 制度免三模型评审,用例过即可发

    # 运行期消费:同一 get_published + PolicyGate 真按规则判定
    scope = Scope(tenant="ph11", subsystem=Subsystem.OA)
    env = await AssetRepository().get_published(AssetType.POLICY_RULE, scope, asset_key="policy_rule")
    assert env is not None, "制度规则未发布到运行期可取处"
    body = PolicyRuleBody.model_validate(env.body)
    gate = PolicyGate()
    assert gate.decide(risk_level=RiskLevel.L1, fields={"days": 20}, policy=body).action == GateAction.REJECT
    assert gate.decide(risk_level=RiskLevel.L1, fields={"days": 8}, policy=body).action == GateAction.CONFIRM
    assert gate.decide(risk_level=RiskLevel.L1, fields={"days": 3}, policy=body).action == GateAction.ALLOW


async def test_policy_publish_blocked_without_cases():
    from dano.agent_tools import tools as T
    run_id = "ph11-nocases"
    _register(run_id)
    d = await T.draft_policy(run_id, {"system_instance_id": "A-OA", "rules": _RULES})
    pub = await T.publish_asset(run_id, {"asset_draft_id": d["asset_draft_id"],
        "validation_run_ids": [], "review_run_ids": []})
    assert not pub["published"], pub
    assert "cases" in pub["reason"], pub      # 缺必需验证种类 cases


async def test_policy_cases_mismatch_blocks_publish():
    from dano.agent_tools import tools as T
    run_id = "ph11-mismatch"
    _register(run_id)
    d = await T.draft_policy(run_id, {"system_instance_id": "A-OA", "rules": _RULES})
    # days=8 实为转审批,用例却期望拦截 → 不符
    res = await T.test_policy_cases(run_id, {"asset_draft_id": d["asset_draft_id"],
        "cases": [{"fields": {"days": 8}, "expect": "拦截"}]})
    assert not res["passed"], res["trace"]
    pub = await T.publish_asset(run_id, {"asset_draft_id": d["asset_draft_id"],
        "validation_run_ids": res["validation_run_ids"], "review_run_ids": []})
    assert not pub["published"], pub          # 用例未过 → 证据 passed=False → 发布被拦
