"""Stage-8 regressions for executable routing and consumer-facing Skill manuals."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from dano.execution.page.flow_spec import FlowCapability, FlowSpec
from dano.execution.page.flow_spec_core.models import (
    CapabilityRelation,
    CapabilityRequestRef,
    FlowStep,
)
from dano.export.skill_package.renderer import (
    _capabilities_md,
    _capability_plans,
    _consumer_contract,
    _field_label,
    _format_list_py,
    _input_forms_md,
    _options_md,
    _public_schema,
    _runtime_plan,
    _script_invocation,
    _script_slug,
    _skill_description,
    _workflow_table,
    package_slug,
    render_skill_package,
)
from dano.export.skill_package.validator import (
    _check_contract_semantics,
    _check_input_fact_alignment,
    validate_skill_package,
)
from dano.onboarding.skill_generation import (
    PlanningMode,
    SkillGenerationRequest,
    export_recording_skill,
    generation_request_fingerprint,
    propose_deterministic_plan,
    validate_skill_plan,
)
from dano.onboarding.skill_generation.catalog import relation_is_usable
from dano.onboarding.skill_generation.quality import match_routes
from dano.orchestrator.types import SkillSpec
from dano.shared.enums import RiskLevel, Subsystem


def _cap(
    capability_id: str,
    title: str,
    kind: str,
    *,
    required: list[str] | None = None,
) -> FlowCapability:
    required = list(required or [])
    return FlowCapability(
        capability_id=capability_id,
        name=capability_id,
        title=title,
        kind=kind,
        requires_human_confirm=False,
        input_schema={
            "type": "object",
            "properties": {name: {"type": "string"} for name in required},
            "required": required,
        },
        output_schema={"type": "object"},
    )


def test_recorded_skill_package_uses_only_the_action_id_as_its_folder_name() -> None:
    action = "action_8a01bc7d87ef4680b2b259147e3d3322"

    assert package_slug(f"admin-dianshixinxi-com-90.{action}") == action


def test_skill_deletion_removes_current_and_legacy_recording_packages(tmp_path: Path) -> None:
    from dano.gateway.app import _cleanup_export_folders, _export_slugs_for_manifest

    skill_id = "admin-dianshixinxi-com-90.action_8a01bc7d87ef4680b2b259147e3d3322"
    current = tmp_path / "action_8a01bc7d87ef4680b2b259147e3d3322"
    legacy = tmp_path / (
        "dano-admin-dianshixinxi-com-90-action-8a01bc7d87ef4680b2b259147e3d3322-package"
    )
    current.mkdir()
    legacy.mkdir()

    removed = _cleanup_export_folders(
        str(tmp_path),
        _export_slugs_for_manifest({"name": skill_id}),
    )

    assert set(removed) == {str(current.resolve()), str(legacy.resolve())}
    assert not current.exists()
    assert not legacy.exists()


async def test_reexporting_the_same_recording_republishes_the_catalog_entry(tmp_path: Path) -> None:
    from dano.onboarding.recording_stage_seven import working_fingerprint

    result_id = UUID("11111111-1111-1111-1111-111111111111")
    action = "action_8a01bc7d87ef4680b2b259147e3d3322"
    skill_id = f"erp.{action}"
    spec = FlowSpec(subsystem="erp", capabilities=[_cap("query", "查询销售订单", "query")])
    request = SkillGenerationRequest(
        title="销售订单管理",
        business_description="查询销售订单。",
        out_dir=str(tmp_path),
    )
    fingerprint = working_fingerprint(spec)
    plan = propose_deterministic_plan(spec, request, {"query"}, fingerprint)
    capabilities = [{
        "capability_id": "query",
        "name": "query_orders",
        "title": "查询销售订单",
        "kind": "query",
        "input_schema": {"type": "object", "properties": {}},
        "output_schema": {"type": "object"},
        "requires_human_confirm": False,
        "execution_contract": {
            "steps": [{
                "step_id": "query-step",
                "method": "GET",
                "url": "https://example.test/orders",
                "path": "/orders",
            }],
            "links": [],
            "verification_ids": [],
        },
    }]

    def build_skill(_view, *, tenant: str, skill_id: str, title: str, plan):  # noqa: ANN001
        return SkillSpec(
            skill_id=skill_id,
            tenant=tenant,
            subsystem=Subsystem("erp"),
            action=action,
            risk_level=RiskLevel.L1,
            title=title,
            api_request={
                "capabilities": capabilities,
                "_skill_plan": plan.model_dump(mode="json"),
            },
            call_metadata={"skill_plan": plan.model_dump(mode="json")},
            capabilities=capabilities,
        )

    existing_skill = build_skill(
        spec,
        tenant="test",
        skill_id=skill_id,
        title=request.title,
        plan=plan,
    )
    existing_path = tmp_path / render_skill_package(existing_skill, str(tmp_path), tenant="test")
    body = {
        "flow_spec": spec.model_dump(mode="json"),
        "action": action,
        "subsystem": "erp",
        "title": request.title,
        "published": True,
        "skill_id": skill_id,
        "skill_plan": plan.model_dump(mode="json"),
        "skill_export_status": "exported",
        "export_path": str(existing_path),
        "skill_request_fingerprint": generation_request_fingerprint(
            result_id=str(result_id),
            stage_seven_fingerprint=fingerprint,
            request=request,
        ),
    }
    published = 0
    local_was_removed = False
    persisted: dict = {}

    async def proposer(current_spec, current_request, verified, source_fingerprint):  # noqa: ANN001
        return propose_deterministic_plan(
            current_spec,
            current_request,
            verified,
            source_fingerprint,
        )

    async def publish(**_kwargs):  # noqa: ANN003
        nonlocal published
        published += 1
        return {"ok": True, "asset_version": 2}

    async def persist(next_body: dict) -> None:
        persisted.update(next_body)

    def render(skill, out_dir: str, *, tenant: str) -> str:  # noqa: ANN001
        nonlocal local_was_removed
        local_was_removed = not existing_path.exists()
        return render_skill_package(skill, out_dir, tenant=tenant)

    outcome = await export_recording_skill(
        result_id=result_id,
        body=body,
        tenant="test",
        request=request,
        proposer=proposer,
        publish=publish,
        render=render,
        persist=persist,
        build_skill=build_skill,
    )

    assert outcome.status == "exported"
    assert outcome.idempotent is False
    assert outcome.version == 2
    assert published == 1
    assert local_was_removed is True
    assert persisted["skill_export_status"] == "exported"


def _sales_spec() -> FlowSpec:
    return FlowSpec(
        capabilities=[
            _cap("query_detail", "查询销售订单详情", "query", required=["id"]),
            _cap("update_order", "更新销售订单", "update", required=["id", "items"]),
            _cap("revoke_order", "反审核销售订单", "update", required=["ids"]),
        ]
    )


def test_explicit_combinations_are_not_silently_atomized_or_reordered() -> None:
    spec = _sales_spec()
    request = SkillGenerationRequest(
        title="销售订单",
        planning_mode=PlanningMode.DYNAMIC,
        business_description=(
            "先查询销售订单详情，再更新销售订单；"
            "先取消审核销售订单，再更新销售订单。只查询详情时不要写入。"
        ),
        example_requests=["先查详情再更新", "先取消审核再更新"],
    )
    verified = {cap.capability_id for cap in spec.capabilities}

    plan = propose_deterministic_plan(spec, request, verified, "fp-stage8")
    sequences = {tuple(route.capability_sequence) for route in plan.routes}

    assert ("query_detail", "update_order") in sequences
    assert ("revoke_order", "update_order") in sequences
    assert ("update_order", "query_detail") not in sequences
    assert all(len(sequence) == 1 or sequence[-1] != "query_detail" for sequence in sequences)
    assert {(cap_id,) for cap_id in verified} <= sequences
    combo = next(
        route for route in plan.routes
        if route.capability_sequence == ["revoke_order", "update_order"]
    )
    assert combo.checkpoints or any(step.checkpoint for step in combo.steps)
    checked = validate_skill_plan(
        plan,
        spec,
        verified_capability_ids=verified,
        expected_fingerprint="fp-stage8",
    )
    assert checked.ok, checked.errors


def test_every_write_is_confirmed_even_when_upstream_flag_is_false() -> None:
    spec = _sales_spec()
    request = SkillGenerationRequest(
        title="销售订单",
        planning_mode=PlanningMode.DYNAMIC,
        business_description="可以查询销售订单详情，也可以更新或取消审核销售订单。",
    )
    verified = {cap.capability_id for cap in spec.capabilities}

    plan = propose_deterministic_plan(spec, request, verified, "fp-confirm")

    for route in plan.routes:
        write_steps = [
            step for step in route.steps
            if step.capability_id in {"update_order", "revoke_order"}
        ]
        if write_steps:
            assert route.requires_confirmation is True
            assert all(step.confirm_before_execute for step in write_steps)


def test_renderer_keeps_machine_contract_out_of_consumer_manuals() -> None:
    plans = [
        {
            "name": "update_order",
            "title": "更新销售订单",
            "kind": "update",
            "script": "update_order",
            "is_write": True,
            "requires_confirmation": True,
            "requires_verify": False,
            "input_schema": {
                "type": "object",
                "properties": {
                    "ownerId": {
                        "type": "string",
                        "x-dano-option-source": {
                            "source_url": "http://admin.example.test/users/simple-list",
                            "method": "GET",
                            "value_key": "id",
                            "label_key": "name",
                        },
                        "x-options-snapshot": [{"id": "recorded-id", "label": "历史样本"}],
                    }
                },
                "required": ["ownerId"],
            },
            "output_schema": {"type": "object"},
        }
    ]
    skill = type("Skill", (), {"call_metadata": {}, "api_request": {}})()

    capabilities = _capabilities_md(skill, plans)
    options = _options_md(plans)

    assert "完整能力契约" not in capabilities
    assert "```json" not in capabilities
    assert "capability_id" not in capabilities
    assert "完整候选契约" not in options
    assert "```json" not in options
    assert "| 历史样本 |" not in options
    assert "recorded-id" not in options
    assert "GET /users/simple-list" in options
    assert "http://admin.example.test" not in options


def test_write_without_verified_readback_does_not_publish_fake_verifier() -> None:
    api_request = {
        "steps": [
            {
                "step_id": "update",
                "method": "PUT",
                "path": "/orders/update",
                "body_template": {"id": "{{id}}"},
                "params": ["id"],
            }
        ],
        "capabilities": [
            {
                "capability_id": "update_order",
                "name": "update_order",
                "title": "更新销售订单",
                "kind": "update",
                "compiled_step_ids": ["update"],
                "requires_human_confirm": False,
                "input_schema": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                },
            }
        ],
    }
    skill = type("Skill", (), {"api_request": api_request})()

    plan = _capability_plans(skill, None, api_request)[0]

    assert plan["is_write"] is True
    assert plan["requires_confirmation"] is True
    assert plan["requires_verify"] is False
    assert plan["fact_checks"] == []


def test_documented_script_input_keeps_nested_json_shape() -> None:
    command = _script_invocation({
        "script": "create_order",
        "requires_confirmation": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "productId": {"type": "string"},
                            "count": {"type": "number"},
                        },
                        "required": ["productId", "count"],
                    },
                }
            },
            "required": ["items"],
        },
    })

    assert (
        '"items":[{"productId":"<items[].productId>",'
        '"count":"<number:items[].count>"}]'
    ) in command
    assert command.endswith(" --confirm")


def test_package_semantics_rejects_unconfirmed_writes_and_missing_combo(tmp_path: Path) -> None:
    contract = {
        "capabilities": [
            {
                "capability_id": "query_detail",
                "name": "query_detail",
                "kind": "query",
                "is_write": False,
                "requires_confirmation": False,
            },
            {
                "capability_id": "update_order",
                "name": "update_order",
                "kind": "update",
                "is_write": True,
                "requires_confirmation": False,
                "requires_human_confirm": False,
            },
        ],
        "intent_branches": [
            {
                "trigger": "先查详情再更新",
                "capability_sequence": ["query_detail", "update_order"],
                "unresolved": [],
            }
        ],
        "clarification_questions": [],
        "routes": [
            {
                "route_id": "update_order",
                "capability_sequence": ["update_order"],
                "requires_confirmation": False,
                "steps": [
                    {
                        "capability_id": "update_order",
                        "confirm_before_execute": False,
                    }
                ],
            }
        ],
    }
    issues: list[dict] = []

    _check_contract_semantics(tmp_path, contract, issues)

    codes = {issue["code"] for issue in issues}
    assert "write_confirmation" in codes
    assert "intent_combo_missing" in codes


def test_stage8_drops_duplicate_grounded_fallback_read() -> None:
    detail = FlowCapability(
        capability_id="detail",
        name="查看销售订单详情",
        title="查看销售订单详情",
        kind="inspect",
        request_refs=[CapabilityRequestRef(
            request_id="req-detail",
            step_id="detail-step",
            usage="execute",
        )],
        input_schema={
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
    )
    fallback = detail.model_copy(deep=True, update={
        "capability_id": "fallback-detail",
        "name": "get_get",
        "title": "get_get",
        "request_refs": [CapabilityRequestRef(
            request_id="req-fallback",
            step_id="fallback-step",
            usage="execute",
        )],
    })
    spec = FlowSpec(
        steps=[
            FlowStep(step_id="detail-step", method="GET", path="/orders/get"),
            FlowStep(step_id="fallback-step", method="GET", path="/orders/get"),
        ],
        capabilities=[detail, fallback],
        meta={"capability_model": {"fallback_added_capabilities": ["get_get"]}},
    )
    # Stage 8 receives the already materialized Stage-6 capability snapshot.
    detail.request_refs = [CapabilityRequestRef(
        request_id="req-detail", step_id="detail-step", usage="execute",
    )]
    fallback.request_refs = [CapabilityRequestRef(
        request_id="req-fallback", step_id="fallback-step", usage="execute",
    )]
    spec.capabilities = [detail, fallback]

    plan = propose_deterministic_plan(
        spec,
        SkillGenerationRequest(
            title="销售订单",
            planning_mode=PlanningMode.DYNAMIC,
            business_description="查看销售订单详情。",
        ),
        {"detail", "fallback-detail"},
        "fp-duplicate-read",
    )

    assert plan.selected_capability_ids == ["detail"]
    duplicate = next(item for item in plan.unused_capabilities if item.capability_id == "fallback-detail")
    assert "重复" in duplicate.reason
    checked = validate_skill_plan(
        plan,
        spec,
        verified_capability_ids={"detail", "fallback-detail"},
        expected_fingerprint="fp-duplicate-read",
    )
    assert checked.ok, checked.errors


def test_stage8_turns_dangling_derived_id_into_caller_input() -> None:
    api_request = {
        "steps": [{"step_id": "detail", "method": "GET", "path": "/orders/get"}],
        "capabilities": [{
            "capability_id": "detail",
            "name": "查看销售订单详情",
            "title": "查看销售订单详情",
            "kind": "inspect",
            "compiled_step_ids": ["detail"],
            "input_schema": {
                "type": "object",
                "properties": {"id": {
                    "type": "string",
                    "x-dano-derived-from-query": True,
                    "x-dano-source-capability": "get_get",
                    "x-dano-source-output": "data.id",
                }},
                "required": ["id"],
            },
        }],
    }
    skill = type("Skill", (), {"api_request": api_request})()

    plan = _capability_plans(skill, None, api_request)[0]
    field = plan["input_schema"]["properties"]["id"]
    forms = _input_forms_md([plan])

    assert field.get("x-dano-derived-from-query") is not True
    assert "| `id` |" in forms
    assert "该能力没有调用方字段" not in forms


def test_stage8_breaks_confirmed_derived_input_cycles() -> None:
    def capability(name: str, source: str, step_id: str) -> dict:
        return {
            "capability_id": name,
            "name": name,
            "title": name,
            "kind": "inspect",
            "compiled_step_ids": [step_id],
            "input_schema": {
                "type": "object",
                "properties": {"id": {
                    "type": "string",
                    "x-dano-derived-from-query": True,
                    "x-dano-source-capability": source,
                    "x-dano-source-output": "data.id",
                }},
                "required": ["id"],
            },
            "output_schema": {
                "type": "object",
                "properties": {"data": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                }},
            },
        }

    api_request = {
        "steps": [
            {"step_id": "a-step", "method": "GET", "path": "/orders/get"},
            {"step_id": "b-step", "method": "GET", "path": "/orders/get"},
        ],
        "capabilities": [capability("a", "b", "a-step"), capability("b", "a", "b-step")],
    }
    spec = FlowSpec(
        capabilities=[
            FlowCapability.model_validate(item) for item in api_request["capabilities"]
        ],
        capability_relations=[
            CapabilityRelation(
                relation_id="a-to-b",
                type="external_transform",
                mode="external_transform",
                from_capability="a",
                from_output="data.id",
                to_capability="b",
                to_input="id",
                confirmed=True,
            ),
            CapabilityRelation(
                relation_id="b-to-a",
                type="external_transform",
                mode="external_transform",
                from_capability="b",
                from_output="data.id",
                to_capability="a",
                to_input="id",
                confirmed=True,
            ),
        ],
    )
    skill = type("Skill", (), {"api_request": api_request})()

    plans = _capability_plans(skill, spec, api_request)

    assert all(
        plan["input_schema"]["properties"]["id"].get("x-dano-derived-from-query") is not True
        for plan in plans
    )


def test_package_validator_only_requires_caller_visible_form_fields(tmp_path: Path) -> None:
    forms = tmp_path / "references" / "INPUT_FORMS.md"
    forms.parent.mkdir(parents=True)
    forms.write_text(
        "# Native input forms\n\n## 查看销售订单详情 (`detail`)\n\n"
        "该能力没有调用方字段，不调用 `ask_user_question`。\n",
        encoding="utf-8",
    )
    contract = {
        "capabilities": [{
            "capability_id": "detail",
            "name": "detail",
            "title": "查看销售订单详情",
            "input_schema": {
                "type": "object",
                "properties": {"id": {
                    "type": "string",
                    "x-dano-derived-from-query": True,
                    "x-dano-source-capability": "query",
                    "x-dano-source-output": "rows[].id",
                }},
                "required": ["id"],
            },
        }],
    }
    issues: list[dict] = []

    _check_input_fact_alignment(tmp_path, contract, issues)

    assert not [item for item in issues if item["code"] == "input_form_missing_field"]


def test_stage8_does_not_auto_route_unconfirmed_field_mapping() -> None:
    relation = CapabilityRelation(
        relation_id="suggested",
        type="external_transform",
        mode="external_transform",
        from_capability="query",
        from_output="rows[].id",
        to_capability="detail",
        to_input="id",
        confirmed=False,
    )

    assert relation_is_usable(relation) is False


def test_confirmed_binding_is_executable_and_visible_in_the_main_route_table() -> None:
    query = _cap("query", "查询销售订单", "query")
    query.output_schema = {
        "type": "object",
        "properties": {
            "data": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
            },
        },
    }
    update = _cap("update", "修改销售订单", "update", required=["id", "items"])
    spec = FlowSpec(
        capabilities=[query, update],
        capability_relations=[CapabilityRelation(
            relation_id="query-to-update",
            type="external_transform",
            mode="external_transform",
            from_capability="query",
            from_output="data.id",
            to_capability="update",
            to_input="id",
            confirmed=True,
        )],
    )
    plan = propose_deterministic_plan(
        spec,
        SkillGenerationRequest(
            title="销售订单",
            planning_mode=PlanningMode.DYNAMIC,
            business_description="先查询销售订单，再修改销售订单。",
        ),
        {"query", "update"},
        "fp-confirmed-binding",
    )
    route = next(
        item for item in plan.routes
        if item.capability_sequence == ["query", "update"] and item.bindings
    )

    assert route.composition_mode.value == "bound"
    assert not route.checkpoints
    assert route.required_user_inputs == ["items"]
    assert [source.source.value for source in route.steps[1].input_sources] == [
        "confirmed_binding",
        "user",
    ]

    skill = type("Skill", (), {"call_metadata": {"skill_plan": plan.model_dump(mode="json")}})()
    table = "\n".join(_workflow_table(skill, [
        {"name": "query", "title": "查询销售订单", "requires_confirmation": False},
        {"name": "update", "title": "修改销售订单", "requires_confirmation": True},
    ]))

    assert "data.id → id" in table
    assert "人工交接，不自动带入" not in next(
        line for line in table.splitlines() if "查询销售订单 → 修改销售订单" in line
    )


def test_sales_playbook_compiles_only_the_natural_business_sequences() -> None:
    spec = FlowSpec(capabilities=[
        _cap("search", "按条件搜索销售订单", "query"),
        _cap("detail", "获取销售订单详细信息", "inspect", required=["id"]),
        _cap("create", "新增销售订单", "create", required=["items"]),
        _cap("update", "修改销售订单信息", "update", required=["id", "items"]),
        _cap("approve", "审核销售订单", "approve", required=["id"]),
        _cap("unapprove", "反审核销售订单", "reject", required=["id"]),
        _cap("delete", "删除销售订单", "delete", required=["ids"]),
        _cap("export", "导出销售订单为Excel", "export"),
    ])
    request = SkillGenerationRequest(
        title="销售订单",
        planning_mode=PlanningMode.DYNAMIC,
        business_description=(
            "通过“按条件搜索销售订单”定位目标单据，可“获取销售订单详细信息”了解完整信息；"
            "新增订单通过“新增销售订单”录入，后续如需调整内容则使用“修改销售订单信息”保存修改；"
            "订单确认后由相应负责人“审核销售订单”使之生效，若已审核订单需要撤回修改，"
            "则执行“取消审核销售订单”回退状态，再通过“更新销售订单”重新调整；"
            "无效订单通过“删除销售订单”清理；所有数据可“导出销售订单为Excel”用于存档。"
        ),
    )

    plan = propose_deterministic_plan(
        spec,
        request,
        {cap.capability_id for cap in spec.capabilities},
        "fp-sales-playbook",
    )
    sequences = {tuple(route.capability_sequence) for route in plan.routes}

    assert ("search", "detail") in sequences
    assert ("create", "update") in sequences
    assert ("unapprove", "update") in sequences
    assert ("approve", "update") not in sequences
    assert ("search", "update") in sequences
    assert ("search", "approve") in sequences
    assert ("search", "unapprove") in sequences
    assert ("search", "delete") in sequences
    assert all("cap_" not in route.route_id for route in plan.routes)


def test_missing_target_request_prefers_search_handoff_over_atomic_write() -> None:
    spec = FlowSpec(capabilities=[
        _cap("search", "查询销售订单", "query"),
        _cap("update", "修改销售订单", "update", required=["id"]),
    ])
    plan = propose_deterministic_plan(
        spec,
        SkillGenerationRequest(
            title="销售订单",
            planning_mode=PlanningMode.DYNAMIC,
            business_description="可以查询销售订单，也可以修改销售订单。没有指定订单时先查询，让用户选择后再修改。",
        ),
        {"search", "update"},
        "fp-search-handoff",
    )

    matches = match_routes(plan, "先查询销售订单，让我选一条，再修改销售订单")

    assert matches
    assert matches[0].capability_sequence == ["search", "update"]
    assert matches[0].checkpoints
    assert matches[0].composition_mode.value == "handoff"


def test_combination_example_never_claims_a_search_step_that_is_not_in_route() -> None:
    spec = FlowSpec(capabilities=[
        _cap("create", "新增销售订单", "create", required=["items"]),
        _cap("update", "修改销售订单", "update", required=["id"]),
    ])
    plan = propose_deterministic_plan(
        spec,
        SkillGenerationRequest(
            title="销售订单",
            planning_mode=PlanningMode.DYNAMIC,
            business_description="先新增销售订单，再修改销售订单。",
        ),
        {"create", "update"},
        "fp-no-fake-search",
    )
    combo = next(route for route in plan.routes if len(route.capability_sequence) == 2)

    assert "先查出目标" not in combo.examples[0].user_request
    assert "<字段>" not in combo.examples[0].user_request
    assert "新增销售订单" in combo.examples[0].user_request
    assert "修改销售订单" in combo.examples[0].user_request


def test_explicit_example_request_is_kept_as_the_route_example() -> None:
    spec = FlowSpec(capabilities=[
        _cap("search", "查询销售订单", "query"),
        _cap("update", "修改销售订单", "update", required=["id"]),
    ])
    user_example = "先查询销售订单，让我选一条，再修改销售订单"
    plan = propose_deterministic_plan(
        spec,
        SkillGenerationRequest(
            title="销售订单",
            planning_mode=PlanningMode.DYNAMIC,
            business_description="先查询销售订单，再修改选中的销售订单。",
            example_requests=[user_example],
        ),
        {"search", "update"},
        "fp-explicit-example",
    )
    combo = next(route for route in plan.routes if route.capability_sequence == ["search", "update"])

    assert combo.examples[0].user_request == user_example
    assert match_routes(plan, user_example)[0].capability_sequence == ["search", "update"]


def test_consumer_contract_and_runtime_plan_exclude_generator_audit_structure() -> None:
    plan = {
        "name": "查询销售订单",
        "capability_id": "cap_deadbeef12345678",
        "title": "按条件搜索销售订单",
        "kind": "query",
        "script": "query_orders",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "该值由用户在录制页面真实填写",
                    "x-flow-path": "query.id",
                    "x-options-snapshot": [{"id": "recorded", "label": "历史值"}],
                }
            },
        },
        "output_schema": {"type": "object"},
        "requires_confirmation": False,
        "requires_verify": False,
        "is_write": False,
        "fact_checks": [],
        "steps": [{
            "step_id": "0123456789ab",
            "method": "GET",
            "path": "/orders/page",
            "params": ["id"],
            "query_template": {"id": "{{id}}"},
            "selects": [{
                "param": "id",
                "source_url": "http://admin.example.test/options",
                "source_method": "POST",
                "source_body": {"keyword": "{{computed_total_2}}"},
                "source_content_type": "application/json",
                "value_key": "id",
                "label_key": "name",
                "option_map": {"历史值": "recorded"},
            }, {
                "param": "status",
                "option_map": {"启用": "active"},
            }],
            "runtime_fields": [
                {"name": "computed_total", "kind": "copy", "result_field": "existing"},
                {
                    "name": "__dano_runtime_deadbeef",
                    "kind": "sum",
                    "left_field": "left",
                    "right_field": "right",
                    "result_field": "total",
                    "sample_verified": True,
                    "schema_identity_path": "computed",
                },
            ],
        }],
        "links": [{
            "source_step": 0,
            "target_step": 1,
            "source_path": "data.id",
            "target_path": "body.id",
            "kind": "field",
        }],
        "contract": {
            "compiled_step_ids": ["0123456789ab"],
            "evidence": [{"request_id": "req_86"}],
        },
    }
    skill_plan = {
        "planning_mode": "dynamic",
        "selected_capability_ids": ["cap_deadbeef12345678"],
        "source_flow_fingerprint": "fingerprint-secret",
        "unused_capabilities": [{"name": "get_get"}],
        "routes": [{
            "route_id": "查询销售订单",
            "name": "查询销售订单",
            "when_to_use": "只查询销售订单",
            "capability_sequence": ["cap_deadbeef12345678"],
            "required_user_inputs": ["id"],
            "bindings": [],
            "steps": [{
                "step_key": "query",
                "capability_id": "cap_deadbeef12345678",
                "input_sources": [{"field": "id", "source": "user"}],
                "confirm_before_execute": False,
                "done_when": "返回结果",
                "on_failure": "停止",
            }],
            "checkpoints": [],
            "requires_confirmation": False,
            "done_when": "返回结果",
            "failure_behavior": "失败即停止",
        }],
    }

    contract = _consumer_contract(
        type("Skill", (), {"skill_id": "sales", "title": "销售订单"})(),
        [plan],
        skill_plan,
    )
    runtime = _runtime_plan(plan)
    packed = str(contract) + str(runtime)

    for marker in (
        "cap_deadbeef12345678", "compiled_step_ids", "request_id",
        "source_flow_fingerprint", "fingerprint-secret", "get_get",
        "x-options-snapshot", "x-flow-path",
        "录制页面", "sample_verified", "schema_identity_path", "历史值",
        "__dano_runtime", "source_step", "source_url", "source_method",
        "source_body", "source_content_type",
    ):
        assert marker not in packed
    assert contract["selected_operations"] == ["查询销售订单"]
    assert contract["routes"][0]["operation_sequence"] == ["查询销售订单"]
    assert runtime["steps"][0]["path"] == "/orders/page"
    assert runtime["steps"][0]["selects"][0]["endpoint"] == "http://admin.example.test/options"
    assert runtime["steps"][0]["selects"][0]["method"] == "POST"
    assert runtime["steps"][0]["selects"][0]["body_template"] == {"keyword": "{{computed_total_2}}"}
    assert "option_map" not in runtime["steps"][0]["selects"][0]
    assert runtime["steps"][0]["selects"][1]["option_map"] == {"启用": "active"}
    assert runtime["steps"][0]["runtime_fields"][0]["name"] == "computed_total"
    assert runtime["steps"][0]["runtime_fields"][1]["name"] == "computed_total_2"
    assert runtime["links"][0]["from_index"] == 0
    assert runtime["links"][0]["to_index"] == 1
    assert runtime["links"][0]["read_path"] == "data.id"
    assert runtime["links"][0]["write_path"] == "body.id"


def test_dynamic_options_do_not_export_a_historical_enum_snapshot() -> None:
    schema = _public_schema({
        "type": "object",
        "properties": {
            "customerId": {
                "type": "string",
                "description": "页面枚举选项",
                "enum": ["old-id"],
                "x-enum-options": [{"value": "old-id", "label": "历史客户"}],
                "x-options": [{"value": "old-id", "label": "历史客户"}],
                "x-options-source": {"source_url": "/customers"},
                "x-options-incomplete": True,
            },
        },
    })

    field = schema["properties"]["customerId"]
    assert "enum" not in field
    assert "x-enum-options" not in field
    assert "x-options" not in field
    assert "x-options-incomplete" not in field
    assert "历史客户" not in str(schema)
    assert field["description"] == "运行时获取当前有效候选，不使用历史候选快照。"


def test_main_workflow_table_uses_progressive_disclosure() -> None:
    skill = type("Skill", (), {
        "call_metadata": {"skill_plan": {"routes": [
            {
                "route_id": "query-order",
                "name": "查询订单",
                "when_to_use": "用户只需要查询订单",
                "capability_sequence": ["query"],
                "requires_confirmation": False,
            },
            {
                "route_id": "query-then-update",
                "name": "查询后修改订单",
                "when_to_use": "用户要先查询再修改选中订单",
                "capability_sequence": ["query", "update"],
                "checkpoints": [{"prompt": "请从查询结果中选定目标"}],
                "requires_confirmation": True,
                "done_when": "目标订单已确认修改成功",
            },
        ]}},
        "api_request": {},
    })()
    plans = [
        {"capability_id": "query", "name": "query", "title": "查询订单", "requires_confirmation": False},
        {"capability_id": "update", "name": "update", "title": "修改订单", "requires_confirmation": True},
    ]

    table = "\n".join(_workflow_table(skill, plans))

    assert "调用入口" not in table
    assert "scripts/" not in table
    assert "| 用户意图 | 路线 | 步骤顺序 | 跨步数据 | 何时停问 | 确认点 | 完成条件 | 详情 |" in table
    assert "查询订单 → 修改订单" in table
    assert "人工交接，不自动带入" in table
    assert "请从查询结果中选定目标" in table
    assert "目标订单已确认修改成功" in table
    assert "references/CAPABILITIES.md" in table
    assert "references/routes/查询订单-然后-修改订单.md" in table


def test_capability_index_includes_a_business_output_overview() -> None:
    text = _capabilities_md(type("Skill", (), {"call_metadata": {}})(), [{
        "name": "view_order",
        "title": "查看销售订单详情",
        "kind": "inspect",
        "script": "view_order",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "number", "label": "记录编号"}},
            "required": ["id"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "number"},
                "data": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "number", "label": "记录编号"},
                        "no": {"type": "string", "label": "业务编号"},
                        "items": {"type": "array", "label": "明细"},
                    },
                },
                "msg": {"type": "string"},
            },
        },
        "requires_confirmation": False,
        "is_write": False,
    }])

    assert "关键输出概况" in text
    assert "记录编号、业务编号、明细" in text


def test_rendered_package_is_executable_and_contains_no_generation_vocabulary(tmp_path: Path) -> None:
    spec = FlowSpec(capabilities=[
        _cap("query", "查询工作记录", "query", required=["ownerId"]),
        _cap("create", "新增工作记录", "create", required=["items"]),
    ])
    plan = propose_deterministic_plan(
        spec,
        SkillGenerationRequest(
            title="工作记录",
            planning_mode=PlanningMode.DYNAMIC,
            business_description=(
                "核对已有记录并补充缺失项时，先「查询工作记录」，再「新增工作记录」。"
                "查询结果列出已有项和待处理项；新增范围仅限用户确认的未存在项目。"
            ),
        ),
        {"query", "create"},
        "rendered-package-fingerprint",
    )
    capabilities = [
        {
            "capability_id": "query",
            "name": "query_records",
            "title": "查询工作记录",
            "kind": "query",
            "input_schema": {
                "type": "object",
                "properties": {
                    "ownerId": {
                        "type": "string",
                        "x-dano-option-source": {
                            "source_url": "https://example.test/users",
                            "source_method": "GET",
                            "value_key": "id",
                            "label_key": "name",
                        },
                    },
                },
                "required": ["ownerId"],
            },
            "output_schema": {"type": "object"},
            "requires_human_confirm": False,
            "execution_contract": {
                "steps": [{
                    "step_id": "query-step",
                    "method": "GET",
                    "url": "https://example.test/records",
                    "path": "/records",
                    "params": ["ownerId"],
                    "query_template": {"ownerId": "{{ownerId}}"},
                    "selects": [{
                        "param": "ownerId",
                        "source_url": "https://example.test/users",
                        "source_method": "GET",
                        "value_key": "id",
                        "label_key": "name",
                    }],
                }],
                "links": [],
                "verification_ids": [],
            },
        },
        {
            "capability_id": "create",
            "name": "create_records",
            "title": "新增工作记录",
            "kind": "create",
            "input_schema": {
                "type": "object",
                "properties": {
                    "items": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["items"],
            },
            "output_schema": {"type": "object"},
            "requires_human_confirm": True,
            "execution_contract": {
                "steps": [{
                    "step_id": "create-step",
                    "method": "POST",
                    "url": "https://example.test/records",
                    "path": "/records",
                    "content_type": "application/json",
                    "body_template": {"items": "{{items}}"},
                }],
                "links": [],
                "verification_ids": [],
            },
        },
    ]
    skill = SkillSpec(
        skill_id="erp.work-records",
        tenant="test",
        subsystem=Subsystem("erp"),
        action="work-records",
        risk_level=RiskLevel.L3,
        title="工作记录",
        api_request={"capabilities": capabilities, "_skill_plan": plan.model_dump(mode="json")},
        call_metadata={"skill_plan": plan.model_dump(mode="json")},
        capabilities=capabilities,
    )

    folder = tmp_path / render_skill_package(skill, str(tmp_path), tenant="test")
    validation = validate_skill_package(folder)
    packed = "\n".join(
        path.read_text(encoding="utf-8")
        for path in folder.rglob("*")
        if path.is_file() and path.suffix in {".md", ".json", ".py"}
    )

    assert validation["ok"] is True
    for marker in (
        "__dano_runtime", "source_step", "source_url", "source_method",
        "source_body", "source_content_type", "x-options", "录制页面", "历史样本",
        "step_id", "failed_step", "link_id", "verification_id",
    ):
        assert marker not in packed
    handbook = (folder / "SKILL.md").read_text(encoding="utf-8")
    assert "references/routes/查询工作记录-然后-新增工作记录.md" in handbook
    assert "确认哪些项目仍需新增" in handbook
    assert "组合行必须按该行步骤顺序执行" in handbook
    assert "不要把多条路线合并" not in handbook
    assert "scripts/format_list.py" in handbook
    assert "按用户说明办理" in handbook
    assert "没有已确认绑定" in handbook


def test_business_labels_and_complete_dynamic_data_source_are_rendered() -> None:
    assert _field_label("id", {}) == "记录编号"
    assert _field_label("ids", {}) == "记录编号（可多选）"
    assert _field_label("items", {}) == "明细"
    forms = _input_forms_md([{
        "name": "update_order",
        "title": "修改销售订单",
        "requires_confirmation": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "customerId": {
                    "type": "string",
                    "x-dano-option-source": {
                        "source_url": "http://admin.example.test/customers/simple-list",
                        "method": "GET",
                        "value_key": "id",
                        "label_key": "name",
                    },
                }
            },
            "required": ["customerId"],
        },
    }])

    assert '"params": {}' in forms
    assert '"resultPath": "data"' in forms
    assert "删除已由当前对话提供且通过校验的字段" in forms
    assert "没有可确认的 formId" in forms
    assert "历史样本" not in forms


def test_public_schema_does_not_turn_items_field_into_a_fake_property() -> None:
    schema = _public_schema({
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {"type": "object"},
            },
        },
        "required": ["items"],
    })

    assert set(schema["properties"]) == {"items"}
    assert schema["properties"]["items"]["label"] == "明细"


def test_list_formatter_contains_only_public_output_schema() -> None:
    source = _format_list_py([{
        "name": "查询销售订单",
        "output_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "number", "x-dano-identifier-role": "record"},
                "secret": {"type": "string", "x-dano-internal": True},
            },
        },
    }])

    assert "x-dano-" not in source
    assert "secret" not in source


def test_frontmatter_description_is_concise_and_does_not_copy_full_playbook() -> None:
    plans = [
        {"name": "查询销售订单", "title": "查询销售订单"},
        {"name": "修改销售订单", "title": "修改销售订单"},
    ]
    skill = type("Skill", (), {
        "title": "销售订单",
        "call_metadata": {"skill_plan": {
            "composition_summary": "这是一段很长的编排叙述，包含内部全部流程，而且不应逐字进入 description。",
        }},
        "api_request": {},
    })()

    heading, description = _skill_description(skill, plans, None)

    assert heading == "销售订单"
    assert "查询" in description and "修改" in description
    assert "很长的编排叙述" not in description
    assert "不用于" in description
    assert len(description) < 260


def test_chinese_operation_names_keep_readable_script_slugs() -> None:
    assert _script_slug("查询工作汇报统计") == "查询工作汇报统计"
    assert _script_slug("query_records") == "query_records"
    assert _script_slug("Query-Records") == "query_records"


def test_options_and_input_forms_use_the_same_field_option_labels() -> None:
    field = {
        "type": "string",
        "title": "统计周期",
        "enum": ["1", "2", "3"],
        "x-options-snapshot": [
            {"id": "1", "label": "周期甲"},
            {"id": "2", "label": "周期乙"},
            {"id": "3", "label": "周期丙"},
        ],
    }
    plans = [{
        "name": "query_stats",
        "title": "查询统计",
        "input_schema": {
            "type": "object",
            "properties": {"reportType": field},
        },
    }]

    forms = _input_forms_md(plans)
    options = _options_md(plans)

    assert "周期甲" in forms
    assert "周期甲" in options
    assert forms.count("周期甲") >= 1


def test_public_schema_scrubs_implementation_and_recording_leaks() -> None:
    schema = _public_schema({
        "type": "object",
        "properties": {
            "endDate": {
                "type": "date",
                "format": "date",
                "title": "结束日期",
                "description": "用户通过日期选择器填写；提交 query.endDate。",
            },
            "title": {
                "type": "string",
                "description": "表单文本输入框，placeholder提示可选；提交 body.title。",
            },
            "items": {
                "type": "array",
                "description": "提交 body.items 数组。本场为空数组。",
                "items": {"type": "object"},
            },
            "reportType": {
                "type": "string",
                "description": "从URL参数reportType带入，当前禁用不可改。",
            },
        },
    })
    packed = str(schema)

    assert "提交 query" not in packed
    assert "提交 body" not in packed
    assert "本场" not in packed
    assert "placeholder" not in packed
    assert "URL参数" not in packed
    assert "当前禁用" not in packed
    assert schema["properties"]["items"]["description"] == "按当前请求提供符合 schema 的 JSON 数组。"
    assert schema["properties"]["items"]["label"] == "明细"
    assert schema["properties"]["endDate"]["label"] == "结束日期"


def test_array_form_question_asks_for_schema_json_not_a_fake_control() -> None:
    forms = _input_forms_md([{
        "name": "create_record",
        "title": "新增记录",
        "input_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "title": "明细",
                    "items": {
                        "type": "object",
                        "properties": {"content": {"type": "string", "title": "工作内容"}},
                    },
                },
            },
            "required": ["items"],
        },
    }])

    assert "# 输入表单" in forms
    assert "JSON 数组" in forms
    assert "content" in forms


def test_result_then_playbook_renders_combination_route_and_readable_scripts(tmp_path: Path) -> None:
    spec = FlowSpec(capabilities=[
        _cap("stats", "查询工作汇报统计", "query"),
        _cap("create", "新增并提交工作日报", "create", required=["title"]),
    ])
    playbook = "先查询工作汇报统计，根据返回进行新增"
    plan = propose_deterministic_plan(
        spec,
        SkillGenerationRequest(
            title="日报填写",
            planning_mode=PlanningMode.DYNAMIC,
            business_description=playbook,
        ),
        {"stats", "create"},
        "result-then-render",
    )
    capabilities = [
        {
            "capability_id": "stats",
            "name": "查询工作汇报统计",
            "title": "查询工作汇报统计",
            "kind": "query",
            "input_schema": {"type": "object", "properties": {}},
            "output_schema": {"type": "object"},
            "requires_human_confirm": False,
            "execution_contract": {
                "steps": [{
                    "step_id": "stats-step",
                    "method": "GET",
                    "url": "https://example.test/stats",
                    "path": "/stats",
                }],
                "links": [],
                "verification_ids": [],
            },
        },
        {
            "capability_id": "create",
            "name": "新增并提交工作日报",
            "title": "新增并提交工作日报",
            "kind": "create",
            "input_schema": {
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
            },
            "output_schema": {"type": "object"},
            "requires_human_confirm": True,
            "execution_contract": {
                "steps": [{
                    "step_id": "create-step",
                    "method": "POST",
                    "url": "https://example.test/create",
                    "path": "/create",
                    "content_type": "application/json",
                    "body_template": {"title": "{{title}}"},
                }],
                "links": [],
                "verification_ids": [],
            },
        },
    ]
    skill = SkillSpec(
        skill_id="oa.work-report",
        tenant="test",
        subsystem=Subsystem("oa"),
        action="work-report",
        risk_level=RiskLevel.L3,
        title="日报填写",
        api_request={"capabilities": capabilities, "_skill_plan": plan.model_dump(mode="json")},
        call_metadata={"skill_plan": plan.model_dump(mode="json")},
        capabilities=capabilities,
    )

    folder = tmp_path / render_skill_package(skill, str(tmp_path), tenant="test")
    validation = validate_skill_package(folder)
    handbook = (folder / "SKILL.md").read_text(encoding="utf-8")
    contract = (folder / "references" / "CONTRACT.json").read_text(encoding="utf-8")
    scripts = {path.name for path in (folder / "scripts").glob("*.py")}

    assert validation["ok"] is True, validation
    assert playbook in handbook
    assert "references/routes/查询工作汇报统计-然后-新增并提交工作日报.md" in handbook
    assert "确认哪些项目仍需新增" in handbook
    assert "按用户说明办理" in handbook
    assert "没有已确认绑定" in handbook
    assert "name: 日报填写" in handbook
    assert "capability_" not in "\n".join(scripts)
    assert "查询工作汇报统计.py" in scripts
    assert "新增并提交工作日报.py" in scripts
    assert "提交 query" not in contract
    assert "本场" not in contract
    assert (folder / "references" / "routes" / "查询工作汇报统计-然后-新增并提交工作日报.md").exists()
