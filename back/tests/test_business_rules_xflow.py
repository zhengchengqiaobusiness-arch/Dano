"""get_business_rules 兜底:enriched swagger 的 x-flow → 可 grounding 的业务规则;生鲜 CRUD → 空。"""
from __future__ import annotations

from dano.agent_tools.tools import _rules_from_spec_xflow

_PURCHASE_SPEC = {
    "openapi": "3.0.3",
    "paths": {
        "/biz/flow/submit": {
            "post": {
                "x-flow": {
                    "name": "采购申请",
                    "approvalChain": [
                        {"step": "发起", "by": "申请人"},
                        {"step": "直属主管审批", "assignee": "leader"},
                        {"step": "总经理审批", "conditional": "amount > 10000"},
                    ],
                    "escalation": {"when": "amount > 10000", "addApprover": "总经理审批"},
                    "businessValidations": [
                        {"rule": "positive", "params": ["amount", "采购金额"], "desc": "数值必须>0"},
                        {"rule": "budget", "params": ["amount"], "desc": "金额≤本部门预算余额"},
                    ],
                    "rejectBehavior": "校验任一不过 → 系统自动驳回",
                }
            }
        }
    },
}


def test_xflow_yields_groundable_precondition_and_server_side_notes():
    rules = _rules_from_spec_xflow(_PURCHASE_SPEC)
    pre = [r for r in rules if r["kind"] == "precondition"]
    assert pre and pre[0]["check"] == "amount > 0" and pre[0]["fields"] == ["amount"]
    # budget(无查询动作)、升级、驳回 → 服务端说明,不做客户端分支
    assert any(r["kind"] == "server_side" and "预算" in r.get("desc", "") for r in rules)
    assert any(r["kind"] == "server_side" and r.get("condition") == "amount > 10000" for r in rules)
    # 审批链整段留作 preview 文案
    chain = next((r for r in rules if r["kind"] == "approval_chain"), None)
    assert chain and "总经理审批" in chain["chain"]
    # 业务动作名取自 parse_openapi(无 operationId → method_path 切片)
    assert all(r["action"] == "post_biz_flow_submit" for r in rules)


def test_plain_crud_swagger_has_no_rules():
    spec = {"openapi": "3.0.3", "paths": {"/sys/user/list": {"get": {"summary": "用户列表"}}}}
    assert _rules_from_spec_xflow(spec) == []


def test_non_dict_spec_is_safe():
    assert _rules_from_spec_xflow(None) == []  # type: ignore[arg-type]
    assert _rules_from_spec_xflow({}) == []
