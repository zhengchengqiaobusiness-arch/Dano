"""Frozen sales-order baseline for stage-8 Skill route compilation.

The documented evaluation sample had seven callable capabilities, empty
confirmed relations, and a business description that already named the
query / view / create / edit / approve / unapprove / delete handling
order. This module reconstructs that request and contract so later
phases can prove the current “description without executable routes”
gap and then close it.
"""

from __future__ import annotations

from pathlib import Path

from dano.execution.page.flow_spec import CapabilityRelation, FlowCapability, FlowSpec, FlowStep
from dano.onboarding.skill_generation.models import PlanningMode, SkillGenerationRequest

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "stage8_sale_order"

SALE_ORDER_TITLES = (
    ("cap_search", "search_sale_orders", "搜索/筛选销售订单", "query"),
    ("cap_detail", "get_sale_order", "查看销售订单详情", "query"),
    ("cap_update", "update_sale_order", "修改销售订单", "submit"),
    ("cap_approve", "approve_sale_order", "审批销售订单", "submit"),
    ("cap_unapprove", "unapprove_sale_order", "反审销售订单", "submit"),
    ("cap_create", "create_sale_order", "新建销售订单", "submit"),
    ("cap_delete", "delete_sale_order", "删除销售订单", "delete"),
)

SALE_ORDER_DESCRIPTION = (
    "销售订单办理：用户通常会说查订单、看详情、新增、编辑、审核、反审核或删除。"
    "办理顺序是先查询或查看，再对选中的订单做编辑、审核、反审核或删除；新增是单独创建。"
    "只查询或只查看时不要写入。缺目标时必须让用户选定一条。写完要能确认结果。"
)

SALE_ORDER_EXAMPLES = [
    "帮我查鲜生的单",
    "只看看这张订单详情",
    "先查出订单再编辑",
    "查出后再审核那张",
    "新增一张销售订单",
]


def _cap(
    *,
    capability_id: str,
    name: str,
    title: str,
    kind: str,
    required: list[str] | None = None,
    input_props: dict | None = None,
    output_props: dict | None = None,
    confirm: bool = False,
    step_ids: list[str] | None = None,
) -> FlowCapability:
    properties = input_props or {item: {"type": "string"} for item in (required or [])}
    members = list(step_ids or [])
    return FlowCapability(
        capability_id=capability_id,
        name=name,
        title=title,
        kind=kind,
        step_ids=members,
        nodes=[
            {"id": f"call_{index + 1}", "type": "call", "step_id": step_id}
            for index, step_id in enumerate(members)
        ],
        requires_human_confirm=confirm or kind in {"submit", "delete", "withdraw"},
        input_schema={
            "type": "object",
            "properties": properties,
            "required": list(required or []),
        },
        output_schema={
            "type": "object",
            "properties": output_props or {},
        },
    )


def sale_order_spec(*, relations: list[CapabilityRelation] | None = None) -> FlowSpec:
    """Seven verified sales-order capabilities with no confirmed bindings by default."""
    return FlowSpec(
        tenant="tenant",
        subsystem="oa",
        title="销售订单",
        steps=[
            FlowStep(
                step_id=f"s{index + 1}",
                method="GET" if kind == "query" else "POST",
                path=f"/erp/{name}",
            )
            for index, (_cid, name, _title, kind) in enumerate(SALE_ORDER_TITLES)
        ],
        capabilities=[
            _cap(
                capability_id=cid,
                name=name,
                title=title,
                kind=kind,
                step_ids=[f"s{index + 1}"],
                required=["id"] if kind != "query" else [],
                confirm=kind != "query",
                output_props=(
                    {
                        "records": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "label": {"type": "string"},
                                },
                            },
                        }
                    }
                    if kind == "query"
                    else {}
                ),
                input_props=(
                    {"id": {"type": "string"}}
                    if kind != "query"
                    else {}
                ),
            )
            for index, (cid, name, title, kind) in enumerate(SALE_ORDER_TITLES)
        ],
        capability_relations=list(relations or []),
    )


def sale_order_request() -> SkillGenerationRequest:
    return SkillGenerationRequest(
        title="销售订单办理",
        business_description=SALE_ORDER_DESCRIPTION,
        planning_mode=PlanningMode.DYNAMIC,
        example_requests=list(SALE_ORDER_EXAMPLES),
        success_criteria="已按用户指定的那条路线办理完成，写入已确认且结果可核对",
        forbidden_actions="",
    )


def sale_order_verified_ids(spec: FlowSpec | None = None) -> set[str]:
    current = spec or sale_order_spec()
    return {str(cap.capability_id) for cap in current.capabilities if cap.capability_id}


def route_has_human_checkpoint(route) -> bool:  # noqa: ANN001
    """True when a route carries a structured human hand-off, not just a note."""
    checkpoints = getattr(route, "checkpoints", None)
    if checkpoints:
        return True
    for step in getattr(route, "steps", None) or []:
        if getattr(step, "checkpoint", None):
            return True
    return False


def combination_routes(plan) -> list:  # noqa: ANN001
    return [route for route in plan.routes if len(route.capability_sequence) > 1]
