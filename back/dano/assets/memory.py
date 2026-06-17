"""内存资产库(满足 AssetStore 协议)。

用于:零依赖 demo / HTTP demo 入口 / 测试。不依赖 PostgreSQL。
生产用 AssetRepository(PostgreSQL)。
"""

from __future__ import annotations

from uuid import UUID, uuid4

from dano.shared.enums import AssetType, ValidationStatus
from dano.shared.models import AssetEnvelope, Scope


class InMemoryAssetStore:
    def __init__(self) -> None:
        self._rows: dict[UUID, AssetEnvelope] = {}
        self._versions: dict[tuple, int] = {}

    async def create(self, env: AssetEnvelope) -> AssetEnvelope:
        key = (env.asset_type, env.scope.tenant, env.scope.subsystem, env.asset_key)
        version = self._versions.get(key, 0) + 1
        self._versions[key] = version
        saved = env.model_copy(update={"asset_id": uuid4(), "version": version})
        self._rows[saved.asset_id] = saved
        return saved

    async def get(self, asset_id: UUID) -> AssetEnvelope | None:
        return self._rows.get(asset_id)

    def _scoped(self, asset_type: AssetType, scope: Scope) -> list[AssetEnvelope]:
        return [
            e for e in self._rows.values()
            if e.asset_type == asset_type
            and e.scope.tenant == scope.tenant
            and e.scope.subsystem == scope.subsystem
        ]

    async def list_versions(
        self, asset_type: AssetType, scope: Scope, asset_key: str = "default"
    ) -> list[AssetEnvelope]:
        rows = [e for e in self._scoped(asset_type, scope) if e.asset_key == asset_key]
        return sorted(rows, key=lambda e: e.version, reverse=True)

    async def get_published(
        self, asset_type: AssetType, scope: Scope, asset_key: str = "default"
    ) -> AssetEnvelope | None:
        pub = [
            e for e in await self.list_versions(asset_type, scope, asset_key)
            if e.validation_status == ValidationStatus.PUBLISHED
        ]
        return pub[0] if pub else None

    async def list_published(
        self, asset_type: AssetType, scope: Scope
    ) -> list[AssetEnvelope]:
        latest: dict[str, AssetEnvelope] = {}
        for e in self._scoped(asset_type, scope):
            if e.validation_status != ValidationStatus.PUBLISHED:
                continue
            cur = latest.get(e.asset_key)
            if cur is None or e.version > cur.version:
                latest[e.asset_key] = e
        return list(latest.values())

    async def set_status(
        self, asset_id: UUID, status: ValidationStatus
    ) -> AssetEnvelope | None:
        env = self._rows.get(asset_id)
        if env:
            env = env.model_copy(update={"validation_status": status})
            self._rows[asset_id] = env
        return env
