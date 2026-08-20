"""Regressions taken from real recording output, expressed without site-specific names."""

from __future__ import annotations

import json

import pytest

from dano.execution.page.flow_materialization.field_contracts.computed import (
    _infer_computed_runtime_fields,
)
from dano.execution.page.flow_materialization.builder import sync_flow_spec_models
from dano.execution.page.flow_spec import to_flow_spec
from dano.execution.page.flow_spec_core.models import (
    FlowSpec,
    FlowLink,
    FlowStep,
    ParamField,
    RequestAnalysis,
    RequestFact,
    SelectBinding,
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
            "field_identity_id": "field-id-beta-attachment",
            "observed_at": 100,
            "filename": "",
            "file_count": 0,
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
            "field_identity_id": "field-id-beta-attachment",
            "observed_at": 200,
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


def test_reanalysis_repairs_saved_unbound_file_control() -> None:
    fact, analysis = _request_fact(
        "req_saved", sequence=1, path="/v8/saved", role="business_write",
        response_json={"id": 7},
    )
    fact.method = "POST"
    fact.post_data = json.dumps({"title": "saved"})
    step = FlowStep(
        step_id="step_saved",
        method="POST",
        path="/v8/saved",
        url="http://generic.invalid/v8/saved",
        body_source=fact.post_data,
        source_meta={
            "request_id": "req_saved",
            "page_id": "page_saved",
            "frame_id": "frame_saved",
            "role": "business_write",
        },
        params=[ParamField(
            path="title", key="title", label="Title", value="saved",
            type="string", wire_type="string", source_kind="user_input",
            category="user_param", exposed_to_user=True,
        )],
    )
    spec = FlowSpec(tenant="t", subsystem="generic", steps=[step])
    spec.request_facts.requests = [fact]
    spec.request_facts.analysis = {"req_saved": analysis}
    spec.request_facts.field_evidence = [
        {
            "evidence_id": "field-evidence-title",
            "binding_status": "bound",
            "request_id": "req_saved",
            "wire_path": "body.title",
            "field_aliases": ["title"],
            "control_kind": "text",
            "form_root": "saved editor",
            "surface": "dialog",
            "in_dialog": True,
            "page_id": "page_saved",
            "frame_id": "frame_saved",
        },
        {
            "evidence_id": "field-evidence-saved-file",
            "binding_status": "unbound",
            "field_aliases": ["document"],
            "label": "Document",
            "control_kind": "file",
            "filename": "",
            "file_count": 0,
            "form_root": "saved editor",
            "surface": "dialog",
            "in_dialog": True,
            "page_id": "page_saved",
            "frame_id": "frame_saved",
        },
    ]

    repaired = sync_flow_spec_models(spec)

    document = next(param for param in repaired.steps[0].params if param.key == "document")
    assert document.type == "file"
    assert document.source.get("kind") == "file_input"
    assert document.source.get("unsupported_execution") is True


def test_reanalysis_invalidates_legacy_false_computed_and_constant_sources() -> None:
    query = FlowStep(
        step_id="step_query_saved",
        method="GET",
        path="/v8/search",
        source_meta={"request_id": "req_query_saved", "role": "business_get"},
        params=[ParamField(
            path="query.state", key="state", value="0",
            type="enum", wire_type="string", source_kind="constant",
            category="system_const", exposed_to_user=False,
            source={"kind": "constant", "actor": "agent", "required_state": "unknown"},
            evidence=[
                {
                    "kind": "page_control",
                    "control_kind": "select",
                    "editable": True,
                    "disabled": False,
                    "read_only": False,
                    "request_path": "query.state",
                },
                {
                    "kind": "param_source",
                    "source_kind": "constant",
                    "evidence_refs": ["field-evidence-state"],
                },
            ],
        )],
    )
    detail = FlowStep(
        step_id="step_detail_saved",
        method="GET",
        path="/v8/detail",
        source_meta={"request_id": "req_detail_saved", "role": "business_get"},
        response_json={"data": {"accountId": 2}},
    )
    update = FlowStep(
        step_id="step_update_saved",
        method="PUT",
        path="/v8/update",
        body_source=json.dumps({"accountId": 2}),
        source_meta={"request_id": "req_update_saved", "role": "business_write"},
        params=[ParamField(
            path="accountId", key="accountId", value=2,
            type="enum", wire_type="number", source_kind="computed",
            category="runtime_var", exposed_to_user=False,
            source={
                "kind": "computed",
                "strategy": "date_span_days",
                "start_field": "startsAt",
                "end_field": "endsAt",
                "sample_verified": True,
            },
        )],
    )
    spec = FlowSpec(
        tenant="t",
        subsystem="generic",
        steps=[query, detail, update],
        links=[FlowLink(
            source_step_id=detail.step_id,
            source_path="data.accountId",
            target_step_id=update.step_id,
            target_path="accountId",
            confirmed=True,
            confidence=1.0,
        )],
    )
    spec.request_facts.field_evidence = [{
        "evidence_id": "field-evidence-state",
        "binding_status": "bound",
        "request_id": "req_query_saved",
        "wire_path": "query.state",
        "control_kind": "select",
        "editable": True,
        "disabled": False,
        "read_only": False,
        "value": "0",
    }]

    repaired = sync_flow_spec_models(spec)

    state = repaired.steps[0].params[0]
    account = repaired.steps[2].params[0]
    assert state.source_kind != "constant"
    assert state.category == "user_param"
    assert state.exposed_to_user is True
    assert account.source_kind == "previous_response"
    assert account.source.get("step_id") == detail.step_id


def test_reanalysis_rebinds_suffixed_page_choices_to_bound_query_controls() -> None:
    """Repeated visible labels and preloaded catalogs must not cross-wire enums."""
    page_id = "page_generic"
    frame_id = "frame_generic"
    state_url = "http://generic.invalid/v6/dictionaries/simple-list"
    owner_url = "http://generic.invalid/v6/principals/simple-list"
    wrong_url = "http://generic.invalid/v6/inventory/simple-list"
    state_options = [
        {"label": "Pending", "value": "10", "dictType": "review_state"},
        {"label": "Accepted", "value": "20", "dictType": "review_state"},
    ]
    owner_rows = [
        {"id": 7, "displayName": "Avery", "categoryName": "Long contextual A"},
        {"id": 8, "displayName": "Blake", "categoryName": "Long contextual B"},
        {"id": 9, "displayName": "Blake", "categoryName": "Long contextual C"},
    ]

    def option_fact(request_id: str, sequence: int, url: str, rows: list[dict]) -> RequestFact:
        return RequestFact(
            request_id=request_id,
            request_index=sequence,
            sequence=sequence,
            method="GET",
            url=url,
            path=url.split("generic.invalid", 1)[-1],
            page_id=page_id,
            frame_id=frame_id,
            response_status=200,
            response_json={"code": 0, "data": rows},
        )

    wrong_fact = option_fact(
        "req_wrong", 1, wrong_url,
        [{"id": 20, "name": "Foreign value"}, {"id": 30, "name": "Other"}],
    )
    state_fact = option_fact("req_state", 2, state_url, state_options)
    owner_fact = option_fact("req_owner", 3, owner_url, owner_rows)
    owner_repeat_fact = option_fact("req_owner_repeat", 4, owner_url, owner_rows)
    collision_fact = option_fact(
        "req_collision", 5, "http://generic.invalid/v6/records/page", [
            {"referenceId": 7, "displayName": "Avery", "phase": "open"},
            {"referenceId": 8, "displayName": "Blake", "phase": "closed"},
            {"referenceId": 10, "displayName": "Foreign", "phase": "open"},
        ],
    )
    search_fact = RequestFact(
        request_id="req_search",
        request_index=6,
        sequence=6,
        method="GET",
        url="http://generic.invalid/v6/records?pageNo=1&reviewCode=20&ownerId=7",
        path="/v6/records",
        query={"pageNo": "1", "reviewCode": "20", "ownerId": "7"},
        page_id=page_id,
        frame_id=frame_id,
        response_status=200,
        response_json={"code": 0, "data": {"list": []}},
    )
    step = FlowStep(
        step_id="step_search",
        method="GET",
        path=search_fact.path,
        url=search_fact.url,
        semantic_role="business_get",
        source_meta={
            "request_id": search_fact.request_id,
            "sequence": search_fact.sequence,
            "page_id": page_id,
            "frame_id": frame_id,
        },
        params=[
            ParamField(
                path="query.reviewCode", key="Review state", label="Review state",
                value="20", type="enum", wire_type="string", source_kind="api_option",
                source={
                    "kind": "api_option", "source_url": wrong_url,
                    "source_request_id": "req_wrong", "value_key": "id", "label_key": "name",
                },
                enum_options=[{"label": "Foreign value", "value": 20}],
            ),
            ParamField(
                path="query.ownerId", key="Owner", label="Owner",
                value="7", type="number", wire_type="string", source_kind="user_input",
                source={"kind": "user_input", "actor": "agent"},
                category="user_param", exposed_to_user=True,
            ),
        ],
        selects=[SelectBinding(
            path="query.reviewCode",
            source_url=wrong_url,
            source_request_id="req_wrong",
            value_key="id",
            label_key="name",
            options=[{"label": "Foreign value", "value": 20}],
            option_map={"Foreign value": 20},
            enum_source="api",
            enum_confirmed=True,
        )],
    )
    spec = FlowSpec(tenant="t", subsystem="generic", steps=[step])
    spec.request_facts.requests = [
        wrong_fact, state_fact, owner_fact, owner_repeat_fact, collision_fact, search_fact,
    ]
    spec.request_facts.analysis = {
        request_id: RequestAnalysis(
            request_id=request_id,
            role="read_option" if request_id != "req_search" else "business_get",
            keep=True,
            confidence=0.95,
        )
        for request_id in (
            "req_wrong", "req_state", "req_owner", "req_owner_repeat", "req_search",
            "req_collision",
        )
    }
    spec.request_facts.field_evidence = [
        {
            "evidence_id": f"field-evidence-state-{index}",
            "occurrence_id": f"field-occ-state-{index}",
            "binding_status": "bound",
            "request_id": "req_search",
            "wire_path": "query.reviewCode",
            "label": "Review state",
            "field_aliases": ["reviewCode"],
            "control_kind": "select",
            "axes": ["name", "type"],
            "editable": True,
            "op": "snapshot",
            "page_id": page_id,
            "frame_id": frame_id,
            "observed_at": index,
        }
        for index in (1, 2)
    ] + [
        {
            "evidence_id": f"field-evidence-owner-{index}",
            "occurrence_id": f"field-occ-owner-{index}",
            "binding_status": "bound",
            "request_id": "req_search",
            "wire_path": "query.ownerId",
            "label": "Owner",
            "field_aliases": ["ownerId"],
            "control_kind": "select",
            "axes": ["name", "type"],
            "editable": True,
            "op": "snapshot",
            "page_id": page_id,
            "frame_id": frame_id,
            "observed_at": index,
        }
        for index in (1, 2)
    ]
    spec.request_facts.option_sources = [{
        "kind": "page_enum_options",
        "options": {
            "Review state#2": {
                "field_key": "Review state#2",
                "field_aliases": [],
                "control_kind": "select",
                "page_id": page_id,
                "frame_id": frame_id,
                "action_id": "action_pick_state",
                "transaction_id": "tx_pick_state",
                "enum_source": "script_dictionary",
                "mapping_complete": True,
                "source_url": state_url,
                "dict_type": "review_state",
                "options": state_options,
            },
            "Owner#2": {
                "field_key": "Owner#2",
                "field_aliases": [],
                "control_kind": "select",
                "page_id": page_id,
                "frame_id": frame_id,
                "action_id": "action_pick_owner",
                "transaction_id": "tx_pick_owner",
                "enum_source": "dom",
                "mapping_complete": False,
                "options": [{"label": "Avery"}, {"label": "Blake"}],
            },
        },
    }]

    repaired = sync_flow_spec_models(spec)

    by_path = {param.path: param for param in repaired.steps[0].params}
    state = by_path["query.reviewCode"]
    owner = by_path["query.ownerId"]
    assert state.source_kind == "api_option"
    assert state.source.get("source_url") == state_url
    assert state.source.get("source_request_id") == "req_state"
    assert state.enum_value_map == {"Pending": "10", "Accepted": "20"}
    assert owner.source_kind == "api_option", owner.model_dump(mode="json")
    assert owner.source.get("source_url") == owner_url
    assert owner.source.get("source_request_id") == ""
    assert set(owner.enum_value_map or {}) == {"Avery", "Blake [8]", "Blake [9]"}
    assert set((owner.enum_value_map or {}).values()) == {7, 8, 9}


def test_reanalysis_restores_exact_option_request_cohort() -> None:
    old_fact, old_analysis = _request_fact(
        "req_old", sequence=1, path="/v4/account-options", role="read_option",
        response_json={"data": [{"id": 1, "name": "Old"}]},
    )
    first_fact, first_analysis = _request_fact(
        "req_first", sequence=2, path="/v4/customer-options", role="read_option",
        response_json={"data": [{"id": 11, "name": "Customer"}]},
    )
    second_fact, second_analysis = _request_fact(
        "req_second", sequence=3, path="/v4/user-options", role="read_option",
        response_json={"data": [{"id": 12, "name": "User"}]},
    )
    exact_fact, exact_analysis = _request_fact(
        "req_exact", sequence=4, path="/v4/account-options", role="read_option",
        response_json={"data": [
            {"id": 2, "name": "Current"},
            {"id": 3, "name": "Duplicate"},
            {"id": 4, "name": "Duplicate"},
        ]},
    )
    write_fact, write_analysis = _request_fact(
        "req_write", sequence=5, path="/v4/entities", role="business_write",
        response_json={"ok": True},
    )
    write_fact.post_data = json.dumps({"accountId": 2})
    old_fact.trigger_event_id = "event_old"
    old_fact.trigger_action_id = "action_old"
    old_fact.trigger_transaction_id = "tx_old"
    for fact in (first_fact, second_fact, exact_fact):
        fact.trigger_event_id = "event_open"
        fact.trigger_action_id = "action_open"
        fact.trigger_transaction_id = "tx_open"

    old_step = FlowStep(
        step_id="step_old",
        method="GET",
        path=old_fact.path,
        url=old_fact.url,
        response_json=old_fact.response_json,
        semantic_role="read_option",
        source_meta={
            "request_id": "req_old",
            "role": "read_option",
            "sequence": 1,
            "page_id": "page_generic",
            "frame_id": "frame_generic",
            "trigger_event_id": "event_old",
            "trigger_action_id": "action_old",
            "trigger_transaction_id": "tx_old",
        },
    )
    write_step = FlowStep(
        step_id="step_write",
        method="POST",
        path=write_fact.path,
        url=write_fact.url,
        body_source=write_fact.post_data,
        semantic_role="business_write",
        source_meta={
            "request_id": "req_write",
            "role": "business_write",
            "sequence": 5,
            "page_id": "page_generic",
            "frame_id": "frame_generic",
        },
        params=[ParamField(
            path="accountId",
            key="accountId",
            value=2,
            type="enum",
            wire_type="number",
            source_kind="previous_response",
            source={"kind": "previous_response", "step_id": "step_old", "path": "data[0].id"},
        )],
    )
    spec = FlowSpec(tenant="t", subsystem="generic", steps=[old_step, write_step])
    spec.links = [FlowLink(
        source_step_id="step_old",
        source_path="data[0].id",
        target_step_id="step_write",
        target_path="accountId",
        confidence=0.75,
        confirmed=False,
        meta={"actor": "agent", "verified": False},
    )]
    spec.request_facts.requests = [
        old_fact, first_fact, second_fact, exact_fact, write_fact,
    ]
    spec.request_facts.analysis = {
        "req_old": old_analysis,
        "req_first": first_analysis,
        "req_second": second_analysis,
        "req_exact": exact_analysis,
        "req_write": write_analysis,
    }
    spec.meta = {
        "capability_model": {
            "semantic_plan": {
                "capabilities": [{
                    "name": "create_entity",
                    "title": "Create entity",
                    "kind": "create",
                    "anchor_step_id": "step_write",
                    "request_refs": [
                        {"step_id": "req_first", "usage": "option_source"},
                        {"step_id": "req_second", "usage": "option_source"},
                        {"step_id": "step_old", "usage": "option_source"},
                        {"step_id": "step_write", "usage": "execute"},
                    ],
                }],
                "unresolved_items": [],
            },
        },
    }

    repaired = sync_flow_spec_models(spec)

    step_by_request = {
        str((step.source_meta or {}).get("request_id") or ""): step
        for step in repaired.steps
    }
    assert {"req_first", "req_second", "req_exact"} <= set(step_by_request)
    capability = next(cap for cap in repaired.capabilities if cap.name == "create_entity")
    option_request_ids = {
        ref.request_id for ref in capability.request_refs if ref.usage == "option_source"
    }
    assert option_request_ids == {"req_first", "req_second", "req_exact"}
    assert not any(
        link.source_step_id == old_step.step_id
        and link.target_step_id == write_step.step_id
        for link in repaired.links
    )
    account = next(param for param in step_by_request["req_write"].params if param.path == "accountId")
    assert account.source_kind == "api_option"
    assert account.source.get("source_request_id") == "req_exact"
    assert {item["value"] for item in account.enum_options or []} == {2, 3, 4}
    assert len({item["label"] for item in account.enum_options or []}) == 3
