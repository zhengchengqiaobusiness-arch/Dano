from __future__ import annotations

from copy import deepcopy
from uuid import UUID

import pytest

from dano_recording.api.decision_commands import (
    DecisionCommandError,
    _automatic_source_projection_kind,
    _find_edit_step,
    _find_param,
    _materialize_request,
    _source_binding,
    apply_edits,
)
from dano_recording.executability import _fields, check_executability
from dano_recording.domain.fields import AxisDecision, AxisOrigin, FieldDimension
from dano_recording.field_registry import FieldRegistry
from dano_recording.pi_semantic_ops import apply_pi_semantic_operations


STEP_UUID = "11111111-1111-4111-8111-111111111111"
FIELD_UUID = "22222222-2222-4222-8222-222222222222"


def _v3_snapshot() -> dict:
    return {
        "recording_contract_version": 1,
        "steps": [
            {
                "step_uuid": STEP_UUID,
                "step_id": "mutable-step-name",
                "params": [
                    {
                        "field_uuid": FIELD_UUID,
                        "field_id": FIELD_UUID,
                        "path": "body.display_name",
                        "key": "display_name",
                    }
                ],
            }
        ],
        "request_facts": {
            "requests": [
                {
                    "request_index": 7,
                    "request_id": "request-observation-opaque",
                    "observation_id": "observation-opaque",
                    "request_definition_id": "definition-opaque",
                    "method": "POST",
                    "path": "/api/items",
                    "url": "https://example.test/api/items",
                }
            ]
        },
    }


def _valid_v3_snapshot() -> dict:
    snapshot = _v3_snapshot()
    snapshot["steps"][0].update({"method": "GET", "path": "/api/items"})
    snapshot["capabilities"] = []
    snapshot["links"] = []
    snapshot["request_facts"]["requests"][0]["disposition"] = "supporting"
    return snapshot


def test_executability_deduplicates_field_registry_rows_by_canonical_step_uuid() -> None:
    snapshot = {
        "effective_fields": [{
            "field_contract_id": "legacy-field-contract",
            "request_id": "request-definition-1",
            "location": "header",
            "wire_path": "authorization",
            "value_provider": {"kind": "unresolved"},
        }],
        "steps": [{
            "step_id": "mutable-display-step",
            "step_uuid": STEP_UUID,
            "params": [{
                "field_uuid": FIELD_UUID,
                "field_contract_id": "legacy-field-contract",
                "step_uuid": STEP_UUID,
                "location": "header",
                "wire_path": "authorization",
                "source_binding": {
                    "kind": "runtime_context",
                    "runtime_resolver": "credential_headers.Authorization",
                },
            }],
        }],
        "field_registry": {
            "fields": [{
                "field_uuid": FIELD_UUID,
                "wire_binding_ids": ["binding-1"],
                "decisions": {},
            }],
            "bindings": [{
                "binding_id": "binding-1",
                "step_uuid": STEP_UUID,
                "request_definition_id": "request-definition-1",
                "wire_path": "header.authorization",
            }],
        },
    }

    fields = _fields(snapshot, {})

    assert len(fields) == 1
    assert fields[0]["source_binding"]["runtime_resolver"] == (
        "credential_headers.Authorization"
    )


def test_v3_step_and_field_edits_reject_mutable_identity_fallbacks() -> None:
    snapshot = _v3_snapshot()

    with pytest.raises(DecisionCommandError, match="require step_uuid"):
        _find_edit_step(snapshot, {"step_id": "mutable-step-name"})

    step = _find_edit_step(snapshot, {"step_uuid": STEP_UUID})
    with pytest.raises(DecisionCommandError, match="require field_uuid"):
        _find_param(
            step,
            {"param_path": "body.display_name"},
            require_uuid=True,
        )
    assert (
        _find_param(step, {"field_uuid": FIELD_UUID}, require_uuid=True)[
            "field_uuid"
        ]
        == FIELD_UUID
    )


def test_v3_request_materialization_rejects_index_and_accepts_stable_identity() -> None:
    snapshot = _v3_snapshot()
    snapshot["steps"] = []

    with pytest.raises(DecisionCommandError, match="request_index is display-only"):
        _materialize_request(snapshot, {"request_index": 7})

    by_observation = _materialize_request(
        deepcopy(snapshot),
        {"observation_id": "observation-opaque"},
    )
    assert by_observation["request_id"] == "request-observation-opaque"
    assert by_observation["step_uuid"]

    by_definition = _materialize_request(
        deepcopy(snapshot),
        {"request_definition_id": "definition-opaque"},
    )
    assert by_definition["request_id"] == "request-observation-opaque"
    assert by_definition["step_uuid"]


def test_v3_request_materialization_rejects_conflicting_stable_identities() -> None:
    snapshot = _v3_snapshot()
    snapshot["steps"] = []
    snapshot["request_facts"]["requests"].append(
        {
            "request_index": 8,
            "request_id": "other-request",
            "observation_id": "other-observation",
            "request_definition_id": "other-definition",
            "method": "POST",
            "path": "/api/other",
            "url": "https://example.test/api/other",
        }
    )

    with pytest.raises(DecisionCommandError, match="stable request identities conflict"):
        _materialize_request(
            snapshot,
            {
                "request_id": "request-observation-opaque",
                "request_definition_id": "other-definition",
            },
        )


def test_resolve_review_only_allows_server_advisories_to_be_ignored() -> None:
    snapshot = _valid_v3_snapshot()
    snapshot["review_items"] = [
        {
            "id": "deterministic-check",
            "kind": "validator_warning",
            "fingerprint": "sha256:deterministic",
            "resolved": False,
        }
    ]

    with pytest.raises(DecisionCommandError, match="not found|only Advisory"):
        apply_edits(
            snapshot,
            [
                {
                    "op": "resolve_review",
                    "review_id": "deterministic-check",
                    "fingerprint": "sha256:deterministic",
                    "issue_kind": "validator_warning",
                    "resolved": True,
                }
            ],
        )


def test_resolve_review_recomputes_advisory_and_is_idempotent() -> None:
    snapshot = _valid_v3_snapshot()
    advisory = check_executability(snapshot)["advisories"][0]
    edit = {
        "op": "resolve_review",
        "review_id": advisory["id"],
        "fingerprint": advisory["fingerprint"],
        "issue_kind": "advisory",
        "resolved": True,
    }

    ignored = apply_edits(snapshot, [edit])
    ignored_again = apply_edits(ignored, [edit])

    assert ignored_again["meta"]["ignored_advisory_fingerprints"] == [
        advisory["fingerprint"]
    ]


def test_resolve_review_rejects_stale_stored_advisory() -> None:
    snapshot = _valid_v3_snapshot()
    snapshot["review_items"] = [
        {
            "id": "stale-advisory",
            "kind": "advisory",
            "fingerprint": "sha256:stale-advisory",
            "resolved": False,
        }
    ]

    with pytest.raises(DecisionCommandError, match="not found"):
        apply_edits(
            snapshot,
            [
                {
                    "op": "resolve_review",
                    "review_id": "stale-advisory",
                    "fingerprint": "sha256:stale-advisory",
                    "issue_kind": "advisory",
                    "resolved": True,
                }
            ],
        )


def test_automatic_source_restore_reads_the_step_enum_binding() -> None:
    step = {
        "selects": [
            {
                "param": "display_name",
                "path": "body.display_name",
                "source_url": "/api/options",
            }
        ]
    }
    param = {"key": "display_name", "path": "body.display_name"}

    assert _automatic_source_projection_kind(
        param,
        _source_binding({"kind": "caller"}),
        step,
    ) == "api_option"


def test_source_replacement_atomically_clears_stale_enum_projection() -> None:
    snapshot = _valid_v3_snapshot()
    step = snapshot["steps"][0]
    param = step["params"][0]
    param.update(
        {
            "source_kind": "manual_enum",
            "source_binding": {"kind": "caller"},
            "enum_binding": {"kind": "static"},
            "enum_options": ["A", "B"],
            "enum_value_map": {"A": 1, "B": 2},
        }
    )
    step["selects"] = [
        {"param": "display_name", "path": "body.display_name", "options": ["A", "B"]}
    ]

    edited = apply_edits(
        snapshot,
        [
            {
                "op": "update",
                "step_uuid": STEP_UUID,
                "field_uuid": FIELD_UUID,
                "field": "source_binding",
                "value": {"kind": "caller"},
                "source_kind_projection": "user_input",
            }
        ],
    )
    field = edited["steps"][0]["params"][0]
    assert field["source_kind"] == "user_input"
    assert field["enum_binding"] is None
    assert field["enum_options"] is None
    assert field["enum_value_map"] is None
    assert edited["steps"][0]["selects"] == []


def test_switching_between_enum_source_kinds_cannot_reuse_the_old_domain() -> None:
    snapshot = _valid_v3_snapshot()
    step = snapshot["steps"][0]
    param = step["params"][0]
    param.update(
        {
            "source_kind": "api_option",
            "source_binding": {"kind": "caller"},
            "enum_binding": {"kind": "dynamic", "request_definition_id": "old-options"},
            "enum_options": [{"label": "旧选项", "value": "old"}],
            "enum_value_map": {"旧选项": "old"},
        }
    )
    step["selects"] = [
        {
            "param": "display_name",
            "path": "body.display_name",
            "source_url": "/api/old-options",
        }
    ]

    edited = apply_edits(
        snapshot,
        [
            {
                "op": "update",
                "step_uuid": STEP_UUID,
                "field_uuid": FIELD_UUID,
                "field": "source_binding",
                "value": {"kind": "caller"},
                "source_kind_projection": "manual_enum",
            }
        ],
    )

    field = edited["steps"][0]["params"][0]
    assert field["source_kind"] == "manual_enum"
    assert field["enum_binding"] is None
    assert field["enum_options"] is None
    assert field["enum_value_map"] is None
    assert edited["steps"][0]["selects"] == []


def test_type_and_classification_updates_do_not_rewrite_the_source_axis() -> None:
    snapshot = _valid_v3_snapshot()
    param = snapshot["steps"][0]["params"][0]
    param.update(
        {
            "type": "string",
            "business_type": "string",
            "category": "user_param",
            "source_kind": "previous_response",
            "source_binding": {
                "kind": "previous_response",
                "request_definition_id": "upstream-request",
                "response_path": "data.id",
            },
        }
    )
    source_before = deepcopy(param["source_binding"])

    edited = apply_edits(
        snapshot,
        [
            {
                "op": "update",
                "step_uuid": STEP_UUID,
                "field_uuid": FIELD_UUID,
                "field": "type",
                "value": "number",
            },
            {
                "op": "update",
                "step_uuid": STEP_UUID,
                "field_uuid": FIELD_UUID,
                "field": "category",
                "value": "runtime_var",
            },
        ],
    )
    field = edited["steps"][0]["params"][0]
    assert field["business_type"] == "number"
    assert field["category"] == "runtime_var"
    assert field["source_kind"] == "previous_response"
    assert field["source_binding"] == source_before


def test_classification_edit_and_restore_update_the_canonical_axis_and_ui_projection() -> None:
    snapshot = _valid_v3_snapshot()
    lineage_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    registry = FieldRegistry(lineage_id)
    registry.register_field(field_uuid=FIELD_UUID)
    registry.apply_axis_decision(
        FIELD_UUID,
        AxisDecision(
            axis=FieldDimension.CLASSIFICATION,
            value="user_param",
            origin=AxisOrigin.DETERMINISTIC,
            decided_at_revision=0,
        ),
    )
    snapshot.update(
        {
            "lineage_id": str(lineage_id),
            "revision": 0,
            "field_registry": registry.snapshot().model_dump(mode="json"),
        }
    )
    param = snapshot["steps"][0]["params"][0]
    param.update(
        {
            "classification": "user_param",
            "category": "user_param",
            "source_kind": "previous_response",
            "source_binding": {
                "kind": "previous_response",
                "request_definition_id": "upstream-request",
                "response_path": "data.id",
            },
        }
    )
    source_before = deepcopy(param["source_binding"])

    edited = apply_edits(
        snapshot,
        [
            {
                "op": "update",
                "step_uuid": STEP_UUID,
                "field_uuid": FIELD_UUID,
                "field": "category",
                "value": "runtime_var",
            }
        ],
    )
    field = edited["steps"][0]["params"][0]
    canonical = FieldRegistry.from_snapshot(edited["field_registry"]).get_field(
        FIELD_UUID
    )
    assert field["classification"] == "runtime_var"
    assert field["category"] == "runtime_var"
    assert field["source_binding"] == source_before
    assert canonical.decisions[FieldDimension.CLASSIFICATION].manual_override is True
    assert canonical.decisions[FieldDimension.CLASSIFICATION].value == "runtime_var"

    restored = apply_edits(
        edited,
        [
            {
                "op": "clear_field_axis",
                "step_uuid": STEP_UUID,
                "field_uuid": FIELD_UUID,
                "axis": "category",
            }
        ],
    )
    restored_field = restored["steps"][0]["params"][0]
    restored_canonical = FieldRegistry.from_snapshot(
        restored["field_registry"]
    ).get_field(FIELD_UUID)
    assert restored_field["classification"] == "user_param"
    assert restored_field["category"] == "user_param"
    assert restored_field["source_binding"] == source_before
    assert (
        restored_canonical.decisions[FieldDimension.CLASSIFICATION].manual_override
        is False
    )


def test_pi_canonical_axes_atomically_refresh_the_existing_ui_projections() -> None:
    snapshot = _valid_v3_snapshot()
    lineage_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    registry = FieldRegistry(lineage_id)
    registry.register_field(field_uuid=FIELD_UUID)
    snapshot.update(
        {
            "lineage_id": str(lineage_id),
            "revision": 0,
            "field_registry": registry.snapshot().model_dump(mode="json"),
        }
    )
    step = snapshot["steps"][0]
    step.update(
        {
            "request_definition_id": "definition-opaque",
            "response_schema": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
            },
        }
    )
    param = step["params"][0]
    param.update(
        {
            "classification": "user_param",
            "category": "user_param",
            "source_kind": "user_input",
            "source": {"kind": "user_input"},
            "source_binding": {"kind": "caller"},
        }
    )
    common = {
        "target_uuid": FIELD_UUID,
        "evidence_ids": ["request-observation-opaque"],
        "confidence": 0.9,
        "expected_revision": 0,
    }

    result = apply_pi_semantic_operations(
        snapshot,
        {
            "expected_revision": 0,
            "operations": [
                {
                    **common,
                    "op": "set_field_axis",
                    "axis": "classification",
                    "value": "runtime_var",
                },
                {
                    **common,
                    "op": "set_field_axis",
                    "axis": "source_binding",
                    "value": {
                        "kind": "previous_response",
                        "request_definition_id": "definition-opaque",
                        "response_path": "id",
                    },
                },
            ],
        },
    )

    field = result["steps"][0]["params"][0]
    assert field["classification"] == "runtime_var"
    assert field["category"] == "runtime_var"
    assert field["source_kind"] == "previous_response"
    assert field["source"] == {
        "kind": "previous_response",
        "source_request_id": "definition-opaque",
        "request_definition_id": "definition-opaque",
        "source_path": "id",
        "response_path": "id",
    }
