from __future__ import annotations

import asyncio

import pytest

from dano.execution.page.flow_spec import FlowSpec
from dano.onboarding.recording_gateway import (
    RecordingGatewaySession,
    RecordingSessionConfig,
    _project_page_enums,
)


def test_page_enum_projection_is_generic_and_merges_duplicate_observations() -> None:
    projected = _project_page_enums({
        "field": {
            "field_key": "field",
            "options": [{"label": "A", "value": "1"}],
            "control_kind": "select",
        },
        "field:second": {
            "field_key": "field",
            "options": [{"label": "B", "value": "2"}],
            "control_kind": "select",
        },
    }, {})

    assert projected["field"]["options"] == [{"label": "A", "value": "1"}]
    assert projected["field:second"]["options"] == [{"label": "B", "value": "2"}]


def test_recording_gateway_contains_no_business_specific_contract_names() -> None:
    from pathlib import Path

    source = Path("dano/onboarding/recording_gateway.py").read_text(encoding="utf-8")
    for forbidden in (
        "pageNo", "pageSize", "startUserSelectAssignees", "oa_duty_leave",
        "/admin-api/oa/", "请假类型",
    ):
        assert forbidden not in source


def test_gateway_route_exposes_only_canonical_recording_commands() -> None:
    import inspect

    from dano.gateway import app as gateway

    source = inspect.getsource(gateway.record_ws)
    for retired in (
        "orchestrate_flow", "auto_fix_flow", "publish_request", "flow_update",
        "refresh_flow_spec", 't == "finalize"', 't == "terminate"',
    ):
        assert retired not in source
    routes = [
        route for route in gateway.app.routes
        if getattr(route, "path", "") == "/onboarding/page/record"
    ]
    assert len(routes) == 1


@pytest.mark.asyncio
async def test_live_operator_question_is_deferred_to_final_analysis() -> None:
    class Pi:
        def __init__(self) -> None:
            self.flow_spec = FlowSpec()

        def current_flow_spec(self) -> FlowSpec:
            return self.flow_spec.model_copy(deep=True)

        def bind_flow_spec(self, spec: FlowSpec) -> None:
            self.flow_spec = spec.model_copy(deep=True)

    async def unused(*_args):  # noqa: ANN002
        raise AssertionError("service is not needed")

    session = RecordingGatewaySession(
        config=RecordingSessionConfig(
            tenant="tenant",
            subsystem="system",
            recording_id="recording_" + "a" * 32,
            action="action_1",
            start_url="https://example.invalid",
        ),
        send=None,
        pi_factory=unused,
        publisher=unused,
    )
    session._pi = Pi()

    result = await session._record_live_question(
        text="两个页面标签证据冲突，请确认业务含义",
        options=["含义 A", "含义 B"],
        context_ref="field:1",
    )

    assert result == {
        "answered": False,
        "reason": "deferred_until_final_analysis",
    }
    [question] = session._pi.flow_spec.meta["live_pending_questions"]
    assert question["text"] == "两个页面标签证据冲突，请确认业务含义"
    assert question["context_ref"] == "field:1"


@pytest.mark.asyncio
async def test_finish_command_forwards_machine_verification_switch() -> None:
    class Workflow:
        title = ""
        machine_verification: bool | None = None

        async def set_title(self, title: str) -> None:
            self.title = title

        async def finish(self, *, machine_verification: bool = False) -> None:
            self.machine_verification = machine_verification

    async def unused(*_args):  # noqa: ANN002
        raise AssertionError("service is not needed")

    session = RecordingGatewaySession(
        config=RecordingSessionConfig(
            tenant="tenant",
            subsystem="system",
            recording_id="recording_" + "b" * 32,
            action="action_1",
            start_url="https://example.invalid",
        ),
        send=None,
        pi_factory=unused,
        publisher=unused,
    )
    session.capture = object()  # type: ignore[assignment]
    workflow = Workflow()
    session.workflow = workflow  # type: ignore[assignment]

    await session.dispatch({
        "type": "finish",
        "title": "业务操作",
        "machine_verification": True,
    })

    assert workflow.title == "业务操作"
    assert workflow.machine_verification is True


@pytest.mark.asyncio
async def test_freeze_waits_for_current_live_analysis_and_keeps_its_notebook() -> None:
    class Capture:
        paused = False

        async def flush_recording(self) -> None:
            return None

        def pause_recording(self) -> None:
            self.paused = True

        def captured_all_requests(self) -> list[dict]:
            return []

    class Pi:
        def __init__(self) -> None:
            self.flow_spec = FlowSpec()

        def current_flow_spec(self) -> FlowSpec:
            return self.flow_spec.model_copy(deep=True)

        def bind_live_recording(self, *_args, **_kwargs) -> None:  # noqa: ANN002, ANN003
            return None

    async def unused(*_args):  # noqa: ANN002
        raise AssertionError("service is not needed")

    session = RecordingGatewaySession(
        config=RecordingSessionConfig(
            tenant="tenant",
            subsystem="system",
            recording_id="recording_" + "c" * 32,
            action="action_1",
            start_url="https://example.invalid",
        ),
        send=None,
        pi_factory=unused,
        publisher=unused,
    )
    capture = Capture()
    pi = Pi()
    session.capture = capture  # type: ignore[assignment]
    session._pi = pi

    async def live_turn() -> None:
        await asyncio.sleep(0.01)
        pi.flow_spec.meta = {
            "agent_insights": [{"kind": "param_source", "text": "已识别字段来源"}],
        }

    session._live_task = asyncio.create_task(live_turn())
    await session._freeze_capture()

    assert capture.paused is True
    assert session._live_task.done()
    assert session._live_task.cancelled() is False
    assert session._live_notebook is not None
    assert session._live_notebook.insights == [
        {"kind": "param_source", "text": "已识别字段来源"},
    ]


@pytest.mark.asyncio
async def test_freeze_drains_a_queued_live_batch_before_direct_export() -> None:
    class Capture:
        async def flush_recording(self) -> None:
            return None

        def pause_recording(self) -> None:
            return None

        def captured_all_requests(self) -> list[dict]:
            return [{"request_id": "req-1"}]

    class Pi:
        def __init__(self) -> None:
            self.flow_spec = FlowSpec()
            self.reasons: list[str] = []

        async def notify_live_batch(self, delta: dict) -> None:
            self.reasons.append(str(delta["reason"]))
            self.flow_spec.meta = {
                "agent_insights": [{"kind": "role", "text": "已识别业务请求"}],
            }

        def current_flow_spec(self) -> FlowSpec:
            return self.flow_spec.model_copy(deep=True)

        def bind_live_recording(self, *_args, **_kwargs) -> None:  # noqa: ANN002, ANN003
            return None

    async def unused(*_args):  # noqa: ANN002
        raise AssertionError("service is not needed")

    session = RecordingGatewaySession(
        config=RecordingSessionConfig(
            tenant="tenant",
            subsystem="system",
            recording_id="recording_" + "d" * 32,
            action="action_queued",
            start_url="https://example.invalid",
        ),
        send=None,
        pi_factory=unused,
        publisher=unused,
    )
    pi = Pi()
    session.capture = Capture()  # type: ignore[assignment]
    session._pi = pi
    session._live_pending_reason = "submit_candidate"

    await session._freeze_capture()

    assert session._capture_frozen is True
    assert pi.reasons == ["submit_candidate", "final_request_tail"]
    assert session._live_pending_reason == ""
    assert session._live_notebook is not None
    assert session._live_notebook.insights == [
        {"kind": "role", "text": "已识别业务请求"},
    ]


@pytest.mark.asyncio
async def test_freeze_drains_the_final_unanalysed_request_tail() -> None:
    class Capture:
        async def flush_recording(self) -> None:
            return None

        def pause_recording(self) -> None:
            return None

        def captured_all_requests(self) -> list[dict]:
            return [
                {"request_id": "req-1"},
                {"request_id": "req-2"},
                {"request_id": "req-3"},
            ]

    class Pi:
        def __init__(self) -> None:
            self.flow_spec = FlowSpec()
            self.since: list[int] = []

        async def notify_live_batch(self, delta: dict) -> None:
            self.since.append(int(delta["since_seq"]))

        def current_flow_spec(self) -> FlowSpec:
            return self.flow_spec.model_copy(deep=True)

        def bind_live_recording(self, *_args, **_kwargs) -> None:  # noqa: ANN002, ANN003
            return None

    async def unused(*_args):  # noqa: ANN002
        raise AssertionError("service is not needed")

    session = RecordingGatewaySession(
        config=RecordingSessionConfig(
            tenant="tenant",
            subsystem="system",
            recording_id="recording_" + "e" * 32,
            action="action_tail",
            start_url="https://example.invalid",
        ),
        send=None,
        pi_factory=unused,
        publisher=unused,
    )
    pi = Pi()
    session.capture = Capture()  # type: ignore[assignment]
    session._pi = pi
    session._last_live_count = 2

    await session._freeze_capture()

    assert pi.since == [2]
    assert session._last_live_count == 3


@pytest.mark.asyncio
async def test_freeze_always_runs_final_tail_for_an_existing_recording() -> None:
    class Capture:
        async def flush_recording(self) -> None:
            return None

        def pause_recording(self) -> None:
            return None

        def captured_all_requests(self) -> list[dict]:
            return [{"request_id": "req-1"}, {"request_id": "req-2"}]

    class Pi:
        def __init__(self) -> None:
            self.flow_spec = FlowSpec()
            self.batches: list[dict] = []

        async def notify_live_batch(self, delta: dict) -> None:
            self.batches.append(dict(delta))

        def current_flow_spec(self) -> FlowSpec:
            return self.flow_spec.model_copy(deep=True)

        def bind_live_recording(self, *_args, **_kwargs) -> None:  # noqa: ANN002, ANN003
            return None

    async def unused(*_args):  # noqa: ANN002
        raise AssertionError("service is not needed")

    session = RecordingGatewaySession(
        config=RecordingSessionConfig(
            tenant="tenant",
            subsystem="system",
            recording_id="recording_" + "9" * 32,
            action="action_final_tail",
            start_url="https://example.invalid",
        ),
        send=None,
        pi_factory=unused,
        publisher=unused,
    )
    pi = Pi()
    session.capture = Capture()  # type: ignore[assignment]
    session._pi = pi
    session._last_live_count = 2

    await session._freeze_capture()

    assert pi.batches == [{"reason": "final_request_tail", "since_seq": 2}]


def test_non_static_read_request_schedules_live_analysis() -> None:
    async def unused(*_args):  # noqa: ANN002
        raise AssertionError("service is not needed")

    session = RecordingGatewaySession(
        config=RecordingSessionConfig(
            tenant="tenant",
            subsystem="system",
            recording_id="recording_" + "f" * 32,
            action="action_read",
            start_url="https://example.invalid",
        ),
        send=None,
        pi_factory=unused,
        publisher=unused,
    )
    scheduled: list[str] = []
    session._schedule_live = scheduled.append  # type: ignore[method-assign]

    session._on_request({
        "method": "GET",
        "url": "https://example.invalid/api/records",
        "resource_type": "xhr",
    })
    session._on_request({
        "method": "GET",
        "url": "https://example.invalid/assets/app.js",
        "resource_type": "script",
    })

    assert scheduled == ["business_request"]


@pytest.mark.asyncio
async def test_live_plan_submission_is_checkpointed_before_the_pi_turn_finishes() -> None:
    class Capture:
        def captured_all_requests(self) -> list[dict]:
            return [{"request_id": "req-1"}, {"request_id": "req-2"}]

    class Workflow:
        class Snapshot:
            draft = None

        snapshot = Snapshot()
        updates: list[list[dict]] = []

        async def update_live_insights(self, insights: list[dict]) -> None:
            self.updates.append(insights)

    class Pi:
        def __init__(self) -> None:
            self.flow_spec = FlowSpec()
            self.submission_listener = None

        def bind_live_recording(self, *_args, **_kwargs) -> None:  # noqa: ANN002, ANN003
            return None

        def bind_submission_listener(self, listener) -> None:  # noqa: ANN001
            self.submission_listener = listener

    pi = Pi()

    async def pi_factory(_fresh: bool):
        return pi

    async def unused(*_args):  # noqa: ANN002
        raise AssertionError("service is not needed")

    session = RecordingGatewaySession(
        config=RecordingSessionConfig(
            tenant="tenant",
            subsystem="system",
            recording_id="recording_" + "e" * 32,
            action="action_live_checkpoint",
            start_url="https://example.invalid",
        ),
        send=None,
        pi_factory=pi_factory,
        publisher=unused,
    )
    session.capture = Capture()  # type: ignore[assignment]
    workflow = Workflow()
    session.workflow = workflow  # type: ignore[assignment]

    await session._ensure_pi(False)
    assert pi.submission_listener is not None

    accepted = FlowSpec(meta={
        "agent_insights": [{"kind": "role", "text": "已识别业务请求"}],
    })
    pi.submission_listener(accepted, "plan")
    await asyncio.sleep(0)

    assert workflow.updates == [[{"kind": "role", "text": "已识别业务请求"}]]
    assert session._live_notebook is not None
    assert session._live_notebook.insights == workflow.updates[0]
