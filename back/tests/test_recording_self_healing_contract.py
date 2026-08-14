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
from dano.onboarding.recording_verify import run_recording_verification, verification_report


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


@pytest.mark.asyncio
async def test_verification_stops_after_three_identical_no_progress_attempts() -> None:
    class NoProgressSession:
        def __init__(self) -> None:
            self.flow_spec = _fact_check_query_leaking_into_write()
            submit = next(item for item in self.flow_spec.capabilities if item.name == "submit_item")
            submit.request_refs = [item for item in submit.request_refs if item.step_id == "submit"]
            submit.nodes = [item for item in submit.nodes if item.get("step_id") != "query"]
            submit.request_refs.append(submit.request_refs[0].model_copy(deep=True))
            self.calls = 0

        def current_flow_spec(self):
            return self.flow_spec.model_copy(deep=True)

        def bind_flow_spec(self, spec):
            self.flow_spec = spec.model_copy(deep=True)

        async def prompt(self, *_args, **_kwargs):
            self.calls += 1
            return {"status": "no_change"}

    session = NoProgressSession()
    report = await run_recording_verification(session, max_rounds=99, timeout_s=10)

    assert session.calls == 3
    assert report["complete"] is False
    assert report["stop_reason"] == "no_progress"
    assert session.flow_spec.meta["verification_run"]["status"] == "no_progress"
    assert session.flow_spec.meta.get("unverified") in (None, [])


def test_terminate_protocol_preserves_recording_workspace() -> None:
    source = inspect.getsource(gateway.record_ws)

    assert '"type": "analysis_terminated"' in source
    assert 'raise _RecordingTerminated' not in source
    assert 'resume_state["analysis_generation"]' in source
    assert "pending_operator_question" in source


def test_frontend_termination_preserves_draft_stage_and_canvas() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "skillfrontend" / "src" / "components" / "PageRecorder.tsx"
    ).read_text(encoding="utf-8")
    handler = source[source.index('m.type === "analysis_terminated"'):]
    handler = handler[:handler.index('else if (m.type ===', 1)]

    for forbidden in ("resetEditorState()", "setWorkspaceStage(0)", "clearFrame()", 'setPhase("idle")'):
        assert forbidden not in handler


@pytest.mark.asyncio
async def test_verification_termination_stops_without_marking_the_draft_unverified() -> None:
    class TerminatedSession:
        def __init__(self) -> None:
            self.flow_spec = _fact_check_query_leaking_into_write()
            self.calls = 0

        def current_flow_spec(self):
            return self.flow_spec.model_copy(deep=True)

        def bind_flow_spec(self, spec):
            self.flow_spec = spec.model_copy(deep=True)

        async def prompt(self, *_args, **_kwargs):
            self.calls += 1
            return {"status": "completed"}

    session = TerminatedSession()

    async def terminate_prompt(_operation):
        _operation.close()
        return {"status": "analysis_terminated"}

    report = await run_recording_verification(
        session,
        prompt_runner=terminate_prompt,
        max_rounds=5,
        timeout_s=10,
    )

    assert session.calls == 0
    assert report["stop_reason"] == "analysis_terminated"
    assert report["complete"] is False
    assert session.flow_spec.meta.get("unverified") in (None, [])


def test_operator_timeout_is_a_resumable_waiting_state() -> None:
    source = inspect.getsource(gateway.record_ws)
    ask = source[source.index("async def _ask_operator"):source.index("async def _run_live_analysis")]
    frontend = (
        Path(__file__).resolve().parents[2]
        / "skillfrontend" / "src" / "components" / "PageRecorder.tsx"
    ).read_text(encoding="utf-8")
    runtime_prompt = (
        Path(__file__).resolve().parents[2]
        / "back" / "agent" / "run_recording_pi.mjs"
    ).read_text(encoding="utf-8")

    assert '"status": "waiting_for_operator"' in ask
    assert "pending_operator_question" in ask
    assert "asyncio.wait({future}, timeout=60)" in ask
    assert 'message.get("issue_id")' in source
    assert "issue_id: String(m.issue_id" in frontend
    assert "issue_id: question.issue_id" in frontend
    assert "按最佳假设继续" not in runtime_prompt


def test_specialized_pi_mutations_use_only_plan_or_repair_entrypoints() -> None:
    source = inspect.getsource(gateway.record_ws)
    frontend = (
        Path(__file__).resolve().parents[2]
        / "skillfrontend" / "src" / "components" / "PageRecorder.tsx"
    ).read_text(encoding="utf-8")

    assert 'elif t == "step_naming":' not in source
    assert 'elif t == "business_description":' not in source
    assert 'type: "step_naming"' not in frontend
    assert 'type: "business_description"' not in frontend
    assert 'onClick={orchestrateFlow}' in frontend
