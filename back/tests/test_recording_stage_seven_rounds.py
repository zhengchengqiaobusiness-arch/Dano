"""Stage-seven repair must run one capability at a time, anchored in stage six."""

from __future__ import annotations

import pytest

from dano.execution.page.flow_spec import FlowCapability, FlowLink, FlowSpec, FlowStep
from dano.onboarding.recording_runtime import (
    ProductionRecordingServices,
    _capability_brief,
    _capability_repair_prompt,
    _group_issues_by_capability,
)
from dano.onboarding.recording_workflow import (
    PipelineContext,
    WorkflowActivity,
    WorkflowIssue,
)


def _spec() -> FlowSpec:
    return FlowSpec(
        tenant="tenant",
        subsystem="oa",
        steps=[
            FlowStep(step_id="step_edit", method="PUT", path="/erp/sale-order/update"),
            FlowStep(step_id="step_del", method="DELETE", path="/erp/sale-order/delete"),
        ],
        links=[
            FlowLink(
                link_id="link_no",
                source_step_id="step_edit",
                source_path="data.no",
                target_step_id="step_edit",
                target_path="no",
                confirmed=True,
                meta={"verified": True},
            ),
        ],
        capabilities=[
            FlowCapability(
                name="edit_sale_order",
                title="编辑销售订单",
                kind="update",
                capability_id="cap_edit",
                step_ids=["step_edit"],
                nodes=[{
                    "id": "call_1", "type": "call", "usage": "execute",
                    "request_id": "req_98", "method": "PUT",
                    "path": "/erp/sale-order/update", "step_id": "step_edit",
                }],
            ),
            FlowCapability(
                name="delete_sale_order",
                title="删除销售订单",
                kind="delete",
                capability_id="cap_del",
                step_ids=["step_del"],
                nodes=[{
                    "id": "call_1", "type": "call", "usage": "execute",
                    "request_id": "req_104", "method": "DELETE",
                    "path": "/erp/sale-order/delete", "step_id": "step_del",
                }],
            ),
        ],
    )


def _issue(issue_id: str, step_id: str, code: str = "write_verify") -> WorkflowIssue:
    return WorkflowIssue(
        issue_id=issue_id,
        code=code,
        message=f"写操作 `{step_id}` 还没有回读校验",
        resolver="collect_evidence",
        target={"step_id": step_id} if step_id else {},
        allowed_operations=["bind_verify_read"],
    )


def test_group_issues_follow_stage_six_capability_order() -> None:
    spec = _spec()
    issues = (
        _issue("flow:misc", "", code="unassigned_business_step"),
        _issue("write_verify:step_del", "step_del"),
        _issue("write_verify:step_edit", "step_edit"),
    )
    groups = _group_issues_by_capability(spec, issues)
    assert [cap.capability_id if cap else None for cap, _ in groups] == [
        "cap_edit", "cap_del", None,
    ]
    assert [issue.issue_id for issue in groups[0][1]] == ["write_verify:step_edit"]
    assert [issue.issue_id for issue in groups[1][1]] == ["write_verify:step_del"]
    assert [issue.issue_id for issue in groups[2][1]] == ["flow:misc"]


def test_capability_brief_carries_anchor_steps_and_dependencies() -> None:
    spec = _spec()
    brief = _capability_brief(spec, spec.capabilities[0])
    assert brief["name"] == "edit_sale_order"
    assert brief["anchor"]["step_id"] == "step_edit"
    assert brief["anchor"]["method"] == "PUT"
    assert brief["steps"] == [
        {"step_id": "step_edit", "method": "PUT", "path": "/erp/sale-order/update"},
    ]
    assert brief["dependencies"][0]["link_id"] == "link_no"


def test_capability_repair_prompt_scopes_one_capability() -> None:
    spec = _spec()
    capability = spec.capabilities[0]
    prompt = _capability_repair_prompt(
        capability=capability,
        brief=_capability_brief(spec, capability),
        issues=(_issue("write_verify:step_edit", "step_edit"),),
        index=1,
        total=2,
    )
    assert "编辑销售订单" in prompt
    assert "第 1/2 组" in prompt
    assert "阶段六" in prompt
    assert "write_verify:step_edit" in prompt
    assert "step_del" not in prompt
    assert "不要顺手处理" in prompt


class _FakePi:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self._spec: FlowSpec | None = None
        self.last_submission_kind = ""

    def bind_flow_spec(self, spec: FlowSpec) -> None:
        self._spec = spec.model_copy(deep=True)

    def current_flow_spec(self) -> FlowSpec:
        assert self._spec is not None
        return self._spec

    async def prompt(self, text: str) -> None:
        self.prompts.append(text)
        assert self._spec is not None
        for step in self._spec.steps:
            if f'"step_id":"{step.step_id}"' in text:
                step.fact_check = {
                    "verified": True,
                    "verification_id": f"v-{step.step_id}",
                }
        self.last_submission_kind = "repair"


def _context(activities: list[WorkflowActivity]) -> PipelineContext:
    async def progress(step, label, round_number=0):  # noqa: ANN001, ANN202
        return None

    async def ask(question):  # noqa: ANN001, ANN202
        raise AssertionError("no operator question expected")

    async def record(activity: WorkflowActivity) -> None:
        activities.append(activity)

    return PipelineContext(
        progress=progress,
        ask_operator=ask,
        cancelled=lambda: False,
        activity=record,
    )


@pytest.mark.asyncio
async def test_repair_prompts_pi_once_per_capability() -> None:
    spec = _spec()
    pi = _FakePi()

    async def pi_provider(fresh: bool) -> _FakePi:  # noqa: FBT001
        return pi

    async def materializer(use_live_notebook, context):  # noqa: ANN001, ANN202
        raise AssertionError("materializer not used")

    async def publisher(release_spec, candidate, context):  # noqa: ANN001, ANN202
        raise AssertionError("publisher not used")

    services = ProductionRecordingServices(
        recording_id="rec_1",
        materializer=materializer,
        pi_provider=pi_provider,
        publisher=publisher,
    )
    activities: list[WorkflowActivity] = []
    context = _context(activities)
    context.current_round = 3
    issues = (
        _issue("write_verify:step_edit", "step_edit"),
        _issue("write_verify:step_del", "step_del"),
    )
    repaired = await services.repair(spec.model_dump(mode="json"), issues, {}, context)

    assert len(pi.prompts) == 2
    assert "编辑销售订单" in pi.prompts[0]
    assert "write_verify:step_edit" in pi.prompts[0]
    assert "write_verify:step_del" not in pi.prompts[0]
    assert "删除销售订单" in pi.prompts[1]
    assert "write_verify:step_del" in pi.prompts[1]

    report = context.last_repair_report
    assert report is not None
    assert "write_verify:step_edit" in report.resolved
    assert "write_verify:step_del" in report.resolved
    assert not report.still_pending

    labels = [activity.label for activity in activities]
    assert any("编辑销售订单" in label and "第 1/2 组" in label for label in labels)
    assert any("删除销售订单" in label and "第 2/2 组" in label for label in labels)
    assert any("解决 1 项" in label for label in labels)
    assert all(activity.round == 3 for activity in activities)
    assert repaired["capabilities"]


@pytest.mark.asyncio
async def test_repair_keeps_meta_only_progress_without_fingerprint_change() -> None:
    """fact_check binding does not move the execution fingerprint but must be kept."""
    spec = _spec()
    pi = _FakePi()

    async def pi_provider(fresh: bool) -> _FakePi:  # noqa: FBT001
        return pi

    async def unused(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("not used")

    services = ProductionRecordingServices(
        recording_id="rec_2",
        materializer=unused,
        pi_provider=pi_provider,
        publisher=unused,
    )
    activities: list[WorkflowActivity] = []
    context = _context(activities)
    issues = (_issue("write_verify:step_edit", "step_edit"),)
    repaired = await services.repair(spec.model_dump(mode="json"), issues, {}, context)

    edit_step = next(
        step for step in repaired["steps"] if step["step_id"] == "step_edit"
    )
    assert edit_step["fact_check"]["verified"] is True
    report = context.last_repair_report
    assert report is not None
    assert "write_verify:step_edit" in report.resolved


@pytest.mark.asyncio
async def test_repair_continues_after_one_capability_fails() -> None:
    spec = _spec()

    class _FailFirstPi(_FakePi):
        async def prompt(self, text: str) -> None:
            if "step_del" not in text:
                self.prompts.append(text)
                self.last_submission_kind = ""
                raise RuntimeError("replay failed: 401 未登录")
            await super().prompt(text)

    pi = _FailFirstPi()

    async def pi_provider(fresh: bool) -> _FakePi:  # noqa: FBT001
        return pi

    async def unused(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("not used")

    services = ProductionRecordingServices(
        recording_id="rec_3",
        materializer=unused,
        pi_provider=pi_provider,
        publisher=unused,
    )
    activities: list[WorkflowActivity] = []
    context = _context(activities)
    issues = (
        _issue("write_verify:step_edit", "step_edit"),
        _issue("write_verify:step_del", "step_del"),
    )
    await services.repair(spec.model_dump(mode="json"), issues, {}, context)

    report = context.last_repair_report
    assert report is not None
    assert "write_verify:step_edit" in report.still_pending
    assert "write_verify:step_del" in report.resolved
    labels = [activity.label for activity in activities]
    assert any("修复未落地" in label for label in labels)
    assert any("删除销售订单" in label for label in labels)


class _NoopPi(_FakePi):
    async def prompt(self, text: str) -> None:
        self.prompts.append(text)
        self.last_submission_kind = "repair"


@pytest.mark.asyncio
async def test_capability_repair_budget_marks_unverified() -> None:
    spec = _spec()
    pi = _NoopPi()

    async def pi_provider(fresh: bool) -> _FakePi:  # noqa: FBT001
        return pi

    async def unused(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("not used")

    services = ProductionRecordingServices(
        recording_id="rec_budget",
        materializer=unused,
        pi_provider=pi_provider,
        publisher=unused,
    )
    activities: list[WorkflowActivity] = []
    context = _context(activities)
    issues = (_issue("write_verify:step_edit", "step_edit"),)
    payload = spec.model_dump(mode="json")
    await services.repair(payload, issues, {}, context)
    await services.repair(payload, issues, {}, context)
    repaired = await services.repair(payload, issues, {}, context)

    assert len(pi.prompts) == 2
    unverified = repaired["meta"]["unverified"]
    assert any(
        item["target_kind"] == "write_verify"
        and item["target_id"] == "step_edit"
        and item["actor"] == "orchestrator"
        for item in unverified
    )
    assert repaired["meta"]["capability_verification"]["cap_edit"]["status"] == "blocked"
    assert any("修复预算" in activity.label for activity in activities)


@pytest.mark.asyncio
async def test_repair_records_capability_verification_and_flow_group_key() -> None:
    spec = _spec()
    spec.steps.append(FlowStep(step_id="step_orphan", method="DELETE", path="/erp/sale-order/delete-batch"))
    pi = _FakePi()

    async def pi_provider(fresh: bool) -> _FakePi:  # noqa: FBT001
        return pi

    async def unused(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("not used")

    services = ProductionRecordingServices(
        recording_id="rec_flow",
        materializer=unused,
        pi_provider=pi_provider,
        publisher=unused,
    )
    context = _context([])
    issues = (
        _issue("write_verify:step_edit", "step_edit"),
        _issue("write_verify:step_orphan", "step_orphan"),
    )
    repaired = await services.repair(spec.model_dump(mode="json"), issues, {}, context)
    verification = repaired["meta"]["capability_verification"]
    assert verification["cap_edit"]["status"] == "verified"
    assert "__flow__" in verification
    assert context.capability_rounds["cap_edit"] == 1
    assert context.capability_rounds["__flow__"] == 1


def test_plan_thought_lists_capabilities_sequentially() -> None:
    from dano.onboarding.recording_workflow import _issues_grouped_by_capability, _plan_thought

    spec = _spec()
    issues = (
        _issue("write_verify:step_del", "step_del"),
        _issue("write_verify:step_edit", "step_edit"),
        _issue("write_verify:step_edit_enum", "step_edit", code="enum"),
    )
    groups = _issues_grouped_by_capability(spec.model_dump(mode="json"), issues)
    assert [title for _, title, _ in groups] == ["编辑销售订单", "删除销售订单"]
    assert len(groups[0][2]) == 2
    assert len(groups[1][2]) == 1
    plan = _plan_thought(spec.model_dump(mode="json"), issues)
    assert plan.index("编辑销售订单") < plan.index("删除销售订单")
    assert "一次只处理一个能力" in plan
