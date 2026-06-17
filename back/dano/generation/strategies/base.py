"""生成策略协议(可变层):按业务区分「怎么拆、怎么定方案、给什么编码骨架」。

不变的循环(controller)调用策略的这几个方法;新增业务类型只需实现一个策略并注册。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from dano.generation.artifacts import GoalBrief
from dano.shared.asset_bodies import PlanBody


@runtime_checkable
class GenerationStrategy(Protocol):
    name: str

    def matches(self, actions: list[dict]) -> bool:
        """该动作清单是否适用本策略(具体策略在前、兜底策略在后)。"""
        ...

    def decompose(self, goal: GoalBrief) -> PlanBody:
        """拆解 + 定方案:把目标流程拆成步骤与契约,产出可评审的 PlanBody。"""
        ...

    def code_skeleton(self, plan: PlanBody) -> str:
        """给「编码」步骤的参考骨架/约束(入口 run(inputs, creds)->dict,源码零凭证)。"""
        ...
