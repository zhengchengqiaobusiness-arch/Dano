from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import dano.execution.page.capability_views as capability_views
from dano.execution.page.capability_contracts import (
    _capability_business_key,
    _capability_relation_schemas_compatible,
)
from dano.execution.page.capability_identity import (
    _annotate_identifier_sources,
    _identifier_value_is_grounding_evidence,
)
from dano.execution.page.capability_validation import (
    _capability_validation_report,
    _capability_param_enum_issue,
)
from dano.execution.page.capability_semantic import (
    _required_public_action_request_ids,
    _semantic_plan_coverage,
)
from dano.execution.page.capability_orchestration import sync_capability_scoped_views
from dano.execution.page.flow_materialization.field_contracts.common import (
    _grounded_screenshot_query_path,
)
from dano.execution.page.flow_materialization.field_contracts.option_repair import (
    _option_binding_semantic_families,
    _repair_structural_option_bindings,
    _restore_executable_option_request_ids,
)
from dano.execution.page.flow_materialization.field_contracts.option_projection import (
    _best_option_projection_path,
    _infer_selected_option_row_fields,
)
from dano.execution.page.flow_materialization.builder import (
    _apply_mechanical_field_contracts,
    _rebind_saved_field_evidence,
    sync_flow_spec_models,
    to_flow_spec,
)
from dano.execution.page.flow_materialization.field_contracts.computed import (
    _infer_arithmetic_computed_fields,
    _infer_collection_computed_fields,
)
from dano.execution.page.flow_materialization.field_contracts.create_form import (
    _apply_create_form_field_contracts,
)
from dano.execution.page.flow_materialization.field_contracts.edit_form import (
    _apply_edit_form_field_contracts,
)
from dano.execution.page.flow_materialization.field_contracts.page_rules import (
    _apply_page_rule_caller_override,
)
from dano.execution.page.flow_materialization.field_contracts.query_form import (
    _apply_query_form_field_contracts,
)
from dano.execution.page.flow_materialization.field_contracts.row_command import (
    _apply_row_command_field_contracts,
)
from dano.execution.page.flow_materialization.request_usage import _same_request_cohort
from dano.execution.page.flow_materialization.response_maps import (
    _latest_response_key_map_candidates,
    _response_list_paths,
    _same_response_cohort,
)
from dano.execution.page.flow_spec import apply_recording_agent_submission
from dano.execution.page.flow_spec_core.models import (
    CapabilityRequestRef,
    FlowCapability,
    FlowLink,
    FlowSpec,
    FlowStep,
    ParamField,
    RequestAnalysis,
    RequestFact,
    RequestUsage,
    SelectBinding,
)
from dano.execution.page.flow_spec_validate import validate_flow_spec
from dano.execution.page.recording_agent_contract import (
    _semantic_fact_snapshot,
    recording_capability_plan_complete,
)
from dano.execution.page.recording_facts import _list_payload_has_reference_contract
from dano.execution.page.recording_field_evidence import (
    _associate_unsubmitted_file_controls,
    _same_recorded_value,
    _temporal_alignment,
    bind_field_evidence,
)
from dano.execution.page.recording_live import (
    _reconcile_captured_value_dependencies,
    _record_agent_op,
    recording_delta,
)
from dano.execution.page.request_capture import (
    _apply_runtime_fields,
    _field_related_to_option_source,
)
from dano.execution.page.request_identity import request_identity_matches
from dano.execution.page.value_tracing import discover_workflow_value_links
from dano.onboarding.recording_release import _capability_spec


def readonly_number(path: str, key: str, value: float) -> ParamField:
    return ParamField(
        path=path,
        key=key,
        value=value,
        type="number",
        wire_type="number",
        required=False,
        source_kind="unknown",
        evidence=[{
            "kind": "page_control",
            "control_kind": "number",
            "read_only": True,
            "editable": False,
        }],
    )


def test_sparse_lists_are_not_sampled_or_truncated() -> None:
    rows = [{"value": index} for index in range(12)]
    rows[11] = {"productId": 7, "productName": "late row"}
    assert _list_payload_has_reference_contract(rows)

    nested = [{"value": index} for index in range(4)]
    nested[3] = {"children": [{"id": 1}]}
    assert "[]children" not in _response_list_paths(nested)
    assert "[].children" in _response_list_paths(nested)


def test_dates_use_recorded_browser_timezone_only() -> None:
    epoch_ms = int(datetime(2026, 8, 20, 16, tzinfo=UTC).timestamp() * 1000)
    assert not _same_recorded_value("2026-08-21", epoch_ms)
    assert _same_recorded_value("2026-08-21", epoch_ms, 8 * 60)
    assert not _same_recorded_value("2026-08-21", epoch_ms, 0)


def test_late_evidence_needs_exact_causality() -> None:
    assert _temporal_alignment(10.0, 11.0) == (False, None)
    assert _temporal_alignment(10.0, 11.0, allow_late=True) == (True, 0.0)


def test_short_value_binds_when_unique_in_exact_scope() -> None:
    bound = bind_field_evidence(
        [{
            "request_id": "r1",
            "method": "GET",
            "url": "https://example.test/orders?status=1",
            "query": {"status": "1"},
            "page_id": "p1",
            "frame_id": "f1",
        }],
        [],
        [{
            "value": "1",
            "label": "状态",
            "control_kind": "select",
            "surface": "page",
            "page_id": "p1",
            "frame_id": "f1",
            "url": "https://example.test/orders",
        }],
    )
    assert bound[0]["binding_status"] == "bound"
    assert bound[0]["wire_path"] == "query.status"
    assert bound[0]["binding_method"] == "unique_value_same_scope"


def test_inline_table_header_is_the_field_name_axis() -> None:
    bound = bind_field_evidence(
        [{
            "request_id": "create",
            "method": "POST",
            "url": "https://example.test/orders",
            "post_data": {"items": [{"count": 8}]},
            "page_id": "p1",
            "frame_id": "f1",
            "trigger_action_id": "save",
        }],
        [],
        [{
            "value": 8,
            "label": "税率（%）",
            "column_label": "数量",
            "control_kind": "number",
            "control_surface": "table_inline",
            "row_index": 0,
            "surface": "dialog",
            "page_id": "p1",
            "frame_id": "f1",
            "action_id": "save",
        }],
    )
    assert bound[0]["label"] == "数量"
    assert bound[0]["wire_path"] == "body.items[0].count"


def test_unsubmitted_file_never_invents_wire_path() -> None:
    evidence = [
        {
            "control_kind": "text",
            "binding_status": "bound",
            "request_id": "write-1",
            "wire_path": "body.remark",
            "page_id": "p1",
            "frame_id": "f1",
            "surface": "dialog",
            "form_root": "form-1",
        },
        {
            "control_kind": "file",
            "binding_status": "unbound",
            "field_identity_id": "attachment",
            "label": "附件",
            "page_id": "p1",
            "frame_id": "f1",
            "surface": "dialog",
            "form_root": "form-1",
        },
    ]
    _associate_unsubmitted_file_controls(
        evidence,
        [{
            "request_id": "write-1",
            "method": "POST",
            "url": "https://example.test/orders",
            "post_data": {"remark": "x"},
            "page_id": "p1",
            "frame_id": "f1",
        }],
    )
    file_evidence = evidence[1]
    assert file_evidence["binding_status"] == "unresolved_non_executable"
    assert file_evidence["owner_request_id"] == "write-1"
    assert "wire_path" not in file_evidence


def test_delta_branch_paging_exposes_full_response() -> None:
    request = {
        "request_id": "r1",
        "method": "GET",
        "url": "https://example.test/items",
        "response_json": {"rows": [{"id": index} for index in range(20)]},
    }
    delta = recording_delta(
        None,
        captured_requests=[request],
        page_events=[],
        field_evidence=[],
        request_id="r1",
        branch_path="response_json.rows",
        branch_cursor=7,
        branch_limit=5,
    )
    assert delta["branch"]["total"] == 20
    assert delta["branch"]["next_cursor"] == 12
    assert delta["branch"]["value"][0]["id"] == 7


def test_agent_operation_audit_is_not_capped_at_500() -> None:
    spec = FlowSpec()
    for index in range(520):
        _record_agent_op(spec, {"op": "set_goal", "sequence": index})
    assert len(spec.meta["recording_agent_ops"]) == 520
    assert spec.meta["recording_agent_ops"][0]["sequence"] == 0


def test_route_only_identity_is_rejected() -> None:
    assert not request_identity_matches(
        {"method": "GET", "url": "/orders"},
        {"method": "GET", "url": "/orders", "request_id": "different-action"},
    )


def test_repeated_route_does_not_cross_action_cohorts() -> None:
    source = {
        "method": "GET",
        "url": "/orders",
        "page_id": "p1",
        "frame_id": "f1",
        "trigger_action_id": "search-open",
    }
    candidate = {**source, "trigger_action_id": "search-closed"}
    assert not _same_request_cohort(source, candidate)
    assert not _same_response_cohort(
        FlowStep(method="GET", path="/orders", source_meta=source),
        candidate,
    )


def test_screenshot_output_leaf_cannot_invent_query_input() -> None:
    step = FlowStep(
        method="GET",
        path="/orders",
        params=[],
        response_json={"data": {"status": "approved"}},
    )
    assert _grounded_screenshot_query_path(
        step,
        {
            "path": "query.status",
            "evidence": [{
                "source": "screenshot",
                "control_kind": "select",
                "editable": True,
            }],
        },
    ) is None


def test_response_key_maps_do_not_choose_globally_nearest_action() -> None:
    rows = [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]
    requests = [
        {
            "request_id": "options-a",
            "sequence": 1,
            "response_json": {"rows": rows},
            "page_id": "p1",
            "frame_id": "f1",
            "trigger_action_id": "action-a",
        },
        {
            "request_id": "options-b",
            "sequence": 2,
            "response_json": {"rows": rows},
            "page_id": "p1",
            "frame_id": "f1",
            "trigger_action_id": "action-b",
        },
        {
            "request_id": "write",
            "sequence": 3,
            "post_data": {"assignments": {"a": 1}},
            "page_id": "p1",
            "frame_id": "f1",
        },
    ]
    assert _latest_response_key_map_candidates(requests) == []


def test_object_relations_require_nested_required_properties() -> None:
    assert not _capability_relation_schemas_compatible(
        {"type": "object", "properties": {"id": {"type": "string"}}},
        {
            "type": "object",
            "required": ["id", "name"],
            "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
        },
    )


def test_identifier_scan_is_complete_and_short_ids_remain_evidence() -> None:
    schema = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
        },
    }
    rows = [{} for _ in range(80)] + [{"id": 7}]
    found = _annotate_identifier_sources(schema, rows)
    assert any(item["values"] == {"7"} for item in found)
    assert _identifier_value_is_grounding_evidence("7")


def test_business_group_uses_full_stable_resource_path() -> None:
    orders = FlowStep(method="POST", path="/api/v1/workflow/order/create")
    leave = FlowStep(method="POST", path="/gateway/v1/workflow/leave/create")
    assert _capability_business_key(orders) != _capability_business_key(leave)


def test_api_option_requires_executable_source_contract() -> None:
    param = ParamField(
        path="body.productId",
        key="productId",
        type="enum",
        source_kind="api_option",
        source={"kind": "api_option"},
    )
    assert "缺少可执行字段" in _capability_param_enum_issue(param, FlowSpec())


def test_unconfirmed_public_capability_is_a_release_error() -> None:
    spec = FlowSpec(
        steps=[FlowStep(
            step_id="search",
            method="GET",
            path="/orders",
            source_meta={"role": "business_get"},
        )],
        capabilities=[FlowCapability(
            name="search_orders",
            kind="query",
            step_ids=["search"],
            nodes=[{"node_id": "call-search", "type": "call", "step_id": "search"}],
            confirmed=False,
        )],
    )
    report = validate_flow_spec(spec)["capability_validation"]
    assert any(
        item.get("code") == "unconfirmed_public_capability"
        for item in report["skill_level"]["errors"]
    )


def test_generic_status_field_does_not_bind_to_unrelated_presence_api() -> None:
    field = {
        "path": "outStatus",
        "field_aliases": ["outStatus"],
        "control_kind": "select",
        "action_id": "order-filter",
    }
    assert not _field_related_to_option_source(
        field,
        {
            "url": "https://example.test/im/user/online-status",
            "trigger_action_id": "background-presence",
        },
    )
    assert _field_related_to_option_source(
        field,
        {
            "url": "https://example.test/order/status/options",
            "trigger_action_id": "order-filter",
        },
    )


def test_structural_repair_keeps_status_filter_off_unrelated_presence_api() -> None:
    target = FlowStep(
        step_id="search",
        method="GET",
        path="/orders/page?outStatus=1",
        params=[ParamField(
            path="query.outStatus",
            key="outStatus",
            label="出库数量",
            value="1",
            type="enum",
            wire_type="string",
            source_kind="page_enum",
            source={"kind": "page_enum", "enum_confirmed": False},
            enum_options=["未出库", "部分出库", "全部出库"],
            evidence=[{
                "kind": "page_control",
                "source": "recorder_dom",
                "control_kind": "select",
                "binding_status": "bound",
                "editable": True,
                "request_path": "query.outStatus",
            }],
        )],
        source_meta={"request_id": "search", "page_id": "p1", "frame_id": "f1"},
    )
    presence = FlowStep(
        step_id="presence",
        method="GET",
        path="/im/user/online-status",
        response_json={"data": [
            {"id": "1", "nickname": "管理员"},
            {"id": "2", "nickname": "测试员"},
        ]},
        semantic_role="read_option",
        source_meta={
            "request_id": "presence",
            "role": "read_option",
            "page_id": "p1",
            "frame_id": "f1",
        },
    )
    spec = FlowSpec(
        steps=[presence, target],
        meta={"stage_1_6_contract_version": 2},
    )

    _repair_structural_option_bindings(spec)

    out_status = target.params[0]
    assert out_status.source_kind == "page_enum"
    assert out_status.enum_options == ["未出库", "部分出库", "全部出库"]
    assert "online-status" not in str(out_status.source)


def test_submitted_capabilities_cannot_complete_as_smaller_subset() -> None:
    submitted = [{"name": f"ability_{index}"} for index in range(8)]
    spec = FlowSpec(
        capabilities=[FlowCapability(name=f"ability_{index}") for index in range(6)],
        meta={
            "capability_model": {
                "status": "ready",
                "proposal_gate": {"accepted": True},
                "semantic_coverage": {"complete": True},
                "submitted_semantic_plan": {"capabilities": submitted},
                "semantic_plan": {"capabilities": submitted[:6]},
            },
        },
    )
    assert not recording_capability_plan_complete(spec)


def test_exact_submitted_plan_is_confirmed_and_can_complete() -> None:
    spec = FlowSpec(
        title="Orders",
        business_description="Query orders and report errors to the caller.",
        steps=[FlowStep(
            step_id="search",
            name="search",
            method="GET",
            path="/api/orders",
            url="/api/orders",
            response_json={"rows": [{"id": 1}]},
            source_meta={
                "request_id": "req-search",
                "role": "business_get",
                "page_id": "p1",
                "frame_id": "f1",
            },
        )],
    )
    spec.request_facts.requests = [RequestFact(
        request_id="req-search",
        request_index=1,
        method="GET",
        url="/api/orders",
        path="/api/orders",
        response_status=200,
        response_json={"rows": [{"id": 1}]},
        page_id="p1",
        frame_id="f1",
    )]
    spec.request_facts.analysis = {
        "req-search": RequestAnalysis(
            request_id="req-search",
            role="business_get",
            keep=True,
            confidence=0.99,
        ),
    }
    spec.request_facts.usage = {
        "req-search": RequestUsage(
            request_id="req-search",
            materialized_step_id="search",
            state="materialized",
        ),
    }
    plan = {
        "semantic_plan": {
            "business_understanding": {
                "business_name": "Orders",
                "summary": "Query orders",
                "intent": "query",
                "object": "orders",
                "purpose": "query orders",
            },
            "capabilities": [{
                "name": "search_orders",
                "title": "Search orders",
                "kind": "query",
                "anchor_step_id": "search",
                "request_refs": [{
                    "step_id": "search",
                    "request_id": "req-search",
                    "usage": "execute",
                }],
            }],
            "unresolved_items": [],
        },
        "ops": [],
        "_submitted_semantic_keys": [
            "business_understanding", "capabilities", "unresolved_items",
        ],
    }
    result = asyncio.run(apply_recording_agent_submission(
        spec,
        submission=plan,
        mode="plan",
    ))
    model = result.meta["capability_model"]
    assert model["submitted_count"] == model["materialized_count"] == 1
    assert result.capabilities[0].confirmed
    assert result.capabilities[0].confirmation_hash
    assert recording_capability_plan_complete(result)


def test_field_axis_edits_run_before_semantic_plan_compilation() -> None:
    spec = FlowSpec(
        title="Sales orders",
        business_description="Create a sales order and return the result.",
        steps=[FlowStep(
            step_id="create",
            name="create",
            method="POST",
            path="/sale-order/create",
            url="/sale-order/create",
            body_source='{"accountId": 2}',
            response_json={"code": 0, "data": 67},
            source_meta={
                "request_id": "req-create",
                "role": "business_write",
                "page_id": "p1",
                "frame_id": "f1",
            },
            params=[ParamField(
                path="accountId",
                key="accountId",
                label="accountId",
                value=2,
                type="number",
                wire_type="number",
                category="user_param",
                source_kind="user_input",
                source={"kind": "sample", "path": "accountId", "recorded": True},
                exposed_to_user=True,
                editable=True,
                required=False,
            )],
        )],
    )
    spec.request_facts.requests = [RequestFact(
        request_id="req-create",
        request_index=1,
        method="POST",
        url="/sale-order/create",
        path="/sale-order/create",
        post_data={"accountId": 2},
        response_status=200,
        response_json={"code": 0, "data": 67},
        page_id="p1",
        frame_id="f1",
    )]
    spec.request_facts.analysis = {
        "req-create": RequestAnalysis(
            request_id="req-create",
            role="business_write",
            keep=True,
            confidence=0.99,
        ),
    }
    spec.request_facts.usage = {
        "req-create": RequestUsage(
            request_id="req-create",
            materialized_step_id="create",
            state="materialized",
        ),
    }
    spec.request_facts.field_evidence = [{
        "evidence_id": "field-account",
        "binding_status": "unbound",
        "label": "结算账户",
        "field": "结算账户",
        "control_kind": "select",
        "editable": True,
        "disabled": False,
        "required_observed": False,
        "page_id": "p1",
        "frame_id": "f1",
    }]
    submission = {
        "semantic_plan": {
            "business_understanding": {
                "business_name": "销售订单",
                "summary": "新增销售订单",
            },
            "capabilities": [{
                "name": "create_sale_order",
                "title": "新增销售订单",
                "kind": "create",
                "anchor_step_id": "create",
                "request_refs": [{
                    "step_id": "create",
                    "request_id": "req-create",
                    "usage": "execute",
                }],
            }],
            "unresolved_items": [],
        },
        "ops": [
            {
                "op": "set_param_source",
                "step_id": "create",
                "path": "accountId",
                "source_kind": "caller_input",
                "reason": "该选择由调用方在页面上提供。",
                "evidence_refs": ["field-account"],
            },
            {
                "op": "set_param_type",
                "step_id": "create",
                "path": "accountId",
                "business_type": "enum",
                "reason": "页面证据表明这是单选控件。",
                "evidence_refs": ["field-account"],
            },
            {
                "op": "set_param_required",
                "step_id": "create",
                "path": "accountId",
                "required": False,
                "reason": "页面控件没有必填标记。",
                "evidence_refs": ["field-account"],
            },
            {
                "op": "rename_field",
                "step_id": "create",
                "path": "accountId",
                "label": "结算账户",
                "reason": "页面表单明确标注该字段为结算账户。",
                "evidence_refs": ["field-account"],
            },
        ],
        "_submitted_semantic_keys": [
            "business_understanding", "capabilities", "unresolved_items",
        ],
    }

    result = asyncio.run(apply_recording_agent_submission(
        spec,
        submission=submission,
        mode="plan",
    ))

    account = next(param for param in result.steps[0].params if param.path == "accountId")
    assert [
        (item["op"], item["status"], item["reason"])
        for item in result.meta["recording_agent_session"]["op_results"]
    ] == [
        ("set_param_source", "applied", ""),
        ("set_param_type", "applied", ""),
        ("set_param_required", "applied", ""),
        ("rename_field", "applied", ""),
    ]
    assert account.label == "结算账户"
    assert account.key == "accountId"
    assert account.path == "accountId"
    assert account.type == "enum"
    assert account.source_kind == "user_input"
    assert account.category == "user_param"
    assert account.exposed_to_user is True
    assert account.required is False
    assert {
        item.get("kind")
        for item in account.evidence
        if isinstance(item, dict) and item.get("actor") == "agent"
    } >= {"param_source", "param_type", "param_required", "field_name"}
    assert result.meta["capability_model"]["semantic_coverage"]["complete"] is True
    assert len(result.capabilities) == 1
    assert recording_capability_plan_complete(result)


def test_unique_scalar_lookup_links_to_semantic_wire_field() -> None:
    links = discover_workflow_value_links([
        {
            "request_id": "stock",
            "sequence": 1,
            "method": "GET",
            "url": "https://example.test/product/stock/get-count",
            "response_json": {"data": 1234.0},
            "page_id": "p1",
            "frame_id": "f1",
        },
        {
            "request_id": "create",
            "sequence": 2,
            "method": "POST",
            "url": "https://example.test/sale/order/create",
            "post_data": {"stockCount": 1234.0},
            "page_id": "p1",
            "frame_id": "f1",
        },
    ])
    assert any(
        item["source_request_id"] == "stock"
        and item["source_path"] == "response.data"
        and item["target_path"] == "body.stockCount"
        for item in links
    )


def test_scalar_lookup_ignores_standard_response_envelope_fields() -> None:
    links = discover_workflow_value_links([
        {
            "request_id": "stock",
            "sequence": 1,
            "method": "GET",
            "url": "https://example.test/product/stock/get-count",
            "response_json": {"code": 0, "msg": "", "data": 120.001},
            "page_id": "p1",
            "frame_id": "f1",
        },
        {
            "request_id": "create",
            "sequence": 2,
            "method": "POST",
            "url": "https://example.test/sale/order/create",
            "post_data": {"items": [{"stockCount": 120.001}]},
            "page_id": "p1",
            "frame_id": "f1",
        },
    ])
    assert any(
        item["source_request_id"] == "stock"
        and item["source_path"] == "response.data"
        and item["target_path"] == "body.items[0].stockCount"
        for item in links
    )


def test_scalar_lookup_consumed_by_write_is_preflight_not_public_ability() -> None:
    captured = [
        {
            "request_id": "lookup",
            "sequence": 1,
            "method": "GET",
            "url": "https://example.test/catalog/availability/get-count?itemId=7",
            "path": "/catalog/availability/get-count",
            "query": {"itemId": ["7"]},
            "response_json": {"code": 0, "msg": "", "data": 120.001},
            "response_status": 200,
            "resource_type": "xhr",
            "content_type": "application/json",
            "page_id": "p1",
            "frame_id": "f1",
            "trigger_action_id": "choose-item",
            "trigger_transaction_id": "p1|f1|choose-item",
            "trigger_op": "fill",
            "trigger_locator": "label=Item",
        },
        {
            "request_id": "create",
            "sequence": 2,
            "method": "POST",
            "url": "https://example.test/orders/create",
            "path": "/orders/create",
            "post_data": (
                '{"items":[{"itemId":7,"availabilityCount":120.001,"count":2}]}'
            ),
            "response_json": {"code": 0, "data": 99},
            "response_status": 200,
            "resource_type": "xhr",
            "content_type": "application/json",
            "page_id": "p1",
            "frame_id": "f1",
            "trigger_action_id": "save-order",
            "trigger_transaction_id": "p1|f1|save-order",
            "trigger_op": "fill",
            "trigger_locator": "label=Tax rate",
        },
    ]
    spec = to_flow_spec(
        captured,
        request_role_overrides={
            "lookup": {"role": "business_get", "keep": True, "confidence": 0.9},
            "create": {"role": "business_write", "keep": True, "confidence": 0.99},
        },
    )

    lookup = next(
        step for step in spec.steps
        if step.path.split("?", 1)[0] == "/catalog/availability/get-count"
    )
    create = next(step for step in spec.steps if step.path == "/orders/create")
    availability = next(param for param in create.params if param.key == "availabilityCount")

    assert lookup.source_meta["role"] == "read_context"
    assert lookup.source_meta["control_preflight_for_write_ids"] == [create.step_id]
    assert availability.source_kind == "previous_response"
    assert availability.source["step_id"] == lookup.step_id
    assert _required_public_action_request_ids(spec) == {"create"}


def test_short_semantic_scalar_is_materialized_as_runtime_dependency() -> None:
    stock_param = ParamField(
        path="items[0].stockCount",
        key="stockCount",
        label="库存",
        value=1234,
        type="number",
        wire_type="number",
        category="system_const",
        source_kind="constant",
        source={"kind": "recorded_control_default"},
        evidence=[{
            "kind": "page_control",
            "control_kind": "text",
            "disabled": True,
            "editable": False,
        }],
    )
    spec = FlowSpec(steps=[
        FlowStep(
            step_id="stock",
            method="GET",
            path="/product/stock/get-count",
            response_json={"code": 0, "msg": "", "data": 1234.0},
            source_meta={"request_id": "req-stock", "page_id": "p1", "frame_id": "f1"},
        ),
        FlowStep(
            step_id="create",
            method="POST",
            path="/sale-order/create",
            body_source='{"items":[{"stockCount":1234}]}',
            params=[stock_param],
            source_meta={"request_id": "req-create", "page_id": "p1", "frame_id": "f1"},
        ),
    ])
    spec.request_facts.requests = [
        RequestFact(
            request_id="req-stock",
            sequence=1,
            method="GET",
            url="/product/stock/get-count",
            path="/product/stock/get-count",
            response_json={"code": 0, "msg": "", "data": 1234.0},
            page_id="p1",
            frame_id="f1",
        ),
        RequestFact(
            request_id="req-create",
            sequence=2,
            method="POST",
            url="/sale-order/create",
            path="/sale-order/create",
            post_data={"items": [{"stockCount": 1234}]},
            page_id="p1",
            frame_id="f1",
        ),
    ]

    _reconcile_captured_value_dependencies(spec)
    spec = sync_flow_spec_models(spec)

    assert any(
        link.source_step_id == "stock"
        and link.source_path == "data"
        and link.target_step_id == "create"
        and link.target_path == "body.items[0].stockCount"
        for link in spec.links
    )
    assert spec.steps[1].params[0].source_kind == "previous_response"


def test_create_form_editable_control_beats_system_name_hint() -> None:
    creator = ParamField(
        path="body.creator",
        key="creator",
        value="operator-1",
        source_kind="unknown",
        source={"kind": "unknown"},
        category="runtime_var",
        exposed_to_user=False,
        editable=False,
        evidence=[{
            "kind": "page_control",
            "control_kind": "text",
            "editable": True,
            "disabled": False,
            "read_only": False,
            "binding_status": "bound",
        }],
    )
    step = FlowStep(
        step_id="create",
        method="POST",
        path="/orders/create",
        params=[
            creator,
            ParamField(path="body.remark", key="remark", value="note"),
        ],
    )
    spec = FlowSpec(steps=[step])

    _apply_create_form_field_contracts(spec)

    assert creator.source_kind == "user_input"
    assert creator.category == "user_param"
    assert creator.exposed_to_user is True
    assert creator.editable is True


def test_edit_form_editable_hydrated_field_beats_audit_name_hint() -> None:
    creator = ParamField(
        path="body.creator",
        key="creator",
        value="operator-1",
        source_kind="previous_response",
        source={
            "kind": "previous_response",
            "step_id": "detail",
            "link_id": "detail-to-update-creator",
            "response_path": "data.creator",
            "allow_caller_override": False,
        },
        category="runtime_var",
        exposed_to_user=False,
        editable=False,
        evidence=[{
            "kind": "page_control",
            "control_kind": "text",
            "editable": True,
            "disabled": False,
            "read_only": False,
            "binding_status": "bound",
        }],
    )
    step = FlowStep(
        step_id="update",
        method="PUT",
        path="/orders/update",
        params=[
            creator,
            ParamField(
                path="body.customerId", key="customerId", value=8,
                source_kind="previous_response",
                source={
                    "kind": "previous_response",
                    "step_id": "detail",
                    "link_id": "detail-to-update-customer",
                    "response_path": "data.customerId",
                },
            ),
            ParamField(
                path="body.remark", key="remark", value="note",
                source_kind="previous_response",
                source={
                    "kind": "previous_response",
                    "step_id": "detail",
                    "link_id": "detail-to-update-remark",
                    "response_path": "data.remark",
                },
            ),
        ],
    )
    spec = FlowSpec.model_construct(
        steps=[step],
        meta={"stage_1_6_contract_version": 2},
    )

    _apply_edit_form_field_contracts(spec)

    assert creator.source_kind == "previous_response"
    assert creator.source["allow_caller_override"] is True
    assert creator.category == "user_param"
    assert creator.exposed_to_user is True
    assert creator.editable is True


def test_edit_form_preserves_pi_source_decision_without_name_override() -> None:
    creator = ParamField(
        path="body.creator",
        key="creator",
        value="operator-1",
        source_kind="user_input",
        source={
            "kind": "user_input",
            "actor": "agent",
            "reason": "Pi classified this exact field as caller input.",
        },
        category="user_param",
        exposed_to_user=True,
        editable=True,
        evidence=[{
            "actor": "agent",
            "kind": "param_source",
            "source_kind": "caller_input",
            "evidence_refs": ["req-update"],
        }],
    )
    step = FlowStep(
        step_id="update",
        method="PUT",
        path="/orders/update",
        params=[
            creator,
            ParamField(
                path="body.customerId", key="customerId", value=8,
                source_kind="previous_response",
                source={
                    "kind": "previous_response", "step_id": "detail",
                    "link_id": "detail-to-update-customer",
                    "response_path": "data.customerId",
                },
            ),
            ParamField(
                path="body.remark", key="remark", value="note",
                source_kind="previous_response",
                source={
                    "kind": "previous_response", "step_id": "detail",
                    "link_id": "detail-to-update-remark",
                    "response_path": "data.remark",
                },
            ),
            ParamField(
                path="body.orderTime", key="orderTime", value=1,
                source_kind="previous_response",
                source={
                    "kind": "previous_response", "step_id": "detail",
                    "link_id": "detail-to-update-time",
                    "response_path": "data.orderTime",
                },
            ),
        ],
    )
    spec = FlowSpec.model_construct(
        steps=[step],
        meta={"stage_1_6_contract_version": 2},
    )

    _apply_edit_form_field_contracts(spec)

    assert creator.source_kind == "user_input"
    assert creator.source["actor"] == "agent"
    assert creator.category == "user_param"
    assert creator.exposed_to_user is True
    assert creator.editable is True


def test_query_editable_control_beats_system_name_hint() -> None:
    template_id = ParamField(
        path="query.templateId",
        key="templateId",
        value=7,
        source_kind="unknown",
        source={"kind": "unknown"},
        category="runtime_var",
        exposed_to_user=False,
        editable=False,
        evidence=[{
            "kind": "page_control",
            "control_kind": "select",
            "editable": True,
            "disabled": False,
            "read_only": False,
            "binding_status": "bound",
        }],
    )
    spec = FlowSpec.model_construct(steps=[FlowStep(
        step_id="list",
        method="GET",
        path="/orders/page",
        params=[template_id],
    )])

    _apply_query_form_field_contracts(spec)

    assert template_id.source_kind == "form_option"
    assert template_id.category == "user_param"
    assert template_id.exposed_to_user is True
    assert template_id.editable is True


def test_editable_selected_projection_beats_audit_name_hint() -> None:
    creator_name = ParamField(
        path="body.creatorName",
        key="creatorName",
        value="operator-1",
        source_kind="selected_option_field",
        source={
            "kind": "selected_option_field",
            "source_field": "name",
            "allow_caller_override": False,
        },
        category="runtime_var",
        exposed_to_user=False,
        editable=False,
        evidence=[{
            "kind": "page_control",
            "control_kind": "text",
            "editable": True,
            "disabled": False,
            "read_only": False,
            "binding_status": "bound",
        }],
    )
    spec = FlowSpec.model_construct(
        steps=[FlowStep(
            step_id="save",
            method="POST",
            path="/orders/save",
            params=[creator_name],
        )],
        meta={"stage_1_6_contract_version": 2},
    )

    _apply_page_rule_caller_override(spec)

    assert creator_name.source_kind == "selected_option_field"
    assert creator_name.source["allow_caller_override"] is True
    assert creator_name.category == "user_param"
    assert creator_name.exposed_to_user is True
    assert creator_name.editable is True


def test_parallel_create_and_edit_fields_share_grounded_business_name() -> None:
    def fields(stock_label: str, *, grounded: bool) -> list[ParamField]:
        return [
            ParamField(path="customerId", key="customerId", label="客户"),
            ParamField(path="items[0].count", key="count", label="数量"),
            ParamField(
                path="items[0].stockCount",
                key="stockCount",
                label=stock_label,
                type="number",
                wire_type="number",
                name_source="dom" if grounded else "auto",
                confidence_tier="linked" if grounded else "clarify",
            ),
        ]

    create = FlowStep(
        step_id="create",
        method="POST",
        path="/orders/create",
        params=fields("stockCount", grounded=False),
        source_meta={"page_id": "orders", "frame_id": "main"},
    )
    update = FlowStep(
        step_id="update",
        method="PUT",
        path="/orders/update",
        params=fields("库存", grounded=True),
        source_meta={"page_id": "orders", "frame_id": "main"},
    )
    unrelated = FlowStep(
        step_id="customer-create",
        method="POST",
        path="/customers/create",
        params=fields("stockCount", grounded=False),
        source_meta={"page_id": "orders", "frame_id": "main"},
    )
    spec = FlowSpec(
        steps=[create, update, unrelated],
        meta={"stage_1_6_contract_version": 2},
    )

    _apply_mechanical_field_contracts(spec)

    create_stock = next(param for param in create.params if param.key == "stockCount")
    assert create_stock.label == "库存"
    assert create_stock.name_source == "recorded_parallel_field"
    assert create_stock.type == "number"
    unrelated_stock = next(
        param for param in unrelated.params if param.key == "stockCount"
    )
    assert unrelated_stock.label == "stockCount"


def test_parallel_edit_form_recovers_missing_editable_control_contract() -> None:
    field_values = {"accountId": 1, "productId": 10}

    def control_param(path: str, key: str, label: str, *, required: bool) -> ParamField:
        return ParamField(
            path=path,
            key=key,
            label=label,
            value=field_values.get(key, 2),
            type="enum" if key.endswith("Id") else "number",
            wire_type="number",
            required=required,
            name_source="dom",
            confidence_tier="grounded",
            evidence=[{
                "kind": "page_control",
                "source": "recorder_dom",
                "control_kind": "select" if key.endswith("Id") else "number",
                "editable": True,
                "disabled": False,
                "read_only": False,
                "required": required,
                "required_observed": required,
            }],
        )

    create = FlowStep(
        step_id="create",
        method="POST",
        path="/orders/create",
        params=[
            control_param("accountId", "accountId", "结算账户", required=False),
            control_param("items[0].productId", "productId", "产品名称", required=True),
            control_param("items[0].count", "count", "数量", required=True),
            control_param("items[0].productPrice", "productPrice", "产品单价", required=False),
            control_param("items[0].taxPercent", "taxPercent", "税率", required=False),
        ],
        source_meta={
            "request_id": "req-create", "sequence": 3,
            "page_id": "orders", "frame_id": "main",
        },
    )
    update_params = [
        ParamField(
            path=path,
            key=key,
            label=key,
            value=field_values.get(key, 2),
            type="number",
            wire_type="number",
            source_kind="previous_response",
            source={
                "kind": "previous_response",
                "link_id": f"link-{key}",
                "step_id": "detail",
                "response_path": f"data.{path}",
                "allow_caller_override": False,
            },
        )
        for path, key in (
            ("accountId", "accountId"),
            ("items[0].productId", "productId"),
            ("items[0].count", "count"),
            ("items[0].productPrice", "productPrice"),
            ("items[0].taxPercent", "taxPercent"),
        )
    ]
    update = FlowStep(
        step_id="update",
        method="PUT",
        path="/orders/update",
        params=update_params,
        source_meta={
            "request_id": "req-update", "sequence": 6,
            "page_id": "orders", "frame_id": "main",
        },
    )
    spec = FlowSpec(
        steps=[create, update],
        capabilities=[FlowCapability(
            name="update_order",
            step_ids=["update"],
            request_refs=[CapabilityRequestRef(
                request_id="req-stale-option",
                usage="option_source",
            )],
        )],
        meta={"stage_1_6_contract_version": 2},
    )
    spec.request_facts.requests = [
        RequestFact(
            request_id="req-product-options",
            method="GET",
            path="/products/simple-list",
            url="/products/simple-list",
            response_json={"data": [
                {"id": 10, "name": "Widget"},
                {"id": 11, "name": "Gadget"},
            ]},
            sequence=4,
            page_id="orders",
            frame_id="main",
        ),
        RequestFact(
            request_id="req-account-options",
            method="GET",
            path="/accounts/simple-list",
            url="/accounts/simple-list",
            response_json={"data": [
                {"id": 1, "name": "Main"},
                {"id": 2, "name": "Backup"},
            ]},
            sequence=5,
            page_id="orders",
            frame_id="main",
        ),
    ]
    spec.request_facts.analysis = {
        request_id: RequestAnalysis(
            request_id=request_id, role="read_option", keep=True,
        )
        for request_id in ("req-product-options", "req-account-options")
    }

    _apply_mechanical_field_contracts(spec)

    account = next(param for param in update.params if param.key == "accountId")
    product = next(param for param in update.params if param.key == "productId")
    count = next(param for param in update.params if param.key == "count")
    assert account.label == "结算账户"
    assert account.exposed_to_user is True
    assert account.editable is True
    assert account.type == "enum"
    account_options = account.source.get("option_source") or account.source
    assert account_options["source_request_id"] == "req-account-options"
    assert any(
        item.get("source") == "recorded_parallel_form"
        for item in account.evidence
        if isinstance(item, dict)
    )
    if account.source_kind == "previous_response":
        assert account.source["allow_caller_override"] is True
    assert product.label == "产品名称"
    assert product.type == "enum"
    assert product.required is True
    product_options = product.source.get("option_source") or product.source
    assert product_options["source_request_id"] == "req-product-options"
    assert any(
        item.get("source") == "recorded_parallel_form"
        for item in product.evidence
        if isinstance(item, dict)
    )
    assert not any(
        item.get("source") == "recorded_parallel_form"
        for item in count.evidence
        if isinstance(item, dict)
    )


def test_readonly_recorded_default_does_not_override_confirmed_edit_hydration() -> None:
    stock_param = ParamField(
        path="items[0].stockCount",
        key="stockCount",
        value=120.001,
        type="number",
        wire_type="number",
        category="system_const",
        source_kind="constant",
        source={"kind": "recorded_control_default"},
    )
    spec = FlowSpec(
        steps=[
            FlowStep(
                step_id="detail",
                method="GET",
                path="/sale-order/get",
                response_json={"data": {"items": [{"stockCount": 120.001}]}},
            ),
            FlowStep(
                step_id="update",
                method="PUT",
                path="/sale-order/update",
                body_source='{"items":[{"stockCount":120.001}]}',
                params=[stock_param],
            ),
        ],
        links=[FlowLink(
            source_step_id="detail",
            source_path="data.items[0].stockCount",
            target_step_id="update",
            target_path="items[0].stockCount",
            confirmed=True,
            confidence=0.99,
            evidence={
                "kind": "record_hydration",
                "match_count": 3,
                "identity_paths": ["data.id"],
            },
            meta={"captured_record_hydration": True},
        )],
        meta={"stage_1_6_contract_version": 2},
    )

    spec = sync_flow_spec_models(spec)

    hydrated = spec.steps[1].params[0]
    assert hydrated.source_kind == "previous_response"
    assert hydrated.source["response_path"] == "data.items[0].stockCount"
    assert hydrated.editable is False


def test_existing_textarea_evidence_refreshes_numeric_looking_string_type() -> None:
    param = ParamField(
        path="body.remark",
        key="remark",
        label="备注",
        value="1",
        type="number",
        wire_type="string",
        source_kind="user_input",
        evidence=[{
            "kind": "page_control",
            "control_kind": "textarea",
            "request_path": "body.remark",
            "binding_status": "bound",
        }],
    )
    spec = FlowSpec(
        steps=[FlowStep(
            step_id="create",
            method="POST",
            path="/sale-order/create",
            body_source='{"remark":"1"}',
            params=[param],
            source_meta={"request_id": "req-create"},
        )],
        meta={"stage_1_6_contract_version": 2},
    )
    spec.request_facts.requests = [RequestFact(
        request_id="req-create",
        method="POST",
        url="/sale-order/create",
        path="/sale-order/create",
        post_data={"remark": "1"},
    )]
    spec.request_facts.field_evidence = [{
        "binding_status": "bound",
        "request_id": "req-create",
        "wire_path": "body.remark",
        "label": "备注",
        "control_kind": "textarea",
        "editable": True,
        "disabled": False,
        "read_only": False,
    }]

    _rebind_saved_field_evidence(spec)

    assert param.type == "string"


def test_weak_form_order_binding_moves_control_to_matching_alias() -> None:
    creator = ParamField(
        path="creator",
        key="creator",
        label="备注",
        value="1",
        type="number",
        wire_type="number",
        source_kind="previous_response",
        source={"kind": "previous_response", "response_path": "data.creator"},
        editable=False,
        exposed_to_user=False,
        evidence=[{
            "kind": "page_control",
            "evidence_id": "field-remark",
            "control_kind": "textarea",
            "request_path": "creator",
            "binding_status": "bound",
        }],
    )
    remark = ParamField(
        path="remark",
        key="remark",
        label="remark",
        value="1",
        type="number",
        wire_type="string",
        source_kind="user_input",
    )
    spec = FlowSpec(
        steps=[FlowStep(
            step_id="edit",
            method="PUT",
            path="/sale-order/update",
            body_source='{"creator":"1","remark":"1"}',
            params=[creator, remark],
            source_meta={"request_id": "req-edit"},
        )],
        meta={"stage_1_6_contract_version": 2},
    )
    spec.request_facts.requests = [RequestFact(
        request_id="req-edit",
        method="PUT",
        url="/sale-order/update",
        path="/sale-order/update",
        post_data={"creator": "1", "remark": "1"},
    )]
    spec.request_facts.field_evidence = [{
        "binding_status": "bound",
        "binding_method": "unique_remaining_form_order",
        "request_id": "req-edit",
        "wire_path": "creator",
        "label": "备注",
        "field": "备注",
        "field_aliases": ["remark"],
        "control_kind": "textarea",
        "editable": True,
        "disabled": False,
        "read_only": False,
        "evidence_id": "field-remark",
    }]

    _rebind_saved_field_evidence(spec)
    _apply_mechanical_field_contracts(spec)

    assert creator.label == "creator"
    assert creator.type == "string"
    assert not any(
        item.get("evidence_id") == "field-remark"
        for item in creator.evidence
        if isinstance(item, dict)
    )
    assert remark.label == "备注"
    assert remark.type == "string"
    assert any(
        item.get("evidence_id") == "field-remark"
        for item in remark.evidence
        if isinstance(item, dict)
    )


def _sale_order_option_spec(*, target_source_kind: str = "unknown") -> FlowSpec:
    product_rows = [
        {"id": 5, "name": "联想thinkpad", "unitName": "份", "barCode": "313131"},
        {"id": 6, "name": "apple", "unitName": "台", "barCode": "616161"},
    ]
    product_id = ParamField(
        path="body.items[0].productId",
        key="productId",
        label="产品",
        value=5,
        type="number",
        wire_type="number",
        source_kind=target_source_kind,
        source={
            "kind": target_source_kind,
            **(
                {"link_id": "detail-link", "allow_caller_override": False}
                if target_source_kind == "previous_response" else {}
            ),
        },
        evidence=[{
            "kind": "page_control",
            "control_kind": "select",
            "binding_status": "bound",
            "editable": True,
            "request_path": "body.items[0].productId",
        }],
    )
    target = FlowStep(
        step_id="create",
        method="POST",
        path="/sale-order/create",
        params=[
            ParamField(
                path="body.items[0].id",
                key="id",
                value=5,
                type="number",
                wire_type="number",
                source_kind="previous_response",
                source={"kind": "previous_response", "link_id": "line-id"},
            ),
            product_id,
            ParamField(
                path="body.items[0].productUnitName",
                key="productUnitName",
                label="单位",
                value="份",
                type="string",
                wire_type="string",
                source_kind="constant",
                source={"kind": "recorded_control_default"},
            ),
            ParamField(
                path="body.items[0].productBarCode",
                key="productBarCode",
                label="条码",
                value="313131",
                type="string",
                wire_type="string",
                source_kind="constant",
                source={"kind": "recorded_control_default"},
            ),
        ],
        source_meta={"request_id": "req-create", "page_id": "p1", "frame_id": "f1"},
    )
    source = FlowStep(
        step_id="product-options",
        method="GET",
        path="/product/simple-list",
        response_json={"data": product_rows},
        semantic_role="read_option",
        source_meta={
            "request_id": "req-product-options",
            "role": "read_option",
            "page_id": "p1",
            "frame_id": "f1",
        },
    )
    spec = FlowSpec(
        steps=[source, target],
        capabilities=[FlowCapability(
            name="create_sale_order",
            kind="create",
            step_ids=["create"],
            request_refs=[
                {"request_id": "req-product-options", "step_id": "product-options", "usage": "option_source"},
                {"request_id": "req-create", "step_id": "create", "usage": "execute"},
            ],
        )],
        meta={"stage_1_6_contract_version": 2},
    )
    spec.request_facts.requests = [RequestFact(
        request_id="req-product-options",
        method="GET",
        url="/product/simple-list",
        path="/product/simple-list",
        response_json={"data": product_rows},
        page_id="p1",
        frame_id="f1",
    )]
    spec.request_facts.analysis = {
        "req-product-options": RequestAnalysis(
            request_id="req-product-options",
            role="read_option",
            keep=True,
            confidence=0.99,
        ),
    }
    return spec


def test_exact_option_scope_binds_api_source_and_selected_row_projections() -> None:
    spec = _sale_order_option_spec()

    _repair_structural_option_bindings(spec)
    _infer_selected_option_row_fields(spec)

    by_key = {param.key: param for param in spec.steps[1].params}
    assert by_key["productId"].source_kind == "api_option"
    assert by_key["productId"].source["source_request_id"] == "req-product-options"
    assert by_key["productUnitName"].source_kind == "selected_option_field"
    assert by_key["productUnitName"].source["response_path"] == "unitName"
    assert by_key["productBarCode"].source_kind == "selected_option_field"
    assert by_key["productBarCode"].source["response_path"] == "barCode"
    assert by_key["productUnitName"].source["selector_path"] == "body.items[0].productId"
    assert by_key["id"].source_kind != "selected_option_field"


def test_selected_row_projection_does_not_cross_structural_groups() -> None:
    spec = _sale_order_option_spec()
    target = spec.steps[1]
    target.params.append(ParamField(
        path="body.customerId",
        key="customerId",
        label="客户",
        value=5,
        type="number",
        wire_type="number",
        source_kind="api_option",
        source={
            "kind": "api_option",
            "source_url": "/product/simple-list",
            "source_request_id": "req-product-options",
            "value_key": "id",
            "label_key": "name",
        },
    ))

    _repair_structural_option_bindings(spec)

    customer = next(param for param in target.params if param.key == "customerId")
    assert customer.source_kind != "selected_option_field"
    assert customer.source.get("selector_path") != "body.items[0].productId"


def test_selected_row_projection_does_not_guess_from_boring_scalar_values() -> None:
    row = {"id": 1, "name": "管理员"}

    assert _best_option_projection_path(row, "body.remark", "1") == ""
    assert _best_option_projection_path(row, "body.id", 1) == "id"


def test_selected_row_projection_preserves_pi_source_decision() -> None:
    spec = _sale_order_option_spec()
    _repair_structural_option_bindings(spec)
    unit = next(
        param for param in spec.steps[1].params
        if param.key == "productUnitName"
    )
    unit.category = "user_param"
    unit.source_kind = "user_input"
    unit.source = {
        "kind": "user_input",
        "actor": "agent",
        "reason": "Pi classified this exact target field as caller input.",
    }
    unit.exposed_to_user = True
    unit.editable = True
    unit.evidence.append({
        "actor": "agent",
        "kind": "param_source",
        "source_kind": "caller_input",
        "evidence_refs": ["req-create", "req-product-options"],
    })

    _infer_selected_option_row_fields(spec)

    assert unit.source_kind == "user_input"
    assert unit.source["actor"] == "agent"
    assert unit.category == "user_param"
    assert unit.exposed_to_user is True


def test_computed_inference_preserves_pi_source_decision() -> None:
    total = ParamField(
        path="body.totalPrice",
        key="totalPrice",
        value=10,
        type="number",
        wire_type="number",
        category="user_param",
        source_kind="user_input",
        source={
            "kind": "user_input",
            "actor": "agent",
            "reason": "Pi classified this exact field as caller input.",
        },
        exposed_to_user=True,
        editable=True,
        evidence=[{
            "actor": "agent",
            "kind": "param_source",
            "source_kind": "caller_input",
            "evidence_refs": ["req-create"],
        }],
    )
    spec = FlowSpec.model_construct(steps=[FlowStep(
        step_id="create",
        method="POST",
        path="/orders/create",
        params=[
            ParamField(
                path="body.productPrice", key="productPrice", value=5,
                type="number", wire_type="number", source_kind="user_input",
            ),
            ParamField(
                path="body.count", key="count", value=2,
                type="number", wire_type="number", source_kind="user_input",
            ),
            total,
        ],
    )])

    _infer_arithmetic_computed_fields(spec)

    assert total.source_kind == "user_input"
    assert total.source["actor"] == "agent"
    assert total.category == "user_param"
    assert total.exposed_to_user is True


def test_row_command_preserves_pi_source_decision() -> None:
    status = ParamField(
        path="query.status",
        key="status",
        value=20,
        type="number",
        wire_type="number",
        category="user_param",
        source_kind="user_input",
        source={
            "kind": "user_input",
            "actor": "agent",
            "reason": "Pi classified this exact field as caller input.",
        },
        exposed_to_user=True,
        editable=True,
        evidence=[{
            "actor": "agent",
            "kind": "param_source",
            "source_kind": "caller_input",
            "evidence_refs": ["req-command"],
        }],
    )
    spec = FlowSpec.model_construct(steps=[FlowStep(
        step_id="command",
        method="PUT",
        path="/orders/update-status",
        params=[
            ParamField(
                path="query.id",
                key="id",
                value=70,
                source_kind="selected_record_identity",
            ),
            status,
        ],
    )])

    _apply_row_command_field_contracts(spec)

    assert status.source_kind == "user_input"
    assert status.source["actor"] == "agent"
    assert status.category == "user_param"
    assert status.exposed_to_user is True


def test_pi_field_workset_exposes_independent_axes_and_multi_field_projections() -> None:
    spec = _sale_order_option_spec()
    _repair_structural_option_bindings(spec)
    _infer_selected_option_row_fields(spec)

    snapshot = _semantic_fact_snapshot(spec)
    by_path = {
        item["wire_path"]: item
        for item in snapshot["field_decision_workset"]
    }

    unit = by_path["body.items[0].productUnitName"]
    barcode = by_path["body.items[0].productBarCode"]
    assert set(unit["axes"]) == {
        "name", "type", "source", "requiredness", "ownership",
    }
    unit_projection = next(
        item for item in unit["axes"]["source"]["candidates"]
        if item.get("origin_kind") == "selected_option_field"
    )
    barcode_projection = next(
        item for item in barcode["axes"]["source"]["candidates"]
        if item.get("origin_kind") == "selected_option_field"
    )
    assert unit_projection["source_request_id"] == "req-product-options"
    assert barcode_projection["source_request_id"] == "req-product-options"
    assert unit_projection["response_path"] == "unitName"
    assert barcode_projection["response_path"] == "barCode"
    assert unit_projection["selector_path"] == "body.items[0].productId"
    assert barcode_projection["selector_path"] == "body.items[0].productId"


def test_pi_field_workset_keeps_unmaterialized_editable_controls_visible() -> None:
    spec = FlowSpec()
    spec.request_facts.field_evidence = [{
        "evidence_id": "field-customer",
        "field_identity_id": "customer-select",
        "label": "客户",
        "field_aliases": ["customerId"],
        "control_kind": "select",
        "editable": True,
        "disabled": False,
        "required_observed": True,
        "binding_status": "unbound",
        "binding_candidates": ["body.customerId", "body.customerName"],
        "page_id": "p1",
        "frame_id": "f1",
    }]
    spec.request_facts.option_sources = [{
        "kind": "api_response",
        "request_id": "req-customer-options",
        "method": "GET",
        "path": "/customers/simple-list",
        "page_id": "p1",
        "frame_id": "f1",
        "response_schema": {
            "type": "object",
            "properties": {"data": {"type": "array"}},
        },
    }]

    snapshot = _semantic_fact_snapshot(spec)

    assert snapshot["field_decision_count"] == 1
    field = snapshot["field_decision_workset"][0]
    assert field["field_identity_id"] == "customer-select"
    assert field["wire_path"] == ""
    assert field["binding_candidates"] == ["body.customerId", "body.customerName"]
    assert field["axes"]["name"]["candidates"][0]["value"] == "客户"
    assert field["axes"]["requiredness"]["evidence"][0]["required"] is True
    assert field["axes"]["ownership"]["editable"] is True
    assert field["axes"]["source"]["candidates"] == [{
        "origin_kind": "api_option_candidate",
        "source_request_id": "req-customer-options",
        "method": "GET",
        "path": "/customers/simple-list",
        "actor": "capture",
    }]


def test_selected_option_row_projects_multiple_editable_siblings_without_hiding_them() -> None:
    spec = _sale_order_option_spec()
    product_rows = [
        {
            "id": 5,
            "name": "联想thinkpad",
            "unitName": "份",
            "barCode": "313131",
            "productPrice": 2,
            "taxPercent": 13,
        },
        {
            "id": 6,
            "name": "apple",
            "unitName": "台",
            "barCode": "616161",
            "productPrice": 3,
            "taxPercent": 17,
        },
    ]
    spec.steps[0].response_json = {"data": product_rows}
    spec.request_facts.requests[0].response_json = {"data": product_rows}
    spec.steps[1].params.extend([
        ParamField(
            path="body.items[0].productPrice",
            key="productPrice",
            label="产品单价",
            value=2,
            type="number",
            wire_type="number",
            required=True,
            source_kind="unknown",
            source={"kind": "unknown"},
            category="runtime_var",
            exposed_to_user=False,
            editable=False,
            evidence=[{
                "kind": "page_control",
                "control_kind": "number",
                "disabled": False,
                "read_only": False,
                "required_observed": True,
            }, {
                "kind": "page_required",
                "binding_status": "bound",
            }],
        ),
        ParamField(
            path="body.items[0].taxPercent",
            key="taxPercent",
            label="税率",
            value=13,
            type="number",
            wire_type="number",
            required=False,
            source_kind="previous_response",
            source={
                "kind": "previous_response",
                "step_id": "edit-detail",
                "response_path": "data.items[0].taxPercent",
            },
            category="runtime_var",
            exposed_to_user=False,
            editable=False,
            evidence=[{
                "kind": "page_control",
                "control_kind": "number",
                "disabled": False,
                "read_only": False,
            }],
        ),
    ])

    _repair_structural_option_bindings(spec)
    _apply_mechanical_field_contracts(spec)
    sync_flow_spec_models(spec)

    by_key = {param.key: param for param in spec.steps[1].params}
    for key, response_path in {
        "productUnitName": "unitName",
        "productBarCode": "barCode",
        "productPrice": "productPrice",
        "taxPercent": "taxPercent",
    }.items():
        assert by_key[key].source_kind == "selected_option_field"
        assert by_key[key].source["source_request_id"] == "req-product-options"
        assert by_key[key].source["response_path"] == response_path

    for key in ("productPrice", "taxPercent"):
        assert by_key[key].source["allow_caller_override"] is True
        assert by_key[key].category == "user_param"
        assert by_key[key].exposed_to_user is True
        assert by_key[key].editable is True
    assert by_key["productPrice"].required is True
    assert by_key["taxPercent"].required is False
    assert by_key["productUnitName"].exposed_to_user is False
    assert by_key["productBarCode"].exposed_to_user is False

    selector = next(
        binding for binding in spec.steps[1].selects
        if binding.path == "body.items[0].productId"
    )
    assert selector.field_projections == {
        "body.items[0].productUnitName": "unitName",
        "body.items[0].productBarCode": "barCode",
        "body.items[0].productPrice": "productPrice",
        "body.items[0].taxPercent": "taxPercent",
    }


def test_form_option_is_upgraded_to_executable_api_option() -> None:
    spec = _sale_order_option_spec(target_source_kind="form_option")

    _repair_structural_option_bindings(spec)

    product_id = next(param for param in spec.steps[1].params if param.key == "productId")
    assert product_id.source_kind == "api_option"
    assert product_id.source["source_request_id"] == "req-product-options"


def test_edit_hydration_keeps_prefill_and_overlays_executable_option_source() -> None:
    spec = _sale_order_option_spec(target_source_kind="previous_response")
    product_id = next(param for param in spec.steps[1].params if param.key == "productId")
    product_id.source_kind = "previous_response"
    product_id.source = {
        "kind": "previous_response",
        "link_id": "detail-link",
        "step_id": "detail",
        "response_path": "data.items[0].productId",
        "allow_caller_override": False,
    }
    product_id.exposed_to_user = False
    product_id.editable = False

    _repair_structural_option_bindings(spec)

    assert product_id.source_kind == "previous_response"
    assert product_id.source["allow_caller_override"] is True
    assert product_id.source["option_source"]["source_request_id"] == "req-product-options"
    assert product_id.exposed_to_user is True
    assert product_id.editable is True


def test_url_grounded_option_source_uses_nearest_prior_recorded_occurrence() -> None:
    target = FlowStep(
        step_id="search",
        method="GET",
        path="/orders?pageNo=1&productId=4",
        source_meta={
            "request_id": "req-search",
            "request_index": 10,
            "page_id": "p1",
            "frame_id": "f1",
        },
        params=[ParamField(
            path="query.productId",
            key="productId",
            label="产品",
            value="4",
            type="enum",
            wire_type="string",
            source_kind="api_option",
            source={
                "kind": "api_option",
                "source_url": "/product/simple-list",
                "value_key": "id",
                "label_key": "name",
                "id_path": "query.productId",
            },
        )],
    )
    spec = FlowSpec(steps=[target])
    spec.request_facts.requests = [
        RequestFact(
            request_id="req-options-before",
            request_index=9,
            method="GET",
            path="/product/simple-list",
            url="/product/simple-list",
            response_json={"data": [{"id": 4, "name": "ThinkPad"}]},
            page_id="p1",
            frame_id="f1",
        ),
        RequestFact(
            request_id="req-options-after",
            request_index=11,
            method="GET",
            path="/product/simple-list",
            url="/product/simple-list",
            response_json={"data": [{"id": 4, "name": "ThinkPad"}]},
            page_id="p1",
            frame_id="f1",
        ),
    ]

    repaired = _restore_executable_option_request_ids(spec)

    assert repaired == 1
    assert target.params[0].source["source_request_id"] == "req-options-before"


def test_creator_field_matches_user_directory_semantics() -> None:
    assert "person" in _option_binding_semantic_families("creator 创建人")
    assert "person" in _option_binding_semantic_families("/system/user/simple-list")


def test_constant_row_metadata_does_not_make_option_source_ambiguous() -> None:
    spec = _sale_order_option_spec(target_source_kind="page_enum")
    rows = spec.steps[0].response_json["data"]
    for row in rows:
        row["avatar"] = "https://example.invalid/shared-avatar.png"
    spec.request_facts.requests[0].response_json = spec.steps[0].response_json
    spec.request_facts.option_sources = [{
        "kind": "page_enum_options",
        "options": {
            "产品": {
                "field_key": "产品",
                "field_aliases": ["productId"],
                "page_id": "p1",
                "frame_id": "f1",
                "control_kind": "select",
                "snapshot_truncated": False,
                "options": [
                    {"label": "联想thinkpad"},
                    {"label": "apple"},
                ],
            },
        },
    }]

    _repair_structural_option_bindings(spec)

    product_id = next(param for param in spec.steps[1].params if param.key == "productId")
    assert product_id.source_kind == "api_option"
    assert product_id.source["source_request_id"] == "req-product-options"


def test_inferred_option_source_is_capability_member_and_survives_scoping() -> None:
    spec = _sale_order_option_spec()
    _repair_structural_option_bindings(spec)
    capability = spec.capabilities[0]
    capability.nodes = [{"type": "call", "step_id": "create"}]
    capability.request_refs = [
        ref for ref in capability.request_refs if ref.usage == "execute"
    ]

    sync_capability_scoped_views(spec)

    option_refs = [
        ref for ref in capability.request_refs
        if ref.request_id == "req-product-options" and ref.usage == "option_source"
    ]
    assert len(option_refs) == 1
    capability.request_refs.append(option_refs[0].model_copy(update={
        "step_id": "product-options",
    }))

    sync_capability_scoped_views(spec)

    assert len([
        ref for ref in capability.request_refs
        if ref.request_id == "req-product-options" and ref.usage == "option_source"
    ]) == 1

    # The release boundary also derives this closure from field ownership, so
    # an imported/stale capability cannot prune a still-executable source fact.
    capability.request_refs = [
        ref for ref in capability.request_refs if ref.usage == "execute"
    ]
    scoped = _capability_spec(spec, capability)
    assert {fact.request_id for fact in scoped.request_facts.requests} == {
        "req-product-options",
    }


def test_prepared_capability_view_does_not_rematerialize_flow(monkeypatch) -> None:
    spec = _sale_order_option_spec()

    def fail_sync(_spec):
        raise AssertionError("prepared capability view rematerialized the FlowSpec")

    monkeypatch.setattr(capability_views, "sync_flow_spec_models", fail_sync)

    views = capability_views._capability_contract_views(spec, _prepared=True)

    assert len(views) == 1


def test_unmapped_query_enum_keeps_page_choices_and_detail_id_is_record_selector() -> None:
    query = FlowStep(
        step_id="search",
        method="GET",
        path="/orders?pageNo=1&outStatus=0",
        source_meta={"request_id": "req-search", "role": "business_get"},
        params=[ParamField(
            path="query.outStatus",
            key="outStatus",
            label="出库状态",
            value="0",
            type="enum",
            wire_type="string",
            category="user_param",
            source_kind="page_enum",
            source={"kind": "page_enum", "enum_confirmed": False},
            enum_options=["未出库", "部分出库", "全部出库"],
            exposed_to_user=True,
            editable=True,
            required=False,
        )],
    )
    detail = FlowStep(
        step_id="detail",
        method="GET",
        path="/orders/get?id=67",
        source_meta={"request_id": "req-detail", "role": "business_get"},
        params=[ParamField(
            path="query.id",
            key="id",
            label="id",
            value="67",
            type="number",
            wire_type="string",
            source_kind="user_input",
            source={"kind": "sample"},
            exposed_to_user=True,
            editable=True,
            required=False,
        )],
    )
    spec = FlowSpec(steps=[query, detail])

    _apply_query_form_field_contracts(spec)

    out_status = query.params[0]
    assert out_status.source_kind == "page_enum"
    assert out_status.type == "enum"
    assert out_status.enum_options == ["未出库", "部分出库", "全部出库"]
    assert out_status.source["enum_confirmed"] is False
    query.selects = [SelectBinding(
        param="outStatus",
        path="query.outStatus",
        id_path="query.outStatus",
        options=["未出库", "部分出库", "全部出库"],
        enum_confirmed=False,
    )]
    out_status.source_kind = "page_enum"
    out_status.source = {
        **out_status.source,
        "kind": "page_enum",
        "enum_confirmed": False,
    }
    out_status.type = "enum"
    out_status.enum_options = ["未出库", "部分出库", "全部出库"]

    _apply_query_form_field_contracts(spec)

    assert out_status.source_kind == "page_enum"
    assert out_status.type == "enum"
    assert out_status.enum_options == ["未出库", "部分出库", "全部出库"]
    assert out_status.source["enum_confirmed"] is False
    assert len(query.selects) == 1
    record_id = detail.params[0]
    assert record_id.label == "记录"
    assert record_id.source_kind == "selected_record_identity"
    assert record_id.required is True


def test_missing_public_action_is_nonblocking_and_left_for_grounded_fallback() -> None:
    from dano.execution.page.capability_semantic import _required_public_action_request_ids

    spec = FlowSpec()
    spec.request_facts.requests = [
        RequestFact(
            request_id="req-create",
            method="POST",
            path="/sale-order/create",
            url="/sale-order/create",
            trigger_action_id="save-create",
            post_data={"id": 1},
        ),
        RequestFact(
            request_id="req-refresh",
            method="GET",
            path="/sale-order/page",
            url="/sale-order/page",
            trigger_action_id="save-create",
        ),
        RequestFact(
            request_id="req-detail",
            method="GET",
            path="/sale-order/get",
            url="/sale-order/get?id=67",
            query={"id": "67"},
            trigger_action_id="open-detail",
        ),
        RequestFact(
            request_id="req-edit-hydration",
            method="GET",
            path="/sale-order/get",
            url="/sale-order/get?id=67",
            query={"id": "67"},
            trigger_action_id="open-edit",
        ),
    ]
    spec.request_facts.analysis = {
        "req-create": RequestAnalysis(request_id="req-create", role="business_write", keep=True, confidence=0.99),
        "req-refresh": RequestAnalysis(request_id="req-refresh", role="business_get", keep=True, confidence=0.99),
        "req-detail": RequestAnalysis(request_id="req-detail", role="business_get", keep=True, confidence=0.99),
        "req-edit-hydration": RequestAnalysis(request_id="req-edit-hydration", role="read_context", keep=True, confidence=0.99),
    }

    required = _required_public_action_request_ids(spec)

    assert required == {"req-create", "req-detail"}

    incomplete_plan = {
        "business_understanding": {"business_name": "销售订单", "summary": "管理销售订单"},
        "capabilities": [{
            "name": "create_sale_order",
            "title": "新增销售订单",
            "kind": "create",
            "anchor_step_id": "req-create",
            "request_refs": [{
                "request_id": "req-create",
                "step_id": "req-create",
                "usage": "execute",
            }],
        }],
        "unresolved_items": [],
    }
    spec.meta = {
        "capability_model": {
            "status": "awaiting_materialization",
            "proposal_gate": {"accepted": True},
            "semantic_coverage": {"complete": True},
            "submitted_semantic_plan": incomplete_plan,
            "semantic_plan": incomplete_plan,
        },
    }
    assert recording_capability_plan_complete(spec)


def test_write_refresh_and_initial_read_are_not_extra_public_capabilities() -> None:
    spec = FlowSpec(
        steps=[
            FlowStep(
                step_id="initial-list",
                method="GET",
                path="/sale-order/page",
                source_meta={"request_id": "req-initial", "role": "business_get"},
            ),
            FlowStep(
                step_id="create",
                method="POST",
                path="/sale-order/create",
                source_meta={"request_id": "req-create", "role": "business_write"},
            ),
        ],
        capabilities=[FlowCapability(
            name="create_sale_order",
            title="新增销售订单",
            kind="create",
            step_ids=["create"],
            request_refs=[{
                "request_id": "req-create",
                "step_id": "create",
                "usage": "execute",
            }],
            nodes=[{"node_id": "call-create", "type": "call", "step_id": "create"}],
            confirmed=True,
        )],
    )
    spec.request_facts.requests = [
        RequestFact(
            request_id="req-initial",
            method="GET",
            path="/sale-order/page",
            url="/sale-order/page",
        ),
        RequestFact(
            request_id="req-create",
            method="POST",
            path="/sale-order/create",
            url="/sale-order/create",
            post_data={"id": 1},
            trigger_action_id="save-create",
        ),
        RequestFact(
            request_id="req-refresh",
            method="GET",
            path="/sale-order/page",
            url="/sale-order/page",
            trigger_action_id="save-create",
        ),
    ]
    spec.request_facts.analysis = {
        "req-initial": RequestAnalysis(request_id="req-initial", role="business_get", keep=True),
        "req-create": RequestAnalysis(request_id="req-create", role="business_write", keep=True),
        "req-refresh": RequestAnalysis(request_id="req-refresh", role="business_get", keep=True),
    }
    spec.request_facts.usage = {
        "req-initial": RequestUsage(
            request_id="req-initial",
            materialized_step_id="initial-list",
            state="materialized",
        ),
        "req-create": RequestUsage(
            request_id="req-create",
            materialized_step_id="create",
            state="materialized",
        ),
    }

    integrity = _capability_validation_report(spec)["materialization_integrity"]

    assert not any(
        item["target"].get("request_id") == "req-refresh"
        for item in integrity["errors"]
    )
    assert not any(
        item["target"].get("request_id") == "req-initial"
        for item in integrity["errors"]
    )


def test_unused_optional_file_picker_does_not_block_unrelated_capabilities() -> None:
    spec = FlowSpec()
    spec.request_facts.field_evidence = [{
        "evidence_id": "file-snapshot",
        "control_kind": "file",
        "op": "snapshot",
        "required": False,
        "file_count": 0,
        "files": [],
        "unsupported_execution": True,
    }]

    snapshot_report = _capability_validation_report(spec)

    assert not any(
        item["code"] == "unsupported_file_execution"
        for item in snapshot_report["materialization_integrity"]["errors"]
    )

    spec.request_facts.field_evidence[0].update({
        "op": "upload",
        "file_count": 1,
        "filename": "contract.pdf",
    })
    upload_report = _capability_validation_report(spec)

    assert any(
        item["code"] == "unsupported_file_execution"
        for item in upload_report["materialization_integrity"]["errors"]
    )


def test_caller_facing_id_wire_name_requires_a_business_label() -> None:
    spec = FlowSpec(steps=[FlowStep(
        step_id="create",
        method="POST",
        path="/sale-order/create",
        source_meta={"request_id": "req-create", "role": "business_write"},
        params=[ParamField(
            path="accountId",
            key="accountId",
            label="accountId",
            value=2,
            type="number",
            wire_type="number",
            category="user_param",
            source_kind="user_input",
            source={"kind": "user_input"},
            exposed_to_user=True,
            editable=True,
            required=False,
        )],
    )])
    spec.request_facts.requests = [RequestFact(
        request_id="req-create",
        method="POST",
        path="/sale-order/create",
        url="/sale-order/create",
        post_data={"accountId": 2},
    )]
    spec.request_facts.analysis = {
        "req-create": RequestAnalysis(
            request_id="req-create",
            role="business_write",
            keep=True,
            confidence=0.99,
        ),
    }
    plan = {
        "business_understanding": {"business_name": "销售订单", "summary": "新增销售订单"},
        "capabilities": [{
            "name": "create_sale_order",
            "title": "新增销售订单",
            "kind": "create",
            "anchor_step_id": "create",
            "request_refs": [{
                "request_id": "req-create",
                "step_id": "create",
                "usage": "execute",
            }],
        }],
        "unresolved_items": [],
    }

    coverage = _semantic_plan_coverage(spec, {"semantic_plan": plan})

    assert "field_axis_contract" in coverage["missing"]
    assert coverage["field_axis_gaps"] == [{
        "step_id": "create",
        "path": "accountId",
        "axes": ["name"],
    }]


def test_line_and_collection_formulas_are_executable() -> None:
    step = FlowStep(
        step_id="create",
        params=[
            ParamField(path="items", key="items", value=[], type="array", source_kind="user_input", source={
                "kind": "dynamic_structure_input",
                "structure_kind": "array_object",
                "array_container_path": "items",
            }),
            ParamField(path="items[0].count", key="count", value=8, type="number", wire_type="number", source_kind="user_input"),
            ParamField(path="items[0].productPrice", key="productPrice", value=5000, type="number", wire_type="number", source_kind="selected_option_field"),
            readonly_number("items[0].totalProductPrice", "totalProductPrice", 40000),
            ParamField(path="items[0].taxPercent", key="taxPercent", value=10, type="number", wire_type="number", source_kind="user_input"),
            readonly_number("items[0].taxPrice", "taxPrice", 4000),
            readonly_number("items[0].totalPrice", "lineTotalPrice", 44000),
            ParamField(path="discountPercent", key="discountPercent", value=10, type="number", wire_type="number", source_kind="user_input"),
            readonly_number("discountPrice", "discountPrice", 4400),
            readonly_number("totalPrice", "totalPrice", 39600),
        ],
    )
    spec = FlowSpec(steps=[step])
    _infer_arithmetic_computed_fields(spec)
    _infer_collection_computed_fields(spec)
    by_key = {param.key: param for param in spec.steps[0].params}
    assert by_key["totalProductPrice"].source.get("strategy") == "product"
    assert by_key["taxPrice"].source.get("strategy") == "percent_of"
    assert by_key["lineTotalPrice"].source.get("strategy") == "sum"
    assert by_key["discountPrice"].source.get("strategy") == "percent_of_collection_sum"
    assert by_key["totalPrice"].source.get("strategy") == "difference_collection_sum"

    runtime = _apply_runtime_fields(
        {
            "items": [{"count": 8, "productPrice": 5000, "taxPercent": 10}],
            "discountPercent": 10,
        },
        {"runtime_fields": [
            {"name": "row_product", "kind": "array_item_formula", "strategy": "product", "container_field": "items", "left_field": "count", "right_field": "productPrice", "result_field": "totalProductPrice"},
            {"name": "row_tax", "kind": "array_item_formula", "strategy": "percent_of", "container_field": "items", "left_field": "totalProductPrice", "right_field": "taxPercent", "result_field": "taxPrice"},
            {"name": "row_total", "kind": "array_item_formula", "strategy": "sum", "container_field": "items", "left_field": "totalProductPrice", "right_field": "taxPrice", "result_field": "totalPrice"},
            {"name": "discountPrice", "kind": "percent_of_collection_sum", "container_field": "items", "item_field": "totalPrice", "right_field": "discountPercent"},
            {"name": "totalPrice", "kind": "difference_collection_sum", "container_field": "items", "item_field": "totalPrice", "right_field": "discountPrice"},
        ]},
    )
    assert runtime["discountPrice"] == 4400
    assert runtime["totalPrice"] == 39600
