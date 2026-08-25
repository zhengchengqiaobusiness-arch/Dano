from __future__ import annotations

import asyncio

import dano.execution.page.request_capture as request_capture
import dano.onboarding.recording_gateway as recording_gateway
from dano.agent_tools.tools import _apply_recording_submission_atomic
from dano.execution.page.capability_compiler import compile_capabilities
from dano.execution.page.capability_refs import _attach_option_source_memberships
from dano.execution.page.capability_io import _sync_capability_io_schemas
from dano.execution.page.capability_views import _capability_view_step_ids
from dano.execution.page.capability_contracts import (
    _mark_repeated_write_observations,
    _planned_capability_has_public_anchor,
)
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
from dano.execution.page.flow_spec_core.request_contract import _runtime_select_bindings
from dano.execution.page.flow_spec_core.request_contract import _compile_row_enrichment
from dano.execution.page.flow_spec_core.request_contract import _compile_option_provider_link
from dano.execution.page.flow_materialization.field_contracts.dynamic_array import (
    _materialize_dynamic_array_inputs,
)
from dano.execution.page.recording_agent_contract import (
    apply_recording_agent_submission,
    recording_agent_submission_status,
    recording_capability_plan_complete,
)
from dano.execution.page.recording_live import apply_recording_agent_edit
from dano.onboarding.recording_gateway import (
    RecordingGatewaySession,
    RecordingSessionConfig,
)


def _capability_plan(name: str, request_id: str, *, kind: str = "create") -> dict:
    return {
        "business_understanding": {"business_name": "Orders", "summary": "Manage orders"},
        "capabilities": [{
            "name": name,
            "title": name,
            "kind": kind,
            "anchor_step_id": request_id,
            "request_refs": [{
                "request_id": request_id,
                "step_id": request_id,
                "usage": "execute",
            }],
        }],
        "unresolved_items": [],
    }


def test_capability_compile_keeps_transitive_option_source_steps() -> None:
    options = FlowStep(
        step_id="product-options",
        method="GET",
        path="/products/options",
        response_json={"data": [{"id": 7, "name": "Laptop"}]},
        source_meta={"request_id": "req-options", "role": "read_option"},
    )
    stock = FlowStep(
        step_id="stock",
        method="GET",
        path="/stock/count",
        params=[ParamField(
            path="query.productId",
            key="productId",
            value=7,
            category="user_param",
            source_kind="api_option",
            source={
                "kind": "api_option",
                "source_step_id": "product-options",
                "source_request_id": "req-options",
                "source_url": "/products/options",
                "value_key": "id",
                "label_key": "name",
            },
        )],
        selects=[SelectBinding(
            param="productId",
            path="query.productId",
            source_url="/products/options",
            source_request_id="req-options",
            value_key="id",
            label_key="name",
            enum_confirmed=True,
        )],
        response_json={"data": 2},
        source_meta={"request_id": "req-stock", "role": "business_get"},
    )
    create = FlowStep(
        step_id="create",
        method="POST",
        path="/orders/create",
        body_source='{"productId":7,"stockCount":2}',
        body_template={"productId": "{{productId}}", "stockCount": "{{stockCount}}"},
        params=[
            ParamField(path="productId", key="productId", value=7),
            ParamField(
                path="stockCount",
                key="stockCount",
                value=2,
                category="runtime_var",
                source_kind="previous_response",
                exposed_to_user=False,
                editable=False,
                source={"kind": "previous_response", "step_id": "stock", "response_path": "data"},
            ),
        ],
        source_meta={"request_id": "req-create", "role": "business_write"},
    )
    capability = FlowCapability(
        name="create_order",
        kind="create",
        request_refs=[
            CapabilityRequestRef(
                request_id="req-options", step_id="product-options", usage="option_source",
            ),
            CapabilityRequestRef(
                request_id="req-stock", step_id="stock", usage="preflight",
            ),
            CapabilityRequestRef(
                request_id="req-create", step_id="create", usage="execute",
            ),
        ],
        nodes=[
            {"id": "stock-call", "type": "call", "step_id": "stock", "usage": "preflight"},
            {"id": "create-call", "type": "call", "step_id": "create", "usage": "execute"},
        ],
    )
    spec = FlowSpec()
    spec.steps = [options, stock, create]
    spec.capabilities = [capability]

    assert _capability_view_step_ids(spec, capability) == [
        "product-options", "stock", "create",
    ]


def test_dynamic_array_selector_replaces_stale_aggregate_binding() -> None:
    aggregate = ParamField(
        path="items",
        key="items",
        value=[{"productId": 7, "count": 1}],
        type="array",
        wire_type="array",
        category="user_param",
        source_kind="user_input",
        source={
            "kind": "dynamic_structure_input",
            "structure_kind": "array_object",
            "array_container_path": "items",
        },
    )
    product = ParamField(
        path="items[0].productId",
        key="productId",
        value=7,
        category="user_param",
        source_kind="api_option",
        source={
            "kind": "api_option",
            "source_url": "/products/options",
            "source_request_id": "req-products",
            "array_container_path": "items",
            "array_item_path": "productId",
        },
    )
    unit = ParamField(
        path="items[0].unitName",
        key="unitName",
        value="box",
        category="runtime_var",
        source_kind="selected_option_field",
        exposed_to_user=False,
        editable=False,
        source={
            "kind": "selected_option_field",
            "selector_path": "items[0].productId",
            "response_path": "unitName",
            "array_container_path": "items",
            "array_item_path": "unitName",
        },
    )
    count = ParamField(
        path="items[0].count",
        key="count",
        value=1,
        category="user_param",
        source_kind="user_input",
        source={"array_container_path": "items", "array_item_path": "count"},
    )
    stale = SelectBinding(
        param="items",
        path="items",
        source_url="/products/options",
        value_key="id",
        label_key="name",
        multi=True,
        label_subkey="productId",
        element_template={
            "productId": {"item_key": "id"},
            "count": {"item_key": "items[0].count"},
            "stockCount": {"item_key": "accountId"},
        },
        field_projections={
            "count": "items[0].count",
            "stockCount": "accountId",
        },
        enum_confirmed=True,
    )
    selector = SelectBinding(
        param="productId",
        path="items[0].productId",
        source_url="/products/options",
        source_request_id="req-products",
        value_key="id",
        label_key="name",
        enum_confirmed=True,
    )
    step = FlowStep(
        step_id="create",
        params=[aggregate, product, unit, count],
        selects=[stale, selector],
    )
    spec = FlowSpec()
    spec.steps = [step]

    _materialize_dynamic_array_inputs(spec)

    bindings = [binding for binding in step.selects if binding.path == "items"]
    assert len(bindings) == 1
    assert bindings[0].source_request_id == "req-products"
    assert bindings[0].element_template == {
        "productId": {"item_key": "id"},
        "unitName": {"item_key": "unitName"},
    }
    assert bindings[0].field_projections == {"items[*].unitName": "unitName"}
    runtime = _runtime_select_bindings(step)
    assert len(runtime) == 1
    assert runtime[0]["param"] == "items"


def test_edit_prefill_option_binding_remains_runtime_executable() -> None:
    step = FlowStep(
        step_id="update",
        params=[ParamField(
            path="customerId",
            key="customerId",
            value=8,
            category="user_param",
            source_kind="previous_response",
            source={
                "kind": "previous_response",
                "allow_caller_override": True,
                "option_source": {
                    "source_url": "/customers/options",
                    "value_key": "id",
                    "label_key": "name",
                },
            },
        )],
        selects=[SelectBinding(
            param="customerId",
            path="customerId",
            source_url="/customers/options",
            value_key="id",
            label_key="name",
            enum_confirmed=True,
        )],
    )

    assert [binding["param"] for binding in _runtime_select_bindings(step)] == ["customerId"]


def test_repeating_row_preflight_is_compiled_as_row_enrichment() -> None:
    source = {
        "step_id": "stock",
        "method": "GET",
        "path": "/stock/count",
        "query_template": {"productId": "{{stock_product_id}}"},
        "params": ["stock_product_id"],
        "selects": [{
            "param": "stock_product_id",
            "path": "query.productId",
            "source_url": "/products/options",
            "value_key": "id",
            "label_key": "name",
        }],
    }
    target = {
        "step_id": "create",
        "method": "POST",
        "path": "/orders/create",
        "selects": [{
            "param": "items",
            "path": "items",
            "multi": True,
            "label_subkey": "productId",
            "source_url": "/products/options",
            "value_key": "id",
            "label_key": "name",
        }],
    }

    assert _compile_row_enrichment(
        source,
        target,
        target_path="items[0].stockCount",
        source_path="data",
    )
    enrichment = target["selects"][0]["row_enrichments"][0]
    assert enrichment["source_param"] == "stock_product_id"
    assert enrichment["selector_field"] == "productId"
    assert enrichment["target_field"] == "stockCount"
    assert enrichment["response_path"] == "data"


def test_option_collection_link_is_not_compiled_as_scalar_override() -> None:
    source = {
        "step_id": "customers",
        "method": "GET",
        "path": "/customers/options",
    }
    target = {
        "step_id": "create",
        "method": "POST",
        "path": "/orders/create",
        "selects": [{
            "param": "customerId",
            "path": "customerId",
            "source_url": "/customers/options",
            "value_key": "id",
            "label_key": "name",
        }],
    }

    assert _compile_option_provider_link(
        source,
        target,
        target_path="customerId",
        source_path="data[].id",
    )


def test_repeating_row_selector_runs_dependent_source_for_each_row(monkeypatch) -> None:
    async def fetch_products(*_args, **_kwargs):
        return request_capture._FetchedItems([
            {"id": 7, "name": "Laptop", "unitName": "box"},
            {"id": 8, "name": "Phone", "unitName": "piece"},
        ])

    calls: list[int] = []

    async def execute_enrichment(_request, fields, **_kwargs):
        product_id = int(fields["stock_product_id"])
        calls.append(product_id)
        return {"ok": True, "response": {"data": {7: 12, 8: 3}[product_id]}}

    monkeypatch.setattr(request_capture, "_fetch_select_list", fetch_products)
    monkeypatch.setattr(request_capture, "execute_api_request", execute_enrichment)
    api_request = {
        "selects": [{
            "param": "items",
            "path": "items",
            "multi": True,
            "label_subkey": "productId",
            "source_url": "/products/options",
            "value_key": "id",
            "label_key": "name",
            "element_template": {
                "productId": {"item_key": "id"},
                "unitName": {"item_key": "unitName"},
            },
            "row_enrichments": [{
                "request": {"method": "GET", "path": "/stock/count"},
                "source_param": "stock_product_id",
                "selector_field": "productId",
                "target_field": "stockCount",
                "response_path": "data",
            }],
        }],
    }
    fields = {
        "items": [
            {"productId": "Laptop", "count": 2},
            {"productId": "Phone", "count": 1},
        ],
    }

    resolved = asyncio.run(request_capture._resolve_list_selects(
        api_request,
        fields,
        base_url="https://example.test",
        storage_state=None,
        token_key=None,
        verify=True,
    ))

    assert calls == [7, 8]
    assert resolved["items"] == [
        {"productId": 7, "count": 2, "unitName": "box", "stockCount": 12},
        {"productId": 8, "count": 1, "unitName": "piece", "stockCount": 3},
    ]


def test_edit_hydration_preserves_caller_overrides_and_expands_system_rows() -> None:
    response = {
        "data": {
            "customerId": 8,
            "items": [
                {"id": 73, "count": 111, "stockCount": 2},
                {"id": 74, "count": 11, "stockCount": 0},
            ],
        },
    }
    fields = {
        "customerId": "李白",
        "items": [
            {"productId": 7, "count": 2},
            {"productId": 8, "count": 3},
        ],
    }
    customer_link = {
        "source_path": "data.customerId",
        "target_path": "customerId",
        "fallback_input": {"param": "customerId"},
    }
    count_link = {
        "source_path": "data.items[0].count",
        "target_path": "items[0].count",
        "fallback_input": {"param": "items", "item_path": "count"},
    }
    id_link = {
        "source_path": "data.items[0].id",
        "target_path": "items[0].id",
    }
    stock_link = {
        "source_path": "data.items[0].stockCount",
        "target_path": "items[0].stockCount",
    }

    assert request_capture._link_value_overrides(response, customer_link, fields) == []
    assert request_capture._link_value_overrides(response, count_link, fields) == []
    assert request_capture._link_value_overrides(response, id_link, fields) == [
        (("items", 0, "id"), 73),
        (("items", 1, "id"), 74),
    ]
    assert request_capture._link_value_overrides(response, stock_link, fields) == [
        (("items", 0, "stockCount"), 2),
        (("items", 1, "stockCount"), 0),
    ]


def test_capability_keeps_only_the_option_request_bound_to_its_field() -> None:
    old_options = FlowStep(
        step_id="old-options",
        method="GET",
        path="/accounts/options",
        source_meta={"request_id": "req-old"},
    )
    update = FlowStep(
        step_id="update",
        method="PUT",
        path="/orders/update",
        params=[ParamField(
            path="accountId",
            key="accountId",
            value=2,
            category="user_param",
            source_kind="api_option",
            source={
                "kind": "api_option",
                "source_url": "/accounts/options",
                "source_request_id": "req-current",
            },
        )],
        selects=[SelectBinding(
            param="accountId",
            path="accountId",
            source_url="/accounts/options",
            source_request_id="req-current",
            value_key="id",
            label_key="name",
            enum_confirmed=True,
        )],
        source_meta={"request_id": "req-update"},
    )
    capability = FlowCapability(
        name="update_order",
        nodes=[
            {"type": "call", "step_id": "old-options", "usage": "option_source"},
            {"type": "call", "step_id": "update", "usage": "execute"},
        ],
        request_refs=[
            CapabilityRequestRef(
                request_id="req-old",
                step_id="old-options",
                path="/accounts/options",
                usage="option_source",
                origin="compiler",
                confirmed=True,
            ),
            CapabilityRequestRef(
                request_id="req-update",
                step_id="update",
                path="/orders/update",
                usage="execute",
            ),
        ],
    )
    spec = FlowSpec()
    spec.steps = [old_options, update]
    spec.capabilities = [capability]
    spec.request_facts.requests = [RequestFact(
        request_id="req-current",
        method="GET",
        path="/accounts/options",
        response_json={"data": [{"id": 2, "name": "Main"}]},
    )]

    _attach_option_source_memberships(spec)

    option_refs = [ref for ref in capability.request_refs if ref.usage == "option_source"]
    assert [(ref.request_id, ref.path) for ref in option_refs] == [
        ("req-current", "/accounts/options"),
    ]


def test_field_and_relation_backlog_does_not_block_complete_capability_snapshot() -> None:
    plan = _capability_plan("create_sale_order", "req-create")
    plan["unresolved_items"] = [{
        "type": "field_source",
        "title": "accountId source remains unresolved",
        "blocking": True,
    }]
    spec = FlowSpec(
        capabilities=[FlowCapability(name="create_sale_order")],
        meta={
            "capability_model": {
                "status": "needs_review",
                "proposal_gate": {"accepted": False, "reasons": ["field_axis_contract"]},
                "semantic_coverage": {
                    "complete": False,
                    "missing": ["field_axis_contract"],
                    "field_axis_gaps": [{"step_id": "create", "path": "accountId", "axes": ["source"]}],
                },
                "submitted_semantic_plan": plan,
                "semantic_plan": plan,
                "submitted_count": 1,
                "materialized_count": 1,
                "capability_compilation_errors": ["field source remains unresolved"],
            },
            "recording_agent_session": {
                "op_results": [{
                    "index": 7,
                    "op": "set_param_source",
                    "status": "rejected",
                    "requested_target": {"request_id": "req-create", "wire_path": "body.accountId"},
                    "reason": "field source remains unresolved",
                }],
            },
        },
    )
    spec.request_facts.requests = [RequestFact(
        request_id="req-create",
        method="POST",
        path="/orders/create",
        trigger_action_id="create-order",
    )]
    spec.request_facts.analysis = {
        "req-create": RequestAnalysis(
            request_id="req-create", role="business_write", keep=True,
        ),
    }

    status = recording_agent_submission_status(spec)

    assert recording_capability_plan_complete(spec)
    assert status["capability_plan_complete"] is True
    assert status["submission_complete"] is True
    assert status["must_retry"] == [7]
    assert status["field_axis_gaps"]


def test_rejected_field_operation_keeps_the_applied_capability_plan_terminal() -> None:
    step = FlowStep(
        step_id="create",
        method="POST",
        path="/orders/create",
        params=[ParamField(path="accountId", key="accountId", value=2)],
        source_meta={"request_id": "req-create", "role": "business_write"},
    )
    spec = FlowSpec(steps=[step])
    spec.request_facts.requests = [RequestFact(
        request_id="req-create",
        method="POST",
        path="/orders/create",
        post_data={"accountId": 2},
        trigger_action_id="create-order",
    )]
    spec.request_facts.analysis = {
        "req-create": RequestAnalysis(
            request_id="req-create", role="business_write", keep=True,
        ),
    }
    spec.request_facts.usage = {
        "req-create": RequestUsage(
            request_id="req-create", materialized_step_id="create", state="materialized",
        ),
    }
    submission = {
        "semantic_plan": _capability_plan("create_sale_order", "req-create"),
        "ops": [{
            "op": "set_param_type",
            "request_id": "req-create",
            "path": "accountId",
            "business_type": "unsupported-type",
            "reason": "Deliberately invalid field repair for the regression seam.",
            "evidence_refs": ["req-create"],
        }],
    }

    result = asyncio.run(apply_recording_agent_submission(
        spec, submission=submission, mode="plan",
    ))
    status = recording_agent_submission_status(result)

    assert [capability.name for capability in result.capabilities] == ["create_sale_order"]
    assert status["must_retry"] == [0]
    assert status["submission_complete"] is True


def test_complete_semantic_snapshot_replaces_stale_machine_capabilities() -> None:
    query = FlowStep(
        step_id="query",
        method="GET",
        path="/orders/page",
        response_json={"data": {"list": [{"id": 1}]}},
        source_meta={"request_id": "req-query", "role": "business_get"},
    )
    inspect = FlowStep(
        step_id="inspect",
        method="GET",
        path="/orders/get",
        params=[ParamField(
            path="query.id", key="id", value=1,
            category="user_param", source_kind="user_input", exposed_to_user=True,
        )],
        response_json={"data": {"id": 1}},
        source_meta={"request_id": "req-inspect", "role": "business_get"},
    )

    def planned(name: str, kind: str, step_id: str, request_id: str) -> dict:
        return {
            "name": name,
            "title": name,
            "kind": kind,
            "anchor_step_id": step_id,
            "request_refs": [{
                "request_id": request_id,
                "step_id": step_id,
                "usage": "execute",
            }],
        }

    old_plan = {
        "business_understanding": {"business_name": "Orders"},
        "capabilities": [
            planned("sale_order_query", "query", "query", "req-query"),
            planned("inspect_legacy", "inspect", "inspect", "req-inspect"),
        ],
        "unresolved_items": [],
    }
    new_plan = {
        "business_understanding": {"business_name": "Orders"},
        "capabilities": [
            planned("sale_order_query", "query", "query", "req-query"),
        ],
        "unresolved_items": [],
    }
    spec = FlowSpec(
        steps=[query, inspect],
        capabilities=[
            FlowCapability(name="sale_order_query", kind="query"),
            FlowCapability(name="inspect_legacy", kind="inspect"),
        ],
        meta={
            "stage_1_6_contract_version": 2,
            "capability_model": {
                "source": "verified_request_graph",
                "semantic_plan": old_plan,
                "submitted_semantic_plan": old_plan,
            },
        },
    )
    for step, request_id in ((query, "req-query"), (inspect, "req-inspect")):
        spec.request_facts.requests.append(RequestFact(
            request_id=request_id,
            method="GET",
            path=step.path,
            response_json=step.response_json,
        ))
        spec.request_facts.analysis[request_id] = RequestAnalysis(
            request_id=request_id, role="business_get", keep=True,
        )
        spec.request_facts.usage[request_id] = RequestUsage(
            request_id=request_id, materialized_step_id=step.step_id, state="materialized",
        )

    result = asyncio.run(apply_recording_agent_submission(
        spec,
        submission={"semantic_plan": new_plan, "ops": []},
        mode="plan",
    ))

    assert [capability.name for capability in result.capabilities] == ["sale_order_query"]
    model = result.meta["capability_model"]
    assert model["submitted_count"] == model["materialized_count"] == 1
    assert model["extra_materialized_names"] == []


def _row_command(
    step_id: str,
    *,
    record_id: int,
    status: int,
    classified_status: bool = True,
) -> FlowStep:
    return FlowStep(
        step_id=step_id,
        method="PUT",
        path="/orders/update-status",
        params=[
            ParamField(
                path="query.id",
                key="id",
                value=record_id,
                source_kind="selected_record_identity",
            ),
            ParamField(
                path="query.status",
                key="status",
                value=status,
                category="system_const" if classified_status else "user_param",
                source_kind="constant" if classified_status else "unknown",
                exposed_to_user=False,
                editable=False,
            ),
        ],
        source_meta={
            "request_id": step_id,
            "role": "business_write",
            "trigger_op": "click",
            "trigger_locator": "button:确定",
            "page_id": "orders",
            "frame_id": "main",
        },
    )


def test_row_command_discriminator_separates_approve_and_withdraw() -> None:
    approve = _row_command("approve-1", record_id=101, status=20)
    withdraw = _row_command(
        "withdraw-1", record_id=101, status=10, classified_status=False,
    )
    repeated_approve = _row_command("approve-2", record_id=202, status=20)
    spec = FlowSpec(steps=[approve, withdraw, repeated_approve])
    spec.request_facts.usage = {
        step.step_id: RequestUsage(
            request_id=step.step_id,
            materialized_step_id=step.step_id,
            state="materialized",
        )
        for step in spec.steps
    }
    spec.request_facts.analysis = {
        step.step_id: RequestAnalysis(
            request_id=step.step_id,
            role="business_write",
            keep=True,
        )
        for step in spec.steps
    }

    _mark_repeated_write_observations(spec)

    assert "duplicate_observation_of" not in withdraw.source_meta
    assert repeated_approve.source_meta["duplicate_observation_of"] == approve.step_id


def test_safe_export_request_role_is_canonicalized_to_business_read() -> None:
    step = FlowStep(
        step_id="export",
        method="GET",
        path="/orders/export",
        response_json={"downloadUrl": "/files/orders.xlsx"},
        source_meta={
            "request_id": "req-export",
            "trigger_op": "click",
            "trigger_locator": "button:导出",
        },
    )
    spec = FlowSpec(steps=[step])
    spec.request_facts.requests = [RequestFact(
        request_id="req-export",
        method="GET",
        path="/orders/export",
        trigger_action_id="export-orders",
    )]

    apply_recording_agent_edit(spec, {
        "op": "set_request_role",
        "request_id": "req-export",
        "role": "business_write",
        "reason": "The export button produced this request.",
        "evidence_refs": ["req-export"],
    }, record=False)

    assert spec.request_facts.analysis["req-export"].role == "business_get"
    assert spec.steps[0].source_meta["role"] == "business_get"
    assert _planned_capability_has_public_anchor(spec, "export", ["export"])


def test_compiler_recovers_stale_anchor_from_grounded_execute_reference() -> None:
    step = FlowStep(
        step_id="detail",
        method="GET",
        path="/orders/get",
        response_json={"data": {"id": 1}},
        source_meta={"request_id": "req-detail", "role": "business_get"},
    )
    spec = FlowSpec(steps=[step])
    spec.request_facts.requests = [RequestFact(
        request_id="req-detail", method="GET", path="/orders/get", query={"id": 1},
    )]
    spec.request_facts.analysis = {
        "req-detail": RequestAnalysis(request_id="req-detail", role="business_get", keep=True),
    }
    plan = {
        "business_understanding": {"business_name": "Orders"},
        "capabilities": [{
            "name": "inspect_order",
            "title": "Inspect order",
            "kind": "inspect",
            "anchor_step_id": "stale-live-step",
            "request_refs": [{
                "request_id": "req-detail",
                "step_id": "detail",
                "usage": "execute",
            }],
        }],
        "unresolved_items": [],
    }

    compilation = compile_capabilities(spec, plan)

    assert [capability.name for capability in compilation.capabilities] == ["inspect_order"]
    assert compilation.capabilities[0].step_ids[-1] == "detail"


def test_compiler_preserves_submitted_preflight_membership_without_inferred_link() -> None:
    context = FlowStep(
        step_id="customer-options",
        method="GET",
        path="/customers/simple-list",
        source_meta={"request_id": "req-customer-options", "role": "business_get"},
    )
    create = FlowStep(
        step_id="create",
        method="POST",
        path="/orders/create",
        source_meta={"request_id": "req-create", "role": "business_write"},
    )
    spec = FlowSpec(steps=[context, create])
    plan = {
        "business_understanding": {"business_name": "Orders"},
        "capabilities": [{
            "name": "create_order",
            "title": "Create order",
            "kind": "create",
            "anchor_step_id": "create",
            "request_refs": [
                {
                    "request_id": "req-customer-options",
                    "step_id": "customer-options",
                    "usage": "preflight",
                },
                {
                    "request_id": "req-create",
                    "step_id": "create",
                    "usage": "execute",
                },
            ],
        }],
        "unresolved_items": [],
    }

    compilation = compile_capabilities(spec, plan)

    assert [
        (ref.request_id, ref.usage)
        for ref in compilation.capabilities[0].request_refs
    ] == [
        ("req-customer-options", "preflight"),
        ("req-create", "execute"),
    ]


def test_auxiliary_request_params_do_not_expand_public_capability_inputs() -> None:
    preflight = FlowStep(
        step_id="page-context",
        method="GET",
        path="/orders/page",
        params=[ParamField(
            path="query.no",
            key="query_no",
            value="XSDD-RECORDED",
            source_kind="user_input",
            category="user_param",
            exposed_to_user=True,
        )],
        source_meta={"request_id": "req-page", "role": "business_get"},
    )
    create = FlowStep(
        step_id="create",
        method="POST",
        path="/orders/create",
        params=[ParamField(
            path="body.customerId",
            key="customerId",
            label="客户",
            value=5,
            source_kind="user_input",
            category="user_param",
            exposed_to_user=True,
        )],
        source_meta={"request_id": "req-create", "role": "business_write"},
    )
    fact_check = FlowStep(
        step_id="verify",
        method="GET",
        path="/orders/get",
        params=[ParamField(
            path="query.id",
            key="verify_id",
            value=70,
            source_kind="user_input",
            category="user_param",
            exposed_to_user=True,
        )],
        source_meta={"request_id": "req-verify", "role": "business_get"},
    )
    spec = FlowSpec(steps=[preflight, create, fact_check])
    plan = {
        "business_understanding": {"business_name": "Orders"},
        "capabilities": [{
            "name": "create_order",
            "title": "Create order",
            "kind": "create",
            "anchor_step_id": "create",
            "request_refs": [
                {"request_id": "req-page", "step_id": "page-context", "usage": "preflight"},
                {"request_id": "req-create", "step_id": "create", "usage": "execute"},
                {"request_id": "req-verify", "step_id": "verify", "usage": "fact_check"},
            ],
        }],
        "unresolved_items": [],
    }

    capability = compile_capabilities(spec, plan).capabilities[0]

    assert set(capability.input_schema["properties"]) == {"customerId"}
    assert {field.key for field in capability.inputs} == {"customerId"}
    memberships = {(ref.step_id, ref.usage) for ref in capability.request_refs}
    assert ("create", "execute") in memberships
    assert ("verify", "fact_check") in memberships


def test_record_selector_preflight_remains_a_public_capability_input() -> None:
    detail = FlowStep(
        step_id="detail",
        method="GET",
        path="/orders/get",
        params=[ParamField(
            path="query.id",
            key="id",
            label="记录",
            value=70,
            required=True,
            source_kind="selected_record_identity",
            source={"kind": "selected_record_identity", "required_state": "required"},
            category="user_param",
            exposed_to_user=True,
            editable=True,
        )],
        source_meta={"request_id": "req-detail", "role": "business_get"},
    )
    update = FlowStep(
        step_id="update",
        method="PUT",
        path="/orders/update",
        body_source='{"remark":"updated"}',
        params=[ParamField(
            path="body.remark",
            key="remark",
            label="备注",
            value="updated",
            source_kind="user_input",
            category="user_param",
            exposed_to_user=True,
        )],
        source_meta={"request_id": "req-update", "role": "business_write"},
    )
    spec = FlowSpec(
        steps=[detail, update],
        capabilities=[FlowCapability(
            name="update_order",
            kind="update",
            step_ids=["detail", "update"],
            nodes=[
                {"type": "call", "step_id": "detail"},
                {"type": "call", "step_id": "update"},
            ],
            request_refs=[
                {"request_id": "req-detail", "step_id": "detail", "usage": "preflight"},
                {"request_id": "req-update", "step_id": "update", "usage": "execute"},
            ],
        )],
        meta={"stage_1_6_contract_version": 2},
    )

    _sync_capability_io_schemas(spec)

    capability = spec.capabilities[0]
    assert set(capability.input_schema["properties"]) == {"id", "remark"}
    assert "id" in capability.input_schema["required"]


def test_array_option_preflight_does_not_duplicate_structured_execute_input() -> None:
    option_source = {
        "kind": "api_option",
        "source_request_id": "req-products",
        "source_url": "/products/simple-list",
        "value_key": "id",
        "label_key": "name",
    }
    stock = FlowStep(
        step_id="stock",
        method="GET",
        path="/stock/get-count",
        params=[ParamField(
            path="query.productId",
            key="get_stock_query_productId",
            label="产品",
            type="enum",
            wire_type="number",
            source_kind="api_option",
            source={**option_source, "original_key": "productId", "collision_resolved": True},
            category="user_param",
            exposed_to_user=True,
        )],
        source_meta={"request_id": "req-stock", "role": "read_context"},
    )
    create = FlowStep(
        step_id="create",
        method="POST",
        path="/orders/create",
        body_source='{"items":[{"productId":5}]}',
        params=[ParamField(
            path="body.items[0].productId",
            key="productId",
            label="产品",
            value=5,
            type="enum",
            wire_type="number",
            source_kind="api_option",
            source=dict(option_source),
            category="user_param",
            exposed_to_user=True,
        )],
        source_meta={"request_id": "req-create", "role": "business_write"},
    )
    spec = FlowSpec(
        steps=[stock, create],
        capabilities=[FlowCapability(
            name="create_order",
            kind="create",
            step_ids=["stock", "create"],
            nodes=[
                {"type": "call", "step_id": "stock"},
                {"type": "call", "step_id": "create"},
            ],
            request_refs=[
                {"request_id": "req-stock", "step_id": "stock", "usage": "preflight"},
                {"request_id": "req-create", "step_id": "create", "usage": "execute"},
            ],
        )],
        meta={"stage_1_6_contract_version": 2},
    )

    _sync_capability_io_schemas(spec)

    capability = spec.capabilities[0]
    assert set(capability.input_schema["properties"]) == {"items"}
    assert create.params[0].key == "productId"


def test_stale_collision_key_restores_when_auxiliary_field_leaves_scope() -> None:
    create = FlowStep(
        step_id="create",
        method="POST",
        path="/orders/create",
        body_source='{"customerId":5}',
        params=[ParamField(
            path="body.customerId",
            key="body_customerId",
            label="客户",
            value=5,
            type="enum",
            wire_type="number",
            source_kind="api_option",
            source={
                "kind": "api_option",
                "source_request_id": "req-customers",
                "source_url": "/customers/simple-list",
                "value_key": "id",
                "label_key": "name",
            },
            category="user_param",
            exposed_to_user=True,
            evidence=[{
                "kind": "field_key_collision_resolved",
                "original_key": "customerId",
                "resolved_key": "body_customerId",
                "path": "body.customerId",
                "step_id": "create",
                "actor": "heuristic",
            }],
        )],
        source_meta={"request_id": "req-create", "role": "business_write"},
    )
    spec = FlowSpec(
        steps=[create],
        capabilities=[FlowCapability(
            name="create_order",
            kind="create",
            step_ids=["create"],
            nodes=[{"type": "call", "step_id": "create"}],
            request_refs=[{
                "request_id": "req-create", "step_id": "create", "usage": "execute",
            }],
        )],
        meta={"stage_1_6_contract_version": 2},
    )

    _sync_capability_io_schemas(spec)

    assert create.params[0].key == "customerId"
    assert set(spec.capabilities[0].input_schema["properties"]) == {"customerId"}


def test_top_level_and_array_item_keys_keep_their_structural_namespaces() -> None:
    def stale_remark(path: str, resolved_key: str) -> ParamField:
        return ParamField(
            path=path,
            key=resolved_key,
            label="备注",
            value="note",
            source_kind="user_input",
            source={"kind": "user_input"},
            category="user_param",
            exposed_to_user=True,
            evidence=[{
                "kind": "field_key_collision_resolved",
                "original_key": "remark",
                "resolved_key": resolved_key,
                "path": path,
                "step_id": "update",
                "actor": "heuristic",
            }],
        )

    update = FlowStep(
        step_id="update",
        method="PUT",
        path="/orders/update",
        body_source='{"remark":"note","items":[{"remark":"line note"}]}',
        params=[
            stale_remark("body.remark", "put_orders_update_body_remark"),
            stale_remark("body.items[0].remark", "put_orders_update_body_remark_abcd1234"),
        ],
        source_meta={"request_id": "req-update", "role": "business_write"},
    )
    spec = FlowSpec(
        steps=[update],
        capabilities=[FlowCapability(
            name="update_order",
            kind="update",
            step_ids=["update"],
            nodes=[{"type": "call", "step_id": "update"}],
            request_refs=[{
                "request_id": "req-update", "step_id": "update", "usage": "execute",
            }],
        )],
        meta={"stage_1_6_contract_version": 2},
    )

    _sync_capability_io_schemas(spec)

    assert [param.key for param in update.params if param.path.endswith("remark")] == [
        "remark", "remark",
    ]
    properties = spec.capabilities[0].input_schema["properties"]
    assert "remark" in properties
    assert "remark" in properties["items"]["items"]["properties"]


def test_edit_hydration_is_an_optional_override_without_recorded_default() -> None:
    detail = FlowStep(
        step_id="detail",
        method="GET",
        path="/orders/get",
        response_json={"data": {"remark": "old-record-value"}},
        source_meta={"request_id": "req-detail", "role": "business_get"},
    )
    update = FlowStep(
        step_id="update",
        method="PUT",
        path="/orders/update",
        params=[ParamField(
            path="body.remark",
            key="remark",
            label="备注",
            value="old-record-value",
            default_value="old-record-value",
            required=True,
            source_kind="previous_response",
            source={
                "kind": "previous_response",
                "step_id": "detail",
                "response_path": "data.remark",
                "allow_caller_override": True,
            },
            category="user_param",
            exposed_to_user=True,
            editable=True,
            evidence=[{
                "kind": "page_control",
                "control_kind": "textarea",
                "disabled": False,
                "read_only": False,
                "binding_status": "bound",
                "request_path": "body.remark",
            }],
        )],
        source_meta={"request_id": "req-update", "role": "business_write"},
    )
    spec = FlowSpec(
        steps=[detail, update],
        links=[FlowLink(
            source_step_id="detail",
            source_path="data.remark",
            target_step_id="update",
            target_path="body.remark",
            confirmed=True,
        )],
        capabilities=[FlowCapability(
            name="update_order",
            kind="update",
            step_ids=["detail", "update"],
            nodes=[
                {"type": "call", "step_id": "detail"},
                {"type": "call", "step_id": "update"},
            ],
            request_refs=[
                {"request_id": "req-detail", "step_id": "detail", "usage": "preflight"},
                {"request_id": "req-update", "step_id": "update", "usage": "execute"},
            ],
        )],
        meta={"stage_1_6_contract_version": 2},
    )

    _sync_capability_io_schemas(spec)

    capability = spec.capabilities[0]
    remark_schema = capability.input_schema["properties"]["remark"]
    assert "default" not in remark_schema
    assert "remark" not in capability.input_schema["required"]
    assert len(capability.inputs) == 1
    assert capability.inputs[0].required is False


def test_grounded_actions_produce_capabilities_without_a_model_plan() -> None:
    from dano.execution.page.capability_compiler import ensure_grounded_capability_output

    step = FlowStep(
        step_id="create",
        name="create-order",
        method="POST",
        path="/orders/create",
        source_meta={
            "request_id": "req-create",
            "role": "business_write",
            "trigger_op": "submit",
            "trigger_locator": "button:新增",
        },
    )
    spec = FlowSpec(steps=[step])
    spec.request_facts.requests = [RequestFact(
        request_id="req-create",
        method="POST",
        path="/orders/create",
        trigger_action_id="create-order",
    )]
    spec.request_facts.analysis = {
        "req-create": RequestAnalysis(
            request_id="req-create", role="business_write", keep=True,
        ),
    }
    spec.request_facts.usage = {
        "req-create": RequestUsage(
            request_id="req-create", materialized_step_id="create", state="materialized",
        ),
    }

    result = ensure_grounded_capability_output(spec)

    assert len(result.capabilities) == 1
    assert result.capabilities[0].step_ids[-1] == "create"
    assert result.meta["capability_model"]["source"] == "grounded_action_fallback"


def test_repeated_detail_read_does_not_create_fallback_capability() -> None:
    from dano.execution.page.capability_compiler import ensure_grounded_capability_output

    first = FlowStep(
        step_id="detail-first",
        name="GET_get",
        method="GET",
        path="/orders/get?id=69",
        source_meta={"request_id": "req-first", "role": "business_get"},
    )
    repeated = FlowStep(
        step_id="detail-repeated",
        name="GET_get",
        method="GET",
        path="/orders/get?id=70",
        source_meta={"request_id": "req-repeated", "role": "business_get"},
    )
    plan = {
        "business_understanding": {"business_name": "Orders"},
        "capabilities": [{
            "name": "inspect_order",
            "title": "Inspect order",
            "kind": "inspect",
            "anchor_step_id": "detail-first",
            "request_refs": [{
                "request_id": "req-first",
                "step_id": "detail-first",
                "usage": "execute",
            }],
        }],
        "unresolved_items": [],
    }
    spec = FlowSpec(
        steps=[first, repeated],
        meta={"capability_model": {
            "semantic_plan": plan,
            "submitted_semantic_plan": plan,
            "proposal_gate": {"accepted": False},
        }},
    )
    spec.request_facts.requests = [
        RequestFact(
            request_id="req-first",
            method="GET",
            path="/orders/get",
            url="/orders/get?id=69",
            query={"id": ["69"]},
            trigger_action_id="open-first",
        ),
        RequestFact(
            request_id="req-repeated",
            method="GET",
            path="/orders/get",
            url="/orders/get?id=70",
            query={"id": ["70"]},
            trigger_action_id="open-repeated",
        ),
    ]
    spec.request_facts.analysis = {
        request_id: RequestAnalysis(
            request_id=request_id, role="business_get", keep=True,
        )
        for request_id in ("req-first", "req-repeated")
    }
    spec.request_facts.usage = {
        "req-first": RequestUsage(
            request_id="req-first", materialized_step_id="detail-first", state="materialized",
        ),
        "req-repeated": RequestUsage(
            request_id="req-repeated", materialized_step_id="detail-repeated", state="materialized",
        ),
    }

    result = ensure_grounded_capability_output(spec)

    assert [capability.name for capability in result.capabilities] == ["inspect_order"]
    assert result.meta["capability_model"].get("fallback_added_capabilities", []) == []


def test_grounded_fallback_restores_eight_submitted_capabilities_from_seven() -> None:
    from dano.execution.page.capability_compiler import ensure_grounded_capability_output

    steps = [
        FlowStep(
            step_id=f"action-{index}",
            method="POST",
            path=f"/orders/action/{index}",
            source_meta={"request_id": f"req-{index}", "role": "business_write"},
        )
        for index in range(8)
    ]
    plan = {
        "business_understanding": {"business_name": "Orders"},
        "capabilities": [
            {
                "name": f"ability_{index}",
                "title": f"Ability {index}",
                "kind": "submit",
                "anchor_step_id": f"action-{index}",
                "request_refs": [{
                    "request_id": f"req-{index}",
                    "step_id": f"action-{index}",
                    "usage": "execute",
                }],
            }
            for index in range(8)
        ],
        "unresolved_items": [],
    }
    spec = FlowSpec(
        steps=steps,
        capabilities=[
            FlowCapability(name=f"ability_{index}", step_ids=[f"action-{index}"])
            for index in range(7)
        ],
        meta={"capability_model": {
            "semantic_plan": plan,
            "submitted_semantic_plan": plan,
            "submitted_count": 8,
            "materialized_count": 7,
            "missing_submitted_names": ["ability_7"],
        }},
    )
    spec.request_facts.requests = [
        RequestFact(
            request_id=f"req-{index}",
            method="POST",
            path=f"/orders/action/{index}",
            trigger_action_id=f"action-{index}",
        )
        for index in range(8)
    ]
    spec.request_facts.analysis = {
        request_id: RequestAnalysis(request_id=request_id, role="business_write", keep=True)
        for request_id in (f"req-{index}" for index in range(8))
    }
    spec.request_facts.usage = {
        f"req-{index}": RequestUsage(
            request_id=f"req-{index}",
            materialized_step_id=f"action-{index}",
            state="materialized",
        )
        for index in range(8)
    }

    result = ensure_grounded_capability_output(spec)

    assert [capability.name for capability in result.capabilities] == [
        f"ability_{index}" for index in range(8)
    ]
    assert result.meta["capability_model"]["missing_submitted_names"] == []


def test_detail_capability_retargets_from_edit_hydration_without_extra_ability() -> None:
    from dano.execution.page.capability_compiler import ensure_grounded_capability_output

    detail = FlowStep(
        step_id="detail",
        method="GET",
        path="/orders/get?id=69",
        response_json={"data": {"id": 69}},
        source_meta={"request_id": "req-detail", "role": "business_get"},
    )
    hydration = FlowStep(
        step_id="edit-hydration",
        method="GET",
        path="/orders/get?id=70",
        response_json={"data": {"id": 70}},
        source_meta={
            "request_id": "req-edit-hydration",
            "role": "business_get",
            "control_preflight_for_write": True,
            "record_hydration_for_write_ids": ["update"],
            "control_preflight_for_write_ids": ["update"],
        },
    )
    edit_account_options = FlowStep(
        step_id="edit-account-options",
        method="GET",
        path="/accounts/simple-list",
        response_json={"data": [{"id": 1, "name": "Main"}]},
        source_meta={"request_id": "req-edit-account-options", "role": "read_option"},
    )
    update = FlowStep(
        step_id="update",
        method="PUT",
        path="/orders/update",
        body_source='{"id":70}',
        params=[ParamField(
            path="body.id",
            key="id",
            value=70,
            source_kind="previous_response",
            source={
                "kind": "previous_response",
                "step_id": "edit-hydration",
                "response_path": "data.id",
            },
        )],
        source_meta={"request_id": "req-update", "role": "business_write"},
    )
    plan = {
        "business_understanding": {"business_name": "Orders"},
        "capabilities": [
            {
                "name": "inspect_order",
                "title": "Inspect order",
                "kind": "inspect",
                "anchor_step_id": "edit-hydration",
                "request_refs": [
                    {"step_id": "edit-hydration", "usage": "execute"},
                    {"step_id": "edit-account-options", "usage": "option_source"},
                ],
            },
            {
                "name": "update_order",
                "title": "Update order",
                "kind": "update",
                "anchor_step_id": "update",
                "request_refs": [
                    {"step_id": "edit-hydration", "usage": "preflight"},
                    {"step_id": "update", "usage": "execute"},
                ],
            },
            {
                "name": "inspect_fallback",
                "title": "GET get",
                "kind": "inspect",
                "anchor_step_id": "detail",
                "request_refs": [{"step_id": "detail", "usage": "execute"}],
            },
        ],
        "unresolved_items": [],
    }
    spec = FlowSpec(
        steps=[detail, hydration, edit_account_options, update],
        links=[FlowLink(
            source_step_id="edit-hydration",
            source_path="data.id",
            target_step_id="update",
            target_path="body.id",
            confirmed=True,
            confidence=1.0,
            reason="编辑页详情回填后提交更新",
            meta={"captured_record_hydration": True},
        )],
        meta={"capability_model": {
            "semantic_plan": plan,
            "submitted_semantic_plan": plan,
            "source": "grounded_action_fallback",
            "fallback_added_capabilities": ["inspect_fallback"],
        }},
    )
    spec.request_facts.requests = [
        RequestFact(
            request_id="req-detail",
            method="GET",
            path="/orders/get",
            url="/orders/get?id=69",
            query={"id": ["69"]},
            trigger_action_id="open-detail",
        ),
        RequestFact(
            request_id="req-edit-hydration",
            method="GET",
            path="/orders/get",
            url="/orders/get?id=70",
            query={"id": ["70"]},
            trigger_action_id="open-edit",
        ),
        RequestFact(
            request_id="req-edit-account-options",
            method="GET",
            path="/accounts/simple-list",
            url="/accounts/simple-list",
            trigger_action_id="open-edit",
        ),
        RequestFact(
            request_id="req-update",
            method="PUT",
            path="/orders/update",
            url="/orders/update",
            post_data={"id": 70},
            trigger_action_id="save-edit",
        ),
    ]
    spec.request_facts.analysis = {
        "req-detail": RequestAnalysis(
            request_id="req-detail", role="business_get", keep=True,
        ),
        "req-edit-hydration": RequestAnalysis(
            request_id="req-edit-hydration", role="business_get", keep=True,
        ),
        "req-edit-account-options": RequestAnalysis(
            request_id="req-edit-account-options", role="read_option", keep=True,
        ),
        "req-update": RequestAnalysis(
            request_id="req-update", role="business_write", keep=True,
        ),
    }
    spec.request_facts.usage = {
        "req-detail": RequestUsage(
            request_id="req-detail", materialized_step_id="detail", state="materialized",
        ),
        "req-edit-hydration": RequestUsage(
            request_id="req-edit-hydration",
            materialized_step_id="edit-hydration",
            state="materialized",
        ),
        "req-edit-account-options": RequestUsage(
            request_id="req-edit-account-options",
            materialized_step_id="edit-account-options",
            state="materialized",
        ),
        "req-update": RequestUsage(
            request_id="req-update", materialized_step_id="update", state="materialized",
        ),
    }

    result = ensure_grounded_capability_output(spec)

    assert [capability.name for capability in result.capabilities] == [
        "inspect_order", "update_order",
    ]
    inspect, compiled_update = result.capabilities
    assert [
        (ref.request_id, ref.usage) for ref in inspect.request_refs
    ] == [("req-detail", "execute")]
    assert ("req-edit-hydration", "preflight") in [
        (ref.request_id, ref.usage) for ref in compiled_update.request_refs
    ]
    assert ("req-update", "execute") in [
        (ref.request_id, ref.usage) for ref in compiled_update.request_refs
    ]
    assert ("req-edit-account-options", "option_source") in [
        (ref.request_id, ref.usage) for ref in compiled_update.request_refs
    ]
    assert result.meta["capability_model"]["fallback_added_capabilities"] == []
    assert result.meta["capability_model"]["capability_compilation_errors"] == []
    assert result.meta["capability_model"]["retargeted_capabilities"] == [{
        "capability": "inspect_order",
        "from_request_id": "req-edit-hydration",
        "to_request_id": "req-detail",
    }]


def test_submitted_preflight_read_does_not_become_an_extra_public_ability() -> None:
    from dano.execution.page.capability_compiler import ensure_grounded_capability_output

    detail = FlowStep(
        step_id="detail",
        method="GET",
        path="/orders/get?id=69",
        response_json={"data": {"id": 69}},
        source_meta={"request_id": "req-detail", "role": "business_get"},
    )
    hydration = FlowStep(
        step_id="edit-hydration",
        method="GET",
        path="/orders/get?id=70",
        response_json={"data": {"id": 70}},
        source_meta={"request_id": "req-edit-hydration", "role": "business_get"},
    )
    update = FlowStep(
        step_id="update",
        method="PUT",
        path="/orders/update",
        body_source='{"id":70}',
        params=[ParamField(
            path="body.id",
            key="id",
            value=70,
            source_kind="previous_response",
            source={
                "kind": "previous_response",
                "step_id": "edit-hydration",
                "response_path": "data.id",
            },
        )],
        source_meta={"request_id": "req-update", "role": "business_write"},
    )
    plan = {
        "business_understanding": {"business_name": "Orders"},
        "capabilities": [
            {
                "name": "inspect_order",
                "title": "Inspect order",
                "kind": "inspect",
                "anchor_step_id": "detail",
                "request_refs": [{
                    "request_id": "req-detail",
                    "step_id": "detail",
                    "usage": "execute",
                }],
            },
            {
                "name": "update_order",
                "title": "Update order",
                "kind": "update",
                "anchor_step_id": "update",
                "request_refs": [
                    {
                        "request_id": "req-edit-hydration",
                        "step_id": "edit-hydration",
                        "usage": "preflight",
                    },
                    {
                        "request_id": "req-update",
                        "step_id": "update",
                        "usage": "execute",
                    },
                ],
            },
        ],
        "unresolved_items": [],
    }
    spec = FlowSpec(
        steps=[detail, hydration, update],
        meta={"capability_model": {
            "semantic_plan": plan,
            "submitted_semantic_plan": plan,
        }},
    )
    spec.request_facts.requests = [
        RequestFact(
            request_id="req-detail",
            method="GET",
            path="/orders/get",
            url="/orders/get?id=69",
            trigger_action_id="open-detail",
        ),
        RequestFact(
            request_id="req-edit-hydration",
            method="GET",
            path="/orders/get",
            url="/orders/get?id=70",
            trigger_action_id="open-edit",
        ),
        RequestFact(
            request_id="req-update",
            method="PUT",
            path="/orders/update",
            url="/orders/update",
            post_data={"id": 70},
            trigger_action_id="save-edit",
        ),
    ]
    spec.request_facts.analysis = {
        "req-detail": RequestAnalysis(
            request_id="req-detail", role="business_get", keep=True,
        ),
        "req-edit-hydration": RequestAnalysis(
            request_id="req-edit-hydration", role="business_get", keep=True,
        ),
        "req-update": RequestAnalysis(
            request_id="req-update", role="business_write", keep=True,
        ),
    }
    spec.request_facts.usage = {
        "req-detail": RequestUsage(
            request_id="req-detail", materialized_step_id="detail", state="materialized",
        ),
        "req-edit-hydration": RequestUsage(
            request_id="req-edit-hydration",
            materialized_step_id="edit-hydration",
            state="materialized",
        ),
        "req-update": RequestUsage(
            request_id="req-update", materialized_step_id="update", state="materialized",
        ),
    }

    result = ensure_grounded_capability_output(spec)

    assert [capability.name for capability in result.capabilities] == [
        "inspect_order", "update_order",
    ]
    assert result.meta["capability_model"].get("fallback_added_capabilities", []) == []


def test_grounded_fallback_respects_submitted_support_membership() -> None:
    from dano.execution.page.capability_compiler import ensure_grounded_capability_output

    context = FlowStep(
        step_id="detail-context",
        method="GET",
        path="/orders/get",
        source_meta={"request_id": "req-detail-context", "role": "business_get"},
    )
    create = FlowStep(
        step_id="create",
        method="POST",
        path="/orders/create",
        source_meta={"request_id": "req-create", "role": "business_write"},
    )
    plan = {
        "business_understanding": {"business_name": "Orders"},
        "capabilities": [{
            "name": "create_order",
            "title": "Create order",
            "kind": "create",
            "anchor_step_id": "create",
            "request_refs": [
                {
                    "request_id": "req-detail-context",
                    "step_id": "detail-context",
                    "usage": "preflight",
                },
                {
                    "request_id": "req-create",
                    "step_id": "create",
                    "usage": "execute",
                },
            ],
        }],
        "unresolved_items": [],
    }
    spec = FlowSpec(
        steps=[context, create],
        meta={"capability_model": {
            "semantic_plan": plan,
            "submitted_semantic_plan": plan,
        }},
    )
    spec.request_facts.requests = [
        RequestFact(
            request_id="req-detail-context",
            method="GET",
            path="/orders/get",
            trigger_action_id="open-create",
        ),
        RequestFact(
            request_id="req-create",
            method="POST",
            path="/orders/create",
            trigger_action_id="save-create",
        ),
    ]
    spec.request_facts.analysis = {
        "req-detail-context": RequestAnalysis(
            request_id="req-detail-context", role="business_get", keep=True,
        ),
        "req-create": RequestAnalysis(
            request_id="req-create", role="business_write", keep=True,
        ),
    }
    spec.request_facts.usage = {
        "req-detail-context": RequestUsage(
            request_id="req-detail-context",
            materialized_step_id="detail-context",
            state="materialized",
        ),
        "req-create": RequestUsage(
            request_id="req-create", materialized_step_id="create", state="materialized",
        ),
    }

    result = ensure_grounded_capability_output(spec)

    assert [capability.name for capability in result.capabilities] == ["create_order"]
    assert [
        (ref.request_id, ref.usage)
        for ref in result.capabilities[0].request_refs
    ] == [
        ("req-detail-context", "preflight"),
        ("req-create", "execute"),
    ]
    assert result.meta["capability_model"].get("fallback_added_capabilities", []) == []


def test_submission_tool_returns_exact_capability_diagnostics() -> None:
    class Session:
        def __init__(self) -> None:
            self.spec = FlowSpec()
            self.last_submission_kind = ""

        def current_flow_spec(self) -> FlowSpec:
            return self.spec.model_copy(deep=True)

        async def apply_submission(self, *_args, **_kwargs) -> dict:
            return {
                "flow_version": 3,
                "submission_complete": True,
                "submitted_capability_count": 8,
                "materialized_capability_count": 7,
                "missing_submitted_capabilities": ["export_orders"],
                "missing_public_action_request_ids": ["req-export"],
                "field_axis_gaps": [{"step_id": "create", "path": "accountId"}],
            }

    result = asyncio.run(_apply_recording_submission_atomic(
        Session(), {}, mode="plan", base_flow_version=0,
    ))

    assert result["submitted_capability_count"] == 8
    assert result["materialized_capability_count"] == 7
    assert result["missing_submitted_capabilities"] == ["export_orders"]
    assert result["missing_public_action_request_ids"] == ["req-export"]
    assert result["field_axis_gaps"]


def test_final_tail_model_failure_does_not_abort_freeze(monkeypatch) -> None:
    class Capture:
        def captured_all_requests(self) -> list[dict]:
            return [{"request_id": "req-create"}]

        def recorded_page_events(self) -> list[dict]:
            return []

        def recorded_field_evidence(self) -> list[dict]:
            return []

        def recorded_page_enum_options(self) -> dict:
            return {}

    class Pi:
        def __init__(self) -> None:
            self.flow_spec = FlowSpec(meta={
                "capability_model": {
                    "semantic_plan": _capability_plan("create_sale_order", "req-create"),
                },
            })

        def bind_live_recording(self, *_args, **_kwargs) -> None:
            return None

        def current_flow_spec(self) -> FlowSpec:
            return self.flow_spec.model_copy(deep=True)

        async def notify_live_batch(self, _delta: dict) -> dict:
            raise RuntimeError("model timeout")

    pi = Pi()

    async def pi_factory(_fresh: bool) -> Pi:
        return pi

    monkeypatch.setattr(recording_gateway, "emit_run_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(recording_gateway, "emit_run_exception", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(recording_gateway, "note_run_fact", lambda *_args, **_kwargs: None)
    session = RecordingGatewaySession(
        config=RecordingSessionConfig(
            tenant="default",
            subsystem="default",
            recording_id="recording_" + "a" * 32,
            action="record",
            start_url="https://example.test/orders",
        ),
        send=None,
        pi_factory=pi_factory,
        publisher=None,  # type: ignore[arg-type]
    )
    session.capture = Capture()  # type: ignore[assignment]
    session._live_pending_reason = "final_request_tail"

    asyncio.run(session._drain_live())

    assert session._live_pending_reason == ""
    assert session._live_notebook is not None
