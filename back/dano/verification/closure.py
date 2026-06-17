"""验证闭环编排(流程9):断言判定 → 事实核查 → 三要素 → (可选)审判 → 终态。"""

from __future__ import annotations

from typing import Any

import structlog
from pydantic import BaseModel

from dano.verification.fact_check import FactChecker
from dano.verification.judge import JudgeAgent
from dano.shared.enums import Outcome, RiskLevel, TaskState
from dano.shared.models import ExecResult

log = structlog.get_logger(__name__)

_RISK_ORDER = {RiskLevel.L1: 1, RiskLevel.L2: 2, RiskLevel.L3: 3, RiskLevel.L4: 4, RiskLevel.L5: 5}


class ClosureResult(BaseModel):
    state: TaskState
    detail: str = ""


class VerificationClosure:
    """审判何时开(文档流程9):接入期默认开(force_judge);运行期 L3+ 开,L1/L2 不开。"""

    def __init__(
        self,
        *,
        fact_checker: FactChecker | None = None,
        judge: JudgeAgent | None = None,
        judge_min_risk: RiskLevel = RiskLevel.L3,
    ) -> None:
        self.fact_checker = fact_checker or FactChecker()
        self.judge = judge  # None = 无审判能力
        self.judge_min_risk = judge_min_risk

    def _should_judge(self, risk_level: RiskLevel | None, force: bool) -> bool:
        if self.judge is None:
            return False
        if force:
            return True
        if risk_level is None:
            return False
        return _RISK_ORDER[risk_level] >= _RISK_ORDER[self.judge_min_risk]

    async def verify(
        self,
        exec_result: ExecResult,
        *,
        fact_expr: str | None,
        before: dict[str, Any],
        after: dict[str, Any],
        fields: dict[str, Any],
        intent: str = "",
        action: str = "",
        risk_level: RiskLevel | None = None,
        force_judge: bool = False,
    ) -> ClosureResult:
        # 1. 断言全为真?
        if exec_result.outcome != Outcome.PASSED:
            return ClosureResult(state=TaskState.FAILED, detail="断言未全部通过")

        # 2. 事实核查:重查比对操作前后
        fc = self.fact_checker.check(
            fact_expr, before=before, after=after,
            response=exec_result.structured_output, fields=fields,
        )
        if not fc.passed:
            return ClosureResult(state=TaskState.FAILED, detail=fc.detail)

        # 3. 三要素:真实成果 + 新鲜证据 + 审计已写
        has_result = bool(exec_result.structured_output)
        has_evidence = exec_result.evidence.response_body is not None
        if not (has_result and has_evidence):
            return ClosureResult(state=TaskState.FAILED, detail="三要素不齐(成果/证据缺失)")

        # 4. 审判(按风险/接入期门控)
        if self._should_judge(risk_level, force_judge):
            verdict = await self.judge.review(
                intent=intent, action=action,
                trace={"assertions": [r.model_dump() for r in exec_result.assertion_results],
                       "output": exec_result.structured_output},
            )
            if not verdict.reasonable:
                return ClosureResult(state=TaskState.FAILED, detail=f"审判不合理: {verdict.reason}")

        log.info("closure.passed", action=action)
        return ClosureResult(state=TaskState.COMPLETED, detail="通过:断言+事实核查+三要素")
