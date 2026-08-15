"""Recording V2 capability-centric scenario regressions.

These tests exercise cross-model invariants instead of isolated helper output:
request facts remain complete, capability nodes define execution scope, and the
derived field/dependency/schema views stay aligned with that scope.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import dano.execution.page.flow_spec as flow_spec_module
from dano.execution.page.recorder import RecordSession
from dano.execution.page.repair_ops import collect_capability_findings
from dano.execution.page.flow_spec import (
    CapabilityField,
    CapabilityRelation,
    CapabilityRequestRef,
    FlowCapability,
    FlowLink,
    FlowSpec,
    FlowStep,
    ParamField,
    SelectBinding,
    apply_flow_edits,
    flow_spec_to_api_request,
    flow_spec_to_client,
    orchestrate_flow_capabilities,
    prepare_flow_spec_for_publish,
    promote_request_to_step,
    sync_flow_spec_models,
    to_flow_spec,
    validate_flow_spec,
)


def _call_nodes(step_ids: list[str]) -> list[dict]:
    return [
        {"id": f"call_{index}", "type": "call", "step_id": step_id}
        for index, step_id in enumerate(step_ids)
    ]


def _strict_submission(
    *abilities: tuple[str, str, str, str, list[tuple[str, str]]],
) -> dict:
    """Build an explicit current-protocol plan; never infer public abilities."""
    return {
        "semantic_plan": {
            "business_understanding": {"summary": "录制业务能力"},
            "capabilities": [
                {
                    "name": name,
                    "title": title,
                    "kind": kind,
                    "anchor_step_id": anchor,
                    "request_refs": [
                        {"step_id": step_id, "usage": usage}
                        for step_id, usage in refs
                    ],
                }
                for name, title, kind, anchor, refs in abilities
            ],
            "unresolved_items": [],
        },
        "ops": [],
    }


def _get(index: int, path: str, response_json: dict) -> dict:
    return {
        "index": index,
        "sequence": index,
        "method": "GET",
        "url": f"https://oa.example.test{path}",
        "content_type": "application/json",
        "headers": {"Authorization": "Bearer test"},
        "response_status": 200,
        "response_json": response_json,
    }


def test_search_result_referenced_by_later_write_remains_business_query() -> None:
    query = {
        "index": 1,
        "request_id": "query",
        "method": "GET",
        "url": "https://example.test/api/applications/page?pageNo=1&pageSize=10&status=2",
        "query": {"pageNo": "1", "pageSize": "10", "status": "2"},
        "trigger_action_id": "search-action",
        "trigger_transaction_id": "page|main|search-action",
        "trigger_op": "click",
        "trigger_locator": "text=Search",
        "response_json": {
            "data": {
                "list": [{
                    "id": "record-1", "status": 2,
                    "createdAt": "2026-07-20 20:00:00",
                    "description": "same recorded value",
                }],
                "total": 1,
            },
        },
    }
    later_write = {
        "index": 2,
        "method": "POST",
        "url": "https://example.test/api/applications/submit",
        "post_data": '{"description":"same recorded value"}',
    }

    classified = flow_spec_module.classify_network_request(
        query, trace=[query, later_write],
    )

    assert classified["role"] == "business_get"
    assert classified["keep"] is True


def test_same_action_value_collision_does_not_turn_textarea_into_api_enum() -> None:
    textarea = ParamField(
        path="description", key="Description", value="1", default_value="1",
        type="string", wire_type="string", required=True,
        category="user_param", source_kind="user_input",
        evidence=[{
            "kind": "page_required", "source": "recorder_dom",
            "request_path": "description",
        }],
    )
    submit = FlowStep(
        step_id="submit", method="POST", path="/api/applications/submit",
        params=[textarea],
        source_meta={
            "role": "business_write", "trigger_action_id": "submit-action",
            "trigger_transaction_id": "page|main|submit-action",
        },
    )
    unrelated = FlowStep(
        step_id="users", method="GET", path="/system/users/list",
        response_json={"data": [{"id": 1, "name": "Administrator"}, {"id": 2, "name": "Reviewer"}]},
        source_meta={
            "role": "read_context", "trigger_action_id": "submit-action",
            "trigger_transaction_id": "page|main|submit-action",
        },
    )
    spec = FlowSpec(steps=[submit, unrelated])

    repaired = flow_spec_module._repair_structural_option_bindings(spec)

    assert repaired == 0
    assert textarea.type == "string"
    assert textarea.category == "user_param"
    assert textarea.source_kind == "user_input"
    assert submit.selects == []


def test_weak_info_token_does_not_bind_use_info_to_permission_list() -> None:
    use_info = ParamField(
        path="useInfo", key="useInfo", value="1", default_value="1",
        type="string", wire_type="string", required=True,
        category="user_param", source_kind="user_input",
        evidence=[{
            "kind": "page_required", "source": "recorder_dom",
            "request_path": "useInfo",
        }],
    )
    submit = FlowStep(
        step_id="submit", method="POST", path="/api/applications/submit",
        params=[use_info], source_meta={"role": "business_write"},
    )
    permissions = FlowStep(
        step_id="permissions", method="GET",
        path="/admin-api/system/auth/get-permission-info",
        response_json={"data": [
            {"id": 1, "name": "System Management"},
            {"id": 2, "name": "Infrastructure"},
        ]},
        source_meta={"role": "read_context"},
    )
    spec = FlowSpec(steps=[submit, permissions])

    repaired = flow_spec_module._repair_structural_option_bindings(spec)

    assert repaired == 0
    assert use_info.type == "string"
    assert use_info.source_kind == "user_input"
    assert use_info.enum_options is None
    assert submit.selects == []


def test_numeric_looking_info_query_remains_text_without_a_number_control() -> None:
    fields = flow_spec_module._params_from_get_query({
        "method": "GET",
        "url": "https://example.test/api/applications/page?useInfo=1",
    })

    assert fields[0]["path"] == "query.useInfo"
    assert fields[0]["type"] == "string"
    assert fields[0]["wire_type"] == "string"


def test_later_plain_analysis_removes_a_stale_weak_text_option_binding() -> None:
    stale_source = {
        "kind": "api_option",
        "source_url": "/admin-api/system/auth/get-permission-info",
        "value_key": "id",
        "label_key": "name",
    }
    spec = FlowSpec(
        steps=[FlowStep(
            step_id="submit", method="POST", path="/api/applications/submit",
            source_meta={"request_id": "req-submit", "role": "business_write"},
            params=[ParamField(
                path="useInfo", key="useInfo", label="useInfo", value="1",
                type="enum", wire_type="string", category="user_param",
                source_kind="api_option", source=stale_source,
                enum_options=[{"label": "System Management", "value": "1"}],
                enum_value_map={"System Management": "1"},
            )],
            selects=[SelectBinding(
                path="useInfo", param="useInfo",
                source_url=stale_source["source_url"],
                value_key="id", label_key="name",
                options=[{"label": "System Management", "value": "1"}],
                option_map={"System Management": "1"},
            )],
        )],
        request_facts=flow_spec_module.RequestFacts(field_evidence=[{
            "event_id": "event-use-info", "binding_status": "bound",
            "request_id": "req-submit", "wire_path": "body.useInfo",
            "control_kind": "textarea", "field_aliases": ["useInfo"],
        }]),
    )
    optimized = apply_flow_edits(spec, [{
        "op": "set_param_source", "step_id": "submit",
        "wire_path": "body.useInfo", "source_kind": "caller_input",
        "reason": "录制文本域由操作人填写", "evidence_refs": ["event-use-info"],
    }, {
        "op": "set_param_type", "step_id": "submit",
        "wire_path": "body.useInfo", "business_type": "string",
        "reason": "录制控件是文本域", "evidence_refs": ["event-use-info"],
    }])
    field = optimized.steps[0].params[0]

    assert (field.type, field.wire_type) == ("string", "string")
    assert (field.category, field.source_kind) == ("user_param", "user_input")
    assert field.enum_options is None
    assert field.enum_value_map is None
    assert optimized.steps[0].selects == []


def test_later_plain_analysis_repairs_numeric_sample_type_for_text_field() -> None:
    spec = FlowSpec(
        steps=[FlowStep(
            step_id="query", method="GET", path="/api/applications/page?useInfo=1",
            source_meta={"request_id": "req-query", "role": "business_get"},
            response_json={"data": {"list": [{"id": 1}], "total": 1}},
            params=[ParamField(
                path="query.useInfo", key="useInfo", value="1",
                type="number", wire_type="string", category="user_param",
                source_kind="user_input",
            )],
        )],
        request_facts=flow_spec_module.RequestFacts(field_evidence=[{
            "event_id": "event-use-info", "binding_status": "bound",
            "request_id": "req-query", "wire_path": "query.useInfo",
            "control_kind": "text", "field_aliases": ["useInfo"],
        }]),
    )
    optimized = apply_flow_edits(spec, [{
        "op": "set_param_type", "step_id": "query",
        "wire_path": "query.useInfo", "business_type": "string",
        "reason": "录制控件是文本输入框", "evidence_refs": ["event-use-info"],
    }])
    field = optimized.steps[0].params[0]

    assert (field.type, field.wire_type) == ("string", "string")
    assert field.source_kind == "user_input"


def _post(index: int, path: str, body: dict | list, response_json: dict | None = None) -> dict:
    return {
        "index": index,
        "sequence": index,
        "method": "POST",
        "url": f"https://oa.example.test{path}",
        "content_type": "application/json",
        "headers": {"Authorization": "Bearer test", "Content-Type": "application/json"},
        "post_data": json.dumps(body, ensure_ascii=False),
        "response_status": 200,
        "response_json": response_json or {"code": 0, "data": True},
    }


def _walk_nodes(nodes: list[dict]) -> list[dict]:
    flattened: list[dict] = []
    for node in nodes:
        flattened.append(node)
        for key in ("children", "steps", "then", "else", "otherwise"):
            child = node.get(key)
            if isinstance(child, list):
                flattened.extend(_walk_nodes([item for item in child if isinstance(item, dict)]))
    return flattened


@pytest.fixture(scope="module")
def r0_seal_recording_truth_spec() -> FlowSpec:
    """脱敏真实录制：列表查询、审批前置、候选源和最终提交的完整混合流程。"""
    option_response = {"data": [
        {"id": "seal-a", "name": "公司章"},
        {"id": "seal-b", "name": "财务章"},
    ]}
    process_definition_id = "oa_seal_apply:1:def"
    captured = [
        _get(
            1,
            "/admin-api/oa/seal-apply/page?pageNo=1&pageSize=10&processStatus=2",
            {"data": {"list": [], "total": 0}},
        ),
        _get(
            2,
            "/admin-api/bpm/process-definition/get?key=oa_seal_apply",
            {"data": {"id": process_definition_id}},
        ),
        _get(
            3,
            "/admin-api/bpm/process-instance/get-approval-detail?"
            "processDefinitionId=oa_seal_apply%3A1%3Adef&activityId=StartUserNode",
            {"data": {"node": "StartUserNode"}},
        ),
        _get(4, "/admin-api/bd/seal/simple-list?status=0", option_response),
        _post(5, "/admin-api/oa/seal-apply/submit-process", {
            "sealId": "seal-a",
            "applyTitle": "项目用章",
            "useTime": 1784476800000,
            "backTime": 1784563200000,
            "useInfo": "项目材料",
            "billType": "oa_seal_apply",
            "processDefKey": "oa_seal_apply",
            "remark": "当天归还",
        }),
    ]
    for request in captured:
        request.update({"page_id": "seal-page", "frame_id": "main", "resource_type": "xhr"})
    captured[0].update({
        "trigger_action_id": "query-seal-applications",
        "trigger_transaction_id": "txn-query-seal-applications",
        "trigger_op": "click",
        "trigger_locator": "button[type=submit]",
    })
    for request in (captured[1], captured[2], captured[4]):
        request.update({
            "trigger_action_id": "submit-seal-application",
            "trigger_transaction_id": "txn-submit-seal-application",
        })
    captured[3].update({
        "trigger_action_id": "select-seal",
        "trigger_transaction_id": "txn-select-seal",
        "trigger_op": "select",
        "trigger_locator": "[role=combobox]",
    })

    def control(path: str, label: str, kind: str, value: str) -> dict:
        return {
            "path": path,
            "key": path.rsplit(".", 1)[-1],
            "suggest_name": label,
            "name_source": "dom",
            "label": label,
            "value": value,
            "field_aliases": [path.rsplit(".", 1)[-1]],
            "control_kind": kind,
            "page_id": "seal-page",
            "frame_id": "main",
        }

    spec = to_flow_spec(
        captured,
        reads=[{"url": captured[3]["url"], "json": option_response, "role": "read_option"}],
        samples={
            "流程状态": "审批中",
            "公章": "公司章",
            "申请标题": "项目用章",
            "使用日期": "2026-07-20",
            "归还日期": "2026-07-21",
            "使用描述": "项目材料",
            "备注": "当天归还",
        },
        required_labels={"公章", "申请标题", "使用日期", "归还日期", "使用描述"},
        page_enum_options={
            "流程状态": {
                "field_key": "流程状态",
                "field_aliases": ["processStatus"],
                "control_kind": "select",
                "selected": "审批中",
                "selected_label": "审批中",
                "selected_value": "2",
                "mapping_complete": False,
                "options": ["未提交", "审批中", "审批通过", "审批不通过", "已取消"],
                "page_id": "seal-page",
                "frame_id": "main",
            },
        },
        field_evidence=[
            control("query.processStatus", "流程状态", "select", "审批中"),
            control("sealId", "公章", "select", "公司章"),
            control("applyTitle", "申请标题", "text", "项目用章"),
            control("useTime", "使用日期", "date", "2026-07-20"),
            control("backTime", "归还日期", "date", "2026-07-21"),
            control("useInfo", "使用描述", "textarea", "项目材料"),
            control("remark", "备注", "textarea", "当天归还"),
        ],
        page_events=[
            {"type": "control_open", "field_aliases": ["processStatus"], "page_id": "seal-page"},
            {"type": "control_select", "field_aliases": ["sealId"], "page_id": "seal-page"},
        ],
        recording_mode="browser",
    )
    query = _r0_step(spec, "/oa/seal-apply/page")
    definition = _r0_step(spec, "/process-definition/get")
    approval = _r0_step(spec, "/get-approval-detail")
    submit = _r0_step(spec, "/seal-apply/submit-process")
    return asyncio.run(orchestrate_flow_capabilities(
        spec,
        submission=_strict_submission(
            (
                "query_seal_applications", "查询用章申请", "query_status", query.step_id,
                [(query.step_id, "execute")],
            ),
            (
                "submit_seal_application", "提交用章申请", "submit", submit.step_id,
                [
                    (definition.step_id, "preflight"),
                    (approval.step_id, "preflight"),
                    (submit.step_id, "execute"),
                ],
            ),
        ),
    ))


def _r0_step(spec: FlowSpec, path_fragment: str) -> FlowStep:
    return next(step for step in spec.steps if path_fragment in (step.path or step.url))


def _r0_param(spec: FlowSpec, step_fragment: str, path: str) -> ParamField:
    step = _r0_step(spec, step_fragment)
    return next(param for param in step.params if param.path == path)


def test_r0_seal_truth_preserves_facts_capability_boundaries_and_relations(
    r0_seal_recording_truth_spec: FlowSpec,
):
    spec = r0_seal_recording_truth_spec
    assert len(spec.request_facts.requests) == 5
    assert len(spec.request_facts.page_events) == 2
    assert len(spec.steps) == 4
    assert not any("/bd/seal/simple-list" in step.path for step in spec.steps)

    capabilities = {cap.kind: cap for cap in spec.capabilities}
    assert set(capabilities) == {"query_status", "submit"}
    assert [
        _r0_step(spec, "/oa/seal-apply/page").step_id,
    ] == capabilities["query_status"].step_ids
    # The semantic plan may propose preflight membership, but executable call
    # nodes are derived only from machine-verified links. These unverified
    # reads remain materialized facts and do not become executable calls yet.
    assert capabilities["submit"].step_ids == [
        _r0_step(spec, "/seal-apply/submit-process").step_id,
    ]

    option_ref = next(
        ref for ref in capabilities["submit"].request_refs
        if "/bd/seal/simple-list" in ref.path
    )
    assert option_ref.usage == "option_source"
    assert option_ref.step_id == ""

    assert len(spec.links) == 1
    link = spec.links[0]
    assert link.source_step_id == _r0_step(spec, "/process-definition/get").step_id
    assert link.source_path == "data.id"
    assert link.target_step_id == _r0_step(spec, "/get-approval-detail").step_id
    assert link.target_path == "query.processDefinitionId"
    assert link.confirmed is True
    assert spec.capability_relations == []

    seal = _r0_param(spec, "/seal-apply/submit-process", "sealId")
    assert seal.source["source_request_id"] == "4"
    assert seal.source["value_key"] == "id"
    assert seal.source["label_key"] == "name"
    process_definition = _r0_param(spec, "/get-approval-detail", "query.processDefinitionId")
    assert process_definition.source["response_path"] == "data.id"
    assert process_definition.source["target_path"] == "query.processDefinitionId"


@pytest.mark.parametrize(
    (
        "step_fragment", "path", "name", "default_value", "business_type",
        "wire_type", "category", "source_kind", "required",
    ),
    [
        ("/oa/seal-apply/page", "query.pageNo", "pageNo", "1", "number", "number", "user_param", "page_context", False),
        ("/oa/seal-apply/page", "query.pageSize", "pageSize", "10", "number", "number", "user_param", "page_context", False),
        (
            "/oa/seal-apply/page", "query.processStatus", "流程状态", None,
            "enum", "string", "user_param", "page_enum", False,
        ),
        ("/process-definition/get", "query.key", "key", "oa_seal_apply", "string", "string", "system_const", "constant", False),
        ("/get-approval-detail", "query.processDefinitionId", "processDefinitionId", None, "string", "string", "runtime_var", "previous_response", False),
        ("/get-approval-detail", "query.activityId", "activityId", "StartUserNode", "string", "string", "system_const", "constant", False),
        ("/seal-apply/submit-process", "sealId", "公章", None, "enum", "string", "user_param", "api_option", True),
        ("/seal-apply/submit-process", "applyTitle", "申请标题", None, "string", "string", "user_param", "user_input", True),
        (
            "/seal-apply/submit-process", "useTime", "使用日期", None,
            "datetime", "number", "user_param", "user_input", True,
        ),
        (
            "/seal-apply/submit-process", "backTime", "归还日期", None,
            "datetime", "number", "user_param", "user_input", True,
        ),
        ("/seal-apply/submit-process", "useInfo", "使用描述", None, "string", "string", "user_param", "user_input", True),
        ("/seal-apply/submit-process", "billType", "billType", "oa_seal_apply", "string", "string", "system_const", "constant", False),
        ("/seal-apply/submit-process", "processDefKey", "processDefKey", "oa_seal_apply", "string", "string", "system_const", "constant", False),
        ("/seal-apply/submit-process", "remark", "备注", None, "string", "string", "user_param", "user_input", False),
    ],
)
def test_r0_seal_truth_resolves_each_field_axis_independently(
    r0_seal_recording_truth_spec: FlowSpec,
    step_fragment: str,
    path: str,
    name: str,
    default_value,
    business_type: str,
    wire_type: str,
    category: str,
    source_kind: str,
    required: bool,
):
    param = _r0_param(r0_seal_recording_truth_spec, step_fragment, path)
    assert {
        "path": param.path,
        "name": param.label or param.key,
        "default_value": param.default_value,
        "business_type": param.type,
        "wire_type": param.wire_type,
        "category": param.category,
        "source_kind": param.source_kind,
        "required": param.required,
    } == {
        "path": path,
        "name": name,
        "default_value": default_value,
        "business_type": business_type,
        "wire_type": wire_type,
        "category": category,
        "source_kind": source_kind,
        "required": required,
    }


@pytest.mark.parametrize(
    ("request_index", "expected_role"),
    [
        ("1", "business_get"),
        ("2", "read_context"),
        ("3", "read_context"),
        ("4", "read_option"),
        ("5", "business_write"),
    ],
)
def test_r0_seal_truth_classifies_each_interface_role(
    r0_seal_recording_truth_spec: FlowSpec,
    request_index: str,
    expected_role: str,
):
    analysis = r0_seal_recording_truth_spec.request_facts.analysis[request_index]
    assert analysis.role == expected_role


@pytest.mark.parametrize(
    ("capability_kind", "path_fragment", "expected_role", "expected_usage"),
    [
        ("query_status", "/oa/seal-apply/page", "business_get", "execute"),
        ("submit", "/seal-apply/submit-process", "business_write", "execute"),
        ("submit", "/bd/seal/simple-list", "read_option", "option_source"),
    ],
)
def test_r0_seal_truth_separates_interface_role_from_capability_usage(
    r0_seal_recording_truth_spec: FlowSpec,
    capability_kind: str,
    path_fragment: str,
    expected_role: str,
    expected_usage: str,
):
    capability = next(
        cap for cap in r0_seal_recording_truth_spec.capabilities
        if cap.kind == capability_kind
    )
    ref = next(ref for ref in capability.request_refs if path_fragment in ref.path)
    assert (ref.role, ref.usage) == (expected_role, expected_usage)


def test_same_command_transaction_keeps_auxiliary_json_interface_in_operation():
    transaction = "page-1|frame-1|action-cancel"
    auxiliary = _get(1, "/api/workflow/preflight", {"allowed": True})
    auxiliary.update({
        "resource_type": "xhr",
        "trigger_transaction_id": transaction,
        "trigger_action_id": "action-cancel",
        "trigger_op": "click",
        "causality_confidence": "high",
        "_request_role": {
            "role": "noise", "keep": False, "reason": "response arrived after initial classification",
            "confidence": 0.2,
        },
    })
    command = _post(2, "/api/application/cancel", {"id": "one"})
    command.update({
        "resource_type": "xhr",
        "trigger_transaction_id": transaction,
        "trigger_action_id": "action-cancel",
        "trigger_op": "click",
        "causality_confidence": "high",
        "_request_role": {
            "role": "business_write", "keep": True, "reason": "command request",
            "confidence": 0.99,
        },
    })

    spec = to_flow_spec([auxiliary, command])
    assert {step.path for step in spec.steps} == {
        "/api/workflow/preflight", "/api/application/cancel",
    }


def test_optimize_fills_placeholder_capability_title_and_intent_without_model_guess():
    spec = FlowSpec(
        title="酒店申请",
        steps=[FlowStep(
            step_id="cancel", method="DELETE", path="/api/application/cancel",
            source_meta={"role": "business_write"},
        )],
        capabilities=[FlowCapability(
            name="capability_2", title="能力 2", intent="", kind="submit",
            nodes=[{"id": "call_cancel", "type": "call", "step_id": "cancel"}],
        )],
        meta={"capability_model": {"status": "ready"}},
    )

    optimized = asyncio.run(orchestrate_flow_capabilities(spec, submission={"ops": []}))
    capability = next(cap for cap in optimized.capabilities if cap.name == "capability_2")
    assert capability.title == "取消酒店申请"
    assert "取消酒店申请" in capability.intent
    assert "真实接口" not in capability.intent
    assert capability.step_ids == ["cancel"]


def test_capability_explanation_matching_never_compares_tied_plan_dicts():
    spec = FlowSpec(
        title="Seal application",
        steps=[FlowStep(
            step_id="definition",
            method="GET",
            path="/api/process-definition/get",
            source_meta={"role": "business_get"},
        )],
        capabilities=[FlowCapability(
            name="legacy_query",
            title="Capability 1",
            intent="",
            kind="query_status",
            nodes=[{
                "id": "call_definition",
                "type": "call",
                "step_id": "definition",
            }],
        )],
    )
    semantic_plan = {
        "capabilities": [
            {
                "name": "load_definition_a",
                "title": "Load definition A",
                "kind": "query_status",
                "step_ids": ["definition"],
                "request_refs": [{
                    "step_id": "definition",
                    "usage": "execute",
                }],
            },
            {
                "name": "load_definition_b",
                "title": "Load definition B",
                "kind": "query_status",
                "step_ids": ["definition"],
                "request_refs": [{
                    "step_id": "definition",
                    "usage": "execute",
                }],
            },
        ],
    }

    optimized = flow_spec_module._ensure_capability_explanations(
        spec, semantic_plan,
    )

    assert optimized.capabilities[0].title not in {
        "Load definition A", "Load definition B",
    }
    assert optimized.capabilities[0].intent


def test_capability_nodes_expand_stale_step_ids_and_derive_all_three_step_views():
    definition = FlowStep(
        step_id="definition",
        method="GET",
        url="/process/definition",
        path="/process/definition",
        params=[ParamField(path="query.key", key="流程类型", value="leave", category="system_const")],
        response_json={"data": {"id": "PROC-001"}},
    )
    detail = FlowStep(
        step_id="detail",
        method="GET",
        url="/process/detail",
        path="/process/detail",
        params=[ParamField(
            path="query.processId",
            key="流程定义ID",
            value="PROC-001",
            category="runtime_var",
            source_kind="previous_response",
        )],
        response_json={"data": {"approverId": "USER-009"}},
    )
    submit = FlowStep(
        step_id="submit",
        method="POST",
        url="/leave/submit",
        path="/leave/submit",
        params=[
            ParamField(
                path="approverId",
                key="审批人",
                value="USER-009",
                category="runtime_var",
                source_kind="previous_response",
            ),
            ParamField(path="reason", key="原因", value="年假", category="user_param", required=True),
        ],
    )
    spec = FlowSpec(
        flow_id="three-call-capability",
        steps=[definition, detail, submit],
        links=[
            FlowLink(
                source_step_id="definition",
                source_path="data.id",
                target_step_id="detail",
                target_path="query.processId",
                confirmed=True,
            ),
            FlowLink(
                source_step_id="detail",
                source_path="data.approverId",
                target_step_id="submit",
                target_path="approverId",
                confirmed=True,
            ),
        ],
        capabilities=[FlowCapability(
            name="submit_leave",
            kind="submit",
            nodes=[
                {"id": "call_definition", "type": "call", "step_id": "definition"},
                {"id": "call_detail", "type": "call", "step_id": "detail"},
                {"id": "call_submit", "type": "call", "step_id": "submit"},
                {"id": "return_submit", "type": "return", "from": "submit", "path": "response"},
            ],
        )],
    )

    flow_spec_module._normalize_capability_references(spec)
    synced = flow_spec_module._sync_capability_io_schemas(spec)
    cap = synced.capabilities[0]

    assert cap.step_ids == ["definition", "detail", "submit"]
    assert {field.step_id for field in cap.request_fields} == {"definition", "detail", "submit"}
    assert {
        (dep.source.get("step_id"), dep.target.get("step_id"))
        for dep in cap.dependencies
    } == {("definition", "detail"), ("detail", "submit")}
    client_cap = flow_spec_to_client(synced)["capabilities"][0]
    assert client_cap["step_ids"] == ["definition", "detail", "submit"]
    assert set(client_cap["input_schema"]["properties"]) == {"原因"}


def test_remove_capability_step_recursively_clears_nested_condition_map_and_loop_calls():
    spec = FlowSpec(
        flow_id="nested-remove",
        steps=[
            FlowStep(step_id="keep", method="GET", url="/keep", path="/keep"),
            FlowStep(step_id="remove", method="POST", url="/remove", path="/remove"),
        ],
        capabilities=[FlowCapability(
            name="nested",
            kind="submit",
            nodes=[{
                "id": "condition_1",
                "type": "condition",
                "then": [{"id": "call_remove_1", "type": "call", "step_id": "remove"}],
                "else": [{
                    "id": "map_1",
                    "type": "map",
                    "children": [{
                        "id": "loop_1",
                        "type": "loop",
                        "steps": [
                            {"id": "call_keep", "type": "call", "step_id": "keep"},
                            {"id": "call_remove_2", "type": "call", "step_id": "remove"},
                        ],
                    }],
                }],
            }],
        )],
    )

    edited = apply_flow_edits(spec, [{
        "op": "remove_capability_step",
        "capability_name": "nested",
        "step_id": "remove",
    }])
    cap = edited.capabilities[0]
    call_ids = [node.get("step_id") for node in _walk_nodes(cap.nodes) if node.get("type") == "call"]

    assert cap.step_ids == ["keep"]
    assert call_ids == ["keep"]
    assert any(step.step_id == "remove" for step in edited.steps)


def test_stale_capability_fields_are_removed_from_validation_and_input_schema():
    stale = CapabilityField(
        field_id="stale-field",
        scope="input",
        display_name="已删除字段",
        key="stale",
        path="missing.path",
        step_id="submit",
        source_kind="user_input",
        exposed_to_caller=True,
        locked=True,
        confirmed=True,
    )
    spec = FlowSpec(
        flow_id="stale-field-prune",
        steps=[FlowStep(
            step_id="submit",
            method="POST",
            url="/leave/submit",
            path="/leave/submit",
            params=[ParamField(
                path="reason",
                key="原因",
                value="年假",
                category="user_param",
                source_kind="user_input",
                required=True,
            )],
            success_rule={"kind": "http_status", "values": [200]},
        )],
        capabilities=[FlowCapability(
            name="submit_leave",
            title="提交请假",
            kind="submit",
            nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
            fields=[stale],
            inputs=[stale],
            input_schema={
                "type": "object",
                "properties": {"stale": {"type": "string"}},
                "required": ["stale"],
            },
            confirmed=True,
            requires_human_confirm=False,
            status="confirmed",
        )],
    )

    edited = apply_flow_edits(spec, [{
        "op": "update_capability",
        "capability_name": "submit_leave",
        "field": "title",
        "value": "提交请假",
    }])
    cap = edited.capabilities[0]
    report = validate_flow_spec(edited)

    assert {field.path for field in cap.request_fields} == {"reason"}
    assert "fields" not in cap.model_dump()
    assert set(cap.input_schema["properties"]) == {"原因"}
    assert cap.input_schema["required"] == ["原因"]
    assert all("missing.path" not in message and "stale" not in message for message in report["errors"])


def test_to_flow_spec_materializes_high_confidence_business_query_and_dependency_closure():
    captured = [
        {
            **_get(1, "/daily-report/page?window=current", {"data": {"list": [{"date": "2026-05-01"}]}}),
            "query": {"window": "current"},
            "trigger_op": "control_open",
            "trigger_locator": "label=Report number",
            "trigger_transaction_id": "txn-business-query",
            "causality_confidence": "high",
            "_request_role": {
                "role": "business_get", "keep": True,
                "confidence": 0.94, "reason": "user query with business filters",
            },
        },
        _get(2, "/process/definition/get?key=daily", {"data": {"id": "PROC-UNIQUE-001"}}),
        _post(
            3,
            "/daily-report/submit",
            {"processId": "PROC-UNIQUE-001", "content": "完成回归测试"},
        ),
    ]
    captured[2].update({
        "trigger_op": "submit",
        "trigger_transaction_id": "txn-business-submit",
        "causality_confidence": "high",
    })

    spec = to_flow_spec(captured, samples={"content": "完成回归测试"})
    process_param = next(
        param for step in spec.steps if "/daily-report/submit" in step.path
        for param in step.params if param.path == "processId"
    )
    process_param.category = "runtime_var"
    process_param.source_kind = "previous_response"

    assert [step.method for step in spec.steps] == ["GET", "GET", "POST"]
    assert [step.path.split("?", 1)[0] for step in spec.steps] == [
        "/daily-report/page",
        "/process/definition/get",
        "/daily-report/submit",
    ]
    assert len(spec.request_facts.requests) == 3
    independent = next(fact for fact in spec.request_facts.requests if "/daily-report/page" in fact.path)
    assert independent.request_id in {
        (step.source_meta or {}).get("request_id") for step in spec.steps
    }
    assert spec.request_facts.usage[independent.request_id].state == "materialized"

    query_step = next(step for step in spec.steps if "/daily-report/page" in step.path)
    definition_step = next(step for step in spec.steps if "/process/definition/get" in step.path)
    submit_step = next(step for step in spec.steps if "/daily-report/submit" in step.path)
    orchestrated = asyncio.run(orchestrate_flow_capabilities(
        spec,
        submission=_strict_submission(
            (
                "query_daily_reports", "查询日报", "query_status", query_step.step_id,
                [(query_step.step_id, "execute")],
            ),
            (
                "submit_daily_report", "提交日报", "submit", submit_step.step_id,
                [(definition_step.step_id, "preflight"), (submit_step.step_id, "execute")],
            ),
        ),
    ))
    by_kind = {cap.kind: cap for cap in orchestrated.capabilities}
    assert set(by_kind) == {"query_status", "submit"}
    assert [orchestrated.steps[[s.step_id for s in orchestrated.steps].index(sid)].path.split("?", 1)[0]
            for sid in by_kind["query_status"].step_ids] == ["/daily-report/page"]
    assert [orchestrated.steps[[s.step_id for s in orchestrated.steps].index(sid)].path.split("?", 1)[0]
            for sid in by_kind["submit"].step_ids] == ["/daily-report/submit"]


def test_to_flow_spec_keeps_direct_user_triggered_business_read_even_if_role_is_context():
    detail = {
        **_get(1, "/requests/42", {"data": {"id": 42, "status": "pending"}}),
        "trigger_op": "click",
        "trigger_locator": "text=查看进度",
        "trigger_transaction_id": "txn-view-progress",
        "causality_confidence": "high",
        "_request_role": {
            "role": "read_context",
            "keep": True,
            "confidence": 0.91,
            "reason": "用户点击查看进度后读取业务记录",
        },
    }

    spec = to_flow_spec([detail])

    assert len(spec.steps) == 1
    assert spec.steps[0].path == "/requests/42"
    assert spec.steps[0].source_meta["role"] == "business_get"


def test_unique_real_value_dependency_is_confirmed_but_ambiguous_value_is_not():
    unique = to_flow_spec([
        _get(1, "/process/definition/get", {"data": {"taskId": "TASK-UNIQUE-001"}}),
        _post(2, "/leave/submit", {"taskId": "TASK-UNIQUE-001", "reason": "年假"}),
    ], samples={"reason": "年假"})

    assert len(unique.links) == 1
    assert unique.links[0].confirmed is True
    assert unique.links[0].confidence == 0.96

    ambiguous = to_flow_spec([
        _get(1, "/process/definition/get", {"data": {"taskId": "TASK-SHARED-001"}}),
        _get(2, "/process/instance/detail", {"data": {"taskId": "TASK-SHARED-001"}}),
        _post(3, "/leave/submit", {"taskId": "TASK-SHARED-001", "reason": "年假"}),
    ], samples={"reason": "年假"})

    # 同一个值来自多个上游响应时来源不唯一，不能生成随机候选依赖。
    assert ambiguous.links == []
    assert all(link.confidence == 0.85 for link in ambiguous.links)


def test_seal_application_keeps_control_preflights_and_maps_long_id_enum():
    seal_id = "f13a450364df1b8a269365f90f44aee0"
    process_id = "oa_seal_apply:1:aa840521"
    option_response = {"data": [
        {"id": seal_id, "name": "行政公章"},
        {"id": "d8896f988f51434ea6cdb1a48d71ee99", "name": "合同章"},
    ]}
    captured = [
        _get(1, "/system/seal/simple-list", option_response),
        _get(2, "/bpm/process-definition/get?key=oa_seal_apply", {"data": {"id": process_id}}),
        _get(
            3,
            "/bpm/approval-detail?processDefinitionId=oa_seal_apply%3A1%3Aaa840521&activityId=StartUserNode",
            {"data": {"node": "StartUserNode"}},
        ),
        _post(4, "/seal-apply/submit-process", {
            "sealId": seal_id,
            "applyTitle": "出差用章申请",
            "billType": "oa_seal_apply",
            "processDefKey": "oa_seal_apply",
        }),
    ]

    spec = to_flow_spec(
        captured,
        reads=[{"url": captured[0]["url"], "json": option_response, "role": "read_option"}],
        samples={"印章": "行政公章", "申请标题": "出差用章申请"},
        field_evidence=[{
            "path": "sealId",
            "key": "sealId",
            "suggest_name": "印章",
            "name_source": "dom",
            "label": "印章",
            "value": "行政公章",
            "field_aliases": ["sealId"],
            "control_kind": "select",
            "page_id": "seal-form",
            "frame_id": "main",
        }],
    )

    assert [step.method for step in spec.steps] == ["GET", "GET", "POST"]
    assert all((step.source_meta or {}).get("control_preflight_for_write") for step in spec.steps[:2])
    assert len(spec.links) == 1
    assert spec.links[0].target_path == "query.processDefinitionId"
    assert spec.links[0].confirmed is True
    submit = spec.steps[-1]
    seal = next(param for param in submit.params if param.path == "sealId")
    assert seal.key == "印章"
    assert seal.type == "enum"
    assert seal.category == "user_param"
    assert seal.source_kind == "api_option"
    assert seal.enum_value_map == {
        "行政公章": seal_id,
        "合同章": "d8896f988f51434ea6cdb1a48d71ee99",
    }
    assert seal.need_human_confirm is False
    assert all(
        not param.need_human_confirm
        for step in spec.steps
        for param in step.params
        if param.path in {"query.key", "query.processDefinitionId", "query.activityId", "billType", "processDefKey"}
    )

    submit_anchor = spec.steps[-1]
    orchestrated = asyncio.run(orchestrate_flow_capabilities(
        spec,
        submission=_strict_submission((
            "submit_seal_application", "提交用章申请", "submit", submit_anchor.step_id,
            [
                *((step.step_id, "preflight") for step in spec.steps[:-1]),
                (submit_anchor.step_id, "execute"),
            ],
        )),
    ))
    submit_cap = next(cap for cap in orchestrated.capabilities if cap.kind == "submit")
    assert submit_cap.step_ids == [submit_anchor.step_id]
    assert not any(cap.kind == "query_status" for cap in orchestrated.capabilities)

    validate_flow_spec(orchestrated)
    assert not any("前置接口保留" in item.title for item in orchestrated.review_items if not item.resolved)


def test_query_result_names_do_not_invent_batch_for_single_row_submit():
    query = FlowStep(
        step_id="query_missing", method="GET", path="/daily/page",
        source_meta={"role": "read_context", "confidence": 0.96},
        response_json={"data": {"filled_dates": ["2026-05-01"], "missing_dates": ["2026-05-11"]}},
    )
    submit = FlowStep(
        step_id="submit_one", method="POST", path="/daily/submit", url="/daily/submit",
        body_source='{"date":"2026-05-11","content":"开发"}',
        source_meta={"role": "submit_anchor"},
        params=[
            ParamField(path="date", key="日报日期", type="date", category="user_param", source_kind="user_input"),
            ParamField(path="content", key="工作内容", category="user_param", source_kind="user_input"),
        ],
    )

    out = asyncio.run(orchestrate_flow_capabilities(
        FlowSpec(steps=[query, submit]),
        submission=_strict_submission((
            "submit_daily_report", "提交日报", "submit", "submit_one",
            [("submit_one", "execute")],
        )),
    ))
    submit_cap = next(cap for cap in out.capabilities if cap.kind == "submit")

    assert "entries" not in submit_cap.input_schema.get("properties", {})
    assert not any(node.get("type") == "foreach" for node in submit_cap.nodes)
    assert out.capability_relations == []


def test_query_required_and_text_wire_type_follow_observed_controls_not_sample_shape():
    params = flow_spec_module._params_from_get_query(
        {
            "method": "GET",
            "url": (
                "https://oa.example.test/hotel/page?hotelName=1&street=1"
                "&pageNo=1&pageSize=10"
            ),
        },
        field_evidence=[
            {
                "label": "酒店名称",
                "field_aliases": ["hotelName"],
                "control_kind": "text",
            },
            {
                "label": "所在街道",
                "field_aliases": ["street"],
                "control_kind": "text",
            },
        ],
        required_labels={"酒店名称"},
    )
    by_path = {param["path"]: param for param in params}

    assert by_path["query.hotelName"]["required"] is True
    assert by_path["query.street"]["required"] is False
    assert by_path["query.hotelName"]["type"] == "string"
    assert by_path["query.hotelName"]["wire_type"] == "string"
    assert by_path["query.pageNo"]["required"] is False


def test_schema_defaults_are_type_safe_and_only_pagination_is_silently_applicable():
    schema = flow_spec_module._capability_input_schema([
        ParamField(
            path="query.pageNo", key="pageNo", value="1", type="integer",
            category="user_param", source_kind="user_input", required=False,
            exposed_to_user=True,
        ),
        ParamField(
            path="id", key="id", value="H-100", type="string",
            category="user_param", source_kind="user_input", required=True,
            exposed_to_user=True,
        ),
        ParamField(
            path="confirmed", key="confirmed", value="false", type="boolean",
            category="user_param", source_kind="user_input", required=False,
            exposed_to_user=True,
        ),
        ParamField(
            path="roomType", key="roomType", value="2", type="enum",
            category="user_param", source_kind="page_enum", required=False,
            exposed_to_user=True, enum_options=["标准间", "大床房"],
            enum_value_map={"标准间": 1, "大床房": 2},
        ),
        ParamField(
            path="unknownCode", key="unknownCode", value="9", type="enum",
            category="user_param", source_kind="user_input", required=False,
            exposed_to_user=True,
        ),
    ])
    props = schema["properties"]

    assert props["pageNo"]["default"] == 1
    assert props["pageNo"]["x-dano-apply-default"] is True
    assert "default" not in props["id"]
    assert "x-dano-apply-default" not in props["id"]
    assert "default" not in props["confirmed"]
    assert "default" not in props["roomType"]
    assert "default" not in props["unknownCode"]


def test_richer_observed_query_response_defines_record_item_schema_and_id():
    empty_response = {"code": 0, "data": {"list": [], "total": 0}}
    populated_response = {
        "code": 0,
        "data": {"list": [{"id": "H-1", "hotelName": "海景酒店"}], "total": 1},
    }
    query = FlowStep(
        step_id="query", method="GET",
        url="/hotel/page?pageNo=1&pageSize=10", path="/hotel/page",
        source_meta={"request_id": "query-empty", "role": "business_get"},
        response_json=empty_response,
    )
    spec = FlowSpec(
        steps=[query],
        request_facts=flow_spec_module.RequestFacts(requests=[
            flow_spec_module.RequestFact(
                request_id="query-empty", method="GET", path="/hotel/page",
                url="/hotel/page?pageNo=1&pageSize=10", response_json=empty_response,
            ),
            flow_spec_module.RequestFact(
                request_id="query-populated", method="GET", path="/hotel/page",
                url="/hotel/page?pageNo=1&pageSize=10&hotelName=%E6%B5%B7%E6%99%AF",
                response_json=populated_response,
            ),
        ]),
    )

    out = asyncio.run(orchestrate_flow_capabilities(
        spec,
        submission=_strict_submission((
            "query_hotels", "查询酒店", "query_status", "query",
            [("query", "execute")],
        )),
    ))
    records = out.capabilities[0].output_schema["properties"]["records"]

    assert records["items"]["properties"]["id"]["type"] == "string"
    assert records["items"]["properties"]["hotelName"]["type"] == "string"
    assert out.steps[0].source_meta["response_shape_enriched"] is True


def test_enum_binding_without_real_label_value_contract_is_not_executable_or_guessed():
    param = ParamField(
        path="query.processStatus", key="流程状态", value="1", type="enum",
        wire_type="string", category="user_param", source_kind="api_option",
        exposed_to_user=True,
    )
    step = FlowStep(
        step_id="query", method="GET", path="/hotel/page",
        params=[param],
        selects=[SelectBinding(
            param="流程状态", path="query.processStatus", enum_source="api",
            source_url="/dict/process-status",
        )],
    )

    sync_flow_spec_models(FlowSpec(steps=[step]))

    # 保留未确认来源仅供诊断/后续人工补齐，但不得把它当成可执行枚举合同。
    assert len(step.selects) == 1
    assert step.selects[0].enum_confirmed is False
    assert step.selects[0].options is None
    assert step.selects[0].option_map is None
    assert param.type == "string"
    assert param.source_kind == "user_input"
    assert param.enum_options is None
    assert param.enum_value_map is None


def test_partial_page_enum_cannot_execute_only_because_current_label_is_recorded():
    """A partial snapshot must map every exposed label, including non-current choices."""
    param = ParamField(
        path="requestType",
        key="申请类型",
        value="病假",
        type="enum",
        wire_type="string",
        category="user_param",
        source_kind="page_enum",
        exposed_to_user=True,
        enum_options=["病假", "事假"],
        enum_value_map={"病假": "2"},
        source={"mapping_complete": False},
    )

    assert flow_spec_module._incomplete_page_enum_is_executable(param) is False


def test_partial_page_enum_is_executable_when_every_label_has_an_explicit_value():
    param = ParamField(
        path="requestType",
        key="申请类型",
        value="2",
        type="enum",
        wire_type="string",
        category="user_param",
        source_kind="page_enum",
        exposed_to_user=True,
        enum_options=["病假", "事假"],
        enum_value_map={"病假": "2", "事假": "3"},
        source={"mapping_complete": False},
    )

    assert flow_spec_module._incomplete_page_enum_is_executable(param) is True


def test_unrelated_same_value_list_is_not_bound_as_option_source():
    """A matching recorded value is not causal evidence for an option endpoint."""
    source = FlowStep(
        step_id="permissions",
        method="GET",
        path="/api/permissions/simple-list",
        response_json={"data": [{"id": "1", "name": "管理员"}, {"id": "2", "name": "访客"}]},
        source_meta={
            "role": "read_option",
            "trigger_transaction_id": "txn-permissions",
            "trigger_action_id": "open-permissions",
        },
    )
    target = FlowStep(
        step_id="submit",
        method="POST",
        path="/api/request/submit",
        params=[ParamField(
            path="requestType",
            key="申请类型",
            value="1",
            category="user_param",
            source_kind="user_input",
            evidence=[{"kind": "page_control", "control_kind": "select"}],
        )],
        source_meta={
            "trigger_transaction_id": "txn-submit-request",
            "trigger_action_id": "submit-request",
        },
    )
    spec = FlowSpec(steps=[source, target])

    repaired = flow_spec_module._repair_structural_option_bindings(spec)

    assert repaired == 0
    assert target.params[0].source_kind != "api_option"
    assert not any(binding.path == "requestType" for step in spec.steps for binding in step.selects)


def test_same_transaction_without_field_contract_is_not_option_source_evidence():
    source = FlowStep(
        step_id="permissions",
        method="GET",
        path="/api/permissions/simple-list",
        response_json={"data": [{"id": "1", "name": "管理员"}, {"id": "2", "name": "访客"}]},
        source_meta={
            "role": "read_option",
            "trigger_transaction_id": "txn-request-form",
            "trigger_action_id": "submit-request",
        },
    )
    target = FlowStep(
        step_id="submit",
        method="POST",
        path="/api/request/submit",
        params=[ParamField(
            path="requestType",
            key="申请类型",
            value="1",
            category="user_param",
            source_kind="user_input",
            evidence=[{"kind": "page_control", "control_kind": "select"}],
        )],
        source_meta={
            "trigger_transaction_id": "txn-request-form",
            "trigger_action_id": "submit-request",
        },
    )
    spec = FlowSpec(steps=[source, target])

    repaired = flow_spec_module._repair_structural_option_bindings(spec)

    assert repaired == 0
    assert target.params[0].source_kind == "user_input"
    assert target.params[0].enum_value_map is None


def test_structurally_generic_list_is_not_promoted_without_business_evidence():
    request = _get(
        1,
        "/api/common/list",
        {"data": [
            {"nodeKey": "n1", "caption": "Inbox", "route": "/inbox", "icon": "mail"},
            {"nodeKey": "n2", "caption": "Tasks", "route": "/tasks", "icon": "check"},
        ]},
    )
    request.update({"resource_type": "xhr"})

    classification = flow_spec_module.classify_network_request(request)

    assert classification["role"] != "business_get"
    assert classification["keep"] is False


def test_multipart_text_fields_remain_supported_request_data():
    request = {
        "method": "POST",
        "url": "https://oa.example.test/api/request/submit",
        "content_type": "multipart/form-data; boundary=abc",
        "post_data": (
            "--abc\r\nContent-Disposition: form-data; name=\"reason\"\r\n\r\n"
            "annual leave\r\n--abc--\r\n"
        ),
        "response_status": 200,
        "response_json": {"ok": True},
    }

    classification = flow_spec_module.classify_network_request(request)

    assert classification["role"] == "business_write"
    assert classification["keep"] is True
    assert classification.get("unsupported") is not True


def test_recorder_classification_uses_full_action_and_transaction_context():
    entry = {
        "method": "POST",
        "url": "https://crm.example.test/api/op/query",
        "post_data": json.dumps({"region": "east"}),
        "response_json": {"payload": {"rows": [{"record": "r1", "amount": 4}]}},
        "trigger_op": "click",
        "trigger_locator": "button[type=submit]",
        "trigger_action_id": "search-orders",
        "trigger_transaction_id": "page|main|search-orders",
        "page_id": "orders",
        "frame_id": "main",
    }
    session = RecordSession()

    session._classify_entry(entry)

    assert entry["role"] == "business_get"
    assert entry["keep"] is True


def test_fact_check_request_ref_survives_capability_view_sync():
    spec = FlowSpec(
        steps=[
            FlowStep(step_id="submit", method="POST", path="/api/submit"),
            FlowStep(step_id="verify", method="GET", path="/api/verify"),
            FlowStep(step_id="prepare", method="GET", path="/api/prepare"),
        ],
        capabilities=[FlowCapability(
            name="submit_order",
            kind="submit",
            nodes=_call_nodes(["submit"]),
            request_refs=[CapabilityRequestRef(
                step_id="verify",
                method="GET",
                path="/api/verify",
                usage="fact_check",
                origin="planner",
            ), CapabilityRequestRef(
                step_id="prepare",
                method="GET",
                path="/api/prepare",
                usage="preflight",
                origin="planner",
            )],
        )],
    )

    synced = sync_flow_spec_models(spec)

    assert [(ref.step_id, ref.usage) for ref in synced.capabilities[0].request_refs] == [
        ("submit", "execute"),
        ("verify", "fact_check"),
        ("prepare", "preflight"),
    ]


def test_write_operation_identity_prefers_transaction_over_locator():
    def write(step_id: str, locator: str) -> FlowStep:
        return FlowStep(
            step_id=step_id,
            method="POST",
            path=f"/api/{step_id}",
            source_meta={
                "trigger_transaction_id": "txn-one",
                "trigger_action_id": "action-one",
                "trigger_op": "click",
                "trigger_locator": locator,
                "page_id": "page-one",
                "frame_id": "main",
                "causality_confidence": "high",
            },
        )

    assert flow_spec_module._write_operation_key(write("audit", "button.audit")) == (
        flow_spec_module._write_operation_key(write("save", "button.save"))
    )


def test_value_equality_without_causal_evidence_does_not_keep_flow_link():
    source = FlowStep(
        step_id="source",
        method="GET",
        path="/api/source",
        response_json={"data": {"title": "UNIQUE-LONG-TITLE"}},
        source_meta={"sequence": 1},
    )
    target = FlowStep(
        step_id="target",
        method="POST",
        path="/api/target",
        params=[ParamField(
            path="title",
            key="title",
            value="UNIQUE-LONG-TITLE",
            category="user_param",
            source_kind="user_input",
            evidence=[{"source": "page_control", "kind": "text"}],
        )],
        source_meta={"sequence": 2},
    )
    links = [FlowLink(
        link_id="accidental-value",
        source_step_id="source",
        source_path="data.title",
        target_step_id="target",
        target_path="title",
        confirmed=True,
        confidence=0.97,
        evidence={"kind": "value_match"},
    )]

    flow_spec_module._prune_unsafe_auto_links([source, target], links)

    assert links == []


def test_value_match_across_independent_actions_restores_delete_id_to_user_input():
    source = FlowStep(
        step_id="submit",
        method="POST",
        path="/hotel/submit",
        response_json={"data": "HOTEL-1"},
        source_meta={
            "sequence": 1,
            "trigger_action_id": "action_submit",
            "trigger_transaction_id": "transaction_submit",
        },
    )
    target_param = ParamField(
        path="query.id",
        key="id",
        value="HOTEL-1",
        category="runtime_var",
        source_kind="previous_response",
        source={"link_id": "wrong-cross-action-link"},
        exposed_to_user=False,
    )
    target = FlowStep(
        step_id="delete",
        method="DELETE",
        path="/hotel/delete?id=HOTEL-1",
        params=[target_param],
        source_meta={
            "sequence": 2,
            "trigger_action_id": "action_delete",
            "trigger_transaction_id": "transaction_delete",
        },
    )
    links = [FlowLink(
        link_id="wrong-cross-action-link",
        source_step_id="submit",
        source_path="data",
        target_step_id="delete",
        target_path="query.id",
        confirmed=True,
        confidence=0.98,
        evidence={
            "kind": "value_match",
            "source_action_id": "action_submit",
            "target_action_id": "action_delete",
        },
    )]

    flow_spec_module._sync_link_sources([source, target], links)

    assert links == []
    assert target_param.category == "user_param"
    assert target_param.source_kind == "user_input"
    assert target_param.exposed_to_user is True


def test_generic_relation_builder_does_not_create_named_field_special_case():
    spec = FlowSpec(capabilities=[
        FlowCapability(
            name="query_status",
            kind="query_status",
            output_schema={"type": "object", "properties": {
                "missing_dates": {"type": "array", "items": {"type": "string"}},
            }},
        ),
        FlowCapability(
            name="submit_batch",
            kind="submit_batch",
            input_schema={"type": "object", "properties": {
                "entries": {"type": "array", "items": {"type": "object"}},
            }},
        ),
    ])

    result = flow_spec_module._ensure_external_transform_relations(spec)

    assert result.capability_relations == []


def test_execution_fingerprint_ignores_descriptive_copy():
    original = FlowSpec(
        flow_id="fingerprint",
        title="Original title",
        business_description="Original description",
        goal={"intent": "Original goal"},
        steps=[FlowStep(
            step_id="submit", name="Original step", method="POST", path="/api/submit",
            params=[ParamField(path="reason", key="reason", description="Original field")],
        )],
        capabilities=[FlowCapability(
            name="submit", title="Original capability", intent="Original intent",
            kind="submit", step_ids=["submit"], nodes=[{"id": "call", "type": "call", "step_id": "submit"}],
        )],
    )
    renamed = original.model_copy(deep=True)
    renamed.title = "Localized title"
    renamed.business_description = "Localized description"
    renamed.goal["intent"] = "Localized goal"
    renamed.steps[0].name = "Localized step"
    renamed.steps[0].params[0].description = "Localized field"
    renamed.capabilities[0].title = "Localized capability"
    renamed.capabilities[0].intent = "Localized intent"

    assert flow_spec_module.flow_spec_fingerprint(original) == flow_spec_module.flow_spec_fingerprint(renamed)

    changed = original.model_copy(deep=True)
    changed.steps[0].params[0].required = False
    assert flow_spec_module.flow_spec_fingerprint(original) != flow_spec_module.flow_spec_fingerprint(changed)


def test_query_outputs_are_projected_from_arbitrary_response_fields():
    step = FlowStep(
        step_id="query", method="GET", path="/custom/search",
        source_meta={"role": "business_get"},
        response_json={"result": {"widgets": [{"id": 1}], "cursor": "next"}},
    )

    mappings = flow_spec_module._query_output_mappings([step])

    assert [(item["name"], item["response_path"]) for item in mappings] == [
        ("widgets", "result.widgets"), ("cursor", "result.cursor"),
    ]


def test_screenshot_without_positive_required_marker_cannot_downgrade_required():
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit",
        method="POST",
        path="/api/submit",
        params=[ParamField(path="reason", key="原因", value="leave", required=True)],
    )])

    flow_spec_module._apply_capability_field_to_param(
        spec,
        {
            "step_id": "submit",
            "wire_path": "reason",
            "key": "原因",
            "required": False,
            "evidence": [{
                "source": "screenshot",
                "screenshot_name": "form.png",
                "control_kind": "input",
                "editable": True,
                "required": False,
            }],
        },
        scope="input",
        actor="planner",
    )

    assert spec.steps[0].params[0].required is True


def test_screenshot_value_cannot_be_promoted_to_default_value():
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit",
        method="POST",
        path="/api/submit",
        params=[ParamField(
            path="days",
            key="天数",
            value="2",
            default_value=None,
            type="number",
        )],
    )])

    flow_spec_module._apply_capability_field_to_param(
        spec,
        {
            "step_id": "submit",
            "wire_path": "days",
            "key": "天数",
            "visible_default": "2",
            "evidence": [{
                "source": "screenshot",
                "screenshot_name": "form.png",
                "control_kind": "input",
                "editable": True,
                "visible_value": "2",
            }],
        },
        scope="input",
        actor="planner",
    )

    assert spec.steps[0].params[0].default_value is None


def test_query_output_fields_use_mapped_response_schema_types():
    query = FlowStep(
        step_id="query",
        method="GET",
        path="/daily/page",
        source_meta={"role": "business_get"},
        response_json={"data": {"missing_dates": ["2026-05-11"], "total": 1}},
    )

    out = asyncio.run(orchestrate_flow_capabilities(
        FlowSpec(steps=[query]),
        submission=_strict_submission((
            "query_daily_reports", "查询日报", "query_status", "query",
            [("query", "execute")],
        )),
    ))
    cap = out.capabilities[0]
    fields = {field.key: field.type for field in cap.outputs}

    assert fields["missing_dates"] == "array"
    assert fields["total"] == "number"
    assert set(cap.output_schema["required"]) == {"missing_dates", "total"}
    assert all(field.required for field in cap.outputs)


def test_planner_batch_kind_requires_recorded_or_operator_batch_evidence():
    query = FlowStep(
        step_id="query_missing",
        method="GET",
        path="/daily/page",
        source_meta={"role": "business_get"},
        response_json={"data": {"missing_dates": ["2026-05-11"]}},
    )
    submit = FlowStep(
        step_id="submit_one",
        method="POST",
        path="/daily/submit",
        body_source='{"date":"2026-05-11","content":"开发"}',
        params=[
            ParamField(path="date", key="日报日期", type="date", source_kind="user_input"),
            ParamField(path="content", key="工作内容", source_kind="user_input"),
        ],
    )
    spec = FlowSpec(
        steps=[query, submit],
        capabilities=[FlowCapability(
            name="submit_batch",
            kind="submit_batch",
            nodes=_call_nodes(["submit_one"]),
            evidence=[{"kind": "planner"}],
        )],
    )

    repaired = flow_spec_module._repair_generated_capability_contracts(spec)

    assert repaired.capabilities[0].kind == "submit"
    assert repaired.capabilities[0].name == "submit"


def test_external_transform_relation_prunes_only_stale_derived_mapping():
    query = FlowCapability(
        name="query_status",
        kind="query_status",
        output_schema={"type": "object", "properties": {"records": {"type": "array"}}},
    )
    submit = FlowCapability(
        name="submit_batch",
        kind="submit_batch",
        input_schema={"type": "object", "properties": {"entries": {"type": "array"}}},
    )
    stale = CapabilityRelation(
        relation_id="stale",
        from_capability="query_status",
        from_output="missing_dates",
        to_capability="submit_batch",
        to_input="entries",
        evidence={"kind": "typed_capability_contract"},
    )
    manual = stale.model_copy(deep=True)
    manual.relation_id = "manual"
    manual.evidence = {"kind": "user_confirmed"}
    spec = FlowSpec(capabilities=[query, submit], capability_relations=[stale, manual])

    flow_spec_module._ensure_external_transform_relations(spec)

    assert [relation.relation_id for relation in spec.capability_relations] == ["manual"]


def test_query_then_submit_does_not_invent_relation_without_field_mapping():
    query = FlowStep(
        step_id="query_status", method="GET", path="/records/page",
        source_meta={"role": "business_get"},
        response_json={"data": {"records": [{"date": "2026-05-01"}]}},
    )
    submit = FlowStep(
        step_id="submit", method="POST", path="/records/submit",
        body_source='{"date":"2026-05-02","content":"开发"}',
        source_meta={"role": "submit_anchor"},
        params=[ParamField(path="date", key="日期", type="date", source_kind="user_input")],
    )

    out = asyncio.run(orchestrate_flow_capabilities(
        FlowSpec(steps=[query, submit]),
        submission=_strict_submission(
            (
                "query_records", "查询记录", "query_status", "query_status",
                [("query_status", "execute")],
            ),
            (
                "submit_record", "提交记录", "submit", "submit",
                [("submit", "execute")],
            ),
        ),
    ))

    assert {cap.kind for cap in out.capabilities} == {"query_status", "submit"}
    assert out.capability_relations == []
    report = validate_flow_spec(out)
    assert not any("output/input 字段" in message for message in report["errors"])


def test_reoptimization_can_refresh_auto_accepted_semantics_but_keeps_user_owned_text():
    page_context = {
        "path": "/oa/common/hotel-apply",
        "visible_titles": ["酒店申请"],
    }
    step = FlowStep(
        step_id="withdraw", method="DELETE",
        path="/admin-api/bpm/process-instance/cancel-by-start-user",
        source_meta={
            "role": "business_write", "trigger_locator": "role=button[name=撤回]",
        },
    )
    auto = FlowCapability(
        name="submit", title="提交业务申请",
        intent="调用方提供业务字段；Skill 按已纳入接口顺序执行前置查询、依赖注入和最终提交。",
        nodes=[{"id": "call_withdraw", "type": "call", "step_id": "withdraw"}],
        confirmed=True, updated_by="planner", confidence=0.95,
    )
    optimized = asyncio.run(orchestrate_flow_capabilities(
        FlowSpec(title="cancel-by-start-user 流程(1 步)", steps=[step], capabilities=[auto], meta={
            "page_context": page_context,
            "capability_model": {"status": "ready", "semantic_plan": {}},
        }),
        submission={"semantic_plan": {
            "business_understanding": {"business_name": "酒店申请"},
            "capabilities": [{
                "name": "withdraw_hotel_application", "kind": "withdraw",
                "title": "撤回酒店申请", "intent": "撤回用户选定的酒店申请记录。",
                "anchor_step_id": "withdraw",
                "request_refs": [{"step_id": "withdraw", "usage": "execute"}],
            }],
            "unresolved_items": [],
        }, "ops": []},
        generation_mode="optimize",
    ))

    capability = optimized.capabilities[0]
    assert capability.name == "withdraw_hotel_application"
    assert capability.title == "撤回酒店申请"
    assert capability.intent == "撤回用户选定的酒店申请记录。"


def test_page_enum_binding_is_projected_to_param_and_capability_contract():
    step = FlowStep(
        step_id="submit",
        method="POST",
        path="/leave/submit",
        body_source='{"type":"2"}',
        params=[ParamField(
            path="type", key="请假类型", value="2", type="string",
            category="user_param", source_kind="user_input", required=True,
        )],
        selects=[SelectBinding(
            param="请假类型", path="type", enum_source="dom", enum_confirmed=True,
            options=[
                {"label": "病假", "value": "1"},
                {"label": "事假", "value": "2"},
                {"label": "婚假", "value": "3"},
            ],
            option_map={"病假": "1", "事假": "2", "婚假": "3"},
        )],
        response_json={"code": 0, "data": {"id": "leave-1"}},
    )
    spec = FlowSpec(
        steps=[step],
        capabilities=[FlowCapability(name="submit", kind="submit", nodes=_call_nodes(["submit"]))],
    )

    prepared = prepare_flow_spec_for_publish(spec)
    param = prepared.steps[0].params[0]
    capability_field = prepared.capabilities[0].inputs[0]
    api_request, errors = flow_spec_to_api_request(prepared)

    assert errors == []
    assert (param.type, param.source_kind) == ("enum", "page_enum")
    assert param.enum_value_map == {"病假": "1", "事假": "2", "婚假": "3"}
    assert capability_field.source_kind == "page_enum"
    assert capability_field.enum_options == param.enum_options
    assert api_request["capabilities"][0]["input_schema"]["properties"]["请假类型"]["enum"] == ["病假", "事假", "婚假"]
    assert not any("内部 ID/短码" in message for message in validate_flow_spec(prepared)["errors"])


def test_api_option_binding_preserves_source_request_in_capability_field():
    step = FlowStep(
        step_id="submit",
        method="POST",
        path="/leave/submit",
        body_source='{"assigneeId":"142"}',
        params=[ParamField(
            path="assigneeId", key="审批人", value="142", type="string",
            category="user_param", source_kind="user_input", required=True,
        )],
        selects=[SelectBinding(
            param="审批人", path="assigneeId", source_url="/users/options",
            source_method="GET", source_role="read_option", source_request_id="users-options",
            value_key="id", label_key="name", enum_source="api", enum_confirmed=True,
            options=[{"label": "张三", "value": "142"}], option_map={"张三": "142"},
        )],
        response_json={"code": 0},
    )
    spec = FlowSpec(
        steps=[step],
        capabilities=[FlowCapability(name="submit", kind="submit", nodes=_call_nodes(["submit"]))],
    )

    prepared = prepare_flow_spec_for_publish(spec)
    param = prepared.steps[0].params[0]
    field = prepared.capabilities[0].inputs[0]

    assert (param.type, param.wire_type, param.source_kind) == ("enum", "string", "api_option")
    assert param.source["source_url"] == "/users/options"
    assert param.source["source_request_id"] == "users-options"
    assert field.source_kind == "api_option"
    assert field.source["source_url"] == "/users/options"
    assert field.enum_value_map == {"张三": "142"}


def test_api_option_reselection_refreshes_candidates_while_preserving_wire_type():
    captured = [
        _get(1, "/api/old/options", {"data": []}),
        _get(2, "/api/new/options", {"data": [
            {"code": 2, "title": "行政章"},
            {"code": 3, "title": "合同章"},
        ]}),
        _post(3, "/seal/borrow", {"sealCode": 2}),
    ]
    spec = to_flow_spec(captured, samples={"公章": 2})
    submit = spec.steps[-1]
    param = next(item for item in submit.params if item.path == "sealCode")
    param.type = "number"
    param.source_kind = "api_option"
    submit.selects = [SelectBinding(
        param=param.key,
        path=param.path,
        source_url="https://oa.example.test/api/old/options",
        source_request_id="1",
        value_key="id",
        label_key="name",
        options=[],
        enum_source="api",
    )]

    spec = sync_flow_spec_models(spec)
    binding = spec.steps[-1].selects[0]
    binding.source_url = "https://oa.example.test/api/new/options"
    spec = sync_flow_spec_models(spec)
    submit = spec.steps[-1]
    param = next(item for item in submit.params if item.path == "sealCode")
    binding = submit.selects[0]

    assert (param.type, param.wire_type, param.source_kind) == ("enum", "number", "api_option")
    assert (binding.value_key, binding.label_key) == ("code", "title")
    assert binding.options == [
        {"label": "行政章", "value": 2},
        {"label": "合同章", "value": 3},
    ]
    assert param.enum_options == binding.options


def test_empty_api_candidates_are_valid_and_do_not_emit_dynamic_enum_warning():
    step = FlowStep(
        step_id="submit",
        method="POST",
        path="/seal/borrow",
        params=[ParamField(
            path="sealId",
            key="公章",
            type="enum",
            category="user_param",
            source_kind="api_option",
            source={"kind": "api_option"},
            enum_options=None,
            enum_value_map=None,
        )],
        selects=[SelectBinding(param="公章", path="sealId", options=[])],
    )
    report = validate_flow_spec(FlowSpec(
        steps=[step],
        capabilities=[FlowCapability(name="submit", kind="submit", nodes=_call_nodes(["submit"]))],
    ))
    messages = [*report["errors"], *report["warnings"]]

    assert not any("动态枚举缺少可执行的实时来源接口" in message for message in messages)
    assert not any("标记为接口选项，但缺少可执行" in message for message in messages)


def test_empty_page_enum_without_a_wire_mapping_returns_to_user_input():
    param = ParamField(
        path="status", key="流程状态", value="1", type="enum", wire_type="string",
        category="user_param", source_kind="page_enum",
        enum_options=None, enum_value_map=None,
    )

    synchronized = sync_flow_spec_models(FlowSpec(steps=[FlowStep(
        step_id="submit", method="POST", path="/apply", params=[param],
    )]))
    repaired = synchronized.steps[0].params[0]

    assert repaired.type == "string"
    assert repaired.source_kind == "user_input"
    assert repaired.enum_options is None


def test_option_endpoint_unmatched_filters_are_constants_but_recorded_search_is_input():
    spec = to_flow_spec([_get(
        1,
        "/system/seal/simple-list?status=0&keyword=%E8%A1%8C%E6%94%BF",
        {"data": [{"id": "s1", "name": "行政章"}]},
    )], samples={"搜索词": "行政"})
    step = promote_request_to_step(spec, request_index=1)
    by_path = {param.path: param for param in step.params}

    assert (by_path["query.status"].category, by_path["query.status"].source_kind) == (
        "system_const", "constant",
    )
    assert (by_path["query.keyword"].category, by_path["query.keyword"].source_kind) == (
        "user_param", "user_input",
    )


def test_screenshot_plan_cannot_overwrite_grounded_field_axes():
    field = ParamField(
        path="projectId", key="项目名称", label="项目名称", value="p-1",
        type="enum", wire_type="string", category="user_param", source_kind="api_option",
        source={"kind": "api_option", "source_url": "/api/projects", "value_key": "id", "label_key": "name"},
        confidence_tier="grounded", name_source="sample",
        evidence=[{
            "kind": "page_control", "source": "recorder_dom",
            "field_aliases": ["projectId"], "control_kind": "select",
        }],
    )
    spec = FlowSpec(
        steps=[FlowStep(
            step_id="submit", method="POST", path="/api/timesheet/submit",
            body_source='{"projectId":"p-1"}', params=[field],
        )],
        capabilities=[FlowCapability(
            nodes=[{"id": "call_1", "type": "call", "step_id": "submit"}],
        )],
    )
    submission = {"semantic_plan": {
        "business_understanding": {"business_name": "工时申报"},
        "request_roles": [],
        "field_semantics": [{
            "step_id": "submit", "wire_path": "projectId", "public_name": "错误字段名",
            "business_type": "number", "category": "runtime_var", "source_kind": "previous_response",
            "confidence": 0.99, "evidence": [{"source": "screenshot", "label": "错误字段名"}],
        }],
        "capabilities": [{
            "name": "submit_timesheet", "kind": "submit", "title": "提交工时", "step_ids": ["submit"],
        }],
        "capability_relations": [], "unresolved_items": [],
    }, "ops": []}

    out = asyncio.run(orchestrate_flow_capabilities(spec, submission=submission, generation_mode="optimize"))
    result = out.steps[0].params[0]
    assert (result.key, result.label, result.type) == ("项目名称", "项目名称", "enum")
    assert (result.category, result.source_kind) == ("user_param", "api_option")
    assert result.source["source_url"] == "/api/projects"

def test_publish_preparation_removes_stale_batch_fields_outputs_and_goal_capability():
    step = FlowStep(
        step_id="submit", method="POST", path="/leave/submit",
        body_source='{"reason":"事假"}',
        params=[ParamField(path="reason", key="原因", value="事假", required=True)],
        response_json={"code": 0, "data": {"id": "leave-1"}},
    )
    capability = FlowCapability(
        name="submit", kind="submit", nodes=_call_nodes(["submit"]),
        input_schema={"type": "object", "properties": {"entries": {"type": "array"}}, "required": ["entries"]},
        output_schema={"type": "object", "properties": {}},
        inputs=[CapabilityField(scope="input", key="entries", path="entries", type="array", locked=True)],
        outputs=[CapabilityField(scope="output", key="response", path="response", type="object", locked=True)],
        output_mapping=[{"kind": "final_response", "name": "result", "step_id": "submit", "response_path": "response"}],
    )
    spec = FlowSpec(
        steps=[step], capabilities=[capability],
        goal={"intent": "提交请假", "capabilities": ["list_options", "submit"]},
    )

    prepared = prepare_flow_spec_for_publish(spec)
    api_request, errors = flow_spec_to_api_request(prepared)

    assert errors == []
    assert prepared.goal["capabilities"] == ["submit"]
    assert set(prepared.capabilities[0].input_schema["properties"]) == {"原因"}
    assert [field.key for field in prepared.capabilities[0].inputs] == ["原因"]
    assert "result" in prepared.capabilities[0].output_schema["properties"]
    assert collect_capability_findings(api_request) == []


def test_final_response_output_uses_one_canonical_name_in_fields_and_schema():
    step = FlowStep(
        step_id="submit", method="POST", path="/submit",
        body_source='{"reason":"test"}',
        params=[ParamField(path="reason", key="原因", value="test", required=True)],
        response_json={"code": 0, "data": {"id": "one"}},
    )
    spec = FlowSpec(
        steps=[step],
        capabilities=[FlowCapability(
            name="submit", kind="submit", nodes=_call_nodes(["submit"]),
            output_mapping=[{"kind": "final_response", "step_id": "submit", "response_path": "response"}],
        )],
    )

    prepared = prepare_flow_spec_for_publish(spec)
    cap = prepared.capabilities[0]
    api_request, errors = flow_spec_to_api_request(prepared)

    assert errors == []
    assert set(cap.output_schema["properties"]) == {"output_1"}
    assert [field.key for field in cap.outputs] == ["output_1"]
    assert collect_capability_findings(api_request) == []


def test_relation_without_field_mapping_is_canonicalized_as_caller_decision():
    query = FlowStep(
        step_id="query", method="GET", path="/leave/page",
        response_json={"data": {"records": []}},
    )
    submit = FlowStep(
        step_id="submit", method="POST", path="/leave/submit",
        body_source='{"reason":"test"}',
        params=[ParamField(path="reason", key="原因", value="test")],
        response_json={"code": 0},
    )
    spec = FlowSpec(
        steps=[query, submit],
        capabilities=[
            FlowCapability(name="query_status", kind="query_status", nodes=_call_nodes(["query"])),
            FlowCapability(name="submit", kind="submit", nodes=_call_nodes(["submit"])),
        ],
        capability_relations=[CapabilityRelation(
            relation_id="legacy-empty-transform",
            type="suggested_call_chain",
            mode="external_transform",
            from_capability="query_status",
            to_capability="submit",
            confirmed=True,
        )],
    )

    prepared = prepare_flow_spec_for_publish(spec)
    relation = prepared.capability_relations[0]
    api_request, errors = flow_spec_to_api_request(prepared)

    assert errors == []
    assert (relation.type, relation.mode) == ("caller_decision", "caller_decision")
    assert relation.from_output == "" and relation.to_input == ""
    assert not any("relation" in item["kind"] for item in collect_capability_findings(api_request))


def test_external_transform_with_only_one_field_remains_invalid():
    relation = CapabilityRelation(
        type="external_transform", mode="external_transform",
        from_capability="query_status", from_output="records",
        to_capability="submit", to_input="",
    )

    normalized = flow_spec_module._normalize_capability_relation_semantics(relation)

    assert normalized.type == "external_transform"
    assert normalized.mode == "external_transform"


def test_screenshot_visible_options_replace_a_stale_api_source_only_when_unique():
    people = FlowStep(
        step_id="people", method="GET", path="/api/hr/users/page",
        response_json={"data": {"list": [
            {"id": 145, "name": "财务A"},
            {"id": 148, "name": "审批B"},
        ]}},
        source_meta={"role": "read_option"},
    )
    tenants = FlowStep(
        step_id="tenants", method="GET", path="/api/system/tenant/list",
        response_json={"data": {"list": [
            {"id": 145, "name": "租户甲"},
            {"id": 149, "name": "租户乙"},
        ]}},
        source_meta={"role": "read_option"},
    )
    submit = FlowStep(
        step_id="submit", method="POST", path="/api/request/submit",
        source_meta={"role": "business_write"},
        params=[ParamField(
            path="assigneeId", key="审批人", label="审批人", value=145,
            type="enum", wire_type="number", category="user_param",
            source_kind="api_option",
            source={
                "kind": "api_option", "source_step_id": "tenants",
                "source_url": "/api/system/tenant/list",
                "value_key": "id", "label_key": "name",
            },
            evidence=[{
                "source": "screenshot", "visible_label": "审批人",
                "visible_value": "财务A", "control_kind": "select", "editable": True,
                "options": ["财务A", "审批B"],
            }],
        )],
    )
    spec = FlowSpec(steps=[people, tenants, submit])

    repaired = flow_spec_module._repair_structural_option_bindings(spec)
    field = submit.params[0]

    assert repaired == 1
    assert field.source_kind == "api_option"
    assert field.source["source_step_id"] == "people"
    assert field.enum_value_map == {"财务A": 145, "审批B": 148}

def test_r5_auto_flow_links_require_real_ordered_type_compatible_endpoints():
    source = FlowStep(
        step_id="source", method="GET", path="/api/source",
        response_json={"data": {"id": "ENTITY-1", "count": 1}},
        source_meta={"sequence": 1},
    )
    target = FlowStep(
        step_id="target", method="POST", path="/api/target",
        params=[
            ParamField(path="entityId", key="entityId", value="ENTITY-1", category="runtime_var"),
            ParamField(path="count", key="count", value="1", wire_type="string", category="runtime_var"),
        ],
        source_meta={"sequence": 2},
    )
    late_source = FlowStep(
        step_id="late", method="GET", path="/api/late",
        response_json={"data": {"id": "ENTITY-1"}},
        source_meta={"sequence": 3},
    )
    links = [
        FlowLink(
            link_id="valid", source_step_id="source", source_path="data.id",
            target_step_id="target", target_path="entityId", confirmed=True, confidence=0.98,
            evidence={"kind": "value_match"},
        ),
        FlowLink(
            link_id="missing-source", source_step_id="source", source_path="data.missing",
            target_step_id="target", target_path="entityId", confirmed=True, confidence=0.98,
            evidence={"kind": "value_match"},
        ),
        FlowLink(
            link_id="reverse-time", source_step_id="late", source_path="data.id",
            target_step_id="target", target_path="entityId", confirmed=True, confidence=0.98,
            evidence={"kind": "value_match"},
        ),
        FlowLink(
            link_id="type-collision", source_step_id="source", source_path="data.count",
            target_step_id="target", target_path="count", confirmed=True, confidence=0.98,
            evidence={"kind": "value_match"},
        ),
    ]

    flow_spec_module._prune_unsafe_auto_links([source, target, late_source], links)

    assert [link.link_id for link in links] == ["valid"]


def test_r5_value_only_link_to_opaque_runtime_field_is_rejected():
    source = FlowStep(
        step_id="source", method="GET", response_json={"data": {"value": "SAME"}},
        source_meta={"sequence": 1},
    )
    target = FlowStep(
        step_id="target", method="POST", source_meta={"sequence": 2},
        params=[ParamField(path="opaque", key="opaque", value="SAME", category="runtime_var")],
    )
    links = [FlowLink(
        link_id="value-only", source_step_id="source", source_path="data.value",
        target_step_id="target", target_path="opaque", confirmed=True, confidence=0.99,
        evidence={"kind": "value_match"},
    )]

    flow_spec_module._prune_unsafe_auto_links([source, target], links)

    assert links == []


def test_r5_field_projection_requires_selected_wire_value_not_visible_label():
    selects = [{
        "path": "projectId", "source_url": "/api/projects/options",
        "value_key": "code", "label_key": "title",
    }]
    fields = [
        {"path": "projectId", "value": "Project B"},
        {"path": "team", "value": "Team B"},
    ]
    reads = [{
        "url": "/api/projects/options",
        "json": {"items": [{"code": "P-2", "title": "Project B", "team": "Team B"}]},
    }]

    flow_spec_module._attach_select_field_projections(selects, fields, reads)

    assert "field_projections" not in selects[0]


def test_r5_field_projection_requires_exact_typed_submitted_value():
    selects = [{
        "path": "projectId", "source_url": "/api/projects/options",
        "value_key": "code", "label_key": "title",
    }]
    fields = [
        {"path": "projectId", "value": "P-2"},
        {"path": "quota", "value": "7"},
    ]
    reads = [{
        "url": "/api/projects/options",
        "json": {"items": [{"code": "P-2", "title": "Project B", "quota": 7}]},
    }]

    flow_spec_module._attach_select_field_projections(selects, fields, reads)

    assert "field_projections" not in selects[0]


def test_screenshot_analysis_reconciles_stale_option_binding_with_recorded_control_fact():
    direct_input = ParamField(
        path="guestCount", key="入住人数", label="入住人数", value=1,
        type="enum", wire_type="number", category="user_param",
        source_kind="api_option",
        source={"kind": "api_option", "source_url": "/api/tenant/options"},
        enum_options=[{"label": "租户甲", "value": 1}, {"label": "租户乙", "value": 2}],
        enum_value_map={"租户甲": 1, "租户乙": 2},
        evidence=[{
            "kind": "page_control", "source": "recorder_dom",
            "control_kind": "number", "editable": True,
            "field_aliases": ["guestCount"],
        }],
    )
    submit = FlowStep(
        step_id="submit", method="POST", path="/api/apply", params=[direct_input],
        selects=[SelectBinding(
            path="guestCount", source_url="/api/tenant/options",
            value_key="id", label_key="name", enum_source="api", enum_confirmed=True,
            options=direct_input.enum_options, option_map=direct_input.enum_value_map,
        )],
    )
    spec = FlowSpec(steps=[submit])

    optimized = asyncio.run(orchestrate_flow_capabilities(
        spec,
        submission={"_analysis_screenshot_count": 1, "ops": []},
        generation_mode="optimize",
    ))

    repaired = optimized.steps[0].params[0]
    assert (repaired.type, repaired.source_kind) == ("number", "user_input")
    assert repaired.enum_options is None
    assert optimized.steps[0].selects == []


def test_r6_screenshot_field_identity_does_not_cross_same_named_paths():
    first = FlowStep(
        step_id="create", method="POST", path="/api/create",
        params=[ParamField(path="body.status", key="Status", type="string")],
    )
    second = FlowStep(
        step_id="approve", method="POST", path="/api/approve",
        params=[ParamField(path="body.status", key="Status", type="string")],
    )
    spec = FlowSpec(steps=[first, second])

    changed = flow_spec_module._apply_capability_field_to_param(
        spec,
        {
            "step_id": "approve", "wire_path": "body.status", "key": "Approval status",
            "type": "enum", "category": "user_param", "source_kind": "page_enum",
            "required": True, "enum_options": ["Pending", "Approved"],
            "evidence": [{
                "source": "screenshot", "screenshot_name": "approve.png",
                "control_kind": "select", "editable": True, "required": True,
            }],
        },
        scope="input",
        actor="planner",
    )

    assert changed is True
    assert (first.params[0].key, first.params[0].type) == ("Status", "string")
    assert (second.params[0].key, second.params[0].type) == ("Approval status", "enum")


def _page_recorder_source() -> str:
    return (
        Path(__file__).resolve().parents[2]
        / "skillfrontend"
        / "src"
        / "components"
        / "PageRecorder.tsx"
    ).read_text(encoding="utf-8")








@pytest.mark.parametrize(
    ("host", "option_path", "business_path", "wrapper"),
    [
        ("crm.invalid", "/x/lookup", "/x/search", "payload"),
        ("erp.invalid", "/v9/reference", "/v9/filter", "choices"),
        ("legacy.invalid", "/service/a", "/service/b", "resultSet"),
    ],
)
def test_r8_request_roles_are_invariant_to_host_path_wrapper_and_field_names(
    host: str,
    option_path: str,
    business_path: str,
    wrapper: str,
):
    option_rows = {wrapper: {"bucket": [
        {"refCode": "wire-a", "displayText": "Visible A"},
        {"refCode": "wire-b", "displayText": "Visible B"},
    ]}}
    option_request = {
        "index": 1,
        "method": "GET",
        "url": f"https://{host}{option_path}",
        "response_json": option_rows,
        "trigger_op": "select",
        "trigger_locator": "[role=combobox]",
        "trigger_transaction_id": "txn-option",
    }
    unrelated_list = {
        **option_request,
        "index": 2,
        "url": f"https://{host}{option_path}-unrelated",
        "trigger_op": "",
        "trigger_locator": "",
        "trigger_transaction_id": "",
    }
    business_query = {
        "index": 3,
        "method": "GET",
        "url": f"https://{host}{business_path}?q=active",
        "query": {"q": "active"},
        "response_json": {wrapper: {"bucket": [
            {"recordToken": "r-1", "measure": 7, "captionText": "First"},
        ]}},
        "trigger_op": "click",
        "trigger_locator": "button[type=submit]",
        "trigger_transaction_id": "txn-query",
    }

    assert flow_spec_module.classify_network_request(option_request)["role"] == "read_option"
    assert flow_spec_module.classify_network_request(unrelated_list)["role"] == "read_context"
    assert flow_spec_module.classify_network_request(business_query)["role"] == "business_get"


def test_r8_empty_business_query_uses_action_and_filter_evidence_not_endpoint_tokens():
    request = {
        "index": 1,
        "method": "GET",
        "url": "https://held-out.invalid/q7/z9?page=1",
        "query": {"page": 1},
        "response_json": {"opaque": {"bucket": []}},
        "trigger_op": "click",
        "trigger_locator": "button[type=submit]",
        "trigger_transaction_id": "txn-held-out-query",
    }

    assert flow_spec_module.classify_network_request(request)["role"] == "business_get"


def test_r8_recording_rules_do_not_contain_scenario_specific_identifiers():
    source = Path(flow_spec_module.__file__).read_text(encoding="utf-8")

    for forbidden in (
        "seal_apply vs hotel_apply",
        "seal chooser",
        "oa|bpm|system|workflow|process",
    ):
        assert forbidden not in source
