"""Regressions taken from real recording output, expressed without site-specific names."""

from __future__ import annotations

import json

import pytest

from dano.execution.page.flow_materialization.field_contracts.computed import (
    _infer_computed_runtime_fields,
)
from dano.execution.page.flow_spec import to_flow_spec
from dano.execution.page.flow_spec_core.models import (
    FlowSpec,
    FlowStep,
    ParamField,
    RequestAnalysis,
    RequestFact,
)
from dano.execution.page.recording_live import (
    apply_recording_agent_edit,
    merge_live_agent_state,
)


def test_two_date_filters_do_not_make_unrelated_scalars_computed() -> None:
    step = FlowStep(
        step_id="step_query",
        method="GET",
        path="/v9/entities",
        source_meta={"request_id": "req_query", "sequence": 1},
        params=[
            ParamField(
                path="query.pageNo", key="pageNo", value="1",
                type="number", wire_type="number", source_kind="unknown",
            ),
            ParamField(
                path="query.ownerCode", key="ownerCode", value="73",
                type="string", wire_type="string", source_kind="unknown",
            ),
            ParamField(
                path="query.period[0]", key="period[0]", value="2026-04-08 00:00:00",
                type="date", wire_type="string", source_kind="user_input",
                category="user_param", exposed_to_user=True,
            ),
            ParamField(
                path="query.period[1]", key="period[1]", value="2026-04-09 00:00:00",
                type="date", wire_type="string", source_kind="user_input",
                category="user_param", exposed_to_user=True,
            ),
        ],
    )
    spec = FlowSpec(tenant="t", subsystem="generic", steps=[step])

    _infer_computed_runtime_fields(spec)

    by_path = {param.path: param for param in step.params}
    assert by_path["query.pageNo"].source_kind != "computed"
    assert by_path["query.ownerCode"].source_kind != "computed"


def test_two_dates_do_not_make_an_unrelated_numeric_code_computed() -> None:
    step = FlowStep(
        step_id="step_code",
        method="POST",
        path="/v9/entities",
        source_meta={"request_id": "req_code", "sequence": 1},
        params=[
            ParamField(
                path="ownerCode", key="ownerCode", value="1",
                type="string", wire_type="string", source_kind="unknown",
            ),
            ParamField(
                path="startsAt", key="startsAt", value="2026-06-10 00:00:00",
                type="date", wire_type="string", source_kind="user_input",
            ),
            ParamField(
                path="endsAt", key="endsAt", value="2026-06-11 00:00:00",
                type="date", wire_type="string", source_kind="user_input",
            ),
        ],
    )
    spec = FlowSpec(tenant="t", subsystem="generic", steps=[step])

    _infer_computed_runtime_fields(spec)

    owner_code = next(param for param in step.params if param.key == "ownerCode")
    assert owner_code.source_kind != "computed"


def test_agent_cannot_freeze_editable_snapshot_value_as_constant() -> None:
    evidence_id = "field-evidence-generic-choice"
    param = ParamField(
        path="query.choice", key="choice", value="0",
        type="enum", wire_type="string", source_kind="unknown",
        evidence=[{
            "kind": "page_control",
            "evidence_id": evidence_id,
            "control_kind": "select",
            "editable": True,
            "disabled": False,
            "read_only": False,
            "interacted": False,
        }],
    )
    step = FlowStep(
        step_id="step_query",
        method="GET",
        path="/v3/search",
        source_meta={"request_id": "req_query"},
        params=[param],
    )
    spec = FlowSpec(tenant="t", subsystem="generic", steps=[step])
    spec.request_facts.field_evidence = [{
        "evidence_id": evidence_id,
        "binding_status": "bound",
        "request_id": "req_query",
        "wire_path": "query.choice",
        "control_kind": "select",
        "editable": True,
        "disabled": False,
        "read_only": False,
        "op": "snapshot",
        "value": "0",
    }]

    with pytest.raises(ValueError, match="constant"):
        apply_recording_agent_edit(spec, {
            "op": "set_param_source",
            "step_id": "step_query",
            "path": "query.choice",
            "source_kind": "constant",
            "reason": "the sample happened to stay unchanged",
            "evidence_refs": [evidence_id],
        })


def _request_fact(
    request_id: str,
    *,
    sequence: int,
    path: str,
    role: str,
    response_json: object,
) -> tuple[RequestFact, RequestAnalysis]:
    fact = RequestFact(
        request_id=request_id,
        request_index=sequence,
        sequence=sequence,
        method="GET" if role == "read_option" else "POST",
        url=f"http://generic.invalid{path}",
        path=path,
        page_id="page_generic",
        frame_id="frame_generic",
        response_status=200,
        response_json=response_json,
    )
    analysis = RequestAnalysis(
        request_id=request_id,
        role=role,
        keep=True,
        confidence=1.0,
    )
    return fact, analysis


def test_finalize_materializes_exact_planned_option_request() -> None:
    old_fact, old_analysis = _request_fact(
        "req_old", sequence=1, path="/v4/options", role="read_option",
        response_json={"data": [{"id": 1, "name": "old"}]},
    )
    new_fact, new_analysis = _request_fact(
        "req_new", sequence=2, path="/v4/options", role="read_option",
        response_json={"data": [{"id": 2, "name": "new"}]},
    )
    write_fact, write_analysis = _request_fact(
        "req_write", sequence=3, path="/v4/entities", role="business_write",
        response_json={"ok": True},
    )
    write_fact.post_data = json.dumps({"choiceId": 2})
    finalized = FlowSpec(
        tenant="t",
        subsystem="generic",
        steps=[
            FlowStep(
                step_id="step_old", method="GET", path="/v4/options",
                url="http://generic.invalid/v4/options",
                semantic_role="read_option",
                source_meta={"request_id": "req_old", "sequence": 1, "role": "read_option"},
                response_json=old_fact.response_json,
            ),
            FlowStep(
                step_id="step_write", method="POST", path="/v4/entities",
                url="http://generic.invalid/v4/entities",
                semantic_role="business_write",
                source_meta={"request_id": "req_write", "sequence": 3, "role": "business_write"},
                params=[ParamField(
                    path="choiceId", key="choiceId", value=2,
                    type="number", wire_type="number", source_kind="user_input",
                    category="user_param", exposed_to_user=True,
                )],
            ),
        ],
    )
    finalized.request_facts.requests = [old_fact, new_fact, write_fact]
    finalized.request_facts.analysis = {
        "req_old": old_analysis,
        "req_new": new_analysis,
        "req_write": write_analysis,
    }
    live = FlowSpec(tenant="t", subsystem="generic")
    live.meta = {
        "capability_model": {
            "semantic_plan": {
                "business_understanding": {"business_name": "Generic entities"},
                "capabilities": [{
                    "name": "create_entity",
                    "title": "Create entity",
                    "kind": "create",
                    "anchor_step_id": "req_write",
                    "request_refs": [
                        {"step_id": "req_new", "usage": "option_source"},
                        {"step_id": "req_write", "usage": "execute"},
                    ],
                }],
                "unresolved_items": [],
            },
        },
    }

    merged = merge_live_agent_state(live, finalized)

    step_by_request = {
        str((step.source_meta or {}).get("request_id") or ""): step
        for step in merged.steps
    }
    assert "req_new" in step_by_request
    capability = next(cap for cap in merged.capabilities if cap.name == "create_entity")
    referenced_requests = {
        ref.request_id
        or str((next(
            step for step in merged.steps if step.step_id == ref.step_id
        ).source_meta or {}).get("request_id") or "")
        for ref in capability.request_refs
    }
    assert "req_new" in referenced_requests
    assert "req_old" not in referenced_requests


def test_unselected_file_control_remains_a_caller_file_input() -> None:
    spec = to_flow_spec(
        captured_requests=[{
            "request_id": "req_submit",
            "request_index": 1,
            "sequence": 1,
            "method": "POST",
            "url": "http://generic.invalid/v8/entities",
            "content_type": "application/json",
            "post_data": json.dumps({"title": "draft"}),
            "response_status": 200,
            "response_json": {"id": 9},
            "page_id": "page_generic",
            "frame_id": "frame_generic",
            "trigger_action_id": "action_submit",
            "trigger_transaction_id": "tx_submit",
            "_request_role": {"role": "business_write", "keep": True, "confidence": 1.0},
        }],
        field_evidence=[
            {
                "field": "title",
                "label": "Heading",
                "field_aliases": ["title"],
                "control_kind": "text",
                "value": "draft",
                "surface": "dialog",
                "in_dialog": True,
                "form_root": "entity editor",
                "action_id": "action_submit",
                "transaction_id": "tx_submit",
                "page_id": "page_generic",
                "frame_id": "frame_generic",
                "binding_status": "bound",
                "request_id": "req_submit",
                "wire_path": "body.title",
                "editable": True,
            },
            {
                "field": "artifact",
                "label": "Artifact",
                "field_aliases": ["artifact"],
                "control_kind": "file",
                "filename": "",
                "file_count": 0,
                "multiple": False,
                "surface": "dialog",
                "in_dialog": True,
                "form_root": "entity editor",
                "action_id": "action_submit",
                "transaction_id": "tx_submit",
                "page_id": "page_generic",
                "frame_id": "frame_generic",
                "binding_status": "unbound",
                "editable": True,
            },
        ],
        page_events=[{
            "event_id": "event_submit",
            "kind": "submit",
            "action_id": "action_submit",
            "transaction_id": "tx_submit",
        }],
        page_context={"url": "http://generic.invalid/editor", "path": "/editor"},
    )

    submit = next(step for step in spec.steps if step.path == "/v8/entities")
    file_params = [
        param for param in submit.params
        if str((param.source or {}).get("kind") or "") == "file_input"
    ]
    assert len(file_params) == 1
    assert file_params[0].type == "file"
    assert file_params[0].exposed_to_user is True
    assert (file_params[0].source or {}).get("unsupported_execution") is True


def test_unselected_file_control_uses_exact_form_siblings_without_action_ids() -> None:
    requests = [
        {
            "request_id": "req_alpha",
            "request_index": 1,
            "sequence": 1,
            "method": "POST",
            "url": "http://generic.invalid/v8/alpha",
            "content_type": "application/json",
            "post_data": json.dumps({"alphaTitle": "first"}),
            "response_status": 200,
            "response_json": {"id": 1},
            "page_id": "page_generic",
            "frame_id": "frame_generic",
            "_request_role": {"role": "business_write", "keep": True, "confidence": 1.0},
        },
        {
            "request_id": "req_beta",
            "request_index": 2,
            "sequence": 2,
            "method": "POST",
            "url": "http://generic.invalid/v8/beta",
            "content_type": "application/json",
            "post_data": json.dumps({"betaTitle": "second"}),
            "response_status": 200,
            "response_json": {"id": 2},
            "page_id": "page_generic",
            "frame_id": "frame_generic",
            "_request_role": {"role": "business_write", "keep": True, "confidence": 1.0},
        },
    ]
    evidence = [
        {
            "field_aliases": ["alphaTitle"],
            "control_kind": "text",
            "value": "first",
            "surface": "dialog",
            "in_dialog": True,
            "form_root": "alpha editor",
            "page_id": "page_generic",
            "frame_id": "frame_generic",
        },
        {
            "field_aliases": ["betaTitle"],
            "control_kind": "text",
            "value": "second",
            "surface": "dialog",
            "in_dialog": True,
            "form_root": "beta editor",
            "page_id": "page_generic",
            "frame_id": "frame_generic",
        },
        {
            "field_aliases": ["attachment"],
            "label": "Attachment",
            "control_kind": "file",
            "filename": "",
            "file_count": 0,
            "surface": "dialog",
            "in_dialog": True,
            "form_root": "beta editor",
            "page_id": "page_generic",
            "frame_id": "frame_generic",
        },
    ]

    spec = to_flow_spec(
        captured_requests=requests,
        field_evidence=evidence,
        page_events=[{"event_id": "snapshot_generic", "kind": "snapshot"}],
        page_context={"url": "http://generic.invalid/editor", "path": "/editor"},
    )

    alpha = next(step for step in spec.steps if step.path == "/v8/alpha")
    beta = next(step for step in spec.steps if step.path == "/v8/beta")
    assert not any((param.source or {}).get("kind") == "file_input" for param in alpha.params)
    beta_files = [
        param for param in beta.params
        if (param.source or {}).get("kind") == "file_input"
    ]
    assert [param.key for param in beta_files] == ["attachment"]
    assert beta_files[0].source.get("wire_path_observed") is False
