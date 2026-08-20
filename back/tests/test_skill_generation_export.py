"""Stage-8 manual Skill export: gates, idempotency, failure, and package planning."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from dano.export.skill_package.renderer import (
    _contract_planning_fields,
    _fallback_skill_md,
    _filter_plans_for_export,
    _skill_plan_payload,
    package_slug,
)
from dano.export.skill_package.validator import _check_skill, validate_skill_package
from dano.onboarding.recording_stage_seven import working_fingerprint
from dano.onboarding.recording_results import recording_skill_lifecycle
from dano.onboarding.skill_generation.export import SkillExportError, export_recording_skill
from dano.onboarding.skill_generation.export_view import build_export_view
from dano.onboarding.skill_generation.models import PlanningMode, SkillGenerationRequest
from dano.onboarding.skill_generation.planner import propose_deterministic_plan

from test_skill_generation_plan import VERIFIED, _three_cap_spec


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
    (root / "reference.md").write_text(
        "## Business hard rules\n\n- none\n\n## Fallback browser steps\n\n- none\n\n"
        "## API chain\n\n- GET /oa/leave/page verification_id=aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee\n",
        encoding="utf-8",
    )
    contract = {
        "protocol": "dano.skill_package.contract.v1",
        "skill": {"id": skill.skill_id, "name": package_slug(skill.skill_id)},
        "capabilities": [
            {
                "name": item["name"],
                "capability_id": item["capability_id"],
                "script": f"scripts/{item['script']}.py",
                "verify_script": f"scripts/verify_{item['script']}.py",
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
async def test_manual_export_requires_stage_seven_verified(tmp_path: Path) -> None:
    spec = _three_cap_spec()
    body = _verified_body(spec)
    body["machine_verification_status"] = "running"
    body["stage_seven"]["status"] = "running"
    stored: list[dict] = []

    with pytest.raises(SkillExportError) as exc:
        await export_recording_skill(
            result_id=uuid4(),
            body=body,
            tenant="tenant",
            request=_request(out_dir=str(tmp_path)),
            persist=stored.append,
            publish=_ok_publish,
            render=_render_valid,
            proposer=_deterministic_proposer,
        )
    assert exc.value.status_code == 409
    assert "阶段7" in exc.value.detail
    assert not stored or not stored[-1].get("published")


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
async def test_incomplete_relation_export_does_not_publish(tmp_path: Path) -> None:
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
    assert outcome.status == "needs_clarification"
    assert outcome.clarification_questions
    assert not outcome.export_path
    assert stored[-1]["published"] is False
    assert recording_skill_lifecycle(stored[-1]) == "verified_not_exported"


@pytest.mark.asyncio
async def test_flow_change_invalidates_old_skill_plan(tmp_path: Path) -> None:
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
    })
    with pytest.raises(SkillExportError) as exc:
        stale = _verified_body(spec)
        stale["stage_seven_fingerprint"] = "old-fp"
        stale["stage_seven"]["working_fingerprint"] = "old-fp"
        await export_recording_skill(
            result_id=result_id,
            body=stale,
            tenant="tenant",
            request=_request(out_dir=str(tmp_path)),
            publish=_ok_publish,
            render=_render_valid,
            proposer=_deterministic_proposer,
        )
    assert exc.value.status_code == 409
    assert "指纹" in exc.value.detail
    del body


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
    assert "## Business purpose" in text
    assert "## When to use" in text
    assert "## Planning or routing rules" in text
    assert "## Examples" in text
    for route in plan.routes:
        assert route.route_id in text
        assert route.examples[0].user_request in text
    assert "Transport" in text and "Preconditions" in text and "Pitfalls" in text
    issues: list[dict] = []
    _check_skill(Path("SKILL.md"), text, issues)
    assert issues == []


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
