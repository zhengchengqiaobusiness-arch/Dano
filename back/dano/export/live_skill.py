"""Pure recording-to-Skill contracts used by the live forge and final exporter."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlsplit

from dano.execution.page.flow_spec import FlowSpec


LIVE_SKILL_DRAFT_PROTOCOL = "dano.live_skill_draft.v1"


def _data(value: FlowSpec | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, FlowSpec):
        return value.model_dump(mode="json", exclude_none=True)
    if not isinstance(value, Mapping):
        raise TypeError("flow_spec must be a FlowSpec or object")
    return copy.deepcopy(dict(value))


def _slug(value: Any, fallback: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").casefold()).strip("-")
    return text or fallback


def _pointer(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("/"):
        return text
    parts = [part for part in re.split(r"\.|\[|\]", text) if part]
    return "/" + "/".join(part.replace("~", "~0").replace("/", "~1") for part in parts)


def _schema(value: Any) -> dict[str, Any]:
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, list):
        return {"type": "array", "items": _schema(value[0]) if value else {}}
    if isinstance(value, Mapping):
        return {
            "type": "object",
            "properties": {str(key): _schema(item) for key, item in value.items()},
        }
    return {}


def _json_type(value: Any) -> str:
    normalized = str(value or "string").casefold()
    return {
        "datetime": "string",
        "date": "string",
        "enum": "string",
        "list-enum": "array",
        "int": "integer",
        "bool": "boolean",
    }.get(normalized, normalized if normalized in {
        "null", "boolean", "integer", "number", "string", "object", "array",
    } else "string")


def _origin(spec: Mapping[str, Any]) -> str:
    meta = spec.get("meta") if isinstance(spec.get("meta"), Mapping) else {}
    page = meta.get("page_context") if isinstance(meta.get("page_context"), Mapping) else {}
    candidates = (page.get("url"), meta.get("source_page_url"))
    for raw in candidates:
        parsed = urlsplit(str(raw or ""))
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    for step in spec.get("steps") or []:
        parsed = urlsplit(str((step or {}).get("url") or ""))
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    raise ValueError("FlowSpec 缺少可验证的目标 HTTP(S) origin")


def _source_digest(spec: Mapping[str, Any]) -> str:
    raw = json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _step_ids(capability: Mapping[str, Any]) -> list[str]:
    ids = [str(item) for item in capability.get("step_ids") or [] if str(item)]
    if ids:
        return list(dict.fromkeys(ids))

    def visit(nodes: Sequence[Any]) -> None:
        for raw in nodes:
            if not isinstance(raw, Mapping):
                continue
            step_id = str(raw.get("step_id") or "")
            if step_id and step_id not in ids:
                ids.append(step_id)
            for key in ("steps", "then", "otherwise", "else", "children"):
                child = raw.get(key)
                if isinstance(child, list):
                    visit(child)

    visit(capability.get("nodes") or [])
    return ids


def _target(param: Mapping[str, Any], step: Mapping[str, Any]) -> tuple[str, str]:
    path = str(param.get("path") or param.get("key") or "")
    lowered = path.casefold()
    for prefix, location in (("query.", "query"), ("header.", "header"), ("cookie.", "cookie"), ("body.", "body")):
        if lowered.startswith(prefix):
            return location, _pointer(path[len(prefix):])
    key = str(param.get("key") or path.rsplit(".", 1)[-1])
    request_path = str(step.get("path") or step.get("url") or "")
    if "{" + key + "}" in request_path:
        return "path", _pointer(key)
    method = str(step.get("method") or "GET").upper()
    return ("query" if method in {"GET", "HEAD"} else "body"), _pointer(path)


def _input_schema(params: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for param in params:
        source_kind = str(param.get("source_kind") or "unknown")
        override = str((param.get("source") or {}).get("override_policy") or "")
        exposed = param.get("exposed_to_user", param.get("exposed_to_caller", True)) is True
        if not exposed or (source_kind != "user_input" and override != "caller_may_override"):
            continue
        name = str(param.get("key") or param.get("path") or "").rsplit(".", 1)[-1]
        if not name:
            continue
        field = {"type": _json_type(param.get("type"))}
        if param.get("description"):
            field["description"] = str(param["description"])
        if param.get("enum_options"):
            field["enum"] = copy.deepcopy(param["enum_options"])
        properties[name] = field
        if param.get("required") is True and source_kind == "user_input":
            required.append(name)
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(dict.fromkeys(required)),
        "properties": properties,
    }


def _binding(param: Mapping[str, Any], step: Mapping[str, Any], action_by_step: Mapping[str, str]) -> dict[str, Any]:
    location, target = _target(param, step)
    source_kind = str(param.get("source_kind") or "unknown")
    source = param.get("source") if isinstance(param.get("source"), Mapping) else {}
    base = {
        "targetLocation": location,
        "targetPointer": target,
        "observedType": _json_type(param.get("wire_type") or param.get("type")),
    }
    name = str(param.get("key") or param.get("path") or "").rsplit(".", 1)[-1]
    if source_kind == "user_input":
        return {**base, "source": "caller_input", "from": _pointer(name)}
    if source_kind == "previous_response":
        producer = action_by_step.get(str(source.get("step_id") or ""))
        response_path = source.get("response_path") or source.get("path")
        if not producer or not response_path:
            raise ValueError(f"previous_response 字段 {name} 缺少 producer/path")
        return {
            **base,
            "source": "prior_response",
            "fromAction": producer,
            "from": _pointer(response_path),
            "overridePolicy": str(source.get("override_policy") or (
                "caller_may_override" if param.get("exposed_to_user") is True else "not_exposed"
            )),
        }
    if source_kind in {"constant", "system_constant"}:
        value = source.get("value", param.get("default_value", param.get("value")))
        return {**base, "source": "constant", "value": copy.deepcopy(value)}
    if source_kind in {"request_header", "cookie", "storage", "current_user"}:
        if location not in {"header", "cookie"}:
            raise ValueError(f"runtime credential field {name} must target header/cookie")
        return {**base, "source": "server_auth"}
    if source_kind in {"system_time", "system_generated", "computed"}:
        strategy = str(source.get("strategy") or source.get("kind") or "")
        if not strategy:
            raise ValueError(f"computed 字段 {name} 缺少确定性 strategy")
        return {**base, "source": "computed", "strategy": strategy}
    raise ValueError(f"字段 {name} 的 source_kind={source_kind!r} 尚不支持确定性运行")


def _success(step: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"httpStatuses": [200]}
    rule = step.get("success_rule") if isinstance(step.get("success_rule"), Mapping) else {}
    field = str(rule.get("field") or "")
    values = list(rule.get("ok_values") or [])
    if field and values:
        result["businessPredicate"] = {"pointer": _pointer(field), "in": copy.deepcopy(values)}
    return result


def _evidence_refs(step: Mapping[str, Any]) -> list[str]:
    meta = step.get("source_meta") if isinstance(step.get("source_meta"), Mapping) else {}
    refs = [str(item) for item in meta.get("evidence_refs") or [] if str(item)]
    if not refs and meta.get("request_id"):
        refs.append(f"requests/{meta['request_id']}")
    if not refs:
        raise ValueError(f"step {step.get('step_id') or step.get('name')} 缺少 evidenceRefs")
    return list(dict.fromkeys(refs))


def _completion_gaps(capability: Mapping[str, Any], steps: Mapping[str, Mapping[str, Any]]) -> list[str]:
    gaps: list[str] = []
    if capability.get("confirmed") is not True or capability.get("status") != "confirmed":
        gaps.append("capability confirmation")
    capability_steps = _step_ids(capability)
    if not capability_steps:
        gaps.append("capability action order")
    for step_id in capability_steps:
        step = steps.get(step_id)
        if step is None:
            gaps.append(f"missing step {step_id}")
            continue
        meta = step.get("source_meta") if isinstance(step.get("source_meta"), Mapping) else {}
        if not (meta.get("evidence_refs") or meta.get("request_id")):
            gaps.append(f"{step_id} evidence")
        if not isinstance(step.get("success_rule"), Mapping) or not step.get("success_rule"):
            gaps.append(f"{step_id} business success")
        if str(step.get("method") or "GET").upper() not in {"GET", "HEAD", "OPTIONS"} and not step.get("fact_check"):
            gaps.append(f"{step_id} recheck")
    return gaps


def _capability_fields(capability: Mapping[str, Any], steps: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for step_id in _step_ids(capability):
        for param in steps.get(step_id, {}).get("params") or []:
            if not isinstance(param, Mapping):
                continue
            source = param.get("source") if isinstance(param.get("source"), Mapping) else {}
            fields.append({
                "stepId": step_id,
                "path": str(param.get("path") or ""),
                "sourceKind": str(param.get("source_kind") or "unknown"),
                "source": copy.deepcopy(dict(source)),
                "overridePolicy": str(source.get("override_policy") or (
                    "caller_may_override" if param.get("exposed_to_user") is True else "not_exposed"
                )),
                "exposedToCaller": param.get("exposed_to_user", True) is True,
                "evidence": copy.deepcopy(param.get("evidence") or []),
            })
    return fields


def compile_flow_spec_contract(
    flow_spec: FlowSpec | Mapping[str, Any],
    *,
    skill_id: str,
    decisions: Sequence[Mapping[str, Any]] = (),
    limitations: Sequence[Mapping[str, Any]] = (),
    require_complete: bool = True,
) -> dict[str, Any]:
    """Compile host-owned FlowSpec facts into canonical, fail-closed Contract v1."""
    spec = _data(flow_spec)
    steps = {
        str(step.get("step_id") or ""): step
        for step in spec.get("steps") or []
        if isinstance(step, Mapping) and str(step.get("step_id") or "")
    }
    action_by_step = {
        step_id: _slug(step_id, f"action-{index + 1}")
        for index, step_id in enumerate(steps)
    }
    raw_capabilities = [
        item for item in spec.get("capabilities") or [] if isinstance(item, Mapping)
    ]
    unsupported_reasons: dict[str, tuple[str, list[str]]] = {}

    def capability_evidence(capability: Mapping[str, Any]) -> list[str]:
        refs: list[str] = []
        for step_id in _step_ids(capability):
            step = steps.get(step_id) or {}
            meta = step.get("source_meta") if isinstance(step.get("source_meta"), Mapping) else {}
            refs.extend(str(item) for item in meta.get("evidence_refs") or [] if str(item))
        return list(dict.fromkeys(refs)) or ["references/limitations.md"]

    supported_capabilities = [
        item for item in raw_capabilities if not _completion_gaps(item, steps)
    ]
    for item in raw_capabilities:
        gaps = _completion_gaps(item, steps)
        if gaps:
            capability_id = _slug(item.get("name") or item.get("capability_id"), "capability")
            unsupported_reasons[f"unsupported-{capability_id}"] = (
                "尚未证据闭合: " + ", ".join(gaps),
                capability_evidence(item),
            )

    # Remove a capability whenever one of its runtime bindings cannot be
    # resolved from caller input, a safe constant/auth source, a supported
    # computation, or another still-supported action. Repeat to close over
    # producer dependencies after a producer capability is removed.
    while True:
        supported_step_ids = {
            step_id for capability in supported_capabilities for step_id in _step_ids(capability)
        }
        rejected: dict[int, str] = {}
        for capability in supported_capabilities:
            try:
                for step_id in _step_ids(capability):
                    step = steps[step_id]
                    for param in step.get("params") or []:
                        if not isinstance(param, Mapping):
                            continue
                        binding = _binding(param, step, action_by_step)
                        producer = str((param.get("source") or {}).get("step_id") or "")
                        if binding.get("source") == "prior_response" and producer not in supported_step_ids:
                            raise ValueError(f"prior_response producer {producer!r} 不可执行")
            except (KeyError, TypeError, ValueError) as exc:
                rejected[id(capability)] = str(exc)
        if not rejected:
            break
        kept: list[Mapping[str, Any]] = []
        for capability in supported_capabilities:
            reason = rejected.get(id(capability))
            if reason is None:
                kept.append(capability)
                continue
            capability_id = _slug(capability.get("name") or capability.get("capability_id"), "capability")
            unsupported_reasons[f"unsupported-{capability_id}"] = (
                "确定性运行不支持: " + reason,
                capability_evidence(capability),
            )
        supported_capabilities = kept

    supported_step_ids = {
        step_id for capability in supported_capabilities for step_id in _step_ids(capability)
    }
    referenced_step_ids = {
        step_id for capability in raw_capabilities for step_id in _step_ids(capability)
    }
    for step_id in steps:
        if step_id not in referenced_step_ids:
            unsupported_reasons[f"unsupported-step-{_slug(step_id, 'step')}"] = (
                "接口步骤未被任何证据闭合能力引用，运行时不暴露",
                capability_evidence({"step_ids": [step_id]}),
            )
    if not raw_capabilities:
        unsupported_reasons["unsupported-empty-contract"] = (
            "没有可交付的请求能力",
            ["references/limitations.md"],
        )
    if require_complete and unsupported_reasons:
        details = "; ".join(
            f"{item_id}: {reason}" for item_id, (reason, _refs) in unsupported_reasons.items()
        )
        raise ValueError("canonical Contract 不完整，禁止自动发布: " + details)
    actions: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    for step_id, step in steps.items():
        if step_id not in supported_step_ids:
            continue
        params = [item for item in step.get("params") or [] if isinstance(item, Mapping)]
        bindings = [_binding(param, step, action_by_step) for param in params]
        for binding in bindings:
            if binding["source"] == "prior_response":
                relations.append({
                    "producerAction": binding["fromAction"],
                    "producerPointer": binding["from"],
                    "consumerAction": action_by_step[step_id],
                    "consumerPointer": binding["targetPointer"],
                    "observedType": binding["observedType"],
                    "sourceKind": "prior_response",
                    "overridePolicy": binding["overridePolicy"],
                })
        content_type = str(step.get("content_type") or "").casefold()
        method = str(step.get("method") or "GET").upper()
        body_kind = (
            "multipart" if "multipart" in content_type
            else "form" if "x-www-form-urlencoded" in content_type
            else "none" if method in {"GET", "HEAD"} and step.get("body_template") in (None, "")
            else "json"
        )
        raw_path = str(step.get("path") or step.get("url") or "/")
        parsed_path = urlsplit(raw_path)
        request_path = parsed_path.path if parsed_path.scheme and parsed_path.netloc else raw_path.split("?", 1)[0]
        action: dict[str, Any] = {
            "id": action_by_step[step_id],
            "description": str(step.get("name") or step_id),
            "effect": "read" if method in {"GET", "HEAD", "OPTIONS"} else "mutation",
            "inputSchema": _input_schema(params),
            "outputSchema": _schema(step.get("response_json")),
            "request": {
                "method": method,
                "path": request_path or "/",
                "bodyKind": body_kind,
                "responseKind": "json" if step.get("response_json") is not None else "empty",
                "bindings": bindings,
            },
            "success": _success(step),
            "evidenceRefs": _evidence_refs(step),
        }
        if isinstance(step.get("fact_check"), Mapping) and step["fact_check"]:
            action["recheck"] = copy.deepcopy(dict(step["fact_check"]))
        actions.append(action)

    capabilities = []
    for raw in supported_capabilities:
        if not isinstance(raw, Mapping):
            continue
        action_order = [action_by_step[item] for item in _step_ids(raw) if item in action_by_step]
        if not action_order:
            continue
        capabilities.append({
            "id": _slug(raw.get("name") or raw.get("capability_id"), f"capability-{len(capabilities) + 1}"),
            "description": str(raw.get("title") or raw.get("intent") or raw.get("name") or ""),
            "status": "frozen" if raw.get("confirmed") is True and raw.get("status") == "confirmed" else "draft",
            "actionOrder": action_order,
            "inputSchema": copy.deepcopy(raw.get("input_schema") or {"type": "object"}),
            "outputSchema": copy.deepcopy(raw.get("output_schema") or {"type": "object"}),
            "fields": _capability_fields(raw, steps),
            "completionEvidence": list(dict.fromkeys(
                ref for step_id in _step_ids(raw) if step_id in steps for ref in _evidence_refs(steps[step_id])
            )),
        })

    unsupported = []
    rendered_limitations = [
        copy.deepcopy(dict(item)) for item in limitations if isinstance(item, Mapping)
    ]
    for index, item in enumerate(limitations):
        if not isinstance(item, Mapping):
            continue
        refs = [str(ref) for ref in item.get("evidenceRefs") or [] if str(ref)]
        unsupported.append({
            "id": _slug(item.get("id"), f"limitation-{index + 1}"),
            "reason": str(item.get("reason") or "unsupported runtime behavior"),
            "evidenceRefs": refs or ["references/limitations.md"],
        })
    for item_id, (reason, refs) in unsupported_reasons.items():
        unsupported.append({"id": item_id, "reason": reason, "evidenceRefs": refs})
        rendered_limitations.append({"id": item_id, "reason": reason})
    meta = spec.get("meta") if isinstance(spec.get("meta"), Mapping) else {}
    goal = spec.get("goal") if isinstance(spec.get("goal"), Mapping) else {}
    live_goal = goal.get("live_goal_spec") if isinstance(goal.get("live_goal_spec"), Mapping) else goal
    goal_version = int(live_goal.get("version") or 0)
    contract = {
        "schemaVersion": "1.0",
        "skill": {
            "id": _slug(skill_id, "generated-skill"),
            "version": "1.0.0",
            "description": str(spec.get("business_description") or spec.get("title") or skill_id),
        },
        "source": {
            "fileName": str(meta.get("source_file") or "recording-evidence.json"),
            "sha256": _source_digest(spec),
        },
        "target": {"origin": _origin(spec)},
        "actions": actions,
        "relations": relations,
        "capabilities": capabilities,
        "decisions": [copy.deepcopy(dict(item)) for item in decisions if isinstance(item, Mapping)],
        "limitations": rendered_limitations,
        "unsupportedCapabilities": unsupported,
    }
    if goal_version > 0:
        contract["goalVersion"] = goal_version
    return contract


def _observation_value(observation: Any, name: str, default: Any) -> Any:
    if isinstance(observation, Mapping):
        return observation.get(name, default)
    return getattr(observation, name, default)


def _snapshot_text(observation: Any, name: str) -> str:
    snapshot = _observation_value(observation, name, {})
    if isinstance(snapshot, Mapping):
        return str(snapshot.get("snapshot") or "")
    return ""


def _json_pointer_get(value: Any, pointer: str) -> Any:
    current = value
    if pointer == "":
        return current
    if not pointer.startswith("/"):
        raise ValueError(f"invalid JSON pointer: {pointer}")
    for encoded in pointer[1:].split("/"):
        part = encoded.replace("~1", "/").replace("~0", "~")
        current = current[int(part)] if isinstance(current, list) else current[part]
    return current


def _json_pointer_set(target: dict[str, Any], pointer: str, value: Any) -> None:
    parts = [part.replace("~1", "/").replace("~0", "~") for part in pointer.lstrip("/").split("/")]
    current: dict[str, Any] = target
    for part in parts[:-1]:
        child = current.setdefault(part, {})
        if not isinstance(child, dict):
            raise ValueError(f"input pointer conflicts with another recorded value: {pointer}")
        current = child
    if not parts or not parts[-1]:
        raise ValueError("caller input pointer must not be the document root")
    existing = current.get(parts[-1], value)
    if existing != value:
        raise ValueError(f"caller input pointer has conflicting recorded values: {pointer}")
    current[parts[-1]] = copy.deepcopy(value)


def _merge_input_documents(target: dict[str, Any], incoming: Mapping[str, Any], path: str = "") -> None:
    """Merge caller inputs by JSON leaf, rejecting only true leaf conflicts."""
    for key, value in incoming.items():
        pointer = f"{path}/{str(key).replace('~', '~0').replace('/', '~1')}"
        if key not in target:
            target[key] = copy.deepcopy(value)
            continue
        current = target[key]
        if isinstance(current, dict) and isinstance(value, Mapping):
            _merge_input_documents(current, value, pointer)
        elif current != value:
            raise ValueError(
                f"交付验证证据中的 capability 输入冲突: {pointer}",
            )


def _body_value(request: Mapping[str, Any], body_kind: str) -> Any:
    raw = request.get("request_json", request.get("post_data"))
    if body_kind == "none":
        return None
    if not isinstance(raw, str):
        return copy.deepcopy(raw)
    if body_kind == "form":
        return {
            name: values if len(values) > 1 else values[0]
            for name, values in parse_qs(raw, keep_blank_values=True).items()
        }
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("交付验证证据中的请求 body 不是可重放 JSON") from exc


def _request_headers(request: Mapping[str, Any]) -> dict[str, Any]:
    raw = request.get("headers") or request.get("request_headers") or {}
    if isinstance(raw, Mapping):
        return {str(name).casefold(): value for name, value in raw.items()}
    if isinstance(raw, list):
        return {
            str(item.get("name") or "").casefold(): item.get("value")
            for item in raw if isinstance(item, Mapping) and item.get("name")
        }
    raise ValueError("交付验证证据中的请求 headers 无效")


def _recorded_path_values(template: str, actual: str) -> dict[str, str]:
    names: list[str] = []
    pattern = ""
    cursor = 0
    for match in re.finditer(r"\{([^{}]+)\}", template):
        pattern += re.escape(template[cursor:match.start()]) + "([^/]+)"
        names.append(match.group(1))
        cursor = match.end()
    pattern += re.escape(template[cursor:])
    matched = re.fullmatch(pattern, actual)
    if matched is None:
        raise ValueError("交付验证证据中的请求路径与 Contract 不一致")
    from urllib.parse import unquote

    return {name: unquote(value) for name, value in zip(names, matched.groups(), strict=True)}


def _canonical_recorded_request(
    action: Mapping[str, Any], request: Mapping[str, Any], origin: str,
) -> dict[str, Any]:
    parsed = urlsplit(str(request.get("url") or ""))
    observed_origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
    if observed_origin != origin:
        raise ValueError("交付验证证据中的请求 origin 与 Contract 不一致")
    contract_request = action["request"]
    if str(request.get("method") or "").upper() != contract_request["method"]:
        raise ValueError("交付验证证据中的请求方法与 Contract 不一致")
    headers = _request_headers(request)
    rendered_headers = []
    cookie_names: list[str] = []
    raw_cookies = request.get("cookies")
    observed_cookies = {
        str(item.get("name") or ""): item.get("value")
        for item in raw_cookies or [] if isinstance(item, Mapping) and item.get("name")
    } if isinstance(raw_cookies, list) else {}
    if "cookie" in headers:
        observed_cookies.update({
            name.strip(): value for part in str(headers["cookie"]).split(";")
            for name, separator, value in [part.partition("=")] if separator and name.strip()
        })
    for binding in contract_request["bindings"]:
        name = str(binding["targetPointer"]).strip("/").replace("~1", "/").replace("~0", "~")
        if binding["targetLocation"] == "header":
            if name.casefold() not in headers:
                raise ValueError(f"交付验证证据缺少请求 header: {name}")
            rendered_headers.append({
                "name": name,
                "value": "<redacted>" if binding["source"] == "server_auth" else headers[name.casefold()],
            })
        elif binding["targetLocation"] == "cookie":
            if name not in observed_cookies:
                raise ValueError(f"交付验证证据缺少请求 cookie: {name}")
            cookie_names.append(name)
    body_kind = str(contract_request["bodyKind"])
    return {
        "method": contract_request["method"],
        "origin": origin,
        "path": parsed.path or "/",
        "query": [
            {"name": name, "value": value}
            for name, value in parse_qsl(parsed.query, keep_blank_values=True)
        ],
        "bodyKind": body_kind,
        "body": _body_value(request, body_kind),
        "headers": sorted(rendered_headers, key=lambda item: item["name"].casefold()),
        "cookieNames": sorted(cookie_names),
    }


def _recorded_binding_value(
    action: Mapping[str, Any], canonical_request: Mapping[str, Any], binding: Mapping[str, Any],
) -> Any:
    location = binding["targetLocation"]
    pointer = str(binding["targetPointer"])
    name = pointer.strip("/").replace("~1", "/").replace("~0", "~")
    if location == "path":
        return _recorded_path_values(action["request"]["path"], str(canonical_request["path"]))[name]
    if location == "query":
        matches = [item["value"] for item in canonical_request["query"] if item["name"] == name]
        if len(matches) != 1:
            raise ValueError(f"交付验证证据中的 query 参数不唯一: {name}")
        return matches[0]
    if location == "body":
        return _json_pointer_get(canonical_request["body"], pointer)
    if location == "header":
        matches = [item["value"] for item in canonical_request["headers"] if item["name"].casefold() == name.casefold()]
        if len(matches) != 1:
            raise ValueError(f"交付验证证据中的 header 参数不唯一: {name}")
        return matches[0]
    raise ValueError(f"交付验证证据暂不支持 caller_input 位置: {location}")


def _value_pointers(value: Any, expected: Any, pointer: str = "") -> list[str]:
    matches = [pointer] if value == expected else []
    if isinstance(value, Mapping):
        for key, child in value.items():
            encoded = str(key).replace("~", "~0").replace("/", "~1")
            matches.extend(_value_pointers(child, expected, f"{pointer}/{encoded}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            matches.extend(_value_pointers(child, expected, f"{pointer}/{index}"))
    return matches


def _verification_comparisons(
    reconciliation: Mapping[str, Any], response: Any, page_after: str,
) -> list[dict[str, Any]]:
    explicit = reconciliation.get("verification_comparisons")
    if isinstance(explicit, list) and explicit:
        rows = [copy.deepcopy(dict(item)) for item in explicit if isinstance(item, Mapping)]
    else:
        rows = []
        fields = reconciliation.get("fields") if isinstance(reconciliation.get("fields"), Mapping) else {}
        for field in fields.values():
            if not isinstance(field, Mapping) or field.get("matched") is not True or field.get("source") != "business_response":
                continue
            value = field.get("actual")
            pointers = _value_pointers(response, value)
            page_text = str(value)
            if len(pointers) == 1 and page_text and page_text in page_after:
                rows.append({
                    "pageText": page_text,
                    "responsePointer": pointers[0],
                    "expectedValue": copy.deepcopy(value),
                })
    verified: list[dict[str, Any]] = []
    base_fields = {"pageText", "responsePointer", "expectedValue"}
    attestation_fields = {
        "write_action_id", "actualValue", "request_id", "response_evidence_ref",
        "page_evidence_ref", "verified",
    }
    for row in rows:
        if not base_fields <= set(row) or set(row) - base_fields - attestation_fields:
            raise ValueError("交付验证证据中的 page comparison 结构无效")
        if set(row) & attestation_fields and not (
            row.get("verified") is True
            and row.get("actualValue") == row.get("expectedValue")
            and str(row.get("response_evidence_ref") or "")
            and str(row.get("page_evidence_ref") or "")
        ):
            raise ValueError("交付验证证据中的宿主 page comparison 核验无效")
        if not str(row["pageText"]) or str(row["pageText"]) not in page_after:
            raise ValueError("交付验证证据中的 page comparison 不存在于操作后页面")
        try:
            actual = _json_pointer_get(response, str(row["responsePointer"]))
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise ValueError("交付验证证据中的 page comparison 响应路径不存在") from exc
        if actual != row["expectedValue"]:
            raise ValueError("交付验证证据中的 page comparison 与业务响应不一致")
        verified.append({key: copy.deepcopy(row[key]) for key in (
            "pageText", "responsePointer", "expectedValue",
        )})
    if not verified:
        raise ValueError("交付验证证据缺少真实 page/business comparison")
    return verified


def _verified_write_recheck(
    *,
    write_action_id: str,
    goal_version: int | None,
    goal_item_id: str,
    observations: Sequence[Any],
    reconciliations: Sequence[Mapping[str, Any]],
    ledger: Sequence[Mapping[str, Any]],
) -> tuple[Any, Mapping[str, Any]]:
    """Resolve one later, host-recorded page recheck for a write action."""
    candidates: list[tuple[Any, Mapping[str, Any]]] = []
    for observation in observations:
        recheck_action_id = str(_observation_value(observation, "action_id", "") or "")
        semantic = _observation_value(observation, "semantic", {})
        if (
            not recheck_action_id
            or recheck_action_id == write_action_id
            or not isinstance(semantic, Mapping)
            or str(semantic.get("recheck_for_action_id") or "") != write_action_id
            or _observation_value(observation, "ok", False) is not True
            or not _snapshot_text(observation, "after_snapshot")
        ):
            continue
        ledger_matches = [
            row for row in ledger
            if row.get("kind") == "page_recheck"
            and str(row.get("action_id") or "") == recheck_action_id
            and str(row.get("recheck_for_action_id") or "") == write_action_id
            and str(row.get("goal_item_id") or "") == goal_item_id
            and row.get("status") == "valid"
            and (
                goal_version is None
                or 0 < int(row.get("goal_version") or 0) <= goal_version
            )
        ]
        reconciliation_matches = [
            row for row in reconciliations
            if str(row.get("action_id") or "") == recheck_action_id
            and str(row.get("recheck_for_action_id") or "") == write_action_id
            and str(row.get("goal_item_id") or "") == goal_item_id
            and row.get("status") == "verified"
            and (
                goal_version is None
                or 0 < int(row.get("goal_version") or 0) <= goal_version
            )
        ]
        if len(ledger_matches) == 1 and len(reconciliation_matches) == 1:
            candidates.append((observation, reconciliation_matches[0]))
    if len(candidates) != 1:
        raise ValueError(
            f"交付验证证据缺少唯一、独立且由宿主核对的 page_recheck: {write_action_id}",
        )
    return candidates[0]


def _goal_evidence_lineage(
    row: Mapping[str, Any], *, goal_version: int | None, completed_goal_items: set[str], where: str,
) -> str:
    if goal_version is None:
        return str(row.get("goal_item_id") or "")
    evidence_version = int(row.get("goal_version") or 0)
    goal_item_id = str(row.get("goal_item_id") or "")
    if not 0 < evidence_version <= goal_version:
        raise ValueError(f"交付验证证据 GoalSpec 版本无效: {where}")
    if not goal_item_id or goal_item_id not in completed_goal_items:
        raise ValueError(f"交付验证证据不属于当前已完成 GoalSpec 能力: {where}")
    return goal_item_id


def build_delivery_verification_manifest(
    contract: Mapping[str, Any],
    flow_spec: FlowSpec | Mapping[str, Any],
    *,
    action_observations: Sequence[Any],
    reconciliations: Sequence[Mapping[str, Any]],
    evidence_ledger: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build the publish proof solely from captured browser and reconciled host facts."""
    canonical = copy.deepcopy(dict(contract))
    spec = _data(flow_spec)
    actions = {str(item.get("id") or ""): item for item in canonical.get("actions") or [] if isinstance(item, Mapping)}
    if not actions:
        raise ValueError("交付验证证据无法覆盖空 Contract")
    steps = [item for item in spec.get("steps") or [] if isinstance(item, Mapping)]
    step_by_action = {
        _slug(step.get("step_id"), f"action-{index + 1}"): step
        for index, step in enumerate(steps)
    }
    observations = list(action_observations)
    reconciliation_rows = [item for item in reconciliations if isinstance(item, Mapping)]
    ledger_rows = [item for item in evidence_ledger if isinstance(item, Mapping)]
    goal_version = int(canonical["goalVersion"]) if canonical.get("goalVersion") is not None else None
    live_goal = (spec.get("goal") or {}).get("live_goal_spec")
    goal_capabilities = live_goal.get("capabilities") or [] if isinstance(live_goal, Mapping) else []
    completed_goal_items = {
        str(item.get("id") or "")
        for item in goal_capabilities
        if isinstance(item, Mapping)
        and item.get("status") == "completed"
        and item.get("id")
    }
    origin = str((canonical.get("target") or {}).get("origin") or "")
    action_evidence: dict[str, dict[str, Any]] = {}
    caller_inputs: dict[str, dict[str, Any]] = {}

    for action_id, action in actions.items():
        step = step_by_action.get(action_id)
        source_meta = step.get("source_meta") if isinstance(step, Mapping) and isinstance(step.get("source_meta"), Mapping) else {}
        request_id = str(source_meta.get("request_id") or "")
        if not request_id:
            raise ValueError(f"交付验证证据缺少 FlowSpec request_id: {action_id}")
        matches = []
        for observation in observations:
            for request in _observation_value(observation, "network_requests", []) or []:
                if isinstance(request, Mapping) and str(request.get("request_id") or "") == request_id:
                    matches.append((observation, request))
        if len(matches) != 1:
            raise ValueError(f"交付验证证据中的录制请求不唯一: {action_id}")
        observation, recorded_request = matches[0]
        observation_id = str(_observation_value(observation, "action_id", "") or "")
        if not observation_id or _observation_value(observation, "ok", False) is not True:
            raise ValueError(f"交付验证证据中的 Agent Browser 动作未成功: {action_id}")
        if recorded_request.get("trigger_action_id") not in (None, "", observation_id):
            raise ValueError(f"交付验证证据中的请求与 Agent Browser 动作不一致: {action_id}")
        before = _snapshot_text(observation, "before_snapshot")
        immediate_after = _snapshot_text(observation, "after_snapshot")
        if not before or not immediate_after:
            raise ValueError(f"交付验证证据缺少 page-before/page-after: {action_id}")
        matching_reconciliations = [
            row for row in reconciliation_rows
            if str(row.get("action_id") or "") == observation_id and row.get("status") == "verified"
        ]
        if len(matching_reconciliations) != 1:
            raise ValueError(f"交付验证证据缺少唯一 verified reconciliation: {action_id}")
        reconciliation = matching_reconciliations[0]
        goal_item_id = _goal_evidence_lineage(
            reconciliation,
            goal_version=goal_version,
            completed_goal_items=completed_goal_items,
            where=action_id,
        )
        valid_ledger = [
            row for row in ledger_rows
            if str(row.get("action_id") or "") == observation_id
            and str(row.get("goal_item_id") or "") == goal_item_id
            and row.get("status") in {"valid", "historical_effect"}
            and (
                goal_version is None
                or 0 < int(row.get("goal_version") or 0) <= goal_version
            )
        ]
        valid_kinds = {str(row.get("kind") or "") for row in valid_ledger}
        if not {"request_observed", "business_response"} <= valid_kinds:
            raise ValueError(f"交付验证证据缺少 controller request/business ledger: {action_id}")
        comparison_reconciliation = reconciliation
        after = immediate_after
        if action["request"]["method"] not in {"GET", "HEAD", "OPTIONS"}:
            recheck_observation, comparison_reconciliation = _verified_write_recheck(
                write_action_id=observation_id,
                goal_version=goal_version,
                goal_item_id=goal_item_id,
                observations=observations,
                reconciliations=reconciliation_rows,
                ledger=ledger_rows,
            )
            after = _snapshot_text(recheck_observation, "after_snapshot")
        canonical_request = _canonical_recorded_request(action, recorded_request, origin)
        response_data = copy.deepcopy(recorded_request.get("response_json"))
        if response_data is None and action["request"]["responseKind"] != "empty":
            raise ValueError(f"交付验证证据缺少业务响应: {action_id}")
        if isinstance(step, Mapping) and step.get("response_json") != response_data:
            raise ValueError(f"交付验证证据中的业务响应与 FlowSpec 不一致: {action_id}")
        status = int(recorded_request.get("status") or recorded_request.get("status_code") or recorded_request.get("response_status") or 0)
        if status not in action["success"]["httpStatuses"]:
            raise ValueError(f"交付验证证据中的 HTTP 状态未满足 Contract: {action_id}")
        response = {
            "status": status,
            "kind": action["request"]["responseKind"],
            "data": response_data,
        }
        action_evidence[action_id] = {
            "id": action_id,
            "pageBefore": {"snapshot": before},
            "pageAfter": {"snapshot": after},
            "har": {"request": canonical_request, "response": copy.deepcopy(response)},
            "businessResponse": copy.deepcopy(response),
            "comparisons": _verification_comparisons(
                comparison_reconciliation, response_data, after,
            ),
        }
        inputs: dict[str, Any] = {}
        for binding in action["request"]["bindings"]:
            if binding.get("source") == "caller_input":
                _json_pointer_set(
                    inputs,
                    str(binding["from"]),
                    _recorded_binding_value(action, canonical_request, binding),
                )
        caller_inputs[action_id] = inputs

    targets: list[tuple[str, list[str]]] = []
    covered: set[str] = set()
    for capability in canonical.get("capabilities") or []:
        if not isinstance(capability, Mapping):
            continue
        order = [str(item) for item in capability.get("actionOrder") or []]
        if order:
            targets.append((str(capability.get("id") or ""), order))
            covered.update(order)
    targets.extend((action_id, [action_id]) for action_id in actions if action_id not in covered)
    replays = []
    for target, order in targets:
        inputs: dict[str, Any] = {}
        for action_id in order:
            _merge_input_documents(inputs, caller_inputs[action_id])
        replays.append({
            "target": target,
            "input": inputs,
            "actions": [copy.deepcopy(action_evidence[action_id]) for action_id in order],
        })
    manifest = {
        "protocol": "dano.skill_delivery_evidence.v1",
        "sourceSha256": str((canonical.get("source") or {}).get("sha256") or ""),
        "replays": replays,
    }
    if canonical.get("goalVersion") is not None:
        manifest["goalVersion"] = canonical["goalVersion"]
    if (canonical.get("skill") or {}).get("contractDigest"):
        manifest["contractDigest"] = canonical["skill"]["contractDigest"]
    return manifest


def _merge_records(existing: Sequence[Any], incoming: Sequence[Any], key: str) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for item in [*existing, *incoming]:
        if not isinstance(item, Mapping):
            continue
        record = copy.deepcopy(dict(item))
        semantic = item.get("page_semantic") if isinstance(item.get("page_semantic"), Mapping) else {}
        identity = str(
            item.get(key)
            or item.get("id")
            or (
                f"page_semantic:{semantic.get('fingerprint')}"
                if item.get("kind") == "page_semantic" and semantic.get("fingerprint")
                else ""
            )
            or _source_digest(record)
        )
        merged[identity] = record
    return list(merged.values())


def _working_contract(
    spec: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Expose the grounded page/request/field/relation material formed so far."""
    pages: dict[str, dict[str, Any]] = {}
    for row in evidence:
        if not isinstance(row, Mapping) or row.get("status") == "stale":
            continue
        semantic = row.get("page_semantic")
        if row.get("kind") != "page_semantic" or not isinstance(semantic, Mapping):
            continue
        page = copy.deepcopy(dict(semantic))
        identity = str(page.get("fingerprint") or page.get("page_id") or _source_digest(page))
        pages[identity] = page

    requests: list[dict[str, Any]] = []
    fields: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    for raw_step in spec.get("steps") or []:
        if not isinstance(raw_step, Mapping):
            continue
        step_id = str(raw_step.get("step_id") or "")
        meta = raw_step.get("source_meta") if isinstance(raw_step.get("source_meta"), Mapping) else {}
        requests.append({
            "id": step_id,
            "method": str(raw_step.get("method") or "GET").upper(),
            "path": str(raw_step.get("path") or raw_step.get("url") or ""),
            "evidence_refs": [str(item) for item in meta.get("evidence_refs") or [] if str(item)],
        })
        for raw_param in raw_step.get("params") or []:
            if not isinstance(raw_param, Mapping):
                continue
            source = raw_param.get("source") if isinstance(raw_param.get("source"), Mapping) else {}
            raw_source_kind = str(raw_param.get("source_kind") or "unknown")
            source_kind = {
                "previous_response": "prior_response",
                "current_user": "runtime_context",
                "storage": "runtime_context",
                "cookie": "runtime_context",
                "page_context": "runtime_context",
                "request_header": "runtime_context",
                "system_time": "runtime_context",
                "system_generated": "runtime_context",
                "constant": "system_constant",
                "page_enum": "api_option",
                "static_enum": "api_option",
                "manual_enum": "api_option",
                "form_option": "api_option",
                "selected_option_field": "api_option",
            }.get(raw_source_kind, raw_source_kind)
            path = str(raw_param.get("path") or raw_param.get("key") or "")
            fields.append({
                "step_id": step_id,
                "path": path,
                "public_name": str(raw_param.get("public_name") or raw_param.get("key") or path),
                "source_kind": source_kind,
                "required": bool(raw_param.get("required")),
                "exposed_to_caller": raw_param.get("exposed_to_user", True) is True,
                "override_policy": str(source.get("override_policy") or (
                    "caller_may_override" if raw_param.get("exposed_to_user", True) is True else "not_exposed"
                )),
                "evidence": copy.deepcopy(raw_param.get("evidence") or []),
            })
            producer = str(source.get("step_id") or "")
            if source_kind == "prior_response" and producer:
                relations.append({
                    "producer_step_id": producer,
                    "producer_path": str(source.get("response_path") or source.get("path") or ""),
                    "consumer_step_id": step_id,
                    "consumer_path": path,
                    "source_kind": "prior_response",
                    "override_policy": str(source.get("override_policy") or "not_exposed"),
                })
    return {
        "pages": list(pages.values()),
        "requests": requests,
        "fields": fields,
        "relations": relations,
    }


def _goal_capability_id(
    capability: Mapping[str, Any], goal_spec: Mapping[str, Any],
    planned_capabilities: Sequence[Mapping[str, Any]], *, only_capability: bool,
) -> str:
    goal_capabilities = [
        item for item in goal_spec.get("capabilities") or [] if isinstance(item, Mapping)
    ]
    references = {
        str(capability.get(key) or "").strip()
        for key in ("goal_item_id", "name", "capability_id", "title")
        if str(capability.get(key) or "").strip()
    }
    step_ids = set(_step_ids(capability))
    planned_matches = [
        item for item in planned_capabilities
        if str(item.get("name") or "") in references
        or str(item.get("capability_id") or "") in references
    ]
    if not planned_matches and step_ids:
        planned_matches = [
            item for item in planned_capabilities
            if {str(value) for value in item.get("step_ids") or []} == step_ids
        ]
    if len(planned_matches) == 1:
        explicit = str(planned_matches[0].get("goal_capability_id") or "")
        if any(str(item.get("id") or "") == explicit for item in goal_capabilities):
            return explicit
    for goal in goal_capabilities:
        goal_id = str(goal.get("id") or "")
        label = str(goal.get("label") or "")
        if goal_id in references or label in references:
            return goal_id
    if only_capability and len(goal_capabilities) == 1:
        return str(goal_capabilities[0].get("id") or "")
    return ""


def live_skill_draft_completion(
    draft: Mapping[str, Any] | None, goal_spec: Mapping[str, Any],
) -> dict[str, Any]:
    """Prove that the current GoalSpec is represented by frozen live chapters."""
    current = dict(draft or {})
    version = int(goal_spec.get("version") or 0)
    if current.get("protocol") != LIVE_SKILL_DRAFT_PROTOCOL:
        return {"complete": False, "reason": "live_skill_draft_missing"}
    if int(current.get("goal_version") or 0) != version:
        return {"complete": False, "reason": "goal_version_mismatch"}
    expected = {
        str(item.get("id") or "")
        for item in goal_spec.get("capabilities") or []
        if isinstance(item, Mapping) and item.get("id")
    }
    chapters = current.get("chapters") if isinstance(current.get("chapters"), Mapping) else {}
    frozen = {
        str(item.get("goal_capability_id") or "")
        for item in chapters.values()
        if isinstance(item, Mapping)
        and item.get("status") == "frozen"
        and item.get("goal_capability_id")
    }
    missing = sorted(expected - frozen)
    return {
        "complete": bool(expected) and not missing,
        "reason": "" if expected and not missing else "live_skill_chapters_incomplete",
        "goal_version": version,
        "expected_goal_capability_ids": sorted(expected),
        "frozen_goal_capability_ids": sorted(frozen),
        "missing_goal_capability_ids": missing,
    }


def update_live_skill_draft(
    current: Mapping[str, Any] | None,
    *,
    goal_spec: Mapping[str, Any],
    flow_spec: FlowSpec | Mapping[str, Any],
    decisions: Sequence[Mapping[str, Any]] = (),
    evidence: Sequence[Mapping[str, Any]] = (),
    chapter_states: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Incrementally refresh chapter material and freeze evidence-closed capabilities."""
    previous = copy.deepcopy(dict(current or {}))
    spec = _data(flow_spec)
    version = int(goal_spec.get("version") or 1)
    old_chapters = previous.get("chapters") if isinstance(previous.get("chapters"), Mapping) else {}
    chapters: dict[str, dict[str, Any]] = {}
    steps = {str(item.get("step_id") or ""): item for item in spec.get("steps") or [] if isinstance(item, Mapping)}
    raw_capabilities = [
        item for item in spec.get("capabilities") or [] if isinstance(item, Mapping)
    ]
    meta = spec.get("meta") if isinstance(spec.get("meta"), Mapping) else {}
    capability_model = (
        meta.get("capability_model")
        if isinstance(meta.get("capability_model"), Mapping) else {}
    )
    semantic_plan = (
        capability_model.get("semantic_plan")
        if isinstance(capability_model.get("semantic_plan"), Mapping) else {}
    )
    planned_capabilities = [
        item for item in semantic_plan.get("capabilities") or [] if isinstance(item, Mapping)
    ]
    for index, capability in enumerate(raw_capabilities):
        if not isinstance(capability, Mapping):
            continue
        chapter_id = _slug(capability.get("name") or capability.get("capability_id"), f"capability-{index + 1}")
        material = {
            "capability": copy.deepcopy(dict(capability)),
            "steps": [copy.deepcopy(steps[item]) for item in _step_ids(capability) if item in steps],
        }
        digest = _source_digest(material)
        contract_closed = not _completion_gaps(capability, steps)
        goal_capability_id = _goal_capability_id(
            capability,
            goal_spec,
            planned_capabilities,
            only_capability=len(raw_capabilities) == 1,
        )
        host_state = (
            chapter_states.get(goal_capability_id, {})
            if chapter_states is not None and goal_capability_id else {}
        )
        host_closed = bool(
            chapter_states is None
            or (
                host_state.get("status") == "frozen"
                and int(host_state.get("goal_version") or 0) == version
            )
        )
        closed = contract_closed and host_closed
        old = old_chapters.get(chapter_id) if isinstance(old_chapters.get(chapter_id), Mapping) else {}
        unchanged_frozen = old.get("status") == "frozen" and old.get("contract_digest") == digest
        chapters[chapter_id] = {
            "id": chapter_id,
            "title": str(capability.get("title") or capability.get("name") or chapter_id),
            "status": "frozen" if closed else "draft",
            "goal_capability_id": goal_capability_id,
            "host_evidence_closed": host_closed,
            "contract_closed": contract_closed,
            "contract_digest": digest,
            "frozen_goal_version": old.get("frozen_goal_version") if unchanged_frozen else version if closed else None,
            "material": material,
        }
    return {
        "protocol": LIVE_SKILL_DRAFT_PROTOCOL,
        "goal_version": version,
        "revision": int(previous.get("revision") or 0) + 1,
        "chapters": chapters,
        "decisions": _merge_records(previous.get("decisions") or [], decisions, "decision_id"),
        "evidence": _merge_records(previous.get("evidence") or [], evidence, "evidence_id"),
        "working_contract": _working_contract(spec, evidence),
    }
