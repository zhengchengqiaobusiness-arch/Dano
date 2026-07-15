from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from dano_recording.bootstrap import RecordingApplication
from dano_recording.api.protocol import CreateRecordingRequest
from dano_recording.persistence.repository import RevisionConflict
from dano_recording.pi.coordinator import RecordingPiCoordinator
from dano_recording.pi.sessions import PiSidecarClient
from dano_recording.pi_semantic_ops import (
    PiSemanticOperationError,
    apply_pi_semantic_operations,
)
from dano_recording.publish.asset_projection import project_asset
from dano_recording.publish.review import ReviewCollector
from dano_recording.publish.service import RecordingPublishService
from dano_recording.runtime.capability_executor import execute_recording_capability


def _semantic_snapshot(*, manual_name: bool = False) -> dict[str, Any]:
    field_uuid = "11111111-1111-4111-8111-111111111111"
    step_uuid = "22222222-2222-4222-8222-222222222222"
    capability_uuid = "33333333-3333-4333-8333-333333333333"
    request_definition_id = "55555555-5555-4555-8555-555555555555"
    pins = {
        f"field:{step_uuid}:{field_uuid}:display_name": "user"
    } if manual_name else {}
    return {
        "tenant": "tenant-a",
        "recording_id": "recording-a",
        "subsystem": "A-OA",
        "action": "query_items",
        "title": "Query items",
        "revision": 3,
        "meta": {"decision_origins": pins},
        "links": [],
        "request_facts": {"requests": [{
            "request_id": "request-evidence-1",
            "request_definition_id": request_definition_id,
            "method": "DELETE",
            "url": "https://oa.example/api/items/7",
            "path": "/api/items/7",
            "disposition": "materialized",
            "response_schema": {"type": "object", "properties": {"id": {"type": "string"}}},
        }]},
        "steps": [{
            "step_id": step_uuid,
            "step_uuid": step_uuid,
            "request_id": "request-evidence-1",
            "request_definition_id": request_definition_id,
            "method": "DELETE",
            "url": "https://oa.example/api/items/7",
            "path": "/api/items/7",
            "risk_level": "L1",
            "requires_confirmation": False,
            "response_schema": {"type": "object", "properties": {"id": {"type": "string"}}},
            "params": [{
                "field_uuid": field_uuid,
                "field_contract_id": field_uuid,
                "field_id": field_uuid,
                "path": "query.kind",
                "location": "query",
                "wire_path": "kind",
                "name": "Human name" if manual_name else "kind",
                "display_name": "Human name" if manual_name else "kind",
                "type": "string",
                "business_type": "string",
                "required": False,
                "value_provider": {"kind": "user_input"},
                "evidence_ids": ["request-evidence-1"],
            }],
        }],
        "capabilities": [{
            "capability_id": capability_uuid,
            "capability_uuid": capability_uuid,
            "name": "delete_item",
            "step_ids": [step_uuid],
            "step_uuids": [step_uuid],
            "request_refs": [{
                "request_id": "request-evidence-1",
                "request_definition_id": request_definition_id,
                "step_id": step_uuid,
                "step_uuid": step_uuid,
            }],
            "risk_level": "L1",
            "requires_confirmation": False,
        }],
    }


def _operation(*, axis: str, value: Any, target: str = "11111111-1111-4111-8111-111111111111") -> dict[str, Any]:
    return {
        "op": "set_field_axis",
        "target_uuid": target,
        "axis": axis,
        "value": value,
        "evidence_ids": ["request-evidence-1"],
        "confidence": 0.9,
        "expected_revision": 3,
    }


def test_pi_operations_are_atomic_evidence_bound_and_preserve_manual_axis() -> None:
    snapshot = _semantic_snapshot(manual_name=True)
    with pytest.raises(PiSemanticOperationError, match="manual field axis"):
        apply_pi_semantic_operations(snapshot, {
            "expected_revision": 3,
            "operations": [_operation(axis="display_name", value="Pi name")],
        })
    assert snapshot["steps"][0]["params"][0]["display_name"] == "Human name"

    result = apply_pi_semantic_operations(snapshot, {
        "expected_revision": 3,
        "operations": [_operation(axis="business_type", value="integer")],
    })
    field = result["steps"][0]["params"][0]
    assert field["display_name"] == "Human name"
    assert field["business_type"] == "integer"
    assert field["axis_decisions"]["business_type"]["origin"] == "pi"
    # Pi cannot lower a captured DELETE, regardless of which semantic axis it changes.
    assert result["steps"][0]["risk_level"] == "L3"
    assert result["capabilities"][0]["risk_level"] == "L3"
    assert result["capabilities"][0]["requires_confirmation"] is True


def test_pi_batch_rolls_back_on_unknown_target_evidence_or_revision() -> None:
    snapshot = _semantic_snapshot()
    original = deepcopy(snapshot)
    invalid = _operation(
        axis="business_type",
        value="integer",
        target="99999999-9999-4999-8999-999999999999",
    )
    with pytest.raises(PiSemanticOperationError, match="target does not exist"):
        apply_pi_semantic_operations(snapshot, {
            "expected_revision": 3,
            "operations": [_operation(axis="business_type", value="number"), invalid],
        })
    assert snapshot == original


@pytest.mark.parametrize(
    ("operation", "manual_axis"),
    [
        ({
            "op": "set_capability_name",
            "target_uuid": "33333333-3333-4333-8333-333333333333",
            "value": "pi_rename",
        }, "name"),
        ({
            "op": "delete_capability",
            "target_uuid": "33333333-3333-4333-8333-333333333333",
            "value": None,
        }, "membership"),
        ({
            "op": "split_capability",
            "target_uuid": "33333333-3333-4333-8333-333333333333",
            "value": {"capabilities": []},
        }, "membership"),
        ({
            "op": "move_request_to_capability",
            "target_uuid": "55555555-5555-4555-8555-555555555555",
            "value": {
                "capability_uuid": "33333333-3333-4333-8333-333333333333",
            },
        }, "membership"),
    ],
)
def test_pi_capability_operations_cannot_override_manual_semantic_axes(
    operation: dict[str, Any],
    manual_axis: str,
) -> None:
    snapshot = _semantic_snapshot()
    capability = snapshot["capabilities"][0]
    capability["semantic_decisions"] = {
        manual_axis: {
            "origin": "manual",
            "manual_override": True,
            "revision": 3,
        }
    }
    operation = {
        **operation,
        "evidence_ids": ["request-evidence-1"],
        "confidence": 0.9,
        "expected_revision": 3,
    }
    with pytest.raises(PiSemanticOperationError, match="manual capability"):
        apply_pi_semantic_operations(
            snapshot,
            {"expected_revision": 3, "operations": [operation]},
        )


def test_pi_cannot_recreate_capability_manually_deleted_by_user() -> None:
    snapshot = _semantic_snapshot()
    deleted_id = "44444444-4444-4444-8444-444444444444"
    snapshot["meta"]["decision_origins"][
        f"capability:{deleted_id}:deleted"
    ] = "user"
    operation = {
        "op": "create_capability",
        "target_uuid": snapshot["steps"][0]["step_id"],
        "value": {
            "capability_uuid": deleted_id,
            "name": "recreated_by_pi",
            "step_uuids": [snapshot["steps"][0]["step_uuid"]],
        },
        "evidence_ids": ["request-evidence-1"],
        "confidence": 0.9,
        "expected_revision": 3,
    }
    with pytest.raises(PiSemanticOperationError, match="manual capability"):
        apply_pi_semantic_operations(
            snapshot,
            {"expected_revision": 3, "operations": [operation]},
        )
    assert snapshot["steps"][0]["params"][0]["business_type"] == "string"

    bad_evidence = _operation(axis="classification", value="business")
    bad_evidence["evidence_ids"] = ["invented-proof"]
    with pytest.raises(PiSemanticOperationError, match="unknown evidence"):
        apply_pi_semantic_operations(snapshot, {
            "expected_revision": 3, "operations": [bad_evidence],
        })
    with pytest.raises(PiSemanticOperationError, match="revision conflict"):
        apply_pi_semantic_operations(snapshot, {
            "expected_revision": 2, "operations": [],
        })


@pytest.mark.parametrize("operation", ["link_field_binding", "unlink_field_binding"])
@pytest.mark.parametrize("manual_location", ["field", "registry"])
def test_pi_binding_operations_cannot_bypass_manual_source_axis(
    operation: str,
    manual_location: str,
) -> None:
    snapshot = _semantic_snapshot()
    field = snapshot["steps"][0]["params"][0]
    manual_decision = {
        "source_binding": {
            "origin": "manual",
            "manual_override": True,
            "revision": 3,
        }
    }
    if manual_location == "field":
        field["axis_decisions"] = manual_decision
    else:
        snapshot["field_registry"] = {
            "fields": [{
                "field_uuid": field["field_uuid"],
                "decisions": manual_decision,
            }]
        }
    value: Any = {
        "binding_uuid": "66666666-6666-4666-8666-666666666666",
        "request_definition_id": "55555555-5555-4555-8555-555555555555",
    }
    if operation == "unlink_field_binding":
        value = {"binding_uuid": value["binding_uuid"]}
    with pytest.raises(PiSemanticOperationError, match="manual field axis"):
        apply_pi_semantic_operations(snapshot, {
            "expected_revision": 3,
            "operations": [{
                "op": operation,
                "target_uuid": field["field_uuid"],
                "value": value,
                "evidence_ids": ["request-evidence-1"],
                "confidence": 0.9,
                "expected_revision": 3,
            }],
        })


def _publishable_snapshot(*, unresolved_provider: bool) -> dict[str, Any]:
    snapshot = _semantic_snapshot()
    snapshot["steps"][0].update({
        "method": "GET",
        "url": "https://oa.example/api/items",
        "path": "/api/items",
        "risk_level": "L1",
        "requires_confirmation": False,
    })
    snapshot["request_facts"]["requests"][0].update({
        "method": "GET", "url": "https://oa.example/api/items", "path": "/api/items",
    })
    snapshot["capabilities"][0].update({
        "name": "query_items", "risk_level": "L1", "requires_confirmation": False,
        "confirmed": True,
    })
    field = snapshot["steps"][0]["params"][0]
    field.update({
        "wire_required": True,
        "required": True,
        "exposed": True,
        "value_provider": {"kind": "unresolved" if unresolved_provider else "user_input"},
    })
    return snapshot


def test_contract_faults_publish_unverified_and_only_verified_is_direct_callable() -> None:
    unverified = _publishable_snapshot(unresolved_provider=True)
    candidate = project_asset(unverified, revision=3)
    assert candidate.body["verification_status"] == "unverified"
    assert candidate.body["publication_status"] == "published_unverified"
    assert any(
        item["code"] == "wire_required_without_provider"
        for item in candidate.body["contract_faults"]
    )

    verified = project_asset(_publishable_snapshot(unresolved_provider=False), revision=3)
    assert verified.body["verification_status"] == "verified"
    assert verified.body["api_request"]["direct_call_enabled"] is True


class _Writer:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def publish(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"published": True, "asset_id": "asset", "version": 1}


@pytest.mark.asyncio
async def test_publish_binds_snapshot_hash_and_keeps_failed_pi_opinion_advisory() -> None:
    snapshot = _publishable_snapshot(unresolved_provider=True)
    collector = ReviewCollector()
    writer = _Writer()

    async def provider(_recording_id: str, _revision: int) -> dict[str, Any]:
        return deepcopy(snapshot)

    async def reviews(recording_id: str, revision: int) -> list[dict[str, Any]]:
        for role in ("acceptance", "security", "compliance"):
            collector.submit_active(
                recording_id=recording_id,
                revision=revision,
                role=role,
                verdict={
                    "passed": role != "acceptance",
                    "reasons": ["rename this capability"] if role == "acceptance" else [],
                    "pi_session_id": f"session-{role}",
                },
            )
        return []

    service = RecordingPublishService(
        snapshot_provider=provider,
        review_runner=reviews,
        review_collector=collector,
        asset_writer=writer,
    )
    result = await service.publish("recording-a", 3)
    assert result["published"] is True
    assert result["publication_status"] == "published_unverified"
    assert result["contract_fault_count"] > 0
    assert result["review_advisories"][0]["kind"] == "advisory"
    call = writer.calls[0]
    assert call["validation"]["snapshot_hash"].startswith("sha256:")
    assert {item["snapshot_hash"] for item in call["reviews"]} == {
        call["validation"]["snapshot_hash"]
    }


@pytest.mark.asyncio
async def test_pi_reviewer_unavailability_is_three_isolated_advisories_not_a_publish_gate() -> None:
    snapshot = _publishable_snapshot(unresolved_provider=False)
    collector = ReviewCollector()
    writer = _Writer()

    async def provider(_recording_id: str, _revision: int) -> dict[str, Any]:
        return deepcopy(snapshot)

    async def unavailable(_recording_id: str, _revision: int) -> list[dict[str, Any]]:
        raise RuntimeError("sidecar offline")

    service = RecordingPublishService(
        snapshot_provider=provider,
        review_runner=unavailable,
        review_collector=collector,
        asset_writer=writer,
    )
    result = await service.publish("recording-a", 3)
    assert result["published"] is True
    assert result["publication_status"] == "published_verified"
    assert len(result["review_advisories"]) == 3
    reviews = writer.calls[0]["reviews"]
    assert len(reviews) == 3
    assert all(item["passed"] is False and item["unavailable"] for item in reviews)
    assert len({item["pi_session_id"] for item in reviews}) == 3
    assert all(item["evidence"] == [] for item in reviews)


class _NoSend:
    def __init__(self) -> None:
        self.called = False

    async def request(self, *_args: Any, **_kwargs: Any) -> Any:
        self.called = True
        raise AssertionError("unverified runtime must not send")


@pytest.mark.asyncio
async def test_unverified_api_is_blocked_before_network() -> None:
    api = project_asset(_publishable_snapshot(unresolved_provider=True), revision=3).body["api_request"]
    sender = _NoSend()
    result = await execute_recording_capability(
        api,
        {"kind": "x"},
        capability="query_items",
        confirm=False,
        base_url="https://oa.example",
        sender=sender,
    )
    assert result["stage"] == "unverified_contract"
    assert sender.called is False


class _CoordinatorClient:
    model_id = "server-model"

    def __init__(self) -> None:
        self.coordinator: RecordingPiCoordinator | None = None
        self.duplicate_rejected = False

    async def open_session(self, **kwargs: Any) -> dict[str, Any]:
        return {"session_path": f"session/{kwargs['session_id']}"}

    async def prompt(self, *, session_id: str, prompt: str, revision: int) -> dict[str, Any]:
        assert self.coordinator is not None
        payload = {"operations": [], "expected_revision": revision}
        await self.coordinator.handle_tool(session_id, "apply_semantic_operations", payload)
        with pytest.raises(ValueError, match="exactly one submission"):
            await self.coordinator.handle_tool(session_id, "apply_semantic_operations", payload)
        self.duplicate_rejected = True
        return {"turn": 1, "session_path": "session/planner", "final_text": "ok"}


@pytest.mark.asyncio
async def test_coordinator_allows_exactly_one_atomic_commit_per_turn() -> None:
    client = _CoordinatorClient()
    submissions: list[tuple[str, dict[str, Any]]] = []

    async def submit(_recording_id: str, tool: str, payload: dict[str, Any]) -> dict[str, Any]:
        submissions.append((tool, payload))
        return {"accepted": True, "revision": 4}

    async def state(_recording_id: str) -> dict[str, Any]:
        return {"pi_projection": {"revision": 3}}

    coordinator = RecordingPiCoordinator(
        client=client,  # type: ignore[arg-type]
        state_provider=state,
        submission_handler=submit,
    )
    client.coordinator = coordinator
    await coordinator.plan("recording-a", 3)
    assert client.duplicate_rejected is True
    assert len(submissions) == 1
    assert submissions[0][0] == "submit_recording_plan"


@pytest.mark.asyncio
async def test_sidecar_restart_recovers_every_registered_session(tmp_path: Path) -> None:
    async def tool_handler(*_args: Any) -> dict[str, Any]:
        return {}

    client = PiSidecarClient(
        script_path=Path(__file__).parents[1] / "src" / "dano_recording" / "pi" / "runtime" / "sidecar.mjs",
        tool_handler=tool_handler,
        env={"PI_STUB": "1", "DANO_PI_SESSION_DIR": str(tmp_path / "pi")},
        request_timeout_s=20,
    )
    await client.start()
    session_ids = []
    for role in ("planner", "acceptance", "security", "compliance"):
        session_id = f"session-{role}"
        session_ids.append(session_id)
        await client.open_session(
            session_id=session_id,
            recording_id="recording-a",
            role=role,
        )
    assert client._proc is not None  # noqa: SLF001
    client._proc.kill()  # noqa: SLF001
    await client._proc.wait()  # noqa: SLF001
    for session_id in session_ids:
        result = await client.prompt(session_id=session_id, prompt="resume", revision=3)
        assert result["final_text"] == "PI_STUB"
    await client.close()


@pytest.mark.asyncio
async def test_application_uses_semantic_batch_and_publish_provider_rejects_old_revision(
    tmp_path: Path,
) -> None:
    service = RecordingApplication(pi_env={"PI_STUB": "1"}, artifact_root=tmp_path)
    await service.start()
    created = await service.create_session(
        "tenant-a",
        CreateRecordingRequest(
            subsystem="oa",
            start_url="https://oa.example/app",
            base_url="https://oa.example",
        ),
    )
    snapshot = _semantic_snapshot()
    snapshot.update({"recording_id": created.recording_id, "revision": 0})
    first, _ = await service._commit_snapshot(  # noqa: SLF001
        "tenant-a",
        created.recording_id,
        expected_revision=0,
        snapshot=snapshot,
        actor="deterministic",
    )
    op = _operation(axis="business_type", value="number")
    op["expected_revision"] = 1
    result = await service._pi_submission(  # noqa: SLF001
        created.recording_id,
        "submit_recording_plan",
        {"expected_revision": 1, "operations": [op]},
    )
    assert result["revision"] == 2
    latest = await service.repository.get_revision("tenant-a", created.recording_id)
    assert latest is not None
    assert latest.snapshot["steps"][0]["params"][0]["business_type"] == "number"
    with pytest.raises(PiSemanticOperationError):
        # The lower operation layer also rejects stale replay independently of
        # repository optimistic locking.
        apply_pi_semantic_operations(first.snapshot, {
            "expected_revision": 0, "operations": [],
        })
    with pytest.raises(RevisionConflict):
        await service._publish_snapshot(created.recording_id, 1)  # noqa: SLF001
    with pytest.raises(Exception, match="semantic operation batch"):
        await service._pi_submission(  # noqa: SLF001
            created.recording_id,
            "submit_recording_plan",
            {"expected_revision": 2, "plan": {"title": "unbounded replacement"}},
        )
    await service.close()
