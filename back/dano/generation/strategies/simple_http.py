"""最简策略:单接口业务(多为只读查询)→ 一个薄适配器。

兜底策略(matches 恒真,注册时置于末位);复杂业务由更具体的策略(workflow_bpmn 等)接管。
"""

from __future__ import annotations

from dano.generation.artifacts import GoalBrief
from dano.shared.asset_bodies import PlanBody


class SimpleHttpStrategy:
    name = "simple_http"

    def matches(self, actions: list[dict]) -> bool:
        return True                                   # 兜底:总能用最简策略

    def decompose(self, goal: GoalBrief) -> PlanBody:
        a = goal.actions[0] if goal.actions else {}
        step = f"{a.get('method', 'GET')} {a.get('endpoint', '')}".strip()
        req = list(a.get("required_in", []))
        return PlanBody(
            flow=goal.flow, strategy=self.name,
            steps=[step] if step else [],
            contract={"action": a},
            user_fields=req, required_fields=req,
            success_rule="response.code == null or response.code == 200",
        )

    def code_skeleton(self, plan: PlanBody) -> str:
        action = plan.contract.get("action", {})
        return (
            "import httpx\n"
            "def run(inputs, creds):\n"
            f"    # {action.get('method','GET')} {action.get('endpoint','')}\n"
            "    headers = {'Authorization': 'Bearer ' + creds.get('token','')}\n"
            "    # 用 inputs 作查询参数/请求体,返回解析后的响应体(dict)\n"
            "    ...\n"
            "    return {}\n"
        )
