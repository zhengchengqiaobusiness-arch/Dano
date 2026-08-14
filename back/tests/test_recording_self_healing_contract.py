"""Red-capable contracts for the recording self-healing work.

These tests describe public release, verification and websocket behaviour.  They
start as strict xfails so the test-only baseline remains runnable; each feature
stage removes its matching xfail before changing production code.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from dano.execution.page.capability_compiler import compile_capabilities
from dano.execution.page.flow_spec import (
    CapabilityRequestRef,
    FlowSpec,
    FlowStep,
    ParamField,
    RequestFact,
    RequestFacts,
)
from dano.gateway import app as gateway
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


@pytest.mark.xfail(strict=True, reason="fact-check fields still contaminate write inputs")
def test_fact_check_query_fields_do_not_become_write_capability_inputs() -> None:
    decision = evaluate_recording_release(_fact_check_query_leaking_into_write())
    submit = next(item for item in decision.capabilities if item.name == "submit_item")

    assert not any("query.pageNo" in reason for reason in submit.reasons)


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


@pytest.mark.xfail(strict=True, reason="release blockers are not verification todos yet")
def test_verification_report_feeds_release_blockers_back_as_todos() -> None:
    report = verification_report(_fact_check_query_leaking_into_write())

    assert report["release_issues"]
    assert any(item["kind"] == "release_issue" for item in report["todos"])


@pytest.mark.xfail(strict=True, reason="termination still closes the recording session")
def test_terminate_protocol_preserves_recording_workspace() -> None:
    source = inspect.getsource(gateway.record_ws)

    assert '"type": "analysis_terminated"' in source
    assert 'raise _RecordingTerminated' not in source


@pytest.mark.xfail(strict=True, reason="frontend termination still resets stage and draft")
def test_frontend_termination_preserves_draft_stage_and_canvas() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "skillfrontend" / "src" / "components" / "PageRecorder.tsx"
    ).read_text(encoding="utf-8")
    handler = source[source.index('m.type === "analysis_terminated"'):]
    handler = handler[:handler.index('else if (m.type ===', 1)]

    for forbidden in ("resetEditorState()", "setWorkspaceStage(0)", "clearFrame()", 'setPhase("idle")'):
        assert forbidden not in handler


@pytest.mark.xfail(strict=True, reason="operator timeout still falls through instead of pausing")
def test_operator_timeout_is_a_resumable_waiting_state() -> None:
    source = inspect.getsource(gateway.record_ws)
    ask = source[source.index("async def _ask_operator"):source.index("async def _run_live_analysis")]

    assert '"status": "waiting_for_operator"' in ask
    assert "pending_operator_question" in ask


@pytest.mark.xfail(strict=True, reason="parallel custom Pi operation branches still exist")
def test_specialized_pi_mutations_use_only_plan_or_repair_entrypoints() -> None:
    source = inspect.getsource(gateway.record_ws)

    assert 'elif t == "step_naming":' not in source
    assert 'elif t == "business_description":' not in source
