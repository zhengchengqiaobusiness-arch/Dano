from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from dano.onboarding.recording_pipeline import (
    CanonicalRecordingRuntime,
    RecordingPipelineServices,
)
from dano.onboarding.recording_workflow import (
    PipelineCheck,
    PipelineContext,
    PipelineSeed,
    RecordingWorkflow,
    SelfHealingPipeline,
    WorkflowIssue,
    WorkflowSnapshot,
    WorkflowStatus,
    _draft_fingerprint,
)


ROOT = Path(__file__).resolve().parents[1]
ONBOARDING = ROOT / "dano" / "onboarding"


async def _progress(*_args) -> None:  # noqa: ANN002
    return None


async def _no_ask(_question) -> str:  # noqa: ANN001
    raise AssertionError("operator must not be asked")


def _context(**kwargs) -> PipelineContext:  # noqa: ANN003
    return PipelineContext(
        progress=kwargs.get("progress", _progress),
        ask_operator=kwargs.get("ask_operator", _no_ask),
        cancelled=kwargs.get("cancelled", lambda: False),
        persist_stage_six=kwargs.get("persist_stage_six"),
    )


def _draft(rev: int = 1) -> dict:
    return {
        "title": "请假",
        "rev": rev,
        "capabilities": [{"capability_id": "cap_submit", "name": "submit"}],
        "request_facts": {"requests": [{"request_id": "req_1"}]},
    }


def test_onboarding_has_no_second_plan_path() -> None:
    assert not hasattr(RecordingPipelineServices, "plan_capabilities")
    for path in ONBOARDING.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        assert "plan_capabilities" not in names
        assert "PlanCapabilities" not in names
    pipeline = (ONBOARDING / "recording_pipeline.py").read_text(encoding="utf-8")
    assert "materialize_recording" in pipeline
    assert "plan_capabilities" not in pipeline


def test_self_healing_pipeline_has_no_fixed_round_cap() -> None:
    source = (ONBOARDING / "recording_workflow.py").read_text(encoding="utf-8")
    assert "max_rounds" not in source
    assert "自动处理达到" not in source
    assert "while True:" in source


def test_publish_exports_only_current_action() -> None:
    source = (ROOT / "dano" / "gateway" / "app.py").read_text(encoding="utf-8")
    assert "action=action" in source
    assert "direct_recording_export=not machine_verification_enabled" in source
    assert "export_all" not in source


@pytest.mark.asyncio
async def test_prepare_returns_materialized_spec_without_replanning() -> None:
    calls: list[str] = []

    async def materialize(_live, _context):  # noqa: ANN001
        calls.append("materialize")
        return _draft()

    async def forbidden(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("prepare 不得进入阶段 7")

    runtime = CanonicalRecordingRuntime(RecordingPipelineServices(
        materialize_recording=materialize,
        verify=forbidden,
        repair=forbidden,
        publish=forbidden,
    ))
    draft = await runtime.prepare(
        PipelineSeed(kind="recording", machine_verification=True),
        _context(),
    )
    assert calls == ["materialize"]
    assert draft["capabilities"][0]["capability_id"] == "cap_submit"


@pytest.mark.asyncio
async def test_default_off_skips_stage_seven_and_publishes_same_spec() -> None:
    order: list[str] = []
    saved: list[dict] = []

    class Runtime:
        async def prepare(self, seed, context):  # noqa: ANN001
            order.append("prepare")
            return _draft()

        async def check(self, draft, context):  # noqa: ANN001
            order.append("check")
            raise AssertionError("关闭验证不得 check")

        async def repair(self, draft, issues, operator_answers, context):  # noqa: ANN001
            order.append("repair")
            raise AssertionError("关闭验证不得 repair")

        async def publish(self, draft, context):  # noqa: ANN001
            order.append("publish")
            return {"action": "action_1", "ok": True}

    async def persist(draft):  # noqa: ANN001
        order.append("persist")
        saved.append(draft)

    outcome = await SelfHealingPipeline(Runtime()).run(
        PipelineSeed(kind="recording", machine_verification=False),
        _context(persist_stage_six=persist),
    )
    assert outcome.status == WorkflowStatus.PUBLISHED
    assert order == ["prepare", "persist", "publish"]
    assert saved[0]["capabilities"][0]["capability_id"] == "cap_submit"
    assert outcome.release == {"action": "action_1", "ok": True}


@pytest.mark.asyncio
async def test_verification_on_uses_same_stage_six_spec() -> None:
    seen: list[dict] = []

    class Runtime:
        async def prepare(self, seed, context):  # noqa: ANN001
            return _draft()

        async def check(self, draft, context):  # noqa: ANN001
            seen.append(draft)
            return PipelineCheck(draft=draft, issues=())

        async def repair(self, draft, issues, operator_answers, context):  # noqa: ANN001
            raise AssertionError("no repair")

        async def publish(self, draft, context):  # noqa: ANN001
            return {"ok": True, "fingerprint": _draft_fingerprint(draft)}

    outcome = await SelfHealingPipeline(Runtime()).run(
        PipelineSeed(kind="recording", machine_verification=True),
        _context(),
    )
    assert outcome.status == WorkflowStatus.PUBLISHED
    assert seen[0] == _draft()
    assert outcome.release["fingerprint"] == _draft_fingerprint(_draft())


@pytest.mark.asyncio
async def test_same_spec_stage_seven_is_stable() -> None:
    class Runtime:
        async def prepare(self, seed, context):  # noqa: ANN001
            return dict(seed.draft or {})

        async def check(self, draft, context):  # noqa: ANN001
            return PipelineCheck(draft=draft, issues=())

        async def repair(self, draft, issues, operator_answers, context):  # noqa: ANN001
            raise AssertionError("no repair")

        async def publish(self, draft, context):  # noqa: ANN001
            return {"fingerprint": _draft_fingerprint(draft)}

    first = await SelfHealingPipeline(Runtime()).run(
        PipelineSeed(kind="edited_spec", draft=_draft(), machine_verification=True),
        _context(),
    )
    second = await SelfHealingPipeline(Runtime()).run(
        PipelineSeed(kind="edited_spec", draft=_draft(), machine_verification=True),
        _context(),
    )
    assert first.release == second.release
    assert first.draft == second.draft


@pytest.mark.asyncio
async def test_external_block_keeps_complete_draft() -> None:
    issue = WorkflowIssue(
        issue_id="ext",
        code="external",
        message="外部系统不可用",
        resolver="external_blocked",
    )

    class Runtime:
        async def prepare(self, seed, context):  # noqa: ANN001
            return _draft()

        async def check(self, draft, context):  # noqa: ANN001
            return PipelineCheck(draft=draft, issues=(issue,))

        async def repair(self, draft, issues, operator_answers, context):  # noqa: ANN001
            raise AssertionError("external block must not repair")

        async def publish(self, draft, context):  # noqa: ANN001
            raise AssertionError("must not publish")

    outcome = await SelfHealingPipeline(Runtime()).run(
        PipelineSeed(kind="recording", machine_verification=True),
        _context(),
    )
    assert outcome.status == WorkflowStatus.EDITABLE
    assert outcome.draft == _draft()
    assert outcome.issues[0].resolver == "external_blocked"


@pytest.mark.asyncio
async def test_cancel_keeps_stage_six_draft() -> None:
    started = asyncio.Event()

    class Runtime:
        async def prepare(self, seed, context):  # noqa: ANN001
            return _draft()

        async def check(self, draft, context):  # noqa: ANN001
            started.set()
            await asyncio.sleep(30)
            return PipelineCheck(draft=draft, issues=())

        async def repair(self, draft, issues, operator_answers, context):  # noqa: ANN001
            raise AssertionError("cancelled")

        async def publish(self, draft, context):  # noqa: ANN001
            raise AssertionError("cancelled")

    workflow = RecordingWorkflow(
        WorkflowSnapshot(run_id="r1", action="action_1"),
        SelfHealingPipeline(Runtime()),
    )
    await workflow.start()
    await workflow.finish(machine_verification=True)
    await asyncio.wait_for(started.wait(), timeout=2)
    await workflow.cancel()
    assert workflow.snapshot.status == WorkflowStatus.CANCELLED
    assert workflow.snapshot.draft == _draft()


@pytest.mark.asyncio
async def test_cancel_marks_cancelled_before_slow_teardown() -> None:
    started = asyncio.Event()
    seen: list[str] = []

    class Runtime:
        async def prepare(self, seed, context):  # noqa: ANN001
            return _draft()

        async def check(self, draft, context):  # noqa: ANN001
            started.set()
            await asyncio.sleep(30)
            return PipelineCheck(draft=draft, issues=())

        async def repair(self, draft, issues, operator_answers, context):  # noqa: ANN001
            raise AssertionError("cancelled")

        async def publish(self, draft, context):  # noqa: ANN001
            raise AssertionError("cancelled")

    async def hang_cancel() -> None:
        await asyncio.sleep(30)

    workflow = RecordingWorkflow(
        WorkflowSnapshot(run_id="r1", action="action_1"),
        SelfHealingPipeline(Runtime()),
        listener=lambda snapshot: seen.append(snapshot.status.value),
        cancel_listener=hang_cancel,
    )
    await workflow.start()
    await workflow.finish(machine_verification=True)
    await asyncio.wait_for(started.wait(), timeout=2)
    task = asyncio.create_task(workflow.cancel())
    await asyncio.sleep(0.05)
    assert workflow.snapshot.status == WorkflowStatus.CANCELLED
    assert "cancelled" in seen
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_operator_answer_is_written_back_to_flow_spec(monkeypatch) -> None:
    from dano.execution.page.flow_spec import FlowCapability, FlowSpec, FlowStep, ParamField
    from dano.onboarding.recording_runtime import ProductionRecordingServices

    async def auto_fix(spec, **_kwargs):  # noqa: ANN001, ANN003
        return spec

    monkeypatch.setattr("dano.onboarding.recording_runtime.auto_fix_flow_spec", auto_fix)
    spec = FlowSpec(
        tenant="tenant",
        subsystem="oa",
        title="请假",
        steps=[FlowStep(
            step_id="submit",
            name="提交",
            params=[ParamField(path="body.reason", key="reason", label="请假原因", field_id="reason", required=False)],
        )],
        capabilities=[FlowCapability(capability_id="cap_submit", name="submit")],
    )
    services = ProductionRecordingServices(
        recording_id="r1",
        materializer=lambda *_a: (_ for _ in ()).throw(AssertionError()),
        pi_provider=lambda *_a: (_ for _ in ()).throw(AssertionError()),
        publisher=lambda *_a: (_ for _ in ()).throw(AssertionError()),
    )
    draft = await services.repair(
        spec.model_dump(mode="json"),
        (WorkflowIssue(
            issue_id="reason",
            code="required_axis_unconfirmed",
            message="required",
            resolver="operator",
            target={"step_id": "submit", "field_id": "reason", "wire_path": "body.reason"},
        ),),
        {"reason": "必填"},
        _context(),
    )
    assert draft["steps"][0]["params"][0]["required"] is True
    assert draft["capabilities"][0]["capability_id"] == "cap_submit"
