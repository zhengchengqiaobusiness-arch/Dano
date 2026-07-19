"""流程12:Skill 生命周期状态机。

模板 → 复制到A公司 → 绑定资产 → 测试中 →(测试通过)待发布 → 已发布 → 运行中
                                          ↘(测试失败)绑定资产
运行中 →(漂移/连续失败达阈值)异常暂停 →(流程11生成补丁)绑定资产
                                       ↘(人工确认无需改资产)运行中
运行中 → 已下线

规则:不允许「AI 生成草稿直接用」;只管状态流转,失败修复交流程10/11。
非法转移直接拒绝(抛错),保证状态可审计。
"""

from __future__ import annotations

from typing import Protocol

import structlog
from pydantic import BaseModel

from dano.shared.enums import SkillState, Subsystem

log = structlog.get_logger(__name__)

# 合法转移表
_TRANSITIONS: dict[SkillState, set[SkillState]] = {
    SkillState.TEMPLATE: {SkillState.COPIED},
    SkillState.COPIED: {SkillState.BOUND},
    SkillState.BOUND: {SkillState.TESTING},
    SkillState.TESTING: {SkillState.PENDING_RELEASE, SkillState.BOUND},  # 通过/失败
    SkillState.PENDING_RELEASE: {SkillState.PUBLISHED},
    # §5:阶段一停在「已发布」,不自动「运行中」。已发布可被暂停;RUNNING 由就绪检查另定。
    SkillState.PUBLISHED: {SkillState.RUNNING, SkillState.SUSPENDED, SkillState.RETIRED},
    SkillState.RUNNING: {SkillState.SUSPENDED, SkillState.RETIRED},
    SkillState.SUSPENDED: {SkillState.BOUND, SkillState.PUBLISHED, SkillState.RUNNING},
    SkillState.RETIRED: set(),
}


class SkillRecord(BaseModel):
    skill_id: str
    subsystem: Subsystem
    action: str
    state: SkillState = SkillState.TEMPLATE
    asset_version: int = 0          # 当前绑定/发布的资产版本(用于回滚)
    history: list[str] = []


class IllegalTransition(RuntimeError):
    pass


class SkillStore(Protocol):
    async def get(self, skill_id: str) -> SkillRecord | None: ...
    async def put(self, record: SkillRecord) -> None: ...
    async def delete(self, skill_id: str) -> int: ...
    async def all(self) -> list[SkillRecord]: ...


class InMemorySkillStore:
    def __init__(self) -> None:
        self._rows: dict[str, SkillRecord] = {}

    async def get(self, skill_id: str) -> SkillRecord | None:
        return self._rows.get(skill_id)

    async def put(self, record: SkillRecord) -> None:
        self._rows[record.skill_id] = record

    async def delete(self, skill_id: str) -> int:
        return 1 if self._rows.pop(skill_id, None) is not None else 0

    async def all(self) -> list[SkillRecord]:
        return list(self._rows.values())


class SkillLifecycle:
    """生命周期状态机操作。所有状态变更经此,保证合法 + 留痕。"""

    def __init__(self, store: SkillStore | None = None) -> None:
        self.store = store or InMemorySkillStore()

    async def register(self, skill_id: str, subsystem: Subsystem, action: str) -> SkillRecord:
        rec = SkillRecord(skill_id=skill_id, subsystem=subsystem, action=action)
        await self.store.put(rec)
        return rec

    async def transition(self, skill_id: str, to: SkillState) -> SkillRecord:
        rec = await self.store.get(skill_id)
        if rec is None:
            raise KeyError(skill_id)
        if to not in _TRANSITIONS[rec.state]:
            raise IllegalTransition(f"{skill_id}: {rec.state} → {to} 非法")
        rec.history.append(f"{rec.state}→{to}")
        rec.state = to
        await self.store.put(rec)
        log.info("lifecycle.transition", skill_id=skill_id, to=to.value)
        return rec

    async def drive(self, skill_id: str, path: list[SkillState]) -> SkillRecord:
        """按给定路径连续流转(如 模板→...→运行中)。"""
        rec = await self.store.get(skill_id)
        for to in path:
            rec = await self.transition(skill_id, to)
        return rec

    async def register_published(self, skill_id: str, subsystem: Subsystem, action: str,
                                 version: int = 1) -> SkillRecord:
        """接入产出登记并驱动到「已发布」(§5:不自动到运行中)。幂等。"""
        if await self.store.get(skill_id) is not None:
            rec = await self.store.get(skill_id)
            # Delayed outbox retries may arrive after a newer publication.
            # Registration is idempotent and must never downgrade the index.
            rec.asset_version = max(rec.asset_version, version)
            await self.store.put(rec)
            return rec
        await self.register(skill_id, subsystem, action)
        await self.drive(skill_id, [SkillState.COPIED, SkillState.BOUND, SkillState.TESTING,
                                    SkillState.PENDING_RELEASE, SkillState.PUBLISHED])
        rec = await self.store.get(skill_id)
        rec.asset_version = version
        await self.store.put(rec)
        return rec

    # —— 流程10/11 对接的便捷操作 ——
    async def suspend(self, skill_id: str) -> SkillRecord:
        """异常暂停(流程10 达阈值 / 流程11 触发)。已发布或运行中可暂停。"""
        rec = await self.store.get(skill_id)
        if rec and rec.state in (SkillState.RUNNING, SkillState.PUBLISHED):
            return await self.transition(skill_id, SkillState.SUSPENDED)
        return rec  # 已非可暂停态则幂等

    async def recover_to_published(self, skill_id: str, new_version: int) -> SkillRecord:
        """自愈/补丁后恢复到「已发布」(异常暂停→已发布),记新版本(旧版保留可回滚)。"""
        rec = await self.store.get(skill_id)
        if rec and rec.state == SkillState.SUSPENDED:
            await self.transition(skill_id, SkillState.PUBLISHED)
        rec = await self.store.get(skill_id)
        rec.asset_version = new_version
        await self.store.put(rec)
        return rec

    async def resume_no_change(self, skill_id: str) -> SkillRecord:
        """人工确认无需改资产 → 直接恢复运行。"""
        return await self.transition(skill_id, SkillState.RUNNING)

    async def recover_via_patch(self, skill_id: str, new_version: int) -> SkillRecord:
        """流程11 生成补丁后恢复:异常暂停→绑定资产→测试中→待发布→已发布→运行中。"""
        await self.drive(skill_id, [
            SkillState.BOUND, SkillState.TESTING, SkillState.PENDING_RELEASE,
            SkillState.PUBLISHED, SkillState.RUNNING,
        ])
        rec = await self.store.get(skill_id)
        rec.asset_version = new_version
        await self.store.put(rec)
        return rec
