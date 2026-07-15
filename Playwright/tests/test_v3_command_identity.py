from __future__ import annotations

from copy import deepcopy

import pytest

from dano_recording.api.decision_commands import (
    DecisionCommandError,
    _find_edit_step,
    _find_param,
    _materialize_request,
)


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
