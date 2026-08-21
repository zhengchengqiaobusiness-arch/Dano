"""Stage 5: option catalogs, enum maps, and selected-row projection."""
from __future__ import annotations

from typing import Any
import json
from urllib.parse import urlparse, parse_qs, urlencode
import re
from dano.execution.page.flow_spec_core.models import (
    FlowSpec,
    ParamField,
    SelectBinding,
)
from dano.execution.page.request_capture import (
    _leaf_paths,
    as_list_payload,
    looks_like_read_request,
)
from dano.execution.page.recording_facts import (
    _SCREENSHOT_OPTION_CONTROL_KINDS,
    _choice_control_triggered,
    _field_leaf_token,
    _list_payload_has_reference_contract,
    _looks_pagination_field,
    _read_is_entity_enrichment_lookup,
    _recording_evidence_matches_request,
    _request_has_business_query_evidence,
    _request_has_option_endpoint_hint,
    _request_has_reference_entity_hint,
    _request_path,
)
from dano.execution.page.flow_materialization.field_contracts.caller_ownership import (
    _field_has_unlocked_editable_control,
    _param_has_editable_control_evidence,
    _param_was_caller_typed,
)
from dano.execution.page.flow_materialization.field_contracts.common import (
    _looks_audit_system_leaf,
    _looks_page_context_field,
    _param_control_is_readonly,
    _param_control_kinds,
    _param_group_prefix,
    _param_has_manual_contract,
)
from dano.execution.page.flow_materialization.field_contracts.record_identity import (
    _looks_row_identity_leaf,
    _param_is_document_record_identity,
)


_OPTION_SOURCE_KINDS = {"api_option", "page_enum", "static_enum", "manual_enum", "form_option"}


def _is_option_source_url(url: str) -> bool:
    path = _request_path({"url": url}).lower()
    segs = {s for s in re.split(r"[^a-z0-9]+", path) if s}
    if segs & {"dict", "dictionary", "option", "options", "select", "simple", "simplelist", "tree", "candidate", "candidates"}:
        return True
    if path.endswith(("/list", "/simple-list", "/tree", "/select", "/options", "/candidates")):
        return True
    last = path.rsplit("/", 1)[-1]
    if re.search(r"(?:^|[-_])(?:get|query|select)?[a-z0-9]*(?:list|tree|options?|candidates?)(?:by|$|[-_])", last):
        return True
    return False


def _read_is_option_source(read: dict) -> bool:
    role = str(read.get("role") or read.get("request_role") or "")
    payload = read.get("json", read.get("response_json"))
    has_list_payload = bool(as_list_payload(payload))
    if _read_is_entity_enrichment_lookup(read):
        return False
    if _request_has_business_query_evidence(read):
        # A business collection and an option endpoint can both return rows
        # shaped like ``{id, name}``.  A recorded search/list action with real
        # business filters owns those fields as caller-facing query inputs; its
        # result shape alone must not turn the whole request into a chooser.
        return False
    if role == "explicit_read_option":
        return has_list_payload
    if role == "read_option":
        return has_list_payload
    if has_list_payload and _choice_control_triggered(read):
        # Endpoint naming is not portable. A list response causally triggered by
        # opening/selecting a choice control is grounded option-source evidence.
        return True
    if not role and has_list_payload and _list_payload_has_reference_contract(payload):
        # Explicit read snapshots may predate causal metadata. They are only
        # candidates here; field binding still requires an exact selected wire
        # value plus control/semantic ownership and rejects collisions.
        return True
    return False


def _read_transport_can_supply_options(read: dict) -> bool:
    """Allow ordinary reads and explicitly classified POST option queries."""
    method = str(read.get("method") or "GET").upper()
    if method in {"GET", "HEAD"}:
        return True
    if method != "POST" or _read_is_entity_enrichment_lookup(read):
        return False
    role = str(read.get("role") or read.get("request_role") or "")
    return role in {"read_option", "option_source", "explicit_read_option"} or (
        _choice_control_triggered(read)
        and looks_like_read_request(
            str(read.get("url") or read.get("path") or ""),
            read.get("post_data"),
        )
    )


def _read_is_business_entity_collection(read: dict, payload: Any) -> bool:
    """Return whether a business read can resolve one caller-selected entity."""
    return bool(
        str(read.get("role") or read.get("request_role") or "") == "business_get"
        and str(read.get("method") or "GET").upper() in {"GET", "HEAD"}
        and _list_payload_has_reference_contract(payload)
        and not _read_is_entity_enrichment_lookup(read)
        and not _request_has_option_endpoint_hint(read)
        and not _request_has_reference_entity_hint(read)
    )


def _option_candidate_reads(reads: list[dict] | None) -> list[dict]:
    return [
        read for read in (reads or [])
        if isinstance(read, dict)
        and not _read_is_entity_enrichment_lookup(read)
        and (
            _read_is_option_source(read)
            or str(
                read.get("role")
                or read.get("request_role")
                or (read.get("_request_role") or {}).get("role")
                or ""
            ) in {"option", "read_option", "option_source", "explicit_read_option"}
            or _choice_control_triggered(read)
        )
    ]


def _option_source_contract_endpoint(url: str) -> str:
    """Normalize snapshot pagination while preserving semantic query scope."""
    parsed = urlparse(str(url or ""))
    endpoint = f"{parsed.netloc}{parsed.path}" if parsed.path else str(url or "")
    snapshot_keys = {
        "cursor", "keyword", "search", "searchtext", "q", "query", "_",
        "ts", "timestamp", "nonce", "cachebuster", "sort", "sortby",
        "order", "orderby", "token", "accesstoken", "auth", "authorization",
        "apikey", "signature", "sign", "sig", "session", "sessionid", "jwt",
        "traceid", "spanid",
    }
    semantic_query: list[tuple[str, Any]] = []
    for key, values in sorted(parse_qs(parsed.query, keep_blank_values=True).items()):
        normalized_key = re.sub(r"[^a-z0-9]+", "", key.casefold())
        if (
            normalized_key in snapshot_keys
            or _looks_pagination_field(key, f"query.{key}")
        ):
            continue
        semantic_query.extend((key, value) for value in values)
    if semantic_query:
        endpoint += "?" + urlencode(semantic_query, doseq=True)
    return endpoint


def _select_source_kind(sel: SelectBinding | None) -> str:
    if sel is None:
        return "static_enum"
    if sel.enum_source == "dom":
        return "page_enum"
    if sel.enum_source == "manual":
        return "manual_enum"
    if sel.source_url:
        return "api_option"
    if sel.options:
        return "static_enum"
    return "static_enum"


def _select_source_reason(kind: str, *, id_field: bool = False) -> str:
    if id_field:
        return "该字段是选择项对应的内部 ID，运行期随用户选择自动写入，不暴露给用户手填"
    if kind == "api_option":
        return "该字段来自接口候选源，运行期从接口获取真实候选"
    if kind == "page_enum":
        return "该字段来自录制页面真实下拉快照，属于页面固定枚举"
    if kind == "manual_enum":
        return "该字段来自人工维护的枚举候选"
    if kind == "static_enum":
        return "该字段来自固定枚举候选"
    return "该字段来自选择型字段"


def _enum_options_for_param(sb) -> list | None:
    """把 SelectBinding 序列化成前端 + 运行期都好读的 enum_options:
    - 若有 option_map(label→value)→ 返回 [{label, value}] 字典列表(前端 DataList/Playbook 都能用)
    - 若只有 label → 返回 labels 字符串列表(向后兼容,前端显示用)
    - 没有枚举 → None
    通用,不绑具体业务。
    """
    if sb is None:
        return None
    om = sb.option_map if isinstance(sb.option_map, dict) else None
    opts = list(sb.options or [])
    out = []
    for o in opts:
        pair = _enum_label_value(o)
        if pair is None:
            continue
        label, parsed_value = pair
        if om and label in om:
            out.append({"label": label, "value": om[label]})
        elif (
            isinstance(o, dict) and "value" in o and o.get("value") is not None
        ) or (isinstance(o, (list, tuple)) and len(o) >= 2 and o[1] is not None):
            out.append({"label": label, "value": parsed_value})
        else:
            out.append(label)
    if om:
        return out or None
    if opts:
        return out or None
    return None


def _enum_value_map_for_param(sb) -> dict | None:
    """label → value 映射;前端隐藏 prompt + 运行期 API 用同一份。"""
    if sb is None:
        return None
    om = sb.option_map if isinstance(sb.option_map, dict) else None
    if om:
        return dict(om)
    derived = _enum_option_map_from_options(list(sb.options or []))
    if derived and any(str(k) != str(v) for k, v in derived.items()):
        return derived
    return None


def _enum_label_value(opt) -> tuple[str, Any] | None:
    """Normalize string/dict/list/tuple options to a display label and wire value."""
    if isinstance(opt, dict):
        raw_label = next(
            (
                opt.get(key) for key in ("label", "text", "name", "value")
                if opt.get(key) not in (None, "")
            ),
            "",
        )
        label = str(raw_label).strip()
        if not label:
            return None
        return label, opt.get("value", label)
    if isinstance(opt, (list, tuple)):
        if not opt:
            return None
        label = str(opt[0] if opt[0] is not None else "").strip()
        if not label:
            return None
        return label, opt[1] if len(opt) >= 2 else opt[0]
    label = str(opt or "").strip()
    if not label:
        return None
    return label, label


def _explicit_enum_value_map(options: list[Any] | None, value_map: dict[str, Any] | None) -> dict[str, Any]:
    """Keep only recorded or operator-supplied label/value pairs; never invent identity pairs."""
    explicit = dict(value_map or {})
    for option in options or []:
        if not (
            (isinstance(option, dict) and "value" in option)
            or (isinstance(option, (list, tuple)) and len(option) >= 2)
        ):
            continue
        pair = _enum_label_value(option)
        if pair is not None:
            explicit.setdefault(pair[0], pair[1])
    return explicit


def _enum_options_description(kind: str, options: list[Any] | None, value_map: dict[str, Any] | None = None) -> str | None:
    if not options:
        return None
    title = "页面枚举选项" if kind == "page_enum" else "枚举选项"
    if kind == "api_option":
        title = "接口候选选项"
    elif kind == "manual_enum":
        title = "手工枚举选项"
    elif kind == "static_enum":
        title = "固定枚举选项"
    elif kind == "form_option":
        title = "表单枚举选项"
    parts: list[str] = []
    seen: set[str] = set()
    for opt in options:
        pair = _enum_label_value(opt)
        if pair is None:
            continue
        label, value = pair
        if value_map and label in value_map:
            value = value_map[label]
        text = label if str(label) == str(value) else f"{label}={value}"
        if text in seen:
            continue
        seen.add(text)
        parts.append(text)
    if not parts:
        return None
    return f"{title}：{'、'.join(parts)}"


def _upsert_option_description(reason: str, detail: str | None) -> str:
    """Replace an older option snapshot from the same source instead of appending it."""
    reason = str(reason or "")
    if not detail:
        return reason
    prefix = detail.split("：", 1)[0]
    parts = [
        part for part in reason.split("；")
        if part.strip() and not part.strip().startswith(f"{prefix}：")
    ]
    parts.append(detail)
    return "；".join(parts)


_OPTION_DESCRIPTION_PREFIXES = (
    "页面枚举选项：", "接口候选选项：", "手工枚举选项：", "固定枚举选项：", "表单枚举选项：", "枚举选项：",
)


def _strip_option_descriptions(text: str | None) -> str:
    return "；".join(
        part for part in str(text or "").split("；")
        if part.strip() and not part.strip().startswith(_OPTION_DESCRIPTION_PREFIXES)
    )


def _enum_option_map_from_options(options: list[Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for opt in options or []:
        pair = _enum_label_value(opt)
        if pair and pair[1] is not None:
            out[pair[0]] = pair[1]
    return out


def _recorded_scalar_values_match(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    return type(left) is type(right) and left == right


_BORING_COMPOSITE_VALUES = frozenset({
    "", "0", "1", "true", "false", "null", "none", "undefined",
})


def _composite_values_match(left: Any, right: Any) -> bool:
    # Missing/empty values are absence, not correlation evidence. Treating two
    # empty strings as a match can project an unrelated optional response leaf
    # (for example an avatar URL) into an empty business request field.
    if left in (None, "") or right in (None, ""):
        return False
    if _recorded_scalar_values_match(left, right):
        return True
    if isinstance(left, bool) or isinstance(right, bool):
        return False
    try:
        return float(left) == float(right)
    except (TypeError, ValueError):
        return str(left).strip() == str(right).strip()


def _projection_path_score(source_path: str, target_path: str) -> int:
    def parts(value: str) -> list[str]:
        return [
            token.casefold()
            for token in re.findall(r"[A-Za-z]+|\d+|[\u4e00-\u9fff]+", str(value or ""))
        ]

    source_parts = parts(source_path)
    target_parts = parts(target_path)
    if not source_parts or not target_parts:
        return 0
    if source_parts[-1] == target_parts[-1]:
        return 100
    if len(source_parts) >= 2 and "".join(source_parts[-2:]) == "".join(target_parts[-1:]):
        return 90
    if target_parts[-1] == "id" and source_parts[-1] == "id":
        return 40
    if source_parts[-1] == "id" and target_parts[-1].endswith("id"):
        return 40
    return 0


def _projection_path_parts(value: Any) -> list[str]:
    raw_parts = [part for part in re.split(r"\.|\[\d+\]", str(value or "")) if part]
    return raw_parts


def _projection_leaf_norm(value: str) -> str:
    return re.sub(r"[^\w]+", "", str(value or ""), flags=re.UNICODE).replace("_", "").casefold()


def _projection_path_tokens(value: Any) -> list[str]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(value or ""))
    return [token.casefold() for token in re.findall(r"[A-Za-z]+|\d+|[\u4e00-\u9fff]+", text)]


def _projection_path_score(source_path: str, target_path: str) -> int:
    """Score a catalog leaf against a write-body sibling by structure, not vendor names.

    Exact leaves win.  A write leaf may carry an entity prefix
    (``productName`` ← ``name``, ``productId`` ← ``id``); a catalog path may
    also be more specific than the write leaf.  Quantity and total leaves are
    filtered by the caller so ``price`` cannot claim ``totalPrice``.
    """
    source_parts = _projection_path_parts(source_path)
    target_parts = _projection_path_parts(target_path)
    if not source_parts or not target_parts:
        return 0
    source_leaf_raw = source_parts[-1]
    target_leaf_raw = target_parts[-1]
    source_leaf = _projection_leaf_norm(source_leaf_raw)
    target_leaf = _projection_leaf_norm(target_leaf_raw)
    if source_leaf == target_leaf:
        return 100
    if len(source_parts) >= 2 and _projection_leaf_norm("".join(source_parts[-2:])) == target_leaf:
        return 90
    if len(target_parts) >= 2 and _projection_leaf_norm("".join(target_parts[-2:])) == source_leaf:
        return 90
    source_tokens = _projection_path_tokens(source_leaf_raw)
    target_tokens = _projection_path_tokens(target_leaf_raw)
    if source_tokens == target_tokens:
        return 100
    if (
        source_tokens
        and target_tokens
        and len(source_leaf) >= 3
        and target_leaf.endswith(source_leaf)
        and target_tokens[-len(source_tokens):] == source_tokens
    ):
        return 80
    if (
        source_tokens
        and target_tokens
        and len(target_leaf) >= 3
        and source_leaf.endswith(target_leaf)
        and source_tokens[-len(target_tokens):] == target_tokens
    ):
        return 80
    if (
        source_tokens
        and target_tokens
        and len(source_leaf) >= 3
        and target_tokens[:len(source_tokens)] == source_tokens
    ):
        return 78
    if (
        source_tokens
        and target_tokens
        and len(target_leaf) >= 3
        and source_tokens[:len(target_tokens)] == target_tokens
    ):
        return 78
    if (
        target_tokens and target_tokens[-1] == "id"
        and source_tokens and source_tokens[-1] == "id"
    ):
        return 40
    return 0


def _best_option_projection_path(
    row: dict[str, Any],
    target_path: str,
    value: Any,
    *,
    min_score: int = 75,
    allow_unique_value_fallback: bool = True,
) -> str:
    candidates = [
        (_projection_path_score(source_path, target_path), source_path)
        for source_path, _tokens, _raw_value, raw in _leaf_paths(row)
        if _composite_values_match(value, raw)
    ]
    best_score = max((score for score, _path in candidates), default=0)
    best_paths = [
        source_path for score, source_path in candidates
        if score == best_score and score >= min_score
    ]
    if len(best_paths) == 1:
        return best_paths[0]
    if best_paths:
        return ""
    if not allow_unique_value_fallback:
        return ""
    # Last-resort evidence tier: the captured target value occurs on exactly
    # one scalar leaf of the already selected row. This recovers projections
    # when frontend and backend use unrelated names without guessing from
    # position or a vendor vocabulary. Repeated values remain unresolved.
    same_value_paths = list(dict.fromkeys(source_path for _score, source_path in candidates))
    return same_value_paths[0] if len(same_value_paths) == 1 else ""


def _attach_select_field_projections(
    selects: list[dict],
    fields: list[dict],
    reads: list[dict],
) -> None:
    """Map sibling request fields from the same selected option object.

    A selector often returns the chosen record plus sibling business fields.
    Projection requires both equal captured values and a unique structural path
    match; endpoint names and vendor-specific field names are never consulted.
    """
    reads_by_path = {
        _request_path({"url": str(read.get("url") or read.get("path") or "")}): read
        for read in reads or [] if isinstance(read, dict)
    }

    for select in selects or []:
        source_url = str(select.get("source_url") or "")
        value_key = str(select.get("value_key") or "")
        label_key = str(select.get("label_key") or "")
        select_path = str(select.get("path") or "")
        if not source_url or not value_key or not label_key or not select_path:
            continue
        source = reads_by_path.get(_request_path({"url": source_url}))
        items = as_list_payload((source or {}).get("json", (source or {}).get("response_json"))) or []
        selected_field = next((item for item in fields if str(item.get("path") or "") == select_path), None)
        if selected_field is None:
            continue
        matches = [
            item for item in items if isinstance(item, dict)
            and _recorded_scalar_values_match(
                selected_field.get("raw_value", selected_field.get("value")),
                item.get(value_key),
            )
        ]
        if len(matches) != 1:
            continue
        selected_item = matches[0]
        excluded = {select_path, str(select.get("id_path") or "")}
        select_group = _param_group_prefix(select_path)
        projections: dict[str, str] = {}
        for field in fields:
            target_path = str(field.get("path") or "")
            if (
                not target_path
                or target_path in excluded
                or _param_group_prefix(target_path) != select_group
                or field.get("recorded_user_input")
                or _field_has_unlocked_editable_control(field)
                or _param_is_quantity_or_formula_leaf(str(field.get("key") or ""), target_path)
            ):
                continue
            response_path = _best_option_projection_path(
                selected_item,
                target_path,
                field.get("raw_value", field.get("value")),
            )
            if response_path:
                projections[target_path] = response_path
        if projections:
            select["field_projections"] = projections


def _page_enum_options_for_request(req: dict, options: dict | None) -> dict:
    return {
        str(key): item for key, item in (options or {}).items()
        if not isinstance(item, dict) or _recording_evidence_matches_request(req, item)
    }




def _param_has_option_control_evidence(param: ParamField) -> bool:
    return any(
        isinstance(item, dict)
        and (
            item.get("canonical_screenshot_control") is True
            or str(item.get("source") or item.get("kind") or "").lower()
            in {
                "recorder_dom", "page", "page_snapshot", "page_control",
                "screenshot", "reference_screenshot", "uploaded_screenshot",
            }
        )
        and str(item.get("control_kind") or "").lower()
        in _SCREENSHOT_OPTION_CONTROL_KINDS
        for item in (param.evidence or [])
    )






_ENUM_PARAM_TYPES = frozenset({"enum", "list-enum"})


_ENUM_SOURCE_KINDS = frozenset({
    "page_enum", "static_enum", "manual_enum", "form_option",
})


def _option_row_match_count(
    row: dict[str, Any], members: list[ParamField], *, allow_unique_value_fallback: bool,
) -> int:
    matched = 0
    for param in members:
        if param.value in (None, ""):
            continue
        if _param_is_quantity_or_formula_leaf(param.key, param.path):
            continue
        if _best_option_projection_path(
            row, param.path, param.value,
            allow_unique_value_fallback=allow_unique_value_fallback,
        ):
            matched += 1
    return matched


def _group_option_source_request_ids(members: list[ParamField]) -> tuple[bool, set[str]]:
    """Return genuine chooser ownership for one request-field group.

    Selected-row projections cannot establish their own catalog ownership;
    otherwise two coincidentally equal root fields can make an unrelated
    catalog self-confirming. Only a chooser contract (or a hydrated chooser
    carrying its option source) anchors projections for the group.
    """
    anchored = False
    request_ids: set[str] = set()
    for param in members:
        source = param.source or {}
        option_source = source.get("option_source")
        if param.source_kind in {"api_option", "form_option"}:
            anchored = True
            if source.get("source_request_id"):
                request_ids.add(str(source["source_request_id"]))
        elif param.source_kind == "previous_response" and isinstance(option_source, dict):
            anchored = True
            if option_source.get("source_request_id"):
                request_ids.add(str(option_source["source_request_id"]))
        for item in param.evidence or []:
            if not isinstance(item, dict) or item.get("source") != "option_source":
                continue
            anchored = True
            if item.get("source_request_id"):
                request_ids.add(str(item["source_request_id"]))
    return anchored, request_ids


def _infer_selected_option_row_fields(spec: FlowSpec) -> None:
    """Project write-body siblings from the unique captured option row they share."""
    modern_contract = int(
        (spec.meta or {}).get("stage_1_6_contract_version") or 0
    ) >= 2
    catalogs: list[tuple[str, list[dict[str, Any]]]] = []
    for fact in spec.request_facts.requests or []:
        analysis = spec.request_facts.analysis.get(str(fact.request_id or "")) if fact.request_id else None
        read = fact.model_dump(exclude_none=True)
        read["role"] = str(analysis.role if analysis is not None else read.get("role") or "")
        if not (
            read["role"] in {"option", "read_option", "option_source", "explicit_read_option"}
            or _choice_control_triggered(read)
        ):
            continue
        rows = [item for item in (as_list_payload(fact.response_json) or []) if isinstance(item, dict)]
        if len(rows) >= 2:
            catalogs.append((str(fact.request_id or ""), rows))
    if not catalogs:
        return
    exact_option_requests_by_target: dict[str, set[str]] = {}
    for capability in spec.capabilities or []:
        request_ids = {
            str(ref.request_id or "")
            for ref in capability.request_refs or []
            if ref.usage == "option_source" and str(ref.request_id or "")
        }
        if not request_ids:
            continue
        for step_id in capability.step_ids or []:
            exact_option_requests_by_target.setdefault(step_id, set()).update(request_ids)
    for step in spec.steps or []:
        if str(step.method or "").upper() not in {"POST", "PUT", "PATCH"}:
            continue
        exact_request_ids = exact_option_requests_by_target.get(step.step_id, set())
        step_catalogs = [
            item for item in catalogs
            if not exact_request_ids or item[0] in exact_request_ids
        ]
        groups: dict[str, list[ParamField]] = {}
        for param in step.params or []:
            groups.setdefault(_param_group_prefix(param.path), []).append(param)
        for members in groups.values():
            has_catalog_anchor, owned_request_ids = _group_option_source_request_ids(members)
            if modern_contract and not has_catalog_anchor:
                continue
            scored: list[tuple[int, str, dict[str, Any]]] = []
            for request_id, rows in step_catalogs:
                if modern_contract and owned_request_ids and request_id not in owned_request_ids:
                    continue
                hits = [
                    (row, _option_row_match_count(
                        row, members,
                        allow_unique_value_fallback=modern_contract,
                    ))
                    for row in rows
                ]
                if modern_contract:
                    best_count = max((count for _row, count in hits), default=0)
                    good = [(row, count) for row, count in hits if count == best_count]
                else:
                    good = [(row, count) for row, count in hits if count >= 2]
                    best_count = good[0][1] if len(good) == 1 else 0
                if best_count < 2 or len(good) != 1:
                    continue
                row, count = good[0]
                scored.append((count, request_id, row))
            if not scored:
                continue
            best = max(item[0] for item in scored)
            winners = [item for item in scored if item[0] == best]
            unique_rows = {
                json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
                for _count, _request_id, row in winners
            }
            if len(unique_rows) != 1:
                continue
            _count, request_id, row = winners[0]
            projected_paths: set[str] = set()
            for sibling in members:
                if sibling.locked or _param_has_manual_contract(sibling):
                    continue
                # The chooser is the input that selects the row; it cannot also
                # be a projection from that same row. This matters for schemas
                # whose selector is named `*Ref`, `code`, or another non-`*Id`
                # field.
                if modern_contract and sibling.source_kind in {"api_option", "form_option", "page_enum"}:
                    continue
                if sibling.source_kind in {
                    "computed", "current_user", "system_time", "system_generated",
                    "page_context", "request_header",
                }:
                    continue
                if (
                    _looks_page_context_field(sibling.key, sibling.path)
                    or _looks_audit_system_leaf(sibling.key, sibling.path)
                    or _field_leaf_token(sibling.key, sibling.path) in {"ownerid", "userid"}
                ):
                    continue
                sibling_leaf = _field_leaf_token(sibling.key, sibling.path)
                if sibling_leaf.endswith("id") or sibling_leaf in {"id", "ids"}:
                    if _looks_row_identity_leaf(sibling.key, sibling.path) or _param_is_document_record_identity(sibling):
                        continue
                    if not (_param_control_kinds(sibling) & {"select", "combobox", "radio"}):
                        continue
                    response_path = _best_option_projection_path(
                        row, sibling.path, sibling.value, min_score=40,
                        allow_unique_value_fallback=modern_contract,
                    )
                    if (
                        response_path
                        and sibling.source_kind in {"", "unknown", "page_default"}
                        and not _param_has_manual_contract(sibling)
                    ):
                        sibling.category = "user_param"
                        sibling.source_kind = "form_option"
                        sibling.source = {
                            "kind": "form_option",
                            "path": sibling.path,
                            "source_request_id": request_id,
                            "response_path": response_path,
                        }
                        sibling.exposed_to_user = True
                        sibling.editable = True
                        sibling.need_human_confirm = False
                        sibling.reason = "所选目录行的标识由调用方选择，运行期再带出同行字段"
                    continue
                if _param_is_quantity_or_formula_leaf(sibling.key, sibling.path):
                    continue
                if (
                    _param_has_editable_control_evidence(sibling)
                    and not _param_control_is_readonly(sibling)
                    and (
                        sibling.source_kind == "previous_response"
                        or _param_was_caller_typed(sibling)
                    )
                ):
                    continue
                response_path = _best_option_projection_path(
                    row, sibling.path, sibling.value,
                    allow_unique_value_fallback=modern_contract,
                )
                if not response_path:
                    continue
                selector = next(
                    (
                        item for item in members
                        if item is not sibling
                        and (
                            _field_leaf_token(item.key, item.path).endswith("id")
                            or _field_leaf_token(item.key, item.path) in {"id", "ids"}
                        )
                    ),
                    None,
                )
                sibling.category = "runtime_var"
                sibling.source_kind = "selected_option_field"
                sibling.source = {
                    "kind": "selected_option_field",
                    "selector_path": selector.path if selector is not None else "",
                    "selector_param": selector.key if selector is not None else "",
                    "source_request_id": request_id,
                    "response_path": response_path,
                    "target_path": sibling.path,
                }
                sibling.exposed_to_user = False
                sibling.editable = False
                sibling.required = False
                sibling.need_human_confirm = False
                sibling.reason = f"该字段来自所选记录的 `{response_path}`，运行期随选择自动写入"
                projected_paths.add(str(sibling.path or ""))
            if projected_paths:
                step.selects = [
                    binding for binding in (step.selects or [])
                    if str(binding.path or "") not in projected_paths
                    and str(binding.id_path or "") not in projected_paths
                ]


def _enum_sources_compatible(dst: ParamField, src: ParamField) -> bool:
    if dst.source_kind != src.source_kind:
        return False
    dst_source = dst.source or {}
    src_source = src.source or {}
    if dst.source_kind == "api_option":
        return bool(dst_source.get("source_url")) and (
            _request_path({"url": str(dst_source.get("source_url") or "")})
            == _request_path({"url": str(src_source.get("source_url") or "")})
        )
    if dst.source_kind == "page_enum":
        return bool(
            dst_source.get("enum_confirmed") is True
            and src_source.get("enum_confirmed") is True
            and (dst.key or dst.label or dst.path) == (src.key or src.label or src.path)
        )
    return dst.source_kind in {"manual_enum", "static_enum", "form_option"}


def _refresh_param_enum_description(param: ParamField) -> None:
    base_description = _strip_option_descriptions(param.description)
    base_reason = _strip_option_descriptions(param.reason)
    detail = _enum_options_description(param.source_kind, param.enum_options, param.enum_value_map)
    param.description = _upsert_option_description(base_description, detail) or None
    param.reason = _upsert_option_description(base_reason, detail)


def _merge_enum_values(dst: ParamField, src: ParamField) -> None:
    if not _enum_sources_compatible(dst, src):
        _refresh_param_enum_description(dst)
        return
    if not dst.enum_options and src.enum_options:
        dst.enum_options = list(src.enum_options)
    elif dst.enum_options and src.enum_options:
        seen = {json.dumps(x, ensure_ascii=False, sort_keys=True, default=str) for x in dst.enum_options}
        for opt in src.enum_options:
            marker = json.dumps(opt, ensure_ascii=False, sort_keys=True, default=str)
            if marker not in seen:
                dst.enum_options.append(opt)
                seen.add(marker)
    if not dst.enum_value_map and src.enum_value_map:
        dst.enum_value_map = dict(src.enum_value_map)
    elif dst.enum_value_map and src.enum_value_map:
        dst.enum_value_map = {**src.enum_value_map, **dst.enum_value_map}
    _refresh_param_enum_description(dst)

_PENDING_FLOW_SPEC_HELPERS = {'_AUTOMATED_FIELD_EDIT_ACTORS': 'dano.execution.page.flow_spec_core.controlled_edits', '_FLOW_PATH_MISSING': 'dano.execution.page.flow_spec_core.normalization', '_find_param': 'dano.execution.page.flow_spec_core.controlled_edits', '_find_step': 'dano.execution.page.flow_spec_core.controlled_edits', '_flow_path_lookup': 'dano.execution.page.flow_spec_core.normalization', '_incomplete_page_enum_is_executable': 'dano.execution.page.flow_release', '_infer_type_from_value': 'dano.execution.page.flow_spec_core.normalization', '_looks_catalog_attribute_leaf': 'dano.execution.page.flow_materialization.field_contracts.edit_form', '_looks_display_echo_field': 'dano.execution.page.flow_materialization.field_contracts.edit_form', '_looks_unit_price_formula_leaf': 'dano.execution.page.flow_materialization.field_contracts.computed', '_param_is_quantity_or_formula_leaf': 'dano.execution.page.flow_materialization.field_contracts.computed', '_record_param_manual_contract': 'dano.execution.page.flow_spec_core.controlled_edits', '_strip_body_prefix': 'dano.execution.page.flow_spec_core.normalization'}


def _bind_flow_spec_helpers() -> None:
    import sys
    module_globals = globals()
    for name, owner in _PENDING_FLOW_SPEC_HELPERS.items():
        mod = sys.modules.get(owner)
        if mod is None or not hasattr(mod, name):
            continue
        module_globals[name] = getattr(mod, name)


_bind_flow_spec_helpers()
