"""租户/系统实例存储:PG 持久化 + 内存实现(满足 RegistryStore 协议)。"""

from __future__ import annotations

from typing import Protocol

import structlog

from dano.registry.models import SystemInstance, TenantRecord
from dano.shared.enums import Subsystem

log = structlog.get_logger(__name__)


class RegistryStore(Protocol):
    async def create_tenant(self, rec: TenantRecord) -> TenantRecord: ...
    async def get_tenant(self, tenant: str) -> TenantRecord | None: ...
    async def get_tenant_by_key(self, api_key: str) -> TenantRecord | None: ...
    async def list_tenants(self) -> list[TenantRecord]: ...
    async def create_instance(self, inst: SystemInstance) -> SystemInstance: ...
    async def list_instances(self, tenant: str) -> list[SystemInstance]: ...
    async def get_instance(self, tenant: str, subsystem: Subsystem) -> SystemInstance | None: ...
    async def mark_onboarded(self, tenant: str, subsystem: Subsystem) -> None: ...


class InMemoryRegistry:
    def __init__(self) -> None:
        self._tenants: dict[str, TenantRecord] = {}
        self._instances: dict[tuple[str, str], SystemInstance] = {}

    async def create_tenant(self, rec: TenantRecord) -> TenantRecord:
        self._tenants[rec.tenant] = rec
        return rec

    async def get_tenant(self, tenant: str) -> TenantRecord | None:
        return self._tenants.get(tenant)

    async def get_tenant_by_key(self, api_key: str) -> TenantRecord | None:
        return next((t for t in self._tenants.values() if t.api_key == api_key), None)

    async def list_tenants(self) -> list[TenantRecord]:
        return list(self._tenants.values())

    async def create_instance(self, inst: SystemInstance) -> SystemInstance:
        self._instances[(inst.tenant, inst.subsystem.value)] = inst
        return inst

    async def list_instances(self, tenant: str) -> list[SystemInstance]:
        return [i for i in self._instances.values() if i.tenant == tenant]

    async def get_instance(self, tenant: str, subsystem: Subsystem) -> SystemInstance | None:
        return self._instances.get((tenant, subsystem.value))

    async def mark_onboarded(self, tenant: str, subsystem: Subsystem) -> None:
        inst = self._instances.get((tenant, subsystem.value))
        if inst:
            inst.status = "onboarded"


class PgRegistry:
    """PostgreSQL 持久化登记。无状态,依赖全局连接池。"""

    async def create_tenant(self, rec: TenantRecord) -> TenantRecord:
        from dano.infra.db import get_pool

        async with get_pool().acquire() as conn:
            await conn.execute(
                """
                INSERT INTO tenants (tenant, display_name, deploy, worker_location, log_policy, api_key)
                VALUES ($1,$2,$3,$4,$5,$6)
                ON CONFLICT (tenant) DO UPDATE SET
                    display_name=EXCLUDED.display_name, deploy=EXCLUDED.deploy,
                    worker_location=EXCLUDED.worker_location, log_policy=EXCLUDED.log_policy
                """,  # ON CONFLICT 不覆盖 api_key:保留公司既有唯一标识
                rec.tenant, rec.display_name, rec.deploy, rec.worker_location,
                rec.log_policy, rec.api_key,
            )
        log.info("registry.tenant_created", tenant=rec.tenant)
        return rec

    async def get_tenant(self, tenant: str) -> TenantRecord | None:
        from dano.infra.db import get_pool

        async with get_pool().acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM tenants WHERE tenant=$1", tenant)
        return TenantRecord(**dict(row)) if row else None

    async def get_tenant_by_key(self, api_key: str) -> TenantRecord | None:
        from dano.infra.db import get_pool

        async with get_pool().acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM tenants WHERE api_key=$1", api_key)
        return TenantRecord(**dict(row)) if row else None

    async def list_tenants(self) -> list[TenantRecord]:
        from dano.infra.db import get_pool

        async with get_pool().acquire() as conn:
            rows = await conn.fetch("SELECT * FROM tenants ORDER BY tenant")
        return [TenantRecord(**dict(r)) for r in rows]

    async def create_instance(self, inst: SystemInstance) -> SystemInstance:
        from dano.infra.db import get_pool

        async with get_pool().acquire() as conn:
            await conn.execute(
                """
                INSERT INTO system_instances (tenant, subsystem, type_template, integration, status)
                VALUES ($1,$2,$3,$4,$5)
                ON CONFLICT (tenant, subsystem) DO UPDATE SET
                    type_template=EXCLUDED.type_template, integration=EXCLUDED.integration
                """,
                inst.tenant, inst.subsystem.value, inst.type_template, inst.integration, inst.status,
            )
        log.info("registry.instance_created", tenant=inst.tenant, subsystem=inst.subsystem.value)
        return inst

    async def list_instances(self, tenant: str) -> list[SystemInstance]:
        from dano.infra.db import get_pool

        async with get_pool().acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM system_instances WHERE tenant=$1 ORDER BY subsystem", tenant
            )
        return [self._row(r) for r in rows]

    async def get_instance(self, tenant: str, subsystem: Subsystem) -> SystemInstance | None:
        from dano.infra.db import get_pool

        async with get_pool().acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM system_instances WHERE tenant=$1 AND subsystem=$2",
                tenant, subsystem.value,
            )
        return self._row(row) if row else None

    async def mark_onboarded(self, tenant: str, subsystem: Subsystem) -> None:
        from dano.infra.db import get_pool

        async with get_pool().acquire() as conn:
            await conn.execute(
                "UPDATE system_instances SET status='onboarded' WHERE tenant=$1 AND subsystem=$2",
                tenant, subsystem.value,
            )

    @staticmethod
    def _row(row) -> SystemInstance:  # noqa: ANN001
        return SystemInstance(
            tenant=row["tenant"], subsystem=Subsystem(row["subsystem"]),
            type_template=row["type_template"], integration=row["integration"], status=row["status"],
        )
