"""Stable, revision-bound review issues for the recording workbench.

Semantic observations are deliberately advisory.  They can help a user improve a
draft, but they are never allowed to masquerade as an executability failure.  A
``ContractFault`` is produced only by deterministic contract checks.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Iterable, Literal

IssueKind = Literal["advisory", "contract_fault"]


def issue_fingerprint(
    *, kind: IssueKind, code: str, target: dict[str, Any], details: Any = None,
) -> str:
    canonical = json.dumps(
        {"kind": kind, "code": code, "target": target, "details": details},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def make_issue(
    *,
    kind: IssueKind,
    code: str,
    message: str,
    revision: int,
    target: dict[str, Any] | None = None,
    evidence_ids: Iterable[str] = (),
    details: Any = None,
    severity: str | None = None,
) -> dict[str, Any]:
    target = deepcopy(target or {"kind": "flow"})
    fingerprint = issue_fingerprint(kind=kind, code=code, target=target, details=details)
    issue_id = f"{kind}:{fingerprint.removeprefix('sha256:')[:20]}"
    issue = {
        "id": issue_id,
        "issue_id": issue_id,
        "kind": kind,
        "type": kind,
        "code": code,
        "title": code.replace("_", " "),
        "message": message,
        "reason": message,
        "severity": severity or ("high" if kind == "contract_fault" else "medium"),
        "revision": int(revision),
        "fingerprint": fingerprint,
        "target": target,
        "evidence_ids": list(dict.fromkeys(str(value) for value in evidence_ids if value)),
        "resolved": False,
    }
    for key in ("field_uuid", "step_uuid", "capability_uuid"):
        if target.get(key):
            issue[key] = str(target[key])
    return issue


def reviewer_advisories(
    reviews: Iterable[dict[str, Any]], *, revision: int,
) -> list[dict[str, Any]]:
    """Project model review findings to non-blocking, locatable advisories."""

    result: list[dict[str, Any]] = []
    for review in reviews:
        role = str(review.get("role") or "reviewer")
        reasons = [str(value) for value in review.get("reasons") or [] if str(value).strip()]
        if review.get("passed") and not reasons:
            continue
        if not reasons:
            reasons = [f"{role} reviewer requested a semantic follow-up"]
        for reason in reasons:
            result.append(make_issue(
                kind="advisory",
                code=f"pi_{role}_advice",
                message=reason,
                revision=revision,
                target={"kind": "flow", "review_role": role},
                evidence_ids=review.get("evidence") or (),
                details={"role": role, "reason": reason},
            ))
    return result


def apply_ignored_advisories(
    advisories: Iterable[dict[str, Any]], *, ignored_fingerprints: Iterable[str],
) -> list[dict[str, Any]]:
    ignored = {str(value) for value in ignored_fingerprints}
    result: list[dict[str, Any]] = []
    for raw in advisories:
        item = deepcopy(raw)
        item["ignored"] = str(item.get("fingerprint") or "") in ignored
        result.append(item)
    return result


def build_review_report(
    *,
    revision: int,
    contract_faults: Iterable[dict[str, Any]],
    advisories: Iterable[dict[str, Any]],
    ignored_fingerprints: Iterable[str] = (),
) -> dict[str, Any]:
    faults = [deepcopy(value) for value in contract_faults]
    advice = apply_ignored_advisories(
        advisories, ignored_fingerprints=ignored_fingerprints,
    )
    visible = [value for value in advice if not value.get("ignored")]
    verified = not faults
    return {
        "revision": int(revision),
        "executability_status": "verified" if verified else "unverified",
        "publication_status": "draft",
        "direct_call_enabled": verified,
        "contract_faults": faults,
        "advisories": advice,
        "visible_advisories": visible,
        "contract_fault_count": len(faults),
        "advisory_count": len(visible),
    }
