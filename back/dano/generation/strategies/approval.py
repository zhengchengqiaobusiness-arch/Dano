"""审批办理策略:对已发起的流程做 同意/驳回/退回(operateType 200/201/202)。

与 workflow_bpmn 区别:那是"发起"侧(创建流程),这是"办理"侧(推进他人发起的任务)。
事实核查:办理后该任务应离开我的待办(回查待办不再含该 taskId)。
"""

from __future__ import annotations

from dano.generation.artifacts import GoalBrief
from dano.shared.asset_bodies import FactCheckSpec, PlanBody


class ApprovalStrategy:
    name = "approval"

    def matches(self, actions: list[dict]) -> bool:
        blob = " ".join((a.get("name", "") + " " + a.get("summary", "")) for a in actions).lower()
        return any(k in blob for k in ("approve", "agree", "reject", "审批", "驳回", "同意", "办理"))

    def decompose(self, goal: GoalBrief) -> PlanBody:
        return PlanBody(
            flow=goal.flow, strategy=self.name,
            steps=["取待办 taskId", "biz/flow/submit(operateType: 200同意/201驳回/202退回, flowTask{taskId,...})"],
            contract={"submit_endpoint": "/biz/flow/submit",
                      "operate_type": {"agree": "200", "reject": "201", "return": "202"}},
            user_fields=list(goal.test_input.keys()),
            required_fields=list(goal.test_input.keys()),
            success_rule="response.code == null or response.code == 200",
            fact_check=FactCheckSpec(
                endpoint="/workflow/todo/list/table?taskId={taskId}&pageNum=1&pageSize=5",
                method="GET", assert_expr="response.total == 0", retries=5, backoff_s=0.8),
        )

    def code_skeleton(self, plan: PlanBody) -> str:
        return (
            "import httpx\n"
            "def run(inputs, creds):\n"
            "    base = inputs['__base_url__'].rstrip('/')\n"
            "    h = {'Authorization': 'Bearer ' + creds['token']}\n"
            "    op = inputs.get('operateType', '200')   # 200同意/201驳回/202退回\n"
            "    flow_task = inputs['flowTask']           # 至少含 taskId/procInsId/defId\n"
            "    with httpx.Client(timeout=30, verify=False) as c:\n"
            "        ack = c.post(base + '/biz/flow/submit',\n"
            "                     json={'operateType': op, 'flowTask': flow_task}, headers=h).json()\n"
            "    return {'code': ack.get('code')}\n"
        )
