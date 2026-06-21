"""Phase 2 · 切片2.2:DSL v2 资产模型(向后兼容 + 节点校验,纯离线)。"""
from __future__ import annotations

import pytest

from dano.schemas.validate import SchemaError, validate_asset_body
from dano.shared.asset_bodies import WorkflowSkillBody, WorkflowStep
from dano.shared.enums import AssetType


def test_legacy_workflow_still_valid():
    # 旧形态:步骤只有 action+inputs、无 kind → 默认 call,完全兼容
    body = {
        "action": "submit_leave", "title": "提交请假",
        "steps": [
            {"action": "start_leave_flow", "inputs": {"templateId": "const:t1"}},
            {"action": "submit_flow_task", "inputs": {"taskId": "step:start_leave_flow.data.taskId"}},
        ],
    }
    m = validate_asset_body(AssetType.WORKFLOW, body)
    assert isinstance(m, WorkflowSkillBody)
    assert all(s.kind == "call" for s in m.steps)
    assert m.preconditions == [] and m.invariants == [] and m.preview is False


def test_dsl_v2_full_shape_valid():
    body = {
        "action": "submit_leave", "title": "提交请假",
        "user_fields": ["startDate", "endDate", "leaveType", "title"],
        "required_fields": ["startDate", "endDate", "leaveType", "title"],
        "preconditions": [
            {"check": "balance >= leave_days", "message": "余额不足",
             "evidence": {"query_action": "query_balance", "params": {"type": "field:leaveType"}}},
        ],
        "steps": [
            {"kind": "compute", "outputs": {"leave_days": "business_days(startDate, endDate)"}},
            {"kind": "branch", "condition": "leave_days > 3",
             "then": [{"action": "start_leave_flow", "inputs": {"templateId": "const:director"}}],
             "otherwise": [{"action": "start_leave_flow", "inputs": {"templateId": "const:supervisor"}}]},
            {"action": "submit_flow_task",
             "inputs": {"taskId": "step:start_leave_flow.data.taskId", "days": "var:leave_days"}},
        ],
        "invariants": [
            {"check": "response.code == 200", "message": "未生成实例"},
        ],
        "preview": True,
    }
    m = validate_asset_body(AssetType.WORKFLOW, body)
    kinds = [s.kind for s in m.steps]
    assert kinds == ["compute", "branch", "call"]
    assert m.steps[1].then[0].action == "start_leave_flow"   # 嵌套子步也被校验为 call
    assert m.preview is True and len(m.preconditions) == 1 and len(m.invariants) == 1


def test_foreach_and_select_valid():
    body = {
        "action": "batch_entry", "steps": [
            {"kind": "foreach", "over": "field:items", "as_var": "row",
             "steps": [{"action": "add_item", "inputs": {"name": "item:name"}}]},
            {"kind": "select", "from_action": "list_approvers", "list_path": "data.rows",
             "label_template": "{name}-{dept}", "bind": "approver_id"},
        ],
    }
    m = validate_asset_body(AssetType.WORKFLOW, body)
    assert m.steps[0].kind == "foreach" and m.steps[0].as_var == "row"
    assert m.steps[1].kind == "select" and m.steps[1].bind == "approver_id"


@pytest.mark.parametrize("bad_step", [
    {"kind": "compute"},                                  # 缺 outputs
    {"kind": "branch"},                                   # 缺 condition
    {"kind": "foreach", "steps": []},                     # 缺 over
    {"kind": "select", "from_action": "q"},               # 缺 bind
    {"kind": "call"},                                     # 缺 action
])
def test_invalid_nodes_rejected(bad_step):
    with pytest.raises(SchemaError):
        validate_asset_body(AssetType.WORKFLOW, {"action": "x", "steps": [bad_step]})


def test_nested_invalid_node_rejected():
    # 分支子步缺 action 也应被拒(嵌套递归校验)
    body = {"action": "x", "steps": [
        {"kind": "branch", "condition": "true", "then": [{"kind": "call"}]},
    ]}
    with pytest.raises(SchemaError):
        validate_asset_body(AssetType.WORKFLOW, body)
