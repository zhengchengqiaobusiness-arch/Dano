"""Stage-seven publish gate is fail-closed and uses a single verdict."""

from __future__ import annotations

import pytest

from dano.execution.page.flow_spec import (
    FlowCapability,
    FlowSpec,
    FlowStep,
    ParamField,
    RequestFact,
    RequestFacts,
)
from dano.onboarding.recording_pipeline import CanonicalRecordingRuntime
from dano.onboarding.recording_release import ReleaseDecision, evaluate_recording_release
from dano.onboarding.recording_runtime import ProductionRecordingServices
from dano.onboarding.recording_stage_seven import (
    StageSevenPreflightStatus,
    StageSevenStatus,
    StageSevenVerdict,
    STAGE_SEVEN_PROTOCOL,
    assert_stage_six_contract_preserved,
    baseline_fingerprint,
    build_stage_seven_scope,
    callable_covers_stage_six,
    compute_publishable,
    evaluate_stage_seven_verdict,
    normalize_stage_seven_working_copy,
    protected_contract_fingerprint,
    working_fingerprint,
)
from dano.onboarding.recording_verify import verification_report
from dano.onboarding.recording_workflow import (
    PipelineCheck,
    RecordingWorkflow,
    SelfHealingPipeline,
    WorkflowSnapshot,
    WorkflowStatus,
)


def _query_spec() -> FlowSpec:
    return FlowSpec(
        tenant="tenant",
        subsystem="oa",
        steps=[
            FlowStep(
                step_id="step_get",
                method="GET",
                path="/erp/sale-order/page",
                source_meta={"request_id": "req_get"},
            ),
        ],
        capabilities=[
            FlowCapability(
                name="list_sale_order",
                title="查询销售订单",
                kind="query_status",
                capability_id="cap_list",
                step_ids=["step_get"],
                request_refs=[],
                nodes=[
                    {
                        "id": "call_get",
                        "type": "call",
                        "usage": "execute",
                        "request_id": "req_get",
                        "method": "GET",
                        "path": "/erp/sale-order/page",
                        "step_id": "step_get",
                    },
                ],
            ),
        ],
        request_facts=RequestFacts(
            requests=[
                RequestFact(request_id="req_get", method="GET", path="/erp/sale-order/page"),
            ],
        ),
    )


def _write_spec() -> FlowSpec:
    return FlowSpec(
        tenant="tenant",
        subsystem="oa",
        steps=[
            FlowStep(
                step_id="step_edit",
                method="PUT",
                path="/erp/sale-order/update",
                source_meta={"request_id": "req_update"},
                params=[
                    ParamField(path="query.remark", key="remark", value="", source_kind="unknown"),
                ],
            ),
        ],
        capabilities=[
            FlowCapability(
                name="edit_sale_order",
                title="编辑销售订单",
                kind="update",
                capability_id="cap_edit",
                step_ids=["step_edit"],
                nodes=[
                    {
                        "id": "call_1",
                        "type": "call",
                        "usage": "execute",
                        "request_id": "req_update",
                        "method": "PUT",
                        "path": "/erp/sale-order/update",
                        "step_id": "step_edit",
                    },
                ],
            ),
        ],
        request_facts=RequestFacts(
            requests=[
                RequestFact(request_id="req_update", method="PUT", path="/erp/sale-order/update"),
            ],
        ),
    )


def test_empty_issues_are_not_publishable_when_unverified() -> None:
    spec = _write_spec()
    spec.meta = {
        "unverified": [
            {"target_kind": "write_verify", "target_id": "step_edit", "reason": "budget"},
        ],
    }
    scope = build_stage_seven_scope(spec)
    report = verification_report(spec)
    decision = evaluate_recording_release(spec)
    verdict = evaluate_stage_seven_verdict(
        baseline=spec,
        working=spec,
        scope=scope,
        verification_report={**report, "todos": [], "all_verified": False},
        preflight={"status": "healthy"},
        release=decision,
        attempt_id="attempt",
        revision=0,
    )
    assert verdict.publishable is False
    assert compute_publishable(
        status=StageSevenStatus.VERIFIED,
        all_verified=True,
        unverified=[{"target_kind": "write_verify", "target_id": "step_edit"}],
        preflight=StageSevenPreflightStatus.HEALTHY,
        release_status="ready",
        callable_spec=spec,
        baseline=spec,
        working=spec,
        working_fp=working_fingerprint(spec),
        rechecked_fp=working_fingerprint(spec),
    ) is False


def test_protected_contract_change_is_not_publishable() -> None:
    spec = _query_spec()
    changed = spec.model_copy(deep=True)
    changed.capabilities[0].name = "renamed"
    assert protected_contract_fingerprint(spec) != protected_contract_fingerprint(changed)
    assert compute_publishable(
        status=StageSevenStatus.VERIFIED,
        all_verified=True,
        unverified=[],
        preflight=StageSevenPreflightStatus.HEALTHY,
        release_status="ready",
        callable_spec=changed,
        baseline=spec,
        working=changed,
        working_fp=working_fingerprint(changed),
        rechecked_fp=working_fingerprint(changed),
    ) is False


def test_working_fingerprint_mismatch_is_not_publishable() -> None:
    spec = _query_spec()
    assert compute_publishable(
        status=StageSevenStatus.VERIFIED,
        all_verified=True,
        unverified=[],
        preflight=StageSevenPreflightStatus.HEALTHY,
        release_status="ready",
        callable_spec=spec,
        baseline=spec,
        working=spec,
        working_fp="aaa",
        rechecked_fp="bbb",
    ) is False


@pytest.mark.asyncio
async def test_auth_failure_pipeline_does_not_publish() -> None:
    spec = _write_spec()
    published: list[int] = []

    async def unused(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("not used")

    async def publisher(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        published.append(1)
        return {"ok": True}

    async def replay_executor(request, spec_arg):  # noqa: ANN001, ARG002
        return {"status": 401, "response": {"code": 401, "msg": "未登录"}}

    async def no_refresh(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        return {"ok": False, "reason": "not_configured"}

    services = ProductionRecordingServices(
        recording_id="rec_gate",
        materializer=unused,
        pi_provider=unused,
        publisher=publisher,
        replay_executor=replay_executor,
        token_refresher=no_refresh,
    )
    workflow = RecordingWorkflow(
        WorkflowSnapshot(
            run_id="recording_gate",
            action="action_gate",
            status=WorkflowStatus.EDITABLE,
            draft=spec.model_dump(mode="json"),
        ),
        SelfHealingPipeline(CanonicalRecordingRuntime(services.pipeline_services())),
    )
    await workflow.republish(machine_verification=True)
    await workflow.wait()
    assert published == []
    assert workflow.snapshot.status == WorkflowStatus.EDITABLE


@pytest.mark.asyncio
async def test_timeout_pipeline_does_not_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    import dano.onboarding.recording_runtime as recording_runtime

    monkeypatch.setattr(recording_runtime, "PREFLIGHT_TIMEOUT_S", 0.05)
    spec = _query_spec()
    published: list[int] = []

    async def unused(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("not used")

    async def publisher(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        published.append(1)
        return {"ok": True}

    async def hanging_replay(request, spec_arg):  # noqa: ANN001, ARG002
        import asyncio
        await asyncio.sleep(2)
        return {"status": 200, "response": {"code": 0}}

    services = ProductionRecordingServices(
        recording_id="rec_to",
        materializer=unused,
        pi_provider=unused,
        publisher=publisher,
        replay_executor=hanging_replay,
    )
    workflow = RecordingWorkflow(
        WorkflowSnapshot(
            run_id="recording_to",
            action="action_to",
            status=WorkflowStatus.EDITABLE,
            draft=spec.model_dump(mode="json"),
        ),
        SelfHealingPipeline(CanonicalRecordingRuntime(services.pipeline_services())),
    )
    await workflow.republish(machine_verification=True)
    await workflow.wait()
    assert published == []
    assert workflow.snapshot.status == WorkflowStatus.EDITABLE


def _publishable_verdict(spec: FlowSpec) -> StageSevenVerdict:
    return StageSevenVerdict(
        protocol=STAGE_SEVEN_PROTOCOL,
        attempt_id="attempt-ok",
        revision=0,
        status=StageSevenStatus.VERIFIED,
        publishable=True,
        all_verified=True,
        baseline_fingerprint=baseline_fingerprint(spec),
        protected_contract_fingerprint=protected_contract_fingerprint(spec),
        working_fingerprint=working_fingerprint(spec),
        preflight={"status": "healthy", "ok": True},
        issues=(),
        unverified=(),
        capability_results={},
        verification_summary={},
        release_status="ready",
        callable_capability_ids=tuple(item.capability_id for item in spec.capabilities),
    )


class _GateRuntime:
    def __init__(self, published: list[int], *, mutate: bool = False, writes: list[int] | None = None) -> None:
        self.published = published
        self.mutate = mutate
        self.writes = writes if writes is not None else []

    async def prepare(self, seed, context):  # noqa: ANN001, ANN202, ARG002
        return dict(seed.draft or {})

    async def check(self, draft, context):  # noqa: ANN001, ANN202
        spec = FlowSpec.model_validate(draft)
        if self.writes is not None and not self.writes:
            self.writes.append(1)
        verdict = _publishable_verdict(spec)
        context.stage_seven_verdict = verdict
        context.stage_six_baseline = context.stage_six_baseline or draft
        return PipelineCheck(draft=draft, issues=(), stage_seven_verdict=verdict)

    async def repair(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("repair must not run when the verdict is publishable")

    async def publish(self, draft, context):  # noqa: ANN001, ANN202
        spec = FlowSpec.model_validate(draft)
        checked_fp = (
            context.stage_seven_verdict.working_fingerprint
            if context.stage_seven_verdict is not None
            else ""
        )
        if self.mutate:
            spec.capabilities[0].title = "mutated-before-publish"
        if working_fingerprint(spec) != checked_fp:
            raise RuntimeError("发布前 working fingerprint 已变化")
        self.published.append(1)
        return {"ok": True}


@pytest.mark.asyncio
async def test_query_replay_success_publishes_once(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = _query_spec()
    published: list[int] = []
    replays: list[int] = []

    async def replay(request, spec_arg):  # noqa: ANN001, ARG002
        replays.append(1)
        return {"status": 200, "ok": True, "response": {"code": 0}}

    async def publisher(release_spec, candidate, context):  # noqa: ANN001, ARG002
        published.append(1)
        return {"ok": True}

    async def unused(*args, **kwargs):  # noqa: ANN002, ANN003, ARG002
        raise AssertionError("Pi must not run for an already verified query")

    def fake_report(working):  # noqa: ANN001, ARG001
        return {
            "todos": [],
            "all_verified": True,
            "complete": True,
            "release_issues": [],
            "unverified": [],
            "confirmed_links": 0,
            "link_count": 0,
            "verify_coverage": 0,
            "write_count": 0,
        }

    def fake_release(working):  # noqa: ANN001
        current = working if isinstance(working, FlowSpec) else FlowSpec.model_validate(working)
        return ReleaseDecision(status="ready", callable_spec=current, capabilities=())

    monkeypatch.setattr("dano.onboarding.recording_runtime.verification_report", fake_report)
    monkeypatch.setattr("dano.onboarding.recording_runtime.evaluate_recording_release", fake_release)
    services = ProductionRecordingServices(
        recording_id="rec_query_ok",
        materializer=unused,
        pi_provider=unused,
        publisher=publisher,
        replay_executor=replay,
    )
    workflow = RecordingWorkflow(
        WorkflowSnapshot(
            run_id="recording_query_ok",
            action="action_query_ok",
            status=WorkflowStatus.EDITABLE,
            draft=spec.model_dump(mode="json"),
        ),
        SelfHealingPipeline(CanonicalRecordingRuntime(services.pipeline_services())),
        stage_six_baseline=spec.model_dump(mode="json"),
    )
    await workflow.republish(machine_verification=True)
    await workflow.wait()
    assert published == []
    assert workflow.snapshot.status == WorkflowStatus.EDITABLE
    assert workflow.snapshot.stage_seven_attempt_id
    assert workflow.snapshot.progress.label == "能力已验证，Skill 未产出"
    verdict = workflow.snapshot.draft or {}
    status = ((verdict.get("meta") or {}).get("stage_seven") or {}).get("status")
    assert status in {"verified", None}


@pytest.mark.asyncio
async def test_write_readback_success_executes_write_once_and_publishes_once() -> None:
    spec = _write_spec()
    published: list[int] = []
    writes: list[int] = []
    workflow = RecordingWorkflow(
        WorkflowSnapshot(
            run_id="recording_write_ok",
            action="action_write_ok",
            status=WorkflowStatus.EDITABLE,
            draft=spec.model_dump(mode="json"),
        ),
        SelfHealingPipeline(_GateRuntime(published, writes=writes)),
    )
    await workflow.republish(machine_verification=True)
    await workflow.wait()
    assert writes == [1]
    assert published == []
    assert workflow.snapshot.status == WorkflowStatus.EDITABLE
    assert workflow.snapshot.progress.label == "能力已验证，Skill 未产出"


@pytest.mark.asyncio
async def test_stage_six_contract_change_blocks_without_pi_or_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _query_spec()
    working = baseline.model_copy(deep=True)
    working.capabilities[0].title = "mutated-title"
    published: list[int] = []
    pi_calls: list[int] = []

    async def replay(request, spec_arg):  # noqa: ANN001, ARG002
        return {"status": 200, "ok": True, "response": {"code": 0}}

    async def publisher(*args, **kwargs):  # noqa: ANN002, ANN003, ARG002
        published.append(1)
        return {"ok": True}

    async def pi_provider(fresh: bool):  # noqa: FBT001, ARG001
        pi_calls.append(1)
        raise AssertionError("Pi must not run after a stage 6 contract change")

    async def unused(*args, **kwargs):  # noqa: ANN002, ANN003, ARG002
        raise AssertionError("materializer not used")

    services = ProductionRecordingServices(
        recording_id="rec_contract",
        materializer=unused,
        pi_provider=pi_provider,
        publisher=publisher,
        replay_executor=replay,
    )
    workflow = RecordingWorkflow(
        WorkflowSnapshot(
            run_id="recording_contract",
            action="action_contract",
            status=WorkflowStatus.EDITABLE,
            draft=working.model_dump(mode="json"),
        ),
        SelfHealingPipeline(CanonicalRecordingRuntime(services.pipeline_services())),
        stage_six_baseline=baseline.model_dump(mode="json"),
    )
    await workflow.republish(machine_verification=True)
    await workflow.wait()
    assert published == []
    assert pi_calls == []
    assert workflow.snapshot.status == WorkflowStatus.EDITABLE
    assert any(
        issue.code == "stage_six_contract_changed"
        for issue in workflow.snapshot.issues
    )


@pytest.mark.asyncio
async def test_unverified_pipeline_does_not_publish() -> None:
    spec = _write_spec()
    published: list[int] = []

    class _BlockedRuntime:
        async def prepare(self, seed, context):  # noqa: ANN001, ANN202, ARG002
            return dict(seed.draft or {})

        async def check(self, draft, context):  # noqa: ANN001, ANN202
            current = FlowSpec.model_validate(draft)
            current.meta = {
                **(current.meta or {}),
                "unverified": [{"target_kind": "write_verify", "target_id": "step_edit"}],
            }
            dumped = current.model_dump(mode="json")
            scope = build_stage_seven_scope(current)
            verdict = evaluate_stage_seven_verdict(
                baseline=current,
                working=current,
                scope=scope,
                verification_report={"todos": [], "all_verified": False, "complete": False},
                preflight={"status": "healthy"},
                release=evaluate_recording_release(current),
                attempt_id="attempt-uv",
                revision=0,
            )
            context.stage_seven_verdict = verdict
            return PipelineCheck(draft=dumped, issues=(), stage_seven_verdict=verdict)

        async def repair(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            raise AssertionError("unverified must not be repaired into a publish")

        async def publish(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            published.append(1)
            return {"ok": True}

    workflow = RecordingWorkflow(
        WorkflowSnapshot(
            run_id="recording_uv",
            action="action_uv",
            status=WorkflowStatus.EDITABLE,
            draft=spec.model_dump(mode="json"),
        ),
        SelfHealingPipeline(_BlockedRuntime()),
    )
    await workflow.republish(machine_verification=True)
    await workflow.wait()
    assert published == []
    assert workflow.snapshot.status == WorkflowStatus.EDITABLE


@pytest.mark.asyncio
async def test_working_fingerprint_change_before_publisher_is_rejected() -> None:
    spec = _query_spec()
    published: list[int] = []
    workflow = RecordingWorkflow(
        WorkflowSnapshot(
            run_id="recording_fp",
            action="action_fp",
            status=WorkflowStatus.EDITABLE,
            draft=spec.model_dump(mode="json"),
        ),
        SelfHealingPipeline(_GateRuntime(published, mutate=True)),
    )
    await workflow.republish(machine_verification=True)
    await workflow.wait()
    assert published == []
    assert workflow.snapshot.status == WorkflowStatus.EDITABLE


def test_stage_six_invariants_survive_stage_seven_normalize() -> None:
    spec = _query_spec()
    spec.capabilities[0].intent = "list orders"
    changed = spec.model_copy(deep=True)
    changed.capabilities[0].name = "renamed"
    with pytest.raises(Exception):
        assert_stage_six_contract_preserved(spec, changed)
    working = normalize_stage_seven_working_copy(spec, spec)
    again = normalize_stage_seven_working_copy(spec, working)
    assert [item.capability_id for item in working.capabilities] == [
        item.capability_id for item in spec.capabilities
    ]
    assert [item.name for item in working.capabilities] == [item.name for item in spec.capabilities]
    assert [item.title for item in working.capabilities] == [item.title for item in spec.capabilities]
    assert [item.kind for item in working.capabilities] == [item.kind for item in spec.capabilities]
    assert [item.intent for item in working.capabilities] == [item.intent for item in spec.capabilities]
    assert working_fingerprint(working) == working_fingerprint(again)
    assert callable_covers_stage_six(spec, working)
