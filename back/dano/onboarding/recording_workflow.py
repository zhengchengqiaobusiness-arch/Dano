"""Single authoritative state model for the recording workflow.

The gateway and the browser UI observe this module through ``WorkflowSnapshot``.
Implementation details such as Pi prompts, verification rounds and publishing are
represented by ``progress.step`` rather than separate externally visible states.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class WorkflowStatus(StrEnum):
    IDLE = "idle"
    RECORDING = "recording"
    PROCESSING = "processing"
    WAITING_OPERATOR = "waiting_operator"
    EDITABLE = "editable"
    PUBLISHED = "published"
    CANCELLED = "cancelled"
    FAILED = "failed"


class WorkflowStep(StrEnum):
    READY = "ready"
    CAPTURING = "capturing"
    FREEZING = "freezing"
    MATERIALIZING = "materializing"
    ANALYZING = "analyzing"
    RESOLVING = "resolving"
    COMPILING = "compiling"
    VERIFYING = "verifying"
    REVIEWING = "reviewing"
    PUBLISHING = "publishing"
    EXPORTING = "exporting"
    COMPLETE = "complete"


class WorkflowProgress(BaseModel):
    step: WorkflowStep = WorkflowStep.READY
    label: str = ""
    round: int = Field(default=0, ge=0)
    request_count: int = Field(default=0, ge=0)


class WorkflowIssue(BaseModel):
    issue_id: str
    code: str
    message: str
    severity: str = "blocking"
    resolver: str = "external_blocked"
    target: dict[str, str] = Field(default_factory=dict)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    allowed_operations: list[str] = Field(default_factory=list)


class WorkflowQuestion(BaseModel):
    question_id: str
    issue_id: str
    text: str
    options: list[str] = Field(default_factory=list)
    context_ref: str = ""


class WorkflowSnapshot(BaseModel):
    run_id: str
    action: str
    revision: int = Field(default=0, ge=0)
    status: WorkflowStatus = WorkflowStatus.IDLE
    progress: WorkflowProgress = Field(default_factory=WorkflowProgress)
    capture_frozen: bool = False
    draft: dict[str, Any] | None = None
    issues: list[WorkflowIssue] = Field(default_factory=list)
    question: WorkflowQuestion | None = None
    release: dict[str, Any] | None = None
    error: str = ""


CANONICAL_RECORDING_COMMANDS = frozenset({
    "start", "input", "finish", "patch_draft", "republish", "answer", "cancel", "ping",
})


_ALLOWED_TRANSITIONS: dict[WorkflowStatus, frozenset[WorkflowStatus]] = {
    WorkflowStatus.IDLE: frozenset({WorkflowStatus.RECORDING, WorkflowStatus.FAILED}),
    WorkflowStatus.RECORDING: frozenset({
        WorkflowStatus.PROCESSING, WorkflowStatus.CANCELLED, WorkflowStatus.FAILED,
    }),
    WorkflowStatus.PROCESSING: frozenset({
        WorkflowStatus.WAITING_OPERATOR, WorkflowStatus.EDITABLE,
        WorkflowStatus.PUBLISHED, WorkflowStatus.CANCELLED, WorkflowStatus.FAILED,
    }),
    WorkflowStatus.WAITING_OPERATOR: frozenset({
        WorkflowStatus.PROCESSING, WorkflowStatus.CANCELLED, WorkflowStatus.FAILED,
    }),
    WorkflowStatus.EDITABLE: frozenset({
        WorkflowStatus.PROCESSING, WorkflowStatus.CANCELLED, WorkflowStatus.FAILED,
    }),
    WorkflowStatus.PUBLISHED: frozenset({
        WorkflowStatus.EDITABLE, WorkflowStatus.PROCESSING, WorkflowStatus.FAILED,
    }),
    WorkflowStatus.CANCELLED: frozenset({
        WorkflowStatus.PROCESSING, WorkflowStatus.FAILED,
    }),
    WorkflowStatus.FAILED: frozenset({
        WorkflowStatus.PROCESSING, WorkflowStatus.CANCELLED,
    }),
}


def transition_snapshot(
    snapshot: WorkflowSnapshot,
    status: WorkflowStatus,
    *,
    progress: WorkflowProgress | None = None,
    **changes: Any,
) -> WorkflowSnapshot:
    """Return the next authoritative snapshot or reject an impossible transition."""

    if status != snapshot.status and status not in _ALLOWED_TRANSITIONS[snapshot.status]:
        raise ValueError(f"invalid recording workflow transition: {snapshot.status} -> {status}")
    payload = {
        **snapshot.model_dump(mode="python"),
        **changes,
        "status": status,
        "revision": snapshot.revision + 1,
    }
    if progress is not None:
        payload["progress"] = progress
    if status == WorkflowStatus.WAITING_OPERATOR and not payload.get("question"):
        raise ValueError("waiting_operator requires a question")
    if status == WorkflowStatus.PUBLISHED and not payload.get("release"):
        raise ValueError("published requires a release")
    if status in {
        WorkflowStatus.PROCESSING,
        WorkflowStatus.WAITING_OPERATOR,
        WorkflowStatus.EDITABLE,
        WorkflowStatus.PUBLISHED,
    }:
        payload["capture_frozen"] = True
    if status != WorkflowStatus.WAITING_OPERATOR:
        payload["question"] = None
    return WorkflowSnapshot.model_validate(payload)

