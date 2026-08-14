"""Regression contracts for recording self-healing and operator takeover."""

from __future__ import annotations

from pathlib import Path

from dano.execution.page.capability_compiler import compile_capabilities
from dano.execution.page.flow_spec import (
    CapabilityRequestRef,
    FlowSpec,
    FlowStep,
    ParamField,
    RequestFact,
    RequestFacts,
)
from dano.onboarding.recording_release import evaluate_recording_release
from dano.onboarding.recording_verify import verification_report


def _fact_check_query_leaking_into_write() -> FlowSpec:
    spec = FlowSpec(
        steps=[
            FlowStep(
                step_id="query",
                method="GET",
                path="/items",
                params=[ParamField(
                    path="query.pageNo",
                    key="pageNo",
                    value=1,
                    category="user_param",
                    source_kind="page_context",
                    exposed_to_user=True,
                    source={
                        "context_key": "pageNo",
                        "default_value": 1,
                        "caller_override": True,
                        "required_state": "optional",
                    },
                )],
                source_meta={"request_id": "req-query", "role": "business_get"},
                response_json={"data": {"list": [], "total": 0}},
            ),
            FlowStep(
                step_id="submit",
                method="POST",
                path="/items/create",
                body_source='{"reason":"recorded"}',
                body_template={"reason": "{{reason}}"},
                params=[ParamField(
                    path="body.reason",
                    key="reason",
                    value="recorded",
                    category="user_param",
                    source_kind="user_input",
                    exposed_to_user=True,
                    source={"required_state": "required"},
                )],
                source_meta={"request_id": "req-submit", "role": "business_write"},
                fact_check={"verified": True, "verification_id": "verify-write"},
            ),
        ],
        request_facts=RequestFacts(requests=[
            RequestFact(request_id="req-query", method="GET", path="/items"),
            RequestFact(request_id="req-submit", method="POST", path="/items/create"),
        ]),
    )
    spec = compile_capabilities(spec, {"capabilities": [
        {"name": "query_items", "title": "查询项目", "kind": "query", "anchor_step_id": "query"},
        {"name": "submit_item", "title": "提交项目", "kind": "submit", "anchor_step_id": "submit"},
    ]}).spec
    submit = next(item for item in spec.capabilities if item.name == "submit_item")
    submit.request_refs.append(CapabilityRequestRef(
        request_id="req-query",
        step_id="query",
        method="GET",
        path="/items",
        usage="fact_check",
        origin="verified_dependency",
    ))
    submit.nodes.append({"id": "fact_check_query", "type": "call", "step_id": "query"})
    return spec


def test_release_exposes_structured_repair_issues() -> None:
    decision = evaluate_recording_release(_fact_check_query_leaking_into_write())
    submit = next(item for item in decision.to_dict()["capabilities"] if item["name"] == "submit_item")

    assert submit["issues"]
    assert {
        "issue_id", "check_code", "capability_id", "step_id", "field_id",
        "wire_path", "resolver", "evidence_refs", "suggested_operations", "message",
    } <= set(submit["issues"][0])


def test_fact_check_query_fields_do_not_become_write_capability_inputs() -> None:
    decision = evaluate_recording_release(_fact_check_query_leaking_into_write())
    submit = next(item for item in decision.capabilities if item.name == "submit_item")

    assert not any("query.pageNo" in reason for reason in submit.reasons)


def test_pagination_context_survives_capability_compilation() -> None:
    spec = _fact_check_query_leaking_into_write()
    page = next(param for param in spec.steps[0].params if param.path == "query.pageNo")

    assert page.source_kind == "page_context"
    assert page.category == "user_param"
    assert page.exposed_to_user is True
    assert page.editable is True
    assert page.required is False
    assert page.source["kind"] == "page_context"
    assert page.source["context_key"] == "pageNo"
    assert page.source["default_value"] == 1
    assert page.source["caller_override"] is True
    assert page.source["required_state"] == "optional"


def test_unconfirmed_write_required_axis_is_an_operator_issue() -> None:
    spec = _fact_check_query_leaking_into_write()
    reason = next(param for param in spec.steps[1].params if param.path == "body.reason")
    reason.source = {**reason.source, "required_state": "unknown"}
    decision = evaluate_recording_release(spec)
    submit = next(item for item in decision.to_dict()["capabilities"] if item["name"] == "submit_item")

    issue = next(
        item for item in submit["issues"]
        if item["check_code"] == "required_axis_unconfirmed"
        and item["wire_path"] == "body.reason"
    )
    assert issue["resolver"] == "operator"
    assert issue["step_id"] == "submit"
    assert issue["wire_path"] == "body.reason"


def test_verification_report_feeds_release_blockers_back_as_todos() -> None:
    report = verification_report(_fact_check_query_leaking_into_write())

    assert report["release_issues"]
    assert any(item["kind"] == "release_issue" for item in report["todos"])


def test_runtime_review_prompt_requires_structured_self_healing_issues() -> None:
    runtime_prompt = (
        Path(__file__).resolve().parents[2]
        / "back" / "agent" / "run_recording_pi.mjs"
    ).read_text(encoding="utf-8")
    review_prompt = runtime_prompt.split("审核任务必须", 1)[1].split("不得泄漏", 1)[0]

    assert "issues" in review_prompt
    assert "final_review_rejected" in review_prompt
    assert "拒绝时" in review_prompt and "必须" in review_prompt
