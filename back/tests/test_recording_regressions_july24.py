import json

import dano.execution.page.flow_spec as flow_spec_module
from dano.execution.page.flow_spec import (
    FlowLink,
    FlowCapability,
    FlowStep,
    ParamField,
    to_flow_spec,
)


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
    step_by_request_id = {
        str((step.source_meta or {}).get("request_id")): step
        for step in spec.steps
    }
    assert set(step_by_request_id) == {"detail", "submit"}
    detail = step_by_request_id["detail"]
    submit = step_by_request_id["submit"]
    assert detail.source_meta["control_preflight_for_write_ids"] == [submit.step_id]
    capability = FlowCapability(
        name="submit_application",
        title="提交申请",
        kind="submit",
        nodes=[
            {"id": "call_detail", "type": "call", "step_id": detail.step_id},
            {"id": "call_submit", "type": "call", "step_id": submit.step_id},
        ],
    )
    capability = flow_spec_module.sync_flow_spec_models(
        flow_spec_module.FlowSpec(steps=[detail, submit], capabilities=[capability])
    ).capabilities[0]
    assert set(capability.step_ids) == {detail.step_id, submit.step_id}


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
