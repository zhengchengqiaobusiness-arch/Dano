"""Stage-seven start must paint the analysis page before replay HTTP returns."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from dano.execution.page.flow_spec import (
    FlowCapability,
    FlowSpec,
    FlowStep,
    RequestFact,
    RequestFacts,
)
from dano.onboarding.recording_pipeline import CanonicalRecordingRuntime
from dano.onboarding.recording_runtime import ProductionRecordingServices
from dano.onboarding.recording_workflow import (
    PipelineContext,
    SelfHealingPipeline,
    WorkflowActivity,
    WorkflowProgress,
    WorkflowSnapshot,
    WorkflowStatus,
    WorkflowStep,
)


def _spec() -> FlowSpec:
    return FlowSpec(
        tenant="tenant",
        subsystem="oa",
        steps=[
            FlowStep(
                step_id="step_get",
                method="GET",
                path="/erp/sale-order/page",
                source_meta={"request_id": "req_get"},
                headers={"Authorization": "Bearer secret-token"},
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
                RequestFact(
                    request_id="req_get",
                    method="GET",
                    path="/erp/sale-order/page",
                    headers={"Authorization": "Bearer secret-token"},
                    post_data="{}",
                    response_json={"code": 0, "data": {"list": [{"id": 1}]}},
                ),
            ],
        ),
    )


def _progress_context(progress_labels: list[str], activities: list[WorkflowActivity]) -> PipelineContext:
    async def progress(step, label, round_number=0):  # noqa: ANN001, ANN202
        progress_labels.append(label)

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


def test_processing_snapshot_skips_heavy_flow_spec_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    from dano.onboarding.recording_gateway import workflow_snapshot_client_payload

    def boom(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("processing snapshots must not compile or validate FlowSpec")

    monkeypatch.setattr("dano.onboarding.recording_gateway.validate_flow_spec", boom)
    monkeypatch.setattr("dano.onboarding.recording_gateway.flow_spec_to_client", boom)
    monkeypatch.setattr("dano.onboarding.recording_gateway.flow_spec_fingerprint", boom)

    draft = _spec().model_dump(mode="json")
    payload = workflow_snapshot_client_payload(
        WorkflowSnapshot(
            run_id="recording_start",
            action="action_start",
            status=WorkflowStatus.PROCESSING,
            progress=WorkflowProgress(step=WorkflowStep.VERIFYING, label="正在开始机器验证"),
            draft=draft,
        )
    )
    dumped = json.dumps(payload, ensure_ascii=False)
    assert payload["progress"]["label"] == "正在开始机器验证"
    assert payload["draft"]["capabilities"][0]["title"] == "查询销售订单"
    assert "secret-token" not in dumped
    assert payload.get("check_report") is None


def test_processing_snapshot_payload_stays_fast_if_validate_hangs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dano.onboarding.recording_gateway import workflow_snapshot_client_payload

    def hang(_spec):  # noqa: ANN001
        time.sleep(2)
        return {"passed": True, "errors": []}

    monkeypatch.setattr("dano.onboarding.recording_gateway.validate_flow_spec", hang)
    started = time.perf_counter()
    payload = workflow_snapshot_client_payload(
        WorkflowSnapshot(
            run_id="recording_fast",
            action="action_fast",
            status=WorkflowStatus.PROCESSING,
            progress=WorkflowProgress(step=WorkflowStep.VERIFYING, label="正在开始机器验证"),
            draft=_spec().model_dump(mode="json"),
        )
    )
    assert time.perf_counter() - started < 0.5
    assert payload["draft"]["capabilities"]


@pytest.mark.asyncio
async def test_verify_reports_progress_before_preflight_http_returns() -> None:
    started = asyncio.Event()
    progress_labels: list[str] = []
    activities: list[WorkflowActivity] = []

    async def unused(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("not used")

    async def slow_replay(request, spec_arg):  # noqa: ANN001, ANN202
        started.set()
        await asyncio.sleep(0.4)
        return {"status": 200, "response": {"code": 0, "data": {}}}

    services = ProductionRecordingServices(
        recording_id="rec_progress",
        materializer=unused,
        pi_provider=unused,
        publisher=unused,
        replay_executor=slow_replay,
    )
    task = asyncio.create_task(
        services.verify(_spec().model_dump(mode="json"), _progress_context(progress_labels, activities)),
    )
    await asyncio.wait_for(started.wait(), timeout=2)
    assert any("回放" in label or "登录" in label for label in progress_labels)
    assert any("回放" in item.label or "登录" in item.label for item in activities)
    assert not task.done()
    await task


@pytest.mark.asyncio
async def test_preflight_timeout_does_not_block_stage_seven_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import dano.onboarding.recording_runtime as recording_runtime

    monkeypatch.setattr(recording_runtime, "PREFLIGHT_TIMEOUT_S", 0.05)

    async def unused(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("not used")

    async def hanging_replay(request, spec_arg):  # noqa: ANN001, ANN202
        await asyncio.sleep(2)
        return {"status": 200, "response": {"code": 0}}

    services = ProductionRecordingServices(
        recording_id="rec_timeout",
        materializer=unused,
        pi_provider=unused,
        publisher=unused,
        replay_executor=hanging_replay,
    )
    started = time.perf_counter()
    draft, issues = await services.verify(
        _spec().model_dump(mode="json"),
        _progress_context([], []),
    )
    assert time.perf_counter() - started < 0.5
    preflight = (draft.get("meta") or {}).get("verification_run", {}).get("preflight") or {}
    assert preflight.get("auth_failed") is not True
    assert all(issue.resolver != "external_blocked" for issue in issues)


@pytest.mark.asyncio
async def test_pipeline_snapshot_progress_reaches_listener_before_preflight() -> None:
    snapshots: list[WorkflowSnapshot] = []
    started = asyncio.Event()

    async def unused(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("not used")

    async def slow_replay(request, spec_arg):  # noqa: ANN001, ANN202
        started.set()
        await asyncio.sleep(0.3)
        return {"status": 200, "response": {"code": 0}}

    services = ProductionRecordingServices(
        recording_id="rec_listener",
        materializer=unused,
        pi_provider=unused,
        publisher=unused,
        replay_executor=slow_replay,
    )
    from dano.onboarding.recording_workflow import RecordingWorkflow

    async def listener(snapshot: WorkflowSnapshot) -> None:
        snapshots.append(snapshot)

    workflow = RecordingWorkflow(
        WorkflowSnapshot(
            run_id="recording_listener",
            action="action_listener",
            status=WorkflowStatus.EDITABLE,
            draft=_spec().model_dump(mode="json"),
        ),
        SelfHealingPipeline(CanonicalRecordingRuntime(services.pipeline_services())),
        listener=listener,
    )
    await workflow.republish(machine_verification=True)
    await asyncio.wait_for(started.wait(), timeout=2)
    labels = [item.progress.label for item in snapshots]
    assert "正在开始机器验证" in labels
    assert snapshots[0].draft
    assert snapshots[0].draft.get("capabilities")
    await workflow.cancel()
    await workflow.wait()


def test_recording_result_detail_skips_heavy_projection_and_keeps_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import datetime, timezone
    from uuid import uuid4

    from dano.assets.drafts import AssetDraft
    from dano.onboarding.recording_results import recording_result_detail, stage_six_result_body
    from dano.shared.enums import AssetType, Subsystem

    def boom(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("opening a saved result must not compile FlowSpec")

    monkeypatch.setattr("dano.execution.page.flow_spec.flow_spec_to_client", boom)

    spec = _spec().model_dump(mode="json")
    spec["request_facts"]["requests"][0]["headers"] = {"Authorization": "Bearer secret-token"}
    body = stage_six_result_body(
        action="action_saved",
        title="销售订单管理",
        goal="查询销售订单",
        tenant="tenant",
        subsystem="oa",
        draft=spec,
    )
    saved = AssetDraft(
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
    started = time.perf_counter()
    payload = recording_result_detail(saved)
    assert time.perf_counter() - started < 0.5
    dumped = json.dumps(payload, ensure_ascii=False)
    assert payload["capability_count"] == 1
    assert payload["draft"]["capabilities"][0]["title"] == "查询销售订单"
    assert "secret-token" not in dumped
