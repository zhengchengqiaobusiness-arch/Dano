"""Stage-8 regressions for executable routing and consumer-facing Skill manuals."""

from __future__ import annotations

from pathlib import Path

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
    _skill_description,
)
from dano.export.skill_package.validator import (
    _check_contract_semantics,
    _check_input_fact_alignment,
)
from dano.onboarding.skill_generation import (
    PlanningMode,
    SkillGenerationRequest,
    propose_deterministic_plan,
    validate_skill_plan,
)
from dano.onboarding.skill_generation.catalog import relation_is_usable
from dano.onboarding.skill_generation.quality import match_routes


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
            "通过“按条件搜索销售订单”定位目标单据，“获取销售订单详细信息”了解完整信息；"
            "使用“新增销售订单”录入新订单，后续调整内容则通过“修改销售订单信息”保存修改；"
            "订单确认后“审核销售订单”生效，若需撤回修改则“反审核销售订单”后重新修改；"
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
    assert "新增销售订单" in combo.examples[0].user_request
    assert "修改销售订单" in combo.examples[0].user_request


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
                "value_key": "id",
                "label_key": "name",
                "option_map": {"历史值": "recorded"},
            }],
            "runtime_fields": [{
                "name": "computed",
                "kind": "sum",
                "left_field": "left",
                "right_field": "right",
                "sample_verified": True,
                "schema_identity_path": "computed",
            }],
        }],
        "links": [],
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
    ):
        assert marker not in packed
    assert contract["selected_operations"] == ["查询销售订单"]
    assert contract["routes"][0]["operation_sequence"] == ["查询销售订单"]
    assert runtime["steps"][0]["path"] == "/orders/page"


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
