"""Stage 5: field identity, array binding, required tri-state, sources, long recordings."""

from __future__ import annotations

import json

from dano.execution.page.flow_spec import (
    FlowSpec,
    FlowStep,
    ParamField,
    _apply_create_form_field_contracts,
    _infer_arithmetic_computed_fields,
    to_flow_spec,
)
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
