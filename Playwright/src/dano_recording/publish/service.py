"""Freeze → compile → deterministic check → Pi reviews → Dano asset transaction."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
from typing import Any, Awaitable, Callable, Protocol
from uuid import uuid4

from .asset_projection import project_asset
from .review import ReviewCollector
from dano_recording.executability import check_executability
from dano_recording.review_advice import reviewer_advisories

SnapshotProvider = Callable[[str, int], Awaitable[dict[str, Any]]]
ReviewRunner = Callable[[str, int], Awaitable[list[dict[str, Any]]]]


class AssetWriter(Protocol):
    async def publish(self, **kwargs) -> dict[str, Any]: ...  # noqa: ANN003


class RecordingPublishService:
    def __init__(
        self,
        *,
        snapshot_provider: SnapshotProvider,
        review_runner: ReviewRunner,
        review_collector: ReviewCollector,
        asset_writer: AssetWriter,
    ) -> None:
        self.snapshot_provider = snapshot_provider
        self.review_runner = review_runner
        self.review_collector = review_collector
        self.asset_writer = asset_writer
        self._locks: defaultdict[tuple[str, int], asyncio.Lock] = defaultdict(asyncio.Lock)

    async def publish(self, recording_id: str, revision: int) -> dict[str, Any]:
        async with self._locks[(recording_id, revision)]:
            return await self._publish_locked(recording_id, revision)

    async def _publish_locked(self, recording_id: str, revision: int) -> dict[str, Any]:
        snapshot = deepcopy(await self.snapshot_provider(recording_id, revision))
        actual_revision = int(snapshot.get("revision") or 0)
        if actual_revision != revision:
            return {
                "published": False,
                "stage": "revision_conflict",
                "reason": f"requested revision {revision}, current revision {actual_revision}",
            }
        snapshot_hash = _snapshot_hash(snapshot)
        candidate = project_asset(snapshot, revision=revision)
        validation = _deterministic_validation(snapshot, candidate.body)
        validation["content_hash"] = candidate.content_hash
        validation["snapshot_hash"] = snapshot_hash
        candidate = candidate.model_copy(update={"validation": validation})
        if not validation["passed"]:
            return {"published": False, "stage": "validation", "validation": validation}
        self.review_collector.begin(
            recording_id, revision, candidate.content_hash, snapshot_hash,
        )
        review_failure = "reviewer completed without a submission"
        try:
            await self.review_runner(recording_id, revision)
        except Exception as exc:  # noqa: BLE001
            review_failure = str(exc) or type(exc).__name__
        self.review_collector.submit_unavailable(
            recording_id=recording_id,
            revision=revision,
            reason=review_failure,
        )
        try:
            reviews = self.review_collector.collect(
                recording_id, revision, candidate.content_hash, snapshot_hash,
            )
        except Exception:
            # Hash/session/revision mismatches are publication-integrity errors,
            # unlike model availability, and must remain hard failures.
            self.review_collector.abort(recording_id, revision)
            raise
        # Pi semantic findings are advice, never a hidden executability gate.
        # Three isolated review submissions are still mandatory and immutable.
        review_advice = reviewer_advisories(reviews, revision=revision)
        validation["review_advisories"] = review_advice
        # Re-read the exact revision after review.  Repositories are append-only,
        # but this check also protects custom providers from mutable snapshots.
        checked_snapshot = deepcopy(await self.snapshot_provider(recording_id, revision))
        checked_snapshot_hash = _snapshot_hash(checked_snapshot)
        checked_candidate = project_asset(checked_snapshot, revision=revision)
        if (
            checked_snapshot_hash != snapshot_hash
            or checked_candidate.content_hash != candidate.content_hash
        ):
            return {
                "published": False,
                "stage": "freeze",
                "reason": "frozen revision changed during review",
            }
        result = await self.asset_writer.publish(
            run_id=f"recording-v3-{uuid4().hex}",
            tenant=candidate.tenant,
            subsystem=candidate.subsystem,
            action=candidate.action,
            body=candidate.body,
            validation=validation,
            reviews=reviews,
        )
        return {
            **result,
            "recording_id": recording_id,
            "revision": revision,
            "content_hash": candidate.content_hash,
            "snapshot_hash": snapshot_hash,
            "action": candidate.action,
            "skill_id": f"{candidate.subsystem}.{candidate.action}",
            "verification_status": candidate.body.get("verification_status"),
            "publication_status": candidate.body.get("publication_status"),
            "contract_faults": validation.get("contract_faults") or [],
            "contract_fault_count": len(validation.get("contract_faults") or []),
            "review_advisories": review_advice,
        }


def _snapshot_hash(snapshot: dict[str, Any]) -> str:
    canonical = json.dumps(
        snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    )
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def _deterministic_validation(snapshot: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    integrity_issues: list[str] = []
    api = body.get("api_request") or {}
    steps = list(api.get("steps") or [])
    raw_facts: Any = snapshot.get("request_facts")
    if raw_facts is None:
        nested_facts = snapshot.get("facts") or {}
        raw_facts = nested_facts.get("requests") if isinstance(nested_facts, dict) else []
    if isinstance(raw_facts, dict):
        raw_facts = raw_facts.get("requests") or []
    facts = [dict(item) for item in raw_facts or [] if isinstance(item, dict)]
    materialized = [
        item for item in facts
        if (item.get("disposition") or item.get("role")) == "materialized"
    ]
    if body.get("recording_engine") != "playwright_v3" or api.get("recording_engine") != "playwright_v3":
        integrity_issues.append("asset and api_request require matching playwright_v3 markers")
    revision = int(snapshot.get("revision") or 0)
    if int(api.get("revision") or -1) != revision:
        integrity_issues.append("api_request revision does not match the frozen snapshot")
    executability = check_executability(snapshot, body)
    expected_status = executability["executability_status"]
    if body.get("verification_status") != expected_status or api.get("verification_status") != expected_status:
        integrity_issues.append("asset verification marker does not match deterministic executability")
    capabilities = list(body.get("capabilities") or [])
    return {
        # ContractFaults intentionally do not make publication structurally
        # invalid.  They produce a published_unverified artifact whose runtime
        # refuses direct invocation.
        "passed": not integrity_issues,
        "revision": revision,
        "issues": integrity_issues,
        "contract_faults": executability["contract_faults"],
        "advisories": executability["advisories"],
        "executability_status": expected_status,
        "direct_call_enabled": executability["direct_call_enabled"],
        "captured_requests": len(facts),
        "materialized_requests": len(materialized),
        "runtime_steps": len(steps),
        "capabilities": len(capabilities),
    }
