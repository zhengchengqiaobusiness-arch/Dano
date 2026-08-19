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
from dano.infra.token_store import overlay_runtime_auth, runtime_replay_auth
from dano.onboarding.recording_release import evaluate_recording_release
from dano.onboarding.recording_runtime import ProductionRecordingServices
from dano.onboarding.recording_verify import (
    REPLAY_SKIPPED_MESSAGE,
    replay_auth_failed,
    select_preflight_probe,
)
from dano.onboarding.recording_workflow import (
    PipelineContext,
    WorkflowActivity,
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


class _StubPi:
    last_submission_kind = None

    def __init__(self) -> None:
        self.prompts = 0

    async def prompt(self, text):  # noqa: ANN001, ARG002
        self.prompts += 1
        raise AssertionError("Pi must not collect replay evidence after auth skip")

    def bind_flow_spec(self, spec) -> None:  # noqa: ANN001, ARG002
        return None


def test_runtime_auth_drops_stale_recording_cookie() -> None:
    headers, storage = runtime_replay_auth(
        {"Cookie": "sid=stale", "Authorization": "Bearer old", "Tenant-Id": "1"},
        {"Authorization": "Bearer fresh"},
        {"cookies": [{"name": "sid", "value": "stale"}]},
    )
    assert headers == {"Authorization": "Bearer fresh", "Tenant-Id": "1"}
    assert storage is None
    assert overlay_runtime_auth(
        {"Cookie": "sid=stale", "Tenant-Id": "1"},
        {},
    ) == {"Cookie": "sid=stale", "Tenant-Id": "1"}


@pytest.mark.asyncio
async def test_auth_failure_skips_replay_and_continues_without_pi() -> None:
    spec = _spec()
    stub = _StubPi()

    async def pi_provider(fresh: bool):  # noqa: FBT001, ARG001, ANN202
        return stub

    async def unused(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("not used")

    async def replay_executor(request, spec_arg):  # noqa: ANN001, ANN202
        assert "tenant" not in str(request.get("path") or "")
        return {"status": 401, "response": {"code": 401, "msg": "未登录"}}

    async def no_refresh(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        return {"ok": False, "reason": "not_configured"}

    services = ProductionRecordingServices(
        recording_id="rec_preflight",
        materializer=unused,
        pi_provider=pi_provider,
        publisher=unused,
        replay_executor=replay_executor,
        token_refresher=no_refresh,
    )
    activities: list[WorkflowActivity] = []
    draft, issues = await services.verify(spec.model_dump(mode="json"), _context(activities))
    assert all(issue.code != "replay_auth" for issue in issues)
    assert all(issue.resolver != "external_blocked" for issue in issues)
    assert all(not issue.code.startswith("write_") for issue in issues)
    blocked = [item.label for item in activities if item.status == "blocked"]
    assert not any("登录态" in label for label in blocked)
    preflight = (draft.get("meta") or {}).get("verification_run", {}).get("preflight") or {}
    assert preflight.get("auth_failed") is True
    assert preflight.get("skip_replay") is True
    assert preflight.get("refresh_status") == "not_configured"
    edit_step = next(step for step in (draft.get("steps") or []) if step["step_id"] == "step_edit")
    remark = next(param for param in edit_step["params"] if param["path"] == "query.remark")
    assert remark["source_kind"] == "user_input"
    assert stub.prompts == 0
    assert any(REPLAY_SKIPPED_MESSAGE in item.label for item in activities)
    assert not any("整体流程" in item.label for item in activities)
    unverified = (draft.get("meta") or {}).get("unverified") or []
    assert any(item.get("target_kind") == "write_verify" for item in unverified if isinstance(item, dict))


def test_unverified_replay_targets_do_not_block_release() -> None:
    spec = _spec()
    spec.meta = {
        "unverified": [
            {"target_kind": "write_verify", "target_id": "step_edit", "reason": "skip"},
        ],
    }
    decision = evaluate_recording_release(spec)
    codes = {
        issue.check_code
        for capability in decision.capabilities
        for issue in capability.issues
    }
    assert "write_readback_missing" not in codes


@pytest.mark.asyncio
async def test_auth_failure_refreshes_token_and_continues() -> None:
    spec = _spec()
    probes = {"count": 0}
    refreshes = {"count": 0}

    async def unused(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("not used")

    async def replay_executor(request, spec_arg):  # noqa: ANN001, ANN202
        probes["count"] += 1
        if probes["count"] == 1:
            return {"status": 401, "response": {"code": 401, "msg": "未登录"}}
        return {"status": 200, "response": {"code": 0, "data": {}}}

    async def refresher(tenant, subsystem, *, force=False):  # noqa: ANN001, ARG001, FBT002
        refreshes["count"] += 1
        assert force is True
        assert tenant == "tenant"
        return {"ok": True, "status": "refreshed"}

    services = ProductionRecordingServices(
        recording_id="rec_refresh",
        materializer=unused,
        pi_provider=unused,
        publisher=unused,
        replay_executor=replay_executor,
        token_refresher=refresher,
    )
    activities: list[WorkflowActivity] = []
    draft, issues = await services.verify(spec.model_dump(mode="json"), _context(activities))
    assert probes["count"] == 2
    assert refreshes["count"] == 1
    assert all(issue.code != "replay_auth" for issue in issues)
    preflight = (draft.get("meta") or {}).get("verification_run", {}).get("preflight") or {}
    assert preflight.get("auth_failed") is not True
    assert preflight.get("refreshed") is True
    labels = [item.label for item in activities]
    assert any("正在自动刷新凭证" in label for label in labels)
    assert any("凭证已刷新" in label for label in labels)


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
