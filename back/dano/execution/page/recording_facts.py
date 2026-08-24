"""Stage 2: captured network/page facts adapted into RequestFacts."""
from __future__ import annotations

from typing import Any
import copy
import hashlib
import json
from urllib.parse import urlparse, parse_qs
import re
from dano.execution.page.flow_spec_core.models import (
    FlowSpec,
    RequestAnalysis,
    RequestFact,
    RequestFacts,
    RequestUsage,
)
from dano.execution.page.request_capture import (
    _is_idlike,
    _leaf_paths,
    _multipart_contains_file,
    _parse_body,
    _pick_label_key,
    as_list_payload,
    classify_request_role,
    looks_internal_param_name,
    looks_like_auth_write,
    looks_like_read_request,
)
from dano.execution.page.recording_field_selection import select_field_contract_evidence


_REQUEST_OBSERVER_KEYS = (
    "trigger_action_id", "trigger_transaction_id", "trigger_event_id",
    "trigger_op", "trigger_locator", "trigger_page_context",
    "page_context",
    "action_delta_ms", "causality_confidence",
    "resource_type", "navigation_request",
)


def _path_from_url(url: str, base_url: str = "") -> str:
    if base_url and url.startswith(base_url):
        return url[len(base_url):] or "/"
    if url.startswith("http"):
        u = urlparse(url)
        return (u.path or "/") + (("?" + u.query) if u.query else "")
    return url or "/"


def _field_leaf_token(key: str, path: str = "") -> str:
    leaf = re.sub(r"[^a-z0-9]+", "", str(key or "").lower())
    if leaf:
        return leaf
    return re.sub(r"[^a-z0-9]+", "", str(path or "").split(".")[-1].lower())


_RECORD_IDENTITY_LEAVES = frozenset({
    "id", "ids", "recordid", "requestid", "applicationid",
    "businessid", "entityid", "orderid", "billid", "billno",
    "docid", "documentid",
})


def _is_document_record_identity_path(key: str, path: str) -> bool:
    """True only for the document/record id itself, never line or chooser ids.

    ``query.ids`` / ``query.ids[0]`` name the document being mutated.
    Nested array paths such as ``items[0].itemId`` identify a selected catalog
    row, not the document being edited.
    """
    relative = str(path or key or "")
    for prefix in ("query.", "body.", "path.", "request."):
        if relative.startswith(prefix):
            relative = relative[len(prefix):]
            break
    if "." in re.sub(r"\[\d+\]", "", relative):
        return False
    return _field_leaf_token(key, path) in _RECORD_IDENTITY_LEAVES


def _looks_pagination_field(key: str, path: str) -> bool:
    raw = re.sub(r"[^a-z0-9]+", "", f"{key}.{path}".lower())
    return raw.endswith(("pageno", "pagenum", "pagesize", "pageindex", "currentpage", "limit", "offset"))


def _list_payload_has_reference_contract(payload: Any) -> bool:
    items = as_list_payload(payload) or []
    # A list response is one contract, not a ten-row sample.  Sparse backends
    # often emit labels/identifiers only on later rows, so inspect every
    # observed object before deciding this is not a reference source.
    for sample in (item for item in items if isinstance(item, dict)):
        for key in sample:
            if _is_idlike(str(key)) and _pick_label_key(sample, str(key)) != key:
                return True
    return False


def _choice_control_triggered(request: dict) -> bool:
    trigger_op = str(request.get("trigger_op") or "").lower()
    trigger_locator = str(request.get("trigger_locator") or "").lower()
    return (
        trigger_op in {"select", "pick"}
        or (
            trigger_op == "click"
            and any(token in trigger_locator for token in (
                "select", "combobox", "cascader", "picker", "dropdown",
            ))
        )
    )


def _read_is_entity_enrichment_lookup(read: dict) -> bool:
    """Detect batched entity enrichment reads such as online-status lookups.

    These responses often contain an ``id``/display pair and were therefore
    mistaken for selectable directories.  A batched lookup is different: a
    plural identity query names the exact entities to enrich and the response
    returns those same identities. Only an explicitly confirmed option-source
    role can override this structural contract; an ordinary click/select
    trigger is shared by both the directory request and its status hydration.
    """
    if str(read.get("role") or read.get("request_role") or "") == "explicit_read_option":
        return False

    items = [item for item in (as_list_payload(
        read.get("json", read.get("response_json")),
    ) or []) if isinstance(item, dict)]
    if not items:
        return False

    path_tokens = [
        token.casefold()
        for token in re.split(r"[^a-zA-Z0-9]+", _request_path(read))
        if token
    ]
    operation_tokens = {
        "get", "batch", "online", "status", "statuses", "detail", "details",
        "info", "profile", "hydrate", "hydration", "check", "validate",
        "page", "pages", "list", "lists", "option", "options", "query",
        "search", "simple", "all",
    }
    trailing_operations: list[str] = []
    while path_tokens and path_tokens[-1] in operation_tokens:
        trailing_operations.append(path_tokens.pop())
    endpoint_owner = path_tokens[-1] if path_tokens else ""
    trailing_set = set(trailing_operations)
    enrichment_operations = {
        "online", "status", "statuses", "detail", "details", "info",
        "profile", "hydrate", "hydration", "check", "validate",
    }
    candidate_operations = {
        "page", "pages", "list", "lists", "option", "options", "query",
        "search", "simple", "all",
    }
    if trailing_set & candidate_operations and not trailing_set & enrichment_operations:
        # A scoped directory such as ``/user/options?userIds=...`` is still a
        # candidate source.  The identity filter narrows the directory; it
        # does not turn the response into a status/detail hydration call.
        return False

    def owns_endpoint(stem: str) -> bool:
        if not stem:
            return True
        variants = {stem, f"{stem}s", f"{stem}es"}
        if stem.endswith("y"):
            variants.add(f"{stem[:-1]}ies")
        variants.update({
            "people" if stem == "person" else "",
            "persons" if stem == "people" else "",
        })
        return endpoint_owner in (variants - {""})

    response_keys = {
        re.sub(r"[^a-z0-9]+", "", str(key).casefold()): str(key)
        for item in items
        for key in item
    }
    identity_inputs = list(_request_query_values(read).items())

    def collect_body_inputs(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(item, dict):
                    identity_inputs.append((str(key), item if isinstance(item, list) else [item]))
                collect_body_inputs(item)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, (dict, list)):
                    collect_body_inputs(item)

    body = _parse_body(read.get("post_data"))
    if body is not None:
        collect_body_inputs(body)

    for query_key, raw_values in identity_inputs:
        compact_key = re.sub(r"[^a-z0-9]+", "", str(query_key).casefold())
        stem = ""
        identity_suffix = "id"
        if compact_key == "ids":
            pass
        elif compact_key == "keys":
            identity_suffix = "key"
        elif compact_key.endswith("ids") and len(compact_key) > 3:
            stem = compact_key[:-3]
        elif compact_key.endswith("keys") and len(compact_key) > 4:
            stem = compact_key[:-4]
            identity_suffix = "key"
        elif compact_key.endswith("idlist") and len(compact_key) > 6:
            stem = compact_key[:-6]
        elif compact_key.endswith("keylist") and len(compact_key) > 7:
            stem = compact_key[:-7]
            identity_suffix = "key"
        else:
            continue

        # ``roleIds`` on a user directory filters users by role; it does not
        # turn the returned users into role candidates.  Require the entity
        # family named by the plural identity key to own the endpoint.
        if not owns_endpoint(stem):
            continue

        requested: set[str] = set()
        for raw_value in raw_values:
            values = raw_value if isinstance(raw_value, list) else [raw_value]
            for value in values:
                requested.update(
                    token for token in re.split(r"[,;|\s]+", str(value or "").strip())
                    if token
                )
        if not requested:
            continue

        response_key = (
            response_keys.get(f"{stem}{identity_suffix}")
            or (
                response_keys.get(identity_suffix)
                if not stem or trailing_set & enrichment_operations
                else None
            )
        )
        if not response_key:
            continue
        returned = {
            str(item.get(response_key))
            for item in items
            if item.get(response_key) not in (None, "")
        }
        if returned and returned <= requested:
            return True
    return False


def _query_param_type(key: str, value: Any) -> str:
    text = str(value or "").strip()
    key_text = str(key or "").lower()
    if re.search(r"(?:date|time|day|日期|时间)", key_text):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return "date"
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[ t]\d{2}:\d{2}(?::\d{2})?)?", text, re.I):
            return "datetime"
    if text.lower() in {"true", "false"}:
        return "boolean"
    # A recorded sample is not a schema.  Free-text business fields commonly
    # contain a numeric-looking value (for example a short description of
    # "1"), so their semantic key must take precedence over value inference.
    if re.search(
        r"(?:name|title|desc|description|info|remark|memo|note|keyword|text|content|label|subject|reason|purpose)",
        key_text,
    ):
        return "string"
    if re.fullmatch(r"-?(?:\d+|\d+\.\d+)", text) and not re.search(
        r"(?:id|code|key|type|status|no|number)", key_text,
    ):
        return "number"
    return "string"


def _request_query_values(req: dict) -> dict[str, list[Any]]:
    raw = req.get("query")
    if isinstance(raw, dict) and raw:
        return {
            str(key): list(value) if isinstance(value, list) else [value]
            for key, value in raw.items()
        }
    try:
        return parse_qs(urlparse(str(req.get("url") or req.get("path") or "")).query, keep_blank_values=True)
    except Exception:  # noqa: BLE001
        return {}


def _params_from_get_query(
    req: dict,
    samples: dict | None = None,
    page_enum_options: dict | None = None,
    field_evidence: list[dict] | None = None,
    required_labels: set | None = None,
) -> list[dict]:
    """GET 请求：从 URL query string 提参，并保持 wire key 与显示名分离。

    DOM 样例只有显示名和值，不能逐字段按值贪心匹配。查询条件里经常同时
    出现 ``pageNo=1``、``billCode=1``、``processStatus=1``；旧实现会让它们
    争抢同一个“单据编号”标签。这里先用控件 ``name/data-prop`` 等结构别名
    认领枚举字段，再在剩余非分页字段中做一对一值匹配。真实 query key 始终
    保存在 ``key/path``，中文仅进入 ``suggest_name``。
    """
    qs = _request_query_values(req)
    if not qs:
        return []
    required_labels = required_labels or set()

    def norm_identifier(value: Any) -> str:
        return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(value or "")).casefold()

    required_norms = {
        norm_identifier(value)
        for value in required_labels
        if norm_identifier(value)
    }

    def has_required_evidence(key: str, label: str, control: dict[str, Any]) -> bool:
        """A search filter is mandatory only when the page said so.

        A value occurring in the captured URL proves that the filter was used;
        it does not prove that callers must always provide it.  Structural
        aliases are accepted because frameworks often put the required marker
        on a control whose visible label was normalized separately.
        """
        if _looks_pagination_field(key, f"query.{key}") or not required_norms:
            return False
        names = {key, label, f"query.{key}", *(control.get("field_aliases") or [])}
        return any(norm_identifier(name) in required_norms for name in names if norm_identifier(name))

    raw_keys = list(qs)
    labels: dict[str, str] = {key: key for key in raw_keys}
    grounded: set[str] = set()
    control_by_key: dict[str, dict] = {}

    # All controls (text/date/number/select) can expose the real query property.
    # Select one coherent contract per exact wire path before alias fallback;
    # repeated snapshots must not make the last coincidental value match win.
    bound_by_key: dict[str, list[dict[str, Any]]] = {}
    for item in field_evidence or []:
        if not isinstance(item, dict):
            continue
        bound_path = str(item.get("wire_path") or "").removeprefix("request.")
        if str(item.get("binding_status") or "") == "bound" and bound_path.startswith("query."):
            bound_key = bound_path.removeprefix("query.")
            if bound_key in raw_keys:
                bound_by_key.setdefault(bound_key, []).append(item)
    for key, items in bound_by_key.items():
        selected = select_field_contract_evidence(items, f"query.{key}")
        if selected is None:
            continue
        label = str(selected.get("label") or selected.get("field") or "").strip()
        if label:
            labels[key] = label
        grounded.add(key)
        control_by_key[key] = selected

    for item in field_evidence or []:
        if not isinstance(item, dict):
            continue
        bound_path = str(item.get("wire_path") or "").removeprefix("request.")
        if str(item.get("binding_status") or "") == "bound" and bound_path.startswith("query."):
            continue
        aliases = [
            str(value).strip() for value in (item.get("field_aliases") or [])
            if str(value or "").strip()
        ]
        alias_norms = {norm_identifier(alias) for alias in aliases if norm_identifier(alias)}
        matches = [
            key for key in raw_keys
            if norm_identifier(key) in alias_norms
            or norm_identifier(f"query.{key}") in alias_norms
        ]
        if len(matches) != 1:
            continue
        key = matches[0]
        label = str(item.get("label") or item.get("field") or "").strip()
        if label:
            labels[key] = label
        grounded.add(key)
        control_by_key.setdefault(key, item)

    # Strongest evidence: the opened DOM control reports its actual name/id.
    # Page-wide enums belong to GET filters. A write that reuses ``?status=``
    # is carrying a command discriminator, not that list dropdown.
    for raw_enum_key, raw_options in (page_enum_options or {}).items() if str(req.get("method") or "GET").upper() == "GET" else ():
        if not isinstance(raw_options, dict):
            continue
        field_key = str(raw_options.get("field_key") or raw_enum_key or "").strip()
        aliases = [
            str(value).strip()
            for value in (raw_options.get("field_aliases") or [])
            if str(value or "").strip()
        ]
        alias_norms = {norm_identifier(alias) for alias in aliases if norm_identifier(alias)}
        matches = [
            key for key in raw_keys
            if norm_identifier(key) in alias_norms
            or norm_identifier(f"query.{key}") in alias_norms
        ]
        if len(matches) != 1:
            continue
        key = matches[0]
        if field_key and not looks_internal_param_name(field_key):
            labels[key] = field_key
        grounded.add(key)
        control_by_key.setdefault(key, {
            "control_kind": "select",
            "field_aliases": aliases,
        })

    # Value evidence is safe only after structural matches and pagination have
    # been removed, and only when exactly one wire field remains for a sample.
    has_bound_identity_protocol = any(
        isinstance(item, dict) and bool(item.get("binding_status"))
        for item in (field_evidence or [])
    )
    sample_items = [] if has_bound_identity_protocol else [
        (str(label).strip(), str(value).strip())
        for label, value in (samples or {}).items()
        if str(label or "").strip() and value not in (None, "")
    ]
    claimed_labels = {labels[key] for key in grounded if labels.get(key)}
    for sample_label, sample_value in sample_items:
        if sample_label in claimed_labels:
            continue
        candidates = []
        for key in raw_keys:
            if key in grounded or _looks_pagination_field(key, f"query.{key}"):
                continue
            value = (qs.get(key) or [""])[0]
            if str(value).strip() == sample_value:
                candidates.append(key)
        if len(candidates) != 1:
            continue
        key = candidates[0]
        labels[key] = sample_label
        grounded.add(key)
        claimed_labels.add(sample_label)

    out: list[dict] = []
    for k, vals in qs.items():
        v = (vals or [""])[0]
        label = labels.get(k) or str(k)
        control = control_by_key.get(k) or {}
        recorded_user_input = str(control.get("op") or "").lower() in {"fill", "select", "pick"}
        control_kind = str(control.get("control_kind") or "").lower()
        inferred_type = _query_param_type(k, v)
        if control_kind in {"text", "textarea"}:
            inferred_type = "string"
        elif control_kind == "number":
            inferred_type = "number"
        elif control_kind == "date":
            inferred_type = "date"
        elif control_kind in {"datetime", "time"}:
            inferred_type = "datetime"
        elif control_kind in _SCREENSHOT_OPTION_CONTROL_KINDS:
            # A closed select still proves choice semantics even when its popup
            # options were not expanded during recording.
            inferred_type = "enum"
        elif recorded_user_input and inferred_type == "number":
            # Legacy recordings know the operator typed the value even when the
            # browser did not yet expose input[type]. Numeric-looking free text
            # such as billCode/useInfo remains text unless a number control says
            # otherwise.
            inferred_type = "string"
        wire_type = _query_param_type(k, v)
        if control_kind in {"text", "textarea"}:
            # URL query values are text emitted by a real text control.  A
            # numeric-looking sample such as hotelName=1 is not a numeric wire
            # contract merely because this recording used digits.
            wire_type = "string"
        required_evidence = (
            bool(control.get("required_observed"))
            if isinstance(control.get("required_observed"), bool)
            else has_required_evidence(k, label, control)
        )
        out.append({
            "path": f"query.{k}",
            "key": k,
            "suggest_name": label,
            "value": v,
            # A real text control remains text even when this particular sample
            # contains only digits (for example useInfo="1231").
            "type": inferred_type,
            "wire_type": wire_type,
            "required": required_evidence,
            "required_state": (
                "required" if bool(control.get("required_observed")) else "optional"
                if isinstance(control.get("required_observed"), bool)
                else "unknown"
            ),
            "required_state_grounded": has_bound_identity_protocol,
            "confidence": 0.9 if label != k else 0.75,
            "confidence_tier": "grounded" if label != k else "auto",
            "name_source": "dom" if k in control_by_key and label != k else "sample" if label != k else "auto",
            "recorded_user_input": recorded_user_input,
            "field_aliases": list(control.get("field_aliases") or []),
            "control_kind": control_kind or "unknown",
            "constraints": {
                name: control.get(name)
                for name in ("minimum", "maximum")
                if isinstance(control.get(name), (int, float))
                and not isinstance(control.get(name), bool)
            },
        })
    return out


def _recording_evidence_matches_request(req: dict, item: dict) -> bool:
    """Keep DOM facts on the page/frame that produced the network request."""
    binding_status = str(item.get("binding_status") or "")
    if binding_status:
        if binding_status not in {"bound", "bound_unsupported"}:
            return False
        request_id = _request_fact_key(_request_fact_entry(req, {}))
        return bool(request_id and request_id == str(item.get("request_id") or ""))
    req_page = str(req.get("page_id") or "")
    req_frame = str(req.get("frame_id") or "")
    item_page = str(item.get("page_id") or "")
    item_frame = str(item.get("frame_id") or "")
    if req_page and item_page and req_page != item_page:
        return False
    if req_frame and item_frame and req_frame != item_frame:
        return False
    def route_identity(value: dict) -> str:
        for context in (value.get("trigger_page_context"), value.get("page_context")):
            if not isinstance(context, dict):
                continue
            path = str(context.get("path") or "").strip()
            if path:
                return path.rstrip("/") or "/"
            url = str(context.get("url") or "").strip()
            if url:
                return urlparse(url).path.rstrip("/") or "/"
        return ""
    req_route = route_identity(req)
    item_route = route_identity(item)
    if req_route and item_route and req_route != item_route:
        return False
    return True


def _field_evidence_for_request(req: dict, evidence: list[dict] | None) -> list[dict]:
    bound = [
        item for item in (evidence or [])
        if isinstance(item, dict) and _recording_evidence_matches_request(req, item)
    ]
    if bound:
        return bound
    # Preserve the fact that evidence existed in this scope but could not be
    # tied to one request field.  Downstream naming may then fail closed instead
    # of reviving value-based field identity.
    unresolved_in_scope = any(
        isinstance(item, dict)
        and str(item.get("binding_status") or "") in {"ambiguous", "unbound"}
        and _recording_evidence_matches_scope(req, item)
        for item in (evidence or [])
    )
    return [{"binding_status": "unresolved"}] if unresolved_in_scope else []


def _recording_evidence_matches_scope(req: dict, item: dict) -> bool:
    """Scope-only part of evidence matching, including unresolved evidence."""
    req_page = str(req.get("page_id") or "")
    req_frame = str(req.get("frame_id") or "")
    item_page = str(item.get("page_id") or "")
    item_frame = str(item.get("frame_id") or "")
    if req_page and item_page and req_page != item_page:
        return False
    if req_frame and item_frame and req_frame != item_frame:
        return False

    def route_identity(value: dict) -> str:
        for context in (value.get("trigger_page_context"), value.get("page_context")):
            if not isinstance(context, dict):
                continue
            path = str(context.get("path") or "").strip()
            if path:
                return path.rstrip("/") or "/"
            url = str(context.get("url") or "").strip()
            if url:
                return urlparse(url).path.rstrip("/") or "/"
        return ""

    req_route = route_identity(req)
    item_route = route_identity(item)
    return not (req_route and item_route and req_route != item_route)


_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


_NOISE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".css", ".js", ".map", ".woff", ".woff2", ".ico")


_NOISE_SEGS = {
    "heartbeat", "metrics", "metric", "track", "trace", "analytics",
    "log", "logs", "beacon", "ping", "sse", "socket", "websocket", "ws",
}


_OPTION_SEGS = {"list", "options", "option", "dict", "select", "candidates", "tree", "users", "roles"}


_WRITE_HINT_SEGS = {
    "submit", "save", "create", "update", "send", "apply", "start", "commit",
    "confirm", "approve", "complete", "finish", "chat",
}


_BORING_LINK_VALUES = {"", "0", "1", "true", "false", "200", "ok", "success", "none", "null"}


def _request_path(req: dict) -> str:
    url = req.get("url") or req.get("path") or ""
    try:
        return urlparse(url).path if str(url).startswith("http") else str(url).split("?", 1)[0]
    except Exception:
        return str(url or "")


def _request_segments(req: dict) -> set[str]:
    return {s.lower() for s in re.split(r"[^a-zA-Z0-9]+", _request_path(req)) if s}


def _request_has_write_hint(req: dict) -> bool:
    if _request_segments(req) & _WRITE_HINT_SEGS:
        return True
    leaf = _request_path(req).rstrip("/").rsplit("/", 1)[-1]
    normalized = re.sub(r"[^a-z0-9]+", "", leaf.casefold())
    return bool(re.match(
        r"^(?:submit|save|create|update|send|apply|start|commit|confirm|approve|complete|finish)",
        normalized,
    ))


def _request_values(req: dict) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    body = _parse_body(req.get("post_data"))
    if body is not None:
        for path, _tokens, sv, _raw in _leaf_paths(body):
            out.append((path, str(sv)))
    query = dict(req.get("query") or {})
    if not query:
        try:
            query = {k: vals[0] if vals else "" for k, vals in parse_qs(urlparse(req.get("url") or "").query).items()}
        except Exception:
            query = {}
    for k, v in query.items():
        if isinstance(v, list):
            for i, item in enumerate(v):
                out.append((f"query.{k}[{i}]", str(item)))
        else:
            out.append((f"query.{k}", str(v)))
    return out


def _response_values(req: dict) -> list[tuple[str, str]]:
    data = req.get("response_json")
    if data is None:
        return []
    try:
        return [(path, str(sv)) for path, _tokens, sv, _raw in _leaf_paths(data)]
    except Exception:
        return []


def _trace_pos(req: dict, trace: list[dict]) -> int:
    for i, item in enumerate(trace):
        if item is req:
            return i
    idx = req.get("index")
    if idx is not None:
        for i, item in enumerate(trace):
            if item.get("index") == idx:
                return i
    return -1


def _useful_link_value(value: str) -> bool:
    v = str(value or "").strip().lower()
    return bool(v and v not in _BORING_LINK_VALUES and len(v) >= 3)


def _dependency_consumer_candidate(request: dict) -> bool:
    """Return whether a later request can ground a business dependency.

    A value copied between two background SDK calls is not a workflow edge.
    Keep command-anchored requests, explicit command paths and parameterized
    reads; these are protocol evidence rather than host/path allowlists.
    """
    method = str(request.get("method") or "GET").upper()
    if _is_noise_request(request) or looks_like_auth_write(
        str(request.get("url") or ""), request.get("post_data"),
    ):
        return False
    if _request_has_command_anchor(request) or _request_has_write_hint(request):
        return True
    return bool(method in {"GET", "HEAD"} and _business_filter_count(request) > 0)


def _response_referenced_later(req: dict, trace: list[dict]) -> dict | None:
    pos = _trace_pos(req, trace)
    if pos < 0:
        return None
    response_values = [(p, v) for p, v in _response_values(req) if _useful_link_value(v)]
    if not response_values:
        return None
    # H23 修复:把后续 trace 的值提前合并到一个 map(保留每个 value 第一次出现的 target + value),
    # 避免每 later 重算;O(N+M) 比原 O(N²) 快一个量级。**source_path 必须保留 response 那一端的字段路径**(消费方依赖),
    # 所以 path 在命中时再从 response_values 里取。
    pool_values: dict[str, dict] = {}      # value → {target_url, target_method}
    for later in trace[pos + 1:]:
        if not _dependency_consumer_candidate(later):
            continue
        for _p, v in _request_values(later):
            if _useful_link_value(v) and v not in pool_values:
                pool_values[v] = {
                    "target_url": later.get("url") or "",
                    "target_method": (later.get("method") or "").upper(),
                }
    for path, value in response_values:
        hit = pool_values.get(value)
        if hit is not None:
            return {"source_path": path, "value": value,
                    "target_url": hit["target_url"], "target_method": hit["target_method"]}
    return None


def _sample_hit_count(req: dict, samples: dict | None) -> int:
    values = {v for _p, v in _request_values(req)}
    return sum(1 for v in (samples or {}).values() if v not in (None, "") and str(v) in values)


def _is_noise_request(req: dict) -> bool:
    path = _request_path(req).lower()
    if path.endswith(_NOISE_EXTS):
        return True
    return bool(_request_segments(req) & _NOISE_SEGS)


def _looks_telemetry_request(req: dict) -> bool:
    """Detect generic SDK event envelopes without naming vendors or hosts."""
    body = _parse_body(req.get("post_data"))
    envelopes = body if isinstance(body, list) else [body]
    for envelope in envelopes:
        if not isinstance(envelope, dict):
            continue
        events = envelope.get("events")
        if not isinstance(events, list) or not events:
            continue
        event_rows = [item for item in events if isinstance(item, dict)]
        if not event_rows:
            continue
        event_shape = sum(
            1 for item in event_rows
            if item.get("event") not in (None, "")
            and any(key in item for key in ("local_time_ms", "timestamp", "event_time"))
        )
        envelope_shape = any(key in envelope for key in ("header", "user", "sdk", "context"))
        if event_shape == len(event_rows) and envelope_shape:
            return True
    return False


def _looks_graphql_request(req: dict) -> bool:
    url = str(req.get("url") or req.get("path") or "").lower()
    if "graphql" in url:
        return True
    payload = req.get("post_data")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:  # noqa: BLE001
            payload = {}
    if not isinstance(payload, dict):
        return False
    query = str(payload.get("query") or "").lstrip()
    return query.startswith(("query", "mutation", "subscription")) or query.startswith("{")


def _role_row(req: dict, *, role: str, keep: bool, reason: str, confidence: float,
              semantic: dict | None = None, evidence: dict | None = None) -> dict:
    url = req.get("url") or ""
    row = {
        "index": req.get("index"),
        "method": (req.get("method") or "").upper(),
        "url": url,
        "path": _path_from_url(url),
        "role": role,
        "keep": keep,
        "reason": reason,
        "keep_reason": reason if keep else "",
        "filter_reason": "" if keep else reason,
        "confidence": confidence,
        "actor": "heuristic",
    }
    if semantic:
        row.update({
            "semantic_role": semantic.get("semanticRole", ""),
            "side_effect": semantic.get("sideEffect", ""),
            "risk_level": semantic.get("risk_level", ""),
        })
    if evidence:
        row["evidence"] = evidence
    return row


def _list_payload_is_business_records(req: dict, items: list[dict] | list[Any]) -> bool:
    sample = next((item for item in items[:5] if isinstance(item, dict)), None)
    if not sample or _choice_control_triggered(req):
        return False
    if _request_has_business_query_evidence(req):
        return True
    if _list_payload_has_reference_contract(req.get("response_json")):
        return False
    # Field names describe a row's shape, not why the request happened. Generic
    # payloads such as messages/logs commonly contain status/content/progress and
    # must not become public business capabilities without operation evidence.
    return False


def _list_payload_has_conventional_option_contract(payload: Any) -> bool:
    """Recognize common ID/name rows without treating arbitrary two-column records as options."""
    items = as_list_payload(payload) or []
    sample = next((item for item in items[:5] if isinstance(item, dict)), None)
    if not sample:
        return False
    normalized = {
        re.sub(r"[^a-z0-9]+", "", str(key).casefold())
        for key in sample
    }
    return bool(
        normalized & {"id", "value", "code", "key", "uuid"}
        and normalized & {"name", "label", "text", "title", "displayname"}
    )


def _request_has_option_endpoint_hint(req: dict) -> bool:
    leaf = _request_path(req).rstrip("/").rsplit("/", 1)[-1].casefold()
    normalized = re.sub(r"[^a-z0-9]+", "", leaf)
    return bool(re.search(
        r"(?:simplelist|options?|candidates?|dictionary|dict|tree|list|select)$",
        normalized,
    ))


def _request_has_reference_entity_hint(req: dict) -> bool:
    segments = _request_segments(req)
    return bool(
        segments & {"system", "identity", "directory", "iam", "masterdata", "lookup", "reference"}
        and segments & {
            "user", "users", "person", "people", "employee", "employees",
            "member", "members", "dept", "department", "team", "role", "tenant",
            "post", "position",
        }
    )


def _response_has_scalar_business_value(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    for key in ("data", "result", "value"):
        value = payload.get(key)
        if value not in (None, "") and not isinstance(value, (dict, list)):
            return True
    return False


_QUERY_ACTION_RE = re.compile(
    r"(?:查询|搜索|筛选|检索|查看|详情|进度|预览|刷新|列表|导出|"
    r"\bquery\b|\bsearch\b|\bfilter\b|\bview\b|\bdetail\b|\bprogress\b|"
    r"\bpreview\b|\brefresh\b|\blist\b|\bexport\b)",
    re.I,
)


_INTERNAL_WORKFLOW_READ_RE = re.compile(
    r"(?:process-definition|approval-detail|form-config|permissions?|current-user|auth)",
    re.I,
)


_BUSINESS_QUERY_PATH_RE = re.compile(
    r"(?:^|/)(?:page|list|search|query|history|records?|status|statistics|detail)(?:/|$|\?)",
    re.I,
)


_TYPEAHEAD_PATH_RE = re.compile(
    r"(?:sug(?:gest(?:ion)?s?|data)|autocomplete|typeahead|search[_-]?hint|complete[_-]?word)",
    re.I,
)


def _has_query_action_evidence(trigger_op: Any, trigger_locator: Any) -> bool:
    return bool(
        str(trigger_op or "").strip().lower() in {"click", "submit"}
        and _QUERY_ACTION_RE.search(str(trigger_locator or ""))
    )


def _request_has_business_query_evidence(req: dict) -> bool:
    if _INTERNAL_WORKFLOW_READ_RE.search(_request_path(req)):
        # Workflow definitions, approval metadata and form configuration are
        # orchestration reads.  They can feed a business capability, but their
        # fixed routing parameters are not end-user search filters.
        return False
    business_filters = _business_filter_count(req)
    trigger_op = str(req.get("trigger_op") or "").lower()
    trigger_locator = str(req.get("trigger_locator") or "").lower()
    submitted_query = trigger_op == "submit" or (
        trigger_op == "click" and "submit" in trigger_locator
    ) or _has_query_action_evidence(
        trigger_op, trigger_locator,
    )
    action_grounded = bool(
        _request_transaction_id(req)
        and (business_filters > 0 or submitted_query)
        and trigger_op in {"click", "submit"}
    )
    route_grounded = bool(
        business_filters > 0
        and trigger_op not in {"fill", "select", "pick"}
        and _BUSINESS_QUERY_PATH_RE.search(_request_path(req))
        and not _TYPEAHEAD_PATH_RE.search(_request_path(req))
        and not (
            _list_payload_has_reference_contract(req.get("response_json"))
            and _request_has_option_endpoint_hint(req)
        )
    )
    return bool(
        not _choice_control_triggered(req)
        and not _looks_telemetry_request(req)
        and (action_grounded or route_grounded)
    )


def classify_network_request(req: dict, trace: list[dict] | None = None,
                             samples: dict | None = None) -> dict:
    """给网络请求打角色、保留决策和原因。

    这里不修改原始请求，只产出解释性事实。后续 FlowSpec 用 keep=true 的请求建主流程，
    所有请求的判定都会进入 meta.request_roles 供人工核对。
    """
    trace = trace or [req]
    method = (req.get("method") or "GET").upper()
    semantic = classify_request_role(req)
    url = req.get("url") or ""

    if _is_noise_request(req):
        return _role_row(req, role="noise", keep=False,
                         reason="静态资源、心跳或埋点请求，不进入业务流程",
                         confidence=0.98, semantic=semantic)

    if _looks_telemetry_request(req):
        return _role_row(req, role="telemetry", keep=False,
                         reason="SDK 事件上报包只记录页面行为，不进入业务流程",
                         confidence=0.98, semantic=semantic)

    if _looks_graphql_request(req):
        return _role_row(req, role="unsupported_graphql", keep=False,
                         reason="GraphQL 请求可能包含多操作与动态 selection set；当前 FlowSpec 暂不自动复用",
                         confidence=0.92, semantic=semantic)

    if looks_like_auth_write(url, req.get("post_data")):
        return _role_row(req, role="auth", keep=False,
                         reason="登录/鉴权/令牌刷新请求，只作为身份来源，不进入业务流程",
                         confidence=0.96, semantic=semantic)

    response_ref = _response_referenced_later(req, trace)
    list_items = as_list_payload(req.get("response_json"))
    if list_items is not None and _read_is_entity_enrichment_lookup(req):
        return _role_row(
            req,
            role="read_context",
            keep=False,
            reason="实体状态/详情补充查询只丰富已选对象，不作为候选目录或独立能力",
            confidence=0.94,
            semantic=semantic,
        )

    if method not in _WRITE_METHODS:
        if list_items is not None and _choice_control_triggered(req):
            count = len(list_items or [])
            return _role_row(req, role="read_option", keep=False,
                             reason=f"读接口返回候选列表/枚举源({count}项)，作为字段来源，不进入主流程",
                             confidence=0.9, semantic=semantic)
        if response_ref and list_items is None:
            return _role_row(req, role="business_get", keep=True,
                             reason="GET 响应值被后续业务请求引用，作为前置步骤保留",
                             confidence=0.96, semantic=semantic, evidence=response_ref)
        # A submitted search remains a callable query even when a row value is
        # reused later. Value reuse alone cannot turn a result table into a
        # dropdown source.
        if _request_has_business_query_evidence(req):
            return _role_row(req, role="business_get", keep=True,
                             reason="用户查询动作携带非分页业务条件，作为独立查询能力候选",
                             confidence=0.94, semantic=semantic)
        if list_items is not None and _list_payload_is_business_records(req, list_items):
            return _role_row(req, role="business_get", keep=True,
                             reason="列表响应包含业务记录并具有查询动作证据，作为独立查询能力候选",
                             confidence=0.93, semantic=semantic)
        if (
            list_items is not None
            and response_ref
            and _list_payload_has_reference_contract(req.get("response_json"))
            and not _read_is_entity_enrichment_lookup(req)
        ):
            count = len(list_items or [])
            return _role_row(req, role="read_option", keep=False,
                             reason=f"读接口返回候选列表/枚举源({count}项)，作为字段来源，不进入主流程",
                             confidence=0.9, semantic=semantic)
        if response_ref:
            return _role_row(req, role="business_get", keep=True,
                             reason="GET 响应值被后续业务请求引用，作为前置步骤保留",
                             confidence=0.96, semantic=semantic, evidence=response_ref)
        if _business_filter_count(req) > 0 and _response_has_scalar_business_value(req.get("response_json")):
            return _role_row(req, role="business_get", keep=True,
                             reason="参数化读请求返回业务标量值，作为独立查询能力候选",
                             confidence=0.9, semantic=semantic)
        if list_items is not None and (
            (
                _list_payload_has_conventional_option_contract(req.get("response_json"))
                and _request_has_option_endpoint_hint(req)
            )
            or (
                _list_payload_has_reference_contract(req.get("response_json"))
                and _request_has_reference_entity_hint(req)
            )
        ) and not _read_is_entity_enrichment_lookup(req):
            return _role_row(req, role="read_option", keep=False,
                             reason="列表响应具备明确候选项契约，作为字段来源但不进入主流程",
                             confidence=0.88, semantic=semantic)
        return _role_row(req, role="read_context", keep=False,
                         reason="普通读接口，未发现后续业务请求依赖，默认不进入主流程",
                         confidence=0.68, semantic=semantic)

    if semantic.get("semanticRole") == "destructive":
        return _role_row(req, role="business_write", keep=True,
                         reason="危险写请求，保留事实并交给发布层/人工审核拦截",
                         confidence=0.98, semantic=semantic)

    if looks_like_read_request(url, req.get("post_data")):
        if _request_has_business_query_evidence(req):
            return _role_row(req, role="business_get", keep=True,
                             reason="POST 查询由用户查询动作触发并携带业务筛选条件，作为独立查询能力候选",
                             confidence=0.94, semantic=semantic)
        if response_ref:
            return _role_row(req, role="read_context", keep=True,
                             reason="POST 查询响应被后续业务请求引用，作为前置上下文步骤保留",
                             confidence=0.88, semantic=semantic, evidence=response_ref)
        if list_items is not None and _list_payload_is_business_records(req, list_items):
            return _role_row(req, role="business_get", keep=True,
                             reason="POST 查询返回业务记录列表，作为独立查询能力候选",
                             confidence=0.93, semantic=semantic)
        if list_items is not None and _choice_control_triggered(req):
            count = len(list_items or [])
            return _role_row(req, role="read_option", keep=False,
                             reason=f"POST 查询返回候选列表/枚举源({count}项)，作为字段来源，不进入主流程",
                             confidence=0.9, semantic=semantic)
        if list_items is not None and not _read_is_entity_enrichment_lookup(req) and (
            _list_payload_has_conventional_option_contract(req.get("response_json"))
            or _request_has_option_endpoint_hint(req)
        ):
            return _role_row(req, role="read_option", keep=False,
                             reason="POST 查询返回明确候选项列表，作为字段来源但不进入主流程",
                             confidence=0.86, semantic=semantic)
        return _role_row(req, role="read_context", keep=False,
                         reason="POST 查询/搜索类接口，未发现被后续步骤依赖，默认不进入主流程",
                         confidence=0.72, semantic=semantic)

    sample_hits = _sample_hit_count(req, samples)
    body = _parse_body(req.get("post_data"))
    has_file_input = _multipart_contains_file(req.get("post_data"))
    if (
        sample_hits > 0
        or has_file_input
        or _request_has_write_hint(req)
        or _request_has_command_anchor(req)
    ):
        role = "submit_anchor" if sample_hits > 0 else "business_write"
        reason = (
            "请求体包含用户录制输入值，判定为提交锚点"
            if sample_hits > 0
            else (
                "multipart 请求包含调用方文件输入，保留为业务步骤"
                if has_file_input
                else "写请求具有明确提交动作或业务命令语义，保留为业务步骤"
            )
        )
        evidence = (
            {"sample_hits": sample_hits}
            if sample_hits > 0
            else ({"file_input": True} if has_file_input else None)
        )
        return _role_row(req, role=role, keep=True, reason=reason,
                         confidence=0.93 if sample_hits > 0 else 0.86,
                         semantic=semantic, evidence=evidence)

    if body is not None or semantic.get("sideEffect") == "write":
        return _role_row(req, role="read_context", keep=False,
                         reason="写请求缺少用户操作、录制输入或业务命令证据，保留为背景事实",
                         confidence=0.7, semantic=semantic)

    return _role_row(req, role="read_context", keep=False,
                     reason="缺少可解析请求体且未发现业务依赖，默认过滤",
                     confidence=0.55, semantic=semantic)


def _request_role_key(req: dict) -> Any:
    request_id = str(req.get("request_id") or "").strip()
    if request_id:
        return ("request_id", request_id)
    if req.get("index") is not None:
        return ("index", req.get("index"))
    # Raw browser fixtures and legacy callers may not carry a durable request
    # id/index. Keep those request objects distinct here; semantic de-duplication
    # happens later and must not make every repeated URL select every duplicate.
    return ("object", id(req))


def _request_order_value(request: dict) -> float | None:
    for key in ("sequence", "request_index", "index", "started_at_ms", "timestamp"):
        value = request.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _request_precedes(left: dict, right: dict) -> bool:
    left_order = _request_order_value(left)
    right_order = _request_order_value(right)
    return left_order is not None and right_order is not None and left_order < right_order


_WORKFLOW_CONTEXT_TOKENS = (
    "processdefinition", "processdef", "workflowkey", "flowkey",
    "billtype", "formtype", "businesstype", "templatekey", "appkey",
)


def _workflow_context_values_for_request(request: dict) -> set[str]:
    """Extract stable routing values that distinguish one workflow from another."""
    values: set[str] = set()
    for field_path, raw_value in _request_values(request):
        value = str(raw_value or "").strip()
        norm_path = re.sub(r"[^a-z0-9]+", "", str(field_path or "").lower())
        if len(value) < 6 or value.lower() in _BORING_LINK_VALUES:
            continue
        if re.fullmatch(r"d{4}-d{2}-d{2}(?:[ t].*)?", value):
            continue
        if any(token in norm_path for token in _WORKFLOW_CONTEXT_TOKENS) or (
            (request.get("method") or "").upper() == "GET" and norm_path.endswith("key")
        ):
            values.add(value)
    return values


def _record_identity_values_for_request(request: dict) -> tuple[str, ...]:
    """Distinguish two captures of the same endpoint that name different records.

    Workflow context requires long routing tokens.  Short document ids such as
    ``query.id=36`` vs ``query.id=37`` are still different facts and must not
    collapse into one preflight/detail step.
    """
    values: list[str] = []
    for field_path, raw_value in _request_values(request):
        value = str(raw_value or "").strip()
        if not value:
            continue
        key = str(field_path).rsplit(".", 1)[-1].split("[", 1)[0]
        if not _is_document_record_identity_path(key, field_path):
            continue
        values.append(f"{_field_leaf_token(key, field_path)}={value}")
    return tuple(sorted(values))


def _request_transaction_id(request: dict) -> str:
    explicit = str(request.get("trigger_transaction_id") or "").strip()
    if explicit:
        return explicit
    action_id = str(request.get("trigger_action_id") or "").strip()
    if not action_id:
        return ""
    return "|".join(part for part in (
        str(request.get("page_id") or "page_unknown"),
        str(request.get("frame_id") or "frame_unknown"),
        action_id,
    ))


def _request_has_command_anchor(request: dict) -> bool:
    return bool(
        _request_transaction_id(request)
        and str(request.get("trigger_op") or "").lower() in {"click", "submit"}
        and str(request.get("causality_confidence") or "high").lower() in {"high", "medium"}
        and not bool(request.get("navigation_request"))
    )


def _preread_dedupe_key(
    req: dict,
) -> tuple[str, str, str, str, str, tuple[str, ...], str, tuple[str, ...]]:
    # Same endpoint may serve several workflows. Distinct routing values are
    # different facts and must never collapse into the latest request.
    context = tuple(sorted(_workflow_context_values_for_request(req)))
    # A visible command is a public-operation boundary. The same endpoint is
    # routinely reused by edit/detail/progress actions; collapsing those facts
    # makes later capability planning irrecoverably lose two of the actions.
    command = _request_transaction_id(req) if _request_has_command_anchor(req) else ""
    # Record identity is independent of command/workflow tokens.  Two detail
    # GETs that only differ by ``id`` are two facts, even when both are short
    # integers and share the same click-less hydration path.
    identity = _record_identity_values_for_request(req)
    parsed = urlparse(str(req.get("url") or ""))
    origin = f"{parsed.scheme.casefold()}://{parsed.netloc.casefold()}" if parsed.netloc else ""
    return (
        (req.get("method") or "GET").upper(),
        origin,
        _request_path(req),
        str(req.get("page_id") or req.get("pageId") or ""),
        str(req.get("frame_id") or req.get("frameId") or ""),
        context,
        command,
        identity,
    )


_TRANSPORT_FILTER_KEYS = frozenset({
    "signature", "sign", "nonce", "timestamp", "ts", "callback", "jsonp",
    "token", "session", "sessionid", "trace", "traceid", "spanid", "requestid",
    "sdk", "sdkversion", "version", "cache", "cachebuster", "webid", "deviceid",
    "mstoken", "abogus",
})


def _caller_filter_key(key: str, path: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "", str(key or "").casefold())
    return bool(
        normalized
        and normalized not in _TRANSPORT_FILTER_KEYS
        and not _looks_pagination_field(str(key), path)
    )


def _business_filter_count(req: dict) -> int:
    """Count caller-meaningful filters without treating pagination as business input."""
    query = req.get("query")
    if not isinstance(query, dict):
        try:
            query = parse_qs(urlparse(str(req.get("url") or "")).query, keep_blank_values=True)
        except Exception:  # noqa: BLE001
            query = {}
    count = sum(
        1 for key, value in (query or {}).items()
        if _caller_filter_key(str(key), f"query.{key}")
        and any(str(item).strip() for item in (value if isinstance(value, list) else [value]))
    )
    body = _parse_body(req.get("post_data"))
    if isinstance(body, (dict, list)):
        for path, _tokens, value, _raw in _leaf_paths(body):
            key = str(path).rsplit(".", 1)[-1].split("[", 1)[0]
            if _caller_filter_key(key, f"body.{path}") and str(value).strip():
                count += 1
    return count


def _preread_candidate_score(req: dict) -> tuple[int, int, int, float]:
    """Prefer the searched request over an initial/refresh request on the same endpoint."""
    business_filters = _business_filter_count(req)
    query_size = len(req.get("query") or _params_from_get_query(req))
    sequence = _request_sequence_value(req.get("sequence", req.get("index"))) or 0.0
    return (
        business_filters,
        query_size,
        1 if req.get("response_json", req.get("json")) is not None else 0,
        sequence,
    )


def _dedupe_preread_candidates(preread_cands: list[dict]) -> list[dict]:
    """同一路径反复触发时保留业务条件最完整的一次，序号仅作为同分兜底。"""
    best_by_path: dict[
        tuple[str, str, str, str, str, tuple[str, ...], str, tuple[str, ...]],
        dict,
    ] = {}
    for req in preread_cands:
        key = _preread_dedupe_key(req)
        current = best_by_path.get(key)
        if current is None or _preread_candidate_score(req) >= _preread_candidate_score(current):
            best_by_path[key] = req
    return [
        req for req in preread_cands
        if best_by_path.get(_preread_dedupe_key(req)) is req
    ]


def _dedupe_request_identities(requests: list[dict]) -> list[dict]:
    """Keep one canonical capture when the recorder repeats a durable identity."""
    best_by_identity: dict[Any, dict] = {}
    for request in requests:
        key = _request_role_key(request)
        current = best_by_identity.get(key)
        if current is None or _preread_candidate_score(request) >= _preread_candidate_score(current):
            best_by_identity[key] = request
    return [
        request for request in requests
        if best_by_identity.get(_request_role_key(request)) is request
    ]


def _attach_request_role(req: dict, role: dict) -> dict:
    out = dict(req)
    out["_request_role"] = role
    return out


def _request_fact_entry(req: dict, role: dict) -> dict[str, Any]:
    """Normalize one captured request into the canonical RequestFacts shape."""
    request_index = req.get("index")
    response_json = req.get("response_json", req.get("json"))
    query = _request_query_values(req)
    body = _parse_body(req.get("post_data"))
    body_paths = [
        path for path, _tokens, _serialized, _raw in (_leaf_paths(body) if body is not None else [])
    ]
    out = {
        "request_index": request_index,
        # Leave the identity empty when the recorder supplied neither id nor
        # index; _request_fact_key then derives the same stable signature every
        # time this request is normalized.
        "request_id": str(req.get("request_id") or req.get("id") or request_index or ""),
        "page_id": req.get("page_id") or req.get("pageId"),
        "frame_id": req.get("frame_id") or req.get("frameId"),
        "sequence": req.get("sequence", request_index),
        "method": (req.get("method") or "").upper(),
        "url": req.get("url") or "",
        "path": _request_path(req),
        "headers": dict(req.get("headers") or {}),
        "query": query,
        "query_paths": [f"query.{key}" for key in query],
        "body_paths": body_paths,
        "content_type": req.get("content_type") or "",
        "post_data": req.get("post_data"),
        "response_status": req.get("response_status", req.get("status")),
        "response_json": response_json,
        "response_kind": req.get("response_kind") or ("json" if response_json is not None else ""),
        "response_text": req.get("response_text"),
        "response_empty": bool(req.get("response_empty")),
        "response_size": req.get("response_size"),
        "response_schema": _schema_from_response_value(response_json) if response_json is not None else {},
        "timestamp": req.get("timestamp") or req.get("captured_at"),
    }
    for causal_key in _REQUEST_OBSERVER_KEYS:
        causal_value = req.get(causal_key)
        if causal_value not in (None, ""):
            out[causal_key] = causal_value
    return out


def _request_signature(req: dict) -> tuple[str, str]:
    return ((req.get("method") or "GET").upper(), _request_path(req))


def _request_fact_key(entry: dict[str, Any]) -> str:
    request_id = str(entry.get("request_id") or "").strip()
    if request_id:
        return request_id
    request_index = entry.get("request_index")
    if request_index is not None:
        return f"idx:{request_index}"
    raw = json.dumps({
        "method": (entry.get("method") or "").upper(),
        "path": entry.get("path") or entry.get("url") or "",
        "sequence": entry.get("sequence"),
    }, ensure_ascii=False, sort_keys=True, default=str)
    return "sig:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _request_fact_from_entry(entry: dict[str, Any]) -> RequestFact:
    payload = dict(entry)
    payload["request_id"] = _request_fact_key(entry)
    return RequestFact.model_validate(payload)


def _request_analysis_from_entry(entry: dict[str, Any], role: dict[str, Any], bucket: str) -> RequestAnalysis:
    role_name = str(role.get("role") or "")
    if role_name == "submit_anchor":
        role_name = "business_write"
    semantic_roles = [str(value) for value in (role.get("semantic_roles") or []) if str(value)]
    role_semantic = {
        "business_get": "business_query",
        "read_option": "option_source",
        "read_context": "context_read",
        "submit_anchor": "business_write",
        "business_write": "business_write",
    }.get(role_name)
    if role_semantic and role_semantic not in semantic_roles:
        semantic_roles.append(role_semantic)
    return RequestAnalysis.model_validate({
        **role,
        "request_id": _request_fact_key(entry),
        "role": role_name,
        "semantic_roles": semantic_roles,
        "keep": bool(role.get("keep")),
        "reason": role.get("reason") or role.get("keep_reason") or role.get("filter_reason") or "",
        "confidence": float(role.get("confidence") or 0.0),
        "evidence": {
            **dict(role.get("evidence") or {}),
            "actor": str(role.get("actor") or "heuristic"),
        },
        "bucket": bucket,
        "filter_reason": role.get("filter_reason") or "",
    })


def _is_api_like_request_fact(entry: dict[str, Any], role: dict[str, Any] | None = None) -> bool:
    path = _request_path(entry).lower()
    if not path:
        return False
    if re.search(r"\.(?:css|js|mjs|map|png|jpe?g|gif|svg|ico|webp|woff2?|ttf|eot|html?|txt|xml)$", path):
        return False
    role_name = str((role or {}).get("role") or entry.get("role") or "")
    if role_name in {"noise", "auth"}:
        return False
    if role_name in {"submit_anchor", "business_write", "business_get", "read_context", "read_option"}:
        return True
    if entry.get("response_json") is not None:
        return True
    method = str(entry.get("method") or "").upper()
    content_type = str(entry.get("content_type") or "").lower()
    return bool(
        method in {"GET", "POST", "PUT", "PATCH", "DELETE"}
        and (
            entry.get("response_status") is not None
            or entry.get("query_paths")
            or entry.get("body_paths")
            or any(token in content_type for token in ("json", "form", "multipart"))
        )
    )


def _option_sources_from_page_enum_options(
    page_enum_options: dict[str, Any] | None,
    captured_requests: list[dict] | None = None,
    facts_by_id: dict[str, RequestFact] | None = None,
) -> list[dict[str, Any]]:
    if not page_enum_options:
        return []
    enriched = copy.deepcopy(page_enum_options)
    facts = list((facts_by_id or {}).values())
    facts_by_request_id = {fact.request_id: fact for fact in facts if fact.request_id}
    facts_by_request_index = {
        fact.request_index: fact for fact in facts if fact.request_index is not None
    }

    def alias_path(value: Any) -> str:
        path = str(value or "").strip()
        if ":" in path:
            path = path.rsplit(":", 1)[-1]
        return path.removeprefix("query.")

    for raw_entry in enriched.values():
        if not isinstance(raw_entry, dict):
            continue
        aliases = {
            alias_path(alias)
            for alias in [raw_entry.get("field_key"), *(raw_entry.get("field_aliases") or [])]
            if alias_path(alias)
        }
        source_path = _request_path({"url": str(raw_entry.get("source_url") or "")})
        if source_path:
            raw_entry["source_request_ids"] = [
                fact.request_id
                for fact in facts
                if (fact.method or "GET").upper() == "GET"
                and _request_path({"url": fact.path or fact.url}) == source_path
            ]
        observations: list[dict[str, Any]] = []
        for request in captured_requests or []:
            if not _recording_evidence_matches_request(request, raw_entry):
                continue
            fact = facts_by_request_id.get(str(request.get("request_id") or ""))
            if fact is None and request.get("index") is not None:
                fact = facts_by_request_index.get(request.get("index"))
            if fact is None:
                continue
            for wire_path, value in _request_values(request):
                if alias_path(wire_path) not in aliases:
                    continue
                observations.append({
                    "request_id": fact.request_id,
                    "request_index": fact.request_index,
                    "method": fact.method,
                    "path": fact.path or _request_path({"url": fact.url}),
                    "wire_path": wire_path,
                    "value": value,
                    "sequence": fact.sequence,
                    **{
                        key: getattr(fact, key, None)
                        for key in (
                            "trigger_action_id", "trigger_transaction_id", "action_delta_ms",
                        )
                        if getattr(fact, key, None) not in (None, "")
                    },
                })
        if observations:
            raw_entry["request_value_observations"] = observations
        raw_entry["trace_status"] = {
            "control": "observed" if raw_entry.get("control_kind") or aliases else "missing",
            "candidates": "observed" if raw_entry.get("options") else "missing",
            "selection": "observed" if raw_entry.get("selected_label") or raw_entry.get("selected") else "missing",
            "selected_value": "observed" if raw_entry.get("selected_value") not in (None, "") else "missing",
            "source_request": (
                "observed" if raw_entry.get("source_request_ids")
                else "missing" if source_path else "not_declared"
            ),
            "submitted_value": "observed" if observations else "missing",
            "mapping": "complete" if raw_entry.get("mapping_complete") is True else "incomplete",
        }
    return [{"kind": "page_enum_options", "options": enriched}]


def _api_option_source_refs(
    facts_by_id: dict[str, RequestFact],
    analysis: dict[str, RequestAnalysis],
) -> list[dict[str, Any]]:
    """Index captured API enum sources without duplicating their response facts."""
    sources: list[dict[str, Any]] = []
    for request_id, fact in facts_by_id.items():
        request_analysis = analysis.get(request_id)
        semantic_roles = set(request_analysis.semantic_roles or []) if request_analysis else set()
        if not request_analysis or (
            request_analysis.role != "read_option"
            and "enum_options" not in semantic_roles
            and "read_option" not in semantic_roles
        ):
            continue
        read = fact.model_dump(exclude_none=True)
        read["role"] = request_analysis.role
        if _read_is_entity_enrichment_lookup(read):
            continue
        sources.append({
            "kind": "api_response",
            "request_id": request_id,
            "method": fact.method or "GET",
            "path": fact.path or _request_path({"url": fact.url}),
            "page_id": fact.page_id,
            "frame_id": fact.frame_id,
            "sequence": fact.sequence,
            "query_paths": list(getattr(fact, "query_paths", []) or []),
            "response_schema": copy.deepcopy(fact.response_schema or {}),
            **{
                key: getattr(fact, key, None)
                for key in (
                    "trigger_action_id", "trigger_transaction_id", "trigger_event_id",
                    "action_delta_ms", "causality_confidence",
                )
                if getattr(fact, key, None) not in (None, "")
            },
        })
    return sources


def _page_enum_options_from_request_facts(request_facts: RequestFacts | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for source in (request_facts.option_sources if request_facts else []) or []:
        if not isinstance(source, dict):
            continue
        if source.get("kind") == "page_enum_options" and isinstance(source.get("options"), dict):
            out.update(source.get("options") or {})
    return out


def _build_request_facts(
    captured_requests: list[dict],
    request_roles: list[dict],
    selected_keys: set[Any],
    *,
    diagnostics: list[dict[str, Any]] | None = None,
    page_enum_options: dict[str, Any] | None = None,
    page_events: list[dict[str, Any]] | None = None,
    field_evidence: list[dict[str, Any]] | None = None,
) -> RequestFacts:
    """Build the canonical request ledger directly from recorder evidence."""
    facts_by_id: dict[str, RequestFact] = {}
    fact_scores: dict[str, tuple[int, int, int, float]] = {}
    analysis: dict[str, RequestAnalysis] = {}
    usage: dict[str, RequestUsage] = {}
    selected_signatures: set[tuple[str, str]] = set()
    for req, role in zip(captured_requests or [], request_roles or []):
        entry = _request_fact_entry(req, role)
        if not _is_api_like_request_fact(entry, role):
            continue
        rid = _request_fact_key(entry)
        fact = _request_fact_from_entry(entry)
        fact_score = _preread_candidate_score(req)
        replace_fact = rid not in facts_by_id or fact_score >= fact_scores[rid]
        if replace_fact:
            facts_by_id[rid] = fact
            fact_scores[rid] = fact_score

        key = _request_role_key(req)
        role_name = str(role.get("role") or "")
        signature = _request_signature(entry)
        if key in selected_keys:
            bucket = "selected_steps"
            selected_signatures.add(signature)
        elif (
            role_name in {"read_option", "read_context", "business_get"}
            and entry.get("response_json") is not None
            and signature not in selected_signatures
        ):
            bucket = "candidate_reads"
        else:
            bucket = "filtered_requests"
        if replace_fact:
            analysis[rid] = _request_analysis_from_entry(entry, role, bucket)

            materialized_step_id = str(req.get("materialized_step_id") or "")
            usage[rid] = RequestUsage(
                request_id=rid,
                materialized_step_id=materialized_step_id,
                state="materialized" if materialized_step_id else "captured",
            )

    requests = sorted(
        facts_by_id.values(),
        key=lambda fact: (
            _request_sequence_value(fact.sequence if fact.sequence is not None else fact.request_index) is None,
            _request_sequence_value(fact.sequence if fact.sequence is not None else fact.request_index) or 0,
        ),
    )
    option_sources = _option_sources_from_page_enum_options(
        page_enum_options,
        captured_requests,
        facts_by_id,
    )
    option_sources.extend(_api_option_source_refs(facts_by_id, analysis))
    return RequestFacts(
        requests=requests,
        diagnostics=list(diagnostics or []),
        field_evidence=copy.deepcopy(field_evidence or []),
        page_events=list(page_events or []),
        option_sources=option_sources,
        analysis=analysis,
        usage=usage,
    )


def _request_fact_item(
    fact: RequestFact,
    analysis: RequestAnalysis | None,
    usage: RequestUsage | None,
) -> dict[str, Any]:
    item = fact.model_dump(exclude_none=True)
    item.update({
        "role": analysis.role if analysis else "",
        "semantic_roles": list(analysis.semantic_roles or []) if analysis else [],
        "keep": bool(analysis.keep) if analysis else False,
        "reason": analysis.reason if analysis else "",
        "confidence": float(analysis.confidence) if analysis else 0.0,
        "evidence": dict(analysis.evidence or {}) if analysis else {},
        "bucket": analysis.bucket if analysis else "",
        "filter_reason": analysis.filter_reason if analysis else "",
        "state": usage.state if usage else "captured",
        "materialized_step_id": usage.materialized_step_id if usage else "",
        "used_by_capabilities": list(usage.used_by_capabilities or []) if usage else [],
    })
    return item


def _request_fact_items(spec: FlowSpec) -> list[dict[str, Any]]:
    return [
        _request_fact_item(
            fact,
            spec.request_facts.analysis.get(fact.request_id or _request_fact_key(fact.model_dump())),
            spec.request_facts.usage.get(fact.request_id or _request_fact_key(fact.model_dump())),
        )
        for fact in (spec.request_facts.requests or [])
    ]


def _find_request_fact_item(
    spec: FlowSpec,
    *,
    request_index: Any = None,
    request_id: str = "",
) -> dict[str, Any] | None:
    for item in _request_fact_items(spec):
        if request_index is not None and item.get("request_index") == request_index:
            return item
        if request_id and str(item.get("request_id") or "") == request_id:
            return item
    return None


_SCREENSHOT_OPTION_CONTROL_KINDS = frozenset({
    "select", "combobox", "cascader", "picker", "radio", "tree_select",
})


def _merge_response_schemas(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Merge observed response shapes without treating the first row as universal."""
    if left == right:
        return copy.deepcopy(left)
    if not left or not right:
        return {}
    left_type = left.get("type")
    right_type = right.get("type")
    if left_type == right_type == "object":
        left_props = left.get("properties") if isinstance(left.get("properties"), dict) else {}
        right_props = right.get("properties") if isinstance(right.get("properties"), dict) else {}
        return {
            "type": "object",
            "properties": {
                name: (
                    _merge_response_schemas(left_props[name], right_props[name])
                    if name in left_props and name in right_props
                    else copy.deepcopy(left_props.get(name, right_props.get(name, {})))
                )
                for name in dict.fromkeys([*left_props, *right_props])
            },
        }
    if left_type == right_type == "array":
        return {
            "type": "array",
            "items": _merge_response_schemas(
                left.get("items") if isinstance(left.get("items"), dict) else {},
                right.get("items") if isinstance(right.get("items"), dict) else {},
            ),
        }
    if {left_type, right_type} <= {"integer", "number"}:
        return {"type": "number"}
    alternatives: list[dict[str, Any]] = []
    for schema in (left, right):
        nested = schema.get("anyOf") if set(schema) == {"anyOf"} else None
        candidates = nested if isinstance(nested, list) else [schema]
        for candidate in candidates:
            if isinstance(candidate, dict) and candidate not in alternatives:
                alternatives.append(copy.deepcopy(candidate))
    return {"anyOf": alternatives}


def _schema_from_response_value(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {"type": "number"}
    if isinstance(value, list):
        item_schema: dict[str, Any] = {}
        for item in value[:80]:
            observed = _schema_from_response_value(item)
            item_schema = (
                observed if not item_schema
                else _merge_response_schemas(item_schema, observed)
            )
        return {"type": "array", "items": item_schema}
    if isinstance(value, dict):
        return {
            "type": "object",
            "properties": {
                str(k): _schema_from_response_value(v)
                for k, v in list(value.items())[:80]
            },
        }
    if value is None:
        # A recorded null carries no evidence about the field's eventual scalar
        # type.  Keeping it unconstrained prevents later non-null rows/pages
        # from being rejected by a false ``null-only`` output contract.
        return {}
    return {"type": "string"}


def _compact_repeated_endpoint_observations(
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collapse repeated background reads in the model-only projection.

    Raw RequestFacts stay append-only.  The model needs the endpoint shape and
    causal samples, not dozens of identical polling/option-read payloads.
    Business, retained and materialized requests are never collapsed.
    """
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    passthrough: list[dict[str, Any]] = []
    for item in items:
        role = str(item.get("role") or "")
        kind = str(item.get("kind") or "")
        path = str(item.get("path") or item.get("url") or "")
        collapsible = bool(path) and (
            kind == "api_response"
            or (
                str(item.get("method") or "GET").upper() in {"GET", "HEAD", "OPTIONS"}
                and role not in {"business_get", "business_write", "submit_anchor"}
                and item.get("keep") is not True
                and not item.get("materialized_step_id")
            )
        )
        if not collapsible:
            passthrough.append(item)
            continue
        schema = json.dumps(
            item.get("response_schema") or {},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        query = item.get("query") if isinstance(item.get("query"), dict) else {}
        query_identity = {
            str(key): (value[0] if isinstance(value, list) and len(value) == 1 else value)
            for key, value in query.items()
            if not _looks_pagination_field(str(key), f"query.{key}")
        }
        signature = (
            kind or role,
            str(item.get("method") or "GET").upper(),
            path,
            json.dumps(item.get("query_paths") or [], ensure_ascii=False, sort_keys=True),
            json.dumps(query_identity, ensure_ascii=False, sort_keys=True, default=str),
            json.dumps(item.get("body_paths") or [], ensure_ascii=False, sort_keys=True),
            schema,
        )
        groups.setdefault(signature, []).append(item)

    compacted = list(passthrough)
    for observations in groups.values():
        latest = dict(observations[-1])
        if len(observations) > 1:
            request_ids = [
                str(item.get("request_id") or "") for item in observations
                if item.get("request_id")
            ]
            event_ids = [
                str(item.get("trigger_event_id") or "") for item in observations
                if item.get("trigger_event_id")
            ]
            action_ids = [
                str(item.get("trigger_action_id") or "") for item in observations
                if item.get("trigger_action_id")
            ]
            latest.update({
                "observation_count": len(observations),
                "request_id_samples": list(dict.fromkeys(request_ids[:1] + request_ids[-3:])),
                "trigger_event_id_samples": list(dict.fromkeys(event_ids[:1] + event_ids[-3:])),
                "trigger_action_id_samples": list(dict.fromkeys(action_ids[:1] + action_ids[-3:])),
            })
        compacted.append(latest)
    return sorted(
        compacted,
        key=lambda item: _request_order_value(item)
        if _request_order_value(item) is not None else -1,
    )


def _request_fact_has_record_identity(item: dict[str, Any]) -> bool:
    query = item.get("query")
    if isinstance(query, dict):
        for key, raw in query.items():
            value = raw[0] if isinstance(raw, list) and raw else raw
            if _is_document_record_identity_path(str(key), f"query.{key}") and str(value or "").strip():
                return True
    return bool(_record_identity_values_for_request(item))


def _request_fact_signature_key(entry: dict[str, Any]) -> str:
    from dano.execution.page.request_identity import request_composite_signature

    return request_composite_signature(entry)


def _request_fact_key_from_entry(entry: dict[str, Any]) -> str:
    if entry.get("request_id"):
        return f"id:{entry.get('request_id')}"
    if entry.get("request_index") is not None:
        return f"idx:{entry.get('request_index')}"
    signature = _request_fact_signature_key(entry)
    return f"sig:{signature}" if signature else ""


def _request_sequence_value(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:  # noqa: BLE001
        return None

_PENDING_FLOW_SPEC_HELPERS = {'route_identity': 'dano.execution.page.recording_semantic_index'}


def _bind_flow_spec_helpers() -> None:
    import sys
    module_globals = globals()
    for name, owner in _PENDING_FLOW_SPEC_HELPERS.items():
        mod = sys.modules.get(owner)
        if mod is None or not hasattr(mod, name):
            continue
        module_globals[name] = getattr(mod, name)


_bind_flow_spec_helpers()
