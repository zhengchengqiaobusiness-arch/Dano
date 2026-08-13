from __future__ import annotations

from types import SimpleNamespace

import pytest

from dano.execution.page.capability_compiler import compile_capabilities
from dano.execution.page.flow_spec import (
    CapabilityRequestRef,
    FlowCapability,
    FlowSpec,
    FlowStep,
    ParamField,
    RequestFact,
    RequestFacts,
    prepare_flow_spec_for_publish,
)
from dano.onboarding.recording_pi import RecordingPiError, RecordingPiSession
from dano.onboarding.recording_release import evaluate_recording_release


def _mixed_spec() -> FlowSpec:
    source = FlowSpec(
        flow_id="release-policy",
        steps=[
            FlowStep(
                step_id="query",
                method="GET",
                path="/items",
                params=[ParamField(
                    path="query.pageNo",
                    key="pageNo",
                    value=1,
                    required=False,
                    category="user_param",
                    source_kind="user_input",
                    source={"required_state": "optional"},
                )],
                source_meta={"request_id": "req-query", "role": "business_get"},
                response_json={"data": {"list": [], "total": 0}},
            ),
            # Deliberately not executable: a captured write without a body.
            FlowStep(
                step_id="submit",
                method="POST",
                path="/items/create",
                params=[ParamField(
                    path="body.reason",
                    key="reason",
                    value="recorded",
                    category="user_param",
                    source_kind="user_input",
                    source={"required_state": "required"},
                )],
                source_meta={"request_id": "req-submit", "role": "business_write"},
            ),
        ],
        request_facts=RequestFacts(requests=[
            RequestFact(request_id="req-query", method="GET", path="/items"),
            RequestFact(request_id="req-submit", method="POST", path="/items/create"),
        ]),
    )
    plan = {"capabilities": [
        {"name": "query_items", "title": "查询项目", "kind": "query", "anchor_step_id": "query"},
        {"name": "submit_item", "title": "提交项目", "kind": "submit", "anchor_step_id": "submit"},
    ]}
    return compile_capabilities(source, plan).spec


def test_publish_sync_keeps_capability_anchor_execute_when_step_is_shared_preflight():
    spec = FlowSpec(
        steps=[FlowStep(
            step_id="query",
            method="GET",
            path="/items",
            source_meta={
                "request_id": "req-query",
                "role": "business_get",
                "control_preflight_for_write": True,
            },
            response_json={"data": {"list": [], "total": 0}},
        )],
        capabilities=[FlowCapability(
            name="query_items",
            kind="query_status",
            nodes=[
                {"id": "call_1", "type": "call", "step_id": "query"},
                {"id": "return_1", "type": "return", "from": "query", "path": "response"},
            ],
            request_refs=[CapabilityRequestRef(
                request_id="req-query",
                step_id="query",
                usage="preflight",
                origin="planner",
            )],
        )],
        request_facts=RequestFacts(requests=[
            RequestFact(request_id="req-query", method="GET", path="/items"),
        ]),
    )

    prepared = prepare_flow_spec_for_publish(spec)

    assert [(ref.step_id, ref.usage) for ref in prepared.capabilities[0].request_refs] == [
        ("query", "execute"),
    ]


def test_publish_sync_projects_bound_page_required_evidence_to_write_contract():
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit",
        method="POST",
        path="/items/create",
        params=[ParamField(
            path="body.reason",
            key="reason",
            required=False,
            category="user_param",
            source_kind="user_input",
            source={"required_state": "unknown"},
            evidence=[{
                "kind": "page_required",
                "source": "recorder_dom",
                "request_path": "body.reason",
                "binding_status": "bound",
            }],
        )],
    )])

    prepared = prepare_flow_spec_for_publish(spec)
    reason = prepared.steps[0].params[0]

    assert reason.required is True
    assert reason.source["required_state"] == "required"


def test_release_keeps_verified_query_callable_and_write_in_draft_only():
    draft = _mixed_spec()
    before = draft.model_dump(mode="json")

    decision = evaluate_recording_release(draft)

    assert decision.status == "partial"
    assert decision.machine_passed is True
    assert [cap.name for cap in decision.callable_spec.capabilities] == ["query_items"]
    assert {item.name: item.passed for item in decision.capabilities} == {
        "query_items": True,
        "submit_item": False,
    }
    assert draft.model_dump(mode="json") == before
    assert [cap.name for cap in draft.capabilities] == ["query_items", "submit_item"]


def test_release_returns_verification_incomplete_when_no_capability_passes():
    spec = _mixed_spec()
    spec.capabilities = [cap for cap in spec.capabilities if cap.name == "submit_item"]

    decision = evaluate_recording_release(spec)

    assert decision.status == "verification_incomplete"
    assert decision.callable_spec is None
    assert decision.machine_passed is False
    assert decision.blocking_reasons


def test_capability_validation_failure_is_a_machine_release_failure():
    spec = _mixed_spec()
    query = next(cap for cap in spec.capabilities if cap.name == "query_items")
    spec.capabilities = [query]
    query.kind = "not-a-real-capability-kind"

    decision = evaluate_recording_release(spec)

    assert decision.status == "verification_incomplete"
    item = decision.capabilities[0]
    assert item.checks["capability_validation"] is False


def test_three_true_model_verdicts_cannot_override_machine_failure():
    session = RecordingPiSession(
        tenant="tenant-a",
        subsystem="A-OA",
        recording_id="recording_11111111111111111111111111111111",
    )
    session.last_submission_kind = "review"
    session.last_review = {
        "base_flow_version": 1,
        "all_passed": True,
        "verdicts": [
            {"role": role, "passed": True, "reasons": [], "model_id": session.model_id}
            for role in ("acceptance", "security", "compliance")
        ],
    }
    machine_failure = SimpleNamespace(
        machine_passed=False,
        blocking_reasons=("submit_item: write verification missing",),
    )

    with pytest.raises(RecordingPiError, match="模型审核不能覆盖"):
        session.require_publish_review(
            flow_version=1,
            flow_fingerprint="ignored",
            machine_decision=machine_failure,
        )


def test_release_evaluates_abilities_independently_from_global_generation_marker():
    spec = _mixed_spec()
    spec.capabilities = [cap for cap in spec.capabilities if cap.name == "query_items"]
    spec.meta["capability_generation"] = {
        "protocol": "dano.capability-generation.v2",
        "initial_completed": False,
        "status": "incomplete_agent_plan",
    }

    decision = evaluate_recording_release(spec)

    assert decision.status == "ready"
    assert decision.callable_spec is not None
    assert [cap.name for cap in decision.callable_spec.capabilities] == ["query_items"]
