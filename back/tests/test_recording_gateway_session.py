from __future__ import annotations

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
