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

    current = _sync_capability_io_schemas(spec.model_copy(deep=True))
    # A state poll is a projection of the current draft, not a release build.
    # Release preparation is intentionally reserved for mutation/finalization.
    report = validate_flow_spec(current, _prepared=True, _projection=True)
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
            _semantic_mutable_context(current, validation=report),
            max_depth=6,
            max_items=40,
            max_string=500,
        ),
        "validation": compact_model_payload(report, max_depth=6, max_items=40, max_string=500),
        "projection": {
            "bounded": True,
            "note": "Large collections and payload branches include explicit __truncated_* markers.",
        },
    }

_PENDING_FLOW_SPEC_HELPERS = {'_semantic_fact_snapshot': 'dano.execution.page.recording_agent_contract', '_semantic_mutable_context': 'dano.execution.page.capability_semantic', '_sync_capability_io_schemas': 'dano.execution.page.capability_io', 'validate_flow_spec': 'dano.execution.page.flow_spec_validate'}


def _bind_flow_spec_helpers() -> None:
    import sys
    module_globals = globals()
    for name, owner in _PENDING_FLOW_SPEC_HELPERS.items():
        mod = sys.modules.get(owner)
        if mod is None or not hasattr(mod, name):
            continue
        module_globals[name] = getattr(mod, name)


_bind_flow_spec_helpers()
