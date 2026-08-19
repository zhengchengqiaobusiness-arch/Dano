"""Pure client-facing FlowSpec projections and redaction."""
from __future__ import annotations

from typing import Any
import json
from dano.execution.page.flow_spec_core.models import (
    FlowSpec,
    FlowStep,
    ParamField,
    SelectBinding,
)
from dano.execution.page.request_capture import (
    bounded_response_sample,
    normalized_leaf_paths,
)
from dano.execution.page.capability_contracts import (
    _PUBLIC_ROLE_BY_INTERNAL,
    _PUBLIC_SOURCE_BY_INTERNAL,
    _title_without_step_suffix,
)
from dano.execution.page.flow_materialization.titles import (
    _derive_step_name,
)
from dano.execution.page.flow_spec_core.fingerprints import (
    _flow_fingerprint,
)
from dano.execution.page.flow_materialization.field_contracts.option_sync import (
    _hydrate_select_source_contract,
)
from dano.execution.page.flow_spec_core.normalization import (
    _strip_body_prefix,
)


def flow_spec_to_summary(spec: FlowSpec) -> dict:
    spec = sync_flow_spec_models(spec.model_copy(deep=True))
    summary_meta = dict(spec.meta or {})
    summary_meta.pop("request_graph", None)
    return {
        "flow_id": spec.flow_id,
        "title": spec.title,
        "recording_mode": spec.recording_mode,
        "diagnostic_count": len(spec.diagnostics),
        "step_count": len(spec.steps),
        "link_count": len(spec.links),
        "capability_count": len(spec.capabilities or []),
        "review_count": len(spec.review_items),
        "current_version": spec.meta.get("current_version"),
        "risk_level": spec.risk_level,
        "schema_version": spec.schema_version,
        "capabilities": [
            {
                "name": c.name,
                "title": c.title,
                "kind": c.kind,
                "step_ids": c.step_ids,
                "confirmed": c.confirmed,
                "requires_human_confirm": c.requires_human_confirm,
                "confidence": c.confidence,
            }
            for c in (spec.capabilities or [])
        ],
        "steps": [
            {
                "step_id": s.step_id,
                "name": s.name,
                "method": s.method,
                "path": s.path,
                "risk_level": s.risk_level,
                "param_count": len(s.params),
                "select_count": len(s.selects),
                "identity_count": len(s.identity),
            }
            for s in spec.steps
        ],
        "links": [
            {
                "link_id": link.link_id,
                "source_step_id": link.source_step_id,
                "source_path": link.source_path,
                "target_step_id": link.target_step_id,
                "target_path": link.target_path,
                "confirmed": link.confirmed,
                "confidence": link.confidence,
            }
            for link in spec.links
        ],
        "meta": summary_meta,
    }


_CLIENT_SECRET_KEY_HINTS = (
    "authorization", "cookie", "token", "satoken", "jwt", "password", "passwd",
    "secret", "credential", "session", "ticket",
)


def _client_redact_sensitive(node, key_hint: str = ""):
    key_l = str(key_hint or "").lower()
    if isinstance(node, dict):
        grounded_identity = any(
            node.get(key) not in (None, "", [])
            for key in ("wire_path", "path", "field_key", "field_aliases", "key", "field")
        )
        value_hint = next((
            str(node.get(key) or "")
            for key in ("wire_path", "path", "field_key", "key", "field")
            if node.get(key) not in (None, "")
        ), key_hint)
        aliases = " ".join(str(value) for value in (node.get("field_aliases") or []))
        if aliases:
            value_hint = f"{value_hint} {aliases}".strip()
        return {
            k: _client_redact_sensitive(
                v,
                value_hint if str(k) in {
                    "value", "selected_value", "visible_value", "default", "default_value",
                } else (
                    key_hint
                    if not grounded_identity and any(h in key_l for h in _CLIENT_SECRET_KEY_HINTS)
                    else str(k)
                ),
            )
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [_client_redact_sensitive(v, key_hint) for v in node]
    if key_l and any(h in key_l for h in _CLIENT_SECRET_KEY_HINTS):
        return "***"
    return node


def _client_response_projection(value: Any) -> tuple[Any, dict[str, Any]]:
    """Return a bounded UI sample plus facts describing the raw response."""
    sample = bounded_response_sample(value)
    try:
        raw_chars = len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str))
        sample_chars = len(json.dumps(sample, ensure_ascii=False, separators=(",", ":"), default=str))
    except Exception:  # noqa: BLE001 - projection metadata is best effort
        raw_chars = sample_chars = 0
    return sample, {
        "raw_chars": raw_chars,
        "sample_chars": sample_chars,
        "truncated": bool(raw_chars > sample_chars),
        "normalized_paths": normalized_leaf_paths(value),
    }


def _public_source_kind(param: dict[str, Any]) -> str:
    internal = str(param.get("source_kind") or "")
    if internal in _PUBLIC_SOURCE_BY_INTERNAL:
        return _PUBLIC_SOURCE_BY_INTERNAL[internal]
    if internal == "unknown":
        return "unknown"
    return "caller_input" if param.get("exposed_to_user") else "constant"


def _public_request_role(role: Any) -> str:
    return _PUBLIC_ROLE_BY_INTERNAL.get(str(role or ""), "support")


def flow_spec_to_client(spec: FlowSpec) -> dict:
    """Return the bounded, redacted projection used by the recording workbench.

    The browser never returns this projection as an authoritative FlowSpec, so
    raw request bodies, transport headers, identities and full responses can
    remain exclusively on the server.
    """
    client_spec = sync_flow_spec_models(spec.model_copy(deep=True))
    _normalize_capability_references(client_spec)
    data = refresh_review_items(_sync_capability_io_schemas(client_spec)).model_dump()
    data["meta"] = {**(data.get("meta") or {}), "current_fingerprint": _flow_fingerprint(spec)}
    data["meta"].pop("request_graph", None)
    request_facts = data.get("request_facts") or {}
    for analysis in (request_facts.get("analysis") or {}).values():
        if isinstance(analysis, dict):
            analysis["role"] = _public_request_role(analysis.get("role"))
    for evidence_key in ("field_evidence", "option_sources", "page_events"):
        if request_facts.get(evidence_key):
            request_facts[evidence_key] = _client_redact_sensitive(request_facts[evidence_key])
    for req in request_facts.get("requests") or []:
        if req.get("headers"):
            req["headers"] = {k: "***" for k in (req.get("headers") or {})}
        if req.get("post_data") is not None:
            req["post_data"] = ""
        if req.get("response_json") is not None:
            projected, projection = _client_response_projection(req.get("response_json"))
            req["response_json"] = _client_redact_sensitive(projected)
            req["response_projection"] = projection
    for st in data.get("steps") or []:
        st["semantic_role"] = _public_request_role(st.get("semantic_role"))
        if isinstance(st.get("source_meta"), dict) and st["source_meta"].get("role"):
            st["source_meta"]["role"] = _public_request_role(st["source_meta"].get("role"))
        st["headers"] = {k: "***" for k in (st.get("headers") or {})}
        st["body_source"] = ""
        st["backup_body_source"] = ""
        for param in st.get("params") or []:
            if not isinstance(param, dict):
                continue
            # Keep the evidence-backed origin (api_option / page_enum /
            # page_default / page_rule / ...). The 7-kind public contract is
            # only a grouping of who supplies the value.
            param["public_source_kind"] = _public_source_kind(param)
            # ``category`` is an internal compatibility value derived from
            # source_kind.  Exposing both axes lets clients create impossible
            # combinations, so the workbench owns only the executable source.
            param.pop("category", None)
            if isinstance(param.get("source"), dict) and not param["source"].get("kind"):
                param["source"] = {**param["source"], "kind": param["source_kind"]}
        if st.get("response_json") is not None:
            projected, projection = _client_response_projection(st.get("response_json"))
            st["response_json"] = _client_redact_sensitive(projected)
            st["response_projection"] = projection
        for select in st.get("selects") or []:
            if select.get("source_headers"):
                select["source_headers"] = {k: "***" for k in (select.get("source_headers") or {})}
            if select.get("source_body") is not None:
                select["source_body"] = ""
        for idn in st.get("identity") or []:
            if idn.get("value") is not None:
                idn["value"] = "***"
    return data


_CLIENT_SERVER_OWNED_STEP_FIELDS = frozenset({
    "headers", "body_source", "backup_body_source", "response_json", "response_projection",
    "identity", "params", "sample_inputs", "source_meta", "url", "path", "method", "content_type",
})


_CLIENT_SELECT_EDIT_FIELDS = frozenset({
    "param", "path", "source_url", "value_key", "label_key", "category_key", "category_value",
    "multi", "element_template", "label_subkey", "count", "options", "option_map", "enum_source",
    "enum_confirmed", "id_path", "id_tokens", "field_projections",
})


def _client_select_patch(spec: FlowSpec, edit: dict[str, Any]) -> dict[str, Any]:
    """Convert one select-binding patch into a safe step edit.

    Transport facts (headers/body/content type/request identity) never come from
    the browser. Existing values remain server-owned; a changed source is
    rehydrated from RequestFacts.
    """
    step_id = str(edit.get("step_id") or "")
    if not step_id:
        raise ValueError("upsert_select missing step_id")
    raw = edit.get("binding")
    if not isinstance(raw, dict):
        raise ValueError("upsert_select missing binding object")
    step = _find_step(spec, step_id)
    path = str(raw.get("path") or "")
    param = str(raw.get("param") or "")
    if not path and not param:
        raise ValueError("upsert_select requires path or param")
    target_param = _resolve_param_reference(step, path) if path else None
    param_name_is_unique = bool(param) and sum(item.key == param for item in step.params) == 1
    index = next((
        idx for idx, current in enumerate(step.selects)
        if (
            path
            and target_param is not None
            and _reference_targets_param(step, current.path or current.id_path or "", target_param)
        )
        or (
            param_name_is_unique
            and not current.path
            and not current.id_path
            and current.param == param
        )
    ), -1)
    existing = step.selects[index] if index >= 0 else None
    source_changed = bool(
        "source_url" in raw
        and str(raw.get("source_url") or "") != str(existing.source_url if existing else "")
    )
    merged = existing.model_dump() if existing is not None and not source_changed else {}
    for field in _CLIENT_SELECT_EDIT_FIELDS:
        if field in raw:
            merged[field] = raw[field]
    binding = SelectBinding.model_validate(merged)
    if existing is None or source_changed:
        _hydrate_select_source_contract(spec, binding)
    next_selects = list(step.selects)
    if index >= 0:
        next_selects[index] = binding
    else:
        next_selects.append(binding)
    return {
        "op": "update",
        "step_id": step_id,
        "field": "selects",
        "value": [item.model_dump() for item in next_selects],
    }


_CLIENT_SOURCE_KINDS = frozenset({
    "caller_input", "constant", "session", "context", "response_binding", "computed",
    "generated",
})


def _client_source_patch(spec: FlowSpec, edit: dict[str, Any]) -> list[dict[str, Any]]:
    step = _find_step(spec, str(edit.get("step_id") or ""))
    param = _find_param(
        step,
        str(edit.get("param_path") or ""),
        field_id=str(edit.get("field_id") or ""),
        param_key=str(edit.get("param_key") or ""),
        param_label=str(edit.get("param_label") or ""),
    )
    public_kind = str(edit.get("value") or "")
    if public_kind not in _CLIENT_SOURCE_KINDS and public_kind not in _INTERNAL_SOURCE_CONTRACT:
        raise ValueError(f"unsupported parameter source: {public_kind}")
    current_kind = str(param.source_kind or "")
    current_public = _PUBLIC_SOURCE_BY_INTERNAL.get(current_kind, "")
    source = dict(param.source or {})
    if public_kind in _INTERNAL_SOURCE_CONTRACT and public_kind not in _CLIENT_SOURCE_KINDS:
        internal_kind = public_kind
        category, exposed = _INTERNAL_SOURCE_CONTRACT[public_kind]
        source = {**source, "kind": internal_kind, "path": param.path}
        base = {
            "op": "update",
            "actor": "user",
            "step_id": step.step_id,
            "param_path": param.path,
        }
        return [
            {**base, "field": "source_kind", "value": internal_kind},
            {**base, "field": "source", "value": source},
            {**base, "field": "category", "value": category},
            {**base, "field": "exposed_to_user", "value": exposed},
        ]

    if public_kind == "caller_input":
        internal_kind = current_kind if current_public == public_kind else "user_input"
        source = {**source, "kind": internal_kind, "path": param.path}
        category, exposed = "user_param", True
    elif public_kind == "constant":
        internal_kind = "constant"
        source = {"kind": "constant", "path": param.path}
        category, exposed = "system_const", False
    elif public_kind == "session":
        if current_public == public_kind:
            internal_kind = current_kind
        elif param.path.startswith("headers."):
            internal_kind = "request_header"
            source = {"kind": "request_header", "header": param.path.split(".", 1)[1]}
        else:
            internal_kind = "current_user"
            source = {"kind": "identity", "path": param.path}
        category, exposed = "runtime_var", False
    elif public_kind == "context":
        internal_kind = "page_context"
        context_key = str(source.get("context_key") or param.key or "").strip()
        if not context_key:
            raise ValueError(f"context source requires an explicit key: {param.path}")
        source = {**source, "kind": "page_context", "context_key": context_key, "path": param.path}
        category, exposed = "runtime_var", False
    elif public_kind == "response_binding":
        if current_public != public_kind:
            raise ValueError(
                f"response_binding must be configured from a recorded dependency: {param.path}"
            )
        internal_kind = current_kind
        category, exposed = "runtime_var", False
    elif public_kind == "computed":
        if current_public != public_kind:
            raise ValueError(f"computed source requires an existing executable formula: {param.path}")
        internal_kind = current_kind
        category, exposed = "runtime_var", False
    else:
        if current_public != public_kind:
            raise ValueError(f"generated source requires an existing executable strategy: {param.path}")
        internal_kind = current_kind
        category, exposed = "runtime_var", False

    base = {
        "op": "update",
        "actor": "user",
        "step_id": step.step_id,
        "param_path": param.path,
    }
    return [
        {**base, "field": "source_kind", "value": internal_kind},
        {**base, "field": "source", "value": source},
        {**base, "field": "category", "value": category},
        {**base, "field": "exposed_to_user", "value": exposed},
    ]


def _description_param_key(param: ParamField) -> str:
    return param.label or param.key or param.path


def _description_source_text(param: ParamField) -> str:
    source = param.source or {}
    kind = param.source_kind or "unknown"
    if kind == "previous_response":
        step = source.get("step_name") or source.get("step_id") or "前置步骤"
        path = source.get("response_path") or "响应字段"
        return f"来自 {step} 的 {path}"
    if kind == "current_user":
        return "运行期从当前登录态读取"
    if kind == "request_header":
        header = source.get("header") or "请求头"
        return f"运行期从请求头 {header} 读取"
    if kind == "system_time":
        return "运行期由系统时间生成"
    if kind == "system_generated":
        labels = {"uuid": "UUID", "random_string": "随机字符串", "random_number": "随机数字"}
        strategy = str(source.get("strategy") or "uuid")
        return f"运行期由系统生成 {labels.get(strategy, strategy)}"
    if kind == "computed":
        return "运行期根据其它调用参数自动计算"
    if kind == "page_context":
        return "运行期从页面/应用上下文读取"
    if kind == "api_option":
        return "来自接口候选源"
    if kind == "page_enum":
        return "来自录制页面固定下拉"
    if kind == "manual_enum":
        return "来自人工维护枚举"
    if kind == "static_enum":
        return "来自固定枚举候选"
    if kind == "form_option":
        return "来自选择型字段"
    if kind == "constant":
        return "录制流程内固定值"
    if kind == "user_input":
        return "来自用户录制输入"
    return "来源待确认"


def _description_value(value: Any) -> str:
    if value in (None, ""):
        return ""
    text = str(value)
    return text if len(text) <= 80 else f"{text[:77]}..."


def _description_rule(rule: dict[str, Any] | None) -> str:
    if not rule:
        return "使用通用 HTTP/响应成功判断"
    try:
        text = json.dumps(rule, ensure_ascii=False, default=str)
    except Exception:
        text = str(rule)
    return text if len(text) <= 160 else f"{text[:157]}..."


def _unique_params(spec: FlowSpec, category: str) -> list[tuple[FlowStep, ParamField]]:
    seen: set[tuple[str, str]] = set()
    out: list[tuple[FlowStep, ParamField]] = []
    for st in spec.steps:
        for p in st.params:
            if p.category != category:
                continue
            key = (p.key or p.path, p.source_kind or "")
            if key in seen:
                continue
            seen.add(key)
            out.append((st, p))
    return out


def _semantic_purpose(spec: FlowSpec) -> str:
    semantic_plan = ((spec.meta or {}).get("capability_model") or {}).get("semantic_plan") or {}
    understanding = semantic_plan.get("business_understanding") if isinstance(semantic_plan, dict) else None
    if isinstance(understanding, dict):
        grounded = str(
            understanding.get("intent")
            or understanding.get("purpose")
            or understanding.get("summary")
            or ""
        ).strip()
        if grounded:
            return grounded[:240]
    return ""


def _default_purpose(spec: FlowSpec) -> str:
    if not spec.steps:
        return "本流程未包含任何操作步骤，暂不能生成可执行 Skill。"
    title = _title_without_step_suffix(spec.title) or (spec.steps[-1].name or _derive_step_name(spec.steps[-1]))
    return (
        f"该 Skill 用于按录制得到的 {len(spec.steps)} 个步骤执行「{title}」，"
        "并在运行期重新解析用户参数、系统常量和接口依赖。"
    )


def render_business_description(spec: FlowSpec) -> str:
    """Generate a deterministic description from accepted FlowSpec facts."""
    current = refresh_review_items(_sync_capability_io_schemas(spec.model_copy(deep=True)))
    lines: list[str] = [
        "# 业务流程说明",
        "",
        "## 1. 业务目的",
        _semantic_purpose(current) or _default_purpose(current),
        "",
        "## 对外业务能力",
    ]

    if current.capabilities:
        by_id = {s.step_id: s for s in current.steps}
        for i, cap in enumerate(current.capabilities, 1):
            kind_label = {
                "query_status": "状态查询",
                "list_options": "选项列表",
                "validate_batch": "批量校验",
                "submit_batch": "批量提交",
                "submit": "提交",
            }.get(cap.kind, cap.kind)
            status = "已确认" if cap.confirmed else "未确认"
            lines.append(f"{i}. {cap.title or cap.name}（{kind_label}，{status}）")
            if cap.intent:
                lines.append(f"   - 说明：{cap.intent}")
            cap_steps = [by_id[sid] for sid in (cap.step_ids or []) if sid in by_id]
            if cap_steps:
                chain = " -> ".join(f"{st.method} {st.path or st.url}" for st in cap_steps)
                lines.append(f"   - 接口链：`{chain}`")
            props = (cap.input_schema or {}).get("properties") or {}
            required = set((cap.input_schema or {}).get("required") or [])
            if props:
                fields = []
                for key, schema in list(props.items())[:20]:
                    typ = schema.get("type") if isinstance(schema, dict) else "string"
                    req = "必填" if key in required else "可选"
                    fields.append(f"{key}:{typ}/{req}")
                lines.append(f"   - 输入：{', '.join(fields)}")
            if cap.caller_responsibilities:
                lines.append(f"   - 调用方负责：{'；'.join(map(str, cap.caller_responsibilities))}")
            if cap.skill_responsibilities:
                lines.append(f"   - Skill 负责：{'；'.join(map(str, cap.skill_responsibilities))}")
    else:
        lines.append("- 未生成业务能力编排，请先点击“生成/优化编排”。")

    lines.extend([
        "",
        "## 2. 用户需要提供的参数",
    ]
    )

    user_params = [(s, p) for s, p in _unique_params(current, "user_param") if p.exposed_to_user]
    if user_params:
        for _st, p in user_params:
            required = "必填" if p.required else "可选"
            reason = p.reason or _description_source_text(p)
            lines.append(f"- {_description_param_key(p)}：{p.type}，{required}。{reason}")
    else:
        lines.append("- 无。当前 FlowSpec 没有暴露给用户的 user_param。")

    lines.extend(["", "## 3. 系统自动处理的变量"])
    runtime_params = _unique_params(current, "runtime_var")
    if runtime_params:
        for _st, p in runtime_params:
            lines.append(f"- {_description_param_key(p)}：{_description_source_text(p)}。")
    else:
        lines.append("- 无。")

    lines.extend(["", "## 4. 固定系统常量"])
    const_params = _unique_params(current, "system_const")
    if const_params:
        for _st, p in const_params:
            value = _description_value(p.value)
            suffix = f"，录制值 `{value}`" if value else ""
            confirm = "，需人工确认" if p.need_human_confirm else ""
            lines.append(f"- {_description_param_key(p)}：{_description_source_text(p)}{suffix}{confirm}。")
    else:
        lines.append("- 无。")

    lines.extend(["", "## 5. 执行步骤"])
    if current.steps:
        for i, st in enumerate(current.steps, 1):
            name = st.name or _derive_step_name(st)
            role = st.source_meta.get("role") or st.semantic_role or "business_step"
            lines.append(f"{i}. {name}")
            lines.append(f"   调用 `{st.method} {st.path or st.url}`，角色 `{role}`，风险等级 `{st.risk_level}`。")
    else:
        lines.append("无可执行步骤。")

    lines.extend(["", "## 6. 接口依赖关系"])
    if current.links:
        for lk in current.links:
            source = next((s for s in current.steps if s.step_id == lk.source_step_id), None)
            target = next((s for s in current.steps if s.step_id == lk.target_step_id), None)
            source_name = source.name or source.path if source else lk.source_step_id
            target_name = target.name or target.path if target else lk.target_step_id
            status = "已确认" if lk.confirmed else "待确认"
            lines.append(f"- {source_name}.response.{lk.source_path} -> {target_name}.body.{_strip_body_prefix(lk.target_path)}（{status}）。")
    else:
        lines.append("- 未发现跨接口字段依赖。")

    lines.extend(["", "## 7. 成功判断"])
    if current.steps:
        for st in current.steps:
            name = st.name or _derive_step_name(st)
            lines.append(f"- {name}：{_description_rule(st.success_rule)}。")
    else:
        lines.append("- 无。")

    lines.extend(["", "## 8. 风险与注意事项"])
    risks: list[str] = [f"整体风险等级为 `{current.risk_level}`。"]
    if any(p.category == "runtime_var" and p.source_kind == "unknown" for st in current.steps for p in st.params):
        risks.append("存在来源未知的 runtime_var，不能直接使用录制旧值。")
    if any(p.category == "system_const" and p.exposed_to_user for st in current.steps for p in st.params):
        risks.append("存在仍暴露给用户的 system_const，需要隐藏或改分类。")
    if any(st.method == "GET" and not st.body_source for st in current.steps):
        risks.append("存在 GET 前置步骤，执行时会按 query_template 构造运行期 URL。")
    for risk in risks:
        lines.append(f"- {risk}")

    lines.extend([
        "",
        "## 8.1 失败处理",
        "- 任一步接口返回失败、响应无法解析或必需依赖取值为空时，立即停止后续写操作，并向调用方返回失败步骤、接口路径和原始错误摘要。",
        "- 写操作不做隐式重试；是否重试由调用方根据幂等性和业务确认结果决定。",
    ])

    lines.extend(["", "## 9. 需要人工确认的问题"])
    unresolved = [item for item in current.review_items if not item.resolved]
    if unresolved:
        for item in unresolved[:20]:
            target = item.target.get("path") or item.target.get("link_id") or item.target.get("step_id") or item.target.get("path")
            target_text = f" `{target}`" if target else ""
            lines.append(f"- [{item.severity}] {item.title}{target_text}：{item.reason}")
        if len(unresolved) > 20:
            lines.append(f"- 另有 {len(unresolved) - 20} 个待确认项，请在 FlowSpec 编辑器中查看。")
    else:
        lines.append("- 无。")

    return "\n".join(lines)

_PENDING_FLOW_SPEC_HELPERS = {'_INTERNAL_SOURCE_CONTRACT': 'dano.execution.page.flow_spec_core.controlled_edits', '_find_param': 'dano.execution.page.flow_spec_core.controlled_edits', '_find_step': 'dano.execution.page.flow_spec_core.controlled_edits', '_reference_targets_param': 'dano.execution.page.flow_spec_core.controlled_edits', '_resolve_param_reference': 'dano.execution.page.flow_spec_core.controlled_edits', 'refresh_review_items': 'dano.execution.page.flow_materialization.review_items', 'sync_flow_spec_models': 'dano.execution.page.flow_materialization.builder', '_normalize_capability_references': 'dano.execution.page.capability_nodes', '_sync_capability_io_schemas': 'dano.execution.page.capability_io'}


def _bind_flow_spec_helpers() -> None:
    import sys
    module_globals = globals()
    for name, owner in _PENDING_FLOW_SPEC_HELPERS.items():
        mod = sys.modules.get(owner)
        if mod is None or not hasattr(mod, name):
            continue
        module_globals[name] = getattr(mod, name)


_bind_flow_spec_helpers()
