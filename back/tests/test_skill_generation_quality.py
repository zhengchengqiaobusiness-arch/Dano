"""Runnable stage-8 handbook metrics from the WeChat evaluation list."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from dano.export.skill_package.validator import validate_skill_package
from dano.onboarding.recording_stage_seven import working_fingerprint
from dano.onboarding.skill_generation.export import _default_render, export_recording_skill
from dano.onboarding.skill_generation.models import PlanningMode, SkillGenerationRequest
from dano.onboarding.skill_generation.planner import propose_deterministic_plan
from dano.onboarding.skill_generation.quality import (
    combo_pairs,
    context_load_cost,
    human_correction_ready,
    match_routes,
    should_stop_but_continued,
    silent_branch_drops,
    silent_sequence_drop,
    skill_hit,
    tool_invocation_ready,
    unnecessary_asks,
    write_capability_ids,
)
from dano.onboarding.skill_generation.validate import validate_skill_plan
from stage8_sale_order_fixture import (
    SALE_ORDER_EXPLICIT_COMBOS,
    sale_order_request,
    sale_order_spec,
    sale_order_verified_ids,
)
from test_skill_generation_export import _ok_publish, _sale_verified_body, _deterministic_proposer
from test_skill_generation_plan import VERIFIED, _three_cap_spec


def test_sale_order_skill_hit_and_leave_skill_miss() -> None:
    sale = propose_deterministic_plan(
        sale_order_spec(),
        sale_order_request(),
        sale_order_verified_ids(),
        "fp-quality-sale",
    )
    leave_spec = _three_cap_spec()
    leave = propose_deterministic_plan(
        leave_spec,
        SkillGenerationRequest(
            title="请假办理",
            business_description="用户可以查询待办记录，也可以查询后选择一条记录进行提交。",
            planning_mode=PlanningMode.DYNAMIC,
        ),
        VERIFIED,
        "fp-quality-leave",
    )
    utterance = "帮我查鲜生的单"
    assert skill_hit(sale, utterance)
    assert not skill_hit(leave, utterance)
    assert not silent_sequence_drop(sale, sale_order_spec())


def test_sale_order_route_match_and_read_only_does_not_write() -> None:
    spec = sale_order_spec()
    plan = propose_deterministic_plan(spec, sale_order_request(), sale_order_verified_ids(spec), "fp-quality-route")
    search = match_routes(plan, "帮我查鲜生的单")
    assert search
    assert search[0].capability_sequence == ["cap_search"]
    assert not any(cap_id in write_capability_ids(spec) for cap_id in search[0].capability_sequence)
    edit = match_routes(plan, "先查出订单再编辑")
    assert edit
    assert edit[0].capability_sequence[:2] == ["cap_search", "cap_update"]


def test_consecutive_utterance_hits_combo_and_not_leave_skill() -> None:
    spec = _three_cap_spec(confirmed_query_submit=False, confirmed_option_submit=False)
    plan = propose_deterministic_plan(
        spec,
        SkillGenerationRequest(
            title="请假办理",
            business_description="查询完成紧接着提交请假",
            example_requests=["查询完成紧接着提交请假"],
            planning_mode=PlanningMode.DYNAMIC,
        ),
        VERIFIED,
        "fp-quality-consecutive",
    )
    utterance = "查询完成紧接着提交请假"
    assert skill_hit(plan, utterance)
    assert not silent_sequence_drop(plan, spec)
    matched = match_routes(plan, utterance)
    assert matched
    assert matched[0].capability_sequence[:2] == ["cap_query", "cap_submit"]


def test_quality_gate_catches_real_order_phrases_and_not_immediate_write() -> None:
    spec = _three_cap_spec(confirmed_query_submit=False, confirmed_option_submit=False)
    phrases = (
        "查询结束就提交请假",
        "查询继而提交请假",
        "查询优先，提交其次",
        "查询在前，提交在后",
        "完成查询方可提交",
    )
    for phrase in phrases:
        plan = propose_deterministic_plan(
            spec,
            SkillGenerationRequest(
                title="请假办理",
                business_description=phrase,
                example_requests=[phrase],
                planning_mode=PlanningMode.DYNAMIC,
            ),
            VERIFIED,
            f"fp-quality-{phrase}",
        )
        assert skill_hit(plan, phrase), phrase
        assert not silent_sequence_drop(plan, spec), phrase
        matched = match_routes(plan, phrase)
        assert matched, phrase
        assert matched[0].capability_sequence[:2] == ["cap_query", "cap_submit"], phrase
    immediate = propose_deterministic_plan(
        spec,
        SkillGenerationRequest(
            title="请假办理",
            business_description="马上提交请假",
            example_requests=["马上提交请假"],
            planning_mode=PlanningMode.DYNAMIC,
        ),
        VERIFIED,
        "fp-quality-immediate",
    )
    assert skill_hit(immediate, "马上提交请假")
    assert not silent_sequence_drop(immediate, spec)
    matched = match_routes(immediate, "马上提交请假")
    assert matched
    assert matched[0].capability_sequence == ["cap_submit"]
    assert not immediate.clarification_questions


def test_explicit_branches_have_zero_silent_drops() -> None:
    spec = sale_order_spec()
    plan = propose_deterministic_plan(spec, sale_order_request(), sale_order_verified_ids(spec), "fp-quality-drop")
    assert combo_pairs(plan) >= set(SALE_ORDER_EXPLICIT_COMBOS)
    assert silent_branch_drops(plan) == []
    assert not plan.clarification_questions


def test_unbound_combo_must_stop_and_bound_combo_must_not_reask() -> None:
    unbound_spec = _three_cap_spec(confirmed_query_submit=False, confirmed_option_submit=False)
    unbound = propose_deterministic_plan(
        unbound_spec,
        SkillGenerationRequest(
            title="请假",
            business_description="先查询待办记录再提交请假。",
            planning_mode=PlanningMode.DYNAMIC,
        ),
        VERIFIED,
        "fp-quality-stop",
    )
    writes = write_capability_ids(unbound_spec)
    continued = [
        issue
        for route in unbound.routes
        for issue in should_stop_but_continued(route, write_ids=writes)
    ]
    assert continued == []
    combo = next(route for route in unbound.routes if route.capability_sequence[:2] == ["cap_query", "cap_submit"])
    assert combo.checkpoints
    assert tool_invocation_ready(combo) == []
    assert human_correction_ready(combo) == []

    bound_spec = _three_cap_spec()
    bound = propose_deterministic_plan(
        bound_spec,
        SkillGenerationRequest(
            title="请假",
            business_description="先查询待办记录再提交请假。",
            planning_mode=PlanningMode.DYNAMIC,
        ),
        VERIFIED,
        "fp-quality-bound",
    )
    bound_combo = next(
        route
        for route in bound.routes
        if route.capability_sequence[:2] == ["cap_query", "cap_submit"] and route.bindings
    )
    assert unnecessary_asks(bound_combo) == []


@pytest.mark.asyncio
async def test_atomic_context_does_not_load_all_combo_files(tmp_path: Path) -> None:
    spec = sale_order_spec()
    request = sale_order_request()
    request.out_dir = str(tmp_path)
    plan = propose_deterministic_plan(spec, request, sale_order_verified_ids(spec), working_fingerprint(spec))
    checked = validate_skill_plan(
        plan,
        spec,
        verified_capability_ids=sale_order_verified_ids(spec),
        expected_fingerprint=working_fingerprint(spec),
    )
    assert checked.ok, checked.errors
    outcome = await export_recording_skill(
        result_id=uuid4(),
        body=_sale_verified_body(spec),
        tenant="tenant",
        request=request,
        persist=lambda _body: None,
        publish=_ok_publish,
        render=_default_render,
        proposer=_deterministic_proposer,
    )
    assert outcome.status == "exported", outcome.errors
    root = Path(outcome.export_path)
    assert validate_skill_package(root)["ok"]
    atomic = next(route for route in plan.routes if route.capability_sequence == ["cap_search"])
    cost = context_load_cost(root, atomic.route_id)
    assert cost["handbook_chars"] < 8000
    combo_dir = root / "references" / "routes"
    assert combo_dir.is_dir()
    assert cost["all_route_chars"] > cost["route_chars"]
    skill_md = (root / "SKILL.md").read_text(encoding="utf-8")
    assert "不必读取组合路线" in skill_md
    assert not (combo_dir / f"{atomic.route_id}.md").is_file()
