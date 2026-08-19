"""Deterministic stage-seven triage: compiler binding, empty fields, selectors."""

from __future__ import annotations

from dano.execution.page.capability_compiler import compile_capabilities
from dano.execution.page.flow_spec import (
    CapabilityField,
    FlowCapability,
    FlowLink,
    FlowSpec,
    FlowStep,
    ParamField,
    RequestFact,
    RequestFacts,
    SelectBinding,
    validate_flow_spec,
)
from dano.execution.page.capability_contracts import _capability_execute_record_selector
from dano.onboarding.recording_release import evaluate_recording_release
from dano.onboarding.recording_verify import (
    MACHINE_REPAIR_DISPOSITION,
    assign_unassigned_internal_steps,
    candidate_read_request_ids,
)


def test_option_source_matches_existing_step_by_method_and_path() -> None:
    spec = FlowSpec(
        tenant="tenant",
        subsystem="oa",
        steps=[
            FlowStep(
                step_id="step_dict",
                method="GET",
                path="/admin-api/system/dict-data/simple-list",
                source_meta={"request_id": "req_dict_step"},
            ),
            FlowStep(
                step_id="step_edit",
                method="PUT",
                path="/erp/sale-order/update",
                source_meta={"request_id": "req_update"},
                params=[
                    ParamField(path="body.status", key="status", value="1"),
                ],
                selects=[
                    SelectBinding(path="body.status", source_request_id="req_dict_fact"),
                ],
            ),
        ],
        request_facts=RequestFacts(
            requests=[
                RequestFact(request_id="req_dict_fact", method="GET", path="/admin-api/system/dict-data/simple-list"),
                RequestFact(request_id="req_update", method="PUT", path="/erp/sale-order/update"),
            ],
        ),
    )
    compilation = compile_capabilities(spec, {
        "capabilities": [{
            "name": "edit_sale_order",
            "title": "编辑销售订单",
            "kind": "update",
            "anchor_step_id": "step_edit",
        }],
    })
    assert not compilation.errors
    cap = compilation.spec.capabilities[0]
    option = next(ref for ref in cap.request_refs if ref.usage == "option_source")
    assert option.request_id == "req_dict_fact"
    assert option.step_id == "step_dict"
    option_nodes = [
        node for node in cap.nodes
        if isinstance(node, dict) and node.get("usage") == "option_source"
    ]
    assert option_nodes
    assert all(node.get("step_id") == "step_dict" for node in option_nodes)
    recompiled = compile_capabilities(compilation.spec, {
        "capabilities": [{
            "name": "edit_sale_order",
            "title": "编辑销售订单",
            "kind": "update",
            "anchor_step_id": "step_edit",
        }],
    })
    assert next(
        ref.step_id for ref in recompiled.spec.capabilities[0].request_refs
        if ref.usage == "option_source"
    ) == "step_dict"


def test_option_source_without_step_keeps_ref_and_skips_call_node() -> None:
    spec = FlowSpec(
        tenant="tenant",
        subsystem="oa",
        steps=[
            FlowStep(
                step_id="step_edit",
                method="PUT",
                path="/erp/sale-order/update",
                source_meta={"request_id": "req_update"},
                params=[
                    ParamField(path="body.status", key="status", value="1"),
                ],
                selects=[
                    SelectBinding(path="body.status", source_request_id="req_dict_orphan"),
                ],
            ),
        ],
        request_facts=RequestFacts(
            requests=[
                RequestFact(
                    request_id="req_dict_orphan",
                    method="GET",
                    path="/admin-api/system/dict-data/simple-list",
                ),
                RequestFact(request_id="req_update", method="PUT", path="/erp/sale-order/update"),
            ],
        ),
    )
    compilation = compile_capabilities(spec, {
        "capabilities": [{
            "name": "edit_sale_order",
            "title": "编辑销售订单",
            "kind": "update",
            "anchor_step_id": "step_edit",
        }],
    })
    cap = compilation.spec.capabilities[0]
    option = next(ref for ref in cap.request_refs if ref.usage == "option_source")
    assert option.request_id == "req_dict_orphan"
    assert option.step_id == ""
    assert not [
        node for node in cap.nodes
        if isinstance(node, dict) and node.get("type") == "call" and not node.get("step_id")
    ]
    report = validate_flow_spec(compilation.spec)
    assert not any("未绑定有效接口步骤" in str(item) for item in report.get("errors") or [])


def test_assign_unassigned_confirmed_link_becomes_preflight() -> None:
    spec = FlowSpec(
        tenant="tenant",
        subsystem="oa",
        steps=[
            FlowStep(step_id="step_get", method="GET", path="/erp/sale-order/get"),
            FlowStep(step_id="step_edit", method="PUT", path="/erp/sale-order/update"),
        ],
        links=[
            FlowLink(
                link_id="link_id",
                source_step_id="step_get",
                source_path="data.id",
                target_step_id="step_edit",
                target_path="query.id",
                confirmed=True,
                meta={"verified": True},
            ),
        ],
        capabilities=[
            FlowCapability(
                name="edit_sale_order",
                capability_id="cap_edit",
                kind="update",
                step_ids=["step_edit"],
                nodes=[{
                    "id": "call_1", "type": "call", "usage": "execute",
                    "step_id": "step_edit", "method": "PUT", "path": "/erp/sale-order/update",
                }],
            ),
        ],
    )
    assigned = assign_unassigned_internal_steps(spec)
    cap = assigned.capabilities[0]
    assert "step_get" in cap.step_ids
    assert any(ref.usage == "preflight" and ref.step_id == "step_get" for ref in cap.request_refs)


def test_assign_unassigned_orphan_write_marked_internal() -> None:
    spec = FlowSpec(
        tenant="tenant",
        subsystem="oa",
        steps=[
            FlowStep(step_id="step_edit", method="PUT", path="/erp/sale-order/update"),
            FlowStep(step_id="step_extra", method="DELETE", path="/erp/sale-order/delete-batch"),
        ],
        capabilities=[
            FlowCapability(
                name="edit_sale_order",
                capability_id="cap_edit",
                kind="update",
                step_ids=["step_edit"],
                nodes=[{
                    "id": "call_1", "type": "call", "usage": "execute", "step_id": "step_edit",
                }],
            ),
        ],
    )
    assigned = assign_unassigned_internal_steps(spec)
    assert "step_extra" in assigned.meta["internal_step_ids"]
    assert assigned.capabilities[0].step_ids == ["step_edit"]


def test_empty_recorded_value_without_evidence_stays_unknown() -> None:
    spec = FlowSpec(
        tenant="tenant",
        subsystem="oa",
        steps=[
            FlowStep(
                step_id="step_edit",
                method="PUT",
                path="/erp/sale-order/update",
                params=[
                    ParamField(path="query.remark", key="remark", value="", source_kind="unknown"),
                ],
            ),
        ],
    )
    from dano.onboarding.recording_stage_seven import apply_stage_seven_recorded_evidence_fixes

    fixed = apply_stage_seven_recorded_evidence_fixes(spec)
    param = fixed.steps[0].params[0]
    assert param.source_kind == "unknown"


def test_update_record_id_is_not_internal_field_error() -> None:
    field = CapabilityField(
        scope="input",
        path="query.id",
        key="id",
        exposed_to_caller=True,
        source_kind="user_input",
    )
    cap = FlowCapability(name="edit_sale_order", kind="update", confirmed=True)
    assert _capability_execute_record_selector(cap, field)
    spec = FlowSpec(
        tenant="tenant",
        subsystem="oa",
        steps=[
            FlowStep(
                step_id="step_edit",
                method="PUT",
                path="/erp/sale-order/update",
                params=[ParamField(path="query.id", key="id", value="12", source_kind="user_input")],
            ),
        ],
        capabilities=[
            FlowCapability(
                name="edit_sale_order",
                title="编辑销售订单",
                kind="update",
                confirmed=True,
                capability_id="cap_edit",
                step_ids=["step_edit"],
                nodes=[{
                    "id": "call_1", "type": "call", "usage": "execute",
                    "step_id": "step_edit", "method": "PUT", "path": "/erp/sale-order/update",
                }],
                inputs=[field],
            ),
        ],
    )
    report = validate_flow_spec(spec)
    internal_errors = [
        item for item in ((report.get("capability_validation") or {}).get("capability_internal") or {}).get("errors") or []
        if isinstance(item, dict) and item.get("code") == "capability_internal_field_exposed"
    ]
    assert not internal_errors


def test_candidate_reads_are_same_resource_gets() -> None:
    spec = FlowSpec(
        tenant="tenant",
        subsystem="oa",
        steps=[
            FlowStep(
                step_id="step_edit",
                method="PUT",
                path="/erp/sale-order/update",
                source_meta={"request_id": "req_update"},
            ),
        ],
        request_facts=RequestFacts(
            requests=[
                RequestFact(request_id="req_get", method="GET", path="/erp/sale-order/get"),
                RequestFact(request_id="req_page", method="GET", path="/erp/sale-order/page"),
                RequestFact(request_id="req_tenant", method="GET", path="/admin-api/system/tenant/get-by-website"),
                RequestFact(request_id="req_im", method="GET", path="/admin-api/im/online-status"),
                RequestFact(request_id="req_other", method="GET", path="/crm/customer/get"),
            ],
        ),
    )
    ids = candidate_read_request_ids(spec, spec.steps[0])
    assert ids == ["req_get", "req_page"]
    assert "req_tenant" not in ids
    assert "req_im" not in ids


def test_machine_repair_disposition_table_is_complete() -> None:
    expected = {
        "capability_validation_failed",
        "unassigned_business_step",
        "unassigned_materialized_step",
        "capability_internal_field_exposed",
        "caller_field_not_compiled",
        "public_execute_anchor_invalid",
        "capability_usage_invalid",
        "request_compilation_failed",
        "dry_run_failed",
        "dynamic_structure_binding_missing",
        "dynamic_structure_recorded_key_exposed",
        "dynamic_structure_stale_leaf",
    }
    assert expected <= set(MACHINE_REPAIR_DISPOSITION)
    assert MACHINE_REPAIR_DISPOSITION["dynamic_structure_binding_missing"] == "skill"
    assert MACHINE_REPAIR_DISPOSITION["capability_validation_failed"] == "python"
    decision = evaluate_recording_release(FlowSpec(
        tenant="tenant",
        subsystem="oa",
        steps=[FlowStep(step_id="step_edit", method="PUT", path="/erp/sale-order/update")],
        capabilities=[
            FlowCapability(
                name="edit_sale_order",
                kind="update",
                capability_id="cap_edit",
                step_ids=["step_edit"],
                nodes=[{
                    "id": "call_1", "type": "call", "usage": "execute",
                    "step_id": "step_edit",
                }],
            ),
        ],
    ))
    ghost_ops = {
        "reconcile_capability_membership",
        "reconcile_dynamic_structure",
        "submit_recording_repair",
    }
    for capability in decision.capabilities:
        for issue in capability.issues:
            if MACHINE_REPAIR_DISPOSITION.get(issue.check_code) in {"python", "dead_end"}:
                assert not (set(issue.suggested_operations) & ghost_ops)
