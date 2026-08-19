"""Stage-seven replay health preflight must not send Pi into a 401 loop."""

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
from dano.onboarding.recording_runtime import ProductionRecordingServices
from dano.onboarding.recording_verify import (
    AUTH_EXPIRED_MESSAGE,
    replay_auth_failed,
    select_preflight_probe,
)
from dano.onboarding.recording_workflow import (
    PipelineContext,
    PipelineSeed,
    SelfHealingPipeline,
    WorkflowActivity,
    WorkflowStatus,
)


def _spec() -> FlowSpec:
    return FlowSpec(
        tenant="tenant",
        subsystem="oa",
        steps=[
            FlowStep(
                step_id="step_get",
                method="GET",
                path="/erp/sale-order/get",
                source_meta={"request_id": "req_get"},
            ),
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
                step_ids=["step_get", "step_edit"],
                nodes=[
                    {
                        "id": "call_get", "type": "call", "usage": "preflight",
                        "request_id": "req_get", "method": "GET",
                        "path": "/erp/sale-order/get", "step_id": "step_get",
                    },
                    {
                        "id": "call_1", "type": "call", "usage": "execute",
                        "request_id": "req_update", "method": "PUT",
                        "path": "/erp/sale-order/update", "step_id": "step_edit",
                    },
                ],
            ),
        ],
        request_facts=RequestFacts(
            requests=[
                RequestFact(request_id="req_get", method="GET", path="/erp/sale-order/get"),
                RequestFact(request_id="req_update", method="PUT", path="/erp/sale-order/update"),
                RequestFact(request_id="req_tenant", method="GET", path="/admin-api/system/tenant/get-by-website"),
            ],
        ),
    )


def _context(activities: list[WorkflowActivity] | None = None) -> PipelineContext:
    async def progress(step, label, round_number=0):  # noqa: ANN001, ANN202
        return None

    async def ask(question):  # noqa: ANN001, ANN202
        raise AssertionError("no operator question expected")

    async def record(activity: WorkflowActivity) -> None:
        if activities is not None:
            activities.append(activity)

    return PipelineContext(
        progress=progress,
        ask_operator=ask,
        cancelled=lambda: False,
        activity=record,
    )


def test_select_preflight_probe_prefers_capability_business_get() -> None:
    probe = select_preflight_probe(_spec())
    assert probe is not None
    assert probe["request_id"] == "req_get"
    assert "tenant" not in str(probe.get("path") or "")
    assert "get-by-website" not in str(probe.get("path") or "")


def test_replay_auth_failed_ignores_token_mentions_in_payload() -> None:
    assert replay_auth_failed(401, {"msg": "未登录"})
    assert replay_auth_failed(200, {"code": 401, "msg": "登录已过期"})
    assert not replay_auth_failed(200, {"token": "abc", "msg": "ok"})


@pytest.mark.asyncio
async def test_auth_failure_returns_editable_without_pi() -> None:
    spec = _spec()
    pi_calls = {"count": 0}

    async def exploding_pi(fresh: bool):  # noqa: FBT001, ANN202
        pi_calls["count"] += 1
        raise AssertionError("Pi must not run after replay auth failure")

    async def unused(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("not used")

    async def replay_executor(request, spec_arg):  # noqa: ANN001, ANN202
        assert "tenant" not in str(request.get("path") or "")
        return {"status": 401, "response": {"code": 401, "msg": "未登录"}}

    services = ProductionRecordingServices(
        recording_id="rec_preflight",
        materializer=unused,
        pi_provider=exploding_pi,
        publisher=unused,
        replay_executor=replay_executor,
    )
    activities: list[WorkflowActivity] = []
    context = _context(activities)
    pipeline = SelfHealingPipeline(CanonicalRecordingRuntime(services.pipeline_services()))
    outcome = await pipeline.run(
        PipelineSeed(
            kind="edited_spec",
            draft=spec.model_dump(mode="json"),
            machine_verification=True,
        ),
        context,
    )
    assert outcome.status == WorkflowStatus.EDITABLE
    assert outcome.issues
    assert all(issue.resolver == "external_blocked" for issue in outcome.issues)
    assert all(AUTH_EXPIRED_MESSAGE in issue.message for issue in outcome.issues)
    preflight = (outcome.draft or {}).get("meta", {}).get("verification_run", {}).get("preflight")
    assert preflight["auth_failed"] is True
    edit_step = next(step for step in (outcome.draft or {}).get("steps") or [] if step["step_id"] == "step_edit")
    remark = next(param for param in edit_step["params"] if param["path"] == "query.remark")
    assert remark["source_kind"] == "user_input"
    assert pi_calls["count"] == 0


@pytest.mark.asyncio
async def test_network_error_does_not_block_repair() -> None:
    spec = _spec()

    async def unused(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("not used")

    async def replay_executor(request, spec_arg):  # noqa: ANN001, ANN202
        raise ConnectionError("dns failed")

    services = ProductionRecordingServices(
        recording_id="rec_net",
        materializer=unused,
        pi_provider=unused,
        publisher=unused,
        replay_executor=replay_executor,
    )
    draft, issues = await services.verify(spec.model_dump(mode="json"), _context())
    assert (draft.get("meta") or {}).get("verification_run", {}).get("preflight", {}).get("auth_failed") is not True
    assert all(issue.resolver != "external_blocked" for issue in issues)
