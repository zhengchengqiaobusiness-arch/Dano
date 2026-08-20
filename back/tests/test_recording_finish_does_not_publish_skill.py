"""Recording finish runs stage 1–7 and must not publish or export a Skill."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from dano.assets.drafts import AssetDraft
from dano.execution.page.flow_spec import FlowCapability, FlowSpec, FlowStep, RequestFact, RequestFacts
from dano.onboarding.recording_pipeline import CanonicalRecordingRuntime
from dano.onboarding.recording_release import ReleaseDecision
from dano.onboarding.recording_runtime import ProductionRecordingServices
from dano.onboarding.recording_results import (
    invalidate_skill_after_capability_edit,
    recording_result_summary,
    recording_skill_lifecycle,
    stage_six_result_body,
)
from dano.onboarding.recording_stage_seven import (
    StageSevenPreflightStatus,
    StageSevenStatus,
    compute_publishable,
    working_fingerprint,
)
from dano.onboarding.recording_workflow import (
    PipelineCheck,
    RecordingWorkflow,
    SelfHealingPipeline,
    WorkflowSnapshot,
    WorkflowStatus,
)
from dano.shared.enums import AssetType, Subsystem


def _query_spec() -> FlowSpec:
    return FlowSpec(
        tenant="tenant",
        subsystem="oa",
        title="查询销售订单",
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


def _saved_result(body: dict) -> AssetDraft:
    return AssetDraft(
        asset_draft_id=uuid4(),
        run_id="recording_saved",
        tenant="tenant",
        subsystem=Subsystem("oa"),
        asset_type=AssetType.PAGE_SCRIPT,
        asset_key="recording-result:action_saved",
        body=body,
        content_hash="hash",
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_recording_finish_does_not_publish_skill() -> None:
    spec = _query_spec()
    published: list[int] = []
    persisted: list[dict] = []

    class _VerifiedRuntime:
        async def prepare(self, seed, context):  # noqa: ANN001, ANN202
            draft = spec.model_dump(mode="json")
            context.remember_draft(draft)
            return draft

        async def check(self, draft, context):  # noqa: ANN001, ANN202
            from dano.onboarding.recording_stage_seven import (
                STAGE_SEVEN_PROTOCOL,
                StageSevenStatus,
                StageSevenVerdict,
                baseline_fingerprint,
                protected_contract_fingerprint,
                working_fingerprint,
            )

            current = FlowSpec.model_validate(draft)
            verdict = StageSevenVerdict(
                protocol=STAGE_SEVEN_PROTOCOL,
                attempt_id="attempt-finish",
                revision=0,
                status=StageSevenStatus.VERIFIED,
                publishable=True,
                all_verified=True,
                baseline_fingerprint=baseline_fingerprint(current),
                protected_contract_fingerprint=protected_contract_fingerprint(current),
                working_fingerprint=working_fingerprint(current),
                preflight={"status": "healthy", "ok": True},
                issues=(),
                unverified=(),
                capability_results={},
                verification_summary={},
                release_status="ready",
                callable_capability_ids=("cap_list",),
            )
            context.stage_seven_verdict = verdict
            return PipelineCheck(draft=draft, issues=(), stage_seven_verdict=verdict)

        async def repair(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            raise AssertionError("verified finish must not repair")

        async def publish(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            published.append(1)
            return {"ok": True, "skill_id": "oa.action_finish"}

    async def persist_stage_six(draft):  # noqa: ANN001
        persisted.append(dict(draft))

    workflow = RecordingWorkflow(
        WorkflowSnapshot(
            run_id="recording_finish",
            action="action_finish",
            status=WorkflowStatus.IDLE,
        ),
        SelfHealingPipeline(_VerifiedRuntime()),
        persist_stage_six=persist_stage_six,
    )
    await workflow.start()
    await workflow.finish(machine_verification=True)
    await workflow.wait()

    assert published == []
    assert persisted, "stage 1–6 result must be persisted"
    assert workflow.snapshot.status == WorkflowStatus.EDITABLE
    assert workflow.snapshot.release is None
    assert workflow.snapshot.progress.label == "能力已验证，Skill 未产出"

    body = stage_six_result_body(
        action="action_finish",
        title="查询销售订单",
        goal="查询",
        tenant="tenant",
        subsystem="oa",
        draft=persisted[0],
        published=False,
        machine_verification_ran=True,
    )
    body["machine_verification_status"] = "verified"
    summary = recording_result_summary(_saved_result(body))
    assert summary["published"] is False
    assert summary["skill_lifecycle"] == "verified_not_exported"


@pytest.mark.asyncio
async def test_recording_finish_without_verification_does_not_publish() -> None:
    spec = _query_spec()
    published: list[int] = []

    class _StageSixRuntime:
        async def prepare(self, seed, context):  # noqa: ANN001, ANN202
            return spec.model_dump(mode="json")

        async def check(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            raise AssertionError("skipped verification must not check")

        async def repair(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            raise AssertionError("skipped verification must not repair")

        async def publish(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            published.append(1)
            return {"ok": True}

    workflow = RecordingWorkflow(
        WorkflowSnapshot(
            run_id="recording_skip",
            action="action_skip",
            status=WorkflowStatus.IDLE,
        ),
        SelfHealingPipeline(_StageSixRuntime()),
    )
    await workflow.start()
    await workflow.finish(machine_verification=False)
    await workflow.wait()
    assert published == []
    assert workflow.snapshot.status == WorkflowStatus.EDITABLE
    assert workflow.snapshot.progress.label == "第 1～6 阶段已完成，Skill 未产出"


@pytest.mark.asyncio
async def test_verified_query_runtime_does_not_call_publisher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = _query_spec()
    published: list[int] = []

    async def unused(*args, **kwargs):  # noqa: ANN002, ANN003, ARG002
        raise AssertionError("materializer is not used for edited_spec")

    async def publisher(*args, **kwargs):  # noqa: ANN002, ANN003, ARG002
        published.append(1)
        return {"ok": True}

    async def replay(request, spec_arg):  # noqa: ANN001, ARG002
        return {"status": 200, "ok": True, "response": {"code": 0}}

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
        recording_id="rec_no_export",
        materializer=unused,
        pi_provider=unused,
        publisher=publisher,
        replay_executor=replay,
    )
    workflow = RecordingWorkflow(
        WorkflowSnapshot(
            run_id="recording_no_export",
            action="action_no_export",
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


def test_verified_recording_result_is_not_exported_until_stage_eight() -> None:
    spec = _query_spec().model_dump(mode="json")
    body = stage_six_result_body(
        action="action_saved",
        title="销售订单管理",
        goal="查询销售订单",
        tenant="tenant",
        subsystem="oa",
        draft=spec,
        published=False,
        machine_verification_ran=True,
    )
    body["machine_verification_status"] = "verified"
    body["stage_seven_fingerprint"] = "fp-verified"
    summary = recording_result_summary(_saved_result(body))
    assert summary["published"] is False
    assert summary["skill_lifecycle"] == "verified_not_exported"
    assert recording_skill_lifecycle(body) == "verified_not_exported"
    assert summary["stage_seven_fingerprint"] == "fp-verified"


def test_existing_stage_one_to_seven_contract_is_unchanged() -> None:
    spec = _query_spec()
    fingerprint = working_fingerprint(spec)
    assert compute_publishable(
        status=StageSevenStatus.VERIFIED,
        all_verified=True,
        unverified=[],
        preflight=StageSevenPreflightStatus.HEALTHY,
        release_status="ready",
        callable_spec=spec,
        baseline=spec,
        working=spec,
        working_fp=fingerprint,
        rechecked_fp=fingerprint,
    ) is True
    assert compute_publishable(
        status=StageSevenStatus.VERIFIED,
        all_verified=False,
        unverified=[{"target_kind": "write_verify", "target_id": "step_edit"}],
        preflight=StageSevenPreflightStatus.HEALTHY,
        release_status="ready",
        callable_spec=spec,
        baseline=spec,
        working=spec,
        working_fp=fingerprint,
        rechecked_fp=fingerprint,
    ) is False
    body = {
        "published": True,
        "skill_id": "oa.leave",
        "skill_export_status": "exported",
        "skill_plan": {"selected_capability_ids": ["cap_list"]},
        "machine_verification_status": "verified",
    }
    stale = invalidate_skill_after_capability_edit(body)
    assert stale["machine_verification_status"] == "stale"
    assert stale["skill_plan_valid"] is False
    assert stale["skill_needs_reexport"] is True
    assert stale["skill_plan"] == body["skill_plan"]
    assert recording_skill_lifecycle(stale) == "needs_reexport"
