"""Step B · FlowSpec 字段/link/step 编辑测试。"""

import pytest

from dano.execution.page.flow_spec import (
    FlowSpec, FlowStep, FlowLink, ParamField,
    apply_flow_edits, validate_flow_spec, _infer_type_from_value,
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
