"""结构化业务目标(P1·WS5):接入期据材料**确定性**生成 Goal,作复合流程的 grounding 锚。

- `build_goal`:从 spec + dialect + templateId + 业务字段 合成 GoalBody(成功标准 / 候选步 / 禁止步)。
- `goal_grounding`:校验工作流步骤不得命中 forbidden_steps(删除/审批他人/越权)。
全部纯函数、零业务字面量:成功标准按"提交类复合流程"通用模板 + 审批链是否存在派生;
禁止步从 spec 动作名按危险动词模式抽(delete/approve 他人/terminate/bypass…),不针对某个业务写死。
"""

from __future__ import annotations

import re

from dano.capabilities import doc_parser
from dano.shared.asset_bodies import GoalBody

# 危险动词:相对"提交本人申请"而言越权/破坏性的动作 → 进 forbidden_steps,不得编入提交流程。
_FORBIDDEN_VERBS = (
    "delete", "remove", "terminate", "revoke", "bypass", "cancelall",
    "approve", "agree", "reject", "return", "admin", "jumpactivity", "assign", "delegate",
)


def forbidden_actions(spec: dict) -> list[str]:
    """从 spec 全部动作里按危险动词抽出"提交流程禁止编入"的动作名(去重排序)。"""
    out: set[str] = set()
    try:
        for a in doc_parser.parse_openapi(spec or {}):
            n = a.name.lower().replace("_", "")
            if any(v in n for v in _FORBIDDEN_VERBS):
                out.add(a.name)
    except Exception:  # noqa: BLE001 - 解析异常不应阻断建流程
        return []
    return sorted(out)


def _slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", s or "").strip("_") or "goal"


def build_goal(spec: dict, dialect, *, template_id: str = "", business: str = "",  # noqa: ANN001
               title: str = "", required_inputs: list[str] | None = None,
               optional_inputs: list[str] | None = None, candidate_steps: list[str] | None = None,
               risk_level: str = "L3", requires_confirmation: bool = True) -> GoalBody:
    """据接入材料确定性合成业务 Goal。成功标准按"提交类复合流程"通用派生 + 审批链存在则加节点标准。"""
    meta = {}
    if dialect is not None and template_id:
        try:
            meta = dialect.parse_approval_chain(spec, template_id) or {}
        except Exception:  # noqa: BLE001
            meta = {}
    biz = business or meta.get("flow") or template_id or "business"
    success = ["业务单据已创建", "表单字段已真实持久化", "审批流程已发起"]
    if meta.get("approvalChain"):
        success.append("当前流程已进入有效审批节点")
    return GoalBody(
        goal_id=f"{_slug(business or template_id)}.submit",
        business_type=biz,
        selected_template=template_id or "",
        intent=title or f"创建并提交{biz}",
        required_inputs=list(required_inputs or []),
        optional_inputs=list(optional_inputs or []),
        success_criteria=success,
        candidate_steps=list(candidate_steps or []),
        forbidden_steps=forbidden_actions(spec),
        risk_level=risk_level,
        requires_confirmation=requires_confirmation,
    )


def goal_grounding(goal: GoalBody, step_actions: list[str]) -> list[str]:
    """硬校验:工作流步骤不得命中 Goal.forbidden_steps(危险动作)。返回问题列表(空=通过)。"""
    forb = set(goal.forbidden_steps)
    return [f"步骤 '{s}' 命中 Goal.forbiddenSteps(危险动作:删除/审批他人/越权),禁止编入提交流程"
            for s in step_actions if s in forb]
