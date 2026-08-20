"""Manual stage-8 Skill export for one recording result."""

from __future__ import annotations

import inspect
import json
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from dano.execution.page.flow_spec_core.models import FlowSpec
from dano.onboarding.skill_generation.catalog import capability_ref, verified_capability_ids
from dano.onboarding.skill_generation.export_view import build_export_view
from dano.onboarding.skill_generation.models import (
    SkillGenerationRequest,
    SkillPlan,
    generation_request_fingerprint,
)
from dano.onboarding.skill_generation.planner import generate_skill_plan
from dano.onboarding.skill_generation.validate import plan_to_contract_payload

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


def _current_spec(body: dict[str, Any]) -> FlowSpec:
    checkpoint = body.get("stage_seven") if isinstance(body.get("stage_seven"), dict) else {}
    working = checkpoint.get("working_flow_spec")
    raw = working if isinstance(working, dict) and working else body.get("flow_spec")
    if not isinstance(raw, dict):
        raise SkillExportError(409, "录制结果没有可导出的 FlowSpec")
    return FlowSpec.model_validate(raw)


def _stage_seven_status(body: dict[str, Any]) -> str:
    checkpoint = body.get("stage_seven") if isinstance(body.get("stage_seven"), dict) else {}
    return str(body.get("machine_verification_status") or checkpoint.get("status") or "")


def _stage_seven_is_required(
    body: dict[str, Any],
    request: SkillGenerationRequest | None = None,
) -> bool:
    """Switch on → stage 7 is mandatory. Switch off → stage 6 result may export."""

    if request is not None and request.require_stage_seven is False:
        return False
    if request is not None and request.require_stage_seven is True:
        return True
    status = _stage_seven_status(body)
    if status in {"verified", "running", "waiting_operator", "stale", "failed"}:
        return True
    return bool(body.get("machine_verification_required") or body.get("machine_verification_ran"))


def _stage_seven_ready(
    body: dict[str, Any],
    spec: FlowSpec,
    request: SkillGenerationRequest | None = None,
) -> tuple[str, bool]:
    from dano.onboarding.recording_stage_seven import StageSevenStatus, working_fingerprint

    current_fp = working_fingerprint(spec)
    required = _stage_seven_is_required(body, request)
    if not required:
        return current_fp, False
    status = _stage_seven_status(body)
    if status != StageSevenStatus.VERIFIED:
        raise SkillExportError(409, "阶段7未验证通过，不能生成 Skill")
    stored_fp = str(
        body.get("stage_seven_fingerprint")
        or (body.get("stage_seven") or {}).get("working_fingerprint")
        or ""
    )
    if not stored_fp or stored_fp != current_fp:
        raise SkillExportError(409, "阶段7指纹与当前 FlowSpec 不一致，请重新验证后再产出 Skill")
    return stored_fp, True


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
    )


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

    release_spec, candidate = prepare_flow_release_candidate(view)
    api_request, errors = flow_spec_to_api_request(release_spec, _prepared=True)
    if errors or not api_request:
        raise SkillExportError(409, "导出视图无法编译为 Skill 包：" + "；".join(errors or ["未知错误"]))
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
    if not str(request.business_description or "").strip():
        raise SkillExportError(400, "业务描述不能为空")
    if not str(request.out_dir or "").strip():
        raise SkillExportError(400, "导出目录不能为空")
    spec = _current_spec(body)
    fingerprint, stage_seven_required = _stage_seven_ready(body, spec, request)
    if stage_seven_required:
        verified = verified_capability_ids(
            spec,
            stage_seven=body.get("stage_seven") if isinstance(body.get("stage_seven"), dict) else None,
        )
        if not verified:
            raise SkillExportError(409, "阶段7没有已验证能力，不能生成 Skill")
    else:
        verified = {capability_ref(cap) for cap in spec.capabilities if capability_ref(cap)}
        if not verified:
            raise SkillExportError(409, "尚未生成可导出能力，不能生成 Skill")
    request_fp = generation_request_fingerprint(
        result_id=str(result_id),
        stage_seven_fingerprint=fingerprint,
        request=request,
    )
    if str(body.get("skill_request_fingerprint") or "") == request_fp and _already_exported(body):
        plan = body.get("skill_plan") if isinstance(body.get("skill_plan"), dict) else {}
        used = list((plan or {}).get("used_capabilities") or [])
        if not used and plan:
            try:
                used = _used_capability_rows(spec, SkillPlan.model_validate(plan))
            except Exception:  # noqa: BLE001 - idempotent path still returns the stored plan
                used = []
        return SkillExportOutcome(
            status="exported",
            skill_id=str(body.get("skill_id") or ""),
            skill_name=str(request.title or body.get("title") or ""),
            version=int(body.get("skill_version") or 1),
            planning_mode=str((plan or {}).get("planning_mode") or request.planning_mode),
            used_capabilities=used,
            unused_capabilities=list((plan or {}).get("unused_capabilities") or []),
            routes=list((plan or {}).get("routes") or []),
            export_path=str(body.get("export_path") or body.get("skill_export_path") or ""),
            plan=plan,
            idempotent=True,
        )

    await _call_persist(persist, {
        **body,
        "skill_export_status": "generating",
        "skill_plan_valid": False,
    })

    planned = await generate_skill_plan(
        spec,
        request,
        verified_capability_ids=verified,
        source_flow_fingerprint=fingerprint,
        proposer=proposer,
    )
    if planned.status != "planned" or planned.plan is None:
        await _call_persist(persist, {
            **body,
            "skill_export_status": planned.status,
            "skill_plan": planned.plan.model_dump(mode="json") if planned.plan else None,
            "skill_plan_valid": False,
            "published": bool(body.get("published")),
        })
        return SkillExportOutcome(
            status=planned.status,
            clarification_questions=planned.clarification_questions,
            errors=planned.errors,
            plan=planned.plan.model_dump(mode="json") if planned.plan else None,
        )

    plan = planned.plan
    title = str(request.title or body.get("title") or spec.title or "").strip()
    skill_id = _stable_skill_id(body, title)
    view = build_export_view(spec, plan.selected_capability_ids)
    if build_skill is not None:
        skill = build_skill(view, tenant=tenant, skill_id=skill_id, title=title, plan=plan)
    else:
        try:
            skill = build_export_skill_spec(
                view, tenant=tenant, skill_id=skill_id, title=title, plan=plan,
            )
        except SkillExportError:
            if render is None:
                raise
            skill = _minimal_export_skill(
                view, tenant=tenant, skill_id=skill_id, title=title, plan=plan,
            )
    out_dir = str(request.out_dir).strip()
    render_fn = render or _default_render
    publish_fn = publish or _default_publish
    published_report: dict[str, Any] | None = None
    export_path = ""
    try:
        slug = render_fn(skill, out_dir, tenant=tenant)
        export_path = str(Path(out_dir) / slug)
        _assert_package_matches_plan(export_path, plan)
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
        plan_payload = plan.model_dump(mode="json")
        used_rows = _used_capability_rows(spec, plan)
        plan_payload["used_capabilities"] = used_rows
        next_body = {
            **body,
            "published": True,
            "machine_verification_ran": True if stage_seven_required else bool(body.get("machine_verification_ran")),
            "machine_verification_required": bool(stage_seven_required or body.get("machine_verification_required")),
            "skill_id": skill_id,
            "skill_version": version,
            "skill_plan": plan_payload,
            "skill_plan_valid": True,
            "skill_export_status": "exported",
            "export_path": export_path,
            "skill_export_path": export_path,
            "skill_request_fingerprint": request_fp,
            "skill_needs_reexport": False,
        }
        await _call_persist(persist, next_body)
        return SkillExportOutcome(
            status="exported",
            skill_id=skill_id,
            skill_name=title,
            version=version,
            planning_mode=str(plan.planning_mode),
            used_capabilities=used_rows,
            unused_capabilities=[item.model_dump(mode="json") for item in plan.unused_capabilities],
            routes=[route.model_dump(mode="json") for route in plan.routes],
            export_path=export_path,
            plan=plan_payload,
        )
    except SkillExportError:
        await _fail_export(body, persist, export_path, published_report)
        raise
    except Exception as exc:  # noqa: BLE001 - export failure must not look published
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
    api_request, errors = flow_spec_to_api_request(view)
    if errors or not api_request:
        raise RuntimeError("发布导出视图失败：" + "；".join(errors or ["未知错误"]))
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
