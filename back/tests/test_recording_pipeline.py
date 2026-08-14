from __future__ import annotations

import pytest

from dano.onboarding.recording_pipeline import (
    CanonicalRecordingRuntime,
    RecordingPipelineServices,
)
from dano.onboarding.recording_workflow import (
    PipelineContext,
    PipelineSeed,
    SelfHealingPipeline,
    WorkflowIssue,
    WorkflowStatus,
)


async def _noop_progress(*_args) -> None:  # noqa: ANN002
    return None


async def _no_answer(_question) -> str:  # noqa: ANN001
    raise AssertionError("operator must not be asked")


def _context() -> PipelineContext:
    return PipelineContext(
        progress=_noop_progress,
        ask_operator=_no_answer,
        cancelled=lambda: False,
    )


@pytest.mark.asyncio
async def test_first_publication_consumes_live_notebook_once() -> None:
    calls: list[tuple[str, bool]] = []

    async def materialize(use_live, _context):  # noqa: ANN001
        calls.append(("materialize", use_live))
        return {"capabilities": []}

    async def plan(draft, use_live, _context):  # noqa: ANN001
        calls.append(("plan", use_live))
        return {**draft, "capabilities": ["query", "write"]}

    async def clean(draft, _context):  # noqa: ANN001
        return draft, ()

    async def repair(*_args):  # noqa: ANN002
        raise AssertionError("repair must not run")

    async def publish(draft, _context):  # noqa: ANN001
        return {"capability_count": len(draft["capabilities"])}

    runtime = CanonicalRecordingRuntime(RecordingPipelineServices(
        materialize_recording=materialize,
        plan_capabilities=plan,
        verify=clean,
        review=clean,
        repair=repair,
        publish=publish,
    ))
    outcome = await SelfHealingPipeline(runtime).run(
        PipelineSeed(kind="recording", use_live_notebook=True),
        _context(),
    )

    assert outcome.status == WorkflowStatus.PUBLISHED
    assert outcome.release == {"capability_count": 2}
    assert calls == [("materialize", True), ("plan", True)]


@pytest.mark.asyncio
async def test_republish_uses_edited_draft_without_live_notebook() -> None:
    calls: list[tuple[str, bool]] = []

    async def materialize(*_args):  # noqa: ANN002
        raise AssertionError("republish must not rematerialize recording facts")

    async def plan(draft, use_live, _context):  # noqa: ANN001
        calls.append(("plan", use_live))
        return draft

    async def clean(draft, _context):  # noqa: ANN001
        return draft, ()

    async def repair(*_args):  # noqa: ANN002
        raise AssertionError("repair must not run")

    async def publish(_draft, _context):  # noqa: ANN001
        return {"skill_id": "skill"}

    runtime = CanonicalRecordingRuntime(RecordingPipelineServices(
        materialize_recording=materialize,
        plan_capabilities=plan,
        verify=clean,
        review=clean,
        repair=repair,
        publish=publish,
    ))
    outcome = await SelfHealingPipeline(runtime).run(
        PipelineSeed(
            kind="edited_spec",
            draft={"capabilities": ["query"]},
            use_live_notebook=False,
        ),
        _context(),
    )

    assert outcome.status == WorkflowStatus.PUBLISHED
    assert calls == [("plan", False)]


@pytest.mark.asyncio
async def test_review_blocker_returns_to_same_repair_loop_before_atomic_publish() -> None:
    review_issue = WorkflowIssue(
        issue_id="review-1",
        code="final_review_rejected",
        message="能力关系缺少验证",
        resolver="machine_repair",
    )
    events: list[str] = []

    async def materialize(_use_live, _context):  # noqa: ANN001
        return {"review_fixed": False}

    async def plan(draft, _use_live, _context):  # noqa: ANN001
        events.append("plan")
        return draft

    async def verify(draft, _context):  # noqa: ANN001
        events.append("verify")
        return draft, ()

    async def review(draft, _context):  # noqa: ANN001
        events.append("review")
        return draft, (() if draft["review_fixed"] else (review_issue,))

    async def repair(draft, issues, _answers, _context):  # noqa: ANN001
        events.append("repair")
        assert issues == (review_issue,)
        return {**draft, "review_fixed": True}

    async def publish(_draft, _context):  # noqa: ANN001
        events.append("publish")
        return {"published": True}

    runtime = CanonicalRecordingRuntime(RecordingPipelineServices(
        materialize_recording=materialize,
        plan_capabilities=plan,
        verify=verify,
        review=review,
        repair=repair,
        publish=publish,
    ))
    outcome = await SelfHealingPipeline(runtime).run(
        PipelineSeed(kind="recording", use_live_notebook=True),
        _context(),
    )

    assert outcome.status == WorkflowStatus.PUBLISHED
    assert events == ["plan", "verify", "review", "repair", "verify", "review", "publish"]


@pytest.mark.asyncio
async def test_any_capability_issue_prevents_partial_publish() -> None:
    issue = WorkflowIssue(
        issue_id="write-required",
        code="required_unconfirmed",
        message="写能力必填性未确认",
        resolver="external_blocked",
        target={"capability_id": "write"},
    )
    published = False

    async def materialize(_use_live, _context):  # noqa: ANN001
        return {"capabilities": ["query", "write"]}

    async def plan(draft, _use_live, _context):  # noqa: ANN001
        return draft

    async def verify(draft, _context):  # noqa: ANN001
        return draft, (issue,)

    async def review(draft, _context):  # noqa: ANN001
        return draft, ()

    async def repair(draft, _issues, _answers, _context):  # noqa: ANN001
        return draft

    async def publish(_draft, _context):  # noqa: ANN001
        nonlocal published
        published = True
        return {}

    runtime = CanonicalRecordingRuntime(RecordingPipelineServices(
        materialize_recording=materialize,
        plan_capabilities=plan,
        verify=verify,
        review=review,
        repair=repair,
        publish=publish,
    ))
    outcome = await SelfHealingPipeline(runtime).run(
        PipelineSeed(kind="recording", use_live_notebook=True),
        _context(),
    )

    assert outcome.status == WorkflowStatus.EDITABLE
    assert outcome.issues == (issue,)
    assert published is False
