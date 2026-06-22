"""真机暴露的 bug 回归:get_action_schema 必须按 parse_spec 的命名(method_path)定位,
而非只认 operationId(你的 swagger 没有 operationId,旧实现一律找不到 → pi 反复猜名直到超时)。"""
from __future__ import annotations

import pytest

from dano.agent_tools import materials, tools

_SPEC = {
    "openapi": "3.0.3",
    "paths": {
        "/workflow/handle/startFlow": {"post": {
            "summary": "发起",
            "requestBody": {"content": {"application/json": {"schema": {
                "type": "object", "required": ["templateId"],
                "properties": {"templateId": {"type": "string"}}}}}},
            "responses": {"200": {"content": {"application/json": {"schema": {
                "type": "object", "properties": {"data": {"type": "object",
                    "properties": {"taskId": {"type": "string"}}}}}}}}},
        }},
        "/biz/flow/submit": {"post": {"summary": "提交"}},
    },
}


async def test_resolves_by_derived_name():
    materials.register(materials.MaterialContext(
        run_id="rGAS", tenant="t", system_instance_id="A-OA", subsystem="A-OA", openapi=_SPEC))
    try:
        out = await tools.get_action_schema("rGAS", {"system_instance_id": "A-OA",
                                                     "action": "post_workflow_handle_startFlow"})
        assert out["endpoint"] == "/workflow/handle/startFlow"
        assert out["method"] == "POST"
        assert out["request_schema"]["properties"]["templateId"]["type"] == "string"
    finally:
        materials.clear_run("rGAS")


async def test_unknown_action_lists_available():
    materials.register(materials.MaterialContext(
        run_id="rGAS2", tenant="t", system_instance_id="A-OA", subsystem="A-OA", openapi=_SPEC))
    try:
        with pytest.raises(tools.ToolError) as ei:
            await tools.get_action_schema("rGAS2", {"system_instance_id": "A-OA", "action": "startFlow"})
        # 错误信息列出真实可用动作名,pi 能据此自我纠正(不再反复瞎猜)
        assert "post_workflow_handle_startFlow" in str(ei.value)
    finally:
        materials.clear_run("rGAS2")


# ── 信源直通:从提交端点 schema(oneOf 多模板 + 嵌套 flowTask.variables)抽字段类型/描述 ──
_SUBMIT_SPEC = {
    "paths": {
        "/workflow/handle/startFlow": {"post": {"description": "x"}},
        "/biz/flow/submit": {"post": {"requestBody": {"content": {"application/json": {"schema":
            {"oneOf": [{"$ref": "#/components/schemas/Submit_purchase_template"},
                       {"$ref": "#/components/schemas/Submit_payment_template"}]}}}}}},
    },
    "components": {"schemas": {
        "AjaxResult": {},
        "Submit_purchase_template": {"type": "object", "properties": {
            "flowTask": {"type": "object", "properties": {"variables": {"type": "object", "properties": {
                "quantity": {"type": "number", "description": "采购数量"},
                "amount": {"type": "number", "description": "采购金额(元)"},
                "reason": {"type": "string", "description": "采购事由"}}}}}}},
        "Submit_payment_template": {"type": "object", "properties": {
            "flowTask": {"type": "object", "properties": {"variables": {"type": "object", "properties": {
                "payee": {"type": "string", "description": "收款方"}}}}}}},
    }},
}


def test_submit_leaf_fields_picks_variant_and_keeps_types():
    from dano.capabilities.oa_templates import match_template
    t = match_template(_SUBMIT_SPEC)
    leaves = tools._submit_leaf_fields(_SUBMIT_SPEC, t, "purchase_template")
    assert leaves["amount"]["type"] == "number" and leaves["amount"]["description"] == "采购金额(元)"
    assert leaves["quantity"]["type"] == "number"
    assert "payee" not in leaves                     # 只取 Submit_purchase_template 这一支,不串味
    assert leaves["amount"]["path"] == "flowTask.variables.amount"   # 记录嵌套点路径(供可追溯映射)


def test_field_mappings_are_traceable():
    from dano.capabilities.oa_templates import match_template
    t = match_template(_SUBMIT_SPEC)
    leaves = tools._submit_leaf_fields(_SUBMIT_SPEC, t, "purchase_template")
    maps = tools._field_mappings(leaves, ["amount", "quantity", "unknown_field"],
                                 "/biz/flow/submit", "purchase_template")
    by = {m["standard_field"]: m for m in maps}
    assert "unknown_field" not in by                  # schema 里没有来源 → 不臆造映射
    assert by["amount"]["target_location"] == "flowTask.variables.amount"
    assert by["amount"]["target_type"] == "number"
    assert by["amount"]["source"] == {"type": "openapi", "path": "/biz/flow/submit",
                                      "schema_ref": "Submit_purchase_template.flowTask.variables.amount"}


def test_merge_field_types_priority():
    """WS6 类型合并优先级:真实表单(权威)> submit schema > 已有(名字启发式)。"""
    user_fields = ["amount", "leaveType", "title", "extra"]
    leaves = {
        "amount": {"type": "number"},      # schema 说 number
        "leaveType": {"type": "string"},   # schema 说 string
        "title": {"type": "string"},
    }
    form_types = {
        "leaveType": "enum",               # 真实表单 el-select → enum(应压过 schema 的 string)
        "amount": "number",
    }
    existing = {"title": "string", "extra": "string"}  # 名字启发式兜底
    out = tools._merge_field_types(user_fields, leaves, form_types, existing)
    assert out["leaveType"] == "enum"      # 表单权威覆盖 schema
    assert out["amount"] == "number"       # 表单 = schema,无冲突
    assert out["title"] == "string"        # 表单无、schema 有但 existing 已有 → 保留 existing
    assert out["extra"] == "string"        # 都无来源 → 保留已有启发式
    # existing 不被原地修改
    assert existing == {"title": "string", "extra": "string"}
