from __future__ import annotations

import json

from dano.execution.page.recorder import RecordSession, assign_step_field_keys
from dano.gateway.app import _frontend_recording_field_metadata


def test_repeated_events_for_same_field_and_locator_reuse_one_key() -> None:
    steps = [
        {
            "op": "fill",
            "locator": "label=使用日期",
            "field": "使用日期",
            "value": "2026-07-10",
            "required": True,
        },
        {
            "op": "fill",
            "locator": "label=使用日期",
            "field": "使用日期",
            "value": "2026-07-11",
            "required": True,
        },
        {
            "op": "select",
            "locator": "label=使用日期",
            "field": "使用日期",
            "value": "2026-07-12",
            "required": True,
            "options": ["2026-07-11", "2026-07-12"],
        },
    ]

    assert assign_step_field_keys(steps) == {0: "使用日期", 1: "使用日期", 2: "使用日期"}

    session = RecordSession()
    session.steps = steps
    _, samples = session.recorded_steps()
    assert samples == {"使用日期": "2026-07-12"}
    assert session.recorded_required_labels() == {"使用日期"}
    assert session.recorded_page_enum_options() == {
        "使用日期": {
            "options": ["2026-07-11", "2026-07-12"],
            "field_key": "使用日期",
            "selected": "2026-07-12",
        }
    }


def test_same_field_name_with_different_locators_keeps_distinct_keys() -> None:
    steps = [
        {
            "op": "select",
            "locator": "css=#start-date",
            "field": "日期",
            "value": "开始",
            "required": True,
            "options": ["开始", "结束"],
        },
        {
            "op": "select",
            "locator": "css=#end-date",
            "field": "日期",
            "value": "结束",
            "required": True,
            "options": ["开始", "结束"],
        },
        {
            "op": "fill",
            "locator": "css=#start-date",
            "field": "日期",
            "value": "开始-更新",
            "required": True,
        },
    ]

    assert assign_step_field_keys(steps) == {0: "日期", 1: "日期#2", 2: "日期"}

    session = RecordSession()
    session.steps = steps
    _, samples = session.recorded_steps()
    assert samples == {"日期": "开始-更新", "日期#2": "结束"}
    assert session.recorded_required_labels() == {"日期", "日期#2"}
    assert set(session.recorded_page_enum_options()) == {"日期", "日期#2"}


def test_same_field_and_locator_on_different_pages_stays_distinct() -> None:
    steps = [
        {"op": "fill", "locator": "label=日期", "field": "日期", "page_id": "page_1"},
        {"op": "fill", "locator": "label=日期", "field": "日期", "page_id": "page_2"},
    ]

    assert assign_step_field_keys(steps) == {0: "日期", 1: "日期#2"}


def test_record_callback_does_not_coalesce_same_locator_across_pages() -> None:
    session = RecordSession()
    payload = json.dumps({"op": "fill", "locator": "label=日期", "field": "日期", "value": "x"})
    page_1, page_2 = object(), object()

    session._on_record({"page": page_1, "frame": None}, payload)
    session._on_record({"page": page_2, "frame": None}, payload)

    assert len(session.steps) == 2
    assert assign_step_field_keys(session.steps) == {0: "日期", 1: "日期#2"}


def test_popup_options_without_a_field_use_the_previous_field_key() -> None:
    session = RecordSession()
    session.steps = [
        {"op": "click", "locator": "label=请假类型", "field": "请假类型", "value": ""},
        {"op": "pick", "locator": "text=病假", "value": "病假", "options": ["病假", "事假"]},
        {"op": "fill", "locator": "label=请假类型", "field": "请假类型", "value": "病假"},
    ]

    _, samples = session.recorded_steps()
    assert samples == {"请假类型": "病假"}
    assert session.recorded_page_enum_options()["请假类型"]["selected"] == "病假"


def test_zero_and_false_samples_are_not_dropped() -> None:
    session = RecordSession()
    session.steps = [
        {"op": "select", "locator": "label=数量", "field": "数量", "value": 0},
        {"op": "select", "locator": "label=启用", "field": "启用", "value": False},
    ]

    _, samples = session.recorded_steps()
    assert samples == {"数量": 0, "启用": False}


def test_gateway_frontend_steps_use_the_same_field_mapping() -> None:
    steps = [
        {
            "op": "fill",
            "locator": "label=使用日期",
            "field": "使用日期",
            "value": "2026-07-10",
            "required": True,
        },
        {
            "op": "select",
            "locator": "label=使用日期",
            "field": "使用日期",
            "value": "2026-07-11",
            "required": True,
            "options": ["2026-07-10", "2026-07-11"],
        },
    ]

    samples, required, enums = _frontend_recording_field_metadata(steps)

    assert samples == {"使用日期": "2026-07-11"}
    assert required == {"使用日期"}
    assert enums["使用日期"]["field_key"] == "使用日期"
    assert "使用日期#2" not in samples


def test_submit_snapshot_preserves_required_range_and_page_business_context() -> None:
    session = RecordSession()
    session._on_record(None, json.dumps({
        "op": "form_snapshot",
        "required_fields": ["使用时间", "申请标题"],
        "fields": [
            {"label": "使用时间", "value": "2026-07-09 00:00:00", "required": True},
            {"label": "使用时间", "value": "2026-08-11 23:59:59", "required": True},
            {"label": "备注", "value": "出差", "required": False},
        ],
        "page_context": {
            "path": "/oa/seal-apply",
            "document_title": "OA 管理系统",
            "visible_titles": ["公章借阅", "申请信息"],
        },
    }, ensure_ascii=False))

    assert session.steps == []
    assert session.recorded_required_labels() == {"使用时间", "申请标题"}
    assert session.recorded_form_samples() == {
        "使用时间": "2026-07-09 00:00:00",
        "使用时间#2": "2026-08-11 23:59:59",
        "备注": "出差",
    }
    assert session.form_snapshots[-1]["page_context"]["visible_titles"][0] == "公章借阅"
