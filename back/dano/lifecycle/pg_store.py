"""SkillStore 的 PostgreSQL 实现(流程12 生命周期持久化)。

接入产出登记、保障期暂停/恢复/回滚都落本表,跨进程重启留存,与资产库同源。
满足 state_machine.SkillStore 协议(get/put/all);asyncpg 惰性导入。
"""

from __future__ import annotations

import json

import structlog

from dano.infra.db import get_pool
from dano.lifecycle.state_machine import SkillRecord
from dano.shared.enums import SkillState, Subsystem

log = structlog.get_logger(__name__)


def _row_to_record(row) -> SkillRecord:  # noqa: ANN001
    return SkillRecord(
        skill_id=row["skill_id"],
        subsystem=Subsystem(row["subsystem"]),
        action=row["action"],
        state=SkillState(row["state"]),
        asset_version=row["asset_version"],
        history=json.loads(row["history"]),
    )


class PgSkillStore:
    """生命周期状态机的 PostgreSQL 持久化存储。无状态,依赖全局连接池。"""

    async def get(self, skill_id: str) -> SkillRecord | None:
        pool = get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM skill_lifecycle WHERE skill_id = $1", skill_id
            )
        return _row_to_record(row) if row else None

    async def put(self, record: SkillRecord) -> None:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO skill_lifecycle
                    (skill_id, subsystem, action, state, asset_version, history, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, now())
                ON CONFLICT (skill_id) DO UPDATE SET
                    state = EXCLUDED.state,
                    asset_version = EXCLUDED.asset_version,
                    history = EXCLUDED.history,
                    updated_at = now()
                """,
                record.skill_id,
                record.subsystem.value,
                record.action,
                record.state.value,
                record.asset_version,
                json.dumps(record.history, ensure_ascii=False),
            )

    async def delete(self, skill_id: str) -> int:
        pool = get_pool()
        async with pool.acquire() as conn:
            res = await conn.execute("DELETE FROM skill_lifecycle WHERE skill_id = $1", skill_id)
        rows = int(res.split()[-1]) if res and res.split()[-1].isdigit() else 0
        log.info("lifecycle.deleted", skill_id=skill_id, rows=rows)
        return rows

    async def all(self) -> list[SkillRecord]:
        pool = get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM skill_lifecycle ORDER BY skill_id")
        return [_row_to_record(r) for r in rows]
