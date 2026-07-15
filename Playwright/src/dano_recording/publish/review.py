"""Bind Pi review submissions to one frozen revision and content hash."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass(slots=True)
class ReviewCollector:
    values: dict[tuple[str, int, str], dict[str, Any]] = field(default_factory=dict)
    active_hashes: dict[tuple[str, int], str] = field(default_factory=dict)
    active_snapshot_hashes: dict[tuple[str, int], str] = field(default_factory=dict)
    server_reviewer_identities: dict[tuple[str, str], dict[str, str]] = field(default_factory=dict)

    def register_server_sessions(
        self,
        recording_id: str,
        sessions: dict[str, str],
        *,
        model_id: str,
    ) -> None:
        for role in ("acceptance", "security", "compliance"):
            session_id = str(sessions.get(role) or "")
            if session_id:
                self.server_reviewer_identities[(recording_id, role)] = {
                    "pi_session_id": session_id,
                    "model_id": str(model_id or "recording-pi"),
                }

    def ensure_server_sessions(self, recording_id: str) -> dict[str, dict[str, str]]:
        """Return three server-owned identities even while Pi is unavailable."""

        result: dict[str, dict[str, str]] = {}
        for role in ("acceptance", "security", "compliance"):
            key = (recording_id, role)
            identity = self.server_reviewer_identities.get(key)
            if identity is None:
                identity = {
                    "pi_session_id": f"unavailable-{role}-{uuid4().hex}",
                    "model_id": "recording-pi-unavailable",
                }
                self.server_reviewer_identities[key] = identity
            result[role] = dict(identity)
        return result

    def begin(
        self, recording_id: str, revision: int, content_hash: str,
        snapshot_hash: str | None = None,
    ) -> None:
        self.active_hashes[(recording_id, revision)] = content_hash
        self.active_snapshot_hashes[(recording_id, revision)] = snapshot_hash or content_hash
        for role in ("acceptance", "security", "compliance"):
            self.values.pop((recording_id, revision, role), None)

    def abort(self, recording_id: str, revision: int) -> None:
        self.active_hashes.pop((recording_id, revision), None)
        self.active_snapshot_hashes.pop((recording_id, revision), None)

    def submit_active(
        self,
        *,
        recording_id: str,
        revision: int,
        role: str,
        verdict: dict[str, Any],
    ) -> dict[str, Any]:
        content_hash = self.active_hashes.get((recording_id, revision))
        if not content_hash:
            raise ValueError("no frozen release candidate is awaiting review")
        return self.submit(
            recording_id=recording_id,
            revision=revision,
            content_hash=content_hash,
            snapshot_hash=self.active_snapshot_hashes.get((recording_id, revision), content_hash),
            role=role,
            verdict=verdict,
        )

    def submit(
        self,
        *,
        recording_id: str,
        revision: int,
        content_hash: str,
        snapshot_hash: str | None = None,
        role: str,
        verdict: dict[str, Any],
    ) -> dict[str, Any]:
        if role not in {"acceptance", "security", "compliance"}:
            raise ValueError("invalid reviewer role")
        pi_session_id = str(verdict.get("pi_session_id") or "")
        if not pi_session_id:
            raise ValueError("review verdict lacks its isolated Pi session identity")
        item = {
            "role": role,
            "revision": revision,
            "content_hash": content_hash,
            "snapshot_hash": snapshot_hash or content_hash,
            # Pi review is advisory, but its recorded meaning must still be
            # fail-closed: only the JSON boolean true is a passing verdict.
            # Strings and integers must never acquire Python truthiness here.
            "passed": verdict.get("passed") is True,
            "reasons": [str(value) for value in verdict.get("reasons") or []],
            "evidence": [str(value) for value in verdict.get("evidence") or []],
            "pi_session_id": pi_session_id,
            # The coordinator injects this from sidecar configuration; model-authored
            # ``model_id`` input is intentionally ignored.
            "model_id": str(verdict.get("_server_model_id") or "recording-pi"),
            "unavailable": bool(verdict.get("unavailable")),
        }
        self.values[(recording_id, revision, role)] = item
        return item

    def submit_unavailable(
        self,
        *,
        recording_id: str,
        revision: int,
        reason: str,
    ) -> list[dict[str, Any]]:
        """Fill missing reviewer rows with server-owned unavailable advice."""

        identities = self.ensure_server_sessions(recording_id)
        submitted: list[dict[str, Any]] = []
        for role in ("acceptance", "security", "compliance"):
            if self.values.get((recording_id, revision, role)) is not None:
                continue
            identity = identities[role]
            submitted.append(self.submit_active(
                recording_id=recording_id,
                revision=revision,
                role=role,
                verdict={
                    "passed": False,
                    "reasons": [f"Pi {role} reviewer unavailable: {reason}"],
                    "evidence": [],
                    "pi_session_id": identity["pi_session_id"],
                    "_server_model_id": identity["model_id"],
                    "unavailable": True,
                },
            ))
        return submitted

    def collect(
        self, recording_id: str, revision: int, content_hash: str,
        snapshot_hash: str | None = None,
    ) -> list[dict[str, Any]]:
        values = [self.values.get((recording_id, revision, role)) for role in (
            "acceptance", "security", "compliance"
        )]
        if any(item is None for item in values):
            raise ValueError("not all isolated Pi reviewers submitted a verdict")
        if any(item["content_hash"] != content_hash for item in values if item):
            raise ValueError("review verdict is bound to a different content hash")
        expected_snapshot = snapshot_hash or self.active_snapshot_hashes.get(
            (recording_id, revision), content_hash
        )
        if any(item["snapshot_hash"] != expected_snapshot for item in values if item):
            raise ValueError("review verdict is bound to a different revision snapshot hash")
        session_ids = {str(item["pi_session_id"]) for item in values if item}
        if len(session_ids) != 3:
            raise ValueError("Pi reviewers must use three isolated sessions")
        self.active_hashes.pop((recording_id, revision), None)
        self.active_snapshot_hashes.pop((recording_id, revision), None)
        return [item for item in values if item]
