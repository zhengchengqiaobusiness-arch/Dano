"""Stage 2: page-control capture and a single request/response fact parser."""

from __future__ import annotations

import asyncio
import json

import pytest

from dano.execution.page.recorder import (
    _RECORDER_JS,
    RecordSession,
    has_recorded_value,
)
from dano.execution.page.recording_field_evidence import (
    _parse_body as evidence_parse_body,
    _request_fields,
    bind_field_evidence,
)
from dano.execution.page.recording_facts import classify_network_request
from dano.execution.page.request_capture import _parse_body as capture_parse_body
from dano.execution.page.request_capture import parse_recorded_request_body


PAGE = {"url": "http://example.test/app/form", "path": "/app/form", "document_title": "表单"}


def _feed(session: RecordSession, payload: dict) -> None:
    payload = {
        "page_id": "page_1",
        "frame_id": "frame_1",
        **payload,
    }
    session._on_record({}, json.dumps(payload, ensure_ascii=False))


def test_false_and_zero_are_recorded_values() -> None:
    assert has_recorded_value({"value": False}) is True
    assert has_recorded_value({"value": 0}) is True
    assert has_recorded_value({"value": True}) is True
    assert has_recorded_value({"value": "0"}) is True
    assert has_recorded_value({"value": ""}) is False
    assert has_recorded_value({"value": None}) is False
    assert "value: value || ''" not in _RECORDER_JS
    assert "value === null || value === undefined" in _RECORDER_JS


def test_checkbox_false_is_a_field_fact() -> None:
    session = RecordSession()
    _feed(session, {
        "op": "toggle",
        "locator": "css=input[name=agree]",
        "field": "agree",
        "value": False,
        "checked": False,
        "control_kind": "checkbox",
        "field_aliases": ["agree"],
        "action_id": "action_box",
        "page_context": PAGE,
    })
    event = session.page_events[-1]
    assert event["has_value"] is True
    evidence = session.recorded_field_evidence()
    item = next(row for row in evidence if "agree" in (row.get("field_aliases") or [row.get("field")]))
    assert item["value"] is False
    assert item["control_kind"] == "checkbox"


def test_checkbox_true_and_radio_group_and_switch_and_contenteditable() -> None:
    session = RecordSession()
    _feed(session, {
        "op": "toggle",
        "locator": "css=input[name=vip]",
        "field": "vip",
        "value": True,
        "checked": True,
        "control_kind": "checkbox",
        "field_aliases": ["vip"],
        "action_id": "action_vip",
        "page_context": PAGE,
    })
    _feed(session, {
        "op": "select",
        "locator": 'css=input[type="radio"][name=status]',
        "field": "status",
        "value": "open",
        "control_kind": "radio",
        "field_aliases": ["status"],
        "options": [{"label": "打开", "value": "open"}, {"label": "关闭", "value": "closed"}],
        "selected_label": "打开",
        "group_name": "status",
        "action_id": "action_radio",
        "page_context": PAGE,
    })
    _feed(session, {
        "op": "toggle",
        "locator": "css=[role=switch]",
        "field": "enabled",
        "value": True,
        "checked": True,
        "control_kind": "switch",
        "field_aliases": ["enabled"],
        "action_id": "action_switch",
        "page_context": PAGE,
    })
    _feed(session, {
        "op": "fill",
        "locator": "css=[contenteditable=true]",
        "field": "note",
        "value": "hello",
        "control_kind": "contenteditable",
        "field_aliases": ["note"],
        "action_id": "action_note",
        "page_context": PAGE,
    })
    kinds = {item.get("control_kind"): item for item in session.recorded_field_evidence()}
    assert kinds["checkbox"]["value"] is True
    assert kinds["radio"]["value"] == "open"
    assert kinds["radio"]["options"][0]["label"] == "打开"
    assert kinds["switch"]["value"] is True
    assert kinds["contenteditable"]["value"] == "hello"
    assert "role=\"switch\"" in _RECORDER_JS or "role='switch'" in _RECORDER_JS
    assert "contenteditable" in _RECORDER_JS


def test_file_input_does_not_use_fakepath_as_business_value() -> None:
    session = RecordSession()
    _feed(session, {
        "op": "upload",
        "locator": "css=input[name=attachment]",
        "field": "attachment",
        "value": r"C:\fakepath\contract.pdf",
        "filename": "contract.pdf",
        "mime_type": "application/pdf",
        "size": 1200,
        "multiple": False,
        "file_count": 1,
        "control_kind": "file",
        "field_aliases": ["attachment"],
        "action_id": "action_file",
        "page_context": PAGE,
    })
    item = session.recorded_field_evidence()[-1]
    assert item["control_kind"] == "file"
    assert "fakepath" not in str(item.get("value") or "").lower()
    assert item.get("filename") == "contract.pdf"
    _, samples = session.recorded_steps()
    for value in samples.values():
        assert "fakepath" not in str(value).lower()


def test_form_snapshot_shares_action_and_transaction_with_submit() -> None:
    session = RecordSession()
    _feed(session, {
        "op": "form_snapshot",
        "action_id": "action_submit",
        "fields": [{"field": "name", "label": "姓名", "value": "张三", "field_aliases": ["name"]}],
        "page_context": PAGE,
    })
    _feed(session, {
        "op": "submit",
        "locator": "text=保存",
        "field": "",
        "value": "",
        "action_id": "action_submit",
        "page_context": PAGE,
    })
    snapshot = session.form_snapshots[-1]
    submit = next(event for event in session.page_events if event.get("op") == "submit")
    assert snapshot["action_id"] == "action_submit"
    assert snapshot["transaction_id"]
    assert snapshot["transaction_id"] == submit["transaction_id"]


def test_zero_survives_form_samples() -> None:
    session = RecordSession()
    _feed(session, {
        "op": "form_snapshot",
        "action_id": "action_save",
        "fields": [
            {"field": "count", "label": "数量", "value": 0, "field_aliases": ["count"]},
            {"field": "agree", "label": "同意", "value": False, "field_aliases": ["agree"], "control_kind": "checkbox"},
        ],
        "page_context": PAGE,
    })
    samples = session.recorded_form_samples()
    assert 0 in samples.values() or samples.get("数量") == 0 or samples.get("count") == 0
    assert False in samples.values() or samples.get("同意") is False or samples.get("agree") is False


@pytest.mark.asyncio
async def test_table_inline_snapshot_preserves_header_and_real_row_occurrences() -> None:
    from playwright.async_api import async_playwright

    recorded: list[dict] = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context()

        async def receive(_source, raw: str) -> None:
            recorded.append(json.loads(raw))

        await context.expose_binding("__danoRecord", receive)
        await context.add_init_script(f"({_RECORDER_JS})()")
        page = await context.new_page()
        await page.set_content(
            """
            <form aria-label="line editor">
              <table aria-label="line items">
                <thead><tr>
                  <th scope="col" data-field="catalogId">条目</th>
                  <th scope="col" data-field="quantity">数值</th>
                </tr></thead>
                <tbody>
                  <tr data-row-key="row-a">
                    <td><input role="combobox" aria-controls="choices-a" value="Alpha"></td>
                    <td><input type="number" value="2"></td>
                  </tr>
                  <tr data-row-key="row-b">
                    <td><input role="combobox" aria-controls="choices-b" value="Beta"></td>
                    <td><input type="number" value="5"></td>
                  </tr>
                </tbody>
              </table>
            </form>
            """
        )
        fields = await page.evaluate("window.__danoFormFieldEvidence()")
        await browser.close()

    quantities = [item for item in fields if item.get("label") == "数值"]
    assert [item["value"] for item in quantities] == ["2", "5"]
    assert [item["row_index"] for item in quantities] == [0, 1]
    assert [item["row_identity"] for item in quantities] == ["row-a", "row-b"]
    assert all(item["column_index"] == 1 for item in quantities)
    assert all(item["table_id"] == "line items" for item in quantities)
    assert all(item["control_surface"] == "table_inline" for item in quantities)
    assert all("quantity" in item["field_aliases"] for item in quantities)
    assert all(item["form_root"] == "line editor" for item in quantities)

    catalogs = [item for item in fields if item.get("label") == "条目"]
    assert len(catalogs) == 2
    assert all("catalogId" in item["field_aliases"] for item in catalogs)

    session = RecordSession()
    _feed(session, {
        "op": "form_snapshot",
        "action_id": "action_rows",
        "fields": fields,
        "page_context": PAGE,
    })
    occurrences = session.recorded_field_evidence()
    assert len({item["occurrence_id"] for item in occurrences}) == 4
    bound = bind_field_evidence(
        [{
            "request_id": "req_rows",
            "method": "POST",
            "url": "http://example.test/lines/save",
            "page_id": "page_1",
            "frame_id": "frame_1",
            "page_context": PAGE,
            "trigger_action_id": "action_rows",
            "trigger_transaction_id": "page_1|frame_1|action_rows",
            "post_data": json.dumps({
                "lines": [
                    {"catalogId": "a", "quantity": 2},
                    {"catalogId": "b", "quantity": 5},
                ]
            }),
            "role": "business_write",
        }],
        [],
        occurrences,
    )
    quantity_paths = [
        item.get("wire_path") for item in bound if item.get("label") == "数值"
    ]
    assert quantity_paths == ["body.lines[0].quantity", "body.lines[1].quantity"]


@pytest.mark.asyncio
async def test_split_header_table_preserves_all_inline_controls_and_select_is_not_fill() -> None:
    """Framework tables may render header/body in separate native tables."""
    from playwright.async_api import async_playwright

    recorded: list[dict] = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context()

        async def receive(_source, raw: str) -> None:
            recorded.append(json.loads(raw))

        await context.expose_binding("__danoRecord", receive)
        await context.add_init_script(f"({_RECORDER_JS})()")
        page = await context.new_page()
        await page.set_content(
            """
            <form aria-label="order editor">
              <div class="el-table" aria-label="line items">
                <div class="el-table__header-wrapper">
                  <table class="el-table__header"><thead><tr>
                    <th data-field="productId">Product</th>
                    <th data-field="count">Quantity</th>
                  </tr></thead></table>
                </div>
                <div class="el-table__body-wrapper">
                  <table class="el-table__body"><tbody>
                    <tr data-row-key="row-1">
                      <td><input id="product" role="combobox" readonly value="Alpha"></td>
                      <td><input id="quantity" type="number" value="2"></td>
                    </tr>
                  </tbody></table>
                </div>
              </div>
            </form>
            """
        )
        fields = await page.evaluate("window.__danoFormFieldEvidence()")
        await page.dispatch_event("#product", "input")
        await page.dispatch_event("#product", "change")
        await page.dispatch_event("#product", "blur")
        await page.wait_for_timeout(350)
        await browser.close()

    product = next(item for item in fields if item.get("label") == "Product")
    quantity = next(item for item in fields if item.get("label") == "Quantity")
    assert product["control_kind"] == "select"
    assert product["column_label"] == "Product"
    assert product["row_index"] == 0
    assert product["row_identity"] == "row-1"
    assert product["table_id"] == "line items"
    assert "productId" in product["field_aliases"]
    assert quantity["control_kind"] == "number"
    assert quantity["column_label"] == "Quantity"
    assert "count" in quantity["field_aliases"]
    assert not any(
        item.get("op") == "fill" and item.get("field") == "Product"
        for item in recorded
    )


@pytest.mark.asyncio
async def test_form_snapshot_keeps_hidden_native_file_control_without_selection() -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.expose_binding("__danoRecord", lambda _source, _raw: None)
        await context.add_init_script(f"({_RECORDER_JS})()")
        page = await context.new_page()
        await page.set_content(
            """
            <form aria-label="asset editor">
              <label for="asset-file">Artifact</label>
              <input id="asset-file" name="asset" type="file" style="display:none" multiple>
              <button type="button" aria-controls="asset-file">Choose</button>
            </form>
            """
        )
        fields = await page.evaluate("window.__danoFormFieldEvidence()")
        await browser.close()

    asset = next(item for item in fields if "asset" in (item.get("field_aliases") or []))
    assert asset["control_kind"] == "file"
    assert asset["file_count"] == 0
    assert asset["multiple"] is True
    assert asset["value"] == ""
    assert "asset" in asset["field_aliases"]


def test_exact_compiled_label_prop_pair_recovers_missing_dom_alias() -> None:
    session = RecordSession()
    session.script_sources = [{
        "url": "http://ui.invalid/assets/form.js",
        "text": 'component(FormItem,{label:"Magnitude",prop:"amount"})',
    }]
    _feed(session, {
        "op": "form_snapshot",
        "action_id": "action_amount",
        "fields": [{
            "field": "Magnitude",
            "label": "Magnitude",
            "value": "7",
            "field_aliases": [],
            "control_kind": "number",
            "surface": "dialog",
            "in_dialog": True,
        }],
        "page_context": PAGE,
    })

    evidence = session.recorded_field_evidence()
    assert evidence[0]["field_aliases"] == ["amount"]
    assert "script_form_declaration" in evidence[0]["identity_sources"]


def test_shared_body_parser_json_and_stringified_json() -> None:
    parsed = parse_recorded_request_body('{"name":"张三","ok":true}')
    assert parsed["kind"] == "json"
    assert parsed["value"]["name"] == "张三"
    nested = parse_recorded_request_body(
        json.dumps({"formData": json.dumps({"name": "张三", "departmentId": 3}, ensure_ascii=False)}, ensure_ascii=False),
        "application/json",
    )
    paths = nested["field_paths"]
    assert any(path.endswith("formData.name") or path == "formData.name" for path in paths)
    assert any("departmentId" in path for path in paths)


def test_shared_body_parser_form_repeated_keys() -> None:
    parsed = parse_recorded_request_body("tag=a&tag=b", "application/x-www-form-urlencoded")
    assert parsed["kind"] == "form"
    assert parsed["value"]["tag"] == ["a", "b"]
    capture = capture_parse_body("tag=a&tag=b")
    evidence = evidence_parse_body({"post_data": "tag=a&tag=b", "content_type": "application/x-www-form-urlencoded"})
    assert capture["tag"] == ["a", "b"]
    assert evidence["tag"] == ["a", "b"]


def test_shared_body_parser_multipart_text_and_file() -> None:
    body = (
        "--abc123\r\n"
        'Content-Disposition: form-data; name="title"\r\n\r\n'
        "合同\r\n"
        "--abc123\r\n"
        'Content-Disposition: form-data; name="attachment"; filename="a.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
        "%PDF-fake-bytes\r\n"
        "--abc123--"
    )
    parsed = parse_recorded_request_body(body, "multipart/form-data; boundary=abc123")
    assert parsed["kind"] == "multipart"
    assert parsed["value"]["title"] == "合同"
    assert "attachment" not in parsed["value"] or parsed["value"].get("attachment") in (None, "")
    assert parsed["file_fields"][0]["name"] == "attachment"
    assert parsed["file_fields"][0]["filename"] == "a.pdf"
    assert "abc123" not in (parsed["value"] or {})
    fields = _request_fields({"post_data": body, "content_type": "multipart/form-data; boundary=abc123", "url": "http://example.test/upload"})
    assert "body.title" in fields
    assert "body.attachment" in fields


def test_multipart_file_request_remains_in_the_recorded_business_flow() -> None:
    body = (
        "--boundary-x\r\n"
        'Content-Disposition: form-data; name="document"; filename="note.txt"\r\n'
        "Content-Type: text/plain\r\n\r\n"
        "hello\r\n"
        "--boundary-x--"
    )
    role = classify_network_request({
        "request_id": "req_file",
        "method": "POST",
        "url": "http://files.invalid/v3/blobs",
        "content_type": "multipart/form-data; boundary=boundary-x",
        "post_data": body,
        "trigger_action_id": "action_file",
        "trigger_transaction_id": "tx_file",
    })
    assert role["keep"] is True
    assert role["role"] in {"business_write", "submit_anchor"}


def test_file_control_becomes_a_caller_file_input_in_flow_spec() -> None:
    from dano.execution.page.flow_spec import to_flow_spec

    body = (
        "--file-boundary\r\n"
        'Content-Disposition: form-data; name="document"; filename="note.txt"\r\n'
        "Content-Type: text/plain\r\n\r\n"
        "hello\r\n"
        "--file-boundary--"
    )
    spec = to_flow_spec(
        captured_requests=[{
            "request_id": "req_file",
            "sequence": 1,
            "method": "POST",
            "url": "http://files.invalid/v3/blobs",
            "content_type": "multipart/form-data; boundary=file-boundary",
            "post_data": body,
            "response_status": 200,
            "response_json": {"key": "blob-key"},
            "page_id": "page_file",
            "frame_id": "frame_main",
            "trigger_action_id": "action_file",
            "trigger_transaction_id": "tx_file",
            "_request_role": {"role": "business_write", "keep": True, "confidence": 1.0},
        }],
        field_evidence=[{
            "field": "document",
            "label": "Document",
            "filename": "note.txt",
            "mime_type": "text/plain",
            "size": 5,
            "file_count": 1,
            "multiple": False,
            "field_aliases": ["document"],
            "control_kind": "file",
            "action_id": "action_file",
            "transaction_id": "tx_file",
            "page_id": "page_file",
            "frame_id": "frame_main",
            "op": "upload",
            "binding_status": "bound",
            "request_id": "req_file",
            "wire_path": "body.document",
            "editable": True,
            "required_state": "unknown",
        }],
        page_events=[{"event_id": "event_file", "kind": "upload", "action_id": "action_file"}],
        page_context={"url": "http://files.invalid/form", "path": "/form"},
    )
    upload = next(step for step in spec.steps if step.path == "/v3/blobs")
    document = next(param for param in upload.params if param.path == "document")
    assert document.type == "file"
    assert document.source_kind == "user_input"
    assert document.source["kind"] == "file_input"
    assert document.exposed_to_user is True
    from dano.execution.page.capability_compiler import compile_capabilities

    compilation = compile_capabilities(spec, {
        "business_understanding": {"business_name": "Generic upload"},
        "capabilities": [{
            "name": "submit_document",
            "title": "Submit document",
            "kind": "create",
            "anchor_step_id": upload.step_id,
        }],
        "unresolved_items": [],
    })
    assert compilation.errors == []
    file_schema = next(iter(compilation.capabilities[0].input_schema["properties"].values()))
    assert file_schema["x-dano-business-type"] == "file"
    assert file_schema["type"] == "string"
    assert file_schema["format"] == "binary"


def test_two_step_upload_response_is_linked_to_the_business_request() -> None:
    from dano.execution.page.flow_spec import to_flow_spec

    upload_body = (
        "--upload-boundary\r\n"
        'Content-Disposition: form-data; name="asset"; filename="proof.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
        "bytes\r\n"
        "--upload-boundary--"
    )
    spec = to_flow_spec(
        captured_requests=[
            {
                "request_id": "req_upload",
                "sequence": 1,
                "method": "POST",
                "url": "http://uploads.invalid/v2/assets",
                "content_type": "multipart/form-data; boundary=upload-boundary",
                "post_data": upload_body,
                "response_status": 200,
                "response_json": {"data": {"assetKey": "asset-random-42"}},
                "page_id": "page_editor",
                "frame_id": "frame_main",
                "trigger_action_id": "action_save",
                "trigger_transaction_id": "tx_save",
                "_request_role": {"role": "business_write", "keep": True, "confidence": 1.0},
            },
            {
                "request_id": "req_save",
                "sequence": 2,
                "method": "POST",
                "url": "http://business.invalid/v2/records",
                "content_type": "application/json",
                "post_data": json.dumps({"title": "Draft", "assetKey": "asset-random-42"}),
                "response_status": 200,
                "response_json": {"ok": True},
                "page_id": "page_editor",
                "frame_id": "frame_main",
                "trigger_action_id": "action_save",
                "trigger_transaction_id": "tx_save",
                "_request_role": {"role": "business_write", "keep": True, "confidence": 1.0},
            },
        ],
        field_evidence=[{
            "field": "asset",
            "label": "Proof",
            "filename": "proof.pdf",
            "mime_type": "application/pdf",
            "file_count": 1,
            "multiple": False,
            "field_aliases": ["asset"],
            "control_kind": "file",
            "action_id": "action_save",
            "transaction_id": "tx_save",
            "page_id": "page_editor",
            "frame_id": "frame_main",
            "op": "upload",
            "binding_status": "bound",
            "request_id": "req_upload",
            "wire_path": "body.asset",
            "editable": True,
            "required_state": "unknown",
        }],
        page_events=[{"event_id": "event_save", "kind": "click", "action_id": "action_save"}],
        page_context={"url": "http://business.invalid/editor", "path": "/editor"},
    )
    upload = next(step for step in spec.steps if step.path == "/v2/assets")
    save = next(step for step in spec.steps if step.path == "/v2/records")
    link = next(
        link for link in spec.links
        if link.source_step_id == upload.step_id and link.target_step_id == save.step_id
    )
    assert link.source_path.endswith("assetKey")
    assert link.target_path.endswith("assetKey")
    assert link.confirmed is True


def test_same_method_and_path_on_different_origins_remain_distinct_steps() -> None:
    from dano.execution.page.flow_spec import to_flow_spec

    requests = [
        {
            "request_id": f"req_{host}",
            "sequence": sequence,
            "method": "GET",
            "url": f"http://{host}.invalid/v8/records?scope={host}",
            "response_status": 200,
            "response_json": {"source": host},
            "page_id": "page_records",
            "frame_id": "frame_main",
            "trigger_action_id": f"action_{host}",
            "trigger_transaction_id": f"tx_{host}",
            "_request_role": {"role": "business_get", "keep": True, "confidence": 1.0},
        }
        for sequence, host in enumerate(("alpha", "beta"), 1)
    ]
    spec = to_flow_spec(
        captured_requests=requests,
        field_evidence=[],
        page_events=[],
        page_context={"url": "http://page.invalid/records", "path": "/records"},
    )
    matched = [step for step in spec.steps if "/v8/records" in step.path]
    assert {step.source_meta.get("request_id") for step in matched} == {"req_alpha", "req_beta"}


def test_same_origin_and_path_in_different_frames_remain_distinct_steps() -> None:
    from dano.execution.page.flow_spec import to_flow_spec

    requests = [
        {
            "request_id": f"req_{frame}",
            "sequence": sequence,
            "method": "GET",
            "url": f"http://frames.invalid/v8/records?scope={frame}",
            "response_status": 200,
            "response_json": {"source": frame},
            "page_id": "page_records",
            "frame_id": frame,
            "_request_role": {"role": "business_get", "keep": True, "confidence": 1.0},
        }
        for sequence, frame in enumerate(("frame_a", "frame_b"), 1)
    ]
    spec = to_flow_spec(
        captured_requests=requests,
        field_evidence=[],
        page_events=[],
        page_context={"url": "http://frames.invalid/records", "path": "/records"},
    )
    matched = [step for step in spec.steps if "/v8/records" in step.path]
    assert {step.source_meta.get("request_id") for step in matched} == {"req_frame_a", "req_frame_b"}


def test_text_control_keeps_text_business_type_for_numeric_wire_sample() -> None:
    from dano.execution.page.flow_spec import to_flow_spec

    spec = to_flow_spec(
        captured_requests=[{
            "request_id": "req_code",
            "method": "POST",
            "url": "http://types.invalid/v2/records",
            "content_type": "application/json",
            "post_data": json.dumps({"code": 1}),
            "response_status": 200,
            "response_json": {"ok": True},
            "page_id": "page_code",
            "frame_id": "frame_main",
            "trigger_action_id": "action_save",
            "trigger_transaction_id": "tx_save",
            "_request_role": {"role": "business_write", "keep": True, "confidence": 1.0},
        }],
        field_evidence=[{
            "field": "code",
            "label": "Record code",
            "value": "1",
            "field_aliases": ["code"],
            "control_kind": "text",
            "action_id": "action_save",
            "transaction_id": "tx_save",
            "page_id": "page_code",
            "frame_id": "frame_main",
            "op": "fill",
            "binding_status": "bound",
            "request_id": "req_code",
            "wire_path": "body.code",
            "editable": True,
            "required_state": "unknown",
        }],
        page_events=[{"event_id": "event_save", "kind": "click", "action_id": "action_save"}],
        page_context={"url": "http://types.invalid/editor", "path": "/editor"},
    )
    code = next(param for step in spec.steps for param in step.params if param.path == "code")
    assert code.type == "string"
    assert code.wire_type == "number"


def test_structural_text_evidence_beats_unrelated_numeric_control_on_same_wire_path() -> None:
    from dano.execution.page.flow_spec import to_flow_spec

    requests = [
        {
            "request_id": "req_filter",
            "method": "GET",
            "url": "http://types.invalid/v2/records?descriptor=1",
            "response_status": 200,
            "response_json": {"items": []},
            "page_id": "page_types",
            "frame_id": "frame_main",
            "trigger_action_id": "action_filter",
            "trigger_transaction_id": "tx_filter",
            "_request_role": {"role": "business_get", "keep": True, "confidence": 1.0},
        },
        {
            "request_id": "req_update",
            "method": "PUT",
            "url": "http://types.invalid/v2/records/current",
            "content_type": "application/json",
            "post_data": json.dumps({"descriptor": 1}),
            "response_status": 200,
            "response_json": {"ok": True},
            "page_id": "page_types",
            "frame_id": "frame_main",
            "trigger_action_id": "action_update",
            "trigger_transaction_id": "tx_update",
            "_request_role": {"role": "business_write", "keep": True, "confidence": 1.0},
        },
    ]
    evidence = []
    for request_id, wire_path, surface in (
        ("req_filter", "query.descriptor", "page"),
        ("req_update", "body.descriptor", "dialog"),
    ):
        evidence.extend([
            {
                "field": "descriptor",
                "label": "Descriptor",
                "field_aliases": ["descriptor"],
                "control_kind": "text",
                "value": "1",
                "binding_status": "bound",
                "request_id": request_id,
                "wire_path": wire_path,
                "page_id": "page_types",
                "frame_id": "frame_main",
                "surface": surface,
            },
            {
                "field": "pageIndex",
                "label": "Page",
                "field_aliases": [],
                "control_kind": "number",
                "value": "1",
                "binding_status": "bound",
                "request_id": request_id,
                "wire_path": wire_path,
                "page_id": "page_types",
                "frame_id": "frame_main",
                "surface": "page",
            },
        ])

    spec = to_flow_spec(
        captured_requests=requests,
        field_evidence=evidence,
        page_events=[],
        page_context={"url": "http://types.invalid/records", "path": "/records"},
    )
    matched = {
        param.path: param
        for step in spec.steps
        for param in step.params
        if param.path in {"query.descriptor", "descriptor"}
    }
    assert matched["query.descriptor"].type == "string"
    assert matched["query.descriptor"].label == "Descriptor"
    assert matched["descriptor"].type == "string"
    assert matched["descriptor"].label == "Descriptor"


def test_checkbox_false_binds_to_body_field() -> None:
    request = {
        "request_id": "req_save",
        "method": "POST",
        "url": "http://example.test/save",
        "post_data": json.dumps({"agree": False}),
        "page_id": "page_1",
        "frame_id": "frame_1",
        "trigger_action_id": "action_box",
        "trigger_transaction_id": "page_1|frame_1|action_box",
        "role": "business_write",
    }
    evidence = bind_field_evidence(
        [request],
        [{"event_id": "event_1", "action_id": "action_box", "transaction_id": "page_1|frame_1|action_box"}],
        [{
            "field": "agree",
            "label": "同意",
            "field_aliases": ["agree"],
            "control_kind": "checkbox",
            "value": False,
            "op": "toggle",
            "action_id": "action_box",
            "transaction_id": "page_1|frame_1|action_box",
            "page_id": "page_1",
            "frame_id": "frame_1",
            "page_context": PAGE,
        }],
    )
    assert evidence[0]["binding_status"] == "bound"
    assert evidence[0]["wire_path"] == "body.agree"
    assert evidence[0]["value"] is False


def test_file_control_binds_to_multipart_name() -> None:
    body = (
        "--abc123\r\n"
        'Content-Disposition: form-data; name="attachment"; filename="a.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
        "bytes\r\n"
        "--abc123--"
    )
    request = {
        "request_id": "req_upload",
        "method": "POST",
        "url": "http://example.test/upload",
        "content_type": "multipart/form-data; boundary=abc123",
        "post_data": body,
        "page_id": "page_1",
        "frame_id": "frame_1",
        "trigger_action_id": "action_file",
        "trigger_transaction_id": "page_1|frame_1|action_file",
        "role": "business_write",
    }
    evidence = bind_field_evidence(
        [request],
        [{"event_id": "event_1", "action_id": "action_file", "transaction_id": "page_1|frame_1|action_file"}],
        [{
            "field": "attachment",
            "label": "附件",
            "field_aliases": ["attachment"],
            "control_kind": "file",
            "filename": "a.pdf",
            "value": "a.pdf",
            "op": "upload",
            "action_id": "action_file",
            "transaction_id": "page_1|frame_1|action_file",
            "page_id": "page_1",
            "frame_id": "frame_1",
            "page_context": PAGE,
        }],
    )
    assert evidence[0]["binding_status"] == "bound"
    assert evidence[0]["wire_path"] == "body.attachment"


class _FakeRequest:
    def __init__(self, method: str, url: str) -> None:
        self.method = method
        self.url = url
        self.resource_type = "xhr"
        self.headers = {}
        self.frame = None


class _FakeResponse:
    def __init__(self, *, method: str, url: str, status: int, content_type: str, payload) -> None:
        self.url = url
        self.status = status
        self.headers = {"content-type": content_type}
        self.request = _FakeRequest(method, url)
        self._payload = payload

    async def json(self):
        if not isinstance(self._payload, (dict, list)):
            raise ValueError("not json")
        return self._payload

    async def text(self):
        if isinstance(self._payload, bytes):
            return self._payload.decode("utf-8", errors="replace")
        if self._payload is None:
            return ""
        return str(self._payload)

    async def body(self):
        if isinstance(self._payload, bytes):
            return self._payload
        return (await self.text()).encode("utf-8")


async def _ingest(session: RecordSession, response: _FakeResponse) -> None:
    session._request_fact_index[id(response.request)] = session._record_all(
        response.request.method, response.url, page_id="page_1", frame_id="frame_1",
    )
    await session._on_response(response)


def test_delete_json_and_204_and_text_and_xml_responses_are_kept() -> None:
    session = RecordSession()
    asyncio.run(_ingest(session, _FakeResponse(
        method="DELETE", url="http://example.test/doc/1", status=200,
        content_type="application/json", payload={"ok": True},
    )))
    asyncio.run(_ingest(session, _FakeResponse(
        method="DELETE", url="http://example.test/doc/2", status=204,
        content_type="", payload=None,
    )))
    asyncio.run(_ingest(session, _FakeResponse(
        method="POST", url="http://example.test/echo", status=200,
        content_type="text/plain", payload="saved",
    )))
    asyncio.run(_ingest(session, _FakeResponse(
        method="GET", url="http://example.test/meta.xml", status=200,
        content_type="application/xml", payload="<ok/>",
    )))
    asyncio.run(_ingest(session, _FakeResponse(
        method="GET", url="http://example.test/broken.json", status=500,
        content_type="application/json", payload="not-json",
    )))
    asyncio.run(_ingest(session, _FakeResponse(
        method="GET", url="http://example.test/report/download", status=200,
        content_type="", payload=b"PK\x03\x04\x00\xff\x00\x80",
    )))
    by_url = {item["url"]: item for item in session.all_requests}
    assert by_url["http://example.test/doc/1"]["response_json"] == {"ok": True}
    assert by_url["http://example.test/doc/1"]["response_kind"] == "json"
    assert by_url["http://example.test/doc/2"]["status"] == 204
    assert by_url["http://example.test/doc/2"]["response_empty"] is True
    assert by_url["http://example.test/echo"]["response_text"] == "saved"
    assert "<ok/>" in str(by_url["http://example.test/meta.xml"].get("response_text") or "")
    broken = by_url["http://example.test/broken.json"]
    assert broken["status"] == 500
    assert "json" in str(broken.get("content_type") or "").lower()
    assert broken.get("response_json") in (None, "")
    binary = by_url["http://example.test/report/download"]
    assert binary["response_kind"] == "binary"
    assert binary["response_size"] == 8
    assert binary.get("response_text") in (None, "")


def test_resp_dispatch_includes_delete() -> None:
    source = RecordSession._resp_dispatch.__code__.co_consts
    joined = " ".join(str(item) for item in source)
    assert "DELETE" in joined


def test_false_and_zero_survive_flow_spec_params() -> None:
    from dano.execution.page.flow_spec import to_flow_spec

    spec = to_flow_spec(
        captured_requests=[{
            "request_id": "req_save",
            "sequence": 1,
            "method": "POST",
            "url": "http://example.test/save",
            "post_data": json.dumps({"agree": False, "count": 0}),
            "response_status": 200,
            "response_json": {"ok": True},
            "page_id": "page_1",
            "frame_id": "frame_1",
            "page_context": PAGE,
            "trigger_action_id": "action_save",
            "trigger_transaction_id": "page_1|frame_1|action_save",
            "_request_role": {"role": "business_write", "keep": True, "confidence": 0.95},
        }],
        field_evidence=[
            {
                "label": "同意", "field": "agree", "value": False,
                "field_aliases": ["agree"], "control_kind": "checkbox",
                "page_id": "page_1", "frame_id": "frame_1", "page_context": PAGE,
                "action_id": "action_save", "transaction_id": "page_1|frame_1|action_save",
                "op": "toggle", "binding_status": "bound", "request_id": "req_save",
                "wire_path": "body.agree",
            },
            {
                "label": "数量", "field": "count", "value": 0,
                "field_aliases": ["count"], "control_kind": "number",
                "page_id": "page_1", "frame_id": "frame_1", "page_context": PAGE,
                "action_id": "action_save", "transaction_id": "page_1|frame_1|action_save",
                "op": "fill", "binding_status": "bound", "request_id": "req_save",
                "wire_path": "body.count",
            },
        ],
        page_events=[{"event_id": "ev_save", "kind": "click", "action_id": "action_save"}],
        page_context=PAGE,
    )
    step = next(item for item in spec.steps if str(item.path or item.url or "").endswith("/save"))
    agree = next(param for param in step.params if "agree" in str(param.path or param.key))
    count = next(param for param in step.params if "count" in str(param.path or param.key))
    assert agree.value is False
    assert count.value == 0
    assert agree.type == "boolean"

