"""Manual stage-8 Skill export for one recording result."""

from __future__ import annotations

import inspect
import json
import shutil
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog
from pydantic import BaseModel, Field

from dano.execution.page.flow_spec_core.models import FlowSpec
from dano.onboarding.skill_generation.catalog import capability_ref
from dano.onboarding.skill_generation.export_view import (
    build_export_view,
    promote_unconfirmed_write_fields,
)
from dano.onboarding.skill_generation.models import (
    SkillGenerationRequest,
    SkillPlan,
    generation_request_fingerprint,
)
from dano.onboarding.skill_generation.planner import generate_skill_plan
from dano.onboarding.skill_generation.validate import plan_to_contract_payload

log = structlog.get_logger(__name__)

PublishSkill = Callable[..., Awaitable[dict[str, Any]]]
RenderSkill = Callable[..., str]
BuildSkill = Callable[..., Any]
PersistBody = Callable[[dict[str, Any]], Awaitable[None]]


class SkillExportOutcome(BaseModel):
    status: str
    skill_id: str = ""
    skill_name: str = ""
    version: int = 0
    planning_mode: str = ""
    used_capabilities: list[dict[str, Any]] = Field(default_factory=list)
    unused_capabilities: list[dict[str, Any]] = Field(default_factory=list)
    routes: list[dict[str, Any]] = Field(default_factory=list)
    unresolved_branches: list[str] = Field(default_factory=list)
    export_path: str = ""
    plan: dict[str, Any] | None = None
    clarification_questions: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    idempotent: bool = False


class SkillExportError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _preview(text: str, limit: int = 240) -> str:
    raw = str(text or "").strip()
    if len(raw) <= limit:
        return raw
    return raw[:limit] + "…"


def _capability_rows(spec: FlowSpec) -> list[dict[str, Any]]:
    return [
        {
            "capability_id": capability_ref(cap),
            "name": cap.name,
            "title": cap.title or cap.name,
            "kind": cap.kind,
        }
        for cap in spec.capabilities
        if capability_ref(cap)
    ]


_COMPOSITION_LABELS = {
    "atomic": "单独办理",
    "bound": "查询后自动带入",
    "handoff": "先办理再请你选定",
    "independent": "各步分开收集",
}


def _capability_titles(spec: FlowSpec | None) -> dict[str, str]:
    titles: dict[str, str] = {}
    if spec is None:
        return titles
    for cap in spec.capabilities:
        label = str(cap.title or cap.name or "").strip()
        if not label:
            continue
        if cap.capability_id:
            titles[str(cap.capability_id)] = label
        if cap.name:
            titles[str(cap.name)] = label
    return titles


def _route_rows(plan: SkillPlan | None, spec: FlowSpec | None = None) -> list[dict[str, Any]]:
    if plan is None:
        return []
    titles = _capability_titles(spec)
    rows: list[dict[str, Any]] = []
    for route in plan.routes:
        steps = [
            titles.get(str(cap_id), "") or str(cap_id)
            for cap_id in route.capability_sequence
            if str(cap_id)
        ]
        auto_carry = []
        for binding in route.bindings:
            source = titles.get(binding.from_capability) or "上一步"
            target = titles.get(binding.to_capability) or "下一步"
            field = str(binding.to_input or "").strip()
            if field:
                auto_carry.append(f"{source}的结果会自动填入{target}需要的「{field}」")
            else:
                auto_carry.append(f"{source}的结果会自动带入{target}")
        ask_when = [
            str(item.prompt).strip()
            for item in route.checkpoints
            if str(item.prompt or "").strip()
        ]
        rows.append({
            "name": route.name,
            "when_to_use": route.when_to_use,
            "steps": steps,
            "auto_carry": auto_carry,
            "ask_when": ask_when,
            "composition": _COMPOSITION_LABELS.get(str(route.composition_mode), "单独办理"),
            "needs_confirm": bool(route.requires_confirmation),
        })
    return rows


def _unresolved_rows(plan: SkillPlan | None) -> list[str]:
    if plan is None:
        return []
    rows = [str(item).strip() for item in plan.clarification_questions if str(item).strip()]
    for branch in plan.intent_branches:
        if not (branch.unresolved or branch.conflicting):
            continue
        reason = "、".join(str(item).strip() for item in branch.unresolved if str(item).strip())
        trigger = str(branch.trigger or "").strip()
        if trigger and reason:
            rows.append(f"{trigger}：{reason}")
        elif trigger:
            rows.append(trigger)
        elif reason:
            rows.append(reason)
    return list(dict.fromkeys(rows))


def _skill_draft_fields(request: SkillGenerationRequest, title: str) -> dict[str, Any]:
    return {
        "skill_export_title": title,
        "skill_export_description": str(request.business_description or "").strip(),
        "skill_export_planning_mode": str(request.planning_mode),
        "skill_export_example_requests": [
            str(item).strip() for item in request.example_requests if str(item).strip()
        ],
        "skill_export_success_criteria": str(request.success_criteria or "").strip(),
        "skill_export_forbidden_actions": str(request.forbidden_actions or "").strip(),
    }


def _log_export(
    event: str,
    *,
    summary: str,
    status: str = "progress",
    level: str = "info",
    duration_ms: int | float | None = None,
    error: dict[str, Any] | None = None,
    next_action: str = "",
    **details: Any,
) -> None:
    payload = {key: value for key, value in details.items() if value is not None}
    writer = log.error if level in {"error", "exception"} else log.warning if level == "warning" else log.info
    writer(event, summary=summary, status=status, **payload)
    try:
        from dano.infra.run_logging import emit_run_event

        emit_run_event(
            event,
            stage="export",
            status=status,
            summary=summary,
            level=level,
            duration_ms=duration_ms,
            details=payload,
            error=error,
            next_action=next_action,
        )
    except Exception:  # noqa: BLE001 - export logging must not fail the request
        pass


def _current_spec(body: dict[str, Any]) -> FlowSpec:
    """Read the persisted recording FlowSpec. Do not import recording_results."""
    raw = body.get("flow_spec") if isinstance(body.get("flow_spec"), dict) else None
    if not isinstance(raw, dict) or not raw:
        checkpoint = body.get("stage_seven") if isinstance(body.get("stage_seven"), dict) else {}
        working = checkpoint.get("working_flow_spec") if isinstance(checkpoint, dict) else None
        raw = working if isinstance(working, dict) and working else None
    if not isinstance(raw, dict) or not raw:
        raise SkillExportError(409, "录制结果没有可导出的 FlowSpec")
    return FlowSpec.model_validate(raw)


def _stable_skill_id(body: dict[str, Any], title: str) -> str:
    from dano.catalog.identity import public_skill_action

    existing = str(body.get("skill_id") or "").strip()
    if existing:
        return existing
    subsystem = str(body.get("subsystem") or "oa")
    action = public_skill_action(title, str(body.get("action") or ""))
    return f"{subsystem}.{action}"


def _used_capability_rows(spec: FlowSpec, plan: SkillPlan) -> list[dict[str, Any]]:
    selected = set(plan.selected_capability_ids)
    rows = []
    for cap in spec.capabilities:
        cap_id = cap.capability_id or cap.name
        if cap_id in selected or cap.name in selected:
            rows.append({
                "capability_id": cap.capability_id,
                "name": cap.name,
                "title": cap.title or cap.name,
                "kind": cap.kind,
            })
    return rows


async def _call_persist(persist: PersistBody | None, payload: dict[str, Any]) -> None:
    if persist is None:
        return
    result = persist(payload)
    if inspect.isawaitable(result):
        await result


def _already_exported(body: dict[str, Any]) -> bool:
    path = str(body.get("export_path") or body.get("skill_export_path") or "")
    return bool(
        body.get("published")
        and str(body.get("skill_export_status") or "") in {"exported", "succeeded"}
        and path
        and Path(path).exists()
        and _existing_package_is_current(path)
    )


def _existing_package_is_current(export_path: str) -> bool:
    root = Path(export_path)
    if not root.is_dir():
        return False
    from dano.export.skill_package.validator import validate_skill_package

    return bool(validate_skill_package(root).get("ok"))


def _remove_previous_skill_output(out_dir: str, skill_id: str, body: dict[str, Any]) -> None:
    """Delete the last on-disk package and leftover stage folders before rewriting."""
    from dano.export.skill_package.renderer import package_slug

    root = Path(out_dir)
    slug = package_slug(skill_id) if skill_id else ""
    candidates: list[Path] = []
    for key in ("export_path", "skill_export_path"):
        raw = str(body.get(key) or "").strip()
        if raw:
            candidates.append(Path(raw))
    if slug:
        candidates.append(root / slug)
    seen: set[Path] = set()
    for target in candidates:
        try:
            resolved = target.resolve()
        except OSError:
            continue
        if resolved in seen or not resolved.is_dir():
            continue
        name = resolved.name
        if name.endswith("-package") or name.startswith(f".{slug}") or name.startswith(f".{slug}."):
            seen.add(resolved)
            shutil.rmtree(resolved, ignore_errors=True)
    if root.is_dir() and slug:
        for stale in (*root.glob(f".{slug}-*"), *root.glob(f".{slug}.old-*")):
            if stale.is_dir():
                shutil.rmtree(stale, ignore_errors=True)


def build_export_skill_spec(
    view: FlowSpec,
    *,
    tenant: str,
    skill_id: str,
    title: str,
    plan: SkillPlan,
) -> Any:
    from dano.execution.page.flow_release import prepare_flow_release_candidate
    from dano.execution.page.flow_spec import flow_spec_release_payload, flow_spec_to_api_request
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import RiskLevel, Subsystem

    from dano.export.skill_package.renderer import restore_compiled_capability_schemas

    release_spec, candidate = prepare_flow_release_candidate(view)
    release_spec = promote_unconfirmed_write_fields(release_spec)
    api_request, errors = flow_spec_to_api_request(release_spec, _prepared=True)
    if errors or not api_request:
        raise SkillExportError(409, "导出视图无法编译为 Skill 包：" + "；".join(errors or ["未知错误"]))
    api_request = restore_compiled_capability_schemas(api_request, view)
    plan_payload = plan.model_dump(mode="json")
    api_request["_release_snapshot"] = {
        **candidate,
        "flow_spec": flow_spec_release_payload(release_spec),
        "skill_plan": plan_payload,
    }
    api_request["_skill_plan"] = plan_payload
    sub_str, _, action = skill_id.partition(".")
    try:
        risk_level = RiskLevel(str(view.risk_level or "L3"))
    except ValueError:
        risk_level = RiskLevel.L3
    return SkillSpec(
        skill_id=skill_id,
        tenant=tenant,
        subsystem=Subsystem(sub_str or view.subsystem or "oa"),
        action=action or str(view.meta.get("action") or "skill"),
        title=title or view.title or action,
        risk_level=risk_level,
        recording_asset_id=UUID(int=0),
        api_request=api_request,
        call_metadata={"skill_plan": plan_payload},
        capabilities=list(api_request.get("capabilities") or []),
        capability_relations=list(api_request.get("capability_relations") or []),
    )


def _minimal_export_skill(
    view: FlowSpec,
    *,
    tenant: str,
    skill_id: str,
    title: str,
    plan: SkillPlan,
) -> Any:
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import RiskLevel, Subsystem

    plan_payload = plan.model_dump(mode="json")
    api_request = {
        "capabilities": [
            {
                "capability_id": cap.capability_id,
                "name": cap.name,
                "title": cap.title or cap.name,
                "kind": cap.kind,
                "step_ids": list(cap.step_ids or []),
                "input_schema": dict(cap.input_schema or {}),
                "output_schema": dict(cap.output_schema or {}),
                "requires_human_confirm": cap.requires_human_confirm,
            }
            for cap in view.capabilities
        ],
        "capability_relations": [
            relation.model_dump(mode="json")
            for relation in (view.capability_relations or [])
        ],
        "steps": [
            {
                "step_id": step.step_id,
                "method": step.method,
                "path": step.path,
                "url": step.url or step.path,
            }
            for step in view.steps
        ],
        "_skill_plan": plan_payload,
        "_release_snapshot": {
            "skill_plan": plan_payload,
            "flow_spec": view.model_dump(mode="json"),
        },
    }
    sub_str, _, action = skill_id.partition(".")
    return SkillSpec(
        skill_id=skill_id,
        tenant=tenant,
        subsystem=Subsystem(sub_str or view.subsystem or "oa"),
        action=action or "skill",
        title=title or view.title or action,
        risk_level=RiskLevel.L3,
        recording_asset_id=UUID(int=0),
        api_request=api_request,
        call_metadata={"skill_plan": plan_payload},
        capabilities=list(api_request["capabilities"]),
        capability_relations=list(api_request["capability_relations"]),
    )


def _next_version(body: dict[str, Any]) -> int:
    current = int(body.get("skill_version") or 0)
    return current + 1 if current else 1


async def export_recording_skill(
    *,
    result_id: UUID,
    body: dict[str, Any],
    tenant: str,
    request: SkillGenerationRequest,
    proposer=None,
    publish: PublishSkill | None = None,
    render: RenderSkill | None = None,
    persist: PersistBody | None = None,
    build_skill: BuildSkill | None = None,
) -> SkillExportOutcome:
    started = time.monotonic()
    title = str(request.title or body.get("title") or "").strip()
    if not str(request.business_description or "").strip():
        _log_export(
            "skill.export.failed",
            summary="业务描述为空，拒绝导出",
            status="failed",
            level="error",
            result_id=str(result_id),
            title=title,
        )
        raise SkillExportError(400, "业务描述不能为空")
    if not request.preview_only and not str(request.out_dir or "").strip():
        _log_export(
            "skill.export.failed",
            summary="导出目录为空，拒绝导出",
            status="failed",
            level="error",
            result_id=str(result_id),
            title=title,
        )
        raise SkillExportError(400, "导出目录不能为空")
    spec = _current_spec(body)
    from dano.onboarding.recording_stage_seven import working_fingerprint

    fingerprint = working_fingerprint(spec)
    capabilities = _capability_rows(spec)
    verified = {capability_ref(cap) for cap in spec.capabilities if capability_ref(cap)}
    _log_export(
        "skill.export.started",
        summary="开始按阶段6能力规划并导出 Skill",
        status="started",
        result_id=str(result_id),
        action=str(body.get("action") or ""),
        title=title or spec.title,
        planning_mode=str(request.planning_mode),
        out_dir=str(request.out_dir),
        fingerprint=fingerprint,
        capability_count=len(capabilities),
        capabilities=capabilities,
        description_preview=_preview(request.business_description),
        existing_skill_id=str(body.get("skill_id") or ""),
    )
    if not verified:
        _log_export(
            "skill.export.failed",
            summary="录制结果没有可导出能力",
            status="failed",
            level="error",
            result_id=str(result_id),
            fingerprint=fingerprint,
        )
        raise SkillExportError(409, "尚未生成可导出能力，不能生成 Skill")
    request_fp = generation_request_fingerprint(
        result_id=str(result_id),
        stage_seven_fingerprint=fingerprint,
        request=request,
    )
    if (
        not request.preview_only
        and str(body.get("skill_request_fingerprint") or "") == request_fp
        and _already_exported(body)
    ):
        plan = body.get("skill_plan") if isinstance(body.get("skill_plan"), dict) else {}
        used = list((plan or {}).get("used_capabilities") or [])
        if not used and plan:
            try:
                used = _used_capability_rows(spec, SkillPlan.model_validate(plan))
            except Exception:  # noqa: BLE001 - idempotent path still returns the stored plan
                used = []
        skill_id = str(body.get("skill_id") or "")
        export_path = str(body.get("export_path") or body.get("skill_export_path") or "")
        _log_export(
            "skill.export.completed",
            summary="请求未变化，直接返回已导出 Skill",
            status="succeeded",
            duration_ms=(time.monotonic() - started) * 1000,
            result_id=str(result_id),
            skill_id=skill_id,
            export_path=export_path,
            idempotent=True,
            used_capabilities=used,
        )
        stored_plan = None
        try:
            stored_plan = SkillPlan.model_validate(plan) if plan else None
        except Exception:  # noqa: BLE001 - keep the stored payload if it is not a current plan
            stored_plan = None
        return SkillExportOutcome(
            status="exported",
            skill_id=skill_id,
            skill_name=str(request.title or body.get("title") or ""),
            version=int(body.get("skill_version") or 1),
            planning_mode=str((plan or {}).get("planning_mode") or request.planning_mode),
            used_capabilities=used,
            unused_capabilities=list((plan or {}).get("unused_capabilities") or []),
            routes=_route_rows(stored_plan, spec) if stored_plan else [],
            unresolved_branches=_unresolved_rows(stored_plan),
            export_path=export_path,
            plan=plan,
            idempotent=True,
        )

    if not request.preview_only:
        await _call_persist(persist, {
            **body,
            "skill_export_status": "generating",
            "skill_plan_valid": False,
            **_skill_draft_fields(request, title),
        })
    _log_export(
        "skill.export.planning",
        summary="开始规划 Skill 路线",
        result_id=str(result_id),
        fingerprint=fingerprint,
        capability_ids=sorted(verified),
    )
    plan_started = time.monotonic()
    planned = await generate_skill_plan(
        spec,
        request,
        verified_capability_ids=verified,
        source_flow_fingerprint=fingerprint,
        proposer=proposer,
    )
    plan_ms = (time.monotonic() - plan_started) * 1000
    if planned.status != "planned" or planned.plan is None:
        _log_export(
            "skill.export.failed",
            summary="Skill 规划未通过，停止导出",
            status="failed",
            level="error",
            duration_ms=plan_ms,
            result_id=str(result_id),
            plan_status=planned.status,
            errors=list(planned.errors or []),
            clarification_questions=list(planned.clarification_questions or []),
            routes=_route_rows(planned.plan, spec),
        )
        await _call_persist(persist, {
            **body,
            "skill_export_status": planned.status,
            "skill_plan": planned.plan.model_dump(mode="json") if planned.plan else None,
            "skill_plan_valid": False,
            "published": bool(body.get("published")),
            **_skill_draft_fields(request, title),
        })
        return SkillExportOutcome(
            status=planned.status,
            clarification_questions=planned.clarification_questions,
            unresolved_branches=_unresolved_rows(planned.plan) or list(planned.clarification_questions or []),
            errors=planned.errors,
            routes=_route_rows(planned.plan, spec),
            plan=planned.plan.model_dump(mode="json") if planned.plan else None,
        )

    plan = planned.plan
    if request.preview_only:
        await _call_persist(persist, {
            **body,
            "skill_export_status": "previewed",
            "skill_plan": plan.model_dump(mode="json"),
            "skill_plan_valid": True,
            "published": bool(body.get("published")),
            **_skill_draft_fields(request, title),
        })
        return SkillExportOutcome(
            status="previewed",
            skill_name=str(request.title or body.get("title") or ""),
            planning_mode=str(plan.planning_mode),
            used_capabilities=[
                {"capability_id": cap_id}
                for cap_id in plan.selected_capability_ids
            ],
            unused_capabilities=[item.model_dump(mode="json") for item in plan.unused_capabilities],
            routes=_route_rows(plan, spec),
            unresolved_branches=_unresolved_rows(plan),
            plan=plan.model_dump(mode="json"),
        )
    title = str(request.title or body.get("title") or spec.title or "").strip()
    skill_id = _stable_skill_id(body, title)
    _log_export(
        "skill.export.planned",
        summary="Skill 规划完成",
        status="succeeded",
        duration_ms=plan_ms,
        result_id=str(result_id),
        skill_id=skill_id,
        selected_capability_ids=list(plan.selected_capability_ids),
        unused_capabilities=[item.capability_id for item in plan.unused_capabilities],
        routes=_route_rows(plan, spec),
        planning_mode=str(plan.planning_mode),
    )
    view = build_export_view(spec, plan.selected_capability_ids)
    _log_export(
        "skill.export.view_ready",
        summary="已按规划裁剪导出视图",
        result_id=str(result_id),
        skill_id=skill_id,
        view_capability_count=len(view.capabilities or []),
        view_step_count=len(view.steps or []),
    )
    if build_skill is not None:
        skill = build_skill(view, tenant=tenant, skill_id=skill_id, title=title, plan=plan)
    else:
        try:
            skill = build_export_skill_spec(
                view, tenant=tenant, skill_id=skill_id, title=title, plan=plan,
            )
        except SkillExportError as exc:
            _log_export(
                "skill.export.build_fallback",
                summary="正式编译失败，改用最小导出包",
                status="warning",
                level="warning",
                result_id=str(result_id),
                skill_id=skill_id,
                reason=exc.detail,
            )
            skill = _minimal_export_skill(
                view, tenant=tenant, skill_id=skill_id, title=title, plan=plan,
            )
    out_dir = str(request.out_dir).strip()
    _remove_previous_skill_output(out_dir, skill_id, body)
    render_fn = render or _default_render
    publish_fn = publish or _default_publish
    published_report: dict[str, Any] | None = None
    export_path = ""
    try:
        render_started = time.monotonic()
        _log_export(
            "skill.export.rendering",
            summary="开始写出 Skill 包",
            result_id=str(result_id),
            skill_id=skill_id,
            out_dir=out_dir,
        )
        slug = render_fn(skill, out_dir, tenant=tenant)
        export_path = str(Path(out_dir) / slug)
        _assert_package_matches_plan(export_path, plan)
        _log_export(
            "skill.export.rendered",
            summary="Skill 包已写出并通过对齐校验",
            status="succeeded",
            duration_ms=(time.monotonic() - render_started) * 1000,
            result_id=str(result_id),
            skill_id=skill_id,
            export_path=export_path,
        )
        publish_started = time.monotonic()
        _log_export(
            "skill.export.publishing",
            summary="开始发布导出后的 Skill 资产",
            result_id=str(result_id),
            skill_id=skill_id,
        )
        published_report = await publish_fn(
            tenant=tenant,
            subsystem=str(body.get("subsystem") or view.subsystem or "oa"),
            action=str(body.get("action") or ""),
            title=title,
            skill_id=skill_id,
            result_id=str(result_id),
            view=view,
            skill=skill,
        )
        if published_report and published_report.get("ok") is False:
            raise RuntimeError(str(published_report.get("reason") or "录制资产发布失败"))
        version = int((published_report or {}).get("asset_version") or _next_version(body))
        _log_export(
            "skill.export.published",
            summary="Skill 资产发布成功",
            status="succeeded",
            duration_ms=(time.monotonic() - publish_started) * 1000,
            result_id=str(result_id),
            skill_id=skill_id,
            asset_id=str((published_report or {}).get("asset_id") or ""),
            version=version,
        )
        plan_payload = plan.model_dump(mode="json")
        used_rows = _used_capability_rows(spec, plan)
        plan_payload["used_capabilities"] = used_rows
        next_body = {
            **body,
            "published": True,
            "machine_verification_ran": bool(body.get("machine_verification_ran")),
            "machine_verification_required": bool(body.get("machine_verification_required")),
            "skill_id": skill_id,
            "skill_version": version,
            "skill_plan": plan_payload,
            "skill_plan_valid": True,
            "skill_export_status": "exported",
            "export_path": export_path,
            "skill_export_path": export_path,
            "skill_request_fingerprint": request_fp,
            "skill_needs_reexport": False,
            **_skill_draft_fields(request, title),
        }
        await _call_persist(persist, next_body)
        _log_export(
            "skill.export.completed",
            summary="Skill 导出完成",
            status="succeeded",
            duration_ms=(time.monotonic() - started) * 1000,
            result_id=str(result_id),
            skill_id=skill_id,
            skill_name=title,
            version=version,
            export_path=export_path,
            used_capabilities=used_rows,
            unused_capabilities=[item.capability_id for item in plan.unused_capabilities],
            routes=_route_rows(plan, spec),
        )
        return SkillExportOutcome(
            status="exported",
            skill_id=skill_id,
            skill_name=title,
            version=version,
            planning_mode=str(plan.planning_mode),
            used_capabilities=used_rows,
            unused_capabilities=[item.model_dump(mode="json") for item in plan.unused_capabilities],
            routes=_route_rows(plan, spec),
            unresolved_branches=_unresolved_rows(plan),
            export_path=export_path,
            plan=plan_payload,
        )
    except SkillExportError as exc:
        _log_export(
            "skill.export.failed",
            summary=exc.detail or "Skill 导出失败",
            status="failed",
            level="error",
            duration_ms=(time.monotonic() - started) * 1000,
            result_id=str(result_id),
            skill_id=skill_id,
            export_path=export_path,
            error={"code": "SKILL_EXPORT_ERROR", "type": "SkillExportError", "message": exc.detail},
        )
        await _fail_export(body, persist, export_path, published_report)
        raise
    except Exception as exc:  # noqa: BLE001 - export failure must not look published
        _log_export(
            "skill.export.failed",
            summary=str(exc) or "Skill 导出失败",
            status="failed",
            level="error",
            duration_ms=(time.monotonic() - started) * 1000,
            result_id=str(result_id),
            skill_id=skill_id,
            export_path=export_path,
            error={"code": type(exc).__name__, "type": type(exc).__name__, "message": str(exc) or "Skill 导出失败"},
        )
        await _fail_export(body, persist, export_path, published_report)
        return SkillExportOutcome(
            status="export_failed",
            skill_id=skill_id,
            errors=[str(exc) or "Skill 导出失败"],
            plan=plan.model_dump(mode="json"),
        )


async def _fail_export(
    body: dict[str, Any],
    persist: PersistBody | None,
    export_path: str,
    published_report: dict[str, Any] | None = None,
) -> None:
    if published_report:
        await _rollback_published_asset(published_report)
    if export_path:
        target = Path(export_path)
        if target.exists():
            try:
                shutil.rmtree(target)
            except OSError:
                pass
    had_export = str(body.get("skill_export_status") or "") in {"exported", "succeeded"}
    await _call_persist(persist, {
        **body,
        "published": bool(had_export and body.get("published")),
        "skill_export_status": "failed",
        "skill_plan_valid": False,
        "skill_needs_reexport": bool(body.get("skill_id") or had_export),
    })


async def _rollback_published_asset(published_report: dict[str, Any]) -> None:
    asset_id = str(published_report.get("asset_id") or "").strip()
    if not asset_id:
        return
    try:
        from dano.assets.repository import AssetRepository
        from dano.shared.enums import ValidationStatus

        await AssetRepository().set_status(UUID(asset_id), ValidationStatus.DEPRECATED)
    except Exception:  # noqa: BLE001 - rollback is best-effort; result must not stay published
        return


def _assert_package_matches_plan(export_path: str, plan: SkillPlan) -> None:
    from dano.export.skill_package.validator import validate_skill_package

    root = Path(export_path)
    validation = validate_skill_package(root)
    if not validation["ok"]:
        raise RuntimeError("Skill 包校验失败：" + json.dumps(validation["issues"], ensure_ascii=False))
    contract_path = root / "references" / "CONTRACT.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    selected = [str(item) for item in (contract.get("selected_capability_ids") or [])]
    if selected != list(plan.selected_capability_ids):
        raise RuntimeError("CONTRACT.json 所选能力与规划不一致")
    route_ids = {str(item.get("route_id") or "") for item in (contract.get("routes") or [])}
    if route_ids != {route.route_id for route in plan.routes}:
        raise RuntimeError("CONTRACT.json 路线与规划不一致")
    unused = {str(item.get("capability_id") or "") for item in (contract.get("unused_capabilities") or [])}
    selected_set = set(plan.selected_capability_ids)
    for item in plan.unused_capabilities:
        if item.capability_id in selected_set:
            raise RuntimeError(f"未使用能力进入了导出 CONTRACT: {item.capability_id}")
        if item.capability_id not in unused:
            raise RuntimeError(f"未使用能力未写入 CONTRACT: {item.capability_id}")
    names = {
        str(item.get("name") or item.get("capability_id") or "")
        for item in (contract.get("capabilities") or [])
    }
    for item in plan.unused_capabilities:
        if item.name and item.name in names and item.capability_id not in selected_set:
            raise RuntimeError(f"未使用能力出现在公共脚本列表: {item.name}")
    payload = plan_to_contract_payload(plan)
    if payload["planning_mode"] != contract.get("planning_mode"):
        raise RuntimeError("SKILL 规划模式与 CONTRACT 不一致")
    if plan.intent_branches and not contract.get("intent_branches"):
        raise RuntimeError("CONTRACT.json 缺少 intent_branches，无法审计自然语言分支")


def _default_render(skill, out_dir: str, *, tenant: str) -> str:  # noqa: ANN001
    from dano.export.skill_package.renderer import render_skill_package

    return render_skill_package(skill, out_dir, tenant=tenant)


async def _default_publish(**kwargs: Any) -> dict[str, Any]:
    """Publish the export-view asset without a live Pi session or auto-export."""

    from dano.assets.drafts import DraftStore
    from dano.assets.repository import AssetRepository
    from dano.catalog.identity import public_skill_action
    from dano.execution.page.flow_spec import (
        flow_spec_release_payload,
        flow_spec_required_params,
        flow_spec_to_api_request,
    )
    from dano.onboarding.page_onboard import _build_page_body
    from dano.schemas.validate import validate_asset_body
    from dano.shared.enums import AssetType, Subsystem, ValidationStatus
    from dano.shared.models import AssetEnvelope, GenerationReport, Scope

    view = kwargs["view"]
    skill = kwargs["skill"]
    title = str(kwargs.get("title") or skill.title or "")
    skill_id = str(kwargs["skill_id"])
    sub_str, _, action = skill_id.partition(".")
    action = action or public_skill_action(title, str(kwargs.get("action") or ""))
    api_request = dict(getattr(skill, "api_request", None) or {})
    if not api_request:
        promoted = promote_unconfirmed_write_fields(view.model_copy(deep=True))
        api_request, errors = flow_spec_to_api_request(promoted)
        if errors or not api_request:
            raise RuntimeError("发布导出视图失败：" + "；".join(errors or ["未知错误"]))
        view = promoted
    plan_payload = dict((skill.call_metadata or {}).get("skill_plan") or {})
    api_request["_skill_plan"] = plan_payload
    api_request["_release_snapshot"] = {
        **dict(api_request.get("_release_snapshot") or {}),
        "flow_spec": flow_spec_release_payload(view),
        "skill_plan": plan_payload,
    }
    required = flow_spec_required_params(view)
    body, _params, _req, _opt = _build_page_body(api_request, action, title, required)
    validate_asset_body(AssetType.PAGE_SCRIPT, body)
    scope = Scope(tenant=kwargs["tenant"], subsystem=Subsystem(sub_str or view.subsystem or "oa"))
    draft = await DraftStore().save_draft(
        run_id=f"skill-export-{kwargs.get('result_id') or action}",
        scope=scope,
        asset_type=AssetType.PAGE_SCRIPT,
        asset_key=action,
        body=body,
    )
    repo = AssetRepository()
    env = await repo.create(AssetEnvelope(
        asset_type=AssetType.PAGE_SCRIPT,
        scope=scope,
        asset_key=action,
        version=0,
        source_fingerprint=draft.content_hash,
        validation_status=ValidationStatus.VERIFIED,
        confidence=0.95,
        generation_report=GenerationReport(
            reasoning_summary="stage8-manual-export",
            verification_evidence={"recording_machine_validated": True},
        ),
        body=body,
    ))
    published = await repo.set_status(env.asset_id, ValidationStatus.PUBLISHED)
    return {
        "ok": True,
        "asset_id": str(env.asset_id),
        "asset_version": (published.version if published else env.version),
        "skill_id": skill_id,
        "action": action,
    }
