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

    async def get_tenant_by_username(self, username: str) -> TenantRecord | None:
        return next((t for t in self._tenants.values() if t.username == username), None)

    async def update_tenant_password(self, tenant: str, password_hash: str) -> None:
        rec = self._tenants.get(tenant)
        if rec is not None:
            self._tenants[tenant] = rec.model_copy(update={"password_hash": password_hash})

    # ── 两步验证(TOTP)──
    async def set_totp_pending(self, tenant: str, secret: str) -> None:
        self._update(tenant, {"totp_pending": secret})

    async def activate_totp(self, tenant: str, secret: str, backup_hashes: list[str]) -> None:
        self._update(tenant, {"totp_secret": secret, "totp_pending": "",
                              "backup_codes": list(backup_hashes)})

    async def disable_totp(self, tenant: str) -> None:
        self._update(tenant, {"totp_secret": "", "totp_pending": "", "backup_codes": []})

    async def set_backup_codes(self, tenant: str, backup_hashes: list[str]) -> None:
        self._update(tenant, {"backup_codes": list(backup_hashes)})

    def _update(self, tenant: str, changes: dict) -> None:
        rec = self._tenants.get(tenant)
        if rec is not None:
            self._tenants[tenant] = rec.model_copy(update=changes)


class PgRegistry:
    """PostgreSQL 持久化登记。无状态,依赖全局连接池。"""

    async def create_tenant(self, rec: TenantRecord) -> TenantRecord:
        from dano.infra.db import get_pool

        async with get_pool().acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO tenants (tenant, display_name, deploy, worker_location, log_policy, username, password_hash, api_key)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                ON CONFLICT (tenant) DO UPDATE SET
                    display_name=EXCLUDED.display_name, deploy=EXCLUDED.deploy,
                    worker_location=EXCLUDED.worker_location, log_policy=EXCLUDED.log_policy,
                    username=EXCLUDED.username
                RETURNING *
                """,  # ON CONFLICT 不覆盖 api_key/password_hash:保留既有;RETURNING 拿持久化后的真实行
                rec.tenant, rec.display_name, rec.deploy, rec.worker_location,
                rec.log_policy, rec.username, rec.password_hash, rec.api_key,
            )
        log.info("registry.tenant_created", tenant=rec.tenant)
        return TenantRecord(**dict(row))   # 幂等:返回持久化的记录(已存在则带其原 api_key)

    async def get_tenant_by_key(self, api_key: str) -> TenantRecord | None:
        from dano.infra.db import get_pool

        async with get_pool().acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM tenants WHERE api_key=$1", api_key)
        return TenantRecord(**dict(row)) if row else None

    async def get_tenant_by_username(self, username: str) -> TenantRecord | None:
        from dano.infra.db import get_pool

        async with get_pool().acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM tenants WHERE username=$1", username)
        return TenantRecord(**dict(row)) if row else None

    async def update_tenant_password(self, tenant: str, password_hash: str) -> None:
        from dano.infra.db import get_pool

        async with get_pool().acquire() as conn:
            await conn.execute(
                "UPDATE tenants SET password_hash=$2 WHERE tenant=$1", tenant, password_hash
            )

    # ── 两步验证(TOTP)。新列都有默认空值,create_tenant 的 INSERT 无需改动 ──
    async def set_totp_pending(self, tenant: str, secret: str) -> None:
        await self._execute("UPDATE tenants SET totp_pending=$2 WHERE tenant=$1", tenant, secret)

    async def activate_totp(self, tenant: str, secret: str, backup_hashes: list[str]) -> None:
        await self._execute(
            "UPDATE tenants SET totp_secret=$2, totp_pending='', backup_codes=$3 WHERE tenant=$1",
            tenant, secret, list(backup_hashes))

    async def disable_totp(self, tenant: str) -> None:
        await self._execute(
            "UPDATE tenants SET totp_secret='', totp_pending='', backup_codes='{}' "
            "WHERE tenant=$1", tenant)

    async def set_backup_codes(self, tenant: str, backup_hashes: list[str]) -> None:
        await self._execute("UPDATE tenants SET backup_codes=$2 WHERE tenant=$1",
                            tenant, list(backup_hashes))

    @staticmethod
    async def _execute(sql: str, *args) -> None:
        from dano.infra.db import get_pool

        async with get_pool().acquire() as conn:
            await conn.execute(sql, *args)
