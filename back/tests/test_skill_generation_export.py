"""Stage-8 manual Skill export: gates, idempotency, failure, and package planning."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from dano.export.skill_package.renderer import (
    _capabilities_md,
    _capability_plans,
    _contract_planning_fields,
    _fallback_skill_md,
    _filter_plans_for_export,
    _input_forms_bundle,
    _input_forms_md,
    _options_md,
    _route_file_md,
    _skill_plan_payload,
    package_slug,
)
from dano.export.skill_package.validator import _check_skill, validate_skill_package
from dano.onboarding.skill_generation.validate import HANDBOOK_BAN_MARKERS
from dano.onboarding.recording_stage_seven import working_fingerprint
from dano.onboarding.recording_results import recording_skill_lifecycle
from dano.onboarding.skill_generation.export import (
    SkillExportError,
    _default_render,
    _route_rows,
    export_recording_skill,
)
from stage8_sale_order_fixture import (
    SALE_ORDER_EXPLICIT_COMBOS,
    SALE_ORDER_TITLES,
    combination_routes,
    combo_pair,
    route_has_human_checkpoint,
    sale_order_request,
    sale_order_spec,
    sale_order_verified_ids,
)
from dano.execution.page.flow_spec_core.models import ParamField
from dano.onboarding.skill_generation.export_view import build_export_view
from dano.onboarding.skill_generation.models import PlanningMode, SkillGenerationRequest
from dano.onboarding.skill_generation.planner import propose_deterministic_plan

from test_skill_generation_plan import VERIFIED, _cap, _three_cap_spec
from dano.execution.page.flow_spec import FlowSpec, FlowStep


def _verified_body(spec, *, extra: dict | None = None) -> dict:
    payload = spec.model_dump(mode="json")
    fingerprint = working_fingerprint(spec)
    body = {
        "title": spec.title or "请假办理",
        "subsystem": "oa",
        "action": "action_deadbeef",
        "flow_spec": payload,
        "machine_verification_status": "verified",
        "machine_verification_ran": True,
        "stage_seven_fingerprint": fingerprint,
        "published": False,
        "stage_seven": {
            "status": "verified",
            "working_fingerprint": fingerprint,
            "working_flow_spec": payload,
            "verdict": {"callable_capability_ids": sorted(VERIFIED)},
        },
    }
    if extra:
        body.update(extra)
    return body


def _request(**kwargs) -> SkillGenerationRequest:
    payload = {
        "title": "请假办理",
        "business_description": "用户可以查询待办记录，也可以查询后选择一条记录进行提交；不要使用选项字典。",
        "planning_mode": PlanningMode.FIXED,
        "example_requests": ["帮我查待办并提交一条"],
        "success_criteria": "选中记录已提交",
        "out_dir": str(kwargs.pop("out_dir", "")),
    }
    payload.update(kwargs)
    return SkillGenerationRequest.model_validate(payload)


def _write_valid_package(root: Path, skill, plan: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    scripts = root / "scripts"
    references = root / "references"
    scripts.mkdir(parents=True, exist_ok=True)
    references.mkdir(parents=True, exist_ok=True)
    selected = [str(item) for item in (plan.get("selected_capability_ids") or []) if str(item)]
    raw_caps = list(getattr(skill, "capabilities", None) or skill.api_request.get("capabilities") or [])
    caps = [
        cap for cap in raw_caps
        if not selected
        or (cap.get("capability_id") or cap.get("name")) in selected
        or cap.get("name") in selected
    ]
    if not caps and raw_caps:
        caps = list(raw_caps)
    plans = [
        {
            "name": cap.get("name") or cap.get("capability_id"),
            "capability_id": cap.get("capability_id") or "",
            "title": cap.get("title") or cap.get("name"),
            "kind": cap.get("kind") or "operation",
            "script": str(cap.get("name") or cap.get("capability_id")),
            "requires_confirmation": bool(cap.get("requires_human_confirm")),
            "requires_verify": bool(cap.get("requires_human_confirm")),
            "input_schema": cap.get("input_schema") or {"type": "object", "properties": {}},
            "output_schema": cap.get("output_schema") or {"type": "object"},
            "preconditions": [],
            "caller_responsibilities": [],
            "skill_responsibilities": [],
        }
        for cap in caps
    ]
    skill_md = _fallback_skill_md(skill, package_slug(skill.skill_id), plans, None)
    (root / "SKILL.md").write_text(skill_md, encoding="utf-8")
    (references / "CAPABILITIES.md").write_text(_capabilities_md(skill, plans), encoding="utf-8")
    (references / "OPTIONS.md").write_text(_options_md(plans), encoding="utf-8")
    forms_text, form_files = _input_forms_bundle(plans)
    (references / "INPUT_FORMS.md").write_text(forms_text, encoding="utf-8")
    for rel, content in form_files.items():
        target = references / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    routes_dir = references / "routes"
    for route in plan.get("routes") or []:
        sequence = route.get("capability_sequence") or []
        route_id = str(route.get("route_id") or "").strip()
        if len(sequence) > 1 and route_id:
            routes_dir.mkdir(parents=True, exist_ok=True)
            (routes_dir / f"{route_id}.md").write_text(_route_file_md(route, plans), encoding="utf-8")
    contract = {
        "protocol": "dano.skill_package.contract.v1",
        "skill": {"id": skill.skill_id, "name": package_slug(skill.skill_id)},
        "capabilities": [
            {
                "name": item["name"],
                "capability_id": item["capability_id"],
                "title": item.get("title") or item["name"],
                "script": f"scripts/{item['script']}.py",
                "verify_script": f"scripts/verify_{item['script']}.py",
                "input_schema": item.get("input_schema") or {"type": "object", "properties": {}},
            }
            for item in plans
        ],
        "planning_mode": plan.get("planning_mode"),
        "selected_capability_ids": selected,
        "routes": plan.get("routes") or [],
        "bindings": [
            binding
            for route in (plan.get("routes") or [])
            for binding in (route.get("bindings") or [])
        ],
        "unused_capabilities": plan.get("unused_capabilities") or [],
        "source_flow_fingerprint": plan.get("source_flow_fingerprint") or "",
        "intent_branches": plan.get("intent_branches") or [],
        "composition_summary": plan.get("composition_summary") or "",
        "composition_notes": plan.get("composition_notes") or [],
    }
    (references / "CONTRACT.json").write_text(
        json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    script = (
        "import json, sys\n"
        "if '--help' in sys.argv:\n"
        "    print('ok')\n"
        "    raise SystemExit(0)\n"
        "print(json.dumps({'ok': True, 'status': 'succeeded'}))\n"
    )
    (scripts / "client.py").write_text(script, encoding="utf-8")
    for item in plans:
        (scripts / f"{item['script']}.py").write_text(script, encoding="utf-8")
        (scripts / f"verify_{item['script']}.py").write_text(script, encoding="utf-8")


def _render_valid(skill, out_dir: str, *, tenant: str) -> str:
    del tenant
    slug = package_slug(skill.skill_id)
    _write_valid_package(Path(out_dir) / slug, skill, _skill_plan_payload(skill))
    return slug


async def _ok_publish(**_kwargs) -> dict:
    return {"ok": True, "asset_id": str(uuid4()), "asset_version": 1}


async def _deterministic_proposer(spec, request, verified_ids, fingerprint):
    return propose_deterministic_plan(spec, request, verified_ids, fingerprint)


@pytest.mark.asyncio
async def test_manual_export_does_not_require_stage_seven(tmp_path: Path) -> None:
    spec = _three_cap_spec()
    body = _verified_body(spec)
    body["machine_verification_status"] = "running"
    body["stage_seven"]["status"] = "running"
    stored: list[dict] = []

    outcome = await export_recording_skill(
        result_id=uuid4(),
        body=body,
        tenant="tenant",
        request=_request(out_dir=str(tmp_path), require_stage_seven=True),
        persist=stored.append,
        publish=_ok_publish,
        render=_render_valid,
        proposer=_deterministic_proposer,
    )
    assert outcome.status == "exported"
    assert outcome.plan
    assert stored[-1].get("published") is True


@pytest.mark.asyncio
async def test_manual_export_skips_stage_seven_when_switch_off(tmp_path: Path) -> None:
    spec = _three_cap_spec()
    body = {
        "title": spec.title or "请假办理",
        "subsystem": "oa",
        "action": "action_skip_stage7",
        "flow_spec": spec.model_dump(mode="json"),
        "machine_verification_status": "",
        "machine_verification_ran": False,
        "machine_verification_required": False,
        "published": False,
    }
    outcome = await export_recording_skill(
        result_id=uuid4(),
        body=body,
        tenant="tenant",
        request=_request(out_dir=str(tmp_path), require_stage_seven=False),
        persist=lambda _body: None,
        publish=_ok_publish,
        render=_render_valid,
        proposer=_deterministic_proposer,
    )
    assert outcome.status == "exported"
    assert outcome.plan
    assert outcome.used_capabilities


@pytest.mark.asyncio
async def test_manual_export_requires_business_description(tmp_path: Path) -> None:
    spec = _three_cap_spec()
    with pytest.raises(SkillExportError) as exc:
        await export_recording_skill(
            result_id=uuid4(),
            body=_verified_body(spec),
            tenant="tenant",
            request=_request(business_description="   ", out_dir=str(tmp_path)),
            publish=_ok_publish,
            render=_render_valid,
            proposer=_deterministic_proposer,
        )
    assert exc.value.status_code == 400
    assert "业务描述" in exc.value.detail


@pytest.mark.asyncio
async def test_export_failure_does_not_mark_published(tmp_path: Path) -> None:
    spec = _three_cap_spec()
    stored: list[dict] = []

    async def boom(**_kwargs):
        raise RuntimeError("发布失败")

    outcome = await export_recording_skill(
        result_id=uuid4(),
        body=_verified_body(spec),
        tenant="tenant",
        request=_request(out_dir=str(tmp_path)),
        persist=stored.append,
        publish=boom,
        render=_render_valid,
        proposer=_deterministic_proposer,
    )
    assert outcome.status == "export_failed"
    assert stored[-1]["published"] is False
    assert stored[-1]["skill_export_status"] == "failed"
    assert recording_skill_lifecycle(stored[-1]) == "verified_not_exported"
    assert not list(tmp_path.glob("dano-*-package"))


@pytest.mark.asyncio
async def test_repeated_identical_export_is_idempotent(tmp_path: Path) -> None:
    spec = _three_cap_spec()
    result_id = uuid4()
    publishes = {"count": 0}

    async def publish(**_kwargs):
        publishes["count"] += 1
        return {"ok": True, "asset_id": str(uuid4()), "asset_version": 1}

    first = await export_recording_skill(
        result_id=result_id,
        body=_verified_body(spec),
        tenant="tenant",
        request=_request(out_dir=str(tmp_path)),
        persist=lambda _body: None,
        publish=publish,
        render=_render_valid,
        proposer=_deterministic_proposer,
    )
    assert first.status == "exported"
    assert first.idempotent is False
    assert publishes["count"] == 1

    next_body = _verified_body(spec, extra={
        "published": True,
        "skill_id": first.skill_id,
        "skill_version": first.version,
        "skill_plan": first.plan,
        "skill_export_status": "exported",
        "export_path": first.export_path,
        "skill_request_fingerprint": first.plan and None,
    })
    from dano.onboarding.skill_generation.models import generation_request_fingerprint

    next_body["skill_request_fingerprint"] = generation_request_fingerprint(
        result_id=str(result_id),
        stage_seven_fingerprint=next_body["stage_seven_fingerprint"],
        request=_request(out_dir=str(tmp_path)),
    )
    second = await export_recording_skill(
        result_id=result_id,
        body=next_body,
        tenant="tenant",
        request=_request(out_dir=str(tmp_path)),
        persist=lambda _body: None,
        publish=publish,
        render=_render_valid,
        proposer=_deterministic_proposer,
    )
    assert second.status == "exported"
    assert second.idempotent is True
    assert second.skill_id == first.skill_id
    assert publishes["count"] == 1
    assert second.used_capabilities
    assert {item.get("capability_id") for item in second.used_capabilities} >= {"cap_query", "cap_submit"}


@pytest.mark.asyncio
async def test_reexport_deletes_previous_package_and_stale_stage(tmp_path: Path) -> None:
    spec = _three_cap_spec()
    first = await export_recording_skill(
        result_id=uuid4(),
        body=_verified_body(spec),
        tenant="tenant",
        request=_request(out_dir=str(tmp_path), business_description="查询待办并提交一条"),
        persist=lambda _body: None,
        publish=_ok_publish,
        render=_render_valid,
        proposer=_deterministic_proposer,
    )
    assert first.status == "exported"
    assert first.export_path
    old = Path(first.export_path)
    assert old.is_dir()
    (old / "STALE.txt").write_text("old-output", encoding="utf-8")
    stale_stage = old.parent / f".{old.name}-leftover"
    stale_stage.mkdir()
    (stale_stage / "tmp").write_text("tmp", encoding="utf-8")

    second = await export_recording_skill(
        result_id=uuid4(),
        body=_verified_body(spec, extra={"skill_id": first.skill_id, "export_path": first.export_path}),
        tenant="tenant",
        request=_request(out_dir=str(tmp_path), business_description="只查询待办，不要提交"),
        persist=lambda _body: None,
        publish=_ok_publish,
        render=_render_valid,
        proposer=_deterministic_proposer,
    )
    assert second.status == "exported"
    assert second.idempotent is False
    assert Path(second.export_path).is_dir()
    assert not (Path(second.export_path) / "STALE.txt").exists()
    assert not stale_stage.exists()


@pytest.mark.asyncio
async def test_stale_package_is_rewritten_instead_of_idempotent(tmp_path: Path) -> None:
    spec = _three_cap_spec()
    result_id = uuid4()
    request = _request(out_dir=str(tmp_path))
    first = await export_recording_skill(
        result_id=result_id,
        body=_verified_body(spec),
        tenant="tenant",
        request=request,
        persist=lambda _body: None,
        publish=_ok_publish,
        render=_render_valid,
        proposer=_deterministic_proposer,
    )
    assert first.status == "exported"
    assert first.export_path
    export_root = Path(first.export_path)
    assert export_root.is_dir()
    (export_root / "SKILL.md").write_text(
        "---\nname: stale\ndescription: old\n---\n# stale\n",
        encoding="utf-8",
    )
    from dano.onboarding.skill_generation.models import generation_request_fingerprint

    next_body = _verified_body(spec, extra={
        "published": True,
        "skill_id": first.skill_id,
        "skill_version": first.version,
        "skill_plan": first.plan,
        "skill_export_status": "exported",
        "export_path": first.export_path,
    })
    next_body["skill_request_fingerprint"] = generation_request_fingerprint(
        result_id=str(result_id),
        stage_seven_fingerprint=next_body["stage_seven_fingerprint"],
        request=request,
    )
    second = await export_recording_skill(
        result_id=result_id,
        body=next_body,
        tenant="tenant",
        request=request,
        persist=lambda _body: None,
        publish=_ok_publish,
        render=_render_valid,
        proposer=_deterministic_proposer,
    )
    assert second.status == "exported"
    assert second.idempotent is False
    text = (Path(second.export_path) / "SKILL.md").read_text(encoding="utf-8")
    assert "## 适用场景" in text
    assert "查询待办" in text or "提交请假" in text or "query_leave" in text


@pytest.mark.asyncio
async def test_incomplete_relation_export_uses_user_inputs(tmp_path: Path) -> None:
    spec = _three_cap_spec(confirmed_query_submit=False, confirmed_option_submit=False)
    stored: list[dict] = []
    outcome = await export_recording_skill(
        result_id=uuid4(),
        body=_verified_body(spec),
        tenant="tenant",
        request=_request(
            out_dir=str(tmp_path),
            planning_mode=PlanningMode.DYNAMIC,
            business_description="用户可以查询待办记录，也可以查询后选择一条记录进行提交。",
        ),
        persist=stored.append,
        publish=_ok_publish,
        render=_render_valid,
        proposer=_deterministic_proposer,
    )
    assert outcome.status == "exported"
    assert outcome.export_path
    assert stored[-1]["published"] is True
    routes = (outcome.plan or {}).get("routes") or []
    sequences = {tuple(route.get("capability_sequence") or []) for route in routes}
    assert ("cap_query",) in sequences
    combo = next(route for route in routes if len(route.get("capability_sequence") or []) > 1)
    assert not combo.get("bindings")
    assert combo.get("checkpoints") or any(step.get("checkpoint") for step in (combo.get("steps") or []))
    write = next(route for route in routes if (route.get("capability_sequence") or [""])[0] == "cap_submit")
    assert not write.get("bindings")
    assert "id" in (write.get("required_user_inputs") or [])


@pytest.mark.asyncio
async def test_stage_six_query_hint_exports_without_stage_seven(tmp_path: Path) -> None:
    spec = _three_cap_spec(confirmed_query_submit=False, confirmed_option_submit=False)
    body = {
        "title": spec.title or "点狮ERP销售订单操作能力录制",
        "subsystem": "oa",
        "action": "action_stage6_only",
        "flow_spec": spec.model_dump(mode="json"),
        "published": False,
    }
    outcome = await export_recording_skill(
        result_id=uuid4(),
        body=body,
        tenant="tenant",
        request=_request(
            out_dir=str(tmp_path),
            planning_mode=PlanningMode.DYNAMIC,
            title="点狮ERP销售订单操作能力录制",
            business_description="用户可以查询待办记录，也可以查询后选择一条记录进行提交。",
        ),
        persist=lambda _body: None,
        publish=_ok_publish,
        render=_render_valid,
        proposer=_deterministic_proposer,
    )
    assert outcome.status == "exported"
    assert not outcome.clarification_questions
    assert outcome.used_capabilities


@pytest.mark.asyncio
async def test_export_ignores_stage_seven_working_spec(tmp_path: Path) -> None:
    spec = _three_cap_spec()
    working = spec.model_copy(deep=True)
    working.title = "阶段7工作副本不应被导出"
    working.capabilities = list(working.capabilities) + [
        spec.capabilities[0].model_copy(update={"capability_id": "cap_stage7_only", "name": "stage7_only"}),
    ]
    body = _verified_body(spec)
    body["stage_seven"]["working_flow_spec"] = working.model_dump(mode="json")
    outcome = await export_recording_skill(
        result_id=uuid4(),
        body=body,
        tenant="tenant",
        request=_request(out_dir=str(tmp_path)),
        persist=lambda _body: None,
        publish=_ok_publish,
        render=_render_valid,
        proposer=_deterministic_proposer,
    )
    assert outcome.status == "exported"
    used = {item.get("capability_id") for item in outcome.used_capabilities}
    assert "cap_stage7_only" not in used


@pytest.mark.asyncio
async def test_flow_change_reexports_from_stage_six_spec(tmp_path: Path) -> None:
    spec = _three_cap_spec()
    result_id = uuid4()
    first = await export_recording_skill(
        result_id=result_id,
        body=_verified_body(spec),
        tenant="tenant",
        request=_request(out_dir=str(tmp_path)),
        persist=lambda _body: None,
        publish=_ok_publish,
        render=_render_valid,
        proposer=_deterministic_proposer,
    )
    changed = spec.model_copy(deep=True)
    changed.title = "请假办理-已改"
    body = _verified_body(changed, extra={
        "published": True,
        "skill_id": first.skill_id,
        "skill_version": first.version,
        "skill_plan": first.plan,
        "skill_export_status": "exported",
        "export_path": first.export_path,
        "skill_request_fingerprint": "stale-fingerprint",
        "stage_seven_fingerprint": "old-fp",
    })
    body["stage_seven"]["working_fingerprint"] = "old-fp"
    second = await export_recording_skill(
        result_id=result_id,
        body=body,
        tenant="tenant",
        request=_request(out_dir=str(tmp_path)),
        persist=lambda _body: None,
        publish=_ok_publish,
        render=_render_valid,
        proposer=_deterministic_proposer,
    )
    assert second.status == "exported"
    assert second.idempotent is False
    assert second.plan
    assert second.plan.get("source_flow_fingerprint") != "old-fp"


@pytest.mark.asyncio
async def test_generated_package_contains_only_selected_capabilities(tmp_path: Path) -> None:
    spec = _three_cap_spec()
    outcome = await export_recording_skill(
        result_id=uuid4(),
        body=_verified_body(spec),
        tenant="tenant",
        request=_request(out_dir=str(tmp_path)),
        persist=lambda _body: None,
        publish=_ok_publish,
        render=_render_valid,
        proposer=_deterministic_proposer,
    )
    assert outcome.status == "exported"
    assert "cap_query" in outcome.plan["selected_capability_ids"]
    assert "cap_submit" in outcome.plan["selected_capability_ids"]
    unused = {item["capability_id"] for item in outcome.unused_capabilities}
    assert unused == {"cap_option"}
    contract = json.loads(
        (Path(outcome.export_path) / "references" / "CONTRACT.json").read_text(encoding="utf-8")
    )
    packed = {item.get("capability_id") or item.get("name") for item in contract["capabilities"]}
    assert "cap_option" not in packed
    assert "query_leave_options" not in packed
    assert contract["unused_capabilities"]
    view = build_export_view(spec, outcome.plan["selected_capability_ids"])
    assert {cap.capability_id for cap in view.capabilities} == {"cap_query", "cap_submit"}
    assert spec.capabilities[1].capability_id == "cap_option"


def test_promote_unconfirmed_write_fields_for_export() -> None:
    spec = _three_cap_spec()
    spec.meta["stage_1_6_contract_version"] = 2
    spec.steps[2].name = "POST_create"
    spec.steps[2].params = [
        ParamField(
            path="body.items[0].productId",
            key="productId",
            value="P001",
            category="internal",
            source_kind="unknown",
            exposed_to_user=False,
        )
    ]
    spec.capabilities[2].step_ids = ["s3"]
    view = build_export_view(spec, ["cap_submit"])
    param = next(item for item in view.steps[0].params if item.key == "productId")
    assert param.source_kind == "user_input"
    assert param.exposed_to_user is True
    assert param.category == "user_param"
    original = spec.steps[2].params[0]
    assert original.source_kind == "unknown"
    assert original.exposed_to_user is False


@pytest.mark.asyncio
async def test_unconfirmed_write_fields_do_not_block_export(tmp_path: Path) -> None:
    spec = _three_cap_spec()
    spec.meta["stage_1_6_contract_version"] = 2
    spec.steps[2].name = "POST_create"
    spec.steps[2].params = [
        ParamField(
            path="body.items[0].productId",
            key="productId",
            value="P001",
            category="internal",
            source_kind="unknown",
            exposed_to_user=False,
        )
    ]
    spec.capabilities[2].step_ids = ["s3"]
    stored: list[dict] = []
    outcome = await export_recording_skill(
        result_id=uuid4(),
        body=_verified_body(spec),
        tenant="tenant",
        request=_request(
            out_dir=str(tmp_path),
            title="点狮ERP销售订单操作能力录制",
            business_description="用户可以查询待办记录，也可以查询后选择一条记录进行提交。",
        ),
        persist=stored.append,
        publish=_ok_publish,
        render=_render_valid,
        proposer=_deterministic_proposer,
    )
    assert outcome.status == "exported"
    assert stored[-1]["skill_export_title"] == "点狮ERP销售订单操作能力录制"
    assert "查询待办记录" in stored[-1]["skill_export_description"]


def test_existing_single_capability_package_still_works(tmp_path: Path) -> None:
    skill = SimpleNamespace(skill_id="oa.query_leave", title="查询请假", action="query_leave", call_metadata={}, api_request={})
    plans = [{
        "name": "query_leave",
        "title": "查询请假",
        "script": "query_leave",
        "requires_confirmation": False,
        "requires_verify": False,
        "input_schema": {"type": "object", "properties": {}, "required": []},
    }]
    skill_md = _fallback_skill_md(skill, "dano-oa-query-leave-package", plans, None)
    issues: list[dict] = []
    _check_skill(Path("SKILL.md"), skill_md, issues)
    assert issues == []
    root = tmp_path / "legacy"
    _write_valid_package(
        root,
        SimpleNamespace(
            skill_id="oa.query_leave",
            title="查询请假",
            action="query_leave",
            call_metadata={},
            api_request={"capabilities": [{"capability_id": "cap_query", "name": "query_leave", "title": "查询"}]},
            capabilities=[{"capability_id": "cap_query", "name": "query_leave", "title": "查询"}],
        ),
        {
            "planning_mode": "",
            "selected_capability_ids": [],
            "routes": [],
            "unused_capabilities": [],
        },
    )
    # Strip planning fields so this looks like a pre-stage-8 package.
    contract_path = root / "references" / "CONTRACT.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    for key in ("planning_mode", "selected_capability_ids", "routes", "bindings", "unused_capabilities", "source_flow_fingerprint"):
        contract.pop(key, None)
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    (root / "SKILL.md").write_text(skill_md, encoding="utf-8")
    (root / "scripts" / "query_leave.py").write_text(
        "import json,sys\n"
        "if '--help' in sys.argv:\n print('ok'); raise SystemExit(0)\n"
        "print(json.dumps({'ok': True}))\n",
        encoding="utf-8",
    )
    (root / "scripts" / "verify_query_leave.py").write_text(
        (root / "scripts" / "query_leave.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    result = validate_skill_package(root)
    assert result["ok"], result["issues"]


def test_generated_skill_contains_single_and_multi_capability_examples() -> None:
    spec = _three_cap_spec()
    request = _request(out_dir="ignored", planning_mode=PlanningMode.DYNAMIC, business_description=(
        "用户可以只查询待办，也可以直接提交，也可以先查询再提交；提交字段也可从选项中选择。"
    ))
    plan = propose_deterministic_plan(spec, request, VERIFIED, "fp-dyn")
    skill = SimpleNamespace(
        skill_id="oa.leave",
        title="请假办理",
        action="leave",
        call_metadata={"skill_plan": plan.model_dump(mode="json")},
        api_request={"_skill_plan": plan.model_dump(mode="json")},
    )
    plans = [
        {"name": "query_leave", "capability_id": "cap_query", "title": "查询待办", "script": "query_leave",
         "requires_confirmation": False, "requires_verify": False,
         "input_schema": {"type": "object", "properties": {}, "required": []}},
        {"name": "submit_leave", "capability_id": "cap_submit", "title": "提交请假", "script": "submit_leave",
         "requires_confirmation": True, "requires_verify": True,
         "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    ]
    text = _fallback_skill_md(skill, "dano-oa-leave-package", plans, None)
    for title in ("适用场景", "不适用场景", "选择工作流", "组合与交接规则", "执行协议", "成功、失败与停止", "按需读取资源"):
        assert f"## {title}" in text
    assert "generator-guides" not in text
    assert "solo_" not in text
    assert "查询待办" in text
    assert "提交请假" in text
    assert "references/routes/" in text
    assert "查询后" in text or "人工交接" in text or "确认绑定" in text
    issues: list[dict] = []
    _check_skill(Path("SKILL.md"), text, issues)
    assert issues == []
    assert any(len(route.capability_sequence) > 1 for route in plan.routes)
    assert "规划依据（用户业务描述）" not in text
    assert "组合约定：" in text or "人工交接" in text
    assert "阶段" not in text
    assert "原子能力" not in text
    assert "录制识别顺序" not in text


def test_renderer_does_not_invent_capability_fields() -> None:
    plan = {
        "planning_mode": "dynamic",
        "selected_capability_ids": ["cap_submit"],
        "routes": [{
            "route_id": "write",
            "capability_sequence": ["cap_submit"],
            "steps": [{
                "capability_id": "cap_submit",
                "step_key": "submit_selected",
                "input_sources": [
                    {"field": "invented", "source": "user"},
                    {"field": "id", "source": "confirmed_binding"},
                ],
            }],
        }],
    }
    skill = SimpleNamespace(skill_id="oa.leave", call_metadata={"skill_plan": plan}, api_request={})
    kept = _filter_plans_for_export(
        [{
            "name": "submit_leave",
            "capability_id": "cap_submit",
            "input_schema": {"type": "object", "properties": {}, "required": []},
        }],
        skill,
    )
    schema = kept[0]["input_schema"]
    properties = schema.get("properties") or {}
    assert "invented" not in properties
    assert "id" not in properties
    assert "invented" not in (schema.get("required") or [])
    assert "x-dano-derived-from-query" not in json.dumps(schema, ensure_ascii=False)


def test_route_file_uses_business_language() -> None:
    route = {
        "route_id": "cap_query_then_cap_submit",
        "name": "查询待办 → 提交请假",
        "when_to_use": "查询完成紧接着提交请假",
        "capability_sequence": ["cap_query", "cap_submit"],
        "steps": [
            {
                "capability_id": "cap_query",
                "step_key": "query",
                "done_when": "已返回可核对的查询结果",
                "input_sources": [],
            },
            {
                "capability_id": "cap_submit",
                "step_key": "submit_selected",
                "done_when": "写入已确认",
                "checkpoint": True,
                "input_sources": [{"field": "id", "source": "user"}],
            },
        ],
        "checkpoints": [{"prompt": "请选定要提交的记录"}],
        "examples": [{"user_request": "查询完成紧接着提交请假"}],
        "done_when": "提交已确认",
        "failure_behavior": "失败时停止，不得重试",
    }
    plans = [
        {"name": "query_leave", "capability_id": "cap_query", "title": "查询待办", "script": "query_leave"},
        {"name": "submit_leave", "capability_id": "cap_submit", "title": "提交请假", "script": "submit_leave"},
    ]
    text = _route_file_md(route, plans)
    assert "submit_selected" not in text
    assert "←user" not in text
    assert "`id` 由用户提供" in text
    assert "第1步" in text
    assert "查询待办" in text


def test_renderer_planning_fields_and_selected_filter() -> None:
    plan = {
        "planning_mode": "fixed",
        "selected_capability_ids": ["cap_query", "cap_submit"],
        "routes": [{"route_id": "main", "bindings": [{"from_output": "id", "to_input": "id"}]}],
        "unused_capabilities": [{"capability_id": "cap_option", "name": "query_leave_options", "reason": "未要求"}],
        "source_flow_fingerprint": "fp-1",
    }
    skill = SimpleNamespace(skill_id="oa.leave", call_metadata={"skill_plan": plan}, api_request={})
    fields = _contract_planning_fields(skill)
    assert fields["planning_mode"] == "fixed"
    assert fields["selected_capability_ids"] == ["cap_query", "cap_submit"]
    assert fields["intent_branches"] == []
    assert fields["unused_capabilities"][0]["capability_id"] == "cap_option"
    kept = _filter_plans_for_export(
        [
            {"name": "query_leave", "capability_id": "cap_query"},
            {"name": "query_leave_options", "capability_id": "cap_option"},
            {"name": "submit_leave", "capability_id": "cap_submit"},
        ],
        skill,
    )
    assert [item["capability_id"] for item in kept] == ["cap_query", "cap_submit"]


def test_credentials_are_not_written_to_skill_package(tmp_path: Path) -> None:
    skill = SimpleNamespace(
        skill_id="oa.leave",
        title="请假",
        action="leave",
        call_metadata={
            "skill_plan": {
                "summary": "办理请假，token=should-not-matter",
                "planning_mode": "fixed",
                "selected_capability_ids": ["cap_query"],
                "routes": [{
                    "route_id": "main",
                    "name": "查询",
                    "when_to_use": "查询",
                    "capability_sequence": ["cap_query"],
                    "examples": [{"user_request": "查一下", "done_when": "已返回"}],
                    "bindings": [],
                }],
                "unused_capabilities": [],
                "source_flow_fingerprint": "fp",
            }
        },
        api_request={
            "authorization": "Bearer recorded-token-value",
            "cookie": "session=abc123secret",
            "capabilities": [{
                "capability_id": "cap_query",
                "name": "query_leave",
                "title": "查询",
            }],
        },
        capabilities=[{
            "capability_id": "cap_query",
            "name": "query_leave",
            "title": "查询",
        }],
    )
    plans = [{
        "name": "query_leave",
        "capability_id": "cap_query",
        "title": "查询",
        "script": "query_leave",
        "requires_confirmation": False,
        "requires_verify": False,
        "input_schema": {"type": "object", "properties": {}, "required": []},
    }]
    text = _fallback_skill_md(skill, "dano-oa-leave-package", plans, None)
    assert "Bearer recorded-token-value" not in text
    assert "session=abc123secret" not in text
    root = tmp_path / "pkg"
    _write_valid_package(root, skill, skill.call_metadata["skill_plan"])
    (root / "SKILL.md").write_text(text, encoding="utf-8")
    result = validate_skill_package(root)
    assert result["ok"], result["issues"]
    leaked = [issue for issue in result["issues"] if issue["code"] == "credential_leak"]
    assert leaked == []


def test_export_keeps_stage_six_contract_and_writes_business_triggers() -> None:
    skill = SimpleNamespace(
        skill_id="admin.erp_sale",
        title="点狮ERP销售订单操作能力录制",
        action="action_abc",
        risk_level="",
        call_metadata={
            "skill_plan": {
                "summary": "本页面的实际操作流程：搜索/筛选销售订单 → 新增销售订单。",
                "trigger_phrases": ["点狮ERP销售订单操作能力录制"],
                "selected_capability_ids": ["cap_search", "cap_create"],
                "routes": [],
                "planning_mode": "dynamic",
            }
        },
        api_request={
            "steps": [
                {
                    "step_id": "s1",
                    "method": "GET",
                    "url": "http://admin.example.com/admin-api/erp/sale-order/page?pageNo=1&no=1&customerId=8",
                    "path": "/admin-api/erp/sale-order/page?pageNo=1&no=1&customerId=8",
                    "url_template": "",
                    "query_template": {
                        "pageNo": "1",
                        "pageSize": "10",
                        "no": "{{订单单号}}",
                        "customerId": "{{客户}}",
                        "outStatus": "0",
                    },
                    "params": ["订单单号", "客户"],
                    "success_rule": {"field": "code", "ok_values": ["0"]},
                    "sample_inputs": {"订单单号": "1", "客户": "8"},
                },
                {
                    "step_id": "s2",
                    "method": "POST",
                    "url": "http://admin.example.com/admin-api/erp/sale-order/create",
                    "path": "/admin-api/erp/sale-order/create",
                    "body_template": {
                        "customerId": "{{customerId}}",
                        "discountPrice": 117105,
                        "items": "{{items}}",
                    },
                    "success_rule": {"field": "code", "ok_values": ["1020201001"]},
                    "sample_inputs": {"customerId": 8},
                },
            ],
            "capabilities": [
                {
                    "capability_id": "cap_search",
                    "name": "search-sale-orders",
                    "title": "搜索/筛选销售订单",
                    "kind": "query",
                    "compiled_step_ids": ["s1"],
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "订单单号": {"type": "string"},
                            "客户": {"type": "string"},
                        },
                        "required": [],
                    },
                },
                {
                    "capability_id": "cap_create",
                    "name": "create-sale-order",
                    "title": "新增销售订单",
                    "kind": "create",
                    "compiled_step_ids": ["s2"],
                    "requires_human_confirm": True,
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "customerId": {"type": "string"},
                            "post_sale_order_create_body_totalPrice": {
                                "type": "number",
                                "default": -106555,
                            },
                            "items": {"type": "array"},
                        },
                        "required": ["customerId", "items"],
                    },
                },
            ],
        },
    )
    plans = _capability_plans(skill, None, skill.api_request)
    search = next(item for item in plans if item["name"] == "search-sale-orders")
    create = next(item for item in plans if item["name"] == "create-sale-order")
    assert search["steps"][0]["path"] == "/admin-api/erp/sale-order/page?pageNo=1&no=1&customerId=8"
    assert search["steps"][0]["sample_inputs"] == {"订单单号": "1", "客户": "8"}
    assert search["steps"][0]["query_template"]["outStatus"] == "0"
    assert create["steps"][0]["body_template"]["discountPrice"] == 117105
    assert create["steps"][0]["success_rule"]["ok_values"] == ["1020201001"]
    assert create["requires_verify"] is True
    assert "post_sale_order_create_body_totalPrice" in create["input_schema"]["properties"]
    assert create["input_schema"]["properties"]["post_sale_order_create_body_totalPrice"]["default"] == -106555

    text = _fallback_skill_md(skill, "dano-admin-erp-package", plans, None)
    applicable = text.split("## 适用场景", 1)[1].split("##", 1)[0]
    assert "点狮ERP销售订单操作能力录制" not in applicable
    assert "用户要搜索/筛选销售订单时使用" not in text
    assert "用户要新增销售订单时使用" not in text
    assert "本页面的实际操作流程" not in text
    assert "name: sale-order-operations" in text
    assert "不要用于" in text
    assert "等2项业务能力" not in text
    assert "不得把录制样例" not in text
    assert "## 选择工作流" in text
    description = text.split("description:", 1)[1].split("\n", 1)[0]
    assert "。不要用于" in description or "不要用于" in description
    assert "先查再问" in description or "不要写入" in description
    assert "customerId=8" not in text
    assert "117105" not in text


def test_dynamic_plan_skips_recording_title_triggers() -> None:
    spec = _three_cap_spec()
    plan = propose_deterministic_plan(
        spec,
        SkillGenerationRequest(
            title="点狮ERP销售订单操作能力录制",
            business_description="本页面的实际操作流程：查询待办 → 提交请假。用户可按业务需要执行其中一项或多项操作。",
            planning_mode=PlanningMode.DYNAMIC,
        ),
        VERIFIED,
        "fp-copy",
    )
    assert all("录制" not in item for item in plan.trigger_phrases)
    assert "本页面的实际操作流程" not in plan.summary
    assert not any(route.route_id.startswith("solo_") for route in plan.routes)
    assert plan.composition_notes
    for route in plan.routes:
        assert "本页面的实际操作流程" not in route.when_to_use
        assert route.examples
        assert "本页面的实际操作流程" not in route.examples[0].user_request


def test_handoff_without_bindings_is_written_into_skill_md() -> None:
    spec = _three_cap_spec(confirmed_query_submit=False, confirmed_option_submit=False)
    request = SkillGenerationRequest(
        title="请假办理",
        business_description="可以只查询，也可以查询后再提交",
        planning_mode=PlanningMode.DYNAMIC,
    )
    plan = propose_deterministic_plan(spec, request, VERIFIED, "fp-handoff-md")
    assert not any(route.bindings for route in plan.routes if len(route.capability_sequence) > 1)
    assert any("先查再问" in item for item in plan.composition_notes)
    assert any("只读" in item and "不得执行写入" in item for item in plan.composition_notes)
    skill = SimpleNamespace(
        skill_id="oa.leave",
        title="请假办理",
        action="leave",
        call_metadata={"skill_plan": plan.model_dump(mode="json")},
        api_request={"_skill_plan": plan.model_dump(mode="json")},
    )
    plans = [
        {"name": "query_leave", "capability_id": "cap_query", "title": "查询待办", "script": "query_leave",
         "requires_confirmation": False, "requires_verify": False,
         "input_schema": {"type": "object", "properties": {}, "required": []}},
        {"name": "submit_leave", "capability_id": "cap_submit", "title": "提交请假", "script": "submit_leave",
         "requires_confirmation": True, "requires_verify": True,
         "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    ]
    text = _fallback_skill_md(skill, "dano-oa-leave-package", plans, None)
    assert "先查再问" in text
    assert "阶段" not in text
    assert "原子能力" not in text
    assert "录制识别顺序" not in text
    for marker in HANDBOOK_BAN_MARKERS:
        assert marker not in text


def test_user_playbook_appears_in_skill_relation_and_description() -> None:
    playbook = "先按客户找到订单，只要看不要改；要审批时先让我指定哪一条"
    spec = _three_cap_spec(confirmed_query_submit=False, confirmed_option_submit=False)
    request = SkillGenerationRequest(
        title="销售订单办理",
        business_description=playbook,
        planning_mode=PlanningMode.DYNAMIC,
        example_requests=["帮我查鲜生的单"],
    )
    plan = propose_deterministic_plan(spec, request, VERIFIED, "fp-playbook")
    skill = SimpleNamespace(
        skill_id="oa.sale",
        title="销售订单办理",
        action="sale",
        call_metadata={"skill_plan": plan.model_dump(mode="json")},
        api_request={"_skill_plan": plan.model_dump(mode="json")},
    )
    plans = [
        {"name": "query_leave", "capability_id": "cap_query", "title": "搜索/筛选销售订单", "script": "search_sale_orders",
         "requires_confirmation": False, "requires_verify": False,
         "input_schema": {"type": "object", "properties": {}, "required": []}},
        {"name": "submit_leave", "capability_id": "cap_submit", "title": "审批销售订单", "script": "approve_sale_order",
         "requires_confirmation": True, "requires_verify": True,
         "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    ]
    text = _fallback_skill_md(skill, "dano-oa-sale-package", plans, spec)
    relation = text.split("## 适用场景", 1)[1].split("## 选择工作流", 1)[0]
    assert playbook in relation or playbook in text.split("## 组合与交接规则", 1)[1]
    description = text.split("description:", 1)[1].split("\n", 1)[0]
    assert "不要改" in description or "查找" in description or "找到" in description
    assert "审批" in description
    assert "用户要搜索/筛选销售订单、用户要查看" not in text
    issues: list[dict] = []
    _check_skill(Path("SKILL.md"), text, issues)
    assert issues == []


def test_fallback_skill_md_rejects_handbook_ban_markers() -> None:
    spec = _three_cap_spec()
    request = SkillGenerationRequest(
        title="请假办理",
        business_description="可以只查询，也可以查询后再提交。",
        planning_mode=PlanningMode.DYNAMIC,
    )
    plan = propose_deterministic_plan(spec, request, VERIFIED, "fp-ban")
    skill = SimpleNamespace(
        skill_id="oa.leave",
        title="请假办理",
        action="leave",
        call_metadata={"skill_plan": plan.model_dump(mode="json")},
        api_request={"_skill_plan": plan.model_dump(mode="json")},
    )
    plans = [
        {"name": "query_leave", "capability_id": "cap_query", "title": "查询待办", "script": "query_leave",
         "requires_confirmation": False, "requires_verify": False,
         "input_schema": {"type": "object", "properties": {}, "required": []}},
        {"name": "submit_leave", "capability_id": "cap_submit", "title": "提交请假", "script": "submit_leave",
         "requires_confirmation": True, "requires_verify": True,
         "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    ]
    text = _fallback_skill_md(skill, "dano-oa-leave-package", plans, spec)
    for marker in HANDBOOK_BAN_MARKERS:
        assert marker not in text
    for title in ("适用场景", "不适用场景", "选择工作流", "组合与交接规则", "执行协议", "成功、失败与停止", "按需读取资源"):
        assert f"## {title}" in text
    steps = text.split("## 执行协议", 1)[1].split("##", 1)[0]
    numbered = [line for line in steps.splitlines() if line[:2].rstrip(".").isdigit() or line[:1].isdigit()]
    assert steps.count("Done when:") >= 5
    assert numbered
    issues: list[dict] = []
    _check_skill(Path("SKILL.md"), text, issues)
    assert issues == []


def _sale_order_seven_spec() -> FlowSpec:
    titles = [
        ("cap_search", "search_sale_orders", "搜索/筛选销售订单", "query"),
        ("cap_detail", "get_sale_order", "查看销售订单详情", "query"),
        ("cap_update", "update_sale_order", "修改销售订单", "submit"),
        ("cap_approve", "approve_sale_order", "审批销售订单", "submit"),
        ("cap_unapprove", "unapprove_sale_order", "反审销售订单", "submit"),
        ("cap_create", "create_sale_order", "新建销售订单", "submit"),
        ("cap_delete", "delete_sale_order", "删除销售订单", "delete"),
    ]
    return FlowSpec(
        tenant="tenant",
        subsystem="oa",
        title="销售订单",
        steps=[FlowStep(step_id=f"s{index + 1}", method="GET" if kind == "query" else "POST", path=f"/erp/{name}")
               for index, (_cid, name, _title, kind) in enumerate(titles)],
        capabilities=[
            _cap(
                capability_id=cid,
                name=name,
                title=title,
                kind=kind,
                required=["id"] if kind != "query" else [],
                confirm=kind != "query",
                output_props={"records": {"type": "array"}} if kind == "query" else {},
                input_props={"id": {"type": "string"}, "status": {"type": "string", "x-enum-value-map": {"通过": "1"}, "option_map": {"通过": "1"}}} if kind != "query" else {},
            )
            for cid, name, title, kind in titles
        ],
        capability_relations=[],
    )


def test_sale_order_handbook_is_an_executable_agent_playbook() -> None:
    spec = _sale_order_seven_spec()
    verified = {cap.capability_id for cap in spec.capabilities}
    playbook = "先按客户或单号找到订单；只看时不要改不要审；要改或审批时先让我指定哪一条再写"
    request = SkillGenerationRequest(
        title="销售订单办理",
        business_description=playbook,
        planning_mode=PlanningMode.DYNAMIC,
        example_requests=["帮我查鲜生的单", "只看看", "把那张过一下"],
    )
    plan = propose_deterministic_plan(spec, request, verified, "fp-sale")
    skill = SimpleNamespace(
        skill_id="admin.erp_sale",
        title="销售订单办理",
        action="sale-order",
        call_metadata={"skill_plan": plan.model_dump(mode="json")},
        api_request={"_skill_plan": plan.model_dump(mode="json"), "capabilities": [
            {"capability_id": cap.capability_id, "name": cap.name, "title": cap.title, "kind": cap.kind, "input_schema": cap.input_schema}
            for cap in spec.capabilities
        ]},
    )
    plans = [
        {
            "name": cap.name,
            "capability_id": cap.capability_id,
            "title": cap.title,
            "script": cap.name,
            "requires_confirmation": cap.kind != "query",
            "requires_verify": cap.kind != "query",
            "input_schema": cap.input_schema,
        }
        for cap in spec.capabilities
    ]
    text = _fallback_skill_md(skill, "dano-sale-package", plans, spec)
    assert playbook in text
    assert "7项业务能力" not in text
    assert "搜索/筛选销售订单" in text
    assert "先查再问" in text or "指定" in text or "人工交接" in text
    assert "INPUT_FORMS.md" in text
    assert "只要查询时不要写入" in text or "不要改" in text
    for marker in HANDBOOK_BAN_MARKERS:
        assert marker not in text
    issues: list[dict] = []
    _check_skill(Path("SKILL.md"), text, issues)
    assert issues == []
    write = next(item for item in plans if item["name"] == "update_sale_order")
    assert write["input_schema"]["properties"]["status"]["option_map"] == {"通过": "1"}
    assert write["input_schema"]["properties"]["status"]["x-enum-value-map"] == {"通过": "1"}


def test_two_combination_routes_render_as_isolated_files() -> None:
    spec = _three_cap_spec()
    request = SkillGenerationRequest(
        title="请假办理",
        business_description="可以先查询再提交，也可以先查选项再提交。",
        planning_mode=PlanningMode.DYNAMIC,
    )
    plan = propose_deterministic_plan(spec, request, VERIFIED, "fp-two")
    combos = [route for route in plan.routes if len(route.capability_sequence) > 1]
    assert len(combos) >= 2
    skill = SimpleNamespace(
        skill_id="oa.leave",
        title="请假办理",
        call_metadata={"skill_plan": plan.model_dump(mode="json")},
        api_request={"_skill_plan": plan.model_dump(mode="json")},
    )
    plans = [
        {"name": "query_leave", "capability_id": "cap_query", "title": "查询待办", "script": "query_leave"},
        {"name": "query_leave_options", "capability_id": "cap_option", "title": "查询请假选项", "script": "query_leave_options"},
        {"name": "submit_leave", "capability_id": "cap_submit", "title": "提交请假", "script": "submit_leave", "requires_confirmation": True},
    ]
    texts = [_route_file_md(route.model_dump(mode="json"), plans) for route in combos[:2]]
    assert texts[0] != texts[1]
    assert all("## 完整示例" in text for text in texts)
    skill_md = _fallback_skill_md(skill, "pkg", plans, spec)
    assert all(f"references/routes/{route.route_id}.md" in skill_md for route in combos[:2])
    assert skill_md.count("## 选择工作流") == 1


def test_new_package_files_and_purity() -> None:
    spec = _three_cap_spec(confirmed_query_submit=False, confirmed_option_submit=False)
    request = SkillGenerationRequest(
        title="请假办理",
        business_description="先查询待办再提交请假。",
        planning_mode=PlanningMode.DYNAMIC,
    )
    plan = propose_deterministic_plan(spec, request, VERIFIED, "fp-pure")
    skill = SimpleNamespace(
        skill_id="oa.leave",
        title="请假办理",
        call_metadata={"skill_plan": plan.model_dump(mode="json")},
        api_request={"_skill_plan": plan.model_dump(mode="json")},
    )
    plans = [
        {"name": "query_leave", "capability_id": "cap_query", "title": "查询待办", "script": "query_leave",
         "requires_confirmation": False, "input_schema": {"type": "object", "properties": {}, "required": []}},
        {"name": "submit_leave", "capability_id": "cap_submit", "title": "提交请假", "script": "submit_leave",
         "requires_confirmation": True,
         "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    ]
    skill_md = _fallback_skill_md(skill, "pkg", plans, spec)
    capabilities = _capabilities_md(skill, plans)
    options = _options_md(plans)
    combo = next(route for route in plan.routes if len(route.capability_sequence) > 1)
    route_md = _route_file_md(combo.model_dump(mode="json"), plans)
    assert "CAPABILITIES.md" in skill_md
    assert "OPTIONS.md" in skill_md
    assert "INPUT_FORMS.md" in skill_md
    assert "人工交接" in skill_md and "人工交接" in route_md
    assert "用户原话" in route_md
    assert "generator-guides" not in skill_md
    assert "阶段 8" not in skill_md + capabilities + options + route_md
    assert "version:" not in skill_md.split("---", 2)[1]
    assert "compatibility:" not in skill_md.split("---", 2)[1]
    assert "| 能力 |" in capabilities
    assert "不要把历史样本当成默认值" in options
    assert "用户原话" in route_md
    assert "输入来源" in route_md
    assert "确认" in route_md
    assert "完成" in route_md
    assert "失败" in route_md or "停止" in route_md
    assert "当前操作缺少必填字段时" in skill_md
    assert "选择「" in skill_md or "references/routes/" in skill_md


def test_bound_combination_shows_only_confirmed_bindings() -> None:
    spec = _three_cap_spec()
    request = SkillGenerationRequest(
        title="请假办理",
        business_description="可以先查询再提交。",
        planning_mode=PlanningMode.DYNAMIC,
    )
    plan = propose_deterministic_plan(spec, request, VERIFIED, "fp-bound-md")
    combo = next(route for route in plan.routes if route.bindings)
    plans = [
        {"name": "query_leave", "capability_id": "cap_query", "title": "查询待办", "script": "query_leave"},
        {"name": "submit_leave", "capability_id": "cap_submit", "title": "提交请假", "script": "submit_leave",
         "requires_confirmation": True},
    ]
    route_md = _route_file_md(combo.model_dump(mode="json"), plans)
    skill_md = _fallback_skill_md(
        SimpleNamespace(skill_id="oa.leave", title="请假办理", call_metadata={"skill_plan": plan.model_dump(mode="json")}, api_request={}),
        "pkg",
        plans,
        spec,
    )
    for binding in combo.bindings:
        assert binding.from_output in route_md
        assert binding.to_input in route_md
        assert binding.from_output in skill_md or binding.to_input in skill_md
    assert "customerId" not in route_md
    assert "推测" not in route_md


def test_option_facts_live_only_in_options_md() -> None:
    plans = [{
        "name": "submit_leave",
        "title": "提交请假",
        "script": "submit_leave",
        "requires_confirmation": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "x-dano-option-source": {
                        "endpoint": "/oa/leave/unique-options",
                        "method": "GET",
                        "resultPath": "data",
                        "idField": "value",
                        "labelField": "label",
                    },
                },
            },
            "required": ["status"],
        },
    }]
    skill = SimpleNamespace(skill_id="oa.leave", title="请假", call_metadata={}, api_request={})
    options = _options_md(plans)
    forms = _input_forms_md(plans)
    capabilities = _capabilities_md(skill, plans)
    table = forms.split("| 字段 |", 1)[1].split("回答处理", 1)[0]
    assert "/oa/leave/unique-options" in options
    assert "/oa/leave/unique-options" not in table
    assert "/oa/leave/unique-options" not in capabilities
    assert "OPTIONS.md" in table


def test_long_input_forms_get_toc_or_split() -> None:
    properties = {
        f"field_{index}": {"type": "string", "title": f"字段{index}"}
        for index in range(18)
    }
    plans = [{
        "name": "create_order",
        "title": "新建销售订单",
        "script": "create_order",
        "input_schema": {"type": "object", "properties": properties, "required": list(properties)},
    }]
    text, extras = _input_forms_bundle(plans)
    assert extras
    rel = next(iter(extras))
    assert rel.startswith("forms/")
    assert f"references/{rel}" in text
    assert "## 目录" in text or "forms/" in text


@pytest.mark.asyncio
async def test_unknown_action_needs_clarification_and_does_not_export(tmp_path: Path) -> None:
    spec = _three_cap_spec()
    stored: list[dict] = []
    outcome = await export_recording_skill(
        result_id=uuid4(),
        body=_verified_body(spec),
        tenant="tenant",
        request=_request(
            out_dir=str(tmp_path),
            planning_mode=PlanningMode.DYNAMIC,
            business_description="请把待办记录导出成报表。",
            example_requests=["帮我导出报表"],
            success_criteria="已经导出",
            forbidden_actions="不要提交",
        ),
        persist=stored.append,
        publish=_ok_publish,
        render=_render_valid,
        proposer=_deterministic_proposer,
    )
    assert outcome.status == "needs_clarification"
    assert outcome.clarification_questions
    assert outcome.unresolved_branches
    assert not outcome.export_path
    assert stored[-1]["skill_export_status"] == "needs_clarification"
    assert stored[-1]["skill_export_planning_mode"] == "dynamic"
    assert stored[-1]["skill_export_example_requests"] == ["帮我导出报表"]
    assert stored[-1]["skill_export_success_criteria"] == "已经导出"
    assert stored[-1]["skill_export_forbidden_actions"] == "不要提交"
    assert recording_skill_lifecycle(stored[-1]) == "needs_clarification"
    assert not list(tmp_path.glob("*-package"))


def test_route_rows_use_business_language() -> None:
    spec = _three_cap_spec()
    plan = propose_deterministic_plan(
        spec,
        SkillGenerationRequest(
            title="请假办理",
            business_description="可以先查询再提交。",
            planning_mode=PlanningMode.DYNAMIC,
        ),
        VERIFIED,
        "fp-rows",
    )
    rows = _route_rows(plan, spec)
    combo = next(item for item in rows if len(item["steps"]) > 1)
    assert "查询待办记录" in combo["steps"]
    assert "提交请假" in combo["steps"]
    assert "cap_query" not in json.dumps(combo, ensure_ascii=False)
    assert "binding" not in json.dumps(combo, ensure_ascii=False)
    assert combo["composition"] in {"查询后自动带入", "先办理再请你选定", "各步分开收集"}
    if combo["auto_carry"]:
        assert any("自动" in item for item in combo["auto_carry"])


def _sale_verified_body(spec) -> dict:
    payload = spec.model_dump(mode="json")
    fingerprint = working_fingerprint(spec)
    verified = sorted(sale_order_verified_ids(spec))
    return {
        "title": spec.title or "销售订单",
        "subsystem": "admin",
        "action": "erp-372468ecf111",
        "flow_spec": payload,
        "machine_verification_status": "verified",
        "machine_verification_ran": True,
        "stage_seven_fingerprint": fingerprint,
        "published": False,
        "skill_id": "admin.dianshixinxi.com.90.erp.372468ecf111",
        "stage_seven": {
            "status": "verified",
            "working_fingerprint": fingerprint,
            "working_flow_spec": payload,
            "verdict": {"callable_capability_ids": verified},
        },
    }


@pytest.mark.asyncio
async def test_sale_order_package_regenerates_from_generator(tmp_path: Path) -> None:
    spec = sale_order_spec()
    request = sale_order_request()
    request.out_dir = str(tmp_path)
    plan = propose_deterministic_plan(spec, request, sale_order_verified_ids(spec), working_fingerprint(spec))
    combos = combination_routes(plan)
    assert len(spec.capabilities) == 7
    assert {combo_pair(route) for route in combos} >= set(SALE_ORDER_EXPLICIT_COMBOS)
    assert len({route.route_id for route in plan.routes}) == len(plan.routes)
    assert all(not route.bindings for route in combos)
    assert all(route_has_human_checkpoint(route) for route in combos)
    assert not plan.clarification_questions

    outcome = await export_recording_skill(
        result_id=uuid4(),
        body=_sale_verified_body(spec),
        tenant="tenant",
        request=request,
        persist=lambda _body: None,
        publish=_ok_publish,
        render=_default_render,
        proposer=_deterministic_proposer,
    )
    assert outcome.status == "exported", outcome.errors or outcome.clarification_questions
    root = Path(outcome.export_path)
    assert root.is_dir()
    assert (root / "SKILL.md").is_file()
    assert (root / "references" / "CAPABILITIES.md").is_file()
    assert (root / "references" / "OPTIONS.md").is_file()
    assert (root / "references" / "INPUT_FORMS.md").is_file()
    assert not (root / "references" / "OPERATIONS.md").exists()
    assert not (root / "references" / "generator-guides").exists()
    skill_md = (root / "SKILL.md").read_text(encoding="utf-8")
    assert "## 选择工作流" in skill_md
    assert "GET /" not in skill_md
    assert "generator-guides" not in skill_md
    route_files = list((root / "references" / "routes").glob("*.md"))
    assert len(route_files) == len(combos)
    for route in combos:
        route_file = root / "references" / "routes" / f"{route.route_id}.md"
        assert route_file.is_file()
        text = route_file.read_text(encoding="utf-8")
        assert "## 完整示例" in text
        assert "用户原话" in text
        assert "人工交接" in text or "请" in text
        assert not route.bindings
    contract = json.loads((root / "references" / "CONTRACT.json").read_text(encoding="utf-8"))
    packed = {
        str(item.get("name") or "")
        for item in contract.get("capabilities") or []
    }
    assert packed >= {name for _cid, name, _title, _kind in SALE_ORDER_TITLES}
    contract_ids = [str(item.get("route_id") or "") for item in contract.get("routes") or []]
    assert len(contract_ids) == len(set(contract_ids))
    assert contract.get("intent_branches")
    for route_file in route_files:
        route_text = route_file.read_text(encoding="utf-8")
        assert "←user" not in route_text
        assert "submit_selected" not in route_text
    forms = (root / "references" / "INPUT_FORMS.md").read_text(encoding="utf-8")
    update = next(item for item in contract["capabilities"] if item.get("capability_id") == "cap_update")
    schema = update.get("input_schema") or {}
    assert "x-dano-derived-from-query" not in json.dumps(schema, ensure_ascii=False)
    update_route = next(
        item
        for item in contract.get("routes") or []
        if [str(cap) for cap in (item.get("capability_sequence") or [])][:2] == ["cap_search", "cap_update"]
    )
    assert "id" in (update_route.get("required_user_inputs") or [])
    if "id" in (schema.get("properties") or {}):
        assert "id" in forms
    result = validate_skill_package(root)
    assert result["ok"], result["issues"]
    atomic_only = [line for line in skill_md.splitlines() if "不必读取组合路线" in line]
    assert atomic_only


@pytest.mark.asyncio
async def test_preview_only_does_not_write_package(tmp_path: Path) -> None:
    spec = sale_order_spec()
    request = sale_order_request()
    request.out_dir = str(tmp_path)
    request.preview_only = True
    stored: list[dict] = []
    outcome = await export_recording_skill(
        result_id=uuid4(),
        body=_sale_verified_body(spec),
        tenant="tenant",
        request=request,
        persist=stored.append,
        publish=_ok_publish,
        render=_default_render,
        proposer=_deterministic_proposer,
    )
    assert outcome.status == "previewed", outcome.errors or outcome.clarification_questions
    assert not outcome.export_path
    assert outcome.routes
    assert not list(tmp_path.glob("*-package"))
    assert stored[-1]["skill_export_status"] == "previewed"
