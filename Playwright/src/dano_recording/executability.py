"""Deterministic executability contract checks.

This module intentionally does not use Pi output as a gate.  It answers one
narrow question: can the frozen revision satisfy every wire contract at runtime?
"""

from __future__ import annotations

import json
import re
from typing import Any

from .enum_resolver import enum_contract_fault
from .review_advice import build_review_report, make_issue
from .runtime.conditions import condition_value
from .runtime.option_resolver import enum_runtime_contract

_WRITES = {"POST", "PUT", "PATCH", "DELETE"}
_DANGEROUS = {"DELETE", "PATCH", "PUT", "POST"}
_SECRET = re.compile(r"(?:authorization|cookie|password|passwd|secret|token|api[_-]?key)", re.I)
_SUPPORTED_RUNTIME_RESOLVERS = {"runtime_context.current_tenant.id"}
_RECORD_COLLECTIONS = {"records", "items", "results"}


def _revision(snapshot: dict[str, Any]) -> int:
    return int(snapshot.get("revision") or 0)


def _step_target(step: dict[str, Any]) -> dict[str, Any]:
    value = str(step.get("step_uuid") or step.get("step_id") or "")
    return {"kind": "step", "step_uuid": value, "step_id": value}


def _field_target(field: dict[str, Any]) -> dict[str, Any]:
    value = str(
        field.get("field_uuid") or field.get("field_contract_id")
        or field.get("field_id") or ""
    )
    return {"kind": "field", "field_uuid": value, "field_contract_id": value}


def _cap_target(capability: dict[str, Any]) -> dict[str, Any]:
    value = str(capability.get("capability_uuid") or capability.get("capability_id") or "")
    return {"kind": "capability", "capability_uuid": value, "capability_id": value}


def _fields(snapshot: dict[str, Any], body: dict[str, Any]) -> list[dict[str, Any]]:
    api = body.get("api_request") or {}
    values: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    sources: list[Any] = [
        api.get("field_contracts"), snapshot.get("effective_fields"), snapshot.get("fields"),
    ]
    for step in snapshot.get("steps") or []:
        if isinstance(step, dict):
            sources.append([
                {
                    **item,
                    "step_id": item.get("step_id") or step.get("step_id"),
                    "step_uuid": item.get("step_uuid") or step.get("step_uuid"),
                    "request_definition_id": (
                        item.get("request_definition_id")
                        or step.get("request_definition_id")
                    ),
                }
                for item in step.get("params") or [] if isinstance(item, dict)
            ])
    registry = snapshot.get("field_registry") or {}
    if isinstance(registry, dict):
        bindings = [
            item for item in registry.get("bindings") or [] if isinstance(item, dict)
        ]
        registry_rows: list[dict[str, Any]] = []
        for field in registry.get("fields") or []:
            if not isinstance(field, dict):
                continue
            field_uuid = str(field.get("field_uuid") or "")
            binding_ids = {str(value) for value in field.get("wire_binding_ids") or []}
            for binding in bindings:
                if str(binding.get("binding_id") or "") not in binding_ids:
                    continue
                wire_path = str(binding.get("wire_path") or "")
                prefix, separator, remaining = wire_path.partition(".")
                location = prefix if separator and prefix in {
                    "path", "query", "body", "form", "header",
                } else "body"
                registry_rows.append({
                    "field_uuid": field_uuid,
                    "field_contract_id": field_uuid,
                    "step_uuid": str(binding.get("step_uuid") or ""),
                    "request_definition_id": str(
                        binding.get("request_definition_id") or ""
                    ),
                    "location": location,
                    "wire_path": remaining if separator and location == prefix else wire_path,
                    "axis_decisions": field.get("decisions") or {},
                })
        sources.append(registry_rows)
    for source in sources:
        for raw in source if isinstance(source, list) else []:
            if not isinstance(raw, dict):
                continue
            key = (
                str(raw.get("field_uuid") or raw.get("field_contract_id") or raw.get("field_id") or ""),
                str(raw.get("step_id") or raw.get("step_uuid") or ""),
                str(raw.get("wire_path") or raw.get("path") or raw.get("key") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            values.append(raw)
    return values


def _required(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").lower()
    if text == "true":
        return True
    if text == "false":
        return False
    return None


def _axis_value(field: dict[str, Any], axis: str) -> Any:
    decisions = field.get("axis_decisions") or field.get("decisions") or {}
    if not isinstance(decisions, dict):
        return None
    decision = decisions.get(axis)
    if isinstance(decision, dict) and "value" in decision:
        return decision["value"]
    return decision


def _provider(field: dict[str, Any]) -> dict[str, Any]:
    decision = _axis_value(field, "source_binding")
    value = (
        decision or field.get("source_binding") or field.get("value_provider")
        or field.get("provider") or field.get("source") or {}
    )
    return value if isinstance(value, dict) else {}


def _schema_contains(schema: Any, path: str) -> bool:
    if not path:
        return False
    node = schema
    for token in [value for value in re.split(r"\.|\[\d+\]", path) if value]:
        if not isinstance(node, dict):
            return False
        if node.get("type") == "array" or "items" in node:
            node = node.get("items") or {}
        properties = node.get("properties")
        if isinstance(properties, dict) and token in properties:
            node = properties[token]
            continue
        if token in node and token not in {"type", "required", "description"}:
            node = node[token]
            continue
        return False
    return True


def _json_schema_ok(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    try:
        json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError):
        return False
    kind = value.get("type")
    if kind is not None and kind not in {
        "null", "boolean", "object", "array", "number", "string", "integer",
    }:
        return False
    return not (kind == "object" and "properties" in value and not isinstance(value["properties"], dict))


def _choice_resolvable(field: dict[str, Any]) -> bool:
    choice = (
        _axis_value(field, "enum_binding")
        or field.get("enum_binding") or field.get("choice_contract") or {}
    )
    if not isinstance(choice, dict):
        return False
    if (
        field.get("caller_supplies_wire_value")
        or choice.get("caller_supplies_wire_value")
        or choice.get("input_mode") == "wire_value"
    ):
        return True
    try:
        contract = enum_runtime_contract(choice)
    except (TypeError, ValueError):
        return False
    return contract is not None and enum_contract_fault(contract) is None


def check_executability(
    snapshot: dict[str, Any], body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = body or snapshot
    revision = _revision(snapshot)
    api = body.get("api_request") or {}
    steps = [value for value in api.get("steps") or snapshot.get("steps") or [] if isinstance(value, dict)]
    capabilities = [
        value for value in body.get("capabilities") or api.get("capabilities")
        or snapshot.get("capabilities") or [] if isinstance(value, dict)
    ]
    step_by_uuid = {
        str(value.get("step_uuid")): value for value in steps if value.get("step_uuid")
    }
    step_by_legacy_id = {
        str(value.get("step_id")): value for value in steps if value.get("step_id")
    }
    request_definition_to_step = {
        str(value.get("request_definition_id")): value
        for value in steps if value.get("request_definition_id")
    }
    faults: list[dict[str, Any]] = []
    advice: list[dict[str, Any]] = []

    def fault(code: str, message: str, target: dict[str, Any], *, details: Any = None) -> None:
        faults.append(make_issue(
            kind="contract_fault", code=code, message=message, revision=revision,
            target=target, details=details,
        ))

    def advisory(code: str, message: str, target: dict[str, Any], *, details: Any = None) -> None:
        advice.append(make_issue(
            kind="advisory", code=code, message=message, revision=revision,
            target=target, details=details,
        ))

    if not steps:
        fault("missing_endpoint_request", "能力缺少终点业务请求。", {"kind": "flow"})

    request_facts: Any = snapshot.get("request_facts")
    if isinstance(request_facts, dict):
        request_facts = request_facts.get("requests") or []
    request_facts = [value for value in request_facts or [] if isinstance(value, dict)]
    runtime_request_ids = {
        str(value.get("request_definition_id") or value.get("request_id") or "") for value in steps
    }
    for request in request_facts:
        request_id = str(request.get("request_definition_id") or request.get("request_id") or "")
        disposition = str(request.get("disposition") or request.get("role") or "")
        if not disposition:
            advisory(
                "request_disposition_unknown",
                "已捕获请求尚未完成业务用途分类。",
                {"kind": "request", "request_uuid": request_id},
            )
        if disposition == "materialized" and request_id not in runtime_request_ids:
            fault(
                "materialized_request_missing",
                "已标记为执行步骤的请求没有进入运行时合同。",
                {"kind": "request", "request_uuid": request_id},
            )

    for step in steps:
        target = _step_target(step)
        if not step.get("step_uuid"):
            fault(
                "missing_step_uuid",
                "V3 运行时步骤缺少 canonical step_uuid。",
                target,
            )
        method = str(step.get("method") or "").upper()
        if not method or not (step.get("url") or step.get("path")):
            fault("incomplete_endpoint_request", "终点请求缺少 method 或 URL/path。", target)
        if method in _DANGEROUS and not (
            str(step.get("risk_level") or "").upper() in {"L3", "L4", "L5"}
            and (
                step.get("requires_confirmation") is True
                or step.get("requires_human_confirm") is True
            )
        ):
            fault("dangerous_request_policy", "危险请求缺少 L3+ 风险级别或最终确认合同。", target)

    all_fields = _fields(snapshot, body)
    field_names = {
        str(field.get("field_uuid") or field.get("field_contract_id") or field.get("field_id") or ""):
        str(field.get("public_name") or field.get("display_name") or field.get("name") or "")
        for field in all_fields
    }
    for field in all_fields:
        target = _field_target(field)
        required_contract = field.get("required_contract") or {}
        if not isinstance(required_contract, dict):
            required_contract = {}
        wire_required = _required(
            _axis_value(field, "wire_required")
            if _axis_value(field, "wire_required") is not None
            else required_contract.get(
                "wire_required", field.get("wire_required", field.get("required_by_wire")),
            )
        )
        caller_required = _required(
            _axis_value(field, "caller_required")
            if _axis_value(field, "caller_required") is not None
            else required_contract.get(
                "caller_required", field.get("caller_required", field.get("required")),
            )
        )
        source_decision = _axis_value(field, "source_binding")
        provider = (
            source_decision if isinstance(source_decision, dict)
            else required_contract.get("provider") or _provider(field)
        )
        provider = provider if isinstance(provider, dict) else {}
        provider_kind = str(provider.get("kind") or "").lower()
        if field.get("sensitive_constant_removed"):
            fault(
                "identity_constant_provider",
                "捕获的身份或个人信息常量已删除；必须改为可信运行时 resolver。",
                target,
            )
        if not (field.get("location") and (field.get("wire_path") or field.get("path") or field.get("key"))):
            fault("incomplete_field_binding", "字段缺少明确的 wire location 或 path。", target)
        if provider_kind in {"", "unknown", "unresolved", "none"}:
            safely_omitted = bool(
                field.get("wire_template_omitted") and wire_required is False
            )
            if not safely_omitted:
                missing_code = (
                    "wire_required_without_provider"
                    if wire_required is True else "wire_binding_provider_unavailable"
                )
                fault(
                    missing_code,
                    "请求模板保留了该 wire binding，但当前版本没有可满足它的运行时 provider。",
                    target,
                    details={"wire_required": wire_required},
                )
        exposure = _axis_value(field, "exposure")
        exposed = bool(exposure) if exposure is not None else field.get(
            "exposed", field.get("exposed_to_caller"),
        )
        if caller_required is True and exposed is False:
            fault(
                "caller_required_not_exposed",
                "调用方必填字段未暴露在工具输入合同中。",
                target,
            )
        for condition_name in ("wire_condition", "caller_condition"):
            condition = required_contract.get(condition_name)
            if condition is None:
                continue
            try:
                condition_value(condition, {}, field_names)
            except (TypeError, ValueError) as exc:
                fault(
                    "invalid_required_condition",
                    "字段必填条件不是受支持的安全条件合同。",
                    target,
                    details={"condition": condition_name, "error": str(exc)},
                )
        if wire_required is True and provider_kind in {
            "caller", "caller_input", "user_input", "option_source",
        } and exposed is False:
            fault(
                "wire_caller_provider_not_exposed",
                "wire 必填字段依赖调用方输入，但该字段未暴露给调用方。",
                target,
            )
        classification = str(
            _axis_value(field, "classification")
            or field.get("classification") or field.get("sensitivity") or ""
        )
        public_name = str(
            field.get("public_name") or field.get("display_name") or field.get("name")
            or field.get("wire_name") or field.get("wire_path") or ""
        )
        if (classification in {"credential", "secret"} or _SECRET.search(public_name)) and not (
            provider.get("runtime_resolver")
            or provider_kind in {"runtime_context", "request_header", "credential_store", "secret_ref"}
        ):
            fault(
                "secret_without_runtime_resolver",
                "敏感字段没有可信运行时 resolver，不能由录制快照或调用方明文提供。",
                target,
            )
        if provider_kind == "runtime_context":
            resolver = str(provider.get("runtime_resolver") or "").strip()
            if resolver not in _SUPPORTED_RUNTIME_RESOLVERS:
                fault(
                    "runtime_resolver_unavailable",
                    "Dano 未提供该字段声明的可信运行时 resolver，不能启用直接调用。",
                    target,
                    details={"runtime_resolver": resolver},
                )
        if provider_kind in {"previous_response", "dependency_response"}:
            source_id = str(provider.get("request_definition_id") or "")
            if not source_id:
                fault(
                    "dependency_source_identity_missing",
                    "上游响应来源缺少 canonical request_definition_id。",
                    target,
                )
                continue
            source = request_definition_to_step.get(source_id)
            path = str(provider.get("response_path") or provider.get("source_path") or "")
            if source is not None:
                source_uuid = str(source.get("step_uuid") or "")
                source_alias = str(source.get("step_id") or "")
                declared_uuid = str(provider.get("source_step_uuid") or "")
                declared_alias = str(provider.get("source_step_id") or "")
                if (
                    (declared_uuid and declared_uuid != source_uuid)
                    or (
                        declared_alias
                        and declared_alias not in {source_uuid, source_alias}
                    )
                ):
                    fault(
                        "dependency_source_identity_mismatch",
                        "上游响应的 UUID/别名与 request_definition_id 指向不同步骤。",
                        target,
                        details={
                            "request_definition_id": source_id,
                            "source_step_uuid": declared_uuid,
                            "source_step_id": declared_alias,
                        },
                    )
            if source is None or not _schema_contains(source.get("response_schema") or {}, path):
                fault(
                    "missing_upstream_response_path",
                    "上游响应字段路径在已捕获 response schema 中不存在。",
                    target,
                    details={"source": source_id, "path": path},
                )
        business_type = str(
            _axis_value(field, "business_type")
            or field.get("business_type") or field.get("type") or ""
        ).lower()
        enum_axis = _axis_value(field, "enum_binding")
        has_enum = bool(enum_axis or field.get("enum_binding") or field.get("choice_contract")) or business_type == "enum"
        if has_enum and not _choice_resolvable(field):
            fault(
                "enum_label_not_resolvable",
                "当前枚举不能将调用方输入稳定转换为接口 wire value。",
                target,
            )
        choice = enum_axis or field.get("enum_binding") or field.get("choice_contract") or {}
        coverage = str(
            choice.get("mapping_coverage")
            or (choice.get("enum_evidence") or {}).get("mapping_coverage")
            or ""
        ) if isinstance(choice, dict) else ""
        if coverage in {
            "selected_only", "observed_set", "unknown",
        }:
            advisory(
                "partial_enum_coverage",
                "枚举证据只覆盖录制时已观察范围，不能宣称为全域静态枚举。",
                target,
            )
        confidence = field.get("confidence")
        if isinstance(confidence, (int, float)) and confidence < 0.6:
            advisory("low_semantic_confidence", "该字段的业务语义置信度较低。", target)
        if wire_required is not True and provider_kind in {"", "unknown", "unresolved"}:
            advisory("optional_provider_unknown", "可选字段的值来源仍未确定。", target)

    for capability in capabilities:
        target = _cap_target(capability)
        if not capability.get("capability_uuid"):
            fault(
                "missing_capability_uuid",
                "V3 能力缺少 canonical capability_uuid。",
                target,
            )
        step_uuids = [str(value) for value in capability.get("step_uuids") or []]
        step_ids = [str(value) for value in capability.get("step_ids") or []]
        if not step_uuids:
            fault(
                "missing_capability_step_uuids",
                "V3 能力必须用 canonical step_uuid 声明执行步骤。",
                target,
            )
            executable = []
        else:
            executable = [step_by_uuid[value] for value in step_uuids if value in step_by_uuid]
            missing_uuids = [value for value in step_uuids if value not in step_by_uuid]
            if missing_uuids:
                fault(
                    "capability_step_uuid_missing",
                    "能力声明的 canonical step_uuid 在运行时步骤中不存在。",
                    target,
                    details={"step_uuids": missing_uuids},
                )
            if step_ids and len(step_ids) != len(step_uuids):
                fault(
                    "capability_step_reference_mismatch",
                    "能力的 step_uuid/step_id 列表长度不一致。",
                    target,
                )
            for index, step_uuid in enumerate(step_uuids):
                if index >= len(step_ids):
                    continue
                if step_by_uuid.get(step_uuid) is not step_by_legacy_id.get(step_ids[index]):
                    fault(
                        "capability_step_reference_mismatch",
                        "能力的 step_uuid 与 step_id 指向不同运行时步骤。",
                        target,
                        details={"step_uuid": step_uuid, "step_id": step_ids[index]},
                    )
        for ref in [item for item in capability.get("request_refs") or [] if isinstance(item, dict)]:
            ref_uuid = str(ref.get("step_uuid") or "")
            ref_id = str(ref.get("step_id") or "")
            if not ref_uuid:
                fault(
                    "request_ref_missing_step_uuid",
                    "V3 request_ref 缺少 canonical step_uuid。",
                    target,
                    details={"step_id": ref_id},
                )
                continue
            if ref_uuid not in step_by_uuid:
                fault(
                    "request_ref_step_uuid_missing",
                    "request_ref 声明的 step_uuid 不存在。",
                    target,
                    details={"step_uuid": ref_uuid},
                )
            elif ref_id and step_by_uuid[ref_uuid] is not step_by_legacy_id.get(ref_id):
                fault(
                    "request_ref_step_mismatch",
                    "request_ref 的 step_uuid 与 step_id 指向不同步骤。",
                    target,
                    details={"step_uuid": ref_uuid, "step_id": ref_id},
                )
        if not executable or not any(
            str(value.get("method") or "") and (value.get("url") or value.get("path"))
            for value in executable
        ):
            fault("capability_missing_endpoint", "能力缺少终点业务请求。", target)
        for schema_name in ("input_schema", "output_schema"):
            schema = capability.get(schema_name)
            if schema is not None and not _json_schema_ok(schema):
                fault(
                    "schema_not_serializable",
                    f"能力的 {schema_name} 不是可序列化 JSON Schema。",
                    target,
                    details={"schema": schema_name},
                )
        read_only = bool(executable) and all(
            str(value.get("method") or "").upper() in {"GET", "HEAD"}
            for value in executable
        )
        if read_only:
            output_schema = capability.get("output_schema") or {}
            output_properties = (
                output_schema.get("properties")
                if isinstance(output_schema, dict)
                and isinstance(output_schema.get("properties"), dict)
                else {}
            )
            for output_name, collection in output_properties.items():
                if (
                    output_name not in _RECORD_COLLECTIONS
                    or not isinstance(collection, dict)
                    or collection.get("type") != "array"
                ):
                    continue
                items = collection.get("items") if isinstance(collection.get("items"), dict) else {}
                item_properties = (
                    items.get("properties")
                    if isinstance(items.get("properties"), dict) else {}
                )
                if not item_properties:
                    fault(
                        "query_record_schema_missing",
                        "只读记录集合必须声明每条记录的字段结构。",
                        target,
                        details={"output": output_name},
                    )
                    continue
                record_id = str(collection.get("x-record-id-field") or "").strip()
                id_schema = item_properties.get(record_id) if record_id else None
                if (
                    not record_id
                    or not isinstance(id_schema, dict)
                    or str(id_schema.get("type") or "") not in {"string", "integer", "number"}
                ):
                    fault(
                        "query_record_identity_missing",
                        "只读记录集合缺少可验证的稳定记录 ID 字段。",
                        target,
                        details={
                            "output": output_name,
                            "x-record-id-field": record_id,
                        },
                    )
        if not capability.get("confirmed"):
            advisory("capability_boundary_unconfirmed", "能力边界尚未由用户确认。", target)
        dangerous = [value for value in executable if str(value.get("method") or "").upper() in _DANGEROUS]
        if dangerous and not (
            str(capability.get("risk_level") or "").upper() in {"L3", "L4", "L5"}
            and (
                capability.get("requires_confirmation") is True
                or capability.get("requires_human_confirm") is True
            )
        ):
            fault("dangerous_request_policy", "危险请求缺少 L3+ 风险级别或最终确认合同。", target)

    if not capabilities:
        fault("missing_business_capability", "当前版本没有可定位的业务能力合同。", {"kind": "flow"})
    owned_steps = {
        str(value) for capability in capabilities
        for value in list(capability.get("step_uuids") or []) + [
            ref.get("step_uuid")
            for ref in capability.get("request_refs") or []
            if isinstance(ref, dict)
        ]
        if value
    }
    orphaned = sorted(
        str(step.get("step_uuid") or "")
        for step in steps
        if str(step.get("step_uuid") or "") not in owned_steps
    )
    if orphaned:
        fault(
            "orphan_runtime_steps",
            "运行时步骤没有归属任何业务能力。",
            {"kind": "flow"},
            details={"step_uuids": orphaned},
        )

    ignored = ((snapshot.get("meta") or {}).get("ignored_advisory_fingerprints") or [])
    return build_review_report(
        revision=revision,
        contract_faults=faults,
        advisories=advice,
        ignored_fingerprints=ignored,
    )
