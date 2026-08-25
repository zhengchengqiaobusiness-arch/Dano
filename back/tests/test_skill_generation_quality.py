"""Executable handbook-quality checks for stage-8 Skill planning."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

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
from dano.onboarding.skill_generation.export import _skill_draft_fields
from dano.onboarding.recording_results import (
    _refresh_business_description,
    generate_business_description,
    recording_result_detail,
    recording_result_summary,
    stage_six_result_body,
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


def test_stage_six_generates_an_editable_business_description_from_operations() -> None:
    spec = FlowSpec(capabilities=[
        _cap("search", "查询工作记录", "query"),
        _cap("create", "新增工作记录", "create", ["items"]),
    ])
    draft = spec.model_dump(mode="json")

    description = generate_business_description(draft)
    body = stage_six_result_body(
        action="work-records",
        title="工作记录",
        goal={},
        tenant="default",
        subsystem="oa",
        draft=draft,
    )

    assert "查询工作记录" in description
    assert "新增工作记录" in description
    assert "已存在项与待处理项" in description
    assert "结构化内容由调用方提供" in description
    assert "阶段" not in description
    assert "接口" not in description
    assert "录制" not in description
    assert body["skill_export_description"] == description
    assert body["skill_export_description_origin"] == "generated"
    assert body["skill_export_description_stale"] is False

    plan = propose_deterministic_plan(
        spec,
        SkillGenerationRequest(
            title="工作记录",
            planning_mode=PlanningMode.DYNAMIC,
            business_description=description,
        ),
        {"search", "create"},
        "generated-description-fingerprint",
    )
    combo = next(route for route in plan.routes if route.capability_sequence == ["search", "create"])
    assert combo.checkpoints[0].choice_source == "free_text"
    assert "确认哪些项目仍需新增" in combo.checkpoints[0].prompt
    assert "结构化内容" in combo.checkpoints[0].prompt
    assert plan.clarification_questions == []


def test_capability_change_preserves_manual_business_description() -> None:
    draft = FlowSpec(capabilities=[_cap("search", "查询工作记录", "query")]).model_dump(mode="json")
    body = {
        "skill_export_description": "这是人工确认过的办理说明。",
        "skill_export_description_origin": "manual",
        "skill_export_description_fingerprint": "old",
    }

    refreshed = _refresh_business_description(body, draft)

    assert refreshed["skill_export_description"] == "这是人工确认过的办理说明。"
    assert refreshed["skill_export_description_origin"] == "manual"
    assert refreshed["skill_export_description_stale"] is True


def test_legacy_generated_description_is_not_misclassified_as_manual() -> None:
    draft = FlowSpec(capabilities=[_cap("search", "查询工作记录", "query")]).model_dump(mode="json")
    generated = generate_business_description(draft)
    request = SkillGenerationRequest(title="工作记录", business_description=generated)

    fields = _skill_draft_fields(
        request,
        "工作记录",
        {"flow_spec": draft, "fingerprint": "current"},
    )
    manual_fields = _skill_draft_fields(
        request.model_copy(update={"business_description": generated + "\n人工补充。"}),
        "工作记录",
        {"flow_spec": draft, "fingerprint": "current"},
    )

    assert fields["skill_export_description_origin"] == "generated"
    assert manual_fields["skill_export_description_origin"] == "manual"


def test_history_summary_does_not_generate_business_description(monkeypatch) -> None:  # noqa: ANN001
    def unexpected_generation(_draft) -> str:  # noqa: ANN001
        raise AssertionError("history list must not compile a business description")

    monkeypatch.setattr(
        "dano.onboarding.recording_results.generate_business_description",
        unexpected_generation,
    )
    draft = SimpleNamespace(
        asset_draft_id=uuid4(),
        asset_key="recording-result:work-records",
        created_at=None,
        body={
            "action": "work-records",
            "title": "工作记录",
            "fingerprint": "saved-fingerprint",
            "skill_export_description": "已保存的业务描述",
            "skill_export_description_origin": "generated",
        },
    )

    summary = recording_result_summary(draft)

    assert summary["skill_export_description"] == "已保存的业务描述"
    assert summary["skill_export_description_fingerprint"] == "saved-fingerprint"


def test_result_detail_generates_description_for_legacy_result() -> None:
    spec = FlowSpec(capabilities=[_cap("search", "查询工作记录", "query")])
    draft = SimpleNamespace(
        asset_draft_id=uuid4(),
        asset_key="recording-result:work-records",
        created_at=None,
        body={
            "action": "work-records",
            "title": "工作记录",
            "fingerprint": "saved-fingerprint",
            "flow_spec": spec.model_dump(mode="json"),
        },
    )

    detail = recording_result_detail(draft)

    assert "查询工作记录" in detail["skill_export_description"]
    assert detail["skill_export_description_origin"] == "generated"
    assert detail["skill_export_description_fingerprint"] == detail["draft_fingerprint"]
