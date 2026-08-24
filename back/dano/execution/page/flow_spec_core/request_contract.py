"""Compile an already-materialized FlowSpec into executable request contracts."""
from __future__ import annotations

from typing import Any
import copy
import hashlib
from urllib.parse import urlparse
from dano.execution.page.flow_spec_core.models import (
    FlowCapability,
    FlowSpec,
    FlowStep,
    ParamField,
    SelectBinding,
)
from dano.execution.page.request_capture import (
    build_api_request,
    extract_auth_headers,
    self_check,
    substitute,
)
from dano.execution.page.flow_spec_core.normalization import (
    _clean_path_prefix,
    _flow_path_set,
    _strip_body_prefix,
)
from dano.execution.page.flow_spec_core.owner_runtime import (
    bind_owner_runtime,
)


def _dynamic_array_containers(step: FlowStep) -> set[str]:
    return {
        str((param.source or {}).get("array_container_path") or param.path or "")
        for param in step.params or []
        if (
            str((param.source or {}).get("kind") or "") == "dynamic_structure_input"
            and str((param.source or {}).get("structure_kind") or "") == "array_object"
        )
    }


def _param_is_dynamic_array_leaf(step: FlowStep, param: ParamField) -> bool:
    return bool(
        (param.source or {}).get("array_item_member") is True
        and str((param.source or {}).get("array_container_path") or "")
        in _dynamic_array_containers(step)
    )


def _step_samples(step: FlowStep) -> dict:
    samples = dict(step.sample_inputs or {})
    for p in step.params:
        if (
            p.key
            and p.value not in (None, "")
            and not _param_is_dynamic_array_leaf(step, p)
            and p.source_kind != "dynamic_structure"
            and str((p.source or {}).get("kind") or "") != "dynamic_structure_leaf"
        ):
            samples[p.key] = p.value
    return samples


def _step_param_map(step: FlowStep) -> dict[str, str]:
    """只把 user_param 暴露给 Skill 调用者；常量/运行期变量保留在流程内部。"""
    out: dict[str, str] = {}
    for p in step.params:
        if _param_is_dynamic_array_leaf(step, p):
            continue
        if not _param_exposed_to_caller(p):
            continue
        key = (p.key or "").strip()
        if key:
            out[p.path] = key
    return out


def _unresolved_recorded_literal_errors(step: FlowStep) -> list[str]:
    """Reject unresolved write leaves before their capture sample becomes executable data."""
    if str(step.method or "").upper() not in {"POST", "PUT", "PATCH", "DELETE"}:
        return []
    errors: list[str] = []
    for param in step.params or []:
        if param.path.startswith(("query.", "path.")):
            continue
        if _param_is_dynamic_array_leaf(step, param):
            # The caller-owned aggregate replaces the recorded array as a
            # whole, so its member sample is not retained in body_template.
            continue
        if _param_exposed_to_caller(param):
            continue
        if str(param.source_kind or "unknown").strip().lower() not in {
            "",
            "unknown",
            "ambiguous",
        }:
            continue
        path = str(param.path or param.key or "").removeprefix("body.")
        errors.append(
            f"步骤 `{step.name or step.path or step.step_id}` 的写入字段 `{path}` 来源未确认，"
            "不能把录制样例作为运行时常量"
        )
    return errors


def _step_wire_formats(step: FlowStep) -> dict[str, str]:
    """Map stable public input names to their explicit on-wire formats."""
    return {
        str(param.key): str(param.wire_format)
        for param in step.params
        if (
            _param_exposed_to_caller(param)
            and not _param_is_dynamic_array_leaf(step, param)
            and param.key
            and param.wire_format
        )
    }


def _executable_identity_source(value: Any) -> bool:
    """Return whether the existing request runtime can resolve this source.

    FlowSpec also keeps advisory identity guesses (for example a body field
    named ``user_id`` whose concrete session location was not captured).  An
    advisory body path is useful evidence for Pi, but it is not a runtime
    source and must not be emitted into the executable request.
    """
    kind, separator, location = str(value or "").partition(":")
    return bool(
        separator
        and location
        and kind in {"cookie", "localStorage", "requestHeader"}
    )


def _step_runtime_identity(step: FlowStep) -> list[dict[str, Any]]:
    """Compile session-owned body fields through the existing identity runtime."""
    values = [
        item.model_dump(exclude_none=True)
        for item in step.identity
        if _executable_identity_source(item.source)
    ]
    for param in step.params:
        if param.category != "runtime_var":
            continue
        source = dict(param.source or {})
        if (
            param.source_kind == "current_user"
            and _executable_identity_source(source.get("path"))
        ):
            values.append({
                "path": _strip_body_prefix(param.path),
                "source": str(source["path"]),
                "value": param.value,
            })
        elif param.source_kind == "request_header" and source.get("header"):
            values.append({
                "path": _strip_body_prefix(param.path),
                "source": f"requestHeader:{source['header']}",
                "value": param.value,
            })
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for item in values:
        deduped[(str(item.get("path") or ""), str(item.get("source") or ""))] = item
    return list(deduped.values())


def _dynamic_array_aggregate_for_path(
    step: FlowStep,
    path: str,
) -> tuple[ParamField, str] | None:
    normalized = _strip_body_prefix(str(path or ""))
    for param in step.params or []:
        source = param.source or {}
        if not (
            str(source.get("kind") or "") == "dynamic_structure_input"
            and str(source.get("structure_kind") or "") == "array_object"
        ):
            continue
        container = str(source.get("array_container_path") or param.path or "")
        prefix = f"{container}["
        if not normalized.startswith(prefix) or "]." not in normalized:
            continue
        return param, normalized.split("].", 1)[1]
    return None


_EXECUTABLE_RUNTIME_RULES = frozenset({
    "date_range_end", "date_span_days", "date_span_days_json",
    "product", "sum", "difference", "percent_of", "remainder_after_percent",
    "collection_sum", "percent_of_collection_sum", "difference_collection_sum",
})


def _runtime_rule_for_param(param: ParamField) -> dict[str, Any] | None:
    if param.source_kind not in {"computed", "page_rule"}:
        return None
    source = dict(param.source or {})
    formula = source.get("formula")
    if (
        param.source_kind == "page_rule"
        and not isinstance(formula, dict)
        and source.get("executable") is not True
    ):
        return None
    if isinstance(formula, dict):
        source = {**source, **formula}
    kind = str(source.get("strategy") or source.get("kind") or "")
    if kind not in _EXECUTABLE_RUNTIME_RULES:
        return None
    source["kind"] = kind
    if param.source_kind == "computed":
        return source
    source["formula"] = {
        key: value for key, value in source.items()
        if key not in {"formula", "path"}
    }
    source["executable"] = True
    return source


def _compiled_capability_step(step: dict[str, Any]) -> dict[str, Any]:
    keep = {
        "step_id", "step_name", "method", "url", "url_template", "path",
        "content_type", "body_template", "query_template", "params", "success_rule",
        "field_types", "wire_formats", "runtime_fields", "selects", "system_values",
        "fact_check",
    }
    contract = {
        key: copy.deepcopy(value)
        for key, value in step.items()
        if key in keep and value is not None
    }
    contract["selects"] = [
        {
            key: copy.deepcopy(value)
            for key, value in binding.items()
            if key not in {"actor", "confidence", "verification_id"}
            and value is not None
        }
        for binding in (step.get("selects") or [])
        if isinstance(binding, dict)
    ]
    return contract


def _flow_step_query_template(
    step: FlowStep,
) -> tuple[dict[str, Any], list[str], dict[str, Any], dict[str, str], list[dict[str, Any]]]:
    query_template: dict[str, Any] = {}
    params: list[str] = []
    samples: dict[str, Any] = {}
    field_types: dict[str, str] = {}
    runtime_fields: list[dict[str, Any]] = []
    for p in step.params:
        if not p.path.startswith("query."):
            continue
        query_key = _query_key_from_param(p)
        if not query_key:
            continue
        if p.category == "user_param":
            name = (p.key or query_key).strip()
            if not name:
                continue
            query_template[query_key] = "{{" + name + "}}"
            if name not in params:
                params.append(name)
            if p.value not in (None, ""):
                samples[name] = p.value
            field_types[name] = p.type
        elif p.category == "runtime_var":
            # 运行期变量不是最终用户参数。GET query 里先保留录制值，若有 FlowLink 指向 query.xxx，
            # execute_api_workflow 会在运行期用上游响应覆盖；没有可靠来源时由 review_items 提醒人工确认。
            runtime_rule = _runtime_rule_for_param(p)
            if p.source_kind in {"system_time", "system_generated"} or runtime_rule is not None:
                runtime_name = f"__dano_runtime_{hashlib.sha1((step.step_id + ':' + p.path).encode()).hexdigest()[:10]}"
                if runtime_rule is not None:
                    runtime_field = {"name": runtime_name, **runtime_rule}
                    strategy = str(runtime_field.get("strategy") or "")
                    if not strategy:
                        strategy = str(runtime_field.get("kind") or "")
                else:
                    strategy = str((p.source or {}).get("strategy") or "")
                    if not strategy:
                        strategy = (
                            ("now_date" if p.type == "date" else "now_iso")
                            if p.source_kind == "system_time" and p.type in {"string", "date", "datetime"}
                            else "now_ms" if p.source_kind == "system_time" else "uuid"
                        )
                    runtime_field = {"name": runtime_name, "kind": strategy}
                query_template[query_key] = "{{" + runtime_name + "}}"
                runtime_field["kind"] = strategy
                runtime_fields.append(runtime_field)
            else:
                query_template[query_key] = p.value
        else:
            query_template[query_key] = p.value
    return query_template, params, samples, field_types, runtime_fields


def _flow_step_url_template(
    step: FlowStep,
) -> tuple[str, list[str], dict[str, Any], dict[str, str]]:
    path_params = [param for param in step.params if param.path.startswith("path.")]
    if not path_params:
        return "", [], {}, {}
    parsed = urlparse(step.url or step.path)
    segments = parsed.path.split("/")
    names: list[str] = []
    samples: dict[str, Any] = {}
    field_types: dict[str, str] = {}
    for param in path_params:
        try:
            position = int(param.path.split(".", 1)[1])
        except (TypeError, ValueError):
            continue
        if position < 0 or position >= len(segments):
            continue
        name = str(param.key or param.label or f"path_{position}").strip()
        if not name:
            continue
        segments[position] = "{{" + name + "}}"
        names.append(name)
        if param.value not in (None, ""):
            samples[name] = param.value
        field_types[name] = param.type
    if not names:
        return "", [], {}, {}
    return parsed._replace(path="/".join(segments)).geturl(), list(dict.fromkeys(names)), samples, field_types


def flow_spec_user_params(spec: FlowSpec) -> list[str]:
    bind_owner_runtime()
    names: list[str] = []
    active_step_ids = _active_capability_step_ids(spec)
    for st in spec.steps:
        if active_step_ids is not None and st.step_id not in active_step_ids:
            continue
        for name in _step_param_map(st).values():
            if name not in names:
                names.append(name)
    return names


def flow_spec_required_params(spec: FlowSpec) -> list[str]:
    bind_owner_runtime()
    names: list[str] = []
    active_step_ids = _active_capability_step_ids(spec)
    for st in spec.steps:
        if active_step_ids is not None and st.step_id not in active_step_ids:
            continue
        for p in st.params:
            if _param_is_dynamic_array_leaf(st, p):
                continue
            if not _param_requires_caller_input(p):
                continue
            key = (p.key or "").strip()
            if key and key not in names:
                names.append(key)
    return names


def _select_param_for_runtime(step: FlowStep, binding: SelectBinding) -> ParamField | None:
    """Return the current field contract owned by a recorded select binding."""
    if binding.path:
        matched = next((
            param for param in (step.params or [])
            if param.path == binding.path
        ), None)
        if matched is not None:
            return matched
    if binding.id_path:
        return next((
            param for param in (step.params or [])
            if param.path == binding.id_path
        ), None)
    return None


def _select_binding_is_runtime_executable(step: FlowStep, binding: SelectBinding) -> bool:
    """Execute only an explicitly confirmed binding compatible with the live field contract.

    ``step.selects`` also keeps historical recorder evidence so the workbench can
    restore or inspect it.  It must not override an operator who has changed the
    field back to ordinary text/user input, nor may an incomplete candidate be
    promoted merely because it survived in that evidence list.
    """
    if binding.enum_confirmed is not True:
        return False
    param = _select_param_for_runtime(step, binding)
    if param is None or param.category != "user_param" or not param.exposed_to_user:
        return False
    source_kind = str(param.source_kind or "")
    if source_kind not in {"api_option", *_ENUM_SOURCE_KINDS}:
        return False
    if (
        source_kind != "api_option"
        and _param_field_manually_edited(param, "type")
        and param.type not in _ENUM_PARAM_TYPES
    ):
        return False
    if source_kind == "api_option":
        configured_url = str((param.source or {}).get("source_url") or "").strip()
        if configured_url and _request_path({"url": configured_url}) != _request_path({"url": binding.source_url}):
            return False
        return bool(binding.source_url and binding.value_key and binding.label_key)

    options = list(binding.options or [])
    if not options:
        return False
    option_map = dict(binding.option_map or _enum_option_map_from_options(options))
    labels: list[str] = []
    for option in options:
        pair = _enum_label_value(option)
        if pair is None or pair[0] in labels or pair[1] is None:
            return False
        labels.append(pair[0])
    return bool(labels) and all(label in option_map and option_map[label] is not None for label in labels)


def _runtime_select_bindings(step: FlowStep) -> list[dict[str, Any]]:
    """Serialize only bindings that remain executable after workbench edits."""
    current_key_by_path = {p.path: p.key for p in (step.params or [])}
    out: list[dict[str, Any]] = []
    for binding in step.selects or []:
        if not _select_binding_is_runtime_executable(step, binding):
            continue
        item = binding.model_dump(exclude_none=True)
        for metadata_key in ("actor", "confidence", "verification_id"):
            item.pop(metadata_key, None)
        if not item.get("field_projections"):
            item.pop("field_projections", None)
        if binding.path in current_key_by_path:
            item["param"] = current_key_by_path[binding.path]
        out.append(item)
    return out


def _flow_step_to_api_step(
    step: FlowStep,
    *,
    reject_unresolved_literals: bool = False,
) -> tuple[dict | None, list[str]]:
    errors: list[str] = []
    if reject_unresolved_literals:
        unresolved_literal_errors = _unresolved_recorded_literal_errors(step)
        if unresolved_literal_errors:
            return None, unresolved_literal_errors
    runtime_errors = [err for p in step.params if (err := _runtime_param_publish_error(p))]
    if runtime_errors:
        return None, runtime_errors
    if not step.body_source:
        body_params = [
            param for param in step.params
            if not param.path.startswith(("query.", "path."))
        ]
        if body_params:
            errors.append(f"步骤 `{step.name or step.path or step.step_id}` 缺少请求体，Body 字段没有可执行落点")
            return None, errors
        if step.method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            query_template, params, samples, field_types, runtime_fields = _flow_step_query_template(step)
            url_template, path_params, path_samples, path_types = _flow_step_url_template(step)
            selects = _runtime_select_bindings(step)
            apir = {
                "step_id": step.step_id,
                "step_name": step.name,
                "method": step.method.upper(),
                "url": step.url or step.path,
                "url_template": url_template,
                "path": step.path,
                "content_type": step.content_type,
                "body_template": None,
                "query_template": query_template,
                "params": list(dict.fromkeys([*params, *path_params])),
                "sample_inputs": {**samples, **path_samples},
                "auth_headers": extract_auth_headers(step.headers),
                "field_types": {**field_types, **path_types},
                "selects": selects,
                "identity": [],
                "system_values": [],
                "runtime_fields": runtime_fields,
            }
            wire_formats = _step_wire_formats(step)
            if wire_formats:
                apir["wire_formats"] = wire_formats
            if step.success_rule:
                apir["success_rule"] = step.success_rule
            if step.fact_check:
                apir["fact_check"] = step.fact_check
            if step.response_json is not None:
                apir["response_json"] = step.response_json
            return apir, errors
        errors.append(f"步骤 `{step.name or step.path or step.step_id}` 使用了不支持的 HTTP 方法 `{step.method}`")
        return None, errors
    req = {
        "method": step.method,
        "url": step.url or step.path,
        "post_data": step.body_source,
        "content_type": step.content_type,
        "headers": step.headers,
    }
    if step.source_meta.get("response_status") is not None:
        req["response_status"] = step.source_meta.get("response_status")
    if step.response_json is not None:
        req["response_json"] = step.response_json
    param_map = _step_param_map(step)
    selects = _runtime_select_bindings(step)
    select_paths = set()
    for item in selects:
        path = str(item.get("path") or "")
        if path:
            select_paths.add(path)
    for p in step.params:
        if (
            p.category == "user_param"
            and p.source_kind in {"page_enum", "static_enum", "manual_enum", "form_option"}
            and p.enum_options
            and not (
                _param_field_manually_edited(p, "type")
                and p.type not in _ENUM_PARAM_TYPES
            )
            and p.path not in select_paths
        ):
            selects.append({
                "param": p.key,
                "path": p.path,
                "source_url": "",
                "value_key": "",
                "label_key": "",
                "options": list(p.enum_options),
                "count": len(p.enum_options),
                "option_map": dict(p.enum_value_map or _enum_option_map_from_options(p.enum_options)),
                "enum_source": "manual",
                "enum_confirmed": True,
            })
            select_paths.add(p.path)
    apir = build_api_request(
        req,
        param_map,
        selects=selects,
        identity=_step_runtime_identity(step),
        typed=_step_samples(step),
    )
    if apir is None:
        errors.append(f"步骤 `{step.name or step.path or step.step_id}` 请求体无法解析，不能发布为请求型 Skill")
        return None, errors
    body_runtime_fields: list[dict[str, Any]] = []
    for param in step.params:
        runtime_rule = _runtime_rule_for_param(param)
        if runtime_rule is None or param.path.startswith(("query.", "path.")):
            continue
        runtime_name = f"__dano_runtime_{hashlib.sha1((step.step_id + ':' + param.path).encode()).hexdigest()[:10]}"
        array_target = _dynamic_array_aggregate_for_path(step, param.path)
        if array_target is not None:
            aggregate, relative_result = array_target
            runtime_field = {
                "name": runtime_name,
                **runtime_rule,
                "kind": "array_item_formula",
                "strategy": str(runtime_rule.get("strategy") or ""),
                "container_field": str(aggregate.key or aggregate.path),
                "result_field": relative_result,
            }
            body_runtime_fields.append(runtime_field)
            continue
        if not _flow_path_set(
            apir.get("body_template"),
            _strip_body_prefix(param.path),
            "{{" + runtime_name + "}}",
        ):
            errors.append(
                f"步骤 `{step.name or step.path or step.step_id}` 的计算字段 `{param.path}` 没有请求体落点"
            )
            continue
        runtime_field = {"name": runtime_name, **runtime_rule}
        runtime_field["kind"] = str(runtime_field.get("strategy") or runtime_field.get("kind") or "")
        body_runtime_fields.append(runtime_field)
    query_template, query_params, query_samples, query_types, runtime_fields = _flow_step_query_template(step)
    if query_template:
        apir["query_template"] = query_template
        apir["params"] = list(dict.fromkeys([*(apir.get("params") or []), *query_params]))
        apir["sample_inputs"] = {**(apir.get("sample_inputs") or {}), **query_samples}
        apir["field_types"] = {**(apir.get("field_types") or {}), **query_types}
        apir["runtime_fields"] = [*(apir.get("runtime_fields") or []), *runtime_fields]
    if body_runtime_fields:
        apir["runtime_fields"] = [*(apir.get("runtime_fields") or []), *body_runtime_fields]
    url_template, path_params, path_samples, path_types = _flow_step_url_template(step)
    if url_template:
        apir["url_template"] = url_template
        apir["params"] = list(dict.fromkeys([*(apir.get("params") or []), *path_params]))
        apir["sample_inputs"] = {**(apir.get("sample_inputs") or {}), **path_samples}
        apir["field_types"] = {**(apir.get("field_types") or {}), **path_types}
    explicit_system_values = [item.model_dump(exclude_none=True) for item in step.system_values]
    for p in step.params:
        if p.category != "runtime_var" or p.source_kind not in {"system_time", "system_generated"}:
            continue
        kind = str((p.source or {}).get("strategy") or "")
        if not kind:
            if p.source_kind == "system_generated":
                kind = "uuid"
            else:
                kind = (
                    "now_date" if p.type == "date"
                    else "now_iso" if p.type in {"string", "datetime"}
                    else "now_ms"
                )
        explicit_system_values.append({"path": _strip_body_prefix(p.path), "kind": kind})
    if explicit_system_values:
        deduped_system_values: dict[tuple[str, str], dict[str, Any]] = {}
        for item in [*(apir.get("system_values") or []), *explicit_system_values]:
            deduped_system_values[(str(item.get("path") or ""), str(item.get("kind") or ""))] = item
        apir["system_values"] = list(deduped_system_values.values())
    apir["step_id"] = step.step_id
    apir["step_name"] = step.name
    wire_formats = _step_wire_formats(step)
    if wire_formats:
        apir["wire_formats"] = wire_formats
    if step.success_rule:
        apir["success_rule"] = step.success_rule
    if step.fact_check:
        apir["fact_check"] = step.fact_check
    return apir, errors


def compile_capability_to_api_request(
    spec: FlowSpec,
    capability: str | FlowCapability | None = None,
    *,
    capability_id: str | None = None,
    capability_name: str | None = None,
) -> tuple[dict | None, list[str]]:
    try:
        view = capability_to_flow_spec_view(
            spec,
            capability,
            capability_id=capability_id,
            capability_name=capability_name,
        )
    except ValueError as exc:
        return None, [str(exc)]
    api_request, errors = flow_spec_to_api_request(view, _prepared=True)
    if api_request is not None:
        cap = view.capabilities[0] if view.capabilities else None
        if cap is not None:
            api_request["selected_capability"] = {
                "name": cap.name,
                "capability_id": cap.capability_id,
                "kind": cap.kind,
            }
            contracts = flow_spec_capability_contracts(
                view, capability_id=cap.capability_id, _prepared=True,
            )
            if contracts:
                api_request["compiled_capability"] = contracts[0]
    return api_request, errors


def flow_spec_to_api_request(
    spec: FlowSpec,
    *,
    capability: str | FlowCapability | None = None,
    capability_id: str | None = None,
    capability_name: str | None = None,
    _prepared: bool = False,
    _include_capability_contracts: bool = True,
    _embed_capability_steps: bool | None = None,
) -> tuple[dict | None, list[str]]:
    """把编辑后的 FlowSpec 转成 run_request_onboarding 可消费的 api_request。

    支持有 body 的写请求，也支持无 body 的 GET 前置步骤(query_template)。
    """
    bind_owner_runtime()
    if capability is not None or capability_id or capability_name:
        return compile_capability_to_api_request(
            spec,
            capability,
            capability_id=capability_id,
            capability_name=capability_name,
        )
    if not spec.steps:
        return None, ["FlowSpec 没有任何步骤，不能发布"]
    if not _prepared:
        spec = prepare_flow_spec_for_publish(spec)
    active_step_ids = _active_capability_step_ids(spec)
    strict_source_contract = int(
        (spec.meta or {}).get("stage_1_6_contract_version") or 0
    ) >= 2

    built_steps: list[dict] = []
    step_id_to_index: dict[str, int] = {}
    errors: list[str] = []
    for st in spec.steps:
        if active_step_ids is not None and st.step_id not in active_step_ids:
            continue
        apir, step_errors = _flow_step_to_api_step(
            st,
            reject_unresolved_literals=strict_source_contract,
        )
        if step_errors:
            errors.extend(step_errors)
            continue
        assert apir is not None
        step_id_to_index[st.step_id] = len(built_steps)
        built_steps.append(apir)

    if errors:
        return None, errors
    if not built_steps:
        return None, ["FlowSpec 没有可发布的请求步骤"]

    for lk in spec.links:
        if active_step_ids is not None and not (
            lk.source_step_id in active_step_ids and lk.target_step_id in active_step_ids
        ):
            continue
        if lk.source_step_id not in step_id_to_index or lk.target_step_id not in step_id_to_index:
            errors.append(f"链接 `{lk.link_id}` 指向不存在的步骤")
            continue
        target_idx = step_id_to_index[lk.target_step_id]
        source_idx = step_id_to_index[lk.source_step_id]
        if source_idx >= target_idx:
            errors.append(f"链接 `{lk.link_id}` 的来源步骤必须早于目标步骤")
            continue
        target_path = _clean_path_prefix(lk.target_path, "body.")
        source_path = _clean_path_prefix(lk.source_path, "response.")
        if not target_path or not source_path:
            errors.append(f"链接 `{lk.link_id}` 缺少 source_path 或 target_path")
            continue
        link_kind = str(lk.kind or "value")
        if link_kind in {"structure", "response_key_map"}:
            structure_link = {
                "link_id": lk.link_id,
                "target_path": lk.target_container_path or target_path,
                "target_tokens": lk.target_tokens,
                "source_step": source_idx,
                "source_path": lk.source_collection_path or source_path,
                "source_tokens": lk.source_tokens,
                "mode": "response_key_map" if link_kind == "response_key_map" else "response_keys",
            }
            if link_kind == "response_key_map":
                structure_link.update({
                    "kind": link_kind,
                    "source_collection_path": lk.source_collection_path or source_path,
                    "source_key_path": lk.source_key_path,
                    "source_label_path": lk.source_label_path,
                    "value_binding": copy.deepcopy(lk.value_binding or {}),
                })
            built_steps[target_idx].setdefault("structure_links", []).append(structure_link)
            continue
        built_steps[target_idx].setdefault("links", []).append({
            "target_path": target_path,
            "target_tokens": lk.target_tokens,
            "source_step": source_idx,
            "source_path": source_path,
            "source_tokens": lk.source_tokens,
        })
    if errors:
        return None, errors

    if len(built_steps) == 1:
        out = built_steps[0]
    else:
        params = flow_spec_user_params(spec)
        samples: dict[str, Any] = {}
        field_types: dict[str, str] = {}
        wire_formats: dict[str, str] = {}
        for st in built_steps:
            samples.update(st.get("sample_inputs") or {})
            field_types.update(st.get("field_types") or {})
            wire_formats.update(st.get("wire_formats") or {})
        out = {
            "steps": built_steps,
            "params": params,
            "sample_inputs": samples,
            "field_types": field_types,
        }
        if wire_formats:
            out["wire_formats"] = wire_formats

    if spec.goal:
        out["goal"] = spec.goal
    caps = list(spec.capabilities or [])
    if caps:
        compiled_by_id = {
            str(step.get("step_id") or ""): step
            for step in built_steps
            if str(step.get("step_id") or "")
        }
        trusted_verification_ids = {
            str(item.get("verification_id") or "")
            for item in (spec.meta or {}).get("verification_log") or []
            if isinstance(item, dict)
            and item.get("status") == "passed"
            and item.get("verification_id")
        }
        serialized_capabilities: list[dict[str, Any]] = []
        for capability in caps:
            serialized = _capability_to_api_dict(spec, capability)
            execution = dict(serialized.get("execution_contract") or {})
            step_ids = [
                str(item) for item in serialized.get("compiled_step_ids") or []
                if str(item) in compiled_by_id
            ]
            positions = {step_id: index for index, step_id in enumerate(step_ids)}
            embed_capability_steps = (
                int((spec.meta or {}).get("stage_1_6_contract_version") or 0) >= 2
                if _embed_capability_steps is None
                else _embed_capability_steps
            )
            if embed_capability_steps:
                execution.update({
                    "protocol": "dano.capability_plan.v2",
                    "steps": [_compiled_capability_step(compiled_by_id[step_id]) for step_id in step_ids],
                    "links": _compiled_capability_links(spec, positions),
                    "verification_ids": sorted(trusted_verification_ids),
                })
            serialized["execution_contract"] = execution
            serialized_capabilities.append(serialized)
        out["capabilities"] = serialized_capabilities
        out["capability_relations"] = [relation.model_dump(exclude_none=True) for relation in spec.capability_relations]
        out["capability_graph"] = {
            "protocol": "dano.capability_graph.v1",
            "nodes": [c.name or c.capability_id for c in caps],
            "relations": [relation.model_dump(exclude_none=True) for relation in spec.capability_relations],
        }
        if _include_capability_contracts:
            out["capability_contracts"] = flow_spec_capability_contracts(
                spec, _prepared=True,
            )
        out["capability_protocol"] = "dano.capability_plan.v1"
        out["workflow_nodes"] = {
            c.name: _capability_execution_contract(spec, c)
            for c in caps
            if c.name
        }
    out["_flow_spec"] = flow_spec_to_summary(spec)
    return out, []


def _compiled_capability_links(
    spec: FlowSpec, positions: dict[str, int],
) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    for link in executable_flow_links(spec):
        if (
            link.source_step_id not in positions
            or link.target_step_id not in positions
            or positions[link.source_step_id] >= positions[link.target_step_id]
        ):
            continue
        verification_id = str(
            (link.meta or {}).get("verification_id")
            or (link.evidence or {}).get("verification_id")
            or ""
        )
        links.append({
            "link_id": link.link_id,
            "kind": str(link.kind or "value"),
            "source_step": positions[link.source_step_id],
            "source_path": link.source_path,
            "target_step": positions[link.target_step_id],
            "target_path": link.target_path,
            "param_name": link.param_name or "",
            "verification_id": verification_id,
            "source_collection_path": link.source_collection_path,
            "source_key_path": link.source_key_path,
            "source_label_path": link.source_label_path,
            "target_container_path": link.target_container_path,
            "value_binding": dict(link.value_binding or {}),
        })
    return links


def _api_params(api_request: dict) -> list[str]:
    names = list(api_request.get("params") or [])
    for st in api_request.get("steps") or []:
        for name in st.get("params") or []:
            if name not in names:
                names.append(name)
    return names


def _api_sample_inputs(api_request: dict) -> dict[str, Any]:
    samples = dict(api_request.get("sample_inputs") or {})
    for st in api_request.get("steps") or []:
        samples.update(st.get("sample_inputs") or {})
    return samples


def _dry_fields(api_request: dict, fields: dict[str, Any] | None = None) -> dict[str, Any]:
    out = _api_sample_inputs(api_request)
    out.update(fields or {})
    for name in _api_params(api_request):
        out.setdefault(name, f"__DRY_{name}__")
    return out


def _dry_step_preview(step: dict, fields: dict[str, Any], index: int) -> dict:
    body = None
    query = None
    constructible = True
    error = ""
    if isinstance(step.get("body_template"), (dict, list)):
        try:
            body = substitute(step.get("body_template"), fields, step.get("sample_inputs") or {})
        except Exception as exc:  # noqa: BLE001
            constructible = False
            error = str(exc)
    if isinstance(step.get("query_template"), dict):
        try:
            query = substitute(step.get("query_template"), fields, step.get("sample_inputs") or {})
        except Exception as exc:  # noqa: BLE001
            constructible = False
            error = str(exc)
    return {
        "index": index,
        "method": step.get("method"),
        "path": step.get("path"),
        "url": step.get("url"),
        "params": list(step.get("params") or []),
        "links": list(step.get("links") or []),
        "has_body": body is not None,
        "body_preview": body,
        "has_query": query is not None,
        "query_preview": query,
        "constructible": constructible,
        "error": error,
    }


def _fact_check_report(api_request: dict | None) -> dict:
    if not api_request:
        return {"configured": False, "passed": False, "reason": "未生成 api_request"}
    fc = api_request.get("fact_check")
    if not fc:
        for st in api_request.get("steps") or []:
            if st.get("fact_check"):
                fc = st.get("fact_check")
                break
    if not fc:
        return {"configured": False, "passed": True, "reason": "未配置 fact_check，dry-run 仅做结构校验"}
    endpoint = fc.get("endpoint")
    assertion = fc.get("assertion")
    if assertion is not None:
        from dano.execution.page.replay import _validate_assertion_contract

        missing = [] if endpoint else ["endpoint"]
        assertion_error = ""
        try:
            _validate_assertion_contract(assertion)
        except ValueError as exc:
            assertion_error = str(exc)
        passed = not missing and not assertion_error
        return {
            "configured": True,
            "passed": passed,
            "missing": missing,
            "spec": fc,
            "reason": (
                "fact_check 严格断言配置完整" if passed
                else assertion_error or f"fact_check 缺少 {', '.join(missing)}"
            ),
        }
    match_field = fc.get("match_field")
    param = fc.get("param")
    missing = [name for name, value in {
        "endpoint": endpoint,
        "match_field": match_field,
        "param": param,
    }.items() if not value]
    return {
        "configured": True,
        "passed": not missing,
        "missing": missing,
        "spec": fc,
        "reason": "fact_check 配置完整" if not missing else f"fact_check 缺少 {', '.join(missing)}",
    }


def dry_run_flow_spec(
    spec: FlowSpec,
    fields: dict[str, Any] | None = None,
    *,
    _prepared: bool = False,
    _compiled: tuple[dict | None, list[str]] | None = None,
) -> dict:
    """静态 dry-run：不触网，只验证 FlowSpec 能否构造为可执行请求计划。"""
    api_request, build_errors = (
        _compiled
        if _compiled is not None
        else flow_spec_to_api_request(spec, _prepared=_prepared)
    )
    if build_errors or api_request is None:
        return {
            "ok": False,
            "mode": "dry_run",
            "stage": "build",
            "build_errors": build_errors,
            "self_check": [],
            "missing_params": [],
            "request_count": 0,
            "execution_plan": [],
            "fact_check": _fact_check_report(api_request),
        }

    params = _api_params(api_request)
    samples = _api_sample_inputs(api_request)
    provided = dict(fields or {})
    missing = [
        name for name in flow_spec_required_params(spec)
        if name not in provided and name not in samples
    ]
    dry_fields = _dry_fields(api_request, fields)
    self_check_errors = self_check(api_request)
    raw_steps = api_request.get("steps") or [api_request]
    plan = [_dry_step_preview(st, dry_fields, i) for i, st in enumerate(raw_steps)]
    construct_errors = [p["error"] for p in plan if p.get("error")]
    fact = _fact_check_report(api_request)
    ok = not build_errors and not self_check_errors and not construct_errors and not missing and bool(fact.get("passed"))
    return {
        "ok": ok,
        "mode": "dry_run",
        "stage": "ok" if ok else "check",
        "build_errors": build_errors,
        "self_check": self_check_errors,
        "construct_errors": construct_errors,
        "missing_params": missing,
        "params": params,
        "required": flow_spec_required_params(spec),
        "request_count": len(raw_steps),
        "execution_plan": [
            {
                "index": p["index"],
                "method": p["method"],
                "path": p["path"],
                "params": p["params"],
                "link_count": len(p["links"]),
                "constructible": p["constructible"],
                "has_body": p["has_body"],
            }
            for p in plan
        ],
        "request_previews": plan,
        "fact_check": fact,
    }

_PENDING_FLOW_SPEC_HELPERS = {'_ENUM_PARAM_TYPES': 'dano.execution.page.flow_materialization.field_contracts.option_projection', '_ENUM_SOURCE_KINDS': 'dano.execution.page.flow_materialization.field_contracts.option_projection', '_enum_label_value': 'dano.execution.page.flow_materialization.field_contracts.option_projection', '_enum_option_map_from_options': 'dano.execution.page.flow_materialization.field_contracts.option_projection', '_param_exposed_to_caller': 'dano.execution.page.flow_materialization.field_contracts.caller_ownership', '_param_field_manually_edited': 'dano.execution.page.flow_materialization.field_contracts.common', '_param_requires_caller_input': 'dano.execution.page.flow_materialization.field_contracts.caller_ownership', '_query_key_from_param': 'dano.execution.page.flow_materialization.request_steps', '_request_path': 'dano.execution.page.recording_facts', '_runtime_param_publish_error': 'dano.execution.page.flow_release', 'flow_spec_to_summary': 'dano.execution.page.flow_client_projection', 'prepare_flow_spec_for_publish': 'dano.execution.page.flow_release', '_active_capability_step_ids': 'dano.execution.page.capability_refs', '_capability_execution_contract': 'dano.execution.page.capability_views', '_capability_to_api_dict': 'dano.execution.page.capability_views', 'executable_flow_links': 'dano.execution.page.capability_views', '_validate_assertion_contract': 'dano.execution.page.replay', 'capability_to_flow_spec_view': 'dano.execution.page.capability_views', 'flow_spec_capability_contracts': 'dano.execution.page.capability_views'}


def _bind_flow_spec_helpers() -> None:
    import sys
    module_globals = globals()
    for name, owner in _PENDING_FLOW_SPEC_HELPERS.items():
        mod = sys.modules.get(owner)
        if mod is None or not hasattr(mod, name):
            continue
        module_globals[name] = getattr(mod, name)


_bind_flow_spec_helpers()
