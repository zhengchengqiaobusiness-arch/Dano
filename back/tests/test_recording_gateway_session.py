from __future__ import annotations

from dano.onboarding.recording_gateway import _project_page_enums


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
