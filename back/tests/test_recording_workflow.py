from __future__ import annotations

import pytest

from dano.onboarding.recording_workflow import (
    CANONICAL_RECORDING_COMMANDS,
    WorkflowProgress,
    WorkflowQuestion,
    WorkflowSnapshot,
    WorkflowStatus,
    WorkflowStep,
    transition_snapshot,
)


def _snapshot() -> WorkflowSnapshot:
    return WorkflowSnapshot(run_id="run-1", action="action_1")


def test_recording_workflow_has_one_small_command_surface() -> None:
    assert CANONICAL_RECORDING_COMMANDS == {
        "start", "input", "finish", "patch_draft", "republish", "answer", "cancel", "ping",
    }


def test_recording_workflow_reaches_publish_through_authoritative_snapshots() -> None:
    current = transition_snapshot(_snapshot(), WorkflowStatus.RECORDING)
    current = transition_snapshot(
        current,
        WorkflowStatus.PROCESSING,
        progress=WorkflowProgress(step=WorkflowStep.FREEZING, label="冻结录制事实"),
    )
    current = transition_snapshot(
        current,
        WorkflowStatus.PUBLISHED,
        progress=WorkflowProgress(step=WorkflowStep.COMPLETE, label="发布完成"),
        release={"skill_id": "skill-1"},
    )

    assert current.revision == 3
    assert current.capture_frozen is True
    assert current.status == WorkflowStatus.PUBLISHED
    assert current.release == {"skill_id": "skill-1"}


def test_recording_workflow_waits_for_operator_and_resumes_same_run() -> None:
    current = transition_snapshot(_snapshot(), WorkflowStatus.RECORDING)
    current = transition_snapshot(current, WorkflowStatus.PROCESSING)
    current = transition_snapshot(
        current,
        WorkflowStatus.WAITING_OPERATOR,
        question=WorkflowQuestion(
            question_id="q1", issue_id="i1", text="请选择审批策略",
        ),
    )
    resumed = transition_snapshot(current, WorkflowStatus.PROCESSING)

    assert resumed.run_id == current.run_id
    assert resumed.question is None
    assert resumed.revision == current.revision + 1


def test_recording_workflow_republish_does_not_require_recording_state() -> None:
    current = transition_snapshot(_snapshot(), WorkflowStatus.RECORDING)
    current = transition_snapshot(current, WorkflowStatus.PROCESSING)
    current = transition_snapshot(current, WorkflowStatus.EDITABLE, draft={"flow_id": "draft"})
    republishing = transition_snapshot(
        current,
        WorkflowStatus.PROCESSING,
        progress=WorkflowProgress(step=WorkflowStep.VERIFYING),
    )

    assert republishing.draft == {"flow_id": "draft"}
    assert republishing.capture_frozen is True


def test_recording_workflow_rejects_impossible_or_incomplete_states() -> None:
    with pytest.raises(ValueError, match="invalid recording workflow transition"):
        transition_snapshot(_snapshot(), WorkflowStatus.PUBLISHED, release={"skill_id": "x"})
    recording = transition_snapshot(_snapshot(), WorkflowStatus.RECORDING)
    processing = transition_snapshot(recording, WorkflowStatus.PROCESSING)
    with pytest.raises(ValueError, match="requires a question"):
        transition_snapshot(processing, WorkflowStatus.WAITING_OPERATOR)
    with pytest.raises(ValueError, match="requires a release"):
        transition_snapshot(processing, WorkflowStatus.PUBLISHED)

