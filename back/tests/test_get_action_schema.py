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
    assert leaves["amount"] == {"type": "number", "description": "采购金额(元)"}
    assert leaves["quantity"]["type"] == "number"
    assert "payee" not in leaves                     # 只取 Submit_purchase_template 这一支,不串味
