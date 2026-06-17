"""只读查询策略:全 GET 的业务(列表/详情查询)。无副作用,故无需事实核查。"""

from __future__ import annotations

from dano.generation.artifacts import GoalBrief
from dano.shared.asset_bodies import PlanBody


class CrudQueryStrategy:
    name = "crud_query"

    def matches(self, actions: list[dict]) -> bool:
        return bool(actions) and all(
            (a.get("method", "GET") or "GET").upper() == "GET" for a in actions)

    def decompose(self, goal: GoalBrief) -> PlanBody:
        a = goal.actions[0] if goal.actions else {}
        return PlanBody(
            flow=goal.flow, strategy=self.name,
            steps=[f"GET {a.get('endpoint', '')}".strip()],
            contract={"action": a, "read_only": True},
            user_fields=list(a.get("required_in", [])),
            required_fields=list(a.get("required_in", [])),
            success_rule="response.code == null or response.code == 200",
            fact_check=None,                       # 只读无副作用 → 不需要回查
        )

    def code_skeleton(self, plan: PlanBody) -> str:
        ep = plan.contract.get("action", {}).get("endpoint", "")
        return (
            "import httpx\n"
            "def run(inputs, creds):\n"
            "    base = inputs['__base_url__'].rstrip('/')\n"
            "    h = {'Authorization': 'Bearer ' + creds['token']}\n"
            "    ep = " + repr(ep) + "   # GET 端点:用 inputs['query'] 作查询参数\n"
            "    with httpx.Client(timeout=30, verify=False) as c:\n"
            "        r = c.get(base + ep, params=inputs.get('query', {}), headers=h)\n"
            "    return r.json()\n"
        )
