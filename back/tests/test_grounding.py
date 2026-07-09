"""Phase 3 · DSL v2 grounding 校验(纯离线):臆造的动作/字段/变量/函数必须被挡下。"""
from __future__ import annotations

from dano.onboarding.dsl_grounding import (
    branch_ids,
    check_grounding,
    collect_field_refs,
    coverage_gaps,
)
from dano.shared.asset_bodies import Invariant, WorkflowSkillBody, WorkflowStep

_PUB = {"query_balance", "start_leave_flow", "submit_flow_task"}


def _grounded_body() -> WorkflowSkillBody:
    return WorkflowSkillBody(
        action="submit_leave",
        user_fields=["startDate", "endDate", "leaveType"],
        required_fields=["startDate", "endDate"],
        preconditions=[Invariant(check="response.balance >= business_days(startDate, endDate)",
                                 evidence={"query_action": "query_balance", "params": {}})],
        steps=[
            WorkflowStep(kind="compute", outputs={"leave_days": "business_days(startDate, endDate)"}),
            WorkflowStep(kind="branch", condition="leave_days > 3",
                         then=[WorkflowStep(action="start_leave_flow", inputs={"templateId": "const:d"})],
                         otherwise=[WorkflowStep(action="start_leave_flow", inputs={"templateId": "const:s"})]),
            WorkflowStep(action="submit_flow_task",
                         inputs={"taskId": "step:start_leave_flow.data.taskId", "days": "var:leave_days"}),
        ],
        invariants=[Invariant(check="response.code == 200")],
    )


def test_fully_grounded_passes():
    assert check_grounding(_grounded_body(), published_actions=_PUB) == []


def test_unpublished_action_flagged():
    issues = check_grounding(_grounded_body(), published_actions={"query_balance", "start_leave_flow"})
    assert any("调用动作未发布: submit_flow_task" in i for i in issues)


def test_unauthorized_function_flagged():
    body = WorkflowSkillBody(action="x", user_fields=["d"], steps=[
        WorkflowStep(kind="compute", outputs={"y": "evil_func(d)"})])
    issues = check_grounding(body, published_actions=set())
    assert any("未授权函数 'evil_func'" in i for i in issues)


def test_invented_identifier_flagged():
    body = WorkflowSkillBody(action="x", steps=[
        WorkflowStep(kind="branch", condition="foo > 3",
                     then=[WorkflowStep(action="a", inputs={})])])
    issues = check_grounding(body, published_actions={"a"})
    assert any("未知标识 'foo'" in i for i in issues)


def test_field_not_declared_flagged():
    body = WorkflowSkillBody(action="x", user_fields=[], steps=[
        WorkflowStep(action="a", inputs={"p": "field:notdeclared"})])
    issues = check_grounding(body, published_actions={"a"})
    assert any("field 引用未声明字段 'notdeclared'" in i for i in issues)


def test_undefined_var_and_bad_step_flagged():
    body = WorkflowSkillBody(action="x", steps=[
        WorkflowStep(action="a", inputs={"p": "var:undef", "q": "step:nonstep.x"})])
    issues = check_grounding(body, published_actions={"a"})
    assert any("引用未定义变量 'undef'" in i for i in issues)
    assert any("step 引用非本流程步骤 'nonstep'" in i for i in issues)


def test_select_and_evidence_action_grounding():
    body = WorkflowSkillBody(action="x", steps=[
        WorkflowStep(kind="select", from_action="list_approvers", bind="ap"),
        WorkflowStep(action="assign", inputs={"to": "select:ap"})],
        invariants=[Invariant(check="response.code == 200",
                              evidence={"query_action": "ghost_query"})])
    issues = check_grounding(body, published_actions={"assign"})
    assert any("select 候选来源未发布: list_approvers" in i for i in issues)
    assert any("回查动作未发布: ghost_query" in i for i in issues)


def test_holidays_is_ambient_allowed():
    # holidays 是运行期注入的环境变量,表达式可直接引用(不算臆造)
    body = WorkflowSkillBody(action="x", user_fields=["startDate", "endDate"], steps=[
        WorkflowStep(kind="compute", outputs={"d": "business_days(startDate, endDate, holidays)"}),
        WorkflowStep(action="a", inputs={"days": "var:d"})])
    assert check_grounding(body, published_actions={"a"}) == []


def test_branch_ids_nested():
    steps = [
        {"kind": "compute", "outputs": {"x": "1"}},
        {"kind": "branch", "condition": "a > 1",
         "then": [{"kind": "branch", "condition": "b > 1",
                   "then": [{"action": "x"}], "otherwise": []}],
         "otherwise": [{"action": "y"}]},
    ]
    assert branch_ids(steps) == ["1", "1.t.0"]


def test_coverage_gaps_partial():
    static = ["1", "1.t.0"]
    observed = [[["1", True], ["1.t.0", True]], [["1", False]]]   # 嵌套分支只走过 then
    assert coverage_gaps(static, observed) == [{"branch": "1.t.0", "missing": ["otherwise"]}]


def test_coverage_gaps_full_and_unreached():
    assert coverage_gaps(["1"], [[["1", True]], [["1", False]]]) == []     # 两臂全覆盖
    assert coverage_gaps(["9"], [[]]) == [{"branch": "9", "missing": ["then", "otherwise"]}]  # 从未到达


def test_collect_field_refs_recurses():
    body = _grounded_body()
    # 在分支臂加一个 field 引用,确认递归收集
    body.steps[1].then[0].inputs["title"] = "field:reason"
    refs = collect_field_refs(body.steps)
    assert "reason" in refs
