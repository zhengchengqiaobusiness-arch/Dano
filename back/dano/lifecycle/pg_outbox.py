"""PostgreSQL lifecycle-registration outbox."""

from __future__ import annotations

from dano.infra.db import get_pool
from dano.lifecycle.outbox import LifecycleRegistration
from dano.shared.enums import Subsystem


def _from_row(row) -> LifecycleRegistration:  # noqa: ANN001
    return LifecycleRegistration(
        skill_id=row["skill_id"],
        subsystem=Subsystem(row["subsystem"]),
        action=row["action"],
        asset_version=row["asset_version"],
        last_error=row["last_error"] or "",
        attempts=row["attempts"],
    )


class PgLifecycleOutboxStore:
    async def enqueue(self, item: LifecycleRegistration) -> None:
        async with get_pool().acquire() as connection:
            await connection.execute(
                """
                INSERT INTO lifecycle_registration_outbox
                    (skill_id, subsystem, action, asset_version, last_error, attempts, updated_at)
                VALUES ($1, $2, $3, $4, '', 0, now())
                ON CONFLICT (skill_id, asset_version) DO UPDATE SET
                    subsystem = EXCLUDED.subsystem,
                    action = EXCLUDED.action,
                    updated_at = now()
                """,
                item.skill_id,
                item.subsystem.value,
                item.action,
                item.asset_version,
            )

    async def pending(self) -> list[LifecycleRegistration]:
        async with get_pool().acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT skill_id, subsystem, action, asset_version, last_error, attempts
                FROM lifecycle_registration_outbox
                ORDER BY updated_at, skill_id, asset_version
                """
            )
        return [_from_row(row) for row in rows]

    async def mark_failed(self, item: LifecycleRegistration, error: str) -> None:
        async with get_pool().acquire() as connection:
            await connection.execute(
                """
                UPDATE lifecycle_registration_outbox
                SET last_error = $3, attempts = attempts + 1, updated_at = now()
                WHERE skill_id = $1 AND asset_version = $2
                """,
                item.skill_id,
                item.asset_version,
                str(error)[:2000],
            )

    async def mark_completed(self, item: LifecycleRegistration) -> None:
        async with get_pool().acquire() as connection:
            await connection.execute(
                """
                DELETE FROM lifecycle_registration_outbox
                WHERE skill_id = $1 AND asset_version = $2
                """,
                item.skill_id,
                item.asset_version,
            )

