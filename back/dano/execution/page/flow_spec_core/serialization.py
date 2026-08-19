"""Pure FlowSpec JSON conversion helpers."""
from __future__ import annotations

from typing import Any

from dano.execution.page.flow_spec_core.models import FlowSpec


def flow_spec_release_payload(spec: FlowSpec) -> dict[str, Any]:
    """Return the canonical RequestFacts-only release representation."""
    payload = spec.model_dump(mode="json", exclude_none=True)
    meta = dict(payload.get("meta") or {})
    meta.pop("request_graph", None)
    payload["meta"] = meta
    return payload
