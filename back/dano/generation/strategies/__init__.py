"""策略注册表:具体策略在前、兜底(simple_http)在后。

新增业务策略:实现 base.GenerationStrategy + register_strategy(...) 即可,无需改循环。
"""

from __future__ import annotations

from dano.generation.strategies.approval import ApprovalStrategy
from dano.generation.strategies.base import GenerationStrategy
from dano.generation.strategies.crud_query import CrudQueryStrategy
from dano.generation.strategies.simple_http import SimpleHttpStrategy
from dano.generation.strategies.workflow_bpmn import WorkflowBpmnStrategy

_STRATEGIES: list[GenerationStrategy] = []


def register_strategy(strategy: GenerationStrategy) -> None:
    """注册策略(插到最前 = 更高优先级);兜底策略最先注册,故始终在末位。"""
    _STRATEGIES.insert(0, strategy)


def get_strategy(name: str) -> GenerationStrategy | None:
    return next((s for s in _STRATEGIES if s.name == name), None)


def select_strategy(actions: list[dict]) -> GenerationStrategy | None:
    """按动作清单选策略:从前到后取第一个 matches 的(具体优先,simple_http 兜底)。"""
    for s in _STRATEGIES:
        try:
            if s.matches(actions):
                return s
        except Exception:  # noqa: BLE001 - 单个策略匹配异常不应让选择崩
            continue
    return None


# 注册顺序 = 优先级反序(register 插到最前)。兜底先注册(末位),具体策略后注册(前位)。
# 最终匹配优先级:workflow_bpmn > approval > crud_query > simple_http(兜底)。
register_strategy(SimpleHttpStrategy())
register_strategy(CrudQueryStrategy())
register_strategy(ApprovalStrategy())
register_strategy(WorkflowBpmnStrategy())

__all__ = ["register_strategy", "get_strategy", "select_strategy", "GenerationStrategy"]
