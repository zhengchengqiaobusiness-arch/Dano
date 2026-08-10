"""Executor-owned verification evidence.

Only execution paths import :func:`record_verification`. Agent submissions can
look records up, but cannot choose or manufacture their identifiers.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import re
from threading import RLock
from uuid import uuid4


VERIFICATION_KINDS = frozenset({
    "replay_read",
    "perturb_link",
    "write_execute",
    "verify_read",
    "enum_snapshot",
})

_LOCK = RLock()
_RECORDS: dict[str, dict] = {}
_SECRET_HINTS = ("authorization", "cookie", "token", "jwt", "password", "secret", "session", "credential")
_INLINE_SECRET_RE = re.compile(
    r"(?i)\b(Bearer|Basic|Token)\s+[A-Za-z0-9._~+/-]{8,}|\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
)


def _sanitize(node, key_hint: str = ""):  # noqa: ANN001, ANN202
    if isinstance(node, dict):
        return {key: _sanitize(value, str(key)) for key, value in node.items()}
    if isinstance(node, list):
        return [_sanitize(value, key_hint) for value in node]
    if any(hint in key_hint.casefold() for hint in _SECRET_HINTS):
        return "***"
    if isinstance(node, str):
        return _INLINE_SECRET_RE.sub("***", node)
    return deepcopy(node)


def record_verification(*, kind: str, subject: dict, evidence: dict) -> str:
    """Persist executor-generated evidence and return its unguessable id."""
    if kind not in VERIFICATION_KINDS:
        raise ValueError(f"unsupported verification kind: {kind}")
    if not isinstance(subject, dict) or not isinstance(evidence, dict):
        raise TypeError("verification subject and evidence must be objects")
    verification_id = str(uuid4())
    record = {
        "verification_id": verification_id,
        "kind": kind,
        "subject": _sanitize(subject),
        "evidence": _sanitize(evidence),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with _LOCK:
        _RECORDS[verification_id] = record
    return verification_id


def get_verification(verification_id: str) -> dict | None:
    """Return a defensive copy of one executor-owned verification record."""
    with _LOCK:
        record = _RECORDS.get(str(verification_id or ""))
        return deepcopy(record) if record is not None else None


def find_verification(verification_id: str, verification_log: list[dict] | None = None) -> dict | None:
    """Resolve live evidence first, then a trusted persisted FlowSpec log."""
    record = get_verification(verification_id)
    if record is not None:
        return record
    target = str(verification_id or "")
    for item in verification_log or []:
        if isinstance(item, dict) and str(item.get("verification_id") or "") == target:
            return deepcopy(item)
    return None


def _clear_verifications_for_tests() -> None:
    with _LOCK:
        _RECORDS.clear()
