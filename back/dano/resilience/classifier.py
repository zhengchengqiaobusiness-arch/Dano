"""流程10 第1步:失败快速分类 → 恢复决策。

登录/短暂网络 → 限次重试;页面/字段变更 → 流程11 自愈;权限/参数/配置/系统 → 转人工。
"""

from __future__ import annotations

from dano.shared.enums import FailureClass, RecoveryAction

_MAP: dict[FailureClass, RecoveryAction] = {
    FailureClass.LOGIN: RecoveryAction.RETRY,
    FailureClass.NETWORK: RecoveryAction.RETRY,
    FailureClass.PAGE_FIELD: RecoveryAction.REGENERATE,
    FailureClass.PERMISSION: RecoveryAction.HUMAN,
    FailureClass.PARAM: RecoveryAction.HUMAN,
    FailureClass.CONFIG: RecoveryAction.HUMAN,
    FailureClass.SYSTEM: RecoveryAction.HUMAN,
}


def classify(failure_class: FailureClass | None) -> RecoveryAction:
    if failure_class is None:
        return RecoveryAction.HUMAN
    return _MAP.get(failure_class, RecoveryAction.HUMAN)
