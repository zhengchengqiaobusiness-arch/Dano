from __future__ import annotations

import asyncio

import pytest

from dano.onboarding.recording_workflow import (
    CANONICAL_RECORDING_COMMANDS,
    PipelineCheck,
    PipelineContext,
    PipelineOutcome,
    PipelineSeed,
    RecordingWorkflow,
    SelfHealingPipeline,
    WorkflowActivity,
    WorkflowIssue,
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


@pytest.mark.asyncio
async def test_processing_and_terminal_snapshots_preserve_captured_request_count() -> None:
    class FailingPipeline:
        async def run(self, seed, context):  # noqa: ANN001
            await context.progress(WorkflowStep.VERIFYING, "正在验证", 1)
            return PipelineOutcome(
                status=WorkflowStatus.FAILED,
                draft={"capabilities": []},
                error="处理超时",
            )

    workflow = RecordingWorkflow(_snapshot(), FailingPipeline())
    await workflow.start()
    await workflow.update_recording(request_count=91)
    await workflow.finish()
    result = await workflow.wait()

    assert result.status == WorkflowStatus.FAILED
    assert result.progress.request_count == 91


@pytest.mark.asyncio
async def test_live_insight_checkpoint_does_not_regress_processing_progress() -> None:
    class Pipeline:
        async def run(self, seed, context):  # noqa: ANN001
            return PipelineOutcome(status=WorkflowStatus.EDITABLE, draft={})

    current = transition_snapshot(
        transition_snapshot(_snapshot(), WorkflowStatus.RECORDING),
        WorkflowStatus.PROCESSING,
        progress=WorkflowProgress(
            step=WorkflowStep.MATERIALIZING,
            label="正在生成能力草稿",
            request_count=105,
        ),
    )
    workflow = RecordingWorkflow(current, Pipeline())

    result = await workflow.update_live_insights([
        {"kind": "param_source", "text": "已识别字段来源"},
    ])

    assert result.status == WorkflowStatus.PROCESSING
    assert result.progress.step == WorkflowStep.MATERIALIZING
    assert result.progress.request_count == 105
    assert result.insights == [
        {"kind": "param_source", "text": "已识别字段来源"},
    ]


@pytest.mark.asyncio
async def test_unexpected_publish_failure_preserves_latest_authoritative_draft() -> None:
    class FailingPublishPipeline:
        async def run(self, seed, context):  # noqa: ANN001
            context.remember_draft({
                "steps": [{"step_id": "query"}, {"step_id": "submit"}],
                "capabilities": [{"name": "query_records"}, {"name": "submit_record"}],
            })
            raise RuntimeError("publish adapter rejected the candidate")

    workflow = RecordingWorkflow(_snapshot(), FailingPublishPipeline())
    await workflow.start()
    await workflow.finish()
    result = await workflow.wait()

    assert result.status == WorkflowStatus.FAILED
    assert result.error == "publish adapter rejected the candidate"
    assert len(result.draft["steps"]) == 2
    assert len(result.draft["capabilities"]) == 2


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


class _ImmediatePipeline:
    def __init__(self) -> None:
        self.seeds: list[PipelineSeed] = []

    async def run(self, seed, context):  # noqa: ANN001
        self.seeds.append(seed)
        await context.progress(WorkflowStep.ANALYZING, "分析")
        return PipelineOutcome(
            status=WorkflowStatus.PUBLISHED,
            draft={"flow_id": "flow"},
            release={"skill_id": "skill"},
        )


@pytest.mark.asyncio
async def test_recording_workflow_finish_is_one_idempotent_task() -> None:
    release = asyncio.Event()

    class SlowPipeline:
        calls = 0

        async def run(self, seed, context):  # noqa: ANN001
            self.calls += 1
            await release.wait()
            return PipelineOutcome(
                status=WorkflowStatus.PUBLISHED,
                draft={"flow_id": "flow"},
                release={"skill_id": "skill"},
            )

    pipeline = SlowPipeline()
    workflow = RecordingWorkflow(_snapshot(), pipeline)
    await workflow.start()
    await workflow.finish()
    await workflow.finish()
    assert pipeline.calls == 0
    await asyncio.sleep(0)
    assert pipeline.calls == 1
    release.set()
    result = await workflow.wait()
    assert result.status == WorkflowStatus.PUBLISHED
    assert pipeline.calls == 1


@pytest.mark.asyncio
async def test_recording_workflow_persists_structured_resolution_activity() -> None:
    issue = WorkflowIssue(
        issue_id="dependency:1",
        code="dependency",
        message="验证上游响应是否能驱动后续请求",
        resolver="collect_evidence",
        target={"dependency_id": "dep-1"},
        allowed_operations=["perturb_replay"],
    )

    class ActivityPipeline:
        async def run(self, seed, context):  # noqa: ANN001
            await context.record(WorkflowActivity(
                step=WorkflowStep.RESOLVING,
                round=1,
                status="running",
                label="正在补充依赖验证证据",
                issue_id=issue.issue_id,
                code=issue.code,
                target=issue.target,
            ))
            await context.record(WorkflowActivity(
                step=WorkflowStep.VERIFYING,
                round=2,
                status="resolved",
                label="依赖验证已通过",
                issue_id=issue.issue_id,
                code=issue.code,
                target=issue.target,
            ))
            return PipelineOutcome(
                status=WorkflowStatus.PUBLISHED,
                draft={"flow_id": "flow"},
                release={"skill_id": "skill"},
            )

    workflow = RecordingWorkflow(_snapshot(), ActivityPipeline())
    await workflow.start()
    await workflow.finish()
    result = await workflow.wait()

    assert [item.status for item in result.activity] == ["running", "resolved"]
    assert [item.sequence for item in result.activity] == [1, 2]
    assert result.activity[-1].target == {"dependency_id": "dep-1"}


@pytest.mark.asyncio
async def test_recording_workflow_operator_answer_resumes_same_task() -> None:
    class QuestionPipeline:
        async def run(self, seed, context):  # noqa: ANN001
            answer = await context.ask_operator(WorkflowQuestion(
                question_id="q1", issue_id="i1", text="请选择",
            ))
            return PipelineOutcome(
                status=WorkflowStatus.PUBLISHED,
                draft={"answer": answer},
                release={"skill_id": "skill"},
            )

    workflow = RecordingWorkflow(_snapshot(), QuestionPipeline())
    await workflow.start()
    await workflow.finish()
    for _ in range(10):
        if workflow.snapshot.status == WorkflowStatus.WAITING_OPERATOR:
            break
        await asyncio.sleep(0)
    assert workflow.snapshot.question is not None
    await workflow.answer("q1", "同意")
    result = await workflow.wait()
    assert result.status == WorkflowStatus.PUBLISHED
    assert result.draft == {"answer": "同意"}


@pytest.mark.asyncio
async def test_recording_workflow_republish_excludes_live_notebook() -> None:
    pipeline = _ImmediatePipeline()
    snapshot = WorkflowSnapshot(
        run_id="run-1",
        action="action-1",
        status=WorkflowStatus.EDITABLE,
        capture_frozen=True,
        draft={"flow_id": "edited"},
    )
    workflow = RecordingWorkflow(snapshot, pipeline)
    await workflow.republish(machine_verification=True)
    await workflow.wait()

    assert pipeline.seeds == [PipelineSeed(
        kind="edited_spec",
        draft={"flow_id": "edited"},
        use_live_notebook=False,
        machine_verification=True,
    )]


@pytest.mark.asyncio
async def test_recording_workflow_finish_defaults_machine_verification_off() -> None:
    pipeline = _ImmediatePipeline()
    workflow = RecordingWorkflow(_snapshot(), pipeline)
    await workflow.start()

    await workflow.finish()
    await workflow.wait()

    assert pipeline.seeds == [PipelineSeed(
        kind="recording",
        use_live_notebook=True,
        machine_verification=False,
    )]


@pytest.mark.asyncio
async def test_recording_workflow_same_draft_republish_is_idempotent() -> None:
    pipeline = _ImmediatePipeline()
    workflow = RecordingWorkflow(
        WorkflowSnapshot(
            run_id="run-1", action="action-1", status=WorkflowStatus.EDITABLE,
            capture_frozen=True, draft={"flow_id": "edited"},
        ),
        pipeline,
    )

    await workflow.republish()
    await workflow.wait()
    await workflow.republish()
    await asyncio.sleep(0)

    assert len(pipeline.seeds) == 1


@pytest.mark.asyncio
async def test_failed_republish_preserves_draft_and_can_retry_new_revision() -> None:
    class FailOncePipeline:
        calls = 0

        async def run(self, seed, context):  # noqa: ANN001
            self.calls += 1
            if self.calls == 1:
                return PipelineOutcome(status=WorkflowStatus.FAILED, error="temporary failure")
            return PipelineOutcome(
                status=WorkflowStatus.PUBLISHED,
                draft=dict(seed.draft or {}),
                release={"skill_id": "skill-1"},
            )

    pipeline = FailOncePipeline()
    workflow = RecordingWorkflow(
        WorkflowSnapshot(
            run_id="run-1", action="action-1", status=WorkflowStatus.EDITABLE,
            capture_frozen=True, draft={"flow_id": "edited"},
        ),
        pipeline,
    )

    await workflow.republish()
    failed = await workflow.wait()
    assert failed.status == WorkflowStatus.FAILED
    assert failed.draft == {"flow_id": "edited"}

    await workflow.republish()
    published = await workflow.wait()
    assert published.status == WorkflowStatus.PUBLISHED
    assert pipeline.calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [WorkflowStatus.FAILED, WorkflowStatus.CANCELLED])
async def test_failed_or_cancelled_draft_can_be_edited_before_retry(status) -> None:  # noqa: ANN001
    workflow = RecordingWorkflow(
        WorkflowSnapshot(
            run_id="run-1", action="action-1", status=status,
            capture_frozen=True, draft={"flow_id": "old"}, revision=3,
        ),
        _ImmediatePipeline(),
    )

    result = await workflow.patch_draft({"flow_id": "corrected"}, expected_revision=3)

    assert result.status == WorkflowStatus.EDITABLE
    assert result.draft == {"flow_id": "corrected"}
    assert result.release is None


@pytest.mark.asyncio
async def test_recording_workflow_persists_each_authoritative_snapshot(tmp_path) -> None:
    path = tmp_path / "action.json"
    workflow = RecordingWorkflow(_snapshot(), _ImmediatePipeline(), snapshot_path=path)

    result = await workflow.start()

    assert path.exists()
    assert WorkflowSnapshot.model_validate_json(path.read_text(encoding="utf-8")) == result


@pytest.mark.asyncio
async def test_recording_workflow_cancel_preserves_draft_and_stops_task() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class WaitingPipeline:
        async def run(self, seed, context):  # noqa: ANN001
            started.set()
            await asyncio.Event().wait()

    workflow = RecordingWorkflow(
        WorkflowSnapshot(
            run_id="run-1", action="action-1", status=WorkflowStatus.EDITABLE,
            capture_frozen=True, draft={"flow_id": "draft"},
        ),
        WaitingPipeline(),
        cancel_listener=lambda: cancelled.set(),
    )
    await workflow.republish()
    await started.wait()
    result = await workflow.cancel()

    assert result.status == WorkflowStatus.CANCELLED
    assert result.draft == {"flow_id": "draft"}
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_self_healing_pipeline_repairs_until_publish_without_external_requeue() -> None:
    class Runtime:
        checks = 0

        async def prepare(self, seed, context):  # noqa: ANN001
            return {"fixed": False, "used_live": seed.use_live_notebook}

        async def check(self, draft, context):  # noqa: ANN001
            self.checks += 1
            issues = () if draft["fixed"] else (
                WorkflowIssue(
                    issue_id="i1", code="missing", message="缺少字段",
                    resolver="machine_repair",
                ),
            )
            return PipelineCheck(draft=draft, issues=issues)

        async def repair(self, draft, issues, operator_answers, context):  # noqa: ANN001
            return {**draft, "fixed": True}

        async def publish(self, draft, context):  # noqa: ANN001
            return {"skill_id": "skill", "fixed": draft["fixed"]}

    runtime = Runtime()
    pipeline = SelfHealingPipeline(runtime)
    progress = []
    activity = []
    context = PipelineContext(
        progress=lambda step, label, round_number: _append_progress(
            progress, step, label, round_number,
        ),
        ask_operator=lambda question: _answer(""),
        cancelled=lambda: False,
        activity=lambda item: _append_activity(activity, item),
    )
    outcome = await pipeline.run(PipelineSeed(
        kind="recording",
        use_live_notebook=True,
        machine_verification=True,
    ), context)

    assert outcome.status == WorkflowStatus.PUBLISHED
    assert outcome.release == {"skill_id": "skill", "fixed": True}
    assert runtime.checks == 2
    assert any(item[0] == WorkflowStep.RESOLVING for item in progress)
    assert [item.status for item in activity] == ["pending", "running", "resolved"]


@pytest.mark.asyncio
async def test_published_snapshot_uses_the_frozen_release_draft() -> None:
    class Runtime:
        async def prepare(self, seed, context):  # noqa: ANN001
            return {"name": "planner-draft"}

        async def check(self, draft, context):  # noqa: ANN001
            raise AssertionError("verification is disabled")

        async def repair(self, draft, issues, operator_answers, context):  # noqa: ANN001
            raise AssertionError("verification is disabled")

        async def publish(self, draft, context):  # noqa: ANN001
            context.remember_draft({"name": "frozen-release"})
            return {"skill_id": "skill"}

    context = PipelineContext(
        progress=lambda *args: _answer(None),
        ask_operator=lambda question: _answer(""),
        cancelled=lambda: False,
    )

    outcome = await SelfHealingPipeline(Runtime()).run(PipelineSeed(
        kind="recording",
        machine_verification=False,
    ), context)

    assert outcome.status == WorkflowStatus.PUBLISHED
    assert outcome.draft == {"name": "frozen-release"}


@pytest.mark.asyncio
async def test_self_healing_pipeline_stops_after_bounded_no_progress() -> None:
    issue = WorkflowIssue(
        issue_id="i1", code="missing", message="无法自动变化", resolver="machine_repair",
    )

    class Runtime:
        repairs = 0

        async def prepare(self, seed, context):  # noqa: ANN001
            return {"same": True}

        async def check(self, draft, context):  # noqa: ANN001
            return PipelineCheck(draft=draft, issues=(issue,))

        async def repair(self, draft, issues, operator_answers, context):  # noqa: ANN001
            self.repairs += 1
            return draft

        async def publish(self, draft, context):  # noqa: ANN001
            raise AssertionError("must not publish")

    runtime = Runtime()
    pipeline = SelfHealingPipeline(runtime, max_rounds=5, max_unchanged_rounds=2)
    context = PipelineContext(
        progress=lambda *args: _answer(None),
        ask_operator=lambda question: _answer(""),
        cancelled=lambda: False,
    )
    outcome = await pipeline.run(PipelineSeed(
        kind="edited_spec",
        draft={},
        machine_verification=True,
    ), context)

    assert outcome.status == WorkflowStatus.EDITABLE
    assert outcome.error == "自动处理连续没有产生有效变化"
    assert runtime.repairs == 2


@pytest.mark.asyncio
async def test_self_healing_pipeline_does_not_count_draft_churn_as_progress() -> None:
    issue = WorkflowIssue(
        issue_id="dependency:stable",
        code="dependency",
        message="同一依赖仍未验证",
        resolver="collect_evidence",
        target={"dependency_id": "stable"},
    )

    class Runtime:
        repairs = 0

        async def prepare(self, seed, context):  # noqa: ANN001
            return {"normalization_revision": 0}

        async def check(self, draft, context):  # noqa: ANN001
            return PipelineCheck(draft=draft, issues=(issue,))

        async def repair(self, draft, issues, operator_answers, context):  # noqa: ANN001
            self.repairs += 1
            return {"normalization_revision": self.repairs}

        async def publish(self, draft, context):  # noqa: ANN001
            raise AssertionError("must not publish")

    runtime = Runtime()
    pipeline = SelfHealingPipeline(runtime, max_rounds=5, max_unchanged_rounds=2)
    context = PipelineContext(
        progress=lambda *args: _answer(None),
        ask_operator=lambda question: _answer(""),
        cancelled=lambda: False,
    )

    outcome = await pipeline.run(PipelineSeed(
        kind="edited_spec",
        draft={},
        machine_verification=True,
    ), context)

    assert outcome.status == WorkflowStatus.EDITABLE
    assert outcome.error == "自动处理连续没有产生有效变化"
    assert runtime.repairs == 2


@pytest.mark.asyncio
async def test_self_healing_pipeline_asks_only_operator_issues() -> None:
    issue = WorkflowIssue(
        issue_id="approval",
        code="required_axis_unconfirmed",
        message="写能力字段 `step:body.approverIds` 的 required 轴未确认",
        resolver="operator",
        target={"field_label": "审批人", "wire_path": "body.approverIds"},
    )

    class Runtime:
        answer = ""

        async def prepare(self, seed, context):  # noqa: ANN001
            return {"answer": ""}

        async def check(self, draft, context):  # noqa: ANN001
            return PipelineCheck(draft=draft, issues=() if draft["answer"] else (issue,))

        async def repair(self, draft, issues, operator_answers, context):  # noqa: ANN001
            self.answer = operator_answers["approval"]
            return {"answer": self.answer}

        async def publish(self, draft, context):  # noqa: ANN001
            return {"skill_id": "skill"}

    runtime = Runtime()
    pipeline = SelfHealingPipeline(runtime)
    questions = []

    async def ask(question):  # noqa: ANN001
        questions.append(question)
        return "直属领导"

    context = PipelineContext(
        progress=lambda *args: _answer(None),
        ask_operator=ask,
        cancelled=lambda: False,
    )
    outcome = await pipeline.run(PipelineSeed(
        kind="recording",
        machine_verification=True,
    ), context)

    assert outcome.status == WorkflowStatus.PUBLISHED
    assert runtime.answer == "直属领导"
    assert [question.issue_id for question in questions] == ["approval"]
    assert questions[0].text == "请确认“审批人”在调用这个能力时是否必须提供。"
    assert questions[0].options == ["必填", "选填"]


async def _append_progress(target, step, label, round_number):  # noqa: ANN001
    target.append((step, label, round_number))


async def _append_activity(target, item):  # noqa: ANN001
    target.append(item)


async def _answer(value):  # noqa: ANN001
    return value
