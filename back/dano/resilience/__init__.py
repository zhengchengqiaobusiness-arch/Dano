"""保障期:流程10 失败处理与熔断 · 流程11 漂移自愈与再生成。

流程10:分类→留证→限次重试/转人工→达阈值暂停。
流程11:只处理页面/字段漂移→增量再侦察→最小补丁→离线验证→灰度+回滚。
"""

from dano.resilience.circuit_breaker import CircuitBreaker, InMemoryCounter
from dano.resilience.classifier import classify
from dano.resilience.drift import DriftDetector
from dano.resilience.handler import FailureHandler, RecoveryDecision

# 注:流程11 自愈在重写里改由 pi Sidecar 驱动,见 dano.assurance.service.self_heal。

__all__ = [
    "classify", "CircuitBreaker", "InMemoryCounter",
    "FailureHandler", "RecoveryDecision", "DriftDetector",
]
