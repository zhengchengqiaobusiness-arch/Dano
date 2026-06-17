"""制度 + 风险闸门(流程6 第6步)。

- L4/L5:拒绝
- L3:需确认卡片(用户确认)
- 制度规则:对意图字段跑规则,effect=拦截→拒绝,转审批→需确认
- 否则放行
制度规则求值复用 shared/expr。
"""

from __future__ import annotations

from enum import StrEnum

import structlog
from pydantic import BaseModel

from dano.shared.asset_bodies import PolicyRuleBody
from dano.shared.enums import RiskLevel
from dano.shared.expr import ExprError, safe_eval

log = structlog.get_logger(__name__)


class GateAction(StrEnum):
    ALLOW = "allow"
    REJECT = "reject"
    CONFIRM = "confirm"


class GateDecision(BaseModel):
    action: GateAction
    reason: str = ""


class PolicyGate:
    def decide(
        self, *, risk_level: RiskLevel, fields: dict, policy: PolicyRuleBody | None
    ) -> GateDecision:
        # 高风险直接拒绝
        if risk_level in (RiskLevel.L4, RiskLevel.L5):
            return GateDecision(action=GateAction.REJECT, reason=f"风险等级 {risk_level} 禁止执行")

        # 制度规则
        if policy is not None:
            for rule in policy.rules:
                try:
                    hit = bool(safe_eval(rule.condition, fields))
                except ExprError:
                    continue
                if not hit:
                    continue
                if rule.effect == "拦截":
                    return GateDecision(action=GateAction.REJECT, reason=f"制度拦截: {rule.description}")
                if rule.effect == "转审批":
                    return GateDecision(action=GateAction.CONFIRM, reason=f"制度需审批: {rule.description}")

        # L3 需确认
        if risk_level == RiskLevel.L3:
            return GateDecision(action=GateAction.CONFIRM, reason="L3 写动作需用户确认")

        return GateDecision(action=GateAction.ALLOW, reason="L1/L2 直接执行")
