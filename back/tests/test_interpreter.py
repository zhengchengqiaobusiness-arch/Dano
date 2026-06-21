"""Phase 2 · 切片2.3:DSL v2 通用解释器(call/compute/branch/foreach/select + 前置/不变量)。

纯离线:注入 FakeExecutor + FakeStore,不碰 PG/网络/LLM。直接驱动 Orchestrator._run_workflow。
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from dano.orchestrator.orchestrator import Orchestrator
from dano.orchestrator.skills import SkillRegistry
from dano.orchestrator.types import Intent, SkillSpec
from dano.shared.asset_bodies import WorkflowStep
from dano.shared.enums import AssetType, RiskLevel, Subsystem, TaskState


class _Resp:
    def __init__(self, http: int, body: dict) -> None:
        self.http, self.body = http, body


class _Executor:
    def __init__(self, handler) -> None:  # noqa: ANN001
        self.handler = handler
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, connector: dict, body: dict, creds: dict) -> _Resp:
        action = connector.get("action")
        self.calls.append((action, body))
        return self.handler(action, body)


class _Env:
    def __init__(self, body: dict) -> None:
        self.body, self.asset_id = body, None


class _Store:
    def __init__(self, connectors: dict) -> None:
        self.connectors = connectors

    async def get_published(self, asset_type, scope, *, asset_key=None):  # noqa: ANN001
        if asset_type == AssetType.CONNECTOR:
            b = self.connectors.get(asset_key)
            return _Env(b) if b else None
        return None

    async def get(self, asset_id):  # noqa: ANN001
        return None


def _conn(action: str) -> dict:
    return {"endpoint": f"/{action}", "method": "POST", "auth_kind": "token",
            "auth_ref": "vault://t/oa", "action": action}


def _orch(handler, connectors) -> tuple[Orchestrator, _Executor]:  # noqa: ANN001
    ex = _Executor(handler)
    orch = Orchestrator(registry=SkillRegistry([]), store=_Store(connectors), harness=object(),
                        action_executor=ex, resolve_credentials=lambda refs: {"primary": "tok"})
    return orch, ex


def _steps(*ws: WorkflowStep) -> list[dict]:
    return [w.model_dump() for w in ws]


def _skill(**kw) -> SkillSpec:  # noqa: ANN003
    base = dict(skill_id="A-OA.submit_leave", subsystem=Subsystem.OA, action="submit_leave",
                risk_level=RiskLevel.L3, is_workflow=True, workflow_success_rule="response.code == 200")
    base.update(kw)
    return SkillSpec(**base)


# 一条带 前置 / compute / branch / call / 不变量 的复合流程
_LEAVE_STEPS = _steps(
    WorkflowStep(kind="compute", outputs={"leave_days": "business_days(startDate, endDate)"}),
    WorkflowStep(kind="branch", condition="leave_days > 3",
                 then=[WorkflowStep(action="start_leave_flow", inputs={"templateId": "const:director"})],
                 otherwise=[WorkflowStep(action="start_leave_flow", inputs={"templateId": "const:supervisor"})]),
    WorkflowStep(action="submit_flow_task",
                 inputs={"taskId": "step:start_leave_flow.data.taskId", "days": "var:leave_days"}),
)
_PRECOND = [{"check": "response.balance >= business_days(startDate, endDate)",
            "message": "余额不足", "evidence": {"query_action": "query_balance", "params": {}}}]
_INVAR = [{"check": "response.code == 200", "message": "未生成实例", "evidence": None}]
_CONNECTORS = {a: _conn(a) for a in ("query_balance", "start_leave_flow", "submit_flow_task")}


def _leave_handler(balance: int = 5, submit_code: int = 200):  # noqa: ANN001
    def h(action: str, body: dict) -> _Resp:
        if action == "query_balance":
            return _Resp(200, {"code": 200, "balance": balance})
        if action == "start_leave_flow":
            return _Resp(200, {"code": 200, "data": {"taskId": "T1", "procInsId": "P1"}})
        if action == "submit_flow_task":
            return _Resp(200, {"code": submit_code, "procInsId": "P1"})
        return _Resp(404, {})
    return h


async def test_happy_path_compute_branch_invariant():
    orch, ex = _orch(_leave_handler(balance=5), _CONNECTORS)
    skill = _skill(workflow_steps=_LEAVE_STEPS, workflow_preconditions=_PRECOND, workflow_invariants=_INVAR)
    # 2026-06-01(周一)~06-05(周五) → 5 个工作日,balance 5 够,5>3 走 director
    intent = Intent(action_hint="请假", fields={"startDate": "2026-06-01", "endDate": "2026-06-05",
                                               "leaveType": "annual"})
    out = await orch._run_workflow(uuid4(), "t", skill, intent)
    assert out.state == TaskState.COMPLETED
    called = {a: b for a, b in ex.calls}
    assert called["start_leave_flow"]["templateId"] == "director"        # 分支选对
    assert called["submit_flow_task"]["days"] == 5                       # 派生天数串入
    assert called["submit_flow_task"]["taskId"] == "T1"                  # 上一步出参串入


async def test_branch_else_short_leave():
    orch, ex = _orch(_leave_handler(balance=5), _CONNECTORS)
    skill = _skill(workflow_steps=_LEAVE_STEPS, workflow_preconditions=_PRECOND, workflow_invariants=_INVAR)
    intent = Intent(action_hint="请假", fields={"startDate": "2026-06-01", "endDate": "2026-06-01"})  # 1 天
    out = await orch._run_workflow(uuid4(), "t", skill, intent)
    assert out.state == TaskState.COMPLETED
    assert dict(ex.calls)["start_leave_flow"]["templateId"] == "supervisor"


async def test_precondition_blocks_and_does_not_write():
    orch, ex = _orch(_leave_handler(balance=2), _CONNECTORS)        # 余额 2 < 5 天
    skill = _skill(workflow_steps=_LEAVE_STEPS, workflow_preconditions=_PRECOND, workflow_invariants=_INVAR)
    intent = Intent(action_hint="请假", fields={"startDate": "2026-06-01", "endDate": "2026-06-05"})
    out = await orch._run_workflow(uuid4(), "t", skill, intent)
    assert out.state == TaskState.REJECTED
    assert [a for a, _ in ex.calls] == ["query_balance"]            # 只查了余额,绝不写


async def test_invariant_fail_marks_failed():
    orch, _ = _orch(_leave_handler(balance=5, submit_code=500), _CONNECTORS)  # 提交业务码 500
    skill = _skill(workflow_steps=_LEAVE_STEPS, workflow_preconditions=_PRECOND, workflow_invariants=_INVAR)
    intent = Intent(action_hint="请假", fields={"startDate": "2026-06-01", "endDate": "2026-06-05"})
    out = await orch._run_workflow(uuid4(), "t", skill, intent)
    # submit code=500 → success_rule(step) 先判失败 → FAILED
    assert out.state == TaskState.FAILED


async def test_foreach_iterates():
    seen: list = []

    def h(action: str, body: dict) -> _Resp:
        seen.append(body.get("name"))
        return _Resp(200, {"code": 200})

    orch, _ = _orch(h, {"add_item": _conn("add_item")})
    steps = _steps(WorkflowStep(kind="foreach", over="field:items", as_var="row",
                                steps=[WorkflowStep(action="add_item", inputs={"name": "item:name"})]))
    skill = _skill(workflow_steps=steps)
    intent = Intent(action_hint="批量", fields={"items": [{"name": "a"}, {"name": "b"}, {"name": "c"}]})
    out = await orch._run_workflow(uuid4(), "t", skill, intent)
    assert out.state == TaskState.COMPLETED
    assert seen == ["a", "b", "c"]


async def test_select_preselected_and_ambiguous():
    def h(action: str, body: dict) -> _Resp:
        if action == "list_approvers":
            return _Resp(200, {"data": {"rows": [{"id": "A1"}, {"id": "A2"}]}})
        return _Resp(200, {"code": 200})

    conns = {"list_approvers": _conn("list_approvers"), "assign": _conn("assign")}
    steps = _steps(
        WorkflowStep(kind="select", from_action="list_approvers", list_path="data.rows",
                     label_template="{id}", bind="approver_id"),
        WorkflowStep(action="assign", inputs={"to": "var:approver_id"}),
    )
    skill = _skill(workflow_steps=steps)

    # 多候选未预选 → NEEDS_SELECT,带候选
    orch, _ = _orch(h, conns)
    out = await orch._run_workflow(uuid4(), "t", skill, Intent(action_hint="指派", fields={}))
    assert out.state == TaskState.NEEDS_SELECT
    assert out.audit["select"]["bind"] == "approver_id"
    assert len(out.audit["select"]["candidates"]) == 2

    # 已预选 → 直接用,assign 收到选中值
    orch2, ex2 = _orch(h, conns)
    out2 = await orch2._run_workflow(uuid4(), "t", skill, Intent(action_hint="指派", fields={"approver_id": "A2"}))
    assert out2.state == TaskState.COMPLETED
    assert dict(ex2.calls)["assign"]["to"] == "A2"


async def test_branch_arm_recorded_in_audit():
    skill = _skill(workflow_steps=_LEAVE_STEPS, workflow_preconditions=_PRECOND, workflow_invariants=_INVAR)
    # 5 天 → 分支 "1" 走 then(True)
    orch, _ = _orch(_leave_handler(balance=5), _CONNECTORS)
    out = await orch._run_workflow(uuid4(), "t", skill,
                                   Intent(action_hint="请假", fields={"startDate": "2026-06-01", "endDate": "2026-06-05"}))
    assert out.audit["branches"] == [["1", True]]
    # 1 天 → 分支 "1" 走 otherwise(False)
    orch2, _ = _orch(_leave_handler(balance=5), _CONNECTORS)
    out2 = await orch2._run_workflow(uuid4(), "t", skill,
                                     Intent(action_hint="请假", fields={"startDate": "2026-06-01", "endDate": "2026-06-01"}))
    assert out2.audit["branches"] == [["1", False]]


async def test_holidays_injected_into_compute():
    seen: dict = {}

    def h(action: str, body: dict) -> _Resp:
        seen.update(body)
        return _Resp(200, {"code": 200})

    ex = _Executor(h)
    orch = Orchestrator(registry=SkillRegistry([]), store=_Store({"rec": _conn("rec")}), harness=object(),
                        action_executor=ex, resolve_credentials=lambda r: {"primary": "t"},
                        holidays=["2026-06-03"])     # 运行期注入的日历源
    steps = _steps(
        WorkflowStep(kind="compute", outputs={"d": "business_days(startDate, endDate, holidays)"}),
        WorkflowStep(action="rec", inputs={"days": "var:d"}))
    skill = _skill(workflow_steps=steps)
    out = await orch._run_workflow(uuid4(), "t", skill,
                                   Intent(action_hint="x", fields={"startDate": "2026-06-01", "endDate": "2026-06-05"}))
    assert out.state == TaskState.COMPLETED
    assert seen["days"] == 4        # 06-01~06-05 工作日 5,扣节假日 06-03 → 4


async def test_capability_gap_when_connector_missing():
    orch, _ = _orch(_leave_handler(), {})            # 没有任何连接器
    skill = _skill(workflow_steps=_steps(WorkflowStep(action="start_leave_flow", inputs={})))
    out = await orch._run_workflow(uuid4(), "t", skill, Intent(action_hint="x", fields={}))
    assert out.state == TaskState.CAPABILITY_GAP
