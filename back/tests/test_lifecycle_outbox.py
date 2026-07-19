from __future__ import annotations

import inspect
from pathlib import Path

from dano.lifecycle.outbox import (
    InMemoryLifecycleOutboxStore,
    LifecycleRegistrationReconciler,
)
from dano.lifecycle.state_machine import InMemorySkillStore, SkillLifecycle
from dano.shared.enums import Subsystem
from dano.gateway import app as gateway


REPO_ROOT = Path(__file__).resolve().parents[2]


class FailableLifecycle(SkillLifecycle):
    def __init__(self) -> None:
        super().__init__(InMemorySkillStore())
        self.fail = True
        self.calls = 0

    async def register_published(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        self.calls += 1
        if self.fail:
            raise RuntimeError("lifecycle store unavailable")
        return await super().register_published(*args, **kwargs)


async def test_registration_failure_is_reported_as_published_pending_then_compensated() -> None:
    lifecycle = FailableLifecycle()
    outbox = InMemoryLifecycleOutboxStore()
    reconciler = LifecycleRegistrationReconciler(lifecycle, outbox)

    result = await reconciler.register_or_defer(
        skill_id="oa.submit_hours",
        subsystem=Subsystem("oa"),
        action="submit_hours",
        asset_version=4,
    )

    assert result == {
        "lifecycle_pending": True,
        "lifecycle_message": "资产已发布，生命周期登记待补偿",
        "lifecycle_error": "lifecycle store unavailable",
        "asset_version": 4,
    }
    assert len(await outbox.pending()) == 1

    lifecycle.fail = False
    assert await reconciler.reconcile() == {"completed": 1, "pending": 0}
    assert await outbox.pending() == []
    record = await lifecycle.store.get("oa.submit_hours")
    assert record is not None and record.asset_version == 4


async def test_duplicate_registration_is_idempotent_and_never_downgrades_version() -> None:
    lifecycle = SkillLifecycle(InMemorySkillStore())
    reconciler = LifecycleRegistrationReconciler(lifecycle, InMemoryLifecycleOutboxStore())

    first = await reconciler.register_or_defer(
        skill_id="oa.submit_hours",
        subsystem=Subsystem("oa"),
        action="submit_hours",
        asset_version=5,
    )
    duplicate = await reconciler.register_or_defer(
        skill_id="oa.submit_hours",
        subsystem=Subsystem("oa"),
        action="submit_hours",
        asset_version=5,
    )
    delayed_old = await reconciler.register_or_defer(
        skill_id="oa.submit_hours",
        subsystem=Subsystem("oa"),
        action="submit_hours",
        asset_version=3,
    )

    assert not first["lifecycle_pending"]
    assert not duplicate["lifecycle_pending"]
    assert not delayed_old["lifecycle_pending"]
    record = await lifecycle.store.get("oa.submit_hours")
    assert record is not None and record.asset_version == 5


async def test_new_reconciler_recovers_pending_work_after_restart() -> None:
    durable_store = InMemoryLifecycleOutboxStore()
    unavailable = FailableLifecycle()
    before_restart = LifecycleRegistrationReconciler(unavailable, durable_store)
    await before_restart.register_or_defer(
        skill_id="crm.create_customer",
        subsystem=Subsystem("crm"),
        action="create_customer",
        asset_version=2,
    )

    restarted_lifecycle = SkillLifecycle(InMemorySkillStore())
    after_restart = LifecycleRegistrationReconciler(restarted_lifecycle, durable_store)

    assert await after_restart.reconcile() == {"completed": 1, "pending": 0}
    record = await restarted_lifecycle.store.get("crm.create_customer")
    assert record is not None and record.asset_version == 2


def test_pg_migration_defines_durable_idempotent_outbox() -> None:
    sql = (
        Path(__file__).parents[1] / "migrations" / "016_lifecycle_registration_outbox.sql"
    ).read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS lifecycle_registration_outbox" in sql
    assert "PRIMARY KEY (skill_id, asset_version)" in sql


def test_recording_publish_and_client_expose_lifecycle_pending_state() -> None:
    gateway_source = inspect.getsource(gateway.record_ws)
    frontend_source = (
        REPO_ROOT / "skillfrontend" / "src" / "components" / "PageRecorder.tsx"
    ).read_text(encoding="utf-8")

    assert "_lifecycle_reconciler.register_or_defer" in gateway_source
    assert "rep = {**rep, **lifecycle_result}" in gateway_source
    assert "result.lifecycle_pending" in frontend_source
    assert "资产已发布，生命周期登记待补偿" in frontend_source
