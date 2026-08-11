from __future__ import annotations

import asyncio
import time

import pytest

from dano.execution.page.flow_spec import (
    FlowCapability,
    FlowLink,
    FlowSpec,
    FlowStep,
    ParamField,
    RequestAnalysis,
    RequestFact,
    RequestFacts,
    SelectBinding,
    apply_recording_agent_submission,
    apply_flow_edits,
)
from dano.execution.page.replay import evaluate_assertion, execute_write_with_verify
from dano.execution.page.recording_live import dependency_link_signature
from dano.execution.page.verification_log import (
    _clear_verifications_for_tests,
    get_verification,
    record_verification,
)
from dano.onboarding.recording_verify import (
    recorded_goal_slug,
    require_verification_complete,
    run_recording_verification,
    verification_report,
    verification_todos,
)
from dano.onboarding.recording_pi import RecordingPiSession


@pytest.fixture(autouse=True)
def _verification_log_isolation():
    _clear_verifications_for_tests()
    yield
    _clear_verifications_for_tests()


def _spec() -> FlowSpec:
    return FlowSpec(
        steps=[
            FlowStep(
                step_id="detail",
                method="GET",
                path="/items/detail",
                source_meta={"request_id": "req-detail"},
            ),
            FlowStep(
                step_id="submit",
                method="POST",
                path="/items/update",
                source_meta={"request_id": "req-submit"},
                params=[ParamField(path="body.kind", key="kind")],
                selects=[SelectBinding(path="body.kind", source_request_id="req-options", enum_confirmed=False)],
            ),
        ],
        links=[FlowLink(
            link_id="link-1",
            source_step_id="detail",
            source_path="response.data.jobId",
            target_step_id="submit",
            target_path="body.jobId",
            evidence={"source_request_id": "req-detail", "target_request_id": "req-submit"},
            meta={"actor": "agent", "verified": False},
        )],
        capabilities=[FlowCapability(name="update_item", kind="submit", confidence=0.4)],
        request_facts=RequestFacts(
            requests=[
                RequestFact(request_id="req-detail", method="GET", path="/items/detail"),
                RequestFact(request_id="req-submit", method="POST", path="/items/update"),
                RequestFact(request_id="req-verify", method="GET", path="/items/detail"),
                RequestFact(request_id="req-options", method="GET", path="/items/options"),
            ],
            analysis={
                "req-submit": RequestAnalysis(
                    request_id="req-submit",
                    role="business_write",
                    reason="captured write",
                    evidence={"actor": "heuristic"},
                ),
            },
        ),
    )


def _spec_with_unproposed_value_link() -> FlowSpec:
    return FlowSpec(
        steps=[
            FlowStep(
                step_id="detail",
                method="GET",
                path="/items/detail",
                source_meta={"request_id": "req-detail"},
            ),
            FlowStep(
                step_id="submit",
                method="POST",
                path="/items/update",
                params=[ParamField(path="body.jobId", key="jobId", value="JOB998877")],
                source_meta={"request_id": "req-submit"},
            ),
        ],
        request_facts=RequestFacts(requests=[
            RequestFact(
                request_id="req-detail",
                request_index=1,
                method="GET",
                path="/items/detail",
                response_json={"data": {"jobId": "JOB998877"}},
            ),
            RequestFact(
                request_id="req-submit",
                request_index=2,
                method="POST",
                path="/items/update",
                post_data='{"jobId":"JOB998877"}',
            ),
        ]),
    )


def _record_dependency_verification(spec: FlowSpec, link_id: str = "link-1", *, status: str = "passed") -> str:
    link = next(item for item in spec.links if item.link_id == link_id)
    return record_verification(
        kind="dependency_execute",
        subject={"link_id": link.link_id, "signature": dependency_link_signature(link)},
        status=status,
        evidence={"injection_equal": status == "passed"},
        failure_reason="dependency verification failed" if status != "passed" else "",
    )


def test_high_confidence_value_link_becomes_dependency_candidate_todo():
    todos = verification_todos(_spec_with_unproposed_value_link())

    candidate = next(item for item in todos if item["kind"] == "dependency_candidate")
    assert candidate["target_id"] == candidate["link_id"]
    assert candidate["source_request_id"] == "req-detail"
    assert candidate["source_path"] == "response.data.jobId"
    assert candidate["target_request_id"] == "req-submit"
    assert candidate["target_path"] == "body.jobId"
    assert candidate["suggested_tool"] == "submit_recording_repair"
    assert candidate["completion_ops"] == ["propose_dependency", "verify_dependency", "confirm_dependency"]


def test_candidate_link_id_supports_propose_verify_then_confirm():
    spec = _spec_with_unproposed_value_link()
    candidate = next(item for item in verification_todos(spec) if item["kind"] == "dependency_candidate")
    proposed = apply_flow_edits(spec, [{
            "op": "propose_dependency",
            "link_id": candidate["link_id"],
            "source_request_id": candidate["source_request_id"],
            "source_path": candidate["source_path"],
            "target_request_id": candidate["target_request_id"],
            "target_path": candidate["target_path"],
            "evidence": {"heuristic_candidate": True},
        }])
    verification_id = _record_dependency_verification(proposed, candidate["link_id"])
    updated = apply_flow_edits(proposed, [{
            "op": "confirm_dependency",
            "link_id": candidate["link_id"],
            "verification_id": verification_id,
        }])

    assert updated.links[0].link_id == candidate["link_id"]
    assert updated.links[0].confirmed is True
    assert [
        item["op"] for item in (updated.meta or {}).get("recording_agent_ops") or []
    ] == ["propose_dependency", "confirm_dependency"]
    assert not any(item["kind"].startswith("dependency") for item in verification_todos(updated))

    replayed = apply_flow_edits(updated, [{
            "op": "propose_dependency",
            "link_id": candidate["link_id"],
            "source_request_id": candidate["source_request_id"],
            "source_path": candidate["source_path"],
            "target_request_id": candidate["target_request_id"],
            "target_path": candidate["target_path"],
            "evidence": {"heuristic_candidate": True},
        }])
    replayed = apply_flow_edits(replayed, [{
            "op": "confirm_dependency",
            "link_id": candidate["link_id"],
            "verification_id": verification_id,
        }])
    assert len(replayed.links) == 1
    assert [
        item["op"] for item in (replayed.meta or {}).get("recording_agent_ops") or []
    ] == ["propose_dependency", "confirm_dependency"]


@pytest.mark.asyncio
async def test_repair_submission_persists_candidate_propose_and_confirm_ops():
    spec = _spec_with_unproposed_value_link()
    spec.capabilities = []
    candidate = next(item for item in verification_todos(spec) if item["kind"] == "dependency_candidate")
    proposed = await apply_recording_agent_submission(
        spec,
        mode="repair",
        submission={"ops": [{
                "op": "propose_dependency",
                "link_id": candidate["link_id"],
                "source_request_id": candidate["source_request_id"],
                "source_path": candidate["source_path"],
                "target_request_id": candidate["target_request_id"],
                "target_path": candidate["target_path"],
                "evidence": {"heuristic_candidate": True},
            }]},
    )
    verification_id = _record_dependency_verification(proposed, candidate["link_id"])
    updated = await apply_recording_agent_submission(
        proposed,
        mode="repair",
        submission={"ops": [{
                "op": "confirm_dependency",
                "link_id": candidate["link_id"],
                "verification_id": verification_id,
            }]},
    )

    assert len(updated.links) == 1
    assert updated.links[0].link_id == candidate["link_id"]
    assert updated.links[0].confirmed is True
    assert [
        item["op"] for item in (updated.meta or {}).get("recording_agent_ops") or []
    ] == ["propose_dependency", "confirm_dependency"]
    assert not any(item["kind"].startswith("dependency") for item in verification_todos(updated))


def test_record_count_assertion_checks_the_verify_collection():
    empty = evaluate_assertion(
        {"list": [], "total": 0},
        {"verify_records_min_count": 1},
        {},
    )
    populated = evaluate_assertion(
        {"data": {"list": [{"id": "new-record"}], "total": 1}},
        {"verify_records_min_count": 1},
        {},
    )

    assert empty["passed"] is False
    assert empty["actual"] == 0
    assert populated["passed"] is True
    assert populated["actual"] == 1


def test_unknown_assertion_keys_are_rejected_instead_of_falling_back_to_truthy():
    with pytest.raises(ValueError, match="unsupported assertion keys"):
        evaluate_assertion(
            {"list": [], "total": 0},
            {
                "write_response": {"code": 0},
                "verify_response": {"code": 0},
                "verify_records_min_count": 1,
            },
            {},
        )


def test_collection_assertion_requires_a_matching_record_not_an_unrelated_total():
    assertion = {
        "collection_path": "data.list",
        "where": {
            "type": {"equals_input": "type"},
            "reason": {"equals_input": "reason"},
        },
        "min_matches": 1,
    }
    response = {
        "data": {
            "list": [{"type": 1, "reason": "other"}, {"type": 2, "reason": "mine"}],
            "total": 99,
        },
    }

    assert evaluate_assertion(response, assertion, {"type": 2, "reason": "mine"})["passed"] is True
    failed = evaluate_assertion(response, assertion, {"type": 2, "reason": "missing"})
    assert failed["passed"] is False
    assert failed["actual"] == 0


def test_confirm_dependency_rejects_forged_and_mismatched_verification_ids():
    with pytest.raises(ValueError, match="verification_id"):
        apply_flow_edits(_spec(), [{
            "op": "confirm_dependency",
            "link_id": "link-1",
            "verification_id": "forged",
        }])

    wrong = record_verification(
        kind="dependency_execute",
        subject={
            "link_id": "link-other",
            "signature": dependency_link_signature(_spec().links[0]),
        },
        status="passed",
        evidence={"injection_equal": True},
    )
    with pytest.raises(ValueError, match="subject link_id"):
        apply_flow_edits(_spec(), [{
            "op": "confirm_dependency",
            "link_id": "link-1",
            "verification_id": wrong,
        }])


def test_verified_ops_confirm_link_bind_write_check_and_attach_enum():
    spec = _spec()
    spec.steps[0].params.append(ParamField(
        path="query.processVariablesStr",
        key="processVariablesStr",
        source_kind="computed",
        source={
            "kind": "computed",
            "strategy": "date_span_days_json",
            "start_field": "startTime",
            "end_field": "endTime",
            "output_key": "day",
            "sample_verified": True,
        },
    ))
    link_verification = _record_dependency_verification(spec)
    assertion = {"path": "data.kind", "equals_input": "kind"}
    write_verification = record_verification(
        kind="write_execute",
        subject={
            "write_step_id": "submit",
            "write_request_id": "req-submit",
            "verify_request_id": "req-verify",
            "assertion": assertion,
        },
        status="passed",
        evidence={"passed": True},
    )
    options = [{"label": "甲", "value": "A"}, {"label": "乙", "value": "B"}]
    enum_verification = record_verification(
        kind="enum_snapshot",
        subject={"url": "https://example.test/items"},
        status="passed",
        evidence={"snapshot": {"elements": [{"role": "combobox", "options": options}]}},
    )
    updated = apply_flow_edits(spec, [
        {"op": "confirm_dependency", "link_id": "link-1", "verification_id": link_verification},
        {
            "op": "bind_verify_read",
            "write_step_id": "submit",
            "read_request_id": "req-verify",
            "assertion": assertion,
            "verification_id": write_verification,
        },
        {
            "op": "attach_enum_options",
            "step_id": "submit",
            "path": "body.kind",
            "options": options,
            "source_request_id": "req-options",
            "verification_id": enum_verification,
        },
    ])
    assert updated.links[0].confirmed is True
    assert updated.links[0].meta["verification_id"] == link_verification
    assert updated.steps[0].params[0].source["verified"] is True
    assert updated.steps[0].params[0].source["execution_verification_id"] == link_verification
    assert updated.steps[1].fact_check["verification_id"] == write_verification
    assert updated.steps[1].selects[0].verification_id == enum_verification
    assert verification_report(updated)["all_verified"] is True


@pytest.mark.asyncio
async def test_execute_write_with_verify_records_one_grounded_composite(monkeypatch):
    calls = []

    async def fake_replay(request, **kwargs):
        calls.append((request["request_id"], kwargs))
        response = {"data": {"kind": "A"}} if request["request_id"] == "verify" else {"ok": True}
        return {
            "ok": True,
            "verification_status": "passed",
            "failure_reason": "",
            "response": response,
            "verification_id": f"verification-{request['request_id']}",
        }

    monkeypatch.setattr("dano.execution.page.replay.replay_request", fake_replay)
    result = await execute_write_with_verify(
        {"request_id": "write", "method": "POST"},
        {"request_id": "verify", "method": "GET"},
        write_step_id="submit",
        inputs={"kind": "A"},
        assertion={"path": "data.kind", "equals_input": "kind"},
        auth_headers={},
        cleanup_request={"request_id": "cleanup", "method": "DELETE"},
        settle_ms=0,
    )
    assert result["ok"] is True
    assert [call[0] for call in calls] == ["write", "verify", "cleanup"]
    record = get_verification(result["verification_id"])
    assert record["kind"] == "write_execute"
    assert record["subject"]["write_step_id"] == "submit"
    assert record["evidence"]["passed"] is True


@pytest.mark.asyncio
async def test_execute_write_with_verify_does_not_verify_an_empty_readback(monkeypatch):
    async def fake_replay(request, **_kwargs):
        response = {"list": [], "total": 0} if request["request_id"] == "verify" else {"code": 0}
        return {
            "ok": True,
            "verification_status": "passed",
            "failure_reason": "",
            "response": response,
            "verification_id": f"verification-{request['request_id']}",
        }

    monkeypatch.setattr("dano.execution.page.replay.replay_request", fake_replay)
    result = await execute_write_with_verify(
        {"request_id": "write", "method": "POST"},
        {"request_id": "verify", "method": "GET"},
        write_step_id="submit",
        inputs={"type": 2},
        assertion={"verify_records_min_count": 1},
        auth_headers={},
        settle_ms=0,
    )

    assert result["ok"] is False
    assert result["assertion"]["actual"] == 0
    assert get_verification(result["verification_id"])["evidence"]["passed"] is False


@pytest.mark.asyncio
async def test_execute_write_failure_stops_before_verify_and_cleanup(monkeypatch):
    calls = []

    async def fake_replay(request, **_kwargs):
        calls.append(request["request_id"])
        return {
            "ok": False,
            "verification_status": "failed",
            "failure_reason": "HTTP 500",
            "response": {"code": 500},
            "verification_id": "verification-write",
        }

    monkeypatch.setattr("dano.execution.page.replay.replay_request", fake_replay)
    result = await execute_write_with_verify(
        {"request_id": "write", "method": "POST"},
        {"request_id": "verify", "method": "GET"},
        write_step_id="submit",
        inputs={"kind": "A"},
        assertion={"path": "data.kind", "equals_input": "kind"},
        auth_headers={},
        cleanup_request={"request_id": "cleanup", "method": "DELETE"},
        settle_ms=0,
    )

    assert result["ok"] is False
    assert result["verify"] is None
    assert calls == ["write"]
    record = get_verification(result["verification_id"])
    assert record["status"] == "failed"
    assert record["failure_reason"] == "HTTP 500"


class _UnavailableSession:
    def __init__(self, spec: FlowSpec) -> None:
        self.flow_spec = spec

    def current_flow_spec(self):
        return self.flow_spec.model_copy(deep=True)

    def bind_flow_spec(self, spec):
        self.flow_spec = spec.model_copy(deep=True)

    async def prompt(self, *_args, **_kwargs):
        raise RuntimeError("no Pi model or credentials")


@pytest.mark.asyncio
async def test_verification_exhaustion_marks_every_todo_and_still_completes():
    session = _UnavailableSession(_spec())
    progress = []

    async def emit(payload):
        progress.append(payload)

    report = await run_recording_verification(session, progress=emit)
    assert report["complete"] is True
    assert report["all_verified"] is False
    assert {item["target_kind"] for item in report["unverified"]} == {"dependency", "write_verify", "enum"}
    assert session.flow_spec.meta["verification_run"]["complete"] is True
    assert session.flow_spec.capabilities[0].confirmed is True
    assert progress[-1]["stage"] == "completed"


@pytest.mark.asyncio
async def test_final_verification_has_one_total_deadline_and_returns_publishable_state():
    class HangingSession(_UnavailableSession):
        async def prompt(self, *_args, **_kwargs):
            await asyncio.Event().wait()

    session = HangingSession(_spec())
    started = time.monotonic()

    report = await run_recording_verification(session, timeout_s=0.02)

    assert time.monotonic() - started < 0.5
    assert report["complete"] is True
    assert report["all_verified"] is False
    assert report["errors"]
    assert session.flow_spec.meta["verification_run"]["complete"] is True
    assert session.flow_spec.meta["skill_docs_generation"]["fallback_required"] is True


@pytest.mark.asyncio
async def test_zero_operator_verification_loop_completes_with_executor_evidence():
    spec = _spec()
    spec.goal = {"intent": "Update item"}
    assertion = {"path": "data.kind", "equals_input": "kind"}
    link_id = _record_dependency_verification(spec)
    write_id = record_verification(
        kind="write_execute",
        subject={"write_step_id": "submit", "verify_request_id": "req-verify", "assertion": assertion},
        status="passed",
        evidence={"passed": True},
    )
    options = [{"label": "甲", "value": "A"}]
    enum_id = record_verification(
        kind="enum_snapshot",
        subject={"url": "https://example.test/items"},
        status="passed",
        evidence={"snapshot": {"elements": [{"options": options}]}},
    )
    spec.meta["verification_log"] = [
        get_verification(verification_id)
        for verification_id in (link_id, write_id, enum_id)
    ]

    class AgentSession(_UnavailableSession):
        verification_calls = 0
        docs_calls = 0

        async def prompt(self, text, **_kwargs):
            if "submit_skill_docs" in text:
                self.docs_calls += 1
                spec = self.current_flow_spec()
                spec.meta = {
                    **(spec.meta or {}),
                    "skill_docs": {
                        "skill_md": """---
name: update-item
description: Update one item
---

## Transport
Direct HTTP JSON.

## Preconditions
Provide runtime authentication.

## Steps
1. Run update_item.
   Done when: the response and read-back both report success.

## Branch exit
Stop on the first failed request.

## Pitfalls
Do not reuse recorded credentials.
""",
                        "reference_md": f"""# Reference

## API chain
- update_item: GET /items/detail -> POST /items/update; verification_id: {link_id}

## Business hard rules
- Do not delete records.

## Fallback browser steps
1. Use visible role/name labels, never coordinates.
""",
                    },
                }
                self.bind_flow_spec(spec)
                return {"status": "submitted"}
            assert "verification_todos" in text
            self.verification_calls += 1
            self.flow_spec = apply_flow_edits(self.flow_spec, [
                {"op": "confirm_dependency", "link_id": "link-1", "verification_id": link_id},
                {
                    "op": "bind_verify_read", "write_step_id": "submit", "read_request_id": "req-verify",
                    "assertion": assertion, "verification_id": write_id,
                },
                {
                    "op": "attach_enum_options", "step_id": "submit", "path": "body.kind",
                    "options": options, "source_request_id": "req-options", "verification_id": enum_id,
                },
            ])
            return {"status": "submitted"}

    session = AgentSession(spec)
    report = await run_recording_verification(session)
    assert session.verification_calls == 1
    assert session.docs_calls == 1
    assert report["all_verified"] is True
    assert report["unverified"] == []
    assert require_verification_complete(session.flow_spec)["all_verified"] is True
    assert session.flow_spec.capabilities[0].confirmed is True
    assert session.flow_spec.meta["skill_docs_generation"]["valid"] is True
    assert recorded_goal_slug(session.flow_spec) == "update_item"


def test_machine_publish_gate_rejects_unfinished_verification_but_has_debug_escape():
    with pytest.raises(ValueError, match="尚未完成"):
        require_verification_complete(_spec())
    assert require_verification_complete(_spec(), skip_verify=True)["complete"] is False


@pytest.mark.asyncio
async def test_verification_ops_flow_through_the_agent_repair_submission_path():
    verification_id = _record_dependency_verification(_spec())
    session = RecordingPiSession(
        tenant="tenant",
        subsystem="system",
        recording_id="recording_" + "c" * 32,
    )
    session.bind_flow_spec(_spec())
    await session.apply_submission(
        {"ops": [{
            "op": "confirm_dependency",
            "link_id": "link-1",
            "verification_id": verification_id,
        }]},
        mode="repair",
        base_flow_version=0,
    )
    assert session.current_flow_spec().links[0].meta["verified"] is True


def test_gateway_finalize_queues_verified_automatic_publish():
    import inspect

    from dano.gateway.app import record_ws

    source = inspect.getsource(record_ws)
    finalize = source[source.index('elif t == "finalize":'):source.index('elif t == "flow_update":')]
    assert "await _verify_finalized_recording()" in finalize
    assert '"type": "publish_request"' in finalize
    assert '"_auto_publish": True' in finalize
