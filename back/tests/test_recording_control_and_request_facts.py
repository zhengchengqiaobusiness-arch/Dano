"""Stage 2: page-control capture and a single request/response fact parser."""

from __future__ import annotations

import asyncio
import json

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

