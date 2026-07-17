from __future__ import annotations

from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import threading
from typing import Any

import pytest

from dano_recording.bootstrap import RecordingApplication
from dano_recording.api.protocol import CreateRecordingRequest
import dano_recording.pi.coordinator as pi_coordinator_module
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
    lineage_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
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
        "lineage_id": lineage_id,
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


def test_pi_can_atomically_name_flow_steps_and_capabilities() -> None:
    snapshot = _semantic_snapshot()
    flow_uuid = snapshot["lineage_id"]
    step_uuid = snapshot["steps"][0]["step_uuid"]
    capability_uuid = snapshot["capabilities"][0]["capability_uuid"]

    def semantic(op: str, target_uuid: str, value: Any) -> dict[str, Any]:
        return {
            "op": op,
            "target_uuid": target_uuid,
            "value": value,
            "evidence_ids": ["request-evidence-1"],
            "confidence": 0.92,
            "expected_revision": 3,
        }

    result = apply_pi_semantic_operations(snapshot, {
        "expected_revision": 3,
        "operations": [
            semantic(
                "set_flow_goal",
                flow_uuid,
                {"intent": "删除指定项目", "success_criteria": ["项目不再存在"]},
            ),
            semantic("set_flow_action", flow_uuid, "delete_item"),
            semantic(
                "set_business_description",
                flow_uuid,
                "根据项目标识删除记录，并保留人工确认。",
            ),
            semantic("set_step_name", step_uuid, "删除项目请求"),
            semantic("set_step_title", step_uuid, "执行删除项目"),
            semantic("set_capability_name", capability_uuid, "delete_item"),
            semantic("set_capability_title", capability_uuid, "删除项目"),
            semantic(
                "set_capability_description",
                capability_uuid,
                "删除一个指定项目。",
            ),
        ],
    })

    assert result["goal"]["intent"] == "删除指定项目"
    assert result["action"] == "delete_item"
    assert result["business_description"].startswith("根据项目标识")
    assert result["steps"][0]["name"] == "删除项目请求"
    assert result["steps"][0]["title"] == "执行删除项目"
    assert result["capabilities"][0]["title"] == "删除项目"
    assert result["capabilities"][0]["description"] == "删除一个指定项目。"
    assert result["meta"]["pi_semantic_commit"]["operation_count"] == 8


@pytest.mark.parametrize(
    ("operation", "target_kind"),
    [
        ("set_flow_action", "flow"),
        ("set_business_description", "flow"),
        ("set_step_name", "step"),
        ("set_step_title", "step"),
        ("set_capability_name", "capability"),
        ("set_capability_title", "capability"),
        ("set_capability_description", "capability"),
    ],
)
@pytest.mark.parametrize("invalid_value", [None, "", "   ", {"unexpected": "object"}])
def test_pi_semantic_text_operations_reject_empty_or_non_string_values_atomically(
    operation: str,
    target_kind: str,
    invalid_value: Any,
) -> None:
    snapshot = _semantic_snapshot()
    original = deepcopy(snapshot)
    target = {
        "flow": snapshot["lineage_id"],
        "step": snapshot["steps"][0]["step_uuid"],
        "capability": snapshot["capabilities"][0]["capability_uuid"],
    }[target_kind]

    with pytest.raises(PiSemanticOperationError, match="non-empty string"):
        apply_pi_semantic_operations(snapshot, {
            "expected_revision": 3,
            "operations": [{
                "op": operation,
                "target_uuid": target,
                "value": invalid_value,
                "evidence_ids": ["request-evidence-1"],
                "confidence": 0.9,
                "expected_revision": 3,
            }],
        })

    assert snapshot == original


@pytest.mark.parametrize("operation", ["set_flow_action", "set_capability_name"])
@pytest.mark.parametrize("invalid_value", ["9starts_with_digit", "contains-dash", "中文名称"])
def test_pi_executable_names_require_stable_ascii_identifiers(
    operation: str,
    invalid_value: str,
) -> None:
    snapshot = _semantic_snapshot()
    target = (
        snapshot["lineage_id"]
        if operation == "set_flow_action"
        else snapshot["capabilities"][0]["capability_uuid"]
    )

    with pytest.raises(PiSemanticOperationError, match="ASCII identifier"):
        apply_pi_semantic_operations(snapshot, {
            "expected_revision": 3,
            "operations": [{
                "op": operation,
                "target_uuid": target,
                "value": invalid_value,
                "evidence_ids": ["request-evidence-1"],
                "confidence": 0.9,
                "expected_revision": 3,
            }],
        })


def test_pi_flow_goal_cannot_clear_an_existing_goal_with_an_empty_object() -> None:
    snapshot = _semantic_snapshot()
    snapshot["goal"] = {"intent": "保留正确目标"}
    original = deepcopy(snapshot)

    with pytest.raises(PiSemanticOperationError, match="non-empty object"):
        apply_pi_semantic_operations(snapshot, {
            "expected_revision": 3,
            "operations": [{
                "op": "set_flow_goal",
                "target_uuid": snapshot["lineage_id"],
                "value": {},
                "evidence_ids": ["request-evidence-1"],
                "confidence": 0.9,
                "expected_revision": 3,
            }],
        })

    assert snapshot == original


@pytest.mark.parametrize(
    ("operation", "manual_path", "container", "axis"),
    [
        (
            "set_flow_action",
            "flow:action",
            "flow",
            "action",
        ),
        (
            "set_step_title",
            "step:22222222-2222-4222-8222-222222222222:title",
            "step",
            "title",
        ),
        (
            "set_capability_title",
            "capability:33333333-3333-4333-8333-333333333333:title",
            "capability",
            "title",
        ),
    ],
)
def test_pi_naming_operations_preserve_manual_semantic_axes(
    operation: str,
    manual_path: str,
    container: str,
    axis: str,
) -> None:
    snapshot = _semantic_snapshot()
    snapshot["meta"]["decision_origins"][manual_path] = "user"
    target = {
        "flow": snapshot["lineage_id"],
        "step": snapshot["steps"][0]["step_uuid"],
        "capability": snapshot["capabilities"][0]["capability_uuid"],
    }[container]
    with pytest.raises(PiSemanticOperationError, match=f"manual {container}"):
        apply_pi_semantic_operations(snapshot, {
            "expected_revision": 3,
            "operations": [{
                "op": operation,
                "target_uuid": target,
                "value": f"pi-{axis}",
                "evidence_ids": ["request-evidence-1"],
                "confidence": 0.9,
                "expected_revision": 3,
            }],
        })


def test_pi_cannot_clear_a_manual_override() -> None:
    snapshot = _semantic_snapshot(manual_name=True)
    original = deepcopy(snapshot)

    with pytest.raises(PiSemanticOperationError, match="not allowed"):
        apply_pi_semantic_operations(snapshot, {
            "expected_revision": 3,
            "operations": [{
                "op": "clear_manual_override",
                "target_uuid": snapshot["steps"][0]["params"][0]["field_uuid"],
                "axis": "display_name",
                "evidence_ids": ["request-evidence-1"],
                "confidence": 1.0,
                "expected_revision": 3,
            }],
        })

    assert snapshot == original


@pytest.mark.parametrize(
    ("invalid_binding", "message"),
    [
        ({"kind": "invented_source"}, "source_binding"),
        ({"kind": "runtime_context"}, "runtime_resolver"),
        ({"kind": "constant"}, "constant source requires value"),
    ],
)
def test_pi_source_binding_rejects_invalid_atomic_contract_and_rolls_back_batch(
    invalid_binding: dict[str, Any],
    message: str,
) -> None:
    snapshot = _semantic_snapshot()
    original = deepcopy(snapshot)

    with pytest.raises(PiSemanticOperationError, match=message):
        apply_pi_semantic_operations(snapshot, {
            "expected_revision": 3,
            "operations": [
                _operation(axis="business_type", value="integer"),
                _operation(axis="source_binding", value=invalid_binding),
            ],
        })

    assert snapshot == original


def test_pi_plan_modes_are_structured_values() -> None:
    mode = getattr(pi_coordinator_module, "PiPlanMode", None)
    assert mode is not None
    assert mode.STEP_NAMING.value == "step_naming"
    assert mode.BUSINESS_DESCRIPTION.value == "business_description"
    assert mode.RECOMMENDATIONS.value == "llm_recommendations"


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


class _ScopedCoordinatorClient:
    model_id = "server-model"

    def __init__(
        self,
        operations: list[dict[str, Any]],
        *,
        nested_plan: bool = False,
    ) -> None:
        self.operations = operations
        self.nested_plan = nested_plan
        self.coordinator: RecordingPiCoordinator | None = None
        self.prompts: list[str] = []

    async def open_session(self, **kwargs: Any) -> dict[str, Any]:
        return {"session_path": f"session/{kwargs['session_id']}"}

    async def prompt(
        self,
        *,
        session_id: str,
        prompt: str,
        revision: int,
    ) -> dict[str, Any]:
        assert self.coordinator is not None
        self.prompts.append(prompt)
        payload = (
            {"plan": {"operations": self.operations}, "expected_revision": revision}
            if self.nested_plan
            else {"operations": self.operations, "expected_revision": revision}
        )
        await self.coordinator.handle_tool(
            session_id,
            "apply_semantic_operations",
            payload,
        )
        return {"turn": 1, "session_path": "session/planner", "final_text": "ok"}


def _scoped_operation(op: str) -> dict[str, Any]:
    return {
        "op": op,
        "target_uuid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "value": "grounded_value",
        "evidence_ids": ["request-evidence-1"],
        "confidence": 0.9,
        "expected_revision": 3,
    }


async def _scoped_coordinator(
    operations: list[dict[str, Any]],
    *,
    nested_plan: bool = False,
) -> tuple[RecordingPiCoordinator, _ScopedCoordinatorClient, list[dict[str, Any]]]:
    client = _ScopedCoordinatorClient(operations, nested_plan=nested_plan)
    submissions: list[dict[str, Any]] = []

    async def submit(
        _recording_id: str,
        _tool: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        submissions.append(payload)
        return {"accepted": True, "revision": 4}

    async def state(_recording_id: str) -> dict[str, Any]:
        return {"pi_projection": {"revision": 3}}

    coordinator = RecordingPiCoordinator(
        client=client,  # type: ignore[arg-type]
        state_provider=state,
        submission_handler=submit,
    )
    client.coordinator = coordinator
    return coordinator, client, submissions


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "forbidden_operation"),
    [
        (pi_coordinator_module.PiPlanMode.STEP_NAMING, "set_business_description"),
        (pi_coordinator_module.PiPlanMode.BUSINESS_DESCRIPTION, "set_step_title"),
        (pi_coordinator_module.PiPlanMode.RECOMMENDATIONS, "set_business_description"),
    ],
)
async def test_coordinator_rejects_operations_outside_structured_task_mode(
    mode: Any,
    forbidden_operation: str,
) -> None:
    coordinator, _client, submissions = await _scoped_coordinator([
        _scoped_operation(forbidden_operation)
    ])

    with pytest.raises(PermissionError, match="not allowed for Pi task mode"):
        await coordinator.plan("recording-a", 3, mode=mode)

    assert submissions == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "allowed_operation"),
    [
        (pi_coordinator_module.PiPlanMode.STEP_NAMING, "set_step_title"),
        (
            pi_coordinator_module.PiPlanMode.BUSINESS_DESCRIPTION,
            "set_business_description",
        ),
        (pi_coordinator_module.PiPlanMode.REPLAN, "set_flow_action"),
    ],
)
async def test_coordinator_accepts_only_operations_authorized_by_task_mode(
    mode: Any,
    allowed_operation: str,
) -> None:
    coordinator, client, submissions = await _scoped_coordinator([
        _scoped_operation(allowed_operation)
    ])

    await coordinator.plan("recording-a", 3, mode=mode)

    assert len(submissions) == 1
    assert mode.value in client.prompts[0]


@pytest.mark.asyncio
async def test_recommendation_mode_is_read_only_but_commits_its_required_empty_batch() -> None:
    coordinator, _client, submissions = await _scoped_coordinator([])

    await coordinator.plan(
        "recording-a",
        3,
        mode=pi_coordinator_module.PiPlanMode.RECOMMENDATIONS,
    )

    assert len(submissions) == 1
    assert submissions[0]["operations"] == []


@pytest.mark.asyncio
async def test_task_scope_cannot_be_bypassed_with_legacy_nested_plan_shape() -> None:
    coordinator, _client, submissions = await _scoped_coordinator(
        [_scoped_operation("set_business_description")],
        nested_plan=True,
    )

    with pytest.raises(ValueError, match="top-level operations list"):
        await coordinator.plan(
            "recording-a",
            3,
            mode=pi_coordinator_module.PiPlanMode.RECOMMENDATIONS,
        )

    assert submissions == []


def test_pi_runtime_pins_and_guards_assistant_role_compaction_fix() -> None:
    root = Path(__file__).parents[1]
    manifest = json.loads((root / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((root / "package-lock.json").read_text(encoding="utf-8"))
    assert manifest["dependencies"]["@earendil-works/pi-coding-agent"] == "0.79.10"
    assert (
        lock["packages"][""]["dependencies"]["@earendil-works/pi-coding-agent"]
        == "0.79.10"
    )

    node = shutil.which("node")
    assert node is not None
    profile = (
        root / "src" / "dano_recording" / "pi" / "runtime" / "profile.mjs"
    ).resolve().as_uri()
    script = f"""
      import {{ assertCompatiblePiVersion, installedPiVersion }} from {json.dumps(profile)};
      assertCompatiblePiVersion("0.79.8");
      assertCompatiblePiVersion("0.79.10");
      if (installedPiVersion() !== "0.79.10") process.exit(2);
      try {{
        assertCompatiblePiVersion("0.79.7");
        process.exit(3);
      }} catch (error) {{
        if (!String(error.message).includes("assistant-role continuation")) process.exit(4);
      }}
    """
    completed = subprocess.run(
        [node, "--input-type=module", "--eval", script],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


class _FakeOpenAIHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        length = int(self.headers.get("content-length") or 0)
        request = json.loads(self.rfile.read(length) or b"{}")
        server = self.server
        turns = int(getattr(server, "turns", 0)) + 1
        setattr(server, "turns", turns)
        model = str(request.get("model") or "test-model")
        chunk_id = f"chatcmpl-{turns}"
        overflow_turn = int(getattr(server, "overflow_success_turn", 0))
        overflow_success = bool(overflow_turn) and turns == overflow_turn
        response_text = f"turn-{turns}"
        if turns <= int(getattr(server, "long_completion_turns", 0)):
            # Several sub-64KiB replies give prepareCompaction a real old span
            # while keeping every JSONL protocol frame within asyncio's limit.
            response_text += ":" + (
                "x" * int(getattr(server, "long_completion_size", 30_000))
            )
        prompt_tokens = (
            130_000
            if overflow_success
            else 20
        )
        chunks = [
            {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": 1,
                "model": model,
                "choices": [{
                    "index": 0,
                    "delta": {"role": "assistant", "content": response_text},
                    "finish_reason": None,
                }],
            },
            {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": 1,
                "model": model,
                "choices": [{
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop",
                }],
            },
            {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": 1,
                "model": model,
                "choices": [],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": 2,
                    "total_tokens": prompt_tokens + 2,
                },
            },
        ]
        body = "".join(
            f"data: {json.dumps(chunk, separators=(',', ':'))}\n\n"
            for chunk in chunks
        ) + "data: [DONE]\n\n"
        encoded = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: Any) -> None:
        return


@pytest.mark.asyncio
async def test_non_stub_pi_successful_overflow_compacts_without_replaying_assistant(
    tmp_path: Path,
) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOpenAIHandler)
    # A completed response whose reported input usage exceeds the configured
    # context window is Pi's successful-overflow compaction path.  It must
    # compact without retrying/continuing the already completed assistant.
    server.overflow_success_turn = 2
    server.long_completion_turns = 2
    server.long_completion_size = 45_000
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = int(server.server_address[1])
    env = {
        "DANO_PI_API_KEY": "test-key",
        "DANO_PI_BASE_URL": f"http://127.0.0.1:{port}/v1",
        "DANO_PI_PROVIDER": "test-provider",
        "DANO_PI_MODEL": "test-model",
        "DANO_PI_SESSION_DIR": str(tmp_path / "pi"),
    }
    script_path = (
        Path(__file__).parents[1]
        / "src"
        / "dano_recording"
        / "pi"
        / "runtime"
        / "sidecar.mjs"
    )

    async def tool_handler(*_args: Any) -> dict[str, Any]:
        return {}

    events: list[dict[str, Any]] = []

    async def event_handler(event: dict[str, Any]) -> None:
        events.append(event)

    first = PiSidecarClient(
        script_path=script_path,
        tool_handler=tool_handler,
        event_handler=event_handler,
        env=env,
        request_timeout_s=30,
    )
    second: PiSidecarClient | None = None
    try:
        await first.start()
        opened = await first.open_session(
            session_id="non-stub-resume",
            recording_id="recording-a",
            role="planner",
        )
        seed_one = await first.prompt(
            session_id="non-stub-resume",
            prompt="first compactable grounded turn",
            revision=1,
        )
        first_turn = await first.prompt(
            session_id="non-stub-resume",
            prompt="completed response that reports context overflow",
            revision=2,
        )
        assert seed_one["final_text"].startswith("turn-1:")
        assert first_turn["final_text"].startswith("turn-2:")
        compaction_events = [
            message["event"]
            for message in events
            if message.get("type") == "event"
            and str((message.get("event") or {}).get("type", "")).startswith("compaction_")
        ]
        assert [event["type"] for event in compaction_events] == [
            "compaction_start",
            "compaction_end",
        ], events
        assert compaction_events[-1] == {
            "type": "compaction_end",
            "reason": "overflow",
            "aborted": False,
            "willRetry": False,
        }
        session_path = str(first_turn.get("session_path") or opened["session_path"])
        assert Path(session_path).is_file()
        persisted = [
            json.loads(line)
            for line in Path(session_path).read_text(encoding="utf-8").splitlines()
        ]
        assert sum(entry.get("type") == "compaction" for entry in persisted) == 1
        assistant_response_ids = [
            str((entry.get("message") or {}).get("responseId") or "")
            for entry in persisted
            if entry.get("type") == "message"
            and (entry.get("message") or {}).get("role") == "assistant"
        ]
        assert assistant_response_ids.count("chatcmpl-2") == 1
        await first.close()

        second = PiSidecarClient(
            script_path=script_path,
            tool_handler=tool_handler,
            env=env,
            request_timeout_s=30,
        )
        await second.start()
        await second.open_session(
            session_id="non-stub-resume",
            recording_id="recording-a",
            role="planner",
            session_path=session_path,
        )
        resumed = await second.prompt(
            session_id="non-stub-resume",
            prompt="continue from persisted assistant response",
            revision=3,
        )
        assert resumed["final_text"] == "turn-4"
        assert "Cannot continue from message role" not in resumed["final_text"]
        # Two business replies, one compaction summary, and one resumed reply:
        # there is no extra model call replaying the completed assistant.
        assert getattr(server, "turns", 0) == 4
    finally:
        if first.running:
            await first.close()
        if second is not None and second.running:
            await second.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


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
    description = {
        "op": "set_business_description",
        "target_uuid": snapshot["lineage_id"],
        "value": "删除指定项目并要求人工确认。",
        "evidence_ids": ["request-evidence-1"],
        "confidence": 0.95,
        "expected_revision": 2,
    }
    persisted = await service._pi_submission(  # noqa: SLF001
        created.recording_id,
        "submit_recording_plan",
        {"expected_revision": 2, "operations": [description]},
    )
    assert persisted["revision"] == 3
    latest = await service.repository.get_revision("tenant-a", created.recording_id)
    assert latest is not None
    assert latest.snapshot["business_description"] == "删除指定项目并要求人工确认。"
    assert latest.snapshot["meta"]["pi_semantic_commit"]["operation_count"] == 1
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
            {"expected_revision": 3, "plan": {"title": "unbounded replacement"}},
        )
    await service.close()
