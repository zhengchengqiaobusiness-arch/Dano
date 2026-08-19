"""Step A+B+C+D: FlowSpec 完整实现。

- Step A: 收敛函数 to_flow_spec（包含 GET 业务请求）
- Step B: 编辑函数 apply_flow_edits（字段/参数/链接/重排）
- Step C: 链接编辑支持
- Step D: GET 表单手选 + Pi Agent 命名 + 业务说明
"""

from __future__ import annotations

import re
import uuid
import json
import copy
import hashlib
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from urllib.parse import unquote, urlparse, parse_qs, urlencode

# 复用 request_capture 的纯函数
from dano.execution.page.request_capture import (
    _is_const_value,
    _fact_path_tokens,
    _leaf_paths,
    _parse_body,
    _is_system_timestamp,
    bounded_response_sample,
    normalized_leaf_paths,
    as_list_payload,
    apply_page_enum_options,
    build_api_request,
    classify_request_role,
    discover_step_links,
    select_dependency_source,
    page_enum_selects,
    extract_auth_headers,
    flatten_body,
    infer_success_rule,
    write_requests,
    looks_internal_param_name,
    looks_like_auth_write,
    looks_like_read_request,
    parse_recorded_request_body,
    self_check,
    substitute,
    suggest_assignee_names,
    suggest_fact_check,
    suggest_identity,
    suggest_list_selects,
    suggest_select_names,
    suggest_selects,
    _is_idlike,
    _multipart_contains_file,
    _pick_label_key,
)
from dano.execution.page.value_tracing import discover_response_key_maps, discover_workflow_value_links



from dano.execution.page.flow_spec_core.models import (
    CapabilityDependency,
    CapabilityField,
    CapabilityRelation,
    CapabilityRequestRef,
    FlowCapability,
    FlowLink,
    FlowSpec,
    FlowSpecConflictError,
    FlowStep,
    IdentityBinding,
    ParamField,
    RecordedGoal,
    RequestAnalysis,
    RequestFact,
    RequestFacts,
    RequestUsage,
    ReviewItem,
    SelectBinding,
    SystemValue,
    register_sync_flow_spec_models,
)
from dano.execution.page.flow_spec_core.serialization import flow_spec_release_payload
from dano.execution.page.flow_spec_core.fingerprints import (
    _flow_fingerprint,
    _stable_json_hash,
    flow_spec_fingerprint,
)


# ─────────── 数据模型 ───────────






# H19 修复:显式白名单(替代 hasattr 兜底,防止越权改关键字段)
_PARAM_ALLOWED_FIELDS = frozenset({
    "category", "source_kind", "source", "label",
    "reason", "confidence", "name_source", "enum_options",
    "enum_value_map", "locked", "evidence", "description",
})
_STEP_ALLOWED_FIELDS = frozenset({
    "selects", "identity", "params", "sample_inputs",
    "source_meta", "semantic_role", "success_rule", "fact_check",
    "response_json", "notes",
})



































# ─────────── Step A: 收敛函数 ───────────






















































































































































# 一个 query 路径(如 query.status)上的下拉值若在 reads 候选列表里有命中,就被识别为 select






























































































































































def sync_flow_spec_models(spec: FlowSpec) -> FlowSpec:
    _canonicalize_materialized_request_identities(spec)
    _upgrade_materialized_query_facts(spec)
    # Upgrading an initial list request to the richer searched fact can make it
    # converge with a step that already owns that durable request identity.
    _canonicalize_materialized_request_identities(spec)
    _enrich_materialized_response_shapes(spec)
    _rebind_saved_field_evidence(spec)
    _repair_readonly_control_defaults(spec)
    _ground_saved_page_enums(spec)
    # FlowStep 已经是可编辑/可编排接口的物化事实；usage 不能等到能力绑定后才更新，
    # 否则初次分析会把已进入字段页的查询接口仍标成 captured。
    for step in spec.steps:
        for param in step.params:
            if not param.field_id:
                identity = f"{step.step_id}\0{param.path}".encode("utf-8")
                param.field_id = f"pf_{hashlib.sha256(identity).hexdigest()[:16]}"
            if not param.wire_format and param.type in {"date", "datetime", "time", "number", "string"}:
                param.wire_format = _infer_wire_format(
                    param.value if param.value not in (None, "") else param.default_value
                )
        if (step.method or "GET").upper() in {"GET", "HEAD"}:
            # Legacy/imported specs may only carry query values in the URL. Put
            # them into ParamField first so request compilation, capability input
            # schemas and scoped field views all read the same executable truth.
            query_url = step.url if "?" in str(step.url or "") else step.path
            _append_query_params_to_step(step, query_url or step.url)
        _sync_step_option_contracts(spec, step)
        _audit_step_param_contracts(step)
        valid_param_paths = {param.path for param in step.params if param.path}
        for select in step.selects or []:
            if select.id_path and select.id_path not in valid_param_paths:
                select.id_path = None
                select.id_tokens = None
        request_id = str((step.source_meta or {}).get("request_id") or "")
        if not request_id:
            continue
        usage = spec.request_facts.usage.get(request_id) or RequestUsage(request_id=request_id)
        usage.state = "materialized"
        usage.materialized_step_id = step.step_id
        spec.request_facts.usage[request_id] = usage
    return sync_capability_scoped_views(spec)


def _rebind_saved_field_evidence(spec: FlowSpec) -> None:
    """Re-evaluate unresolved DOM facts against the authoritative saved body.

    The client projection deliberately redacts request bodies, but the server
    draft still owns them on FlowStep. Re-analysis therefore must bind from
    server steps instead of freezing an old ``unbound`` result forever.
    """
    evidence = list(getattr(spec.request_facts, "field_evidence", []) or [])
    unresolved_indexes = [
        index
        for index, item in enumerate(evidence)
        if isinstance(item, dict)
        and (
            str(item.get("binding_status") or "") in {"unbound", "unresolved", "ambiguous"}
            # Value-only bindings are deliberately heuristic. Re-evaluate
            # them against the authoritative saved request bodies so improved
            # disambiguation can repair an older binding instead of freezing a
            # textarea value onto an unrelated paging/option request forever.
            or str(item.get("binding_method") or "").startswith("unique_value_")
        )
    ]
    if not unresolved_indexes:
        return
    from dano.execution.page.recording_field_identity import (
        bind_field_evidence,
        canonical_wire_path,
    )

    requests: list[dict[str, Any]] = []
    facts_by_id = {
        str(fact.request_id or ""): fact.model_dump(exclude_none=True)
        for fact in spec.request_facts.requests
        if str(fact.request_id or "")
    }
    for step in spec.steps:
        meta = dict(step.source_meta or {})
        request_id = str(meta.get("request_id") or "")
        if not request_id:
            continue
        analysis = spec.request_facts.analysis.get(request_id)
        fact = facts_by_id.get(request_id, {})
        method = str(step.method or fact.get("method") or "GET").upper()
        query = meta.get("query") or fact.get("query") or {}
        body = step.body_source
        # A client projection intentionally redacts request bodies.  Rebinding
        # against that empty projection would destroy previously captured
        # evidence, so only authoritative server-side request values may
        # participate in this repair pass.
        if method in {"GET", "HEAD", "OPTIONS"}:
            if not query:
                continue
        elif body in (None, "", {}, []):
            continue
        requests.append({
            **fact,
            **meta,
            "request_id": request_id,
            "method": method,
            "url": step.url or step.path,
            "post_data": body,
            "query": query,
            "role": analysis.role if analysis is not None else meta.get("role") or "",
        })
    if not requests:
        return
    unresolved = [evidence[index] for index in unresolved_indexes]
    rebound_unresolved = bind_field_evidence(
        requests,
        list(spec.request_facts.page_events or []),
        unresolved,
        page_enum_options=_page_enum_options_from_request_facts(spec.request_facts),
    )
    rebound = list(evidence)
    for index, item in zip(unresolved_indexes, rebound_unresolved, strict=True):
        rebound[index] = item
    spec.request_facts.field_evidence = rebound
    for step in spec.steps:
        request_id = str((step.source_meta or {}).get("request_id") or "")
        controls = [
            item for item in rebound
            if isinstance(item, dict)
            and item.get("binding_status") == "bound"
            and str(item.get("request_id") or "") == request_id
        ]
        for param in step.params:
            wire_path = canonical_wire_path(step, param.path)
            matches = [item for item in controls if str(item.get("wire_path") or "") == wire_path]
            if len(matches) != 1:
                continue
            control = matches[0]
            label = str(control.get("label") or control.get("field") or "").strip()
            if (
                label
                and not _param_field_manually_edited(param, "key")
                and not _param_field_manually_edited(param, "label")
                and not any(
                    isinstance(item, dict) and item.get("actor") == "agent" and item.get("kind") == "field_name"
                    for item in (param.evidence or [])
                )
            ):
                param.label = label
            control_kind = str(control.get("control_kind") or "").lower()
            if not _param_field_manually_edited(param, "type"):
                if control_kind == "date":
                    param.type = "date"
                elif control_kind in {"datetime", "time"}:
                    param.type = "datetime"
                elif control_kind == "number":
                    param.type = "number"
                elif control_kind == "textarea":
                    param.type = "string"
                elif control_kind == "text" and str(param.wire_type or "") != "number":
                    param.type = "string"
            if (
                param.source_kind in {"unknown", ""}
                and str(step.method or "").upper() in {"GET", "HEAD"}
                and str(param.path or "").startswith("query.")
                and control_kind not in {"", "unknown", "table_column"}
                and not bool(control.get("disabled") or control.get("read_only"))
                and not _param_has_manual_contract(param)
            ):
                param.category = "user_param"
                param.source_kind = "user_input"
                param.source = {
                    **(param.source or {}),
                    "kind": "control_default",
                    "path": param.path,
                    "required_state": str((param.source or {}).get("required_state") or "unknown"),
                }
                param.exposed_to_user = True
                param.editable = True
                param.need_human_confirm = False
                param.reason = "查询页上的可编辑筛选控件；调用方可省略或覆盖录制时的筛选值"
            if not any(
                isinstance(item, dict) and item.get("kind") == "page_control"
                for item in (param.evidence or [])
            ):
                param.evidence = [
                    *(param.evidence or []),
                    {
                        "kind": "page_control",
                        "source": "recorder_dom",
                        "control_kind": control_kind,
                        "interacted": str(control.get("op") or "").lower() in {"fill", "select", "pick"},
                        "request_path": param.path,
                        "binding_status": "bound",
                        "required": bool(control.get("required") or control.get("required_observed")),
                        "surface": str(control.get("surface") or ""),
                        "in_dialog": bool(control.get("in_dialog")),
                        "action_id": str(control.get("action_id") or ""),
                    },
                ]
            if isinstance(control.get("required_observed"), bool):
                param.evidence = [
                    item for item in (param.evidence or [])
                    if not (isinstance(item, dict) and item.get("kind") == "page_required")
                ]
                if control["required_observed"]:
                    param.evidence.append({
                        "kind": "page_required",
                        "source": "recorder_dom",
                        "request_path": param.path,
                        "binding_status": "bound",
                        "evidence_id": control.get("evidence_id") or "",
                    })
                if (
                    not _param_field_manually_edited(param, "required")
                    and not _param_required_agent_classified(param)
                ):
                    observed = bool(control["required_observed"])
                    param.required = bool(observed and _param_exposed_to_caller(param))
                    param.source = {
                        **(param.source or {}),
                        "required_state": "required" if observed else "optional",
                    }


































def _record_param_manual_contract(param: ParamField, fields: list[str] | tuple[str, ...]) -> None:
    """Mark explicit operator-owned ParamField axes before any derived sync."""
    axis_by_field = {
        "key": "name", "label": "name", "name": "name", "display_name": "name",
        "path": "path", "value": "default_value", "default_value": "default_value",
        "type": "type", "wire_type": "path", "category": "category",
        "exposed_to_user": "category", "exposed_to_caller": "category",
        "editable": "category", "source_kind": "source", "source": "source",
        "enum_options": "source", "enum_value_map": "source",
        "required": "required",
    }
    for field in dict.fromkeys(fields):
        if not hasattr(param, field):
            continue
        param.evidence.append({
            "source": "manual_edit",
            "field": field,
            "axis": axis_by_field.get(field, field),
            "status": "locked",
            "kind": "manual_override",
            "value": getattr(param, field),
        })


def _reset_param_source(
    param: ParamField,
    *,
    reason: str | None = None,
    actor: str = "system",
) -> None:
    """把字段从运行期/接口来源恢复成普通用户输入，供删除依赖/重置来源使用。"""
    normalized_actor = str(actor or "system").strip().lower()
    if normalized_actor in _AUTOMATED_FIELD_EDIT_ACTORS and (
        param.locked
        or _param_axis_manually_edited(
            param, "category", "source_kind", "source", "editable",
            "exposed_to_user", "need_human_confirm",
        )
    ):
        return
    param.category = "user_param"
    param.source_kind = "user_input"
    param.source = {"kind": "sample", "path": param.path}
    param.editable = True
    param.exposed_to_user = True
    param.need_human_confirm = False
    param.confidence_tier = "manual"
    param.reason = reason or "已取消运行期/接口来源绑定，改为调用 Skill 时由用户填写"
    if normalized_actor == "user":
        _record_param_manual_contract(param, (
            "category", "source_kind", "source", "editable",
            "exposed_to_user", "need_human_confirm",
        ))






def _transition_param_type(param: ParamField, value: Any) -> None:
    """Apply only the explicitly edited type; never rewrite other field choices."""
    param.type = str(value or "string")






_AUTOMATED_FIELD_EDIT_ACTORS = frozenset({
    "planner", "repair", "auto", "autofix", "optimizer", "system",
})

_DEFAULT_RECORDED_FORBIDDEN_ACTIONS = [
    "调用当前录制范围外的接口",
    "篡改录制事实",
    "泄露认证凭证",
]
_LEGACY_RECORDED_FORBIDDEN_ACTIONS = frozenset({"删除", "作废", "撤销", "终止", "驳回"})

























def _remove_param_incoming_links(spec: FlowSpec, step: FlowStep, param: ParamField) -> None:
    """人工把字段改离上游响应时，依赖与字段来源必须在同一事务内解除。"""
    removed = [
        link for link in spec.links
        if link.target_step_id == step.step_id
        and _reference_targets_param(step, link.target_path, param)
    ]
    for link in removed:
        _record_rejected_dependency(spec, link)
    if removed:
        removed_ids = {link.link_id for link in removed}
        spec.links = [link for link in spec.links if link.link_id not in removed_ids]


def _apply_link_sources(steps: list[FlowStep], links: list[FlowLink]) -> None:
    by_id = {s.step_id: s for s in steps}
    for lk in links:
        if (lk.meta or {}).get("actor") == "agent" and not (lk.meta or {}).get("verified"):
            continue
        link_kind = _flow_link_kind(lk)
        target = by_id.get(lk.target_step_id)
        source = by_id.get(lk.source_step_id)
        if target is None or source is None:
            continue
        target_path = lk.target_path
        if link_kind == "response_key_map":
            # The response supplies the *keys* of this request object, not its
            # assignee values. Keep the stable label-to-value map as caller
            # input while execution translates labels to the latest keys.
            public = next((p for p in target.params if p.path == target_path), None)
            binding = lk.value_binding or {}
            input_field = str(binding.get("input_field") or "").strip()
            if public is not None and input_field:
                option_source = binding.get("option_source")
                input_fields_by_label = {
                    str(label): str(field)
                    for label, field in dict(binding.get("input_fields_by_label") or {}).items()
                    if str(label) and str(field)
                }
                if input_fields_by_label:
                    public.category = "runtime_var"
                    public.source_kind = "dynamic_structure"
                    public.source = {
                        "kind": "dynamic_structure_leaf",
                        "required_state": "internal",
                    }
                    public.required = False
                    public.editable = False
                    public.exposed_to_user = False
                    public.need_human_confirm = False
                    samples = public.value if isinstance(public.value, dict) else {}
                    target.sample_inputs.pop(input_field, None)
                    for label, field in input_fields_by_label.items():
                        if label in samples:
                            target.sample_inputs[field] = copy.deepcopy(samples[label])
                    continue
                public.key = input_field
                public.type = "object"
                public.wire_type = "object"
                public.category = "user_param"
                public.source_kind = "user_input"
                public.source = {
                    "kind": "dynamic_structure_input",
                    "required_state": "required",
                    **({"option_source": copy.deepcopy(option_source)} if isinstance(option_source, dict) else {}),
                }
                public.required = True
                public.editable = True
                public.exposed_to_user = True
                public.need_human_confirm = False
                # response_key_map exposes a stable label-to-value object.
                # Keep the canonical sample in that public form as well; the
                # executor alone translates labels to the latest response keys.
                if isinstance(public.value, dict):
                    target.sample_inputs[input_field] = copy.deepcopy(public.value)
            continue
        if link_kind == "structure":
            # A structure link controls request keys only. It is not a value
            # dependency and must not replace the request container itself.
            continue
        target_param = _resolve_param_reference(target, target_path)
        for p in [target_param] if target_param is not None else []:
            hydration = bool(
                (lk.meta or {}).get("captured_record_hydration")
                or (lk.evidence or {}).get("kind") == "record_hydration"
            )
            captured_binding_overrides_agent_input = bool(
                p.source_kind in {
                    "user_input", "page_default", "unknown", *_OPTION_SOURCE_KINDS,
                }
                and not _param_was_caller_typed(p)
                and lk.confirmed
                and float(lk.confidence or 0.0) >= 0.95
                and not (lk.meta or {}).get("unverified_reason")
                and any(
                    (lk.meta or {}).get(key) is True
                    for key in (
                        "captured_value_match",
                        "captured_structure_match",
                        "captured_record_hydration",
                    )
                )
            )
            if p.locked or _param_axis_manually_edited(
                p, "category", "source_kind", "source", "editable", "exposed_to_user",
            ) or (
                _param_source_agent_classified(p)
                and p.source_kind != "chained"
                and not captured_binding_overrides_agent_input
                and not hydration
            ):
                # 依赖连线和字段来源是独立可编辑的事实。人工已选择
                # 分类/来源后，同步层不得再用旧连线覆盖用户结果。
                continue
            if not _auto_dependency_link_allowed(p, lk.source_path, lk):
                continue
            caller_editable = (
                not _param_control_is_readonly(p)
                and (
                    _param_has_editable_control_evidence(p)
                    or hydration
                    or _param_was_caller_typed(p)
                )
            )
            option_source = dict(p.source or {}) if p.source_kind in _OPTION_SOURCE_KINDS else {}
            if not option_source and isinstance((p.source or {}).get("option_source"), dict):
                option_source = dict((p.source or {}).get("option_source") or {})
            p.category = "user_param" if caller_editable else "runtime_var"
            p.source_kind = "previous_response"
            p.source = {
                "kind": "previous_response",
                "step_id": source.step_id,
                "step_name": source.name,
                "response_path": lk.source_path,
                "target_path": target_path,
                "link_id": lk.link_id,
                "allow_caller_override": caller_editable,
                **({"option_source": option_source} if option_source else {}),
            }
            p.editable = True
            p.exposed_to_user = caller_editable
            if not caller_editable:
                p.default_value = None
                p.required = False
                p.source = {**(p.source or {}), "required_state": "optional"}
            if caller_editable:
                p.reason = (
                    f"编辑场景默认来自上一步 `{source.name or source.path}` 的响应 `{lk.source_path}`；"
                    "调用方仍可修改该字段，显式输入优先于上游默认值"
                )
            else:
                p.reason = (
                    f"该字段由上一步 `{source.name or source.path}` 的响应 `{lk.source_path}` 提供，"
                    "运行期自动注入，不能使用录制旧值"
                )
            if _link_is_auto_generated(lk) or any(
                (lk.meta or {}).get(key) is True
                for key in ("captured_value_match", "captured_structure_match")
            ):
                p.confidence = max(
                    float(p.confidence or 0.0), float(lk.confidence or 0.0),
                )
                if lk.confirmed:
                    p.need_human_confirm = False
            p.confidence_tier = "linked"
            if p.key in target.sample_inputs:
                target.sample_inputs.pop(p.key, None)
            break


def _apply_user_link_source(steps: list[FlowStep], link: FlowLink) -> None:
    """Persist a user-created UI response binding without rewriting type/category."""
    by_id = {step.step_id: step for step in steps}
    source_step = by_id.get(link.source_step_id)
    target_step = by_id.get(link.target_step_id)
    if source_step is None or target_step is None:
        return
    target_path = link.target_path
    param = _resolve_param_reference(target_step, target_path)
    if param is None:
        return
    param.source_kind = "previous_response"
    param.source = {
        "kind": "previous_response",
        "step_id": source_step.step_id,
        "step_name": source_step.name,
        "response_path": link.source_path,
        "target_path": target_path,
        "link_id": link.link_id,
    }
    param.editable = True
    if not param.exposed_to_user:
        param.default_value = None
    param.need_human_confirm = not bool(link.confirmed)
    param.reason = (
        f"该字段由用户绑定到 `{source_step.name or source_step.path or source_step.step_id}` "
        f"的响应 `{link.source_path}`"
    )
    param.confidence = max(float(param.confidence or 0.0), float(link.confidence or 0.0))
    param.confidence_tier = "manual"
    target_step.sample_inputs.pop(param.key, None)
    _record_param_manual_contract(param, ("source_kind", "source"))
























def to_flow_spec(
    captured_requests: list[dict],
    *,
    reads: list[dict] | None = None,
    samples: dict | None = None,
    storage_state: dict | None = None,
    required_labels: set | None = None,
    page_enum_options: dict | None = None,
    field_evidence: list[dict] | None = None,
    page_context: dict | None = None,
    recording_mode: str = "",
    diagnostics: list[dict] | None = None,
    page_events: list[dict] | None = None,
    tenant: str = "",
    subsystem: str = "",
    request_role_overrides: dict[str, dict[str, Any]] | None = None,
    form_samples_by_request: dict | None = None,
    form_samples_by_transaction: dict | None = None,
) -> FlowSpec:
    """收敛：把 record_ws 现有产物 → FlowSpec（包含 GET 业务请求）。"""
    reads = reads or []
    samples = samples or {}
    required_labels = required_labels or set()
    page_enum_options = page_enum_options or {}
    page_context = page_context or {}
    diagnostics = diagnostics or []
    page_events = page_events or []
    recording_mode = recording_mode or "unknown"
    request_role_overrides = request_role_overrides or {}

    request_roles = []
    for request in captured_requests:
        if looks_like_auth_write(
            request.get("url") or request.get("path") or "",
            request.get("post_data"),
        ):
            request_roles.append(
                classify_network_request(request, captured_requests, samples)
            )
            continue
        recorded = request.get("_request_role") if isinstance(request.get("_request_role"), dict) else None
        if recorded is None and request.get("role"):
            recorded = {
                "role": request.get("role"),
                "keep": request.get("keep"),
                "reason": request.get("reason") or request.get("keep_reason") or "",
                "confidence": request.get("confidence") or 0.0,
                "evidence": request.get("evidence") or {},
            }
        request_id = str(request.get("request_id") or "")
        override = request_role_overrides.get(request_id)
        request_roles.append(dict(override) if isinstance(override, dict) else (
            recorded or classify_network_request(request, captured_requests, samples)
        ))
    # A visible user command is stronger public-boundary evidence than a
    # generic ``read_context`` classification. Preserve those reads as
    # callable business operations; background context and option traffic are
    # unchanged because they have no matching command anchor.
    for index, (request, role) in enumerate(zip(captured_requests, request_roles)):
        if (
            str((role or {}).get("role") or "") == "read_context"
            and _has_query_action_evidence(
                request.get("trigger_op"),
                " ".join(filter(None, (
                    str(request.get("trigger_action_id") or ""),
                    str(request.get("trigger_locator") or ""),
                ))),
            )
        ):
            request_roles[index] = {
                **dict(role or {}),
                "role": "business_get",
                "keep": True,
                "confidence": max(float((role or {}).get("confidence") or 0.0), 0.9),
            }
    # Bind DOM facts once, before any field projection.  All later naming,
    # required and enum logic consumes this same explicit identity result.
    observer_events_present = any(
        isinstance(event, dict) and event.get("event_id") and event.get("kind")
        for event in page_events
    )
    if observer_events_present and field_evidence:
        binding_requests = []
        for request, role in zip(captured_requests, request_roles):
            binding_request = dict(request)
            binding_request["request_id"] = _request_fact_key(_request_fact_entry(request, role))
            binding_requests.append(binding_request)
        from dano.execution.page.recording_field_identity import bind_field_evidence

        field_evidence = bind_field_evidence(
            binding_requests, page_events, field_evidence,
            page_enum_options=page_enum_options,
        )
    role_by_key = {_request_role_key(r): role for r, role in zip(captured_requests, request_roles)}
    flow_reads = _merge_flow_read_sources(reads, captured_requests, request_roles)

    # 1) 业务写请求
    write_cands = _dedupe_request_identities([
        c for c in write_requests(captured_requests)
        if (role_by_key.get(_request_role_key(c), {}).get("keep")
            and role_by_key.get(_request_role_key(c), {}).get("role") in {"submit_anchor", "business_write"})
    ])
    selected_write_request_ids = {
        str(request.get("request_id") or "") for request in write_cands if request.get("request_id")
    }
    record_hydration_candidates = _discover_record_hydration_links(
        captured_requests, selected_write_request_ids,
    )
    machine_preflight_request_ids: set[str] = set()
    machine_preflight_request_ids.update(
        str(candidate.get("source_request_id") or "")
        for candidate in record_hydration_candidates
        if candidate.get("source_request_id")
    )
    for candidate in discover_response_key_maps(captured_requests):
        if str(candidate.get("target_request_id") or "") in selected_write_request_ids:
            machine_preflight_request_ids.add(str(candidate.get("source_request_id") or ""))
    # Complete the control chain upstream: a strong response value can feed the
    # request that later produces the dynamic request-key collection.
    strong_value_candidates = discover_workflow_value_links(captured_requests)
    changed = True
    while changed:
        changed = False
        for candidate in strong_value_candidates:
            if str(candidate.get("target_request_id") or "") not in machine_preflight_request_ids:
                continue
            source_request_id = str(candidate.get("source_request_id") or "")
            if source_request_id and source_request_id not in machine_preflight_request_ids:
                machine_preflight_request_ids.add(source_request_id)
                changed = True
    # 2) 前置读候选：business_get 直接进入候选；存在写锚点时，把 read_context
    # 也交给后续数据/控制依赖闭包判断。候选源仍完整保存在 request_facts，只有
    # 进入执行闭包的读取才物化为 FlowStep。
    preread_cands = [
        r for r in captured_requests
        if (
            role_by_key.get(_request_role_key(r), {}).get("role") == "business_get"
            or (
                bool(write_cands)
                and role_by_key.get(_request_role_key(r), {}).get("role") == "read_context"
            )
            or str(r.get("request_id") or "") in machine_preflight_request_ids
        )
    ]
    preread_before_dedupe = len(preread_cands)
    preread_cands = _dedupe_preread_candidates(preread_cands)

    if not write_cands and not preread_cands:
        request_facts = _build_request_facts(
            captured_requests,
            request_roles,
            set(),
            diagnostics=diagnostics,
            page_enum_options=page_enum_options,
            page_events=page_events,
            field_evidence=field_evidence,
        )
        empty_spec = FlowSpec(
            tenant=tenant,
            subsystem=subsystem,
            title="(未捕获到业务请求)",
            recording_mode=recording_mode,
            diagnostics=diagnostics,
            request_facts=request_facts,
            goal=RecordedGoal(
                intent="录制业务请求",
                required_inputs=[],
                success_criteria=["重新录制后捕获至少一个业务 GET 或写请求"],
                output_expectation=["生成可编辑 FlowSpec"],
                forbidden_actions=list(_DEFAULT_RECORDED_FORBIDDEN_ACTIONS),
                risk_level="L1",
                capabilities=[],
            ).model_dump(),
            meta={
                "captured_total": len(captured_requests),
                "captured_write_candidates": 0,
                "reads_count": len(flow_reads),
                "request_roles": request_roles,
                "recording_mode": recording_mode,
                "diagnostics": diagnostics,
                "page_events_count": len(page_events),
                "page_context": page_context,
                "note": "录制未抓到任何业务写请求或业务 GET；用户可能未点提交，或页面是纯 GET 表单",
            },
        )
        return ensure_flow_version(refresh_review_items(empty_spec), "recorded", reason="录制生成空 FlowSpec")

    # 3) Materialize every retained business write. Action/transaction evidence
    # decides capability membership later; it must never erase a captured fact.
    write_idxs = list(range(len(write_cands)))

    selected_write_keys = {_request_role_key(write_cands[i]) for i in write_idxs if 0 <= i < len(write_cands)}
    # A command transaction is stronger boundary evidence than the request-role
    # classifier.  Frameworks often issue an auxiliary GET/POST-read from the
    # same click (permission check, detail lookup, workflow preflight, etc.).
    # Keeping only requests classified as ``business_get`` made such interfaces
    # disappear from the capability even though Observer recorded them as part
    # of the operation.  Admit JSON/XHR-like reads from the same transaction;
    # static resources, navigation and unsupported/auth traffic remain excluded.
    selected_transactions = {
        transaction
        for request in write_cands
        if _request_role_key(request) in selected_write_keys
        if (transaction := _request_transaction_id(request))
    }
    operation_reads: list[dict] = []
    post_write_read_keys: set[Any] = set()
    for request, role_info in zip(captured_requests, request_roles):
        if not selected_transactions or _request_transaction_id(request) not in selected_transactions:
            continue
        if _request_role_key(request) in selected_write_keys or bool(request.get("navigation_request")):
            continue
        resource_type = str(request.get("resource_type") or "").lower()
        if resource_type in {"document", "stylesheet", "image", "media", "font", "script"}:
            continue
        method = str(request.get("method") or "GET").upper()
        role = str((role_info or {}).get("role") or "")
        json_like = bool(
            request.get("response_json") is not None
            or "json" in str(request.get("content_type") or "").lower()
            or resource_type in {"fetch", "xhr", "xmlhttprequest"}
        )
        if method not in {"GET", "HEAD", "POST"} or not json_like:
            continue
        if role in {"auth", "unsupported_upload", "unsupported_graphql"}:
            continue
        transaction_writes = [
            write for write in write_cands
            if _request_role_key(write) in selected_write_keys
            and _request_transaction_id(write) == _request_transaction_id(request)
        ]
        if transaction_writes and not any(
            _request_precedes(request, write) for write in transaction_writes
        ):
            # A refresh/list request emitted after a write is an observation of
            # the result, not a preflight dependency of that write.
            post_write_read_keys.add(_request_role_key(request))
            continue
        operation_reads.append(request)
    if operation_reads:
        preread_cands = _dedupe_preread_candidates([*preread_cands, *operation_reads])
    preread_keys = {_request_role_key(r) for r in preread_cands}
    potential_keys = selected_write_keys | preread_keys
    potential_steps = _dedupe_request_identities([
        r for r in captured_requests if _request_role_key(r) in potential_keys
    ])
    # Build explicit preflight ownership per write request. A boolean
    # "preflight" flag cannot represent a recording containing multiple forms:
    # shared BPM endpoints would otherwise be copied into every submit ability.
    write_positions = {
        idx for idx, req in enumerate(potential_steps)
        if _request_role_key(req) in selected_write_keys
    }
    owners_by_position: dict[int, set[int]] = {
        position: {position} for position in write_positions
    }
    write_context_by_position = {
        position: _workflow_context_values_for_request(potential_steps[position])
        for position in write_positions
    }

    def same_workflow_context(left: str, right: str) -> bool:
        if left == right:
            return True
        shorter, longer = sorted((left, right), key=len)
        if len(shorter) < 6 or not longer.startswith(shorter):
            return False
        return longer[len(shorter):len(shorter) + 1] in {":", "/", "-", "_"}

    for idx, request in enumerate(potential_steps):
        if _request_role_key(request) not in preread_keys:
            continue
        request_context = _workflow_context_values_for_request(request)
        request_transaction = _request_transaction_id(request)
        for write_position in write_positions:
            write_request = potential_steps[write_position]
            write_transaction = _request_transaction_id(write_request)
            context_match = any(
                same_workflow_context(candidate, write_value)
                for candidate in request_context
                for write_value in write_context_by_position.get(write_position, set())
            )
            same_transaction = bool(
                request_transaction
                and request_transaction == write_transaction
            )
            # When both sides have Observer transaction IDs, they are the
            # authoritative operation boundary. Semantic context is only the
            # fallback for older/incomplete recordings.
            if (same_transaction and _request_precedes(request, write_request)) or (
                context_match and not (request_transaction and write_transaction)
                and _request_precedes(request, write_request)
            ):
                owners_by_position.setdefault(idx, set()).add(write_position)
    position_by_request_id = {
        str(request.get("request_id") or ""): index
        for index, request in enumerate(potential_steps)
        if request.get("request_id")
    }
    for candidate in record_hydration_candidates:
        source_pos = position_by_request_id.get(str(candidate.get("source_request_id") or ""))
        target_pos = position_by_request_id.get(str(candidate.get("target_request_id") or ""))
        if (
            source_pos is not None
            and target_pos in write_positions
            and _request_role_key(potential_steps[source_pos]) in preread_keys
        ):
            owners_by_position.setdefault(source_pos, set()).add(target_pos)
    # Exact response-row keys matching a later request object is machine
    # evidence that the read controls the write's request shape. Keep that
    # source in the preflight closure so Pi can propose and verify the richer
    # response_key_map contract instead of freezing recorded dynamic keys.
    for candidate in discover_response_key_maps(captured_requests):
        source_pos = position_by_request_id.get(str(candidate.get("source_request_id") or ""))
        target_pos = position_by_request_id.get(str(candidate.get("target_request_id") or ""))
        if (
            source_pos is not None
            and target_pos in write_positions
            and _request_role_key(potential_steps[source_pos]) in preread_keys
            and _request_precedes(potential_steps[source_pos], potential_steps[target_pos])
        ):
            owners_by_position.setdefault(source_pos, set()).add(target_pos)
    try:
        potential_links = discover_step_links(potential_steps)
    except Exception:
        potential_links = []
    changed = True
    while changed:
        changed = False
        for link in potential_links:
            source_pos = link.get("source_step")
            target_pos = link.get("target_step")
            if not isinstance(source_pos, int) or not isinstance(target_pos, int):
                continue
            target_owners = owners_by_position.get(target_pos, set())
            if (
                target_owners
                and 0 <= source_pos < len(potential_steps)
                and _request_role_key(potential_steps[source_pos]) in preread_keys
            ):
                before = len(owners_by_position.get(source_pos, set()))
                owners_by_position.setdefault(source_pos, set()).update(target_owners)
                changed = changed or len(owners_by_position[source_pos]) != before
            # 控制前置链：某个已选 workflow GET 的响应驱动后续 workflow GET query
            # 时，二者共同属于写能力前置闭包，即使后者响应不直接进入 POST body。
            if (
                owners_by_position.get(source_pos)
                and isinstance(target_pos, int)
                and 0 <= target_pos < len(potential_steps)
                and _request_role_key(potential_steps[target_pos]) in preread_keys
            ):
                before = len(owners_by_position.get(target_pos, set()))
                owners_by_position.setdefault(target_pos, set()).update(owners_by_position[source_pos])
                changed = changed or len(owners_by_position[target_pos]) != before
    required_positions = set(owners_by_position)
    selected_preread_keys = {
        _request_role_key(potential_steps[idx])
        for idx in required_positions
        if 0 <= idx < len(potential_steps) and _request_role_key(potential_steps[idx]) in preread_keys
    }
    preflight_owner_request_keys = {
        _request_role_key(potential_steps[position]): {
            _request_role_key(potential_steps[owner])
            for owner in owners
            if owner in write_positions
        }
        for position, owners in owners_by_position.items()
        if position not in write_positions and owners
    }
    observer_command_anchors = any(
        _request_has_command_anchor(request) for request in captured_requests
    )
    explicitly_approved_business_keys = {
        _request_role_key(request)
        for request in captured_requests
        if (
            isinstance(request_role_overrides.get(str(request.get("request_id") or "")), dict)
            and request_role_overrides[str(request.get("request_id") or "")].get("role") == "business_get"
            and request_role_overrides[str(request.get("request_id") or "")].get("keep") is True
        )
    }
    independent_business_keys = {
        _request_role_key(request)
        for request in preread_cands
        if str((role_by_key.get(_request_role_key(request)) or {}).get("role") or "") == "business_get"
        and _request_role_key(request) not in post_write_read_keys
        and (
            _request_role_key(request) in explicitly_approved_business_keys
            # A visible click alone is not enough to publish a read as an
            # independent capability: opening a create/edit form commonly
            # loads workflow definitions, approval nodes and other support
            # data.  Query-like commands remain public below; support reads
            # are materialized only when the dependency closure proves that
            # a selected write consumes them.
            or _has_query_action_evidence(
                request.get("trigger_op"),
                " ".join(filter(None, (
                    str(request.get("trigger_action_id") or ""),
                    str(request.get("trigger_locator") or ""),
                ))),
            )
            or (
                not _request_has_command_anchor(request)
                and _request_transaction_id(request)
                and _business_filter_count(request) > 0
                and _BUSINESS_QUERY_PATH_RE.search(_request_path(request))
                and float((role_by_key.get(_request_role_key(request)) or {}).get("confidence") or 0.0) >= 0.9
            )
            or (
                not observer_command_anchors
                and float((role_by_key.get(_request_role_key(request)) or {}).get("confidence") or 0.0) >= 0.9
            )
        )
    }
    for index, (request, role_info) in enumerate(zip(captured_requests, request_roles)):
        request_key = _request_role_key(request)
        stable_role = dict(role_info or {})
        if request_key in selected_write_keys and stable_role.get("role") == "submit_anchor":
            stable_role["role"] = "business_write"
        if request_key in preflight_owner_request_keys:
            stable_role.update({
                # Option identity is orthogonal to preflight ownership.  A
                # candidate endpoint can belong to the same command while it
                # still remains an option source rather than a context read.
                "role": (
                    "read_option"
                    if stable_role.get("role") == "read_option"
                    else "read_context"
                ),
                "keep": True,
                "filter_reason": "",
                "confidence": max(float(stable_role.get("confidence") or 0.0), 0.9),
            })
        request_roles[index] = stable_role
    role_by_key = {
        _request_role_key(request): role
        for request, role in zip(captured_requests, request_roles)
    }
    fact_check_read_keys: set[Any] = set()
    for write_request in write_cands:
        write_samples = dict(samples)
        body = _parse_body(write_request.get("post_data"))
        if body is not None:
            for path, tokens, _string_value, raw_value in _leaf_paths(body):
                key = str(tokens[-1]) if tokens else path
                write_samples.setdefault(key, raw_value)
        fact_check = suggest_fact_check(
            write_samples,
            flow_reads,
            write_request=write_request,
        )
        source_request_id = str((fact_check or {}).get("source_request_id") or "")
        source_sequence = (fact_check or {}).get("source_sequence")
        source_endpoint = str((fact_check or {}).get("endpoint") or "")
        source_request = next((
            request for request in captured_requests
            if (
                source_request_id
                and str(request.get("request_id") or "") == source_request_id
            ) or (
                not source_request_id
                and source_sequence is not None
                and _request_order_value(request) == float(source_sequence)
                and str(request.get("url") or request.get("path") or "") == source_endpoint
            )
        ), None)
        if source_request is not None:
            fact_check_read_keys.add(_request_role_key(source_request))
    # A same-transaction read emitted after a write is not a preflight, but it
    # is still an executable verification step. Keep it materialized so the
    # owning write can bind fact_check without moving that assertion onto the
    # read itself.
    selected_keys = (
        selected_write_keys
        | selected_preread_keys
        | independent_business_keys
        | fact_check_read_keys
    )
    request_facts = _build_request_facts(
        captured_requests,
        request_roles,
        selected_keys,
        diagnostics=diagnostics,
        page_enum_options=page_enum_options,
        page_events=page_events,
        field_evidence=field_evidence,
    )
    cands = _dedupe_request_identities([
        r for r in captured_requests if _request_role_key(r) in selected_keys
    ])

    # 4) 每条 → FlowStep
    step_objs: list[FlowStep] = []
    idx_to_step_id: dict[int, str] = {}
    step_by_request_key: dict[Any, FlowStep] = {}
    for pos, req in enumerate(cands):
        request_role = role_by_key.get(_request_role_key(req), {})
        st = _build_step_from_capture(
            _attach_request_role(req, request_role),
            reads=flow_reads,
            samples=_samples_for_captured_request(
                req,
                samples=samples,
                form_samples_by_request=form_samples_by_request,
                form_samples_by_transaction=form_samples_by_transaction,
            ),
            storage_state=storage_state,
            required_labels=required_labels,
            page_enum_options=page_enum_options,
            field_evidence=field_evidence,
            step_index=pos,
        )
        if _request_role_key(req) in selected_preread_keys:
            st.source_meta = {
                **(st.source_meta or {}),
                "control_preflight_for_write": True,
            }
        if _request_role_key(req) in fact_check_read_keys:
            st.source_meta = {
                **(st.source_meta or {}),
                "verification_read": True,
                "actor": "heuristic",
            }
        step_objs.append(st)
        step_by_request_key[_request_role_key(req)] = st
        idx_to_step_id[pos] = st.step_id
        request_id = _request_fact_key(_request_fact_entry(req, request_role))
        usage = request_facts.usage.get(request_id) or RequestUsage(request_id=request_id)
        usage.materialized_step_id = st.step_id
        usage.state = "materialized"
        request_facts.usage[request_id] = usage
    step_id_by_request_id = {
        str((step.source_meta or {}).get("request_id") or ""): step.step_id
        for step in step_objs
        if str((step.source_meta or {}).get("request_id") or "")
    }
    for item in getattr(request_facts, "field_evidence", []) or []:
        if not isinstance(item, dict) or item.get("binding_status") != "bound":
            continue
        step_id = step_id_by_request_id.get(str(item.get("request_id") or ""))
        if step_id:
            item["step_id"] = step_id
    for request_key, owner_request_keys in preflight_owner_request_keys.items():
        step = step_by_request_key.get(request_key)
        if step is None:
            continue
        owner_step_ids = [
            owner.step_id
            for owner_key in owner_request_keys
            if (owner := step_by_request_key.get(owner_key)) is not None
        ]
        step.source_meta = {
            **(step.source_meta or {}),
            "control_preflight_for_write": bool(owner_step_ids),
            "control_preflight_for_write_ids": owner_step_ids,
        }
    hydration_owner_ids: dict[str, list[str]] = {}
    for candidate in record_hydration_candidates:
        source_request_id = str(candidate.get("source_request_id") or "")
        target_step_id = step_id_by_request_id.get(
            str(candidate.get("target_request_id") or ""),
        )
        if source_request_id and target_step_id:
            hydration_owner_ids.setdefault(source_request_id, []).append(target_step_id)
    for source_request_id, owner_step_ids in hydration_owner_ids.items():
        source_step_id = step_id_by_request_id.get(source_request_id)
        source_step = next((
            step for step in step_objs if step.step_id == source_step_id
        ), None)
        if source_step is not None:
            source_step.source_meta = {
                **(source_step.source_meta or {}),
                "record_hydration_for_write_ids": list(dict.fromkeys(owner_step_ids)),
            }
            identity_leaves = {
                re.sub(
                    r"[^a-z0-9]+", "", str(path).split(".")[-1].casefold(),
                )
                for candidate in record_hydration_candidates
                if str(candidate.get("source_request_id") or "") == source_request_id
                for path in candidate.get("identity_paths") or []
            }
            for param in source_step.params or []:
                leaf = re.sub(
                    r"[^a-z0-9]+", "",
                    str(param.path or param.key or "").split(".")[-1].casefold(),
                )
                if (
                    not str(param.path or "").startswith("query.")
                    or leaf not in identity_leaves
                    or param.locked
                    or _param_has_manual_contract(param)
                ):
                    continue
                param.category = "user_param"
                param.source_kind = "user_input"
                param.source = {
                    "kind": "selected_record_identity",
                    "path": param.path,
                    "required_state": "required",
                }
                param.required = True
                param.exposed_to_user = True
                param.editable = True
                param.need_human_confirm = False
                param.reason = "调用方提供要编辑的记录标识；详情接口据此读取其当前字段"
                source_step.sample_inputs[param.key] = param.value

    # 5) 多步 link（自动值驱动）
    link_objs: list[FlowLink] = []
    if len(step_objs) > 1:
        try:
            raw_links = discover_step_links(cands)
            for lk in raw_links:
                src_pos, tgt_pos = lk.get("source_step"), lk.get("target_step")
                if src_pos not in idx_to_step_id or tgt_pos not in idx_to_step_id:
                    continue
                target_step = step_objs[tgt_pos]
                target_path = _strip_body_prefix(str(lk.get("target_path", "")))
                target_param = next((p for p in target_step.params if p.path == target_path), None)
                target_value = str(target_param.value if target_param is not None else "")
                source_request = cands[src_pos]
                target_request = cands[tgt_pos]
                matching_sources: set[tuple[str, str, str]] = set()
                # 唯一性必须以完整请求事实库为准，不能只看已物化步骤；两个候选 GET
                # 返回同一 ID 时，即使去重后只保留一个步骤，也仍属于歧义证据。
                for candidate_request in captured_requests:
                    response_payload = candidate_request.get("response_json")
                    if response_payload is None:
                        continue
                    for response_path, _tokens, scalar, _raw in _leaf_paths(response_payload):
                        if target_value and str(scalar) == target_value:
                            matching_sources.add((
                                str(candidate_request.get("method") or "GET").upper(),
                                _request_path(candidate_request),
                                response_path,
                            ))
                selected_source = select_dependency_source(
                    matching_sources,
                    target_path=target_path,
                    target_method=str(target_request.get("method") or "GET"),
                    target_route=str(
                        target_request.get("url") or target_request.get("path") or ""
                    ),
                )
                current_source = (
                    str(source_request.get("method") or "GET").upper(),
                    _request_path(source_request),
                    str(lk.get("source_path") or "").removeprefix("response."),
                )
                strong_unique_match = (
                    len(target_value) >= 4
                    and selected_source == current_source
                    and target_value.lower() not in {"true", "false", "null", "none", "success"}
                )
                source_leaf = re.sub(
                    r"[^a-z0-9]+", "", str(lk.get("source_path") or "").split(".")[-1].lower()
                )
                target_leaf = re.sub(
                    r"[^a-z0-9]+", "", str(target_path or "").split(".")[-1].lower()
                )
                strong_id_dependency = strong_unique_match and source_leaf == "id" and target_leaf.endswith("id")
                source_action = str(source_request.get("trigger_action_id") or "")
                target_action = str(target_request.get("trigger_action_id") or "")
                causal_supported = bool(target_action)
                same_action_chain = bool(source_action and source_action == target_action)
                captured_dependency_match = bool(
                    strong_unique_match
                    and (
                        same_action_chain
                        # A repeated value may still be resolved across actions
                        # when route semantics select one source from multiple
                        # candidates.  A lone equal value across two unrelated
                        # observed actions remains insufficient evidence.
                        or len(matching_sources) > 1
                    )
                )
                editable_prefill_dependency = bool(
                    target_param is not None
                    and _param_has_editable_control_evidence(target_param)
                    and strong_unique_match
                    and same_action_chain
                    and _dependency_match_score(
                        target_param, str(lk.get("source_path") or "")
                    ) >= 40
                )
                if (
                    not strong_id_dependency
                    and not editable_prefill_dependency
                    and not _auto_dependency_link_allowed(
                        target_param, str(lk.get("source_path") or ""),
                    )
                ):
                    continue
                auto_confirmed = bool(strong_unique_match and (not page_events or causal_supported))
                link_objs.append(FlowLink(
                    source_step_id=idx_to_step_id[src_pos],
                    source_path=lk.get("source_path", ""),
                    source_tokens=lk.get("source_tokens"),
                    target_step_id=idx_to_step_id[tgt_pos],
                    target_path=lk.get("target_path", ""),
                    target_tokens=lk.get("target_tokens"),
                    param_name=None,
                    confirmed=auto_confirmed,
                    confidence=(0.98 if same_action_chain and strong_unique_match
                                else 0.96 if auto_confirmed else 0.9 if strong_unique_match else 0.85),
                    reason=(
                        "同一用户操作触发的请求链中，上游响应值与下游请求字段值唯一一致，判定为运行期依赖"
                        if same_action_chain else
                        "上游响应值与下游请求字段值唯一一致，且下游请求有操作锚点，判定为运行期依赖"
                        if auto_confirmed and page_events else
                        "上游响应值与下游请求字段值一致，但缺少操作因果锚点，保留为待确认依赖"
                        if strong_unique_match and page_events else
                        "上游响应值与下游请求字段值一致，判定为运行期依赖"
                    ),
                    evidence={
                        "source_step": src_pos,
                        "target_step": tgt_pos,
                        "source_path": lk.get("source_path", ""),
                        "target_path": lk.get("target_path", ""),
                        "source_action_id": source_action,
                        "target_action_id": target_action,
                        "same_action_chain": same_action_chain,
                        "observer_available": bool(page_events),
                        **({
                            "captured_value_match": {
                                "occurrences": 1,
                                "source_identity": list(selected_source or ()),
                                "disambiguation": "route_semantics",
                                "candidate_count": len(matching_sources),
                            },
                        } if captured_dependency_match else {}),
                    },
                    meta={
                        "actor": "heuristic",
                        "verified": False,
                        **({"captured_value_match": True} if captured_dependency_match else {}),
                    },
                ))
        except Exception:
            link_objs = []
    for candidate in record_hydration_candidates:
        source_step_id = step_id_by_request_id.get(
            str(candidate.get("source_request_id") or ""),
        )
        target_step_id = step_id_by_request_id.get(
            str(candidate.get("target_request_id") or ""),
        )
        if not source_step_id or not target_step_id:
            continue
        for match in candidate.get("matches") or []:
            source_path = str(match.get("source_path") or "")
            target_path = str(match.get("target_path") or "")
            signature = (source_step_id, source_path, target_step_id, target_path)
            existing_link = next((
                link for link in link_objs
                if (
                    link.source_step_id, link.source_path,
                    link.target_step_id, link.target_path,
                ) == signature
            ), None)
            evidence = {
                "kind": "record_hydration",
                "source_request_id": candidate.get("source_request_id"),
                "target_request_id": candidate.get("target_request_id"),
                "identity_paths": list(candidate.get("identity_paths") or []),
                "match_count": len(candidate.get("matches") or []),
                "captured_source_value": copy.deepcopy(match.get("source_value")),
                "captured_target_value": copy.deepcopy(match.get("target_value")),
                "value_overridden": bool(match.get("value_overridden")),
                "empty_projection": bool(match.get("empty_projection")),
            }
            meta = {
                "actor": "heuristic",
                "captured_record_hydration": True,
            }
            if existing_link is not None:
                existing_link.confirmed = True
                existing_link.confidence = max(float(existing_link.confidence or 0.0), 0.99)
                existing_link.reason = "同一记录详情对象的多个同名字段被后续写请求原样采用"
                existing_link.evidence = evidence
                existing_link.meta = {**dict(existing_link.meta or {}), **meta}
                continue
            link_objs.append(FlowLink(
                source_step_id=source_step_id,
                source_path=source_path,
                target_step_id=target_step_id,
                target_path=target_path,
                confirmed=True,
                confidence=0.99,
                reason="同一记录详情对象的多个同名字段被后续写请求原样采用",
                evidence=evidence,
                meta=meta,
            ))
    _materialize_captured_response_key_maps(
        step_objs, link_objs, captured_requests,
    )
    _sync_link_sources(step_objs, link_objs)

    # 6) 流程整体风险
    overall = "L1"
    for st in step_objs:
        rl = st.risk_level
        if rl == "L4":
            overall = "L4"
            break
        if rl == "L3" and overall != "L4":
            overall = "L3"

    # 7) fact_check — capability/write scoped and causally after the write.
    # A value collision in a user/dictionary endpoint is not verification, and
    # a follow-up GET step must not own the write's check merely because it is
    # last in the workflow.
    for step in step_objs:
        step.fact_check = None
        if (step.method or "").upper() not in {"POST", "PUT", "PATCH"}:
            continue
        meta = step.source_meta or {}
        step_samples = dict(step.sample_inputs or {})
        if not step_samples:
            step_samples = {
                param.key: param.value
                for param in step.params
                if param.key and param.value not in (None, "") and _param_exposed_to_caller(param)
            }
        fc = suggest_fact_check(
            step_samples,
            flow_reads,
            write_request={
                "method": step.method,
                "url": step.url or step.path,
                "sequence": meta.get("sequence", meta.get("request_index")),
                "request_index": meta.get("request_index"),
                "trigger_transaction_id": meta.get("trigger_transaction_id"),
            },
        )
        if fc:
            step.fact_check = fc

    # 8) title
    title = _derive_title(step_objs, extra_contexts=[page_context])

    spec = FlowSpec(
        tenant=tenant,
        subsystem=subsystem,
        title=title,
        business_description="",
        recording_mode=recording_mode,
        diagnostics=diagnostics,
        steps=step_objs,
        links=link_objs,
        goal={},
        risk_level=overall,
        request_facts=request_facts,
        meta={
            "captured_total": len(captured_requests),
            "captured_write_candidates": len(write_cands),
            "captured_business_gets": len([r for r in request_roles if r.get("role") == "business_get"]),
            "captured_preread_candidates_before_dedupe": preread_before_dedupe,
            "captured_preread_candidates": len(preread_cands),
            "captured_workflow_steps": len(step_objs),
            "reads_count": len(flow_reads),
            "request_roles": request_roles,
            "recording_mode": recording_mode,
            "diagnostics": diagnostics,
            "page_events_count": len(page_events),
            "page_context": page_context,
            "field_evidence": list(field_evidence or []),
            "schema_version": 1,
        },
    )
    _mark_repeated_write_observations(spec)
    # ponytail: reuse the existing grounded matcher before the first projection.
    _repair_structural_option_bindings(spec)
    _apply_mechanical_field_contracts(spec)
    return ensure_flow_version(refresh_review_items(ensure_recorded_goal(spec)), "recorded", reason="录制生成 FlowSpec 初版")


























































































































def _apply_mechanical_field_contracts(spec: FlowSpec) -> None:
    """Apply the same origin/ownership rules to every capability family."""
    _infer_selected_option_row_fields(spec)
    _infer_computed_runtime_fields(spec)
    _apply_create_form_field_contracts(spec)
    _apply_edit_form_field_contracts(spec)
    _apply_row_command_field_contracts(spec)
    _apply_query_form_field_contracts(spec)
    _apply_successful_omit_optional(spec)
    _apply_page_rule_caller_override(spec)


















































def _recorded_goal_from_parts(title: str, steps: list[FlowStep], risk_level: str) -> dict[str, Any]:
    write_steps = [s for s in steps if _is_write_step(s)]
    read_steps = [s for s in steps if not _is_write_step(s)]
    params: list[str] = []
    for st in steps:
        for p in st.params:
            if _param_requires_caller_input(p) and p.key and p.key not in params:
                params.append(p.key)
    capabilities: list[str] = []
    if read_steps:
        capabilities.append("query_status")
    if any(st.selects or any(p.enum_options for p in st.params) for st in steps):
        capabilities.append("list_options")
    if write_steps:
        capabilities.append("submit_batch" if any(_looks_batch_step(s) for s in write_steps) else "submit")
    intent = title or (write_steps[-1].name if write_steps else (read_steps[-1].name if read_steps else "录制业务流程"))
    goal = RecordedGoal(
        intent=intent,
        required_inputs=params,
        success_criteria=[
            "所有必填业务字段都有确定来源",
            "提交接口返回成功规则通过" if write_steps else "查询接口返回可解析结果",
            "已纳入能力闭包的接口按依赖顺序执行",
        ],
        output_expectation=[
            "返回所调用能力的最终响应",
            "批量提交时返回 success_count、failed_items 和每条结果" if any(_looks_batch_step(s) for s in write_steps) else "返回执行状态和原始响应",
        ],
        forbidden_actions=list(_DEFAULT_RECORDED_FORBIDDEN_ACTIONS),
        risk_level=risk_level or "L3",
        capabilities=capabilities,
        evidence=[_step_evidence(s) for s in steps[:20]],
    )
    return goal.model_dump(exclude_none=True)


def _recorded_user_param_names(steps: list[FlowStep]) -> list[str]:
    """Required public inputs used by RecordedGoal.required_inputs."""
    params: list[str] = []
    for st in steps:
        for p in st.params:
            if _param_requires_caller_input(p) and p.key and p.key not in params:
                params.append(p.key)
    return params


def ensure_recorded_goal(spec: FlowSpec) -> FlowSpec:
    active_step_ids = _active_capability_step_ids(spec)
    goal_steps = [
        step for step in spec.steps
        if active_step_ids is None or step.step_id in active_step_ids
    ]
    fresh = _recorded_goal_from_parts(spec.title, goal_steps, spec.risk_level)
    if not spec.goal:
        spec.goal = fresh
        return spec
    goal = dict(spec.goal or {})
    # 字段改名/分类/暴露状态会改变最终 Skill 参数。Goal 的 required_inputs 必须跟当前
    # FlowSpec 保持一致，否则发布层会把旧字段名误判成“Agent 臆造字段”并阻断。
    current_inputs = _recorded_user_param_names(goal_steps)
    goal["required_inputs"] = current_inputs
    # Empty axes count as missing: agent set_goal payloads and legacy specs may
    # carry `[]`, and the publish completeness gate checks emptiness, not keys.
    if not goal.get("intent"):
        goal["intent"] = fresh.get("intent") or spec.title
    if not goal.get("success_criteria"):
        goal["success_criteria"] = fresh.get("success_criteria") or []
    if not goal.get("output_expectation"):
        goal["output_expectation"] = fresh.get("output_expectation") or []
    existing_forbidden = {
        str(item).strip() for item in (goal.get("forbidden_actions") or []) if str(item).strip()
    }
    if existing_forbidden == _LEGACY_RECORDED_FORBIDDEN_ACTIONS:
        # Migrate the old generic deny-list.  A recorded withdraw/delete action
        # must not produce a goal that forbids its own observed business step.
        goal["forbidden_actions"] = list(_DEFAULT_RECORDED_FORBIDDEN_ACTIONS)
    elif not existing_forbidden:
        goal["forbidden_actions"] = fresh.get("forbidden_actions") or []
    if not goal.get("risk_level"):
        goal["risk_level"] = fresh.get("risk_level") or spec.risk_level or "L3"
    actual_capabilities = [
        str(cap.name or cap.capability_id)
        for cap in (spec.capabilities or [])
        if str(cap.name or cap.capability_id)
    ]
    goal["capabilities"] = actual_capabilities if spec.capabilities else (fresh.get("capabilities") or [])
    goal["evidence"] = fresh.get("evidence") or goal.get("evidence") or []
    spec.goal = goal
    return spec










































































































































































































































































































































def _review_id(item_type: str, target: dict[str, Any]) -> str:
    parts = [
        item_type,
        str(target.get("step_id") or ""),
        str(target.get("path") or ""),
        str(target.get("link_id") or ""),
        str(target.get("request_index") or ""),
        str(target.get("capability") or target.get("capability_name") or target.get("capability_id") or ""),
        str(target.get("field") or ""),
    ]
    raw = "|".join(parts)
    safe = re.sub(r"[^a-zA-Z0-9_]+", "_", raw).strip("_").lower()
    return f"review_{safe[:96]}" if safe else f"review_{item_type}"


def _review_item(
    item_type: str,
    *,
    severity: str,
    title: str,
    target: dict[str, Any],
    current_guess: str = "",
    suggested_action: str = "",
    reason: str = "",
    confidence: float = 0.0,
    blocking: bool = False,
    ignorable: bool = True,
) -> ReviewItem:
    return ReviewItem(
        id=_review_id(item_type, target),
        type=item_type,
        severity=severity,
        title=title,
        target=target,
        current_guess=current_guess,
        suggested_action=suggested_action,
        reason=reason,
        confidence=confidence,
        blocking=blocking,
        ignorable=ignorable,
    )










def build_review_items(spec: FlowSpec) -> list[ReviewItem]:
    """把 FlowSpec 中的低置信/高风险判断整理成人工确认项。"""
    items: list[ReviewItem] = []
    active_step_ids = _active_capability_step_ids(spec)
    visible_steps = [
        step for step in spec.steps
        if active_step_ids is None or step.step_id in active_step_ids
    ]
    step_ids = {s.step_id for s in visible_steps}
    steps_by_id = {s.step_id: s for s in visible_steps}
    visible_request_indexes = {
        str(step.source_meta.get("request_index"))
        for step in visible_steps
        if step.source_meta.get("request_index") is not None
    }
    visible_request_paths = {
        _request_path({"path": step.path or step.url})
        for step in visible_steps
        if step.path or step.url
    }
    confirmed_dependency_sources = {
        link.source_step_id for link in spec.links
        if link.confirmed and link.source_step_id in step_ids and link.target_step_id in step_ids
    }

    # 来源建议属于能力合同的编辑反馈。尚未生成能力时，字段还没有发布
    # 边界和可定位的能力锚点，不能提前制造“待处理”告警。能力存在后仍
    # 覆盖未归属步骤，但用户明确删除能力时，其原步骤已退出编辑范围。
    if spec.capabilities:
        removed_step_ids = _retired_capability_step_ids(spec)
        for st in spec.steps:
            if st.step_id in removed_step_ids:
                continue
            for p in st.params:
                target = {
                    "kind": "param",
                    "step_id": st.step_id,
                    "step_name": st.name,
                    "path": p.path,
                    "key": p.key,
                    "param_type": p.type,
                    "category": p.category,
                    "source_kind": p.source_kind or "unknown",
                }
                guess = f"{p.category}/{p.source_kind}"
                source_unknown = str(p.source_kind or "").strip().lower() in {"", "unknown"}
                source_advice = _field_source_configuration_advice(p)
                if source_unknown:
                    items.append(_review_item(
                        "field_source_unknown",
                        severity="medium",
                        title=f"字段 {p.path} 的来源尚未识别",
                        target=target,
                        current_guess=guess,
                        suggested_action="configure_or_ignore_field_source",
                        # reason=(
                        #     "系统会保留当前类型、分类和来源组合，不会自动改写或阻止保存、优化、发布；"
                        #     "可补充明确来源，或确认当前人工配置后忽略此提示"
                        # ),
                        confidence=p.confidence,
                        blocking=False,
                        ignorable=True,
                    ))
                elif source_advice:
                    items.append(_review_item(
                        "field_source_incomplete",
                        severity="medium",
                        title=f"字段 {p.path} 的来源配置不完整",
                        target=target,
                        current_guess=guess,
                        suggested_action="configure_or_ignore_field_source",
                        reason=(
                            f"{source_advice}；系统会保留当前人工配置，"
                            "该提示可忽略且不会阻止保存、优化、发布"
                        ),
                        confidence=p.confidence,
                        blocking=False,
                        ignorable=True,
                    ))

    for st in visible_steps:
        for p in st.params:
            target = {
                "kind": "param",
                "step_id": st.step_id,
                "step_name": st.name,
                "path": p.path,
                "key": p.key,
                "param_type": p.type,
                "category": p.category,
                "source_kind": p.source_kind or "unknown",
            }
            guess = f"{p.category}/{p.source_kind}"

            source_unknown = str(p.source_kind or "").strip().lower() in {"", "unknown"}

            source_advice = _field_source_configuration_advice(p)

            if p.need_human_confirm and not source_unknown and not source_advice:
                items.append(_review_item(
                    "field_category",
                    severity="medium",
                    title=f"确认字段 {p.path} 的分类和来源",
                    target=target,
                    current_guess=guess,
                    suggested_action="confirm_field_source",
                    reason=p.reason or "该字段分类由规则推断，建议人工确认",
                    confidence=p.confidence,
                ))

            if p.category == "system_const" and p.exposed_to_user:
                items.append(_review_item(
                    "system_const_exposed",
                    severity="high",
                    title=f"隐藏系统常量 {p.path}",
                    target=target,
                    current_guess=guess,
                    suggested_action="hide_system_const",
                    reason="系统常量不应作为普通 Skill 入参暴露给 agent 或最终用户",
                    confidence=p.confidence,
                ))

    for lk in spec.links:
        # A capability-scoped request may compile only links whose two
        # endpoints are inside that capability's verified closure.  Including
        # a half-owned pending link made its outside endpoint look like a
        # missing step and blocked an otherwise valid ability.
        if active_step_ids is not None and not (
            lk.source_step_id in active_step_ids and lk.target_step_id in active_step_ids
        ):
            continue
        source_step = steps_by_id.get(lk.source_step_id)
        target_step = steps_by_id.get(lk.target_step_id)
        source_label = f"{source_step.name or source_step.path or source_step.url}" if source_step else lk.source_step_id
        target_label = f"{target_step.name or target_step.path or target_step.url}" if target_step else lk.target_step_id
        link_label = f"{source_label}.{lk.source_path} -> {target_label}.{lk.target_path}"
        target = {
            "kind": "link",
            "link_id": lk.link_id,
            "source_step_id": lk.source_step_id,
            "source_path": lk.source_path,
            "target_step_id": lk.target_step_id,
            "target_path": lk.target_path,
        }
        if lk.source_step_id not in step_ids or lk.target_step_id not in step_ids:
            items.append(_review_item(
                "broken_link",
                severity="high",
                title=f"修复断开的接口依赖 {link_label}",
                target=target,
                current_guess="invalid_link",
                suggested_action="fix_or_remove_link",
                reason="该 link 指向不存在的步骤，执行计划无法可靠生成",
                confidence=lk.confidence,
            ))
            continue

        source_path = lk.source_tokens or lk.source_path
        if source_step and source_step.response_json is not None and _flow_path_lookup(source_step.response_json, source_path) is _FLOW_PATH_MISSING:
            items.append(_review_item(
                "link_source_missing",
                severity="high",
                title=f"修复接口依赖来源 {source_label}.{lk.source_path}",
                target=target,
                current_guess="missing_source_path",
                suggested_action="fix_link_source",
                reason="该 link 的 source_path 在上游响应样例里不存在，运行期无法取到要注入的值",
                confidence=lk.confidence,
            ))

        target_path = _strip_body_prefix(lk.target_path)
        if target_step and target_path and not any(p.path == target_path or p.path == lk.target_path for p in target_step.params):
            items.append(_review_item(
                "link_target_missing",
                severity="high",
                title=f"修复接口依赖目标 {target_label}.{lk.target_path}",
                target=target,
                current_guess="missing_target_path",
                suggested_action="fix_link_target",
                reason="该 link 的 target_path 不在目标步骤字段中，运行期可能无法注入",
                confidence=lk.confidence,
            ))

        if not lk.confirmed:
            items.append(_review_item(
                "link_confirmation",
                severity="high",
                title=f"确认接口依赖 {link_label}",
                target=target,
                current_guess="previous_response",
                suggested_action="confirm_link",
                reason=lk.reason or "该 link 由响应值与请求值匹配自动生成，需要人工确认",
                confidence=lk.confidence,
            ))

    for role in spec.meta.get("request_roles") or []:
        role_index = str(role.get("index")) if role.get("index") is not None else ""
        role_path = _request_path({"path": str(role.get("path") or role.get("url") or "")})
        matched_step = next((
            step for step in visible_steps
            if (
                role_index
                and str(step.source_meta.get("request_index")) == role_index
            ) or (
                role_path
                and _request_path({"path": step.path or step.url}) == role_path
            )
        ), None)
        role_is_active = bool(
            matched_step
            or (role_index and role_index in visible_request_indexes)
            or (role_path and role_path in visible_request_paths)
        )
        confidence = float(role.get("confidence") or 0.0)
        needs_role_confirmation = bool(
            role.get("keep")
            and role.get("role") in {"business_get", "read_context"}
            and role_is_active
            and confidence < 0.9
            and not bool(matched_step and matched_step.source_meta.get("manual_added"))
            and not bool(matched_step and matched_step.source_meta.get("control_preflight_for_write"))
            and not bool(matched_step and matched_step.step_id in confirmed_dependency_sources)
        )
        if needs_role_confirmation:
            items.append(_review_item(
                "request_role",
                severity="medium",
                title=f"确认前置接口保留: {role.get('path') or role.get('url')}",
                target={
                    "kind": "request_role",
                    "request_index": role.get("index"),
                    "method": role.get("method"),
                    "path": role.get("path") or role.get("url"),
                },
                current_guess=str(role.get("role") or ""),
                suggested_action="confirm_request_role",
                reason=str(role.get("reason") or "该读接口被自动保留为流程前置步骤"),
                confidence=confidence,
            ))

    if visible_steps and not flow_spec_user_params(spec):
        items.append(_review_item(
            "no_user_param",
            severity="low",
            title="确认 Skill 是否不需要用户输入",
            target={"kind": "flow", "flow_id": spec.flow_id},
            current_guess="no_user_param",
            suggested_action="confirm_or_expose_param",
            reason="当前 FlowSpec 没有 user_param，发布后的 Skill 不会要求用户填写业务参数",
        ))

    if visible_steps and not any((st.success_rule for st in visible_steps)):
        items.append(_review_item(
            "success_rule_missing",
            severity="medium",
            title="补充成功判断规则",
            target={"kind": "flow", "flow_id": spec.flow_id},
            current_guess="missing_success_rule",
            suggested_action="add_success_rule",
            reason="未识别到明确 success_rule，运行期只能使用通用成功判断",
        ))

    deduped: dict[str, ReviewItem] = {}
    for item in items:
        existing = deduped.get(item.id)
        if existing is None or _severity_rank(item.severity) > _severity_rank(existing.severity):
            deduped[item.id] = item
    return list(deduped.values())


def _severity_rank(severity: str) -> int:
    return {"low": 1, "medium": 2, "high": 3}.get(severity, 0)














def refresh_review_items(spec: FlowSpec, *, prepared: bool = False) -> FlowSpec:
    """重建 review_items，并保留同 id 项的已解决状态。

    ID 是稳定 hash(target)，所以同一字段/同一依赖在重建前后 ID 不变，
    用户的 resolved 标记会随 ID 一起被复用，告警不会因为字段重渲染而复活。
    """
    for step in spec.steps:
        _dedupe_step_params(step)
    old_resolved: dict[str, bool] = {}
    legacy_source_resolved: dict[tuple[str, str, str], bool] = {}
    for item in spec.review_items:
        # id 已是 target 的稳定 hash；同字段前后 ID 一致，resolved 跟着保留。
        old_resolved.setdefault(item.id, item.resolved)
        # Preserve dismissals while migrating the two legacy runtime-only
        # source warnings to category-agnostic field source review items.
        legacy_type = {
            "runtime_var_source": "field_source_unknown",
            "runtime_var_missing_source": "field_source_incomplete",
        }.get(item.type)
        if legacy_type:
            target_key = (
                legacy_type,
                str(item.target.get("step_id") or ""),
                str(item.target.get("path") or ""),
            )
            legacy_source_resolved.setdefault(target_key, item.resolved)
    spec.review_items = _generated_review_items(spec, prepared=prepared)
    for item in spec.review_items:
        if item.id in old_resolved:
            item.resolved = old_resolved[item.id]
        elif item.type in {"field_source_unknown", "field_source_incomplete"}:
            target_key = (
                item.type,
                str(item.target.get("step_id") or ""),
                str(item.target.get("path") or ""),
            )
            if target_key in legacy_source_resolved:
                item.resolved = legacy_source_resolved[target_key]
    return spec












# ─────────── P0-0: FlowSpec → 可发布 api_request ───────────


















































































































def validate_flow_spec(spec: FlowSpec) -> dict:
    from dano.execution.page.repair_ops import collect_repair_findings

    # 校验只面对规范化后的当前事实。字段、接口顺序或能力范围改变后产生的旧
    # input/map/return/link 由同步层确定性清理，不能继续作为“用户待处理”告警。
    spec = prepare_flow_spec_for_publish(spec)
    for capability in spec.capabilities or []:
        capability.nodes = _sanitize_capability_nodes(spec, capability)
    spec = _prune_empty_capabilities(spec)
    _normalize_capability_references(spec)
    errors: list[str] = []
    warnings: list[str] = []
    suggestions: list[str] = []
    active_step_ids = _active_capability_step_ids(spec)
    active_steps = [
        step for step in spec.steps
        if active_step_ids is None or step.step_id in active_step_ids
    ]
    review_items = refresh_review_items(
        spec.model_copy(deep=True), prepared=True,
    ).review_items
    blocking_reviews = [
        item for item in review_items
        if item.severity == "high" and not item.resolved and item.type in _PUBLISH_BLOCKING_REVIEW_TYPES
    ]
    # Review items and capability/field classifications are generator output,
    # not publish policy.  Keep them available to the editor as suggestions but
    # never place them in the publish issue list: doing so made a model guess
    # look like a mandatory system rule.
    suggestions.extend([f"生成建议: {item.title}" for item in blocking_reviews])
    diag_errors, diag_warnings = _diagnostic_publish_findings(spec)
    suggestions.extend([*diag_errors, *diag_warnings])
    capability_validation = _capability_validation_report(spec, prepared=True)
    capability_errors = list(capability_validation.get("errors") or [])
    capability_warnings = list(capability_validation.get("warnings") or [])
    # Capability validation describes the executable public contract.  A
    # malformed boundary or illegal membership cannot be repaired by the
    # lower-level request builder, so it is a hard publish error.
    errors.extend(capability_errors)
    suggestions.extend(capability_warnings)
    by_step_id = {step.step_id: step for step in spec.steps}
    for capability in spec.capabilities or []:
        cap_label = capability.title or capability.name or capability.capability_id
        caller_params = [
            param
            for step_id in _capability_node_step_ids(capability)
            for param in (by_step_id.get(step_id).params if by_step_id.get(step_id) else [])
            if _param_exposed_to_caller(param)
        ]
        caller_by_key: dict[str, list[ParamField]] = {}
        for param in caller_params:
            caller_by_key.setdefault(str(param.key or param.path or ""), []).append(param)
        for field_name, duplicates in caller_by_key.items():
            if len(duplicates) > 1 and any(
                not _params_can_share_caller_key(duplicates[0], other)
                for other in duplicates[1:]
            ):
                suggestions.append(f"Capability `{cap_label}` 输入字段 `{field_name}` 同名但对应不同请求字段，建议重命名或解除锁定后自动消歧")
        for field_name, field_schema in (capability.input_schema.get("properties") or {}).items():
            if isinstance(field_schema, dict) and field_schema.get("x-dano-conflicts"):
                suggestions.append(f"Capability `{cap_label}` 输入字段 `{field_name}` 在多个接口中类型或路径冲突")
        if capability.kind == "query_status":
            cap_steps = [by_step_id[sid] for sid in _capability_node_step_ids(capability) if sid in by_step_id]
            if cap_steps and not any(_is_business_query_step(step) for step in cap_steps):
                suggestions.append(f"Capability `{cap_label}` 没有返回业务记录/状态的查询接口，仅包含配置或前置接口")
    api_request, build_errors = flow_spec_to_api_request(spec, _prepared=True)
    errors.extend(build_errors)
    if not flow_spec_user_params(spec):
        suggestions.append("FlowSpec 没有 user_param，发布后的 Skill 不会要求用户输入参数")
    for st in active_steps:
        select_by_path = {s.path: s for s in st.selects if s.path}
        select_by_param = {s.param: s for s in st.selects if s.param}
        for p in st.params:
            enum_contract_error = _capability_param_enum_issue(p)
            if enum_contract_error:
                suggestions.append(f"枚举字段 `{p.key or p.path}` {enum_contract_error}")
            enum_contract_warning = _capability_param_enum_warning(p)
            if enum_contract_warning:
                suggestions.append(f"枚举字段 `{p.key or p.path}` {enum_contract_warning}")
            source_advice = _field_source_configuration_advice(p)
            if source_advice:
                suggestions.append(source_advice)
            if p.category == "runtime_var" and p.source_kind == "unknown":
                suggestions.append(f"字段 `{p.path}` 被判为 runtime_var，但来源仍需确认")
            if p.category == "system_const" and p.exposed_to_user:
                suggestions.append(f"字段 `{p.path}` 是 system_const，但仍暴露给用户")
            if p.source_kind == "api_option":
                sel = select_by_path.get(p.path) or select_by_param.get(p.key)
                if sel and sel.source_url and (sel.source_method or "GET").upper() not in {"GET", "HEAD"} and sel.source_role not in {
                    "business_get", "read_context", "read_option",
                }:
                    suggestions.append(
                        f"字段 `{p.key or p.path}` 的接口选项源 `{sel.source_method} {sel.source_url}` "
                        "未被识别为只读接口，运行期调用可能产生副作用"
                    )
            has_executable_api_options = p.source_kind == "api_option"
            if not has_executable_api_options and _param_looks_exposed_internal_value(p):
                suggestions.append(
                    f"字段 `{p.key or p.path}` 看起来是内部 ID/短码/空标识，不能直接暴露给用户；"
                    "请改为接口枚举映射或系统常量"
                )
            if (
                p.type in {"enum", "list-enum"}
                and p.source_kind in {"page_enum", "static_enum", "manual_enum", "form_option"}
                and p.enum_options
                and not _enum_map_covers_recorded_value(p)
            ):
                suggestions.append(
                    f"枚举字段 `{p.key or p.path}` 当前提交值 `{p.value}` 没有完整 label→value 映射，"
                    "请补充真实选项值映射或重新录制到字典接口"
                )
            if (
                p.type in {"enum", "list-enum"}
                and p.source_kind in {"page_enum", "static_enum", "manual_enum", "form_option"}
                and p.enum_options
                and _enum_options_look_value_only(p)
            ):
                suggestions.append(
                    f"枚举字段 `{p.key or p.path}` 的候选看起来全是内部值/短码，"
                    "不能作为用户可选项导出；请填写 `显示名=真实值`（如 `病假=2`）或重新录制真实下拉"
                )
    for lk in spec.links:
        if active_step_ids is not None and not (
            lk.source_step_id in active_step_ids and lk.target_step_id in active_step_ids
        ):
            continue
        if not lk.confirmed:
            suggestions.append(f"链接 `{lk.link_id}` 尚未人工确认")
    if active_steps and not any((st.success_rule for st in active_steps)):
        suggestions.append("未识别到明确 success_rule，运行期只能使用通用成功判断")
    self_check_errors: list[str] = []
    compiled_issue_groups: dict[str, list[dict[str, Any]]] = {}
    if api_request is not None:
        self_check_errors = self_check(api_request)
        suggestions.extend(self_check_errors)
        repair_findings = collect_repair_findings(api_request)
        compiled_issue_groups = _compiled_contract_issue_groups(
            spec,
            api_request,
            repair_findings,
            resolved_review_ids={item.id for item in review_items if item.resolved},
        )
        # 系统化:session_constant 仅当对应字段**真的被识别为 system_const/constant** 时才算发布阻断;
        # 若字段在 spec 里被标 runtime_var/unknown → 这部分错误让前端 review_items 兜底,
        # 避免一锅端。修复者应在 dynamic_run 时再注入。
        params_by_path: dict[str, dict] = {}
        for st in active_steps:
            for p in st.params:
                params_by_path[p.path] = p.model_dump() if hasattr(p, "model_dump") else p.dict()
        session_errors: list[str] = []
        for f in repair_findings:
            if f.get("kind") != "session_constant":
                continue
            detail = f.get("detail", "")
            path = (f.get("path") or [])
            path_str = ".".join(str(p) for p in path) if isinstance(path, (list, tuple)) else str(path)
            spec_field = params_by_path.get(path_str) or {}
            if spec_field.get("category") in ("runtime_var", "system_const"):
                continue
            session_errors.append(detail)
        suggestions.extend(session_errors)
    dry_run = dry_run_flow_spec(spec, _prepared=True)
    errors = list(dict.fromkeys(str(item) for item in errors if item))
    warnings = list(dict.fromkeys(str(item) for item in warnings if item))
    suggestions = list(dict.fromkeys(str(item) for item in suggestions if item))
    # Generated ReviewItems remain advisory. Field-source items are also
    # projected into the operator field-warning group for visibility, while
    # staying explicitly ignorable and outside the publish pass/fail decision.
    issue_groups = _publish_issue_groups(errors, warnings)
    field_source_issues = _field_source_review_issues(review_items)
    if field_source_issues:
        issue_groups.setdefault("field", []).extend(field_source_issues)
    enum_mapping_issues = _enum_mapping_issues(active_steps)
    if enum_mapping_issues:
        issue_groups.setdefault("field", []).extend(enum_mapping_issues)
    for group, items in compiled_issue_groups.items():
        issue_groups.setdefault(group, []).extend(items)
    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "suggestions": suggestions,
        "issue_groups": issue_groups,
        "dry_run": dry_run,
        "review_items": [item.model_dump() for item in review_items],
        "review_summary": {
            "total": len(review_items),
            "high": len([i for i in review_items if i.severity == "high"]),
            "medium": len([i for i in review_items if i.severity == "medium"]),
            "low": len([i for i in review_items if i.severity == "low"]),
        },
        "self_check": self_check_errors,
        "api_preview": {
            "workflow_steps": len(api_request.get("steps") or []) if api_request else 0,
            "method": api_request.get("method") if api_request else None,
            "path": api_request.get("path") if api_request else None,
            "params": flow_spec_user_params(spec),
            "required": flow_spec_required_params(spec),
        },
        "capability_preview": [
            {
                "name": c.name,
                "kind": c.kind,
                "step_ids": c.step_ids,
                "nodes": c.nodes,
                "confirmed": c.confirmed,
                "requires_human_confirm": c.requires_human_confirm,
                "confidence": c.confidence,
                "status": c.status,
            }
            for c in (spec.capabilities or [])
        ],
        "capability_validation": capability_validation,
    }




















# ─────────── Step B+C: 编辑函数 ───────────
def _find_step(spec: FlowSpec, step_id: str) -> FlowStep:
    for step in spec.steps:
        if step.step_id == step_id:
            return step
    available = [s.step_id for s in spec.steps]
    raise ValueError(f"step not found: {step_id} (available: {available})")


def _find_param(step: FlowStep, param_path: str, *, field_id: str = "", param_key: str = "", param_label: str = "") -> ParamField:
    stable_id = str(field_id or "")
    if stable_id:
        matched = next((param for param in step.params if param.field_id == stable_id), None)
        if matched is not None:
            return matched
    needle = str(param_path or "")
    for param in step.params:
        if param.path == needle:
            return param
    available = [f"{p.path}({p.key})" for p in step.params]
    raise ValueError(f"param not found: {param_path} in step {step.step_id}; available={available}")


def _resolve_param_reference(step: FlowStep, reference_path: str) -> ParamField | None:
    """Resolve legacy body-prefixed paths without collapsing distinct fields."""
    reference = str(reference_path or "")
    if not reference:
        return None
    exact = next((param for param in step.params if param.path == reference), None)
    if exact is not None:
        return exact
    normalized = _strip_body_prefix(reference)
    matches = [
        param for param in step.params
        if _strip_body_prefix(param.path) == normalized
    ]
    return matches[0] if len(matches) == 1 else None


def _reference_targets_param(step: FlowStep, reference_path: str, param: ParamField) -> bool:
    return _resolve_param_reference(step, reference_path) is param


def _find_link(spec: FlowSpec, link_id: str) -> FlowLink:
    for link in spec.links:
        if link.link_id == link_id:
            return link
    available = [link.link_id for link in spec.links]
    raise ValueError(f"link not found: {link_id} (available: {available})")


def _validate_link_endpoint(spec: FlowSpec, step_id: str, label: str) -> None:
    if not any(s.step_id == step_id for s in spec.steps):
        raise ValueError(f"{label} step not found: {step_id}")


def _ensure_unique_link(spec: FlowSpec, link: FlowLink) -> None:
    dup = any(
        existing.source_step_id == link.source_step_id
        and existing.target_step_id == link.target_step_id
        and existing.source_path == link.source_path
        and existing.target_path == link.target_path
        and existing.link_id != link.link_id
        for existing in spec.links
    )
    if dup:
        raise ValueError("duplicate link (same source/target/path exists)")


def _matching_link(spec: FlowSpec, link: FlowLink) -> FlowLink | None:
    for existing in spec.links:
        if (
            existing.source_step_id == link.source_step_id
            and existing.target_step_id == link.target_step_id
            and _strip_body_prefix(existing.source_path) == _strip_body_prefix(link.source_path)
            and _strip_body_prefix(existing.target_path) == _strip_body_prefix(link.target_path)
            and existing.link_id != link.link_id
        ):
            return existing
    return None


def _merge_link(existing: FlowLink, incoming: FlowLink) -> None:
    existing.confirmed = bool(existing.confirmed or incoming.confirmed)
    existing.confidence = max(float(existing.confidence or 0), float(incoming.confidence or 0))
    existing.reason = incoming.reason or existing.reason
    existing.locked = bool(getattr(existing, "locked", False) or getattr(incoming, "locked", False))
    if incoming.param_name:
        existing.param_name = incoming.param_name


def _remove_step(spec: FlowSpec, step_id: str) -> None:
    step = _find_step(spec, step_id)
    spec.steps.remove(step)
    spec.links = [
        lk for lk in spec.links
        if lk.source_step_id != step_id and lk.target_step_id != step_id
    ]
    spec.review_items = [
        item for item in spec.review_items
        if item.target.get("step_id") != step_id
        and item.target.get("source_step_id") != step_id
        and item.target.get("target_step_id") != step_id
    ]













































































def _rename_param_public_key(
    spec: FlowSpec,
    step: FlowStep,
    param: ParamField,
    new_key: str,
    *,
    actor: str,
) -> None:
    """Atomically rename a caller-facing field without touching its wire path.

    ``ParamField.path`` is the executable request contract. ``key``/``label``
    are the public business name.  Keeping the mutation here prevents model
    naming, manual naming and capability-schema regeneration from drifting into
    three different representations of the same field.
    """
    proposed = str(new_key or "").strip()
    if not proposed:
        raise ValueError("field key cannot be empty")
    if proposed == param.key:
        return
    if any(other is not param and other.key == proposed for other in step.params):
        raise ValueError(f"duplicate param key: {proposed}")

    old_key = param.key
    param.key = proposed
    param.label = proposed
    if actor == "user":
        param.name_source = "manual"
        evidence_source = "manual_edit"
    else:
        # A model proposal is useful semantic evidence, not an operator lock.
        # It must remain editable and must never self-confirm its own decision.
        param.name_source = "planner" if actor == "planner" else actor
        evidence_source = f"{actor}_proposal"
    param.evidence.append({
        "source": evidence_source,
        "field": "key",
        **({"axis": "name", "status": "locked", "kind": "manual_override"} if actor == "user" else {}),
        "previous": old_key,
        "value": proposed,
    })

    if old_key in step.sample_inputs:
        step.sample_inputs[proposed] = step.sample_inputs.pop(old_key)
    elif param.value not in (None, ""):
        step.sample_inputs.setdefault(proposed, param.value)
    for binding in step.selects:
        if binding.path == param.path or binding.param == old_key:
            binding.param = proposed

    for capability in spec.capabilities or []:
        for collection_name in (
            "fields", "inputs", "request_fields", "internal_fields",
            "computed_fields", "outputs",
        ):
            for field in getattr(capability, collection_name, []) or []:
                same_wire_field = bool(
                    field.step_id == step.step_id
                    and _strip_body_prefix(field.path or "") == _strip_body_prefix(param.path)
                )
                if same_wire_field or (field.step_id == step.step_id and field.key == old_key):
                    field.key = proposed
                    if field.display_name in {"", old_key}:
                        field.display_name = proposed
        for relation in spec.capability_relations or []:
            if relation.to_capability in {capability.name, capability.capability_id} and relation.to_input == old_key:
                relation.to_input = proposed

        def rename_node_refs(nodes: list[dict[str, Any]]) -> None:
            old_ref = f"input.{old_key}"
            new_ref = f"input.{proposed}"
            for node in nodes or []:
                if not isinstance(node, dict):
                    continue
                for field_name in ("source", "items", "condition", "check"):
                    value = node.get(field_name)
                    if isinstance(value, str):
                        node[field_name] = value.replace(old_ref, new_ref)
                for child_name in ("children", "steps", "then", "else", "otherwise"):
                    if isinstance(node.get(child_name), list):
                        rename_node_refs(node[child_name])

        rename_node_refs(capability.nodes or [])


def apply_flow_edits(spec: FlowSpec, edits: list[dict[str, Any]]) -> FlowSpec:
    """应用编辑列表，返回新 FlowSpec（深拷贝）。"""
    if not edits:
        return refresh_review_items(spec.model_copy(deep=True))

    new_spec = spec.model_copy(deep=True)
    bulk_review_resolutions: list[tuple[set, set, bool]] = []
    needs_dependency_rebuild = False

    for edit in edits:
        op = edit.get("op")

        from dano.execution.page.recording_live import LIVE_RECORDING_AGENT_OPS, apply_recording_agent_edit
        if op in LIVE_RECORDING_AGENT_OPS:
            apply_recording_agent_edit(new_spec, edit)
            continue

        if op == "resolve_reviews":
            resolved = bool(edit.get("resolved", True))
            severities = set(edit.get("severities") or [])
            exclude_severities = set(edit.get("exclude_severities") or [])
            bulk_review_resolutions.append((severities, exclude_severities, resolved))
            generated = _generated_review_items(new_spec)
            old_by_id = {item.id: item for item in new_spec.review_items}
            for item in generated:
                if item.id in old_by_id:
                    item.resolved = old_by_id[item.id].resolved
                if severities and item.severity not in severities:
                    continue
                if exclude_severities and item.severity in exclude_severities:
                    continue
                item.resolved = resolved
            new_spec.review_items = generated
            continue

        if op == "resolve_review":
            item_id = str(edit.get("review_id") or "")
            if not item_id:
                raise ValueError("resolve_review missing review_id")
            found = False
            for item in new_spec.review_items:
                if item.id == item_id:
                    item.resolved = bool(edit.get("resolved", True))
                    found = True
                    break
            if not found:
                generated = _generated_review_items(new_spec)
                for item in generated:
                    if item.id == item_id:
                        item.resolved = bool(edit.get("resolved", True))
                        found = True
                        break
                if found:
                    new_spec.review_items = generated
            if not found:
                raise ValueError(f"review item not found: {item_id}")
            continue

        if op == "update_flow":
            field = str(edit.get("field") or "")
            value = edit.get("value")
            allowed = {"title", "business_description", "risk_level", "goal", "meta"}
            if field not in allowed:
                raise ValueError(f"unknown flow field: {field}")
            setattr(new_spec, field, value)
            actor = str(edit.get("actor") or "user")
            if field == "title" and actor != "planner":
                new_spec.meta = {**(new_spec.meta or {}), "title_source": "user"}
            if field == "business_description" and actor != "planner":
                new_spec.meta = {
                    **(new_spec.meta or {}),
                    "business_description_source": "user",
                }
            continue

        if op == "dedupe_steps":
            _dedupe_flow_steps(new_spec)
            continue

        # 重排步骤
        if op == "reorder_steps":
            order = edit.get("step_ids")
            if not isinstance(order, list):
                raise ValueError("reorder_steps missing step_ids list")
            existing_ids = {s.step_id for s in new_spec.steps}
            new_order_ids = set(order)
            if existing_ids != new_order_ids or len(order) != len(new_spec.steps):
                raise ValueError(
                    f"reorder_steps must include exactly all existing step_ids; "
                    f"got {sorted(new_order_ids)}, expected {sorted(existing_ids)}"
                )
            by_id = {s.step_id: s for s in new_spec.steps}
            new_spec.steps = [by_id[sid] for sid in order]
            continue

        if op == "remove_step":
            step_id = str(edit.get("step_id") or "")
            if not step_id:
                raise ValueError("remove_step missing step_id")
            _remove_step(new_spec, step_id)
            continue

        if op in {"add_candidate_step", "add_request_step"}:
            request_index = edit.get("request_index")
            request_id = str(edit.get("request_id") or "")
            promote_request_to_step(new_spec, request_index=request_index, request_id=request_id)
            needs_dependency_rebuild = True
            continue

        if op == "generate_capabilities":
            raise ValueError(
                "generate_capabilities is retired; submit a strict semantic plan"
            )

        if op == "add_capability":
            raw = dict(edit.get("capability") or {})
            raw.setdefault("name", _flow_capability_id(str(raw.get("kind") or "submit"), str(len(new_spec.capabilities) + 1)))
            raw.setdefault("title", raw["name"])
            raw.setdefault("kind", "submit")
            try:
                cap = FlowCapability.model_validate(raw)
            except ValidationError as e:
                raise ValueError(f"invalid capability data: {e}")
            if any(c.name == cap.name for c in new_spec.capabilities):
                raise ValueError(f"duplicate capability name: {cap.name}")
            _forget_removed_capability(new_spec, cap.name, cap.kind)
            new_spec.capabilities.append(cap)
            continue

        if op == "remove_capability":
            idx = _find_capability_index(new_spec, edit)
            cap = new_spec.capabilities.pop(idx)
            _remember_removed_capability(new_spec, cap.name, cap.kind)
            for step_id in _capability_node_step_ids(cap):
                _remember_removed_capability_step(new_spec, cap.name, step_id)
            removed_refs = {str(cap.name or ""), str(cap.capability_id or "")}
            new_spec.capability_relations = [
                relation for relation in (new_spec.capability_relations or [])
                if str(relation.from_capability or "") not in removed_refs
                and str(relation.to_capability or "") not in removed_refs
            ]
            continue

        if op == "reorder_capabilities":
            refs = edit.get("capability_refs")
            if refs is None:
                refs = edit.get("capability_names")
            if not isinstance(refs, list):
                raise ValueError("reorder_capabilities missing capability_refs list")

            def cap_ref(cap: FlowCapability, idx: int) -> str:
                return str(cap.name or cap.capability_id or f"idx:{idx}")

            by_ref = {cap_ref(c, i): c for i, c in enumerate(new_spec.capabilities)}
            current = set(by_ref)
            requested = {str(x) for x in refs}
            if current != requested or len(refs) != len(new_spec.capabilities):
                raise ValueError(
                    f"reorder_capabilities must include exactly all capability refs; "
                    f"got {sorted(requested)}, expected {sorted(current)}"
                )
            new_spec.capabilities = [by_ref[str(ref)] for ref in refs]
            continue

        if op == "update_capability":
            idx = _find_capability_index(new_spec, edit)
            actor = str(edit.get("actor") or "user").strip().lower()
            field = str(edit.get("field") or "")
            if field not in _CAPABILITY_ALLOWED_FIELDS:
                raise ValueError(f"unknown capability field: {field}")
            if field in {
                "step_ids", "request_refs", "fields", "inputs",
                "request_fields", "internal_fields", "outputs",
            }:
                raise ValueError(f"derived capability field is read-only: {field}")
            value = edit.get("value")
            cap = new_spec.capabilities[idx]
            if field == "name":
                value = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "")).strip("_").lower()
                if not value:
                    raise ValueError("capability name cannot be empty")
                if any(i != idx and c.name == value for i, c in enumerate(new_spec.capabilities)):
                    raise ValueError(f"duplicate capability name: {value}")
            if field in {"confirmed", "requires_human_confirm"}:
                value = bool(value)
            if field == "confidence":
                value = max(0.0, min(1.0, float(value or 0)))
            if field == "computed_fields":
                value = [CapabilityField.model_validate(x) for x in (value or [])]
                if any(item.step_id or item.scope != "computed" for item in value):
                    raise ValueError(
                        "computed_fields only accepts capability-level computed values"
                    )
            if field == "dependencies":
                value = [CapabilityDependency.model_validate(x) for x in (value or [])]
            if field == "confirmed" and value:
                # Confirmation records the operator's decision.  Capability
                # shape, field type/source and enum quality may be generated by
                # a model, so they must not veto that decision.  Executability
                # is checked later by the deterministic request compiler.
                value = True
            if field == "kind":
                _transition_capability_kind(new_spec, cap, value)
            else:
                setattr(cap, field, value)
            if field == "confirmed" and value:
                cap.requires_human_confirm = False
                cap.status = "confirmed"
                cap.confirmation_hash = _capability_confirmation_hash(new_spec, cap)
            elif field == "confirmed":
                cap.status = "draft"
                cap.confirmation_hash = ""
            elif field != "updated_by":
                cap.updated_by = actor
                if field in {
                    "name", "title", "intent", "kind", "request_refs", "step_ids", "nodes",
                    "fields", "inputs", "request_fields", "internal_fields", "computed_fields",
                    "outputs", "dependencies", "input_schema", "output_schema", "output_mapping",
                    "preconditions", "caller_responsibilities", "skill_responsibilities",
                }:
                    cap.confirmed = False
                    cap.confirmation_hash = ""
                    cap.status = "draft"
                    cap.requires_human_confirm = True
            if field in {"step_ids", "nodes"}:
                _sync_capability_order(new_spec, cap)
            continue

        if op == "upsert_capability":
            raw = dict(edit.get("capability") or {})
            actor = str(edit.get("actor") or "user")
            name = str(raw.get("name") or edit.get("capability_name") or edit.get("name") or "")
            if not name:
                raise ValueError("upsert_capability missing name")
            idx = next((i for i, c in enumerate(new_spec.capabilities) if c.name == name), -1)
            if idx < 0:
                raw.setdefault("name", name)
                raw.setdefault("title", raw["name"])
                raw.setdefault("kind", "submit")
                raw.setdefault("confidence", 0.7)
                raw.setdefault("requires_human_confirm", True)
                created = FlowCapability.model_validate(raw)
                created.updated_by = actor
                if actor == "planner":
                    created.confirmed = False
                    created.locked = False
                new_spec.capabilities.append(created)
            else:
                cap = new_spec.capabilities[idx]
                planner_protected = bool(
                    actor == "planner"
                    and (
                        cap.locked
                        or cap.updated_by == "user"
                        # Automatically accepted (>60%) planner drafts remain
                        # optimizable.  A legacy/manual confirmation without a
                        # planner provenance stays protected conservatively.
                        or (cap.confirmed and cap.updated_by != "planner")
                    )
                )
                for key, value in raw.items():
                    if key not in _CAPABILITY_ALLOWED_FIELDS:
                        continue
                    if planner_protected and key not in {"confidence"}:
                        continue
                    if key in {"fields", "inputs", "request_fields", "internal_fields", "computed_fields", "outputs"}:
                        value = [CapabilityField.model_validate(x) for x in (value or [])]
                    elif key == "dependencies":
                        value = [CapabilityDependency.model_validate(x) for x in (value or [])]
                    elif key == "request_refs":
                        value = [CapabilityRequestRef.model_validate(x) for x in (value or [])]
                    setattr(cap, key, value)
                if not planner_protected:
                    cap.updated_by = actor
            continue

        if op in {
            "upsert_capability_field",
            "upsert_input_field",
            "upsert_request_field",
            "upsert_internal_field",
            "upsert_computed_field",
            "upsert_output_field",
        }:
            idx = _find_capability_index(new_spec, edit)
            default_scope = {
                "upsert_input_field": "input",
                "upsert_request_field": "request_field",
                "upsert_internal_field": "internal",
                "upsert_computed_field": "computed",
                "upsert_output_field": "output",
            }.get(op, str(edit.get("scope") or "request_field"))
            raw = dict(edit.get("field_data") or edit.get("field") or {})
            actor = str(edit.get("actor") or "user")
            if actor == "planner":
                # Planner output is a proposal. It cannot self-confirm/self-lock
                # a synthetic aggregate field and then use that field as proof
                # that the recorded request was batch-shaped.
                raw["locked"] = False
                raw["confirmed"] = False
                raw["evidence"] = [
                    *list(raw.get("evidence") or []),
                    {"source": "planner_proposal"},
                ]
            if "field" in edit and not isinstance(edit.get("field"), dict):
                raw["key"] = str(edit.get("field") or "")
            for alias in ("field_id", "key", "path", "step_id", "request_id", "request_index", "type", "source_kind"):
                if alias in edit and alias not in raw:
                    raw[alias] = edit.get(alias)
            if not _apply_capability_field_to_param(
                new_spec, raw, scope=default_scope, actor=actor,
            ):
                # Only capability-owned aggregate inputs/outputs are persisted on
                # FlowCapability. Step-bound fields are redirected to ParamField.
                _upsert_capability_field(new_spec.capabilities[idx], raw, default_scope=default_scope)
            new_spec.capabilities[idx].updated_by = actor
            _invalidate_capability_contract(new_spec.capabilities[idx])
            continue

        if op in {"add_request_to_capability", "add_capability_step"}:
            idx = _find_capability_index(new_spec, edit)
            cap = new_spec.capabilities[idx]
            actor = str(edit.get("actor") or edit.get("origin") or "user")
            step_id = str(edit.get("step_id") or "")
            if not step_id and ("request_index" in edit or edit.get("request_id")):
                step_id = promote_request_to_step(
                    new_spec,
                    request_index=edit.get("request_index"),
                    request_id=str(edit.get("request_id") or ""),
                ).step_id
                needs_dependency_rebuild = True
            step = _find_step(new_spec, step_id)
            usage = str(edit.get("usage") or "execute")
            origin = str(edit.get("origin") or actor or "manual")
            extra_fields = {
                k: v for k, v in edit.items()
                if k not in {"op", "capability_name", "capability_id", "step_id", "actor", "usage", "origin", "request_id", "request_index", "request", "source", "target"}
            }
            _forget_removed_capability_step(new_spec, cap.name, step_id)
            _set_capability_request_membership(
                new_spec, cap, step, usage=usage, origin=origin, extra_fields=extra_fields,
            )
            cap.updated_by = "planner" if actor == "planner" else "user"
            _invalidate_capability_contract(cap)
            if usage in {"execute", "preflight"}:
                _add_step_id_to_capability(new_spec, cap, step_id)
            _sync_capability_order(new_spec, cap)
            continue

        if op in {"remove_request_from_capability", "remove_capability_step"}:
            idx = _find_capability_index(new_spec, edit)
            step_id = str(edit.get("step_id") or "")
            actor = str(edit.get("actor") or edit.get("origin") or "user")
            if actor != "planner":
                _remember_removed_capability_step(
                    new_spec, new_spec.capabilities[idx].name, step_id
                )

            new_spec.capabilities[idx].request_refs = [
                ref
                for ref in new_spec.capabilities[idx].request_refs
                if ref.step_id != step_id
            ]
            new_spec.capabilities[idx].nodes = _remove_capability_step_nodes(
                new_spec.capabilities[idx].nodes or [], step_id,
            )
            new_spec.capabilities[idx].updated_by = (
                "planner" if actor == "planner" else "user"
            )
            _invalidate_capability_contract(new_spec.capabilities[idx])
            _sync_capability_order(new_spec, new_spec.capabilities[idx])
            _sync_capability_output_after_step_removal(new_spec.capabilities[idx])
            continue

        if op == "reorder_capability_steps":
            idx = _find_capability_index(new_spec, edit)
            cap = new_spec.capabilities[idx]
            requested = [str(value) for value in (edit.get("step_ids") or []) if str(value)]
            current = _capability_call_step_ids_from_nodes(cap.nodes or [])
            if len(requested) != len(current) or set(requested) != set(current):
                raise ValueError(
                    "reorder_capability_steps must contain every executable step exactly once"
                )
            cap.nodes = _reorder_capability_call_nodes(
                cap.nodes or [],
                {step_id: index for index, step_id in enumerate(requested)},
            )
            cap.updated_by = str(edit.get("actor") or "user")
            _invalidate_capability_contract(cap)
            _sync_capability_order(new_spec, cap)
            continue

        if op == "bind_dependency":
            idx = _find_capability_index(new_spec, edit)
            cap = new_spec.capabilities[idx]
            raw = dict(edit.get("dependency") or {})
            raw.setdefault("type", edit.get("type") or "response_to_request")
            raw.setdefault("source", edit.get("source") or {
                "step_id": edit.get("source_step") or edit.get("source_step_id") or "",
                "path": edit.get("source_path") or "",
            })
            raw.setdefault("target", edit.get("target") or {
                "step_id": edit.get("target_step") or edit.get("target_step_id") or "",
                "path": edit.get("target_path") or "",
            })
            raw.setdefault("confirmed", bool(edit.get("confirmed", False)))
            raw.setdefault("locked", bool(edit.get("locked", False)))
            raw.setdefault("confidence", float(edit.get("confidence") or 0.75))
            raw.setdefault("reason", edit.get("reason") or "能力级修复绑定的依赖")
            dep = _upsert_capability_dependency(cap, raw)
            # 能力内依赖的两个端点必须同属该能力执行闭包；否则依赖视图会在下一次
            # 同步时被正确判为无效并丢弃，造成“刚绑定又消失”。
            for endpoint in (dep.source or {}, dep.target or {}):
                endpoint_step_id = str(endpoint.get("step_id") or "")
                if endpoint_step_id:
                    _find_step(new_spec, endpoint_step_id)
                    _add_step_id_to_capability(new_spec, cap, endpoint_step_id)
                    if not any(
                        n.get("type") == "call" and n.get("step_id") == endpoint_step_id
                        for n in _iter_capability_nodes(cap.nodes or [])
                        if isinstance(n, dict)
                    ):
                        cap.nodes.append({
                            "id": f"call_{len(cap.nodes or []) + 1}",
                            "type": "call",
                            "step_id": endpoint_step_id,
                        })
            _upsert_global_link_from_capability_dependency(new_spec, dep)
            _sync_capability_order(new_spec, cap)
            _invalidate_capability_contract(cap)
            continue

        if op in {"set_map", "set_condition"}:
            idx = _find_capability_index(new_spec, edit)
            node_type = "map" if op == "set_map" else "condition"
            raw = dict(edit.get("node") or {})
            if node_type == "map":
                raw.setdefault("source", edit.get("source") or "")
                raw.setdefault("target", edit.get("target") or "")
            else:
                raw.setdefault("condition", edit.get("condition") or edit.get("check") or "")
                for branch_key in ("then", "else", "steps", "children", "otherwise"):
                    if branch_key in edit and branch_key not in raw:
                        raw[branch_key] = edit[branch_key]
            if edit.get("node_id"):
                raw.setdefault("id", edit.get("node_id"))
            _upsert_capability_node(new_spec.capabilities[idx], node_type, raw)
            _invalidate_capability_contract(new_spec.capabilities[idx])
            continue

        if op == "set_output_mapping":
            idx = _find_capability_index(new_spec, edit)
            mapping = edit.get("mapping")
            if isinstance(mapping, dict):
                mapping = [mapping]
            if not isinstance(mapping, list):
                mapping = [{
                    "kind": edit.get("kind") or "final_response",
                    "step_id": edit.get("step_id") or edit.get("from") or "",
                    "response_path": edit.get("response_path") or edit.get("path") or "response",
                    "name": edit.get("name") or edit.get("field") or "",
                }]
            _set_capability_return(new_spec.capabilities[idx], mapping)
            _invalidate_capability_contract(new_spec.capabilities[idx])
            continue

        if op == "set_capability_relation":
            raw = dict(edit.get("relation") or {})
            for alias in ("type", "from_capability", "from_output", "to_capability", "to_input", "confidence", "confirmed", "reason", "evidence"):
                if alias in edit and alias not in raw:
                    raw[alias] = edit.get(alias)
            raw.setdefault("requires_user_confirmation", bool(edit.get("requires_user_confirmation", True)))
            _upsert_capability_relation(new_spec, raw)
            refs = {str(raw.get("from_capability") or ""), str(raw.get("to_capability") or "")}
            for capability in new_spec.capabilities:
                if capability.name in refs or capability.capability_id in refs:
                    _invalidate_capability_contract(capability)
            continue

        if op == "bind_option_source":
            _bind_option_source(
                new_spec,
                target_step_id=str(edit.get("target_step") or edit.get("target_step_id") or edit.get("step_id") or ""),
                target_path=str(edit.get("target_path") or edit.get("param_path") or ""),
                source_step_id=str(edit.get("source_step") or edit.get("source_step_id") or ""),
                source_url=str(edit.get("source_url") or ""),
                value_key=str(edit.get("value_key") or ""),
                label_key=str(edit.get("label_key") or ""),
                id_path=str(edit.get("id_path") or ""),
                options=edit.get("options") if isinstance(edit.get("options"), list) else None,
                option_map=edit.get("option_map") if isinstance(edit.get("option_map"), dict) else None,
                multi=bool(edit.get("multi")),
                actor=str(edit.get("actor") or "user"),
            )
            _invalidate_capabilities_for_steps(new_spec, {
                str(edit.get("target_step") or edit.get("target_step_id") or edit.get("step_id") or "")
            })
            continue

        if op == "set_loop_source":
            idx = _find_capability_index(new_spec, edit)
            cap = new_spec.capabilities[idx]
            items = str(edit.get("items") or edit.get("source") or "input.entries")
            _set_capability_loop_source(cap, items)
            cap.updated_by = str(edit.get("actor") or "user")
            _sync_capability_order(new_spec, cap)
            _invalidate_capability_contract(cap)
            continue

        if op == "set_return_mapping":
            idx = _find_capability_index(new_spec, edit)
            mapping = edit.get("mapping")
            if isinstance(mapping, dict):
                mapping = [mapping]
            if not isinstance(mapping, list):
                mapping = [{
                    "kind": edit.get("kind") or "final_response",
                    "step_id": edit.get("step_id") or edit.get("from") or "",
                    "response_path": edit.get("response_path") or edit.get("path") or "response",
                }]
            _set_capability_return(new_spec.capabilities[idx], mapping)
            new_spec.capabilities[idx].updated_by = str(edit.get("actor") or "user")
            _invalidate_capability_contract(new_spec.capabilities[idx])
            continue

        if op == "reject_dependency":
            link_id = str(edit.get("link_id") or "")
            if link_id:
                link = _find_link(new_spec, link_id)
                _record_rejected_dependency(new_spec, link)
                if link in new_spec.links:
                    new_spec.links.remove(link)
                continue
            source_step_id = str(edit.get("source_step_id") or edit.get("source_step") or "")
            source_path = str(edit.get("source_path") or "")
            target_step_id = str(edit.get("target_step_id") or edit.get("target_step") or "")
            target_path = str(edit.get("target_path") or "")
            if not all([source_step_id, source_path, target_step_id, target_path]):
                raise ValueError("reject_dependency missing link_id or source/target tuple")
            _record_rejected_dependency_raw(
                new_spec,
                source_step_id=source_step_id,
                source_path=source_path,
                target_step_id=target_step_id,
                target_path=target_path,
            )
            new_spec.links = [
                lk for lk in new_spec.links
                if _dependency_sig(lk.source_step_id, lk.source_path, lk.target_step_id, lk.target_path)
                not in _rejected_dependency_sigs(new_spec)
            ]
            continue

        # 链接编辑
        if edit.get("link_id"):
            link_id = edit["link_id"]
            if op == "update":
                link = _find_link(new_spec, link_id)
                field = edit.get("field")
                value = edit.get("value")
                if not field:
                    raise ValueError("link update missing field")
                identity_fields = {
                    "source_step_id", "source_path", "target_step_id", "target_path",
                }
                old_identity_value = str(getattr(link, field, "")) if field in identity_fields else ""
                if field == "confirmed":
                    link.confirmed = bool(value)
                elif field == "param_name":
                    link.param_name = str(value) if value is not None else None
                elif field == "source_path":
                    _validate_link_endpoint(new_spec, link.source_step_id, "source")
                    link.source_path = str(value)
                    link.source_tokens = None
                elif field == "target_path":
                    _validate_link_endpoint(new_spec, link.target_step_id, "target")
                    link.target_path = str(value)
                    link.target_tokens = None
                elif field == "source_step_id":
                    _validate_link_endpoint(new_spec, str(value), "source")
                    link.source_step_id = str(value)
                    link.source_tokens = None
                elif field == "target_step_id":
                    _validate_link_endpoint(new_spec, str(value), "target")
                    link.target_step_id = str(value)
                    link.target_tokens = None
                elif field == "link_id":                   # H19 修复:显式禁改 link_id(会被唯一性校验破坏)
                    raise ValueError("link_id is immutable")
                else:
                    # H19 修复:不再 hasattr 兜底(避免改 link_id/reason/internal 等关键字段)
                    raise ValueError(f"unknown link field: {field}")
                if field in identity_fields and str(getattr(link, field, "")) != old_identity_value:
                    from dano.execution.page.recording_live import invalidate_dependency_verification

                    invalidate_dependency_verification(link, f"依赖字段 {field} 已变化，需要重新验证")
                duplicate = _matching_link(new_spec, link)
                if duplicate is not None:
                    _merge_link(duplicate, link)
                    if link in new_spec.links:
                        new_spec.links.remove(link)
                    effective_link = duplicate
                else:
                    effective_link = link
                if (
                    str(edit.get("actor") or "user").strip().lower() == "user"
                    and field == "confirmed"
                    and effective_link.confirmed
                ):
                    _apply_user_link_source(new_spec.steps, effective_link)
                continue

            if op == "remove":
                link = _find_link(new_spec, link_id)
                if edit.get("reset_target"):
                    target_step = _find_step(new_spec, link.target_step_id)
                    target_param = _find_param(target_step, link.target_path)
                    actor = str(edit.get("actor") or "user").strip().lower()
                    if actor in _AUTOMATED_FIELD_EDIT_ACTORS and (
                        target_param.locked or _param_has_manual_contract(target_param)
                    ):
                        continue
                    _reset_param_source(
                        target_param,
                        reason="依赖已由用户移除，字段已恢复为用户输入",
                        actor=actor,
                    )
                if edit.get("record_rejection", True):
                    _record_rejected_dependency(new_spec, link)
                new_spec.links.remove(link)
                continue

        # 添加链接
        if op == "add" and edit.get("link"):
            link_data = dict(edit["link"])
            link_data.setdefault("source_step_id", "")
            link_data.setdefault("target_step_id", "")
            link_data.setdefault("source_path", "")
            link_data.setdefault("target_path", "")
            _validate_link_endpoint(new_spec, link_data["source_step_id"], "source")
            _validate_link_endpoint(new_spec, link_data["target_step_id"], "target")
            try:
                new_link = FlowLink(**link_data)
            except ValidationError as e:
                raise ValueError(f"invalid link data: {e}")
            existing = _matching_link(new_spec, new_link)
            if existing is not None:
                _merge_link(existing, new_link)
                effective_link = existing
            else:
                _ensure_unique_link(new_spec, new_link)
                new_spec.links.append(new_link)
                effective_link = new_link
            actor = str(edit.get("actor") or "user").strip().lower()
            if actor == "user":
                _apply_user_link_source(new_spec.steps, effective_link)
            continue

        # 步骤/参数编辑
        step_id = edit.get("step_id")
        if not step_id:
            raise ValueError("edit missing step_id")

        step = _find_step(new_spec, step_id)

        if op == "update":
            param_path = edit.get("param_path")
            field = edit.get("field")
            value = edit.get("value")
            actor = str(edit.get("actor") or "user").strip().lower()

            if not field:
                raise ValueError("update edit missing field")

            if param_path:
                # 参数级编辑
                if actor in _AUTOMATED_FIELD_EDIT_ACTORS:
                    param = next((item for item in step.params if item.path == param_path), None)
                    if param is None:
                        continue
                    if field == "locked":
                        continue
                    protected_axes = {
                        "key": ("key", "label", "name", "display_name"),
                        "label": ("key", "label", "name", "display_name"),
                        "value": ("value", "default_value"),
                        "source_kind": (
                            "source_kind", "source", "category",
                            "exposed_to_user", "exposed_to_caller",
                        ),
                        "source": (
                            "source_kind", "source", "category",
                            "exposed_to_user", "exposed_to_caller",
                        ),
                        "category": (
                            "category", "exposed_to_user", "exposed_to_caller",
                            "source_kind", "source",
                        ),
                        "exposed_to_user": (
                            "category", "exposed_to_user", "exposed_to_caller",
                            "source_kind", "source",
                        ),
                    }.get(str(field), (str(field),))
                    if _param_has_full_lock(param) or _param_axis_manually_edited(param, *protected_axes):
                        continue
                else:
                    param = _find_param(
                        step,
                        param_path,
                        field_id=str(edit.get("field_id") or ""),
                        param_key=str(edit.get("param_key") or ""),
                        param_label=str(edit.get("param_label") or ""),
                    )
                comparable_value = value
                if field in {"required", "exposed_to_user", "editable", "need_human_confirm", "locked"}:
                    comparable_value = bool(value)
                elif field in {"key", "label", "description", "path", "type", "category", "source_kind"}:
                    comparable_value = str(value or "").strip() if field == "path" else str(value)
                elif field == "value":
                    comparable_value = str(value)
                if hasattr(param, str(field)) and getattr(param, str(field)) == comparable_value:
                    continue
                if field == "key":
                    _rename_param_public_key(new_spec, step, param, str(value), actor=actor)
                elif field == "path":
                    old_path = param.path
                    new_path = str(value or "").strip()
                    if not new_path:
                        raise ValueError("param path cannot be empty")
                    if any(p is not param and p.path == new_path for p in step.params):
                        raise ValueError(f"duplicate param path: {new_path}")
                    linked_targets = [
                        lk for lk in new_spec.links
                        if lk.target_step_id == step.step_id
                        and _reference_targets_param(step, lk.target_path, param)
                    ]
                    source_targets_param = bool(
                        isinstance(param.source, dict)
                        and _reference_targets_param(
                            step, str(param.source.get("target_path") or ""), param,
                        )
                    )
                    param.path = new_path
                    for sb in step.selects:
                        if sb.path == old_path:
                            sb.path = new_path
                        if sb.id_path == old_path:
                            sb.id_path = new_path
                    for idn in step.identity:
                        if idn.path == old_path:
                            idn.path = new_path
                    for sv in step.system_values:
                        if sv.path == old_path:
                            sv.path = new_path
                    for lk in linked_targets:
                        lk.target_path = new_path
                    if isinstance(param.source, dict) and source_targets_param:
                        param.source["target_path"] = new_path
                elif field == "value":
                    param.value = str(value)
                    param.default_value = param.value
                    step.sample_inputs[param.key] = param.value
                elif field == "type":
                    _transition_param_type(param, value)
                elif field == "required":
                    param.required = bool(value)
                elif field == "exposed_to_user":           # H22 修复:bool 字段显式 bool() 转换
                    param.exposed_to_user = bool(value)
                elif field == "editable":
                    param.editable = bool(value)
                elif field == "need_human_confirm":
                    param.need_human_confirm = bool(value)
                elif field in _PARAM_ALLOWED_FIELDS:
                    setattr(param, field, value)
                    if field in {"label", "description"}:
                        param.name_source = "manual"
                else:
                    # H19 修复:不再 hasattr 兜底(避免改 path/source_kind/internal 等关键字段)
                    raise ValueError(f"unknown param field: {field}")
                if actor == "user" and field in {
                    "key", "label", "description", "value", "type", "category", "source_kind", "source",
                    "required", "exposed_to_user", "editable", "need_human_confirm", "enum_options", "enum_value_map",
                }:
                    if field != "key":
                        _record_param_manual_contract(param, (str(field),))
                if field in {
                    "key", "path", "label", "description", "value", "type", "category", "source_kind",
                    "source", "required", "exposed_to_user", "editable", "need_human_confirm",
                    "enum_options", "enum_value_map",
                }:
                    _invalidate_capabilities_for_steps(new_spec, {step.step_id})
            else:
                # 步骤级编辑
                if field == "url":
                    step.url = str(value)
                elif field == "method":
                    step.method = str(value).upper()
                elif field == "headers":
                    step.headers = dict(value)
                elif field == "content_type":
                    step.content_type = str(value)
                elif field == "name":
                    step.name = str(value)
                elif field == "role":
                    role = str(value)
                    step.source_meta = {**(step.source_meta or {}), "role": role}
                    step.semantic_role = role
                elif field == "risk_level":
                    step.risk_level = str(value)
                elif field == "body_source":
                    step.body_source = str(value) if value is not None else ""
                elif field == "path":
                    step.path = str(value)
                    step.url = str(value)
                elif field == "step_id":                   # H19 修复:显式禁改 step_id
                    raise ValueError("step_id is immutable")
                elif field == "selects":
                    try:
                        step.selects = [SelectBinding.model_validate(x) for x in (value or [])]
                        for binding in step.selects:
                            _hydrate_select_source_contract(new_spec, binding)
                            if (
                                actor == "user"
                                and binding.enum_confirmed is None
                                and (
                                    (
                                        binding.source_url
                                        and binding.value_key
                                        and binding.label_key
                                        and (binding.options or binding.option_map)
                                    )
                                    or (
                                        not binding.source_url
                                        and binding.options
                                        and len(_enum_option_map_from_options(binding.options))
                                        == len(binding.options)
                                    )
                                )
                            ):
                                # A complete binding explicitly saved by the
                                # operator is a confirmation, not a model guess.
                                binding.enum_confirmed = True
                    except ValidationError as e:
                        raise ValueError(f"invalid selects data: {e}")
                elif field == "identity":
                    try:
                        step.identity = [IdentityBinding.model_validate(x) for x in (value or [])]
                    except ValidationError as e:
                        raise ValueError(f"invalid identity data: {e}")
                elif field == "params":
                    try:
                        step.params = [ParamField.model_validate(x) for x in (value or [])]
                    except ValidationError as e:
                        raise ValueError(f"invalid params data: {e}")
                elif field in _STEP_ALLOWED_FIELDS:
                    setattr(step, field, value)
                else:
                    # H19 修复:不再 hasattr 兜底
                    raise ValueError(f"unknown step field: {field}")
                if field in {
                    "url", "method", "headers", "content_type", "name", "role", "risk_level",
                    "body_source", "path", "selects", "identity", "params", "source_meta",
                    "semantic_role", "success_rule", "fact_check", "response_json",
                }:
                    _invalidate_capabilities_for_steps(new_spec, {step.step_id})
            continue

        elif op == "reset_param_source":
            param_path = edit.get("param_path")
            if not param_path:
                raise ValueError("reset_param_source missing param_path")
            param = _find_param(
                step,
                param_path,
                field_id=str(edit.get("field_id") or ""),
                param_key=str(edit.get("param_key") or ""),
                param_label=str(edit.get("param_label") or ""),
            )
            target = str(edit.get("to") or "user_input")
            actor = str(edit.get("actor") or "user").strip().lower()
            if actor in _AUTOMATED_FIELD_EDIT_ACTORS and (
                param.locked or _param_has_manual_contract(param)
            ):
                continue
            new_spec.links = [
                lk for lk in new_spec.links
                if not (lk.target_step_id == step.step_id and _reference_targets_param(step, lk.target_path, param))
            ]
            if target == "constant":
                param.category = "system_const"
                param.source_kind = "constant"
                param.source = {"kind": "constant", "path": param.path, "manual": True}
                param.editable = True
                param.exposed_to_user = False
                param.need_human_confirm = False
                param.reason = "已重置为系统固定值，发布后按当前录制值提交"
                if actor == "user":
                    _record_param_manual_contract(param, (
                        "category", "source_kind", "source", "editable",
                        "exposed_to_user", "need_human_confirm",
                    ))
            else:
                _reset_param_source(param, actor=actor)
                step.sample_inputs[param.key] = param.value
            continue

        elif op == "add":
            raw_param_data = edit.get("param")
            if not isinstance(raw_param_data, dict) or not raw_param_data:
                raise ValueError("add edit missing param")
            param_data = dict(raw_param_data)
            explicit_fields = set(param_data)
            if "type" not in param_data and "value" in param_data:
                param_data["type"] = _infer_type_from_value(param_data["value"])
            try:
                new_param = ParamField(**param_data)
            except ValidationError as e:
                raise ValueError(f"invalid param data: {e}")
            actor = str(edit.get("actor") or "user").strip().lower()
            if actor == "user":
                # A field added in the workbench is already an explicit
                # operator decision. Record each supplied contract axis before
                # the final sync so enum/pagination heuristics cannot rewrite it.
                manual_fields = [field for field in (
                    "type", "category", "source_kind", "source",
                    "exposed_to_user", "editable", "required",
                    "need_human_confirm", "enum_options", "enum_value_map",
                ) if field in explicit_fields]
                _record_param_manual_contract(new_param, manual_fields)
                new_param.locked = True
            elif actor in _AUTOMATED_FIELD_EDIT_ACTORS:
                # Planner/repair payloads are proposals and cannot grant
                # themselves operator ownership through locked/manual markers.
                new_param.locked = False
                new_param.evidence = [
                    item for item in (new_param.evidence or [])
                    if not isinstance(item, dict) or item.get("source") != "manual_edit"
                ]
            step.params.append(new_param)
            if new_param.value:
                step.sample_inputs[new_param.key] = new_param.value
            continue

        elif op == "remove":
            param_path = edit.get("param_path")
            if not param_path:
                raise ValueError("remove edit missing param_path")
            param = _find_param(
                step,
                param_path,
                field_id=str(edit.get("field_id") or ""),
                param_key=str(edit.get("param_key") or ""),
                param_label=str(edit.get("param_label") or ""),
            )
            # 字段删除是一个完整的契约删除：不能只移除 params，却留下指向该字段的
            # 依赖、枚举绑定或身份绑定。否则前端看似删除成功，下一轮同步/校验又会
            # 从这些残留引用中恢复旧字段，表现为“修改后无法删除”。
            _remove_param_incoming_links(new_spec, step, param)
            key_is_unique = sum(item.key == param.key for item in step.params) == 1
            label_is_unique = bool(param.label) and sum(item.label == param.label for item in step.params) == 1
            step.selects = [
                binding for binding in (step.selects or [])
                if not (
                    _reference_targets_param(step, binding.path or binding.id_path or "", param)
                    or (
                        not binding.path and not binding.id_path
                        and (
                            (key_is_unique and binding.param == param.key)
                            or (label_is_unique and binding.param == param.label)
                        )
                    )
                )
            ]
            step.identity = [
                binding for binding in (step.identity or [])
                if not _reference_targets_param(step, binding.path or "", param)
            ]
            step.params.remove(param)
            if param.key in step.sample_inputs:
                del step.sample_inputs[param.key]
            _invalidate_capabilities_for_steps(new_spec, {step.step_id})
            continue

        else:
            raise ValueError(f"unknown edit op: {op}")

    _sync_link_sources(new_spec.steps, new_spec.links)
    if needs_dependency_rebuild:
        rebuild_flow_dependencies(new_spec)
    if bulk_review_resolutions:
        generated = _generated_review_items(new_spec)
        old_by_id = {item.id: item for item in new_spec.review_items}
        for item in generated:
            if item.id in old_by_id:
                item.resolved = old_by_id[item.id].resolved
            for severities, exclude_severities, resolved in bulk_review_resolutions:
                if severities and item.severity not in severities:
                    continue
                if exclude_severities and item.severity in exclude_severities:
                    continue
                item.resolved = resolved
        new_spec.review_items = generated

    # 验证
    try:
        FlowSpec.model_validate(new_spec.model_dump())
    except ValidationError as e:
        raise ValueError(f"invalid spec after edits: {e}")

    actions = ",".join(str(e.get("op") or "edit") for e in edits)
    _normalize_capability_references(new_spec)
    return append_flow_version(
        refresh_review_items(_sync_capability_io_schemas(new_spec)),
        "flow_edit",
        reason=actions[:200],
        actor="user",
    )









_INTERNAL_SOURCE_CONTRACT = {
    "user_input": ("user_param", True),
    "api_option": ("user_param", True),
    "page_enum": ("user_param", True),
    "static_enum": ("user_param", True),
    "manual_enum": ("user_param", True),
    "form_option": ("user_param", True),
    "constant": ("system_const", False),
    "page_default": ("user_param", True),
    "page_rule": ("runtime_var", False),
    "request_header": ("runtime_var", False),
    "current_user": ("runtime_var", False),
    "storage": ("runtime_var", False),
    "cookie": ("runtime_var", False),
    "session": ("runtime_var", False),
    "page_context": ("runtime_var", False),
    "context": ("runtime_var", False),
    "previous_response": ("runtime_var", False),
    "dynamic_structure": ("runtime_var", False),
    "selected_option_field": ("runtime_var", False),
    "computed": ("runtime_var", False),
    "generated": ("runtime_var", False),
    "system_time": ("runtime_var", False),
    "system_generated": ("runtime_var", False),
    "unknown": ("runtime_var", False),
}




def apply_client_flow_patch(
    spec: FlowSpec,
    edits: list[dict[str, Any]],
    *,
    expected_fingerprint: str,
) -> FlowSpec:
    """Apply a browser patch without accepting a client-owned FlowSpec.

    The fingerprint gates concurrent edits. RequestFacts and sensitive step
    transport evidence remain authoritative on the server.
    """
    expected = str(expected_fingerprint or "")
    current = flow_spec_fingerprint(spec)
    if not expected:
        raise ValueError("expected_fingerprint is required")
    if expected != current:
        raise FlowSpecConflictError(expected, current)
    if not isinstance(edits, list) or not edits:
        raise ValueError("flow patch requires a non-empty edits list")
    if len(edits) > 200:
        raise ValueError("flow patch contains too many edits")

    safe_edits: list[dict[str, Any]] = []
    for raw_edit in edits:
        if not isinstance(raw_edit, dict):
            raise ValueError("flow patch edits must be objects")
        edit = dict(raw_edit)
        op = str(edit.get("op") or "")
        if op == "update_flow" and str(edit.get("field") or "") == "meta":
            raise ValueError("server-owned flow field: meta")
        if (
            op == "update"
            and edit.get("param_path")
            and str(edit.get("field") or "") == "category"
        ):
            raise ValueError("derived parameter field: category")
        if op in {"add_capability", "upsert_capability"}:
            raw_capability = edit.get("capability")
            if isinstance(raw_capability, dict) and (
                "step_ids" in raw_capability or "request_refs" in raw_capability
            ):
                raise ValueError("client capability membership must be expressed through nodes")
            derived_fields = {
                "fields", "inputs", "request_fields", "internal_fields", "outputs",
            }
            if isinstance(raw_capability, dict) and derived_fields.intersection(raw_capability):
                raise ValueError("client capability field projections are read-only")
        if op == "update" and not edit.get("param_path"):
            field = str(edit.get("field") or "")
            if field in _CLIENT_SERVER_OWNED_STEP_FIELDS or field == "selects":
                raise ValueError(f"server-owned step field: {field}")
        if op == "upsert_select":
            safe_edits.append(_client_select_patch(spec, edit))
        elif (
            op == "update"
            and edit.get("param_path")
            and str(edit.get("field") or "") == "source_kind"
        ):
            safe_edits.extend(_client_source_patch(spec, edit))
        else:
            safe_edits.append(edit)
    return apply_flow_edits(spec, safe_edits)



















































# ─────────── Step D: GET 表单手选 ───────────




# ─────────── Step D: 确定性命名 + 业务说明 ───────────


















from dano.execution.page.recording_facts import (
    _BORING_LINK_VALUES,
    _BUSINESS_QUERY_PATH_RE,
    _INTERNAL_WORKFLOW_READ_RE,
    _NOISE_EXTS,
    _NOISE_SEGS,
    _OPTION_SEGS,
    _QUERY_ACTION_RE,
    _RECORD_IDENTITY_LEAVES,
    _REQUEST_OBSERVER_KEYS,
    _SCREENSHOT_OPTION_CONTROL_KINDS,
    _TRANSPORT_FILTER_KEYS,
    _TYPEAHEAD_PATH_RE,
    _WORKFLOW_CONTEXT_TOKENS,
    _WRITE_HINT_SEGS,
    _WRITE_METHODS,
    _api_option_source_refs,
    _attach_request_role,
    _build_request_facts,
    _business_filter_count,
    _caller_filter_key,
    _choice_control_triggered,
    _compact_repeated_endpoint_observations,
    _dedupe_preread_candidates,
    _dedupe_request_identities,
    _dependency_consumer_candidate,
    _field_evidence_for_request,
    _field_leaf_token,
    _find_request_fact_item,
    _has_query_action_evidence,
    _is_api_like_request_fact,
    _is_document_record_identity_path,
    _is_noise_request,
    _list_payload_has_conventional_option_contract,
    _list_payload_has_reference_contract,
    _list_payload_is_business_records,
    _looks_graphql_request,
    _looks_pagination_field,
    _looks_telemetry_request,
    _merge_response_schemas,
    _option_sources_from_page_enum_options,
    _page_enum_options_from_request_facts,
    _params_from_get_query,
    _path_from_url,
    _preread_candidate_score,
    _preread_dedupe_key,
    _query_param_type,
    _read_is_entity_enrichment_lookup,
    _record_identity_values_for_request,
    _recording_evidence_matches_request,
    _recording_evidence_matches_scope,
    _request_analysis_from_entry,
    _request_fact_entry,
    _request_fact_from_entry,
    _request_fact_has_record_identity,
    _request_fact_item,
    _request_fact_items,
    _request_fact_key,
    _request_fact_key_from_entry,
    _request_fact_signature_key,
    _request_has_business_query_evidence,
    _request_has_command_anchor,
    _request_has_option_endpoint_hint,
    _request_has_reference_entity_hint,
    _request_has_write_hint,
    _request_order_value,
    _request_path,
    _request_precedes,
    _request_query_values,
    _request_role_key,
    _request_segments,
    _request_sequence_value,
    _request_signature,
    _request_transaction_id,
    _request_values,
    _response_has_scalar_business_value,
    _response_referenced_later,
    _response_values,
    _role_row,
    _sample_hit_count,
    _schema_from_response_value,
    _trace_pos,
    _useful_link_value,
    _workflow_context_values_for_request,
    classify_network_request,
)


from dano.execution.page.recording_agent_contract import (
    _LIVE_PLAN_BLOCKING_GAPS,
    _META_ONLY_REPAIR_OPS,
    _RECORDING_AGENT_ALLOWED_OPS,
    _RECORDING_FIELD_OPS,
    _allowed_values_from_exc,
    _live_capability_plan_is_terminal,
    _recording_operation_result,
    _recording_requested_target,
    _recording_resolved_target,
    _semantic_fact_hash,
    _semantic_fact_snapshot,
    _validate_recording_agent_ops,
    apply_recording_agent_submission,
    recording_agent_validation,
    recording_capability_plan_complete,
)
import dano.execution.page.recording_agent_contract as _recording_agent_contract
if hasattr(_recording_agent_contract, '_bind_flow_spec_helpers'):
    _recording_agent_contract._bind_flow_spec_helpers()


from dano.execution.page.recording_analysis_state import (
    _model_visible_request_facts,
    recording_agent_state,
)
import dano.execution.page.recording_analysis_state as _recording_analysis_state
if hasattr(_recording_analysis_state, '_bind_flow_spec_helpers'):
    _recording_analysis_state._bind_flow_spec_helpers()


from dano.execution.page.flow_materialization.titles import (
    _default_step_name,
    _derive_step_name,
    _derive_title,
    _select_name_for_step,
)
import dano.execution.page.flow_materialization.titles as _flow_materialization_titles
if hasattr(_flow_materialization_titles, '_bind_flow_spec_helpers'):
    _flow_materialization_titles._bind_flow_spec_helpers()


from dano.execution.page.flow_materialization.request_steps import (
    _add_request_step_from_fact,
    _append_query_params_to_step,
    _build_step_from_capture,
    _dedupe_flow_steps,
    _dedupe_step_params,
    _detect_composite_entity_selects,
    _detect_query_selects,
    _entry_sequence,
    _infer_wire_format,
    _insert_promoted_step,
    _is_dedupable_read_step,
    _merge_duplicate_step_contract,
    _param_contract_richness,
    _param_dedupe_key,
    _param_quality,
    _param_type_from_value,
    _params_from_url_path,
    _query_key_from_param,
    _recorded_param_sample,
    _request_url_with_query,
    _samples_for_captured_request,
    _step_contract_richness,
    _step_dedupe_key,
    _step_role,
    _step_sequence,
    promote_request_to_step,
)
import dano.execution.page.flow_materialization.request_steps as _flow_materialization_request_steps
if hasattr(_flow_materialization_request_steps, '_bind_flow_spec_helpers'):
    _flow_materialization_request_steps._bind_flow_spec_helpers()


from dano.execution.page.flow_materialization.request_usage import (
    _canonicalize_materialized_request_identities,
    _mark_request_materialized,
    _materialized_step_id_for_request,
    _retarget_step_references,
    _upgrade_materialized_query_facts,
)
import dano.execution.page.flow_materialization.request_usage as _flow_materialization_request_usage
if hasattr(_flow_materialization_request_usage, '_bind_flow_spec_helpers'):
    _flow_materialization_request_usage._bind_flow_spec_helpers()


from dano.execution.page.flow_materialization.links import (
    _auto_dependency_link_allowed,
    _auto_dependency_target_allowed,
    _auto_link_has_grounded_contract,
    _dependency_closure_step_ids,
    _dependency_match_score,
    _dependency_sig,
    _flow_link_kind,
    _link_is_auto_generated,
    _merge_flow_read_sources,
    _previous_response_source_step_id,
    _prune_unsafe_auto_links,
    _record_rejected_dependency,
    _record_rejected_dependency_raw,
    _rejected_dependency_sigs,
    _skip_auto_dependency_target,
    _sync_link_sources,
    rebuild_flow_dependencies,
)
import dano.execution.page.flow_materialization.links as _flow_materialization_links
if hasattr(_flow_materialization_links, '_bind_flow_spec_helpers'):
    _flow_materialization_links._bind_flow_spec_helpers()


from dano.execution.page.flow_materialization.hydration import (
    _discover_record_hydration_links,
)
import dano.execution.page.flow_materialization.hydration as _flow_materialization_hydration
if hasattr(_flow_materialization_hydration, '_bind_flow_spec_helpers'):
    _flow_materialization_hydration._bind_flow_spec_helpers()


from dano.execution.page.flow_materialization.response_maps import (
    _enrich_materialized_response_shapes,
    _latest_response_key_map_candidates,
    _materialize_captured_response_key_maps,
    _response_list_paths,
    _response_shape_evidence_score,
)
import dano.execution.page.flow_materialization.response_maps as _flow_materialization_response_maps
if hasattr(_flow_materialization_response_maps, '_bind_flow_spec_helpers'):
    _flow_materialization_response_maps._bind_flow_spec_helpers()


from dano.execution.page.flow_materialization.field_contracts.common import (
    _CURRENT_USER_LEAVES,
    _MISSING_WIRE_PLACEHOLDERS,
    _PAGE_CONTEXT_LEAVES,
    _SCREENSHOT_CONTROL_KINDS,
    _SCREENSHOT_INTERNAL_SOURCE_KINDS,
    _SESSION_LITERAL_RE,
    _UUID_LITERAL_RE,
    _append_reason_detail,
    _apply_grounded_indexed_range_names,
    _audit_step_param_contracts,
    _canonical_screenshot_control,
    _field_source_configuration_advice,
    _grounded_control_evidence,
    _grounded_screenshot_query_path,
    _header_value_matches_token,
    _is_missing_wire_placeholder,
    _looks_audit_system_leaf,
    _looks_current_user_field,
    _looks_page_context_field,
    _looks_runtime_field,
    _looks_session_literal_after_key_check,
    _looks_session_specific_value,
    _looks_system_const_field,
    _looks_token_field,
    _looks_user_entered_business_field,
    _param_axis_manually_edited,
    _param_control_is_readonly,
    _param_control_kinds,
    _param_field_manually_edited,
    _param_group_prefix,
    _param_has_executable_source,
    _param_has_full_lock,
    _param_has_grounded_direct_input_contract,
    _param_has_grounded_public_name,
    _param_has_grounded_type,
    _param_has_interacted_temporal_control,
    _param_has_manual_contract,
    _param_has_page_control_evidence,
    _param_source_agent_classified,
    _param_source_guess,
    _request_header_source_for_token,
    _screenshot_control_evidence,
    _screenshot_control_supports_axis,
    _semantic_recorded_type,
)
import dano.execution.page.flow_materialization.field_contracts.common as _flow_materialization_field_contracts_common
if hasattr(_flow_materialization_field_contracts_common, '_bind_flow_spec_helpers'):
    _flow_materialization_field_contracts_common._bind_flow_spec_helpers()


from dano.execution.page.flow_materialization.field_contracts.required import (
    _apply_successful_omit_optional,
    _param_has_local_required_marker,
    _param_has_page_required_evidence,
    _param_required_agent_classified,
    _request_present_leaves,
    _successful_peer_omitted_leaves,
)
import dano.execution.page.flow_materialization.field_contracts.required as _flow_materialization_field_contracts_required
if hasattr(_flow_materialization_field_contracts_required, '_bind_flow_spec_helpers'):
    _flow_materialization_field_contracts_required._bind_flow_spec_helpers()


from dano.execution.page.flow_materialization.field_contracts.caller_ownership import (
    _RUNTIME_SUPPLIED_SOURCE_KINDS,
    _external_capability_input,
    _field_has_unlocked_editable_control,
    _param_exposed_to_caller,
    _param_has_editable_control_evidence,
    _param_requires_caller_input,
    _param_was_caller_typed,
)
import dano.execution.page.flow_materialization.field_contracts.caller_ownership as _flow_materialization_field_contracts_caller_ownership
if hasattr(_flow_materialization_field_contracts_caller_ownership, '_bind_flow_spec_helpers'):
    _flow_materialization_field_contracts_caller_ownership._bind_flow_spec_helpers()


from dano.execution.page.flow_materialization.field_contracts.record_identity import (
    _looks_row_identity_leaf,
    _param_is_document_record_identity,
    _record_identity_is_caller_owned,
    _step_has_stable_record_identity,
)
import dano.execution.page.flow_materialization.field_contracts.record_identity as _flow_materialization_field_contracts_record_identity
if hasattr(_flow_materialization_field_contracts_record_identity, '_bind_flow_spec_helpers'):
    _flow_materialization_field_contracts_record_identity._bind_flow_spec_helpers()


from dano.execution.page.flow_materialization.field_contracts.option_projection import (
    _BORING_COMPOSITE_VALUES,
    _ENUM_PARAM_TYPES,
    _ENUM_SOURCE_KINDS,
    _OPTION_DESCRIPTION_PREFIXES,
    _OPTION_SOURCE_KINDS,
    _attach_select_field_projections,
    _best_option_projection_path,
    _bind_option_source,
    _composite_values_match,
    _enum_label_value,
    _enum_option_map_from_options,
    _enum_options_description,
    _enum_options_for_param,
    _enum_sources_compatible,
    _enum_value_map_for_param,
    _explicit_enum_value_map,
    _find_select_binding,
    _ground_saved_page_enums,
    _hydrate_select_source_contract,
    _infer_selected_option_row_fields,
    _is_option_source_url,
    _looks_quantitative_option_target,
    _merge_enum_values,
    _option_binding_semantic_families,
    _option_binding_tokens,
    _option_candidate_reads,
    _option_row_match_count,
    _option_source_contract_endpoint,
    _page_enum_contract_for_param,
    _page_enum_options_for_request,
    _param_has_option_control_evidence,
    _projection_leaf_norm,
    _projection_path_parts,
    _projection_path_score,
    _projection_path_tokens,
    _read_is_business_entity_collection,
    _read_is_option_source,
    _read_transport_can_supply_options,
    _recorded_scalar_values_match,
    _refresh_api_option_display_labels,
    _refresh_param_enum_description,
    _repair_structural_option_bindings,
    _select_source_kind,
    _select_source_reason,
    _strip_option_descriptions,
    _sync_step_option_contracts,
    _upsert_option_description,
    _weak_automatic_text_option_binding,
)
import dano.execution.page.flow_materialization.field_contracts.option_projection as _flow_materialization_field_contracts_option_projection
if hasattr(_flow_materialization_field_contracts_option_projection, '_bind_flow_spec_helpers'):
    _flow_materialization_field_contracts_option_projection._bind_flow_spec_helpers()


from dano.execution.page.flow_materialization.field_contracts.computed import (
    _ARITHMETIC_STRATEGIES,
    _COMPUTED_ARITHMETIC_STRATEGIES,
    _COMPUTED_DATE_STRATEGIES,
    _IDENTITY_ARITHMETIC_EPS,
    _INPUT_OPERAND_KINDS,
    _STABLE_OPERAND_KINDS,
    _arithmetic_match_score,
    _arithmetic_operand_semantic_ok,
    _arithmetic_strong_structure,
    _arithmetic_target_allowed,
    _as_finite_number,
    _computed_formula_is_complete,
    _date_like_epoch_seconds,
    _identity_product_allowed,
    _infer_arithmetic_computed_fields,
    _infer_computed_runtime_fields,
    _is_identity_arithmetic,
    _is_numeric_formula_operand,
    _is_stable_operand,
    _looks_count_formula_leaf,
    _looks_non_quantity_formula_leaf,
    _looks_percent_formula_leaf,
    _looks_total_formula_leaf,
    _looks_unit_price_formula_leaf,
    _numbers_match,
    _operand_quality,
    _param_is_quantity_or_formula_leaf,
    _param_is_temporal,
    _pick_arithmetic_match,
    _timestamp_is_near_request,
)
import dano.execution.page.flow_materialization.field_contracts.computed as _flow_materialization_field_contracts_computed
if hasattr(_flow_materialization_field_contracts_computed, '_bind_flow_spec_helpers'):
    _flow_materialization_field_contracts_computed._bind_flow_spec_helpers()


from dano.execution.page.flow_materialization.field_contracts.create_form import (
    _apply_create_form_field_contracts,
    _create_form_field_is_system_owned,
    _create_unknown_has_caller_evidence,
    _mark_create_form_caller_input,
    _repair_uncontrolled_write_state_fields,
    _step_is_create_or_submit_form,
)
import dano.execution.page.flow_materialization.field_contracts.create_form as _flow_materialization_field_contracts_create_form
if hasattr(_flow_materialization_field_contracts_create_form, '_bind_flow_spec_helpers'):
    _flow_materialization_field_contracts_create_form._bind_flow_spec_helpers()


from dano.execution.page.flow_materialization.field_contracts.edit_form import (
    _apply_edit_form_field_contracts,
    _looks_catalog_attribute_leaf,
    _looks_display_echo_field,
    _mark_system_hydrated_field,
    _repair_readonly_control_defaults,
    _step_is_record_edit_form,
)
import dano.execution.page.flow_materialization.field_contracts.edit_form as _flow_materialization_field_contracts_edit_form
if hasattr(_flow_materialization_field_contracts_edit_form, '_bind_flow_spec_helpers'):
    _flow_materialization_field_contracts_edit_form._bind_flow_spec_helpers()


from dano.execution.page.flow_materialization.field_contracts.query_form import (
    _apply_query_form_field_contracts,
    _mark_query_filter_caller,
    _step_is_business_list_query,
    _step_is_option_read,
    _step_is_record_detail_query,
)
import dano.execution.page.flow_materialization.field_contracts.query_form as _flow_materialization_field_contracts_query_form
if hasattr(_flow_materialization_field_contracts_query_form, '_bind_flow_spec_helpers'):
    _flow_materialization_field_contracts_query_form._bind_flow_spec_helpers()


from dano.execution.page.flow_materialization.field_contracts.row_command import (
    _apply_row_command_field_contracts,
    _param_has_command_local_control,
    _step_is_row_command,
)
import dano.execution.page.flow_materialization.field_contracts.row_command as _flow_materialization_field_contracts_row_command
if hasattr(_flow_materialization_field_contracts_row_command, '_bind_flow_spec_helpers'):
    _flow_materialization_field_contracts_row_command._bind_flow_spec_helpers()


from dano.execution.page.flow_materialization.field_contracts.page_rules import (
    _apply_date_range_companions,
    _apply_page_rule_caller_override,
    _calendar_date_text,
    _mark_auto_fill_caller_override,
    _query_range_index,
)
import dano.execution.page.flow_materialization.field_contracts.page_rules as _flow_materialization_field_contracts_page_rules
if hasattr(_flow_materialization_field_contracts_page_rules, '_bind_flow_spec_helpers'):
    _flow_materialization_field_contracts_page_rules._bind_flow_spec_helpers()


from dano.execution.page.capability_contracts import (
    ALLOWED_CAPABILITY_KINDS,
    READ_CAPABILITY_KINDS,
    WRITE_CAPABILITY_KINDS,
    _ACTIONABLE_PLACEHOLDER_NAME_RE,
    _ACTION_LABELS,
    _CAPABILITY_ALLOWED_FIELDS,
    _CAPABILITY_PATH_PREFIXES,
    _CAPABILITY_REF_USAGE_ORDER,
    _FIELD_MAPPED_CAPABILITY_RELATIONS,
    _GENERIC_CAPABILITY_INTENT_RE,
    _GENERIC_PAGE_TITLE_RE,
    _IDENTIFIER_RELATION_TARGET_KINDS,
    _IDENTIFIER_ROLE_BY_FIELD,
    _IDENTIFIER_ROLE_TITLE,
    _INSTANCE_TITLE_SUFFIX_RE,
    _MUTATING_RECORD_KINDS,
    _NO_SCHEMA_DEFAULT,
    _PUBLIC_ROLE_BY_INTERNAL,
    _PUBLIC_SOURCE_BY_INTERNAL,
    _ROUTING_FIELD_RE,
    _WRITE_COMMAND_DISCRIMINATOR_RE,
    _active_capability_step_ids,
    _add_step_id_to_capability,
    _annotate_identifier_sources,
    _apply_capability_field_to_param,
    _apply_output_presentation_evidence,
    _apply_param_schema_default,
    _apply_semantic_business_understanding,
    _attach_option_source_memberships,
    _auto_confirm_ready_capabilities,
    _auto_fix_target_capability_for_request,
    _auto_fix_target_capability_name,
    _autofix_ops_to_edits,
    _batch_capability_input_schema,
    _business_query_evidence_score,
    _business_type_for_param,
    _canonical_step_summary,
    _canonicalize_public_capability_identities,
    _capability_business_key,
    _capability_call_nodes,
    _capability_call_step_ids_from_nodes,
    _capability_child_nodes,
    _capability_confirmation_hash,
    _capability_contract_view,
    _capability_contract_views,
    _capability_dependency_from_link,
    _capability_dependency_merge_key,
    _capability_dependency_summary,
    _capability_error,
    _capability_execute_record_selector,
    _capability_execution_contract,
    _capability_field_from_param,
    _capability_field_has_valid_source,
    _capability_field_looks_internal,
    _capability_field_summary,
    _capability_field_type,
    _capability_has_explicit_batch_intent,
    _capability_input_refs,
    _capability_input_schema,
    _capability_inputs_from_top_level_schema,
    _capability_intent_needs_refresh,
    _capability_is_batch,
    _capability_kind_family,
    _capability_node_step_ids,
    _capability_operation_kind,
    _capability_output_fields,
    _capability_output_name,
    _capability_output_samples,
    _capability_page_ids,
    _capability_param_enum_issue,
    _capability_param_enum_warning,
    _capability_ref_key,
    _capability_relation_requires_fields,
    _capability_relation_schemas_compatible,
    _capability_removed_step_refs,
    _capability_request_indexes,
    _capability_request_ref_from_step,
    _capability_response_path_exists,
    _capability_schema_array_item_props,
    _capability_schema_field,
    _capability_schema_field_type,
    _capability_scoped_node_step_ids,
    _capability_scoped_step_ids,
    _capability_sequence_window,
    _capability_step_allowed,
    _capability_step_param_exists,
    _capability_step_ref_keys,
    _capability_step_summary,
    _capability_step_was_removed,
    _capability_text_is_placeholder,
    _capability_to_api_dict,
    _capability_types_compatible,
    _capability_validation_report,
    _capability_value_ref_exists,
    _capability_warning,
    _clean_page_business_candidate,
    _collapse_duplicate_generated_capabilities,
    _complete_semantic_plan_from_spec,
    _default_capability_nodes,
    _deterministic_capability_repair_edits,
    _disambiguate_capability_param_keys,
    _eligible_business_write_fact,
    _ensure_capability_explanations,
    _ensure_external_transform_relations,
    _expand_response_key_map_inputs,
    _find_capability_by_ref,
    _find_capability_index,
    _flow_autofix_context,
    _flow_capability_id,
    _forget_removed_capability,
    _forget_removed_capability_step,
    _generalize_capability_title,
    _generated_capability_is_protected,
    _ground_recorded_identifier_relations,
    _grounded_read_operation_steps,
    _identifier_role_for_field,
    _identifier_value_is_grounding_evidence,
    _invalidate_capabilities_for_steps,
    _invalidate_capability_contract,
    _is_business_query_step,
    _is_technical_business_title,
    _is_write_step,
    _iter_capability_nodes,
    _locator_action_name,
    _looks_batch_step,
    _mark_repeated_write_observations,
    _merge_capability_lists_impl,
    _merge_capability_scoped_dependencies,
    _normalize_actionable_placeholder_param_names,
    _normalize_capability_references,
    _normalize_capability_relation_semantics,
    _normalize_generated_capability_semantics,
    _only_grounded_screenshot_query_params_added,
    _option_source_step_ids,
    _orchestration_context,
    _ordered_capability_request_refs,
    _ordered_steps_by_ids,
    _output_field_is_transport_only,
    _page_context_business_name,
    _page_context_business_name_from_contexts,
    _param_path_leaf,
    _params_can_share_caller_key,
    _planned_capability_has_public_anchor,
    _planner_patch_edits,
    _pre_materialization_semantic_plan_coverage,
    _primary_read_operation_step,
    _prune_auth_materializations,
    _prune_empty_capabilities,
    _public_capability_anchor_step_ids,
    _query_operation_key,
    _query_output_mappings,
    _read_status_steps,
    _remember_removed_capability,
    _remember_removed_capability_step,
    _remove_capability_step_nodes,
    _removed_capability_names,
    _reorder_capability_call_nodes,
    _repair_generated_capability_contracts,
    _repeated_write_command_signature,
    _response_identity_match_count,
    _retired_capability_step_ids,
    _same_capability_computed_field,
    _sanitize_capability_nodes,
    _schema_default_for_param,
    _schema_for_param_type,
    _schema_node_at_path,
    _schema_path_exists,
    _select_flow_capability,
    _semantic_candidate_gate,
    _semantic_mutable_context,
    _semantic_plan_coverage,
    _semantic_wire_hash,
    _set_capability_loop_source,
    _set_capability_request_membership,
    _set_capability_return,
    _stable_capability_id,
    _step_body_is_array,
    _step_evidence,
    _step_page_id_from_facts,
    _step_request_fact_for_capability,
    _step_request_key,
    _step_request_signature_key,
    _submit_capability_steps,
    _sync_capability_io_schemas,
    _sync_capability_order,
    _sync_capability_output_after_step_removal,
    _target_input_values,
    _title_without_step_suffix,
    _transition_capability_kind,
    _upsert_capability_dependency,
    _upsert_capability_field,
    _upsert_capability_node,
    _upsert_capability_relation,
    _upsert_global_link_from_capability_dependency,
    _write_command_discriminators,
    _write_contract_is_batch,
    _write_operation_key,
    _write_steps,
    auto_fix_flow_spec,
    capability_to_flow_spec_view,
    executable_flow_links,
    flow_spec_capability_contracts,
    orchestrate_flow_capabilities,
    sync_capability_scoped_views,
)
import dano.execution.page.capability_contracts as _capability_contracts
if hasattr(_capability_contracts, '_bind_flow_spec_helpers'):
    _capability_contracts._bind_flow_spec_helpers()


from dano.execution.page.flow_spec_core.versioning import (
    append_flow_version,
    ensure_flow_version,
)
import dano.execution.page.flow_spec_core.versioning as _flow_spec_core_versioning
if hasattr(_flow_spec_core_versioning, '_bind_flow_spec_helpers'):
    _flow_spec_core_versioning._bind_flow_spec_helpers()


from dano.execution.page.flow_spec_core.normalization import (
    _FLOW_PATH_MISSING,
    _clean_path_prefix,
    _flow_path_lookup,
    _flow_path_set,
    _flow_path_tokens,
    _infer_type_from_value,
    _looks_internal,
    _norm_field_name,
    _sample_value_set,
    _strip_body_prefix,
)
import dano.execution.page.flow_spec_core.normalization as _flow_spec_core_normalization
if hasattr(_flow_spec_core_normalization, '_bind_flow_spec_helpers'):
    _flow_spec_core_normalization._bind_flow_spec_helpers()


from dano.execution.page.flow_spec_core.request_contract import (
    _api_params,
    _api_sample_inputs,
    _dry_fields,
    _dry_step_preview,
    _executable_identity_source,
    _fact_check_report,
    _flow_step_query_template,
    _flow_step_to_api_step,
    _flow_step_url_template,
    _runtime_select_bindings,
    _select_binding_is_runtime_executable,
    _select_param_for_runtime,
    _step_param_map,
    _step_runtime_identity,
    _step_samples,
    _step_wire_formats,
    compile_capability_to_api_request,
    dry_run_flow_spec,
    flow_spec_required_params,
    flow_spec_to_api_request,
    flow_spec_user_params,
)
import dano.execution.page.flow_spec_core.request_contract as _flow_spec_core_request_contract
if hasattr(_flow_spec_core_request_contract, '_bind_flow_spec_helpers'):
    _flow_spec_core_request_contract._bind_flow_spec_helpers()


from dano.execution.page.flow_client_projection import (
    _CLIENT_SECRET_KEY_HINTS,
    _CLIENT_SELECT_EDIT_FIELDS,
    _CLIENT_SERVER_OWNED_STEP_FIELDS,
    _CLIENT_SOURCE_KINDS,
    _client_redact_sensitive,
    _client_response_projection,
    _client_select_patch,
    _client_source_patch,
    _default_purpose,
    _description_param_key,
    _description_rule,
    _description_source_text,
    _description_value,
    _public_request_role,
    _public_source_kind,
    _semantic_purpose,
    _unique_params,
    flow_spec_to_client,
    flow_spec_to_summary,
    render_business_description,
)
import dano.execution.page.flow_client_projection as _flow_client_projection
if hasattr(_flow_client_projection, '_bind_flow_spec_helpers'):
    _flow_client_projection._bind_flow_spec_helpers()


from dano.execution.page.flow_release import (
    _INTERNAL_EXPOSED_PATH_RE,
    _PUBLISH_BLOCKING_REVIEW_TYPES,
    _VALUE_ONLY_LABEL_RE,
    _compiled_contract_issue_groups,
    _compiled_contract_review_items,
    _diagnostic_publish_findings,
    _enum_map_covers_recorded_value,
    _enum_mapping_issues,
    _enum_options_look_value_only,
    _executor_fact_check_is_verified,
    _field_source_review_issues,
    _generated_review_items,
    _incomplete_page_enum_is_executable,
    _legacy_fact_check_is_grounded,
    _manual_enum_mapping_complete,
    _param_looks_exposed_internal_value,
    _prune_invalid_fact_checks,
    _publish_issue_groups,
    _runtime_param_publish_error,
    _select_has_executable_options,
    prepare_flow_release_candidate,
    prepare_flow_spec_for_publish,
)
import dano.execution.page.flow_release as _flow_release
if hasattr(_flow_release, '_bind_flow_spec_helpers'):
    _flow_release._bind_flow_spec_helpers()

register_sync_flow_spec_models(sync_flow_spec_models)

import sys as _sys
for _name, _extracted in list(_sys.modules.items()):
    if (
        isinstance(_name, str)
        and _name.startswith("dano.execution.page.")
        and hasattr(_extracted, "_bind_flow_spec_helpers")
    ):
        _extracted._bind_flow_spec_helpers()
