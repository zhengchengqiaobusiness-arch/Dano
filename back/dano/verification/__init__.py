"""流程9 验证闭环:断言判定 + 事实核查(重查比对)+(可选)审判。

杜绝「点了提交就算成功」:必须重新查询、比对操作前后,确认数据真的变了。
"""

from dano.verification.closure import ClosureResult, VerificationClosure
from dano.verification.fact_check import FactChecker, FactCheckResult
from dano.verification.judge import JudgeAgent, Verdict

__all__ = [
    "VerificationClosure",
    "ClosureResult",
    "FactChecker",
    "FactCheckResult",
    "JudgeAgent",
    "Verdict",
]
