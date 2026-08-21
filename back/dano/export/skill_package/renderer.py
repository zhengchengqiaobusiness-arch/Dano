"""Render published page recordings as self-contained, direct-API skill packages."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import structlog

from dano.infra.run_logging import emit_run_exception, note_run_fact

from dano.export.skill_package.validator import (
    flow_spec_verification_ids,
    validate_skill_package,
)
from dano.onboarding.skill_generation.validate import handbook_text_is_banned


log = structlog.get_logger(__name__)
_SECRET_KEY_RE = re.compile(r"(?i)(authorization|cookie|token|secret|password|session|credential)")
_INLINE_SECRET_RE = re.compile(
    r"(?i)\b(Bearer|Basic|Token)\s+[A-Za-z0-9._~+/-]{8,}|"
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
)


def _slug(value: str) -> str:
    raw = str(value or "skill")
    slug = re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]+", "-", raw.casefold().replace(".", "-").replace("_", "-"))).strip("-")
    if not slug or slug in {"skill", "dano"}:
        slug = "skill-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
    if len(slug) > 80:
        suffix = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
        slug = f"{slug[:69].rstrip('-')}-{suffix}"
    return slug


def package_slug(skill_id: str) -> str:
    """Use a stable suffix so package and proxy exports can coexist."""
    return f"dano-{_slug(skill_id)}-package"


def _script_slug(value: str) -> str:
    raw = str(value or "capability")
    slug = re.sub(r"_+", "_", re.sub(r"[^a-z0-9_]+", "_", raw.casefold().replace("-", "_"))).strip("_")
    slug = slug or "capability_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
    if slug in sys.stdlib_module_names or slug in {"client", "wire_format", "format_list"}:
        slug = f"capability_{slug}"
    return slug


def _scrub(node: Any, key: str = "") -> Any:
    if _SECRET_KEY_RE.search(key):
        return "<runtime-auth>"
    if isinstance(node, dict):
        return {str(k): _scrub(v, str(k)) for k, v in node.items()}
    if isinstance(node, list):
        return [_scrub(value, key) for value in node]
    if isinstance(node, str):
        return _INLINE_SECRET_RE.sub("<runtime-auth>", node)
    return node


def _export_reason_code(exc: BaseException) -> str:
    text = str(exc).casefold()
    if "canonical published capability contract" in text:
        return "CANONICAL_CAPABILITY_CONTRACT_MISSING"
    if "invalid published flowspec" in text:
        return "INVALID_PUBLISHED_FLOWSPEC"
    if "skill package validation failed" in text:
        return "SKILL_PACKAGE_VALIDATION_FAILED"
    return "SKILL_PACKAGE_EXPORT_FAILED"


def _export_failure_details(skill, out_dir: str, exc: BaseException) -> dict[str, Any]:  # noqa: ANN001
    api = dict(getattr(skill, "api_request", None) or {})
    release = dict(api.get("_release_snapshot") or {})
    flow = release.get("flow_spec") if isinstance(release.get("flow_spec"), dict) else {}
    meta = dict(flow.get("meta") or {})
    capabilities = api.get("capabilities") if isinstance(api.get("capabilities"), list) else []
    return {
        "skill_id": getattr(skill, "skill_id", ""),
        "output_directory": out_dir,
        "package_written": False,
        "asset_found": True,
        "published_status": getattr(skill, "lifecycle_state", None) or "published",
        "capability_count": len(capabilities),
        "canonical_contract_present": bool(
            capabilities
            and all(
                isinstance(item, dict)
                and isinstance(item.get("execution_contract"), dict)
                and bool(item["execution_contract"].get("steps"))
                for item in capabilities
            )
        ),
        "capability_model_status": meta.get("capability_model"),
        "flow_version": meta.get("current_version"),
        "release_identity": {
            "skill_id": getattr(skill, "skill_id", ""),
            "recording_asset_id": str(getattr(skill, "recording_asset_id", "") or ""),
            "asset_version": getattr(skill, "created_at", None),
        },
        "error": str(exc),
    }


def _safe_text(value: Any) -> str:
    return str(_scrub(str(value or ""))).replace("\r", " ").strip()


def _flow_spec(skill):  # noqa: ANN001, ANN202
    release = dict((skill.api_request or {}).get("_release_snapshot") or {})
    raw = release.get("flow_spec")
    if not isinstance(raw, dict) or not raw.get("steps"):
        return None
    from dano.execution.page.flow_spec import FlowSpec

    try:
        return FlowSpec.model_validate(raw)
    except Exception as exc:  # noqa: BLE001 - published contract must fail closed
        raise ValueError(
            f"{skill.skill_id} has an invalid published FlowSpec contract: {exc}"
        ) from exc


def _skill_plan_payload(skill) -> dict[str, Any]:  # noqa: ANN001
    meta = dict(getattr(skill, "call_metadata", None) or {})
    if isinstance(meta.get("skill_plan"), dict):
        return dict(meta["skill_plan"])
    api = dict(getattr(skill, "api_request", None) or {})
    if isinstance(api.get("_skill_plan"), dict):
        return dict(api["_skill_plan"])
    release = dict(api.get("_release_snapshot") or {})
    if isinstance(release.get("skill_plan"), dict):
        return dict(release["skill_plan"])
    return {}


def _contract_planning_fields(skill) -> dict[str, Any]:  # noqa: ANN001
    plan = _skill_plan_payload(skill)
    if not plan:
        return {}
    bindings = [
        dict(binding)
        for route in (plan.get("routes") or [])
        if isinstance(route, dict)
        for binding in (route.get("bindings") or [])
        if isinstance(binding, dict)
    ]
    return {
        "planning_mode": plan.get("planning_mode") or "",
        "selected_capability_ids": list(plan.get("selected_capability_ids") or []),
        "routes": list(plan.get("routes") or []),
        "bindings": bindings,
        "unused_capabilities": list(plan.get("unused_capabilities") or []),
        "source_flow_fingerprint": plan.get("source_flow_fingerprint") or "",
        "composition_summary": plan.get("composition_summary") or "",
        "composition_notes": list(plan.get("composition_notes") or []),
        "intent_branches": list(plan.get("intent_branches") or []),
    }


def _plan_keys(plan: dict) -> set[str]:
    return {
        str(plan.get("name") or ""),
        str(plan.get("capability_id") or ""),
    } - {""}


def _filter_plans_for_export(plans: list[dict], skill) -> list[dict]:  # noqa: ANN001
    payload = _skill_plan_payload(skill)
    selected = [str(item) for item in (payload.get("selected_capability_ids") or []) if str(item)]
    if not selected:
        return plans
    selected_set = set(selected)
    kept = [plan for plan in plans if _plan_keys(plan) & selected_set]
    if not kept:
        raise ValueError(f"{skill.skill_id} skill plan selected capabilities are not in the compiled contract")
    return kept


def _compiled_request(skill, spec) -> dict:  # noqa: ANN001
    del spec
    published = dict(skill.api_request or {})
    capabilities = [
        item for item in (published.get("capabilities") or [])
        if isinstance(item, dict)
    ]
    if not capabilities or any(
        not isinstance(item.get("execution_contract"), dict)
        or not isinstance(item["execution_contract"].get("steps"), list)
        or not item["execution_contract"]["steps"]
        for item in capabilities
    ):
        raise ValueError(f"{skill.skill_id} has no canonical published capability contract")
    return published


def _steps(api_request: dict) -> list[dict]:
    if isinstance(api_request.get("steps"), list):
        return [dict(step) for step in api_request["steps"] if isinstance(step, dict)]
    if api_request.get("method"):
        return [dict(api_request)]
    return []


def _capabilities(skill, spec, api_request: dict) -> list[dict]:  # noqa: ANN001
    del skill, spec
    raw = list(api_request.get("capabilities") or [])
    out = [dict(cap) for cap in raw if isinstance(cap, dict)]
    return out


def _base_url(steps: list[dict]) -> str:
    for step in steps:
        parsed = urlparse(str(step.get("url") or ""))
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    return ""


def _safe_step(step: dict) -> dict:
    keep = {
        "step_id", "step_name", "method", "url", "url_template", "path",
        "content_type", "body_template", "query_template", "params", "success_rule",
        "field_types", "wire_formats", "runtime_fields",
        "selects", "system_values", "fact_check",
    }
    projected = {key: step.get(key) for key in keep if step.get(key) is not None}
    projected["selects"] = [
        {
            key: item.get(key)
            for key in (
                "param", "path", "option_map", "multi", "element_template",
                "field_projections", "source_url", "source_method", "source_body",
                "source_content_type", "value_key", "label_key", "category_key",
                "category_value", "id_path",
            )
            if item.get(key) is not None
        }
        for item in step.get("selects") or [] if isinstance(item, dict)
    ]
    return _scrub(projected)


_OBJECT_PREFIXES = (
    "搜索/筛选", "搜索", "筛选", "查询", "查看", "新增", "新建", "修改", "编辑",
    "审批", "审核", "反审", "反审核", "删除", "提交", "办理",
)


def _is_recording_copy(value: Any) -> bool:
    return handbook_text_is_banned(value)


def _norm_name_token(token: str) -> str:
    item = str(token or "").strip("-")
    if item.endswith("s") and len(item) > 3 and not item.endswith("ss"):
        return item[:-1]
    return item


def _skill_frontmatter_name(skill, plans: list[dict]) -> str:  # noqa: ANN001
    slugs = [_slug(str(plan.get("name") or "")) for plan in plans if str(plan.get("name") or "")]
    token_sets = [{_norm_name_token(part) for part in item.split("-") if part} for item in slugs]
    shared: set[str] = set.intersection(*token_sets) if token_sets else set()
    shared.discard("")
    ordered = [
        _norm_name_token(part)
        for part in (slugs[0].split("-") if slugs else [])
        if _norm_name_token(part) in shared
    ]
    ordered = list(dict.fromkeys(ordered))
    if len(ordered) >= 2:
        return _slug("-".join(ordered) + "-operations")[:64]
    if len(shared) == 1:
        return _slug(next(iter(shared)) + "-operations")[:64]
    action = _slug(str(getattr(skill, "action", "") or ""))
    if action and action not in {"skill", "action"}:
        return action[:64]
    return (_slug(slugs[0]) if slugs else "page-operations")[:64]


def _verified_links(spec, step_ids: list[str]) -> list[dict]:  # noqa: ANN001
    if spec is None:
        return []
    from dano.execution.page.flow_spec import executable_flow_links

    allowed = set(step_ids)
    positions = {step_id: index for index, step_id in enumerate(step_ids)}
    links: list[dict] = []
    for link in executable_flow_links(spec):
        verification_id = str((link.meta or {}).get("verification_id") or "")
        if (
            link.source_step_id not in allowed
            or link.target_step_id not in allowed
            or positions[link.source_step_id] >= positions[link.target_step_id]
        ):
            continue
        link_kind = str(link.kind or "value")
        links.append({
            "link_id": link.link_id,
            "kind": link_kind,
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


def consume_upstream_input_schema(compiled: Any, upstream: Any) -> dict[str, Any]:
    """Copy capability schema facts. Restore dropped fields; never invent route fields."""
    packed = dict(compiled) if isinstance(compiled, dict) else {}
    fact = dict(upstream) if isinstance(upstream, dict) else {}
    packed_props = packed.get("properties") if isinstance(packed.get("properties"), dict) else {}
    fact_props = fact.get("properties") if isinstance(fact.get("properties"), dict) else {}
    if not fact_props and not packed_props:
        return {"type": "object", "properties": {}, "required": []}
    if fact_props:
        properties = {}
        for name, field in fact_props.items():
            packed_field = packed_props.get(name)
            if isinstance(field, dict) and isinstance(packed_field, dict):
                properties[str(name)] = {**packed_field, **field}
            else:
                properties[str(name)] = field
    else:
        properties = dict(packed_props)
    required: list[str] = []
    for field in [*(fact.get("required") or []), *(packed.get("required") or [])]:
        name = str(field)
        if name in properties and name not in required:
            required.append(name)
    schema: dict[str, Any] = {"type": "object", "properties": dict(properties), "required": required}
    for source in (fact, packed):
        for key, value in source.items():
            if key in {"type", "properties", "required"}:
                continue
            schema[key] = value
    return schema


def _upstream_capability_schema(spec, skill, cap: dict) -> dict[str, Any]:  # noqa: ANN001
    keys = {str(cap.get("capability_id") or ""), str(cap.get("name") or "")} - {""}
    if spec is not None:
        for item in spec.capabilities or []:
            if str(item.capability_id or "") in keys or str(item.name or "") in keys:
                schema = getattr(item, "input_schema", None)
                if isinstance(schema, dict) and (schema.get("properties") or schema.get("required")):
                    return dict(schema)
    for item in getattr(skill, "capabilities", None) or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("capability_id") or "") in keys or str(item.get("name") or "") in keys:
            schema = item.get("input_schema") or item.get("parameters")
            if isinstance(schema, dict) and (schema.get("properties") or schema.get("required")):
                return dict(schema)
    return {}


def restore_compiled_capability_schemas(api_request: dict, spec) -> dict:  # noqa: ANN001
    """Put FlowSpec capability schemas back onto compiled capabilities. Do not invent fields."""
    raw = dict(api_request or {})
    capabilities = [dict(item) for item in (raw.get("capabilities") or []) if isinstance(item, dict)]
    if spec is None:
        raw["capabilities"] = capabilities
        return raw
    restored: list[dict] = []
    for cap in capabilities:
        fact = _upstream_capability_schema(spec, None, cap)
        compiled = cap.get("input_schema") or cap.get("parameters") or {}
        cap["input_schema"] = consume_upstream_input_schema(compiled, fact)
        restored.append(cap)
    raw["capabilities"] = restored
    return raw


def _capability_plans(skill, spec, api_request: dict) -> list[dict]:  # noqa: ANN001
    plans: list[dict] = []
    used_scripts: set[str] = set()
    for index, cap in enumerate(_capabilities(skill, spec, api_request), 1):
        execution = dict(cap.get("execution_contract") or {})
        owned_steps = [
            dict(step) for step in (execution.get("steps") or [])
            if isinstance(step, dict)
        ]
        capability_owned = bool(owned_steps)
        all_steps = owned_steps if capability_owned else _steps(api_request)
        by_id = {
            str(step.get("step_id") or f"step-{step_index}"): step
            for step_index, step in enumerate(all_steps)
        }
        name = str(cap.get("name") or cap.get("capability_id") or f"capability_{index}")
        script = _script_slug(name)
        if script in used_scripts:
            script += "_" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:6]
        used_scripts.add(script)
        schema = consume_upstream_input_schema(
            cap.get("input_schema") or cap.get("parameters") or {},
            {} if capability_owned else _upstream_capability_schema(spec, skill, cap),
        )
        step_ids = (
            list(by_id)
            if capability_owned
            else _capability_call_step_ids(cap, by_id)
        )
        if not step_ids:
            raise ValueError(
                f"capability {name!r} does not reference any compiled request step"
            )
        links = (
            [dict(item) for item in (execution.get("links") or []) if isinstance(item, dict)]
            if capability_owned
            else _verified_links(spec, step_ids)
        )
        cap_steps = [
            _project_capability_step(
                _safe_step(by_id[step_id]),
                schema=schema,
                cap=cap,
                links=links,
                step_index=step_index,
            )
            for step_index, step_id in enumerate(step_ids)
        ]
        is_write = any(
            str(step.get("method") or "GET").upper() not in {"GET", "HEAD"}
            for step in cap_steps
        )
        fact_checks = []
        trusted_ids = (
            {str(item) for item in (execution.get("verification_ids") or []) if str(item)}
            if capability_owned
            else (flow_spec_verification_ids(spec) if spec is not None else set())
        )
        for step in cap_steps:
            fact_check = step.get("fact_check")
            if (
                isinstance(fact_check, dict)
                and fact_check.get("verified") is True
                and str(fact_check.get("verification_id") or "") in trusted_ids
            ):
                fact_checks.append({"step_id": step.get("step_id"), **_scrub(fact_check)})
        plans.append({
            "name": name,
            "capability_id": str(cap.get("capability_id") or ""),
            "title": str(cap.get("title") or name),
            "kind": str(cap.get("kind") or "operation"),
            "script": script,
            "input_schema": schema,
            "output_schema": dict(cap.get("output_schema") or {"type": "object"}),
            "preconditions": list(cap.get("preconditions") or []),
            "caller_responsibilities": list(cap.get("caller_responsibilities") or []),
            "skill_responsibilities": list(cap.get("skill_responsibilities") or []),
            "steps": cap_steps,
            "links": links,
            "fact_checks": fact_checks,
            "requires_confirmation": (
                bool(cap.get("requires_human_confirm"))
                if capability_owned
                else _capability_confirm_flag(cap, spec)
            ),
            "requires_verify": is_write,
            "execution_contract": execution,
            "contract": _scrub(cap),
        })
    return plans


def _evidence_for_plan(plan: dict, spec) -> list[str]:  # noqa: ANN001
    ids = [str(link.get("verification_id") or "") for link in plan["links"]]
    ids.extend(
        str(item.get("verification_id") or "")
        for item in plan["fact_checks"]
        if item.get("verified") is True
    )
    return list(dict.fromkeys(value for value in ids if value))


_LONG_TEXT_RE = re.compile(
    r"(?:reason|remark|description|content|comment|note|memo|原因|理由|说明|描述|备注|内容)",
    re.I,
)

def _object_from_title(title: str) -> str:
    text = _safe_text(title)
    for prefix in _OBJECT_PREFIXES:
        if text.startswith(prefix):
            rest = text[len(prefix):].strip(" /")
            return rest or text
    return text


def _page_object_heading(skill, plans: list[dict]) -> str:  # noqa: ANN001
    title = _safe_text(getattr(skill, "title", ""))
    if title and not _is_recording_copy(title) and "能力录制" not in title and "等" not in title:
        return title
    first = _safe_text((plans[0].get("title") or plans[0].get("name")) if plans else "")
    return _object_from_title(first) or first or "本页业务"


def _action_labels(plans: list[dict]) -> list[str]:
    labels: list[str] = []
    for item in plans:
        title = _safe_text(item.get("title") or item.get("name"))
        if not title:
            continue
        stripped = title
        for prefix in _OBJECT_PREFIXES:
            if title.startswith(prefix):
                stripped = prefix
                break
        if stripped not in labels:
            labels.append(stripped)
    return labels


def _business_identity(skill, plans: list[dict], spec) -> tuple[str, str]:  # noqa: ANN001
    del spec
    heading = _page_object_heading(skill, plans)
    actions = "、".join(_action_labels(plans)) or "已打包操作"
    description = (
        f"办理本页{heading}：{actions}。"
        "只要查询时不要写入。要改或审批但用户没指定哪一条时，先查再问。"
        "不要用于其它业务对象或未列出的动作。"
    )
    return heading, description


def _clip_description(text: str, limit: int = 1024) -> str:
    value = _safe_text(text)
    if len(value) <= limit:
        return value
    keep = "。不要用于其他业务对象或未列出的动作。"
    body = value
    if "不要用于" in value:
        body, _sep, tail = value.partition("不要用于")
        keep = "。" + _sep + tail
        body = body.rstrip("。")
    available = max(32, limit - len(keep) - 1)
    return body[:available].rstrip("。，, ") + keep


def _skill_description(skill, plans: list[dict], spec) -> tuple[str, str]:  # noqa: ANN001
    heading, generated = _business_identity(skill, plans, spec)
    plan = _skill_plan_payload(skill)
    what = _safe_text(plan.get("composition_summary") or plan.get("summary"))
    if not what or _is_recording_copy(what):
        what = generated.rsplit("不要用于", 1)[0] if "不要用于" in generated else generated
        if not what or _is_recording_copy(what):
            what = f"办理本页{heading}：{'、'.join(_action_labels(plans)) or '已打包操作'}"
    text = what.rstrip("。")
    if "不要写入" not in text and "不要改" not in text and "不要" not in text:
        text = f"{text}。只要查询时不要写入。要改或审批但用户没指定哪一条时，先查再问"
    if "不要用于" not in text and "不用于" not in text and "Do not" not in text:
        text = f"{text}。不要用于其它业务对象或未列出的动作"
    if not text.endswith("。"):
        text += "。"
    return heading, _clip_description(text)


def _schema_field_names(plan: dict) -> set[str] | None:
    schema = plan.get("input_schema")
    if not isinstance(schema, dict):
        return None
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    return {
        str(name)
        for name, raw in properties.items()
        if isinstance(raw, dict) and _is_caller_field(raw)
    }


def _route_schema_fields(route: dict, plans: list[dict]) -> set[str] | None:
    by_ref = _plan_by_ref(plans)
    known = False
    fields: set[str] = set()
    for cap_id in route.get("capability_sequence") or []:
        names = _schema_field_names(by_ref.get(str(cap_id)) or {})
        if names is None:
            continue
        known = True
        fields.update(names)
    return fields if known else None


def _field_label(name: str, field: dict) -> str:
    return _safe_text(
        field.get("title") or field.get("label") or field.get("description") or name
    )


def _option_source(field: dict) -> dict | None:
    source = field.get("x-dano-option-source") or field.get("x-options-source-meta")
    if not isinstance(source, dict):
        return None
    endpoint = _safe_text(
        source.get("endpoint") or source.get("source_url") or source.get("url")
    )
    result_path = source.get("resultPath") or source.get("result_path")
    id_field = source.get("idField") or source.get("value_key") or "id"
    label_field = source.get("labelField") or source.get("label_key") or source.get("label_path")
    if not all((endpoint, id_field, label_field)):
        return None
    data_source: dict[str, Any] = {
        "type": "api",
        "endpoint": endpoint,
        "method": str(source.get("method") or source.get("source_method") or "GET").upper(),
        "idField": id_field,
        "labelField": label_field,
    }
    if result_path:
        data_source["resultPath"] = result_path
    params = source.get("params") or source.get("source_params") or source.get("source_body")
    if isinstance(params, dict) and params:
        data_source["params"] = params
    children = source.get("childrenField") or source.get("children_key")
    if children:
        data_source["childrenField"] = children
    return data_source


def _field_options(field: dict) -> list[dict]:
    raw_options = (
        field.get("x-enum-options")
        or field.get("x-options")
        or field.get("x-options-snapshot")
    )
    if isinstance(raw_options, list):
        options: list[dict] = []
        for raw in raw_options:
            if isinstance(raw, dict):
                value = raw.get("id", raw.get("value"))
                label = raw.get("label", raw.get("name", value))
            else:
                value = raw
                label = raw
            if value is not None:
                options.append({"id": value, "label": str(label)})
        if options:
            return options
    values = list(field.get("enum") or [])
    labels = dict(field.get("x-enum-value-map") or {})
    options: list[dict] = []
    if labels:
        for label, value in labels.items():
            options.append({"id": value, "label": str(label)})
    else:
        options.extend({"id": value, "label": str(value)} for value in values)
    return options


def _is_caller_field(field: dict) -> bool:
    """Project only fields explicitly exposed by the capability contract."""
    return not (
        field.get("x-dano-derived-from-query") is True
        or field.get("x-dano-internal") is True
        or field.get("x-dano-display") is False
        or field.get("x-dano-visibility") == "internal"
    )


_PLACEHOLDER_RE = re.compile(r"^\{\{.+\}\}$")
_ARRAY_FIELD_PATH_RE = re.compile(r"^([^.\[]+)\[(?:\d+|\*|)?\]\.(.+)$")
_EXECUTE_REF_USAGES = frozenset({"preflight", "execute"})


def _is_placeholder(value: Any) -> bool:
    return isinstance(value, str) and bool(_PLACEHOLDER_RE.match(value.strip()))


def _iter_schema_fields(schema: dict | None, prefix: str = "") -> list[tuple[str, str, dict]]:
    raw = schema if isinstance(schema, dict) else {}
    rows: list[tuple[str, str, dict]] = []
    if raw.get("type") == "array" and isinstance(raw.get("items"), dict):
        rows.extend(_iter_schema_fields(raw.get("items"), f"{prefix}[]" if prefix else "[]"))
        return rows
    properties = raw.get("properties") if isinstance(raw.get("properties"), dict) else {}
    for name, field in properties.items():
        if not isinstance(field, dict) or not _is_caller_field(field):
            continue
        if prefix.endswith("[]"):
            path = f"{prefix}.{name}"
        elif prefix:
            path = f"{prefix}.{name}"
        else:
            path = str(name)
        rows.append((path, str(name), field))
        if field.get("type") == "object":
            rows.extend(_iter_schema_fields(field, path))
        elif field.get("type") == "array" and isinstance(field.get("items"), dict):
            rows.extend(_iter_schema_fields(field.get("items"), f"{path}[]"))
    return rows


def _option_binding(path: str, name: str, field: dict) -> dict | None:
    source = field.get("x-dano-option-source") or field.get("x-options-source-meta")
    if not isinstance(source, dict):
        return None
    url = _safe_text(source.get("source_url") or source.get("endpoint") or source.get("url"))
    if not url:
        return None
    value_key = source.get("value_key") or source.get("idField") or "id"
    label_key = source.get("label_key") or source.get("labelField") or source.get("label_path")
    if not label_key:
        return None
    id_path = str(source.get("id_path") or source.get("schema_identity_path") or path)
    binding: dict[str, Any] = {
        "param": name if "." not in path and "[" not in path else name,
        "path": id_path or path,
        "source_url": url,
        "source_method": str(source.get("source_method") or source.get("method") or "GET").upper(),
        "value_key": value_key,
        "label_key": label_key,
        "id_path": id_path or path,
    }
    option_map = field.get("x-enum-value-map")
    if isinstance(option_map, dict) and option_map:
        binding["option_map"] = option_map
    if source.get("source_body") is not None:
        binding["source_body"] = source.get("source_body")
    if source.get("source_content_type"):
        binding["source_content_type"] = source.get("source_content_type")
    if source.get("category_key"):
        binding["category_key"] = source.get("category_key")
        if source.get("category_value") is not None:
            binding["category_value"] = source.get("category_value")
    return binding


def _select_key(item: dict) -> tuple[str, str]:
    return (
        str(item.get("path") or item.get("id_path") or item.get("param") or ""),
        str(item.get("source_url") or ""),
    )


def _source_path(url: Any) -> str:
    raw = str(url or "").split("?", 1)[0]
    if raw.startswith("http"):
        return urlparse(raw).path or raw
    return raw


def _merge_schema_selects(existing: list[dict], schema: dict | None, cap: dict | None = None) -> list[dict]:
    merged: list[dict] = []
    seen_urls: set[str] = set()
    seen: set[tuple[str, str]] = set()
    for item in existing or []:
        if not isinstance(item, dict):
            continue
        key = _select_key(item)
        if key in seen:
            continue
        seen.add(key)
        if item.get("source_url"):
            seen_urls.add(_source_path(item.get("source_url")))
        merged.append(item)
    for path, name, field in _iter_schema_fields(schema):
        binding = _option_binding(path, name, field)
        if not binding:
            continue
        key = _select_key(binding)
        alt = (str(name), str(binding.get("source_url") or ""))
        if key in seen or alt in seen:
            continue
        seen.add(key)
        if binding.get("source_url"):
            seen_urls.add(_source_path(binding.get("source_url")))
        merged.append(binding)
    for ref in (cap or {}).get("request_refs") or []:
        usage, _step_id = _ref_usage_and_step(ref)
        if usage != "option_source":
            continue
        raw = ref if isinstance(ref, dict) else {}
        path = str(raw.get("path") or getattr(ref, "path", "") or "")
        method = str(raw.get("method") or getattr(ref, "method", "") or "GET").upper()
        if not path:
            continue
        url = path
        if _source_path(url) in seen_urls:
            continue
        seen_urls.add(_source_path(url))
        merged.append({
            "param": "",
            "path": path,
            "source_url": url,
            "source_method": method or "GET",
        })
    return merged


def _step_uses_option(step: dict, name: str, path: str) -> bool:
    params = {str(item) for item in (step.get("params") or [])}
    if name in params or path in params:
        return True
    container = str(path).split("[", 1)[0].split(".", 1)[0]
    if container and container in params:
        return True
    blob = json.dumps(
        {"body": step.get("body_template"), "query": step.get("query_template")},
        ensure_ascii=False,
        default=str,
    )
    return f"{{{{{name}}}}}" in blob or f"{{{{{container}}}}}" in blob or f'"{name}"' in blob


def _ref_usage_and_step(ref: Any) -> tuple[str, str]:
    if isinstance(ref, dict):
        return str(ref.get("usage") or ""), str(ref.get("step_id") or "")
    return str(getattr(ref, "usage", "") or ""), str(getattr(ref, "step_id", "") or "")


def _capability_call_step_ids(cap: dict, by_id: dict[str, dict]) -> list[str]:
    ordered: list[str] = []
    for value in cap.get("compiled_step_ids") or cap.get("step_ids") or []:
        step_id = str(value)
        if step_id in by_id and step_id not in ordered:
            ordered.append(step_id)
    preflight: list[str] = []
    execute: list[str] = []
    for ref in cap.get("request_refs") or []:
        usage, step_id = _ref_usage_and_step(ref)
        if usage not in _EXECUTE_REF_USAGES or step_id not in by_id:
            continue
        if usage == "preflight":
            preflight.append(step_id)
        else:
            execute.append(step_id)
    if not ordered:
        return list(dict.fromkeys([*preflight, *execute]))
    for step_id in reversed(preflight):
        if step_id not in ordered:
            ordered.insert(0, step_id)
    for step_id in execute:
        if step_id not in ordered:
            ordered.append(step_id)
    return ordered


def _capability_confirm_flag(cap: dict, spec) -> bool:  # noqa: ANN001
    keys = {str(cap.get("capability_id") or ""), str(cap.get("name") or "")} - {""}
    if spec is not None:
        for item in getattr(spec, "capabilities", None) or []:
            if str(getattr(item, "capability_id", "") or "") in keys or str(getattr(item, "name", "") or "") in keys:
                return bool(getattr(item, "requires_human_confirm", False))
    if "requires_human_confirm" in cap:
        return bool(cap.get("requires_human_confirm"))
    return False


def _path_covers(key: str, paths: set[str]) -> bool:
    for path in paths:
        raw = str(path or "").removeprefix("body.").removeprefix("query.")
        if raw == key or raw.startswith(f"{key}.") or raw.startswith(f"{key}["):
            return True
    return False


def _caller_name_for_key(key: str, fields: list[tuple[str, str, dict]]) -> str:
    for path, name, field in fields:
        flow = str(field.get("x-flow-path") or "")
        if path == key or flow == key:
            return name
        if name == key and "[" not in path and "." not in path:
            return name
        if flow in {f"query.{key}", f"path.{key}", f"body.{key}"}:
            return name
    return ""


def _sanitize_request_mapping(
    template: Any,
    *,
    kind: str,
    params: list[Any],
    fields: list[tuple[str, str, dict]],
    linked: set[str],
    system_paths: set[str],
    formula_paths: set[str],
) -> Any:
    if not isinstance(template, dict):
        return template
    params_set = {str(item) for item in params if str(item)}
    out: dict[str, Any] = {}
    for key, value in template.items():
        name = str(key)
        if _is_placeholder(value):
            out[name] = value
            continue
        caller = _caller_name_for_key(name, fields)
        if caller:
            out[name] = "{{" + caller + "}}"
            continue
        if name in params_set:
            out[name] = "{{" + name + "}}"
            continue
        if _path_covers(name, linked) or _path_covers(name, system_paths) or _path_covers(name, formula_paths):
            continue
        if kind == "query":
            out[name] = value
    return out


def _strip_recorded_query(url: Any) -> Any:
    if not isinstance(url, str) or "?" not in url:
        return url
    return url.split("?", 1)[0]


def _project_capability_step(
    step: dict,
    *,
    schema: dict,
    cap: dict,
    links: list[dict],
    step_index: int,
) -> dict:
    projected = dict(step)
    fields = _iter_schema_fields(schema)
    linked = {
        str(item.get("target_path") or "")
        for item in links
        if int(item.get("target_step", -1)) == step_index
    }
    system_paths = {
        str(item.get("path") or "")
        for item in (projected.get("system_values") or [])
        if isinstance(item, dict)
    }
    formula_paths = set()
    for item in projected.get("runtime_fields") or []:
        if not isinstance(item, dict):
            continue
        for key in ("path", "result_field", "array_item_key", "schema_identity_path"):
            if item.get(key):
                formula_paths.add(str(item.get(key)))
    params = list(projected.get("params") or [])
    if projected.get("body_template") is not None:
        projected["body_template"] = _sanitize_request_mapping(
            projected.get("body_template"),
            kind="body",
            params=params,
            fields=fields,
            linked=linked,
            system_paths=system_paths,
            formula_paths=formula_paths,
        )
    if projected.get("query_template") is not None:
        projected["query_template"] = _sanitize_request_mapping(
            projected.get("query_template"),
            kind="query",
            params=params,
            fields=fields,
            linked=linked,
            system_paths=system_paths,
            formula_paths=formula_paths,
        )
        for key in ("path", "url", "url_template"):
            if projected.get(key):
                projected[key] = _strip_recorded_query(projected.get(key))
    selects = _merge_schema_selects(list(projected.get("selects") or []), schema, cap)
    execute_step = (
        str(projected.get("method") or "GET").upper() not in {"GET", "HEAD"}
        or projected.get("body_template") is not None
    )
    projected["selects"] = [
        item for item in selects
        if _step_uses_option(projected, str(item.get("param") or ""), str(item.get("path") or item.get("id_path") or ""))
        or (execute_step and not item.get("param") and item.get("source_url"))
    ]
    return projected


def _field_control(name: str, field: dict) -> str:
    configured = str(
        field.get("x-dano-control") or field.get("x-ui-control") or field.get("inputType") or ""
    ).strip()
    has_choices = bool(_option_source(field) or _field_options(field))
    if has_choices:
        if configured in {"radio", "checkbox", "select", "treeSelect"}:
            return configured
        return "treeSelect" if field.get("x-dano-tree") else "select"
    if configured in {"text", "textarea", "date", "radio", "checkbox", "select", "treeSelect"}:
        return configured
    if field.get("format") in {"date", "date-time"}:
        return "date"
    if field.get("type") == "boolean":
        return "radio"
    if field.get("type") in {"array", "object"}:
        return "textarea"
    semantic = " ".join((name, _field_label(name, field), _safe_text(field.get("description"))))
    if _LONG_TEXT_RE.search(semantic) or int(field.get("maxLength") or 0) > 200:
        return "textarea"
    return "text"


def _runtime_default(name: str, field: dict, control: str) -> str:
    label = _field_label(name, field)
    if control in {"select", "treeSelect", "radio", "checkbox"}:
        guidance = f"按当前用户语义从候选项选择“{label}”的稳定 id"
    elif control == "date":
        guidance = f"根据当前业务意图生成“{label}”，并符合 dateFormat"
    elif field.get("type") in {"array", "object"}:
        guidance = f"根据当前用户意图生成符合 schema 的 JSON {field.get('type')}"
    elif field.get("type") in {"number", "integer"}:
        guidance = f"从当前用户语义提取“{label}”数值，不得任意使用 0"
    else:
        guidance = f"根据当前用户意图生成可编辑的“{label}”推荐值"
    return f"<调用前必须替换：{guidance}；禁止使用历史样本值>"


def _question_spec(name: str, field: dict, *, required: bool) -> dict:
    control = _field_control(name, field)
    question: dict[str, Any] = {
        "id": name,
        "question": _field_label(name, field),
        "inputType": control,
        "required": required,
        "default": _runtime_default(name, field, control),
    }
    data_source = _option_source(field)
    options = _field_options(field)
    if data_source:
        question["dataSource"] = data_source
    elif options:
        question["options"] = options
    elif control == "radio" and field.get("type") == "boolean":
        question["options"] = [
            {"id": "true", "label": "是"},
            {"id": "false", "label": "否"},
        ]
    if control in {"select", "treeSelect"}:
        question["multiple"] = bool(field.get("type") == "array" or field.get("multiple"))
    if control == "date":
        question["dateFormat"] = str(
            field.get("dateFormat")
            or ("yyyy-MM-dd HH:mm" if field.get("format") == "date-time" else "yyyy-MM-dd")
        )
    return question


def _with_toc_if_long(text: str, headings: list[str]) -> str:
    lines = text.splitlines()
    if len(lines) < 100 or not headings:
        return text
    toc = ["## 目录", ""]
    toc.extend(f"- [{title}](#{re.sub(r'\\s+', '-', title).strip('-') or 'section'})" for title in headings)
    toc.append("")
    body = text
    marker = "## Global rules" if "## Global rules" in text else (f"## {headings[0]}" if headings else "")
    if marker and marker in body:
        prefix, rest = body.split(marker, 1)
        return prefix + "\n".join(toc) + marker + rest
    return "\n".join(toc) + "\n" + text


def _capability_form_section(plan: dict) -> list[str]:
    schema = plan.get("input_schema") if isinstance(plan.get("input_schema"), dict) else {}
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = set(schema.get("required") or [])
    caller_properties = {
        str(name): raw
        for name, raw in properties.items()
        if isinstance(raw, dict) and _is_caller_field(raw)
    }
    title = _safe_text(plan.get("title") or plan.get("name"))
    lines = [
        f"## {title} (`{plan.get('name')}`)",
        "",
    ]
    questions = [
        _question_spec(str(name), raw if isinstance(raw, dict) else {}, required=name in required)
        for name, raw in caller_properties.items()
    ]
    if not questions:
        lines.extend(["该能力没有调用方字段，不调用 `ask_user_question`。", ""])
        return lines
    request = {"title": title, "questions": questions}
    lines.extend([
        "原生分组表单请求：",
        "",
        "```json",
        json.dumps(request, ensure_ascii=False, indent=2),
        "```",
        "",
        "| 字段 | Label | 控件 | JSON 类型 | 必填 | 默认值规则 | 选项来源 |",
        "|---|---|---|---|---|---|---|",
    ])
    for name, raw in caller_properties.items():
        field = raw if isinstance(raw, dict) else {}
        control = _field_control(str(name), field)
        source = _option_source(field)
        options = _field_options(field)
        if source or options:
            source_text = "见 `references/OPTIONS.md` 对应行"
        else:
            source_text = "自由输入"
        label_text = _field_label(str(name), field).replace("|", "\\|")
        default_text = _runtime_default(str(name), field, control).replace("|", "\\|")
        lines.append(
            f"| `{name}` | {label_text} | `{control}` | "
            f"`{field.get('type') or 'string'}` | {'是' if name in required else '否'} | "
            f"{default_text} | {source_text} |"
        )
    lines.extend([
        "",
        "回答处理顺序：按 question id 取值 → 语义与类型转换 → schema 校验 → 仅纠正无效字段 → 契约要求确认时单独确认 → 执行下一步。",
        "",
    ])
    return lines


def _input_forms_bundle(plans: list[dict]) -> tuple[str, dict[str, str]]:
    """Return INPUT_FORMS.md. Keep every capability form in this file."""
    header = [
        "# Native input forms",
        "",
        "本文件只投影能力契约中的调用方字段。当前步骤缺少字段时才阅读对应能力章节。每次需要向用户提问时，必须原生调用 `ask_user_question`；禁止在普通文本、Markdown、XML 或 `<question>` 标签中模拟工具调用。",
        "",
        "## Global rules",
        "",
        "- 同一能力的相关字段尽量合并在一次 `questions[]` 中；每个 `id` 与 `input_schema.properties` 的键逐字一致。",
        "- 下列 `default` 是生成规则占位符，调用前必须替换为结合当前用户意图、当前时间和实时候选生成的非空推荐值；不得把占位符本身传给工具，也不得使用历史样本值。",
        "- 用户回答后，先按 schema 的 `type`、`format`、`enum`、`pattern` 和边界转换为接口线格式。可无歧义转换时自动转换（例如数字文本转 number、日期语义转声明格式、候选 label 转稳定 id）。",
        "- 无法无歧义转换或语义不合法时，只对错误字段发起一次**单字段纠错**表单，说明期望格式并给出新的运行时推荐默认值；不要重问已经有效的字段。",
        "- 能力契约要求执行前确认时，整理完参数后另起一次调用 `ask_user_question({\"confirm\": true, \"formIds\": [\"<answered.formId>\"]})`。确认调用不得带 `title`、`questions`、`options` 或 `multiple`。",
        "- 固定值、系统值和上一步已确认绑定值不重复询问。",
        "",
    ]
    body: list[str] = []
    headings: list[str] = []
    for plan in plans:
        title = _safe_text(plan.get("title") or plan.get("name"))
        if title:
            headings.append(title)
        body.extend(_capability_form_section(plan))
    text = _with_toc_if_long("\n".join(header + body).rstrip() + "\n", headings)
    return text, {}


def _input_forms_md(plans: list[dict]) -> str:
    """Render executable ask_user_question contracts from caller-facing schemas."""
    text, _extras = _input_forms_bundle(plans)
    return text



def _required_fields(plan: dict) -> list[str]:
    schema = plan.get("input_schema") if isinstance(plan.get("input_schema"), dict) else {}
    return [str(name) for name in (schema.get("required") or []) if str(name)]


def _script_for(plans: list[dict], cap_id: str) -> str:
    item = _plan_by_ref(plans).get(str(cap_id or ""))
    if item:
        return f"python scripts/{item['script']}.py"
    return ""


def _cross_step_label(route: dict) -> str:
    bindings = [item for item in (route.get("bindings") or []) if isinstance(item, dict) and item.get("from_output")]
    if bindings:
        return "；".join(
            f"{item.get('from_output')} → {item.get('to_input')}"
            for item in bindings
            if item.get("to_input")
        )
    if route.get("checkpoints") or any(
        isinstance(step, dict) and step.get("checkpoint")
        for step in (route.get("steps") or [])
    ):
        return "人工交接，不自动带入"
    if len(route.get("capability_sequence") or []) > 1:
        return "各步输入独立，不猜跨步字段"
    return "不涉及"


def _ask_when_label(route: dict, plans: list[dict] | None = None) -> str:
    checks = [item for item in (route.get("checkpoints") or []) if isinstance(item, dict)]
    if checks:
        return "；".join(str(item.get("prompt") or "上一步完成后请用户选定目标") for item in checks)
    allowed = _route_schema_fields(route, plans or [])
    fields = [
        str(name)
        for name in (route.get("required_user_inputs") or [])
        if str(name) and (allowed is None or str(name) in allowed)
    ]
    if fields:
        return "只补当前步骤缺少的输入：" + "、".join(f"`{name}`" for name in fields)
    if route.get("required_user_inputs") and allowed is not None:
        return "按已有契约收集当前步骤缺少的输入"
    return "输入已齐则不问"


def _confirm_label(route: dict, plans: list[dict]) -> str:
    writes = []
    for cap_id in route.get("capability_sequence") or []:
        item = _plan_by_ref(plans).get(str(cap_id))
        if item and item.get("requires_confirmation"):
            writes.append(_safe_text(item.get("title") or item.get("name")))
    if writes:
        return "、".join(writes) + " 执行前确认"
    return "无"


def _route_detail_link(route: dict) -> str:
    sequence = [str(item) for item in (route.get("capability_sequence") or []) if str(item)]
    if len(sequence) <= 1:
        return "原子操作，不必读取组合路线"
    route_id = _safe_text(route.get("route_id") or "route")
    return f"[`references/routes/{route_id}.md`](references/routes/{route_id}.md)"


def _all_routes(skill) -> list[dict]:  # noqa: ANN001
    plan = _skill_plan_payload(skill)
    return [item for item in (plan.get("routes") or []) if isinstance(item, dict) and item.get("capability_sequence")]


def _workflow_table(skill, plans: list[dict]) -> list[str]:  # noqa: ANN001
    lines = [
        "## 选择工作流",
        "",
        "根据用户原话只选下表中的一行。一行就是一条路线，不要把多条路线合并成“依次调用相关脚本”。",
        "",
        "| 用户意图 | 路线 | 步骤顺序 | 跨步数据 | 何时问人 | 确认点 | 完成条件 | 详情 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for route in _all_routes(skill):
        titles = [
            _title_for_plan_ref(plans, str(cap_id)) or str(cap_id)
            for cap_id in (route.get("capability_sequence") or [])
        ]
        lines.append(
            f"| {_safe_text(route.get('when_to_use') or route.get('name'))} | "
            f"{_safe_text(route.get('name') or route.get('route_id'))} | "
            f"{' → '.join(titles)} | {_cross_step_label(route)} | {_ask_when_label(route, plans)} | "
            f"{_confirm_label(route, plans)} | {_safe_text(route.get('done_when') or '按该行完成条件核对')} | "
            f"{_route_detail_link(route)} |"
        )
    if len(lines) == 6:
        for item in plans:
            lines.append(
                f"| {_intent_for_plan(skill, item)} | {_safe_text(item.get('title') or item.get('name'))} | "
                f"{_safe_text(item.get('title') or item.get('name'))} | 不涉及 | "
                f"{_operation_collect_hint(item)} | "
                f"{'写前确认' if item.get('requires_confirmation') else '无'} | "
                f"{'已返回业务结果' if not item.get('requires_confirmation') else '已确认并执行成功'} | "
                f"原子操作，不必读取组合路线 |"
            )
    return lines


def _composition_rules(skill) -> list[str]:  # noqa: ANN001
    plan = _skill_plan_payload(skill)
    notes = [
        _safe_text(item)
        for item in (plan.get("composition_notes") or [])
        if _safe_text(item) and not _is_recording_copy(item)
    ]
    lines = [
        "## 组合与交接规则",
        "",
        "1. **原子执行**：单一明确意图时只选一条已打包操作，只补齐该操作缺少的输入。",
        "2. **确认绑定串联**：只有合同列出的绑定可以自动带入；空值、多条但要求单条、类型或基数不符时立即停止自动串联并问人。",
        "3. **人工交接串联**：没有确认绑定时，先做前一步，再展示候选或请用户补充下一步必要输入；只有用户选择通过校验后才恢复路线。",
        "",
    ]
    summary = _safe_text(plan.get("composition_summary") or plan.get("summary"))
    extras = 0
    for item in notes:
        if item.startswith("组合路线「") or item.startswith("用户描述中的先后"):
            continue
        if item.startswith("组合约定：") and summary and item[5:].strip() == summary:
            continue
        lines.append(f"- {item}")
        extras += 1
    if extras:
        lines.append("")
    return lines


def _execution_protocol() -> list[str]:
    return [
        "## 执行协议",
        "",
        "1. 根据用户原话选择唯一工作流；无法唯一选择时先澄清。",
        "   Done when: 选出恰好一条路线，或已提出一个可回答的澄清问题。",
        "2. 按该行详情指针读取路线文件和当前步骤必要的输入表单。原子路线不要加载组合路线全文。",
        "   Done when: 已打开当前路线真正需要的那一个文件。",
        "3. 只收集当前路线当前步骤缺少的输入。固定值、系统值和已确认绑定不重复询问。",
        "   Done when: 当前步骤必填已齐，或用户取消。",
        "4. 按合同处理绑定或人工交接，不猜测跨步字段，不默认第一条候选。",
        "   Done when: 下一步输入已确认，或已停止并说明原因。",
        "5. 契约要求确认的操作先展示目标、关键字段和影响，获得确认后再执行对应脚本；未要求确认的操作收集齐输入后直接执行。",
        "   Done when: 已按契约确认或跳过确认，脚本返回成功，或用户拒绝后未执行。",
        "6. 按路线完成条件验证结果；失败或结果未知时停止，不得静默重试写入。",
        "   Done when: 已按完成条件判定成功、失败或未知。",
        "7. 只报告已确认完成的步骤、未执行步骤和需要用户处理的事项。",
        "   Done when: 汇报与实际执行一致。",
        "",
    ]


def _success_failure_section(skill) -> list[str]:  # noqa: ANN001
    plan = _skill_plan_payload(skill)
    lines = [
        "## 成功、失败与停止",
        "",
        "- 成功：当前路线的完成条件全部成立。",
        "- 失败：任一步脚本失败，立即停止并报告原因。",
        "- 未知写入结果：停止并请人处理，不得用同一载荷重试。",
        "- 用户取消、拒绝确认或候选选择无效：停止并报告未执行。",
        "- 候选为空或多条但要求单条：停问，不得默认第一条。",
        "",
    ]
    for item in plan.get("safety_rules") or []:
        text = _safe_text(item)
        if text and not _is_recording_copy(text):
            lines.append(f"- {text}")
    if lines[-1] != "":
        lines.append("")
    return lines


def _on_demand_resources(skill, plans: list[dict]) -> list[str]:  # noqa: ANN001
    lines = [
        "## 按需读取资源",
        "",
        "- 当前操作缺少必填字段时，读取 `references/INPUT_FORMS.md` 中对应能力的章节。",
        "- 字段需要动态候选，或候选为空/多条时，读取 `references/OPTIONS.md`。",
        "- 需要判断某个能力的输入输出边界时，读取 `references/CAPABILITIES.md`。",
        "- 不要在开始前读取 references 下的全部文件。",
        "- 组合路线只在工作流表「详情」列指向该文件时读取；不要为单次原子操作加载组合文件。",
    ]
    if any(item.get("requires_confirmation") or item.get("requires_verify") for item in plans):
        lines.extend([
            "",
            "## 鉴权",
            "",
            "- 运行期凭证只来自环境变量或本机会话缓存，不要把历史登录信息写进对话。",
        ])
    return lines


def _applicable_sections(skill, plans: list[dict]) -> list[str]:  # noqa: ANN001
    plan = _skill_plan_payload(skill)
    unused = [item for item in (plan.get("unused_capabilities") or []) if isinstance(item, dict)]
    identity = _safe_text(plan.get("composition_summary") or plan.get("summary"))
    lines = ["## 适用场景", ""]
    lines.append("- 用户原话能对应「选择工作流」中恰好一行时使用。")
    route_whens = {
        _safe_text(route.get("when_to_use"))
        for route in _all_routes(skill)
        if _safe_text(route.get("when_to_use"))
    }
    added = 0
    for item in plan.get("trigger_phrases") or []:
        example = _safe_text(item)
        if (
            not example
            or _is_recording_copy(example)
            or example == identity
            or example in "\n".join(lines)
        ):
            continue
        if example in route_whens and added >= 2:
            continue
        lines.append(f"- 例如：{example}")
        added += 1
        if added >= 3:
            break
    if added == 0:
        for when in route_whens:
            if not when or when == identity or when in "\n".join(lines):
                continue
            lines.append(f"- 例如：{when}")
            added += 1
            if added >= 2:
                break
    lines.extend(["", "## 不适用场景", ""])
    lines.append("- 不要用于其它业务对象或未列出的动作。")
    lines.append("- 只要查询或查看时，不得执行写入。")
    lines.append("- 没有已确认绑定却假装已经自动带入时停止，先查再问。")
    lines.append("- 不得编造字段、接口、输出或未确认关系。")
    for item in unused:
        title = _safe_text(item.get("title") or item.get("name"))
        reason = _safe_text(item.get("reason") or "当前业务描述未要求")
        if title:
            lines.append(f"- 不要执行「{title}」：{reason}。")
    lines.append("")
    return lines


def _combination_routes(skill) -> list[dict]:  # noqa: ANN001
    plan = _skill_plan_payload(skill)
    routes = [item for item in (plan.get("routes") or []) if isinstance(item, dict)]
    combinations: list[dict] = []
    for route in routes:
        sequence = [str(item) for item in (route.get("capability_sequence") or []) if str(item)]
        if len(sequence) > 1:
            combinations.append(route)
    return combinations


def _plan_by_ref(plans: list[dict]) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for item in plans:
        for key in (item.get("capability_id"), item.get("name"), item.get("title")):
            if key:
                index[str(key)] = item
    return index


def _recorded_order(plans: list[dict]) -> list[str]:
    """Keep the packed capability order from the recorded contract."""
    return [
        _safe_text(item.get("title") or item.get("name"))
        for item in plans
        if _safe_text(item.get("title") or item.get("name"))
    ]


def _operation_collect_hint(plan: dict) -> str:
    title = _safe_text(plan.get("title") or plan.get("name"))
    required = _required_fields(plan)
    if plan.get("requires_confirmation"):
        if required:
            fields = "、".join(f"`{name}`" for name in required)
            return f"{title}：收集契约中的必填字段（{fields}），执行前确认。"
        return f"{title}：收集该操作调用方字段，执行前确认。"
    if required:
        fields = "、".join(f"`{name}`" for name in required)
        return f"{title}：收集契约中的必填字段（{fields}）。"
    return f"{title}：只收集用户本次给出的调用方字段。"


def _title_for_plan_ref(plans: list[dict], cap_id: str) -> str:
    item = _plan_by_ref(plans).get(str(cap_id or ""))
    if item:
        return _safe_text(item.get("title") or item.get("name"))
    return ""


def _composition_section(skill, plans: list[dict], spec) -> list[str]:  # noqa: ANN001
    plan = _skill_plan_payload(skill)
    notes = [
        _safe_text(item)
        for item in (plan.get("composition_notes") or [])
        if _safe_text(item) and not _is_recording_copy(item)
    ]
    combinations = _combination_routes(skill)
    lines = ["## 能力关系", ""]
    summary = _safe_text(plan.get("composition_summary") or plan.get("summary"))
    if summary and not _is_recording_copy(summary):
        lines.extend([summary, ""])
    if notes:
        lines.extend(f"- {item}" for item in notes)
    else:
        lines.append("- 只要只读操作时，只执行对应只读操作，不得执行写入。")
        lines.append("- 没有自动传值；先后办理就先查再问。")
    if combinations:
        lines.extend(["", "组合路线："])
        for route in combinations:
            scripts: list[str] = []
            for cap_id in route.get("capability_sequence") or []:
                item = _plan_by_ref(plans).get(str(cap_id))
                if item:
                    scripts.append(f"`python scripts/{item['script']}.py`")
                else:
                    title = _title_for_plan_ref(plans, str(cap_id))
                    if title:
                        scripts.append(title)
            when = _safe_text(route.get("when_to_use") or route.get("name"))
            bindings = [item for item in (route.get("bindings") or []) if isinstance(item, dict)]
            if bindings:
                bound = "；".join(
                    f"{_title_for_plan_ref(plans, str(item.get('from_capability') or ''))} 的 {item.get('from_output')} → "
                    f"{_title_for_plan_ref(plans, str(item.get('to_capability') or ''))} 的 {item.get('to_input')}"
                    if _title_for_plan_ref(plans, str(item.get("from_capability") or ""))
                    else f"{item.get('from_output')} → {item.get('to_input')}"
                    for item in bindings
                    if item.get("from_output") and item.get("to_input")
                )
                extra = f"已确认绑定：{bound}" if bound else "使用已确认绑定传值"
            else:
                extra = "没有自动传值，先查再问"
            lines.append(f"- {when}：{' → '.join(scripts)}。{extra}。")
    else:
        lines.append("- 没有自动传值；先后办理就先查再问。")
    auto_lines: list[str] = []
    relations = []
    if spec is not None:
        relations = [
            relation
            for relation in (getattr(spec, "capability_relations", None) or [])
            if getattr(relation, "confirmed", False)
        ]
    for relation in relations:
        source = _title_for_plan_ref(plans, str(getattr(relation, "from_capability", "") or ""))
        target = _title_for_plan_ref(plans, str(getattr(relation, "to_capability", "") or ""))
        if getattr(relation, "from_output", None) and getattr(relation, "to_input", None):
            if source and target:
                auto_lines.append(f"- {source} 的 {relation.from_output} → {target} 的 {relation.to_input}")
            else:
                auto_lines.append(f"- {relation.from_output} → {relation.to_input}")
    if auto_lines:
        lines.extend(["", "自动带入的字段：", *auto_lines])
    return lines


def _intent_for_plan(skill, item: dict) -> str:  # noqa: ANN001
    plan = _skill_plan_payload(skill)
    cap_id = str(item.get("capability_id") or item.get("name") or "")
    name = str(item.get("name") or "")
    for route in plan.get("routes") or []:
        if not isinstance(route, dict):
            continue
        sequence = [str(value) for value in (route.get("capability_sequence") or [])]
        if len(sequence) == 1 and (cap_id in sequence or name in sequence):
            when = _safe_text(route.get("when_to_use"))
            if when and not _is_recording_copy(when):
                return when
    return _safe_text(item.get("title") or item.get("name"))


def _planning_skill_md_sections(skill, plans: list[dict], spec=None) -> list[str]:  # noqa: ANN001
    plan = _skill_plan_payload(skill)
    unused = [item for item in (plan.get("unused_capabilities") or []) if isinstance(item, dict)]
    triggers = [
        str(item) for item in (plan.get("trigger_phrases") or [])
        if str(item).strip() and not _is_recording_copy(item)
    ]
    summary = _safe_text(plan.get("composition_summary") or plan.get("summary"))
    lines = ["## 适用场景", ""]
    if summary and not _is_recording_copy(summary):
        lines.append(f"- {summary}")
    for route in plan.get("routes") or []:
        if not isinstance(route, dict):
            continue
        when = _safe_text(route.get("when_to_use"))
        if when and not _is_recording_copy(when) and when not in "\n".join(lines):
            lines.append(f"- {when}")
    for item in triggers:
        if item not in "\n".join(lines):
            lines.append(f"- {_safe_text(item)}")
    if len(lines) == 2:
        lines.append("- 用户请求与本页已打包操作一致时使用。")
    lines.extend(["", "## 不适用场景", ""])
    lines.append("- 不要用于其它业务对象或未列出的动作。")
    lines.append("- 只要查询或查看时，不得执行写入。")
    lines.append("- 没有已确认绑定却假装已经串联时停止，先查再问。")
    lines.append("- 不得编造字段、接口、输出或未确认关系。")
    for item in unused:
        title = _safe_text(item.get("title") or item.get("name"))
        reason = _safe_text(item.get("reason") or "当前业务描述未要求")
        if title:
            lines.append(f"- 不要执行「{title}」：{reason}。")
    lines.append("")
    lines.extend(_composition_section(skill, plans, spec))
    lines.extend([
        "", "## 操作路由", "",
        "先把用户意图映射到下表中的一条操作，或「能力关系」里的一条组合路线。",
        "",
        "| 用户意图 | 操作 | 脚本 | 必填输入 | 写前确认 | 写后验证 |",
        "|---|---|---|---|---|---|",
    ])
    for item in plans:
        required = ", ".join(f"`{name}`" for name in _required_fields(item)) or "无"
        lines.append(
            f"| {_intent_for_plan(skill, item)} | "
            f"`{item['name']}` | `python scripts/{item['script']}.py` | {required} | "
            f"{'是' if item.get('requires_confirmation') else '否'} | "
            f"{'是' if item.get('requires_verify') else '否'} |"
        )
    combinations = _combination_routes(skill)
    by_ref = _plan_by_ref(plans)
    if combinations:
        lines.extend(["", "组合路线（必须按这条走，不要自行全排列）："])
        for route in combinations:
            scripts: list[str] = []
            for cap_id in route.get("capability_sequence") or []:
                item = by_ref.get(str(cap_id))
                if item:
                    scripts.append(f"`python scripts/{item['script']}.py`")
                else:
                    title = _title_for_plan_ref(plans, str(cap_id))
                    if title:
                        scripts.append(title)
            lines.append(
                f"- {_safe_text(route.get('when_to_use') or route.get('name') or route.get('route_id'))}："
                f"{' → '.join(scripts)}"
            )
    return lines


def _fallback_skill_md(skill, slug: str, plans: list[dict], spec) -> str:  # noqa: ANN001
    del slug
    heading, description = _skill_description(skill, plans, spec)
    skill_name = _skill_frontmatter_name(skill, plans)
    lines = [
        "---",
        f"name: {skill_name}",
        f"description: {json.dumps(description, ensure_ascii=False)}",
        "---",
        "",
        f"# {heading}",
        "",
    ]
    lines.extend(_applicable_sections(skill, plans))
    lines.extend(_workflow_table(skill, plans))
    lines.append("")
    lines.extend(_composition_rules(skill))
    lines.extend(_execution_protocol())
    lines.extend(_success_failure_section(skill))
    lines.extend(_on_demand_resources(skill, plans))
    return "\n".join(lines).rstrip() + "\n"


_HANDBOOK_PROCESS_KEYS = frozenset({
    "evidence", "verification_id", "verification_ids", "confirmation_hash",
    "confidence", "confidence_tier", "actor", "updated_by",
})


def _handbook_contract(node: Any) -> Any:
    if isinstance(node, dict):
        return {
            str(key): _handbook_contract(value)
            for key, value in node.items()
            if str(key) not in _HANDBOOK_PROCESS_KEYS
        }
    if isinstance(node, list):
        return [_handbook_contract(item) for item in node]
    return node


def _capabilities_md(skill, plans: list[dict]) -> str:  # noqa: ANN001
    lines = [
        "# 业务能力",
        "",
        "只在需要判断某个能力的输入输出边界时阅读本文件。不要把这里当成路线说明书。",
        "",
        "| 能力 | 何时使用 | 类型 | 必要输入概况 | 关键输出概况 | 完成判断 | 主要风险 |",
        "|---|---|---|---|---|---|---|",
    ]
    for item in plans:
        write = bool(item.get("requires_verify"))
        confirm = bool(item.get("requires_confirmation"))
        required = "、".join(f"`{name}`" for name in _required_fields(item)) or "无调用方必填"
        if confirm:
            risk = "执行前必须确认；结果未知不得重试" if write else "执行前必须确认"
        elif write:
            risk = "结果未知不得重试"
        else:
            risk = "只读，不得升级成写入"
        done = "已确认并执行成功" if confirm else "已返回可核对的业务结果"
        lines.append(
            f"| {_safe_text(item.get('title') or item.get('name'))} | {_intent_for_plan(skill, item)} | "
            f"{'变更' if write else '只读'} | {required} | 业务结果 | {done} | {risk} |"
        )
    lines.extend([
        "",
        "## 完整能力契约",
        "",
        "以下 JSON 是生成脚本使用的完整契约；字段、来源、规则、步骤和依赖均不省略。",
    ])
    for item in plans:
        execution = dict(item.get("execution_contract") or {})
        execution.setdefault("steps", list(item.get("steps") or []))
        execution.setdefault("links", list(item.get("links") or []))
        payload = {
            **dict(item.get("contract") or {}),
            "name": item.get("name"),
            "capability_id": item.get("capability_id"),
            "title": item.get("title"),
            "kind": item.get("kind"),
            "input_schema": item.get("input_schema") or {},
            "output_schema": item.get("output_schema") or {},
            "preconditions": item.get("preconditions") or [],
            "caller_responsibilities": item.get("caller_responsibilities") or [],
            "skill_responsibilities": item.get("skill_responsibilities") or [],
            "execution_contract": execution,
        }
        lines.extend([
            "",
            f"### {_safe_text(item.get('title') or item.get('name'))}",
            "",
            "```json",
            json.dumps(_handbook_contract(_scrub(payload)), ensure_ascii=False, indent=2, sort_keys=True),
            "```",
        ])
    return "\n".join(lines).rstrip() + "\n"


def _options_md(plans: list[dict]) -> str:
    lines = [
        "# 候选项",
        "",
        "说明候选如何在运行时获得和处理。不要把历史样本当成默认值，也不要在多条结果时默认第一条。",
        "",
        "| 字段/用途 | 候选类型 | 来源 | 何时加载 | 空结果 | 多结果 | 用户自定义是否允许 |",
        "|---|---|---|---|---|---|---|",
    ]
    rows = 0
    contracts: list[tuple[str, str, dict[str, Any]]] = []
    for item in plans:
        schema = item.get("input_schema") if isinstance(item.get("input_schema"), dict) else {}
        for name, field_name, raw in _iter_schema_fields(schema):
            source = _option_source(raw)
            options = _field_options(raw)
            raw_source = raw.get("x-dano-option-source") or raw.get("x-options-source-meta")
            if not source and not options and not isinstance(raw_source, dict):
                continue
            kind = "动态" if source else "固定枚举"
            origin = (
                f"`{source.get('method') or 'GET'} {source.get('endpoint')}`"
                if source
                else f"完整契约共 {len(options)} 项"
            )
            lines.append(
                f"| `{name}` / {_safe_text(item.get('title') or item.get('name'))} | {kind} | {origin or '契约候选'} | "
                f"{'需要选择该字段时' if source else '读取本表即可'} | 按指南允许自由输入、重试或停止 | "
                f"停问，不得默认第一条 | {'允许一个自定义其他' if any(str((option or {}).get('label') or '') in {'其他', 'Other'} for option in options if isinstance(option, dict)) else '否'} |"
            )
            contracts.append((
                _safe_text(item.get("title") or item.get("name")),
                str(name),
                {
                    "field": field_name,
                    "schema": _scrub(raw),
                    "source": _scrub(raw_source) if isinstance(raw_source, dict) else None,
                    "options": _scrub(options),
                    "count": len(options),
                },
            ))
            rows += 1
    if not rows:
        lines.append("| （当前没有独立候选字段） | — | 运行时按输入表单收集 | 缺字段时 | 停问 | 停问 | 视字段而定 |")
    else:
        lines.extend([
            "",
            "## 完整候选契约",
            "",
            "固定枚举逐项保留显示值与提交值；接口候选保留完整来源契约，不截断。",
        ])
        for title, name, contract in contracts:
            lines.extend([
                "",
                f"### {title} / `{name}`",
                "",
                "```json",
                json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True),
                "```",
            ])
    return "\n".join(lines).rstrip() + "\n"


_INPUT_SOURCE_LABELS = {
    "user": "由用户提供",
    "fixed_value": "使用固定值",
    "system_context": "来自系统上下文",
    "confirmed_binding": "由上一步已确认结果带入",
}


def _input_source_label(item: dict, allowed: set[str] | None = None) -> str:
    field = str(item.get("field") or "").strip()
    if field and allowed is not None and field not in allowed:
        return "按已有契约收集"
    how = _INPUT_SOURCE_LABELS.get(str(item.get("source") or "").strip(), "按已有契约收集")
    return f"`{field}` {how}" if field else how


def _route_file_md(route: dict, plans: list[dict]) -> str:
    name = _safe_text(route.get("name") or route.get("route_id"))
    sequence = [str(item) for item in (route.get("capability_sequence") or []) if str(item)]
    steps = [item for item in (route.get("steps") or []) if isinstance(item, dict)]
    checks = [item for item in (route.get("checkpoints") or []) if isinstance(item, dict)]
    example = (route.get("examples") or [{}])[0] if route.get("examples") else {}
    if not isinstance(example, dict):
        example = {}
    lines = [
        f"# {name}",
        "",
        "## 何时选择",
        "",
        _safe_text(route.get("when_to_use") or name),
        "",
        "## 前置条件",
        "",
    ]
    preconditions = [str(item) for item in (route.get("preconditions") or []) if str(item)]
    if preconditions:
        lines.extend(f"- {item}" for item in preconditions)
    else:
        lines.append("- 用户意图与本路线一致，且所需能力都可调用。")
    lines.extend([
        "",
        "## 执行步骤",
        "",
        "| 步骤 | 已打包操作 | 输入来源 | 执行后检查 | 下一步条件 |",
        "|---|---|---|---|---|",
    ])
    if steps:
        for index, step in enumerate(steps):
            cap_id = str(step.get("capability_id") or "")
            title = _title_for_plan_ref(plans, cap_id) or cap_id
            script = _script_for(plans, cap_id)
            allowed = _schema_field_names(_plan_by_ref(plans).get(cap_id) or {})
            sources = "；".join(
                _input_source_label(item, allowed)
                for item in (step.get("input_sources") or [])
                if isinstance(item, dict) and item.get("field")
            ) or ("按已有契约收集" if allowed is not None else "当前步骤缺少的调用方输入")
            nxt = "继续下一步" if index + 1 < len(steps) else "按完成条件结束"
            if step.get("checkpoint"):
                nxt = "在交接点停问，用户选定后再继续"
            lines.append(
                f"| 第{index + 1}步 | {title} `{script}` | {sources} | "
                f"{_safe_text(step.get('done_when') or '结果可核对')} | {nxt} |"
            )
    else:
        for index, cap_id in enumerate(sequence):
            title = _title_for_plan_ref(plans, cap_id) or cap_id
            script = _script_for(plans, cap_id)
            lines.append(
                f"| {index + 1} | {title} `{script}` | 按已有契约收集 | 结果可核对 | "
                f"{'继续下一步' if index + 1 < len(sequence) else '按完成条件结束'} |"
            )
    lines.extend(["", "## 输入来源与交接点", ""])
    if route.get("bindings"):
        for binding in route.get("bindings") or []:
            if not isinstance(binding, dict):
                continue
            lines.append(
                f"- 已确认绑定：{_title_for_plan_ref(plans, str(binding.get('from_capability') or ''))} 的 "
                f"`{binding.get('from_output')}` → {_title_for_plan_ref(plans, str(binding.get('to_capability') or ''))} 的 "
                f"`{binding.get('to_input')}`。空值、超出基数或类型不符时停止自动串联。"
            )
    if checks:
        for item in checks:
            lines.append(
                f"- 人工交接：{item.get('prompt') or '请用户选定下一步目标'}。"
                f"取消时{item.get('on_cancel') or '停止并报告未执行'}。"
            )
    if not route.get("bindings") and not checks:
        lines.append("- 各步输入独立收集，不猜测跨步字段。")
    lines.extend([
        "",
        "## 变更确认",
        "",
        f"- {_confirm_label(route, plans)}。",
        "",
        "## 完成验证",
        "",
        f"- {_safe_text(route.get('done_when') or '路线步骤均已按完成条件核对')}。",
        "",
        "## 失败与停止",
        "",
        f"- {_safe_text(route.get('failure_behavior') or '失败、未知结果或用户取消时停止，不得静默重试')}。",
        "",
        "## 完整示例",
        "",
        f"- 用户原话：{_safe_text(example.get('user_request') or '请按本路线办理，必要输入用 <字段> 占位')}",
        f"- 选择路线：{name}",
        f"- 步骤：{' → '.join(_title_for_plan_ref(plans, cap_id) or cap_id for cap_id in sequence)}",
        f"- 输入来源：{_cross_step_label(route)}",
        f"- 问人：{_ask_when_label(route, plans)}",
        f"- 确认：{_confirm_label(route, plans)}",
        f"- 完成：{_safe_text(example.get('done_when') or route.get('done_when') or '按完成条件核对')}",
        f"- 取消/空结果/失败：{_safe_text(example.get('on_cancel') or '停止并报告未执行')}；"
        f"{_safe_text(example.get('on_empty_or_ambiguous') or '候选为空或多条时停问')}；"
        f"{_safe_text(example.get('on_unknown_write_result') or '写入结果未知时停止且不重试')}。",
        "",
    ])
    return "\n".join(lines).rstrip() + "\n"


def _plan_api_chain(plan: dict, spec) -> str:  # noqa: ANN001
    chain = " -> ".join(
        f"{str(step.get('method') or 'GET').upper()} {step.get('path') or urlparse(str(step.get('url') or '')).path or '/'}"
        for step in plan.get("steps") or []
    ) or "GET /"
    evidence = _evidence_for_plan(plan, spec) if spec is not None else []
    markers = [f"verification_id: {value}" for value in evidence]
    if plan.get("requires_verify") and not plan.get("fact_checks"):
        markers.append("unverified write read-back")
    marker = "; ".join(markers) if markers else "unverified"
    return f"{chain}; {marker}"


def _operations_heading(skill, plans: list[dict]) -> str:  # noqa: ANN001
    title = _safe_text(getattr(skill, "title", "") if not _is_recording_copy(getattr(skill, "title", "")) else "")
    if title:
        return f"{title} 操作说明"
    heading, _description = _business_identity(skill, plans, None)
    if heading:
        return f"{heading} 操作说明"
    return "已打包操作说明"


def _operations_md(skill, plans: list[dict], spec) -> str:  # noqa: ANN001
    combinations = _combination_routes(skill)
    lines = [
        f"# {_operations_heading(skill, plans)}",
        "",
        "只在执行某个操作时阅读对应小节。机器契约以 `CONTRACT.json` 为准。",
        "组合规则只听 `SKILL.md`，不要在这里另发明编排。",
        "",
        "## API chain",
        "",
    ]
    for plan in plans:
        lines.append(f"- `{plan['name']}`: {_plan_api_chain(plan, spec)}")
    lines.extend(["", "## Business hard rules", ""])
    forbidden = list(((spec.goal or {}).get("forbidden_actions") or [])) if spec is not None else []
    if forbidden:
        lines.extend(f"- {_safe_text(value)}" for value in forbidden)
    else:
        lines.append("- 只执行已打包操作，任一请求失败立即停止。")
    if combinations:
        lines.append("- 组合路线必须按 SKILL.md 规划的顺序执行；没有已确认绑定的字段向用户收集。")
    lines.append("")
    for plan in plans:
        schema = plan.get("input_schema") or {}
        properties = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        write = bool(plan.get("requires_verify") or plan.get("requires_confirmation"))
        role = "写入步骤" if write else "查找或查询步骤"
        lines.extend([
            f"## {_safe_text(plan['title'])} (`{plan['name']}`)",
            "",
            f"- 脚本：`scripts/{plan['script']}.py`",
        ])
        if plan.get("requires_verify"):
            lines.append(f"- 验证脚本：`scripts/verify_{plan['script']}.py`")
        lines.extend([
            f"- 写前确认：{'是' if plan.get('requires_confirmation') else '否'}",
            f"- 写后验证：{'是' if plan.get('requires_verify') else '否'}",
            f"- 组合中的位置：{role}。{_operation_collect_hint(plan)}",
            f"- API：`{_plan_api_chain(plan, spec)}`",
            "",
            "执行要点：",
            f"- 确认用户要的是「{_safe_text(plan['title'])}」，查询和写入不要混成一步。",
            f"- 收集输入后执行 `python scripts/{plan['script']}.py --input-json '<JSON>'`。",
        ])
        if plan.get("requires_confirmation"):
            lines.append("- 写前先确认，命令加 `--confirm`。")
        if plan.get("requires_verify"):
            lines.append(f"- 写后再跑 `python scripts/verify_{plan['script']}.py`。")
        lines.extend([
            "",
            "| 字段 | 类型 | 必填 | 说明 |",
            "|---|---|---|---|",
        ])
        if not properties:
            lines.append("| （无） | object | 否 | 无调用方输入 |")
        for name, raw in properties.items():
            field = raw if isinstance(raw, dict) else {}
            description = _safe_text(field.get("description") or field.get("title") or name).replace("|", "\\|")
            lines.append(
                f"| `{name}` | `{field.get('type') or 'string'}` | "
                f"{'是' if name in required else '否'} | {description} |"
            )
        option_lines: list[str] = []
        for name, raw in properties.items():
            field = raw if isinstance(raw, dict) else {}
            live_source = field.get("x-dano-option-source") or field.get("x-options-source-meta")
            values = list(field.get("enum") or [])
            labels = dict(field.get("x-enum-value-map") or {})
            if not live_source and not values and not labels:
                continue
            option_lines.extend([f"### `{name}` 选项", ""])
            if isinstance(live_source, dict) and live_source:
                method = str(live_source.get("source_method") or "GET").upper()
                endpoint = str(live_source.get("source_url") or "")
                option_lines.append(f"- 运行时来源：`{method} {endpoint}`")
            if labels:
                option_lines.extend(f"- `{_safe_text(label)}` → `{value}`" for label, value in labels.items())
            elif values:
                option_lines.extend(f"- `{value}`" for value in values)
            option_lines.append("")
        if option_lines:
            lines.extend(option_lines)
        else:
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _format_list_py(plans: list[dict]) -> str:
    schemas = {
        str(plan["name"]): dict(plan.get("output_schema") or {})
        for plan in plans
    }
    return f'''from __future__ import annotations

import argparse
import json
import sys

SCHEMAS = json.loads({json.dumps(json.dumps(schemas, ensure_ascii=False), ensure_ascii=False)})


def list_rows(value):
    if isinstance(value, dict) and "output" in value:
        return list_rows(value["output"])
    if isinstance(value, dict):
        for key in ("records", "rows", "items", "list"):
            if isinstance(value.get(key), list):
                return value[key]
        if isinstance(value.get("data"), (dict, list)):
            return list_rows(value["data"])
        return [value]
    return value if isinstance(value, list) else [value]


def row_schema(schema):
    properties = (schema or {{}}).get("properties") or {{}}
    for field in properties.values():
        if isinstance(field, dict) and field.get("type") == "array":
            return field.get("items") or {{}}
    return schema or {{}}


def cell(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value).replace("|", r"\\|").replace("\\r", " ").replace("\\n", "<br>")


def main():
    parser = argparse.ArgumentParser(description="format capability output as Markdown table")
    parser.add_argument("--json")
    parser.add_argument("--capability", required=True, choices=sorted(SCHEMAS))
    args = parser.parse_args()
    raw = args.json if args.json is not None else sys.stdin.read()
    value = json.loads(raw.lstrip("\ufeff"))
    rows = list_rows(value)
    if not rows:
        print("无数据")
        return
    rows = [row if isinstance(row, dict) else {{"值": row}} for row in rows]
    properties = (row_schema(SCHEMAS[args.capability]).get("properties") or {{}})
    columns = []
    for row in rows:
        for key in row:
            field = properties.get(key) or {{}}
            if field.get("x-dano-display") is False or field.get("x-dano-internal") is True:
                continue
            if key not in columns:
                columns.append(key)
    if not columns:
        print("无数据")
        return
    labels = [str((properties.get(key) or {{}}).get("title") or key) for key in columns]
    print("| " + " | ".join(cell(label) for label in labels) + " |")
    print("| " + " | ".join("---" for _ in columns) + " |")
    for row in rows:
        print("| " + " | ".join(cell(row.get(key)) for key in columns) + " |")


if __name__ == "__main__":
    main()
'''


def _fallback_reference_md(skill, plans: list[dict], spec) -> str:  # noqa: ANN001
    lines = [f"# {_safe_text(skill.title or skill.skill_id)} reference", "", "## API chain", ""]
    for plan in plans:
        chain = " -> ".join(
            f"{str(step.get('method') or 'GET').upper()} {step.get('path') or urlparse(str(step.get('url') or '')).path or '/'}"
            for step in plan["steps"]
        ) or "GET /"
        evidence = _evidence_for_plan(plan, spec)
        markers = [f"verification_id: {value}" for value in evidence]
        if plan["requires_verify"] and not plan["fact_checks"]:
            markers.append("unverified write read-back")
        marker = "; ".join(markers) if markers else "unverified"
        lines.append(f"- `{plan['name']}`: {chain}; {marker}")
    lines.extend(["", "## Business hard rules", ""])
    forbidden = list(((spec.goal or {}).get("forbidden_actions") or [])) if spec is not None else []
    if forbidden:
        lines.extend(f"- {_safe_text(value)}" for value in forbidden)
    else:
        lines.append("- Execute only the selected capability and stop on the first failed request.")
    lines.extend(["", "## Fallback browser steps", ""])
    events = list((spec.request_facts.page_events or [])) if spec is not None else []
    semantic = []
    for event in events[-50:]:
        if not isinstance(event, dict):
            continue
        kind = _safe_text(event.get("kind") or event.get("op") or event.get("type"))
        role = _safe_text(event.get("role"))
        name = _safe_text(event.get("name") or event.get("label") or event.get("text"))
        if kind or role or name:
            semantic.append(" / ".join(value for value in (kind, role, name) if value))
    if semantic:
        lines.extend(f"{index}. {value}" for index, value in enumerate(semantic[:20], 1))
    else:
        lines.append("1. Open the recorded business page and follow the visible role/name labels matching the capability; do not use coordinates.")
    return "\n".join(lines).rstrip() + "\n"


_CLIENT_TEMPLATE = r'''from __future__ import annotations

import argparse
import copy
import datetime
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urljoin
from uuid import uuid4

import httpx
from wire_format import apply_wire_formats, date_span_days

CONFIG = json.loads(__CONFIG__)
BASE_URL = os.environ.get("DANO_BUSINESS_BASE_URL", CONFIG["base_url"]).rstrip("/")
_PLACEHOLDER = re.compile(r"^\{\{([^{}]+)\}\}$")
_MISSING = object()


def emit(payload):
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str))


def _json_object(raw, label):
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be a JSON object")
    return value


def _cache_headers():
    path = Path.home() / ".dano" / "sessions" / f"{CONFIG['tenant']}__{CONFIG['subsystem'].replace('/', '_')}.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data.get("headers"), dict) and data["headers"]:
        return data["headers"]
    headers = {}
    cookies = data.get("cookies") if isinstance(data, dict) else []
    pairs = [f"{item.get('name')}={item.get('value')}" for item in cookies or [] if item.get("name") and item.get("value")]
    if pairs:
        headers["Cookie"] = "; ".join(pairs)
    for origin in data.get("origins") or []:
        for item in origin.get("localStorage") or []:
            name = str(item.get("name") or "").casefold()
            value = str(item.get("value") or "").strip().strip('"')
            if value and any(hint in name for hint in ("access_token", "accesstoken", "auth_token", "authorization")):
                headers.setdefault("Authorization", value if value.lower().startswith("bearer ") else f"Bearer {value}")
    return headers


def auth_headers():
    raw = os.environ.get("DANO_AUTH_HEADERS")
    if raw:
        return _json_object(raw, "DANO_AUTH_HEADERS")
    cached = _cache_headers()
    if cached:
        return cached
    dano_url = os.environ.get("DANO_URL", "").rstrip("/")
    tenant_key = os.environ.get("DANO_TENANT_KEY", "")
    if dano_url and tenant_key:
        response = httpx.get(
            dano_url + "/v1/settings/token/raw",
            params={"tenant": CONFIG["tenant"], "subsystem": CONFIG["subsystem"]},
            headers={"X-Tenant-Key": tenant_key}, timeout=20,
        )
        response.raise_for_status()
        headers = response.json().get("headers") or {}
        if headers:
            return headers
    raise RuntimeError("authentication unavailable: set DANO_AUTH_HEADERS or configure a session/Dano token source")


def get_path(node, path):
    text = str(path or "").removeprefix("response.").removeprefix("$.")
    if text in {"", "$", "response"}:
        return node
    tokens = [token for token in re.split(r"\.|\[|\]", text) if token]
    current = node
    for token in tokens:
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            return None
    return current


def deep_set(node, path, value):
    tokens = [token for token in re.split(r"\.|\[|\]", str(path or "")) if token]
    current = node
    for token in tokens[:-1]:
        if isinstance(current, list) and token.isdigit():
            while len(current) <= int(token):
                current.append({})
            current = current[int(token)]
        else:
            current = current.setdefault(token, {})
    if not tokens:
        return value
    last = tokens[-1]
    if isinstance(current, list) and last.isdigit():
        while len(current) <= int(last):
            current.append(None)
        current[int(last)] = value
    else:
        current[last] = value
    return node


def render(node, values):
    if isinstance(node, dict):
        rendered = {key: render(value, values) for key, value in node.items()}
        return {key: value for key, value in rendered.items() if value is not _MISSING}
    if isinstance(node, list):
        return [value for item in node if (value := render(item, values)) is not _MISSING]
    if not isinstance(node, str):
        return copy.deepcopy(node)
    match = _PLACEHOLDER.fullmatch(node)
    if match:
        key = match.group(1)
        if key not in values:
            return _MISSING
        return copy.deepcopy(values[key])
    return re.sub(r"\{\{([^{}]+)\}\}", lambda match: str(values[match.group(1)]), node)


def _business_ok(data, rule):
    if isinstance(data, dict) and rule and rule.get("field") in data:
        return str(data[rule["field"]]) in {str(value) for value in rule.get("ok_values") or []}
    if isinstance(data, dict):
        for key in ("code", "status", "errcode", "errCode", "resultCode", "rspCode", "retCode", "flag"):
            if key in data and not isinstance(data[key], (dict, list)):
                return str(data[key]).casefold() in {"200", "0", "00000", "true", "success", "ok", "1"}
        if "success" in data:
            return bool(data["success"])
    return True


def http_json(method, path="", *, url="", query=None, body=None, content_type="application/json", success_rule=None):
    target = url or path
    if not str(target).startswith(("http://", "https://")):
        if not BASE_URL:
            raise RuntimeError("DANO_BUSINESS_BASE_URL is required because the recording has no absolute origin")
        target = urljoin(BASE_URL + "/", str(target).lstrip("/"))
    kwargs = {"params": query or None, "headers": auth_headers(), "timeout": 30}
    if body is not None:
        if "form-urlencoded" in str(content_type).casefold():
            kwargs["data"] = body
        else:
            kwargs["json"] = body
    response = httpx.request(str(method or "GET").upper(), target, **kwargs)
    try:
        data = response.json()
    except ValueError:
        data = response.text
    ok = response.is_success and _business_ok(data, success_rule)
    return {"ok": ok, "status": response.status_code, "data": data, "method": str(method).upper(), "url": str(response.url)}


def settle(seconds=0.25):
    time.sleep(max(0.0, min(float(seconds), 5.0)))


def _live_option_rows(binding, values, cache):
    source_url = str(binding.get("source_url") or "")
    if not source_url:
        return []
    method = str(binding.get("source_method") or "GET").upper()
    body = render(binding.get("source_body"), values) if binding.get("source_body") is not None else None
    cache_key = json.dumps(
        [method, source_url, body], ensure_ascii=False, sort_keys=True, default=str,
    )
    if cache_key not in cache:
        result = http_json(
            method, url=source_url, body=body,
            content_type=binding.get("source_content_type") or "application/json",
        )
        if not result.get("ok"):
            raise RuntimeError(f"option source request failed: {method} {source_url}")
        cache[cache_key] = list_items(result.get("data"))
    rows = [item for item in cache[cache_key] if isinstance(item, dict)]
    category_key = str(binding.get("category_key") or "")
    if category_key:
        expected = str(binding.get("category_value") or "")
        rows = [item for item in rows if str(get_path(item, category_key) or "") == expected]
    return rows


def _selected_option(binding, raw, rows):
    value_key = str(binding.get("value_key") or "")
    label_key = str(binding.get("label_key") or "")
    if not rows or not value_key or not label_key:
        option_map = binding.get("option_map") or {}
        return option_map.get(str(raw), raw), None
    matches = [
        item for item in rows
        if str(get_path(item, label_key)) == str(raw)
        or str(get_path(item, value_key)) == str(raw)
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"option {raw!r} is not uniquely present in live source for {binding.get('param') or binding.get('path')}"
        )
    return get_path(matches[0], value_key), matches[0]


def _nested_array_field(path: str) -> tuple[str, str] | None:
    match = re.match(r"^([^.\[]+)\[(?:\d+|\*|)?\]\.(.+)$", str(path or ""))
    if not match:
        return None
    return match.group(1), match.group(2)


def _apply_selects(step, values, cache):
    current = dict(values)
    projections = {}
    for binding in step.get("selects") or []:
        if binding.get("source_url"):
            _live_option_rows(binding, current, cache)
    for binding in step.get("selects") or []:
        param = str(binding.get("param") or "")
        nested = _nested_array_field(str(binding.get("path") or binding.get("id_path") or ""))
        if nested:
            container, field = nested
            rows_in = current.get(container)
            if not isinstance(rows_in, list):
                continue
            live_rows = _live_option_rows(binding, current, cache)
            owned = []
            for item in rows_in:
                if not isinstance(item, dict) or field not in item:
                    owned.append(item)
                    continue
                row = dict(item)
                row[field], selected_row = _selected_option(binding, row[field], live_rows)
                for target_path, response_path in (binding.get("field_projections") or {}).items():
                    target = _nested_array_field(str(target_path))
                    if selected_row is None or target is None or target[0] != container:
                        continue
                    projected = get_path(selected_row, response_path)
                    if projected is None:
                        raise RuntimeError(
                            f"live option field {response_path!r} is missing for {param or container}"
                        )
                    deep_set(row, target[1], projected)
                owned.append(row)
            current[container] = owned
            continue
        if not param or param not in current:
            continue
        rows = _live_option_rows(binding, current, cache)
        raw = current[param]
        if isinstance(raw, list):
            selected = [_selected_option(binding, value, rows) for value in raw]
            current[param] = [value for value, _row in selected]
            selected_rows = [row for _value, row in selected if row is not None]
        else:
            current[param], selected_row = _selected_option(binding, raw, rows)
            selected_rows = [selected_row] if selected_row is not None else []
        field_projections = binding.get("field_projections") or {}
        if field_projections:
            if len(selected_rows) != 1:
                raise RuntimeError(f"option projections require one selected row for {param}")
            for target_path, response_path in field_projections.items():
                value = get_path(selected_rows[0], response_path)
                if value is None:
                    raise RuntimeError(
                        f"live option field {response_path!r} is missing for {param}"
                    )
                projections[str(target_path)] = value
    return current, projections


def _system_values(step, body):
    for item in step.get("system_values") or []:
        kind = str(item.get("kind") or "")
        value = (
            int(time.time() * 1000) if kind == "now_ms" else
            time.strftime("%Y-%m-%d") if kind == "now_date" else
            time.strftime("%Y-%m-%dT%H:%M:%S") if kind == "now_iso" else
            str(uuid4())
        )
        deep_set(body, item.get("path") or "", value)
    return body


def _runtime_values(step, inputs):
    values = dict(inputs)
    pending = [field for field in (step.get("runtime_fields") or []) if str(field.get("name") or "")]
    progressed = True
    while pending and progressed:
        progressed = False
        still = []
        for field in pending:
            name = str(field.get("name") or "")
            if name in values:
                continue
            kind = str(field.get("kind") or field.get("strategy") or "")
            if kind in {"date_span_days", "date_span_days_json"}:
                start_name = str(field.get("start_field") or "")
                end_name = str(field.get("end_field") or "")
                if start_name not in values or end_name not in values:
                    still.append(field)
                    continue
                days = date_span_days(values[start_name], values[end_name])
                values[name] = (
                    json.dumps(
                        {str(field.get("output_key") or "days"): days},
                        ensure_ascii=False, separators=(",", ":"),
                    )
                    if kind == "date_span_days_json" else days
                )
                progressed = True
                continue
            if kind == "date_range_end":
                start_name = str(field.get("start_field") or "")
                if start_name not in values:
                    still.append(field)
                    continue
                output_format = str(field.get("output_format") or "%Y-%m-%d 23:59:59")
                start = values[start_name]
                if output_format == "epoch_ms":
                    values[name] = int(start) + 86_399_999
                elif output_format == "epoch_s":
                    values[name] = int(start) + 86_399
                else:
                    match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", str(start or ""))
                    if match is None:
                        raise RuntimeError("date_range_end requires a calendar date or epoch value")
                    day = datetime.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
                    values[name] = day.strftime(output_format)
                progressed = True
                continue
            if kind in {"product", "sum", "difference", "percent_of", "remainder_after_percent"}:
                left_name = str(field.get("left_field") or "")
                right_name = str(field.get("right_field") or "")
                if left_name not in values or right_name not in values:
                    still.append(field)
                    continue
                left = float(values[left_name])
                right = float(values[right_name])
                computed = {
                    "product": left * right,
                    "sum": left + right,
                    "difference": left - right,
                    "percent_of": left * right / 100.0,
                    "remainder_after_percent": left * (1.0 - right / 100.0),
                }[kind]
                values[name] = computed
                result_field = str(field.get("result_field") or "")
                if result_field and result_field not in values:
                    values[result_field] = computed
                progressed = True
                continue
            if kind == "array_item_formula":
                container = str(field.get("array_container_path") or field.get("container_field") or "items")
                item_key = str(field.get("array_item_key") or field.get("result_field") or "")
                strategy = str(field.get("strategy") or "product")
                left_name = str(field.get("left_field") or "")
                right_name = str(field.get("right_field") or "")
                rows = values.get(container)
                if not isinstance(rows, list) or not item_key:
                    still.append(field)
                    continue
                for row in rows:
                    if not isinstance(row, dict) or left_name not in row or right_name not in row:
                        continue
                    left = float(row[left_name])
                    right = float(row[right_name])
                    row[item_key] = {
                        "product": left * right,
                        "sum": left + right,
                        "difference": left - right,
                        "percent_of": left * right / 100.0,
                        "remainder_after_percent": left * (1.0 - right / 100.0),
                    }.get(strategy, left * right)
                progressed = True
                continue
            still.append(field)
        pending = still
    if pending:
        missing = str(pending[0].get("name") or "computed")
        raise RuntimeError(f"computed field {missing} is missing operands")
    return apply_wire_formats(values, step.get("wire_formats") or {})


def _response_key_map(link, source, body, values):
    collection = get_path(source, link.get("source_collection_path") or link.get("source_path"))
    binding = link.get("value_binding") or {}
    input_field = str(binding.get("input_field") or "")
    input_fields_by_label = {
        str(label): str(field)
        for label, field in dict(binding.get("input_fields_by_label") or {}).items()
        if str(label) and str(field)
    }
    caller_map = (
        {
            label: values[field]
            for label, field in input_fields_by_label.items()
            if field in values
        }
        if input_fields_by_label else values.get(input_field)
    )
    if not isinstance(collection, list) or not collection:
        raise RuntimeError(f"dynamic structure source unavailable: {link.get('link_id')}")
    if binding.get("kind") != "caller_map_by_label" or not isinstance(caller_map, dict):
        raise RuntimeError(f"dynamic structure input {input_field} must be an object")
    rows = []
    for item in collection:
        key = get_path(item, link.get("source_key_path"))
        label = get_path(item, link.get("source_label_path"))
        if key in (None, "") or label in (None, ""):
            raise RuntimeError(f"dynamic structure node lacks id/name: {link.get('link_id')}")
        rows.append((str(key), str(label)))
    keys = [key for key, _label in rows]
    labels = [label for _key, label in rows]
    if len(set(keys)) != len(keys) or len(set(labels)) != len(labels):
        raise RuntimeError(f"dynamic structure node id/name is duplicated: {link.get('link_id')}")
    required_labels = [str(label) for label in (binding.get("required_labels") or labels)]
    ignored_labels = {str(label) for label in (binding.get("ignored_labels") or [])}
    row_by_label = {label: key for key, label in rows}
    missing_source = [label for label in required_labels if label not in row_by_label]
    unexpected_source = [
        label for label in labels if label not in set(required_labels) | ignored_labels
    ]
    missing = [label for label in required_labels if label not in caller_map]
    extra = [label for label in caller_map if label not in set(required_labels)]
    if missing_source or unexpected_source or missing or extra:
        raise RuntimeError(
            f"dynamic structure labels changed: source_missing={missing_source!r}, "
            f"source_unexpected={unexpected_source!r}, missing={missing!r}, extra={extra!r}"
        )
    wrap = str(binding.get("value_shape") or "direct") in {"single_item_list", "item_list"}
    rebuilt = {
        key: (value if isinstance(value, list) else [value]) if wrap else value
        for label in required_labels
        for key in [row_by_label[label]]
        for value in [caller_map[label]]
    }
    return deep_set(body or {}, link.get("target_container_path") or link.get("target_path"), rebuilt)


def execute_plan(plan, inputs):
    outputs = []
    option_cache = {}
    for index, step in enumerate(plan.get("steps") or []):
        values, option_projections = _apply_selects(step, dict(inputs), option_cache)
        for target, value in option_projections.items():
            leaf = [token for token in re.split(r"[.\[]", str(target)) if token and not token.endswith("]")]
            if leaf and leaf[-1] not in values:
                values[leaf[-1]] = value
        values = _runtime_values(step, values)
        body = render(step.get("body_template"), values) if step.get("body_template") is not None else None
        query = render(step.get("query_template"), values) if step.get("query_template") is not None else None
        url = render(step.get("url_template") or step.get("url") or step.get("path") or "", values)
        for target, value in option_projections.items():
            if target.startswith("query."):
                query = deep_set(query or {}, target[6:], value)
            elif target.startswith("path."):
                values[target[5:]] = value
                url = render(step.get("url_template") or step.get("url") or step.get("path") or "", values)
            else:
                body = deep_set(body or {}, target.removeprefix("body."), value)
        for link in plan.get("links") or []:
            if int(link.get("target_step", -1)) != index:
                continue
            source_index = int(link.get("source_step", -1))
            if source_index < 0 or source_index >= len(outputs):
                raise RuntimeError(f"verified dependency source unavailable: {link.get('link_id')}")
            if str(link.get("kind") or "") == "response_key_map":
                body = _response_key_map(link, outputs[source_index]["data"], body, values)
                continue
            value = get_path(outputs[source_index]["data"], link.get("source_path"))
            if value is None:
                raise RuntimeError(f"verified dependency value missing: {link.get('verification_id')}")
            target = str(link.get("target_path") or "")
            if target.startswith("query."):
                query = deep_set(query or {}, target[6:], value)
            elif target.startswith("path."):
                values[target[5:]] = value
                url = render(step.get("url_template") or step.get("url") or step.get("path") or "", values)
            else:
                body = deep_set(body or {}, target.removeprefix("body."), value)
        if isinstance(body, (dict, list)):
            body = _system_values(step, body)
        result = http_json(
            step.get("method") or "GET", step.get("path") or "", url=url,
            query=query, body=body, content_type=step.get("content_type") or "application/json",
            success_rule=step.get("success_rule"),
        )
        outputs.append(result)
        if not result["ok"]:
            return {"ok": False, "failed_step": step.get("step_id"), "results": outputs}
        if str(step.get("method") or "GET").upper() not in {"GET", "HEAD"}:
            settle()
    return {"ok": True, "results": outputs, "data": outputs[-1]["data"] if outputs else None}


def evaluate_assertion(response, assertion, inputs):
    actual = get_path(response, assertion.get("path") or assertion.get("response_path") or "")
    expected = assertion.get("equals", assertion.get("value"))
    input_path = assertion.get("equals_input") or assertion.get("input_path")
    if input_path:
        expected = get_path(inputs, input_path)
    operator = assertion.get("operator") or ("equals" if expected is not None else "truthy")
    if operator in {"equals", "eq"}:
        return actual == expected
    if operator in {"not_equals", "ne"}:
        return actual != expected
    if operator == "contains":
        return expected in actual if isinstance(actual, (str, list, tuple, set, dict)) else False
    if operator == "exists":
        return actual is not None
    return bool(actual)


def list_items(node):
    if isinstance(node, list):
        return node
    if not isinstance(node, dict):
        return []
    for key in ("list", "records", "rows", "items", "content"):
        if isinstance(node.get(key), list):
            return node[key]
    for key in ("data", "result", "payload"):
        nested = list_items(node.get(key))
        if nested:
            return nested
    return []


def main():
    parser = argparse.ArgumentParser(description="Self-contained business API client")
    parser.add_argument("--show-config", action="store_true")
    args = parser.parse_args()
    emit({"ok": True, "tenant": CONFIG["tenant"], "subsystem": CONFIG["subsystem"], "base_url_configured": bool(BASE_URL)} if args.show_config else {"ok": True})


if __name__ == "__main__":
    main()
'''


_CAPABILITY_TEMPLATE = r'''from __future__ import annotations

import argparse
import datetime
import json
import re
import sys

from client import emit, execute_plan

PLAN = json.loads(__PLAN__)


def _coerce(value, schema):
    kind = str((schema or {}).get("type") or "string")
    if kind in {"object", "array"}:
        return json.loads(value)
    if kind == "integer":
        return int(value)
    if kind == "number":
        return float(value)
    if kind == "boolean":
        lowered = str(value).casefold()
        if lowered not in {"true", "false", "1", "0", "yes", "no"}:
            raise ValueError("boolean must be true/false")
        return lowered in {"true", "1", "yes"}
    return value


def _validate(value, schema, path="input"):
    schema = schema or {}
    if "const" in schema and value != schema.get("const"):
        raise ValueError(f"{path} must equal {schema.get('const')!r}")
    alternatives = schema.get("oneOf") or schema.get("anyOf")
    if alternatives:
        matched = 0
        for alternative in alternatives:
            try:
                _validate(value, alternative, path)
                matched += 1
            except (TypeError, ValueError):
                pass
        if not matched or (schema.get("oneOf") and matched != 1):
            raise ValueError(f"{path} does not match its alternative schemas")
        return
    allowed = schema.get("enum")
    if allowed and value not in allowed:
        raise ValueError(f"{path} must be one of: {', '.join(map(str, allowed))}")
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, dict):
            raise ValueError(f"{path} must be an object")
        properties = schema.get("properties") or {}
        missing = [
            name for name in schema.get("required") or []
            if name not in value or value[name] in (None, "")
        ]
        if missing:
            raise ValueError(f"{path} missing required: {', '.join(missing)}")
        if schema.get("additionalProperties") is False:
            extra = sorted(name for name in value if name not in properties)
            if extra:
                raise ValueError(f"{path} has undeclared fields: {', '.join(extra)}")
        for name, child in properties.items():
            if name in value and value[name] is not None:
                _validate(value[name], child, f"{path}.{name}")
    elif expected == "array":
        if not isinstance(value, list):
            raise ValueError(f"{path} must be an array")
        if schema.get("minItems") is not None and len(value) < int(schema["minItems"]):
            raise ValueError(f"{path} has too few items")
        if schema.get("maxItems") is not None and len(value) > int(schema["maxItems"]):
            raise ValueError(f"{path} has too many items")
        if schema.get("uniqueItems") and len({json.dumps(item, sort_keys=True) for item in value}) != len(value):
            raise ValueError(f"{path} must not contain duplicate items")
        for index, item in enumerate(value):
            _validate(item, schema.get("items") or {}, f"{path}[{index}]")
    elif expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        raise ValueError(f"{path} must be an integer")
    elif expected == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
        raise ValueError(f"{path} must be a number")
    elif expected == "boolean" and not isinstance(value, bool):
        raise ValueError(f"{path} must be a boolean")
    elif expected == "string" and not isinstance(value, str):
        raise ValueError(f"{path} must be a string")
    if isinstance(value, str):
        if schema.get("minLength") is not None and len(value) < int(schema["minLength"]):
            raise ValueError(f"{path} is too short")
        if schema.get("maxLength") is not None and len(value) > int(schema["maxLength"]):
            raise ValueError(f"{path} is too long")
        if schema.get("pattern") and re.search(str(schema["pattern"]), value) is None:
            raise ValueError(f"{path} does not match its pattern")
        if schema.get("format") == "date":
            datetime.date.fromisoformat(value)
        elif schema.get("format") == "date-time":
            datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if schema.get("minimum") is not None and value < schema["minimum"]:
            raise ValueError(f"{path} is below minimum")
        if schema.get("maximum") is not None and value > schema["maximum"]:
            raise ValueError(f"{path} is above maximum")
        if schema.get("exclusiveMinimum") is not None and value <= schema["exclusiveMinimum"]:
            raise ValueError(f"{path} must be greater than exclusiveMinimum")
        if schema.get("exclusiveMaximum") is not None and value >= schema["exclusiveMaximum"]:
            raise ValueError(f"{path} must be less than exclusiveMaximum")


def parser():
    command = argparse.ArgumentParser(description=PLAN.get("title") or PLAN["name"])
    command.add_argument("--input-json", default="{}", help="JSON object merged before named arguments")
    command.add_argument("--confirm", action="store_true", help="confirm an explicitly reviewed write")
    for name, schema in (PLAN.get("input_schema", {}).get("properties") or {}).items():
        command.add_argument(f"--{name}", dest=name, help=str(schema.get("description") or schema.get("title") or name))
    return command


def inputs_from_args(args, command):
    try:
        values = json.loads(args.input_json)
        if not isinstance(values, dict):
            raise ValueError("--input-json must be an object")
        properties = PLAN.get("input_schema", {}).get("properties") or {}
        for name, schema in properties.items():
            raw = getattr(args, name, None)
            if raw is not None:
                values[name] = _coerce(raw, schema)
        missing = [name for name in PLAN.get("input_schema", {}).get("required") or [] if name not in values]
        if missing:
            command.error("missing required inputs: " + ", ".join(missing))
        _validate(values, PLAN.get("input_schema") or {"type": "object"})
        return values
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        command.error(str(exc))


def main():
    command = parser()
    args = command.parse_args()
    inputs = inputs_from_args(args, command)
    if PLAN.get("requires_confirmation") and not args.confirm:
        emit({
            "capability": PLAN["name"], "ok": False, "status": "need_confirm",
            "reason": "write capability requires explicit confirmation",
        })
        return 0
    result = execute_plan(PLAN, inputs)
    emit({
        "capability": PLAN["name"],
        "status": "succeeded" if result.get("ok") else "failed",
        **result,
    })
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
'''


_VERIFY_TEMPLATE = r'''from __future__ import annotations

import sys

from client import emit, evaluate_assertion, http_json, list_items, settle
from __CAP_MODULE__ import PLAN, inputs_from_args, parser


def verify(inputs):
    issues = []
    checks = PLAN.get("fact_checks") or []
    if PLAN.get("requires_verify") and not checks:
        issues.append({"step_id": None, "verification_id": "unverified", "reason": "no verified read-back is available"})
    for check in checks:
        settle(check.get("backoff_s", 0.25))
        response = http_json("GET", check.get("endpoint") or "")
        passed = bool(response.get("ok"))
        assertion = check.get("assertion")
        if passed and isinstance(assertion, dict) and assertion:
            passed = evaluate_assertion(response.get("data"), assertion, inputs)
        elif passed and check.get("match_field") and check.get("param"):
            target = inputs.get(check["param"])
            passed = target is not None and any(
                isinstance(item, dict) and str(item.get(check["match_field"])) == str(target)
                for item in list_items(response.get("data"))
            )
        if not passed:
            issues.append({"step_id": check.get("step_id"), "verification_id": check.get("verification_id") or "unverified", "reason": "read-back assertion failed"})
    return {"ok": not issues, "issues": issues, "checks": len(checks)}


def main():
    command = parser()
    inputs = inputs_from_args(command.parse_args(), command)
    result = verify(inputs)
    emit({"capability": PLAN["name"], **result})
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
'''


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _clean_runtime_artifacts(folder: Path) -> None:
    for path in folder.rglob("__pycache__"):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
    for path in folder.rglob("*.pyc"):
        try:
            path.unlink()
        except OSError:
            pass


def _render_folder(skill, folder: Path, *, tenant: str) -> tuple[list[dict], bool]:  # noqa: ANN001
    api_request = _compiled_request(skill, None)
    plans = _filter_plans_for_export(_capability_plans(skill, None, api_request), skill)
    if not plans:
        raise ValueError(f"{skill.skill_id} has no capability")
    steps = [step for plan in plans for step in (plan.get("steps") or [])]
    slug = package_slug(skill.skill_id)
    skill_md = _fallback_skill_md(skill, slug, plans, None)

    scripts = folder / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    references = folder / "references"
    references.mkdir(parents=True, exist_ok=True)
    _write_text(folder / "SKILL.md", skill_md)
    _write_text(references / "CAPABILITIES.md", _capabilities_md(skill, plans))
    _write_text(references / "OPTIONS.md", _options_md(plans))
    _write_text(references / "INPUT_FORMS.md", _input_forms_md(plans))
    routes_dir = references / "routes"
    for route in _combination_routes(skill):
        route_id = _safe_text(route.get("route_id") or "").strip()
        if not route_id:
            continue
        routes_dir.mkdir(parents=True, exist_ok=True)
        _write_text(routes_dir / f"{route_id}.md", _route_file_md(route, plans))
    config = {
        "tenant": tenant,
        "subsystem": str(skill.subsystem.value if hasattr(skill.subsystem, "value") else skill.subsystem),
        "base_url": _base_url(steps),
    }
    _write_text(scripts / "client.py", _CLIENT_TEMPLATE.replace("__CONFIG__", repr(json.dumps(config, ensure_ascii=False))))
    from dano.execution.page import wire_format as wire_format_module

    _write_text(
        scripts / "wire_format.py",
        Path(wire_format_module.__file__).read_text(encoding="utf-8"),
    )
    _write_text(scripts / "format_list.py", _format_list_py(plans))
    contract = {
        "protocol": "dano.skill_package.contract.v1",
        "skill": {"id": skill.skill_id, "name": slug, "title": skill.title or skill.action},
        "capabilities": [
            _scrub({
                **dict(plan.get("contract") or {}),
                "name": plan["name"],
                "capability_id": plan.get("capability_id") or "",
                "title": plan["title"],
                "kind": plan["kind"],
                "script": f"scripts/{plan['script']}.py",
                "verify_script": f"scripts/verify_{plan['script']}.py" if plan["requires_verify"] else "",
                "requires_confirmation": plan["requires_confirmation"],
                "requires_verify": plan["requires_verify"],
                "input_schema": plan["input_schema"],
                "output_schema": plan["output_schema"],
                "preconditions": plan["preconditions"],
                "caller_responsibilities": plan["caller_responsibilities"],
                "skill_responsibilities": plan["skill_responsibilities"],
                "execution_contract": plan.get("execution_contract") or {
                    "protocol": "dano.capability_plan.v2",
                    "steps": plan.get("steps") or [],
                    "links": plan.get("links") or [],
                },
            })
            for plan in plans
        ],
        "capability_relations": [
            _scrub(dict(relation))
            for relation in (api_request.get("capability_relations") or [])
            if isinstance(relation, dict)
        ],
    }
    contract.update(_contract_planning_fields(skill))
    _write_text(
        references / "CONTRACT.json",
        json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    for plan in plans:
        plan_payload = {**plan, "fact_checks": plan["fact_checks"]}
        module = plan["script"]
        _write_text(
            scripts / f"{module}.py",
            _CAPABILITY_TEMPLATE.replace("__PLAN__", repr(json.dumps(plan_payload, ensure_ascii=False))),
        )
        if plan["requires_verify"]:
            _write_text(
                scripts / f"verify_{module}.py",
                _VERIFY_TEMPLATE.replace("__CAP_MODULE__", module),
            )
    _clean_runtime_artifacts(folder)
    return plans, True


def render_skill_package(skill, out_dir: str, *, tenant: str) -> str:  # noqa: ANN001
    """Render one SkillSpec atomically and return its package folder name."""
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    slug = package_slug(skill.skill_id)
    stage = Path(tempfile.mkdtemp(prefix=f".{slug}-", dir=root))
    try:
        _plans, fallback_used = _render_folder(skill, stage, tenant=tenant)
        _clean_runtime_artifacts(stage)
        validation = validate_skill_package(stage)
        if not validation["ok"] and not fallback_used:
            plans = _filter_plans_for_export(
                _capability_plans(skill, None, _compiled_request(skill, None)),
                skill,
            )
            _write_text(stage / "SKILL.md", _fallback_skill_md(skill, slug, plans, None))
            fallback_used = True
            validation = validate_skill_package(stage)
        _clean_runtime_artifacts(stage)
        if not validation["ok"]:
            raise ValueError(f"skill package validation failed: {validation['issues']}")
        target = root / slug
        backup = root / f".{slug}.old-{uuid4().hex}"
        if target.exists():
            target.rename(backup)
        try:
            stage.rename(target)
        except Exception:
            if backup.exists():
                backup.rename(target)
            raise
        if backup.exists():
            shutil.rmtree(backup)
        log.info("export.skill_package", skill_id=skill.skill_id, folder=slug, fallback_used=fallback_used)
        return slug
    finally:
        if stage.exists():
            shutil.rmtree(stage)


async def write_skill_packages(
    tenant: str,
    out_dir: str,
    *,
    skill_ids: list[str] | None = None,
) -> list[str]:
    """Render every published PAGE_SCRIPT skill selected for one tenant."""
    from dano.assets.repository import AssetRepository
    from dano.orchestrator.skills import SkillRegistry

    repo = AssetRepository()
    try:
        subsystems = await repo.distinct_subsystems(tenant)
    except Exception as exc:  # noqa: BLE001
        log.warning("export.package_discovery_failed", tenant=tenant, error=str(exc))
        subsystems = []
    registry = await SkillRegistry.from_store(repo, tenant=tenant, subsystems=subsystems)
    selected = None if skill_ids is None else set(skill_ids)
    page_skills = [
        skill for skill in registry.skills
        if skill.recording_asset_id is not None and (selected is None or skill.skill_id in selected)
    ]
    written: list[str] = []
    for skill in page_skills:
        try:
            written.append(render_skill_package(skill, out_dir, tenant=tenant))
        except Exception as exc:  # noqa: BLE001 - one malformed legacy asset cannot block peers
            details = _export_failure_details(skill, out_dir, exc)
            note_run_fact(
                skill_package_status="failed",
                exported_count=len(written),
                export_directory=out_dir,
                root_cause=_export_reason_code(exc),
                failed_stage="export",
                canonical_contract_present=details.get("canonical_contract_present"),
            )
            emit_run_exception(
                "skill.package.export.failed",
                exc,
                stage="export",
                code=_export_reason_code(exc),
                cause=(
                    "当前发布资产没有可供 Skill 包生成器读取的规范能力契约"
                    if _export_reason_code(exc) == "CANONICAL_CAPABILITY_CONTRACT_MISSING"
                    else str(exc)
                ),
                artifact_refs=[f"skill_id:{skill.skill_id}"],
                skill_id=skill.skill_id,
                details=details,
                next_action="检查该 asset version 对应的发布能力契约和 release identity",
            )
            log.warning("export.skill_package_failed", skill_id=skill.skill_id, error=str(exc))
    return written
