from __future__ import annotations

import asyncio

import dano.onboarding.recording_gateway as recording_gateway
from dano.agent_tools.tools import _apply_recording_submission_atomic
from dano.execution.page.capability_compiler import compile_capabilities
from dano.execution.page.capability_io import _sync_capability_io_schemas
from dano.execution.page.capability_contracts import (
    _mark_repeated_write_observations,
    _planned_capability_has_public_anchor,
)
from dano.execution.page.flow_spec_core.models import (
    FlowCapability,
    FlowLink,
    FlowSpec,
    FlowStep,
    ParamField,
    RequestAnalysis,
    RequestFact,
    RequestUsage,
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
