"""Pure FlowSpec version metadata helpers."""
from __future__ import annotations

from datetime import datetime, timezone
from dano.execution.page.flow_spec_core.models import (
    FlowSpec,
)
from dano.execution.page.flow_spec_core.fingerprints import (
    _flow_fingerprint,
)


def append_flow_version(
    spec: FlowSpec,
    action: str,
    *,
    reason: str = "",
    actor: str = "system",
) -> FlowSpec:
    """在 FlowSpec.meta 中追加轻量版本记录。"""
    sync_flow_spec_models(spec)
    meta = dict(spec.meta or {})
    versions = list(meta.get("versions") or [])
    current = max(
        [int(meta.get("current_version") or 0)]
        + [int(v.get("version") or 0) for v in versions]
    )
    entry = {
        "version": current + 1,
        "action": action,
        "reason": reason,
        "actor": actor,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "fingerprint": _flow_fingerprint(spec),
        "summary": {
            "steps": len(spec.steps),
            "links": len(spec.links),
            "capabilities": len(spec.capabilities or []),
            "user_params": len(flow_spec_user_params(spec)),
            "review_items": len(spec.review_items),
            "risk_level": spec.risk_level,
        },
    }
    versions.append(entry)
    meta["versions"] = versions[-30:]
    meta["current_version"] = entry["version"]
    spec.meta = meta
    return spec


def ensure_flow_version(spec: FlowSpec, action: str, *, reason: str = "") -> FlowSpec:
    if spec.meta.get("versions"):
        return spec
    return append_flow_version(spec, action, reason=reason)


_PENDING_FLOW_SPEC_HELPERS = ('flow_spec_user_params', 'sync_flow_spec_models',)


def _bind_flow_spec_helpers() -> None:
    import sys
    _flow_spec = sys.modules.get("dano.execution.page.flow_spec")
    if _flow_spec is None or not hasattr(_flow_spec, "to_flow_spec"):
        return
    module_globals = globals()
    for name in _PENDING_FLOW_SPEC_HELPERS:
        if hasattr(_flow_spec, name):
            module_globals[name] = getattr(_flow_spec, name)
