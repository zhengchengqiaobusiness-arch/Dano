"""保障期工作队列:漂移自愈触发 + 能力缺口登记。

把「检测/判定」与「实际再生成」解耦:运行期只**入队**自愈/新增请求,
由后台维护流程(或人工)消费,避免在用户请求路径上同步跑重活。
接口化(内存实现,生产可换 Redis/DB)。
"""

from __future__ import annotations

import structlog
from pydantic import BaseModel, Field

log = structlog.get_logger(__name__)


class HealRequest(BaseModel):
    skill_id: str
    subsystem: str
    action: str
    reason: str = ""


class GapRequest(BaseModel):
    action_hint: str
    fields: dict = Field(default_factory=dict)


class HealQueue:
    """流程11 自愈请求队列(连续失败分类为页面/字段变更时入队)。"""

    def __init__(self) -> None:
        self._items: list[HealRequest] = []

    async def enqueue(self, req: HealRequest) -> None:
        # 去重:同 skill 只保留一条待处理
        if not any(i.skill_id == req.skill_id for i in self._items):
            self._items.append(req)
            log.info("heal.enqueued", skill_id=req.skill_id, reason=req.reason)

    async def pending(self) -> list[HealRequest]:
        return list(self._items)

    async def pop(self) -> HealRequest | None:
        return self._items.pop(0) if self._items else None


class SkillGapQueue:
    """流程12 能力缺口队列:做事但无对应动作 Skill → 登记为产品待办。"""

    def __init__(self) -> None:
        self._items: list[GapRequest] = []

    async def register(self, action_hint: str, fields: dict) -> None:
        self._items.append(GapRequest(action_hint=action_hint, fields=fields))
        log.info("gap.registered", action_hint=action_hint)

    async def pending(self) -> list[GapRequest]:
        return list(self._items)
