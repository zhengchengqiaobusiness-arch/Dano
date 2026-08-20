"""Stage 5: required / optional / unknown evidence."""
from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse
from dano.execution.page.flow_spec_core.models import (
    FlowSpec,
    ParamField,
)
from dano.execution.page.request_capture import (
    parse_recorded_request_body,
)
from dano.execution.page.recording_facts import (
    _field_leaf_token,
    _request_path,
)
from dano.execution.page.flow_materialization.field_contracts.common import (
    _param_has_manual_contract,
)


def _param_required_agent_classified(param: ParamField) -> bool:
    return any(
        isinstance(item, dict)
        and item.get("actor") == "agent"
        and item.get("kind") == "param_required"
        for item in (param.evidence or [])
    )


def _param_has_page_required_evidence(param: ParamField) -> bool:
    """Return true only for a captured page-required marker.

    A populated query string, planner-required flag, field name, or sample value
    is not proof that a search filter is mandatory.
    """
    return any(
        isinstance(item, dict)
        and (
            (
                item.get("kind") == "page_required"
                and str(item.get("binding_status") or "") == "bound"
            )
            or (
                item.get("source") in {"recorder_dom", "page", "page_snapshot"}
                and item.get("required") is True
                and str(item.get("binding_status") or "") == "bound"
            )
        )
        for item in (param.evidence or [])
    )


def _param_has_local_required_marker(param: ParamField) -> bool:
    if _param_has_page_required_evidence(param):
        return True
    return any(
        isinstance(item, dict)
        and item.get("kind") == "page_control"
        and item.get("required") is True
        for item in (param.evidence or [])
    )


def _request_origin(url: str) -> str:
    parsed = urlparse(str(url or ""))
    if not parsed.hostname:
        return ""
    scheme = parsed.scheme.casefold()
    port = parsed.port
    if port is None:
        port = 443 if scheme == "https" else 80 if scheme == "http" else None
    return f"{scheme}://{parsed.hostname.casefold()}:{port}" if port else f"{scheme}://{parsed.hostname.casefold()}"


def _body_schema_family(parsed: dict[str, Any]) -> str:
    value = parsed.get("value")
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        item_kinds = sorted({
            "object" if isinstance(item, dict)
            else "array" if isinstance(item, list)
            else type(item).__name__
            for item in value
        })
        return f"array:{','.join(item_kinds)}"
    return type(value).__name__ if value is not None else "empty"


def _request_present_fields(
    req: dict[str, Any],
) -> tuple[set[tuple[str, str]], dict[str, Any]]:
    fields: set[tuple[str, str]] = set()
    query = req.get("query") or {}
    if isinstance(query, dict):
        for key, values in query.items():
            if values in (None, "", [], [""]):
                continue
            fields.add(("query", str(key)))
    url = str(req.get("url") or "")
    if "?" in url:
        for key, values in parse_qs(url.split("?", 1)[1], keep_blank_values=True).items():
            if values in ([], [""]):
                continue
            fields.add(("query", str(key)))
    parsed = parse_recorded_request_body(req.get("post_data"), str(req.get("content_type") or ""))
    body_observable = bool(
        parsed.get("parse_complete")
        and req.get("post_data") not in (None, "")
    )
    if body_observable:
        for path in parsed.get("field_paths") or []:
            text = str(path or "").removeprefix("body.")
            if text:
                fields.add(("body", text))
    return fields, {
        "kind": str(parsed.get("kind") or "empty"),
        "schema_family": _body_schema_family(parsed),
        "body_observable": body_observable,
    }


def _request_semantic_family(
    *,
    method: str,
    url: str,
    path: str,
    page_id: str,
    frame_id: str,
    role: str,
    body_kind: str,
    body_schema_family: str,
) -> tuple[str, ...]:
    return (
        method.upper(),
        _request_origin(url),
        _request_path({"url": url, "path": path}),
        body_kind,
        body_schema_family,
        page_id,
        frame_id,
        role,
    )


def _successful_peer_omitted_fields(spec: FlowSpec) -> set[tuple[tuple[str, ...], str, str]]:
    groups: dict[tuple[str, ...], list[tuple[set[tuple[str, str]], bool]]] = {}
    for fact in spec.request_facts.requests or []:
        status = int(getattr(fact, "response_status", None) or 0)
        if not (200 <= status < 400):
            continue
        method = str(getattr(fact, "method", "") or "GET").upper()
        url = str(getattr(fact, "url", "") or "")
        request_id = str(getattr(fact, "request_id", "") or "")
        fields, body_meta = _request_present_fields({
            "url": url,
            "query": getattr(fact, "query", None) or {},
            "post_data": getattr(fact, "post_data", None),
            "content_type": getattr(fact, "content_type", "") or "",
        })
        analysis = (spec.request_facts.analysis or {}).get(request_id)
        family = _request_semantic_family(
            method=method,
            url=url,
            path=str(getattr(fact, "path", "") or ""),
            page_id=str(getattr(fact, "page_id", "") or ""),
            frame_id=str(getattr(fact, "frame_id", "") or ""),
            role=str(getattr(analysis, "role", "") or ""),
            body_kind=body_meta["kind"],
            body_schema_family=body_meta["schema_family"],
        )
        groups.setdefault(family, []).append((fields, bool(body_meta["body_observable"])))
    omitted: set[tuple[tuple[str, ...], str, str]] = set()
    for family, observations in groups.items():
        if len(observations) < 2:
            continue
        for location in ("query", "body"):
            comparable = [
                {path for field_location, path in fields if field_location == location}
                for fields, body_observable in observations
                if location == "query" or body_observable
            ]
            if len(comparable) < 2:
                continue
            union: set[str] = set().union(*comparable)
            for present in comparable:
                for field_path in union - present:
                    omitted.add((family, location, field_path))
    return omitted


def _apply_successful_omit_optional(spec: FlowSpec) -> None:
    omitted = _successful_peer_omitted_fields(spec)
    if not omitted:
        return
    for step in spec.steps or []:
        source_meta = step.source_meta or {}
        parsed = parse_recorded_request_body(step.body_source, step.content_type)
        family = _request_semantic_family(
            method=str(step.method or "GET"),
            url=str(step.url or ""),
            path=str(step.path or ""),
            page_id=str(source_meta.get("page_id") or ""),
            frame_id=str(source_meta.get("frame_id") or ""),
            role=str(source_meta.get("role") or step.semantic_role or ""),
            body_kind=str(parsed.get("kind") or "empty"),
            body_schema_family=_body_schema_family(parsed),
        )
        for param in step.params or []:
            if param.locked or _param_has_manual_contract(param):
                continue
            if str((param.source or {}).get("required_state") or "") == "required":
                continue
            if _param_has_page_required_evidence(param) or _param_has_local_required_marker(param):
                continue
            param_path = str(param.path or param.key or "")
            location = "query" if param_path.startswith("query.") else "body"
            field_path = param_path.removeprefix("query.").removeprefix("body.")
            if not field_path:
                field_path = _field_leaf_token(param.key, param.path)
            if (family, location, field_path) not in omitted:
                continue
            param.source = {**(param.source or {}), "required_state": "optional"}
            param.required = False
