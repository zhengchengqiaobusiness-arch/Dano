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
    if item.get("response_text") is not None:
        item["response_text"] = ""
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
    projected["diagnostics"] = []
    steps = projected.get("steps")
    if isinstance(steps, list):
        projected["steps"] = [_redact_request_entry(step) for step in steps]
    facts = projected.get("request_facts")
    if isinstance(facts, dict):
        facts = dict(facts)
        if isinstance(facts.get("requests"), list):
            facts["requests"] = [_redact_request_entry(req) for req in facts["requests"]]
        for key in ("diagnostics", "field_evidence", "option_sources", "page_events"):
            facts[key] = []
        projected["request_facts"] = facts
    meta = projected.get("meta")
    if isinstance(meta, dict):
        meta = dict(meta)
        meta.pop("field_evidence", None)
        meta.pop("diagnostics", None)
        projected["meta"] = meta
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
    machine_verification_required: bool = False,
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
        "machine_verification_required": machine_verification_required,
        "fingerprint": _draft_fingerprint(draft),
    }


def recording_skill_lifecycle(body: dict[str, Any] | None) -> str:
    """Stable client-facing status after stage 1–7, independent of auto-publish."""

    payload = body if isinstance(body, dict) else {}
    export_status = str(payload.get("skill_export_status") or "")
    stage_status = str(payload.get("machine_verification_status") or "")
    checkpoint = payload.get("stage_seven") if isinstance(payload.get("stage_seven"), dict) else {}
    if not stage_status:
        stage_status = str(checkpoint.get("status") or "")
    if payload.get("skill_needs_reexport") or (
        export_status in {"exported", "succeeded"} and payload.get("skill_plan_valid") is False
    ):
        return "needs_reexport"
    if export_status in {"exported", "succeeded"} and payload.get("published"):
        return "exported"
    if export_status == "needs_clarification":
        return "needs_clarification"
    if export_status in {"failed", "generation_failed", "export_failed"}:
        if payload.get("published") or (payload.get("skill_id") and payload.get("skill_needs_reexport")):
            return "export_failed"
        if stage_status == "verified":
            return "verified_not_exported"
        return "export_failed"
    if export_status in {"generating", "planning"}:
        return "generating"
    if stage_status in {"running", "waiting_operator"}:
        return "verifying"
    if stage_status == "verified":
        return "verified_not_exported"
    if stage_status == "stale":
        return "needs_reexport" if payload.get("skill_id") else "stage_six_done"
    return "stage_six_done"


def recording_result_summary(draft: AssetDraft) -> dict[str, Any]:
    body = draft.body or {}
    goal = body.get("goal") if isinstance(body.get("goal"), dict) else {}
    goal_text = str(goal.get("intent") or goal.get("text") or "")
    created = draft.created_at.isoformat() if draft.created_at else str(body.get("created_at") or "")
    checkpoint = body.get("stage_seven") if isinstance(body.get("stage_seven"), dict) else {}
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
        "published": bool(body.get("published")),
        "machine_verification_ran": bool(body.get("machine_verification_ran")),
        "machine_verification_required": bool(body.get("machine_verification_required")),
        "machine_verification_status": str(body.get("machine_verification_status") or ""),
        "stage_seven_attempt_id": str(body.get("stage_seven_attempt_id") or ""),
        "stage_seven_updated_at": str(body.get("stage_seven_updated_at") or ""),
        "stage_seven_fingerprint": str(
            body.get("stage_seven_fingerprint") or checkpoint.get("working_fingerprint") or ""
        ),
        "skill_id": str(body.get("skill_id") or ""),
        "skill_version": int(body.get("skill_version") or 0),
        "skill_export_status": str(body.get("skill_export_status") or ""),
        "skill_export_path": str(body.get("export_path") or body.get("skill_export_path") or ""),
        "skill_lifecycle": recording_skill_lifecycle(body),
        "skill_needs_reexport": bool(body.get("skill_needs_reexport")),
        "skill_export_title": str(body.get("skill_export_title") or ""),
        "skill_export_description": str(body.get("skill_export_description") or ""),
        "skill_export_planning_mode": str(body.get("skill_export_planning_mode") or ""),
        "skill_export_example_requests": list(body.get("skill_export_example_requests") or [])
        if isinstance(body.get("skill_export_example_requests"), list)
        else [str(body.get("skill_export_example_requests") or "").strip()]
        if str(body.get("skill_export_example_requests") or "").strip()
        else [],
        "skill_export_success_criteria": str(body.get("skill_export_success_criteria") or ""),
        "skill_export_forbidden_actions": str(body.get("skill_export_forbidden_actions") or ""),
    }


def latest_recording_spec(body: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the spec every reader should show: saved original, else live working copy."""

    payload = body if isinstance(body, dict) else {}
    flow = payload.get("flow_spec") if isinstance(payload.get("flow_spec"), dict) else None
    checkpoint = payload.get("stage_seven") if isinstance(payload.get("stage_seven"), dict) else None
    working = checkpoint.get("working_flow_spec") if isinstance(checkpoint, dict) else None
    working = working if isinstance(working, dict) and working else None
    if not flow:
        return working
    if not working:
        return flow
    from dano.onboarding.recording_stage_seven import baseline_fingerprint

    stored_baseline = str((checkpoint or {}).get("baseline_fingerprint") or "")
    actual_baseline = baseline_fingerprint(flow)
    if stored_baseline and stored_baseline != actual_baseline:
        return flow
    return working


def recording_result_detail(draft: AssetDraft) -> dict[str, Any]:
    """Return one saved result plus its client FlowSpec for the capability page."""

    payload = recording_result_summary(draft)
    body = draft.body if isinstance(draft.body, dict) else {}
    checkpoint = body.get("stage_seven") if isinstance(body.get("stage_seven"), dict) else None
    spec = latest_recording_spec(body)
    if not isinstance(spec, dict):
        payload["draft"] = None
        return payload
    payload["draft"] = client_recording_draft(spec)
    payload["draft_fingerprint"] = _draft_fingerprint(spec)
    payload["stage_seven"] = {
        "status": str((checkpoint or {}).get("status") or body.get("machine_verification_status") or ""),
        "working_fingerprint": str(
            (checkpoint or {}).get("working_fingerprint") or body.get("stage_seven_fingerprint") or ""
        ),
        "publishable": bool(((checkpoint or {}).get("verdict") or {}).get("publishable")),
    } if checkpoint or body.get("machine_verification_status") else None
    payload["skill_plan"] = body.get("skill_plan") if isinstance(body.get("skill_plan"), dict) else None
    return payload


def _editable_recording_spec(body: dict[str, Any]) -> dict[str, Any]:
    spec = latest_recording_spec(body)
    if not isinstance(spec, dict) or not spec:
        raise ValueError("录制结果没有完整 FlowSpec")
    return spec


def apply_recording_result_edits(
    body: dict[str, Any],
    edits: list[dict[str, Any]],
    *,
    expected_fingerprint: str,
) -> dict[str, Any]:
    """Write capability edits back to the stored result. No verify/repair/export."""

    from dano.execution.page.flow_spec import FlowSpec, FlowSpecConflictError, apply_client_flow_patch, flow_spec_fingerprint

    current_spec = _editable_recording_spec(body)
    current_fp = _draft_fingerprint(current_spec)
    expected = str(expected_fingerprint or "")
    if not expected:
        raise ValueError("expected_fingerprint is required")
    if expected != current_fp:
        raise FlowSpecConflictError(expected, current_fp)
    spec = FlowSpec.model_validate(current_spec)
    updated = apply_client_flow_patch(
        spec,
        list(edits or []),
        expected_fingerprint=flow_spec_fingerprint(spec),
    )
    dumped = updated.model_dump(mode="json")
    next_body = dict(body)
    next_body["flow_spec"] = dumped
    next_body["fingerprint"] = _draft_fingerprint(dumped)
    next_body["capability_count"] = len(list(dumped.get("capabilities") or []))
    next_body["title"] = recording_display_title(
        user_title=str(next_body.get("title") or ""),
        draft=dumped,
    )
    checkpoint = next_body.get("stage_seven") if isinstance(next_body.get("stage_seven"), dict) else None
    if checkpoint is not None:
        from dano.onboarding.recording_stage_seven import baseline_fingerprint

        synced = dict(checkpoint)
        synced["working_flow_spec"] = dumped
        synced["working_fingerprint"] = next_body["fingerprint"]
        synced["baseline_fingerprint"] = baseline_fingerprint(dumped)
        next_body["stage_seven"] = synced
    return next_body


def invalidate_skill_after_capability_edit(body: dict[str, Any]) -> dict[str, Any]:
    """Drop unpublished plans and mark exported Skills as stale after an edit."""

    next_body = dict(body)
    next_body["machine_verification_status"] = "stale"
    next_body["skill_plan_valid"] = False
    if next_body.get("skill_plan") and not next_body.get("published"):
        next_body["skill_plan"] = None
    if next_body.get("published") or str(next_body.get("skill_export_status") or "") in {
        "exported",
        "succeeded",
    }:
        next_body["skill_needs_reexport"] = True
    return next_body


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
    machine_verification_required: bool = False,
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
            machine_verification_required=machine_verification_required,
        ),
    )


async def load_stage_six_flow_spec(store: DraftStore, result_id: UUID) -> dict[str, Any]:
    draft = await store.get_draft(result_id)
    if draft is None or not is_recording_result_key(draft.asset_key):
        raise ValueError("录制结果不存在")
    flow_spec = latest_recording_spec(dict(draft.body or {}))
    if not isinstance(flow_spec, dict):
        raise ValueError("录制结果没有完整 FlowSpec")
    return flow_spec


async def load_stage_seven_resume(
    store: DraftStore,
    result_id: UUID,
    *,
    reset_stage_seven: bool = False,
) -> tuple[dict[str, Any], dict[str, Any] | None, str]:
    from dano.onboarding.recording_stage_seven import load_resumable_working_spec

    draft = await store.get_draft(result_id)
    if draft is None or not is_recording_result_key(draft.asset_key):
        raise ValueError("录制结果不存在")
    return load_resumable_working_spec(dict(draft.body or {}), reset_stage_seven=reset_stage_seven)
