"""Phase A3:上架目录只露业务 skill,复合流程的步骤接口隐藏(纯离线,fake store)。"""
from __future__ import annotations

from uuid import uuid4

from dano.orchestrator.skills import SkillRegistry
from dano.shared.asset_bodies import WorkflowSkillBody, WorkflowStep
from dano.shared.enums import AssetType, Subsystem


class _Env:
    def __init__(self, body: dict, asset_key: str) -> None:
        self.body, self.asset_key, self.asset_id, self.version = body, asset_key, uuid4(), 1


def _conn_env(action: str) -> _Env:
    return _Env({"action": action, "field_bindings": [], "risk_level": "L1"}, action)


class _Store:
    def __init__(self, by_type: dict) -> None:
        self.by_type = by_type

    async def list_published(self, asset_type, scope):  # noqa: ANN001
        return self.by_type.get(asset_type, [])


async def test_workflow_steps_hidden_only_business_skill_shown():
    wf = WorkflowSkillBody(
        action="submit_leave", title="提交请假",
        steps=[WorkflowStep(action="start_leave_flow", inputs={"templateId": "const:t"}),
               WorkflowStep(action="submit_flow_task", inputs={"taskId": "step:start_leave_flow.data.taskId"})],
        user_fields=["leaveDays"], required_fields=["leaveDays"])
    store = _Store({
        AssetType.WORKFLOW: [_Env(wf.model_dump(), "submit_leave")],
        # 两个步骤连接器(应隐藏)+ 一个独立查询连接器(应可见)
        AssetType.CONNECTOR: [_conn_env("start_leave_flow"), _conn_env("submit_flow_task"),
                              _conn_env("query_balance")],
    })
    reg = await SkillRegistry.from_store(store, tenant="t", subsystems=[Subsystem.OA])
    actions = {s.action for s in reg.skills}

    assert "submit_leave" in actions                      # 业务 skill 露出
    assert "query_balance" in actions                     # 独立查询露出
    assert "start_leave_flow" not in actions              # 步骤接口隐藏
    assert "submit_flow_task" not in actions              # 步骤接口隐藏

    sl = next(s for s in reg.skills if s.action == "submit_leave")
    assert sl.is_workflow is True
