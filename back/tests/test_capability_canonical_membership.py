from __future__ import annotations

from pathlib import Path

import pytest

from dano.execution.page.flow_spec import (
    CapabilityRequestRef,
    FlowCapability,
    FlowSpec,
    FlowStep,
    RequestFact,
    RequestFacts,
    _normalize_capability_references,
    apply_client_flow_patch,
    apply_flow_edits,
    flow_spec_fingerprint,)


def _spec(capability: FlowCapability) -> FlowSpec:
    return FlowSpec(
        steps=[
            FlowStep(
                step_id="query",
                method="GET",
                path="/api/query",
                source_meta={"request_id": "req-query", "request_index": 1},
            ),
            FlowStep(
                step_id="submit",
                method="POST",
                path="/api/submit",
                source_meta={"request_id": "req-submit", "request_index": 2},
            ),
        ],
        request_facts=RequestFacts(
            requests=[
                RequestFact(request_id="req-query", request_index=1, method="GET", path="/api/query"),
                RequestFact(request_id="req-submit", request_index=2, method="POST", path="/api/submit"),
            ]
        ),
        capabilities=[capability],
    )


def test_nodes_override_divergent_derived_membership_views() -> None:
    spec = _spec(FlowCapability(
        name="submit",
        nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
        step_ids=["query"],
        request_refs=[CapabilityRequestRef(
            request_id="req-query", step_id="query", usage="execute", origin="manual",
        )],
    ))

    _normalize_capability_references(spec)

    cap = spec.capabilities[0]
    assert cap.step_ids == ["submit"]
    assert [(ref.step_id, ref.request_id, ref.usage) for ref in cap.request_refs] == [
        ("submit", "req-submit", "execute"),
    ]


def test_auxiliary_request_refs_survive_execute_membership_derivation() -> None:
    spec = _spec(FlowCapability(
        name="submit",
        nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
        request_refs=[
            CapabilityRequestRef(request_id="stale", step_id="query", usage="execute"),
            CapabilityRequestRef(
                request_id="req-query",
                step_id="query",
                usage="option_source",
                origin="manual",
                confirmed=True,
            ),
        ],
    ))

    _normalize_capability_references(spec)

    refs = spec.capabilities[0].request_refs
    assert [(ref.step_id, ref.request_id, ref.usage) for ref in refs] == [
        ("submit", "req-submit", "execute"),
        ("query", "req-query", "option_source"),
    ]


@pytest.mark.parametrize("field", ["step_ids", "request_refs"])
def test_derived_membership_views_cannot_be_written_directly(field: str) -> None:
    spec = _spec(FlowCapability(
        name="submit",
        nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
    ))

    with pytest.raises(ValueError, match="derived capability field is read-only"):
        apply_flow_edits(spec, [{
            "op": "update_capability",
            "capability_index": 0,
            "field": field,
            "value": [],
        }])


def test_client_cannot_smuggle_legacy_membership_into_capability_payload() -> None:
    spec = _spec(FlowCapability(name="submit", nodes=[]))

    with pytest.raises(ValueError, match="membership must be expressed through nodes"):
        apply_client_flow_patch(
            spec,
            [{
                "op": "upsert_capability",
                "capability": {"name": "submit", "step_ids": ["submit"]},
            }],
            expected_fingerprint=flow_spec_fingerprint(spec),
        )

def test_frontend_edits_capability_membership_through_one_typed_path() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "skillfrontend"
        / "src"
        / "components"
        / "PageRecorder.tsx"
    ).read_text(encoding="utf-8")

    assert 'field: "request_refs"' not in source
    assert 'updateCapabilityField(idx, "step_ids"' not in source
    assert 'op: "reorder_capability_steps"' in source


def test_frontend_renders_every_non_execute_capability_interface() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "skillfrontend"
        / "src"
        / "components"
        / "PageRecorder.tsx"
    ).read_text(encoding="utf-8")

    assert 'ref.usage !== "execute"' in source
    assert "{capabilityUsageLabel(ref.usage)}" in source


def test_frontend_optimistically_keeps_added_step_visible() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "skillfrontend"
        / "src"
        / "components"
        / "PageRecorder.tsx"
    ).read_text(encoding="utf-8")
    handler = source.split("function addStepToCapability", 1)[1].split(
        "function removeStepFromCapability", 1,
    )[0]

    assert "optimisticNodes" in handler
    assert "_rollback:" in handler
