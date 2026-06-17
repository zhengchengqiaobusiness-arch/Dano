"""断言引擎(流程7/8 断言铁律)。

执行结果只有两态:**跑通(全部断言为真)或跑不通(任一为假)**。
断言是声明式、机器可判的(由 pi coding 在生成连接器时产出),用 shared/expr 安全求值。
绝不接受「应该成功了 / 大概可以」。
"""

from __future__ import annotations

import structlog

from dano.shared.asset_bodies import Assertion, Assertions
from dano.shared.enums import Outcome
from dano.shared.expr import ExprError, safe_eval
from dano.shared.models import AssertionResult

log = structlog.get_logger(__name__)


class AssertionEngine:
    def evaluate_phase(
        self, items: list[Assertion], context: dict
    ) -> list[AssertionResult]:
        results: list[AssertionResult] = []
        for a in items:
            try:
                passed = bool(safe_eval(a.expr, context))
                detail = "" if passed else f"断言为假: {a.expr}"
            except ExprError as e:
                passed = False
                detail = f"断言求值失败: {e}"
            results.append(AssertionResult(name=a.name, passed=passed, detail=detail))
        return results

    def evaluate(
        self, assertions: Assertions, *, pre_context: dict, post_context: dict | None = None
    ) -> tuple[Outcome, list[AssertionResult]]:
        """对前置(+可选后置)断言整体判定。任一为假 → FAILED。"""
        results = self.evaluate_phase(assertions.pre, pre_context)
        if post_context is not None:
            results += self.evaluate_phase(assertions.post, post_context)
        outcome = Outcome.PASSED if all(r.passed for r in results) else Outcome.FAILED
        log.info(
            "assertion.evaluated",
            outcome=outcome.value,
            failed=[r.name for r in results if not r.passed],
        )
        return outcome, results
