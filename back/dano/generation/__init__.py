"""goal 模式「代码自动生成」子系统。

- artifacts:目标/迭代/结果数据契约
- strategies:按业务可插拔的生成策略(可变层)
- coder:创造步骤(拆解/编码/修复)的生成器协议(pi=goal 会话;测试可注入 Fake)
- controller:GenerationLoop —— 拆解→编码→测试→驳回→修复→发布 的闭环(不变层)
"""

from dano.generation.artifacts import Budget, GenerationResult, GoalBrief, IterationRecord
from dano.generation.controller import GenerationLoop

__all__ = ["Budget", "GenerationResult", "GoalBrief", "IterationRecord", "GenerationLoop"]
