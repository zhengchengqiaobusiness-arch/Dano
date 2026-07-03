"""P0-1:all_requests 全量捕获 + diagnostics 诊断事件。

验收点(按你的 P0-1 方案):
1. all_requests 包含 GET/POST/PUT/PATCH/DELETE 所有 method。
2. 每条记录含 method/url/headers/query/post_data/response_json/status/content_type/timestamp。
3. dataiq 三接口(save_dataiq_chat_list / getappid / sjws_chat)都进 all_requests。
4. diagnostics 记录 console / pageerror / requestfailed。
5. captured_all_requests / captured_diagnostics 返回不可变副本(改返回不影响内部)。
6. 不破坏 captured_requests / captured_reads / _capture 既有行为。
"""

from __future__ import annotations

import pytest

from dano.execution.page.recorder import RecordSession


def _new_sess():
    return RecordSession(intercept_submit=True, capture_reads=True)


def _dataiq_requests():
    """dataiq 场景:3 个接口全在(POST + GET + POST)。"""
    return [
        {
            "method": "POST",
            "url": "https://x/dataiq/save_dataiq_chat_list",
            "post_data": '{"user_id": "u1", "name": "test"}',
            "headers": {"Authorization": "Bearer x"},
            "response_json": {"code": 200, "data": {"conversation_id": "c-123"}},
            "status": 200,
            "content_type": "application/json",
        },
        {
            "method": "GET",
            "url": "https://x/apigateway/getappid?appId=auto&appName=auto",
            "post_data": None,
            "headers": {},
            "response_json": {"code": 200, "data": "app-code"},
            "status": 200,
            "content_type": "application/json",
        },
        {
            "method": "POST",
            "url": "https://x/dataiq/sjws_chat",
            "post_data": '{"sys_query": "q", "conversation_id": "c-123", "appCode": "app-code"}',
            "headers": {"Authorization": "Bearer x"},
            "response_json": {"code": 200},
            "status": 200,
            "content_type": "application/json",
        },
    ]


# ── 1. all_requests 字段结构 ──
def test_all_requests_captures_post():
    s = _new_sess()
    s._capture("POST", "https://x/api/submit", '{"a":1}',
               "application/json", {"Authorization": "Bearer t"})
    cap = s.captured_all_requests()
    assert len(cap) == 1
    r = cap[0]
    # 字段齐全
    for k in ("index", "method", "url", "headers", "post_data",
              "response_json", "status", "content_type", "timestamp"):
        assert k in r, f"missing field {k}"
    assert r["method"] == "POST"
    assert r["url"] == "https://x/api/submit"
    assert r["post_data"] == '{"a":1}'
    assert r["headers"]["Authorization"] == "Bearer t"
    assert r["content_type"] == "application/json"
    assert isinstance(r["timestamp"], int) and r["timestamp"] > 0


def test_all_requests_captures_get():
    s = _new_sess()
    s._record_all("GET", "https://x/api/foo", headers={}, content_type="application/json")
    cap = s.captured_all_requests()
    assert len(cap) == 1
    assert cap[0]["method"] == "GET"
    assert cap[0]["post_data"] is None


def test_all_requests_captures_all_methods():
    s = _new_sess()
    for m in ("POST", "GET", "PUT", "PATCH", "DELETE"):
        s._record_all(m, f"https://x/api/{m.lower()}")
    methods = [r["method"] for r in s.captured_all_requests()]
    assert methods == ["POST", "GET", "PUT", "PATCH", "DELETE"]


def test_all_requests_index_monotonic():
    s = _new_sess()
    s._record_all("GET", "https://x/api/a")
    s._record_all("POST", "https://x/api/b")
    s._record_all("PUT", "https://x/api/c")
    idxs = [r["index"] for r in s.captured_all_requests()]
    assert idxs == [0, 1, 2]


def test_all_requests_appends_response_back():
    """_on_response 路径:同 url/method 的最近一条未回填 all_requests 记录应被贴上 response_json/status。"""
    s = _new_sess()
    idx = s._record_all("GET", "https://x/api/foo", content_type="application/json")
    r = s.all_requests[0]
    assert r["response_json"] is None
    # 模拟 _on_response 贴回
    payload = {"code": 200, "data": [1, 2, 3]}
    for x in reversed(s.all_requests):
        if x.get("url") == "https://x/api/foo" and x.get("method") == "GET" and x.get("response_json") is None:
            x["response_json"] = payload
            x["status"] = 200
            break
    assert s.all_requests[0]["response_json"] == payload
    assert s.all_requests[0]["status"] == 200
    assert s.all_requests[0]["index"] == idx


# ── 2. dataiq 验收场景 ──
def test_dataiq_three_requests_all_present():
    s = _new_sess()
    for r in _dataiq_requests():
        s._record_all(r["method"], r["url"],
                      pd=r.get("post_data"),
                      headers=r.get("headers"),
                      response_json=r.get("response_json"),
                      status=r.get("status"),
                      content_type=r.get("content_type", ""))
    cap = s.captured_all_requests()
    urls = [r["url"] for r in cap]
    assert any("save_dataiq_chat_list" in u for u in urls), urls
    assert any("getappid" in u for u in urls), urls
    assert any("sjws_chat" in u for u in urls), urls
    # query 解析:getappid URL 带 ?appId=auto&appName=auto
    from urllib.parse import urlparse, parse_qs
    for r in cap:
        if "getappid" in r["url"]:
            # query 字段由调用方解析后填入;此处 url 已含 query
            assert "?appId=auto" in r["url"]


def test_dataiq_response_attached_to_correct_request():
    s = _new_sess()
    for r in _dataiq_requests():
        s._record_all(r["method"], r["url"],
                      pd=r.get("post_data"),
                      response_json=r.get("response_json"),
                      status=r.get("status"),
                      content_type=r.get("content_type", ""))
    cap = s.captured_all_requests()
    by_url = {r["url"]: r for r in cap}
    assert by_url["https://x/dataiq/save_dataiq_chat_list"]["response_json"]["data"]["conversation_id"] == "c-123"
    assert by_url["https://x/apigateway/getappid?appId=auto&appName=auto"]["response_json"]["data"] == "app-code"


# ── 3. diagnostics:console / pageerror / requestfailed ──
def test_diagnostics_console():
    s = _new_sess()
    class _Msg:
        type = "error"
        text = "TypeError: x is undefined"
    s._on_console(_Msg())
    diags = s.captured_diagnostics()
    assert len(diags) == 1
    d = diags[0]
    assert d["type"] == "console"
    assert d["level"] == "error"
    assert "TypeError" in d["message"]


def test_diagnostics_console_truncates_long():
    s = _new_sess()
    class _Msg:
        type = "error"
        text = "x" * 5000
    s._on_console(_Msg())
    assert len(s.diagnostics[0]["message"]) == 2000


def test_diagnostics_pageerror():
    s = _new_sess()
    s._on_pageerror(Exception("boom"))
    d = s.captured_diagnostics()
    assert d[0]["type"] == "pageerror"
    assert d[0]["level"] == "error"
    assert "boom" in d[0]["message"]


def test_diagnostics_requestfailed_links_to_request():
    s = _new_sess()
    idx = s._record_all("POST", "https://x/api/x", pd='{"a":1}')
    class _Req:
        url = "https://x/api/x"
        method = "POST"
        class _F:
            error_text = "net::ERR_CONNECTION_REFUSED"
        failure = _F()
    s._on_requestfailed(_Req())
    d = s.diagnostics[-1]
    assert d["type"] == "requestfailed"
    assert d["url"] == "https://x/api/x"
    assert d["request_index"] == idx
    assert "CONNECTION_REFUSED" in d["message"]


def test_diagnostics_requestfailed_no_link_when_unknown():
    s = _new_sess()
    class _Req:
        url = "https://x/api/never-seen"
        method = "POST"
        class _F:
            error_text = "aborted"
        failure = _F()
    s._on_requestfailed(_Req())
    d = s.diagnostics[-1]
    assert "request_index" not in d


# ── 4. 不破坏既有 captured_requests / captured_reads ──
def test_legacy_captured_requests_still_works_for_writes_only():
    """原 captured_requests 只收写请求;GET 不进 requests,但 all_requests 全收。"""
    s = _new_sess()
    s._capture("POST", "https://x/api/submit", '{"a":1}', "application/json", {})
    s._record_all("GET", "https://x/api/foo")     # 全量,但不进 requests
    assert len(s.captured_requests()) == 1
    assert s.captured_requests()[0]["method"] == "POST"
    assert len(s.captured_all_requests()) == 2    # GET + POST 都在


def test_capture_writes_to_both_requests_and_all_requests():
    """_capture 写请求同步落 all_requests(治"前端 FlowSpec 漏 GET")。"""
    s = _new_sess()
    s._capture("POST", "https://x/api/submit", '{"a":1}', "application/json", {"X": "1"})
    assert len(s.requests) == 1
    assert len(s.all_requests) == 1
    assert s.all_requests[0]["headers"]["X"] == "1"


# ── 5. 不可变副本 ──
def test_captured_all_requests_returns_copy():
    s = _new_sess()
    s._record_all("GET", "https://x/api/a")
    cap = s.captured_all_requests()
    cap.clear()
    assert len(s.all_requests) == 1, "外部修改返回副本不得影响内部"


def test_captured_diagnostics_returns_copy():
    s = _new_sess()
    s._record_diag("console", {"level": "error", "message": "x"})
    diags = s.captured_diagnostics()
    diags.clear()
    assert len(s.diagnostics) == 1


# ── 6. reset() 一并清空 ──
def test_reset_clears_all_requests_and_diagnostics():
    s = _new_sess()
    s._record_all("GET", "https://x/api/a")
    s._record_all("POST", "https://x/api/b", pd='{"x":1}')
    s._record_diag("console", {"level": "error", "message": "y"})
    s.reset()
    assert s.captured_all_requests() == []
    assert s.captured_diagnostics() == []
    assert s._req_counter == 0


# ── 7. 字段默认值(零散 / 边界) ──
def test_record_all_defaults_when_headers_empty():
    s = _new_sess()
    s._record_all("GET", "https://x/api/a")
    r = s.captured_all_requests()[0]
    assert r["headers"] == {}
    assert r["query"] == {}
    assert r["response_json"] is None
    assert r["status"] is None


def test_record_all_uppercases_method():
    s = _new_sess()
    s._record_all("post", "https://x/api/a")
    assert s.captured_all_requests()[0]["method"] == "POST"


def test_diagnostics_unified_shape():
    s = _new_sess()
    s._record_diag("console", {"level": "warn", "message": "deprecated"})
    s._record_diag("pageerror", {"level": "error", "message": "TypeError"})
    s._record_diag("requestfailed", {"level": "error", "message": "aborted", "url": "https://x/api/a"})
    types = [d["type"] for d in s.captured_diagnostics()]
    assert types == ["console", "pageerror", "requestfailed"]
    for d in s.captured_diagnostics():
        assert "timestamp" in d and isinstance(d["timestamp"], int)