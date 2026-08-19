"""Stage-seven todos stay inside the Stage 6 capability closure."""

from __future__ import annotations

from dano.execution.page.flow_spec import (
    FlowCapability,
    FlowLink,
    FlowSpec,
    FlowStep,
    RequestFact,
    RequestFacts,
    SelectBinding,
)
from dano.onboarding.recording_stage_seven import build_stage_seven_scope
from dano.onboarding.recording_verify import verification_todos


def _spec() -> FlowSpec:
    return FlowSpec(
        tenant="tenant",
        subsystem="oa",
        steps=[
            FlowStep(
                step_id="step_get",
                method="GET",
                path="/erp/sale-order/get",
                source_meta={"request_id": "req_get"},
            ),
            FlowStep(
                step_id="step_edit",
                method="PUT",
                path="/erp/sale-order/update",
                source_meta={"request_id": "req_update"},
                selects=[SelectBinding(path="body.status", source_request_id="req_dict")],
            ),
            FlowStep(
                step_id="step_orphan",
                method="DELETE",
                path="/erp/sale-order/delete-batch",
                source_meta={"request_id": "req_orphan"},
            ),
            FlowStep(
                step_id="step_login",
                method="GET",
                path="/admin-api/system/auth/get-permission-info",
                source_meta={"request_id": "req_login"},
            ),
            FlowStep(
                step_id="step_tenant",
                method="GET",
                path="/admin-api/system/tenant/get-by-website",
                source_meta={"request_id": "req_tenant"},
            ),
            FlowStep(
                step_id="step_dict",
                method="GET",
                path="/admin-api/system/dict-data/simple-list",
                source_meta={"request_id": "req_dict"},
            ),
        ],
        links=[
            FlowLink(
                link_id="link_id",
                source_step_id="step_get",
                source_path="data.id",
                target_step_id="step_edit",
                target_path="query.id",
                confirmed=True,
                evidence={"source_request_id": "req_get", "target_request_id": "req_update"},
            ),
        ],
        capabilities=[
            FlowCapability(
                name="edit_sale_order",
                title="编辑销售订单",
                kind="update",
                capability_id="cap_edit",
                step_ids=["step_edit"],
                request_refs=[],
                nodes=[
                    {
                        "id": "call_1", "type": "call", "usage": "execute",
                        "request_id": "req_update", "method": "PUT",
                        "path": "/erp/sale-order/update", "step_id": "step_edit",
                    },
                ],
            ),
        ],
        meta={"internal_step_ids": ["step_orphan", "step_login", "step_tenant"]},
        request_facts=RequestFacts(
            requests=[
                RequestFact(request_id="req_get", method="GET", path="/erp/sale-order/get"),
                RequestFact(request_id="req_update", method="PUT", path="/erp/sale-order/update"),
                RequestFact(request_id="req_orphan", method="DELETE", path="/erp/sale-order/delete-batch"),
                RequestFact(request_id="req_login", method="GET", path="/admin-api/system/auth/get-permission-info"),
                RequestFact(request_id="req_tenant", method="GET", path="/admin-api/system/tenant/get-by-website"),
                RequestFact(request_id="req_dict", method="GET", path="/admin-api/system/dict-data/simple-list"),
                RequestFact(request_id="req_im", method="GET", path="/admin-api/im/online-status"),
            ],
        ),
    )


def test_orphan_internal_write_does_not_create_write_verify() -> None:
    todos = verification_todos(_spec())
    kinds = {(item.get("kind"), item.get("target_id")) for item in todos}
    assert ("write_verify", "step_orphan") not in kinds
    assert ("write_verify", "step_edit") in kinds


def test_noise_login_tenant_im_requests_are_not_todos() -> None:
    todos = verification_todos(_spec())
    blob = str(todos)
    assert "req_login" not in blob
    assert "req_tenant" not in blob
    assert "req_im" not in blob
    assert "step_login" not in blob
    assert "step_tenant" not in blob


def test_confirmed_upstream_read_enters_same_capability_scope() -> None:
    scope = build_stage_seven_scope(_spec())
    assert "step_get" in scope.member_step_ids
    assert "step_get" in scope.preflight_step_ids
    assert scope.capability_for_step("step_get") == "cap_edit"


def test_option_source_stays_with_using_capability() -> None:
    spec = _spec()
    spec.capabilities[0].request_refs = []
    from dano.execution.page.flow_spec import CapabilityRequestRef

    spec.capabilities[0].request_refs = [
        CapabilityRequestRef(
            request_id="req_update", step_id="step_edit", usage="execute",
            method="PUT", path="/erp/sale-order/update",
        ),
        CapabilityRequestRef(
            request_id="req_dict", step_id="step_dict", usage="option_source",
            method="GET", path="/admin-api/system/dict-data/simple-list",
        ),
    ]
    scope = build_stage_seven_scope(spec)
    assert "step_dict" in scope.option_source_step_ids
    assert "step_dict" in scope.member_step_ids
    assert scope.capability_for_step("step_dict") == "cap_edit"


def test_todos_carry_capability_id_and_target_signature() -> None:
    todos = verification_todos(_spec())
    assert todos
    for todo in todos:
        assert todo.get("capability_id")
        assert todo.get("target_signature")
        assert todo.get("task_id")
        assert todo.get("kind")
        assert todo.get("target_id")


def test_dependency_candidate_does_not_cross_unrelated_capabilities() -> None:
    spec = _spec()
    spec.steps.append(
        FlowStep(
            step_id="step_other",
            method="GET",
            path="/erp/customer/page",
            source_meta={"request_id": "req_other"},
        )
    )
    spec.capabilities.append(
        FlowCapability(
            name="list_customer",
            title="查询客户",
            kind="query_status",
            capability_id="cap_other",
            step_ids=["step_other"],
            nodes=[{
                "id": "call_other", "type": "call", "usage": "execute",
                "request_id": "req_other", "method": "GET",
                "path": "/erp/customer/page", "step_id": "step_other",
            }],
        )
    )
    spec.links.append(
        FlowLink(
            link_id="link_cross",
            source_step_id="step_other",
            source_path="",
            target_step_id="step_edit",
            target_path="",
            confirmed=False,
        )
    )
    todos = verification_todos(spec)
    assert all(item.get("target_id") != "link_cross" for item in todos)
    assert all(item.get("link_id") != "link_cross" for item in todos)
