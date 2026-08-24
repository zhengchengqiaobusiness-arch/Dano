"""Executable handbook-quality checks for stage-8 Skill planning."""

from __future__ import annotations

from dano.execution.page.flow_spec import FlowCapability, FlowSpec
from dano.onboarding.skill_generation import (
    PlanningMode,
    SkillGenerationRequest,
    propose_deterministic_plan,
)
from dano.onboarding.skill_generation.quality import (
    context_load_cost,
    human_correction_ready,
    match_routes,
    silent_branch_drops,
    silent_sequence_drop,
    tool_invocation_ready,
    unnecessary_asks,
)


def _cap(capability_id: str, title: str, kind: str, required: list[str] | None = None) -> FlowCapability:
    required = list(required or [])
    return FlowCapability(
        capability_id=capability_id,
        name=title,
        title=title,
        kind=kind,
        input_schema={
            "type": "object",
            "properties": {name: {"type": "string"} for name in required},
            "required": required,
        },
        output_schema={"type": "object"},
    )


def _plan():  # noqa: ANN202
    spec = FlowSpec(capabilities=[
        _cap("search", "查询销售订单", "query"),
        _cap("detail", "查看销售订单详情", "inspect", ["id"]),
        _cap("update", "修改销售订单", "update", ["id"]),
    ])
    plan = propose_deterministic_plan(
        spec,
        SkillGenerationRequest(
            title="销售订单",
            planning_mode=PlanningMode.DYNAMIC,
            business_description="先查询销售订单，让用户选择后查看详情；未指定目标时先查询再修改。",
            example_requests=["先查询订单，让我选一条，再修改销售订单"],
        ),
        {"search", "detail", "update"},
        "quality-fingerprint",
    )
    return spec, plan


def test_all_described_branches_compile_or_stop_for_clarification() -> None:
    spec, plan = _plan()

    assert silent_branch_drops(plan) == []
    assert silent_sequence_drop(plan, spec) is False


def test_route_match_uses_complete_combination_not_an_atomic_tail() -> None:
    _spec, plan = _plan()

    matches = match_routes(plan, "先查询订单，让我选一条，再修改销售订单")

    assert matches
    assert matches[0].capability_sequence == ["search", "update"]


def test_every_route_is_invocation_and_human_correction_ready() -> None:
    _spec, plan = _plan()

    for route in plan.routes:
        assert tool_invocation_ready(route) == []
        assert human_correction_ready(route) == []
        assert unnecessary_asks(route) == []


def test_combination_context_is_loaded_on_demand(tmp_path) -> None:
    (tmp_path / "references" / "routes").mkdir(parents=True)
    (tmp_path / "SKILL.md").write_text("原子请求不读取组合路线。", encoding="utf-8")
    (tmp_path / "references" / "routes" / "查询-然后-修改.md").write_text(
        "只包含查询后修改这一条路线。",
        encoding="utf-8",
    )

    cost = context_load_cost(tmp_path, "查询-然后-修改")

    assert cost["route_chars"] > 0
    assert cost["handbook_chars"] < 100
    assert cost["all_route_chars"] >= cost["route_chars"]
