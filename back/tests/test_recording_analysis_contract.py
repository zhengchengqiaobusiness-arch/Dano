from __future__ import annotations

import asyncio
import json

import pytest

from dano.agent_tools.tools import (
    ToolError,
    _canonicalize_recording_plan_aliases,
    _normalize_strict_recording_plan_submission,
    _validate_strict_recording_plan,
)
from dano.execution.page.flow_spec import (
    FlowLink,
    FlowSpec,
    FlowStep,
    ParamField,
    RequestFact,
    RequestFacts,
    _semantic_plan_coverage,
    apply_flow_edits,
    apply_recording_agent_submission,
    flow_spec_to_api_request,
    recording_agent_validation,
    recording_capability_plan_complete,
)
from dano.onboarding.recording_pi import (
    RecordingPiSession,
    recover_recording_analysis_submission,
)
from dano.execution.page.recording_live import merge_live_agent_state


def _plan(*, anchor: str, refs: list[dict[str, str]]) -> dict:
    return {
        "semantic_plan": {
            "business_understanding": {"intent": "查询采购订单"},
            "capabilities": [{
                "name": "query_purchase_orders",
                "title": "查询采购订单",
                "kind": "query_status",
                "anchor_step_id": anchor,
                "request_refs": refs,
            }],
            "unresolved_items": [],
        },
        "ops": [],
    }


def test_live_plan_normalizes_unambiguous_step_prefixed_request_ids() -> None:
    spec = FlowSpec(request_facts=RequestFacts(requests=[
        RequestFact(request_id="req_86", method="GET", path="/purchase-order/page"),
        RequestFact(request_id="req_87", method="GET", path="/purchase-order/page"),
    ]))

    normalized = _normalize_strict_recording_plan_submission(
        _plan(
            anchor="step_req_86",
            refs=[
                {"step_id": "step_req_86", "usage": "execute"},
                {"step_id": "step_req_87", "usage": "preflight"},
            ],
        ),
        spec,
    )

    capability = normalized["semantic_plan"]["capabilities"][0]
    assert capability["anchor_step_id"] == "req_86"
    assert [ref["step_id"] for ref in capability["request_refs"]] == ["req_86", "req_87"]


def test_capability_contract_requires_anchor_as_the_only_execute_request() -> None:
    invalid = _plan(
        anchor="req_86",
        refs=[
            {"step_id": "req_86", "usage": "execute"},
            {"step_id": "req_87", "usage": "execute"},
        ],
    )

    with pytest.raises(ToolError, match="唯一 execute.*anchor_step_id"):
        _validate_strict_recording_plan(invalid)


def test_transport_canonicalization_keeps_anchor_as_the_only_execute_request() -> None:
    submitted = _plan(
        anchor="step_req_86",
        refs=[
            {"step_id": "step_req_86", "usage": "execute"},
            {"step_id": "step_req_87", "usage": "execute"},
        ],
    )

    canonical = _canonicalize_recording_plan_aliases(submitted)
    _validate_strict_recording_plan(canonical)

    assert [
        ref["usage"]
        for ref in canonical["semantic_plan"]["capabilities"][0]["request_refs"]
    ] == ["execute", "preflight"]


def _live_spec_with_goal_count(expected_count: int) -> FlowSpec:
    return FlowSpec(
        request_facts=RequestFacts(requests=[
            RequestFact(request_id="req_86", method="GET", path="/purchase-order/page"),
            RequestFact(request_id="req_93", method="POST", path="/purchase-order/create"),
        ]),
        meta={
            "recording_goal_contract": {
                "source": "pi_normalized_goal",
                "expected_count": expected_count,
                "capabilities": [
                    {"ordinal": 1, "name": "查询采购订单"},
                    {"ordinal": 2, "name": "创建采购订单"},
                ][:expected_count],
            },
        },
    )


def _two_capability_plan() -> dict:
    query = _plan(
        anchor="req_86",
        refs=[{"step_id": "req_86", "usage": "execute"}],
    )["semantic_plan"]
    return {
        "semantic_plan": {
            **query,
            "capabilities": [
                *query["capabilities"],
                {
                    "name": "create_purchase_order",
                    "title": "创建采购订单",
                    "kind": "submit",
                    "anchor_step_id": "req_93",
                    "request_refs": [{"step_id": "req_93", "usage": "execute"}],
                },
            ],
        },
        "ops": [],
    }


def test_complete_pre_materialization_plan_is_terminal_for_the_live_turn() -> None:
    updated = asyncio.run(apply_recording_agent_submission(
        _live_spec_with_goal_count(2),
        submission=_two_capability_plan(),
        mode="plan",
    ))

    validation = recording_agent_validation(updated)
    assert updated.meta["capability_model"]["status"] == "awaiting_materialization"
    assert validation["capability_plan_complete"] is True
    assert validation["submission_complete"] is True


def _accepted_live_plan_pending_compile() -> FlowSpec:
    """Reproduce call-c5e317c0: plan stored, compile/generation still incomplete."""
    plan = _two_capability_plan()["semantic_plan"]
    return FlowSpec(
        request_facts=RequestFacts(requests=[
            RequestFact(request_id="req_86", method="GET", path="/purchase-order/page"),
            RequestFact(request_id="req_93", method="POST", path="/purchase-order/create"),
        ]),
        meta={
            "current_version": 4,
            "capability_model": {
                "status": "needs_review",
                "semantic_plan": plan,
                "semantic_coverage": {
                    "complete": False,
                    "missing": ["request_materialization", "field_axis_contract"],
                },
                "proposal_gate": {
                    "accepted": False,
                    "reasons": ["strict_semantic_plan_required"],
                },
            },
            "capability_generation": {
                "initial_completed": False,
                "status": "incomplete_agent_plan",
            },
        },
    )


def test_accepted_live_plan_is_complete_before_materialization() -> None:
    spec = _accepted_live_plan_pending_compile()

    assert recording_capability_plan_complete(spec) is True
    assert recording_agent_validation(spec)["capability_plan_complete"] is True
    assert recording_agent_validation(spec)["submission_complete"] is True


def test_missing_submission_adopts_already_stored_live_plan() -> None:
    recovered = recover_recording_analysis_submission(
        {
            "status": "missing_submission",
            "error": "recording analysis completed without submit_recording_plan",
        },
        _accepted_live_plan_pending_compile(),
    )

    assert recovered["status"] == "submitted"
    assert recovered["accepted_submission"] == "submit_recording_plan"
    assert "error" not in recovered


def test_missing_submission_still_fails_without_a_stored_plan() -> None:
    recovered = recover_recording_analysis_submission(
        {"status": "missing_submission"},
        FlowSpec(request_facts=RequestFacts(requests=[
            RequestFact(request_id="req_86", method="GET", path="/purchase-order/page"),
        ])),
    )

    assert recovered["status"] == "missing_submission"


@pytest.mark.asyncio
async def test_apply_submission_marks_live_plan_turn_complete() -> None:
    session = RecordingPiSession(
        tenant="tenant-1",
        subsystem="sales",
        recording_id="recording_" + "e" * 32,
    )
    session.bind_flow_spec(_live_spec_with_goal_count(2))
    spec = session.current_flow_spec()
    version = int((spec.meta or {}).get("current_version") or 0)

    validation = await session.apply_submission(
        _two_capability_plan(),
        mode="plan",
        base_flow_version=version,
    )

    assert validation["capability_plan_complete"] is True
    assert validation["submission_complete"] is True
    assert session.last_submission_kind == "plan"


def test_pre_materialization_plan_cannot_drop_a_goal_capability() -> None:
    partial = _two_capability_plan()
    partial["semantic_plan"]["capabilities"] = partial["semantic_plan"]["capabilities"][:1]

    updated = asyncio.run(apply_recording_agent_submission(
        _live_spec_with_goal_count(2),
        submission=partial,
        mode="plan",
    ))

    validation = recording_agent_validation(updated)
    assert validation["capability_plan_complete"] is False
    assert "goal_capability_count" in validation["capability_retry_reasons"]


def test_edit_field_keeps_upstream_default_and_caller_override_axes_separate() -> None:
    spec = FlowSpec(
        steps=[
            FlowStep(
                step_id="detail",
                method="GET",
                path="/leave/get",
                response_json={"data": {"reason": "原原因", "processStatus": 0}},
                source_meta={"request_id": "req-detail"},
            ),
            FlowStep(
                step_id="edit-save",
                method="POST",
                path="/leave/update",
                params=[
                    ParamField(path="reason", key="reason", value="修改后的原因"),
                    ParamField(path="processStatus", key="processStatus", value=0),
                ],
                source_meta={"request_id": "req-edit-save"},
            ),
        ],
        request_facts=RequestFacts(
            requests=[
                RequestFact(
                    request_id="req-detail",
                    method="GET",
                    path="/leave/get",
                    response_json={"data": {"reason": "原原因", "processStatus": 0}},
                ),
                RequestFact(
                    request_id="req-edit-save",
                    method="POST",
                    path="/leave/update",
                    post_data={"reason": "修改后的原因", "processStatus": 0},
                ),
            ],
            field_evidence=[{
                "evidence_id": "evt-reason",
                "event_id": "evt-reason",
                "request_id": "req-edit-save",
                "wire_path": "body.reason",
                "label": "请假原因",
                "op": "fill",
                "editable": True,
                "recorded_user_input": True,
                "required": False,
                "binding_status": "bound",
            }],
        ),
    )

    updated = apply_flow_edits(spec, [
        {
            "op": "set_param_source",
            "request_id": "req-edit-save",
            "wire_path": "body.reason",
            "source_kind": "response_binding",
            "origin_request_id": "req-detail",
            "origin_path": "data.reason",
            "reason": "详情响应初始化，页面控件仍允许修改",
            "evidence_refs": ["evt-reason", "req-detail"],
        },
        {
            "op": "rename_field",
            "request_id": "req-edit-save",
            "wire_path": "body.reason",
            "label": "请假原因",
            "reason": "页面标签",
            "evidence_refs": ["evt-reason"],
        },
        {
            "op": "set_param_required",
            "request_id": "req-edit-save",
            "wire_path": "body.reason",
            "required": False,
            "reason": "页面没有必填标记",
            "evidence_refs": ["evt-reason"],
        },
        {
            "op": "set_param_source",
            "request_id": "req-edit-save",
            "wire_path": "body.processStatus",
            "source_kind": "response_binding",
            "origin_request_id": "req-detail",
            "origin_path": "data.processStatus",
            "reason": "流程状态由详情响应提供且没有可编辑控件",
            "evidence_refs": ["req-detail"],
        },
    ])

    fields = {param.path: param for param in updated.steps[1].params}
    reason = fields["reason"]
    assert (reason.label, reason.required) == ("请假原因", False)
    assert reason.source_kind == "previous_response"
    assert reason.source["allow_caller_override"] is True
    assert reason.category == "user_param" and reason.exposed_to_user is True
    status = fields["processStatus"]
    assert status.source_kind == "previous_response"
    assert status.source["allow_caller_override"] is False
    assert status.category == "runtime_var" and status.exposed_to_user is False
    assert {
        (link.source_step_id, link.source_path, link.target_step_id, link.target_path)
        for link in updated.links
    } >= {
        ("detail", "data.reason", "edit-save", "reason"),
        ("detail", "data.processStatus", "edit-save", "processStatus"),
    }


def test_leave_recording_materializes_all_eight_distinct_business_capabilities() -> None:
    contracts = [
        ("query_leave", "查询请假申请", "query_status", "req-query"),
        ("save_leave_draft", "保存请假草稿", "save_draft", "req-draft"),
        ("submit_leave", "提交请假申请", "submit", "req-submit"),
        ("inspect_leave", "查看申请详情", "inspect", "req-detail"),
        ("edit_save_leave", "编辑草稿,草稿保存", "update", "req-edit-save"),
        ("withdraw_leave", "撤回请假申请", "withdraw", "req-withdraw"),
        ("delete_leave", "删除请假申请", "delete", "req-delete"),
        ("edit_submit_leave", "编辑草稿,提交保存", "update", "req-edit-submit"),
    ]
    goal_text = "\n".join([
        "预期产出能力数量：8",
        *(f"能力{index}：{title}" for index, (_name, title, _kind, _request) in enumerate(
            contracts, start=1,
        )),
    ])
    live = FlowSpec(meta={
        "recording_goal_text": goal_text,
        "capability_model": {
            "status": "awaiting_materialization",
            "semantic_plan": {
                "business_understanding": {"intent": "管理请假申请"},
                "capabilities": [
                    {
                        "name": name,
                        "title": title,
                        "kind": kind,
                        "anchor_step_id": request_id,
                        "request_refs": [{"step_id": request_id, "usage": "execute"}],
                    }
                    for name, title, kind, request_id in contracts
                ],
                "unresolved_items": [],
            },
        },
    })

    def meta(request_id: str, index: int, label: str, role: str) -> dict:
        return {
            "request_id": request_id,
            "request_index": index,
            "sequence": index,
            "role": role,
            "trigger_op": "click",
            "trigger_locator": f"text={label}",
            "trigger_transaction_id": f"txn-{request_id}",
            "causality_confidence": "high",
        }

    finalized = FlowSpec(
        steps=[
            FlowStep(step_id="query", method="GET", path="/leave/page", source_meta=meta("req-query", 1, "搜索", "business_get")),
            FlowStep(step_id="draft", method="POST", path="/leave/create", source_meta=meta("req-draft", 2, "保存草稿", "business_write")),
            FlowStep(step_id="submit", method="POST", path="/leave/submit-process", source_meta=meta("req-submit", 3, "提交", "business_write")),
            FlowStep(
                step_id="detail", method="GET", path="/leave/get?id=42",
                response_json={"data": {"id": 42, "reason": "原原因", "processStatus": 0}},
                source_meta=meta("req-detail", 4, "查看详情", "business_get"),
            ),
            FlowStep(
                step_id="edit-save", method="POST", path="/leave/update",
                params=[
                    ParamField(
                        path="reason", key="reason", label="请假原因", value="新原因",
                        category="user_param", source_kind="previous_response",
                        source={
                            "kind": "previous_response", "step_id": "detail",
                            "response_path": "data.reason", "allow_caller_override": True,
                        },
                        required=False, editable=True, exposed_to_user=True,
                    ),
                    ParamField(
                        path="processStatus", key="processStatus", value=0,
                        category="runtime_var", source_kind="previous_response",
                        source={
                            "kind": "previous_response", "step_id": "detail",
                            "response_path": "data.processStatus",
                        },
                        required=False, editable=False, exposed_to_user=False,
                    ),
                ],
                source_meta=meta("req-edit-save", 5, "保存草稿", "business_write"),
            ),
            FlowStep(step_id="withdraw", method="DELETE", path="/process/cancel", source_meta=meta("req-withdraw", 6, "撤回", "business_write")),
            FlowStep(step_id="delete", method="DELETE", path="/leave/delete?id=42", source_meta=meta("req-delete", 7, "删除", "business_write")),
            FlowStep(
                step_id="edit-detail", method="GET", path="/leave/get?id=43",
                response_json={"data": {"id": 43, "reason": "旧原因", "processStatus": 0}},
                source_meta={"request_id": "req-edit-detail", "role": "read_context"},
            ),
            FlowStep(
                step_id="edit-submit", method="POST", path="/leave/submit-process",
                params=[
                    ParamField(
                        path="reason", key="reason", label="请假原因", value="提交原因",
                        category="user_param", source_kind="previous_response",
                        source={
                            "kind": "previous_response", "step_id": "edit-detail",
                            "response_path": "data.reason", "allow_caller_override": True,
                        },
                        required=True, editable=True, exposed_to_user=True,
                    ),
                    ParamField(
                        path="processStatus", key="processStatus", value=0,
                        category="runtime_var", source_kind="previous_response",
                        source={
                            "kind": "previous_response", "step_id": "edit-detail",
                            "response_path": "data.processStatus",
                        },
                        required=False, editable=False, exposed_to_user=False,
                    ),
                ],
                source_meta=meta("req-edit-submit", 9, "提交", "business_write"),
            ),
        ],
        links=[
            FlowLink(
                source_step_id="detail", source_path="data.reason",
                target_step_id="edit-save", target_path="reason",
                confirmed=True, confidence=0.99,
                evidence={"kind": "record_hydration", "identity_paths": ["data.id"]},
                meta={"actor": "capture", "captured_record_hydration": True},
            ),
            FlowLink(
                source_step_id="edit-detail", source_path="data.reason",
                target_step_id="edit-submit", target_path="reason",
                confirmed=True, confidence=0.99,
                evidence={"kind": "record_hydration", "identity_paths": ["data.id"]},
                meta={"actor": "capture", "captured_record_hydration": True},
            ),
            FlowLink(
                source_step_id="detail", source_path="data.processStatus",
                target_step_id="edit-save", target_path="processStatus",
                confirmed=True, confidence=0.99,
                evidence={"kind": "record_hydration", "identity_paths": ["data.id"]},
                meta={"actor": "capture", "captured_record_hydration": True},
            ),
            FlowLink(
                source_step_id="edit-detail", source_path="data.processStatus",
                target_step_id="edit-submit", target_path="processStatus",
                confirmed=True, confidence=0.99,
                evidence={"kind": "record_hydration", "identity_paths": ["data.id"]},
                meta={"actor": "capture", "captured_record_hydration": True},
            ),
        ],
    )

    merged = merge_live_agent_state(live, finalized)

    assert [capability.title for capability in merged.capabilities] == [
        title for _name, title, _kind, _request in contracts
    ]
    execute_steps = [
        next(ref.step_id for ref in capability.request_refs if ref.usage == "execute")
        for capability in merged.capabilities
    ]
    assert execute_steps == [
        "query", "draft", "submit", "detail", "edit-save", "withdraw", "delete", "edit-submit",
    ]
    assert len(set(execute_steps)) == 8
    assert merged.meta["recording_goal_contract"]["satisfied"] is True
    refs_by_title = {
        capability.title: {(ref.step_id, ref.usage) for ref in capability.request_refs}
        for capability in merged.capabilities
    }
    assert ("detail", "preflight") in refs_by_title["编辑草稿,草稿保存"]
    assert ("edit-detail", "preflight") in refs_by_title["编辑草稿,提交保存"]


def test_unknown_origin_is_a_finished_field_contract() -> None:
    spec = FlowSpec(
        steps=[
            FlowStep(
                step_id="create",
                method="POST",
                path="/sale-order/create",
                source_meta={"request_id": "req_create", "role": "business_write"},
                params=[
                    ParamField(
                        path="body.note", key="note", label="备注", type="string",
                        category="user_param", source_kind="user_input",
                        required=False, exposed_to_user=True, value="hello",
                    ),
                    ParamField(
                        path="body.hiddenToken", key="hiddenToken", label="hiddenToken",
                        type="string", category="runtime_var", source_kind="unknown",
                        required=False, exposed_to_user=False, value="tok-1",
                    ),
                ],
            ),
        ],
    )
    coverage = _semantic_plan_coverage(spec, {
        "semantic_plan": {
            "business_understanding": {"intent": "新增销售订单", "business_name": "销售订单"},
            "capabilities": [{
                "name": "create_sale_order",
                "title": "新增销售订单",
                "kind": "create",
                "anchor_step_id": "create",
                "request_refs": [{"step_id": "create", "usage": "execute"}],
            }],
            "unresolved_items": [],
        },
    })

    assert "field_axis_contract" not in coverage["missing"]
    assert coverage["complete"] is True


def test_live_plan_keeps_capabilities_before_steps_materialize() -> None:
    updated = asyncio.run(apply_recording_agent_submission(
        FlowSpec(request_facts=RequestFacts(requests=[
            RequestFact(request_id="req_86", method="GET", path="/sale-order/page"),
            RequestFact(request_id="req_93", method="POST", path="/sale-order/create"),
        ])),
        submission=_two_capability_plan(),
        mode="plan",
    ))

    validation = recording_agent_validation(updated)
    stored = (updated.meta.get("capability_model") or {}).get("semantic_plan") or {}
    assert [item["name"] for item in stored.get("capabilities") or []] == [
        "query_purchase_orders", "create_purchase_order",
    ]
    assert updated.meta["capability_model"]["status"] == "awaiting_materialization"
    assert validation["capability_plan_complete"] is True


def test_unknown_request_leaves_do_not_block_stage_six_compile_or_publish() -> None:
    live = FlowSpec(meta={
        "capability_model": {
            "status": "awaiting_materialization",
            "semantic_plan": {
                "business_understanding": {
                    "intent": "新增销售订单",
                    "business_name": "销售订单",
                },
                "capabilities": [{
                    "name": "create_sale_order",
                    "title": "新增销售订单",
                    "kind": "create",
                    "anchor_step_id": "req_create",
                    "request_refs": [{"step_id": "req_create", "usage": "execute"}],
                }],
                "unresolved_items": [],
            },
        },
    })
    finalized = FlowSpec(
        title="销售订单",
        steps=[
            FlowStep(
                step_id="create",
                method="POST",
                path="/sale-order/create",
                url="http://example.test/sale-order/create",
                source_meta={"request_id": "req_create", "role": "business_write"},
                body_source=json.dumps({"qty": 1, "hiddenToken": "tok-1"}),
                params=[
                    ParamField(
                        path="body.qty", key="qty", label="数量", type="number",
                        category="user_param", source_kind="page_default",
                        required=False, exposed_to_user=True, editable=True,
                        value=1,
                    ),
                    ParamField(
                        path="body.hiddenToken", key="hiddenToken", label="hiddenToken",
                        type="string", category="runtime_var", source_kind="unknown",
                        required=False, exposed_to_user=False, value="tok-1",
                    ),
                ],
            ),
        ],
        request_facts=RequestFacts(requests=[
            RequestFact(request_id="req_create", method="POST", path="/sale-order/create"),
        ]),
    )

    merged = merge_live_agent_state(live, finalized)
    assert [capability.name for capability in merged.capabilities] == ["create_sale_order"]
    api_request, errors = flow_spec_to_api_request(merged)
    assert errors == []
    assert api_request is not None
    body = api_request.get("body_template") or {}
    assert str(body.get("hiddenToken") or "") == "tok-1"
    assert body.get("qty") == 1


def _live_plan(*capabilities: tuple[str, str, str, str]) -> FlowSpec:
    return FlowSpec(meta={
        "capability_model": {
            "status": "awaiting_materialization",
            "semantic_plan": {
                "business_understanding": {
                    "intent": "销售订单",
                    "business_name": "销售订单",
                },
                "capabilities": [
                    {
                        "name": name,
                        "title": title,
                        "kind": kind,
                        "anchor_step_id": request_id,
                        "request_refs": [{"step_id": request_id, "usage": "execute"}],
                    }
                    for name, title, kind, request_id in capabilities
                ],
                "unresolved_items": [],
            },
        },
    })


def _finalize_step(
    step_id: str,
    request_id: str,
    method: str,
    path: str,
    *,
    role: str,
    query: dict | None = None,
) -> FlowStep:
    url = path if not query else path + "?" + "&".join(
        f"{key}={value}" for key, value in query.items()
    )
    return FlowStep(
        step_id=step_id,
        method=method,
        path=url,
        url=f"http://example.test{url}",
        source_meta={
            "request_id": request_id,
            "role": role,
            "query": dict(query or {}),
        },
    )


def test_finalize_keeps_other_capabilities_when_one_anchor_is_missing() -> None:
    live = _live_plan(
        ("sale-order-query", "查询销售订单", "query", "req_86"),
        ("sale-order-detail", "查看销售订单", "inspect", "req_88"),
        ("sale-order-update", "编辑销售订单", "update", "req_98"),
        ("sale-order-delete", "删除销售订单", "delete", "req_100"),
    )
    finalized = FlowSpec(
        title="销售订单",
        steps=[
            _finalize_step("query", "req_86", "GET", "/admin-api/erp/sale-order/page", role="business_get"),
            _finalize_step("update", "req_98", "PUT", "/admin-api/erp/sale-order/update", role="business_write"),
            _finalize_step("delete", "req_100", "DELETE", "/admin-api/erp/sale-order/delete", role="business_write"),
        ],
        request_facts=RequestFacts(requests=[
            RequestFact(request_id="req_86", method="GET", path="/admin-api/erp/sale-order/page"),
            RequestFact(request_id="req_88", method="GET", path="/admin-api/erp/sale-order/get", query={"id": "32"}),
            RequestFact(request_id="req_98", method="PUT", path="/admin-api/erp/sale-order/update"),
            RequestFact(request_id="req_100", method="DELETE", path="/admin-api/erp/sale-order/delete"),
        ]),
    )

    merged = merge_live_agent_state(live, finalized)

    assert [capability.name for capability in merged.capabilities] == [
        "sale-order-query", "sale-order-update", "sale-order-delete",
    ]
    unresolved = merged.meta.get("unresolved_live_agent_ops") or []
    assert any(
        item.get("reason") == "live capability anchors were not materialized at finalize"
        and "req_88" in ((item.get("requested_target") or {}).get("anchor_step_ids") or [])
        for item in unresolved
    )


def test_finalize_remaps_collapsed_detail_anchor_to_equivalent_get() -> None:
    live = _live_plan(
        ("sale-order-query", "查询销售订单", "query", "req_86"),
        ("sale-order-detail", "查看销售订单", "inspect", "req_88"),
        ("sale-order-update", "编辑销售订单", "update", "req_98"),
        ("sale-order-delete", "删除销售订单", "delete", "req_100"),
        ("sale-order-reject", "反审批销售订单", "reject", "req_101"),
    )
    finalized = FlowSpec(
        title="销售订单",
        steps=[
            _finalize_step("query", "req_86", "GET", "/admin-api/erp/sale-order/page", role="business_get"),
            _finalize_step(
                "hydrate", "req_93", "GET", "/admin-api/erp/sale-order/get",
                role="read_context", query={"id": "32"},
            ),
            _finalize_step(
                "inspect-later", "req_103", "GET", "/admin-api/erp/sale-order/get",
                role="business_get", query={"id": "37"},
            ),
            _finalize_step("update", "req_98", "PUT", "/admin-api/erp/sale-order/update", role="business_write"),
            _finalize_step("delete", "req_100", "DELETE", "/admin-api/erp/sale-order/delete", role="business_write"),
            _finalize_step(
                "reject", "req_101", "PUT", "/admin-api/erp/sale-order/update-status",
                role="business_write", query={"id": "36", "status": "10"},
            ),
        ],
        request_facts=RequestFacts(requests=[
            RequestFact(request_id="req_86", method="GET", path="/admin-api/erp/sale-order/page"),
            RequestFact(request_id="req_88", method="GET", path="/admin-api/erp/sale-order/get", query={"id": "32"}),
            RequestFact(request_id="req_93", method="GET", path="/admin-api/erp/sale-order/get", query={"id": "32"}),
            RequestFact(request_id="req_98", method="PUT", path="/admin-api/erp/sale-order/update"),
            RequestFact(request_id="req_100", method="DELETE", path="/admin-api/erp/sale-order/delete"),
            RequestFact(
                request_id="req_101", method="PUT", path="/admin-api/erp/sale-order/update-status",
                query={"id": "36", "status": "10"},
            ),
            RequestFact(request_id="req_103", method="GET", path="/admin-api/erp/sale-order/get", query={"id": "37"}),
        ]),
    )

    merged = merge_live_agent_state(live, finalized)

    assert [capability.name for capability in merged.capabilities] == [
        "sale-order-query",
        "sale-order-detail",
        "sale-order-update",
        "sale-order-delete",
        "sale-order-reject",
    ]
    detail = next(item for item in merged.capabilities if item.name == "sale-order-detail")
    execute = next(ref for ref in detail.request_refs if ref.usage == "execute")
    assert execute.step_id == "hydrate"
    assert not any(
        item.get("reason") == "live capability anchors were not materialized at finalize"
        for item in (merged.meta.get("unresolved_live_agent_ops") or [])
    )
