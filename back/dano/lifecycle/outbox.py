"""Durable compensation for lifecycle registration after asset publication.

AssetRepository remains the publication source of truth. Lifecycle state is a
derived index: failure to update it must be visible and retryable, but must not
turn a successfully published asset into a failed publication.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol

import structlog

from dano.lifecycle.state_machine import SkillLifecycle
from dano.shared.enums import Subsystem

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class LifecycleRegistration:
    skill_id: str
    subsystem: Subsystem
    action: str
    asset_version: int
    last_error: str = ""
    attempts: int = 0


class LifecycleOutboxStore(Protocol):
    async def enqueue(self, item: LifecycleRegistration) -> None: ...
    async def pending(self) -> list[LifecycleRegistration]: ...
    async def mark_failed(self, item: LifecycleRegistration, error: str) -> None: ...
    async def mark_completed(self, item: LifecycleRegistration) -> None: ...


class InMemoryLifecycleOutboxStore:
    def __init__(self) -> None:
        self._pending: dict[tuple[str, int], LifecycleRegistration] = {}

    async def enqueue(self, item: LifecycleRegistration) -> None:
        self._pending.setdefault((item.skill_id, item.asset_version), item)

    async def pending(self) -> list[LifecycleRegistration]:
        return sorted(self._pending.values(), key=lambda item: (item.skill_id, item.asset_version))

    async def mark_failed(self, item: LifecycleRegistration, error: str) -> None:
        self._pending[(item.skill_id, item.asset_version)] = replace(
            item,
            last_error=str(error)[:2000],
            attempts=item.attempts + 1,
        )

    async def mark_completed(self, item: LifecycleRegistration) -> None:
        self._pending.pop((item.skill_id, item.asset_version), None)


class LifecycleRegistrationReconciler:
    def __init__(self, lifecycle: SkillLifecycle, store: LifecycleOutboxStore) -> None:
        self.lifecycle = lifecycle
        self.store = store

    async def register_or_defer(
        self,
        *,
        skill_id: str,
        subsystem: Subsystem,
        action: str,
        asset_version: int,
    ) -> dict:
        item = LifecycleRegistration(
            skill_id=skill_id,
            subsystem=subsystem,
            action=action,
            asset_version=asset_version,
        )
        # Enqueue first. If the process exits after this write, startup
        # reconciliation still completes the already-published asset.
        try:
            await self.store.enqueue(item)
        except Exception as error:  # noqa: BLE001
            log.exception("lifecycle.outbox_enqueue_failed", skill_id=skill_id, error=str(error))
            try:
                await self.lifecycle.register_published(skill_id, subsystem, action, asset_version)
            except Exception as lifecycle_error:  # noqa: BLE001
                return self._pending_result(item, f"outbox={error}; lifecycle={lifecycle_error}")
            return self._completed_result(item)

        try:
            await self.lifecycle.register_published(skill_id, subsystem, action, asset_version)
        except Exception as error:  # noqa: BLE001
            persistence_error = ""
            try:
                await self.store.mark_failed(item, str(error))
            except Exception as store_error:  # noqa: BLE001
                persistence_error = f"; outbox_update={store_error}"
                log.exception(
                    "lifecycle.outbox_failure_update_failed",
                    skill_id=skill_id,
                    error=str(store_error),
                )
            log.warning(
                "lifecycle.registration_deferred",
                skill_id=skill_id,
                asset_version=asset_version,
                error=str(error),
            )
            return self._pending_result(item, f"{error}{persistence_error}")

        try:
            await self.store.mark_completed(item)
        except Exception as error:  # noqa: BLE001
            # A duplicate retry is harmless because register_published is
            # idempotent; never turn a completed lifecycle write into a failed
            # asset publication merely because outbox cleanup was unavailable.
            log.exception("lifecycle.outbox_complete_failed", skill_id=skill_id, error=str(error))
        return self._completed_result(item)

    async def reconcile(self) -> dict:
        completed = 0
        failed = 0
        try:
            pending = await self.store.pending()
        except Exception as error:  # noqa: BLE001
            log.exception("lifecycle.outbox_read_failed", error=str(error))
            return {"completed": 0, "pending": 0, "error": str(error)}
        for item in pending:
            try:
                await self.lifecycle.register_published(
                    item.skill_id,
                    item.subsystem,
                    item.action,
                    item.asset_version,
                )
            except Exception as error:  # noqa: BLE001
                failed += 1
                try:
                    await self.store.mark_failed(item, str(error))
                except Exception as store_error:  # noqa: BLE001
                    log.exception(
                        "lifecycle.outbox_failure_update_failed",
                        skill_id=item.skill_id,
                        error=str(store_error),
                    )
                log.warning(
                    "lifecycle.reconcile_failed",
                    skill_id=item.skill_id,
                    asset_version=item.asset_version,
                    error=str(error),
                )
            else:
                try:
                    await self.store.mark_completed(item)
                except Exception as error:  # noqa: BLE001
                    failed += 1
                    log.exception(
                        "lifecycle.outbox_complete_failed",
                        skill_id=item.skill_id,
                        error=str(error),
                    )
                else:
                    completed += 1
        return {"completed": completed, "pending": failed}

    @staticmethod
    def _pending_result(item: LifecycleRegistration, error: str) -> dict:
        return {
            "lifecycle_pending": True,
            "lifecycle_message": "资产已发布，生命周期登记待补偿",
            "lifecycle_error": str(error),
            "asset_version": item.asset_version,
        }

    @staticmethod
    def _completed_result(item: LifecycleRegistration) -> dict:
        return {
            "lifecycle_pending": False,
            "lifecycle_message": "生命周期登记完成",
            "asset_version": item.asset_version,
        }
