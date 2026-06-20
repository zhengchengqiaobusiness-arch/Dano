"""v2-M3 LLM 拆解:读证据产**结构化 Plan**(不再硬编码 decompose),交给生成闭环编码。

护栏(防臆造):Plan 引用的端点/字段**必须在证据里真实存在**,否则判废(plan() 抛 PlanError),
由控制器安全回退到确定性策略 decompose——LLM 只提方案,绝不能凭空造接口。

spawn 可注入(测试用 fake / 真路径走 OpenAI 兼容文本生成);planner 只产方案,不自证;
发布仍由沙箱真跑 + 三模型 + 事实核查闸门把关。
"""

from __future__ import annotations

import json
import re
from typing import Awaitable, Callable, Protocol

import structlog

from dano.generation.coder import openai_text_spawn
from dano.generation.artifacts import GoalBrief
from dano.shared.asset_bodies import FactCheckSpec, PlanBody

log = structlog.get_logger(__name__)


class PlanError(RuntimeError):
    """LLM 产出的方案不合规(引用了证据里不存在的端点/字段等)。"""


class Planner(Protocol):
    async def plan(self, goal: GoalBrief, strategy) -> PlanBody: ...          # noqa: ANN001
    async def replan(self, goal: GoalBrief, strategy, prev: PlanBody,        # noqa: ANN001
                     reasons: list[str]) -> PlanBody: ...


def _allowed(evidence: dict | None) -> tuple[set[str], set[str]]:
    """从证据里取允许引用的端点集合与字段集合(端点白名单、字段白名单)。

    端点白名单同时收**路径**(/biz/form/save)与**动作名**(post_biz_form_save):无 operationId 的 spec
    里两者都可能被模型引用,只认路径会把正确方案误判为"引用了不存在的端点"。
    """
    ev = evidence or {}
    actions = ev.get("actions") or []
    endpoints: set[str] = set()
    for a in actions:
        if a.get("endpoint"):
            endpoints.add(a["endpoint"])
        if a.get("name"):
            endpoints.add(a["name"])
    fields: set[str] = {f.get("key", "") for f in (ev.get("form_fields") or []) if f.get("key")}
    for a in actions:
        fields.update(a.get("params_in") or [])
    return endpoints, fields


_JS_ISMS = ("&&", "||", "===", "!==")          # null/true/false 是 safe_eval 合法字面量,不算 JS
_CALL = re.compile(r"[A-Za-z_]\w*\s*\(")       # 标识符紧跟左括号 = 函数/方法调用(safe_eval 不支持)


def _expr_problem(expr: object, label: str) -> str | None:
    """判定表达式是否能被 safe_eval 求值:基于 response · 只准属性点取/下标/比较 · 不准函数调用。

    教训:safe_eval 出于安全**不支持 Call 节点**(.get() 等)。曾让模型用 response.get('code'),
    每轮代码明明跑通,却卡在『不支持的表达式节点: Call』。故这里强制属性点取写法。
    """
    e = str(expr or "").strip()
    if not e:
        return f"{label} 为空"
    if e.lower() in ("true", "false", "1", "0"):
        return f"{label} 退化为常量({e}),必须是基于 response 的判断"
    if "response" not in e:
        return f"{label} 必须基于 response(如 response.code == 200 / response.total > 0)"
    for j in _JS_ISMS:
        if j in e:
            return f"{label} 含 JS 写法 {j!r},请改用 Python(and/or/not、!=)"
    if _CALL.search(e):
        return (f"{label} 不能用函数/方法调用(如 .get()/len());求值器只支持属性点取 response.code、"
                "下标 response['code']、比较、null/true/false 字面量")
    return None


def validate_plan(data: dict, evidence: dict | None) -> list[str]:
    """校验 LLM 方案:端点/字段必须在证据内;成败规则与事实核查必须是可用的 Python 判定表达式。"""
    endpoints, fields = _allowed(evidence)
    errs: list[str] = []
    for ep in data.get("endpoints") or []:
        if ep not in endpoints:
            errs.append(f"方案引用了证据中不存在的端点: {ep}")
    for f in [*(data.get("required_fields") or []), *(data.get("user_fields") or [])]:
        if fields and f not in fields:
            errs.append(f"方案引用了证据中不存在的字段: {f}")
    p = _expr_problem(data.get("success_rule"), "success_rule")
    if p:
        errs.append(p)
    fc = data.get("fact_check") or {}
    if not (isinstance(fc, dict) and fc.get("endpoint")):
        errs.append("缺 fact_check.endpoint(写流程必须回查真生效)")
    else:
        fc_ep = (fc.get("endpoint") or "").split("?")[0]
        if fc_ep not in endpoints:
            errs.append(f"事实核查引用了证据中不存在的端点: {fc_ep}")
        pe = _expr_problem(fc.get("assert_expr"), "fact_check.assert_expr")
        if pe:
            errs.append(pe)
    return errs


_JSON = re.compile(r"\{.*\}", re.S)


def _extract_json(text: str) -> dict:
    m = _JSON.search(text or "")
    if not m:
        raise PlanError("LLM 未产出可解析的 JSON 方案")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise PlanError(f"方案 JSON 解析失败: {e}") from e


def _trim_evidence(ev: dict | None, endpoints: list[str]) -> dict:
    """裁剪证据给编码器:相关端点给全详情(含**请求体示例**)+ 全部端点紧凑索引。

    证据已按流程收窄(service 层),故保留全部相关动作(含请求示例),不仅 plan 选中的——
    这样 coder 能看到如 /flow/xxx/start 的完整请求示例,照着填对 form/save 那类双层嵌套。
    """
    ev = ev or {}
    acts = ev.get("actions") or []
    all_eps = [f"{a.get('name')}|{(a.get('method') or 'GET')}|{a.get('endpoint')}" for a in acts]
    return {"actions": acts[:40], "all_endpoints": all_eps[:400],
            "form_fields": ev.get("form_fields") or [], "sample_reads": ev.get("sample_reads") or []}


def _compact_evidence(ev: dict | None) -> str:
    """给拆解器的紧凑证据:**列出全部端点** + 表单字段 + 样例返回(不截掉关键端点)。"""
    ev = ev or {}
    lines = ["全部端点(name|method|endpoint):"]
    for a in (ev.get("actions") or []):
        lines.append(f"  {a.get('name')}|{(a.get('method') or 'GET')}|{a.get('endpoint')}")
    if ev.get("form_fields"):
        lines.append("表单字段(key|label):")
        lines += [f"  {f.get('key')}|{f.get('label')}" for f in ev["form_fields"]]
    if ev.get("sample_reads"):
        lines.append("样例返回(端点→出参路径):")
        lines += [f"  {s.get('endpoint')} → {(s.get('output_paths') or [])[:15]}" for s in ev["sample_reads"]]
    return "\n".join(lines)


def _build_plan(data: dict, goal: GoalBrief) -> PlanBody:
    """v3:完全由模型方案构建 PlanBody(无领域基线回退)。常量来自 goal(__templateId__ 等),
    字段描述用证据表单标签补全,证据裁剪后随 plan 带给编码器。"""
    fc = data.get("fact_check") or {}
    fact = FactCheckSpec.model_validate(fc) if fc.get("endpoint") else None
    # 运行期常量:goal 注入的 __xxx__(模板 id 等),但 __base_url__ 由 invoke 单独注入,不入 consts
    consts = {k: v for k, v in goal.test_input.items()
              if k.startswith("__") and k.endswith("__") and k != "__base_url__"}
    user_fields = list(data.get("user_fields") or [])
    labels = {f.get("key"): f.get("label") for f in ((goal.evidence or {}).get("form_fields") or [])}
    field_docs = {k: labels[k] for k in user_fields if labels.get(k) and labels[k] != k}
    return PlanBody(
        flow=goal.flow, strategy="llm",
        steps=list(data.get("steps") or []), contract=dict(data.get("contract") or {}),
        user_fields=user_fields, required_fields=list(data.get("required_fields") or []),
        field_docs=field_docs, consts=consts,
        evidence=_trim_evidence(goal.evidence, data.get("endpoints") or []),
        success_rule=data.get("success_rule"), fact_check=fact,
    )


_CONTRACT = (
    "只输出一个 JSON 对象(不要其它文字),字段:\n"
    '  steps: string[](人类可读步骤)\n'
    '  endpoints: string[](本流程要调的端点,必须取自下方证据 actions.endpoint;**只列相关的,别贪多**)\n'
    '  contract: object(关键契约要点)\n'
    '  user_fields/required_fields: string[](取自证据 form_fields / params_in)\n'
    '  success_rule: string(判定表达式,基于 response;**只能用属性点取/下标/比较**,'
    '如 "response.code == null or response.code == 200";\n'
    "    **只引用最终接口响应里确实会出现的字段**(如 response.code);**不要臆造 data 等不存在的字段**,"
    "也别把发起步骤(startFlow)的响应字段(如 data)当成提交步骤的成功标志;\n"
    "    **禁止函数/方法调用(如 .get())**、禁止 None(用 null)、禁止 JS 的 &&/||)\n"
    '  fact_check: {endpoint, method:"GET", assert_expr, retries, backoff_s}\n'
    "    —— 必须能区分『真生效』与『空操作』:回查本业务的列表/详情/待办,用提交返回的 id/单号过滤,"
    "断言出现新记录或状态变化,如 \"response.total > 0\";**不要只断言『返回200』,也不要用 .get()**。\n"
    "硬约束:endpoints 与 fact_check.endpoint 只能用证据里出现过的端点,字段只能用证据里的;不得凭空捏造;"
    "判定表达式只用 属性点取(response.code)/下标/比较/null,**不准函数调用**。"
)


def _plan_prompt(goal: GoalBrief, reasons: list[str] | None, prev: PlanBody | None) -> str:
    ev = _compact_evidence(goal.evidence)[:16000]
    base = (f"目标:为业务流程「{goal.flow}」按下方**证据**拆解出可执行方案。\n"
            f"证据(全部端点 + 表单字段 + 样例返回):\n{ev}\n\n{_CONTRACT}\n"
            "注意:证据里**已列出全部端点**,本流程要用的发起/存表单/提交端点就在其中,自己找出来;"
            "若确实缺某端点,在 contract 里说明,**不要凭空捏造**。")
    if prev is not None:
        base += (f"\n\n上一版方案被**事实核查证伪**(代码能跑但没真生效),原因:\n- "
                 + "\n- ".join(reasons or []) + "\n请据此**重拆方案**(可能缺步骤/串联错/契约错),再输出 JSON。")
    return base


# 文本生成式 spawn:async (prompt) -> 模型文本(内含 JSON)。默认走 OpenAI 兼容。
TextSpawn = Callable[[str], Awaitable[str]]


class LlmPlanner:
    """真实 LLM 拆解器:读 goal.evidence 产结构化 Plan,硬校验端点/字段存在性。"""

    def __init__(self, *, spawn: TextSpawn | None = None) -> None:
        from functools import partial
        self._spawn = spawn or partial(openai_text_spawn, tag="planner")

    async def _produce(self, goal: GoalBrief, strategy, *, reasons=None, prev=None) -> PlanBody:  # noqa: ANN001
        # v3:全模型驱动 + 不合规重提示重试(不回退硬编码领域基线)
        last_errs: list[str] = []
        for _ in range(3):
            prompt = _plan_prompt(goal, reasons, prev)
            if last_errs:
                prompt += "\n\n上次方案不合规,必须修正:\n- " + "\n- ".join(last_errs)
            try:
                data = _extract_json(await self._spawn(prompt))
            except PlanError as e:
                last_errs = [str(e)]
                continue
            errs = validate_plan(data, goal.evidence)
            if not errs:
                return _build_plan(data, goal)
            last_errs = errs
        raise PlanError("; ".join(last_errs) or "方案多次不合规")

    async def plan(self, goal: GoalBrief, strategy) -> PlanBody:  # noqa: ANN001
        return await self._produce(goal, strategy)

    async def replan(self, goal: GoalBrief, strategy, prev: PlanBody, reasons: list[str]) -> PlanBody:  # noqa: ANN001
        return await self._produce(goal, strategy, reasons=reasons, prev=prev)
