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
    "propose_dependency",
    "add_pitfall",
    "confirm_dependency",
    "bind_verify_read",
    "attach_enum_options",
    "mark_unverified",
})

_PARAM_SOURCE_KINDS = frozenset({"user_input", "session_header", "page_context", "chained"})
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


def apply_recording_agent_edit(spec, edit: dict, *, record: bool = True) -> None:  # noqa: ANN001
    """Apply one live-only op; unresolved early endpoints remain replayable at finalize."""
    from dano.execution.page.flow_spec import FlowLink, RecordedGoal, RequestAnalysis

    kind = str(edit.get("op") or "")
    if kind not in LIVE_RECORDING_AGENT_OPS:
        raise ValueError(f"unsupported live recording op: {kind}")
    if str(edit.get("actor") or "agent") not in {"agent", "planner", "repair"}:
        raise ValueError("live recording ops must be agent-authored")

    if kind == "set_goal":
        goal = RecordedGoal.model_validate(edit.get("goal") or {})
        if not goal.intent.strip():
            raise ValueError("set_goal requires goal.intent")
        if not goal.evidence:
            raise ValueError("set_goal requires goal.evidence")
        spec.goal = goal.model_dump(mode="json")
        _append_insight(spec, kind="goal", text=f"目标：{goal.intent}", refs=["goal_text"])

    elif kind == "set_request_role":
        request_id = str(edit.get("request_id") or "")
        role = str(edit.get("role") or "")
        reason = str(edit.get("reason") or "").strip()
        evidence_refs = [str(value) for value in edit.get("evidence_refs") or [] if str(value)]
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
        path = str(edit.get("path") or "")
        source_kind = str(edit.get("source_kind") or "")
        reason = str(edit.get("reason") or "").strip()
        if not step_id or not path or source_kind not in _PARAM_SOURCE_KINDS or not reason:
            raise ValueError("set_param_source requires step_id, path, four-class source_kind and reason")
        step = next((item for item in spec.steps if item.step_id == step_id), None)
        param = next((item for item in (step.params if step else []) if item.path == path), None)
        if param is not None:
            if param.locked:
                raise ValueError(f"set_param_source target is locked: {step_id}:{path}")
            param.source_kind = source_kind
            param.source = {
                "kind": source_kind,
                "origin_request_id": str(edit.get("origin_request_id") or ""),
                "origin_path": str(edit.get("origin_path") or ""),
                "reason": reason,
                "actor": "agent",
            }
            param.category = "user_param" if source_kind == "user_input" else "runtime_var"
            param.exposed_to_user = source_kind == "user_input"
            param.reason = reason
            param.evidence = [
                *list(param.evidence or []),
                {"actor": "agent", "kind": "param_source", "reason": reason},
            ]
        _append_insight(spec, kind="param_source", text=f"{step_id}:{path} 来源为 {source_kind}：{reason}", refs=[step_id, path])

    elif kind == "propose_dependency":
        requested_link_id = str(edit.get("link_id") or "")
        source_request_id = str(edit.get("source_request_id") or "")
        target_request_id = str(edit.get("target_request_id") or "")
        target_step_id = str(edit.get("target_step_id") or edit.get("step_id") or "")
        source_path = str(edit.get("source_path") or "")
        target_path = str(edit.get("target_path") or "")
        evidence = edit.get("evidence")
        if not source_request_id or not source_path or not (target_request_id or target_step_id) or not target_path:
            raise ValueError("propose_dependency requires source and target request/step paths")
        if not isinstance(evidence, dict) or not evidence:
            raise ValueError("propose_dependency requires evidence")
        source_step = _request_step(spec, source_request_id)
        target_step = next((step for step in spec.steps if step.step_id == target_step_id), None) or _request_step(spec, target_request_id)
        if source_step is not None and target_step is not None:
            signature = (source_step.step_id, source_path, target_step.step_id, target_path)
            existing = next((
                link for link in spec.links
                if (link.source_step_id, link.source_path, link.target_step_id, link.target_path) == signature
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
        stored = {**deepcopy(edit), "actor": "agent"}
        _record_agent_op(spec, stored)


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
            apply_recording_agent_edit(merged, operation, record=True)
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
