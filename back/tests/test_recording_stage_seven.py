from __future__ import annotations

import pytest

from dano.execution.page.flow_spec import FlowCapability, FlowSpec, FlowStep, ParamField
from dano.onboarding.recording_runtime import ProductionRecordingServices, _apply_operator_answer
from dano.onboarding.recording_workflow import (
    PipelineCheck,
    PipelineContext,
    PipelineSeed,
    SelfHealingPipeline,
    WorkflowIssue,
    WorkflowStatus,
)


async def _progress(*_args) -> None:  # noqa: ANN002
    return None


def _context(**kwargs) -> PipelineContext:  # noqa: ANN003
    return PipelineContext(
        progress=kwargs.get("progress", _progress),
        ask_operator=kwargs.get("ask_operator", _no_ask),
        cancelled=lambda: False,
        activity=kwargs.get("activity"),
    )


async def _no_ask(_question) -> str:  # noqa: ANN001
    raise AssertionError("operator must not be asked")


def _spec() -> FlowSpec:
    return FlowSpec(
        tenant="tenant",
        subsystem="oa",
        title="请假",
        steps=[
            FlowStep(
                step_id="submit",
                name="提交",
                method="POST",
                path="/leave",
                params=[
                    ParamField(
                        path="body.reason",
                        key="reason",
                        label="请假原因",
                        field_id="reason",
                        required=False,
                        source_kind="user_input",
                    ),
                    ParamField(
                        path="body.token",
                        key="token",
                        label="令牌",
                        field_id="token",
                        required=False,
                        source_kind="session",
                    ),
                ],
            )
        ],
        capabilities=[
            FlowCapability(capability_id="cap_submit", name="submit", title="提交申请"),
        ],
    )


@pytest.mark.asyncio
async def test_deterministic_fix_runs_before_operator_answers(monkeypatch) -> None:
    order: list[str] = []

    async def auto_fix(spec, **_kwargs):  # noqa: ANN001, ANN003
        order.append("deterministic")
        return spec

    monkeypatch.setattr("dano.onboarding.recording_runtime.auto_fix_flow_spec", auto_fix)

    class Pi:
        last_submission_kind = "repair"

        def bind_flow_spec(self, spec):  # noqa: ANN001
            self.spec = spec

        def current_flow_spec(self):
            return self.spec

        async def prompt(self, _text):  # noqa: ANN001
            order.append("pi")

    async def provider(_fresh):  # noqa: ANN001
        return Pi()

    services = ProductionRecordingServices(
        recording_id="r1",
        materializer=lambda *_a: (_ for _ in ()).throw(AssertionError()),
        pi_provider=provider,
        publisher=lambda *_a: (_ for _ in ()).throw(AssertionError()),
    )
    issue = WorkflowIssue(
        issue_id="reason",
        code="required_axis_unconfirmed",
        message="required",
        resolver="operator",
        target={"step_id": "submit", "field_id": "reason", "wire_path": "body.reason"},
    )
    draft = await services.repair(
        _spec().model_dump(mode="json"),
        (issue,),
        {"reason": "必填"},
        _context(),
    )

    assert order[0] == "deterministic"
    assert draft["steps"][0]["params"][0]["required"] is True
    assert draft["steps"][0]["params"][0]["source"]["required_state"] == "required"


@pytest.mark.asyncio
async def test_one_operator_failure_does_not_block_other_answers() -> None:
    spec = _spec()
    good = WorkflowIssue(
        issue_id="reason",
        code="required_axis_unconfirmed",
        message="required",
        resolver="operator",
        target={"step_id": "submit", "field_id": "reason", "wire_path": "body.reason"},
    )
    bad = WorkflowIssue(
        issue_id="missing",
        code="required_axis_unconfirmed",
        message="required",
        resolver="operator",
        target={"step_id": "nope", "field_id": "gone"},
    )
    assert _apply_operator_answer(spec, good, "可选") is True
    assert _apply_operator_answer(spec, bad, "可选") is False
    assert spec.steps[0].params[0].required is False
    assert spec.steps[0].params[0].source["required_state"] == "optional"


@pytest.mark.asyncio
async def test_pi_protocol_error_keeps_capabilities(monkeypatch) -> None:
    async def auto_fix(spec, **_kwargs):  # noqa: ANN001, ANN003
        return spec

    monkeypatch.setattr("dano.onboarding.recording_runtime.auto_fix_flow_spec", auto_fix)

    class Pi:
        last_submission_kind = ""

        def bind_flow_spec(self, spec):  # noqa: ANN001
            self.spec = spec

        def current_flow_spec(self):
            return FlowSpec(tenant="tenant", subsystem="oa", capabilities=[])

        async def prompt(self, _text):  # noqa: ANN001
            raise RuntimeError("schema invalid")

    async def provider(_fresh):  # noqa: ANN001
        return Pi()

    services = ProductionRecordingServices(
        recording_id="r1",
        materializer=lambda *_a: (_ for _ in ()).throw(AssertionError()),
        pi_provider=provider,
        publisher=lambda *_a: (_ for _ in ()).throw(AssertionError()),
    )
    context = _context()
    draft = await services.repair(
        _spec().model_dump(mode="json"),
        (WorkflowIssue(issue_id="i1", code="dependency", message="缺依赖", resolver="machine_repair"),),
        {},
        context,
    )

    assert [item["capability_id"] for item in draft["capabilities"]] == ["cap_submit"]
    assert context.last_repair_report is not None
    assert "i1" in context.last_repair_report.still_pending


@pytest.mark.asyncio
async def test_no_progress_requires_fingerprint_and_unapplied_ops() -> None:
    issue = WorkflowIssue(issue_id="i1", code="missing", message="无法变化", resolver="machine_repair")

    class Runtime:
        repairs = 0

        async def prepare(self, seed, context):  # noqa: ANN001
            return {"same": True}

        async def check(self, draft, context):  # noqa: ANN001
            return PipelineCheck(draft=draft, issues=(issue,))

        async def repair(self, draft, issues, operator_answers, context):  # noqa: ANN001
            from dano.onboarding.recording_workflow import RepairReport
            self.repairs += 1
            context.last_repair_report = RepairReport()
            return draft

        async def publish(self, draft, context):  # noqa: ANN001
            raise AssertionError("must not publish")

    outcome = await SelfHealingPipeline(Runtime(), max_unchanged_rounds=2).run(
        PipelineSeed(kind="edited_spec", draft={"same": True}, machine_verification=True),
        _context(),
    )

    assert outcome.status == WorkflowStatus.EDITABLE
    assert outcome.error == "自动处理连续没有产生有效变化"
    assert outcome.draft == {"same": True}


@pytest.mark.asyncio
async def test_fingerprint_change_counts_as_progress() -> None:
    issue = WorkflowIssue(issue_id="i1", code="missing", message="仍在修", resolver="machine_repair")

    class Runtime:
        repairs = 0

        async def prepare(self, seed, context):  # noqa: ANN001
            return {"rev": 0}

        async def check(self, draft, context):  # noqa: ANN001
            if draft["rev"] >= 3:
                return PipelineCheck(draft=draft, issues=())
            return PipelineCheck(draft=draft, issues=(issue,))

        async def repair(self, draft, issues, operator_answers, context):  # noqa: ANN001
            from dano.onboarding.recording_workflow import RepairReport
            self.repairs += 1
            context.last_repair_report = RepairReport(applied=["i1"])
            return {"rev": draft["rev"] + 1}

        async def publish(self, draft, context):  # noqa: ANN001
            return {"ok": True, "rev": draft["rev"]}

    outcome = await SelfHealingPipeline(Runtime(), max_unchanged_rounds=2).run(
        PipelineSeed(kind="edited_spec", draft={"rev": 0}, machine_verification=True),
        _context(),
    )

    assert outcome.status == WorkflowStatus.PUBLISHED
    assert outcome.release == {"ok": True, "rev": 3}


@pytest.mark.asyncio
async def test_repair_applies_valid_answers_when_another_issue_fails(monkeypatch) -> None:
    async def auto_fix(spec, **_kwargs):  # noqa: ANN001, ANN003
        return spec

    monkeypatch.setattr("dano.onboarding.recording_runtime.auto_fix_flow_spec", auto_fix)

    services = ProductionRecordingServices(
        recording_id="r1",
        materializer=lambda *_a: (_ for _ in ()).throw(AssertionError()),
        pi_provider=lambda *_a: (_ for _ in ()).throw(AssertionError()),
        publisher=lambda *_a: (_ for _ in ()).throw(AssertionError()),
    )
    draft = await services.repair(
        _spec().model_dump(mode="json"),
        (
            WorkflowIssue(
                issue_id="reason",
                code="required_axis_unconfirmed",
                message="required",
                resolver="operator",
                target={"step_id": "submit", "field_id": "reason", "wire_path": "body.reason"},
            ),
            WorkflowIssue(
                issue_id="missing",
                code="required_axis_unconfirmed",
                message="required",
                resolver="operator",
                target={"step_id": "nope", "field_id": "gone"},
            ),
        ),
        {"reason": "必填", "missing": "可选"},
        _context(),
    )

    assert draft["steps"][0]["params"][0]["required"] is True
    assert draft["steps"][0]["params"][0]["source"]["required_state"] == "required"
    assert [item["capability_id"] for item in draft["capabilities"]] == ["cap_submit"]


@pytest.mark.asyncio
async def test_progress_can_continue_past_five_rounds() -> None:
    issue = WorkflowIssue(issue_id="i1", code="missing", message="仍在修", resolver="machine_repair")

    class Runtime:
        async def prepare(self, seed, context):  # noqa: ANN001
            return {"rev": 0}

        async def check(self, draft, context):  # noqa: ANN001
            if draft["rev"] >= 6:
                return PipelineCheck(draft=draft, issues=())
            return PipelineCheck(draft=draft, issues=(issue,))

        async def repair(self, draft, issues, operator_answers, context):  # noqa: ANN001
            from dano.onboarding.recording_workflow import RepairReport
            context.last_repair_report = RepairReport(applied=["i1"])
            return {"rev": draft["rev"] + 1}

        async def publish(self, draft, context):  # noqa: ANN001
            return {"ok": True, "rev": draft["rev"]}

    outcome = await SelfHealingPipeline(Runtime(), max_unchanged_rounds=2).run(
        PipelineSeed(kind="edited_spec", draft={"rev": 0}, machine_verification=True),
        _context(),
    )

    assert outcome.status == WorkflowStatus.PUBLISHED
    assert outcome.release == {"ok": True, "rev": 6}


@pytest.mark.asyncio
async def test_operator_question_uses_business_language() -> None:
    from dano.onboarding.recording_workflow import _operator_question

    question = _operator_question(WorkflowIssue(
        issue_id="reason",
        code="required_axis_unconfirmed",
        message="写能力字段 `submit:body.reason` 的 required 轴未确认",
        resolver="operator",
        target={"field_label": "请假原因", "wire_path": "body.reason"},
    ))

    assert "请假原因" in question.text
    assert "必填" in question.text
    assert question.options == ["必填", "可选"]
