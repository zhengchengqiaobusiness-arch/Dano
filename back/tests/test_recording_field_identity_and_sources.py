"""Stage 5: field identity, array binding, required tri-state, sources, long recordings."""

from __future__ import annotations

import json

from dano.execution.page.flow_spec import (
    FlowSpec,
    FlowStep,
    ParamField,
    to_flow_spec,
)
from dano.execution.page.flow_materialization.field_contracts.create_form import _apply_create_form_field_contracts
from dano.execution.page.flow_materialization.field_contracts.computed import _infer_arithmetic_computed_fields
from dano.execution.page.recorder import RecordSession, assign_step_field_keys
from dano.execution.page.recording_field_evidence import bind_field_evidence


PAGE = {"url": "http://example.test/app/form", "path": "/app/form", "document_title": "表单"}
ORDERS = {"url": "http://example.test/app/orders", "path": "/orders", "document_title": "订单"}
CUSTOMERS = {"url": "http://example.test/app/customers", "path": "/customers", "document_title": "客户"}


def _feed(session: RecordSession, payload: dict) -> None:
    payload = {"page_id": "page_1", "frame_id": "frame_1", **payload}
    session._on_record({}, json.dumps(payload, ensure_ascii=False))


def test_two_edit_transactions_keep_separate_occurrences() -> None:
    session = RecordSession()
    _feed(session, {
        "op": "fill", "locator": "css=textarea[name=remark]", "field": "备注",
        "value": "甲", "field_aliases": ["remark"], "control_kind": "textarea",
        "action_id": "action_edit_a", "page_context": PAGE,
    })
    _feed(session, {
        "op": "fill", "locator": "css=textarea[name=remark]", "field": "备注",
        "value": "乙", "field_aliases": ["remark"], "control_kind": "textarea",
        "action_id": "action_edit_b", "page_context": PAGE,
    })
    assert len(session.steps) == 2
    evidence = session.recorded_field_evidence()
    remark = [item for item in evidence if "remark" in (item.get("field_aliases") or [])]
    identities = {item.get("field_identity_id") for item in remark}
    occ = {item.get("evidence_id") for item in remark}
    assert len(identities) == 1
    assert len(occ) == 2
    values = {item.get("value") for item in remark}
    assert values == {"甲", "乙"}


def test_same_locator_different_routes_do_not_share_identity() -> None:
    session = RecordSession()
    _feed(session, {
        "op": "select", "locator": "css=select[name=status]", "field": "状态",
        "value": "open", "field_aliases": ["status"], "control_kind": "select",
        "action_id": "action_orders", "page_context": ORDERS,
        "options": [{"label": "打开", "value": "open"}],
    })
    _feed(session, {
        "op": "enum_snapshot", "locator": "css=select[name=status]", "field": "状态",
        "field_aliases": ["status"], "control_kind": "select",
        "action_id": "action_orders", "page_context": ORDERS,
        "options": [{"label": "打开", "value": "open"}, {"label": "关闭", "value": "closed"}],
    })
    _feed(session, {
        "op": "select", "locator": "css=select[name=status]", "field": "状态",
        "value": "vip", "field_aliases": ["status"], "control_kind": "select",
        "action_id": "action_customers", "page_context": CUSTOMERS,
        "options": [{"label": "会员", "value": "vip"}],
    })
    _feed(session, {
        "op": "enum_snapshot", "locator": "css=select[name=status]", "field": "状态",
        "field_aliases": ["status"], "control_kind": "select",
        "action_id": "action_customers", "page_context": CUSTOMERS,
        "options": [{"label": "会员", "value": "vip"}, {"label": "普通", "value": "normal"}],
    })
    keys = assign_step_field_keys(session.steps)
    assert len(set(keys.values())) == 2
    evidence = session.recorded_field_evidence()
    identities = {
        item.get("field_identity_id")
        for item in evidence
        if "status" in (item.get("field_aliases") or [])
    }
    assert len(identities) == 2
    enums = session.recorded_page_enum_options()
    labels = {
        str(option.get("label") if isinstance(option, dict) else option)
        for entry in enums.values()
        for option in (entry.get("options") or [])
    }
    assert "打开" in labels
    assert "会员" in labels
    mixed = any(
        {"打开", "会员"} <= {
            str(option.get("label") if isinstance(option, dict) else option)
            for option in (entry.get("options") or [])
        }
        for entry in enums.values()
    )
    assert mixed is False


def test_form_samples_do_not_splice_page_and_dialog_from_different_transactions() -> None:
    session = RecordSession()
    _feed(session, {
        "op": "form_snapshot", "action_id": "action_search",
        "fields": [{"field": "keyword", "label": "关键字", "value": "list", "in_dialog": False}],
        "page_context": PAGE,
    })
    _feed(session, {
        "op": "form_snapshot", "action_id": "action_edit",
        "fields": [{"field": "remark", "label": "备注", "value": "dialog", "in_dialog": True}],
        "page_context": PAGE,
    })
    mixed = session.recorded_form_samples()
    assert not ("关键字" in mixed and "备注" in mixed)
    by_tx = session.recorded_form_samples_by_transaction()
    unique_groups = {tuple(sorted((str(k), str(v)) for k, v in group.items())) for group in by_tx.values()}
    assert len(unique_groups) == 2
    values = [tuple(sorted(str(v) for v in group.values())) for group in by_tx.values()]
    assert ("list",) in values
    assert ("dialog",) in values


def test_array_rows_are_not_forced_onto_first_index() -> None:
    requests = [
        {
            "request_id": "req_save",
            "method": "POST",
            "url": "http://example.test/doc/save",
            "page_id": "page_1",
            "frame_id": "frame_1",
            "page_context": PAGE,
            "trigger_action_id": "action_save",
            "trigger_transaction_id": "page_1|frame_1|action_save",
            "post_data": json.dumps({
                "items": [
                    {"productId": "p1", "count": 1},
                    {"productId": "p2", "count": 9},
                ]
            }),
            "role": "business_write",
        }
    ]
    evidence = [
        {
            "label": "数量",
            "field": "count",
            "value": 9,
            "field_aliases": ["count"],
            "control_kind": "number",
            "page_id": "page_1",
            "frame_id": "frame_1",
            "page_context": PAGE,
            "action_id": "action_save",
            "transaction_id": "page_1|frame_1|action_save",
            "op": "fill",
        }
    ]
    bound = bind_field_evidence(requests, [], evidence)
    item = bound[0]
    assert item.get("wire_path") != "body.items[0].count"
    path = str(item.get("wire_path") or "")
    assert path in {"body.items[1].count", "body.items[].count"} or item.get("binding_status") == "ambiguous"


def test_unidentified_page_control_does_not_bind_to_dialog_body_by_value() -> None:
    """A value match cannot move evidence across the page/dialog boundary.

    This is deliberately free of product-specific labels and routes.  Paging,
    counters, and unrelated widgets frequently share small scalar values with
    a later write body; without a structural alias they are not field identity.
    """
    requests = [{
        "request_id": "req_write",
        "method": "POST",
        "url": "http://identity.invalid/v3/resources",
        "post_data": json.dumps({"createdBy": 1}),
        "response_status": 200,
        "page_id": "page_shared",
        "frame_id": "frame_main",
        "trigger_action_id": "action_save",
        "trigger_transaction_id": "tx_save",
        "role": "business_write",
    }]
    evidence = [{
        "label": "Current position",
        "field": "",
        "field_aliases": [],
        "value": 1,
        "control_kind": "number",
        "surface": "page",
        "in_dialog": False,
        "page_id": "page_shared",
        "frame_id": "frame_main",
        "action_id": "action_save",
        "transaction_id": "tx_save",
        "op": "snapshot",
    }]

    item = bind_field_evidence(requests, [], evidence)[0]

    assert item.get("binding_status") != "bound"
    assert not item.get("wire_path")


def test_explicit_request_and_wire_path_bind_each_array_occurrence() -> None:
    request = {
        "request_id": "req_rows",
        "method": "POST",
        "url": "http://occurrence.invalid/v2/submit",
        "post_data": '{"rows":[{"value":"A"},{"value":"B"}]}',
        "response_status": 200,
        "page_id": "page_rows",
        "frame_id": "frame_rows",
    }
    evidence = [
        {
            "label": "Value",
            "field_aliases": ["value"],
            "value": value,
            "request_id": "req_rows",
            "wire_path": f"body.rows[{index}].value",
            "binding_status": "bound",
            "page_id": "page_rows",
            "frame_id": "frame_rows",
        }
        for index, value in enumerate(("A", "B"))
    ]

    bound = bind_field_evidence([request], [], evidence)

    assert [item.get("binding_status") for item in bound] == ["bound", "bound"]
    assert [item.get("wire_path") for item in bound] == [
        "body.rows[0].value",
        "body.rows[1].value",
    ]


def test_retry_requests_in_same_transaction_are_not_fake_ambiguity() -> None:
    failed = {
        "request_id": "req_fail",
        "method": "POST",
        "url": "http://example.test/doc/save",
        "page_id": "page_1",
        "frame_id": "frame_1",
        "page_context": PAGE,
        "trigger_action_id": "action_save",
        "trigger_transaction_id": "tx_save",
        "post_data": json.dumps({"remark": "hello"}),
        "response_status": 500,
        "role": "business_write",
    }
    success = {
        **failed,
        "request_id": "req_ok",
        "response_status": 200,
    }
    evidence = [{
        "label": "备注",
        "field": "remark",
        "value": "hello",
        "field_aliases": ["remark"],
        "control_kind": "textarea",
        "page_id": "page_1",
        "frame_id": "frame_1",
        "page_context": PAGE,
        "action_id": "action_save",
        "transaction_id": "tx_save",
        "op": "fill",
    }]
    bound = bind_field_evidence([failed, success], [], evidence)
    item = bound[0]
    assert item.get("binding_status") == "bound"
    assert item.get("request_id") == "req_ok"


def test_polling_get_does_not_steal_submit_fields() -> None:
    post = {
        "request_id": "req_post",
        "method": "POST",
        "url": "http://example.test/doc/save",
        "page_id": "page_1",
        "frame_id": "frame_1",
        "page_context": PAGE,
        "trigger_action_id": "action_save",
        "trigger_transaction_id": "tx_save",
        "post_data": json.dumps({"keyword": "hello"}),
        "timestamp": 2000,
        "role": "business_write",
    }
    poll = {
        "request_id": "req_poll",
        "method": "GET",
        "url": "http://example.test/heartbeat?keyword=hello",
        "query": {"keyword": ["hello"]},
        "page_id": "page_1",
        "frame_id": "frame_1",
        "page_context": PAGE,
        "timestamp": 2500,
        "role": "read_context",
    }
    evidence = [{
        "label": "关键字",
        "field": "keyword",
        "value": "hello",
        "field_aliases": ["keyword"],
        "control_kind": "text",
        "page_id": "page_1",
        "frame_id": "frame_1",
        "page_context": PAGE,
        "action_id": "action_save",
        "transaction_id": "tx_save",
        "observed_at": 1800,
        "op": "fill",
    }]
    bound = bind_field_evidence([post, poll], [], evidence)
    assert bound[0].get("request_id") == "req_post"


def test_iframe_request_does_not_bind_parent_control() -> None:
    request = {
        "request_id": "req_iframe",
        "method": "POST",
        "url": "http://example.test/embed/save",
        "page_id": "page_1",
        "frame_id": "frame_iframe",
        "page_context": PAGE,
        "trigger_action_id": "action_embed",
        "post_data": json.dumps({"remark": "child"}),
        "role": "business_write",
    }
    evidence = [{
        "label": "备注",
        "field": "remark",
        "value": "child",
        "field_aliases": ["remark"],
        "control_kind": "textarea",
        "page_id": "page_1",
        "frame_id": "frame_parent",
        "page_context": PAGE,
        "action_id": "action_parent",
        "op": "fill",
    }]
    bound = bind_field_evidence([request], [], evidence)
    assert bound[0].get("binding_status") != "bound"


def test_unmarked_control_required_state_is_unknown() -> None:
    session = RecordSession()
    _feed(session, {
        "op": "fill", "locator": "css=input[name=note]", "field": "说明",
        "value": "x", "field_aliases": ["note"], "control_kind": "text",
        "required_state": "unknown", "required_observed": None,
        "action_id": "action_note", "page_context": PAGE,
    })
    item = session.recorded_field_evidence()[-1]
    assert item.get("required_state") == "unknown"
    assert item.get("required_observed") is None


def test_hidden_create_field_is_not_auto_caller_input() -> None:
    spec = FlowSpec(tenant="t", subsystem="oa")
    spec.steps = [FlowStep(
        step_id="step_create",
        method="POST",
        path="/doc/create",
        params=[
            ParamField(path="title", key="title", value="hello", source_kind="user_input"),
            ParamField(path="csrf", key="csrf", value="tok", source_kind="unknown"),
            ParamField(path="partyId", key="partyId", value="9", source_kind="unknown"),
        ],
    )]
    spec.steps[0].params[0].evidence = [{
        "kind": "page_control", "control_kind": "text", "editable": True, "interacted": True,
    }]
    spec.steps[0].params[1].evidence = [{
        "kind": "page_control", "control_kind": "hidden", "hidden": True, "editable": False,
    }]
    _apply_create_form_field_contracts(spec)
    csrf = spec.steps[0].params[1]
    party = spec.steps[0].params[2]
    assert csrf.source_kind != "user_input"
    assert party.source_kind != "form_option"
    assert party.source_kind in {"unknown", ""}


def test_generic_sum_is_not_computed_but_line_total_is() -> None:
    generic = FlowSpec(tenant="t", subsystem="oa")
    generic.steps = [FlowStep(
        step_id="step_g",
        method="POST",
        path="/x",
        params=[
            ParamField(path="a", key="a", value="10", source_kind="user_input"),
            ParamField(path="b", key="b", value="20", source_kind="user_input"),
            ParamField(path="c", key="c", value="30", source_kind="unknown"),
        ],
    )]
    _infer_arithmetic_computed_fields(generic)
    assert generic.steps[0].params[2].source_kind != "computed"

    priced = FlowSpec(tenant="t", subsystem="oa")
    priced.steps = [FlowStep(
        step_id="step_p",
        method="POST",
        path="/x",
        params=[
            ParamField(path="qty", key="qty", value="2", source_kind="user_input"),
            ParamField(path="unitPrice", key="unitPrice", value="5", source_kind="user_input"),
            ParamField(path="lineTotal", key="lineTotal", value="10", source_kind="unknown"),
        ],
    )]
    priced.steps[0].params[2].evidence = [{
        "kind": "page_control", "read_only": True, "editable": False, "control_kind": "text",
    }]
    _infer_arithmetic_computed_fields(priced)
    assert priced.steps[0].params[2].source_kind == "computed"


def test_billno_query_filter_is_not_selected_record_identity() -> None:
    spec = to_flow_spec(
        captured_requests=[{
            "request_id": "req_search",
            "sequence": 1,
            "method": "GET",
            "url": "http://example.test/admin-api/doc/page?billNo=B1",
            "query": {"billNo": ["B1"]},
            "response_status": 200,
            "response_json": {"code": 0, "data": {"list": []}},
            "page_id": "page_1",
            "frame_id": "frame_1",
            "page_context": PAGE,
            "trigger_action_id": "act_search",
            "trigger_transaction_id": "act_search",
            "_request_role": {"role": "business_get", "keep": True, "confidence": 0.95},
        }],
        field_evidence=[{
            "label": "单据号",
            "field": "billNo",
            "value": "B1",
            "field_aliases": ["billNo"],
            "control_kind": "text",
            "required_state": "unknown",
            "page_id": "page_1",
            "frame_id": "frame_1",
            "page_context": PAGE,
            "action_id": "act_search",
            "transaction_id": "act_search",
            "op": "fill",
            "binding_status": "bound",
            "request_id": "req_search",
            "wire_path": "query.billNo",
        }],
        page_events=[{"event_id": "ev_search", "kind": "click", "action_id": "act_search"}],
        page_context=PAGE,
    )
    step = next(item for item in spec.steps if "/doc/page" in str(item.path or item.url or ""))
    bill = next(param for param in step.params if "billNo" in str(param.path or param.key))
    assert str((bill.source or {}).get("kind") or "") != "selected_record_identity"


def test_long_recording_keeps_first_form_facts_after_snapshot_tail_drop() -> None:
    session = RecordSession()
    _feed(session, {
        "op": "form_snapshot",
        "action_id": "action_first",
        "fields": [{
            "field": "title", "label": "标题", "value": "first-title",
            "field_aliases": ["title"], "required_state": "required",
            "required_observed": True, "control_kind": "text",
        }],
        "page_context": PAGE,
    })
    _feed(session, {
        "op": "enum_snapshot", "locator": "css=select[name=kind]", "field": "类型",
        "field_aliases": ["kind"], "control_kind": "select",
        "action_id": "action_first", "page_context": PAGE,
        "options": [{"label": "甲类", "value": "a"}],
    })
    for index in range(35):
        _feed(session, {
            "op": "form_snapshot",
            "action_id": f"action_later_{index}",
            "fields": [
                {
                    "field": f"f{n}", "label": f"字段{n}", "value": f"v{index}-{n}",
                    "field_aliases": [f"f{n}"], "control_kind": "text",
                }
                for n in range(20)
            ],
            "page_context": PAGE,
        })
    assert len(session.form_snapshots) <= 20
    evidence = session.recorded_field_evidence()
    first = next(
        item for item in evidence
        if "title" in (item.get("field_aliases") or []) or item.get("label") == "标题"
    )
    assert first.get("value") == "first-title"
    assert first.get("required_state") == "required"
    enums = session.recorded_page_enum_options()
    assert any(
        any(str(option.get("label") if isinstance(option, dict) else option) == "甲类"
            for option in (entry.get("options") or []))
        for entry in enums.values()
    )
    by_tx = session.recorded_form_samples_by_transaction()
    assert any("first-title" in group.values() for group in by_tx.values())


def test_html_and_aria_required_markers_are_required() -> None:
    from dano.execution.page.recorder import _RECORDER_JS

    assert "function requiredStateOf" in _RECORDER_JS
    assert "aria-required" in _RECORDER_JS
    session = RecordSession()
    _feed(session, {
        "op": "fill", "locator": "css=input[name=title]", "field": "标题",
        "value": "x", "field_aliases": ["title"], "control_kind": "text",
        "required": True, "required_state": "required", "required_observed": True,
        "action_id": "action_title", "page_context": PAGE,
    })
    _feed(session, {
        "op": "fill", "locator": "css=input[name=note]", "field": "说明",
        "value": "y", "field_aliases": ["note"], "control_kind": "text",
        "required": True, "required_state": "required", "required_observed": True,
        "action_id": "action_note", "page_context": PAGE,
    })
    evidence = session.recorded_field_evidence()
    title = next(item for item in evidence if "title" in (item.get("field_aliases") or []))
    note = next(item for item in evidence if "note" in (item.get("field_aliases") or []))
    assert title.get("required_state") == "required"
    assert note.get("required_state") == "required"


def test_empty_string_and_present_value_do_not_prove_optional_or_required() -> None:
    spec = to_flow_spec(
        captured_requests=[{
            "request_id": "req_save",
            "sequence": 1,
            "method": "POST",
            "url": "http://example.test/doc/create",
            "post_data": json.dumps({"remark": "", "title": "hello"}),
            "response_status": 200,
            "response_json": {"code": 0},
            "page_id": "page_1",
            "frame_id": "frame_1",
            "page_context": PAGE,
            "trigger_action_id": "act_create",
            "trigger_transaction_id": "act_create",
            "_request_role": {"role": "business_write", "keep": True, "confidence": 0.95},
        }],
        field_evidence=[
            {
                "label": "备注", "field": "remark", "value": "",
                "field_aliases": ["remark"], "control_kind": "textarea",
                "required_state": "unknown", "required_observed": None,
                "page_id": "page_1", "frame_id": "frame_1", "page_context": PAGE,
                "action_id": "act_create", "transaction_id": "act_create",
                "op": "fill", "binding_status": "bound", "request_id": "req_save",
                "wire_path": "body.remark", "editable": True,
            },
            {
                "label": "标题", "field": "title", "value": "hello",
                "field_aliases": ["title"], "control_kind": "text",
                "required_state": "unknown", "required_observed": None,
                "page_id": "page_1", "frame_id": "frame_1", "page_context": PAGE,
                "action_id": "act_create", "transaction_id": "act_create",
                "op": "fill", "binding_status": "bound", "request_id": "req_save",
                "wire_path": "body.title", "editable": True,
            },
        ],
        page_events=[{"event_id": "ev_create", "kind": "click", "action_id": "act_create"}],
        page_context=PAGE,
    )
    create = next(item for item in spec.steps if "/doc/create" in str(item.path or item.url or ""))
    remark = next(param for param in create.params if "remark" in str(param.path or param.key))
    title = next(param for param in create.params if "title" in str(param.path or param.key))
    assert str((remark.source or {}).get("required_state") or "unknown") == "unknown"
    assert str((title.source or {}).get("required_state") or "unknown") == "unknown"


def test_successful_omit_can_mark_query_filter_optional() -> None:
    spec = to_flow_spec(
        captured_requests=[
            {
                "request_id": "req_full",
                "sequence": 1,
                "method": "GET",
                "url": "http://example.test/admin-api/doc/page?status=20&remark=keep",
                "query": {"status": ["20"], "remark": ["keep"]},
                "response_status": 200,
                "response_json": {"code": 0, "data": {"list": []}},
                "page_id": "page_1",
                "frame_id": "frame_1",
                "page_context": PAGE,
                "trigger_action_id": "act_search_a",
                "trigger_transaction_id": "act_search_a",
                "_request_role": {"role": "business_get", "keep": True, "confidence": 0.95},
            },
            {
                "request_id": "req_omit",
                "sequence": 2,
                "method": "GET",
                "url": "http://example.test/admin-api/doc/page?status=20",
                "query": {"status": ["20"]},
                "response_status": 200,
                "response_json": {"code": 0, "data": {"list": []}},
                "page_id": "page_1",
                "frame_id": "frame_1",
                "page_context": PAGE,
                "trigger_action_id": "act_search_b",
                "trigger_transaction_id": "act_search_b",
                "_request_role": {"role": "business_get", "keep": True, "confidence": 0.95},
            },
        ],
        field_evidence=[{
            "label": "状态", "field": "status", "value": "20",
            "field_aliases": ["status"], "control_kind": "select",
            "required_state": "unknown", "required_observed": None,
            "page_id": "page_1", "frame_id": "frame_1", "page_context": PAGE,
            "action_id": "act_search_a", "transaction_id": "act_search_a",
            "op": "select", "binding_status": "bound", "request_id": "req_full",
            "wire_path": "query.status", "editable": True,
        }],
        page_events=[
            {"event_id": "ev_a", "kind": "click", "action_id": "act_search_a"},
            {"event_id": "ev_b", "kind": "click", "action_id": "act_search_b"},
        ],
        page_context=PAGE,
    )
    listing = next(item for item in spec.steps if "/doc/page" in str(item.path or item.url or ""))
    remark = next(param for param in listing.params if "remark" in str(param.path or param.key))
    status = next(param for param in listing.params if "status" in str(param.path or param.key))
    assert str((remark.source or {}).get("required_state") or "") == "optional"
    assert str((status.source or {}).get("required_state") or "unknown") == "unknown"


def test_successful_omit_does_not_cross_http_method() -> None:
    spec = to_flow_spec(
        captured_requests=[
            {
                "request_id": "req_get_full",
                "sequence": 1,
                "method": "GET",
                "url": "http://scope.invalid/v2/resources?state=open&note=seen",
                "query": {"state": ["open"], "note": ["seen"]},
                "response_status": 200,
                "response_json": {"ok": True},
                "page_id": "page_query",
                "frame_id": "frame_main",
                "trigger_action_id": "action_query_a",
                "trigger_transaction_id": "tx_query_a",
                "_request_role": {"role": "business_get", "keep": True, "confidence": 1.0},
            },
            {
                "request_id": "req_get_omit",
                "sequence": 2,
                "method": "GET",
                "url": "http://scope.invalid/v2/resources?state=open",
                "query": {"state": ["open"]},
                "response_status": 200,
                "response_json": {"ok": True},
                "page_id": "page_query",
                "frame_id": "frame_main",
                "trigger_action_id": "action_query_b",
                "trigger_transaction_id": "tx_query_b",
                "_request_role": {"role": "business_get", "keep": True, "confidence": 1.0},
            },
            {
                "request_id": "req_post",
                "sequence": 3,
                "method": "POST",
                "url": "http://scope.invalid/v2/resources",
                "post_data": json.dumps({"note": "required value"}),
                "response_status": 200,
                "response_json": {"ok": True},
                "page_id": "page_editor",
                "frame_id": "frame_main",
                "trigger_action_id": "action_create",
                "trigger_transaction_id": "tx_create",
                "_request_role": {"role": "business_write", "keep": True, "confidence": 1.0},
            },
        ],
        field_evidence=[{
            "label": "Memo",
            "field": "note",
            "value": "required value",
            "field_aliases": ["note"],
            "control_kind": "text",
            "required_state": "unknown",
            "required_observed": None,
            "page_id": "page_editor",
            "frame_id": "frame_main",
            "action_id": "action_create",
            "transaction_id": "tx_create",
            "op": "fill",
            "binding_status": "bound",
            "request_id": "req_post",
            "wire_path": "body.note",
            "editable": True,
        }],
        page_events=[{"event_id": "event_create", "kind": "click", "action_id": "action_create"}],
        page_context={"url": "http://scope.invalid/editor", "path": "/editor"},
    )
    write = next(step for step in spec.steps if step.method == "POST")
    note = next(param for param in write.params if param.path == "note")
    assert str((note.source or {}).get("required_state") or "unknown") == "unknown"


def test_required_state_does_not_mix_across_surfaces() -> None:
    session = RecordSession()
    _feed(session, {
        "op": "fill", "locator": "css=input[name=keyword]", "field": "关键字",
        "value": "list", "field_aliases": ["keyword"], "control_kind": "text",
        "required_state": "unknown", "required_observed": None,
        "in_dialog": False, "surface": "page",
        "action_id": "action_search", "page_context": PAGE,
    })
    _feed(session, {
        "op": "fill", "locator": "css=input[name=keyword]", "field": "关键字",
        "value": "edit", "field_aliases": ["keyword"], "control_kind": "text",
        "required_state": "required", "required_observed": True,
        "in_dialog": True, "surface": "dialog", "form_root": "dialog_edit",
        "action_id": "action_edit", "page_context": PAGE,
    })
    evidence = session.recorded_field_evidence()
    rows = [item for item in evidence if "keyword" in (item.get("field_aliases") or [])]
    identities = {item.get("field_identity_id") for item in rows}
    assert len(identities) == 2
    states = {(item.get("surface") or ("dialog" if item.get("in_dialog") else "page"), item.get("required_state")) for item in rows}
    assert ("page", "unknown") in states
    assert ("dialog", "required") in states


def test_equal_array_counts_bind_template_not_first_row() -> None:
    requests = [{
        "request_id": "req_save",
        "method": "POST",
        "url": "http://example.test/doc/save",
        "page_id": "page_1",
        "frame_id": "frame_1",
        "page_context": PAGE,
        "trigger_action_id": "action_save",
        "trigger_transaction_id": "page_1|frame_1|action_save",
        "post_data": json.dumps({
            "items": [
                {"productId": "p1", "count": 2},
                {"productId": "p2", "count": 2},
            ]
        }),
        "role": "business_write",
    }]
    evidence = [{
        "label": "数量",
        "field": "count",
        "value": 2,
        "field_aliases": ["count"],
        "control_kind": "number",
        "page_id": "page_1",
        "frame_id": "frame_1",
        "page_context": PAGE,
        "action_id": "action_save",
        "transaction_id": "page_1|frame_1|action_save",
        "op": "fill",
    }]
    bound = bind_field_evidence(requests, [], evidence)
    item = bound[0]
    assert item.get("wire_path") != "body.items[0].count"
    assert item.get("wire_path") == "body.items[].count" or item.get("binding_status") == "ambiguous"


def test_business_list_is_not_option_catalog() -> None:
    spec = to_flow_spec(
        captured_requests=[
            {
                "request_id": "req_list",
                "sequence": 1,
                "method": "GET",
                "url": "http://example.test/admin-api/doc/page",
                "response_status": 200,
                "response_json": {"code": 0, "data": [
                    {"id": 4, "name": "甲件", "price": 5000},
                    {"id": 7, "name": "乙件", "price": 3000},
                ]},
                "page_id": "page_1",
                "frame_id": "frame_1",
                "page_context": PAGE,
                "trigger_action_id": "act_search",
                "_request_role": {"role": "business_get", "keep": True, "confidence": 0.95},
            },
            {
                "request_id": "req_create",
                "sequence": 2,
                "method": "POST",
                "url": "http://example.test/admin-api/doc/create",
                "post_data": json.dumps({
                    "productId": 4,
                    "productName": "甲件",
                    "productPrice": 5000,
                }),
                "response_status": 200,
                "response_json": {"code": 0},
                "page_id": "page_1",
                "frame_id": "frame_1",
                "page_context": PAGE,
                "trigger_action_id": "act_create",
                "_request_role": {"role": "business_write", "keep": True, "confidence": 0.95},
            },
        ],
        field_evidence=[{
            "label": "产品", "field": "productId", "value": 4,
            "field_aliases": ["productId"], "control_kind": "select",
            "page_id": "page_1", "frame_id": "frame_1", "page_context": PAGE,
            "action_id": "act_create", "op": "select",
            "binding_status": "bound", "request_id": "req_create",
            "wire_path": "body.productId", "editable": True,
        }],
        page_events=[{"event_id": "ev_create", "kind": "click", "action_id": "act_create"}],
        page_context=PAGE,
    )
    create = next(item for item in spec.steps if "/doc/create" in str(item.path or item.url or ""))
    name = next(param for param in create.params if "productName" in str(param.path or param.key))
    assert name.source_kind != "selected_option_field"


def test_identical_id_values_do_not_all_become_option_fields() -> None:
    spec = to_flow_spec(
        captured_requests=[
            {
                "request_id": "req_items",
                "sequence": 1,
                "method": "GET",
                "url": "http://example.test/admin-api/item/simple-list",
                "response_status": 200,
                "response_json": {"code": 0, "data": [
                    {"id": 5, "tenantId": 5, "ownerId": 5, "name": "甲件"},
                    {"id": 7, "tenantId": 5, "ownerId": 9, "name": "乙件"},
                ]},
                "page_id": "page_1",
                "frame_id": "frame_1",
                "page_context": PAGE,
                "_request_role": {"role": "read_option", "keep": True, "confidence": 0.95},
            },
            {
                "request_id": "req_create",
                "sequence": 2,
                "method": "POST",
                "url": "http://example.test/admin-api/doc/create",
                "post_data": json.dumps({
                    "partyId": 5,
                    "tenantId": 5,
                    "ownerId": 5,
                    "productName": "甲件",
                }),
                "response_status": 200,
                "response_json": {"code": 0},
                "page_id": "page_1",
                "frame_id": "frame_1",
                "page_context": PAGE,
                "trigger_action_id": "act_create",
                "_request_role": {"role": "business_write", "keep": True, "confidence": 0.95},
            },
        ],
        field_evidence=[{
            "label": "往来单位", "field": "partyId", "value": 5,
            "field_aliases": ["partyId"], "control_kind": "select",
            "page_id": "page_1", "frame_id": "frame_1", "page_context": PAGE,
            "action_id": "act_create", "op": "select",
            "binding_status": "bound", "request_id": "req_create",
            "wire_path": "body.partyId", "editable": True, "in_dialog": True,
        }],
        page_events=[{"event_id": "ev_create", "kind": "click", "action_id": "act_create"}],
        page_context=PAGE,
    )
    create = next(item for item in spec.steps if "/doc/create" in str(item.path or item.url or ""))
    tenant = next(param for param in create.params if "tenantId" in str(param.path or param.key))
    owner = next(param for param in create.params if "ownerId" in str(param.path or param.key))
    assert tenant.source_kind != "selected_option_field"
    assert owner.source_kind != "selected_option_field"
    assert tenant.source_kind != "form_option"
    assert owner.source_kind != "form_option"


def test_to_flow_spec_uses_transaction_form_samples_not_global_splice() -> None:
    spec = to_flow_spec(
        captured_requests=[{
            "request_id": "req_edit",
            "sequence": 1,
            "method": "POST",
            "url": "http://example.test/doc/update",
            "post_data": json.dumps({"remark": "乙"}),
            "response_status": 200,
            "response_json": {"code": 0},
            "page_id": "page_1",
            "frame_id": "frame_1",
            "page_context": PAGE,
            "trigger_action_id": "act_edit",
            "trigger_transaction_id": "tx_edit",
            "_request_role": {"role": "business_write", "keep": True, "confidence": 0.95},
        }],
        field_evidence=[{
            "label": "备注", "field": "remark", "value": "乙",
            "field_aliases": ["remark"], "control_kind": "textarea",
            "page_id": "page_1", "frame_id": "frame_1", "page_context": PAGE,
            "action_id": "act_edit", "transaction_id": "tx_edit",
            "op": "fill", "binding_status": "bound", "request_id": "req_edit",
            "wire_path": "body.remark",
        }],
        samples={"备注": "甲", "关键字": "list"},
        form_samples_by_transaction={"tx_edit": {"备注": "乙"}},
        page_events=[{"event_id": "ev_edit", "kind": "click", "action_id": "act_edit"}],
        page_context=PAGE,
    )
    update = next(item for item in spec.steps if "/doc/update" in str(item.path or item.url or ""))
    assert update.sample_inputs.get("remark") in {"乙", None} or "甲" not in str(update.sample_inputs)
    assert "关键字" not in (update.sample_inputs or {})
    assert "list" not in (update.sample_inputs or {}).values()
