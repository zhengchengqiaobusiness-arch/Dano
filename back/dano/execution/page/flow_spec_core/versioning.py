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
            "user_params": len({
                str(param.key or param.path)
                for step in spec.steps
                for param in (step.params or [])
                if param.category == "user_param" and param.exposed_to_user
            }),
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
