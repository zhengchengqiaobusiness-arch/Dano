"""Phase 1 验收:资产底座端口 + schema 校验 + 草案/验证/发布硬关卡。

需 PostgreSQL(DANO_PG_DSN,默认指 dano_back);连不上则整体跳过。
"""

from __future__ import annotations

import os

import pytest

from dano.shared.enums import AssetType, Subsystem, ValidationStatus
from dano.shared.models import AssetEnvelope, Scope

_DSN = os.environ.get("DANO_PG_DSN", "postgresql://postgres:111111@localhost:5432/dano_back")
_BODY = {"endpoint": "/oa/leave", "method": "POST", "auth_kind": "token",
         "auth_ref": "vault://ph1/oa", "action": "create_leave"}


_MIGRATED = False


@pytest.fixture(autouse=True)
async def _pg():
    """函数作用域:每个测试用例在自身事件循环里建池,避免 asyncpg 跨循环失效。"""
    global _MIGRATED
    os.environ["DANO_PG_DSN"] = _DSN
    from dano.config import get_settings
    get_settings.cache_clear()
    from dano.infra.db import close_pool, init_pool, run_migrations
    try:
        await init_pool()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"PostgreSQL 不可用,跳过 Phase 1 测试: {e}")
    if not _MIGRATED:
        await run_migrations()
        _MIGRATED = True
    yield
    await close_pool()


def _scope(t="ph1-test"):
    return Scope(tenant=t, subsystem=Subsystem.OA)


async def test_asset_repository_port():
    from dano.assets.repository import AssetRepository
    repo = AssetRepository()
    env = await repo.create(AssetEnvelope(
        asset_type=AssetType.CONNECTOR, scope=_scope(), asset_key="create_leave", version=0,
        source_fingerprint="s", validation_status=ValidationStatus.VERIFIED, confidence=0.9, body=_BODY))
    await repo.set_status(env.asset_id, ValidationStatus.PUBLISHED)
    got = await repo.get_published(AssetType.CONNECTOR, _scope(), asset_key="create_leave")
    assert got is not None and got.body["action"] == "create_leave"


def test_schema_validation_accepts_and_rejects():
    from dano.schemas import SchemaError, validate_asset_body
    validate_asset_body(AssetType.CONNECTOR, _BODY)            # 合法过
    with pytest.raises(SchemaError):
        validate_asset_body(AssetType.CONNECTOR, {"foo": "bar"})  # 垃圾拒


async def test_publish_gate_passes_with_required_evidence():
    from dano.assets.drafts import DraftStore
    ds = DraftStore()
    d = await ds.save_draft(run_id="r1", scope=_scope("gate1"), asset_type=AssetType.CONNECTOR,
                            asset_key="create_leave", body=_BODY)
    v1 = await ds.record_validation(asset_draft_id=d.asset_draft_id, kind="connect", passed=True)
    v2 = await ds.record_validation(asset_draft_id=d.asset_draft_id, kind="sandbox", passed=True)
    ok, reason = await ds.verify_publishable(d.asset_draft_id, [v1.validation_run_id, v2.validation_run_id])
    assert ok, reason


async def test_publish_gate_rejects_missing_kind():
    from dano.assets.drafts import DraftStore
    ds = DraftStore()
    d = await ds.save_draft(run_id="r2", scope=_scope("gate2"), asset_type=AssetType.CONNECTOR,
                            asset_key="create_leave", body=_BODY)
    v1 = await ds.record_validation(asset_draft_id=d.asset_draft_id, kind="connect", passed=True)
    ok, reason = await ds.verify_publishable(d.asset_draft_id, [v1.validation_run_id])
    assert not ok and "sandbox" in reason          # 缺 sandbox


async def test_publish_gate_rejects_failed_evidence():
    from dano.assets.drafts import DraftStore
    ds = DraftStore()
    d = await ds.save_draft(run_id="r3", scope=_scope("gate3"), asset_type=AssetType.CONNECTOR,
                            asset_key="create_leave", body=_BODY)
    v1 = await ds.record_validation(asset_draft_id=d.asset_draft_id, kind="connect", passed=True)
    v2 = await ds.record_validation(asset_draft_id=d.asset_draft_id, kind="sandbox", passed=False)
    ok, _ = await ds.verify_publishable(d.asset_draft_id, [v1.validation_run_id, v2.validation_run_id])
    assert not ok                                   # 有未通过证据


async def test_publish_gate_rejects_cross_draft_evidence():
    """跨草案证据(content_hash/草案不符)不能冒充——防换草案。"""
    from dano.assets.drafts import DraftStore
    ds = DraftStore()
    d1 = await ds.save_draft(run_id="r4", scope=_scope("gate4"), asset_type=AssetType.CONNECTOR,
                             asset_key="create_leave", body=_BODY)
    d2 = await ds.save_draft(run_id="r4", scope=_scope("gate4"), asset_type=AssetType.CONNECTOR,
                             asset_key="create_leave", body={**_BODY, "endpoint": "/oa/other"})
    v1 = await ds.record_validation(asset_draft_id=d1.asset_draft_id, kind="connect", passed=True)
    v_other = await ds.record_validation(asset_draft_id=d2.asset_draft_id, kind="sandbox", passed=True)
    ok, _ = await ds.verify_publishable(d1.asset_draft_id, [v1.validation_run_id, v_other.validation_run_id])
    assert not ok                                   # 跨草案证据被拒
