"""Stage-8 regressions for executable routing and consumer-facing Skill manuals."""

from __future__ import annotations

from pathlib import Path

from dano.execution.page.flow_spec import FlowCapability, FlowSpec
from dano.export.skill_package.renderer import (
    _capabilities_md,
    _capability_plans,
    _options_md,
    _script_invocation,
)
from dano.export.skill_package.validator import _check_contract_semantics
from dano.onboarding.skill_generation import (
    PlanningMode,
    SkillGenerationRequest,
    propose_deterministic_plan,
    validate_skill_plan,
)


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
