"""Configurable login-based runtime token refresh."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx

from dano.infra import token_refresh as tr
from dano.infra import token_store
from dano.orchestrator import orchestrator as orchestrator_module
from dano.orchestrator.orchestrator import Orchestrator
from dano.shared.enums import Subsystem, TaskState


async def test_password_http_source_logs_in_and_preserves_existing_headers(monkeypatch) -> None:
    settings = SimpleNamespace(token_refresh_sources={
        "aaa/A-OA": [{
            "type": "password_http",
            "url": "https://oa.example.test/login",
            "body": {"tenant": "{{tenant}}", "username": "{{username}}", "password": "{{password}}"},
            "token_path": "data.accessToken",
            "verify_url": "https://oa.example.test/me",
            "interval_seconds": 1800,
        }],
    })
    monkeypatch.setattr(tr, "get_settings", lambda: settings)
    monkeypatch.setattr(tr, "resolve_credentials", lambda _refs: {
        "tenant": "example", "username": "admin", "password": "secret",
    })

    async def get_token(_tenant, _subsystem):
        return {"headers": {"Tenant-Id": "1"}, "updated_at": None}

    saved = {}

    async def update_token_headers(tenant, subsystem, headers, *, source):
        saved.update(tenant=tenant, subsystem=subsystem, headers=headers, source=source)
        return {"updated_at": "2026-07-22T00:00:00+00:00", "headers": headers}

    monkeypatch.setattr(tr, "get_token", get_token)
    monkeypatch.setattr(tr, "update_token_headers", update_token_headers)

    def login(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("/me"):
            assert request.headers["Authorization"] == "Bearer fresh-token"
            assert request.headers["Tenant-Id"] == "1"
            return httpx.Response(200, json={"id": 1})
        assert json.loads(request.content) == {
            "tenant": "example", "username": "admin", "password": "secret",
        }
        return httpx.Response(200, json={"data": {"accessToken": "fresh-token"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(login)) as client:
        result = await tr.refresh_one("aaa", "A-OA", force=True, client=client)

    assert result["ok"] is True
    assert saved["headers"] == {"Authorization": "Bearer fresh-token"}
    assert saved["source"] == "scheduled:password_http"


async def test_cookie_login_captures_all_cookies_and_verifies_them(monkeypatch) -> None:
    source = {
        "type": "password_http",
        "url": "https://oa.example.test/login",
        "body": {},
        "verify_url": "https://oa.example.test/me",
    }
    monkeypatch.setattr(
        tr, "get_settings", lambda: SimpleNamespace(token_refresh_sources={"aaa/A-OA": [source]}),
    )
    monkeypatch.setattr(tr, "resolve_credentials", lambda _refs: {})

    async def get_token(*_args, **_kwargs):
        return {"headers": {"Tenant-Id": "1", "Cookie": "old=1"}, "updated_at": None}

    saved = {}

    async def save(_tenant, _subsystem, headers, **_kwargs):
        saved.update(headers)
        return {"updated_at": "2026-07-22T00:00:00+00:00"}

    monkeypatch.setattr(tr, "get_token", get_token)
    monkeypatch.setattr(tr, "update_token_headers", save)

    def responses(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("/login"):
            return httpx.Response(200, headers=[
                ("Set-Cookie", "session=fresh; Path=/; HttpOnly"),
                ("Set-Cookie", "route=node-1; Path=/"),
            ])
        assert request.headers["Tenant-Id"] == "1"
        assert "session=fresh" in request.headers["Cookie"]
        assert "route=node-1" in request.headers["Cookie"]
        assert "old=1" not in request.headers["Cookie"]
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(responses)) as client:
        result = await tr.refresh_one("aaa", "A-OA", force=True, client=client)

    assert result["status"] == "refreshed"
    assert "session=fresh" in saved["Cookie"]
    assert "route=node-1" in saved["Cookie"]


async def test_refresh_failure_never_overwrites_current_token(monkeypatch) -> None:
    settings = SimpleNamespace(token_refresh_sources={
        "aaa/A-OA": [{
            "type": "password_http",
            "url": "https://oa.example.test/login",
            "body": {"username": "{{username}}", "password": "{{password}}"},
            "token_path": "data.accessToken",
        }],
    })
    monkeypatch.setattr(tr, "get_settings", lambda: settings)
    monkeypatch.setattr(tr, "resolve_credentials", lambda _refs: {"username": "admin", "password": "bad"})

    async def get_token(_tenant, _subsystem):
        return {"headers": {"Authorization": "Bearer old"}, "updated_at": None}

    async def forbidden_save(*_args, **_kwargs):
        raise AssertionError("failed refresh must not save")

    monkeypatch.setattr(tr, "get_token", get_token)
    monkeypatch.setattr(tr, "update_token_headers", forbidden_save)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(401, json={"msg": "bad credentials"})),
    ) as client:
        result = await tr.refresh_one("aaa", "A-OA", force=True, client=client)

    assert result["ok"] is False
    assert result["reason"] == "all_sources_failed"
    assert "old" not in str(result)


async def test_sources_fall_back_and_not_due_skips_http(monkeypatch) -> None:
    sources = [
        {"type": "password_http", "url": "https://oa.example.test/first", "body": {},
         "token_path": "data.accessToken", "interval_seconds": 600},
        {"type": "http", "url": "https://oa.example.test/second", "body": {},
         "token_path": "token", "allow_unverified_token": True},
    ]
    monkeypatch.setattr(
        tr, "get_settings", lambda: SimpleNamespace(token_refresh_sources={"aaa/A-OA": sources}),
    )
    monkeypatch.setattr(tr, "resolve_credentials", lambda _refs: {})
    updated = datetime.now(timezone.utc) - timedelta(seconds=601)

    async def get_token(_tenant, _subsystem):
        return {"headers": {}, "updated_at": updated.isoformat()}

    saved = []

    async def update(*_args, **kwargs):
        saved.append(kwargs)
        return {"updated_at": datetime.now(timezone.utc).isoformat()}

    monkeypatch.setattr(tr, "get_token", get_token)
    monkeypatch.setattr(tr, "update_token_headers", update)
    calls = []

    def responses(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if str(request.url).endswith("/first"):
            return httpx.Response(500)
        return httpx.Response(200, json={"token": "fallback"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(responses)) as client:
        result = await tr.refresh_one("aaa", "A-OA", client=client)
    assert result["status"] == "refreshed"
    assert len(calls) == 2
    assert saved[0]["source"] == "scheduled:http"

    async def fresh_token(_tenant, _subsystem):
        return {"headers": {}, "updated_at": datetime.now(timezone.utc).isoformat()}

    monkeypatch.setattr(tr, "get_token", fresh_token)
    calls.clear()
    async with httpx.AsyncClient(transport=httpx.MockTransport(responses)) as client:
        result = await tr.refresh_one("aaa", "A-OA", client=client)
    assert result["status"] == "not_due"
    assert calls == []


async def test_refresh_due_without_sources_is_failure(monkeypatch) -> None:
    monkeypatch.setattr(tr, "get_settings", lambda: SimpleNamespace(token_refresh_sources={}))
    result = await tr.refresh_due()
    assert result == {"ok": False, "total": 0, "refreshed": 0, "failed": 0, "results": []}


async def test_refresh_due_runs_systems_concurrently(monkeypatch) -> None:
    monkeypatch.setattr(
        tr,
        "get_settings",
        lambda: SimpleNamespace(token_refresh_sources={"a/one": [{}], "b/two": [{}]}),
    )
    active = 0
    maximum = 0

    async def refresh_one(tenant, subsystem, *, force=False):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        return {"ok": True, "tenant": tenant, "subsystem": subsystem, "status": "refreshed"}

    monkeypatch.setattr(tr, "refresh_one", refresh_one)
    result = await tr.refresh_due()
    assert result["ok"] is True
    assert result["refreshed"] == 2
    assert maximum == 2


async def test_field_option_source_exposes_401_for_refresh_retry(monkeypatch) -> None:
    from dano.execution.page import request_capture

    async def unauthorized(*_args, return_status=False, **_kwargs):
        assert return_status is True
        return None, 401

    monkeypatch.setattr(request_capture, "_get_json", unauthorized)
    result = await request_capture.fetch_field_options(
        {
            "auth_headers": {"Authorization": "Bearer old"},
            "selects": [{
                "param": "status",
                "source_url": "https://oa.example.test/options",
                "source_method": "GET",
                "label_key": "label",
                "value_key": "value",
            }],
        },
        "status",
    )
    assert result["status"] == 401


def test_auth_failure_detection_is_explicit() -> None:
    assert tr.is_auth_failure({"raw": {"status": 401}})
    assert not tr.is_auth_failure({"raw": {"step_result": {"response": {"code": 403}}}})
    assert not tr.is_auth_failure({"raw": {"status": 500, "detail": "business failed"}})


async def test_unverified_token_never_overwrites_current_token(monkeypatch) -> None:
    source = {
        "type": "password_http",
        "url": "https://oa.example.test/login",
        "body": {},
        "token_path": "token",
        "verify_url": "https://oa.example.test/me",
    }
    monkeypatch.setattr(
        tr, "get_settings", lambda: SimpleNamespace(token_refresh_sources={"aaa/A-OA": [source]}),
    )
    monkeypatch.setattr(tr, "resolve_credentials", lambda _refs: {})
    async def no_token(*_args, **_kwargs):
        return None

    monkeypatch.setattr(tr, "get_token", no_token)

    async def forbidden_save(*_args, **_kwargs):
        raise AssertionError("invalid token must not be saved")

    monkeypatch.setattr(tr, "update_token_headers", forbidden_save)

    def responses(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("/login"):
            return httpx.Response(200, json={"token": "invalid"})
        return httpx.Response(401)

    async with httpx.AsyncClient(transport=httpx.MockTransport(responses)) as client:
        result = await tr.refresh_one("aaa", "A-OA", force=True, client=client)
    assert result["ok"] is False
    assert result["errors"] == ["新 token 验证失败:HTTP 401"]


def test_network_errors_do_not_expose_credential_urls() -> None:
    request = httpx.Request("POST", "https://oa.example/login?password=secret")
    error = httpx.ConnectError("cannot connect", request=request)
    assert tr._safe_error(error) == "登录接口网络错误:ConnectError"
    assert "secret" not in tr._safe_error(error)


async def test_recorded_capability_refreshes_and_retries_auth_failure_once(monkeypatch) -> None:
    class Store:
        async def get(self, _asset_id):
            return SimpleNamespace(body={"api_request": {"auth_headers": {"Authorization": "Bearer old"}}})

        async def get_published(self, *_args, **_kwargs):
            return None

    orchestrator = object.__new__(Orchestrator)
    orchestrator.store = Store()
    skill = SimpleNamespace(
        recording_asset_id=uuid4(), subsystem=Subsystem.OA, skill_id="A-OA.query",
    )
    intent = SimpleNamespace(fields={})
    refreshed = False
    refresh_calls = []

    async def refresh_one(_tenant, _subsystem, *, force=False, **_kwargs):
        nonlocal refreshed
        refresh_calls.append(force)
        if force:
            refreshed = True
            return {"ok": True, "status": "refreshed"}
        return {"ok": True, "status": "not_due"}

    async def headers(_tenant, _subsystem):
        return {"Authorization": f"Bearer {'new' if refreshed else 'old'}"}

    calls = []

    async def invoke(**kwargs):
        calls.append(kwargs["api_request"]["auth_headers"]["Authorization"])
        if len(calls) == 1:
            return {"ok": False, "raw": {"status": 401}, "detail": "HTTP 401"}
        return {"ok": True, "raw": {"status": 200}, "response": {"ok": True}}

    from dano.execution.page import sessions
    monkeypatch.setattr(tr, "refresh_one", refresh_one)
    monkeypatch.setattr(token_store, "get_token_headers", headers)
    monkeypatch.setattr(orchestrator_module, "invoke_skill_capability", invoke)
    monkeypatch.setattr(sessions, "session_path_if_exists", lambda *_args: None)

    outcome = await orchestrator._run_recording_capability(
        uuid4(), skill, "query", intent, confirm=False, tenant="aaa",
    )
    assert outcome.state == TaskState.COMPLETED
    assert calls == ["Bearer old", "Bearer new"]
    assert refresh_calls == [False, True]


async def test_field_options_keep_original_401_when_refresh_raises(monkeypatch) -> None:
    class Store:
        async def get(self, _asset_id):
            return SimpleNamespace(body={"api_request": {"selects": []}})

        async def get_published(self, *_args, **_kwargs):
            return None

    skill = SimpleNamespace(
        recording_asset_id=uuid4(), subsystem=Subsystem.OA, skill_id="A-OA.query",
    )
    orchestrator = object.__new__(Orchestrator)
    orchestrator.store = Store()
    orchestrator.registry = SimpleNamespace(by_action=lambda *_args: skill)
    calls = 0

    async def refresh_one(*_args, force=False, **_kwargs):
        nonlocal calls
        calls += 1
        if force:
            raise RuntimeError("refresh unavailable")
        return {"ok": True, "status": "not_due"}

    async def unauthorized(*_args, **_kwargs):
        return {"field": "status", "options": [], "count": 0, "status": 401}

    async def no_headers(*_args, **_kwargs):
        return {}

    from dano.execution.page import request_capture, sessions
    monkeypatch.setattr(tr, "refresh_one", refresh_one)
    monkeypatch.setattr(request_capture, "fetch_field_options", unauthorized)
    monkeypatch.setattr(token_store, "get_token_headers", no_headers)
    monkeypatch.setattr(sessions, "session_path_if_exists", lambda *_args: None)

    result = await orchestrator.list_field_options(
        Subsystem.OA, "query", "status", tenant="aaa",
    )
    assert result["status"] == 401
    assert calls == 2
