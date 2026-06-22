"""Phase A1:业务规则 / 日历源入口(纯离线;materials 进程内,无 PG)。"""
from __future__ import annotations

from dano.agent_tools import materials, tools


async def test_get_business_rules_returns_registered():
    materials.register(materials.MaterialContext(
        run_id="rA1", tenant="t", system_instance_id="A-OA", subsystem="A-OA",
        business_rules=[{"rule_id": "r1", "description": "金额>1000走总监", "condition": "amount > 1000"}],
        holidays=["2026-06-03", "2026-10-01"]))
    try:
        out = await tools.get_business_rules("rA1", {"system_instance_id": "A-OA"})
        assert out["business_rules"][0]["condition"] == "amount > 1000"
        assert out["holidays"] == ["2026-06-03", "2026-10-01"]
    finally:
        materials.clear_run("rA1")


async def test_get_business_rules_empty_default():
    # 无人工规则 + openapi 无 x-flow → 规则空(生鲜 CRUD 文档不强加业务逻辑)
    materials.register(materials.MaterialContext(
        run_id="rA2", tenant="t", system_instance_id="A-OA", subsystem="A-OA",
        openapi={"openapi": "3.0.3", "paths": {"/sys/user/list": {"get": {}}}}))
    try:
        out = await tools.get_business_rules("rA2", {"system_instance_id": "A-OA"})
        assert out["business_rules"] == [] and out["holidays"] == []
    finally:
        materials.clear_run("rA2")


async def test_get_business_rules_falls_back_to_xflow():
    # 无人工规则但 swagger 写了 x-flow → 兜底抽出可 grounding 的前置(amount>0)
    materials.register(materials.MaterialContext(
        run_id="rA3", tenant="t", system_instance_id="A-OA", subsystem="A-OA",
        openapi={"openapi": "3.0.3", "paths": {"/biz/flow/submit": {"post": {"x-flow": {
            "name": "采购申请",
            "businessValidations": [{"rule": "positive", "params": ["amount", "金额"], "desc": "数值必须>0"}],
        }}}}}))
    try:
        out = await tools.get_business_rules("rA3", {"system_instance_id": "A-OA"})
        pre = [r for r in out["business_rules"] if r["kind"] == "precondition"]
        assert pre and pre[0]["check"] == "amount > 0"
    finally:
        materials.clear_run("rA3")
