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
        persist_stage_six=kwargs.get("persist_stage_six"),
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
async def test_unchanged_repair_attempts_are_not_reported_as_progress(monkeypatch) -> None:
    async def auto_fix(spec, **_kwargs):  # noqa: ANN001, ANN003
        return spec

    monkeypatch.setattr("dano.onboarding.recording_runtime.auto_fix_flow_spec", auto_fix)

    class Pi:
        last_submission_kind = "repair"

        def bind_flow_spec(self, spec):  # noqa: ANN001
            self.spec = spec

        def current_flow_spec(self):
            return self.spec

        async def prompt(self, _text):  # noqa: ANN001
            return None

    async def provider(_fresh):  # noqa: ANN001
        return Pi()

    services = ProductionRecordingServices(
        recording_id="r1",
        materializer=lambda *_a: (_ for _ in ()).throw(AssertionError()),
        pi_provider=provider,
        publisher=lambda *_a: (_ for _ in ()).throw(AssertionError()),
    )
    context = _context()
    draft = _spec().model_dump(mode="json")
    repaired = await services.repair(
        draft,
        (
            WorkflowIssue(
                issue_id="i1",
                code="dependency",
                message="缺依赖",
                resolver="machine_repair",
            ),
        ),
        {},
        context,
    )

    assert repaired == draft
    assert context.last_repair_report is not None
    assert context.last_repair_report.applied == []
    assert context.last_repair_report.still_pending == ["i1"]


@pytest.mark.asyncio
async def test_stage_seven_starts_only_after_stage_six_persist() -> None:
    steps: list[tuple[str, str]] = []
    persisted: list[dict] = []

    class Runtime:
        async def prepare(self, seed, context):  # noqa: ANN001
            assert persisted == []
            assert all(step != "verifying" for step, _label in steps)
            return {"rev": 0}

        async def check(self, draft, context):  # noqa: ANN001
            assert persisted == [draft]
            assert any(
                step == "verifying" and label == "第 1～6 阶段已完成，开始机器验证"
                for step, label in steps
            )
            return PipelineCheck(draft=draft, issues=())

        async def repair(self, draft, issues, operator_answers, context):  # noqa: ANN001
            raise AssertionError("no repair before first check")

        async def publish(self, draft, context):  # noqa: ANN001
            return {"ok": True}

    async def persist(draft):  # noqa: ANN001
        assert all(step != "verifying" for step, _label in steps)
        persisted.append(draft)

    async def progress(step, label, round_number=0):  # noqa: ANN001
        steps.append((str(step), str(label)))

    outcome = await SelfHealingPipeline(Runtime()).run(
        PipelineSeed(kind="recording", draft={"rev": 0}, machine_verification=True),
        _context(progress=progress, persist_stage_six=persist),
    )

    assert outcome.status == WorkflowStatus.PUBLISHED
    assert persisted == [{"rev": 0}]
    assert steps[0][0] == "materializing"
    verify_index = next(i for i, item in enumerate(steps) if item[0] == "verifying")
    assert steps[verify_index][1] == "第 1～6 阶段已完成，开始机器验证"
    assert all(item[0] != "verifying" for item in steps[:verify_index])


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


@pytest.mark.asyncio
async def test_stage_seven_activity_uses_thinking_language() -> None:
    from dano.onboarding.recording_workflow import WorkflowActivity

    recorded: list[WorkflowActivity] = []
    issue = WorkflowIssue(
        issue_id="enum-1",
        code="enum",
        message="待处理：enum",
        resolver="collect_evidence",
        target={"path": "body.status"},
    )

    class Runtime:
        async def prepare(self, seed, context):  # noqa: ANN001
            return {"rev": 0}

        async def check(self, draft, context):  # noqa: ANN001
            if draft["rev"] >= 1:
                return PipelineCheck(draft=draft, issues=())
            return PipelineCheck(draft=draft, issues=(issue,))

        async def repair(self, draft, issues, operator_answers, context):  # noqa: ANN001
            from dano.onboarding.recording_workflow import RepairReport
            context.last_repair_report = RepairReport(applied=["enum-1"])
            return {"rev": 1}

        async def publish(self, draft, context):  # noqa: ANN001
            return {"ok": True}

    async def record(item: WorkflowActivity) -> None:
        recorded.append(item)

    outcome = await SelfHealingPipeline(Runtime()).run(
        PipelineSeed(kind="edited_spec", draft={"rev": 0}, machine_verification=True),
        _context(activity=record),
    )

    labels = [item.label for item in recorded]
    assert outcome.status == WorkflowStatus.PUBLISHED
    assert any(item.startswith("发现了问题") for item in labels)
    assert any("我觉得应该这样处理" in item for item in labels)
    assert any(item.startswith("本轮结果") for item in labels)
    assert any(item.startswith("已经处理好了") for item in labels)
    assert not any("正在自动补充验证证据" in item for item in labels)
    assert not any(item == "待处理：enum" for item in labels)
    assert sum(1 for item in labels if item.startswith("发现了问题")) == 1


def _sale_unknown_spec() -> FlowSpec:
    from dano.execution.page.flow_spec import CapabilityRequestRef, RequestFacts, SelectBinding

    return FlowSpec(
        tenant="tenant",
        subsystem="erp",
        title="销售订单",
        steps=[
            FlowStep(
                step_id="query",
                name="查询",
                method="GET",
                path="/sale-order/page",
                source_meta={"request_id": "req_query"},
                params=[
                    ParamField(
                        path="productId",
                        key="productId",
                        label="产品",
                        type="enum",
                        source_kind="user_input",
                        enum_options=[{"label": "苹果", "value": "1"}],
                    ),
                ],
            ),
            FlowStep(
                step_id="create",
                name="新增",
                method="POST",
                path="/sale-order/create",
                source_meta={"request_id": "req_create"},
                params=[
                    ParamField(
                        path="items[0].productId",
                        key="productId",
                        label="产品",
                        field_id="p1",
                        source_kind="unknown",
                    ),
                    ParamField(
                        path="items[0].productBarCode",
                        key="productBarCode",
                        label="条码",
                        field_id="p2",
                        source_kind="unknown",
                    ),
                ],
                selects=[
                    SelectBinding(
                        path="items[0].productId",
                        options=[{"label": "苹果", "value": "1"}],
                        enum_confirmed=True,
                        field_projections={"items[0].productBarCode": "barCode"},
                    ),
                ],
            ),
        ],
        capabilities=[
            FlowCapability(
                capability_id="cap_create",
                name="create",
                title="新增销售订单",
                kind="create",
                step_ids=["create"],
                request_refs=[
                    CapabilityRequestRef(
                        request_id="req_create",
                        step_id="create",
                        usage="execute",
                    ),
                ],
                nodes=[{"step_id": "create"}],
            ),
        ],
        request_facts=RequestFacts.model_validate({
            "field_evidence": [
                {
                    "binding_status": "bound",
                    "request_id": "req_create",
                    "wire_path": "body.items[0].productId",
                    "op": "select",
                    "control_kind": "select",
                    "editable": True,
                    "label": "产品",
                    "required": True,
                },
                {
                    "binding_status": "bound",
                    "request_id": "req_create",
                    "wire_path": "body.items[0].productBarCode",
                    "op": "snapshot",
                    "editable": False,
                    "read_only": True,
                    "label": "条码",
                },
            ],
        }),
    )


def test_recorded_evidence_fixes_unknown_select_and_projection() -> None:
    from dano.onboarding.recording_verify import apply_recorded_evidence_fixes

    spec = apply_recorded_evidence_fixes(_sale_unknown_spec())
    create = next(step for step in spec.steps if step.step_id == "create")
    by_key = {param.key: param for param in create.params}
    assert by_key["productId"].source_kind == "user_input"
    assert by_key["productId"].enum_options
    assert by_key["productBarCode"].source_kind == "previous_response"
    assert by_key["productBarCode"].exposed_to_user is False


def test_verification_todos_do_not_repeat_the_same_product_id() -> None:
    from dano.onboarding.recording_verify import apply_recorded_evidence_fixes, verification_todos

    spec = apply_recorded_evidence_fixes(_sale_unknown_spec())
    enums = [item for item in verification_todos(spec) if item["kind"] == "enum"]
    assert enums == []


def test_same_leaf_on_two_steps_has_distinct_subjects() -> None:
    from dano.onboarding.recording_workflow import _discovery_thought, _issue_subject

    query = WorkflowIssue(
        issue_id="enum:query:productId",
        code="enum",
        message="待处理：enum",
        resolver="collect_evidence",
        target={"step_id": "query", "path": "productId", "field_label": "产品"},
    )
    create = WorkflowIssue(
        issue_id="enum:create:productId",
        code="enum",
        message="待处理：enum",
        resolver="collect_evidence",
        target={
            "step_id": "create",
            "wire_path": "body.items[0].productId",
            "field_label": "产品",
        },
    )
    assert _issue_subject(query) != _issue_subject(create)
    assert "query" in _discovery_thought(query)
    assert "items[0].productId" in _discovery_thought(create) or "create" in _discovery_thought(create)


@pytest.mark.asyncio
async def test_verify_resolves_contradictory_field_stories() -> None:
    services = ProductionRecordingServices(
        recording_id="r1",
        materializer=lambda *_a: (_ for _ in ()).throw(AssertionError()),
        pi_provider=lambda *_a: (_ for _ in ()).throw(AssertionError()),
        publisher=lambda *_a: (_ for _ in ()).throw(AssertionError()),
    )
    draft, issues = await services.verify(
        _sale_unknown_spec().model_dump(mode="json"),
        _context(),
    )
    create = next(step for step in draft["steps"] if step["step_id"] == "create")
    by_key = {param["key"]: param for param in create["params"]}
    assert by_key["productId"]["source_kind"] == "user_input"
    assert by_key["productBarCode"]["source_kind"] == "previous_response"
    field_codes = {(issue.code, issue.target.get("wire_path") or issue.target.get("path") or "") for issue in issues}
    assert not any(code == "field_source_unknown" for code, _path in field_codes)
    enum_paths = [path for code, path in field_codes if code == "enum"]
    assert len(enum_paths) == len(set(enum_paths))
    assert any(issue.code == "write_verify" for issue in issues)


@pytest.mark.asyncio
async def test_repair_does_not_call_pi_for_evidence_resolved_fields(monkeypatch) -> None:
    async def auto_fix(spec, **_kwargs):  # noqa: ANN001, ANN003
        return spec

    monkeypatch.setattr("dano.onboarding.recording_runtime.auto_fix_flow_spec", auto_fix)

    called = False

    class Pi:
        last_submission_kind = "repair"

        def bind_flow_spec(self, spec):  # noqa: ANN001
            self.spec = spec

        def current_flow_spec(self):
            return self.spec

        async def prompt(self, _text):  # noqa: ANN001
            nonlocal called
            called = True

    services = ProductionRecordingServices(
        recording_id="r1",
        materializer=lambda *_a: (_ for _ in ()).throw(AssertionError()),
        pi_provider=lambda _fresh: Pi(),
        publisher=lambda *_a: (_ for _ in ()).throw(AssertionError()),
    )
    await services.repair(
        _sale_unknown_spec().model_dump(mode="json"),
        (
            WorkflowIssue(
                issue_id="src-product",
                code="field_source_unknown",
                message="来源为 unknown",
                resolver="collect_evidence",
                target={"step_id": "create", "wire_path": "items[0].productId"},
            ),
            WorkflowIssue(
                issue_id="src-barcode",
                code="field_source_unknown",
                message="来源为 unknown",
                resolver="collect_evidence",
                target={"step_id": "create", "wire_path": "items[0].productBarCode"},
            ),
        ),
        {},
        _context(),
    )
    assert called is False
