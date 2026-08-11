from __future__ import annotations

import pytest

from dano.execution.page.flow_spec import FlowSpec, FlowStep, ParamField, RequestFact, RequestFacts
from dano.execution.page.recording_field_identity import (
    FieldRef,
    FieldReferenceError,
    canonical_wire_path,
    resolve_field_ref,
)


def _spec() -> FlowSpec:
    return FlowSpec(
        flow_id="field-identity",
        steps=[
            FlowStep(
                step_id="query-step",
                method="GET",
                path="/leave/page",
                params=[ParamField(path="query.type", key="type", value=1)],
                source_meta={"request_id": "req-query"},
            ),
            FlowStep(
                step_id="submit-step",
                method="POST",
                path="/leave/submit",
                params=[
                    ParamField(path="type", key="type", value=1),
                    ParamField(path="reason", key="reason", value="test"),
                ],
                source_meta={"request_id": "req-submit"},
            ),
        ],
        request_facts=RequestFacts(
            requests=[
                RequestFact(request_id="req-query", method="GET", path="/leave/page"),
                RequestFact(request_id="req-submit", method="POST", path="/leave/submit"),
            ]
        ),
    )


def test_resolve_field_ref_maps_canonical_body_path_to_stored_body_param() -> None:
    resolved = resolve_field_ref(
        _spec(),
        FieldRef(request_id="req-submit", wire_path="body.type"),
    )

    assert resolved.step_id == "submit-step"
    assert resolved.request_id == "req-submit"
    assert resolved.stored_path == "type"
    assert resolved.wire_path == "body.type"
    assert resolved.param.value == 1


def test_resolve_field_ref_preserves_query_namespace() -> None:
    spec = _spec()
    resolved = resolve_field_ref(
        spec,
        FieldRef(step_id="query-step", wire_path="query.type"),
    )

    assert canonical_wire_path(resolved.step, resolved.stored_path) == "query.type"
    assert resolved.stored_path == "query.type"


def test_resolve_field_ref_does_not_leaf_match_without_a_step_or_request() -> None:
    with pytest.raises(FieldReferenceError, match="step_id or request_id"):
        resolve_field_ref(_spec(), FieldRef(wire_path="type"))


def test_resolve_field_ref_rejects_wrong_transport_namespace() -> None:
    with pytest.raises(FieldReferenceError, match="field target not found"):
        resolve_field_ref(
            _spec(),
            FieldRef(request_id="req-query", wire_path="body.type"),
        )
