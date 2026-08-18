from __future__ import annotations

import pytest

from dano.execution.page.flow_spec import FlowSpec, to_flow_spec
from dano.onboarding.recording_pi import RecordingPiSession
from dano.execution.page.recorder import RecordSession, _RECORDER_JS


class _Recorder:
    def captured_all_requests(self) -> list[dict]:
        return [
            {
                "request_id": "req_86",
                "sequence": 86,
                "method": "GET",
                "url": "http://example.test/admin-api/erp/sale-order/page?pageNo=1&pageSize=10",
                "path": "/admin-api/erp/sale-order/page",
                "query": {"pageNo": "1", "pageSize": "10"},
                "response_status": 200,
            },
            {
                "request_id": "req_87",
                "sequence": 87,
                "method": "GET",
                "url": "http://example.test/admin-api/erp/sale-order/export-excel",
                "path": "/admin-api/erp/sale-order/export-excel",
                "response_status": 200,
            },
        ]

    def recorded_page_events(self) -> list[dict]:
        return []

    def recorded_field_evidence(self) -> list[dict]:
        return []

    def recorded_page_enum_options(self) -> dict:
        return {}


class _MutableRecorder(_Recorder):
    def __init__(self) -> None:
        self.requests = super().captured_all_requests()

    def captured_all_requests(self) -> list[dict]:
        return self.requests


@pytest.mark.asyncio
async def test_refresh_live_evidence_publishes_captured_requests_to_flow_spec() -> None:
    session = RecordingPiSession(
        tenant="tenant-1",
        subsystem="sales",
        recording_id="recording_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    session.bind_flow_spec(FlowSpec())
    session.bind_live_recording(_Recorder(), goal_text="录制销售订单页面的实际操作")

    await session.refresh_live_evidence()

    facts = session.current_flow_spec().request_facts.requests
    assert [fact.request_id for fact in facts] == ["req_86", "req_87"]
    assert facts[0].path == "/admin-api/erp/sale-order/page"


@pytest.mark.asyncio
async def test_refresh_live_evidence_updates_a_completed_response_without_a_new_request() -> None:
    recorder = _MutableRecorder()
    session = RecordingPiSession(
        tenant="tenant-1",
        subsystem="sales",
        recording_id="recording_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    )
    session.bind_flow_spec(FlowSpec())
    session.bind_live_recording(recorder)
    await session.refresh_live_evidence()

    recorder.requests[0]["response_json"] = {"data": {"list": [{"id": 42}]}}
    await session.refresh_live_evidence()

    facts = session.current_flow_spec().request_facts.requests
    assert facts[0].response_json == {"data": {"list": [{"id": 42}]}}


def test_field_evidence_keeps_snapshot_value_and_alias_after_empty_control_event() -> None:
    recorder = RecordSession()
    recorder.form_snapshots = [{
        "page_id": "page_1",
        "frame_id": "frame_1",
        "page_context": {"path": "/erp/sale/order"},
        "fields": [{
            "field": "客户",
            "label": "客户",
            "value": "鲜生",
            "required": False,
            "field_aliases": ["customerId"],
            "control_kind": "select",
        }],
    }]
    recorder.steps = [{
        "op": "fill",
        "field": "客户",
        "value": "",
        "required": False,
        "field_aliases": [],
        "control_kind": "select",
        "page_id": "page_1",
        "frame_id": "frame_1",
        "page_context": {"path": "/erp/sale/order"},
        "event_id": "event_22",
    }]

    customer = [
        item for item in recorder.recorded_field_evidence()
        if item.get("label") == "客户"
    ]

    assert len(customer) == 1
    assert customer[0]["value"] == "鲜生"
    assert customer[0]["field_aliases"] == ["customerId"]


def test_browser_snapshot_reads_framework_select_value_and_deep_form_alias() -> None:
    assert "vi < 12" in _RECORDER_JS
    assert "evidence.control_kind === 'select'" in _RECORDER_JS
    assert "pickVal(selectHost || el)" in _RECORDER_JS


def test_business_query_keeps_pagination_in_runtime_context() -> None:
    spec = to_flow_spec(captured_requests=[{
        "request_id": "req_86",
        "sequence": 86,
        "method": "GET",
        "url": (
            "http://example.test/admin-api/erp/sale-order/page"
            "?pageNo=1&pageSize=10&no=XSDD001"
        ),
        "query": {"pageNo": ["1"], "pageSize": ["10"], "no": ["XSDD001"]},
        "response_status": 200,
        "response_json": {"data": {"list": []}},
        "trigger_op": "click",
        "trigger_locator": "text=搜索",
        "_request_role": {
            "role": "business_get",
            "keep": True,
            "confidence": 0.99,
            "reason": "搜索按钮触发销售订单查询",
        },
    }])

    params = {param.path: param for param in spec.steps[0].params}
    assert params["query.pageNo"].category == "runtime_var"
    assert params["query.pageNo"].exposed_to_user is False
    assert params["query.pageSize"].category == "runtime_var"
    assert params["query.pageSize"].exposed_to_user is False
    assert params["query.no"].category == "user_param"
    assert params["query.no"].exposed_to_user is True


def test_recorded_select_uses_the_matching_option_api_without_losing_caller_ownership() -> None:
    page_context = {"path": "/erp/sale/order"}
    spec = to_flow_spec(
        captured_requests=[
            {
                "request_id": "req_customer",
                "sequence": 1,
                "method": "GET",
                "url": "http://example.test/admin-api/erp/customer/simple-list",
                "response_status": 200,
                "response_json": {
                    "data": [
                        {"id": 8, "name": "鲜生"},
                        {"id": 9, "name": "李白"},
                    ]
                },
                "page_id": "page_1",
                "frame_id": "frame_1",
                "page_context": page_context,
                "_request_role": {
                    "role": "read_option",
                    "keep": False,
                    "confidence": 0.99,
                },
            },
            {
                "request_id": "req_tenant",
                "sequence": 2,
                "method": "GET",
                "url": "http://example.test/admin-api/system/tenant/simple-list",
                "response_status": 200,
                "response_json": {
                    "data": [
                        {"id": 1, "name": "未退货"},
                        {"id": 2, "name": "部分退货"},
                    ]
                },
                "page_id": "page_1",
                "frame_id": "frame_1",
                "page_context": page_context,
                "_request_role": {
                    "role": "read_option",
                    "keep": False,
                    "confidence": 0.99,
                },
            },
            {
                "request_id": "req_search",
                "sequence": 3,
                "method": "GET",
                "url": (
                    "http://example.test/admin-api/erp/sale-order/page"
                    "?pageNo=1&pageSize=10&customerId=8&returnStatus=1"
                ),
                "query": {
                    "pageNo": ["1"],
                    "pageSize": ["10"],
                    "customerId": ["8"],
                    "returnStatus": ["1"],
                },
                "response_status": 200,
                "response_json": {"data": {"list": []}},
                "page_id": "page_1",
                "frame_id": "frame_1",
                "page_context": page_context,
                "trigger_op": "click",
                "trigger_locator": "text=搜索",
                "_request_role": {
                    "role": "business_get",
                    "keep": True,
                    "confidence": 0.99,
                },
            },
        ],
        field_evidence=[
            {
                "label": "客户",
                "value": "鲜生",
                "control_kind": "select",
                "field_aliases": ["customerId"],
                "required_observed": False,
                "binding_status": "bound",
                "request_id": "req_search",
                "wire_path": "query.customerId",
                "page_id": "page_1",
                "frame_id": "frame_1",
                "page_context": page_context,
            },
            {
                "label": "退货数量",
                "value": "未退货",
                "control_kind": "select",
                "field_aliases": ["returnStatus"],
                "required_observed": False,
                "binding_status": "bound",
                "request_id": "req_search",
                "wire_path": "query.returnStatus",
                "page_id": "page_1",
                "frame_id": "frame_1",
                "page_context": page_context,
            },
        ],
        page_enum_options={
            "客户": {
                "field_key": "客户",
                "field_aliases": ["customerId"],
                "control_kind": "select",
                "selected": "鲜生",
                "selected_label": "鲜生",
                "mapping_complete": False,
                "options": [{"label": "鲜生"}, {"label": "李白"}],
                "page_id": "page_1",
                "frame_id": "frame_1",
                "page_context": page_context,
            },
            "退货数量": {
                "field_key": "退货数量",
                "field_aliases": ["returnStatus"],
                "control_kind": "select",
                "selected": "未退货",
                "selected_label": "未退货",
                "mapping_complete": False,
                "options": [{"label": "未退货"}, {"label": "部分退货"}, {"label": "全部退货"}],
                "page_id": "page_1",
                "frame_id": "frame_1",
                "page_context": page_context,
            },
        },
    )

    search = next(step for step in spec.steps if "/sale-order/page" in step.path)
    params = {param.path: param for param in search.params}
    customer = params["query.customerId"]
    returned = params["query.returnStatus"]

    assert customer.label == "客户"
    assert customer.source_kind == "api_option"
    assert customer.category == "user_param"
    assert customer.exposed_to_user is True
    assert customer.source["source_url"].endswith("/erp/customer/simple-list")
    assert returned.label == "退货数量"
    assert returned.required is False
    assert returned.category == "user_param"
    assert returned.exposed_to_user is True
    assert returned.source_kind != "api_option"
    assert "tenant" not in str(returned.source or {})


def test_sales_order_query_preserves_all_recorded_field_contracts() -> None:
    query = {
        "pageNo": ["1"],
        "pageSize": ["10"],
        "no": ["1"],
        "customerId": ["8"],
        "productId": ["4"],
        "orderTime[0]": ["2026-08-07 00:00:00"],
        "orderTime[1]": ["2026-08-08 23:59:59"],
        "status": ["10"],
        "remark": ["1"],
        "creator": ["100"],
        "outStatus": ["0"],
        "returnStatus": ["1"],
    }
    fields = [
        ("no", "订单单号", "text", "1", "string"),
        ("customerId", "客户", "select", "鲜生", "enum"),
        ("productId", "产品", "select", "钢板", "enum"),
        ("orderTime[0]", "开始日期", "date", "2026-08-07", "date"),
        ("orderTime[1]", "结束日期", "date", "2026-08-08", "date"),
        ("status", "状态", "select", "未审核", "enum"),
        ("remark", "备注", "text", "1", "string"),
        ("creator", "创建人", "select", "管理员", "enum"),
        ("outStatus", "出库数量", "select", "未出库", "enum"),
        ("returnStatus", "退货数量", "select", "未退货", "enum"),
    ]
    spec = to_flow_spec(
        captured_requests=[{
            "request_id": "req_86",
            "sequence": 86,
            "method": "GET",
            "url": "http://example.test/admin-api/erp/sale-order/page",
            "query": query,
            "response_status": 200,
            "response_json": {"data": {"list": []}},
            "trigger_op": "click",
            "trigger_locator": "text=搜索",
            "_request_role": {
                "role": "business_get",
                "keep": True,
                "confidence": 0.99,
            },
        }],
        field_evidence=[{
            "label": label,
            "value": value,
            "control_kind": control_kind,
            "field_aliases": [path.split("[")[0]],
            "required_observed": False,
            "binding_status": "bound",
            "request_id": "req_86",
            "wire_path": f"query.{path}",
        } for path, label, control_kind, value, _ in fields],
    )

    params = {param.path: param for param in spec.steps[0].params}
    for path, label, _control_kind, _value, expected_type in fields:
        param = params[f"query.{path}"]
        assert param.label == label
        assert param.type == expected_type
        assert param.required is False
        assert param.category == "user_param"
        assert param.exposed_to_user is True

    assert params["query.pageNo"].category == "runtime_var"
    assert params["query.pageSize"].category == "runtime_var"
