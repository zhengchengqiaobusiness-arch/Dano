"""Stage 3: read-only recording state/delta projections for the Skill."""
from __future__ import annotations

from typing import Any
from dano.execution.page.flow_spec_core.models import (
    FlowSpec,
)
from dano.execution.page.recording_facts import (
    _compact_repeated_endpoint_observations,
    _request_fact_has_record_identity,
    _request_order_value,
)
from dano.execution.page.recording_live import (
    compact_model_payload,
)


def _model_visible_request_facts(
    request_facts: list[dict[str, Any]], *, max_items: int = 80,
) -> list[dict[str, Any]]:
    """Keep late/candidate requests visible without expanding model context.

    Raw request facts remain complete in FlowSpec and the append-only delta.
    This state projection prioritizes business/causal/materialized facts, then
    fills remaining slots from the newest observations.
    """
    request_facts = _compact_repeated_endpoint_observations(request_facts)
    priority_roles = {"business_get", "business_write", "submit_anchor", "read_option"}
    priority = [
        item for item in request_facts
        if item.get("keep") is True
        or str(item.get("role") or "") in priority_roles
        or item.get("materialized_step_id")
        or str(item.get("trigger_op") or "").lower() in {"click", "submit", "select", "pick"}
        or _request_fact_has_record_identity(item)
    ]
    selected: dict[str, dict[str, Any]] = {}
    for item in [*reversed(priority), *reversed(request_facts)]:
        request_id = str(item.get("request_id") or item.get("request_index") or id(item))
        selected.setdefault(request_id, item)
        if len(selected) >= max_items:
            break
    return sorted(
        selected.values(),
        key=lambda item: _request_order_value(item) if _request_order_value(item) is not None else -1,
    )


def recording_agent_state(spec: FlowSpec) -> dict[str, Any]:
    """Return the authoritative, redacted state available to Pi tools."""
    from dano.execution.page.recording_live import compact_model_payload

    current = refresh_review_items(_sync_capability_io_schemas(spec.model_copy(deep=True)))
    report = validate_flow_spec(current)
    return {
        "flow_version": int((current.meta or {}).get("current_version") or 0),
        "facts": compact_model_payload(
            _semantic_fact_snapshot(current),
            max_depth=8,
            max_items=80,
            max_string=500,
            list_keep="tail",
        ),
        "current_contract": compact_model_payload(
            _semantic_mutable_context(current), max_depth=6, max_items=40, max_string=500,
        ),
        "validation": compact_model_payload(report, max_depth=6, max_items=40, max_string=500),
        "projection": {
            "bounded": True,
            "note": "Large collections and payload branches include explicit __truncated_* markers.",
        },
    }


_PENDING_FLOW_SPEC_HELPERS = ('_semantic_fact_snapshot', '_semantic_mutable_context', '_sync_capability_io_schemas', 'refresh_review_items', 'validate_flow_spec',)


def _bind_flow_spec_helpers() -> None:
    import dano.execution.page.flow_spec as _flow_spec
    module_globals = globals()
    for name in _PENDING_FLOW_SPEC_HELPERS:
        if hasattr(_flow_spec, name):
            module_globals[name] = getattr(_flow_spec, name)
