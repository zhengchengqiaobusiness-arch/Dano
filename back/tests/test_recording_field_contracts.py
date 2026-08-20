"""Mechanical field-axis contracts for generic list / edit / row-command pages."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

from dano.execution.page.capability_compiler import compile_capabilities
from dano.execution.page.flow_materialization.field_contracts.caller_ownership import _param_exposed_to_caller
from dano.execution.page.flow_spec import to_flow_spec
from dano.execution.page.flow_spec_core.models import FlowLink, FlowSpec, FlowStep, ParamField


PAGE = {
    "url": "http://example.test/app/docs",
    "path": "/app/docs",
    "document_title": "单据",
}


def _req(
    request_id: str,
    *,
    method: str,
    url: str,
    sequence: int,
    query: dict | None = None,
    body: dict | None = None,
    response: dict | None = None,
    role: str,
    action: str = "",
    locator: str = "",
) -> dict:
    parsed_query = query or {}
    if "?" in url and not parsed_query:
        parsed_query = parse_qs(urlsplit(url).query, keep_blank_values=True)
    return {
        "request_id": request_id,
        "sequence": sequence,
        "method": method,
        "url": url,
        "query": parsed_query,
        "post_data": None if body is None else json.dumps(body, ensure_ascii=False),
        "response_status": 200,
        "response_json": response if response is not None else {"code": 0, "data": True},
        "page_id": "page_1",
        "frame_id": "frame_1",
        "page_context": PAGE,
        "trigger_page_context": PAGE,
        "trigger_action_id": action,
        "trigger_locator": locator,
        "trigger_transaction_id": action,
        "_request_role": {"role": role, "keep": True, "confidence": 0.95},
    }


def _control(
    *,
    label: str,
    aliases: list[str],
    kind: str,
    value: str,
    request_id: str,
    path: str,
    in_dialog: bool,
    required: bool = False,
    read_only: bool = False,
    op: str = "snapshot",
    source_url: str = "",
) -> dict:
    wire_path = path if path.startswith(("query.", "body.")) else f"body.{path}"
    item = {
        "label": label,
        "field": aliases[0] if aliases else label,
        "value": value,
        "field_aliases": aliases,
        "control_kind": kind,
        "required": required,
        "required_observed": required,
        "binding_status": "bound",
        "request_id": request_id,
        "wire_path": wire_path,
        "op": op,
        "in_dialog": in_dialog,
        "surface": "dialog" if in_dialog else "page",
        "disabled": False,
        "read_only": read_only,
        "page_id": "page_1",
        "frame_id": "frame_1",
        "page_context": PAGE,
    }
    if source_url:
        item["source_url"] = source_url
    return item


def _page_enum(*, label: str, aliases: list[str], selected: str, selected_value, options: list, source_url: str) -> dict:
    return {
        label: {
            "control_kind": "select",
            "field_key": aliases[0],
            "field_aliases": aliases,
            "selected": selected,
            "selected_value": selected_value,
            "options": options,
            "source_url": source_url,
            "enum_source": "api",
            "in_dialog": False,
            "surface": "page",
        }
    }


def _param(step, path: str):
    relative = path.removeprefix("body.").removeprefix("query.")
    for item in step.params:
        if item.path in {path, relative, f"body.{relative}", f"query.{relative}"}:
            return item
    raise AssertionError(f"{path} not found on {step.method} {step.path}: {[p.path for p in step.params]}")


def _step_by_suffix(spec, suffix: str):
    matches = [
        step for step in spec.steps
        if str(step.path or step.url or "").split("?", 1)[0].endswith(suffix)
    ]
    assert matches, f"no step ending with {suffix}: {[step.path for step in spec.steps]}"
    return matches[0]


def _compile(spec, capabilities: list[dict]):
    plan = {
        "business_understanding": {"business_name": "单据"},
        "capabilities": capabilities,
        "unresolved_items": [],
    }
    compilation = compile_capabilities(spec, plan)
    assert not compilation.errors, compilation.errors
    return compilation.spec


def _edit_and_command_spec(*, include_dialog_controls: bool = True):
    dict_url = "http://example.test/admin-api/system/dict-data/simple-list?dictType=doc_status"
    status_options = [
        {"label": "待审", "value": 10},
        {"label": "已审", "value": 20},
    ]
    detail = {
        "id": 32,
        "partyId": 5,
        "qty": 2,
        "unitPrice": 6000,
        "lineTotal": 12000,
        "note": "keep",
        "status": 10,
    }
    update_body = {
        "id": 32,
        "partyId": 5,
        "qty": 2,
        "unitPrice": 6000,
        "lineTotal": 12000,
        "note": "keep",
    }
    evidence = [
        _control(
            label="状态", aliases=["status"], kind="select", value="待审",
            request_id="req_list", path="query.status", in_dialog=False, source_url=dict_url,
        ),
        _control(
            label="单据编号", aliases=["no"], kind="text", value="",
            request_id="req_list", path="query.no", in_dialog=False,
        ),
    ]
    if include_dialog_controls:
        evidence.extend([
            _control(
                label="往来单位", aliases=["partyId"], kind="select", value="甲公司",
                request_id="req_update", path="body.partyId", in_dialog=True,
            ),
            _control(
                label="数量", aliases=["qty"], kind="number", value="2",
                request_id="req_update", path="body.qty", in_dialog=True,
            ),
            _control(
                label="单价", aliases=["unitPrice"], kind="number", value="6000",
                request_id="req_update", path="body.unitPrice", in_dialog=True,
            ),
            _control(
                label="金额", aliases=["lineTotal"], kind="number", value="12000",
                request_id="req_update", path="body.lineTotal", in_dialog=True, read_only=True,
            ),
            _control(
                label="备注", aliases=["note"], kind="textarea", value="keep",
                request_id="req_update", path="body.note", in_dialog=True,
            ),
        ])
    return to_flow_spec(
        captured_requests=[
            _req(
                "req_dict", method="GET", url=dict_url, sequence=1, role="read_option",
                response={"code": 0, "data": status_options},
            ),
            _req(
                "req_list", method="GET",
                url="http://example.test/admin-api/doc/page?pageNo=1&pageSize=10&status=10",
                sequence=2, role="business_get", action="act_search", locator="text=搜索",
                response={"code": 0, "data": {"list": [{"id": 32, "status": 10}]}},
            ),
            _req(
                "req_get", method="GET",
                url="http://example.test/admin-api/doc/get?id=32",
                sequence=3, role="business_get", action="act_edit", locator="text=编辑",
                response={"code": 0, "data": detail},
            ),
            _req(
                "req_update", method="PUT",
                url="http://example.test/admin-api/doc/update",
                sequence=4, role="business_write", action="act_edit", locator="text=确定",
                body=update_body, response={"code": 0, "data": True},
            ),
            _req(
                "req_approve", method="PUT",
                url="http://example.test/admin-api/doc/update-status",
                sequence=5, role="business_write", action="act_approve", locator="text=审批",
                body={"id": 32, "status": 20}, response={"code": 0, "data": True},
            ),
            _req(
                "req_reject", method="PUT",
                url="http://example.test/admin-api/doc/update-status",
                sequence=6, role="business_write", action="act_reject", locator="text=反审批",
                body={"id": 32, "status": 10}, response={"code": 0, "data": True},
            ),
        ],
        reads=[{
            "request_id": "req_dict",
            "method": "GET",
            "url": dict_url,
            "json": status_options,
            "role": "explicit_read_option",
            "page_id": "page_1",
            "frame_id": "frame_1",
            "page_context": PAGE,
        }],
        field_evidence=evidence,
        page_enum_options=_page_enum(
            label="状态",
            aliases=["status", "状态"],
            selected="待审",
            selected_value=10,
            options=status_options,
            source_url=dict_url,
        ),
        page_events=[
            {"event_id": "ev_search", "kind": "click", "action_id": "act_search"},
            {"event_id": "ev_edit", "kind": "click", "action_id": "act_edit"},
            {"event_id": "ev_approve", "kind": "click", "action_id": "act_approve"},
            {"event_id": "ev_reject", "kind": "click", "action_id": "act_reject"},
        ],
        page_context=PAGE,
        samples={"往来单位": "甲公司", "数量": "2", "备注": "keep", "状态": "待审"},
    )


def test_edit_hydrated_fields_are_caller_owned_with_upstream_default() -> None:
    spec = _edit_and_command_spec()
    update = _step_by_suffix(spec, "/doc/update")
    for item in (_param(update, "partyId"), _param(update, "qty"), _param(update, "note")):
        assert _param_exposed_to_caller(item), (item.path, item.source_kind, item.exposed_to_user, item.reason)
        assert item.source_kind == "previous_response"
        assert bool((item.source or {}).get("allow_caller_override"))
        assert "可修改" in (item.reason or "")


def test_edit_formula_totals_are_computed_not_unknown() -> None:
    spec = _edit_and_command_spec()
    total = _param(_step_by_suffix(spec, "/doc/update"), "lineTotal")
    assert total.source_kind == "computed", (total.source_kind, total.reason)
    assert not _param_exposed_to_caller(total)
    assert total.source_kind != "unknown"


def test_row_command_id_is_caller_selected_record_not_upstream() -> None:
    spec = _edit_and_command_spec()
    record_id = _param(_step_by_suffix(spec, "/doc/update-status"), "id")
    assert _param_exposed_to_caller(record_id)
    assert record_id.source_kind == "user_input"
    assert (record_id.source or {}).get("kind") == "selected_record_identity"
    assert record_id.required is True
    assert record_id.source_kind != "previous_response"


def test_row_command_fixed_payload_is_constant_not_live_option() -> None:
    spec = _edit_and_command_spec()
    status = _param(_step_by_suffix(spec, "/doc/update-status"), "status")
    assert status.source_kind == "constant", (status.source_kind, status.type, status.reason)
    assert not _param_exposed_to_caller(status)
    assert status.source_kind != "api_option"
    assert status.type != "enum"


def test_row_command_orchestration_does_not_pull_unrelated_dict_api() -> None:
    spec = _edit_and_command_spec()
    approve = _step_by_suffix(spec, "/doc/update-status")
    spec = _compile(spec, [{
        "name": "approve_doc",
        "title": "审批单据",
        "kind": "update",
        "anchor_step_id": approve.step_id,
        "request_refs": [{"step_id": approve.step_id, "usage": "execute"}],
    }])
    cap = next(item for item in spec.capabilities if item.name == "approve_doc")
    paths = [str(ref.path or "") for ref in cap.request_refs]
    assert any(path.endswith("/doc/update-status") for path in paths)
    assert not any("dict-data" in path or "simple-list" in path for path in paths)
    assert not any(ref.usage == "option_source" for ref in cap.request_refs)


def test_list_filter_keeps_its_own_enum_and_does_not_rename_edit_fields() -> None:
    spec = _edit_and_command_spec()
    filter_status = _param(_step_by_suffix(spec, "/doc/page"), "query.status")
    assert filter_status.source_kind == "api_option"
    assert _param_exposed_to_caller(filter_status)
    party = _param(_step_by_suffix(spec, "/doc/update"), "partyId")
    assert party.label in {"往来单位", "partyId"}
    assert party.path.endswith("partyId")


def test_same_leaf_and_type_from_different_actions_do_not_share_one_input() -> None:
    def caller_param(path: str, action_id: str) -> ParamField:
        return ParamField(
            path=path,
            key="token",
            label="Token",
            value="x",
            type="string",
            wire_type="string",
            required=False,
            category="user_param",
            source_kind="user_input",
            source={"kind": "control_default", "required_state": "unknown"},
            exposed_to_user=True,
            editable=True,
            evidence=[{
                "kind": "page_control",
                "binding_status": "bound",
                "control_kind": "text",
                "action_id": action_id,
            }],
        )

    lookup = FlowStep(
        step_id="step_lookup",
        method="GET",
        url="http://contract.invalid/v4/records?token=x",
        path="/v4/records",
        params=[caller_param("query.token", "action_lookup")],
        source_meta={"request_id": "req_lookup", "role": "business_get"},
    )
    update = FlowStep(
        step_id="step_update",
        method="PUT",
        url="http://contract.invalid/v4/records/current",
        path="/v4/records/current",
        params=[caller_param("token", "action_update")],
        source_meta={"request_id": "req_update", "role": "business_write"},
    )
    spec = FlowSpec(
        tenant="tenant",
        subsystem="subsystem",
        steps=[lookup, update],
        links=[FlowLink(
            source_step_id="step_lookup",
            source_path="data.unrelated",
            target_step_id="step_update",
            target_path="unrelated",
            confirmed=True,
            meta={"captured_structure_match": True},
        )],
    )
    compiled = _compile(spec, [{
        "name": "update_record",
        "title": "Update record",
        "kind": "update",
        "anchor_step_id": "step_update",
    }])
    properties = compiled.capabilities[0].input_schema["properties"]
    assert len(properties) == 2
    assert all("#" not in name for name in properties)
    assert all("x-flow-paths" not in schema for schema in properties.values())


def test_same_field_identity_can_share_one_input_across_member_steps() -> None:
    def shared_param(path: str) -> ParamField:
        return ParamField(
            path=path,
            key="recordCode",
            label="Record code",
            value="A-1",
            type="string",
            wire_type="string",
            required=False,
            category="user_param",
            source_kind="user_input",
            source={"kind": "user_input", "required_state": "unknown"},
            exposed_to_user=True,
            editable=True,
            evidence=[{
                "kind": "page_control",
                "binding_status": "bound",
                "field_identity_id": "field_shared_record_code",
                "occurrence_id": "occurrence_shared_record_code",
            }],
        )

    prepare = FlowStep(
        step_id="step_prepare",
        method="POST",
        url="http://contract.invalid/v4/prepare",
        path="/v4/prepare",
        params=[shared_param("recordCode")],
        source_meta={"request_id": "req_prepare", "role": "business_write"},
    )
    commit = FlowStep(
        step_id="step_commit",
        method="PUT",
        url="http://contract.invalid/v4/commit",
        path="/v4/commit",
        params=[shared_param("recordCode")],
        source_meta={"request_id": "req_commit", "role": "business_write"},
    )
    spec = FlowSpec(
        tenant="tenant",
        subsystem="subsystem",
        steps=[prepare, commit],
        links=[FlowLink(
            source_step_id="step_prepare",
            source_path="response.ticket",
            target_step_id="step_commit",
            target_path="ticket",
            confirmed=True,
        )],
    )
    compiled = _compile(spec, [{
        "name": "commit_record",
        "title": "Commit record",
        "kind": "update",
        "anchor_step_id": "step_commit",
    }])
    properties = compiled.capabilities[0].input_schema["properties"]
    assert list(properties) == ["recordCode"]
    assert [param.key for step in compiled.steps for param in step.params] == [
        "recordCode",
        "recordCode",
    ]


def test_write_query_command_keeps_status_constant() -> None:
    spec = to_flow_spec(
        captured_requests=[
            _req(
                "req_dict", method="GET",
                url="http://example.test/admin-api/system/dict-data/simple-list?dictType=doc_status",
                sequence=1, role="read_option",
                response={"code": 0, "data": [{"label": "待审", "value": 10}, {"label": "已审", "value": 20}]},
            ),
            _req(
                "req_list", method="GET",
                url="http://example.test/admin-api/doc/page?pageNo=1&pageSize=10&status=10",
                sequence=2, role="business_get", action="act_search", locator="text=搜索",
                response={"code": 0, "data": {"list": [{"id": 36, "status": 10}]}},
            ),
            _req(
                "req_approve", method="PUT",
                url="http://example.test/admin-api/doc/update-status?id=36&status=20",
                sequence=3, role="business_write", action="act_approve", locator="text=审批",
                response={"code": 0, "data": True},
            ),
        ],
        field_evidence=[
            _control(
                label="状态", aliases=["status"], kind="select", value="待审",
                request_id="req_list", path="query.status", in_dialog=False,
                source_url="http://example.test/admin-api/system/dict-data/simple-list?dictType=doc_status",
            ),
        ],
        page_enum_options=_page_enum(
            label="状态",
            aliases=["status", "状态"],
            selected="待审",
            selected_value=10,
            options=[{"label": "待审", "value": 10}, {"label": "已审", "value": 20}],
            source_url="http://example.test/admin-api/system/dict-data/simple-list?dictType=doc_status",
        ),
        page_events=[{"event_id": "ev_approve", "kind": "click", "action_id": "act_approve"}],
        page_context=PAGE,
    )
    approve = _step_by_suffix(spec, "/doc/update-status")
    record_id = _param(approve, "query.id")
    status = _param(approve, "query.status")
    assert (record_id.source or {}).get("kind") == "selected_record_identity"
    assert status.source_kind == "constant", (status.source_kind, status.reason)
    assert not _param_exposed_to_caller(status)


def test_line_item_identity_product_is_computed() -> None:
    spec = to_flow_spec(
        captured_requests=[
            _req(
                "req_get", method="GET",
                url="http://example.test/admin-api/doc/get?id=36",
                sequence=1, role="business_get", action="act_edit", locator="text=编辑",
                response={"code": 0, "data": {
                    "id": 36,
                    "partyId": 5,
                    "remark": "x",
                    "items": [{"id": 39, "productId": 4, "count": 1, "productPrice": 5000, "totalPrice": 5000}],
                }},
            ),
            _req(
                "req_update", method="PUT",
                url="http://example.test/admin-api/doc/update",
                sequence=2, role="business_write", action="act_edit", locator="text=确定",
                body={
                    "id": 36,
                    "partyId": 5,
                    "remark": "x",
                    "items": [{"id": 39, "productId": 4, "count": 1, "productPrice": 5000, "totalPrice": 5000}],
                },
            ),
        ],
        page_events=[{"event_id": "ev_edit", "kind": "click", "action_id": "act_edit"}],
        page_context=PAGE,
    )
    update = _step_by_suffix(spec, "/doc/update")
    line_total = _param(update, "items[0].totalPrice")
    assert line_total.source_kind == "computed", (line_total.source_kind, line_total.reason)
    party = _param(update, "partyId")
    assert _param_exposed_to_caller(party)
    assert party.source_kind == "previous_response"


def test_edit_without_dialog_snapshot_still_exposes_hydrated_form_fields() -> None:
    spec = _edit_and_command_spec(include_dialog_controls=False)
    update = _step_by_suffix(spec, "/doc/update")
    for item in (_param(update, "partyId"), _param(update, "note")):
        assert _param_exposed_to_caller(item), (item.path, item.source_kind, item.reason)
        assert item.source_kind == "previous_response"
        assert bool((item.source or {}).get("allow_caller_override"))
    total = _param(update, "lineTotal")
    assert total.source_kind == "computed"
    assert not _param_exposed_to_caller(total)


def test_formatted_dialog_number_binds_to_write_field() -> None:
    spec = to_flow_spec(
        captured_requests=[
            _req(
                "req_get", method="GET",
                url="http://example.test/admin-api/doc/get?id=36",
                sequence=1, role="business_get", action="act_edit", locator="text=编辑",
                response={"code": 0, "data": {"id": 36, "partyId": 5, "discountPercent": 0, "note": "x"}},
            ),
            _req(
                "req_update", method="PUT",
                url="http://example.test/admin-api/doc/update",
                sequence=2, role="business_write", action="act_save", locator="text=确定",
                body={"id": 36, "partyId": 5, "discountPercent": 1110, "note": "x"},
            ),
        ],
        field_evidence=[{
            "label": "优惠率（%）",
            "field": "优惠率（%）",
            "value": "1110.00",
            "field_aliases": [],
            "control_kind": "number",
            "required": False,
            "op": "fill",
            "in_dialog": True,
            "surface": "dialog",
            "page_id": "page_1",
            "frame_id": "frame_1",
            "page_context": PAGE,
            "action_id": "act_fill",
            "observed_at": 1000,
        }],
        page_events=[
            {"event_id": "ev_edit", "kind": "click", "action_id": "act_edit"},
            {"event_id": "ev_save", "kind": "click", "action_id": "act_save"},
        ],
        page_context=PAGE,
    )
    discount = _param(_step_by_suffix(spec, "/doc/update"), "discountPercent")
    assert _param_exposed_to_caller(discount)
    assert discount.label == "优惠率（%）"
    assert discount.type == "number"
    assert discount.source_kind in {"user_input", "previous_response"}


def test_changed_hydrated_field_stays_upstream_default_and_caller_owned() -> None:
    spec = to_flow_spec(
        captured_requests=[
            _req(
                "req_get", method="GET",
                url="http://example.test/admin-api/doc/get?id=36",
                sequence=1, role="business_get", action="act_edit", locator="text=编辑",
                response={"code": 0, "data": {
                    "id": 36, "partyId": 5, "qty": 2, "unitPrice": 6000, "lineTotal": 12000, "note": "keep",
                }},
            ),
            _req(
                "req_update", method="PUT",
                url="http://example.test/admin-api/doc/update",
                sequence=2, role="business_write", action="act_edit", locator="text=确定",
                body={"id": 36, "partyId": 5, "qty": 3, "unitPrice": 6000, "lineTotal": 18000, "note": "keep"},
            ),
        ],
        field_evidence=[
            _control(
                label="数量", aliases=["qty"], kind="number", value="3",
                request_id="req_update", path="body.qty", in_dialog=True, op="fill",
            ),
        ],
        page_events=[{"event_id": "ev_edit", "kind": "click", "action_id": "act_edit"}],
        page_context=PAGE,
    )
    update = _step_by_suffix(spec, "/doc/update")
    qty = _param(update, "qty")
    assert qty.source_kind == "previous_response"
    assert bool((qty.source or {}).get("allow_caller_override"))
    assert _param_exposed_to_caller(qty)
    record_id = _param(update, "id")
    assert not _param_exposed_to_caller(record_id)


def test_header_discount_formula_uses_percent_not_creator() -> None:
    spec = to_flow_spec(
        captured_requests=[
            _req(
                "req_get", method="GET",
                url="http://example.test/admin-api/doc/get?id=36",
                sequence=1, role="business_get", action="act_edit", locator="text=编辑",
                response={"code": 0, "data": {
                    "id": 36,
                    "creator": 1,
                    "totalProductPrice": 5000,
                    "discountPercent": 1110,
                    "discountPrice": 55500,
                    "totalPrice": -50500,
                }},
            ),
            _req(
                "req_update", method="PUT",
                url="http://example.test/admin-api/doc/update",
                sequence=2, role="business_write", action="act_edit", locator="text=确定",
                body={
                    "id": 36,
                    "creator": 1,
                    "totalProductPrice": 5000,
                    "discountPercent": 1110,
                    "discountPrice": 55500,
                    "totalPrice": -50500,
                },
            ),
        ],
        page_events=[{"event_id": "ev_edit", "kind": "click", "action_id": "act_edit"}],
        page_context=PAGE,
    )
    update = _step_by_suffix(spec, "/doc/update")
    creator = _param(update, "creator")
    assert creator.source_kind != "computed"
    assert not _param_exposed_to_caller(creator)
    discount_price = _param(update, "discountPrice")
    total_price = _param(update, "totalPrice")
    assert discount_price.source_kind == "computed", (discount_price.source_kind, discount_price.reason)
    assert total_price.source_kind == "computed", (total_price.source_kind, total_price.reason)
    assert "creator" not in (discount_price.reason or "")
    assert "creator" not in (total_price.reason or "")


def test_list_status_evidence_does_not_bind_to_write_query() -> None:
    from dano.execution.page.recording_field_evidence import bind_field_evidence

    bound = bind_field_evidence(
        [
            _req(
                "req_list", method="GET",
                url="http://example.test/admin-api/doc/page?pageNo=1&status=10",
                sequence=1, role="business_get", action="act_search",
            ),
            _req(
                "req_approve", method="PUT",
                url="http://example.test/admin-api/doc/update-status?id=36&status=20",
                sequence=2, role="business_write", action="act_approve",
            ),
        ],
        [{"event_id": "ev_status", "kind": "change", "action_id": "act_search"}],
        [{
            "label": "状态",
            "field": "状态",
            "field_aliases": ["status"],
            "control_kind": "select",
            "value": "",
            "op": "fill",
            "in_dialog": False,
            "surface": "page",
            "page_id": "page_1",
            "frame_id": "frame_1",
            "action_id": "act_search",
            "page_context": PAGE,
        }],
    )
    assert len(bound) == 1
    assert bound[0]["binding_status"] == "bound"
    assert bound[0]["request_id"] == "req_list"
    assert bound[0]["wire_path"] == "query.status"


def test_date_range_start_binds_to_first_query_index() -> None:
    spec = to_flow_spec(
        captured_requests=[
            _req(
                "req_list", method="GET",
                url=(
                    "http://example.test/admin-api/doc/page?pageNo=1&pageSize=10"
                    "&orderTime%5B0%5D=2026-08-07%2000%3A00%3A00"
                    "&orderTime%5B1%5D=2026-08-07%2023%3A59%3A59"
                ),
                sequence=1, role="business_get", action="act_search",
            ),
        ],
        field_evidence=[{
            "label": "开始日期",
            "field": "开始日期",
            "value": "2026-08-07",
            "field_aliases": [],
            "control_kind": "date",
            "required": False,
            "op": "fill",
            "in_dialog": False,
            "surface": "page",
            "page_id": "page_1",
            "frame_id": "frame_1",
            "action_id": "act_search",
            "observed_at": 1000,
            "page_context": PAGE,
        }],
        page_events=[{"event_id": "ev_search", "kind": "click", "action_id": "act_search", "observed_at": 1100}],
        page_context=PAGE,
    )
    listing = _step_by_suffix(spec, "/doc/page")
    start = _param(listing, "query.orderTime[0]")
    end = _param(listing, "query.orderTime[1]")
    assert start.label == "开始日期"
    assert start.type == "date"
    assert _param_exposed_to_caller(start)
    assert start.source_kind != "unknown"
    assert end.source_kind in {"page_rule", "user_input"}
    if end.source_kind == "page_rule":
        assert not _param_exposed_to_caller(end)


def test_dialog_date_binds_to_business_time_not_audit_stamp() -> None:
    spec = to_flow_spec(
        captured_requests=[
            _req(
                "req_get", method="GET",
                url="http://example.test/admin-api/doc/get?id=36",
                sequence=1, role="business_get", action="act_edit", locator="text=编辑",
                response={"code": 0, "data": {
                    "id": 36, "orderTime": 1786896000000, "createTime": 1786933682000, "remark": "x",
                }},
            ),
            _req(
                "req_update", method="PUT",
                url="http://example.test/admin-api/doc/update",
                sequence=2, role="business_write", action="act_save", locator="text=确定",
                body={"id": 36, "orderTime": 1786896000000, "createTime": 1786933682000, "remark": "x"},
            ),
        ],
        field_evidence=[{
            "label": "订单时间",
            "field": "订单时间",
            "value": "2026-08-17",
            "field_aliases": [],
            "control_kind": "date",
            "required": True,
            "op": "snapshot",
            "in_dialog": True,
            "surface": "dialog",
            "page_id": "page_1",
            "frame_id": "frame_1",
            "page_context": PAGE,
        }],
        page_events=[{"event_id": "ev_edit", "kind": "click", "action_id": "act_edit"}],
        page_context=PAGE,
    )
    update = _step_by_suffix(spec, "/doc/update")
    order_time = _param(update, "orderTime")
    create_time = _param(update, "createTime")
    assert order_time.label == "订单时间"
    assert order_time.type == "date"
    assert order_time.required is True
    assert _param_exposed_to_caller(order_time)
    assert order_time.source_kind == "previous_response"
    assert not _param_exposed_to_caller(create_time)


def test_hydrated_select_keeps_previous_response_when_option_api_is_attached() -> None:
    spec = to_flow_spec(
        captured_requests=[
            _req(
                "req_options", method="GET",
                url="http://example.test/admin-api/erp/customer/simple-list",
                sequence=1, role="read_option",
                response={"code": 0, "data": [{"id": 8, "name": "鲜生"}, {"id": 9, "name": "12"}]},
            ),
            _req(
                "req_get", method="GET",
                url="http://example.test/admin-api/doc/get?id=36",
                sequence=2, role="business_get", action="act_edit", locator="text=编辑",
                response={"code": 0, "data": {"id": 36, "customerId": 8, "remark": "x"}},
            ),
            _req(
                "req_update", method="PUT",
                url="http://example.test/admin-api/doc/update",
                sequence=3, role="business_write", action="act_save", locator="text=确定",
                body={"id": 36, "customerId": 8, "remark": "x"},
            ),
        ],
        field_evidence=[
            _control(
                label="客户", aliases=["customerId"], kind="select", value="鲜生",
                request_id="req_update", path="body.customerId", in_dialog=True,
                source_url="http://example.test/admin-api/erp/customer/simple-list",
            ),
        ],
        reads=[{
            "request_id": "req_options",
            "method": "GET",
            "url": "http://example.test/admin-api/erp/customer/simple-list",
            "json": [{"id": 8, "name": "鲜生"}, {"id": 9, "name": "12"}],
            "role": "explicit_read_option",
            "page_id": "page_1",
            "frame_id": "frame_1",
            "page_context": PAGE,
        }],
        page_events=[{"event_id": "ev_edit", "kind": "click", "action_id": "act_edit"}],
        page_context=PAGE,
    )
    customer = _param(_step_by_suffix(spec, "/doc/update"), "customerId")
    assert customer.source_kind == "previous_response", (customer.source_kind, customer.source, customer.reason)
    assert bool((customer.source or {}).get("allow_caller_override"))
    assert (customer.source or {}).get("option_source")
    assert _param_exposed_to_caller(customer)


def test_null_detail_and_zero_write_still_hydrate_line_tax() -> None:
    spec = to_flow_spec(
        captured_requests=[
            _req(
                "req_get", method="GET",
                url="http://example.test/admin-api/doc/get?id=36",
                sequence=1, role="business_get", action="act_edit", locator="text=编辑",
                response={"code": 0, "data": {
                    "id": 36,
                    "partyId": 5,
                    "remark": "x",
                    "items": [{"id": 39, "count": 1, "productPrice": 5000, "taxPrice": None, "totalPrice": 5000}],
                }},
            ),
            _req(
                "req_update", method="PUT",
                url="http://example.test/admin-api/doc/update",
                sequence=2, role="business_write", action="act_edit", locator="text=确定",
                body={
                    "id": 36,
                    "partyId": 5,
                    "remark": "x",
                    "items": [{"id": 39, "count": 1, "productPrice": 5000, "taxPrice": 0, "totalPrice": 5000}],
                },
            ),
        ],
        page_events=[{"event_id": "ev_edit", "kind": "click", "action_id": "act_edit"}],
        page_context=PAGE,
    )
    tax = _param(_step_by_suffix(spec, "/doc/update"), "items[0].taxPrice")
    assert tax.source_kind == "previous_response", (tax.source_kind, tax.reason)
    assert tax.source_kind != "unknown"


def test_computed_arithmetic_formula_is_a_complete_contract() -> None:
    from dano.execution.page.flow_spec import ParamField
    from dano.execution.page.flow_materialization.field_contracts.common import _field_source_configuration_advice

    param = ParamField(
        key="lineTotal",
        path="body.items[0].lineTotal",
        source_kind="computed",
        source={"strategy": "product", "left_field": "qty", "right_field": "unitPrice"},
    )
    assert _field_source_configuration_advice(param) is None


def test_dialog_control_binds_when_spa_routes_differ() -> None:
    from dano.execution.page.recording_field_evidence import bind_field_evidence

    write = _req(
        "req_update", method="PUT",
        url="http://example.test/admin-api/doc/update",
        sequence=2, role="business_write", action="act_edit",
        body={"remark": "keep"},
    )
    write["page_context"] = {**PAGE, "path": "/app/docs/edit"}
    write["timestamp"] = 1_000.0
    evidence = _control(
        label="备注", aliases=["remark"], kind="textarea", value="keep",
        request_id="req_update", path="body.remark", in_dialog=True, op="fill",
    )
    evidence["observed_at"] = 999.0
    evidence["page_context"] = PAGE
    bound = bind_field_evidence(
        [
            _req(
                "req_list", method="GET",
                url="http://example.test/admin-api/doc/page?pageNo=1",
                sequence=1, role="business_get",
            ),
            write,
        ],
        [],
        [evidence],
    )
    remark = next(item for item in bound if item.get("label") == "备注")
    assert remark.get("binding_status") == "bound"
    assert remark.get("request_id") == "req_update"


def test_debounced_fill_after_request_still_binds() -> None:
    from dano.execution.page.recording_field_evidence import bind_field_evidence

    write = _req(
        "req_update", method="PUT",
        url="http://example.test/admin-api/doc/update",
        sequence=1, role="business_write", action="act_edit",
        body={"remark": "keep"},
    )
    write["timestamp"] = 1_000.0
    evidence = _control(
        label="备注", aliases=["remark"], kind="textarea", value="keep",
        request_id="req_update", path="body.remark", in_dialog=True, op="fill",
    )
    evidence["observed_at"] = 1_001.2
    evidence["action_id"] = "act_edit"
    bound = bind_field_evidence([write], [], [evidence])
    remark = next(item for item in bound if item.get("label") == "备注")
    assert remark.get("binding_status") == "bound"


def test_hydration_detail_id_stays_required_caller_selected_record() -> None:
    spec = _edit_and_command_spec()
    detail = _step_by_suffix(spec, "/doc/get")
    record_id = _param(detail, "query.id")
    assert (record_id.source or {}).get("kind") == "selected_record_identity"
    assert record_id.required is True
    assert _param_exposed_to_caller(record_id)


def test_edit_capability_uses_one_caller_record_identity() -> None:
    spec = _edit_and_command_spec()
    update = _step_by_suffix(spec, "/doc/update")
    compiled = _compile(spec, [{
        "name": "edit_record",
        "title": "Edit record",
        "kind": "update",
        "anchor_step_id": update.step_id,
    }])
    properties = compiled.capabilities[0].input_schema["properties"]
    identity_fields = {
        name: field
        for name, field in properties.items()
        if str(field.get("x-flow-path") or "").removeprefix("query.") == "id"
    }
    assert list(identity_fields) == ["id"]
    assert identity_fields["id"]["x-flow-path"] == "query.id"
    assert all(field.get("x-flow-path") != "query.status" for field in properties.values())
    assert not any(
        str(ref.path or "").endswith("/doc/page")
        for ref in compiled.capabilities[0].request_refs
    )


def _create_form_spec():
    catalog_url = "http://example.test/admin-api/item/simple-list"
    catalog = [
        {"id": 4, "name": "甲件", "barCode": "b1", "price": 5000, "stock": 9, "unitName": "件"},
        {"id": 7, "name": "乙件", "barCode": "b2", "price": 3000, "stock": 3, "unitName": "盒"},
    ]
    create_body = {
        "partyId": 5,
        "accountId": 2,
        "remark": "",
        "items": [{
            "productId": 4,
            "productName": "甲件",
            "productBarCode": "b1",
            "productPrice": 5000,
            "stockCount": 9,
            "productUnitName": "件",
            "count": 2,
            "totalPrice": 10000,
        }],
    }
    return to_flow_spec(
        captured_requests=[
            _req(
                "req_items", method="GET", url=catalog_url, sequence=1, role="read_option",
                response={"code": 0, "data": catalog},
            ),
            _req(
                "req_list", method="GET",
                url="http://example.test/admin-api/doc/page?pageNo=1&pageSize=10",
                sequence=2, role="business_get", action="act_search", locator="text=搜索",
                response={"code": 0, "data": {"list": [{"id": 1}]}},
            ),
            _req(
                "req_create", method="POST",
                url="http://example.test/admin-api/doc/create",
                sequence=3, role="business_write", action="act_create", locator="text=确定",
                body=create_body, response={"code": 0, "data": 88},
            ),
        ],
        reads=[{
            "request_id": "req_items",
            "method": "GET",
            "url": catalog_url,
            "json": catalog,
            "role": "explicit_read_option",
            "page_id": "page_1",
            "frame_id": "frame_1",
            "page_context": PAGE,
        }],
        field_evidence=[
            _control(
                label="往来单位", aliases=["partyId"], kind="select", value="甲公司",
                request_id="req_create", path="body.partyId", in_dialog=True, required=True,
            ),
            _control(
                label="结算账户", aliases=["accountId"], kind="select", value="现金",
                request_id="req_create", path="body.accountId", in_dialog=True, required=True,
            ),
            _control(
                label="数量", aliases=["count"], kind="number", value="2",
                request_id="req_create", path="body.items[0].count", in_dialog=True, required=True,
            ),
            _control(
                label="备注", aliases=["remark"], kind="textarea", value="",
                request_id="req_create", path="body.remark", in_dialog=True, required=False,
            ),
        ],
        page_events=[
            {"event_id": "ev_search", "kind": "click", "action_id": "act_search"},
            {"event_id": "ev_create", "kind": "click", "action_id": "act_create"},
        ],
        page_context=PAGE,
        samples={"往来单位": "甲公司", "结算账户": "现金", "数量": "2"},
    )


def test_create_form_projects_option_row_and_computes_line_total() -> None:
    spec = _create_form_spec()
    create = _step_by_suffix(spec, "/doc/create")
    party = _param(create, "partyId")
    assert _param_exposed_to_caller(party)
    assert party.required is True
    remark = _param(create, "remark")
    assert _param_exposed_to_caller(remark)
    assert remark.required is False
    count = _param(create, "items[0].count")
    assert _param_exposed_to_caller(count)
    assert count.required is True
    assert count.source_kind != "selected_option_field"
    for path, catalog_leaf in (
        ("items[0].productName", "name"),
        ("items[0].productBarCode", "barCode"),
        ("items[0].productPrice", "price"),
        ("items[0].stockCount", "stock"),
        ("items[0].productUnitName", "unitName"),
    ):
        item = _param(create, path)
        assert item.source_kind == "selected_option_field", (path, item.source_kind, item.reason)
        assert _param_exposed_to_caller(item), (path, item.exposed_to_user, item.reason)
        assert bool((item.source or {}).get("allow_caller_override"))
        assert (item.source or {}).get("response_path") == catalog_leaf
        assert item.source_kind != "unknown"
        assert item.source_kind != "computed"
    total = _param(create, "items[0].totalPrice")
    assert total.source_kind == "computed", (total.source_kind, total.reason)
    assert not _param_exposed_to_caller(total)
    chooser = _param(create, "items[0].productId")
    assert chooser.source_kind != "unknown"
    assert _param_exposed_to_caller(chooser) or chooser.source_kind in {"form_option", "user_input", "api_option"}


def test_create_capability_keeps_repeating_rows_as_array_objects() -> None:
    import asyncio

    from dano.execution.page.flow_spec_core.request_contract import flow_spec_to_api_request
    from dano.execution.page.request_capture import execute_api_request

    spec = _create_form_spec()
    create = _step_by_suffix(spec, "/doc/create")
    compiled = _compile(spec, [{
        "name": "create_record",
        "title": "Create record",
        "kind": "create",
        "anchor_step_id": create.step_id,
    }])
    properties = compiled.capabilities[0].input_schema["properties"]
    assert "items" in properties
    array_param = _param(_step_by_suffix(compiled, "/doc/create"), "items")
    assert (array_param.source or {}).get("kind") == "dynamic_structure_input"
    assert (array_param.source or {}).get("structure_kind") == "array_object"
    assert properties["items"]["type"] == "array"
    item_properties = properties["items"]["items"]["properties"]
    assert {"productId", "count"}.issubset(item_properties)
    assert not {"productId", "count"}.intersection(set(properties) - {"items"})

    api_request, errors = flow_spec_to_api_request(compiled, _prepared=True)
    assert errors == []
    assert api_request is not None
    workflow_steps = api_request.get("steps") or [api_request]
    create_request = next(step for step in workflow_steps if str(step.get("path") or "").endswith("/doc/create"))
    assert "items" in create_request["params"]
    assert create_request["body_template"]["items"] == "{{items}}"
    dry = asyncio.run(execute_api_request(
        create_request,
        {
            "partyId": 5,
            "accountId": 2,
            "remark": "two rows",
            "items": [
                {"productId": 4, "productPrice": 10, "count": 2},
                {"productId": 7, "productPrice": 3, "count": 4},
            ],
        },
        send=False,
    ))
    assert dry["ok"] is True, dry
    assert len(dry["body"]["items"]) == 2
    assert dry["body"]["items"][0]["totalPrice"] == 20
    assert dry["body"]["items"][1]["totalPrice"] == 12


def test_dynamic_array_schema_identity_does_not_include_recorded_row_indexes() -> None:
    spec = to_flow_spec(
        captured_requests=[
            _req(
                "req_repeat",
                method="POST",
                url="http://example.test/v3/records",
                sequence=1,
                role="business_write",
                action="act_repeat",
                locator="text=Submit",
                body={
                    "rows": [
                        {"code": "A", "amount": 2},
                        {"code": "B", "amount": 4},
                    ],
                },
            ),
        ],
        field_evidence=[
            _control(
                label="Code",
                aliases=["code"],
                kind="text",
                value="A",
                request_id="req_repeat",
                path="body.rows[0].code",
                in_dialog=True,
                required=True,
            ),
            _control(
                label="Amount",
                aliases=["amount"],
                kind="number",
                value=2,
                request_id="req_repeat",
                path="body.rows[0].amount",
                in_dialog=True,
                required=True,
            ),
            _control(
                label="Code",
                aliases=["code"],
                kind="text",
                value="B",
                request_id="req_repeat",
                path="body.rows[1].code",
                in_dialog=True,
                required=True,
            ),
            _control(
                label="Amount",
                aliases=["amount"],
                kind="number",
                value=4,
                request_id="req_repeat",
                path="body.rows[1].amount",
                in_dialog=True,
                required=True,
            ),
        ],
        page_events=[{"event_id": "ev_repeat", "kind": "click", "action_id": "act_repeat"}],
        page_context=PAGE,
    )
    create = _step_by_suffix(spec, "/v3/records")
    compiled = _compile(spec, [{
        "name": "submit_rows",
        "title": "Submit rows",
        "kind": "create",
        "anchor_step_id": create.step_id,
    }])
    item_properties = compiled.capabilities[0].input_schema["properties"]["rows"]["items"]["properties"]
    assert set(item_properties) == {"code", "amount"}
    assert all("[" not in key and "#" not in key for key in item_properties)
    row_leaves = [
        param for param in _step_by_suffix(compiled, "/v3/records").params
        if (param.source or {}).get("array_item_public") is True
    ]
    assert {(param.source or {}).get("schema_identity_path") for param in row_leaves} == {
        "rows[].code",
        "rows[].amount",
    }
    assert {param.path for param in row_leaves} == {
        "rows[0].code",
        "rows[0].amount",
        "rows[1].code",
        "rows[1].amount",
    }


def test_dynamic_array_members_exposed_later_stay_inside_item_schema() -> None:
    spec = to_flow_spec(
        captured_requests=[
            _req(
                "req_late_row",
                method="POST",
                url="http://example.test/v5/rows",
                sequence=1,
                role="business_write",
                action="act_late_row",
                locator="text=Submit",
                body={"entries": [{"code": "A", "comment": "recorded"}]},
            ),
        ],
        field_evidence=[
            _control(
                label="Code",
                aliases=["code"],
                kind="text",
                value="A",
                request_id="req_late_row",
                path="body.entries[0].code",
                in_dialog=True,
                required=True,
            ),
        ],
        page_events=[{"event_id": "ev_late", "kind": "click", "action_id": "act_late_row"}],
        page_context=PAGE,
    )
    create = _step_by_suffix(spec, "/v5/rows")
    comment = _param(create, "entries[0].comment")
    assert (comment.source or {}).get("array_item_member") is True
    assert (comment.source or {}).get("array_item_public") is False
    comment.category = "user_param"
    comment.source_kind = "user_input"
    comment.source = {**(comment.source or {}), "kind": "user_input"}
    comment.exposed_to_user = True
    comment.editable = True
    comment.locked = True

    compiled = _compile(spec, [{
        "name": "submit_entries",
        "title": "Submit entries",
        "kind": "create",
        "anchor_step_id": create.step_id,
    }])
    properties = compiled.capabilities[0].input_schema["properties"]
    assert "comment" not in properties
    assert "comment" in properties["entries"]["items"]["properties"]


def test_repeating_option_rows_project_each_selected_record(monkeypatch) -> None:
    import asyncio

    from dano.execution.page import request_capture

    async def fake_fetch(*_args, **_kwargs):
        return [
            {"id": 4, "name": "Alpha", "price": 10},
            {"id": 7, "name": "Beta", "price": 3},
        ]

    monkeypatch.setattr(request_capture, "_fetch_select_list", fake_fetch)
    api_request = {
        "auth_headers": {},
        "selects": [{
            "param": "items",
            "path": "items",
            "multi": True,
            "source_url": "http://options.invalid/v1/catalog",
            "value_key": "id",
            "label_key": "name",
            "label_subkey": "productId",
            "element_template": {
                "productId": {"item_key": "id"},
                "productPrice": {"item_key": "price"},
            },
        }],
        "runtime_fields": [{
            "name": "__row_total",
            "kind": "array_item_formula",
            "strategy": "product",
            "container_field": "items",
            "left_field": "productPrice",
            "right_field": "count",
            "result_field": "totalPrice",
        }],
    }
    fields = asyncio.run(request_capture._resolve_list_selects(
        api_request,
        {"items": [
            {"productId": "Alpha", "count": 2},
            {"productId": "Beta", "count": 4, "productPrice": 5},
        ]},
        base_url="",
        storage_state=None,
        token_key=None,
        verify=True,
    ))
    fields = request_capture._apply_runtime_fields(fields, api_request)
    assert fields["items"] == [
        {"productId": 4, "count": 2, "productPrice": 10, "totalPrice": 20},
        {"productId": 7, "count": 4, "productPrice": 5, "totalPrice": 20},
    ]


def test_create_form_unbound_manual_fields_are_caller_not_unknown() -> None:
    spec = to_flow_spec(
        captured_requests=[
            _req(
                "req_create", method="POST",
                url="http://example.test/admin-api/doc/create",
                sequence=1, role="business_write", action="act_create", locator="text=确定",
                body={
                    "partyId": 5,
                    "saleUserId": 3,
                    "remark": "",
                    "discountPercent": 10,
                    "depositPrice": 100,
                    "items": [{"productId": 4, "count": 2, "productPrice": 5000, "totalPrice": 10000}],
                },
            ),
        ],
        field_evidence=[
            _control(
                label="往来单位", aliases=["partyId"], kind="select", value="甲公司",
                request_id="req_create", path="body.partyId", in_dialog=True, required=True,
            ),
        ],
        page_events=[{"event_id": "ev_create", "kind": "click", "action_id": "act_create"}],
        page_context=PAGE,
        samples={"往来单位": "甲公司"},
    )
    create = _step_by_suffix(spec, "/doc/create")
    party = _param(create, "partyId")
    assert _param_exposed_to_caller(party)
    assert party.source_kind != "unknown"
    remark = _param(create, "remark")
    assert remark.source_kind in {"unknown", ""}
    assert not _param_exposed_to_caller(remark)
    for path in ("items[0].count", "saleUserId", "discountPercent", "depositPrice"):
        item = _param(create, path)
        assert item.source_kind in {"unknown", ""}, (path, item.source_kind, item.reason)
        assert not _param_exposed_to_caller(item)
    total = _param(create, "items[0].totalPrice")
    assert total.source_kind == "computed"
    assert not _param_exposed_to_caller(total)


def test_option_row_projection_keeps_best_catalog_when_another_list_also_matches() -> None:
    spec = to_flow_spec(
        captured_requests=[
            _req(
                "req_decoy", method="GET",
                url="http://example.test/admin-api/misc/simple-list",
                sequence=1, role="read_option",
                response={"code": 0, "data": [
                    {"barCode": "b1", "unitName": "件"},
                    {"barCode": "xx", "unitName": "箱"},
                ]},
            ),
            _req(
                "req_items", method="GET",
                url="http://example.test/admin-api/item/simple-list",
                sequence=2, role="read_option",
                response={"code": 0, "data": [
                    {"id": 4, "name": "甲件", "barCode": "b1", "price": 5000, "stock": 9, "unitName": "件"},
                    {"id": 7, "name": "乙件", "barCode": "b2", "price": 3000, "stock": 3, "unitName": "盒"},
                ]},
            ),
            _req(
                "req_create", method="POST",
                url="http://example.test/admin-api/doc/create",
                sequence=3, role="business_write", action="act_create", locator="text=确定",
                body={
                    "partyId": 5,
                    "items": [{
                        "productId": 4,
                        "productName": "甲件",
                        "productBarCode": "b1",
                        "productPrice": 5000,
                        "stockCount": 9,
                        "productUnitName": "件",
                        "count": 2,
                        "totalPrice": 10000,
                    }],
                },
            ),
        ],
        field_evidence=[
            _control(
                label="往来单位", aliases=["partyId"], kind="select", value="甲公司",
                request_id="req_create", path="body.partyId", in_dialog=True, required=True,
            ),
        ],
        page_events=[{"event_id": "ev_create", "kind": "click", "action_id": "act_create"}],
        page_context=PAGE,
    )
    name = _param(_step_by_suffix(spec, "/doc/create"), "items[0].productName")
    assert name.source_kind == "selected_option_field"
    assert (name.source or {}).get("response_path") == "name"


def test_pagination_is_not_a_caller_input() -> None:
    spec = _create_form_spec()
    listing = _step_by_suffix(spec, "/doc/page")
    page_no = _param(listing, "query.pageNo")
    page_size = _param(listing, "query.pageSize")
    assert not _param_exposed_to_caller(page_no)
    assert not _param_exposed_to_caller(page_size)
    spec = _compile(spec, [{
        "name": "query_docs",
        "title": "查询单据",
        "kind": "query",
        "anchor_step_id": listing.step_id,
        "request_refs": [{"step_id": listing.step_id, "usage": "execute"}],
    }])
    cap = next(item for item in spec.capabilities if item.name == "query_docs")
    props = (cap.input_schema or {}).get("properties") or {}
    assert "pageNo" not in props
    assert "pageSize" not in props


def test_write_locator_with_inspect_text_stays_write_family() -> None:
    from dano.execution.page.capability_kinds import (
    _capability_operation_kind,
    _is_write_step,
)

    spec = to_flow_spec(
        captured_requests=[
            _req(
                "req_update", method="PUT",
                url="http://example.test/admin-api/doc/update",
                sequence=1, role="business_write", action="act_save",
                locator="text=详情",
                body={"id": 36, "remark": "keep"},
            ),
        ],
        page_events=[{"event_id": "ev_save", "kind": "click", "action_id": "act_save"}],
        page_context=PAGE,
    )
    step = _step_by_suffix(spec, "/doc/update")
    assert _is_write_step(step)
    assert _capability_operation_kind(step) not in {"inspect", "query_status", "preview"}


def test_unbound_list_filters_are_caller_not_unknown() -> None:
    spec = to_flow_spec(
        captured_requests=[
            _req(
                "req_list", method="GET",
                url=(
                    "http://example.test/admin-api/doc/page?pageNo=1&pageSize=10"
                    "&no=1&remark=1&customerId=8&productId=4&status=20"
                ),
                sequence=1, role="business_get", action="act_search", locator="text=搜索",
                response={"code": 0, "data": {"list": [{"id": 1}]}},
            ),
        ],
        field_evidence=[
            _control(
                label="状态", aliases=["status"], kind="select", value="已审",
                request_id="req_list", path="query.status", in_dialog=False,
            ),
        ],
        page_events=[{"event_id": "ev_search", "kind": "click", "action_id": "act_search"}],
        page_context=PAGE,
    )
    listing = _step_by_suffix(spec, "/doc/page")
    status = _param(listing, "query.status")
    assert _param_exposed_to_caller(status)
    assert status.source_kind != "unknown"
    for path in ("query.no", "query.remark", "query.customerId", "query.productId"):
        item = _param(listing, path)
        assert item.source_kind in {"unknown", ""} or not _param_exposed_to_caller(item), (
            path, item.source_kind, item.reason
        )
    assert not _param_exposed_to_caller(_param(listing, "query.pageNo"))
    spec = _compile(spec, [{
        "name": "search_docs",
        "title": "查询单据",
        "kind": "query",
        "anchor_step_id": listing.step_id,
        "request_refs": [{"step_id": listing.step_id, "usage": "execute"}],
    }])
    props = ((next(item for item in spec.capabilities if item.name == "search_docs").input_schema or {}).get("properties") or {})
    assert "status" in props
    assert "no" not in props
    assert "remark" not in props
    assert "pageNo" not in props


def test_option_source_query_leftover_stays_unknown() -> None:
    from dano.execution.page.flow_materialization.field_contracts.common import _param_source_guess

    guess = _param_source_guess(
        field={"key": "dictType", "value": "doc_status"},
        path="query.dictType",
        key="dictType",
        method="GET",
        identity_paths=set(),
        system_paths=set(),
        select_paths=set(),
        select_id_paths=set(),
        samples={},
        query_is_option_source=True,
        query_is_business_query=False,
    )
    assert guess["source_kind"] == "unknown"
    assert guess["exposed_to_user"] is False
    assert (guess["source"] or {}).get("kind") == "option_query_filter"


def test_readonly_option_row_echo_stays_system() -> None:
    spec = to_flow_spec(
        captured_requests=[
            _req(
                "req_items", method="GET",
                url="http://example.test/admin-api/item/simple-list",
                sequence=1, role="read_option",
                response={"code": 0, "data": [
                    {"id": 4, "name": "甲件", "barCode": "b1", "price": 5000},
                    {"id": 7, "name": "乙件", "barCode": "b2", "price": 3000},
                ]},
            ),
            _req(
                "req_create", method="POST",
                url="http://example.test/admin-api/doc/create",
                sequence=2, role="business_write", action="act_create", locator="text=确定",
                body={
                    "partyId": 5,
                    "items": [{
                        "productId": 4,
                        "productName": "甲件",
                        "productBarCode": "b1",
                        "productPrice": 5000,
                        "count": 2,
                        "totalPrice": 10000,
                    }],
                },
            ),
        ],
        field_evidence=[
            _control(
                label="产品名称", aliases=["productName"], kind="text", value="甲件",
                request_id="req_create", path="body.items[0].productName",
                in_dialog=True, read_only=True,
            ),
        ],
        page_events=[{"event_id": "ev_create", "kind": "click", "action_id": "act_create"}],
        page_context=PAGE,
    )
    name = _param(_step_by_suffix(spec, "/doc/create"), "items[0].productName")
    assert name.source_kind == "selected_option_field"
    assert not _param_exposed_to_caller(name)
    assert name.source_kind != "user_input"
    price = _param(_step_by_suffix(spec, "/doc/create"), "items[0].productPrice")
    assert price.source_kind == "selected_option_field"
    assert _param_exposed_to_caller(price)
    assert price.source_kind != "computed"


def test_detail_get_identity_is_not_a_search_filter() -> None:
    spec = to_flow_spec(
        captured_requests=[
            _req(
                "req_get", method="GET",
                url="http://example.test/admin-api/doc/get?id=32",
                sequence=1, role="business_get", action="act_view", locator="text=详情",
                response={"code": 0, "data": {"id": 32, "remark": "x"}},
            ),
        ],
        page_events=[{"event_id": "ev_view", "kind": "click", "action_id": "act_view"}],
        page_context=PAGE,
    )
    record_id = _param(_step_by_suffix(spec, "/doc/get"), "query.id")
    assert (record_id.source or {}).get("kind") == "selected_record_identity"
    assert _param_exposed_to_caller(record_id)
    assert record_id.source_kind != "unknown"
