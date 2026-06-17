"""M4 验收:可插拔业务策略(按业务区分生成)。

- 路由:不同动作清单选到不同策略(工作流/查询/审批/兜底);
- workflow_bpmn 把若依请假契约沉淀为方案:3 步、operateType=200、双层表单提示、且带事实核查;
- crud_query 只读无事实核查;骨架可正常生成(不抛错)。
纯单测,无 PG。
"""

from __future__ import annotations

from dano.generation.artifacts import GoalBrief
from dano.generation.strategies import select_strategy


def _goal(flow, actions, test_input=None):
    return GoalBrief(run_id="t", system_instance_id="A-OA", flow=flow,
                     actions=actions, test_input=test_input or {})


def test_routing_picks_workflow_for_flow_submit():
    actions = [
        {"name": "start_leave_flow", "method": "POST", "endpoint": "/workflow/handle/startFlow"},
        {"name": "submit_flow_task", "method": "POST", "endpoint": "/biz/flow/submit"},
    ]
    assert select_strategy(actions).name == "workflow_bpmn"


def test_routing_picks_crud_for_all_get():
    actions = [{"name": "list_done", "method": "GET", "endpoint": "/workflow/done/list"}]
    assert select_strategy(actions).name == "crud_query"


def test_routing_picks_approval_for_approve_action():
    actions = [{"name": "approve_task", "method": "POST", "endpoint": "/x",
                "summary": "审批通过"}]
    assert select_strategy(actions).name == "approval"


def test_routing_falls_back_to_simple_http():
    actions = [{"name": "do_thing", "method": "POST", "endpoint": "/misc/do"}]
    assert select_strategy(actions).name == "simple_http"


def test_workflow_bpmn_plan_carries_leave_contract():
    s = select_strategy([{"name": "submit_flow_task", "endpoint": "/biz/flow/submit"}])
    plan = s.decompose(_goal("submit_leave",
                             [{"name": "submit_flow_task", "endpoint": "/biz/flow/submit"}],
                             test_input={"title": "x", "leaveType": "annual"}))
    assert plan.strategy == "workflow_bpmn"
    assert len(plan.steps) == 3
    assert plan.contract["operate_type_submit"] == "200"
    assert "valData" in plan.contract["form_save_note"]
    # 写流程必须带事实核查
    assert plan.fact_check is not None
    assert "total" in plan.fact_check.assert_expr
    # 骨架含已验证的三步端点
    sk = s.code_skeleton(plan)
    for ep in ("/workflow/handle/startFlow", "/biz/form/save", "/biz/flow/submit"):
        assert ep in sk
    assert "creds['token']" in sk and "valData" in sk


def test_crud_query_has_no_factcheck_and_skeleton_ok():
    s = select_strategy([{"name": "q", "method": "GET", "endpoint": "/q/list"}])
    plan = s.decompose(_goal("q", [{"name": "q", "method": "GET", "endpoint": "/q/list"}]))
    assert plan.strategy == "crud_query"
    assert plan.fact_check is None                 # 只读无副作用
    assert "/q/list" in s.code_skeleton(plan)
