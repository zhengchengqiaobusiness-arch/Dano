"""方式B 录制核心:注入式语义动作捕获(真浏览器,缺浏览器自动 skip)。

不测 WebSocket/截屏(实时管线,手动/前端验);测最有价值、可自动化的部分——
用户操作(经真实 DOM 事件)是否被转成正确的语义步骤 + 样例值。
"""
from __future__ import annotations

import asyncio
import json
import time
import pytest

pytest.importorskip("playwright")

from dano.execution.page.driver import PlaywrightPageDriver
from dano.execution.page.recorder import RecordSession, _RECORDER_JS
from dano.execution.page.sessions import SESSION_STORAGE_STATE_KEY


def test_static_script_enum_repairs_only_exact_label_value_mapping() -> None:
    sess = RecordSession()
    sess.script_sources = [{
        "url": "https://example.test/assets/form.js",
        "text": "const processStatusOptions=[{label:'未提交',value:0},{label:'审批中',value:1},{label:'已完成',value:2}]",
    }]
    page_options = {
        "流程状态": {
            "field_key": "processStatus",
            "field_aliases": ["name:processStatus"],
            "options": ["未提交", "审批中", "已完成"],
        },
    }

    sess._supplement_page_enums_from_scripts(page_options)

    assert page_options["流程状态"]["enum_source"] == "script_static"
    assert page_options["流程状态"]["options"] == [
        {"label": "未提交", "value": 0},
        {"label": "审批中", "value": 1},
        {"label": "已完成", "value": 2},
    ]


def test_static_script_enum_does_not_guess_without_unique_field_alias() -> None:
    sess = RecordSession()
    sess.script_sources = [{
        "url": "https://example.test/assets/form.js",
        "text": (
            "const processStatusOptions=[{label:'未提交',value:0},{label:'审批中',value:1}];"
            "const processStatusBackup=[{label:'草稿',value:10},{label:'完成',value:20}]"
        ),
    }]
    page_options = {
        "流程状态": {
            "field_key": "processStatus",
            "field_aliases": ["name:processStatus"],
            "options": ["未提交", "审批中"],
        },
    }

    sess._supplement_page_enums_from_scripts(page_options)

    assert page_options["流程状态"]["options"] == ["未提交", "审批中"]
    assert "enum_source" not in page_options["流程状态"]


def test_large_minified_script_enum_scans_are_bounded_and_keep_exact_results() -> None:
    """A production-sized main bundle must not turn finalize into a minute-long regex scan."""
    noise = 'a.b="noise",' * 100_000
    main = (
        noise
        + 'e.OA_HOTEL_ROOM_TYPE="oa_hotel_room_type",'
        + 'e.OA_HOTEL_ROOM_LEVEL="oa_hotel_room_level";'
        + "const processStatusOptions=[{label:'未提交',value:0},{label:'审批中',value:1}]"
    )
    started = time.perf_counter()
    constants = RecordSession._script_dictionary_constants(main)
    arrays = RecordSession._static_enum_arrays(main)
    elapsed = time.perf_counter() - started

    assert constants["OA_HOTEL_ROOM_TYPE"] == "oa_hotel_room_type"
    assert constants["OA_HOTEL_ROOM_LEVEL"] == "oa_hotel_room_level"
    assert arrays == [{
        "name": "processStatusOptions",
        "options": [{"label": "未提交", "value": 0}, {"label": "审批中", "value": 1}],
        "mapping_complete": True,
        "truncated": False,
    }]
    # This is deliberately generous for shared CI runners.  The optimized
    # scanner normally completes this ~1.2 MB fixture in well under 0.1 s;
    # the former whole-bundle regex takes many seconds.
    assert elapsed < 2.0


def test_dictionary_association_scans_each_field_segment_once() -> None:
    constants = {f"DICT_{index}": f"dict_{index}" for index in range(400)}
    route = ";".join(
        f'prop:"field{index}",render(DictType.DICT_{index})'
        for index in range(300)
    )

    started = time.perf_counter()
    associations = RecordSession._script_dictionary_associations(route, constants)
    elapsed = time.perf_counter() - started

    assert associations["field0"] == {"dict_0"}
    assert associations["field299"] == {"dict_299"}
    assert elapsed < 1.0


def test_compiled_dictionary_enum_binds_exact_field_to_captured_label_value_records() -> None:
    sess = RecordSession()
    sess.script_sources = [
        {
            "url": "https://example.test/assets/index.js",
            "text": (
                'e.OA_HOTEL_ROOM_TYPE="oa_hotel_room_type",'
                'e.OA_HOTEL_ROOM_LEVEL="oa_hotel_room_level",'
                'e.BPM_PROCESS_INSTANCE_STATUS="bpm_process_instance_status"'
            ),
        },
        {
            "url": "https://example.test/assets/hotel-page.js",
            "text": (
                'prop:"roomType",render(getDict(DictType.OA_HOTEL_ROOM_TYPE));'
                'prop:"roomLevel",render(getDict(DictType.OA_HOTEL_ROOM_LEVEL));'
                'prop:"processStatus",render(getDict(DictType.BPM_PROCESS_INSTANCE_STATUS));'
                'prop:"hotelName",renderText()'
            ),
        },
    ]
    sess.dictionary_reads = [{
        "url": "https://example.test/system/dict-data/simple-list",
        "json": {"data": [
            {"dictType": "oa_hotel_room_type", "label": "标准间", "value": 1},
            {"dictType": "oa_hotel_room_type", "label": "大床房", "value": 2},
            {"dictType": "oa_hotel_room_level", "label": "标准", "value": "normal"},
            {"dictType": "oa_hotel_room_level", "label": "豪华", "value": "luxury"},
            {"dictType": "bpm_process_instance_status", "label": "未提交", "value": 0},
            {"dictType": "bpm_process_instance_status", "label": "审批中", "value": 1},
        ]},
    }]
    page_options = {
        "房间类型": {"field_key": "roomType", "field_aliases": ["name:roomType"], "options": ["标准间", "大床房"]},
        "房间等级": {"field_key": "roomLevel", "field_aliases": ["name:roomLevel"], "options": ["标准", "豪华"]},
        "流程状态": {"field_key": "processStatus", "field_aliases": ["name:processStatus"], "options": ["未提交", "审批中"]},
    }

    sess._supplement_page_enums_from_dictionaries(page_options)

    assert page_options["房间类型"]["dict_type"] == "oa_hotel_room_type"
    assert page_options["房间类型"]["options"] == [
        {"label": "标准间", "value": 1}, {"label": "大床房", "value": 2},
    ]
    assert page_options["流程状态"]["options"][0] == {"label": "未提交", "value": 0}
    assert all(item["enum_source"] == "script_dictionary" for item in page_options.values())


def test_compiled_dictionary_enum_bridges_visible_label_to_virtual_form_prop() -> None:
    """Element Plus renders the label but does not put form-item prop on input DOM."""
    sess = RecordSession()
    sess.script_sources = [
        {
            "url": "https://example.test/assets/index.js",
            "text": (
                'e.OA_HOTEL_ROOM_TYPE="oa_hotel_room_type",'
                'e.OA_HOTEL_ROOM_LEVEL="oa_hotel_room_level",'
                'e.BPM_PROCESS_INSTANCE_STATUS="bpm_process_instance_status"'
            ),
        },
        {
            "url": "https://example.test/assets/hotel-page.js",
            "text": (
                r'e(u,{label:"\u623F\u95F4\u7C7B\u578B",prop:"roomType"},'
                r'render(getDict(DictType.OA_HOTEL_ROOM_TYPE)));'
                r'e(u,{label:"\u623F\u95F4\u7B49\u7EA7",prop:"roomLevel"},'
                r'render(getDict(DictType.OA_HOTEL_ROOM_LEVEL)));'
                r'e(u,{label:"\u6D41\u7A0B\u72B6\u6001",prop:"processStatus"},'
                r'render(getDict(DictType.BPM_PROCESS_INSTANCE_STATUS)))'
            ),
        },
    ]
    sess.dictionary_reads = [{
        "url": "https://example.test/system/dict-data/simple-list",
        "json": {"data": [
            {"dictType": "oa_hotel_room_type", "label": "标准间", "value": 1},
            {"dictType": "oa_hotel_room_type", "label": "大床房", "value": 2},
            {"dictType": "oa_hotel_room_level", "label": "标准", "value": 1},
            {"dictType": "oa_hotel_room_level", "label": "豪华", "value": 2},
            {"dictType": "bpm_process_instance_status", "label": "未提交", "value": 0},
            {"dictType": "bpm_process_instance_status", "label": "审批中", "value": 1},
        ]},
    }]
    # This is the actual browser shape: visible labels/options are available,
    # but the rendered Element Plus input exposes no name/data-prop alias.
    page_options = {
        "房间类型": {"field_key": "房间类型", "control_kind": "select", "options": ["标准间", "大床房"]},
        "房间等级": {"field_key": "房间等级", "control_kind": "select", "options": ["标准", "豪华"]},
        "流程状态": {"field_key": "流程状态", "control_kind": "select", "options": ["未提交", "审批中"]},
    }

    sess._supplement_page_enums_from_dictionaries(page_options)

    assert page_options["房间类型"]["field_aliases"] == ["roomType"]
    assert page_options["房间等级"]["field_aliases"] == ["roomLevel"]
    assert page_options["流程状态"]["field_aliases"] == ["processStatus"]
    assert page_options["房间类型"]["options"] == [
        {"label": "标准间", "value": 1}, {"label": "大床房", "value": 2},
    ]
    assert page_options["流程状态"]["dict_type"] == "bpm_process_instance_status"
    assert page_options["流程状态"]["source_url"].endswith("/system/dict-data/simple-list")

    # Prove the repaired aliases are consumed by the next layer, not merely
    # present in recorder output.  This is the real list-page shape where all
    # three controls submit short codes in one GET query.
    from dano.execution.page.flow_spec import to_flow_spec

    spec = to_flow_spec(
        [{
            "method": "GET",
            "url": (
                "https://example.test/admin-api/oa/hotel-apply/page?"
                "pageNo=1&pageSize=10&roomType=1&roomLevel=1&processStatus=1"
            ),
            "headers": {},
            "response_json": {"code": 0, "data": {"list": [], "total": 0}},
        }],
        samples={"房间类型": "标准间", "房间等级": "标准", "流程状态": "审批中"},
        page_enum_options=page_options,
    )
    projected = {
        param.path: (param.key, param.type, param.source_kind)
        for step in spec.steps
        for param in step.params
        if param.path in {"query.roomType", "query.roomLevel", "query.processStatus"}
    }
    assert projected == {
        "query.roomType": ("房间类型", "enum", "api_option"),
        "query.roomLevel": ("房间等级", "enum", "api_option"),
        "query.processStatus": ("流程状态", "enum", "api_option"),
    }


def test_compiled_dictionary_enum_does_not_guess_ambiguous_field_binding() -> None:
    sess = RecordSession()
    sess.script_sources = [{
        "url": "https://example.test/assets/page.js",
        "text": (
            'e.STATUS_A="status_a",e.STATUS_B="status_b";'
            'prop:"status",render(getDict(DictType.STATUS_A),getDict(DictType.STATUS_B))'
        ),
    }]
    sess.dictionary_reads = [{
        "url": "https://example.test/system/dict-data/simple-list",
        "json": {"data": [
            {"dictType": "status_a", "label": "A1", "value": 1},
            {"dictType": "status_a", "label": "A2", "value": 2},
            {"dictType": "status_b", "label": "B1", "value": 1},
            {"dictType": "status_b", "label": "B2", "value": 2},
        ]},
    }]
    page_options = {"状态": {"field_key": "status", "options": ["A1", "A2"]}}

    sess._supplement_page_enums_from_dictionaries(page_options)

    assert page_options["状态"]["options"] == ["A1", "A2"]
    assert "dict_type" not in page_options["状态"]


def test_enum_snapshot_scopes_options_to_the_combobox_owned_popup() -> None:
    assert "document.getElementById(id)" in _RECORDER_JS
    assert "popup.closest('[aria-hidden=\"true\"]')" in _RECORDER_JS
    assert "if (expanded === 'false') return" in _RECORDER_JS


def test_observer_correlates_action_dom_effect_and_request_without_copying_values() -> None:
    sess = RecordSession()
    sess._on_record(None, json.dumps({
        "op": "submit",
        "action_id": "action_7",
        "locator": "role=button[name=提交]",
        "field": "",
        "value": "must-not-enter-page-events",
        "observed_at": 1000,
    }))
    sess._on_record(None, json.dumps({
        "op": "dom_effect",
        "action_id": "action_7",
        "observed_at": 1100,
        "changes": [{"type": "childList", "added": 1, "removed": 0}],
    }))
    sess._record_all("POST", "https://example.test/api/submit", pd='{"secret":"x"}')

    events = sess.recorded_page_events()
    assert [event["kind"] for event in events] == ["action", "dom_effect"]
    assert events[0]["has_value"] is True
    assert "must-not-enter-page-events" not in json.dumps(events, ensure_ascii=False)
    request = sess.captured_all_requests()[0]
    assert request["trigger_action_id"] == "action_7"
    assert request["trigger_transaction_id"] == "page_unknown|frame_unknown|action_7"
    assert request["trigger_event_id"] == "event_1"
    assert request["causality_confidence"] == "high"
    assert events[0]["transaction_id"] == request["trigger_transaction_id"]


def test_actual_list_read_preserves_causality_and_drives_generic_option_binding() -> None:
    from dano.execution.page import flow_spec as flow_spec_module

    class Request:
        method = "POST"
        resource_type = "xhr"

    class Response:
        request = Request()
        url = "https://asset.test/gateway/dispatch"
        headers = {"content-type": "application/json"}
        status = 200

        async def json(self):
            return {"payload": {"rows": [{
                "code": "A-7", "title": "主资产", "quota": {"left": 5},
            }]}}

    sess = RecordSession()
    sess._on_record(None, json.dumps({
        "op": "select", "action_id": "action_asset",
        "locator": "role=combobox[name=资产名称]", "observed_at": int(time.time() * 1000),
    }))
    sess._record_all("POST", Response.url, pd='{"keyword":""}')
    fact = sess.captured_all_requests()[0]
    sess._request_fact_index[id(Response.request)] = fact["index"]

    asyncio.run(sess._on_response(Response()))

    read = sess.captured_reads()[0]
    assert read["trigger_action_id"] == "action_asset"
    assert read["trigger_op"] == "select"
    assert read["trigger_locator"] == "role=combobox[name=资产名称]"
    assert read["causality_confidence"] == "high"
    assert flow_spec_module._read_is_option_source(read) is True

    submit = {
        "method": "POST", "url": "https://asset.test/gateway/dispatch",
        "post_data": '{"assetCode":"A-7","quotaLeft":5}',
    }
    step = flow_spec_module._build_step_from_capture(
        submit, reads=[read], samples={"资产名称": "主资产"},
        storage_state=None, required_labels={"资产名称"}, page_enum_options={},
        step_index=1, field_evidence=[
            {"label": "资产名称", "field_aliases": ["assetCode"], "control_kind": "select", "op": "select"},
            {"label": "剩余额度", "field_aliases": ["quotaLeft"], "control_kind": "number", "op": "snapshot", "disabled": True},
        ],
    )
    params = {param.path: param for param in step.params}
    assert params["assetCode"].source_kind == "api_option"
    assert params["quotaLeft"].source_kind == "selected_option_field"
    assert params["quotaLeft"].source["response_path"] == "quota.left"


def test_actual_recording_keeps_same_rpc_requests_from_distinct_actions() -> None:
    from dano.execution.page.flow_spec import orchestrate_flow_capabilities, to_flow_spec

    sess = RecordSession()
    now = int(time.time() * 1000)
    sess._on_record(None, json.dumps({
        "op": "click", "action_id": "action_save",
        "locator": "button[data-command=save]", "observed_at": now,
    }))
    sess._record_all(
        "POST", "https://example.test/rpc/execute",
        pd='{"operation":"save","payload":{"title":"草稿"}}',
    )
    sess._on_record(None, json.dumps({
        "op": "click", "action_id": "action_submit",
        "locator": "button[data-command=submit]", "observed_at": now + 10,
    }))
    sess._record_all(
        "POST", "https://example.test/rpc/execute",
        pd='{"operation":"submit","payload":{"title":"草稿"}}',
    )

    spec = to_flow_spec(
        captured_requests=sess.captured_all_requests(), reads=[],
        samples={"标题": "草稿"}, page_events=sess.recorded_page_events(),
    )
    assert len(spec.steps) == 2
    assert {step.source_meta.get("trigger_action_id") for step in spec.steps} == {
        "action_save", "action_submit",
    }

    planned = asyncio.run(orchestrate_flow_capabilities(spec, submission={"ops": []}))
    assert {frozenset(cap.step_ids) for cap in planned.capabilities} == {
        frozenset({spec.steps[0].step_id}), frozenset({spec.steps[1].step_id}),
    }


def test_observer_never_attaches_another_pages_last_action() -> None:
    sess = RecordSession()
    sess._on_record(None, json.dumps({
        "op": "click",
        "action_id": "action-query",
        "locator": "role=button[name=查询]",
        "page_id": "page-a",
        "frame_id": "frame-a",
    }))

    sess._record_all(
        "GET",
        "https://example.test/api/background",
        page_id="page-b",
        frame_id="frame-b",
    )

    assert "trigger_action_id" not in sess.captured_all_requests()[0]


def test_reset_clears_observer_causality_state() -> None:
    sess = RecordSession()
    sess._on_record(None, json.dumps({"op": "click", "locator": "text=查询"}))
    sess.reset()
    sess._record_all("GET", "https://example.test/api/query")
    assert sess.recorded_page_events() == []
    assert "trigger_action_id" not in sess.captured_all_requests()[0]


def test_recorder_key_safety_policy() -> None:
    from dano.execution.page.recorder import _safe_recorder_key

    for key in [
        "Escape", "Delete", "Shift+Tab", "Control+A", "Control+C", "Control+X",
        "Meta+C", "Meta+X", "Meta+Z", "Control+Enter", "Control+Backspace",
    ]:
        assert _safe_recorder_key(key)
    for key in [
        "Alt+F4", "F5", "F12", "Control+R", "Control+V", "Control+W",
        "Alt+Delete", "Control+Shift+I",
    ]:
        assert not _safe_recorder_key(key)


def test_screencast_rate_limits_preserve_clarity_without_overloading_ui() -> None:
    from dano.execution.page.recorder import _CAST_ACTIVE_FPS, _CAST_IDLE_FPS, _CAST_QUALITY

    assert 15 <= _CAST_ACTIVE_FPS <= 24
    assert 2 <= _CAST_IDLE_FPS <= 6
    assert _CAST_IDLE_FPS < _CAST_ACTIVE_FPS
    assert _CAST_QUALITY >= 75


class _RecordingMouse:
    def __init__(self, *, fail: dict[str, Exception] | None = None) -> None:
        self.events: list[tuple] = []
        self.fail = fail or {}

    async def _record(self, operation: str, *args) -> None:  # noqa: ANN002
        self.events.append((operation, *args))
        if operation in self.fail:
            raise self.fail[operation]

    async def click(self, x, y, *, button="left") -> None:  # noqa: ANN001
        await self._record("click", x, y, button)

    async def dblclick(self, x, y, *, button="left") -> None:  # noqa: ANN001
        await self._record("dblclick", x, y, button)

    async def move(self, x, y, *, steps=1) -> None:  # noqa: ANN001
        await self._record("move", x, y, steps)

    async def down(self, *, button="left", click_count=1) -> None:  # noqa: ANN001
        await self._record("down", button, click_count)

    async def up(self, *, button="left", click_count=1) -> None:  # noqa: ANN001
        await self._record("up", button, click_count)

    async def wheel(self, dx, dy) -> None:  # noqa: ANN001
        await self._record("wheel", dx, dy)


class _RecordingKeyboard:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    async def insert_text(self, text: str) -> None:
        self.events.append(("text", text))

    async def press(self, key: str) -> None:
        self.events.append(("key", key))


class _InputPage:
    def __init__(self, mouse: _RecordingMouse | None = None, *, closed: bool = False) -> None:
        self.mouse = mouse or _RecordingMouse()
        self.keyboard = _RecordingKeyboard()
        self.closed = closed

    def is_closed(self) -> bool:
        return self.closed

    def on(self, *_args) -> None:  # diag handlers are irrelevant to these protocol tests
        return None


class _InputContext:
    def __init__(self, pages: list[_InputPage]) -> None:
        self.pages = pages


async def test_dispatch_input_supports_pointer_drag_and_explicit_drag() -> None:
    page = _InputPage()
    sess = RecordSession()
    sess.page = page
    sess._context = _InputContext([page])

    assert (await sess.dispatch_input({
        "kind": "pointer_down", "nx": 0.1, "ny": 0.2, "button": 0, "click_count": 2,
    }))["ok"]
    assert (await sess.dispatch_input({"kind": "pointer_move", "nx": 0.7, "ny": 0.8, "steps": 6}))["ok"]
    assert (await sess.dispatch_input({
        "kind": "pointer_up", "nx": 0.7, "ny": 0.8, "button": 0, "click_count": 2,
    }))["ok"]
    assert page.mouse.events == [
        ("move", 128.0, 160.0, 1),
        ("down", "left", 2),
        ("move", 896.0, 640.0, 6),
        ("move", 896.0, 640.0, 1),
        ("up", "left", 2),
    ]

    page.mouse.events.clear()
    result = await sess.dispatch_input({
        "kind": "drag", "from_nx": 0.2, "from_ny": 0.25,
        "nx": 0.9, "ny": 0.75, "button": "left", "steps": 8,
    })
    assert result["ok"]
    assert page.mouse.events == [
        ("move", 256.0, 200.0, 1),
        ("down", "left", 1),
        ("move", 1152.0, 600.0, 8),
        ("up", "left", 1),
    ]


async def test_dispatch_input_supports_double_right_click_and_hover() -> None:
    page = _InputPage()
    sess = RecordSession()
    sess.page = page
    sess._context = _InputContext([page])

    assert (await sess.dispatch_input({"kind": "dblclick", "nx": 0.5, "ny": 0.5, "button": 2}))["ok"]
    assert (await sess.dispatch_input({"kind": "right_click", "nx": 0.25, "ny": 0.4}))["ok"]
    assert (await sess.dispatch_input({"kind": "hover", "nx": 0.8, "ny": 0.1, "steps": 3}))["ok"]
    assert page.mouse.events == [
        ("dblclick", 640.0, 400.0, "right"),
        ("click", 320.0, 320.0, "right"),
        ("move", 1024.0, 80.0, 3),
    ]


async def test_dispatch_input_target_closed_error_isolated_and_switches_page() -> None:
    class TargetClosedError(RuntimeError):
        pass

    failed = _InputPage(_RecordingMouse(fail={"click": TargetClosedError("page closed during click")}))
    fallback = _InputPage()
    sess = RecordSession()
    sess.page = failed
    sess._context = _InputContext([failed, fallback])

    result = await sess.dispatch_input({"kind": "click", "nx": 0.5, "ny": 0.5})
    assert result == {
        "ok": False,
        "recoverable": True,
        "kind": "click",
        "error": "input_dispatch_failed",
        "error_type": "TargetClosedError",
    }
    assert sess.page is fallback
    assert (await sess.dispatch_input({"kind": "text", "text": "会话仍可用"}))["ok"]
    assert fallback.keyboard.events == [("text", "会话仍可用")]


async def test_dispatch_input_generic_operation_error_never_escapes() -> None:
    failed = _InputPage(_RecordingMouse(fail={"move": RuntimeError("navigation interrupted input")}))
    sess = RecordSession()
    sess.page = failed
    sess._context = _InputContext([failed])

    result = await sess.dispatch_input({"kind": "pointer_move", "nx": 0.5, "ny": 0.5})
    assert result["ok"] is False
    assert result["recoverable"] is True
    assert result["error_type"] == "RuntimeError"


async def test_screencast_uses_full_viewport_quality_and_emits_dimensions() -> None:
    class FakeCdp:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict | None]] = []
            self.handlers: dict[str, object] = {}

        async def send(self, method: str, params: dict | None = None) -> None:
            self.calls.append((method, params))

        def on(self, event: str, callback) -> None:  # noqa: ANN001
            self.handlers[event] = callback

    class FakeContext:
        def __init__(self, cdp: FakeCdp) -> None:
            self.cdp = cdp

        async def new_cdp_session(self, _page):  # noqa: ANN001
            return self.cdp

    page = _InputPage()
    cdp = FakeCdp()
    sess = RecordSession()
    sess.page = page
    sess._context = FakeContext(cdp)
    frames: list[dict] = []

    async def on_frame(frame: dict) -> None:
        frames.append(frame)

    await sess.start_screencast(on_frame)
    start_params = next(params for method, params in cdp.calls if method == "Page.startScreencast")
    assert start_params == {"format": "jpeg", "quality": 80, "maxWidth": 1280, "maxHeight": 800}
    task = cdp.handlers["Page.screencastFrame"]({
        "sessionId": 7,
        "data": "jpeg-base64",
        "metadata": {"deviceWidth": 1280, "deviceHeight": 800},
    })
    await task
    await asyncio.sleep(0)
    assert frames == [{
        "seq": 1,
        "data": "jpeg-base64",
        "width": 1280,
        "height": 800,
        "frame_width": 1280,
        "frame_height": 800,
        "viewport_width": 1280,
        "viewport_height": 800,
        "viewport": {"width": 1280, "height": 800},
    }]


async def test_screencast_coalesces_burst_but_flushes_final_dynamic_frame() -> None:
    class FakeCdp:
        def __init__(self) -> None:
            self.handlers: dict[str, object] = {}

        async def send(self, _method: str, _params: dict | None = None) -> None:
            return None

        def on(self, event: str, callback) -> None:  # noqa: ANN001
            self.handlers[event] = callback

    class FakeContext:
        def __init__(self, cdp: FakeCdp) -> None:
            self.cdp = cdp

        async def new_cdp_session(self, _page):  # noqa: ANN001
            return self.cdp

    cdp = FakeCdp()
    sess = RecordSession()
    sess.page = _InputPage()
    sess._context = FakeContext(cdp)
    frames: list[dict] = []

    async def on_frame(frame: dict) -> None:
        frames.append(frame)

    await sess.start_screencast(on_frame)
    callback = cdp.handlers["Page.screencastFrame"]
    await callback({"sessionId": 1, "data": "initial", "metadata": {}})
    await asyncio.sleep(0)
    await callback({"sessionId": 2, "data": "loading", "metadata": {}})
    await callback({"sessionId": 3, "data": "loaded-final", "metadata": {}})

    assert [frame["data"] for frame in frames] == ["initial"]
    await asyncio.sleep((1 / 20) + 0.03)
    assert [frame["data"] for frame in frames] == ["initial", "loaded-final"]
    assert [frame["seq"] for frame in frames] == [1, 2]


def test_same_endpoint_responses_attach_by_request_identity_index() -> None:
    sess = RecordSession()
    first = sess._record_all("GET", "https://example.test/api/items?id=1")
    second = sess._record_all("GET", "https://example.test/api/items?id=1")

    # 较早请求后返回时，不能因为“最近一条”策略贴到第二个请求上。
    assert sess._attach_response(
        url="https://example.test/api/items?id=1", method="GET",
        response_json={"request": "first"}, status=200,
        content_type="application/json", request_index=first,
    )
    assert sess._attach_response(
        url="https://example.test/api/items?id=1", method="GET",
        response_json={"request": "second"}, status=200,
        content_type="application/json", request_index=second,
    )

    assert sess.all_requests[first]["response_json"] == {"request": "first"}
    assert sess.all_requests[second]["response_json"] == {"request": "second"}


def test_recorded_page_enum_options_attach_popup_pick_to_previous_field() -> None:
    sess = RecordSession()
    sess.steps = [
        {"op": "click", "locator": "label=类型", "field": "类型", "value": ""},
        {"op": "pick", "locator": "text=病假", "value": "病假", "options": ["病假", "事假", "婚假"]},
    ]

    enums = sess.recorded_page_enum_options()

    assert enums["类型"]["selected"] == "病假"
    assert enums["类型"]["options"] == ["病假", "事假", "婚假"]


def test_open_dropdown_snapshot_is_persisted_without_executable_pick() -> None:
    sess = RecordSession()
    sess._on_record(None, json.dumps({
        "op": "enum_snapshot",
        "locator": "role=combobox[name=房间类型]",
        "field": "房间类型",
        "options": [
            {"label": "大床房", "value": 2},
            {"label": "双床房", "value": 1},
        ],
        "page_id": "page-1",
        "frame_id": "main",
    }))

    assert sess.steps == []
    assert sess.recorded_page_enum_options() == {
        "房间类型": {
            "options": [
                {"label": "大床房", "value": 2},
                {"label": "双床房", "value": 1},
                ],
                "field_key": "房间类型",
                "selected": "",
                "selected_label": "",
                "mapping_complete": False,
            },
    }
    assert sess.recorded_page_events()[-1]["kind"] == "enum_snapshot"


def test_popup_pick_preserves_options_until_selected_value_is_recorded() -> None:
    """点击弹层项时不得清空刚抓到的候选，未知 value 也不能伪装成 label。"""
    assert "pollPick(activeTrigger, false)" in _RECORDER_JS
    assert "pollPick(trig, true)" in _RECORDER_JS
    assert "if (resetOptions) lastPickOptions = []" in _RECORDER_JS
    assert "return label;" not in _RECORDER_JS


def test_popup_option_value_reads_framework_props_without_label_fallback() -> None:
    """Element/Ant 等自定义 option 的 wire value 常藏在框架 props，而非 DOM attribute。"""
    assert "node.__vue__" in _RECORDER_JS
    assert "node.__vueParentComponent" in _RECORDER_JS
    assert "__reactProps$" in _RECORDER_JS
    assert "__reactFiber$" in _RECORDER_JS
    assert "ng-reflect-value" in _RECORDER_JS
    assert "explicitly named `value` props" in _RECORDER_JS


_HTML = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<form>
  <label for="amt">金额</label><input id="amt" name="amount" type="text">
  <label for="cat">类别</label>
  <select id="cat" name="category"><option value="">--</option><option value="差旅">差旅</option></select>
  <button type="button" id="sub" onclick="document.getElementById('ok').style.display='block'">提交</button>
</form><div id="ok" style="display:none">保存成功</div></body></html>"""


async def _chromium_available() -> bool:
    try:
        d, _ = await PlaywrightPageDriver.launch(headless=True)
        await d.close()
        return True
    except Exception:  # noqa: BLE001
        return False


async def test_record_session_captures_semantic_steps(tmp_path) -> None:  # noqa: ANN001
    if not await _chromium_available():
        pytest.skip("chromium 未安装")
    page = tmp_path / "form.html"
    page.write_text(_HTML, encoding="utf-8")

    sess = RecordSession()
    try:
        await sess.start(page.as_uri())
        # 模拟用户在录制页里操作(真实 DOM 事件 → 注入录制器捕获语义步骤)
        await sess.page.get_by_label("金额").fill("100")
        await sess.page.get_by_label("类别").select_option("差旅")
        await sess.page.get_by_role("button", name="提交").click()
        await sess.page.wait_for_timeout(300)          # 等 expose_binding 回传完成

        steps, samples = sess.recorded_steps()
        ops = [(s["op"], s["locator"]) for s in steps]
        assert ("fill", "label=金额") in ops
        assert ("select", "label=类别") in ops
        assert ("submit", "role=button[name=提交]") in ops
        assert samples.get("amount") == "100"          # 金额→标准字段 amount,值作样例
        assert samples.get("类别") == "差旅"
    finally:
        await sess.stop()


_BIG = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<input id="big" name="amount" style="position:fixed;top:0;left:0;width:1280px;height:300px">
<button style="position:fixed;top:400px;left:0;width:1280px;height:200px">提交</button>
</body></html>"""


async def test_dispatch_input_relays_and_captures(tmp_path) -> None:  # noqa: ANN001
    """输入回传全链路:归一坐标点击 focus → 键盘打字 fill → 点提交 → 语义步骤被捕获。"""
    if not await _chromium_available():
        pytest.skip("chromium 未安装")
    page = tmp_path / "big.html"
    page.write_text(_BIG, encoding="utf-8")
    sess = RecordSession()
    try:
        await sess.start(page.as_uri())
        await sess.dispatch_input({"kind": "click", "nx": 0.5, "ny": 0.2})    # 命中大输入框
        await sess.dispatch_input({"kind": "text", "text": "差旅费100"})       # 含中文 CJK,验 insert_text
        await sess.dispatch_input({"kind": "click", "nx": 0.5, "ny": 0.65})   # 命中提交按钮
        await sess.page.wait_for_timeout(300)
        steps, samples = sess.recorded_steps()
    finally:
        await sess.stop()
    ops = [s["op"] for s in steps]
    assert "fill" in ops and "submit" in ops
    assert samples.get("amount") == "差旅费100"          # 中文经回传被正确填入并捕获


_LOGIN = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<form>
  <input id="u" name="username" placeholder="账号">
  <input id="p" name="password" type="password" placeholder="密码">
  <button type="button">登录</button>
</form></body></html>"""


async def test_password_never_recorded_and_reset(tmp_path) -> None:  # noqa: ANN001
    """安全:密码框(type=password)绝不被录;reset 清空登录步骤。"""
    if not await _chromium_available():
        pytest.skip("chromium 未安装")
    page = tmp_path / "login.html"
    page.write_text(_LOGIN, encoding="utf-8")
    sess = RecordSession()
    try:
        await sess.start(page.as_uri())
        await sess.page.get_by_placeholder("账号").fill("admin")
        await sess.page.get_by_placeholder("密码").fill("secret123")
        await sess.page.wait_for_timeout(300)
        steps, samples = sess.recorded_steps()
        # 账号被录,密码与其值绝不出现
        assert any((s.get("field") or "") == "账号" for s in steps)
        assert not any("password" in (s["locator"] or "") or (s.get("field") or "") == "password" for s in steps)
        assert "secret123" not in str(samples)
        snapshot_fields = await sess.page.evaluate("window.__danoFormFieldEvidence()")
        assert "secret123" not in str(snapshot_fields)
        # reset 清空(登录后只录业务)
        sess.reset()
        assert sess.recorded_steps()[0] == []
    finally:
        await sess.stop()


_CARDS = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<div id="card" style="cursor:pointer;position:fixed;top:0;left:0;width:1280px;height:220px">出差申请</div>
<div class="el-menu-item" style="cursor:pointer;position:fixed;top:300px;left:0;width:1280px;height:200px">我的</div>
</body></html>"""


async def test_captures_card_and_menu_clicks(tmp_path) -> None:  # noqa: ANN001
    """卡片 <div>(cursor:pointer)与菜单 <li>(el-menu-item)的点击也要捕获(按可见文本定位)。"""
    if not await _chromium_available():
        pytest.skip("chromium 未安装")
    page = tmp_path / "cards.html"
    page.write_text(_CARDS, encoding="utf-8")
    sess = RecordSession()
    try:
        await sess.start(page.as_uri())
        await sess.dispatch_input({"kind": "click", "nx": 0.5, "ny": 0.1})   # 卡片 出差申请
        await sess.dispatch_input({"kind": "click", "nx": 0.5, "ny": 0.4})   # 菜单 我的
        await sess.page.wait_for_timeout(300)
        steps, _ = sess.recorded_steps()
    finally:
        await sess.stop()
    locs = [s["locator"] for s in steps]
    assert "text=出差申请" in locs
    assert "text=我的" in locs


_GENERIC = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<div role="button" aria-label="发起出差" style="cursor:pointer;position:fixed;top:0;left:0;width:1280px;height:150px">x</div>
<a href="#d" style="position:fixed;top:200px;left:0;width:1280px;height:100px">详情</a>
<input aria-label="采购金额" style="position:fixed;top:350px;left:0;width:1280px;height:100px">
<div data-testid="reimburse-card" style="cursor:pointer;position:fixed;top:500px;left:0;width:1280px;height:100px">报销</div>
</body></html>"""


async def test_general_semantics_framework_agnostic(tmp_path) -> None:  # noqa: ANN001
    """泛化:不靠任何框架 class —— ARIA role+aria-label 自定义按钮 / 链接 / aria-label 输入 / data-testid 卡片。"""
    if not await _chromium_available():
        pytest.skip("chromium 未安装")
    page = tmp_path / "generic.html"
    page.write_text(_GENERIC, encoding="utf-8")
    sess = RecordSession()
    try:
        await sess.start(page.as_uri())
        await sess.dispatch_input({"kind": "click", "nx": 0.5, "ny": 0.05})   # role=button div(发起→submit)
        await sess.dispatch_input({"kind": "click", "nx": 0.5, "ny": 0.30})   # 链接 详情
        await sess.dispatch_input({"kind": "click", "nx": 0.5, "ny": 0.50})   # aria-label 输入框
        await sess.dispatch_input({"kind": "text", "text": "888"})
        await sess.dispatch_input({"kind": "click", "nx": 0.5, "ny": 0.65})   # data-testid 卡片
        await sess.page.wait_for_timeout(300)
        steps, samples = sess.recorded_steps()
    finally:
        await sess.stop()
    pairs = [(s["op"], s["locator"]) for s in steps]
    assert ("submit", "role=button[name=发起出差]") in pairs       # 自定义 ARIA 按钮 + 提交语义
    assert ("click", "role=link[name=详情]") in pairs               # 隐式 link role
    assert ("click", 'css=[data-testid="reimburse-card"]') in pairs  # testid 最高优先
    assert ("fill", "role=textbox[name=采购金额]") in pairs          # aria-label 表单字段
    assert samples.get("采购金额") == "888"


async def test_record_session_storage_state_snapshot(tmp_path) -> None:  # noqa: ANN001
    """录制会话可抓登录态快照(storageState dict:cookies+origins)→ 回放/运行复用。"""
    if not await _chromium_available():
        pytest.skip("chromium 未安装")
    page = tmp_path / "form.html"
    page.write_text(_HTML, encoding="utf-8")
    sess = RecordSession()
    try:
        await sess.start(page.as_uri())
        state = await sess.storage_state()
    finally:
        await sess.stop()
    assert isinstance(state, dict) and "cookies" in state and "origins" in state


async def test_record_session_restores_session_storage_for_each_origin_before_page_scripts() -> None:
    """sessionStorage 不在 Playwright storage_state 中，主页面和跨域 frame 都必须显式恢复。"""
    if not await _chromium_available():
        pytest.skip("chromium 未安装")
    from aiohttp import web

    async def frame_handler(_req):  # noqa: ANN001
        return web.Response(text=(
            "<!doctype html><html><body><div id='boot'></div><script>"
            "document.getElementById('boot').textContent="
            "sessionStorage.getItem('frame-token')||'missing';"
            "</script></body></html>"
        ), content_type="text/html")

    frame_app = web.Application()
    frame_app.router.add_get("/frame", frame_handler)
    frame_runner = web.AppRunner(frame_app)
    await frame_runner.setup()
    frame_site = web.TCPSite(frame_runner, "127.0.0.1", 0)
    await frame_site.start()
    frame_port = frame_site._server.sockets[0].getsockname()[1]  # noqa: SLF001
    frame_origin = f"http://127.0.0.1:{frame_port}"

    async def main_handler(_req):  # noqa: ANN001
        return web.Response(text=(
            "<!doctype html><html><head><script>"
            "document.title=sessionStorage.getItem('main-token')||'missing';"
            "</script></head><body>"
            f"<iframe src='{frame_origin}/frame'></iframe>"
            "</body></html>"
        ), content_type="text/html")

    main_app = web.Application()
    main_app.router.add_get("/", main_handler)
    main_runner = web.AppRunner(main_app)
    await main_runner.setup()
    main_site = web.TCPSite(main_runner, "127.0.0.1", 0)
    await main_site.start()
    main_port = main_site._server.sockets[0].getsockname()[1]  # noqa: SLF001
    main_origin = f"http://127.0.0.1:{main_port}"

    first = RecordSession()
    second = RecordSession()
    try:
        await first.start(f"{main_origin}/")
        await first.page.wait_for_selector("iframe")
        frame = next(item for item in first.page.frames if item.url.startswith(frame_origin))
        await first.page.evaluate("sessionStorage.setItem('main-token','main-restored')")
        await frame.evaluate("sessionStorage.setItem('frame-token','frame-restored')")
        state = await first.storage_state()

        assert state is not None
        assert state[SESSION_STORAGE_STATE_KEY][main_origin] == [
            {"name": "main-token", "value": "main-restored"},
        ]
        assert state[SESSION_STORAGE_STATE_KEY][frame_origin] == [
            {"name": "frame-token", "value": "frame-restored"},
        ]

        # Inline application scripts read the values during initial parsing.
        # Passing only after goto would leave title/#boot as "missing".
        await second.start(f"{main_origin}/", storage_state=state)
        await second.page.wait_for_selector("iframe")
        restored_frame = next(item for item in second.page.frames if item.url.startswith(frame_origin))
        assert await second.page.title() == "main-restored"
        assert await restored_frame.locator("#boot").text_content() == "frame-restored"
    finally:
        await second.stop()
        await first.stop()
        await main_runner.cleanup()
        await frame_runner.cleanup()


_PICKER = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<div id="trig" aria-haspopup="listbox" style="cursor:pointer;border:1px solid #ccc;width:300px">
  <label for="dp">请假类型</label><input id="dp" readonly placeholder="请选择" style="width:200px">
</div>
<div id="pop" role="listbox" style="display:none"><div id="opt" style="cursor:pointer">事假</div></div>
<script>
  document.getElementById('trig').onclick=function(){document.getElementById('pop').style.display='block';};
  document.getElementById('opt').onclick=function(){document.getElementById('dp').value='事假';document.getElementById('pop').style.display='none';};
</script></body></html>"""


async def test_picker_recorded_as_pick_param_not_clicks(tmp_path) -> None:  # noqa: ANN001
    """选择型控件(触发框 aria-haspopup + role=listbox 弹层):录成一个 pick 参数步,而非写死的选项点击。"""
    if not await _chromium_available():
        pytest.skip("chromium 未安装")
    page = tmp_path / "picker.html"
    page.write_text(_PICKER, encoding="utf-8")
    sess = RecordSession()
    try:
        await sess.start(page.as_uri())
        await sess.page.click("#trig")          # 打开弹层(触发框,不单独记)
        await sess.page.click("#opt")           # 选「事假」(弹层内,不记点击)
        await sess.page.wait_for_timeout(400)   # 等延时读触发框最终值
        steps, samples = sess.recorded_steps()
    finally:
        await sess.stop()
    ops = [(s["op"], s["locator"]) for s in steps]
    assert ("pick", "label=请假类型") in ops            # 录成 pick 参数步
    assert not any(o == "click" and "事假" in (loc or "") for o, loc in ops)   # 没把「事假」录成写死点击
    assert samples.get("请假类型") == "事假"             # 选中值作样例


_OPENER = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<button id="open" onclick="window.open(NEWURL,'_blank')">打开新页</button>
</body></html>"""

_NEWPAGE = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<form><label for="amt">金额</label><input id="amt" name="amount" type="text"></form>
</body></html>"""


async def test_follows_new_tab_and_records_on_it(tmp_path) -> None:  # noqa: ANN001
    """多页 bug 修复:用户点开新标签页/新窗口(window.open / target=_blank)→ 录制会话**跟随**到新页,
    且新页上的操作经 context 级绑定照样被录到(旧实现只挂 self.page,新页既不录又不截屏=打不开)。"""
    if not await _chromium_available():
        pytest.skip("chromium 未安装")
    new = tmp_path / "new.html"
    new.write_text(_NEWPAGE, encoding="utf-8")
    opener = tmp_path / "opener.html"
    opener.write_text(_OPENER.replace("NEWURL", repr(new.as_uri())), encoding="utf-8")
    sess = RecordSession()
    try:
        await sess.start(opener.as_uri())
        first = sess.page
        await sess.page.get_by_role("button", name="打开新页").click()
        await sess.page.wait_for_timeout(600)              # 等新页打开 + 跟随切换
        assert sess.page is not first                       # 活动页已切到新标签页
        await sess.page.get_by_label("金额").fill("100")    # 新页上的输入也要被录到
        await sess.flush_recording()
        steps, samples = sess.recorded_steps()
    finally:
        await sess.stop()
    assert ("fill", "label=金额") in [(s["op"], s["locator"]) for s in steps]
    assert samples.get("amount") == "100"


async def test_multipage_handlers_safe_during_teardown() -> None:
    """治 TargetClosedError:会话拆除中(_closing)迟到的 page close / 新页事件不得在已关 context 上
    new_cdp_session 抛错 —— 确定性:_closing 置位后这些 handler 全部安全返回(无浏览器即可验)。"""
    sess = RecordSession()
    sess._closing = True
    sess._on_frame = lambda d: None        # noqa: E731 —— 截屏已"开"过,验切页不会重开
    # 以下在 _closing 下都应安全返回(不触发 new_cdp_session、不抛)
    await sess._open_screencast()
    await sess._restart_screencast()
    await sess._on_page_close(object())
    await sess._on_new_page(object())
    assert sess._cdp is None


async def test_token_auth_sets_login_cookie() -> None:
    """贴 token → 预置登录态:Admin-Token cookie 注入 context(免在画面里登录)。"""
    if not await _chromium_available():
        pytest.skip("chromium 未安装")
    from playwright.async_api import async_playwright

    from dano.execution.page.driver import apply_token_auth
    pw = await async_playwright().start()
    b = await pw.chromium.launch(headless=True)
    ctx = await b.new_context()
    try:
        await apply_token_auth(ctx, token="tok123", url="https://oa.example.com:8443/prod-api")
        cookies = await ctx.cookies()
        hit = [c for c in cookies if c["name"] == "Admin-Token" and c["value"] == "tok123"]
        assert hit and hit[0]["domain"].endswith("oa.example.com")
    finally:
        await ctx.close(); await b.close(); await pw.stop()


# ── P0-1 真实浏览器集成:验证 all_requests / diagnostics 在真浏览器链路里真能抓到 ──
_HTML_FETCH = """<!doctype html><html><head></head><body>
<button id="g">go</button>
<script>
document.getElementById('g').onclick = async () => {
  await fetch('/api/list?appId=auto&appName=auto');
  document.title = 'GET_DONE';
};
</script>
</body></html>"""

_HTML_THROW = """<!doctype html><html><head></head><body>
<button id="bad">bad</button>
<script>
console.error('init-warning');
// 顶层 throw:Playwright context 级 pageerror 事件必触发
window.addEventListener('error', function (e) { console.log('caught:' + e.message); });
throw new Error('boom-from-page');
</script>
</body></html>"""


async def test_real_browser_all_requests_captures_get(tmp_path) -> None:  # noqa: ANN001
    """真实浏览器:fetch GET 应进 all_requests,且 query 字段被自动解析。

    不依赖远端服务(发同源 fetch 经 service worker / 静态 server 都易跨域踩坑);用 file:// 起一个
    内置 server 起 1 个 GET 接口验。"""
    if not await _chromium_available():
        pytest.skip("chromium 未安装")
    from aiohttp import web
    page = tmp_path / "fetch.html"
    page.write_text(_HTML_FETCH, encoding="utf-8")

    async def handler(req):
        return web.json_response({"rows": []})
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]    # noqa: SLF001 —— aiohttp 没暴露取端口 API
    # 改写 HTML 让 fetch 走真端口
    html = _HTML_FETCH.replace("/api/list", f"http://127.0.0.1:{port}/api/list")
    page.write_text(html, encoding="utf-8")

    sess = RecordSession(intercept_submit=False, capture_reads=False)
    try:
        await sess.start(page.as_uri())
        await sess.page.get_by_role("button", name="go").click()
        try:
            await sess.page.wait_for_function("document.title === 'GET_DONE'", timeout=5000)
        except Exception:  # noqa: BLE001
            pass
        await sess.page.wait_for_timeout(500)
    finally:
        await runner.cleanup()
        await sess.stop()
    cap = sess.captured_all_requests()
    methods = [r["method"] for r in cap]
    assert "GET" in methods, f"GET 应进 all_requests,实际 {methods}"
    # 不重复记录(治 P0-1 重构前的 _record_all 双重记录 bug)
    target = [r for r in cap if "/api/list" in r["url"]]
    assert len(target) == 1, f"同一 GET 在 all_requests 中只能占一行,实际 {len(target)}"
    # query 自动解析(治"看不到 GET 携带什么参数")
    assert target[0]["query"] == {"appId": ["auto"], "appName": ["auto"]}, \
        f"query 应被解析,实际 {target[0]['query']}"


async def test_real_browser_diagnostics_captures_console_and_pageerror(tmp_path) -> None:  # noqa: ANN001
    """真实浏览器:console.error 与 throw 抛出的 pageerror 都应进 diagnostics。"""
    if not await _chromium_available():
        pytest.skip("chromium 未安装")
    page = tmp_path / "throw.html"
    page.write_text(_HTML_THROW, encoding="utf-8")
    sess = RecordSession(intercept_submit=False, capture_reads=False)
    try:
        await sess.start(page.as_uri())
        # 等 init-warning console 与 setTimeout throw 落地
        await sess.page.wait_for_timeout(500)
    finally:
        await sess.stop()
    types = [d["type"] for d in sess.captured_diagnostics()]
    assert "console" in types, f"console 事件应进 diagnostics,实际 {types}"
    assert "pageerror" in types, f"pageerror 应进 diagnostics,实际 {types}"
    # pageerror.message 含原异常文案
    page_errors = [d for d in sess.captured_diagnostics() if d["type"] == "pageerror"]
    assert any("boom-from-page" in d["message"] for d in page_errors), page_errors

