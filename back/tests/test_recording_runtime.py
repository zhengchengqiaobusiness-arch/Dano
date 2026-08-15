from __future__ import annotations

import pytest

from dano.execution.page.flow_spec import FlowSpec, FlowStep, RequestFact, RequestFacts
from dano.execution.page.flow_spec import recording_agent_validation
from dano.onboarding.recording_runtime import (
    ProductionRecordingServices,
    _submit_with_protocol_recovery,
    _todo_issue,
)
from dano.onboarding.recording_workflow import PipelineContext


async def _progress(*_args) -> None:  # noqa: ANN002
    return None


async def _ask(_question) -> str:  # noqa: ANN001
    return "answer"


def _context() -> PipelineContext:
    return PipelineContext(progress=_progress, ask_operator=_ask, cancelled=lambda: False)


@pytest.mark.asyncio
async def test_materializer_receives_live_notebook_policy() -> None:
    policies: list[bool] = []

    async def materialize(use_live, _context):  # noqa: ANN001
        policies.append(use_live)
        return FlowSpec(tenant="tenant", subsystem="system")

    async def pi_provider(_fresh):  # noqa: ANN001
        raise AssertionError("Pi is not needed")

    async def publish(*_args):  # noqa: ANN002
        return {}

    services = ProductionRecordingServices(
        recording_id="recording_1",
        materializer=materialize,
        pi_provider=pi_provider,
        publisher=publish,
    )
    draft = await services.materialize_recording(True, _context())

    assert draft["tenant"] == "tenant"
    assert policies == [True]


def test_verification_todo_becomes_structured_generic_issue() -> None:
    from dano.onboarding.recording_runtime import _todo_issue

    issue = _todo_issue({
        "kind": "write_verify",
        "target_id": "step-1",
        "step_id": "step-1",
        "suggested_tool": "execute_write_with_verify",
    })

    assert issue.code == "write_verify"
    assert issue.resolver == "collect_evidence"
    assert issue.target["step_id"] == "step-1"
    assert issue.allowed_operations == ["execute_write_with_verify"]


def test_validation_distinguishes_structure_verification_and_release_readiness() -> None:
    validation = recording_agent_validation(FlowSpec())

    assert validation["structural_valid"] is validation["report"]["passed"]
    assert validation["verification_complete"] is validation["report"]["recording_verification"]["all_verified"]
    assert validation["release_ready"] is False


@pytest.mark.asyncio
async def test_pi_protocol_error_is_retried_inside_the_same_operation() -> None:
    class Pi:
        last_submission_kind = ""
        prompts: list[str] = []

        async def prompt(self, prompt: str) -> None:
            self.prompts.append(prompt)
            if len(self.prompts) == 1:
                raise ValueError("unknown field: evidence")
            self.last_submission_kind = "plan"

    pi = Pi()
    await _submit_with_protocol_recovery(
        pi,
        prompt="生成能力",
        accepted_kinds={"plan"},
        context=_context(),
    )

    assert len(pi.prompts) == 2
    assert "只使用工具 schema 声明的字段" in pi.prompts[1]


@pytest.mark.asyncio
async def test_repair_protocol_recovery_requires_repair_tool_and_reports_last_error() -> None:
    class Pi:
        last_submission_kind = ""
        prompts: list[str] = []

        async def prompt(self, prompt: str) -> None:
            self.prompts.append(prompt)
            if len(self.prompts) == 1:
                raise ValueError("plan.ops[0] 包含未知字段: evidence")
            self.last_submission_kind = (
                "repair" if "只调用 submit_recording_repair" in prompt else "plan"
            )

    pi = Pi()
    await _submit_with_protocol_recovery(
        pi,
        prompt="修复能力",
        accepted_kinds={"repair"},
        context=_context(),
    )

    assert len(pi.prompts) == 2
    assert "plan.ops[0] 包含未知字段: evidence" in pi.prompts[1]
    assert "只调用 submit_recording_repair" in pi.prompts[1]


@pytest.mark.asyncio
async def test_verify_consumes_passed_write_evidence_before_returning_todos() -> None:
    verification_id = "write-verification"
    spec = FlowSpec(
        steps=[FlowStep(
            step_id="submit",
            method="POST",
            path="/submit",
            source_meta={"request_id": "req-submit"},
        )],
        request_facts=RequestFacts(requests=[
            RequestFact(request_id="req-submit", method="POST", path="/submit"),
            RequestFact(request_id="req-read", method="GET", path="/items"),
        ]),
        meta={"verification_log": [{
            "verification_id": verification_id,
            "kind": "write_execute",
            "status": "passed",
            "subject": {
                "write_step_id": "submit",
                "write_request_id": "req-submit",
                "verify_request_id": "req-read",
                "assertion": {"path": "code", "operator": "equals", "value": 0},
            },
        }]},
    )

    async def unused(*_args):  # noqa: ANN002
        raise AssertionError("service is not needed")

    services = ProductionRecordingServices(
        recording_id="recording_1",
        materializer=unused,
        pi_provider=unused,
        publisher=unused,
    )

    draft, issues = await services.verify(spec.model_dump(mode="json"), _context())

    assert not any(issue.code == "write_verify" for issue in issues)
    assert FlowSpec.model_validate(draft).steps[0].fact_check["verification_id"] == verification_id


@pytest.mark.asyncio
async def test_repair_requires_repair_submission_instead_of_accepting_a_new_plan() -> None:
    class Pi:
        last_submission_kind = ""
        prompts: list[str] = []

        def __init__(self, spec: FlowSpec) -> None:
            self.spec = spec

        def bind_flow_spec(self, spec: FlowSpec) -> None:
            self.spec = spec

        async def prompt(self, prompt: str) -> None:
            self.prompts.append(prompt)
            if len(self.prompts) == 1:
                self.spec.title = "错误计划不应保留"
                self.last_submission_kind = "plan"
            else:
                self.last_submission_kind = "repair"

        def current_flow_spec(self) -> FlowSpec:
            return self.spec

    pi = Pi(FlowSpec())

    async def provide_pi(_fresh):  # noqa: ANN001
        return pi

    async def unused(*_args):  # noqa: ANN002
        raise AssertionError("service is not needed")

    services = ProductionRecordingServices(
        recording_id="recording_1",
        materializer=unused,
        pi_provider=provide_pi,
        publisher=unused,
    )
    issue = _todo_issue({
        "kind": "write_verify",
        "target_id": "submit",
        "step_id": "submit",
        "suggested_tool": "execute_write_with_verify",
    })

    await services.repair(
        FlowSpec().model_dump(mode="json"),
        (issue,),
        {},
        _context(),
    )

    assert len(pi.prompts) == 2
    assert pi.last_submission_kind == "repair"
    assert pi.spec.title == ""


@pytest.mark.asyncio
async def test_deterministic_normalization_does_not_skip_pi_repair(monkeypatch) -> None:
    class Pi:
        last_submission_kind = ""
        prompts: list[str] = []

        def __init__(self) -> None:
            self.spec = FlowSpec()

        def bind_flow_spec(self, spec: FlowSpec) -> None:
            self.spec = spec

        async def prompt(self, prompt: str) -> None:
            self.prompts.append(prompt)
            self.last_submission_kind = "repair"

        def current_flow_spec(self) -> FlowSpec:
            return self.spec

    async def normalize(spec, **_kwargs):  # noqa: ANN001
        normalized = spec.model_copy(deep=True)
        normalized.title = "deterministically-normalized"
        return normalized

    pi = Pi()

    async def provide_pi(_fresh):  # noqa: ANN001
        return pi

    async def unused(*_args):  # noqa: ANN002
        raise AssertionError("service is not needed")

    monkeypatch.setattr("dano.onboarding.recording_runtime.auto_fix_flow_spec", normalize)
    services = ProductionRecordingServices(
        recording_id="recording_1",
        materializer=unused,
        pi_provider=provide_pi,
        publisher=unused,
    )
    issue = _todo_issue({
        "kind": "dependency",
        "target_id": "dep-1",
        "suggested_tool": "perturb_replay",
    })

    repaired = await services.repair(
        FlowSpec().model_dump(mode="json"),
        (issue,),
        {},
        _context(),
    )

    assert len(pi.prompts) == 1
    assert repaired["title"] == "deterministically-normalized"


@pytest.mark.asyncio
async def test_direct_export_freezes_candidate_without_release_verification() -> None:
    class Pi:
        bound: FlowSpec | None = None

        def bind_flow_spec(self, spec: FlowSpec) -> None:
            self.bound = spec

    pi = Pi()
    published: dict = {}

    async def provide_pi(_fresh):  # noqa: ANN001
        return pi

    async def publisher(spec, candidate, _context):  # noqa: ANN001
        published.update({"spec": spec, "candidate": candidate})
        return {"skill_id": "direct-skill"}

    async def unused(*_args):  # noqa: ANN002
        raise AssertionError("service is not needed")

    services = ProductionRecordingServices(
        recording_id="recording_1",
        materializer=unused,
        pi_provider=provide_pi,
        publisher=publisher,
    )
    context = _context()
    context.machine_verification = False

    release = await services.publish(FlowSpec().model_dump(mode="json"), context)

    assert release == {"skill_id": "direct-skill"}
    assert published["candidate"]["machine_verification"] == {
        "enabled": False,
        "status": "skipped_by_operator",
    }
    assert pi.bound is published["spec"]


@pytest.mark.asyncio
async def test_direct_export_rejects_a_draft_that_misses_the_recording_goal_count() -> None:
    async def unused(*_args):  # noqa: ANN002
        raise AssertionError("service is not needed")

    services = ProductionRecordingServices(
        recording_id="recording_1",
        materializer=unused,
        pi_provider=unused,
        publisher=unused,
    )
    context = _context()
    context.machine_verification = False
    draft = FlowSpec(meta={
        "recording_goal_contract": {"expected_count": 2, "materialized_count": 0},
    })

    with pytest.raises(RuntimeError, match="要求产出 2 个能力"):
        await services.publish(draft.model_dump(mode="json"), context)
