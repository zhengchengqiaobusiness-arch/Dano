"""事实核查(流程9 核心)。

杜绝「点了提交就算成功」:必须**重新查询、比对操作前后快照**,确认数据真的变了。
核查判据用 shared/expr 表达,context = {before, after, response, fields}。

示例(建请假):"after.balance == before.balance - fields.days and response.request_id != null"
"""

from __future__ import annotations

from typing import Any

import structlog
from pydantic import BaseModel

from dano.shared.expr import ExprError, safe_eval

log = structlog.get_logger(__name__)


class FactCheckResult(BaseModel):
    passed: bool
    detail: str = ""


class FactChecker:
    def check(
        self,
        expr: str | None,
        *,
        before: dict[str, Any],
        after: dict[str, Any],
        response: dict[str, Any],
        fields: dict[str, Any],
    ) -> FactCheckResult:
        """对核查表达式求值。无表达式 → 视为不要求核查(查询类动作)。"""
        if not expr:
            return FactCheckResult(passed=True, detail="无需事实核查(查询类)")
        ctx = {"before": before, "after": after, "response": response, "fields": fields}
        try:
            passed = bool(safe_eval(expr, ctx))
        except ExprError as e:
            log.warning("fact_check.expr_error", expr=expr, error=str(e))
            return FactCheckResult(passed=False, detail=f"核查表达式错误: {e}")
        detail = "操作前后差异符合预期" if passed else f"数据未按预期改变: {expr}"
        log.info("fact_check", passed=passed, expr=expr)
        return FactCheckResult(passed=passed, detail=detail)
