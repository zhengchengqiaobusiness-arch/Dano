from __future__ import annotations

import json

from dano.execution.page.flow_spec import (
    FlowSpec,
    FlowStep,
    ParamField,
    _infer_arithmetic_computed_fields,
    flow_spec_to_client,
    to_flow_spec,
)


PAGE = {"path": "/desk/document/new"}


def _control(
    *,
    request_id: str,
    path: str,
    label: str,
    aliases: list[str],
    kind: str,
    value: str,
    op: str = "",
    required: bool = False,
    read_only: bool = False,
    page_context: dict | None = None,
) -> dict:
    return {
        "label": label,
        "value": value,
        "control_kind": kind,
        "field_aliases": aliases,
        "required": required,
        "required_observed": required,
        "binding_status": "bound",
        "op": op,
        "read_only": read_only,
        "request_id": request_id,
        "wire_path": path if path.startswith(("body.", "query.")) else f"body.{path}",
        "page_id": "page_1",
        "frame_id": "frame_1",
        "page_context": page_context or PAGE,
    }


def _option_get(
    request_id: str,
    path: str,
    rows: list[dict],
    sequence: int,
    page_context: dict | None = None,
) -> dict:
    return {
        "request_id": request_id,
        "sequence": sequence,
        "method": "GET",
        "url": f"http://example.test/api{path}",
        "response_status": 200,
        "response_json": {"data": rows},
        "page_id": "page_1",
        "frame_id": "frame_1",
        "page_context": page_context or PAGE,
        "_request_role": {"role": "read_option", "keep": False, "confidence": 0.99},
    }


def _enum(label: str, aliases: list[str], selected: str) -> dict:
    return {
        "field_key": label,
        "field_aliases": aliases,
        "control_kind": "select",
        "selected": selected,
        "selected_label": selected,
        "mapping_complete": True,
        "options": [{"label": selected}, {"label": f"{selected}-alt"}],
        "page_id": "page_1",
        "frame_id": "frame_1",
        "page_context": PAGE,
    }


def _params(spec: FlowSpec) -> dict[str, ParamField]:
    step = next(item for item in spec.steps if (item.method or "").upper() == "POST")
    return {param.path: param for param in step.params}


def test_sample_value_alone_does_not_expose_a_caller_field() -> None:
    spec = to_flow_spec(
        captured_requests=[{
            "request_id": "req_write",
            "sequence": 2,
            "method": "POST",
            "url": "http://example.test/api/doc/create",
            "post_data": json.dumps({"note": "hello", "hiddenToken": "tok-1"}),
            "response_status": 200,
            "response_json": {"code": 0},
            "page_id": "page_1",
            "frame_id": "frame_1",
            "page_context": PAGE,
            "_request_role": {"role": "business_write", "keep": True, "confidence": 0.99},
        }],
        samples={"备注": "hello"},
    )

    params = _params(spec)
    note = params["note"]
    assert note.key == "note"
    assert note.source_kind == "unknown"
    assert note.exposed_to_user is False
    assert note.category != "user_param"
    assert note.value == "hello"
    hidden = params["hiddenToken"]
    assert hidden.source_kind == "unknown"
    assert hidden.exposed_to_user is False
    assert hidden.value == "tok-1"


def test_unknown_source_stays_internal_until_evidence_exists() -> None:
    spec = to_flow_spec(
        captured_requests=[{
            "request_id": "req_write",
            "sequence": 2,
            "method": "POST",
            "url": "http://example.test/api/doc/create",
            "post_data": json.dumps({"refCode": "AB-9", "qty": 3}),
            "response_status": 200,
            "page_id": "page_1",
            "frame_id": "frame_1",
            "page_context": PAGE,
            "_request_role": {"role": "business_write", "keep": True, "confidence": 0.99},
        }],
    )

    params = _params(spec)
    assert params["refCode"].source_kind == "unknown"
    assert params["qty"].source_kind == "unknown"
    assert params["refCode"].exposed_to_user is False
    assert params["qty"].exposed_to_user is False
    assert params["refCode"].need_human_confirm is True
    assert params["refCode"].value == "AB-9"
    assert str(params["qty"].value) == "3"


def test_assign_user_leaf_is_not_current_user_without_identity() -> None:
    spec = to_flow_spec(
        captured_requests=[{
            "request_id": "req_write",
            "sequence": 2,
            "method": "POST",
            "url": "http://example.test/api/doc/create",
            "post_data": json.dumps({"assignUserId": 9, "note": "x"}),
            "response_status": 200,
            "page_id": "page_1",
            "frame_id": "frame_1",
            "page_context": PAGE,
            "_request_role": {"role": "business_write", "keep": True, "confidence": 0.99},
        }],
        field_evidence=[_control(
            request_id="req_write", path="body.note", label="说明",
            aliases=["note"], kind="text", value="x", op="fill",
        )],
    )

    params = _params(spec)
    assert params["assignUserId"].source_kind != "current_user"
    assert params["assignUserId"].exposed_to_user is False
    assert params["note"].key == "note"
    assert params["note"].label == "说明"
    assert params["note"].exposed_to_user is True


def test_query_wire_key_stays_on_the_request_leaf() -> None:
    spec = to_flow_spec(
        captured_requests=[{
            "request_id": "req_query",
            "sequence": 1,
            "method": "GET",
            "url": (
                "http://example.test/api/doc/page"
                "?pageNo=1&pageSize=10&period[0]=2026-08-01&period[1]=2026-08-08"
            ),
            "query": {
                "pageNo": ["1"],
                "pageSize": ["10"],
                "period[0]": ["2026-08-01"],
                "period[1]": ["2026-08-08"],
            },
            "response_status": 200,
            "response_json": {"data": {"list": []}},
            "page_id": "page_1",
            "frame_id": "frame_1",
            "page_context": PAGE,
            "trigger_op": "click",
            "trigger_locator": "text=搜索",
            "_request_role": {"role": "business_get", "keep": True, "confidence": 0.99},
        }],
        field_evidence=[
            _control(
                request_id="req_query", path="query.period[0]", label="开始时间",
                aliases=["period"], kind="date", value="2026-08-01", op="fill",
            ),
            _control(
                request_id="req_query", path="query.period[1]", label="结束时间",
                aliases=["period"], kind="date", value="2026-08-08", op="fill",
            ),
        ],
    )

    params = {param.path: param for param in spec.steps[0].params}
    assert params["query.pageNo"].exposed_to_user is False
    assert params["query.pageSize"].exposed_to_user is False
    start = params["query.period[0]"]
    assert start.key == "period[0]"
    assert start.label == "开始时间"
    assert start.exposed_to_user is True
    assert start.required is False


def test_composite_row_and_arithmetic_classify_create_axes() -> None:
    body = {
        "partyId": 5,
        "ownerId": 11,
        "walletId": 2,
        "bookedAt": 1785513600000,
        "note": "1",
        "ratePercent": 110,
        "rateAmount": 352,
        "payable": -32,
        "prepaid": 40,
        "lines": [{
            "itemId": 7,
            "itemCode": "SKU-7",
            "unitName": "箱",
            "onHand": 12.5,
            "unitPrice": 80,
            "qty": 4,
            "lineAmount": 320,
            "taxPrice": None,
            "lineTotal": 320,
        }],
    }
    spec = to_flow_spec(
        captured_requests=[
            _option_get("req_party", "/catalog/party/simple-list", [
                {"id": 5, "name": "甲公司"},
                {"id": 6, "name": "乙公司"},
            ], 1),
            _option_get("req_owner", "/catalog/owner/simple-list", [
                {"id": 11, "name": "李四"},
                {"id": 12, "name": "王五"},
            ], 2),
            _option_get("req_wallet", "/catalog/wallet/simple-list", [
                {"id": 2, "name": "基本户"},
                {"id": 3, "name": "现金"},
            ], 3),
            _option_get("req_item", "/catalog/item/simple-list", [
                {
                    "id": 7, "name": "零件A", "itemCode": "SKU-7",
                    "unitName": "箱", "onHand": 12.5, "unitPrice": 80,
                },
                {
                    "id": 8, "name": "零件B", "itemCode": "SKU-8",
                    "unitName": "件", "onHand": 3, "unitPrice": 15,
                },
            ], 4),
            {
                "request_id": "req_write",
                "sequence": 5,
                "method": "POST",
                "url": "http://example.test/api/doc/create",
                "post_data": json.dumps(body, ensure_ascii=False),
                "response_status": 200,
                "response_json": {"code": 0},
                "page_id": "page_1",
                "frame_id": "frame_1",
                "page_context": PAGE,
                "trigger_op": "click",
                "trigger_locator": "text=确定",
                "_request_role": {"role": "business_write", "keep": True, "confidence": 0.99},
            },
        ],
        field_evidence=[
            _control(
                request_id="req_write", path="body.partyId", label="往来方",
                aliases=["partyId"], kind="select", value="甲公司", op="select", required=True,
            ),
            _control(
                request_id="req_write", path="body.ownerId", label="经办人",
                aliases=["ownerId"], kind="select", value="李四", op="select",
            ),
            _control(
                request_id="req_write", path="body.walletId", label="结算账户",
                aliases=["walletId"], kind="select", value="基本户", op="select", required=True,
            ),
            _control(
                request_id="req_write", path="body.bookedAt", label="单据时间",
                aliases=["bookedAt"], kind="datetime", value="2026-08-01", op="fill", required=True,
            ),
            _control(
                request_id="req_write", path="body.note", label="备注",
                aliases=["note"], kind="text", value="1", op="fill",
            ),
            _control(
                request_id="req_write", path="body.ratePercent", label="优惠率",
                aliases=["ratePercent"], kind="number", value="110", op="fill",
            ),
            _control(
                request_id="req_write", path="body.prepaid", label="预收",
                aliases=["prepaid"], kind="number", value="40", op="fill",
            ),
            _control(
                request_id="req_write", path="body.lines[0].qty", label="数量",
                aliases=["qty"], kind="number", value="4", op="fill",
            ),
        ],
        page_enum_options={
            "往来方": _enum("往来方", ["partyId"], "甲公司"),
            "经办人": _enum("经办人", ["ownerId"], "李四"),
            "结算账户": _enum("结算账户", ["walletId"], "基本户"),
        },
        samples={
            "往来方": "甲公司",
            "经办人": "李四",
            "结算账户": "基本户",
            "备注": "1",
        },
    )

    params = _params(spec)
    party = params["partyId"]
    assert party.key == "partyId"
    assert party.label == "往来方"
    assert party.category == "user_param"
    assert party.exposed_to_user is True
    assert party.required is True
    assert party.source_kind == "api_option"
    assert "party/simple-list" in str((party.source or {}).get("source_url") or "")

    owner = params["ownerId"]
    assert owner.key == "ownerId"
    assert owner.label == "经办人"
    assert owner.source_kind != "current_user"
    assert owner.exposed_to_user is True

    item = params["lines[0].itemId"]
    assert item.key == "itemId"
    assert item.category == "user_param"
    assert item.exposed_to_user is True
    assert item.source_kind == "api_option"
    assert "item/simple-list" in str((item.source or {}).get("source_url") or "")

    for path in ("lines[0].itemCode", "lines[0].unitName", "lines[0].onHand", "lines[0].unitPrice"):
        projected = params[path]
        assert projected.key == path.split(".")[-1]
        assert projected.exposed_to_user is False
        assert projected.source_kind == "selected_option_field"
        assert projected.required is False

    qty = params["lines[0].qty"]
    assert qty.key == "qty"
    assert qty.label == "数量"
    assert qty.exposed_to_user is True
    assert qty.category == "user_param"

    for path in ("lines[0].lineAmount", "lines[0].lineTotal", "rateAmount", "payable"):
        computed = params[path]
        assert computed.exposed_to_user is False
        assert computed.source_kind == "computed"
        assert computed.required is False
        assert (computed.source or {}).get("strategy") in {
            "product", "sum", "difference", "percent_of", "remainder_after_percent",
        }

    assert params["lines[0].lineAmount"].source["strategy"] == "product"
    assert params["rateAmount"].source["strategy"] == "percent_of"
    assert params["note"].required is False
    assert params["prepaid"].exposed_to_user is True


def test_arithmetic_proves_line_and_header_formulas() -> None:
    spec = FlowSpec(steps=[FlowStep(
        step_id="create",
        method="POST",
        path="/doc/create",
        params=[
            ParamField(
                path="ratePercent", key="ratePercent", value="110",
                category="user_param", source_kind="user_input", exposed_to_user=True,
                evidence=[{"kind": "page_control", "interacted": True, "control_kind": "number"}],
            ),
            ParamField(path="rateAmount", key="rateAmount", value="352"),
            ParamField(path="payable", key="payable", value="-32"),
            ParamField(
                path="lines[0].unitPrice", key="unitPrice", value="80",
                source_kind="selected_option_field",
            ),
            ParamField(
                path="lines[0].qty", key="qty", value="4",
                category="user_param", source_kind="user_input",
                evidence=[{"kind": "page_control", "interacted": True, "control_kind": "number"}],
            ),
            ParamField(path="lines[0].lineAmount", key="lineAmount", value="320"),
            ParamField(path="lines[0].lineTotal", key="lineTotal", value="320"),
        ],
        sample_inputs={
            "ratePercent": 110, "rateAmount": 352, "payable": -32,
            "unitPrice": 80, "qty": 4, "lineAmount": 320, "lineTotal": 320,
        },
    )])

    _infer_arithmetic_computed_fields(spec)
    params = {param.key: param for param in spec.steps[0].params}

    assert params["lineAmount"].source_kind == "computed"
    assert params["lineAmount"].source["strategy"] == "product"
    assert params["lineTotal"].source_kind == "computed"
    assert params["rateAmount"].source_kind == "computed"
    assert params["rateAmount"].source["strategy"] == "percent_of"
    assert params["payable"].source_kind == "computed"
    assert params["payable"].exposed_to_user is False
    assert params["qty"].source_kind == "user_input"


def test_identity_quantity_still_binds_when_unit_price_is_selected() -> None:
    spec = FlowSpec(steps=[FlowStep(
        step_id="create",
        method="POST",
        path="/doc/create",
        params=[
            ParamField(
                path="lines[0].unitPrice", key="unitPrice", value="80",
                source_kind="selected_option_field",
            ),
            ParamField(
                path="lines[0].qty", key="qty", value="1",
                category="user_param", source_kind="user_input",
                evidence=[{"kind": "page_control", "interacted": True, "control_kind": "number"}],
            ),
            ParamField(path="lines[0].lineAmount", key="lineAmount", value="80"),
            ParamField(path="lines[0].onHand", key="onHand", value="12.5"),
        ],
    )])

    _infer_arithmetic_computed_fields(spec)
    params = {param.key: param for param in spec.steps[0].params}
    assert params["lineAmount"].source_kind == "computed"
    assert params["lineAmount"].source["strategy"] == "product"
    assert {params["lineAmount"].source["left_field"], params["lineAmount"].source["right_field"]} == {
        "qty", "unitPrice",
    }
    assert params["onHand"].source_kind != "computed"


def test_leave_style_page_uses_the_same_evidence_axes() -> None:
    office = {"path": "/office/request/new"}
    begin = 1_785_513_600_000
    finish = begin + 3 * 86_400_000
    spec = to_flow_spec(
        captured_requests=[
            _option_get("req_kind", "/office/kind/simple-list", [
                {"id": 2, "name": "年假"},
                {"id": 3, "name": "事假"},
            ], 1, page_context=office),
            {
                "request_id": "req_write",
                "sequence": 2,
                "method": "POST",
                "url": "http://example.test/api/office/request/create",
                "post_data": json.dumps({
                    "ticketNo": "",
                    "kindId": 2,
                    "applicantId": 88,
                    "beginAt": begin,
                    "finishAt": finish,
                    "span": 3,
                    "comment": "回家",
                    "submitTime": 1_785_600_000_000,
                }, ensure_ascii=False),
                "response_status": 200,
                "page_id": "page_1",
                "frame_id": "frame_1",
                "page_context": office,
                "_request_role": {"role": "business_write", "keep": True, "confidence": 0.99},
            },
        ],
        field_evidence=[
            _control(
                request_id="req_write", path="body.kindId", label="申请类型",
                aliases=["kindId"], kind="select", value="年假", op="select",
                required=True, page_context=office,
            ),
            _control(
                request_id="req_write", path="body.beginAt", label="开始日期",
                aliases=["beginAt"], kind="date", value="2026-08-01", op="fill",
                required=True, page_context=office,
            ),
            _control(
                request_id="req_write", path="body.finishAt", label="结束日期",
                aliases=["finishAt"], kind="date", value="2026-08-04", op="fill",
                required=True, page_context=office,
            ),
            _control(
                request_id="req_write", path="body.span", label="天数",
                aliases=["span"], kind="number", value="3",
                read_only=True, page_context=office,
            ),
            _control(
                request_id="req_write", path="body.comment", label="事由",
                aliases=["comment"], kind="textarea", value="回家", op="fill",
                required=True, page_context=office,
            ),
        ],
        page_enum_options={"申请类型": _enum("申请类型", ["kindId"], "年假")},
        samples={"申请类型": "年假", "事由": "回家"},
        storage_state={
            "origins": [{
                "localStorage": [
                    {"name": "session", "value": json.dumps({"id": 88, "name": "张三"})},
                ],
            }],
        },
    )

    params = _params(spec)
    assert params["kindId"].key == "kindId"
    assert params["kindId"].label == "申请类型"
    assert params["kindId"].source_kind == "api_option"
    assert params["kindId"].required is True
    assert params["comment"].key == "comment"
    assert params["comment"].label == "事由"
    assert params["comment"].exposed_to_user is True
    assert params["comment"].required is True
    assert params["beginAt"].exposed_to_user is True
    assert params["finishAt"].exposed_to_user is True
    assert params["span"].source_kind == "computed"
    assert params["span"].exposed_to_user is False
    assert params["span"].source["strategy"] == "date_span_days"
    assert {params["span"].source["start_field"], params["span"].source["end_field"]} == {
        "beginAt", "finishAt",
    }
    assert params["ticketNo"].exposed_to_user is False
    assert params["applicantId"].source_kind == "current_user"
    assert params["applicantId"].exposed_to_user is False
    assert params["submitTime"].exposed_to_user is False
    assert params["beginAt"].type == "date"
    assert params["beginAt"].wire_type in {"number", "integer"}
    assert params["finishAt"].type == "date"
    assert params["kindId"].type == "enum"
    assert params["comment"].type == "string"


def test_meeting_style_page_projects_chosen_row_and_computes_fee() -> None:
    room = {"path": "/facility/booking"}
    spec = to_flow_spec(
        captured_requests=[
            _option_get("req_space", "/facility/space/simple-list", [
                {"id": 4, "name": "南山厅", "spaceCode": "A-4", "seats": 12, "hourlyRate": 60},
                {"id": 5, "name": "罗湖厅", "spaceCode": "B-5", "seats": 6, "hourlyRate": 40},
            ], 1, page_context=room),
            {
                "request_id": "req_write",
                "sequence": 2,
                "method": "POST",
                "url": "http://example.test/api/facility/booking/create",
                "post_data": json.dumps({
                    "spaceId": 4,
                    "spaceCode": "A-4",
                    "seats": 12,
                    "hourlyRate": 60,
                    "slotHours": 2,
                    "spaceFee": 120,
                    "memo": "周会",
                }),
                "response_status": 200,
                "page_id": "page_1",
                "frame_id": "frame_1",
                "page_context": room,
                "_request_role": {"role": "business_write", "keep": True, "confidence": 0.99},
            },
        ],
        field_evidence=[
            _control(
                request_id="req_write", path="body.slotHours", label="时长",
                aliases=["slotHours"], kind="number", value="2", op="fill",
                required=True, page_context=room,
            ),
            _control(
                request_id="req_write", path="body.memo", label="说明",
                aliases=["memo"], kind="text", value="周会", op="fill",
                page_context=room,
            ),
        ],
    )

    params = _params(spec)
    chooser = params["spaceId"]
    assert chooser.key == "spaceId"
    assert chooser.source_kind == "api_option"
    assert chooser.exposed_to_user is True
    for path in ("spaceCode", "seats", "hourlyRate"):
        assert params[path].source_kind == "selected_option_field"
        assert params[path].exposed_to_user is False
        assert params[path].key == path
    assert params["slotHours"].key == "slotHours"
    assert params["slotHours"].label == "时长"
    assert params["slotHours"].required is True
    assert params["spaceFee"].source_kind == "computed"
    assert params["spaceFee"].source["strategy"] == "product"
    assert params["memo"].exposed_to_user is True
    assert params["memo"].required is False


def test_editable_page_prefill_stays_caller_overridable() -> None:
    spec = to_flow_spec(
        captured_requests=[{
            "request_id": "req_write",
            "sequence": 2,
            "method": "POST",
            "url": "http://example.test/api/doc/create",
            "post_data": json.dumps({
                "partyId": 5,
                "bookedAt": 1785513600000,
                "ratePercent": 0,
                "prepaid": 0,
                "note": "1",
            }),
            "response_status": 200,
            "page_id": "page_1",
            "frame_id": "frame_1",
            "page_context": PAGE,
            "_request_role": {"role": "business_write", "keep": True, "confidence": 0.99},
        }],
        field_evidence=[
            _control(
                request_id="req_write", path="body.partyId", label="往来方",
                aliases=["partyId"], kind="select", value="甲公司", op="select", required=True,
            ),
            _control(
                request_id="req_write", path="body.bookedAt", label="单据时间",
                aliases=["bookedAt"], kind="datetime", value="2026-08-01", required=True,
            ),
            _control(
                request_id="req_write", path="body.ratePercent", label="优惠率",
                aliases=["ratePercent"], kind="number", value="0",
            ),
            _control(
                request_id="req_write", path="body.prepaid", label="预收",
                aliases=["prepaid"], kind="number", value="0",
            ),
            _control(
                request_id="req_write", path="body.note", label="备注",
                aliases=["note"], kind="text", value="1", op="fill",
            ),
        ],
    )

    params = _params(spec)
    party = params["partyId"]
    assert party.key == "partyId"
    assert party.label == "往来方"
    assert party.exposed_to_user is True
    assert party.required is True

    booked = params["bookedAt"]
    assert booked.key == "bookedAt"
    assert booked.label == "单据时间"
    assert booked.source_kind == "page_default"
    assert booked.exposed_to_user is True
    assert booked.editable is True
    assert booked.required is True
    assert booked.source.get("caller_override") is True

    rate = params["ratePercent"]
    assert rate.key == "ratePercent"
    assert rate.label == "优惠率"
    assert rate.source_kind == "page_default"
    assert rate.exposed_to_user is True
    assert rate.editable is True
    assert rate.required is False
    assert rate.default_value in {0, "0", 0.0}

    assert params["note"].key == "note"
    assert params["note"].label == "备注"
    assert params["note"].exposed_to_user is True
    assert params["note"].required is False


def test_query_filter_is_not_computed_from_numeric_coincidence() -> None:
    spec = to_flow_spec(
        captured_requests=[{
            "request_id": "req_query",
            "sequence": 1,
            "method": "GET",
            "url": "http://example.test/api/doc/page?pageNo=1&pageSize=10&no=1&status=10",
            "query": {
                "pageNo": ["1"],
                "pageSize": ["10"],
                "no": ["1"],
                "status": ["10"],
            },
            "response_status": 200,
            "response_json": {"data": {"list": []}},
            "page_id": "page_1",
            "frame_id": "frame_1",
            "page_context": PAGE,
            "trigger_op": "click",
            "trigger_locator": "text=搜索",
            "_request_role": {"role": "business_get", "keep": True, "confidence": 0.99},
        }],
        field_evidence=[
            _control(
                request_id="req_query", path="query.no", label="单据编号",
                aliases=["no"], kind="text", value="1",
            ),
            _control(
                request_id="req_query", path="query.status", label="状态",
                aliases=["status"], kind="select", value="待审",
            ),
        ],
    )

    params = {param.path: param for param in spec.steps[0].params}
    assert params["query.no"].key == "no"
    assert params["query.no"].label == "单据编号"
    assert params["query.no"].source_kind != "computed"
    assert params["query.no"].category == "user_param"
    assert params["query.no"].exposed_to_user is True
    assert params["query.no"].required is False
    assert params["query.status"].label == "状态"
    assert params["query.status"].required is False


def test_detail_hydration_keeps_editable_fields_caller_overridable() -> None:
    spec = to_flow_spec(
        captured_requests=[
            {
                "request_id": "req_detail",
                "sequence": 1,
                "method": "GET",
                "url": "http://example.test/api/doc/get?id=9",
                "query": {"id": ["9"]},
                "response_status": 200,
                "response_json": {"data": {
                    "id": 9, "title": "旧标题", "remark": "旧备注", "amount": 20,
                }},
                "page_id": "page_1",
                "frame_id": "frame_1",
                "page_context": PAGE,
                "_request_role": {"role": "read_context", "keep": True, "confidence": 0.99},
            },
            {
                "request_id": "req_write",
                "sequence": 2,
                "method": "POST",
                "url": "http://example.test/api/doc/update",
                "post_data": json.dumps({
                    "id": 9,
                    "title": "旧标题",
                    "remark": "旧备注",
                    "amount": 20,
                    "note": "新说明",
                }, ensure_ascii=False),
                "response_status": 200,
                "page_id": "page_1",
                "frame_id": "frame_1",
                "page_context": PAGE,
                "_request_role": {"role": "business_write", "keep": True, "confidence": 0.99},
            },
        ],
        field_evidence=[
            _control(
                request_id="req_write", path="body.title", label="标题",
                aliases=["title"], kind="text", value="旧标题",
            ),
            _control(
                request_id="req_write", path="body.remark", label="备注",
                aliases=["remark"], kind="textarea", value="旧备注",
            ),
            _control(
                request_id="req_write", path="body.amount", label="金额",
                aliases=["amount"], kind="number", value="20",
            ),
            _control(
                request_id="req_write", path="body.note", label="说明",
                aliases=["note"], kind="text", value="新说明", op="fill",
            ),
        ],
    )

    params = _params(spec)
    assert params["id"].source_kind == "previous_response"
    assert params["id"].exposed_to_user is False
    for path in ("title", "remark", "amount"):
        brought = params[path]
        assert brought.key == path
        assert brought.source_kind == "previous_response"
        assert brought.exposed_to_user is True
        assert brought.editable is True
        assert brought.required is False
        assert brought.source.get("allow_caller_override") is True
    assert params["title"].label == "标题"
    assert params["remark"].label == "备注"
    assert params["note"].key == "note"
    assert params["note"].label == "说明"
    assert params["note"].source_kind == "user_input"
    assert params["note"].exposed_to_user is True
    assert params["note"].required is False


def test_required_star_and_label_stay_on_the_bound_caller_field() -> None:
    spec = to_flow_spec(
        captured_requests=[{
            "request_id": "req_write",
            "sequence": 2,
            "method": "POST",
            "url": "http://example.test/api/doc/create",
            "post_data": json.dumps({
                "partyId": 5,
                "itemCode": "SKU-7",
                "qty": 1,
                "prepaid": 0,
            }),
            "response_status": 200,
            "page_id": "page_1",
            "frame_id": "frame_1",
            "page_context": PAGE,
            "_request_role": {"role": "business_write", "keep": True, "confidence": 0.99},
        }],
        field_evidence=[
            _control(
                request_id="req_write", path="body.partyId", label="往来方",
                aliases=["partyId"], kind="select", value="甲公司", op="select", required=True,
            ),
            _control(
                request_id="req_write", path="body.itemCode", label="编码",
                aliases=["itemCode"], kind="text", value="SKU-7", read_only=True,
            ),
            _control(
                request_id="req_write", path="body.qty", label="数量",
                aliases=["qty"], kind="number", value="1", op="fill", required=True,
            ),
            _control(
                request_id="req_write", path="body.prepaid", label="预收",
                aliases=["prepaid"], kind="number", value="0",
            ),
        ],
    )

    params = _params(spec)
    assert params["partyId"].key == "partyId"
    assert params["partyId"].label == "往来方"
    assert params["partyId"].required is True
    assert params["qty"].key == "qty"
    assert params["qty"].label == "数量"
    assert params["qty"].required is True
    assert params["prepaid"].key == "prepaid"
    assert params["prepaid"].label == "预收"
    assert params["prepaid"].source_kind == "page_default"
    assert params["prepaid"].exposed_to_user is True
    assert params["prepaid"].required is False
    assert params["itemCode"].key == "itemCode"
    assert params["itemCode"].label == "编码"
    assert params["itemCode"].required is False
    assert params["itemCode"].exposed_to_user is False


def test_unproven_query_filter_is_not_guessed_as_caller_input() -> None:
    spec = to_flow_spec(
        captured_requests=[{
            "request_id": "req_support",
            "sequence": 1,
            "method": "GET",
            "url": "http://example.test/api/support/ping?traceId=abc-1&flag=1",
            "query": {"traceId": ["abc-1"], "flag": ["1"]},
            "response_status": 200,
            "page_id": "page_1",
            "frame_id": "frame_1",
            "page_context": PAGE,
            "_request_role": {"role": "read_context", "keep": True, "confidence": 0.99},
        }],
    )
    if not spec.steps:
        return
    params = {param.path: param for param in spec.steps[0].params}
    for path in ("query.traceId", "query.flag"):
        if path in params:
            assert params[path].exposed_to_user is False


def test_standalone_delete_ids_are_required_caller_input() -> None:
    spec = to_flow_spec(
        captured_requests=[{
            "request_id": "req_delete",
            "sequence": 1,
            "method": "DELETE",
            "url": "http://example.test/api/doc/delete?ids=39",
            "query": {"ids": ["39"]},
            "response_status": 200,
            "response_json": {"code": 0},
            "page_id": "page_1",
            "frame_id": "frame_1",
            "page_context": PAGE,
            "trigger_op": "click",
            "trigger_locator": 'tr:has-text("DOC-39") >> text=删除',
            "_request_role": {"role": "business_write", "keep": True, "confidence": 0.99},
        }],
    )
    params = {param.path: param for param in spec.steps[0].params}
    ids = params["query.ids"]
    assert ids.key == "ids"
    assert ids.source_kind == "user_input"
    assert ids.category == "user_param"
    assert ids.exposed_to_user is True
    assert ids.required is True
    assert ids.source.get("kind") == "record_identity"


def test_client_projection_keeps_evidence_source_and_type_axes() -> None:
    spec = to_flow_spec(
        captured_requests=[
            _option_get("req_party", "/catalog/party/simple-list", [
                {"id": 5, "name": "甲公司"},
                {"id": 6, "name": "乙公司"},
            ], 1),
            {
                "request_id": "req_write",
                "sequence": 2,
                "method": "POST",
                "url": "http://example.test/api/doc/create",
                "post_data": json.dumps({
                    "partyId": 5,
                    "status": "草稿",
                    "bookedAt": 1785513600000,
                    "unitPrice": 80,
                    "qty": 2,
                    "lineAmount": 160,
                    "previewTitle": "新建单据",
                    "note": "ok",
                }),
                "response_status": 200,
                "page_id": "page_1",
                "frame_id": "frame_1",
                "page_context": PAGE,
                "_request_role": {"role": "business_write", "keep": True, "confidence": 0.99},
            },
        ],
        field_evidence=[
            _control(
                request_id="req_write", path="body.partyId", label="往来方",
                aliases=["partyId"], kind="select", value="甲公司", op="select", required=True,
            ),
            _control(
                request_id="req_write", path="body.status", label="单据状态",
                aliases=["status"], kind="select", value="草稿", op="select",
            ),
            _control(
                request_id="req_write", path="body.bookedAt", label="单据时间",
                aliases=["bookedAt"], kind="datetime", value="2026-08-01", op="fill", required=True,
            ),
            _control(
                request_id="req_write", path="body.unitPrice", label="单价",
                aliases=["unitPrice"], kind="number", value="80",
            ),
            _control(
                request_id="req_write", path="body.qty", label="数量",
                aliases=["qty"], kind="number", value="2", op="fill", required=True,
            ),
            _control(
                request_id="req_write", path="body.previewTitle", label="页面标题",
                aliases=["previewTitle"], kind="text", value="新建单据", read_only=True,
            ),
            _control(
                request_id="req_write", path="body.note", label="备注",
                aliases=["note"], kind="text", value="ok", op="fill",
            ),
        ],
        page_enum_options={"单据状态": _enum("单据状态", ["status"], "草稿")},
        samples={"往来方": "甲公司", "单据状态": "草稿", "备注": "ok"},
    )

    params = _params(spec)
    assert params["partyId"].source_kind == "api_option"
    assert params["partyId"].key == "partyId"
    assert params["partyId"].label == "往来方"
    assert params["partyId"].type == "enum"
    assert params["partyId"].required is True
    assert params["partyId"].exposed_to_user is True
    assert params["status"].source_kind == "page_enum"
    assert params["status"].label == "单据状态"
    assert params["bookedAt"].type == "datetime"
    assert params["bookedAt"].wire_type in {"number", "integer"}
    assert params["bookedAt"].required is True
    assert params["qty"].type == "number"
    assert params["unitPrice"].source_kind == "page_default"
    assert params["unitPrice"].exposed_to_user is True
    assert params["unitPrice"].editable is True
    assert params["lineAmount"].source_kind == "computed"
    assert params["lineAmount"].exposed_to_user is False
    assert params["previewTitle"].source_kind == "page_rule"
    assert params["previewTitle"].exposed_to_user is False
    assert params["previewTitle"].required is False
    assert params["note"].source_kind == "user_input"
    assert params["note"].required is False

    client = flow_spec_to_client(spec)
    client_write = next(
        step for step in client["steps"] if (step.get("method") or "").upper() == "POST"
    )
    client_params = {item["path"]: item for item in client_write.get("params") or []}
    assert client_params["partyId"]["source_kind"] == "api_option"
    assert client_params["partyId"]["source"]["kind"] == "api_option"
    assert client_params["partyId"]["label"] == "往来方"
    assert client_params["partyId"]["key"] == "partyId"
    assert client_params["status"]["source_kind"] == "page_enum"
    assert client_params["bookedAt"]["type"] == "datetime"
    assert client_params["lineAmount"]["source_kind"] == "computed"
    assert client_params["previewTitle"]["source_kind"] == "page_rule"
    assert client_params["note"]["source_kind"] == "user_input"
    assert client_params["note"]["exposed_to_user"] is True
    assert client_params["previewTitle"]["exposed_to_user"] is False
    assert client_params["lineAmount"]["exposed_to_user"] is False


def test_page_rule_does_not_steal_option_projection_or_formula() -> None:
    spec = to_flow_spec(
        captured_requests=[
            _option_get("req_item", "/catalog/item/simple-list", [
                {"id": 7, "name": "零件A", "itemCode": "SKU-7", "unitPrice": 80},
                {"id": 8, "name": "零件B", "itemCode": "SKU-8", "unitPrice": 15},
            ], 1),
            {
                "request_id": "req_write",
                "sequence": 2,
                "method": "POST",
                "url": "http://example.test/api/doc/create",
                "post_data": json.dumps({
                    "itemId": 7,
                    "itemCode": "SKU-7",
                    "unitPrice": 80,
                    "qty": 2,
                    "lineAmount": 160,
                }),
                "response_status": 200,
                "page_id": "page_1",
                "frame_id": "frame_1",
                "page_context": PAGE,
                "_request_role": {"role": "business_write", "keep": True, "confidence": 0.99},
            },
        ],
        field_evidence=[
            _control(
                request_id="req_write", path="body.itemId", label="商品",
                aliases=["itemId"], kind="select", value="零件A", op="select", required=True,
            ),
            _control(
                request_id="req_write", path="body.itemCode", label="编码",
                aliases=["itemCode"], kind="text", value="SKU-7", read_only=True,
            ),
            _control(
                request_id="req_write", path="body.lineAmount", label="金额",
                aliases=["lineAmount"], kind="number", value="160", read_only=True,
            ),
            _control(
                request_id="req_write", path="body.qty", label="数量",
                aliases=["qty"], kind="number", value="2", op="fill", required=True,
            ),
        ],
    )
    params = _params(spec)
    assert params["itemCode"].source_kind == "selected_option_field"
    assert params["lineAmount"].source_kind == "computed"
    assert params["qty"].source_kind == "user_input"
