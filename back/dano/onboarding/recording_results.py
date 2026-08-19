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
_PLACEHOLDER_TITLES = frozenset({
    "", "(未命名)", "(未捕获到业务请求)", "未命名录制", "录制业务", "录制业务流程",
})


def recording_display_title(*, user_title: str = "", draft: dict[str, Any] | None = None) -> str:
    """Prefer the operator title; otherwise use the recorded business name."""
    chosen = str(user_title or "").strip()
    if chosen and chosen not in _PLACEHOLDER_TITLES:
        return chosen
    body = draft if isinstance(draft, dict) else {}
    spec_title = str(body.get("title") or "").strip()
    if spec_title and spec_title not in _PLACEHOLDER_TITLES:
        return spec_title
    meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
    page_context = meta.get("page_context") if isinstance(meta.get("page_context"), dict) else {}
    page_title = str(
        page_context.get("document_title") or ""
    ).strip()
    if page_title and page_title not in _PLACEHOLDER_TITLES:
        return page_title
    plan = ((meta.get("capability_model") or {}) if isinstance(meta.get("capability_model"), dict) else {}).get("semantic_plan")
    understanding = plan.get("business_understanding") if isinstance(plan, dict) else {}
    if isinstance(understanding, dict):
        business_name = str(
            understanding.get("business_name") or understanding.get("object") or ""
        ).strip()
        if business_name and business_name not in _PLACEHOLDER_TITLES:
            return business_name
    return spec_title or chosen or page_title or "未命名录制"


def recording_result_asset_key(action: str) -> str:
    return f"{RECORDING_RESULT_KEY_PREFIX}{action}"


def is_recording_result_key(asset_key: str) -> bool:
    return str(asset_key or "").startswith(RECORDING_RESULT_KEY_PREFIX)


def _redact_headers(headers: Any) -> Any:
    if not isinstance(headers, dict):
        return headers
    return {str(key): "***" for key in headers}


def _redact_request_entry(entry: Any) -> Any:
    if not isinstance(entry, dict):
        return entry
    item = dict(entry)
    if item.get("headers"):
        item["headers"] = _redact_headers(item["headers"])
    if item.get("post_data") is not None:
        item["post_data"] = ""
    item["body_source"] = ""
    item["backup_body_source"] = ""
    if item.get("response_json") is not None:
        item["response_json"] = {}
    return item


def client_recording_draft(draft: dict[str, Any] | None) -> dict[str, Any] | None:
    """Project a saved FlowSpec for the workbench without compiling it.

    ``flow_spec_to_client`` can take tens of seconds on a real recording and
    would leave the capability page empty until it finishes.
    """

    if draft is None:
        return None
    projected = dict(draft)
    steps = projected.get("steps")
    if isinstance(steps, list):
        projected["steps"] = [_redact_request_entry(step) for step in steps]
    facts = projected.get("request_facts")
    if isinstance(facts, dict):
        facts = dict(facts)
        if isinstance(facts.get("requests"), list):
            facts["requests"] = [_redact_request_entry(req) for req in facts["requests"]]
        for key in ("field_evidence", "option_sources", "page_events"):
            if facts.get(key):
                facts[key] = []
        projected["request_facts"] = facts
    return projected


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
        "title": recording_display_title(user_title=title, draft=draft),
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
        "title": recording_display_title(
            user_title=str(body.get("title") or ""),
            draft=body.get("flow_spec") if isinstance(body.get("flow_spec"), dict) else body,
        ),
        "goal_summary": goal_text[:80],
        "capability_count": int(body.get("capability_count") or 0),
        "request_count": int(body.get("request_count") or 0),
        "created_at": created,
        "published": bool(body.get("published")) and bool(body.get("machine_verification_ran")),
        "machine_verification_ran": bool(body.get("machine_verification_ran")),
    }


def recording_result_detail(draft: AssetDraft) -> dict[str, Any]:
    """Return one saved result plus its client FlowSpec for the capability page."""

    payload = recording_result_summary(draft)
    spec = draft.body.get("flow_spec") if isinstance(draft.body, dict) else None
    if not isinstance(spec, dict):
        payload["draft"] = None
        return payload
    payload["draft"] = client_recording_draft(spec)
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
