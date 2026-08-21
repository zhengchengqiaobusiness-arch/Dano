"""Read-only views of verified capabilities and confirmed relations."""

from __future__ import annotations

from typing import Any

from dano.execution.page.capability_kinds import READ_CAPABILITY_KINDS, WRITE_CAPABILITY_KINDS
from dano.execution.page.flow_spec_core.models import CapabilityRelation, FlowCapability, FlowSpec

RISK_WRITE_KINDS = frozenset({
    "delete", "withdraw", "submit", "submit_batch", "approve", "reject",
})
_CONFIRMED_EVIDENCE = frozenset({"user_confirmed", "manual", "manual_relation", "typed_capability_contract"})
_FIELD_MAPPED = frozenset({"external_transform", "data_mapping", "field_mapping"})


def capability_ref(cap: FlowCapability) -> str:
    return str(cap.capability_id or cap.name or "")


def capability_by_id(spec: FlowSpec) -> dict[str, FlowCapability]:
    index: dict[str, FlowCapability] = {}
    for cap in spec.capabilities:
        for key in (cap.capability_id, cap.name):
            if key:
                index[str(key)] = cap
    return index


def is_write_capability(cap: FlowCapability) -> bool:
    kind = str(cap.kind or "").strip().lower()
    if kind in WRITE_CAPABILITY_KINDS:
        return True
    if kind in READ_CAPABILITY_KINDS:
        return False
    return bool(cap.requires_human_confirm)


def capability_family(cap: FlowCapability) -> str:
    """Classify a packed capability for route compilation. Does not invent kinds."""
    kind = str(cap.kind or "").strip().lower()
    title = f"{cap.title} {cap.name} {cap.intent}"
    if kind == "list_options" or any(token in title for token in ("选项", "字典", "下拉", "候选")):
        return "option"
    if kind in READ_CAPABILITY_KINDS or any(token in title for token in ("查询", "查看", "列表", "检索", "筛选")):
        return "query"
    if is_write_capability(cap) or any(token in title for token in ("提交", "保存", "审批", "写入", "新建", "编辑", "更新")):
        return "write"
    return kind or "other"


def is_risk_write(cap: FlowCapability) -> bool:
    return str(cap.kind or "").strip().lower() in RISK_WRITE_KINDS or is_write_capability(cap)


def schema_properties(schema: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    raw = schema if isinstance(schema, dict) else {}
    properties = raw.get("properties")
    if isinstance(properties, dict):
        return {str(key): value for key, value in properties.items() if isinstance(value, dict)}
    return {}


def schema_required(schema: dict[str, Any] | None) -> list[str]:
    raw = schema if isinstance(schema, dict) else {}
    required = raw.get("required")
    if isinstance(required, list):
        return [str(item) for item in required if str(item)]
    return []


def field_type(schema: dict[str, Any] | None, path: str) -> str:
    node = schema_node(schema, path)
    if not node:
        return ""
    return str(node.get("type") or "string")


def schema_node(schema: dict[str, Any] | None, path: str) -> dict[str, Any] | None:
    from dano.execution.page.capability_io import _schema_node_at_path

    raw = str(path or "").strip()
    if not raw:
        return None
    node = _schema_node_at_path(schema, raw)
    if node is not None:
        return node
    properties = schema_properties(schema)
    return properties.get(raw)


def schema_has_field(schema: dict[str, Any] | None, path: str) -> bool:
    return schema_node(schema, path) is not None


def types_compatible(source_type: str, target_type: str) -> bool:
    from dano.execution.page.capability_contracts import _capability_types_compatible

    return _capability_types_compatible(source_type, target_type)


def field_cardinality(schema: dict[str, Any] | None, path: str) -> str:
    """Return one/many for a schema path. Empty means unknown."""

    node = schema_node(schema, path)
    if node is None:
        raw = str(path or "")
        if "[]" in raw or "[*]" in raw:
            return "many" if raw.rstrip(".").endswith(("[]", "[*]")) else "one"
        return ""
    typ = str(node.get("type") or "").strip().lower()
    if typ == "array":
        return "many"
    return "one"


def cardinality_compatible(
    source_schema: dict[str, Any] | None,
    source_path: str,
    target_schema: dict[str, Any] | None,
    target_path: str,
    *,
    source_selector: str = "",
) -> bool:
    """Array values cannot bind to a scalar (or the reverse) without an item selector."""

    resolved_source = str(source_selector or source_path or "").strip()
    source_card = field_cardinality(source_schema, resolved_source)
    if not source_card:
        source_card = field_cardinality(source_schema, source_path)
    target_card = field_cardinality(target_schema, target_path)
    if not source_card or not target_card:
        return True
    if source_card == target_card:
        return True
    extracts_item = "[]" in resolved_source or "[*]" in resolved_source
    if source_card == "many" and target_card == "one":
        return extracts_item
    return False


def confirmed_fixed_or_system_inputs(cap: FlowCapability) -> dict[str, str]:
    """Return required inputs already satisfied by the capability contract."""

    satisfied: dict[str, str] = {}
    for field in list(cap.inputs or []) + list(cap.request_fields or []) + list(cap.computed_fields or []):
        name = str(field.key or field.path or "")
        if not name:
            continue
        source = field.source if isinstance(field.source, dict) else {}
        kind = str(source.get("kind") or field.source_kind or field.category or "")
        if kind in {"constant", "fixed", "system", "auto", "computed"} and (
            field.confirmed or field.category in {"system", "computed", "internal"}
        ):
            satisfied[name] = "system_value" if kind in {"system", "auto", "computed"} else "fixed_value"
        if not field.exposed_to_caller and field.category in {"system", "computed", "internal"}:
            satisfied[name] = satisfied.get(name) or "system_value"
    return satisfied


def verified_capability_ids(
    spec: FlowSpec,
    *,
    stage_seven: dict[str, Any] | None = None,
    explicit: set[str] | None = None,
) -> set[str]:
    if explicit is not None:
        return {str(item) for item in explicit if str(item)}
    checkpoint = stage_seven if isinstance(stage_seven, dict) else {}
    verdict = checkpoint.get("verdict") if isinstance(checkpoint.get("verdict"), dict) else {}
    callable_ids = [
        str(item)
        for item in (verdict.get("callable_capability_ids") or checkpoint.get("callable_capability_ids") or [])
        if str(item)
    ]
    if callable_ids:
        return set(callable_ids)
    results = verdict.get("capability_results") or checkpoint.get("capability_results") or {}
    if isinstance(results, dict) and results:
        passed = {
            str(key)
            for key, value in results.items()
            if str((value or {}).get("status") if isinstance(value, dict) else "") in {"verified", "passed"}
        }
        if passed:
            return passed
    if str(checkpoint.get("status") or verdict.get("status") or "") == "verified":
        return {capability_ref(cap) for cap in spec.capabilities if capability_ref(cap)}
    return set()


def relation_is_usable(relation: CapabilityRelation) -> bool:
    relation_type = str(relation.type or "").strip().lower()
    mode = str(relation.mode or "").strip().lower()
    evidence_kind = str((relation.evidence or {}).get("kind") or "").strip().lower()
    if relation_type == "suggested_call_chain" and not relation.confirmed:
        return False
    if relation.confirmed or evidence_kind in _CONFIRMED_EVIDENCE:
        return True
    if mode in _FIELD_MAPPED and relation.from_output and relation.to_input:
        return True
    return False


def usable_relations(spec: FlowSpec) -> list[CapabilityRelation]:
    return [relation for relation in spec.capability_relations or [] if relation_is_usable(relation)]


def public_capability_catalog(spec: FlowSpec, verified_ids: set[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for cap in spec.capabilities:
        cap_id = capability_ref(cap)
        if cap_id not in verified_ids and cap.name not in verified_ids:
            continue
        rows.append({
            "capability_id": cap.capability_id,
            "name": cap.name,
            "title": cap.title or cap.name,
            "kind": cap.kind,
            "intent": cap.intent,
            "write": is_write_capability(cap),
            "requires_confirmation": bool(cap.requires_human_confirm or is_write_capability(cap)),
            "input_schema": dict(cap.input_schema or {}),
            "output_schema": dict(cap.output_schema or {}),
            "required_inputs": schema_required(cap.input_schema),
            "satisfied_inputs": confirmed_fixed_or_system_inputs(cap),
        })
    return rows
