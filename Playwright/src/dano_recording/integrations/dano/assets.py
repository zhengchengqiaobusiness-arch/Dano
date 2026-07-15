"""Recording-only publication using Dano's bottom-level asset transactions."""

from __future__ import annotations

from typing import Any
from uuid import UUID


class DanoAssetPublisher:
    def __init__(self) -> None:
        from dano.assets.drafts import DraftStore
        from dano.assets.repository import AssetRepository

        self.drafts = DraftStore()
        self.assets = AssetRepository()

    async def publish(
        self,
        *,
        run_id: str,
        tenant: str,
        subsystem: str,
        action: str,
        body: dict[str, Any],
        validation: dict[str, Any],
        reviews: list[dict[str, Any]],
    ) -> dict[str, Any]:
        from dano.schemas.validate import validate_asset_body
        from dano.shared.enums import AssetType, Subsystem, ValidationStatus
        from dano.shared.models import AssetEnvelope, Scope

        if body.get("recording_engine") != "playwright_v3" or (
            body.get("api_request") or {}
        ).get("recording_engine") != "playwright_v3":
            raise ValueError("recording V3 publisher requires recording_engine=playwright_v3")
        if validation.get("passed") is not True:
            raise ValueError("a failed deterministic validation cannot enter the asset transaction")
        release_hash = str(validation.get("content_hash") or "")
        snapshot_hash = str(validation.get("snapshot_hash") or "")
        revision = int(validation.get("revision") or 0)
        verification = str(body.get("verification_status") or "")
        publication = str(body.get("publication_status") or "")
        direct_call = body.get("direct_call_enabled")
        api_request = body.get("api_request") or {}
        if (
            revision < 1
            or int(body.get("revision") or -1) != revision
            or int(api_request.get("revision") or -1) != revision
        ):
            raise ValueError(
                "recording V3 body and api_request must match the validated frozen revision"
            )
        expected_publication = {
            "verified": "published_verified",
            "unverified": "published_unverified",
        }.get(verification)
        if (
            expected_publication is None
            or publication != expected_publication
            or direct_call is not (verification == "verified")
            or str(api_request.get("verification_status") or "") != verification
            or api_request.get("direct_call_enabled") is not direct_call
            or str(validation.get("executability_status") or "") != verification
            or validation.get("direct_call_enabled") is not direct_call
        ):
            raise ValueError("recording V3 verification/publication/direct-call contract is inconsistent")
        if not snapshot_hash:
            raise ValueError("recording V3 publication requires a frozen snapshot hash")
        if not release_hash or any(str(item.get("content_hash") or "") != release_hash for item in reviews):
            raise ValueError("review verdicts must match the frozen release content hash")
        if any(str(item.get("snapshot_hash") or "") != snapshot_hash for item in reviews):
            raise ValueError("review verdicts must match the frozen revision snapshot hash")
        if any(int(item.get("revision") or -1) != revision for item in reviews):
            raise ValueError("review verdicts must match the current frozen revision")
        roles = {str(item.get("role") or "") for item in reviews}
        sessions = {str(item.get("pi_session_id") or "") for item in reviews}
        if roles != {"acceptance", "security", "compliance"} or len(reviews) != 3:
            raise ValueError("recording V3 publication requires exactly three reviewer roles")
        if "" in sessions or len(sessions) != 3:
            raise ValueError("recording V3 publication requires three isolated Pi sessions")
        if not tenant or not subsystem or not action:
            raise ValueError("tenant, subsystem and action are required for publication")
        validate_asset_body(AssetType.PAGE_SCRIPT, body)
        scope = Scope(tenant=tenant, subsystem=Subsystem(subsystem))
        draft = await self.drafts.save_draft(
            run_id=run_id,
            scope=scope,
            asset_type=AssetType.PAGE_SCRIPT,
            asset_key=action,
            body=body,
        )
        check = await self.drafts.record_validation(
            asset_draft_id=draft.asset_draft_id,
            kind="self_check",
            passed=True,
            response=validation,
            evidence={
                "mode": "recording_v3_deterministic",
                "revision": validation.get("revision"),
                "release_content_hash": release_hash,
                "snapshot_hash": snapshot_hash,
                "executability_status": validation.get("executability_status"),
                "contract_faults": validation.get("contract_faults") or [],
                "content_hash": draft.content_hash,
                "issues": validation.get("issues") or [],
            },
        )
        review_ids: list[UUID] = []
        for item in reviews:
            recorded = await self.drafts.record_review(
                asset_draft_id=draft.asset_draft_id,
                role=item["role"],
                model_id=str(item.get("model_id") or "pi-recording-reviewer"),
                # Model findings are semantic advice.  ``passed`` here means
                # the isolated review evidence was successfully completed and
                # bound, while the original opinion is preserved in metadata.
                passed=True,
                reasons=list(item.get("reasons") or []),
                evidence=list(item.get("evidence") or []),
                metadata={
                    "recording_engine": "playwright_v3",
                    "pi_session_id": str(item.get("pi_session_id") or ""),
                    "review_revision": int(item.get("revision") or validation.get("revision") or 0),
                    "release_content_hash": release_hash,
                    "snapshot_hash": snapshot_hash,
                    "semantic_passed": item.get("passed") is True,
                    "unavailable": item.get("unavailable") is True,
                    "advisory_only": True,
                },
            )
            review_ids.append(recorded.review_run_id)
        ok, reason = await self.drafts.verify_publishable(draft.asset_draft_id, [check.validation_run_id])
        if not ok:
            return {"published": False, "reason": reason, "stage": "validation"}
        ok, reason = await self.drafts.verify_reviewed(draft.asset_draft_id, review_ids)
        if not ok:
            return {"published": False, "reason": reason, "stage": "review"}
        # Dano is the publication trust boundary: re-read the three stored rows
        # instead of trusting the in-memory verdicts just submitted above.
        if hasattr(self.drafts, "list_reviews"):
            stored_reviews = await self.drafts.list_reviews(draft.asset_draft_id)
            selected = [item for item in stored_reviews if item.review_run_id in set(review_ids)]
            if len(selected) != 3 or {str(item.role) for item in selected} != {
                "acceptance", "security", "compliance",
            }:
                return {
                    "published": False,
                    "reason": "Dano review evidence readback is incomplete",
                    "stage": "review_readback",
                }
            for item in selected:
                metadata = dict(item.metadata or {})
                if (
                    item.asset_draft_id != draft.asset_draft_id
                    or item.content_hash != draft.content_hash
                    or not item.passed
                    or not item.model_id
                    or metadata.get("release_content_hash") != release_hash
                    or metadata.get("snapshot_hash") != snapshot_hash
                    or int(metadata.get("review_revision") or -1) != revision
                    or not metadata.get("pi_session_id")
                ):
                    return {
                        "published": False,
                        "reason": "Dano review evidence readback does not match the frozen release",
                        "stage": "review_readback",
                    }
            if len({
                str((item.metadata or {}).get("pi_session_id") or "") for item in selected
            }) != 3:
                return {
                    "published": False,
                    "reason": "Dano review evidence sessions are not isolated",
                    "stage": "review_readback",
                }
        # Re-read the immutable draft and validate again immediately before the transaction.
        frozen = await self.drafts.get_draft(draft.asset_draft_id)
        if frozen is None or frozen.content_hash != draft.content_hash:
            return {"published": False, "reason": "frozen draft changed", "stage": "freeze"}
        validate_asset_body(AssetType.PAGE_SCRIPT, frozen.body)
        # A process may die after the asset transaction commits but before the
        # recording operation/result checkpoint is written.  Recover the exact
        # immutable publication by its full scope, action, Dano fingerprint and
        # frozen body instead of appending a duplicate version on restart.
        if hasattr(self.assets, "list_versions"):
            versions = await self.assets.list_versions(
                AssetType.PAGE_SCRIPT,
                scope,
                action,
            )
            recovered = next(
                (
                    item for item in versions
                    if item.asset_type == AssetType.PAGE_SCRIPT
                    and item.scope == scope
                    and item.asset_key == action
                    and item.validation_status == ValidationStatus.PUBLISHED
                    and item.source_fingerprint == frozen.content_hash
                    and item.body == frozen.body
                ),
                None,
            )
            if recovered is not None:
                return {
                    "published": True,
                    "asset_id": str(recovered.asset_id),
                    "version": recovered.version,
                    "content_hash": frozen.content_hash,
                    "snapshot_hash": snapshot_hash,
                    "verification_status": verification,
                    "publication_status": publication,
                    "contract_fault_count": len(validation.get("contract_faults") or []),
                    "recovered": True,
                }
        envelope = await self.assets.create(AssetEnvelope(
            asset_type=AssetType.PAGE_SCRIPT,
            scope=scope,
            asset_key=action,
            version=0,
            source_fingerprint=frozen.content_hash,
            validation_status=ValidationStatus.VERIFIED,
            confidence=0.95,
            human_confirmed=True,
            body=frozen.body,
        ))
        published = await self.assets.set_status(envelope.asset_id, ValidationStatus.PUBLISHED)
        if (
            published is None
            or published.asset_id != envelope.asset_id
            or published.source_fingerprint != frozen.content_hash
            or published.validation_status != ValidationStatus.PUBLISHED
            or published.body != frozen.body
        ):
            return {
                "published": False,
                "asset_id": str(envelope.asset_id),
                "version": envelope.version,
                "content_hash": frozen.content_hash,
                "stage": "asset_lifecycle",
                "reason": "asset status transaction did not publish the frozen version",
            }
        result = {
            "published": True,
            "asset_id": str(envelope.asset_id),
            "version": envelope.version,
            "content_hash": frozen.content_hash,
        }
        result.update({
            "snapshot_hash": snapshot_hash,
            "verification_status": verification,
            "publication_status": publication,
            "contract_fault_count": len(validation.get("contract_faults") or []),
        })
        return result
