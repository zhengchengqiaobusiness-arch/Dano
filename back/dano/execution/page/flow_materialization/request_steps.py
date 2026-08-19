"""Stage 5: captured request facts to FlowStep params and samples."""
from __future__ import annotations

from typing import Any
import copy
from datetime import datetime, timezone
import json
from urllib.parse import unquote, urlparse, parse_qs, urlencode
import re
from dano.execution.page.flow_spec_core.models import (
    FlowSpec,
    FlowStep,
    IdentityBinding,
    ParamField,
    SelectBinding,
    SystemValue,
)
from dano.execution.page.request_capture import (
    _is_idlike,
    _is_system_timestamp,
    _leaf_paths,
    _parse_body,
    _pick_label_key,
    apply_page_enum_options,
    as_list_payload,
    classify_request_role,
    extract_auth_headers,
    flatten_body,
    infer_success_rule,
    page_enum_selects,
    suggest_assignee_names,
    suggest_identity,
    suggest_list_selects,
    suggest_selects,
)
from dano.execution.page.recording_facts import (
    _REQUEST_OBSERVER_KEYS,
    _SCREENSHOT_OPTION_CONTROL_KINDS,
    _WRITE_METHODS,
    _attach_request_role,
    _field_evidence_for_request,
    _find_request_fact_item,
    _looks_pagination_field,
    _page_enum_options_from_request_facts,
    _params_from_get_query,
    _path_from_url,
    _recording_evidence_matches_request,
    _request_fact_items,
    _request_has_business_query_evidence,
    _request_path,
    _request_query_values,
    _request_sequence_value,
    _request_signature,
    _request_transaction_id,
)
from dano.execution.page.flow_materialization.titles import (
    _default_step_name,
    _select_name_for_step,
)


def _recorded_param_sample(value: Any) -> Any:
    """Preserve false/0; only missing values become an empty sample."""
    if value is None:
        return ""
    return value


def _detect_composite_entity_selects(
    fields: list[dict],
    option_reads: list[dict],
    *,
    existing_paths: set[str],
) -> list[dict]:
    """Bind a chooser from a response row when several write fields match that row.

    Modal/table pickers often have no select widget.  A single short ID is not
    enough; two or more field matches on one unique row are.
    """
    out: list[dict] = []
    claimed = set(existing_paths)
    for read in option_reads or []:
        source_url = str(read.get("url") or "").strip()
        items = as_list_payload(read.get("json", read.get("response_json")))
        if not source_url or not items or not isinstance(items[0], dict):
            continue
        row_hits: list[tuple[dict, list[tuple[dict, str]]]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            matches: list[tuple[dict, str]] = []
            for field in fields:
                target_path = str(field.get("path") or "")
                control_kind = str(field.get("control_kind") or "").lower()
                if not target_path or target_path in claimed:
                    continue
                if field.get("recorded_user_input") and control_kind not in {"select", "combobox"}:
                    continue
                if (
                    _field_has_unlocked_editable_control(field)
                    and control_kind not in {"select", "combobox"}
                ):
                    continue
                raw = field.get("raw_value", field.get("value"))
                if raw in (None, ""):
                    continue
                candidates = [
                    (_projection_path_score(source_path, target_path), source_path)
                    for source_path, _tokens, _raw_value, raw_leaf in _leaf_paths(item)
                    if _composite_values_match(raw, raw_leaf)
                    and (
                        _projection_path_score(source_path, target_path) >= 75
                        or (
                            str(raw).strip().casefold() not in _BORING_COMPOSITE_VALUES
                            and _projection_path_score(source_path, target_path) >= 50
                        )
                    )
                ]
                best = max((score for score, _path in candidates), default=0)
                best_paths = [path for score, path in candidates if score == best and best]
                if len(best_paths) == 1:
                    matches.append((field, best_paths[0]))
            if len(matches) >= 2:
                row_hits.append((item, matches))
        if len(row_hits) != 1:
            continue
        selected, matches = row_hits[0]
        chooser = next(
            (
                (field, source_path)
                for field, source_path in matches
                if _is_idlike(str(field.get("path") or field.get("key") or "").split(".")[-1])
            ),
            None,
        )
        if chooser is None:
            continue
        chooser_field, value_key = chooser
        chooser_path = str(chooser_field.get("path") or "")
        label_key = _pick_label_key(selected, value_key.split(".")[-1] if "." in value_key else value_key)
        if not label_key:
            continue
        records = []
        option_map: dict[str, Any] = {}
        seen_values: set[str] = set()
        valid = True
        for item in items:
            if not isinstance(item, dict):
                continue
            label = str(item.get(label_key) or "").strip()
            raw_value = item.get(value_key if value_key in item else value_key.split(".")[-1])
            if not label or raw_value in (None, "") or label in option_map or str(raw_value) in seen_values:
                valid = False
                break
            seen_values.add(str(raw_value))
            option_map[label] = raw_value
            records.append({"label": label, "value": raw_value})
        if not valid or len(records) < 2:
            continue
        projections = {
            str(field.get("path")): source_path
            for field, source_path in matches
            if str(field.get("path")) != chooser_path
        }
        claimed.add(chooser_path)
        claimed.update(projections)
        out.append({
            "path": chooser_path,
            "source_url": source_url,
            "source_request_id": str(read.get("request_id") or read.get("id") or ""),
            "value_key": value_key.split(".")[-1],
            "label_key": label_key,
            "count": len(records),
            "options": records,
            "option_map": option_map,
            "enum_source": "api",
            "enum_confirmed": True,
            "id_path": chooser_path,
            "field_projections": projections,
        })
    return out


def _build_step_from_capture(
    req: dict,
    *,
    reads: list[dict],
    samples: dict,
    storage_state: dict | None,
    required_labels: set,
    page_enum_options: dict,
    step_index: int,
    field_evidence: list[dict] | None = None,
) -> FlowStep:
    method = (req.get("method") or "POST").upper()
    pd = req.get("post_data")
    body = _parse_body(pd)
    page_enum_options = _page_enum_options_for_request(req, page_enum_options)
    field_evidence = _field_evidence_for_request(req, field_evidence)
    if field_evidence and any(
        str(item.get("required_state") or "") == "required" or item.get("required") is True
        for item in field_evidence
        if isinstance(item, dict)
    ):
        # A single SPA page/frame may host several business routes. Required
        # markers must follow the route that emitted this request instead of
        # leaking from the last form snapshot into an earlier query contract.
        required_labels = {
            str(item.get("label") or item.get("field") or "").strip()
            for item in field_evidence
            if (
                str(item.get("required_state") or "") == "required"
                or item.get("required") is True
            )
            and str(item.get("label") or item.get("field") or "").strip()
        }

    # 风险 + 语义角色
    role = classify_request_role(req)
    request_role = req.get("_request_role") or {}
    risk = request_role.get("risk_level") or role.get("risk_level", "L3")

    def has_real_enum_source(sb: SelectBinding) -> bool:
        return bool(sb.options) or bool(sb.source_url and sb.value_key and sb.label_key)

    option_reads = _option_candidate_reads([
        read for read in (reads or [])
        if _recording_evidence_matches_request(req, read)
    ])
    query_is_option_source = method == "GET" and _read_is_option_source(req)
    query_is_business_query = method == "GET" and _request_has_business_query_evidence(req)
    grounded_samples = dict(samples or {})
    for picked, raw_options in (page_enum_options or {}).items():
        if not isinstance(raw_options, dict):
            continue
        field_key = str(raw_options.get("field_key") or "").strip()
        selected = next((
            str(raw_options.get(key))
            for key in ("selected", "selected_label", "label", "value")
            if raw_options.get(key) not in (None, "")
        ), str(picked or ""))
        if field_key and selected:
            grounded_samples.setdefault(field_key, selected)

    # GET 请求：从 URL query string 提参,同时对 query 也跑 select 检测
    # (治"参数来源接口没识别":接口型 query 参数如 keyword=xxx / status=xxx 应该被识别为接口选择字段)
    if method == "GET" or body is None:
        list_paths: list[str] = []
        iden_raw: list[dict] = []
        flat_fields = _params_from_get_query(
            req, grounded_samples, page_enum_options, field_evidence, required_labels,
        )
        # select/选人:在 query 参数名上做下拉检测,与 POST body 同套算法
        # Query parameters on an option-source request configure that source;
        # they are not themselves options selected from its own response.  In
        # particular ``simple-list?status=0`` must remain an internal filter,
        # must not become the chooser nor inherit an unrelated page enum.
        selects_raw = (
            [] if query_is_option_source
            else _detect_query_selects(req, grounded_samples, option_reads, page_enum_options, field_evidence)
        )
    else:
        # 列表多选先识别
        list_selects = suggest_list_selects(pd, option_reads, grounded_samples)
        list_paths = [s["path"] for s in list_selects]

        # 字段拍平
        flat_fields = flatten_body(
            pd, samples, required_labels, collapse_paths=list_paths,
            field_evidence=field_evidence,
        )

        # HTTP writes may carry a JSON/Form body and independent Query
        # parameters at the same time. Preserve both contracts.
        query_fields = _params_from_get_query(
            req, grounded_samples, page_enum_options, field_evidence, required_labels,
        )
        flat_fields.extend(query_fields)

        # select/选人
        selects_raw = suggest_selects(pd, option_reads, grounded_samples, skip_paths=list_paths, fields=flat_fields) + list_selects
        apply_page_enum_options(selects_raw, page_enum_options, post_data=pd, fields=flat_fields)
        selects_raw += page_enum_selects(pd, page_enum_options, {s.get("path", "") for s in selects_raw}, fields=flat_fields)
        # Query-string enums on GET filters stay on the list request. A later
        # write that happens to carry ``?status=`` is a command discriminator,
        # not that list dropdown.
        if method == "GET":
            selects_raw += _detect_query_selects(
                req, grounded_samples, option_reads, page_enum_options, field_evidence,
            )

        # identity(运行期重取)
        iden_raw = suggest_identity(pd, storage_state, samples)

    flat_fields.extend(_params_from_url_path(req, grounded_samples))
    _attach_select_field_projections(selects_raw, flat_fields, option_reads)
    composite_selects = _detect_composite_entity_selects(
        flat_fields,
        option_reads,
        existing_paths={str(item.get("path") or "") for item in selects_raw},
    )
    if composite_selects:
        selects_raw.extend(composite_selects)
        _attach_select_field_projections(selects_raw, flat_fields, option_reads)

    # select 字段配中文名
    sel_names = _select_name_for_step(selects_raw, samples)

    # BPMN 审批人命名兜底
    assignee_names = suggest_assignee_names(pd, option_reads, samples)

    # select 元数据
    selects_meta: list[SelectBinding] = []
    for s in selects_raw:
        selects_meta.append(SelectBinding(
            param="",
            path=s.get("path", ""),
            source_url=s.get("source_url", ""),
            value_key=s.get("value_key", ""),
            label_key=s.get("label_key", ""),
            category_key=s.get("category_key"),
            category_value=s.get("category_value"),
            multi=bool(s.get("multi")),
            element_template=s.get("element_template"),
            label_subkey=s.get("label_subkey"),
            count=int(s.get("count") or 0),
            options=list(s.get("options") or []),
            option_map=dict(s.get("option_map") or {}) or None,
            enum_source=s.get("enum_source"),
            enum_confirmed=s.get("enum_confirmed"),
            id_path=s.get("id_path"),
            id_tokens=s.get("id_tokens"),
            field_projections=dict(s.get("field_projections") or {}),
        ))

    # identity
    identity_meta = [
        IdentityBinding(
            path=i.get("path", ""),
            source=i.get("source", ""),
            tokens=i.get("tokens"),
            value=i.get("value"),
        )
        for i in iden_raw
    ]
    identity_paths = {i.path for i in identity_meta if i.path}

    # system_values
    sys_values: list[SystemValue] = []
    if body is not None:
        for path, tokens, _sv, raw in _leaf_paths(body):
            key = path.split(".")[-1].split("[")[0]
            if _is_system_timestamp(key, raw) and _timestamp_is_near_request(raw, req):
                kind = "now_ms" if len(str(raw)) == 13 else "now_s"
                sys_values.append(SystemValue(path=path, tokens=tokens, kind=kind))
    system_paths = {sv.path for sv in sys_values}
    select_paths = {s.path for s in selects_meta if s.path and has_real_enum_source(s)}
    select_id_paths = {s.id_path for s in selects_meta if s.id_path and has_real_enum_source(s)}
    select_by_path = {s.path: s for s in selects_meta if s.path and has_real_enum_source(s)}
    select_by_id_path = {s.id_path: s for s in selects_meta if s.id_path and has_real_enum_source(s)}

    # success_rule
    sr = None
    if req.get("response_json") is not None:
        sr = infer_success_rule([{"json": req.get("response_json")}])

    # params
    params: list[ParamField] = []
    for f in flat_fields:
        path = f.get("path", "")
        wire_type = f.get("wire_type") or _infer_type_from_value(f.get("value")) or f.get("type") or "string"
        ptype = f.get("type") or wire_type
        if path in list_paths:
            ptype = "list-enum"
        select_meta = select_by_path.get(path)
        if path in select_paths:
            ptype = "list-enum" if select_meta is not None and select_meta.multi else "enum"

        # Wire key stays the invocation contract; the page label is display-only.
        wire_key = str(f.get("key") or "").strip()
        if not wire_key or wire_key == path:
            wire_key = re.sub(r"\[\d+\]$", "", str(path or "").rsplit(".", 1)[-1])
        business_label = str(f.get("suggest_name") or "").strip()
        nm = wire_key
        display_label = business_label or wire_key
        if _looks_pagination_field(str(f.get("key") or ""), path):
            # Pagination names are part of the public invocation contract. Keep
            # their stable wire-facing key while retaining the localized DOM
            # label separately for UI presentation.
            ns = "auto"
        elif path in sel_names:
            display_label = sel_names[path] or display_label
            ns = "sample"
        elif path in assignee_names and (not display_label or display_label == wire_key or _looks_internal(display_label)):
            display_label = assignee_names[path] or display_label
            ns = "assignee"
        else:
            ns = f.get("name_source") or "auto"

        source_guess = _param_source_guess(
            field=f,
            path=path,
            key=nm,
            method=method,
            identity_paths=identity_paths,
            system_paths=system_paths,
            select_paths=select_paths,
            select_id_paths=select_id_paths,
            select_by_path=select_by_path,
            select_by_id_path=select_by_id_path,
            samples=samples,
            request_headers=req.get("headers") or {},
            query_is_option_source=query_is_option_source,
            query_is_business_query=query_is_business_query,
        )
        missing_wire_placeholder = _is_missing_wire_placeholder(f.get("value"))
        if missing_wire_placeholder:
            # Values such as ``undefined`` are evidence that the page failed to
            # supply a value, not reusable constants or caller defaults.  Keep
            # the wire field executable by exposing it as a required input.
            source_guess = {
                "category": "user_param",
                "source_kind": "user_input",
                "source": {
                    "kind": "missing_recorded_value",
                    "path": path,
                    "required_state": "required",
                },
                "editable": True,
                "exposed_to_user": True,
                "reason": "录制请求中的值为空占位符，调用时必须由调用方提供真实值",
                "need_human_confirm": False,
            }
        recorded_option_control = bool(
            ptype in _ENUM_PARAM_TYPES
            and str(f.get("control_kind") or "").lower()
            in _SCREENSHOT_OPTION_CONTROL_KINDS
            and select_meta is None
        )
        if recorded_option_control:
            source_guess = {
                **source_guess,
                "category": "user_param",
                "source_kind": "form_option",
                "source": {"kind": "form_option", "path": path, "enum_confirmed": False},
                "exposed_to_user": True,
                "editable": True,
                "need_human_confirm": True,
                "reason": "录制页面确认该字段为选择控件；候选值尚未完整展开",
            }
        enum_options = _enum_options_for_param(select_meta)
        enum_value_map = _enum_value_map_for_param(select_meta)
        if select_meta is not None and select_meta.enum_source == "dom" and enum_options:
            option_labels = {
                str(pair[0]) for option in enum_options
                if (pair := _enum_label_value(option)) is not None
            }
            submitted_is_label = str(f.get("value") or "") in option_labels
            mapped_labels = {str(key) for key in (enum_value_map or {})}
            if not submitted_is_label and not option_labels.issubset(mapped_labels):
                # Keep every captured label as evidence/description, but do not
                # pretend unseen numeric/short-code values follow DOM order.
                select_meta.enum_confirmed = False
        enum_description = _enum_options_description(source_guess["source_kind"], enum_options, enum_value_map)
        evidence = []
        if f.get("field_aliases") or str(f.get("control_kind") or "unknown") != "unknown":
            evidence.append({
                "kind": "page_control",
                "source": "recorder_dom",
                "field_aliases": list(f.get("field_aliases") or []),
                "control_kind": str(f.get("control_kind") or "unknown"),
                "interacted": bool(f.get("recorded_user_input")),
                "disabled": bool(f.get("control_disabled")),
                "read_only": bool(f.get("control_read_only")),
                "editable": not bool(f.get("control_disabled")) if str(
                    f.get("control_kind") or ""
                ).lower() in {"select", "combobox"} else not bool(
                    f.get("control_disabled") or f.get("control_read_only")
                ),
                "request_path": path,
                "required": (
                    True if str(f.get("required_state") or "") == "required" or f.get("required") is True
                    else False
                ),
                "binding_status": "bound",
                "surface": str(f.get("surface") or ""),
                "in_dialog": bool(f.get("in_dialog")),
                "action_id": str(f.get("action_id") or ""),
                **dict(f.get("constraints") or {}),
            })
        if (str(f.get("required_state") or "") == "required" or f.get("required") is True) and f.get("required_state_grounded"):
            # Persist the page marker as evidence instead of only persisting the
            # resulting boolean. This lets later re-analysis distinguish an
            # actually-required search control from a filter that merely had a
            # value in the recorded URL.
            evidence.append({
                "kind": "page_required",
                "source": "recorder_dom",
                "request_path": path,
                "binding_status": "bound",
            })
        if enum_description and source_guess["source_kind"] in _OPTION_SOURCE_KINDS:
            evidence.append({
                "kind": "enum_options",
                "source_kind": source_guess["source_kind"],
                "option_count": len(enum_options or []),
                "options": enum_options or [],
                "option_map": enum_value_map or {},
            })

        caller_owned = bool(
            source_guess["category"] == "user_param"
            and source_guess["exposed_to_user"]
            and source_guess["source_kind"] not in {
                "previous_response", "current_user", "storage", "cookie",
                "page_context", "request_header", "system_time",
                "system_generated", "computed", "constant", "loop_item",
            }
        )
        params.append(ParamField(
            path=path,
            key=nm,
            label=display_label,
            value="" if missing_wire_placeholder else _recorded_param_sample(f.get("value")),
            type=ptype,
            wire_type=wire_type,
            required=(
                (
                    missing_wire_placeholder
                    or str(f.get("required_state") or "unknown") == "required"
                    or bool(source_guess.get("required"))
                )
                and caller_owned
                and not _looks_pagination_field(nm, path)
            ),
            confidence=float(f.get("confidence") or 0.0),
            confidence_tier=f.get("confidence_tier") or "auto",
            name_source=ns,
            # **系统化**:同时投递 label 列表 + label→value 反查表,确保前端能渲染 + 运行期能做 name→ID 解析。
            enum_options=enum_options,
            enum_value_map=enum_value_map,
            category=source_guess["category"],
            source_kind=source_guess["source_kind"],
            source={
                **source_guess["source"],
                **({
                    "required_state": (
                        "required" if missing_wire_placeholder or bool(source_guess.get("required"))
                        else "optional" if _looks_pagination_field(nm, path)
                        else str(f.get("required_state") or "unknown")
                    ),
                } if missing_wire_placeholder or bool(source_guess.get("required")) or f.get("required_state_grounded") or (
                    f.get("control_evidence_available")
                    and source_guess["category"] == "user_param"
                    and source_guess["exposed_to_user"]
                ) else {}),
                **({
                    "enum_source": select_meta.enum_source,
                    "enum_confirmed": select_meta.enum_confirmed,
                } if select_meta is not None else {}),
            },
            editable=bool(source_guess["editable"]),
            exposed_to_user=bool(source_guess["exposed_to_user"]),
            # A submitted request value is a replay sample, not a reusable
            # caller default. Defaults require explicit page/control evidence.
            default_value=(
                None
                if missing_wire_placeholder
                else f.get("visible_default")
                if f.get("visible_default") is not None
                else f.get("raw_value", f.get("value"))
                if (
                    _looks_pagination_field(nm, path)
                    or source_guess["source_kind"] in {"constant", "page_default"}
                )
                else None
            ),
            reason=_append_reason_detail(source_guess["reason"], enum_description),
            description=enum_description,
            need_human_confirm=bool(
                source_guess["need_human_confirm"]
                or (
                    source_guess["source_kind"] == "page_enum"
                    and select_meta is not None
                    and select_meta.enum_confirmed is False
                )
            ),
            evidence=evidence,
        ))

    # 补回 select 元数据的 param 字段
    path2key = {p.path: p.key for p in params}
    for sb, sraw in zip(selects_meta, selects_raw):
        sb.param = path2key.get(sraw.get("path", ""), "")

    # Fields carried by the selected option object are runtime projections, not
    # additional caller inputs. This covers project -> quota/team/type/approver.
    for binding in selects_meta:
        for target_path, response_path in (binding.field_projections or {}).items():
            target = next((param for param in params if param.path == target_path), None)
            if target is None or target.locked:
                continue
            if target.source_kind in {"user_input", "page_default"}:
                continue
            if _param_has_editable_control_evidence(target):
                continue
            target.category = "runtime_var"
            target.source_kind = "selected_option_field"
            target.source = {
                "kind": "selected_option_field",
                "selector_path": binding.path,
                "selector_param": binding.param,
                "source_url": binding.source_url,
                "response_path": response_path,
                "target_path": target_path,
            }
            target.exposed_to_user = False
            target.editable = False
            target.required = False
            target.need_human_confirm = False
            target.evidence.append({
                "kind": "selected_option_projection",
                "selector_path": binding.path,
                "source_url": binding.source_url,
                "response_path": response_path,
                "target_path": target_path,
            })
            target.reason = f"该字段来自选择项接口中已选记录的 `{response_path}`，运行期随选择自动写入"

    # sample_inputs
    sample_inputs = {p.key: p.value for p in params if p.value}

    # source_meta
    full_url = _request_url_with_query(req)
    source_meta = {
        "method": method,
        "url": full_url,
        "query": dict(req.get("query") or _request_query_values(req)),
        "headers_count": len(req.get("headers") or {}),
        "captured_at": req.get("captured_at"),
        "response_status": req.get("response_status"),
        "request_index": req.get("index"),
        "request_id": str(req.get("request_id") or req.get("id") or req.get("index") or ""),
        "page_id": req.get("page_id"),
        "frame_id": req.get("frame_id"),
        "role": request_role.get("role", ""),
        "keep": request_role.get("keep"),
        "keep_reason": request_role.get("keep_reason") or request_role.get("reason", ""),
        "filter_reason": request_role.get("filter_reason", ""),
        "confidence": request_role.get("confidence"),
        "evidence": request_role.get("evidence"),
        **{
            key: req.get(key)
            for key in _REQUEST_OBSERVER_KEYS
            if req.get(key) not in (None, "")
        },
    }

    path = _path_from_url(full_url)

    return FlowStep(
        name=_default_step_name(req),
        method=method,
        url=full_url,
        path=path,
        headers=extract_auth_headers(req.get("headers")),
        content_type=req.get("content_type") or "application/json",
        body_source=pd or "",
        body_template=None,
        params=params,
        selects=selects_meta,
        identity=identity_meta,
        system_values=sys_values,
        success_rule=sr,
        response_json=req.get("response_json"),
        risk_level=risk,
        semantic_role=request_role.get("semantic_role") or role.get("semanticRole", ""),
        source_meta=source_meta,
        sample_inputs=sample_inputs,
    )


def _request_url_with_query(req: dict) -> str:
    url = str(req.get("url") or req.get("path") or "")
    if "?" in url or not (query := _request_query_values(req)):
        return url
    return f"{url}?{urlencode(query, doseq=True)}"


def _params_from_url_path(req: dict, samples: dict | None = None) -> list[dict]:
    """Ground path parameters only when a URL segment matches one unique user sample."""
    parsed = urlparse(str(req.get("url") or req.get("path") or ""))
    segments = parsed.path.split("/")
    nonempty_positions = [index for index, segment in enumerate(segments) if segment]
    if not nonempty_positions:
        return []
    last_position = nonempty_positions[-1]
    sample_items = [
        (str(label), value)
        for label, value in (samples or {}).items()
        if value not in (None, "") and not _looks_pagination_field(str(label), str(label))
    ]
    out: list[dict] = []
    used_labels: set[str] = set()
    for position in nonempty_positions:
        segment = unquote(segments[position])
        matches = [
            (label, value) for label, value in sample_items
            if label not in used_labels and str(value) == segment
        ]
        if len(matches) != 1 or (position != last_position and len(segment) < 4):
            continue
        label, value = matches[0]
        used_labels.add(label)
        out.append({
            "path": f"path.{position}",
            "key": label,
            "suggest_name": label,
            "value": value,
            "raw_value": value,
            "type": _infer_type_from_value(value),
            "wire_type": _infer_type_from_value(value),
            "required": True,
            "confidence": 0.96,
            "confidence_tier": "grounded",
            "name_source": "sample",
            "recorded_user_input": True,
            "field_aliases": [label],
            "control_kind": "unknown",
        })
    return out


def _detect_query_selects(req: dict, samples: dict | None,
                          reads: list[dict], page_enum_options: dict | None,
                          field_evidence: list[dict] | None = None) -> list[dict]:
    """GET 请求的 query 参数本身也可能是某接口的下拉/枚举字段(典型如 /system/user/page?status=active)。
    把 query 视为扁平 key=值 结构,与 reads 候选做名→label 桥接、同上也试 DOM 选项。
    把命中路径重写为 `query.<key>` 以与 _params_from_get_query 的 path 对齐。通用,不挑系统。

    关键差异:接口型 select 既可能按 label 提交(显示名),也可能按 value 提交(状态码)。所以这里除
    suggest_selects 之外,还做一道 value-形态匹配置信信号 —— 当 query 值与 reads 候选的某
    「value/字典值字段」精准相等,即便没有 label 佐证,也以低置信度挂上 enum 标记,前端会把它
    当作低置信度 enum 项处理。"""
    flat = _params_from_get_query(req, samples, page_enum_options, field_evidence)
    if not flat:
        return []
    selectable_flat = [
        field for field in flat
        if not _looks_pagination_field(str(field.get("key") or ""), str(field.get("path") or ""))
    ]
    if not selectable_flat:
        return []
    syn_body: dict[str, Any] = {
        str(f.get("path") or "").split(".")[-1]: f.get("value")
        for f in selectable_flat if f.get("path")
    }
    synthetic_fields = [
        {**field, "path": str(field.get("path") or "").split(".")[-1]}
        for field in selectable_flat
    ]
    syn_pd = json.dumps(syn_body, ensure_ascii=False)

    # Query filters are especially prone to accidental value collisions: one
    # request commonly contains pageNo=1, billCode=1 and status=1, while an
    # unrelated option endpoint also contains ids 1/2/3.  A recorded form value
    # proves that the caller supplied the filter; it does *not* prove that a
    # candidate API owns that field.  Build DOM enums first (their control
    # name/id is structural evidence), then allow value-based API inference only
    # for fields for which no user input was recorded.
    page_selects = page_enum_selects(
        syn_pd,
        page_enum_options,
        set(),
        fields=synthetic_fields,
    )
    page_paths = {str(item.get("path") or "") for item in page_selects}

    api_fields = [
        field for field in synthetic_fields
        if not bool(field.get("recorded_user_input"))
        and str(field.get("path") or "") not in page_paths
    ]
    api_body = {
        str(field.get("path") or "").split(".")[-1]: field.get("value")
        for field in api_fields if field.get("path")
    }
    api_pd = json.dumps(api_body, ensure_ascii=False)
    api_selects = suggest_selects(
        api_pd, reads or [], samples, skip_paths=[], fields=api_fields,
    ) if api_fields else []
    # Explicit legacy ``reads`` may not carry DOM control metadata. Preserve
    # them only when the query leaf is an exact source token and its recorded
    # wire value resolves to one unambiguous ID/display row. The existing
    # selector then enforces a complete mapping and rejects competing sources.
    legacy_selects: list[dict] = []
    for field in api_fields:
        leaf = str(field.get("path") or "").split(".")[-1]
        wire_value = field.get("value")
        if not leaf or wire_value in (None, ""):
            continue
        inferred: list[dict] = []
        for read in reads or []:
            if str(read.get("role") or "") != "explicit_read_option":
                continue
            if leaf.casefold() not in _option_binding_tokens(read.get("url") or read.get("path") or ""):
                continue
            items = as_list_payload(read.get("json", read.get("response_json"))) or []
            matched_labels = {
                str(item.get(label_key) or "").strip()
                for item in items if isinstance(item, dict)
                for value_key, value in item.items()
                if _is_idlike(str(value_key)) and str(value) == str(wire_value)
                for label_key in [_pick_label_key(item, str(value_key))]
                if label_key != value_key and str(item.get(label_key) or "").strip()
            }
            if len(matched_labels) != 1:
                continue
            grounded_field = {
                **field,
                "control_kind": "select",
                "field_aliases": [leaf],
            }
            inferred.extend(suggest_selects(
                json.dumps({leaf: wire_value}, ensure_ascii=False),
                [read],
                {leaf: next(iter(matched_labels))},
                skip_paths=[],
                fields=[grounded_field],
            ))
        fingerprints = {
            (
                str(item.get("source_url") or ""),
                str(item.get("value_key") or ""),
                str(item.get("label_key") or ""),
                json.dumps(item.get("option_map") or {}, sort_keys=True, default=str),
            )
            for item in inferred
        }
        if len(fingerprints) == 1:
            legacy_selects.append(inferred[0])
    selects_raw = [*page_selects, *api_selects, *legacy_selects]

    # 重写 path 为 query.<key>,保持与 _params_from_get_query 的输出对齐
    for s in selects_raw or []:
        leaf_key = (s.get("path") or "").split(".")[-1].split("[")[0]
        if leaf_key and (s.get("path") or "").startswith("query.") is False:
            new_path = f"query.{leaf_key}"
            s["path"] = new_path
            if isinstance(s.get("id_path"), str) and s["id_path"]:
                id_leaf = s["id_path"].split(".")[-1].split("[")[0]
                if id_leaf:
                    s["id_path"] = f"query.{id_leaf}"
    return selects_raw


def _infer_wire_format(value: Any) -> str:
    """Infer the on-wire value format from a recorded sample (deterministic)."""
    if isinstance(value, bool) or value in (None, ""):
        return ""
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        try:
            number = int(value)
        except (TypeError, ValueError):
            return ""
        if 10**12 <= number < 4 * 10**12:
            return "epoch_ms"
        if 10**9 <= number < 4 * 10**9:
            return "epoch_s"
        return ""
    if isinstance(value, str):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?", value):
            return "datetime_text"
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return "date_text"
    return ""


def _param_contract_richness(param: ParamField) -> tuple[int, int, int, int, float]:
    options = list(param.enum_options or [])
    executable_options = sum(
        1 for option in options
        if isinstance(option, dict)
        and option.get("label") not in (None, "")
        and option.get("value") not in (None, "")
    )
    source_rank = {
        "api_option": 5,
        "page_enum": 4,
        "static_enum": 4,
        "manual_enum": 4,
        "form_option": 2,
        "user_input": 1,
        "constant": 1,
    }.get(str(param.source_kind or ""), 0)
    return (
        executable_options,
        len(param.enum_value_map or {}),
        source_rank,
        len(param.evidence or []),
        float(param.confidence or 0.0),
    )


def _step_contract_richness(step: FlowStep) -> tuple[int, int, int, int]:
    param_scores = [_param_contract_richness(param) for param in (step.params or [])]
    return (
        sum(score[0] + score[1] for score in param_scores),
        sum(score[2] + score[3] for score in param_scores),
        _response_shape_evidence_score(step.response_json),
        len(step.selects or []),
    )


def _merge_duplicate_step_contract(target: FlowStep, source: FlowStep) -> None:
    by_path = {str(param.path or ""): param for param in target.params if param.path}
    for source_param in source.params or []:
        path = str(source_param.path or "")
        target_param = by_path.get(path)
        if target_param is None:
            copied = source_param.model_copy(deep=True)
            target.params.append(copied)
            if path:
                by_path[path] = copied
            continue
        if _param_contract_richness(source_param) > _param_contract_richness(target_param):
            index = target.params.index(target_param)
            copied = source_param.model_copy(deep=True)
            _merge_enum_values(copied, target_param)
            target.params[index] = copied
            by_path[path] = copied
        else:
            _merge_enum_values(target_param, source_param)

    existing_selects = {
        json.dumps(binding.model_dump(exclude_none=True), ensure_ascii=False, sort_keys=True, default=str)
        for binding in (target.selects or [])
    }
    for binding in source.selects or []:
        marker = json.dumps(
            binding.model_dump(exclude_none=True), ensure_ascii=False, sort_keys=True, default=str,
        )
        if marker not in existing_selects:
            target.selects.append(binding.model_copy(deep=True))
            existing_selects.add(marker)

    if _response_shape_evidence_score(source.response_json) > _response_shape_evidence_score(target.response_json):
        target.response_json = copy.deepcopy(source.response_json)
    if not target.body_template and source.body_template:
        target.body_template = copy.deepcopy(source.body_template)
    if not target.body_source and source.body_source:
        target.body_source = source.body_source
    if not target.headers and source.headers:
        target.headers = dict(source.headers)
    target.sample_inputs = {**dict(source.sample_inputs or {}), **dict(target.sample_inputs or {})}


def _samples_for_captured_request(
    request: dict,
    *,
    samples: dict | None = None,
    form_samples_by_request: dict | None = None,
    form_samples_by_transaction: dict | None = None,
) -> dict:
    request_id = str(request.get("request_id") or "")
    if form_samples_by_request and request_id:
        extra = form_samples_by_request.get(request_id)
        if extra:
            return dict(extra)
    tx = _request_transaction_id(request)
    action = str(request.get("trigger_action_id") or "")
    if form_samples_by_transaction:
        extra = (
            form_samples_by_transaction.get(tx)
            or form_samples_by_transaction.get(action)
        )
        if extra:
            return dict(extra)
    return dict(samples or {})


def _step_role(step: FlowStep) -> str:
    return str((step.source_meta or {}).get("role") or step.semantic_role or "").casefold()


def _param_dedupe_key(param: ParamField) -> tuple[str, str]:
    path = _strip_body_prefix(str(param.path or "")).strip()
    key = str(param.key or param.label or "").strip()
    return (path, key if not path else "")


def _param_quality(param: ParamField) -> tuple[int, int, float]:
    source_score = 2 if param.source_kind not in {"", "unknown"} else 0
    if param.source_kind == "selected_option_field":
        source_score += 3
    elif param.source_kind in {"api_option", "page_enum", "static_enum", "manual_enum", "form_option"}:
        source_score += 2
    manual_score = 1 if param.name_source in {"manual", "llm", "planner", "assignee", "sample"} else 0
    return (source_score, manual_score, float(param.confidence or 0.0))


def _dedupe_step_params(step: FlowStep) -> None:
    if not step.params:
        return
    by_key: dict[tuple[str, str], ParamField] = {}
    order: list[tuple[str, str]] = []
    for param in step.params:
        key = _param_dedupe_key(param)
        if not key[0] and not key[1]:
            key = (param.path, param.key)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = param
            order.append(key)
            continue
        keep, drop = (param, existing) if _param_quality(param) > _param_quality(existing) else (existing, param)
        _merge_enum_values(keep, drop)
        by_key[key] = keep
    step.params = [by_key[key] for key in order if key in by_key]


def _query_key_from_param(param: ParamField) -> str:
    if param.path.startswith("query."):
        return param.path[len("query."):]
    return param.key


def _step_dedupe_key(step: FlowStep) -> tuple[str, str]:
    return ((step.method or "GET").upper(), _request_path({"url": step.path or step.url}))


def _is_dedupable_read_step(step: FlowStep) -> bool:
    if (step.method or "").upper() in _WRITE_METHODS:
        return False
    role = (step.source_meta or {}).get("role") or step.semantic_role or ""
    return role in {"", "business_get", "read_context", "read_option"}


def _dedupe_flow_steps(spec: FlowSpec) -> int:
    latest_by_key: dict[tuple[str, str], str] = {}
    for step in spec.steps:
        if _is_dedupable_read_step(step):
            latest_by_key[_step_dedupe_key(step)] = step.step_id

    keep_ids: set[str] = set()
    removed_ids: set[str] = set()
    for step in spec.steps:
        if _is_dedupable_read_step(step) and latest_by_key.get(_step_dedupe_key(step)) != step.step_id:
            removed_ids.add(step.step_id)
        else:
            keep_ids.add(step.step_id)

    if not removed_ids:
        return 0

    spec.steps = [step for step in spec.steps if step.step_id in keep_ids]
    spec.links = [
        lk for lk in spec.links
        if lk.source_step_id not in removed_ids and lk.target_step_id not in removed_ids
    ]
    spec.review_items = [
        item for item in spec.review_items
        if item.target.get("step_id") not in removed_ids
        and item.target.get("source_step_id") not in removed_ids
        and item.target.get("target_step_id") not in removed_ids
    ]
    spec.meta = {
        **(spec.meta or {}),
        "deduped_step_count": int(spec.meta.get("deduped_step_count") or 0) + len(removed_ids),
    }
    return len(removed_ids)


def _param_type_from_value(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "number"
    text = str(value or "")
    if re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return "number"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return "date"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?", text):
        return "datetime"
    return "string"


def _append_query_params_to_step(step: FlowStep, url: str) -> None:
    parsed = urlparse(url or "")
    query = parse_qs(parsed.query or "", keep_blank_values=True)
    if not query:
        return
    existing = {p.path for p in step.params}
    existing_keys = {p.key for p in step.params}
    for key, values in query.items():
        path = f"query.{key}"
        if not key or key in existing or path in existing or key in existing_keys:
            continue
        value = values[0] if values else ""
        source_guess = _param_source_guess(
            field={"path": path, "key": key, "value": value},
            path=path,
            key=key,
            method=(step.method or "GET").upper(),
            identity_paths=set(),
            system_paths=set(),
            select_paths=set(),
            select_id_paths=set(),
            samples=step.sample_inputs or {},
            request_headers=step.headers or {},
        )
        step.params.append(ParamField(
            path=path,
            key=key,
            label=key,
            value=str(value),
            type=_param_type_from_value(value),
            wire_type=_param_type_from_value(value),
            required=bool(source_guess.get("required")),
            category=source_guess["category"],
            source_kind=source_guess["source_kind"],
            source={**source_guess["source"], "from": "query"},
            exposed_to_user=bool(source_guess["exposed_to_user"]),
            editable=bool(source_guess["editable"]),
            need_human_confirm=bool(source_guess["need_human_confirm"]),
            default_value=(
                value if _looks_pagination_field(key, path) else None
            ),
            reason=source_guess["reason"],
        ))
        if value not in (None, ""):
            step.sample_inputs.setdefault(key, value)
        existing.add(path)
        existing_keys.add(key)


def _step_sequence(step: FlowStep) -> float | None:
    meta = step.source_meta or {}
    return _request_sequence_value(meta.get("sequence", meta.get("request_index")))


def _entry_sequence(entry: dict[str, Any]) -> float | None:
    return _request_sequence_value(entry.get("sequence", entry.get("request_index")))


def _insert_promoted_step(spec: FlowSpec, step: FlowStep, entry: dict[str, Any]) -> None:
    """把后加入接口插回合理执行位置，而不是一律追加到最后。"""
    seq = _entry_sequence(entry)
    if seq is not None:
        for idx, existing in enumerate(spec.steps):
            existing_seq = _step_sequence(existing)
            if existing_seq is not None and existing_seq > seq:
                spec.steps.insert(idx, step)
                return

    role = str(entry.get("role") or "")
    method = (step.method or entry.get("method") or "").upper()
    if method == "GET" or role in {"business_get", "read_context", "read_option"}:
        for idx, existing in enumerate(spec.steps):
            if (existing.method or "").upper() in _WRITE_METHODS:
                spec.steps.insert(idx, step)
                return

    spec.steps.append(step)


def _add_request_step_from_fact(spec: FlowSpec, entry: dict[str, Any]) -> FlowStep:
    request_id = str(entry.get("request_id") or "")
    request_index = entry.get("request_index")
    existing = None
    entry_sig = _request_signature(entry)
    for step in spec.steps:
        meta = step.source_meta or {}
        if request_id and str(meta.get("request_id") or "") == request_id:
            existing = step
            break
        if request_index is not None and meta.get("request_index") == request_index:
            existing = step
            break
        if not request_id and request_index is None and ((step.method or "").upper(), _request_path({"url": step.path or step.url})) == entry_sig:
            existing = step
            break
    if existing is None and not request_id and request_index is None:
        existing = next((
            s for s in spec.steps
            if ((s.method or "").upper(), _request_path({"url": s.path or s.url})) == entry_sig
        ), None)
    if existing is not None:
        _mark_request_materialized(spec, entry, materialized_step_id=existing.step_id)
        return existing

    role = {
        "role": entry.get("role") or "read_context",
        "keep": True,
        "reason": "人工从捕获请求加入流程步骤",
        "confidence": entry.get("confidence") or 0.8,
        "evidence": entry.get("evidence") or {},
    }
    req = {
        "index": entry.get("request_index"),
        "request_id": entry.get("request_id"),
        "method": entry.get("method") or "GET",
        "url": entry.get("url") or entry.get("path") or "",
        "headers": entry.get("headers") or {},
        "content_type": entry.get("content_type") or "application/json",
        "post_data": entry.get("post_data"),
        "response_status": entry.get("response_status"),
        "response_json": entry.get("response_json"),
    }
    reads_for_candidate = [
        {"url": s.url or s.path, "json": s.response_json}
        for s in spec.steps
        if s.response_json is not None
    ]
    for item in _request_fact_items(spec):
        if item.get("response_json") is not None:
            reads_for_candidate.append({"url": item.get("url") or item.get("path") or "", "json": item.get("response_json")})
    st = _build_step_from_capture(
        _attach_request_role(req, role),
        reads=reads_for_candidate,
        samples={},
        storage_state=None,
        required_labels=set(),
        page_enum_options=_page_enum_options_from_request_facts(spec.request_facts),
        field_evidence=list((spec.meta or {}).get("field_evidence") or []),
        step_index=len(spec.steps),
    )
    st.path = _request_path(entry)
    _append_query_params_to_step(st, entry.get("url") or entry.get("path") or "")
    st.source_meta = {
        **(st.source_meta or {}),
        "manual_added": True,
        "request_index": entry.get("request_index"),
        "request_id": entry.get("request_id"),
        "page_id": entry.get("page_id"),
        "frame_id": entry.get("frame_id"),
        "sequence": entry.get("sequence"),
        "promoted_at": datetime.now(timezone.utc).isoformat(),
    }
    _insert_promoted_step(spec, st, entry)
    _mark_request_materialized(spec, entry, materialized_step_id=st.step_id)
    return st


def promote_request_to_step(spec: FlowSpec, *, request_index: Any = None, request_id: str = "") -> FlowStep:
    """把 RequestFacts 事实提升为可执行 FlowStepTemplate。

    这是录制 V2 的唯一请求加入入口：手工加入、能力加入、自动修复和发布补齐都走这里。
    """
    entry = _find_request_fact_item(spec, request_index=request_index, request_id=request_id)
    if entry is None:
        raise ValueError(f"captured request not found: {request_index or request_id}")
    return _add_request_step_from_fact(spec, entry)


_PENDING_FLOW_SPEC_HELPERS = ('_BORING_COMPOSITE_VALUES', '_ENUM_PARAM_TYPES', '_OPTION_SOURCE_KINDS', '_append_reason_detail', '_attach_select_field_projections', '_composite_values_match', '_enum_label_value', '_enum_options_description', '_enum_options_for_param', '_enum_value_map_for_param', '_field_has_unlocked_editable_control', '_infer_type_from_value', '_is_missing_wire_placeholder', '_looks_internal', '_mark_request_materialized', '_merge_enum_values', '_option_binding_tokens', '_option_candidate_reads', '_page_enum_options_for_request', '_param_has_editable_control_evidence', '_param_source_guess', '_projection_path_score', '_read_is_option_source', '_response_shape_evidence_score', '_strip_body_prefix', '_timestamp_is_near_request',)


def _bind_flow_spec_helpers() -> None:
    import sys
    _flow_spec = sys.modules.get("dano.execution.page.flow_spec")
    if _flow_spec is None or not hasattr(_flow_spec, "to_flow_spec"):
        return
    module_globals = globals()
    for name in _PENDING_FLOW_SPEC_HELPERS:
        if hasattr(_flow_spec, name):
            module_globals[name] = getattr(_flow_spec, name)
