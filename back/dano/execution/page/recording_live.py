"""Live recording facts and agent-authored semantic annotations."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import re
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from dano.execution.page.recording_field_identity import (
    canonical_wire_path,
    FieldRef,
    FieldReferenceDeferred,
    resolve_field_ref,
    stored_container_path,
)
from dano.execution.page.value_tracing import (
    discover_response_key_maps,
    discover_workflow_value_links,
)
from dano.execution.page.wire_format import date_span_days
from dano.infra.token_store import mask_headers


LIVE_RECORDING_AGENT_OPS = frozenset({
    "set_goal",
    "set_request_role",
    "set_param_source",
    "set_param_type",
    "set_param_required",
    "set_param_enum",
    "rename_field",
    "propose_dependency",
    "add_pitfall",
    "confirm_dependency",
    "bind_verify_read",
    "attach_enum_options",
    "mark_unverified",
})

_PARAM_SOURCE_KINDS = frozenset({
    "caller_input", "constant", "session", "context", "response_binding", "computed",
    "generated",
})
_PARAM_BUSINESS_TYPES = frozenset({
    "string", "email", "url", "number", "integer", "boolean", "date",
    "datetime", "time", "array", "object", "enum", "list-enum",
})
_CANONICAL_REQUEST_ROLES = frozenset({
    "auth", "support", "option", "context", "business_read", "business_write",
})
_REQUEST_ROLE_FAMILIES = (
    ("support", frozenset({
        "support", "telemetry", "metric", "metrics", "trace", "analytics", "beacon",
        "noise", "static", "asset", "heartbeat", "ping", "upload", "graphql",
    })),
    ("auth", frozenset({"auth", "authentication", "authorization", "token", "login"})),
    ("option", frozenset({"option", "options", "dictionary", "dict", "enum", "candidate", "candidates"})),
    ("context", frozenset({"preflight", "context", "navigation", "page", "load", "entry", "fact", "check"})),
    ("business_write", frozenset({
        "write", "submit", "mutation", "create", "update", "delete", "save",
        "approve", "reject", "withdraw", "commit",
    })),
    ("business_read", frozenset({"get", "read", "query", "search", "list", "status", "inspect", "preview"})),
)

_INTERNAL_REQUEST_ROLE = {
    "auth": "auth",
    "support": "read_context",
    "option": "read_option",
    "context": "read_context",
    "business_read": "business_get",
    "business_write": "business_write",
}

_LIVE_NOTEBOOK_OPS = frozenset({
    "set_goal",
    "set_request_role",
    "set_param_source",
    "set_param_type",
    "set_param_required",
    "set_param_enum",
    "rename_field",
    "propose_dependency",
    "add_pitfall",
})


@dataclass(frozen=True)
class LiveNotebook:
    """Evidence-bound live hypotheses, isolated from the formal FlowSpec.

    The Pi shadow spec is an implementation detail of the live tool session.
    Only accepted, replayable candidate operations and their evidence leave
    that shadow.  Final materialization replays these candidates against the
    frozen facts, so a live model conclusion can accelerate the pipeline but
    cannot itself publish, verify, or mutate the authoritative draft.
    """

    meta: dict

    @classmethod
    def from_shadow(cls, shadow) -> "LiveNotebook":  # noqa: ANN001
        raw_meta = dict(getattr(shadow, "meta", None) or {})
        operations = [
            deepcopy(operation)
            for operation in raw_meta.get("recording_agent_ops") or []
            if isinstance(operation, dict)
            and str(operation.get("op") or "") in _LIVE_NOTEBOOK_OPS
        ]
        capability_model = raw_meta.get("capability_model")
        meta = {
            "recording_agent_ops": operations,
            "agent_insights": [
                deepcopy(item)
                for item in raw_meta.get("agent_insights") or []
                if isinstance(item, dict)
            ][-100:],
        }
        if isinstance(capability_model, dict):
            meta["capability_model"] = deepcopy(capability_model)
        if raw_meta.get("agent_answers"):
            meta["agent_answers"] = deepcopy(raw_meta["agent_answers"])
        if raw_meta.get("live_pending_questions"):
            meta["live_pending_questions"] = [
                deepcopy(item)
                for item in raw_meta["live_pending_questions"]
                if isinstance(item, dict)
            ][-50:]
        return cls(meta=meta)

    @property
    def insights(self) -> list[dict]:
        return [
            deepcopy(item)
            for item in self.meta.get("agent_insights") or []
            if isinstance(item, dict)
        ]

    @property
    def pending_questions(self) -> list[dict]:
        return [
            deepcopy(item)
            for item in self.meta.get("live_pending_questions") or []
            if isinstance(item, dict)
        ]

    def apply_to(self, finalized_spec):  # noqa: ANN001, ANN202
        """Revalidate every hypothesis against one finalized fact snapshot."""

        return merge_live_agent_state(
            SimpleNamespace(meta=deepcopy(self.meta)),
            finalized_spec,
        )
_SECRET_QUERY_HINTS = ("authorization", "cookie", "token", "secret", "password", "session", "credential")
_INLINE_SECRET_RE = re.compile(
    r"(?i)\b(Bearer|Basic|Token)\s+[A-Za-z0-9._~+/-]{8,}|\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
)
_DEFAULT_DELTA_LIMIT = 25
_MAX_DELTA_LIMIT = 50


def compact_model_payload(
    node,
    *,
    max_depth: int = 5,
    max_items: int = 20,
    max_string: int = 800,
    _depth: int = 0,
):  # noqa: ANN001, ANN202
    """Keep model-facing evidence useful while placing a hard bound on each branch."""
    if isinstance(node, str):
        if len(node) <= max_string:
            return node
        return f"{node[:max_string]}…<truncated {len(node) - max_string} chars>"
    if isinstance(node, dict):
        if _depth >= max_depth:
            return {"__truncated__": "object", "__total_keys__": len(node)}
        items = list(node.items())
        out = {
            str(key): compact_model_payload(
                value,
                max_depth=max_depth,
                max_items=max_items,
                max_string=max_string,
                _depth=_depth + 1,
            )
            for key, value in items[:max_items]
        }
        if len(items) > max_items:
            out["__truncated_keys__"] = len(items) - max_items
        return out
    if isinstance(node, list):
        if _depth >= max_depth:
            return [{"__truncated_items__": len(node)}]
        out = [
            compact_model_payload(
                value,
                max_depth=max_depth,
                max_items=max_items,
                max_string=max_string,
                _depth=_depth + 1,
            )
            for value in node[:max_items]
        ]
        if len(node) > max_items:
            out.append({"__truncated_items__": len(node) - max_items})
        return out
    return deepcopy(node)


def _redact_url(url: str) -> str:
    parsed = urlparse(url)
    query = [
        (key, "***" if any(hint in key.casefold() for hint in _SECRET_QUERY_HINTS) else value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
    ]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def _redact(node):  # noqa: ANN001, ANN202
    from dano.execution.page.flow_spec import _client_redact_sensitive

    projected = _client_redact_sensitive(node)
    if isinstance(projected, dict):
        return {key: _redact(value) for key, value in projected.items()}
    if isinstance(projected, list):
        return [_redact(value) for value in projected]
    if isinstance(projected, str):
        return _INLINE_SECRET_RE.sub("***", projected)
    return projected


def _request_projection(request: dict) -> dict:
    detailed = bool(
        not str(request.get("role") or "")
        or request.get("keep") is True
        or str(request.get("role") or "") in {
            "business_get", "business_write", "submit_anchor", "read_option",
        }
        or str(request.get("trigger_op") or "").lower() in {
            "click", "submit", "select", "pick", "fill",
        }
    )
    detail_keys = {
        "query", "post_data", "response_json", "headers", "content_type",
    }
    projected = {
        key: deepcopy(request.get(key))
        for key in (
            "request_id", "index", "request_index", "sequence", "timestamp", "method",
            "path", "query", "post_data", "response_status", "status", "response_json",
            "content_type", "role", "keep", "reason", "confidence", "trigger_action_id",
            "trigger_transaction_id", "trigger_op", "trigger_locator", "trigger_page_context",
            "action_delta_ms", "causality_confidence",
        )
        if request.get(key) is not None
        and (detailed or key not in detail_keys)
    }
    projected["url"] = _redact_url(str(request.get("url") or ""))
    if detailed:
        projected["headers"] = mask_headers(request.get("headers"))
    raw_body = projected.get("post_data")
    if isinstance(raw_body, str):
        try:
            projected["post_data"] = json.loads(raw_body)
        except (TypeError, ValueError):
            pass
    projected = _redact(projected)
    for key, settings in {
        "query": (4, 30, 500),
        "post_data": (5, 30, 800),
        # Live deltas share a model turn with the current contract.  The old
        # 20 x 800 response projection made one 10-request page exceed 170 KB
        # and repeatedly exhausted the Pi prompt timeout.  Paths and sample
        # values remain visible; repeated response rows are bounded here.
        "response_json": (3, 8, 160),
    }.items():
        if key in projected:
            projected[key] = compact_model_payload(
                projected[key],
                max_depth=settings[0],
                max_items=settings[1],
                max_string=settings[2],
            )
    return projected


def recording_delta(
    recorder,
    *,
    since_seq: int = 0,
    limit: int = _DEFAULT_DELTA_LIMIT,
    goal_text: str = "",
    captured_requests: list[dict] | None = None,
    page_events: list[dict] | None = None,
) -> dict:  # noqa: ANN001
    """Project a bounded, redacted append-only request delta for the model."""
    requests = (
        captured_requests
        if captured_requests is not None
        else recorder.captured_all_requests()
    )
    start = max(0, min(int(since_seq or 0), len(requests)))
    page_size = max(1, min(int(limit or _DEFAULT_DELTA_LIMIT), _MAX_DELTA_LIMIT))
    fresh = requests[start:start + page_size]
    next_seq = start + len(fresh)
    fresh_ids = {str(request.get("request_id") or "") for request in fresh}
    graph_requests = [
        request for request in requests[:next_seq]
        if not str(request.get("role") or "")
        or request.get("keep") is True
        or str(request.get("role") or "") in {"business_get", "business_write", "submit_anchor"}
    ]
    candidates = [
        item for item in discover_workflow_value_links(graph_requests)
        if item.get("source_request_id") in fresh_ids or item.get("target_request_id") in fresh_ids
    ]
    structure_candidates = [
        item for item in discover_response_key_maps(graph_requests)
        if item.get("source_request_id") in fresh_ids or item.get("target_request_id") in fresh_ids
    ]
    page_events = (
        page_events if page_events is not None else recorder.recorded_page_events()
    )
    action_ids = {
        str(request.get("trigger_action_id") or "") for request in fresh
        if request.get("trigger_action_id")
    }
    transaction_ids = {
        str(request.get("trigger_transaction_id") or "") for request in fresh
        if request.get("trigger_transaction_id")
    }
    related_events = [
        event for event in page_events
        if isinstance(event, dict) and (
            str(event.get("action_id") or "") in action_ids
            or str(event.get("transaction_id") or "") in transaction_ids
        )
    ] if fresh else []
    return {
        "since_seq": start,
        "next_seq": next_seq,
        "total_seq": len(requests),
        "page_size": page_size,
        "has_more": next_seq < len(requests),
        "goal_text": str(goal_text or ""),
        "requests": [_request_projection(request) for request in fresh],
        "page_events": compact_model_payload(_redact(related_events[-50:]), max_depth=5, max_items=50),
        "heuristic_candidates": {
            "request_roles": [
                {
                    "request_id": str(request.get("request_id") or ""),
                    "role": str(request.get("role") or ""),
                    "reason": str(request.get("reason") or ""),
                    "confidence": float(request.get("confidence") or 0),
                }
                for request in fresh
            ],
            "value_links": candidates,
            "response_key_maps": structure_candidates,
        },
    }


def _append_meta_list(spec, key: str, item: dict) -> None:  # noqa: ANN001
    spec.meta = dict(spec.meta or {})
    values = [dict(value) for value in spec.meta.get(key) or [] if isinstance(value, dict)]
    values.append(deepcopy(item))
    spec.meta[key] = values[-500:]


def _request_step(spec, request_id: str):  # noqa: ANN001, ANN202
    if not request_id:
        return None
    for step in spec.steps:
        meta = step.source_meta or {}
        if str(meta.get("request_id") or "") == request_id:
            return step
    usage = (spec.request_facts.usage or {}).get(request_id)
    materialized = str(usage.materialized_step_id or "") if usage is not None else ""
    return next((step for step in spec.steps if step.step_id == materialized), None)


def _canonical_request_role(spec, request_id: str, role: str) -> str:  # noqa: ANN001
    """Translate model vocabulary to the finite role contract used by materialization."""
    normalized = re.sub(r"[^a-z0-9]+", "_", str(role or "").strip().casefold()).strip("_")
    if normalized in _CANONICAL_REQUEST_ROLES:
        return normalized
    tokens = frozenset(normalized.split("_"))
    for canonical, family in _REQUEST_ROLE_FAMILIES:
        if tokens & family:
            return canonical
    if normalized == "execute":
        fact = next(
            (item for item in spec.request_facts.requests if item.request_id == request_id),
            None,
        )
        method = str(getattr(fact, "method", "") or "").upper()
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            return "business_write"
        if method in {"GET", "HEAD", "OPTIONS"}:
            return "business_read"
    raise ValueError(
        f"unsupported request role {role!r}; use one of {sorted(_CANONICAL_REQUEST_ROLES)}"
    )


def _known_request_id(spec, request_id: str) -> bool:  # noqa: ANN001
    return bool(
        request_id
        and (
            any(fact.request_id == request_id for fact in spec.request_facts.requests)
            or request_id in (spec.request_facts.usage or {})
            or request_id in set((spec.meta or {}).get("live_request_ids") or [])
        )
    )


def _field_target(spec, step_or_request_id: str, path: str):  # noqa: ANN001, ANN202
    is_step_id = any(item.step_id == step_or_request_id for item in spec.steps)
    ref = FieldRef(
        step_id=step_or_request_id if is_step_id else "",
        request_id="" if is_step_id else step_or_request_id,
        wire_path=path,
    )
    try:
        resolved = resolve_field_ref(spec, ref)
    except FieldReferenceDeferred:
        return None, None
    return resolved.step, resolved.param


def _canonical_deferred_wire_path(spec, request_id: str, path: str) -> str:  # noqa: ANN001
    raw = str(path or "").removeprefix("request.")
    if raw.startswith(("body.", "query.", "headers.", "url_path[")):
        return raw
    fact = next(
        (item for item in spec.request_facts.requests if item.request_id == request_id),
        None,
    )
    method = str(getattr(fact, "method", "") or "").upper()
    return f"query.{raw}" if method in {"GET", "HEAD", "OPTIONS"} else f"body.{raw}"


def _reject_response_field_target(spec, identifier: str, path: str) -> None:  # noqa: ANN001
    raw = str(path or "").removeprefix("request.")
    if not raw.startswith(("response.", "response[")):
        return
    step = next((item for item in spec.steps if item.step_id == identifier), None)
    request_id = str((step.source_meta or {}).get("request_id") or "") if step else identifier
    fact = next(
        (item for item in spec.request_facts.requests if item.request_id == request_id),
        None,
    )
    available = {
        str(value)
        for value in [
            *(getattr(fact, "query_paths", None) or []),
            *(getattr(fact, "body_paths", None) or []),
            *(getattr(fact, "header_paths", None) or []),
            *(param.path for param in (step.params if step else []) if param.path),
        ]
        if str(value or "")
    }
    if fact is not None and isinstance(getattr(fact, "query", None), dict):
        available.update(f"query.{key}" for key in fact.query)
    suffix = f"; available request paths: {', '.join(sorted(available))}" if available else ""
    raise ValueError(
        f"response path {raw!r} cannot target a request field{suffix}"
    )


def _canonical_recorded_agent_op(spec, edit: dict) -> dict:  # noqa: ANN001
    """Persist deferred field operations as a replayable canonical FieldRef."""
    stored = {**deepcopy(edit), "actor": "agent"}
    if str(edit.get("op") or "") not in {
        "set_param_source", "set_param_type", "set_param_required", "set_param_enum", "rename_field",
        "attach_enum_options",
    }:
        return stored
    identifier = str(edit.get("step_id") or edit.get("request_id") or "")
    path = str(edit.get("wire_path") or edit.get("path") or "")
    if not identifier or not path:
        return stored
    _reject_response_field_target(spec, identifier, path)
    is_step_id = any(item.step_id == identifier for item in spec.steps)
    try:
        resolved = resolve_field_ref(spec, FieldRef(
            step_id=identifier if is_step_id else "",
            request_id="" if is_step_id else identifier,
            wire_path=path,
        ))
        request_id = resolved.request_id
        step_id = resolved.step_id
        wire_path = resolved.wire_path
    except FieldReferenceDeferred:
        request_id = identifier
        step_id = ""
        wire_path = _canonical_deferred_wire_path(spec, request_id, path)
    except ValueError:
        return stored
    stored.pop("path", None)
    stored.pop("step_id", None)
    stored["request_id"] = request_id
    if step_id:
        stored["step_id"] = step_id
    stored["wire_path"] = wire_path
    stored["field_ref"] = {
        **({"step_id": step_id} if step_id else {"request_id": request_id}),
        "wire_path": wire_path,
    }
    return stored


def _evidence_refs(edit: dict) -> list[str]:
    raw = edit.get("evidence_refs") or edit.get("evidence") or []
    if isinstance(raw, (str, dict)):
        raw = [raw]
    return [
        str(item.get("ref") or item.get("source") or "") if isinstance(item, dict) else str(item)
        for item in raw
        if item not in (None, "", {})
    ]


def _grounded_ref_tokens(spec) -> set[str]:  # noqa: ANN001
    """Identifiers of recorded facts an agent conclusion may cite as evidence."""
    tokens: set[str] = {"goal_text"}
    for fact in spec.request_facts.requests or []:
        if fact.request_id:
            tokens.add(str(fact.request_id))
    tokens.update(str(key) for key in (spec.request_facts.usage or {}))
    tokens.update(str(key) for key in (spec.meta or {}).get("live_request_ids") or [])
    for step in spec.steps:
        tokens.add(step.step_id)
        request_id = str((step.source_meta or {}).get("request_id") or "")
        if request_id:
            tokens.add(request_id)
    for event in spec.request_facts.page_events or []:
        if not isinstance(event, dict):
            continue
        for key in ("event_id", "action_id", "transaction_id"):
            value = str(event.get(key) or "")
            if value:
                tokens.add(value)
    for item in getattr(spec.request_facts, "field_evidence", []) or []:
        if isinstance(item, dict):
            for key in ("evidence_id", "event_id", "action_id", "field"):
                value = str(item.get(key) or "")
                if value:
                    tokens.add(value)
    return tokens


def _require_grounded_refs(spec, op: str, evidence_refs: list[str]) -> None:  # noqa: ANN001
    """Field-axis conclusions must cite at least one recorded fact identifier."""
    tokens = _grounded_ref_tokens(spec)
    for ref in evidence_refs:
        text = str(ref)
        if text in tokens or any(token and token in text for token in tokens):
            return
    raise ValueError(
        f"{op} evidence_refs must cite recorded facts "
        "(request_id/event_id/step_id observed in this recording); "
        f"none of {evidence_refs!r} resolves"
    )


def _normalized_field_alias(value: object) -> str:
    text = str(value or "").strip()
    text = text.removeprefix("request.").removeprefix("body.").removeprefix("query.")
    return text.casefold()


def _evidence_matches_refs(item: dict, evidence_refs: list[str]) -> bool:
    identifiers = {
        str(item.get(key) or "")
        for key in ("evidence_id", "event_id", "action_id", "transaction_id")
    }
    identifiers.update(str(value or "") for value in (item.get("event_refs") or []))
    identifiers.discard("")
    return any(
        ref == identifier or ref.endswith(f":{identifier}")
        for ref in evidence_refs
        for identifier in identifiers
    )


def _captured_requests(spec) -> list[dict]:  # noqa: ANN001
    """Return the immutable request facts in value-tracing wire format."""
    requests = [
        fact.model_dump(mode="json")
        for fact in spec.request_facts.requests
    ]
    by_id = {
        str(request.get("request_id") or ""): request
        for request in requests
        if request.get("request_id")
    }
    # Older imported drafts may have response/body samples only on the
    # materialized step.  Fill missing fact fields without overwriting the
    # actual captured request.
    for step in spec.steps:
        request_id = str((step.source_meta or {}).get("request_id") or "")
        request = by_id.get(request_id)
        if request is None:
            continue
        if request.get("response_json") is None and step.response_json is not None:
            request["response_json"] = deepcopy(step.response_json)
        if request.get("post_data") in (None, "") and step.body_source not in (None, ""):
            request["post_data"] = deepcopy(step.body_source)
        request["url"] = request.get("url") or step.url or step.path
        request["path"] = request.get("path") or step.path
        request["method"] = request.get("method") or step.method
    return requests


def _captured_value_match(
    spec,
    *,
    source_request_id: str,
    source_path: str,
    target_step,
    target_path: str,
) -> dict | None:  # noqa: ANN001
    """Find one exact recorded response-to-request value reuse."""
    target_request_id = str((target_step.source_meta or {}).get("request_id") or "")
    source_wire_path = f"response.{str(source_path or '').removeprefix('response.')}"
    target_wire_path = canonical_wire_path(target_step, target_path)
    for candidate in discover_workflow_value_links(_captured_requests(spec)):
        if (
            str(candidate.get("source_request_id") or "") == source_request_id
            and str(candidate.get("source_path") or "") == source_wire_path
            and str(candidate.get("target_request_id") or "") == target_request_id
            and str(candidate.get("target_path") or "") == target_wire_path
        ):
            return deepcopy(candidate)
    return None


def _field_evidence_candidates(
    spec, step, param, *, evidence_refs: list[str] | None = None,
) -> list[dict]:  # noqa: ANN001
    """Return recorder controls that can be tied to this exact request field."""
    request_id = str((step.source_meta or {}).get("request_id") or "")
    wire_path = canonical_wire_path(step, param.path)
    aliases = {
        _normalized_field_alias(param.path),
        _normalized_field_alias(param.key),
        _normalized_field_alias(param.label),
        _normalized_field_alias(str(param.path or "").split(".")[-1]),
    }
    aliases.discard("")
    matches: list[dict] = []
    for item in getattr(spec.request_facts, "field_evidence", []) or []:
        if not isinstance(item, dict):
            continue
        binding_status = str(item.get("binding_status") or "")
        if binding_status:
            if (
                binding_status == "bound"
                and str(item.get("request_id") or "") == request_id
                and str(item.get("wire_path") or "") == wire_path
            ):
                matches.append(item)
            elif (
                binding_status == "unbound"
                and evidence_refs
                and _evidence_matches_refs(item, evidence_refs)
            ):
                # An unbound control is not useless evidence.  A later semantic
                # operation may identify it by its immutable event/evidence id.
                # Bound evidence remains authoritative and can never be
                # retargeted this way.
                matches.append(item)
            continue
        evidence_request_id = str(item.get("request_id") or "")
        if evidence_request_id and request_id and evidence_request_id != request_id:
            continue
        evidence_path = str(item.get("wire_path") or item.get("path") or "")
        if evidence_request_id and evidence_path:
            if evidence_path == wire_path or evidence_path == str(param.path or ""):
                matches.append(item)
            continue
        item_aliases = {
            _normalized_field_alias(item.get("path")),
            _normalized_field_alias(item.get("key")),
            _normalized_field_alias(item.get("field")),
            *(
                _normalized_field_alias(alias)
                for alias in (item.get("field_aliases") or [])
            ),
        }
        item_aliases.discard("")
        if aliases & item_aliases:
            matches.append(item)
    return matches


def _require_required_grounding(
    spec, step, param, required: bool, *, evidence_refs: list[str] | None = None,
) -> None:  # noqa: ANN001
    candidates = _field_evidence_candidates(
        spec, step, param, evidence_refs=evidence_refs,
    )
    observed = {
        item.get("required_observed", item.get("required"))
        for item in candidates
        if isinstance(item.get("required_observed", item.get("required")), bool)
    }
    if not observed:
        raise ValueError(
            f"required conclusion for {param.path} has no field_evidence required marker"
        )
    if required not in observed:
        raise ValueError(
            f"required={required} contradicts field_evidence for {param.path}: "
            f"observed={sorted(observed)}"
        )


def _require_label_grounding(
    spec, step, param, label: str, *, evidence_refs: list[str] | None = None,
) -> None:  # noqa: ANN001
    candidates = _field_evidence_candidates(
        spec, step, param, evidence_refs=evidence_refs,
    )
    labels = {
        str(item.get(key) or "").strip()
        for item in candidates
        for key in ("label", "suggest_name", "visible_label", "text")
        if str(item.get(key) or "").strip()
    }
    if not labels:
        raise ValueError(f"label conclusion for {param.path} has no field_evidence label")
    if label not in labels:
        raise ValueError(
            f"label {label!r} contradicts field_evidence for {param.path}: "
            f"observed={sorted(labels)!r}"
        )


def _require_type_grounding(
    spec, step, param, business_type: str, *, evidence_refs: list[str],
) -> None:  # noqa: ANN001
    candidates = [
        item for item in _field_evidence_candidates(
            spec, step, param, evidence_refs=evidence_refs,
        )
        if _evidence_matches_refs(item, evidence_refs)
    ]
    observed: set[str] = set()
    for item in candidates:
        kind = str(item.get("control_kind") or item.get("input_type") or "").strip().lower()
        multiple = bool(item.get("multiple"))
        has_options = bool(item.get("options"))
        if kind in {"input", "text", "textarea", "rich_text", "password", "hidden"}:
            observed.add("string")
        elif kind in {"email", "url"}:
            observed.update({"string", kind})
        elif kind in {"number", "range", "slider"}:
            observed.update({"number", "integer"})
        elif kind in {"date", "datetime", "datetime-local", "time"}:
            observed.add("datetime" if kind == "datetime-local" else kind)
        elif kind in {"select", "combobox", "cascader", "picker", "radio", "tree_select"}:
            observed.add("list-enum" if multiple else "enum")
        elif kind == "checkbox":
            observed.add("list-enum" if multiple or has_options else "boolean")
        elif kind == "switch":
            observed.add("boolean")
        elif kind in {"upload", "file"}:
            observed.add("array" if multiple else "string")
    if not observed:
        raise ValueError(
            f"type conclusion for {param.path} has no matching field_evidence control kind"
        )
    if business_type not in observed:
        raise ValueError(
            f"type={business_type!r} contradicts field_evidence for {param.path}: "
            f"observed={sorted(observed)!r}"
        )


def _enum_wire_value(value: object, wire_type: str) -> object:
    if wire_type == "string":
        return str(value) if value is not None else value
    if wire_type in {"number", "integer"} and isinstance(value, str):
        try:
            number = float(value)
            return int(number) if number.is_integer() else number
        except ValueError:
            return value
    return value


def _enum_pair(option: object, wire_type: str = "") -> tuple[str, str] | None:
    if not isinstance(option, dict) or "label" not in option or "value" not in option:
        return None
    return (
        str(option.get("label") or "").strip(),
        json.dumps(
            _enum_wire_value(option.get("value"), wire_type),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ),
    )


def _response_values_at_path(node, path: str) -> list:  # noqa: ANN001
    tokens: list[object] = []
    for name, index in re.findall(r"([^.\[\]]+)|\[([^\]]+)\]", str(path or "")):
        token = name or index
        tokens.append("*" if token == "*" else int(token) if token.isdigit() else token)

    current = [node]
    for token in tokens:
        next_values: list[object] = []
        for value in current:
            if token == "*" and isinstance(value, list):
                next_values.extend(value)
            elif isinstance(token, int) and isinstance(value, list) and token < len(value):
                next_values.append(value[token])
            elif isinstance(token, str) and isinstance(value, dict) and token in value:
                next_values.append(value[token])
        current = next_values
        if not current:
            break
    return current


def _structure_target_keys(step, target_path: str) -> list[str]:  # noqa: ANN001
    container = target_path.removeprefix("body.").removesuffix(".*").removesuffix("[*]")
    prefix = f"{container}."
    keys: list[str] = []
    for param in step.params:
        path = str(param.path or "").removeprefix("body.")
        if not path.startswith(prefix):
            continue
        key = re.split(r"[.\[]", path[len(prefix):], maxsplit=1)[0]
        if key and key not in keys:
            keys.append(key)
    return keys


def _ground_param_in_cited_page_controls(
    spec, step, param, evidence_refs: list[str], *, source_kind: str,
) -> None:  # noqa: ANN001
    """Persist the raw page facts that prove an accepted source conclusion."""
    if source_kind != "caller_input" or not evidence_refs:
        return
    _require_grounded_refs(spec, "set_param_source", evidence_refs)
    candidates = _field_evidence_candidates(
        spec, step, param, evidence_refs=evidence_refs,
    )
    controls = []
    for item in candidates:
        if not _evidence_matches_refs(item, evidence_refs):
            continue
        observed_value = item.get("value")
        recorded_value = param.value if param.value not in (None, "") else param.default_value
        if (
            observed_value not in (None, "")
            and recorded_value not in (None, "")
            and str(observed_value) != str(recorded_value)
        ):
            continue
        interacted = bool(
            item.get("recorded_user_input")
            or str(item.get("op") or "").lower() in {
                "fill", "input", "change", "select", "check", "click",
            }
        )
        editable = bool(
            item.get("editable") is True
            or (
                item.get("disabled") is not True
                and item.get("read_only") is not True
                and item.get("control_disabled") is not True
                and item.get("control_read_only") is not True
            )
        )
        if not interacted or not editable:
            continue
        controls.append({
            "kind": "page_control",
            "source": "recorder_dom",
            "evidence_id": str(item.get("evidence_id") or item.get("event_id") or ""),
            "event_id": str(item.get("event_id") or ""),
            "field_aliases": list(item.get("field_aliases") or []),
            "label": str(item.get("label") or item.get("visible_label") or item.get("field") or ""),
            "control_kind": str(item.get("control_kind") or "unknown"),
            "interacted": True,
            "disabled": bool(item.get("disabled", item.get("control_disabled", False))),
            "read_only": bool(item.get("read_only", item.get("control_read_only", False))),
            "editable": True,
            "required_observed": item.get("required_observed", item.get("required")),
            "request_path": canonical_wire_path(step, param.path),
            "binding_status": "agent_grounded",
        })
    existing_ids = {
        str(item.get("evidence_id") or item.get("event_id") or "")
        for item in (param.evidence or [])
        if isinstance(item, dict) and item.get("kind") == "page_control"
    }
    param.evidence = [
        *list(param.evidence or []),
        *(item for item in controls if item["evidence_id"] not in existing_ids),
    ]


def _recorded_enum_contract(  # noqa: ANN001
    spec, step, param, dictionary_source: str = "",
) -> dict | None:
    from dano.execution.page.flow_spec import _page_enum_options_from_request_facts

    candidates = _field_evidence_candidates(spec, step, param)
    param_aliases = {
        _normalized_field_alias(param.path),
        _normalized_field_alias(param.key),
        _normalized_field_alias(param.label),
        _normalized_field_alias(str(param.path or "").split(".")[-1]),
    }
    control_aliases = param_aliases | {
        _normalized_field_alias(value)
        for item in candidates
        for value in (
            item.get("path"), item.get("key"), item.get("label"), item.get("suggest_name"),
            *(item.get("field_aliases") or []),
        )
        if _normalized_field_alias(value)
    }
    bound_select = any(
        str(item.get("control_kind") or "").lower() in {"select", "radio", "checkbox", "cascader"}
        for item in candidates
    )
    all_evidence = [
        item for item in (getattr(spec.request_facts, "field_evidence", []) or [])
        if isinstance(item, dict)
    ]
    for name, raw in _page_enum_options_from_request_facts(spec.request_facts).items():
        if (
            not isinstance(raw, dict)
            or raw.get("mapping_complete") is not True
            or not isinstance(raw.get("options"), list)
            or not raw.get("options")
        ):
            continue
        recorded_source = str(raw.get("dict_type") or raw.get("dictionary_source") or "")
        if dictionary_source and recorded_source and dictionary_source != recorded_source:
            continue
        aliases = {
            _normalized_field_alias(name),
            _normalized_field_alias(raw.get("field_key")),
            *(
                _normalized_field_alias(alias)
                for alias in (raw.get("field_aliases") or [])
            ),
        }
        aliases.discard("")
        if not aliases & control_aliases:
            continue
        option_labels = {
            _normalized_field_alias(name),
            _normalized_field_alias(raw.get("field_key")),
        }
        label_select = any(
            str(item.get("control_kind") or "").lower()
            in {"select", "radio", "checkbox", "cascader"}
            and bool(option_labels & {
                _normalized_field_alias(item.get("label")),
                _normalized_field_alias(item.get("field")),
            })
            for item in all_evidence
        )
        if bound_select or (
            label_select
            and bool(dictionary_source)
            and dictionary_source == recorded_source
        ):
            return raw
    return None


def _append_insight(spec, *, kind: str, text: str, refs: list[str]) -> None:  # noqa: ANN001
    _append_meta_list(spec, "agent_insights", {"kind": kind, "text": text, "refs": refs})


def _record_agent_op(spec, edit: dict) -> None:  # noqa: ANN001
    spec.meta = dict(spec.meta or {})
    values = [
        dict(value) for value in spec.meta.get("recording_agent_ops") or []
        if isinstance(value, dict)
    ]

    def identity(value: dict) -> dict:
        return {key: item for key, item in value.items() if key != "_deferred"}

    signature = identity(edit)
    for index, existing in enumerate(values):
        if identity(existing) != signature:
            continue
        if existing.get("_deferred") and not edit.get("_deferred"):
            values[index] = deepcopy(edit)
        spec.meta["recording_agent_ops"] = values[-500:]
        return
    values.append(deepcopy(edit))
    spec.meta["recording_agent_ops"] = values[-500:]


def _trusted_verification(spec, verification_id: str, kinds: set[str]) -> dict:  # noqa: ANN001
    from dano.execution.page.verification_log import find_verification

    record = find_verification(
        verification_id,
        list((spec.meta or {}).get("verification_log") or []),
    )
    if record is None or str(record.get("kind") or "") not in kinds:
        raise ValueError("verification_id is missing or has the wrong kind")
    if record.get("status") != "passed":
        raise ValueError("verification evidence status is not passed")
    return record


def dependency_link_signature(link) -> str:  # noqa: ANN001
    """Stable signature covered by an executor-owned dependency verification."""
    meta = dict(getattr(link, "meta", None) or {})
    link_kind = str(getattr(link, "kind", "") or "value")
    payload = {
        "kind": link_kind,
        "source_step_id": str(getattr(link, "source_step_id", "") or ""),
        "source_path": str(getattr(link, "source_path", "") or "").removeprefix("response."),
        "target_step_id": str(getattr(link, "target_step_id", "") or ""),
        "target_path": str(getattr(link, "target_path", "") or "").removeprefix("request."),
        "source_collection_path": str(getattr(link, "source_collection_path", "") or meta.get("source_collection_path") or ""),
        "source_key_path": str(getattr(link, "source_key_path", "") or meta.get("source_key_path") or ""),
        "source_label_path": str(getattr(link, "source_label_path", "") or meta.get("source_label_path") or ""),
        "target_container_path": str(getattr(link, "target_container_path", "") or meta.get("target_container_path") or ""),
        "value_binding": getattr(link, "value_binding", None) or meta.get("value_binding") or {},
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def invalidate_dependency_verification(link, reason: str) -> None:  # noqa: ANN001
    """Invalidate executor evidence whenever the dependency identity changes."""
    link.confirmed = False
    meta = dict(getattr(link, "meta", None) or {})
    meta.pop("verification_id", None)
    meta["verified"] = False
    meta["unverified_reason"] = str(reason or "依赖定义已变化，需要重新验证")
    link.meta = meta
    evidence = dict(getattr(link, "evidence", None) or {})
    evidence.pop("verification_id", None)
    link.evidence = evidence


def _step_request_id(spec, step_id: str) -> str:  # noqa: ANN001
    step = next((item for item in spec.steps if item.step_id == step_id), None)
    return str(((step.source_meta if step else {}) or {}).get("request_id") or "")


class _DeferredCompile(Exception):
    """Target/origin facts exist but their steps are not materialized yet."""


def _compile_param_source(spec, step, param, edit: dict, *, source_kind: str, reason: str) -> None:  # noqa: ANN001
    """Compile one agent source classification into executable field state.

    Every accepted classification must leave the param in a state the request
    builder can execute; anything that cannot compile is rejected with a
    message that tells the model how to reclassify (hypothesis → verification,
    never a silent tag).
    """
    from dano.execution.page.flow_spec import FlowLink, _field_source_configuration_advice

    origin_request_id = str(edit.get("origin_request_id") or "")
    origin_path = str(edit.get("origin_path") or "").removeprefix("response.")
    # Source and requiredness are independent field axes.  Reclassifying the
    # source used to replace the whole source object and silently erase the DOM
    # required marker captured moments earlier.
    required_state = str((param.source or {}).get("required_state") or "")

    if source_kind == "caller_input":
        param.source_kind = "user_input"
        param.source = {"kind": "user_input", "reason": reason, "actor": "agent"}
        param.category = "user_param"
        param.exposed_to_user = True

    elif source_kind == "constant":
        if param.value in (None, "") and param.default_value in (None, ""):
            raise ValueError(
                f"constant classification for {param.path} requires a recorded value; "
                "use user_input when the caller must supply it"
            )
        param.source_kind = "constant"
        param.source = {"kind": "constant", "path": param.path, "reason": reason, "actor": "agent"}
        param.category = "system_const"
        param.exposed_to_user = False

    elif source_kind == "session":
        session_key = str(edit.get("session_key") or "").strip()
        if param.path.startswith("headers."):
            param.source_kind = "request_header"
            param.source = {
                "kind": "request_header",
                "header": param.path.split(".", 1)[1],
                "reason": reason,
                "actor": "agent",
            }
        else:
            if not session_key:
                raise ValueError(
                    f"session classification for {param.path} requires session_key "
                    "unless the target is a headers.* field"
                )
            param.source_kind = "current_user"
            param.source = {
                "kind": "identity",
                "path": session_key,
                "reason": reason,
                "actor": "agent",
            }
        param.category = "runtime_var"
        param.exposed_to_user = False
        param.default_value = None

    elif source_kind == "context":
        context_key = str(edit.get("context_key") or "").strip()
        if not context_key:
            raise ValueError(f"context classification for {param.path} requires context_key")
        pagination = bool(re.search(
            r"(?:^|[._-])(page(?:no|num|number|size)?|current|limit|offset)(?:$|[._-])",
            f"{param.path}.{param.key}",
            re.I,
        ))
        param.source_kind = "page_context"
        param.source = {
            "kind": "page_context",
            "context_key": context_key,
            "path": param.path,
            "default_value": param.value if param.value not in (None, "") else param.default_value,
            "caller_override": pagination,
            "reason": reason,
            "actor": "agent",
        }
        param.category = "user_param" if pagination else "runtime_var"
        param.exposed_to_user = pagination
        param.editable = pagination
        if pagination:
            param.required = False
            required_state = "optional"
            if param.default_value in (None, ""):
                param.default_value = param.value

    elif source_kind == "response_binding":
        if not origin_request_id or not origin_path:
            raise ValueError(
                f"response_binding classification for {param.path} requires "
                "origin_request_id and origin_path"
            )
        source_step = _request_step(spec, origin_request_id)
        if source_step is None:
            if _known_request_id(spec, origin_request_id):
                raise _DeferredCompile(
                    "response_binding origin request is captured but not materialized as a step yet"
                )
            raise ValueError(
                f"response_binding origin request {origin_request_id} is not part of the recorded facts"
            )
        captured_match = _captured_value_match(
            spec,
            source_request_id=origin_request_id,
            source_path=origin_path,
            target_step=step,
            target_path=param.path,
        )
        signature = (source_step.step_id, origin_path, step.step_id, param.path)
        link = next((
            item for item in spec.links
            if (
                item.source_step_id,
                str(item.source_path or "").removeprefix("response."),
                item.target_step_id,
                str(item.target_path or "").removeprefix("request."),
            ) == signature
        ), None)
        if link is None:
            link = FlowLink(
                source_step_id=source_step.step_id,
                source_path=origin_path,
                target_step_id=step.step_id,
                target_path=param.path,
            )
            link.confirmed = False
            link.confidence = max(0.75, float(edit.get("confidence") or 0))
            link.reason = reason
            link.evidence = {
                "actor": "agent",
                "source_request_id": origin_request_id,
                "target_request_id": str((step.source_meta or {}).get("request_id") or ""),
                **({"captured_value_match": captured_match} if captured_match else {}),
            }
            link.meta = {
                "verified": False,
                "actor": "agent",
                **({"captured_value_match": True} if captured_match else {}),
            }
            spec.links.append(link)
        elif captured_match:
            link.evidence = {
                **(link.evidence or {}),
                "captured_value_match": captured_match,
            }
            link.meta = {
                **(link.meta or {}),
                "captured_value_match": True,
            }
        param.source_kind = "previous_response"
        param.source = {
            "kind": "previous_response",
            "step_id": source_step.step_id,
            "response_path": origin_path,
            "link_id": link.link_id,
            "origin_request_id": origin_request_id,
            "reason": reason,
            "actor": "agent",
        }
        param.category = "runtime_var"
        param.exposed_to_user = False

    elif source_kind == "generated":
        raw_source = edit.get("source") if isinstance(edit.get("source"), dict) else {}
        strategy = str(edit.get("strategy") or raw_source.get("strategy") or "").strip()
        generated_strategies = {"uuid", "random_string", "random_number"}
        time_strategies = {"now_ms", "now_s", "now_iso", "now_date"}
        if strategy not in generated_strategies | time_strategies:
            raise ValueError(
                f"generated classification for {param.path} requires strategy in "
                f"{sorted(generated_strategies | time_strategies)}"
            )
        sample = param.value if param.value not in (None, "") else param.default_value
        sample_text = str(sample or "").strip()
        compatible = {
            "uuid": bool(re.fullmatch(
                r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
                r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}",
                sample_text,
            )),
            "random_string": isinstance(sample, str) and bool(sample_text),
            "random_number": isinstance(sample, (int, float)) and not isinstance(sample, bool),
            "now_ms": bool(re.fullmatch(r"\d{13}", sample_text)),
            "now_s": bool(re.fullmatch(r"\d{10}", sample_text)),
            "now_iso": bool(re.fullmatch(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?",
                sample_text,
            )),
            "now_date": bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", sample_text)),
        }[strategy]
        if not compatible:
            raise ValueError(
                f"generated classification for {param.path} strategy={strategy} "
                "contradicts the recorded sample"
            )
        param.source_kind = "system_time" if strategy in time_strategies else "system_generated"
        param.source = {
            "kind": param.source_kind,
            "strategy": strategy,
            "reason": reason,
            "actor": "agent",
            "sample_verified": True,
        }
        param.category = "runtime_var"
        param.exposed_to_user = False

    elif source_kind == "computed":
        raw_source = edit.get("source") if isinstance(edit.get("source"), dict) else {}
        strategy = str(edit.get("strategy") or raw_source.get("strategy") or "")
        start_field = str(edit.get("start_field") or raw_source.get("start_field") or "")
        end_field = str(edit.get("end_field") or raw_source.get("end_field") or "")
        output_key = str(edit.get("output_key") or raw_source.get("output_key") or "")
        if strategy != "date_span_days_json" or not start_field or not end_field:
            raise ValueError(
                f"computed classification for {param.path} requires strategy=date_span_days_json "
                "with start_field and end_field naming the user params it derives from"
            )
        def matching_public_field(name: str):  # noqa: ANN202
            matches = []
            for item_step in spec.steps:
                for item_param in item_step.params:
                    aliases = {
                        str(value).strip()
                        for value in (
                            item_param.key,
                            item_param.label,
                            item_param.path,
                            canonical_wire_path(item_step, item_param.path),
                            str(item_param.path or "").split(".")[-1],
                        )
                        if str(value or "").strip()
                    }
                    if name in aliases:
                        matches.append(item_param)
            return matches

        resolved_inputs = []
        for field in (start_field, end_field):
            matches = matching_public_field(field)
            if len(matches) != 1:
                raise ValueError(
                    f"computed classification for {param.path} requires one unambiguous public input "
                    f"for {field!r}; matched={len(matches)}"
                )
            caller_param = matches[0]
            if not (
                caller_param.category == "user_param"
                and caller_param.exposed_to_user
                and caller_param.source_kind == "user_input"
                and (caller_param.source or {}).get("actor") in {"agent", "user"}
            ):
                raise ValueError(
                    f"computed classification for {param.path} requires confirmed public input {field!r}"
                )
            resolved_inputs.append(caller_param)
        try:
            recorded_days = date_span_days(resolved_inputs[0].value, resolved_inputs[1].value)
        except ValueError as exc:
            raise ValueError(
                f"computed classification for {param.path} has invalid recorded date inputs"
            ) from exc
        try:
            recorded_payload = json.loads(str(param.value or param.default_value or ""))
        except (TypeError, ValueError):
            recorded_payload = None
        if not isinstance(recorded_payload, dict) or len(recorded_payload) != 1:
            raise ValueError(
                f"computed classification for {param.path} requires a recorded one-key JSON sample"
            )
        recorded_key, recorded_value = next(iter(recorded_payload.items()))
        output_key = output_key or str(recorded_key)
        if output_key != str(recorded_key) or recorded_value != recorded_days:
            raise ValueError(
                f"computed classification for {param.path} contradicts the recorded sample: "
                f"computed={{{output_key!r}: {recorded_days!r}}} recorded={recorded_payload!r}"
            )
        param.source_kind = "computed"
        param.source = {
            "kind": "computed",
            "strategy": strategy,
            "start_field": start_field,
            "end_field": end_field,
            "output_key": output_key or "days",
            "reason": reason,
            "actor": "agent",
            "sample_verified": True,
            "sample_days": recorded_days,
        }
        param.category = "runtime_var"
        param.exposed_to_user = False

    if required_state:
        param.source = {**(param.source or {}), "required_state": required_state}

    advice = _field_source_configuration_advice(param)
    if advice:
        raise ValueError(f"source classification does not compile: {advice}")


def apply_recording_agent_edit(spec, edit: dict, *, record: bool = True) -> dict:  # noqa: ANN001
    """Apply one live-only op; unresolved early endpoints remain replayable at finalize."""
    from dano.execution.page.flow_spec import FlowLink, ParamField, RecordedGoal, RequestAnalysis

    kind = str(edit.get("op") or "")
    if kind not in LIVE_RECORDING_AGENT_OPS:
        raise ValueError(f"unsupported live recording op: {kind}")
    if str(edit.get("actor") or "agent") not in {"agent", "planner", "repair"}:
        raise ValueError("live recording ops must be agent-authored")
    edit = deepcopy(edit)
    if kind == "set_request_role":
        request_id = str(edit.get("request_id") or "")
        edit["role"] = _canonical_request_role(spec, request_id, str(edit.get("role") or ""))
    stored = _canonical_recorded_agent_op(spec, edit)
    if record and any(
        isinstance(existing, dict)
        and not existing.get("_deferred")
        and {key: value for key, value in existing.items() if key != "_deferred"} == stored
        for existing in (spec.meta or {}).get("recording_agent_ops") or []
    ):
        # Pi may repeat a conclusion after compaction or after reading a fresh
        # flow version.  The requested state is already authoritative, so this
        # is an idempotent success rather than a validation failure.
        return {"status": "applied", "reason": "operation already applied"}

    result = {"status": "applied"}

    if kind == "set_goal":
        raw_goal = dict(edit.get("goal") or {})
        raw_evidence = raw_goal.get("evidence")
        if isinstance(raw_evidence, str):
            raw_evidence = [raw_evidence]
        if isinstance(raw_evidence, list):
            normalized_evidence: list[dict] = []
            for item in raw_evidence:
                if isinstance(item, dict):
                    normalized_evidence.append(dict(item))
                    continue
                text = str(item or "").strip()
                if not text:
                    continue
                source, separator, detail = text.partition(":")
                normalized_evidence.append({
                    "source": source.strip() if separator and source.strip() else "agent",
                    "ref": detail.strip() if separator and detail.strip() else text,
                })
            raw_goal["evidence"] = normalized_evidence
        goal = RecordedGoal.model_validate(raw_goal)
        if not goal.intent.strip():
            raise ValueError("set_goal requires goal.intent")
        if not goal.evidence:
            raise ValueError("set_goal requires goal.evidence")
        # Merge, never wipe: the model states intent/evidence, while axes it
        # leaves empty (forbidden_actions, success_criteria, …) keep their
        # existing values so the publish completeness gate stays satisfied.
        merged_goal = dict(spec.goal or {})
        for key, value in goal.model_dump(mode="json").items():
            if value not in (None, "", [], {}):
                merged_goal[key] = value
        spec.goal = merged_goal
        _append_insight(spec, kind="goal", text=f"目标：{goal.intent}", refs=["goal_text"])

    elif kind == "set_request_role":
        request_id = str(edit.get("request_id") or "")
        public_role = str(edit.get("role") or "")
        role = _INTERNAL_REQUEST_ROLE.get(public_role, "")
        reason = str(edit.get("reason") or "").strip()
        raw_refs = edit.get("evidence_refs")
        if raw_refs in (None, "", []):
            raw_refs = edit.get("evidence")
        if isinstance(raw_refs, (str, dict)):
            raw_refs = [raw_refs]
        evidence_refs = [
            (
                str(value.get("ref") or value.get("source") or json.dumps(value, ensure_ascii=False))
                if isinstance(value, dict)
                else str(value)
            )
            for value in raw_refs or []
            if value not in (None, "", {})
        ]
        if not request_id or not public_role or not role or not reason or not evidence_refs:
            raise ValueError("set_request_role requires request_id, role, reason and evidence_refs")
        current = (spec.request_facts.analysis or {}).get(request_id)
        analysis = current.model_copy(deep=True) if current is not None else RequestAnalysis(request_id=request_id)
        analysis.role = role
        analysis.reason = reason
        analysis.keep = role not in {"noise", "auth", "telemetry"}
        analysis.confidence = max(float(analysis.confidence or 0), float(edit.get("confidence") or 0.8))
        analysis.evidence = {
            **(analysis.evidence or {}),
            "actor": "agent",
            "reason": reason,
            "evidence_refs": evidence_refs,
        }
        spec.request_facts.analysis[request_id] = analysis
        step = _request_step(spec, request_id)
        if step is not None:
            step.semantic_role = role
            step.source_meta = {**(step.source_meta or {}), "role": role, "actor": "agent", "evidence_refs": evidence_refs}
        _append_insight(
            spec,
            kind="role",
            text=f"{request_id} 判定为 {public_role}：{reason}",
            refs=[request_id, *evidence_refs],
        )

    elif kind == "set_param_source":
        step_id = str(edit.get("step_id") or edit.get("request_id") or "")
        path = str(edit.get("path") or edit.get("wire_path") or "")
        source_kind = str(edit.get("source_kind") or "")
        reason = str(edit.get("reason") or "").strip()
        if not step_id or not path or not reason:
            raise ValueError("set_param_source requires step_id, path, source_kind and reason")
        if source_kind not in _PARAM_SOURCE_KINDS:
            raise ValueError(
                "set_param_source source_kind must be one of "
                f"{sorted(_PARAM_SOURCE_KINDS)}; got {source_kind!r}"
            )
        step, param = _field_target(spec, step_id, path)
        if param is not None:
            if param.locked:
                raise ValueError(f"set_param_source target is locked: {step_id}:{path}")
            try:
                _compile_param_source(spec, step, param, edit, source_kind=source_kind, reason=reason)
            except _DeferredCompile as pending:
                result["status"] = "deferred"
                result["deferred"] = True
                result["reason"] = str(pending)
                if record:
                    _record_agent_op(spec, stored)
                return result
            param.reason = reason
            evidence_refs = _evidence_refs(edit)
            _ground_param_in_cited_page_controls(
                spec,
                step,
                param,
                evidence_refs,
                source_kind=source_kind,
            )
            param.evidence = [
                *list(param.evidence or []),
                {
                    "actor": "agent",
                    "kind": "param_source",
                    "source_kind": source_kind,
                    "origin_request_id": str(edit.get("origin_request_id") or ""),
                    "origin_path": str(edit.get("origin_path") or ""),
                    "reason": reason,
                    "evidence_refs": evidence_refs,
                },
            ]
        else:
            result["status"] = "deferred"
            result["deferred"] = True
            result["reason"] = "request is captured but its canonical step is not materialized yet"
        if param is not None:
            _append_insight(spec, kind="param_source", text=f"{step_id}:{path} 来源为 {source_kind}：{reason}", refs=[step_id, path])

    elif kind == "set_param_type":
        step_id = str(edit.get("step_id") or edit.get("request_id") or "")
        path = str(edit.get("path") or edit.get("wire_path") or "")
        business_type = str(edit.get("business_type") or "").strip().lower()
        reason = str(edit.get("reason") or "").strip()
        evidence_refs = _evidence_refs(edit)
        if (
            not step_id or not path or business_type not in _PARAM_BUSINESS_TYPES
            or not reason or not evidence_refs
        ):
            raise ValueError(
                "set_param_type requires step_id, path, supported business_type, reason and evidence_refs"
            )
        _require_grounded_refs(spec, "set_param_type", evidence_refs)
        step, param = _field_target(spec, step_id, path)
        if param is not None:
            if param.locked:
                raise ValueError(f"set_param_type target is locked: {step_id}:{path}")
            _require_type_grounding(
                spec, step, param, business_type, evidence_refs=evidence_refs,
            )
            param.type = business_type
            if business_type not in {"enum", "list-enum"}:
                param.enum_options = None
                param.enum_value_map = None
                if step is not None:
                    step.selects = [
                        binding
                        for binding in (step.selects or [])
                        if str(binding.path or binding.param or "")
                        not in {str(param.path or ""), canonical_wire_path(step, param.path)}
                    ]
            param.evidence = [
                *list(param.evidence or []),
                {
                    "actor": "agent",
                    "kind": "param_type",
                    "business_type": business_type,
                    "reason": reason,
                    "evidence_refs": evidence_refs,
                },
            ]
        else:
            result["status"] = "deferred"
            result["deferred"] = True
            result["reason"] = "request is captured but its canonical step is not materialized yet"
        if param is not None:
            _append_insight(
                spec,
                kind="param_type",
                text=f"{step_id}:{path} 业务类型为 {business_type}：{reason}",
                refs=[step_id, path, *evidence_refs],
            )

    elif kind == "set_param_required":
        step_id = str(edit.get("step_id") or edit.get("request_id") or "")
        path = str(edit.get("path") or edit.get("wire_path") or "")
        required = edit.get("required")
        reason = str(edit.get("reason") or "").strip()
        evidence_refs = _evidence_refs(edit)
        if not step_id or not path or not isinstance(required, bool) or not reason or not evidence_refs:
            raise ValueError(
                "set_param_required requires step_id, path, boolean required, reason and evidence_refs"
            )
        _require_grounded_refs(spec, "set_param_required", evidence_refs)
        step, param = _field_target(spec, step_id, path)
        if param is not None:
            if param.locked:
                raise ValueError(f"set_param_required target is locked: {step_id}:{path}")
            _require_required_grounding(
                spec, step, param, required, evidence_refs=evidence_refs,
            )
            param.required = required
            param.source = {
                **(param.source or {}),
                "required_state": "required" if required else "optional",
            }
            param.evidence = [
                *list(param.evidence or []),
                {
                    "actor": "agent",
                    "kind": "param_required",
                    "required": required,
                    "reason": reason,
                    "evidence_refs": evidence_refs,
                },
            ]
        else:
            result["status"] = "deferred"
            result["deferred"] = True
            result["reason"] = "request is captured but its canonical step is not materialized yet"
        if param is not None:
            _append_insight(
                spec,
                kind="param_required",
                text=f"{step_id}:{path} 必填性为 {required}：{reason}",
                refs=[step_id, path, *evidence_refs],
            )

    elif kind == "set_param_enum":
        step_id = str(edit.get("step_id") or edit.get("request_id") or "")
        path = str(edit.get("path") or edit.get("wire_path") or "")
        reason = str(edit.get("reason") or "").strip()
        evidence_refs = _evidence_refs(edit)
        options = edit.get("options")
        dictionary_source = str(edit.get("dictionary_source") or "").strip()
        if (
            not step_id or not path or not reason or not evidence_refs
            or not isinstance(options, list) or not options
        ):
            raise ValueError(
                "set_param_enum requires step_id, path, non-empty options, reason and evidence_refs"
            )
        _require_grounded_refs(spec, "set_param_enum", evidence_refs)
        step, param = _field_target(spec, step_id, path)
        if param is not None:
            if param.locked:
                raise ValueError(f"set_param_enum target is locked: {step_id}:{path}")
            recorded = _recorded_enum_contract(
                spec, step, param, dictionary_source=dictionary_source,
            )
            if recorded is None:
                raise ValueError(
                    f"enum conclusion for {param.path} has no matching select field_evidence and dictionary source"
                )
            recorded_source = str(recorded.get("dict_type") or recorded.get("dictionary_source") or "")
            if dictionary_source and recorded_source and dictionary_source != recorded_source:
                raise ValueError(
                    f"dictionary_source {dictionary_source!r} contradicts recorded dictionary {recorded_source!r}"
                )
            submitted_pairs = {_enum_pair(option, param.wire_type) for option in options}
            recorded_pairs = {
                _enum_pair(option, param.wire_type)
                for option in (recorded.get("options") or [])
            }
            if None in submitted_pairs or None in recorded_pairs or submitted_pairs != recorded_pairs:
                raise ValueError(
                    f"enum options for {param.path} contradicts recorded dictionary label/value mapping"
                )
            grounded_options = [
                {
                    **deepcopy(option),
                    "label": str(option.get("label") or "").strip(),
                    "value": _enum_wire_value(option.get("value"), param.wire_type),
                }
                for option in (recorded.get("options") or [])
            ]
            param.type = "enum"
            param.category = "user_param"
            param.source_kind = "page_enum"
            required_state = str((param.source or {}).get("required_state") or "")
            param.source = {
                "kind": "page_enum",
                "dictionary_source": recorded_source or dictionary_source,
                "enum_confirmed": True,
                "actor": "agent",
                **({"required_state": required_state} if required_state else {}),
            }
            param.exposed_to_user = True
            param.editable = True
            param.enum_options = grounded_options
            param.enum_value_map = {
                pair[0]: option.get("value")
                for option in grounded_options
                if (pair := _enum_pair(option, param.wire_type)) is not None
            }
            param.evidence = [
                *list(param.evidence or []),
                {
                    "actor": "agent",
                    "kind": "enum_options",
                    "dictionary_source": recorded_source or dictionary_source,
                    "reason": reason,
                    "evidence_refs": evidence_refs,
                },
            ]
        else:
            result["status"] = "deferred"
            result["deferred"] = True
            result["reason"] = "request is captured but its canonical step is not materialized yet"
        if param is not None:
            _append_insight(
                spec,
                kind="enum_options",
                text=f"{step_id}:{path} 枚举已按录制字典绑定：{reason}",
                refs=[step_id, path, *evidence_refs],
            )

    elif kind == "rename_field":
        step_id = str(edit.get("step_id") or edit.get("request_id") or "")
        path = str(edit.get("path") or edit.get("wire_path") or "")
        label = str(edit.get("label") or edit.get("public_name") or edit.get("name") or "").strip()
        reason = str(edit.get("reason") or "").strip()
        evidence_refs = _evidence_refs(edit)
        if not step_id or not path or not label or not reason or not evidence_refs:
            raise ValueError(
                "rename_field requires step_id, path, label, reason and evidence_refs"
            )
        _require_grounded_refs(spec, "rename_field", evidence_refs)
        step, param = _field_target(spec, step_id, path)
        if param is not None:
            if param.locked:
                raise ValueError(f"rename_field target is locked: {step_id}:{path}")
            _require_label_grounding(
                spec, step, param, label, evidence_refs=evidence_refs,
            )
            param.key = label
            param.label = label
            param.name_source = "agent"
            param.evidence = [
                *list(param.evidence or []),
                {
                    "actor": "agent",
                    "kind": "field_name",
                    "label": label,
                    "reason": reason,
                    "evidence_refs": evidence_refs,
                },
            ]
        else:
            result["status"] = "deferred"
            result["deferred"] = True
            result["reason"] = "request is captured but its canonical step is not materialized yet"
        if param is not None:
            _append_insight(
                spec,
                kind="field_name",
                text=f"{step_id}:{path} 业务名称为 {label}：{reason}",
                refs=[step_id, path, *evidence_refs],
            )

    elif kind == "propose_dependency":
        requested_link_id = str(edit.get("link_id") or "")
        source_request_id = str(edit.get("source_request_id") or "")
        target_request_id = str(edit.get("target_request_id") or "")
        target_step_id = str(edit.get("target_step_id") or edit.get("step_id") or "")
        source_path = str(edit.get("source_path") or "").removeprefix("response.")
        target_path = str(edit.get("target_path") or "").removeprefix("request.")
        link_kind = str(edit.get("kind") or edit.get("link_kind") or "value")
        evidence = edit.get("evidence")
        if link_kind not in {"value", "structure", "response_key_map"}:
            raise ValueError("propose_dependency kind must be value, structure or response_key_map")
        source_collection_path = str(edit.get("source_collection_path") or "").removeprefix("response.")
        source_key_path = str(edit.get("source_key_path") or "")
        source_label_path = str(edit.get("source_label_path") or "")
        target_container_path = str(edit.get("target_container_path") or "").removeprefix("request.")
        value_binding = dict(edit.get("value_binding") or {})
        if link_kind == "response_key_map":
            source_path = source_collection_path
            target_path = target_container_path
        if not source_request_id or not source_path or not (target_request_id or target_step_id) or not target_path:
            raise ValueError("propose_dependency requires source and target request/step paths")
        if not isinstance(evidence, dict) or not evidence:
            raise ValueError("propose_dependency requires evidence")
        source_step = _request_step(spec, source_request_id)
        target_step = next((step for step in spec.steps if step.step_id == target_step_id), None) or _request_step(spec, target_request_id)
        dynamic_contract: dict = {}
        if link_kind == "response_key_map":
            if not source_key_path or not source_label_path:
                raise ValueError("response_key_map requires source_collection_path, source_key_path and source_label_path")
            if value_binding.get("kind") != "caller_map_by_label" or not str(value_binding.get("input_field") or ""):
                raise ValueError("response_key_map requires value_binding.kind=caller_map_by_label and input_field")
        if link_kind in {"structure", "response_key_map"} and target_step is not None:
            target_path = stored_container_path(target_step, target_path)
            container = target_path.removesuffix(".*").removesuffix("[*]")
            if not any(
                str(item.path or "").startswith(container)
                for item in target_step.params
            ):
                raise ValueError(
                    f"structure dependency target {target_path} does not match any recorded param "
                    f"on step {target_step.step_id}"
                )
            if source_step is not None and link_kind == "structure":
                source_values = [
                    str(value)
                    for value in _response_values_at_path(source_step.response_json, source_path)
                    if value not in (None, "")
                ]
                target_keys = _structure_target_keys(target_step, target_path)
                if not source_values:
                    raise ValueError(
                        f"structure dependency source {source_path} is absent from the recorded response"
                    )
                if not target_keys or source_values != target_keys:
                    raise ValueError(
                        "structure dependency contradicts recorded request keys: "
                        f"response={source_values!r}, request={target_keys!r}"
                    )
            if source_step is not None and link_kind == "response_key_map":
                collection_values = _response_values_at_path(source_step.response_json, source_collection_path)
                collection = collection_values[0] if len(collection_values) == 1 else None
                if not isinstance(collection, list) or not collection:
                    raise ValueError("response_key_map source_collection_path is absent or not a non-empty list")
                source_keys = []
                source_labels = []
                for row in collection:
                    keys = _response_values_at_path(row, source_key_path)
                    labels = _response_values_at_path(row, source_label_path)
                    if len(keys) != 1 or len(labels) != 1 or keys[0] in (None, "") or labels[0] in (None, ""):
                        raise ValueError("response_key_map source rows must each contain one id and one name")
                    source_keys.append(str(keys[0]))
                    source_labels.append(str(labels[0]))
                if len(set(source_keys)) != len(source_keys) or len(set(source_labels)) != len(source_labels):
                    raise ValueError("response_key_map source ids and labels must be unique")
                try:
                    recorded_body = (
                        json.loads(target_step.body_source)
                        if isinstance(target_step.body_source, str)
                        else deepcopy(target_step.body_source)
                    )
                except (TypeError, ValueError):
                    recorded_body = None
                containers = _response_values_at_path(recorded_body, target_path.removeprefix("body."))
                recorded_container = containers[0] if len(containers) == 1 else None
                if not isinstance(recorded_container, dict):
                    raise ValueError("response_key_map target_container_path is absent from the recorded body")
                recorded_keys = list(map(str, recorded_container.keys()))
                matched_positions = [
                    source_keys.index(key) for key in recorded_keys if key in source_keys
                ]
                if (
                    len(matched_positions) != len(recorded_keys)
                    or matched_positions != sorted(matched_positions)
                ):
                    raise ValueError(
                        "response_key_map contradicts recorded request keys: "
                        f"response={source_keys!r}, request={recorded_keys!r}"
                    )
                matched_labels = [source_labels[index] for index in matched_positions]
                recorded_values = list(recorded_container.values())
                if all(isinstance(value, list) for value in recorded_values):
                    # A selector may allow one or many values.  Keep the wire
                    # arrays intact so the public contract does not collapse a
                    # multi-select field into a scalar merely because this
                    # recording happened to choose one item.
                    value_shape = "item_list"
                    public_values = deepcopy(recorded_values)
                elif all(not isinstance(value, (dict, list)) for value in recorded_values):
                    value_shape = "direct"
                    public_values = recorded_values
                else:
                    raise ValueError("response_key_map recorded target values have an unsupported mixed shape")
                input_field = str(value_binding["input_field"])
                option_source = value_binding.get("option_source")
                if option_source is not None and not isinstance(option_source, dict):
                    raise ValueError("response_key_map value_binding.option_source must be an object")
                value_binding = {
                    **value_binding,
                    "value_shape": value_shape,
                    "required_labels": matched_labels,
                    "ignored_labels": [
                        label for label in source_labels if label not in set(matched_labels)
                    ],
                }
                public_sample = dict(zip(matched_labels, public_values, strict=True))
                container_prefix = target_path.removeprefix("body.") + "."
                dynamic_leaf_paths: set[str] = set()
                for item_param in target_step.params:
                    if str(item_param.path or "").removeprefix("body.").startswith(container_prefix):
                        dynamic_leaf_paths.add(str(item_param.path or ""))
                        item_param.category = "runtime_var"
                        item_param.source_kind = "dynamic_structure"
                        item_param.source = {"kind": "dynamic_structure_leaf", "actor": "agent"}
                        item_param.exposed_to_user = False
                        item_param.editable = False
                        item_param.required = False
                        item_param.need_human_confirm = False
                # Per-node selectors belong to the recorded BPMN version. If
                # retained, model sync promotes Activity_* leaves back to
                # caller-facing option fields and defeats the dynamic map.
                target_step.selects = [
                    binding for binding in (target_step.selects or [])
                    if str(binding.path or binding.id_path or "") not in dynamic_leaf_paths
                ]
                public_param = next((
                    item for item in target_step.params
                    if str(item.path or "").removeprefix("body.") == target_path.removeprefix("body.")
                ), None)
                if public_param is None:
                    public_param = ParamField(path=target_path, key=input_field)
                    target_step.params.append(public_param)
                public_param.key = input_field
                public_param.label = public_param.label or "审批人"
                public_param.value = public_sample
                public_param.type = "object"
                public_param.wire_type = "object"
                public_param.required = True
                public_param.category = "user_param"
                public_param.source_kind = "user_input"
                public_param.source = {
                    "kind": "dynamic_structure_input",
                    "actor": "agent",
                    "required_state": "required",
                    **({"option_source": deepcopy(option_source)} if option_source else {}),
                }
                public_param.exposed_to_user = True
                public_param.need_human_confirm = False
                public_param.reason = "调用方按最新审批节点名称提供人员，运行期按最新节点 ID 组装请求"
                public_param.evidence.append({
                    "source": "response_key_map",
                    "request_id": target_request_id,
                    "wire_path": f"body.{target_path.removeprefix('body.')}",
                    "labels": source_labels,
                    "actor": "agent",
                    "reason": "上游返回稳定业务标签和运行期键，页面选择值按标签映射后组装动态容器",
                })
                # The public contract and the canonical sample must describe
                # the same stable label map.  Keeping recorded Activity_* keys
                # in sample_inputs makes dry-run validate an obsolete BPMN
                # version even though the public parameter is already correct.
                target_step.sample_inputs[input_field] = deepcopy(public_sample)
                for stale_param in target_step.params:
                    if (
                        stale_param is not public_param
                        and str(stale_param.path or "") in dynamic_leaf_paths
                    ):
                        target_step.sample_inputs.pop(str(stale_param.key or stale_param.path), None)
                dynamic_contract = {
                    "source_collection_path": source_collection_path,
                    "source_key_path": source_key_path,
                    "source_label_path": source_label_path,
                    "target_container_path": target_path,
                    "value_binding": value_binding,
                }
        if source_step is not None and target_step is not None:
            captured_value_match = (
                _captured_value_match(
                    spec,
                    source_request_id=source_request_id,
                    source_path=source_path,
                    target_step=target_step,
                    target_path=target_path,
                )
                if link_kind == "value"
                else None
            )
            signature = (source_step.step_id, source_path, target_step.step_id, target_path)
            existing = next((
                link for link in spec.links
                if (
                    link.source_step_id,
                    str(link.source_path or "").removeprefix("response."),
                    link.target_step_id,
                    str(link.target_path or "").removeprefix("request."),
                ) == signature
            ), None)
            id_collision = next((link for link in spec.links if requested_link_id and link.link_id == requested_link_id), None)
            if id_collision is not None and id_collision is not existing:
                raise ValueError("propose_dependency link_id already belongs to another dependency")
            link = existing or FlowLink(
                **({"link_id": requested_link_id} if requested_link_id else {}),
                source_step_id=source_step.step_id,
                source_path=source_path,
                target_step_id=target_step.step_id,
                target_path=target_path,
                kind=link_kind,
                **dynamic_contract,
            )
            invalidate_dependency_verification(link, "依赖提案已更新，需要重新执行 dependency_execute 验证")
            link.kind = link_kind
            for field, value in dynamic_contract.items():
                setattr(link, field, deepcopy(value))
            link.confidence = max(float(link.confidence or 0), float(edit.get("confidence") or 0.75))
            link.reason = str(edit.get("reason") or "agent 提出的待验证依赖")
            link.evidence = {
                **(link.evidence or {}),
                **evidence,
                "actor": "agent",
                "source_request_id": source_request_id,
                "target_request_id": target_request_id,
                **({"captured_value_match": captured_value_match} if captured_value_match else {}),
            }
            captured_structure_match = link_kind in {"structure", "response_key_map"}
            link.meta = {
                **(link.meta or {}),
                "actor": "agent",
                **({"captured_value_match": True} if captured_value_match else {}),
                **({"captured_structure_match": True} if captured_structure_match else {}),
            }
            if link_kind in {"structure", "response_key_map"}:
                link.meta.pop("kind", None)
            if existing is None:
                spec.links.append(link)
        _append_insight(
            spec,
            kind="link",
            text=str(edit.get("reason") or f"发现待验证依赖 {source_request_id}:{source_path} → {target_request_id or target_step_id}:{target_path}"),
            refs=[source_request_id, target_request_id or target_step_id, source_path, target_path],
        )

    elif kind == "add_pitfall":
        text = str(edit.get("text") or "").strip()
        if not text:
            raise ValueError("add_pitfall requires text")
        _append_meta_list(spec, "pitfalls", {"text": text, "evidence_ref": str(edit.get("evidence_ref") or ""), "actor": "agent"})

    elif kind == "confirm_dependency":
        link_id = str(edit.get("link_id") or "")
        verification_id = str(edit.get("verification_id") or "")
        link = next((item for item in spec.links if item.link_id == link_id), None)
        if link is None:
            raise ValueError("confirm_dependency target link does not exist")
        verification_record = _trusted_verification(spec, verification_id, {"dependency_execute"})
        subject = verification_record.get("subject") or {}
        if str(subject.get("link_id") or "") != link.link_id:
            raise ValueError("dependency verification subject link_id does not match")
        if str(subject.get("signature") or "") != dependency_link_signature(link):
            raise ValueError("dependency verification signature does not match current link")
        link.confirmed = True
        link.confidence = 1.0
        link_meta = dict(link.meta or {})
        link_meta.pop("unverified_reason", None)
        link.meta = {**link_meta, "verified": True, "actor": "agent", "verification_id": verification_id}
        link.evidence = {**(link.evidence or {}), "actor": "agent", "verification_id": verification_id}
        spec.meta = {
            **(spec.meta or {}),
            "unverified": [
                item for item in (spec.meta or {}).get("unverified") or []
                if not (
                    isinstance(item, dict)
                    and str(item.get("target_id") or "") == link_id
                    and str(item.get("target_kind") or "")
                    in {"dependency", "dependency_candidate"}
                )
            ],
        }
        source_step = next((item for item in spec.steps if item.step_id == link.source_step_id), None)
        if source_step is not None:
            for param in source_step.params:
                if param.source_kind == "computed" and (param.source or {}).get("sample_verified") is True:
                    param.source = {
                        **(param.source or {}),
                        "verified": True,
                        "execution_verification_id": verification_id,
                    }

    elif kind == "bind_verify_read":
        write_step_id = str(edit.get("write_step_id") or "")
        read_request_id = str(edit.get("read_request_id") or "")
        verification_id = str(edit.get("verification_id") or "")
        assertion = edit.get("assertion")
        write_step = next((item for item in spec.steps if item.step_id == write_step_id), None)
        read_fact = next((item for item in spec.request_facts.requests if item.request_id == read_request_id), None)
        if write_step is None or read_fact is None or not isinstance(assertion, dict) or not assertion:
            raise ValueError("bind_verify_read requires existing write/read endpoints and assertion")
        verification_record = _trusted_verification(spec, verification_id, {"write_execute", "verify_read"})
        subject = verification_record.get("subject") or {}
        if str(subject.get("write_step_id") or "") != write_step_id or str(subject.get("verify_request_id") or "") != read_request_id:
            raise ValueError("write verification subject does not match bind_verify_read endpoints")
        if subject.get("assertion") != assertion:
            raise ValueError("bind_verify_read assertion does not match executed assertion")
        write_step.fact_check = {
            "endpoint": read_fact.path or read_fact.url,
            "source_request_id": read_request_id,
            "assertion": deepcopy(assertion),
            "verification_id": verification_id,
            "verified": True,
            "actor": "agent",
        }
        write_step.source_meta = {**(write_step.source_meta or {}), "verify_actor": "agent", "verification_id": verification_id}
        write_request_id = str((write_step.source_meta or {}).get("request_id") or "")
        analysis = (spec.request_facts.analysis or {}).get(write_request_id)
        if analysis is not None:
            analysis.evidence = {
                **(analysis.evidence or {}),
                "actor": "agent",
                "reason": analysis.reason or "真实写入与读回验证通过",
                "verification_id": verification_id,
                "evidence_refs": [
                    *list((analysis.evidence or {}).get("evidence_refs") or []),
                    f"verification:{verification_id}",
                ],
            }

    elif kind == "attach_enum_options":
        from dano.execution.page.flow_spec import _bind_option_source

        step_id = str(edit.get("step_id") or edit.get("request_id") or "")
        path = str(edit.get("path") or edit.get("wire_path") or "")
        source_request_id = str(edit.get("source_request_id") or "")
        verification_id = str(edit.get("verification_id") or "")
        options = edit.get("options")
        if not isinstance(options, list) or not options:
            raise ValueError("attach_enum_options requires non-empty options")
        verification_record = _trusted_verification(spec, verification_id, {"enum_snapshot"})
        snapshot = ((verification_record.get("evidence") or {}).get("snapshot") or {})
        observed_options = [
            option
            for element in snapshot.get("elements") or [] if isinstance(element, dict)
            for option in element.get("options") or []
        ]
        if observed_options and not all(option in observed_options for option in options):
            raise ValueError("enum options are not grounded in the verified snapshot")
        source_fact = next((item for item in spec.request_facts.requests if item.request_id == source_request_id), None)
        if source_fact is None:
            raise ValueError("attach_enum_options source request does not exist")
        step, param = _field_target(spec, step_id, path)
        if step is None or param is None:
            raise ValueError("attach_enum_options target is not materialized")
        stored_path = param.path
        _bind_option_source(
            spec,
            target_step_id=step.step_id,
            target_path=stored_path,
            source_url=source_fact.path or source_fact.url,
            source_request_id=source_request_id,
            options=options,
            actor="agent",
        )
        binding = next(item for item in step.selects if item.path == stored_path or item.id_path == stored_path)
        binding.actor = "agent"
        binding.confidence = 1.0
        binding.verification_id = verification_id
        param.evidence.append({"actor": "agent", "kind": "enum_options", "verification_id": verification_id})

    elif kind == "mark_unverified":
        target_kind = str(edit.get("target_kind") or "")
        target_id = str(edit.get("target_id") or "")
        reason = str(edit.get("reason") or "").strip()
        if target_kind not in {
            "dependency", "dependency_candidate", "write_verify", "enum",
        } or not target_id or not reason:
            raise ValueError("mark_unverified requires target_kind, target_id and reason")
        _append_meta_list(spec, "unverified", {
            "target_kind": target_kind,
            "target_id": target_id,
            "reason": reason,
            "actor": "agent",
        })
        if target_kind == "dependency":
            link = next((item for item in spec.links if item.link_id == target_id), None)
            if link is None:
                raise ValueError("mark_unverified dependency does not exist")
            link.meta = {**(link.meta or {}), "verified": False, "unverified_reason": reason}
        elif target_kind == "write_verify":
            step = next((item for item in spec.steps if item.step_id == target_id), None)
            if step is None:
                raise ValueError("mark_unverified write step does not exist")
            step.source_meta = {**(step.source_meta or {}), "unverified_reason": reason}
        elif target_kind == "enum":
            if ":" not in target_id:
                raise ValueError("mark_unverified enum target must be step_id:path")
            step_id, path = target_id.split(":", 1)
            try:
                step, param = _field_target(spec, step_id, path)
            except ValueError as exc:
                raise ValueError("mark_unverified enum target does not exist") from exc
            if step is None or param is None:
                raise ValueError("mark_unverified enum target does not exist")

    if record:
        recorded = deepcopy(stored)
        recorded.pop("_deferred", None)
        if result.get("status") == "deferred":
            recorded["_deferred"] = True
        _record_agent_op(spec, recorded)
    return result


def _retarget_unique_equivalent_field_operation(spec, operation: dict) -> dict:  # noqa: ANN001
    """Map a transient request instance to one equivalent materialized step.

    Repeated requests on the same endpoint get different request ids.  A live
    field conclusion may target the pre-materialization instance while the
    canonical step is built from the later one.  Retarget only when method,
    canonical path, page/frame scope and exact wire path identify one step.
    """
    if str(operation.get("op") or "") not in {
        "set_param_source", "set_param_type", "set_param_required", "set_param_enum", "rename_field",
        "attach_enum_options",
    }:
        return operation
    request_id = str(operation.get("request_id") or "")
    wire_path = str(operation.get("wire_path") or operation.get("path") or "")
    if not request_id or not wire_path or _request_step(spec, request_id) is not None:
        return operation
    source_fact = next(
        (item for item in spec.request_facts.requests if item.request_id == request_id),
        None,
    )
    if source_fact is None:
        return operation
    source_method = str(source_fact.method or "GET").upper()
    source_path = str(source_fact.path or source_fact.url or "").split("?", 1)[0]
    candidates: list = []
    for step in spec.steps:
        if str(step.method or "GET").upper() != source_method:
            continue
        step_path = str(step.path or step.url or "").split("?", 1)[0]
        if step_path != source_path:
            continue
        candidate_request_id = str((step.source_meta or {}).get("request_id") or "")
        candidate_fact = next(
            (item for item in spec.request_facts.requests if item.request_id == candidate_request_id),
            None,
        )
        if candidate_fact is not None and any(
            left and right and left != right
            for left, right in (
                (str(source_fact.page_id or ""), str(candidate_fact.page_id or "")),
                (str(source_fact.frame_id or ""), str(candidate_fact.frame_id or "")),
            )
        ):
            continue
        try:
            resolve_field_ref(spec, FieldRef(step_id=step.step_id, wire_path=wire_path))
        except ValueError:
            continue
        candidates.append(step)
    if len(candidates) != 1:
        return operation
    step = candidates[0]
    candidate_request_id = str((step.source_meta or {}).get("request_id") or "")
    updated = deepcopy(operation)
    updated["request_id"] = candidate_request_id
    updated["step_id"] = step.step_id
    updated["field_ref"] = {"step_id": step.step_id, "wire_path": wire_path}
    updated.pop("_deferred", None)
    return updated


def _live_capability_kind_hint(name: str) -> str:
    """Read an operation hint from a Pi-authored public capability name."""
    value = str(name or "").casefold()
    hints = (
        ("withdraw", ("withdraw", "revoke", "cancel", "撤回", "撤销")),
        ("delete", ("delete", "remove", "删除")),
        ("reject", ("reject", "驳回")),
        ("approve", ("approve", "approval", "同意", "审批")),
        ("export", ("export", "download", "导出", "下载")),
        ("inspect", ("inspect", "detail", "view", "详情", "查看")),
        ("preview", ("preview", "预览")),
        ("query_status", ("query", "search", "list", "status", "查询", "搜索", "列表")),
        ("update", ("update", "edit", "modify", "更新", "编辑")),
        ("create", ("create", "insert", "add", "新增", "创建")),
        ("save_draft", ("draft", "暂存", "草稿")),
        ("submit", ("submit", "commit", "apply", "提交", "申请")),
    )
    return next((kind for kind, tokens in hints if any(token in value for token in tokens)), "")


def _unique_live_capability_name(value: str, kind: str, used: set[str]) -> str:
    base = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "")).strip("_").lower()
    base = base[:64] or kind
    name = base
    suffix = 2
    while name in used:
        ending = f"_{suffix}"
        name = f"{base[:64 - len(ending)]}{ending}"
        suffix += 1
    used.add(name)
    return name


_GOAL_CAPABILITY_COUNT_RE = re.compile(
    r"(?:预期|预计|需要)?\s*产出(?:的)?\s*能力(?:数量|数)?\s*[:：=]?\s*(\d+)",
    re.IGNORECASE,
)
_GOAL_CAPABILITY_LINE_RE = re.compile(
    r"^\s*(?:能力|capability)\s*(\d+)\s*[:：-]\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def _recording_goal_contract(spec) -> dict:  # noqa: ANN001
    """Parse the operator's editable target into an executable boundary.

    Only an explicit count activates the strong boundary.  This keeps legacy
    free-form targets readable while making the setup template deterministic.
    """
    raw = str((spec.meta or {}).get("recording_goal_text") or "").strip()
    count_match = _GOAL_CAPABILITY_COUNT_RE.search(raw)
    if count_match is None:
        return {}
    expected_count = int(count_match.group(1))
    if expected_count <= 0:
        return {}
    named = sorted(
        (
            (int(match.group(1)), str(match.group(2) or "").strip())
            for match in _GOAL_CAPABILITY_LINE_RE.finditer(raw)
            if str(match.group(2) or "").strip()
        ),
        key=lambda item: item[0],
    )
    capabilities = [
        {"ordinal": ordinal, "name": name}
        for ordinal, name in named[:expected_count]
    ]
    return {
        "source": "recording_goal_text",
        "expected_count": expected_count,
        "capabilities": capabilities,
    }


def _goal_capability_names(spec, contract: dict) -> list[str]:  # noqa: ANN001
    """Return ordered target names, filling only from the accepted live goal."""
    expected_count = int(contract.get("expected_count") or 0)
    names = [
        str(item.get("name") or "").strip()
        for item in (contract.get("capabilities") or [])
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]
    goal = spec.goal if isinstance(spec.goal, dict) else {}
    for value in goal.get("capabilities") or []:
        name = str(value or "").strip()
        if name and name not in names:
            names.append(name)
        if expected_count and len(names) >= expected_count:
            break
    return names[:expected_count] if expected_count else names


def _semantic_plan_from_live_boundaries(spec) -> dict:  # noqa: ANN001
    """Materialize only abilities whose recorded anchor is unambiguous."""
    from dano.execution.page.flow_spec import (
        _capability_operation_kind,
        _public_capability_anchor_step_ids,
    )

    anchor_ids = _public_capability_anchor_step_ids(spec)
    if not anchor_ids:
        return {}
    steps = {step.step_id: step for step in spec.steps}
    goal = spec.goal if isinstance(spec.goal, dict) else {}
    contract = _recording_goal_contract(spec)
    proposed_names = (
        _goal_capability_names(spec, contract)
        if contract else [
            str(value).strip()
            for value in (goal.get("capabilities") or [])
            if str(value).strip()
        ]
    )
    expected_count = int(contract.get("expected_count") or 0)
    target_slots = proposed_names or ([""] * expected_count if expected_count else [])
    remaining_anchors = list(anchor_ids)
    used_names: set[str] = set()
    capabilities: list[dict] = []
    unresolved_items: list[dict] = []
    action_labels = {
        "query_status": "查询", "inspect": "查看", "preview": "预览",
        "export": "导出", "create": "创建", "update": "更新",
        "save_draft": "暂存", "submit": "提交", "approve": "审批",
        "reject": "驳回", "withdraw": "撤回", "delete": "删除",
    }
    business = str(spec.title or "业务").strip() or "业务"
    contract_names = {
        str(item.get("name") or "").strip()
        for item in (contract.get("capabilities") or [])
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    }
    if not target_slots:
        target_slots = [""] * len(remaining_anchors)
    for proposed_name in target_slots:
        if not remaining_anchors:
            break
        hinted_kind = _live_capability_kind_hint(proposed_name)
        matching_positions = [
            position
            for position, anchor_id in enumerate(remaining_anchors)
            if _capability_operation_kind(steps[anchor_id]) == hinted_kind
        ] if hinted_kind else []
        if len(matching_positions) == 1:
            match_position = matching_positions[0]
        elif not proposed_name:
            # With no operator-owned slot name, every public anchor already is
            # the deterministic boundary.  Preserve its recorded order.
            match_position = 0
        elif not hinted_kind and len(remaining_anchors) == 1:
            match_position = 0
        else:
            unresolved_items.append({
                "kind": "capability_anchor",
                "target": proposed_name,
                "reason": (
                    "recording evidence does not identify exactly one matching public anchor"
                ),
            })
            continue
        anchor_id = remaining_anchors.pop(match_position)
        step = steps[anchor_id]
        kind = _capability_operation_kind(step)
        name = _unique_live_capability_name(proposed_name, kind, used_names)
        title = (
            proposed_name
            if proposed_name in contract_names
            else f"{action_labels.get(kind, '执行')}{business}"
        )
        capabilities.append({
            "name": name,
            "title": title,
            "intent": title,
            "kind": kind,
            "anchor_step_id": anchor_id,
            "request_refs": [{"step_id": anchor_id, "usage": "execute"}],
        })
    return {
        "business_understanding": {
            "business_name": business,
            "summary": str(goal.get("intent") or "").strip(),
        },
        "capabilities": capabilities,
        "unresolved_items": unresolved_items,
    }


def _constrain_semantic_plan_to_goal(spec, semantic_plan: dict) -> dict:  # noqa: ANN001
    """Apply goal count/names without replacing Pi-owned request anchors."""
    contract = _recording_goal_contract(spec)
    if not contract:
        return semantic_plan
    expected_count = int(contract.get("expected_count") or 0)
    target_names = _goal_capability_names(spec, contract)
    candidates = [
        deepcopy(item)
        for item in (semantic_plan.get("capabilities") or [])
        if isinstance(item, dict)
    ]
    selected: list[dict] = []
    used_names: set[str] = set()
    for index in range(expected_count):
        if not candidates:
            break
        target_name = target_names[index] if index < len(target_names) else ""
        target_kind = _live_capability_kind_hint(target_name)
        matching_positions = [
            position
            for position, candidate in enumerate(candidates)
            if target_kind and (
                str(candidate.get("kind") or "") == target_kind
                or _live_capability_kind_hint(str(candidate.get("name") or "")) == target_kind
            )
        ]
        match_position = matching_positions[0] if matching_positions else 0
        candidate = candidates.pop(match_position)
        if target_name:
            candidate["name"] = _unique_live_capability_name(
                target_name,
                str(candidate.get("kind") or "capability"),
                used_names,
            )
            candidate["title"] = target_name
            candidate["intent"] = target_name
        else:
            candidate["name"] = _unique_live_capability_name(
                str(candidate.get("name") or ""),
                str(candidate.get("kind") or "capability"),
                used_names,
            )
        selected.append(candidate)
    return {
        **deepcopy(semantic_plan),
        "business_understanding": deepcopy(
            semantic_plan.get("business_understanding")
            or {}
        ),
        "capabilities": selected,
    }


def merge_live_agent_state(live_spec, finalized_spec):  # noqa: ANN001, ANN202
    """Replay accepted live agent ops onto the canonical finalized FlowSpec."""
    merged = finalized_spec.model_copy(deep=True)
    live_meta = live_spec.meta or {}
    finalized_meta = merged.meta or {}
    live_version = int(live_meta.get("current_version") or 0)
    finalized_version = int(finalized_meta.get("current_version") or 0)
    if live_version >= finalized_version:
        merged.meta = {**finalized_meta, "current_version": live_version}
        if live_meta.get("versions"):
            merged.meta["versions"] = deepcopy(live_meta["versions"])
    for key in (
        "verification_log", "agent_answers", "live_pending_questions",
        "recording_goal_text",
    ):
        if live_meta.get(key):
            merged.meta = {**(merged.meta or {}), key: deepcopy(live_meta[key])}
    goal_contract = _recording_goal_contract(merged)
    if goal_contract:
        merged.meta = {**(merged.meta or {}), "recording_goal_contract": goal_contract}
    unresolved: list[dict] = []
    for operation in live_meta.get("recording_agent_ops") or []:
        if not isinstance(operation, dict):
            continue
        try:
            operation = _retarget_unique_equivalent_field_operation(merged, operation)
            candidate = merged.model_copy(deep=True)
            result = apply_recording_agent_edit(candidate, operation, record=True)
            if result.get("deferred"):
                raise ValueError("field operation remained unresolved after final materialization")
            merged = candidate
        except (TypeError, ValueError) as exc:
            unresolved.append({
                "op": str(operation.get("op") or ""),
                "status": "rejected",
                "requested_target": deepcopy(operation.get("field_ref") or {}),
                "reason": str(exc),
            })
    live_capability_model = live_meta.get("capability_model")
    live_semantic_plan = (
        live_capability_model.get("semantic_plan")
        if isinstance(live_capability_model, dict) else None
    )
    if not (
        isinstance(live_semantic_plan, dict)
        and live_semantic_plan.get("capabilities")
    ):
        live_semantic_plan = _semantic_plan_from_live_boundaries(merged)
        if live_semantic_plan.get("capabilities"):
            live_capability_model = {
                **(live_capability_model if isinstance(live_capability_model, dict) else {}),
                "status": "ready",
                "source": "live_goal_request_roles",
                "semantic_plan": live_semantic_plan,
            }
    if isinstance(live_semantic_plan, dict) and live_semantic_plan.get("capabilities"):
        live_semantic_plan = _constrain_semantic_plan_to_goal(merged, live_semantic_plan)
        if isinstance(live_capability_model, dict):
            live_capability_model = {
                **live_capability_model,
                "semantic_plan": deepcopy(live_semantic_plan),
            }
    if isinstance(live_semantic_plan, dict) and live_semantic_plan.get("capabilities"):
        materialized_plan = deepcopy(live_semantic_plan)
        step_ids = {step.step_id for step in merged.steps}
        step_id_by_request_id = {
            str((step.source_meta or {}).get("request_id") or ""): step.step_id
            for step in merged.steps
            if str((step.source_meta or {}).get("request_id") or "")
        }
        for request_id, usage in (merged.request_facts.usage or {}).items():
            step_id = str(usage.materialized_step_id or "")
            if step_id:
                step_id_by_request_id.setdefault(str(request_id), step_id)

        unresolved_anchors: list[str] = []
        for capability in materialized_plan.get("capabilities") or []:
            if not isinstance(capability, dict):
                continue
            anchor = str(capability.get("anchor_step_id") or "")
            resolved_anchor = anchor if anchor in step_ids else step_id_by_request_id.get(anchor, "")
            if not resolved_anchor:
                unresolved_anchors.append(anchor or "<missing>")
                continue
            capability["anchor_step_id"] = resolved_anchor
            for request_ref in capability.get("request_refs") or []:
                if not isinstance(request_ref, dict):
                    continue
                identifier = str(request_ref.get("step_id") or "")
                resolved = identifier if identifier in step_ids else step_id_by_request_id.get(identifier, "")
                if resolved:
                    request_ref["step_id"] = resolved

        if unresolved_anchors:
            unresolved.append({
                "op": "compile_capabilities",
                "status": "rejected",
                "requested_target": {"anchor_step_ids": unresolved_anchors},
                "reason": "live capability anchors were not materialized at finalize",
            })
        else:
            from dano.execution.page.capability_compiler import compile_capabilities

            candidate = merged.model_copy(deep=True)
            candidate.meta = {
                **(candidate.meta or {}),
                "capability_model": {
                    **deepcopy(live_capability_model),
                    "semantic_plan": materialized_plan,
                },
            }
            compilation = compile_capabilities(candidate, materialized_plan)
            if compilation.errors:
                unresolved.append({
                    "op": "compile_capabilities",
                    "status": "rejected",
                    "requested_target": {},
                    "reason": "; ".join(compilation.errors),
                })
            else:
                merged = compilation.spec

    if goal_contract:
        actual_count = len(merged.capabilities)
        expected_count = int(goal_contract.get("expected_count") or 0)
        goal_contract = {
            **goal_contract,
            "materialized_count": actual_count,
            "satisfied": actual_count == expected_count,
        }
        merged.meta = {**(merged.meta or {}), "recording_goal_contract": goal_contract}
        if actual_count != expected_count:
            unresolved.append({
                "op": "enforce_recording_goal",
                "status": "rejected",
                "requested_target": {"expected_count": expected_count},
                "reason": (
                    f"recording goal expects {expected_count} capabilities, "
                    f"but {actual_count} executable anchors were materialized"
                ),
            })

    if unresolved:
        merged.meta = {**(merged.meta or {}), "unresolved_live_agent_ops": unresolved}
    from dano.execution.page.flow_spec import append_flow_version

    return append_flow_version(
        merged,
        "recording_finalize",
        reason="将实时录制结论绑定到最终请求步骤",
    )


def live_request_role_overrides(live_spec) -> dict[str, dict]:  # noqa: ANN001
    """Project accepted agent role ops for use before canonical materialization."""
    overrides: dict[str, dict] = {}
    for operation in (live_spec.meta or {}).get("recording_agent_ops") or []:
        if not isinstance(operation, dict) or operation.get("op") != "set_request_role":
            continue
        request_id = str(operation.get("request_id") or "")
        try:
            role = _canonical_request_role(
                live_spec,
                request_id,
                str(operation.get("role") or ""),
            )
        except ValueError:
            continue
        reason = str(operation.get("reason") or "")
        if not request_id or not role or not reason:
            continue
        overrides[request_id] = {
            "role": _INTERNAL_REQUEST_ROLE[role],
            "keep": role in {"business_read", "business_write"},
            "reason": reason,
            "confidence": max(0.8, float(operation.get("confidence") or 0)),
            "actor": "agent",
            "evidence": {
                "actor": "agent",
                "reason": reason,
                "evidence_refs": _evidence_refs(operation),
            },
        }
    return overrides


def recording_agent_evidence_issues(spec) -> list[dict]:  # noqa: ANN001
    """Report agent conclusions that lack their required evidence."""
    issues: list[dict] = []
    dynamic_structure_targets = {
        (
            str(link.target_step_id or ""),
            str(link.target_container_path or link.target_path or "").removeprefix("body."),
        )
        for link in spec.links
        if (
            link.kind == "response_key_map"
            and (link.value_binding or {}).get("kind") == "caller_map_by_label"
        )
    }
    for request_id, analysis in (spec.request_facts.analysis or {}).items():
        evidence = analysis.evidence or {}
        if evidence.get("actor") == "agent" and (not evidence.get("reason") or not evidence.get("evidence_refs")):
            issues.append({"kind": "request_role", "target": request_id, "reason": "missing agent evidence refs"})
    for step in spec.steps:
        for param in step.params:
            agent_evidence = [item for item in param.evidence or [] if isinstance(item, dict) and item.get("actor") == "agent"]
            missing_reason = [item for item in agent_evidence if not item.get("reason")]
            legacy_dynamic_target = (
                str(step.step_id or ""),
                str(param.path or "").removeprefix("body."),
            ) in dynamic_structure_targets
            if missing_reason and not (
                legacy_dynamic_target
                and all(item.get("source") == "response_key_map" for item in missing_reason)
            ):
                issues.append({"kind": "param_source", "target": f"{step.step_id}:{param.path}", "reason": "missing agent reason"})
    for link in spec.links:
        if (link.meta or {}).get("actor") == "agent" and not (link.evidence or {}).get("actor"):
            issues.append({"kind": "dependency", "target": link.link_id, "reason": "missing dependency evidence"})
    return issues
