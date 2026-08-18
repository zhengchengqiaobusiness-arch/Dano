"""Stable public identities for catalog skills and capabilities."""

from __future__ import annotations

import hashlib
import re

_SESSION_ACTION_RE = re.compile(r"^(?:action|sk)[_-]?[0-9a-f]{8,}$", re.I)
_GENERIC_SLUGS = {
    "skill", "action", "submit", "query", "capability", "ability",
    "query_status", "submit_batch", "list_options",
}


def is_generated_action_id(value: str) -> bool:
    """True when a string is a recording session token, not a business name."""
    return bool(_SESSION_ACTION_RE.fullmatch(str(value or "").strip()))


def public_skill_action(title: str, session_action: str) -> str:
    """Publish a readable action while keeping the recording session token private.

    Recording websockets still use ``action_{32hex}``. Published skill_id should
    be ``{subsystem}.{sk_<hash>}`` or ``{subsystem}.{title_slug}_{hash}``.
    """
    session = str(session_action or "").strip() or "session"
    if not is_generated_action_id(session):
        return session
    digest = hashlib.sha256(session.encode("utf-8")).hexdigest()[:12]
    slug = re.sub(r"[^a-z0-9]+", "_", str(title or "").casefold()).strip("_")
    if (
        slug
        and len(slug) >= 2
        and slug not in _GENERIC_SLUGS
        and not is_generated_action_id(slug)
        and not re.fullmatch(r"[0-9a-f]{8,}", slug)
    ):
        return f"{slug[:40]}_{digest}"
    return f"sk_{digest}"


def public_capability_id(cap: dict) -> str:
    """Expose a unique capability id instead of a 12-hex or kind-only token."""
    current = str(cap.get("capability_id") or "").strip()
    if current.startswith("cap_") and len(current) >= 20:
        return current
    raw = "|".join((
        str(cap.get("name") or ""),
        str(cap.get("kind") or ""),
        ",".join(str(item) for item in (cap.get("step_ids") or [])),
        str(cap.get("title") or ""),
    ))
    return f"cap_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"
