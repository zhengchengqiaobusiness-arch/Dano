"""Step B · FlowSpec 字段/link/step 编辑测试。"""

import pytest

from dano.execution.page.flow_spec import (
    FlowSpec, FlowStep, FlowLink, ParamField, SelectBinding,
    apply_flow_edits, validate_flow_spec, _infer_type_from_value,
    add_llm_review_recommendations, refresh_review_items, flow_spec_to_api_request,
)


def _make_spec():
    param1 = ParamField(path="form.userId", key="userId", value="123", type="string", required=True)
    param2 = ParamField(path="form.name", key="name", value="test", type="string", required=True)
    step1 = FlowStep(
        step_id="step1", method="POST", url="/api/submit", path="/api/submit",
        params=[param1, param2], risk_level="L3", sample_inputs={"userId": "123", "name": "test"},
    )
    return FlowSpec(flow_id="test", steps=[step1])


# ── Param 编辑 ──
def test_edit_key():
    spec = _make_spec()
    new = apply_flow_edits(spec, [{"op": "update", "step_id": "step1",
                                   "param_path": "form.userId", "field": "key", "value": "newUserId"}])
    assert spec.steps[0].params[0].key == "userId"
    assert new.steps[0].params[0].key == "newUserId"
    assert new.steps[0].params[0].name_source == "manual"
    assert new.meta["current_version"] == 1
    assert new.meta["versions"][0]["action"] == "flow_edit"


def test_edit_key_syncs_label_select_and_exported_api_request():
    param = ParamField(
        path="form.systemName",
        key="oldName",
        label="oldName",
        value="系统A",
        type="string",
        required=True,
        category="user_param",
        source_kind="form_option",
    )
    step = FlowStep(
        step_id="step1",
        method="POST",
        url="/api/submit",
        path="/api/submit",
        body_source='{"form":{"systemName":"系统A","systemId":"id-a"}}',
        params=[param],
        selects=[SelectBinding(
            param="staleAutoName",
            path="form.systemName",
            source_url="/api/options",
            value_key="id",
            label_key="name",
            id_path="form.systemId",
        )],
        sample_inputs={"oldName": "系统A"},
    )
    spec = FlowSpec(flow_id="f", steps=[step])

    new = apply_flow_edits(spec, [{
        "op": "update",
        "step_id": "step1",
        "param_path": "form.systemName",
        "field": "key",
        "value": "应用系统名称",
    }])
    assert new.steps[0].params[0].label == "应用系统名称"
    assert new.steps[0].selects[0].param == "应用系统名称"

    apir, errors = flow_spec_to_api_request(new)

    assert errors == []
    assert apir["params"] == ["应用系统名称"]
    assert apir["sample_inputs"] == {"应用系统名称": "系统A"}
    assert apir["selects"][0]["param"] == "应用系统名称"


def test_edit_param_path_syncs_select_and_target_link():
    step1 = FlowStep(
        step_id="read",
        method="GET",
        url="/api/read",
        path="/api/read",
        response_json={"data": {"id": "A-1"}},
    )
    step2 = FlowStep(
        step_id="write",
        method="POST",
        url="/api/submit",
        path="/api/submit",
        body_source='{"form":{"oldPath":"系统A","systemId":"id-a"}}',
        params=[ParamField(
            path="form.oldPath",
            key="系统名称",
            value="系统A",
            type="enum",
            category="user_param",
            source_kind="form_option",
        )],
        selects=[SelectBinding(
            param="系统名称",
            path="form.oldPath",
            source_url="/api/options",
            value_key="id",
            label_key="name",
            id_path="form.systemId",
        )],
    )
    spec = FlowSpec(
        flow_id="f",
        steps=[step1, step2],
        links=[FlowLink(
            link_id="l1",
            source_step_id="read",
            source_path="data.id",
            target_step_id="write",
            target_path="form.oldPath",
        )],
    )

    new = apply_flow_edits(spec, [{
        "op": "update",
        "step_id": "write",
        "param_path": "form.oldPath",
        "field": "path",
        "value": "form.systemName",
    }])

    assert new.steps[1].params[0].path == "form.systemName"
    assert new.steps[1].selects[0].path == "form.systemName"
    assert new.links[0].target_path == "form.systemName"


def test_static_enum_options_on_param_are_exported_as_selects():
    param = ParamField(
        path="form.leaveType",
        key="请假类型",
        label="请假类型",
        value="事假",
        type="enum",
        required=True,
        category="user_param",
        source_kind="form_option",
        enum_options=["事假", "病假", "年假"],
    )
    step = FlowStep(
        step_id="step1",
        method="POST",
        url="/api/submit",
        path="/api/submit",
        body_source='{"form":{"leaveType":"事假"}}',
        params=[param],
        sample_inputs={"请假类型": "事假"},
    )
    spec = FlowSpec(flow_id="f", steps=[step])

    apir, errors = flow_spec_to_api_request(spec)

    assert errors == []
    assert apir["params"] == ["请假类型"]
    assert apir["field_types"]["请假类型"] == "enum"
    assert apir["selects"][0]["param"] == "请假类型"
    assert apir["selects"][0]["options"] == ["事假", "病假", "年假"]
    assert apir["selects"][0]["enum_source"] == "manual"
    assert apir["selects"][0]["enum_confirmed"] is True


def test_update_select_binding_from_frontend_dicts_is_validated_and_exported():
    param = ParamField(
        path="form.approverId",
        key="审批人",
        label="审批人",
        value="张三",
        type="enum",
        required=True,
        category="user_param",
        source_kind="form_option",
    )
    step = FlowStep(
        step_id="step1",
        method="POST",
        url="/api/submit",
        path="/api/submit",
        body_source='{"form":{"approverId":"115"}}',
        params=[param],
        sample_inputs={"审批人": "张三"},
    )
    spec = FlowSpec(flow_id="f", steps=[step])

    new = apply_flow_edits(spec, [{
        "op": "update",
        "step_id": "step1",
        "field": "selects",
        "value": [{
            "param": "审批人",
            "path": "form.approverId",
            "source_url": "/admin-api/system/user/page?pageNo=1&pageSize=10",
            "value_key": "id",
            "label_key": "nickname",
            "options": ["张三", "李四"],
            "count": 2,
        }],
    }])

    assert isinstance(new.steps[0].selects[0], SelectBinding)
    assert new.steps[0].selects[0].value_key == "id"

    apir, errors = flow_spec_to_api_request(new)

    assert errors == []
    assert apir["selects"][0]["source_url"] == "/admin-api/system/user/page?pageNo=1&pageSize=10"
    assert apir["selects"][0]["value_key"] == "id"
    assert apir["selects"][0]["label_key"] == "nickname"
    assert apir["field_types"]["审批人"] == "enum"


def test_edit_required():
    new = apply_flow_edits(_make_spec(), [{"op": "update", "step_id": "step1",
                                           "param_path": "form.userId", "field": "required", "value": False}])
    assert new.steps[0].params[0].required is False


def test_edit_value():
    new = apply_flow_edits(_make_spec(), [{"op": "update", "step_id": "step1",
                                           "param_path": "form.userId", "field": "value", "value": "456"}])
    assert new.steps[0].params[0].value == "456"
    assert new.steps[0].sample_inputs["userId"] == "456"


def test_edit_type():
    new = apply_flow_edits(_make_spec(), [{"op": "update", "step_id": "step1",
                                           "param_path": "form.userId", "field": "type", "value": "number"}])
    assert new.steps[0].params[0].type == "number"


def test_add_param():
    new = apply_flow_edits(_make_spec(), [{"op": "add", "step_id": "step1", "param": {
        "path": "form.email", "key": "email", "value": "test@example.com",
        "type": "string", "required": False}}])
    assert len(new.steps[0].params) == 3
    assert new.steps[0].sample_inputs["email"] == "test@example.com"


def test_remove_param():
    new = apply_flow_edits(_make_spec(), [{"op": "remove", "step_id": "step1", "param_path": "form.name"}])
    assert len(new.steps[0].params) == 1
    assert "name" not in new.steps[0].sample_inputs


def test_nonexistent_step_lists_available():
    """Bug 修复:step not found 错误含可用 step_id 列表,前端据此自动同步。"""
    spec = _make_spec()
    with pytest.raises(ValueError) as exc:
        apply_flow_edits(spec, [{"op": "update", "step_id": "nope", "field": "url", "value": "/x"}])
    msg = str(exc.value)
    assert "available:" in msg
    assert "step1" in msg


# ── Step 编辑 ──
def test_edit_url():
    new = apply_flow_edits(_make_spec(), [{"op": "update", "step_id": "step1",
                                           "field": "url", "value": "/api/v2/submit"}])
    assert new.steps[0].url == "/api/v2/submit"


def test_edit_method():
    new = apply_flow_edits(_make_spec(), [{"op": "update", "step_id": "step1",
                                           "field": "method", "value": "PUT"}])
    assert new.steps[0].method == "PUT"


def test_edit_headers():
    new = apply_flow_edits(_make_spec(), [{"op": "update", "step_id": "step1",
                                           "field": "headers", "value": {"X-Foo": "bar"}}])
    assert new.steps[0].headers == {"X-Foo": "bar"}


def test_edit_step_role_updates_source_meta_and_semantic_role():
    new = apply_flow_edits(_make_spec(), [{"op": "update", "step_id": "step1",
                                           "field": "role", "value": "submit_anchor"}])
    assert new.steps[0].source_meta["role"] == "submit_anchor"
    assert new.steps[0].semantic_role == "submit_anchor"


def test_update_flow_business_description():
    new = apply_flow_edits(_make_spec(), [{
        "op": "update_flow",
        "field": "business_description",
        "value": "人工修正说明",
    }])
    assert new.business_description == "人工修正说明"


# ── Reorder ──
def _three_step_spec():
    def _st(sid, p):
        return FlowStep(step_id=sid, name=sid, method="POST", url=p, path=p,
                        params=[ParamField(path="x", key="x", value="1", type="string", required=True)])
    return FlowSpec(flow_id="f", steps=[_st("A", "/a"), _st("B", "/b"), _st("C", "/c")])


def test_reorder_basic():
    spec = _three_step_spec()
    new = apply_flow_edits(spec, [{"op": "reorder_steps", "step_ids": ["C", "B", "A"]}])
    assert [s.step_id for s in new.steps] == ["C", "B", "A"]
    assert [s.step_id for s in spec.steps] == ["A", "B", "C"]


def test_reorder_missing_raises():
    spec = _three_step_spec()
    with pytest.raises(ValueError, match="reorder_steps"):
        apply_flow_edits(spec, [{"op": "reorder_steps", "step_ids": ["A", "B"]}])


def test_remove_step_removes_related_links():
    spec = _three_step_spec()
    spec.links = [
        FlowLink(link_id="ab", source_step_id="A", source_path="data.x", target_step_id="B", target_path="x"),
        FlowLink(link_id="bc", source_step_id="B", source_path="data.y", target_step_id="C", target_path="x"),
    ]

    new = apply_flow_edits(spec, [{"op": "remove_step", "step_id": "B"}])

    assert [s.step_id for s in new.steps] == ["A", "C"]
    assert new.links == []
    assert [s.step_id for s in spec.steps] == ["A", "B", "C"]


def test_dedupe_steps_keeps_latest_repeated_read_step():
    def _get(sid, url):
        return FlowStep(
            step_id=sid,
            name=sid,
            method="GET",
            url=url,
            path=url,
            source_meta={"role": "business_get"},
            params=[ParamField(path="query.day", key="day", value="1", type="number")],
        )

    spec = FlowSpec(flow_id="f", steps=[
        _get("old1", "/admin-api/bpm/process-instance/get-approval-detail?processVariablesStr=null"),
        _get("old2", "/admin-api/bpm/process-instance/get-approval-detail?processVariablesStr=1"),
        FlowStep(step_id="submit", name="submit", method="POST", url="/admin-api/oa/duty-leave/submit-process",
                 path="/admin-api/oa/duty-leave/submit-process"),
    ])
    spec.links = [
        FlowLink(link_id="bad", source_step_id="old1", source_path="data.id", target_step_id="submit", target_path="x"),
        FlowLink(link_id="ok", source_step_id="old2", source_path="data.id", target_step_id="submit", target_path="y"),
    ]

    new = apply_flow_edits(spec, [{"op": "dedupe_steps"}])

    assert [s.step_id for s in new.steps] == ["old2", "submit"]
    assert [l.link_id for l in new.links] == ["ok"]
    assert new.meta["deduped_step_count"] == 1


# ── Link 编辑 ──
def _two_step_spec_with_link():
    s1 = FlowStep(step_id="A", name="A", method="POST", url="/a", path="/a",
                  params=[ParamField(path="x", key="x", value="1", type="string", required=True)])
    s2 = FlowStep(step_id="B", name="B", method="POST", url="/b", path="/b",
                  params=[ParamField(path="y", key="y", value="2", type="string", required=True)])
    lk = FlowLink(link_id="l1", source_step_id="A", source_path="data.x",
                  target_step_id="B", target_path="y", confirmed=False, confidence=0.85)
    return FlowSpec(flow_id="f", steps=[s1, s2], links=[lk])


def test_add_link():
    spec = _two_step_spec_with_link()
    new = apply_flow_edits(spec, [{"op": "add", "step_id": "A", "link": {
        "source_step_id": "B", "source_path": "data.z",
        "target_step_id": "A", "target_path": "x",
    }}])
    assert len(new.links) == 2


def test_add_link_bad_source_raises():
    spec = _two_step_spec_with_link()
    with pytest.raises(ValueError, match="source step not found"):
        apply_flow_edits(spec, [{"op": "add", "step_id": "A", "link": {
            "source_step_id": "NOPE", "source_path": "x",
            "target_step_id": "A", "target_path": "x",
        }}])


def test_update_link_confirmed():
    spec = _two_step_spec_with_link()
    new = apply_flow_edits(spec, [{"op": "update", "link_id": "l1",
                                   "field": "confirmed", "value": True}])
    assert new.links[0].confirmed is True


def test_remove_link():
    spec = _two_step_spec_with_link()
    new = apply_flow_edits(spec, [{"op": "remove", "link_id": "l1"}])
    assert len(new.links) == 0


def test_nonexistent_link_lists_available():
    spec = _two_step_spec_with_link()
    with pytest.raises(ValueError) as exc:
        apply_flow_edits(spec, [{"op": "remove", "link_id": "nope"}])
    msg = str(exc.value)
    assert "available:" in msg
    assert "l1" in msg


def test_resolve_review_item_is_preserved_in_validation():
    spec = _two_step_spec_with_link()
    spec = apply_flow_edits(spec, [])
    item = next(i for i in spec.review_items if i.type == "link_confirmation")

    new = apply_flow_edits(spec, [{"op": "resolve_review", "review_id": item.id, "resolved": True}])

    assert next(i for i in new.review_items if i.id == item.id).resolved is True
    report = validate_flow_spec(new)
    assert next(i for i in report["review_items"] if i["id"] == item.id)["resolved"] is True


def test_resolve_reviews_excluding_high():
    spec = _two_step_spec_with_link()
    spec.steps[0].risk_level = "L4"
    spec = apply_flow_edits(spec, [])

    new = apply_flow_edits(spec, [{
        "op": "resolve_reviews",
        "exclude_severities": ["high"],
        "resolved": True,
    }])

    for item in new.review_items:
        if item.severity == "high":
            assert item.resolved is False
        else:
            assert item.resolved is True


def test_runtime_unknown_review_is_not_duplicated_as_field_category():
    spec = _make_spec()
    param = spec.steps[0].params[0]
    param.category = "runtime_var"
    param.source_kind = "unknown"
    param.need_human_confirm = True

    new = apply_flow_edits(spec, [])
    target_items = [i for i in new.review_items if i.target.get("path") == param.path]

    assert [i.type for i in target_items] == ["runtime_var_source"]
    assert target_items[0].severity == "high"


class _FakeLlmClient:
    def __init__(self, payload):
        self.payload = payload
        self.last_user = ""

    async def complete_json(self, *, model: str, system: str, user: str, timeout_s: float):
        self.last_user = user
        return self.payload


@pytest.mark.asyncio
async def test_llm_recommendations_attach_to_review_without_mutating_flow():
    source = FlowStep(
        step_id="s1", method="GET", url="/api/detail", path="/api/detail",
        response_json={"data": {"taskId": "T-100"}},
    )
    target_param = ParamField(
        path="taskId", key="taskId", value="T-100", type="string",
        category="runtime_var", source_kind="unknown",
    )
    target = FlowStep(
        step_id="s2", method="POST", url="/api/submit", path="/api/submit",
        params=[target_param], body_source='{"taskId":"T-100"}',
    )
    spec = refresh_review_items(FlowSpec(flow_id="llm", steps=[source, target]))
    client = _FakeLlmClient({"suggestions": [{
        "review_id": spec.review_items[0].id,
        "action": "bind_previous_response",
        "source_step_id": "s1",
        "source_path": "data.taskId",
        "confidence": 0.86,
        "reason": "字段名与上游 taskId 一致",
    }]})

    out = await add_llm_review_recommendations(spec, llm_client=client, model="fake")

    assert out.steps[1].params[0].source_kind == "unknown"
    assert out.links == []
    assert out.review_items[0].llm_suggestions[0]["action"] == "bind_previous_response"
    assert out.review_items[0].llm_suggestions[0]["source_path"] == "data.taskId"
    assert "T-100" not in client.last_user


@pytest.mark.asyncio
async def test_llm_recommendations_reject_ungrounded_source_path():
    source = FlowStep(
        step_id="s1", method="GET", url="/api/detail", path="/api/detail",
        response_json={"data": {"taskId": "T-100"}},
    )
    target_param = ParamField(
        path="taskId", key="taskId", value="T-100", type="string",
        category="runtime_var", source_kind="unknown",
    )
    target = FlowStep(step_id="s2", method="POST", url="/api/submit", path="/api/submit", params=[target_param])
    spec = refresh_review_items(FlowSpec(flow_id="llm", steps=[source, target]))
    client = _FakeLlmClient({"suggestions": [{
        "review_id": spec.review_items[0].id,
        "action": "bind_previous_response",
        "source_step_id": "s1",
        "source_path": "data.notExist",
        "confidence": 0.9,
        "reason": "bad",
    }]})

    out = await add_llm_review_recommendations(spec, llm_client=client, model="fake")

    assert out.review_items[0].llm_suggestions == []


# ── Type inference ──
def test_infer_type_number():
    assert _infer_type_from_value("123") == "number"


def test_infer_type_boolean():
    assert _infer_type_from_value("true") == "boolean"


def test_infer_type_date():
    assert _infer_type_from_value("2024-01-01") == "date"


def test_infer_type_datetime():
    assert _infer_type_from_value("2024-01-01T12:00:00") == "datetime"


def test_infer_type_string():
    assert _infer_type_from_value("hello") == "string"
    assert _infer_type_from_value(None) == "string"
