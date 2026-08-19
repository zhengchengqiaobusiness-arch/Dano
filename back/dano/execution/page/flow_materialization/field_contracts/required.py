"""Stage 5: required / optional / unknown evidence."""
from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs
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


def _request_present_leaves(req: dict[str, Any]) -> set[str]:
    leaves: set[str] = set()
    query = req.get("query") or {}
    if isinstance(query, dict):
        for key, values in query.items():
            if values in (None, "", [], [""]):
                continue
            leaves.add(str(key))
    url = str(req.get("url") or "")
    if "?" in url:
        for key, values in parse_qs(url.split("?", 1)[1], keep_blank_values=True).items():
            if values in ([], [""]):
                continue
            leaves.add(str(key))
    parsed = parse_recorded_request_body(req.get("post_data"), str(req.get("content_type") or ""))
    for path in parsed.get("field_paths") or []:
        text = str(path or "").removeprefix("body.").removeprefix("query.")
        if text:
            leaves.add(text)
            leaves.add(text.rsplit(".", 1)[-1].split("[")[0])
    return {leaf for leaf in leaves if leaf}


def _successful_peer_omitted_leaves(spec: FlowSpec) -> set[tuple[str, str]]:
    groups: dict[tuple[str, str], list[set[str]]] = {}
    for fact in spec.request_facts.requests or []:
        status = int(getattr(fact, "response_status", None) or 0)
        if not (200 <= status < 400):
            continue
        method = str(getattr(fact, "method", "") or "GET").upper()
        path = _request_path({
            "url": getattr(fact, "url", "") or "",
            "path": getattr(fact, "path", "") or "",
        })
        groups.setdefault((method, path), []).append(_request_present_leaves({
            "url": getattr(fact, "url", "") or "",
            "query": getattr(fact, "query", None) or {},
            "post_data": getattr(fact, "post_data", None),
            "content_type": getattr(fact, "content_type", "") or "",
        }))
    omitted: set[tuple[str, str]] = set()
    for (_method, path), leaf_sets in groups.items():
        if len(leaf_sets) < 2:
            continue
        union: set[str] = set()
        for item in leaf_sets:
            union |= item
        for item in leaf_sets:
            for leaf in union - item:
                omitted.add((path, leaf))
    return omitted


def _apply_successful_omit_optional(spec: FlowSpec) -> None:
    omitted = _successful_peer_omitted_leaves(spec)
    if not omitted:
        return
    for step in spec.steps or []:
        path = _request_path({"url": step.path or step.url or ""})
        for param in step.params or []:
            if param.locked or _param_has_manual_contract(param):
                continue
            if str((param.source or {}).get("required_state") or "") == "required":
                continue
            if _param_has_page_required_evidence(param) or _param_has_local_required_marker(param):
                continue
            leaf = _field_leaf_token(param.key, param.path)
            if (path, leaf) not in omitted:
                continue
            param.source = {**(param.source or {}), "required_state": "optional"}
            param.required = False
