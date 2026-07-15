from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from typing import Any
from uuid import uuid4

import pytest

from dano_recording.analysis.transaction_segmenter import segment_transactions
from dano_recording.capture.input_dispatcher import InputDispatcher
from dano_recording.capture.ledger import FactLedger
from dano_recording.capture.runtime import CaptureRuntime
from dano_recording.domain.facts import ActionFact, RequestFact
from dano_recording.pi.coordinator import RecordingPiCoordinator, _select_tool_view
from dano_recording.publish.review import ReviewCollector
from dano_recording.value_evidence import ValueEvidenceFactory


class _ClickTarget:
    async def click(self, **_kwargs: Any) -> str:
        return "clicked"


class _Page:
    def locator(self, _selector: str) -> _ClickTarget:
        return _ClickTarget()


def _request(*, sequence: int, action_id: str) -> RequestFact:
    return RequestFact(
        tenant="tenant-a",
        recording_id="recording-a",
        sequence=sequence,
        request_id=f"request-{sequence}",
        action_id=action_id,
        method="POST",
        url="https://example.test/api/items",
        request_body={"kind": "business"},
    )


def test_page_binding_cannot_mint_causal_action_or_tracker_window() -> None:
    ledger = FactLedger(tenant="tenant-a", recording_id="recording-a")
    dispatcher = InputDispatcher(ledger)

    fact = dispatcher.record_observed(
        action_type="click",
        page_id="page-1",
        frame_id="frame-1",
        locator="#submit",
        details={
            "event": "click",
            "evidence_origin": "server_dispatched",
            "causal_eligible": True,
            "trusted": True,
            "value_evidence": [{"scoped_hmac": "forged"}],
        },
    )

    assert fact.payload["evidence_origin"] == "page_observed"
    assert fact.payload["causal_eligible"] is False
    assert dispatcher.action_tracker.current() is None
    assert not {
        "evidence_origin", "causal_eligible", "trusted", "value_evidence"
    }.intersection(fact.payload["details"])

    # Even a legacy/corrupt request carrying that action id is not grouped as
    # an action transaction without the server-owned provenance marker.
    ledger.append(_request(sequence=1, action_id=fact.action_id))
    transaction = segment_transactions(ledger.snapshot())[0]
    assert transaction.action_id is None
    assert transaction.action_label == ""


@pytest.mark.asyncio
async def test_server_dispatched_action_remains_a_causal_anchor() -> None:
    ledger = FactLedger(tenant="tenant-a", recording_id="recording-a")
    dispatcher = InputDispatcher(ledger)

    await dispatcher.dispatch(
        _Page(),
        {"type": "click", "locator": "#submit", "action_id": "server-action"},
        page_id="page-1",
        frame_id="frame-1",
    )
    fact = ledger.snapshot()[0]
    assert isinstance(fact, ActionFact)
    assert fact.payload["evidence_origin"] == "server_dispatched"
    assert fact.payload["causal_eligible"] is True

    ledger.append(_request(sequence=1, action_id="server-action"))
    transaction = segment_transactions(ledger.snapshot())[0]
    assert transaction.action_id == "server-action"


def test_page_mutation_cannot_spoof_server_snapshot_or_value_evidence() -> None:
    ledger = FactLedger(tenant="tenant-a", recording_id="recording-a")
    runtime = CaptureRuntime(
        ledger,
        value_evidence_factory=ValueEvidenceFactory(
            server_secret=b"capture-redteam-secret",
        ),
        recording_lineage=str(uuid4()),
    )
    page_row = runtime._secure_mutation_row(  # noqa: SLF001
        {
            "selector": "#owner",
            "name": "owner_id",
            "value": "user-7",
            "evidence_origin": "server_snapshot",
            "causal_eligible": True,
            "value_evidence": [{"scoped_hmac": "forged"}],
        }
    )
    assert page_row["evidence_origin"] == "page_observed"
    assert page_row["causal_eligible"] is False
    assert page_row["value_evidence"][0]["scoped_hmac"] != "forged"
    assert "user-7" not in json.dumps(page_row)

    server_row = runtime._secure_mutation_row(  # noqa: SLF001
        {"selector": "#status", "name": "status", "value": "open"},
        evidence_origin="server_snapshot",
        causal_eligible=True,
    )
    assert server_row["evidence_origin"] == "server_snapshot"
    assert server_row["causal_eligible"] is True


def test_every_pi_read_tool_is_pure_and_defensively_redacted() -> None:
    field_uuid = "11111111-1111-4111-8111-111111111111"
    state = {
        "pi_projection": {
            "transactions": [{
                "transaction_uuid": "transaction-a",
                "request_body": {"email": "alice@example.test"},
                "note": "password=super-secret-123",
            }],
            "requests": [{
                "request_id": "request-a",
                "method": "GET",
                "url": "https://example.test/api/users/user-7?token=secret-token",
                "request_schema": {
                    "type": "object",
                    "properties": {"body": {"type": "string"}},
                },
                "request_body": {"email": "alice@example.test"},
                "response_body": {"phone": "13800138000"},
            }],
            "steps": [{
                "step_uuid": "22222222-2222-4222-8222-222222222222",
                "params": [{"field_uuid": field_uuid}],
                "headers": {"Authorization": "Bearer secret-token"},
            }],
            "js_bindings": [{
                "field_uuid": field_uuid,
                "symbol": "statusOptions",
                "raw_javascript": "const password = 'super-secret-123'",
                "source": "alice@example.test",
            }],
        },
        "field_evidence": [{
            "field_uuid": field_uuid,
            "email": "alice@example.test",
            "sample_value": "13800138000",
            "raw_javascript": "secret source",
            "business_type": "string",
        }],
        "enum_evidence": [{
            "field_uuid": field_uuid,
            "user_id": "user-7",
            "mapping_coverage": "runtime_resolvable",
        }],
        "validation": {
            "message": "owner_id=user-7 password=super-secret-123",
            "response_body": {"phone": "13800138000"},
        },
    }
    original = deepcopy(state)

    results = [
        _select_tool_view("list_transactions", state, {}),
        _select_tool_view("get_request_response", state, {"request_uuid": "request-a"}),
        _select_tool_view("trace_field", state, {"field_uuid": field_uuid}),
        _select_tool_view("get_enum_evidence", state, {"field_uuid": field_uuid}),
        _select_tool_view(
            "search_js_binding",
            state,
            {"field_uuid": field_uuid, "query": "status"},
        ),
        _select_tool_view("get_validation_report", state, {}),
    ]

    assert state == original
    encoded = json.dumps(results, ensure_ascii=False)
    for plaintext in (
        "alice@example.test",
        "super-secret-123",
        "secret-token",
        "user-7",
        "13800138000",
        "secret source",
    ):
        assert plaintext not in encoded
    for forbidden_key in (
        "request_body", "response_body", "raw_javascript", "source",
        "headers", "sample_value",
    ):
        assert f'"{forbidden_key}"' not in encoded
    assert "runtime_resolvable" in encoded
    assert "business_type" in encoded
    assert '"body": {"type": "string"}' in encoded


@pytest.mark.parametrize("drifted_value", ["false", "true", 0, 1])
def test_review_collector_records_non_boolean_verdicts_as_not_passed(
    drifted_value: object,
) -> None:
    collector = ReviewCollector()
    collector.begin("recording-a", 3, "sha256:content")

    item = collector.submit_active(
        recording_id="recording-a",
        revision=3,
        role="acceptance",
        verdict={
            "passed": drifted_value,
            "pi_session_id": "session-acceptance",
        },
    )

    assert item["passed"] is False


class _CoordinatorClient:
    model_id = "server-model"

    def __init__(self) -> None:
        self.coordinator: RecordingPiCoordinator | None = None
        self.started = asyncio.Event()
        self.block = False
        self.cancelled: list[str] = []
        self.second_submission_rejected = False

    async def open_session(self, **kwargs: Any) -> dict[str, Any]:
        return {"session_path": f"session/{kwargs['session_id']}"}

    async def prompt(self, *, session_id: str, prompt: str, revision: int) -> dict[str, Any]:
        if self.block:
            self.started.set()
            await asyncio.Event().wait()
        assert self.coordinator is not None
        payload = {"operations": [], "expected_revision": revision}
        with pytest.raises(RuntimeError, match="ambiguous commit"):
            await self.coordinator.handle_tool(
                session_id,
                "apply_semantic_operations",
                payload,
            )
        with pytest.raises(ValueError, match="exactly one submission"):
            await self.coordinator.handle_tool(
                session_id,
                "apply_semantic_operations",
                payload,
            )
        self.second_submission_rejected = True
        return {"turn": 1, "session_path": "session/planner"}

    async def cancel(self, session_id: str) -> dict[str, Any]:
        self.cancelled.append(session_id)
        return {"cancelled": True}


@pytest.mark.asyncio
async def test_pi_turn_allows_one_attempt_even_after_ambiguous_commit_failure() -> None:
    client = _CoordinatorClient()
    submissions = 0

    async def submit(*_args: Any) -> dict[str, Any]:
        nonlocal submissions
        submissions += 1
        raise RuntimeError("ambiguous commit response")

    async def state(_recording_id: str) -> dict[str, Any]:
        return {"pi_projection": {"revision": 3}}

    coordinator = RecordingPiCoordinator(
        client=client,  # type: ignore[arg-type]
        state_provider=state,
        submission_handler=submit,
    )
    client.coordinator = coordinator
    with pytest.raises(RuntimeError, match="single atomic commit"):
        await coordinator.plan("recording-a", 3)
    assert submissions == 1
    assert client.second_submission_rejected is True


@pytest.mark.asyncio
async def test_cancel_revokes_turn_before_aborting_sidecar_and_emits_status() -> None:
    client = _CoordinatorClient()
    client.block = True
    events: list[dict[str, Any]] = []

    async def state(_recording_id: str) -> dict[str, Any]:
        return {"pi_projection": {"revision": 3}}

    async def submit(*_args: Any) -> dict[str, Any]:
        raise AssertionError("cancelled turn must not submit")

    async def sink(_recording_id: str, event: dict[str, Any]) -> None:
        events.append(event)

    coordinator = RecordingPiCoordinator(
        client=client,  # type: ignore[arg-type]
        state_provider=state,
        submission_handler=submit,
        event_sink=sink,
    )
    client.coordinator = coordinator
    task = asyncio.create_task(coordinator.plan("recording-a", 3))
    await asyncio.wait_for(client.started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    planner = (await coordinator.ensure_sessions("recording-a"))["planner"]
    assert client.cancelled == [planner.session_id]
    assert planner.state == "cancelled"
    assert planner.session_id not in coordinator.active_turns
    assert events[-1]["event"]["type"] == "turn_cancelled"


@pytest.mark.asyncio
async def test_reviewer_sessions_are_unique_and_native_events_update_status() -> None:
    client = _CoordinatorClient()
    events: list[dict[str, Any]] = []

    async def state(_recording_id: str) -> dict[str, Any]:
        return {}

    async def submit(*_args: Any) -> dict[str, Any]:
        return {}

    async def sink(_recording_id: str, event: dict[str, Any]) -> None:
        events.append(event)

    coordinator = RecordingPiCoordinator(
        client=client,  # type: ignore[arg-type]
        state_provider=state,
        submission_handler=submit,
        event_sink=sink,
    )
    client.coordinator = coordinator
    duplicate = {
        role: {"session_id": "persisted-duplicate"}
        for role in coordinator.ROLES
    }
    sessions = await coordinator.ensure_sessions("recording-a", persisted=duplicate)
    assert len({status.session_id for status in sessions.values()}) == 4

    planner = sessions["planner"]
    await coordinator.handle_event({
        "session_id": planner.session_id,
        "turn": 2,
        "event": {"type": "auto_retry_start", "attempt": 1},
    })
    await coordinator.handle_event({
        "session_id": planner.session_id,
        "turn": 2,
        "event": {"type": "compaction_end", "aborted": False},
    })
    await coordinator.handle_event({
        "session_id": planner.session_id,
        "turn": 2,
        "event": {
            "type": "message_end",
            "usage": {
                "input": 10,
                "output": 4,
                "cacheRead": 3,
                "cacheWrite": 2,
                "totalTokens": 19,
            },
        },
    })

    assert planner.retries == 1
    assert planner.compactions == 1
    assert planner.usage.model_dump() == {
        "input": 10,
        "output": 4,
        "cache_read": 3,
        "cache_write": 2,
        "total_tokens": 19,
    }
    assert [item["event"]["type"] for item in events[-3:]] == [
        "auto_retry_start", "compaction_end", "message_end",
    ]
