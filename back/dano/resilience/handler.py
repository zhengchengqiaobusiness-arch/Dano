"""流程10:失败处理与熔断编排。

铁律:失败后绝不继续往下执行。本流程只做分类、留证、限次重试和暂停判定。
分类 → 计数 → 达阈值则异常暂停(进流程12)→ 返回恢复决策(重试/转11/转人工)。
"""

from __future__ import annotations

import structlog
from pydantic import BaseModel

from dano.lifecycle.state_machine import SkillLifecycle
from dano.resilience.circuit_breaker import CircuitBreaker
from dano.resilience.classifier import classify
from dano.shared.enums import FailureClass, RecoveryAction
from dano.shared.models import ExecResult

log = structlog.get_logger(__name__)


class RecoveryDecision(BaseModel):
    action: RecoveryAction
    failure_class: FailureClass | None = None
    count: int = 0
    tripped: bool = False        # 达阈值,已熔断暂停
    should_retry: bool = False   # 可当场限次重试
    reason: str = ""


class FailureHandler:
    def __init__(
        self,
        *,
        breaker: CircuitBreaker | None = None,
        lifecycle: SkillLifecycle | None = None,
        max_retries: int = 2,
    ) -> None:
        self.breaker = breaker or CircuitBreaker()
        self.lifecycle = lifecycle
        self.max_retries = max_retries

    async def handle(
        self, skill_id: str, exec_result: ExecResult, *, attempt: int = 1
    ) -> RecoveryDecision:
        fc = exec_result.failure_class
        action = classify(fc)
        count, tripped = await self.breaker.record_failure(skill_id, fc.value if fc else "unknown")

        if tripped and self.lifecycle is not None:
            await self.lifecycle.suspend(skill_id)  # 异常暂停 → 流程12

        should_retry = (
            action == RecoveryAction.RETRY and not tripped and attempt <= self.max_retries
        )
        reason = {
            RecoveryAction.RETRY: "登录/网络抖动,限次受控重试",
            RecoveryAction.REGENERATE: "页面/字段变更 → 流程11 自愈",
            RecoveryAction.HUMAN: "权限/参数/配置/系统 → 转人工",
        }[action]
        if tripped:
            reason = f"同类失败达阈值({count})→ 异常暂停,进流程12"

        log.info("failure.handled", skill_id=skill_id, action=action.value,
                 count=count, tripped=tripped, retry=should_retry)
        return RecoveryDecision(action=action, failure_class=fc, count=count,
                                tripped=tripped, should_retry=should_retry, reason=reason)

    async def on_success(self, skill_id: str) -> None:
        await self.breaker.reset(skill_id)
