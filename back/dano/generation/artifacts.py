"""goal 模式代码生成的数据契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Budget:
    """生成预算:有界迭代,防 goal 会话不收敛(不存在无限重试)。"""

    max_iters: int = 4


@dataclass
class GoalBrief:
    """一次生成的目标:为某业务流程产出可执行 adapter,验收=沙箱+(后续)事实核查+评审。"""

    run_id: str
    system_instance_id: str
    flow: str                                   # 目标业务流程名,如 submit_leave
    actions: list[dict] = field(default_factory=list)   # parse_spec 动作清单(已选类别)
    test_input: dict = field(default_factory=dict)      # 测试账号用的业务字段值
    budget: Budget = field(default_factory=Budget)
    evidence: dict | None = None                # v2:理解阶段采集的 FlowEvidence(供 LLM 拆解)
    business: str = ""                          # 展开模式:所属业务(同业务多操作共用,供导出归组成剧本 skill)


@dataclass
class IterationRecord:
    """一轮迭代的可追溯记录(第 i 轮:过没过、驳回原因、对应草案)。"""

    index: int
    passed: bool
    reasons: list[str]
    asset_draft_id: str | None = None


@dataclass
class GenerationResult:
    """一次生成的最终结果(成功=已发布 adapter;失败=耗尽预算)。"""

    ok: bool
    flow: str
    asset_id: str | None
    iterations: list[IterationRecord]
    reason: str = ""

    @property
    def rejections(self) -> int:
        """被驳回的轮数(用于证明『非一次成型』)。"""
        return sum(1 for it in self.iterations if not it.passed)
