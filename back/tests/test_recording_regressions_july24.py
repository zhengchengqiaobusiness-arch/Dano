import json

import pytest

import dano.execution.page.flow_spec as flow_spec_module
from dano.agent_tools.tools import _normalize_recording_plan_submission
from dano.execution.page.flow_spec import (
    FlowLink,
    FlowSpec,
    FlowStep,
    ParamField,
    build_default_flow_capabilities,
    to_flow_spec,
)


@pytest.mark.parametrize(
    ("method", "path", "locator", "expected_kind"),
    [
        ("GET", "/api/applications/page?pageNo=1", "button=查询", "query_status"),
        ("GET", "/api/applications/export-excel?pageNo=1", "button=导出", "export"),
        ("POST", "/api/applications/create", "button=保存草稿", "save_draft"),
        ("POST", "/api/applications/submit-process", "button=提交", "submit"),
        ("DELETE", "/api/process/cancel", "button=撤回", "withdraw"),
        ("DELETE", "/api/applications/delete?id=1", "button=删除", "delete"),
    ],
)
def test_recorded_operation_keeps_its_business_capability_kind(
    method: str,
    path: str,
    locator: str,
    expected_kind: str,
) -> None:
    step = FlowStep(
        step_id=expected_kind,
        method=method,
        path=path,
        source_meta={
            "role": "business_get" if method == "GET" else "business_write",
            "trigger_op": "click",
            "trigger_locator": locator,
            "trigger_action_id": f"action-{expected_kind}",
            "causality_confidence": "high",
        },
    )
    capabilities = build_default_flow_capabilities(FlowSpec(steps=[step]))
    assert len(capabilities) == 1
    assert capabilities[0].kind == expected_kind


def test_six_recorded_operations_remain_six_capabilities() -> None:
    operations = [
        ("query_status", "GET", "/api/applications/page", "button=查询"),
        ("save_draft", "POST", "/api/applications/create", "button=保存草稿"),
        ("submit", "POST", "/api/applications/submit-process", "button=提交"),
        ("withdraw", "DELETE", "/api/process/cancel", "button=撤回"),
        ("export", "GET", "/api/applications/export-excel", "button=导出"),
        ("delete", "DELETE", "/api/applications/delete?id=1", "button=删除"),
    ]
    spec = FlowSpec(steps=[
        FlowStep(
            step_id=kind,
            method=method,
            path=path,
            source_meta={
                "role": "business_get" if method == "GET" else "business_write",
                "trigger_op": "click",
                "trigger_locator": locator,
                "trigger_action_id": f"action-{kind}",
                "causality_confidence": "high",
            },
        )
        for kind, method, path, locator in operations
    ])
    capabilities = build_default_flow_capabilities(spec)
    assert {cap.kind: tuple(cap.step_ids) for cap in capabilities} == {
        kind: (kind,) for kind, *_ in operations
    }


def test_unexpanded_select_keeps_choice_semantics() -> None:
    [field] = flow_spec_module.flatten_body(
        json.dumps({"roomType": "1"}),
        {},
        set(),
        field_evidence=[{
            "label": "房间类型",
            "control_kind": "select",
            "op": "select",
            "field_aliases": ["roomType"],
        }],
    )
    source = flow_spec_module._param_source_guess(
        field=field,
        path=field["path"],
        key=field["key"],
        method="POST",
        identity_paths=set(),
        system_paths=set(),
        select_paths=set(),
        select_id_paths=set(),
        samples={},
    )
    assert field["suggest_name"] == "房间类型"
    assert field["type"] == "enum"
    assert source["category"] == "user_param"
    assert source["source_kind"] == "form_option"
    assert source["exposed_to_user"] is True


def test_incremental_pi_plan_may_omit_unchanged_sections() -> None:
    normalized = _normalize_recording_plan_submission(
        {
            "semantic_plan": {
                "capabilities": [{
                    "name": "query_records",
                    "kind": "query",
                    "step_ids": ["query"],
                }],
            },
            "ops": [],
        },
        FlowSpec(steps=[FlowStep(
            step_id="query",
            method="GET",
            path="/api/page",
            source_meta={"role": "business_get"},
        )]),
    )
    semantic = normalized["semantic_plan"]
    assert semantic["capabilities"][0]["name"] == "query_records"
    assert semantic["request_roles"][0]["step_id"] == "query"
    assert semantic["field_semantics"] == []
    assert semantic["capability_relations"] == []
    assert semantic["unresolved_items"] == []


def _captured(
    request_id: str,
    sequence: int,
    method: str,
    path: str,
    *,
    transaction: str,
    locator: str,
    role: str,
    body: str | None = None,
) -> dict:
    return {
        "request_id": request_id,
        "index": sequence,
        "sequence": sequence,
        "method": method,
        "url": f"https://example.test{path}",
        "post_data": body,
        "response_json": {"code": 0, "data": {"id": "record-1"}},
        "resource_type": "xhr",
        "trigger_op": "click",
        "trigger_action_id": transaction,
        "trigger_transaction_id": transaction,
        "trigger_locator": locator,
        "_request_role": {
            "role": role,
            "keep": True,
            "confidence": 0.95,
        },
    }


def test_edit_preflight_is_kept_but_post_write_refresh_is_not_executed() -> None:
    requests = [
        _captured(
            "detail", 1, "GET", "/api/applications/get?id=record-1",
            transaction="edit-submit", locator="button=编辑后提交", role="read_context",
        ),
        _captured(
            "submit", 2, "POST", "/api/applications/submit-process",
            transaction="edit-submit", locator="button=编辑后提交", role="business_write",
            body='{"id":"record-1","title":"修改后"}',
        ),
        _captured(
            "refresh", 3, "GET", "/api/applications/page?pageNo=1",
            transaction="edit-submit", locator="button=编辑后提交", role="business_get",
        ),
    ]
    spec = to_flow_spec(captured_requests=requests)
    capabilities = build_default_flow_capabilities(spec)
    assert len(capabilities) == 1
    assert capabilities[0].kind == "submit"
    step_by_request_id = {
        str((step.source_meta or {}).get("request_id")): step
        for step in spec.steps
    }
    assert set(step_by_request_id) == {"detail", "submit"}
    detail = step_by_request_id["detail"]
    submit = step_by_request_id["submit"]
    assert detail.source_meta["control_preflight_for_write_ids"] == [submit.step_id]
    assert set(capabilities[0].step_ids) == {detail.step_id, submit.step_id}


def test_editable_field_uses_upstream_value_as_overrideable_default() -> None:
    detail = FlowStep(
        step_id="detail",
        name="读取申请详情",
        method="GET",
        path="/api/applications/get?id=record-1",
    )
    update = FlowStep(
        step_id="update",
        name="编辑申请",
        method="POST",
        path="/api/applications/update",
        params=[ParamField(
            path="title",
            key="title",
            label="申请标题",
            value="录制旧值",
            category="user_param",
            source_kind="user_input",
            editable=True,
            exposed_to_user=True,
            evidence=[{
                "kind": "page_control",
                "control_kind": "text",
                "editable": True,
                "interacted": True,
            }],
        )],
        sample_inputs={"title": "录制旧值"},
    )
    link = FlowLink(
        link_id="detail-title",
        source_step_id="detail",
        source_path="data.title",
        target_step_id="update",
        target_path="title",
        confirmed=True,
        confidence=0.99,
        reason="自动值匹配",
        evidence={"same_action_chain": True},
    )

    flow_spec_module._apply_link_sources([detail, update], [link])

    field = update.params[0]
    assert field.category == "user_param"
    assert field.source_kind == "previous_response"
    assert field.exposed_to_user is True
    assert field.editable is True
    assert field.source["allow_caller_override"] is True
    assert "显式输入优先" in field.reason
    assert "title" not in update.sample_inputs
