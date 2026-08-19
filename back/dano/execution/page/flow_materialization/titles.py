"""Stage 5: derive human-readable step titles from captured requests."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse
from dano.execution.page.flow_spec_core.models import (
    FlowStep,
)
from dano.execution.page.request_capture import (
    looks_internal_param_name,
    suggest_select_names,
)


def _default_step_name(req: dict) -> str:
    url = req.get("url") or req.get("path") or ""
    method = (req.get("method") or "POST").upper()
    try:
        path = urlparse(url).path if url.startswith("http") else url
    except Exception:
        path = url
    segs = [s for s in (path or "").split("/") if s]
    last = segs[-1] if segs else ""
    if not last:
        return f"{method}_未命名"
    last = last.split("?")[0].rsplit(".", 1)[0]
    return f"{method}_{last}"


def _select_name_for_step(selects: list[dict], samples: dict) -> dict[str, str]:
    out = suggest_select_names(selects, samples)
    for s in selects or []:
        path = str(s.get("path") or "")
        field_key = str(s.get("field_key") or "").strip()
        if not path or not field_key:
            continue
        if looks_internal_param_name(field_key):
            continue
        out[path] = field_key
    return out


def _derive_title(
    steps: list[FlowStep],
    extra_contexts: list[dict[str, Any]] | None = None,
) -> str:
    if not steps:
        return ""
    # The recorder already carries the page titles that were visible when an
    # operation was clicked.  They are stronger business evidence than an API
    # action suffix (``submit-process``, ``cancel-by-start-user`` and the like).
    # Prefer that evidence before exposing a transport path as the flow title.
    contexts: list[dict[str, Any]] = [
        dict(context)
        for context in (extra_contexts or [])
        if isinstance(context, dict) and context
    ]
    for step in steps:
        meta = step.source_meta or {}
        for key in ("trigger_page_context", "page_context"):
            value = meta.get(key)
            if isinstance(value, dict) and value:
                contexts.append(dict(value))
    page_business = _page_context_business_name_from_contexts(contexts)
    if page_business:
        return page_business
    first = next((s for s in reversed(steps) if (s.method or "").upper() not in {"GET", "HEAD", "OPTIONS"}), steps[-1])
    try:
        url = first.url or first.path
        path = urlparse(url).path if url.startswith("http") else url
    except Exception:
        path = first.path
    segs = [s for s in (path or "").split("/") if s]
    last = segs[-1].split("?")[0] if segs else ""
    if not last:
        return first.name or "(未命名)"
    if len(steps) > 1:
        return f"{last} 流程({len(steps)} 步)"
    return last


def _derive_step_name(step: FlowStep) -> str:
    url = step.url or step.path
    try:
        path = urlparse(url).path if url.startswith("http") else url
    except Exception:
        path = step.path
    segs = [s for s in (path or "").split("/") if s]
    last = segs[-1].split("?")[0] if segs else ""
    method = (step.method or "POST").upper()
    if not last:
        return f"{method}_未命名"
    if step.params:
        return f"{method}_{last}(含{len(step.params)}字段)"
    return f"{method}_{last}"

_PENDING_FLOW_SPEC_HELPERS = {'_page_context_business_name_from_contexts': 'dano.execution.page.capability_contracts'}


def _bind_flow_spec_helpers() -> None:
    import sys
    module_globals = globals()
    for name, owner in _PENDING_FLOW_SPEC_HELPERS.items():
        mod = sys.modules.get(owner)
        if mod is None or not hasattr(mod, name):
            continue
        module_globals[name] = getattr(mod, name)


_bind_flow_spec_helpers()
