"""资产库仓储:五类资产的版本化读写。

设计要点:
- append-only 版本化:升级 = 插入新版本,旧版本保留可回滚(流程11/12)。
- 运行期只消费 published 状态资产;未验证资产不得被 Worker 拿到。
- 按作用域(租户+系统实例)命中消费。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from uuid import UUID

import structlog

from dano.infra.db import get_pool

if TYPE_CHECKING:
    import asyncpg
from dano.shared.enums import AssetType, Subsystem, ValidationStatus
from dano.shared.models import AssetEnvelope, GenerationReport, Scope

log = structlog.get_logger(__name__)


def _row_to_envelope(row: asyncpg.Record) -> AssetEnvelope:
    return AssetEnvelope(
        asset_id=row["asset_id"],
        asset_type=AssetType(row["asset_type"]),
        scope=Scope(tenant=row["tenant"], subsystem=Subsystem(row["subsystem"])),
        asset_key=row["asset_key"],
        version=row["version"],
        source_fingerprint=row["source_fingerprint"],
        validation_status=ValidationStatus(row["validation_status"]),
        confidence=row["confidence"],
        human_confirmed=row["human_confirmed"],
        generation_report=GenerationReport.model_validate_json(row["generation_report"]),
        body=json.loads(row["body"]),
        created_at=row["created_at"],
    )


class AssetRepository:
    """资产库访问。无状态,依赖全局连接池。"""

    async def _next_version(
        self, conn: asyncpg.Connection, asset_type: AssetType, scope: Scope, asset_key: str
    ) -> int:
        row = await conn.fetchrow(
            """
            SELECT COALESCE(MAX(version), 0) + 1 AS v
            FROM assets
            WHERE asset_type = $1 AND tenant = $2 AND subsystem = $3 AND asset_key = $4
            """,
            asset_type.value,
            scope.tenant,
            scope.subsystem.value,
            asset_key,
        )
        return row["v"]

    async def create(self, env: AssetEnvelope) -> AssetEnvelope:
        """插入一个新资产版本。version 自动取该(作用域+asset_key)下的下一个号。"""
        pool = get_pool()
        async with pool.acquire() as conn:
            version = await self._next_version(conn, env.asset_type, env.scope, env.asset_key)
            row = await conn.fetchrow(
                """
                INSERT INTO assets
                    (asset_type, tenant, subsystem, asset_key, version, source_fingerprint,
                     validation_status, confidence, human_confirmed, generation_report, body)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                RETURNING *
                """,
                env.asset_type.value,
                env.scope.tenant,
                env.scope.subsystem.value,
                env.asset_key,
                version,
                env.source_fingerprint,
                env.validation_status.value,
                env.confidence,
                env.human_confirmed,
                env.generation_report.model_dump_json(),
                json.dumps(env.body, ensure_ascii=False),
            )
        log.info(
            "asset.created",
            asset_type=env.asset_type.value,
            subsystem=env.scope.subsystem.value,
            version=version,
        )
        return _row_to_envelope(row)

    async def get(self, asset_id: UUID) -> AssetEnvelope | None:
        pool = get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM assets WHERE asset_id = $1", asset_id)
        return _row_to_envelope(row) if row else None

    async def list_versions(
        self, asset_type: AssetType, scope: Scope, asset_key: str = "default"
    ) -> list[AssetEnvelope]:
        """列出某逻辑资产的全部版本(新→旧),用于回滚选择。"""
        pool = get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM assets
                WHERE asset_type = $1 AND tenant = $2 AND subsystem = $3 AND asset_key = $4
                ORDER BY version DESC
                """,
                asset_type.value,
                scope.tenant,
                scope.subsystem.value,
                asset_key,
            )
        return [_row_to_envelope(r) for r in rows]

    async def get_published(
        self, asset_type: AssetType, scope: Scope, asset_key: str = "default"
    ) -> AssetEnvelope | None:
        """运行期命中消费入口:取该逻辑资产最新的 published 版本。"""
        pool = get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM assets
                WHERE asset_type = $1 AND tenant = $2 AND subsystem = $3 AND asset_key = $4
                  AND validation_status = 'published'
                ORDER BY version DESC
                LIMIT 1
                """,
                asset_type.value,
                scope.tenant,
                scope.subsystem.value,
                asset_key,
            )
        return _row_to_envelope(row) if row else None

    async def list_published(
        self, asset_type: AssetType, scope: Scope
    ) -> list[AssetEnvelope]:
        """枚举该作用域下某类资产的全部已发布逻辑资产(每个 asset_key 取最新版)。

        供 Skill 注册:列出一个子系统下全部已发布连接器(每动作一份)。
        """
        pool = get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT ON (asset_key) *
                FROM assets
                WHERE asset_type = $1 AND tenant = $2 AND subsystem = $3
                  AND validation_status = 'published'
                ORDER BY asset_key, version DESC
                """,
                asset_type.value,
                scope.tenant,
                scope.subsystem.value,
            )
        return [_row_to_envelope(r) for r in rows]

    async def set_status(
        self, asset_id: UUID, status: ValidationStatus
    ) -> AssetEnvelope | None:
        """流转验证状态(流程12 生命周期闸门)。"""
        pool = get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "UPDATE assets SET validation_status = $2 WHERE asset_id = $1 RETURNING *",
                asset_id,
                status.value,
            )
        if row:
            log.info("asset.status_changed", asset_id=str(asset_id), status=status.value)
        return _row_to_envelope(row) if row else None
