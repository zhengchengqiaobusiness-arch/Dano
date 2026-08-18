"""Persist stage-six FlowSpec snapshots on the existing asset_drafts table."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from dano.assets.drafts import AssetDraft, DraftStore
from dano.onboarding.recording_workflow import _draft_fingerprint
from dano.shared.enums import AssetType
from dano.shared.models import Scope

RECORDING_RESULT_KEY_PREFIX = "recording-result:"
RECORDING_RESULT_KIND = "stage_six_recording_result"


def recording_result_asset_key(action: str) -> str:
    return f"{RECORDING_RESULT_KEY_PREFIX}{action}"


def is_recording_result_key(asset_key: str) -> bool:
    return str(asset_key or "").startswith(RECORDING_RESULT_KEY_PREFIX)


def stage_six_result_body(
    *,
    action: str,
    title: str,
    goal: Any,
    tenant: str,
    subsystem: str,
    draft: dict[str, Any],
    published: bool = False,
    machine_verification_ran: bool = False,
) -> dict[str, Any]:
    if isinstance(goal, dict):
        goal_payload = dict(goal)
    else:
        goal_payload = {"text": str(goal or "")}
    requests = []
    facts = draft.get("request_facts")
    if isinstance(facts, dict):
        requests = list(facts.get("requests") or [])
    capabilities = list(draft.get("capabilities") or [])
    return {
        "kind": RECORDING_RESULT_KIND,
        "action": action,
        "title": title,
        "goal": goal_payload,
        "tenant": tenant,
        "subsystem": subsystem,
        "flow_spec": draft,
        "capability_count": len(capabilities),
        "request_count": len(requests),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "published": published,
        "machine_verification_ran": machine_verification_ran,
        "fingerprint": _draft_fingerprint(draft),
    }


def recording_result_summary(draft: AssetDraft) -> dict[str, Any]:
    body = draft.body or {}
    goal = body.get("goal") if isinstance(body.get("goal"), dict) else {}
    goal_text = str(goal.get("intent") or goal.get("text") or "")
    created = draft.created_at.isoformat() if draft.created_at else str(body.get("created_at") or "")
    return {
        "id": str(draft.asset_draft_id),
        "action": str(body.get("action") or draft.asset_key.removeprefix(RECORDING_RESULT_KEY_PREFIX)),
        "title": str(body.get("title") or ""),
        "goal_summary": goal_text[:80],
        "capability_count": int(body.get("capability_count") or 0),
        "request_count": int(body.get("request_count") or 0),
        "created_at": created,
        "published": bool(body.get("published")),
        "machine_verification_ran": bool(body.get("machine_verification_ran")),
    }


def recording_result_detail(draft: AssetDraft) -> dict[str, Any]:
    """Return one saved result plus its client FlowSpec for the capability page."""

    payload = recording_result_summary(draft)
    spec = draft.body.get("flow_spec") if isinstance(draft.body, dict) else None
    if not isinstance(spec, dict):
        payload["draft"] = None
        return payload
    try:
        from dano.execution.page.flow_spec import FlowSpec, flow_spec_to_client

        payload["draft"] = flow_spec_to_client(FlowSpec.model_validate(spec))
    except Exception:  # noqa: BLE001 - viewing must still open with the saved draft
        payload["draft"] = spec
    return payload


async def persist_stage_six_result(
    store: DraftStore,
    *,
    run_id: str,
    scope: Scope,
    action: str,
    title: str,
    goal: Any,
    draft: dict[str, Any],
    published: bool = False,
    machine_verification_ran: bool = False,
) -> AssetDraft:
    return await store.save_draft(
        run_id=run_id,
        scope=scope,
        asset_type=AssetType.PAGE_SCRIPT,
        asset_key=recording_result_asset_key(action),
        body=stage_six_result_body(
            action=action,
            title=title,
            goal=goal,
            tenant=scope.tenant,
            subsystem=scope.subsystem.value,
            draft=draft,
            published=published,
            machine_verification_ran=machine_verification_ran,
        ),
    )


async def load_stage_six_flow_spec(store: DraftStore, result_id: UUID) -> dict[str, Any]:
    draft = await store.get_draft(result_id)
    if draft is None or not is_recording_result_key(draft.asset_key):
        raise ValueError("录制结果不存在")
    flow_spec = draft.body.get("flow_spec")
    if not isinstance(flow_spec, dict):
        raise ValueError("录制结果没有完整 FlowSpec")
    return flow_spec
