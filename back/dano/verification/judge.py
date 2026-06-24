"""审判机制(流程9 可选增强)。

由另一个大模型独立审查整条执行轨迹是否合理(意图与动作匹配、断言是否真被满足、
有无越界副作用、证据是否自洽),输出「合理 / 不合理 + 理由」。

⚠ **当前默认关闭**:验证闭环 `VerificationClosure(judge=None)` 默认不注入 JudgeAgent
(见 verification/closure.py:`_should_judge` —— judge is None 即恒不审判),运行期判定主要靠
**确定性的断言 + 事实核查**,LLM 推理归 pi。本模块是"接好但未启用"的可选层,**不是死代码**。

何时启用(须显式注入 JudgeAgent 到 VerificationClosure):接入期自测可 force_judge=True 开;
运行期对 L3+(judge_min_risk 默认 L3)或新发布/刚自愈 skill 灰度期开。它叠在断言与事实核查之上,
不替代二者。接口化(StructuredLLM Protocol)以便注入 fake / 切换实现。
"""

from __future__ import annotations

from typing import Any, Protocol, TypeVar

import structlog
from pydantic import BaseModel

log = structlog.get_logger(__name__)

_M = TypeVar("_M", bound=BaseModel)


class StructuredLLM(Protocol):
    """结构化 LLM 接口(审判用;重写期 judge 默认关,LLM 推理主要归 pi)。"""

    async def emit(self, *, system: str, user: str, model_cls: type[_M]) -> _M: ...


class Verdict(BaseModel):
    reasonable: bool
    reason: str = ""


class JudgeAgent:
    def __init__(self, llm: StructuredLLM) -> None:
        self.llm = llm

    async def review(self, *, intent: str, action: str, trace: dict[str, Any]) -> Verdict:
        system = (
            "你是执行审判智能体。独立审查一条任务执行轨迹是否合理:"
            "意图与动作是否匹配、断言是否真被满足(防表面满足)、有无越界副作用、证据是否自洽。"
            "只输出结构化结论。"
        )
        user = f"意图: {intent}\n动作: {action}\n执行轨迹: {trace}"
        verdict = await self.llm.emit(system=system, user=user, model_cls=Verdict)
        log.info("judge.review", action=action, reasonable=verdict.reasonable)
        return verdict
