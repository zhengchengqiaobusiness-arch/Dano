"""资产存储接口(Protocol)。

pi_agent 的入库段(ingest)依赖这个接口而非具体 AssetRepository,
以便:① 测试注入内存 fake,不拖入 asyncpg/PostgreSQL;② 未来替换存储实现。
AssetRepository(repository.py)天然满足本协议。
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from dano.shared.enums import AssetType, ValidationStatus
from dano.shared.models import AssetEnvelope, Scope


class AssetStore(Protocol):
    async def create(self, env: AssetEnvelope) -> AssetEnvelope: ...

    async def get(self, asset_id: UUID) -> AssetEnvelope | None: ...

    async def list_versions(
        self, asset_type: AssetType, scope: Scope, asset_key: str = "default"
    ) -> list[AssetEnvelope]: ...

    async def get_published(
        self, asset_type: AssetType, scope: Scope, asset_key: str = "default"
    ) -> AssetEnvelope | None: ...

    async def list_published(
        self, asset_type: AssetType, scope: Scope
    ) -> list[AssetEnvelope]: ...

    async def set_status(
        self, asset_id: UUID, status: ValidationStatus
    ) -> AssetEnvelope | None: ...
