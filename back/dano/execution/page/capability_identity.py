"""Stage 6: identifier relations grounded from recorded values."""
from __future__ import annotations

from typing import Any
import copy
import hashlib
import re
from dano.execution.page.flow_spec_core.models import (
    CapabilityRelation,
    FlowCapability,
    FlowSpec,
    FlowStep,
)
from dano.execution.page.recording_facts import (
    _BORING_LINK_VALUES,
)


_IDENTIFIER_ROLE_BY_FIELD = {
    "processinstanceid": "process_instance",
    "workflowinstanceid": "process_instance",
    "flowinstanceid": "process_instance",
    "billcode": "business_document",
    "billno": "business_document",
    "documentcode": "business_document",
    "documentno": "business_document",
    "documentnumber": "business_document",
    "applicationno": "business_document",
    "applyno": "business_document",
    "recordid": "record",
    "applicationid": "record",
    "applyid": "record",
    "id": "record",
}


_IDENTIFIER_ROLE_TITLE = {
    "process_instance": "流程实例ID",
    "business_document": "业务编号",
    "record": "记录ID",
}


_IDENTIFIER_RELATION_TARGET_KINDS = {
    "inspect", "update", "approve", "reject", "withdraw", "delete",
}


def _identifier_role_for_field(name: Any) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "", str(name or "").casefold())
    return _IDENTIFIER_ROLE_BY_FIELD.get(normalized, "")


def _annotate_identifier_sources(
    schema: dict[str, Any],
    sample: Any,
    *,
    path: str = "",
) -> list[dict[str, Any]]:
    """Mark stable identifier leaves and retain their recorded values as evidence."""
    found: list[dict[str, Any]] = []
    schema_type = str(schema.get("type") or "")
    if schema_type == "array":
        item_schema = schema.get("items") if isinstance(schema.get("items"), dict) else {}
        values = sample if isinstance(sample, list) else []
        item_path = f"{path}[]" if path else "[]"
        if values:
            for item in values[:80]:
                found.extend(_annotate_identifier_sources(item_schema, item, path=item_path))
        else:
            found.extend(_annotate_identifier_sources(item_schema, None, path=item_path))
        return found
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    if properties:
        sample_object = sample if isinstance(sample, dict) else {}
        for name, field_schema in properties.items():
            if not isinstance(field_schema, dict):
                continue
            field_path = f"{path}.{name}" if path else str(name)
            role = _identifier_role_for_field(name)
            if role and field_schema.get("type") not in {"object", "array"}:
                field_schema["x-dano-identifier-role"] = role
                found.append({
                    "path": field_path,
                    "role": role,
                    "values": {
                        str(sample_object[name])
                        for _ in [0]
                        if name in sample_object
                        and sample_object[name] not in (None, "")
                        and not isinstance(sample_object[name], (dict, list))
                    },
                })
            found.extend(_annotate_identifier_sources(
                field_schema,
                sample_object.get(name),
                path=field_path,
            ))
    return found


def _target_input_values(
    capability: FlowCapability,
    input_name: str,
    field_schema: dict[str, Any],
    step_by_id: dict[str, FlowStep],
) -> set[str]:
    values = {
        str(field_schema["default"])
        for _ in [0]
        if field_schema.get("default") not in (None, "")
        and not isinstance(field_schema.get("default"), (dict, list))
    }
    wire_path = str(field_schema.get("x-flow-path") or "")
    for step_id in _capability_scoped_step_ids(capability):
        step = step_by_id.get(step_id)
        if step is None:
            continue
        for param in step.params or []:
            if not (
                input_name in {str(param.key or ""), str(param.label or "")}
                or (wire_path and wire_path == str(param.path or ""))
            ):
                continue
            for value in (param.value, param.default_value):
                if value not in (None, "") and not isinstance(value, (dict, list)):
                    values.add(str(value))
    return values


def _identifier_value_is_grounding_evidence(value: str) -> bool:
    text = str(value or "").strip()
    return (
        len(text) >= 6
        and text.casefold() not in _BORING_LINK_VALUES
        and not re.fullmatch(r"\d{1,5}", text)
    )


def _ground_recorded_identifier_relations(
    spec: FlowSpec,
    step_by_id: dict[str, FlowStep],
) -> FlowSpec:
    """Bind later mutations to the exact identifier field observed in a query.

    Public labels and the generic wire name ``id`` are not evidence. A relation
    is generated only when one recorded mutation value matches exactly one
    semantically named identifier field in a recorded business-query result.
    """
    generated_kind = "recorded_identifier_match"
    spec.capability_relations = [
        relation
        for relation in (spec.capability_relations or [])
        if str((relation.evidence or {}).get("kind") or "") != generated_kind
    ]

    sources: list[dict[str, Any]] = []
    for capability in spec.capabilities or []:
        if capability.kind not in {"query", "query_status", "inspect"}:
            continue
        sample = _capability_output_samples(capability, step_by_id)
        for item in _annotate_identifier_sources(capability.output_schema or {}, sample):
            if item["values"]:
                sources.append({
                    **item,
                    "capability": capability,
                    "pages": _capability_page_ids(spec, capability, step_by_id),
                })

    for target in spec.capabilities or []:
        if target.kind not in _IDENTIFIER_RELATION_TARGET_KINDS:
            continue
        target_ref = target.name or target.capability_id
        target_pages = _capability_page_ids(spec, target, step_by_id)
        for input_name, field_schema in (
            (target.input_schema or {}).get("properties") or {}
        ).items():
            if not isinstance(field_schema, dict):
                continue
            wire_leaf = _param_path_leaf(
                str(field_schema.get("x-flow-path") or input_name)
            )
            wire_role = _identifier_role_for_field(wire_leaf)
            if not wire_role:
                continue
            target_values = {
                value
                for value in _target_input_values(
                    target, str(input_name), field_schema, step_by_id,
                )
                if _identifier_value_is_grounding_evidence(value)
            }
            if not target_values:
                continue
            matches = [
                source for source in sources
                if target_values.intersection(source["values"])
                and (
                    source["capability"].name
                    or source["capability"].capability_id
                ) != target_ref
            ]
            query_matches = [
                source for source in matches
                if source["capability"].kind in {"query", "query_status"}
            ]
            if query_matches:
                # Later detail calls may echo the same ID. The selectable
                # collection remains the actual caller orchestration source.
                matches = query_matches
            if re.sub(r"[^a-z0-9]+", "", wire_leaf.casefold()) != "id":
                matches = [
                    source for source in matches
                    if source["role"] == wire_role
                ]
            same_page = [
                source for source in matches
                if target_pages and target_pages.intersection(source["pages"])
            ]
            if same_page:
                matches = same_page
            identities = {
                (
                    source["capability"].name or source["capability"].capability_id,
                    source["path"],
                    source["role"],
                )
                for source in matches
            }
            if len(identities) != 1:
                continue
            source = matches[0]
            source_ref, source_path, role = next(iter(identities))
            title = _IDENTIFIER_ROLE_TITLE[role]
            field_schema.update({
                "title": title,
                "label": title,
                "description": (
                    f"必须取自能力 `{source_ref}` 输出字段 `{source_path}`；"
                    "不得使用其他 ID、业务编号或录制样本代替。"
                ),
                "x-dano-identifier-role": role,
                "x-dano-derived-from-query": True,
                "x-dano-source-capability": source_ref,
                "x-dano-source-output": source_path,
                "x-dano-require-current-value": True,
            })
            field_schema.pop("default", None)
            field_schema.pop("x-dano-apply-default", None)
            target_wire_path = str(field_schema.get("x-flow-path") or "")
            for step_id in _capability_scoped_step_ids(target):
                target_step = step_by_id.get(step_id)
                if target_step is None:
                    continue
                for param in target_step.params or []:
                    if not (
                        str(param.key or "") == str(input_name)
                        or (target_wire_path and str(param.path or "") == target_wire_path)
                    ):
                        continue
                    if param.source_kind == "unknown":
                        param.category = "user_param"
                        param.source_kind = "user_input"
                        param.source = {
                            "kind": "capability_relation",
                            "source_capability": str(source_ref),
                            "source_output": str(source_path),
                            "target_path": str(param.path or target_wire_path),
                        }
                        param.reason = (
                            f"调用方先执行能力 `{source_ref}`，再把所选记录的"
                            f" `{source_path}` 原值传入；不是自由手填字段"
                        )
                        param.need_human_confirm = False
                    elif str((param.source or {}).get("kind") or "") == "capability_relation":
                        param.source = {
                            **(param.source or {}),
                            "source_capability": str(source_ref),
                            "source_output": str(source_path),
                            "target_path": str(param.path or target_wire_path),
                        }
            relation_identity = "|".join(
                (str(source_ref), str(source_path), str(target_ref), str(input_name))
            )
            relation = CapabilityRelation(
                relation_id="rel_" + hashlib.sha1(
                    relation_identity.encode("utf-8")
                ).hexdigest()[:12],
                type="external_transform",
                mode="external_transform",
                from_capability=str(source_ref),
                from_output=str(source_path),
                to_capability=str(target_ref),
                to_input=str(input_name),
                requires_user_confirmation=True,
                confidence=1.0,
                confirmed=True,
                reason="录制中后续操作参数与查询结果的稳定标识字段精确一致",
                evidence={
                    "kind": generated_kind,
                    "identifier_role": role,
                    "value_hash": hashlib.sha256(
                        sorted(target_values)[0].encode("utf-8")
                    ).hexdigest()[:16],
                },
                transform_owner="caller",
                cardinality="many_to_one",
                required=True,
                source_selector="$." + str(source_path).replace("[]", "[*]"),
                target_path=str(field_schema.get("x-flow-path") or input_name),
                input_schema=copy.deepcopy(field_schema),
                output_schema=copy.deepcopy(
                    _schema_node_at_path(
                        source["capability"].output_schema,
                        str(source_path),
                    ) or {}
                ),
                caller_responsibility=(
                    f"先调用 `{source_ref}` 定位用户选择的同一条业务记录，"
                    f"再把该记录的 `{source_path}` 原值传给 `{target_ref}.{input_name}`；"
                    "禁止使用同一记录的其他 ID 字段。"
                ),
            )
            already_present = any(
                (
                    existing.from_capability,
                    existing.from_output,
                    existing.to_capability,
                    existing.to_input,
                ) == (
                    relation.from_capability,
                    relation.from_output,
                    relation.to_capability,
                    relation.to_input,
                )
                for existing in (spec.capability_relations or [])
            )
            if not already_present:
                spec.capability_relations.append(relation)
    return spec


_PENDING_FLOW_SPEC_HELPERS = ('_capability_output_samples', '_capability_page_ids', '_capability_scoped_step_ids', '_param_path_leaf', '_schema_node_at_path',)


def _bind_flow_spec_helpers() -> None:
    import sys
    _flow_spec = sys.modules.get("dano.execution.page.flow_spec")
    if _flow_spec is None or not hasattr(_flow_spec, "to_flow_spec"):
        return
    module_globals = globals()
    for name in _PENDING_FLOW_SPEC_HELPERS:
        if hasattr(_flow_spec, name):
            module_globals[name] = getattr(_flow_spec, name)
