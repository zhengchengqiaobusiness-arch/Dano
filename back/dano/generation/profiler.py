"""v2-M2 流程画像/路由:决定一条流程怎么拆——模板配方 / 确定性策略 / 交给 LLM 拆解。

这是"不同流程拆开"的落点:不再一招 workflow_bpmn 套所有写流程。
- 命中**已沉淀模板配方**(如请假)→ route=template(快、稳、零回归)。
- 全只读 GET / 审批办理 → route=strategy(现有确定性策略够用)。
- 其它写/复合/文件上传/识别不出 → route=llm(交 LLM 按证据各自拆,M3 接管)。

只做"判别 + 选路",不执行;复用 strategies 的 matches 信号,不重造判别。
"""

from __future__ import annotations

import structlog
from pydantic import BaseModel, Field

from dano.generation.strategies import select_strategy

log = structlog.get_logger(__name__)


class FlowProfile(BaseModel):
    kind: str                              # workflow_bpmn / approval / crud_query / file_upload / simple_http / unknown
    route: str                             # template | strategy | llm
    strategy_name: str | None = None       # route=strategy 时选中的现有策略名
    has_template_recipe: bool = False      # 是否命中 OA 模板的现成复合配方
    reason: str = ""
    signals: list[str] = Field(default_factory=list)


def _is_file_upload(actions: list[dict]) -> bool:
    blob = " ".join((a.get("endpoint", "") + " " + a.get("name", "") + " "
                     + " ".join(a.get("params_in", []))) for a in actions).lower()
    return any(k in blob for k in ("upload", "multipart", "/file/", "attach", "附件", "上传"))


def _is_approval(actions: list[dict]) -> bool:
    """审批办理信号(同 ApprovalStrategy):靠 name/summary 关键词,不靠端点——因发起/办理共用
    /biz/flow/submit,select_strategy 会被 workflow_bpmn 抢先匹配,故这里独立判别。"""
    blob = " ".join((a.get("name", "") + " " + a.get("summary", "")) for a in actions).lower()
    return any(k in blob for k in ("approve", "agree", "reject", "审批", "驳回", "同意", "办理"))


def _template_recipe_actions(template, flow_name: str) -> bool:  # noqa: ANN001
    """该流程是否命中模板里的现成复合配方(按配方 action 名匹配)。"""
    if template is None:
        return False
    try:
        return any(getattr(wf, "action", None) == flow_name for wf in template.workflows())
    except Exception:  # noqa: BLE001 - 模板配方异常不应让画像崩
        return False


def profile_flow(actions: list[dict], *, template=None, flow_name: str = "") -> FlowProfile:  # noqa: ANN001
    """给一条流程的动作清单选路。template 为匹配到的 OATemplate(可空)。"""
    strat = select_strategy(actions)
    strat_name = getattr(strat, "name", None)
    has_recipe = _template_recipe_actions(template, flow_name)
    all_get = bool(actions) and all((a.get("method", "GET") or "GET").upper() == "GET" for a in actions)
    file_up = _is_file_upload(actions)

    # 1) 命中模板配方 → 走模板(已验证,稳,零回归)
    if has_recipe:
        return FlowProfile(kind=strat_name or "workflow_bpmn", route="template", strategy_name=strat_name,
                           has_template_recipe=True, reason="命中 OA 模板现成复合配方,走模板",
                           signals=["template_recipe"])
    # 2) 纯只读查询 → 确定性 crud_query(安全无副作用)
    if all_get:
        return FlowProfile(kind="crud_query", route="strategy", strategy_name=strat_name or "crud_query",
                           reason="全只读 GET,走确定性查询策略", signals=["all_get"])
    # 3) 审批办理(同意/驳回/退回)→ 确定性 approval(独立判别,不靠被抢匹配的 strat_name)
    if _is_approval(actions):
        return FlowProfile(kind="approval", route="strategy", strategy_name="approval",
                           reason="审批办理类,走确定性 approval 策略", signals=["approval"])
    # 4) 文件上传/附件类 → 交 LLM(扁平合并覆盖不了)
    if file_up:
        return FlowProfile(kind="file_upload", route="llm", strategy_name=strat_name,
                           reason="含文件上传/附件,交 LLM 按证据拆", signals=["file_upload"])
    # 5) 其它写/复合(无现成配方)→ 交 LLM 按证据各自拆(不再套请假式 workflow_bpmn)
    return FlowProfile(kind=strat_name or "unknown", route="llm", strategy_name=strat_name,
                       reason="写/复合且无模板配方,交 LLM 按证据拆", signals=["write_no_recipe"])
