"""租户/系统实例存储:PG 持久化 + 内存实现。"""

from __future__ import annotations

import structlog

from dano.registry.models import TenantRecord

log = structlog.get_logger(__name__)


class InMemoryRegistry:
    def __init__(self) -> None:
        self._tenants: dict[str, TenantRecord] = {}

    async def create_tenant(self, rec: TenantRecord) -> TenantRecord:
        existing = self._tenants.get(rec.tenant)
        if existing is not None:            # 幂等:已存在则返回既有(保留其 api_key)
            return existing
        self._tenants[rec.tenant] = rec
        return rec

    async def get_tenant_by_key(self, api_key: str) -> TenantRecord | None:
        return next((t for t in self._tenants.values() if t.api_key == api_key), None)


class PgRegistry:
    """PostgreSQL 持久化登记。无状态,依赖全局连接池。"""

    async def create_tenant(self, rec: TenantRecord) -> TenantRecord:
        from dano.infra.db import get_pool

        async with get_pool().acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO tenants (tenant, display_name, deploy, worker_location, log_policy, api_key)
                VALUES ($1,$2,$3,$4,$5,$6)
                ON CONFLICT (tenant) DO UPDATE SET
                    display_name=EXCLUDED.display_name, deploy=EXCLUDED.deploy,
                    worker_location=EXCLUDED.worker_location, log_policy=EXCLUDED.log_policy
                RETURNING *
                """,  # ON CONFLICT 不覆盖 api_key:保留既有;RETURNING 拿持久化后的真实行
                rec.tenant, rec.display_name, rec.deploy, rec.worker_location,
                rec.log_policy, rec.api_key,
            )
        log.info("registry.tenant_created", tenant=rec.tenant)
        return TenantRecord(**dict(row))   # 幂等:返回持久化的记录(已存在则带其原 api_key)

    async def get_tenant_by_key(self, api_key: str) -> TenantRecord | None:
        from dano.infra.db import get_pool

        async with get_pool().acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM tenants WHERE api_key=$1", api_key)
        return TenantRecord(**dict(row)) if row else None
