"""Manual stage-8 Skill export for one recording result."""

from __future__ import annotations

import inspect
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import structlog
from pydantic import BaseModel, Field, ValidationError

from dano.execution.page.flow_spec_core.models import FlowSpec
from dano.onboarding.skill_generation.catalog import capability_ref
from dano.onboarding.skill_generation.export_view import (
    build_export_view,
)
from dano.onboarding.skill_generation.models import (
    SkillGenerationRequest,
    SkillPlan,
    generation_request_fingerprint,
)
from dano.onboarding.skill_generation.planner import generate_skill_plan

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


def _skill_draft_fields(
    request: SkillGenerationRequest,
    title: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    description = str(request.business_description or "").strip()
    stored = str(body.get("skill_export_description") or "").strip()
    stored_origin = str(body.get("skill_export_description_origin") or "").strip()
    generated = ""
    if not stored:
        from dano.onboarding.recording_results import (
            generate_business_description,
            latest_recording_spec,
        )

        generated = generate_business_description(latest_recording_spec(body))
    origin = (
        "generated"
        if stored_origin != "manual"
        and description
        and description in {stored, generated}
        else "manual"
    )
    return {
        "skill_export_title": title,
        "skill_export_description": description,
        "skill_export_description_origin": origin,
        "skill_export_description_fingerprint": str(body.get("fingerprint") or ""),
        "skill_export_description_stale": False,
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
    try:
        spec = FlowSpec.model_validate(raw)
    except ValidationError as exc:
        raise SkillExportError(409, "录制结果 FlowSpec 无法用于导出") from exc
    from dano.execution.page.flow_spec_core.request_contract import hydrate_recorded_write_bodies

    recording_id = str(body.get("recording_id") or (spec.meta or {}).get("recording_id") or "")
    if recording_id and not (spec.meta or {}).get("recording_id"):
        spec.meta = {**(spec.meta or {}), "recording_id": recording_id}
    return hydrate_recorded_write_bodies(spec, recording_id=recording_id)


def _stable_skill_id(body: dict[str, Any], title: str) -> str:
    from dano.catalog.identity import is_generated_action_id, public_skill_action

    subsystem = str(body.get("subsystem") or "oa").strip() or "oa"
    recording_action = str(body.get("action") or "").strip()
    if recording_action and is_generated_action_id(recording_action):
        return f"{subsystem}.{recording_action}"
    existing = str(body.get("skill_id") or "").strip()
    if existing:
        return existing
    return f"{subsystem}.{public_skill_action(title, recording_action)}"


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


def _capability_compilation_view(view: FlowSpec) -> FlowSpec:
    """Add only the call nodes already declared by capability request refs."""

    from dano.execution.page.capability_refs import (
        _capability_declared_step_ids,
        _capability_node_step_ids,
    )

    compiled = view.model_copy(deep=True)
    for capability in compiled.capabilities:
        existing = set(_capability_node_step_ids(capability))
        nodes = list(capability.nodes or [])
        for step_id in _capability_declared_step_ids(capability):
            if step_id in existing:
                continue
            nodes.append({"type": "call", "step_id": step_id})
            existing.add(step_id)
        capability.nodes = nodes
    return compiled


def _restore_declared_capability_selects(api_request: dict[str, Any], view: FlowSpec) -> dict[str, Any]:
    """Keep explicit picker bindings when the capability schema declares the same source."""

    def normalized_endpoint(value: Any) -> str:
        raw = str(value or "").split("?", 1)[0]
        parsed = urlparse(raw)
        return parsed.path if parsed.scheme and parsed.netloc else raw

    def source_endpoint(field: dict[str, Any]) -> str:
        for key in ("x-dano-option-source", "x-options-source-meta", "dataSource"):
            source = field.get(key)
            if isinstance(source, dict):
                endpoint = source.get("source_url") or source.get("endpoint") or source.get("url")
                if endpoint:
                    return normalized_endpoint(endpoint)
        return ""

    steps_by_id = {str(step.step_id): step for step in view.steps}
    source_capabilities: dict[str, Any] = {}
    for capability in view.capabilities:
        for key in (capability.capability_id, capability.name):
            if key:
                source_capabilities[str(key)] = capability

    packed = dict(api_request)
    restored: list[dict[str, Any]] = []
    for raw in packed.get("capabilities") or []:
        if not isinstance(raw, dict):
            continue
        capability = dict(raw)
        source_capability = source_capabilities.get(str(capability.get("capability_id") or ""))
        if source_capability is None:
            source_capability = source_capabilities.get(str(capability.get("name") or ""))
        execution = dict(capability.get("execution_contract") or {})
        compiled_steps = [
            dict(step) for step in execution.get("steps") or [] if isinstance(step, dict)
        ]
        if source_capability is not None and compiled_steps:
            properties = (
                source_capability.input_schema.get("properties")
                if isinstance(source_capability.input_schema, dict)
                and isinstance(source_capability.input_schema.get("properties"), dict)
                else {}
            )
            execute_ids = {
                str(ref.step_id)
                for ref in source_capability.request_refs or []
                if str(ref.usage or "execute") in {"execute", "preflight"} and ref.step_id
            }
            execute_ids.update(str(item) for item in source_capability.step_ids or [] if str(item))
            declared_by_step: dict[str, list[dict[str, Any]]] = {}
            for step_id in execute_ids:
                source_step = steps_by_id.get(step_id)
                if source_step is None:
                    continue
                for binding in source_step.selects or []:
                    name = str(binding.param or "")
                    field = properties.get(name)
                    endpoint = source_endpoint(field) if isinstance(field, dict) else ""
                    binding_endpoint = normalized_endpoint(binding.source_url)
                    if not endpoint or endpoint != binding_endpoint:
                        continue
                    item = binding.model_dump(exclude_none=True)
                    for key in ("actor", "confidence", "verification_id", "enum_confirmed"):
                        item.pop(key, None)
                    if not item.get("field_projections"):
                        item.pop("field_projections", None)
                    declared_by_step.setdefault(step_id, []).append(item)
            for step in compiled_steps:
                additions = declared_by_step.get(str(step.get("step_id") or ""), [])
                if not additions:
                    continue
                current = [item for item in step.get("selects") or [] if isinstance(item, dict)]
                for addition in additions:
                    current = [
                        item for item in current
                        if not (
                            str(item.get("param") or "") == str(addition.get("param") or "")
                            and str(item.get("path") or "") == str(addition.get("path") or "")
                        )
                    ]
                    current.append(addition)
                step["selects"] = current
            execution["steps"] = compiled_steps
            capability["execution_contract"] = execution
        restored.append(capability)
    packed["capabilities"] = restored
    return packed


def build_export_skill_spec(
    view: FlowSpec,
    *,
    tenant: str,
    skill_id: str,
    title: str,
    plan: SkillPlan,
) -> Any:
    from dano.execution.page.flow_spec import flow_spec_release_payload, flow_spec_to_api_request
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import RiskLevel, Subsystem

    from dano.export.skill_package.renderer import restore_compiled_capability_schemas

    # Stage 8 consumes the selected capability view directly. Running the
    # publish preparation pipeline here would reclassify sources and rebuild
    # schemas a second time, so the exported Skill could diverge from PI.
    api_request, errors = flow_spec_to_api_request(
        _capability_compilation_view(view),
        _prepared=True,
        _embed_capability_steps=True,
    )
    if not api_request:
        raise SkillExportError(409, "导出视图无法编译为 Skill 包：" + "；".join(errors or ["未知错误"]))
    api_request = restore_compiled_capability_schemas(api_request, view)
    api_request = _restore_declared_capability_selects(api_request, view)
    plan_payload = plan.model_dump(mode="json")
    api_request["_release_snapshot"] = {
        "flow_spec": flow_spec_release_payload(view),
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
    if not str(request.out_dir or "").strip():
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
    await _call_persist(persist, {
        **body,
        "skill_export_status": "generating",
        "skill_plan_valid": False,
        **_skill_draft_fields(request, title, body),
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
            **_skill_draft_fields(request, title, body),
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
        skill = build_export_skill_spec(
            view, tenant=tenant, skill_id=skill_id, title=title, plan=plan,
        )
    out_dir = str(request.out_dir).strip()
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
        _log_export(
            "skill.export.rendered",
            summary="Skill 包已写出",
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
            **_skill_draft_fields(request, title, body),
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
        await _fail_export(body, persist, published_report)
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
        await _fail_export(body, persist, published_report)
        return SkillExportOutcome(
            status="export_failed",
            skill_id=skill_id,
            errors=[str(exc) or "Skill 导出失败"],
            plan=plan.model_dump(mode="json"),
        )


async def _fail_export(
    body: dict[str, Any],
    persist: PersistBody | None,
    published_report: dict[str, Any] | None = None,
) -> None:
    if published_report:
        await _rollback_published_asset(published_report)
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
    )
    from dano.onboarding.page_onboard import _build_page_body
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
        raise RuntimeError("导出的 Skill 缺少已编译请求")
    plan_payload = dict((skill.call_metadata or {}).get("skill_plan") or {})
    api_request["_skill_plan"] = plan_payload
    api_request["_release_snapshot"] = {
        **dict(api_request.get("_release_snapshot") or {}),
        "flow_spec": flow_spec_release_payload(view),
        "skill_plan": plan_payload,
    }
    required = flow_spec_required_params(view)
    body, _params, _req, _opt = _build_page_body(api_request, action, title, required)
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
