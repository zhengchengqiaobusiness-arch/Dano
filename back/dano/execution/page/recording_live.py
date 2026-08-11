"""Live recording facts and agent-authored semantic annotations."""
from __future__ import annotations

from copy import deepcopy
import json
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from dano.execution.page.value_tracing import discover_value_links
from dano.infra.token_store import mask_headers


LIVE_RECORDING_AGENT_OPS = frozenset({
    "set_goal",
    "set_request_role",
    "set_param_source",
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
    "user_input", "constant", "session_header", "page_context", "chained", "computed",
})
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
    projected = {
        key: deepcopy(request.get(key))
        for key in (
            "request_id", "index", "request_index", "sequence", "timestamp", "method",
            "path", "query", "post_data", "response_status", "status", "response_json",
            "content_type", "role", "keep", "reason", "confidence", "trigger_action_id",
            "trigger_transaction_id", "action_delta_ms", "causality_confidence",
        )
        if request.get(key) is not None
    }
    projected["url"] = _redact_url(str(request.get("url") or ""))
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
        "response_json": (5, 20, 800),
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
) -> dict:  # noqa: ANN001
    """Project a bounded, redacted append-only request delta for the model."""
    requests = recorder.captured_all_requests()
    start = max(0, min(int(since_seq or 0), len(requests)))
    page_size = max(1, min(int(limit or _DEFAULT_DELTA_LIMIT), _MAX_DELTA_LIMIT))
    fresh = requests[start:start + page_size]
    next_seq = start + len(fresh)
    fresh_ids = {str(request.get("request_id") or "") for request in fresh}
    candidates = [
        item for item in discover_value_links(requests)
        if item.get("source_request_id") in fresh_ids or item.get("target_request_id") in fresh_ids
    ]
    page_events = recorder.recorded_page_events()
    return {
        "since_seq": start,
        "next_seq": next_seq,
        "total_seq": len(requests),
        "page_size": page_size,
        "has_more": next_seq < len(requests),
        "goal_text": str(goal_text or ""),
        "requests": [_request_projection(request) for request in fresh],
        "page_events": compact_model_payload(_redact(page_events[-50:]), max_depth=5, max_items=50),
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
    step = next(
        (item for item in spec.steps if item.step_id == step_or_request_id),
        None,
    ) or _request_step(spec, step_or_request_id)
    if step is None:
        if _known_request_id(spec, step_or_request_id):
            return None, None
        raise ValueError(f"field target not found: {step_or_request_id}:{path}")
    param = next((item for item in step.params if item.path == path), None)
    if param is None:
        raise ValueError(f"field target not found: {step_or_request_id}:{path}")
    return step, param


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
            for key in ("event_id", "action_id", "field"):
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


def _field_evidence_candidates(spec, step, param) -> list[dict]:  # noqa: ANN001
    """Return recorder controls that can be tied to this exact request field."""
    request_id = str((step.source_meta or {}).get("request_id") or "")
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
        evidence_request_id = str(item.get("request_id") or "")
        if evidence_request_id and request_id and evidence_request_id != request_id:
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


def _require_required_grounding(spec, step, param, required: bool) -> None:  # noqa: ANN001
    candidates = _field_evidence_candidates(spec, step, param)
    observed = {
        item.get("required")
        for item in candidates
        if isinstance(item.get("required"), bool)
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


def _require_label_grounding(spec, step, param, label: str) -> None:  # noqa: ANN001
    candidates = _field_evidence_candidates(spec, step, param)
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


def _enum_pair(option: object) -> tuple[str, str] | None:
    if not isinstance(option, dict) or "label" not in option or "value" not in option:
        return None
    return (
        str(option.get("label") or "").strip(),
        json.dumps(option.get("value"), ensure_ascii=False, sort_keys=True, default=str),
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


def _recorded_enum_contract(spec, step, param) -> dict | None:  # noqa: ANN001
    from dano.execution.page.flow_spec import _page_enum_options_from_request_facts

    candidates = _field_evidence_candidates(spec, step, param)
    control_aliases = {
        _normalized_field_alias(value)
        for item in candidates
        for value in (
            item.get("path"), item.get("key"), item.get("label"), item.get("suggest_name"),
            *(item.get("field_aliases") or []),
        )
        if _normalized_field_alias(value)
    }
    if not candidates or not any(
        str(item.get("control_kind") or "").lower() in {"select", "radio", "checkbox", "cascader"}
        for item in candidates
    ):
        return None
    for name, raw in _page_enum_options_from_request_facts(spec.request_facts).items():
        if not isinstance(raw, dict):
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
        if aliases & control_aliases:
            return raw
    return None


def _append_insight(spec, *, kind: str, text: str, refs: list[str]) -> None:  # noqa: ANN001
    _append_meta_list(spec, "agent_insights", {"kind": kind, "text": text, "refs": refs})


def _record_agent_op(spec, edit: dict) -> None:  # noqa: ANN001
    _append_meta_list(spec, "recording_agent_ops", edit)


def _trusted_verification(spec, verification_id: str, kinds: set[str]) -> dict:  # noqa: ANN001
    from dano.execution.page.verification_log import find_verification

    record = find_verification(
        verification_id,
        list((spec.meta or {}).get("verification_log") or []),
    )
    if record is None or str(record.get("kind") or "") not in kinds:
        raise ValueError("verification_id is missing or has the wrong kind")
    evidence = record.get("evidence") or {}
    if evidence.get("passed") is False:
        raise ValueError("verification evidence did not pass")
    return record


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

    if source_kind == "user_input":
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

    elif source_kind == "session_header":
        if not param.path.startswith("headers."):
            raise ValueError(
                f"session_header only applies to header params; {param.path} is not a header. "
                "Use constant for fixed body/query values, chained for upstream-derived values"
            )
        param.source_kind = "request_header"
        param.source = {
            "kind": "request_header",
            "header": param.path.split(".", 1)[1],
            "reason": reason,
            "actor": "agent",
        }
        param.category = "runtime_var"
        param.exposed_to_user = False

    elif source_kind == "page_context":
        context_key = str(edit.get("context_key") or "") or param.path.split(".")[-1].split("[")[0]
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
            if param.default_value in (None, ""):
                param.default_value = param.value

    elif source_kind == "chained":
        if not origin_request_id or not origin_path:
            raise ValueError(
                f"chained classification for {param.path} requires origin_request_id and origin_path"
            )
        source_step = _request_step(spec, origin_request_id)
        if source_step is None:
            if _known_request_id(spec, origin_request_id):
                raise _DeferredCompile(
                    "chained origin request is captured but not materialized as a step yet"
                )
            raise ValueError(
                f"chained origin request {origin_request_id} is not part of the recorded facts"
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
            }
            link.meta = {"verified": False, "actor": "agent"}
            spec.links.append(link)
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
        available_fields = {
            str(value).strip()
            for item_step in spec.steps
            for item_param in item_step.params
            if item_param.category == "user_param" and item_param.exposed_to_user
            for value in (
                item_param.key,
                item_param.label,
                item_param.path,
                str(item_param.path or "").split(".")[-1],
            )
            if str(value or "").strip()
        }
        missing_fields = [
            field for field in (start_field, end_field)
            if field not in available_fields
        ]
        if missing_fields:
            raise ValueError(
                f"computed classification for {param.path} references unknown caller fields: "
                f"{missing_fields!r}"
            )
        if not output_key:
            try:
                recorded_payload = json.loads(str(param.value or param.default_value or ""))
            except (TypeError, ValueError):
                recorded_payload = None
            if isinstance(recorded_payload, dict) and len(recorded_payload) == 1:
                output_key = str(next(iter(recorded_payload)))
        param.source_kind = "computed"
        param.source = {
            "kind": "computed",
            "strategy": strategy,
            "start_field": start_field,
            "end_field": end_field,
            "output_key": output_key or "days",
            "reason": reason,
            "actor": "agent",
        }
        param.category = "runtime_var"
        param.exposed_to_user = False

    advice = _field_source_configuration_advice(param)
    if advice:
        raise ValueError(f"source classification does not compile: {advice}")


def apply_recording_agent_edit(spec, edit: dict, *, record: bool = True) -> dict:  # noqa: ANN001
    """Apply one live-only op; unresolved early endpoints remain replayable at finalize."""
    from dano.execution.page.flow_spec import FlowLink, RecordedGoal, RequestAnalysis

    kind = str(edit.get("op") or "")
    if kind not in LIVE_RECORDING_AGENT_OPS:
        raise ValueError(f"unsupported live recording op: {kind}")
    if str(edit.get("actor") or "agent") not in {"agent", "planner", "repair"}:
        raise ValueError("live recording ops must be agent-authored")
    stored = {**deepcopy(edit), "actor": "agent"}
    if record and any(
        isinstance(existing, dict) and existing == stored
        for existing in (spec.meta or {}).get("recording_agent_ops") or []
    ):
        return {"status": "skipped", "reason": "duplicate operation"}

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
        role = str(edit.get("role") or "")
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
        if not request_id or not role or not reason or not evidence_refs:
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
        _append_insight(spec, kind="role", text=f"{request_id} 判定为 {role}：{reason}", refs=[request_id, *evidence_refs])

    elif kind == "set_param_source":
        step_id = str(edit.get("step_id") or "")
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
                result["deferred"] = True
                result["reason"] = str(pending)
                if record:
                    _record_agent_op(spec, stored)
                return result
            param.reason = reason
            param.evidence = [
                *list(param.evidence or []),
                {
                    "actor": "agent",
                    "kind": "param_source",
                    "source_kind": source_kind,
                    "origin_request_id": str(edit.get("origin_request_id") or ""),
                    "origin_path": str(edit.get("origin_path") or ""),
                    "reason": reason,
                },
            ]
        else:
            result["deferred"] = True
            result["reason"] = "request is captured but its canonical step is not materialized yet"
        _append_insight(spec, kind="param_source", text=f"{step_id}:{path} 来源为 {source_kind}：{reason}", refs=[step_id, path])

    elif kind == "set_param_required":
        step_id = str(edit.get("step_id") or "")
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
            _require_required_grounding(spec, step, param, required)
            param.required = required
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
            result["deferred"] = True
            result["reason"] = "request is captured but its canonical step is not materialized yet"
        _append_insight(
            spec,
            kind="param_required",
            text=f"{step_id}:{path} 必填性为 {required}：{reason}",
            refs=[step_id, path, *evidence_refs],
        )

    elif kind == "set_param_enum":
        step_id = str(edit.get("step_id") or "")
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
            recorded = _recorded_enum_contract(spec, step, param)
            if recorded is None:
                raise ValueError(
                    f"enum conclusion for {param.path} has no matching select field_evidence and dictionary source"
                )
            recorded_source = str(recorded.get("dict_type") or recorded.get("dictionary_source") or "")
            if dictionary_source and recorded_source and dictionary_source != recorded_source:
                raise ValueError(
                    f"dictionary_source {dictionary_source!r} contradicts recorded dictionary {recorded_source!r}"
                )
            submitted_pairs = {_enum_pair(option) for option in options}
            recorded_pairs = {_enum_pair(option) for option in (recorded.get("options") or [])}
            if None in submitted_pairs or None in recorded_pairs or submitted_pairs != recorded_pairs:
                raise ValueError(
                    f"enum options for {param.path} contradicts recorded dictionary label/value mapping"
                )
            param.type = "enum"
            param.category = "user_param"
            param.source_kind = "page_enum"
            param.source = {
                "kind": "page_enum",
                "dictionary_source": recorded_source or dictionary_source,
                "enum_confirmed": True,
                "actor": "agent",
            }
            param.exposed_to_user = True
            param.editable = True
            param.enum_options = deepcopy(options)
            param.enum_value_map = {
                pair[0]: option.get("value")
                for option in options
                if (pair := _enum_pair(option)) is not None
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
            result["deferred"] = True
            result["reason"] = "request is captured but its canonical step is not materialized yet"
        _append_insight(
            spec,
            kind="enum_options",
            text=f"{step_id}:{path} 枚举已按录制字典绑定：{reason}",
            refs=[step_id, path, *evidence_refs],
        )

    elif kind == "rename_field":
        step_id = str(edit.get("step_id") or "")
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
            _require_label_grounding(spec, step, param, label)
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
            result["deferred"] = True
            result["reason"] = "request is captured but its canonical step is not materialized yet"
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
        if link_kind not in {"value", "structure"}:
            raise ValueError("propose_dependency kind must be value or structure")
        if not source_request_id or not source_path or not (target_request_id or target_step_id) or not target_path:
            raise ValueError("propose_dependency requires source and target request/step paths")
        if not isinstance(evidence, dict) or not evidence:
            raise ValueError("propose_dependency requires evidence")
        source_step = _request_step(spec, source_request_id)
        target_step = next((step for step in spec.steps if step.step_id == target_step_id), None) or _request_step(spec, target_request_id)
        if link_kind == "structure" and target_step is not None:
            container = target_path.removesuffix(".*").removesuffix("[*]")
            if not any(
                str(item.path or "").startswith(container)
                for item in target_step.params
            ):
                raise ValueError(
                    f"structure dependency target {target_path} does not match any recorded param "
                    f"on step {target_step.step_id}"
                )
            if source_step is not None:
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
        if source_step is not None and target_step is not None:
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
            )
            link.confirmed = False
            link.confidence = max(float(link.confidence or 0), float(edit.get("confidence") or 0.75))
            link.reason = str(edit.get("reason") or "agent 提出的待验证依赖")
            link.evidence = {
                **(link.evidence or {}),
                **evidence,
                "actor": "agent",
                "source_request_id": source_request_id,
                "target_request_id": target_request_id,
            }
            link.meta = {**(link.meta or {}), "verified": False, "actor": "agent"}
            if link_kind == "structure":
                link.meta["kind"] = "structure"
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
        verification_record = _trusted_verification(spec, verification_id, {"perturb_link"})
        subject = verification_record.get("subject") or {}
        chain = [str(value) for value in subject.get("chain_request_ids") or []]
        source_request_id = str((link.evidence or {}).get("source_request_id") or _step_request_id(spec, link.source_step_id))
        target_request_id = str((link.evidence or {}).get("target_request_id") or _step_request_id(spec, link.target_step_id))
        if not source_request_id or not target_request_id or source_request_id not in chain or target_request_id not in chain:
            raise ValueError("perturb verification subject does not match dependency endpoints")
        if chain.index(source_request_id) >= chain.index(target_request_id):
            raise ValueError("perturb verification chain order does not match dependency")
        if not ((verification_record.get("evidence") or {}).get("linked_paths") or subject.get("linked_paths")):
            raise ValueError("perturb verification did not observe a linked value")
        if str((link.meta or {}).get("kind") or "") == "structure":
            observed_paths = [
                re.sub(r"\[\d+\]", "[*]", str(item.get("path") or "")).removeprefix("response.")
                for item in (
                    (verification_record.get("evidence") or {}).get("linked_paths")
                    or subject.get("linked_paths")
                    or []
                )
                if isinstance(item, dict)
            ]
            expected_path = re.sub(r"\[\d+\]", "[*]", str(link.source_path or "")).removeprefix("response.")
            if expected_path not in observed_paths:
                raise ValueError(
                    "perturb verification did not observe the structure dependency source path"
                )
        link.confirmed = True
        link.confidence = 1.0
        link.meta = {**(link.meta or {}), "verified": True, "actor": "agent", "verification_id": verification_id}
        link.evidence = {**(link.evidence or {}), "actor": "agent", "verification_id": verification_id}

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

        step_id = str(edit.get("step_id") or "")
        path = str(edit.get("path") or "")
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
        _bind_option_source(
            spec,
            target_step_id=step_id,
            target_path=path,
            source_url=source_fact.path or source_fact.url,
            source_request_id=source_request_id,
            options=options,
            actor="agent",
        )
        step = next(item for item in spec.steps if item.step_id == step_id)
        binding = next(item for item in step.selects if item.path == path or item.id_path == path)
        binding.actor = "agent"
        binding.confidence = 1.0
        binding.verification_id = verification_id
        param = next(item for item in step.params if item.path == path)
        param.evidence.append({"actor": "agent", "kind": "enum_options", "verification_id": verification_id})

    elif kind == "mark_unverified":
        target_kind = str(edit.get("target_kind") or "")
        target_id = str(edit.get("target_id") or "")
        reason = str(edit.get("reason") or "").strip()
        if target_kind not in {"dependency", "write_verify", "enum"} or not target_id or not reason:
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
        else:
            if ":" not in target_id:
                raise ValueError("mark_unverified enum target must be step_id:path")
            step_id, path = target_id.split(":", 1)
            step = next((item for item in spec.steps if item.step_id == step_id), None)
            if step is None or not any(item.path == path for item in step.params):
                raise ValueError("mark_unverified enum target does not exist")

    if record:
        _record_agent_op(spec, stored)
    return result


def merge_live_agent_state(live_spec, finalized_spec):  # noqa: ANN001, ANN202
    """Replay accepted live agent ops onto the canonical finalized FlowSpec."""
    merged = finalized_spec.model_copy(deep=True)
    live_meta = live_spec.meta or {}
    for key in ("verification_log", "agent_answers"):
        if live_meta.get(key):
            merged.meta = {**(merged.meta or {}), key: deepcopy(live_meta[key])}
    unresolved: list[dict] = []
    for operation in live_meta.get("recording_agent_ops") or []:
        if not isinstance(operation, dict):
            continue
        try:
            result = apply_recording_agent_edit(merged, operation, record=True)
            if result.get("deferred"):
                raise ValueError("field operation remained unresolved after final materialization")
        except (TypeError, ValueError) as exc:
            unresolved.append({"op": str(operation.get("op") or ""), "reason": str(exc)})
    if unresolved:
        merged.meta = {**(merged.meta or {}), "unresolved_live_agent_ops": unresolved}
    return merged


def recording_agent_evidence_issues(spec) -> list[dict]:  # noqa: ANN001
    """Report agent conclusions that lack their required evidence."""
    issues: list[dict] = []
    for request_id, analysis in (spec.request_facts.analysis or {}).items():
        evidence = analysis.evidence or {}
        if evidence.get("actor") == "agent" and (not evidence.get("reason") or not evidence.get("evidence_refs")):
            issues.append({"kind": "request_role", "target": request_id, "reason": "missing agent evidence refs"})
    for step in spec.steps:
        for param in step.params:
            agent_evidence = [item for item in param.evidence or [] if isinstance(item, dict) and item.get("actor") == "agent"]
            if agent_evidence and not all(item.get("reason") for item in agent_evidence):
                issues.append({"kind": "param_source", "target": f"{step.step_id}:{param.path}", "reason": "missing agent reason"})
    for link in spec.links:
        if (link.meta or {}).get("actor") == "agent" and not (link.evidence or {}).get("actor"):
            issues.append({"kind": "dependency", "target": link.link_id, "reason": "missing dependency evidence"})
    return issues
