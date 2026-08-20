"""Stage 5: FlowSpec materialization orchestration."""
from __future__ import annotations

from typing import Any
import copy
import hashlib
import re
from dano.execution.page.flow_spec_core.models import (
    FlowLink,
    FlowSpec,
    FlowStep,
    ParamField,
    RecordedGoal,
    RequestUsage,
)
from dano.execution.page.request_capture import (
    _leaf_paths,
    _parse_body,
    discover_step_links,
    looks_like_auth_write,
    select_dependency_source,
    suggest_fact_check,
    write_requests,
)
from dano.execution.page.recording_facts import (
    _BUSINESS_QUERY_PATH_RE,
    _attach_request_role,
    _build_request_facts,
    _business_filter_count,
    _dedupe_preread_candidates,
    _dedupe_request_identities,
    _has_query_action_evidence,
    _page_enum_options_from_request_facts,
    _request_fact_entry,
    _request_fact_key,
    _request_has_command_anchor,
    _request_order_value,
    _request_path,
    _request_precedes,
    _request_role_key,
    _request_transaction_id,
    _workflow_context_values_for_request,
    classify_network_request,
)
from dano.execution.page.flow_spec_core.normalization import (
    _strip_body_prefix,
)
from dano.execution.page.flow_materialization.request_steps import (
    _append_query_params_to_step,
    _build_step_from_capture,
    _infer_wire_format,
    _samples_for_captured_request,
)
from dano.execution.page.flow_materialization.field_contracts.create_form import (
    _apply_create_form_field_contracts,
)
from dano.execution.page.flow_materialization.field_contracts.edit_form import (
    _apply_edit_form_field_contracts,
    _repair_readonly_control_defaults,
)
from dano.execution.page.flow_materialization.field_contracts.page_rules import (
    _apply_page_rule_caller_override,
)
from dano.execution.page.flow_materialization.field_contracts.query_form import (
    _apply_query_form_field_contracts,
)
from dano.execution.page.flow_materialization.field_contracts.row_command import (
    _apply_row_command_field_contracts,
)
from dano.execution.page.flow_materialization.field_contracts.required import (
    _apply_successful_omit_optional,
    _param_required_agent_classified,
)
from dano.execution.page.flow_materialization.field_contracts.dynamic_array import (
    _materialize_dynamic_array_inputs,
)
from dano.execution.page.flow_materialization.field_contracts.common import (
    _audit_step_param_contracts,
    _param_field_manually_edited,
    _param_has_manual_contract,
)
from dano.execution.page.flow_materialization.links import (
    _auto_dependency_link_allowed,
    _dependency_match_score,
    _merge_flow_read_sources,
    _sync_link_sources,
)
from dano.execution.page.flow_materialization.request_usage import (
    _canonicalize_materialized_request_identities,
    _upgrade_materialized_query_facts,
)
from dano.execution.page.flow_materialization.titles import (
    _derive_title,
)
from dano.execution.page.flow_materialization.hydration import (
    _discover_record_hydration_links,
)
from dano.execution.page.flow_materialization.response_maps import (
    _enrich_materialized_response_shapes,
    _materialize_captured_response_key_maps,
)
from dano.execution.page.flow_materialization.review_items import (
    refresh_review_items,
)
from dano.execution.page.flow_materialization.field_contracts.option_sync import (
    _ground_saved_page_enums,
    _sync_step_option_contracts,
)
from dano.execution.page.flow_materialization.field_contracts.computed import (
    _infer_computed_runtime_fields,
    _repair_invalid_date_span_contracts,
)
from dano.execution.page.flow_materialization.field_contracts.option_projection import (
    _infer_selected_option_row_fields,
)
from dano.execution.page.flow_materialization.field_contracts.caller_ownership import (
    _param_exposed_to_caller,
    _param_has_editable_control_evidence,
    _param_requires_caller_input,
)
from dano.execution.page.flow_materialization.field_contracts.option_repair import (
    _repair_structural_option_bindings,
)
from dano.execution.page.value_tracing import (
    discover_response_key_maps,
    discover_workflow_value_links,
)
from dano.execution.page.flow_spec_core.versioning import (
    ensure_flow_version,
)
from dano.execution.page.flow_spec_core.owner_runtime import (
    bind_owner_runtime,
)


def sync_flow_spec_models(spec: FlowSpec) -> FlowSpec:
    bind_owner_runtime()
    _canonicalize_materialized_request_identities(spec)
    _upgrade_materialized_query_facts(spec)
    # Upgrading an initial list request to the richer searched fact can make it
    # converge with a step that already owns that durable request identity.
    _canonicalize_materialized_request_identities(spec)
    _enrich_materialized_response_shapes(spec)
    _rebind_saved_field_evidence(spec)
    _materialize_saved_unsupported_file_inputs(spec)
    _repair_invalid_date_span_contracts(spec)
    _repair_ungrounded_saved_agent_constants(spec)
    _sync_link_sources(spec.steps, spec.links)
    _apply_query_form_field_contracts(spec)
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


def _repair_ungrounded_saved_agent_constants(spec: FlowSpec) -> int:
    """Recheck legacy agent constants with the current evidence contract."""
    from dano.execution.page.recording_live import (
        _constant_classification_is_grounded,
    )

    repaired = 0
    for step in spec.steps:
        for param in step.params:
            source = dict(param.source or {})
            if (
                param.source_kind != "constant"
                or str(source.get("kind") or "") != "constant"
                or str(source.get("actor") or "") != "agent"
            ):
                continue
            refs = [
                str(ref)
                for item in (param.evidence or [])
                if isinstance(item, dict)
                and item.get("kind") == "param_source"
                and item.get("source_kind") == "constant"
                for ref in (item.get("evidence_refs") or [])
                if str(ref)
            ]
            if _constant_classification_is_grounded(spec, step, param, refs):
                continue
            param.category = "runtime_var"
            param.source_kind = "unknown"
            param.source = {
                "kind": "unresolved",
                "path": param.path,
                "required_state": str(source.get("required_state") or "unknown"),
                "invalidated_contract": "ungrounded_agent_constant",
            }
            param.required = False
            param.exposed_to_user = False
            param.editable = False
            param.need_human_confirm = True
            param.reason = "旧常量结论只有单次录制样例，已撤销并等待字段级页面或重复观测证据"
            repaired += 1
    return repaired


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
    # File controls absent from the request body need already-bound siblings
    # from the same form as ownership evidence.  The normal rebinding pass only
    # recalculates unresolved items, so perform this association once against
    # the complete saved evidence set.
    from dano.execution.page.recording_field_evidence import (
        _associate_unsubmitted_file_controls,
    )

    _associate_unsubmitted_file_controls(rebound, requests)
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


def _materialize_saved_unsupported_file_inputs(spec: FlowSpec) -> None:
    """Project saved unsubmitted file controls into existing FlowSteps."""
    evidence = list(getattr(spec.request_facts, "field_evidence", []) or [])
    for step in spec.steps:
        request_id = str((step.source_meta or {}).get("request_id") or "")
        existing_paths = {str(param.path or "") for param in step.params}
        for item in evidence:
            if (
                not isinstance(item, dict)
                or str(item.get("binding_status") or "") != "bound_unsupported"
                or str(item.get("request_id") or "") != request_id
                or str(item.get("control_kind") or "").lower() != "file"
                or item.get("unsupported_execution") is not True
            ):
                continue
            path = str(item.get("wire_path") or "").removeprefix("body.")
            if not path or path in existing_paths:
                continue
            key = path.rsplit(".", 1)[-1]
            required_observed = item.get("required_observed")
            step.params.append(ParamField(
                path=path,
                key=key,
                label=str(item.get("label") or item.get("field") or key),
                value="",
                type="file-list" if item.get("multiple") else "file",
                wire_type="file",
                required=required_observed is True,
                confidence=1.0,
                confidence_tier="grounded",
                name_source="dom",
                category="user_param",
                source_kind="user_input",
                source={
                    "kind": "file_input",
                    "wire_path": f"body.{path}",
                    "wire_path_observed": False,
                    "unsupported_execution": True,
                    "required_state": (
                        "required" if required_observed is True
                        else "optional" if required_observed is False
                        else "unknown"
                    ),
                    "filename": str(item.get("filename") or ""),
                    "mime_type": str(item.get("mime_type") or ""),
                    "multiple": bool(item.get("multiple")),
                    "file_count": int(item.get("file_count") or 0),
                },
                editable=True,
                exposed_to_user=True,
                need_human_confirm=False,
                reason=(
                    "页面表单包含调用方文件输入，但录制未提交文件，"
                    "保留能力输入并明确标记执行不支持"
                ),
                evidence=[{
                    "kind": "page_control",
                    "source": "recorder_dom",
                    "control_kind": "file",
                    "request_path": path,
                    "binding_status": "bound_unsupported",
                    "occurrence_id": str(item.get("evidence_id") or ""),
                    "surface": str(item.get("surface") or ""),
                    "in_dialog": bool(item.get("in_dialog")),
                    "action_id": str(item.get("action_id") or ""),
                    "transaction_id": str(item.get("transaction_id") or ""),
                    "page_id": str(item.get("page_id") or ""),
                    "frame_id": str(item.get("frame_id") or ""),
                }],
            ))
            existing_paths.add(path)


_DEFAULT_RECORDED_FORBIDDEN_ACTIONS = [
    "调用当前录制范围外的接口",
    "篡改录制事实",
    "泄露认证凭证",
]


_LEGACY_RECORDED_FORBIDDEN_ACTIONS = frozenset({"删除", "作废", "撤销", "终止", "驳回"})


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
    bind_owner_runtime()
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
            "stage_1_6_contract_version": 2,
        },
    )
    _mark_repeated_write_observations(spec)
    # ponytail: reuse the existing grounded matcher before the first projection.
    _repair_structural_option_bindings(spec)
    _apply_mechanical_field_contracts(spec)
    _materialize_dynamic_array_inputs(spec)
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

_PENDING_FLOW_SPEC_HELPERS = {'_generated_review_items': 'dano.execution.page.flow_release', '_mark_repeated_write_observations': 'dano.execution.page.capability_contracts', 'sync_capability_scoped_views': 'dano.execution.page.capability_orchestration', '_active_capability_step_ids': 'dano.execution.page.capability_refs', '_is_write_step': 'dano.execution.page.capability_kinds', '_looks_batch_step': 'dano.execution.page.capability_kinds', '_retired_capability_step_ids': 'dano.execution.page.capability_refs', '_step_evidence': 'dano.execution.page.capability_refs'}


def _bind_flow_spec_helpers() -> None:
    import sys
    module_globals = globals()
    for name, owner in _PENDING_FLOW_SPEC_HELPERS.items():
        mod = sys.modules.get(owner)
        if mod is None or not hasattr(mod, name):
            continue
        module_globals[name] = getattr(mod, name)


_bind_flow_spec_helpers()
