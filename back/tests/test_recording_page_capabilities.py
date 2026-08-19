from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from dano.execution.page.capability_compiler import compile_capabilities
from dano.execution.page.flow_spec import (
    FlowSpec,
    FlowStep,
    ParamField,
    _step_has_stable_record_identity,
    flow_spec_to_client,
    to_flow_spec,
)
from dano.execution.page.recording_live import merge_live_agent_state


SALE_PAGE = {
    "path": "/erp/sale/order",
    "url": "http://admin.dianshixinxi.com:90/erp/sale/order",
    "document_title": "销售订单",
    "visible_titles": ["销售订单"],
}
LEAVE_PAGE = {
    "path": "/office/leave",
    "url": "http://example.test/office/leave",
    "document_title": "请假申请",
    "visible_titles": ["请假申请"],
}


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
        "page_context": page_context or SALE_PAGE,
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
        "url": f"http://admin.dianshixinxi.com:90{path}",
        "response_status": 200,
        "response_json": {"data": rows},
        "page_id": "page_1",
        "frame_id": "frame_1",
        "page_context": page_context or SALE_PAGE,
        "_request_role": {"role": "read_option", "keep": False, "confidence": 0.99},
    }


def _enum(label: str, aliases: list[str], selected: str, page_context: dict | None = None) -> dict:
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
        "page_context": page_context or SALE_PAGE,
    }


def _step_id_for_request(spec: FlowSpec, request_id: str) -> str:
    for step in spec.steps:
        if str((step.source_meta or {}).get("request_id") or "") == request_id:
            return step.step_id
    raise AssertionError(f"missing materialized step for {request_id}")


def _cap(
    spec: FlowSpec,
    name: str,
    title: str,
    kind: str,
    request_id: str,
) -> dict:
    step_id = _step_id_for_request(spec, request_id)
    return {
        "name": name,
        "title": title,
        "kind": kind,
        "anchor_step_id": step_id,
        "request_refs": [{"step_id": step_id, "usage": "execute"}],
    }


def _compile(spec: FlowSpec, capabilities: list[dict], business_name: str = "") -> FlowSpec:
    plan = {
        "business_understanding": {
            "business_name": business_name or str(spec.title or ""),
            "summary": str(spec.title or business_name or ""),
        },
        "capabilities": capabilities,
        "unresolved_items": [],
    }
    compiled = compile_capabilities(spec, plan)
    assert compiled.errors == [], compiled.errors
    return compiled.spec


def _sale_order_plan(spec: FlowSpec, *, include_inspect: bool = False) -> list[dict]:
    capabilities = [
        _cap(spec, "query_sale_orders", "查询销售订单", "query_status", "req_query"),
        _cap(spec, "export_sale_orders", "导出销售订单", "export", "req_export"),
        _cap(spec, "create_sale_order", "新增销售订单", "create", "req_write"),
    ]
    if include_inspect:
        capabilities.append(
            _cap(spec, "inspect_sale_order", "查看销售订单", "inspect", "req_detail"),
        )
    return capabilities


def _by_kind(spec: FlowSpec) -> dict[str, object]:
    return {capability.kind: capability for capability in spec.capabilities}


def _ref_path(value: str) -> str:
    raw = str(value or "")
    parsed = urlparse(raw)
    return parsed.path or raw.split("?", 1)[0]


def _execute_paths(capability) -> list[str]:
    return [
        _ref_path(ref.path)
        for ref in (capability.request_refs or [])
        if ref.usage == "execute"
    ]


def _option_paths(capability) -> list[str]:
    return [
        _ref_path(ref.path)
        for ref in (capability.request_refs or [])
        if ref.usage == "option_source"
    ]


def _schema(capability) -> tuple[dict, list[str]]:
    schema = capability.input_schema or {}
    return dict(schema.get("properties") or {}), list(schema.get("required") or [])


def _sale_order_spec(*, price_op: str = "", include_inspect: bool = False) -> FlowSpec:
    body = {
        "customerId": 5,
        "accountId": 2,
        "saleUserId": 165,
        "orderTime": 1785513600000,
        "remark": "1",
        "discountPercent": 110,
        "discountPrice": 6600,
        "totalPrice": -600,
        "depositPrice": 1110,
        "items": [{
            "productId": 3,
            "productUnitName": "台",
            "productBarCode": "0101010101",
            "productPrice": 6000,
            "stockCount": 924.5,
            "count": 1,
            "totalProductPrice": 6000,
            "taxPrice": None,
            "totalPrice": 6000,
        }],
    }
    query = {
        "pageNo": ["1"],
        "pageSize": ["10"],
        "no": ["1"],
        "customerId": ["5"],
        "productId": ["3"],
        "orderTime[0]": ["2026-08-07 00:00:00"],
        "orderTime[1]": ["2026-08-08 23:59:59"],
        "status": ["10"],
        "remark": ["1"],
        "creator": ["100"],
        "outStatus": ["0"],
        "returnStatus": ["1"],
    }
    query_fields = [
        ("no", "订单单号", "text", "1"),
        ("customerId", "客户", "select", "甲公司"),
        ("productId", "产品", "select", "主机"),
        ("orderTime[0]", "开始日期", "date", "2026-08-07"),
        ("orderTime[1]", "结束日期", "date", "2026-08-08"),
        ("status", "状态", "select", "未审核"),
        ("remark", "备注", "text", "1"),
        ("creator", "创建人", "select", "管理员"),
        ("outStatus", "出库数量", "select", "未出库"),
        ("returnStatus", "退货数量", "select", "未退货"),
    ]
    return to_flow_spec(
        captured_requests=[
            _option_get("req_customer", "/admin-api/erp/customer/simple-list", [
                {"id": 5, "name": "甲公司"}, {"id": 6, "name": "乙公司"},
            ], 1),
            _option_get("req_account", "/admin-api/erp/account/simple-list", [
                {"id": 2, "name": "基本户"}, {"id": 3, "name": "现金"},
            ], 2),
            _option_get("req_user", "/admin-api/system/user/simple-list", [
                {"id": 165, "name": "李四"}, {"id": 166, "name": "王五"},
            ], 3),
            _option_get("req_product", "/admin-api/erp/product/simple-list", [
                {
                    "id": 3, "name": "主机", "productBarCode": "0101010101",
                    "productUnitName": "台", "stockCount": 924.5, "productPrice": 6000,
                },
                {
                    "id": 4, "name": "配件", "productBarCode": "0202020202",
                    "productUnitName": "件", "stockCount": 12, "productPrice": 80,
                },
            ], 4),
            {
                "request_id": "req_tenant",
                "sequence": 5,
                "method": "GET",
                "url": "http://admin.dianshixinxi.com:90/admin-api/system/tenant/simple-list",
                "response_status": 200,
                "response_json": {"data": [{"id": 1, "name": "未退货"}, {"id": 2, "name": "部分退货"}]},
                "page_id": "page_1",
                "frame_id": "frame_1",
                "page_context": SALE_PAGE,
                "_request_role": {"role": "read_option", "keep": False, "confidence": 0.99},
            },
            {
                "request_id": "req_query",
                "sequence": 6,
                "method": "GET",
                "url": "http://admin.dianshixinxi.com:90/admin-api/erp/sale-order/page",
                "query": query,
                "response_status": 200,
                "response_json": {"data": {"list": [{"id": 1, "no": "SO-1"}], "total": 1}},
                "page_id": "page_1",
                "frame_id": "frame_1",
                "page_context": SALE_PAGE,
                "trigger_op": "click",
                "trigger_locator": "text=搜索",
                "_request_role": {"role": "business_get", "keep": True, "confidence": 0.99},
            },
            {
                "request_id": "req_export",
                "sequence": 7,
                "method": "GET",
                "url": "http://admin.dianshixinxi.com:90/admin-api/erp/sale-order/export-excel",
                "query": {"no": ["1"], "customerId": ["5"]},
                "response_status": 200,
                "response_json": {"data": "ok"},
                "page_id": "page_1",
                "frame_id": "frame_1",
                "page_context": SALE_PAGE,
                "trigger_op": "click",
                "trigger_locator": "text=确定",
                "_request_role": {"role": "business_get", "keep": True, "confidence": 0.99},
            },
            {
                "request_id": "req_write",
                "sequence": 8,
                "method": "POST",
                "url": "http://admin.dianshixinxi.com:90/admin-api/erp/sale-order/create",
                "post_data": json.dumps(body, ensure_ascii=False),
                "response_status": 200,
                "response_json": {"code": 0},
                "page_id": "page_1",
                "frame_id": "frame_1",
                "page_context": SALE_PAGE,
                "trigger_op": "click",
                "trigger_locator": "text=确定",
                "_request_role": {"role": "business_write", "keep": True, "confidence": 0.99},
            },
            *(
                [{
                    "request_id": "req_detail",
                    "sequence": 9,
                    "method": "GET",
                    "url": "http://admin.dianshixinxi.com:90/admin-api/erp/sale-order/get?id=40",
                    "query": {"id": ["40"]},
                    "response_status": 200,
                    "response_json": {"data": {"id": 40, "no": "SO-1"}},
                    "page_id": "page_1",
                    "frame_id": "frame_1",
                    "page_context": SALE_PAGE,
                    "trigger_op": "click",
                    "trigger_locator": "text=详情",
                    "_request_role": {"role": "business_get", "keep": True, "confidence": 0.99},
                }]
                if include_inspect else []
            ),
        ],
        field_evidence=[
            *(
                _control(
                    request_id="req_query", path=f"query.{path}", label=label,
                    aliases=[path.split("[")[0]], kind=kind, value=value,
                )
                for path, label, kind, value in query_fields
            ),
            _control(
                request_id="req_write", path="body.customerId", label="客户",
                aliases=["customerId"], kind="select", value="甲公司", op="select", required=True,
            ),
            _control(
                request_id="req_write", path="body.accountId", label="结算账户",
                aliases=["accountId"], kind="select", value="基本户", op="select",
            ),
            _control(
                request_id="req_write", path="body.saleUserId", label="销售人员",
                aliases=["saleUserId"], kind="select", value="李四", op="select",
            ),
            _control(
                request_id="req_write", path="body.orderTime", label="订单时间",
                aliases=["orderTime"], kind="datetime", value="2026-08-01", op="fill", required=True,
            ),
            _control(
                request_id="req_write", path="body.remark", label="备注",
                aliases=["remark"], kind="text", value="1", op="fill",
            ),
            _control(
                request_id="req_write", path="body.discountPercent", label="优惠率",
                aliases=["discountPercent"], kind="number", value="110", op="fill",
            ),
            _control(
                request_id="req_write", path="body.depositPrice", label="收取订金",
                aliases=["depositPrice"], kind="number", value="1110", op="fill",
            ),
            _control(
                request_id="req_write", path="body.items[0].productId", label="产品名称",
                aliases=["productId"], kind="select", value="主机", op="select", required=True,
            ),
            _control(
                request_id="req_write", path="body.items[0].count", label="数量",
                aliases=["count"], kind="number", value="1", op="fill", required=True,
            ),
            _control(
                request_id="req_write", path="body.items[0].productPrice", label="产品单价",
                aliases=["productPrice"], kind="number", value="6000", op=price_op,
            ),
            _control(
                request_id="req_write", path="body.items[0].productBarCode", label="条码",
                aliases=["productBarCode"], kind="text", value="0101010101", read_only=True,
            ),
            _control(
                request_id="req_write", path="body.items[0].productUnitName", label="单位",
                aliases=["productUnitName"], kind="text", value="台", read_only=True,
            ),
            _control(
                request_id="req_write", path="body.items[0].stockCount", label="库存",
                aliases=["stockCount"], kind="number", value="924.5", read_only=True,
            ),
        ],
        page_enum_options={
            "客户": _enum("客户", ["customerId"], "甲公司"),
            "退货数量": {
                **_enum("退货数量", ["returnStatus"], "未退货"),
                "mapping_complete": False,
            },
        },
        page_context=SALE_PAGE,
        samples={
            "客户": "甲公司",
            "结算账户": "基本户",
            "销售人员": "李四",
            "备注": "1",
        },
    )


def _delete_request(
    request_id: str,
    *,
    sequence: int,
    ids: str,
    path: str = "/admin-api/erp/sale-order/delete",
    locator: str = "",
    page_context: dict | None = None,
) -> dict:
    return {
        "request_id": request_id,
        "sequence": sequence,
        "method": "DELETE",
        "url": f"http://admin.dianshixinxi.com:90{path}?ids={ids}",
        "query": {"ids": [ids]},
        "response_status": 200,
        "response_json": {"code": 0},
        "page_id": "page_1",
        "frame_id": "frame_1",
        "page_context": page_context or SALE_PAGE,
        "trigger_op": "click",
        "trigger_locator": locator or f'tr:has-text("SO-{ids}") >> text=删除',
        "trigger_action_id": f"act_{request_id}",
        "trigger_transaction_id": f"txn_{request_id}",
        "causality_confidence": "high",
        "_request_role": {"role": "business_write", "keep": True, "confidence": 0.99},
    }


def _sale_order_search_and_deletes(*ids: str) -> FlowSpec:
    captured = [
        {
            "request_id": "req_query",
            "sequence": 1,
            "method": "GET",
            "url": "http://admin.dianshixinxi.com:90/admin-api/erp/sale-order/page?pageNo=1&pageSize=10",
            "query": {"pageNo": ["1"], "pageSize": ["10"]},
            "response_status": 200,
            "response_json": {
                "data": {
                    "list": [{"id": int(item), "no": f"SO-{item}"} for item in ids],
                    "total": len(ids),
                },
            },
            "page_id": "page_1",
            "frame_id": "frame_1",
            "page_context": SALE_PAGE,
            "trigger_op": "click",
            "trigger_locator": "text=搜索",
            "_request_role": {"role": "business_get", "keep": True, "confidence": 0.99},
        },
        *(
            _delete_request(f"req_delete_{item}", sequence=index + 2, ids=item)
            for index, item in enumerate(ids)
        ),
    ]
    spec = to_flow_spec(captured_requests=captured, page_context=SALE_PAGE)
    spec.meta = {
        **(spec.meta or {}),
        "recording_goal_text": "\n".join([
            f"预期产出能力数量：{1 + len(ids)}",
            "能力1：搜索销售订单",
            *(
                f"能力{index}：删除销售订单 (ID {item})"
                for index, item in enumerate(ids, start=2)
            ),
        ]),
    }
    return spec


def test_line_item_id_is_not_document_record_identity() -> None:
    line_only = FlowStep(
        step_id="create",
        method="POST",
        params=[
            ParamField(path="items[0].itemId", key="itemId", value="3"),
            ParamField(path="items[0].count", key="count", value="1"),
        ],
    )
    assert _step_has_stable_record_identity(line_only) is False

    document = FlowStep(
        step_id="update",
        method="PUT",
        params=[ParamField(path="id", key="id", value="9")],
    )
    assert _step_has_stable_record_identity(document) is True

    batch_delete = FlowStep(
        step_id="delete",
        method="DELETE",
        params=[ParamField(path="query.ids", key="ids", value="39")],
    )
    assert _step_has_stable_record_identity(batch_delete) is True


def test_repeated_delete_clicks_compile_one_caller_identity_capability() -> None:
    raw = _sale_order_search_and_deletes("39", "38")
    spec = _compile(raw, [
        _cap(raw, "query_sale_orders", "查询销售订单", "query_status", "req_query"),
        _cap(raw, "delete_sale_order", "删除销售订单", "delete", "req_delete_39"),
    ], "销售订单")
    deletes = [capability for capability in spec.capabilities if capability.kind == "delete"]
    queries = [capability for capability in spec.capabilities if capability.kind == "query_status"]

    assert len(queries) == 1
    assert len(deletes) == 1
    assert "38" not in str(deletes[0].title or "")
    assert "39" not in str(deletes[0].title or "")
    assert "删除" in str(deletes[0].title or "")
    assert _execute_paths(deletes[0]) == ["/admin-api/erp/sale-order/delete"]
    assert all("/sale-order/page" not in path for path in _execute_paths(deletes[0]))

    delete_step = next(step for step in spec.steps if (step.method or "").upper() == "DELETE")
    ids_param = next(param for param in delete_step.params if str(param.key or "").casefold() == "ids")
    assert ids_param.source_kind == "user_input"
    assert ids_param.exposed_to_user is True
    assert ids_param.required is True
    assert ids_param.category == "user_param"

    props, required = _schema(deletes[0])
    assert "ids" in props
    assert "ids" in required


def test_similar_delete_contracts_stay_distinct() -> None:
    raw = to_flow_spec(
        captured_requests=[
            _delete_request("req_order", sequence=1, ids="39"),
            _delete_request(
                "req_item",
                sequence=2,
                ids="7",
                path="/admin-api/erp/sale-order-item/delete",
                locator="text=删除明细",
            ),
        ],
        page_context=SALE_PAGE,
    )
    spec = _compile(raw, [
        _cap(raw, "delete_sale_order", "删除销售订单", "delete", "req_order"),
        _cap(raw, "delete_sale_order_item", "删除销售订单明细", "delete", "req_item"),
    ], "销售订单")
    deletes = [capability for capability in spec.capabilities if capability.kind == "delete"]
    assert len(deletes) == 2
    assert sorted(_execute_paths(capability)[0] for capability in deletes) == [
        "/admin-api/erp/sale-order-item/delete",
        "/admin-api/erp/sale-order/delete",
    ]


def test_single_delete_without_page_control_still_exposes_ids() -> None:
    raw = to_flow_spec(
        captured_requests=[_delete_request("req_delete", sequence=1, ids="39")],
        page_context=SALE_PAGE,
    )
    spec = _compile(raw, [
        _cap(raw, "delete_sale_order", "删除销售订单", "delete", "req_delete"),
    ], "销售订单")
    delete = next(capability for capability in spec.capabilities if capability.kind == "delete")
    props, required = _schema(delete)
    assert set(props) == {"ids"}
    assert required == ["ids"]
    step = next(item for item in spec.steps if (item.method or "").upper() == "DELETE")
    assert step.params[0].source_kind == "user_input"
    assert step.params[0].category == "user_param"
    assert "未决" not in str(step.params[0].reason or "")
    assert "record_identity" in str((step.params[0].source or {}).get("kind") or "")


def test_materialize_without_skill_plan_does_not_invent_capabilities() -> None:
    raw = _sale_order_spec()
    assert raw.capabilities == []

    merged = merge_live_agent_state(FlowSpec(meta={}), raw)
    assert merged.capabilities == []
    gateway = (
        Path(__file__).resolve().parents[1]
        / "dano"
        / "onboarding"
        / "recording_gateway.py"
    ).read_text(encoding="utf-8")
    assert "compile_recorded_capabilities" not in gateway
    assert "missing_semantic_plan" in gateway


def test_sale_order_page_compiles_query_export_and_create() -> None:
    raw = _sale_order_spec()
    spec = _compile(raw, _sale_order_plan(raw), "销售订单")
    by_kind = _by_kind(spec)

    assert spec.title == "销售订单"
    assert set(by_kind) == {"query_status", "export", "create"}
    assert all("销售订单" in str(capability.title or "") for capability in spec.capabilities)

    query = by_kind["query_status"]
    export = by_kind["export"]
    create = by_kind["create"]

    assert _execute_paths(query) == ["/admin-api/erp/sale-order/page"]
    assert _execute_paths(export) == ["/admin-api/erp/sale-order/export-excel"]
    assert _execute_paths(create) == ["/admin-api/erp/sale-order/create"]
    assert all("/export-excel" not in path for path in _execute_paths(query))
    assert all("/sale-order/page" not in path for path in _execute_paths(create))
    assert all("/sale-order/create" not in path for path in _execute_paths(query))

    option_paths = _option_paths(create)
    assert any(path.endswith("/erp/customer/simple-list") for path in option_paths)
    assert any(path.endswith("/erp/product/simple-list") for path in option_paths)
    assert any(path.endswith("/erp/account/simple-list") for path in option_paths)
    assert any(path.endswith("/system/user/simple-list") for path in option_paths)
    assert all("tenant/simple-list" not in path for path in option_paths)
    assert all(ref.usage != "execute" for ref in create.request_refs if "simple-list" in str(ref.path or ""))

    usages = [ref.usage for ref in create.request_refs]
    option_indexes = [index for index, usage in enumerate(usages) if usage == "option_source"]
    execute_indexes = [index for index, usage in enumerate(usages) if usage == "execute"]
    assert option_indexes
    assert execute_indexes
    assert max(option_indexes) < min(execute_indexes)
    option_nodes = [
        node for node in (create.nodes or [])
        if isinstance(node, dict) and node.get("usage") == "option_source"
    ]
    execute_nodes = [
        node for node in (create.nodes or [])
        if isinstance(node, dict) and (
            node.get("usage") == "execute" or node.get("step_id") == create.request_refs[-1].step_id
        )
    ]
    assert option_nodes
    assert execute_nodes

    client = flow_spec_to_client(spec)
    client_create = next(item for item in client["capabilities"] if item.get("kind") == "create")
    client_write = next(
        step for step in client["steps"] if (step.get("method") or "").upper() == "POST"
    )
    client_params = {item["path"]: item for item in client_write.get("params") or []}
    assert client_params["customerId"]["source_kind"] == "api_option"
    assert client_params["customerId"]["label"] == "客户"
    assert client_params["customerId"]["key"] == "customerId"
    assert client_params["orderTime"]["type"] == "datetime"
    assert client_params["items[0].productBarCode"]["source_kind"] == "selected_option_field"
    assert client_params["discountPrice"]["source_kind"] == "computed"
    assert client_params["items[0].productPrice"]["source_kind"] == "page_default"
    assert client_params["items[0].productPrice"]["exposed_to_user"] is True
    client_usages = [ref.get("usage") for ref in client_create.get("request_refs") or []]
    assert "option_source" in client_usages
    assert client_usages.index("option_source") < client_usages.index("execute")


def test_sale_order_query_schema_keeps_filters_not_pagination() -> None:
    raw = _sale_order_spec()
    spec = _compile(raw, _sale_order_plan(raw), "销售订单")
    props, required = _schema(_by_kind(spec)["query_status"])

    assert "pageNo" not in props
    assert "pageSize" not in props
    assert props["no"]["label"] == "订单单号"
    assert props["customerId"]["label"] == "客户"
    assert props["productId"]["label"] == "产品"
    assert props["orderTime[0]"]["label"] == "开始日期"
    assert props["orderTime[1]"]["label"] == "结束日期"
    assert props["status"]["label"] == "状态"
    assert props["remark"]["label"] == "备注"
    assert props["creator"]["label"] == "创建人"
    assert props["outStatus"]["label"] == "出库数量"
    assert props["returnStatus"]["label"] == "退货数量"
    assert required == []
    assert "tenant" not in str(props["returnStatus"].get("x-dano-option-source") or {}).lower()


def test_sale_order_create_schema_exposes_only_caller_fields() -> None:
    raw = _sale_order_spec()
    spec = _compile(raw, _sale_order_plan(raw), "销售订单")
    write = next(step for step in spec.steps if (step.method or "").upper() == "POST")
    params = {param.path: param for param in write.params}
    props, required = _schema(_by_kind(spec)["create"])

    assert params["customerId"].source_kind == "api_option"
    assert params["customerId"].label == "客户"
    assert params["saleUserId"].source_kind != "current_user"
    assert params["saleUserId"].label == "销售人员"
    assert params["items[0].productId"].source_kind == "api_option"
    for path in (
        "items[0].productBarCode",
        "items[0].productUnitName",
        "items[0].stockCount",
    ):
        assert params[path].source_kind == "selected_option_field"
        assert params[path].exposed_to_user is False
        assert params[path].key == path.split(".")[-1]
    assert params["items[0].productPrice"].source_kind == "page_default"
    assert params["items[0].productPrice"].exposed_to_user is True
    assert params["items[0].productPrice"].editable is True
    assert params["items[0].productPrice"].required is False
    for path in ("discountPrice", "totalPrice", "items[0].totalProductPrice", "items[0].totalPrice"):
        assert params[path].source_kind == "computed"
        assert params[path].exposed_to_user is False
    assert params["items[0].count"].key == "count"
    assert params["items[0].count"].label == "数量"
    assert params["items[0].count"].exposed_to_user is True

    assert set(props) == {
        "customerId", "accountId", "saleUserId", "orderTime",
        "remark", "discountPercent", "depositPrice", "productId", "productPrice", "count",
    }
    assert props["customerId"]["label"] == "客户"
    assert props["saleUserId"]["label"] == "销售人员"
    assert props["depositPrice"]["label"] == "收取订金"
    assert props["count"]["label"] == "数量"
    assert set(required) == {"customerId", "orderTime", "productId", "count"}
    for hidden in (
        "productBarCode", "productUnitName", "stockCount",
        "discountPrice", "totalPrice", "totalProductPrice", "taxPrice",
    ):
        assert hidden not in props


def test_edited_unit_price_stays_caller_input() -> None:
    raw = _sale_order_spec(price_op="fill")
    spec = _compile(raw, _sale_order_plan(raw), "销售订单")
    write = next(step for step in spec.steps if (step.method or "").upper() == "POST")
    params = {param.path: param for param in write.params}
    props, _required = _schema(_by_kind(spec)["create"])

    assert params["items[0].productPrice"].source_kind == "user_input"
    assert params["items[0].productPrice"].exposed_to_user is True
    assert props["productPrice"]["label"] == "产品单价"


def test_sale_order_detail_compiles_inspect() -> None:
    raw = _sale_order_spec(include_inspect=True)
    spec = _compile(raw, _sale_order_plan(raw, include_inspect=True), "销售订单")
    by_kind = _by_kind(spec)
    assert set(by_kind) >= {"query_status", "export", "create", "inspect"}
    assert _execute_paths(by_kind["inspect"]) == ["/admin-api/erp/sale-order/get"]
    assert all("/sale-order/get" not in path for path in _execute_paths(by_kind["create"]))
    assert all("/sale-order/get" not in path for path in _execute_paths(by_kind["query_status"]))


def test_leave_page_still_splits_query_and_create() -> None:
    begin = 1_785_513_600_000
    finish = begin + 3 * 86_400_000
    raw = to_flow_spec(
        captured_requests=[
            {
                "request_id": "req_kind",
                "sequence": 1,
                "method": "GET",
                "url": "http://example.test/api/office/kind/simple-list",
                "response_status": 200,
                "response_json": {"data": [{"id": 2, "name": "年假"}, {"id": 3, "name": "事假"}]},
                "page_id": "page_1",
                "frame_id": "frame_1",
                "page_context": LEAVE_PAGE,
                "_request_role": {"role": "read_option", "keep": False, "confidence": 0.99},
            },
            {
                "request_id": "req_query",
                "sequence": 2,
                "method": "GET",
                "url": "http://example.test/api/office/leave/page",
                "query": {"pageNo": ["1"], "pageSize": ["10"], "keyword": ["回家"], "status": ["0"]},
                "response_status": 200,
                "response_json": {"data": {"list": [], "total": 0}},
                "page_id": "page_1",
                "frame_id": "frame_1",
                "page_context": LEAVE_PAGE,
                "trigger_op": "click",
                "trigger_locator": "text=查询",
                "_request_role": {"role": "business_get", "keep": True, "confidence": 0.99},
            },
            {
                "request_id": "req_write",
                "sequence": 3,
                "method": "POST",
                "url": "http://example.test/api/office/leave/create",
                "post_data": json.dumps({
                    "kindId": 2,
                    "beginAt": begin,
                    "finishAt": finish,
                    "span": 3,
                    "comment": "回家",
                }),
                "response_status": 200,
                "response_json": {"code": 0},
                "page_id": "page_1",
                "frame_id": "frame_1",
                "page_context": LEAVE_PAGE,
                "trigger_op": "click",
                "trigger_locator": "text=提交",
                "_request_role": {"role": "business_write", "keep": True, "confidence": 0.99},
            },
        ],
        field_evidence=[
            _control(
                request_id="req_query", path="query.keyword", label="关键字",
                aliases=["keyword"], kind="text", value="回家", op="fill",
                page_context=LEAVE_PAGE,
            ),
            _control(
                request_id="req_query", path="query.status", label="状态",
                aliases=["status"], kind="select", value="待审批",
                page_context=LEAVE_PAGE,
            ),
            _control(
                request_id="req_write", path="body.kindId", label="请假类型",
                aliases=["kindId"], kind="select", value="年假", op="select",
                required=True, page_context=LEAVE_PAGE,
            ),
            _control(
                request_id="req_write", path="body.beginAt", label="开始日期",
                aliases=["beginAt"], kind="date", value="2026-08-01", op="fill",
                required=True, page_context=LEAVE_PAGE,
            ),
            _control(
                request_id="req_write", path="body.finishAt", label="结束日期",
                aliases=["finishAt"], kind="date", value="2026-08-04", op="fill",
                required=True, page_context=LEAVE_PAGE,
            ),
            _control(
                request_id="req_write", path="body.span", label="天数",
                aliases=["span"], kind="number", value="3",
                read_only=True, page_context=LEAVE_PAGE,
            ),
            _control(
                request_id="req_write", path="body.comment", label="事由",
                aliases=["comment"], kind="textarea", value="回家", op="fill",
                required=True, page_context=LEAVE_PAGE,
            ),
        ],
        page_enum_options={"请假类型": _enum("请假类型", ["kindId"], "年假", LEAVE_PAGE)},
        page_context=LEAVE_PAGE,
        samples={"请假类型": "年假", "事由": "回家"},
    )
    spec = _compile(raw, [
        _cap(raw, "query_leave", "查询请假申请", "query_status", "req_query"),
        _cap(raw, "submit_leave", "提交请假申请", "submit", "req_write"),
    ], "请假申请")
    by_kind = _by_kind(spec)

    assert spec.title == "请假申请"
    assert set(by_kind) == {"query_status", "submit"}
    assert _execute_paths(by_kind["query_status"]) == ["/api/office/leave/page"]
    assert _execute_paths(by_kind["submit"]) == ["/api/office/leave/create"]

    query_props, query_required = _schema(by_kind["query_status"])
    assert "pageNo" not in query_props
    assert query_props["keyword"]["label"] == "关键字"
    assert query_props["status"]["label"] == "状态"
    assert query_required == []

    create_props, create_required = _schema(by_kind["submit"])
    assert set(create_props) == {"kindId", "beginAt", "finishAt", "comment"}
    assert create_props["kindId"]["label"] == "请假类型"
    assert create_props["comment"]["label"] == "事由"
    assert set(create_required) == {"kindId", "beginAt", "finishAt", "comment"}
    assert "span" not in create_props
