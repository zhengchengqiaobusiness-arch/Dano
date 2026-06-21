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
    materials.register(materials.MaterialContext(
        run_id="rA2", tenant="t", system_instance_id="A-OA", subsystem="A-OA"))
    try:
        out = await tools.get_business_rules("rA2", {"system_instance_id": "A-OA"})
        assert out == {"business_rules": [], "holidays": []}
    finally:
        materials.clear_run("rA2")
