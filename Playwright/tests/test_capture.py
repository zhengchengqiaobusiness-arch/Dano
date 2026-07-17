from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from dano_recording.capture.browser_session import (
    BrowserCapture,
    BrowserSessionManager,
    InvalidResumeToken,
)
from dano_recording.capture.ledger import FactLedger
from dano_recording.capture.ledger import CaptureCapacityExceeded
from dano_recording.capture.network_observer import NetworkObserver, NetworkObserverConfig
from dano_recording.capture.redaction import RedactionPolicy
from dano_recording.capture.response_collector import ResponseCollector
from dano_recording.capture.runtime import CaptureRuntime
from dano_recording.capture.safety import URLSafetyPolicy
from dano_recording.domain.facts import FactKind, RecordingFact, RequestFact


class FakeRequest:
    def __init__(self, method: str, url: str, post_data=None, headers=None, *, navigation=False) -> None:
        self.method = method
        self.url = url
        self.post_data = post_data
        self.headers = headers or {}
        self.resource_type = "fetch"
        self.frame = None
        self.failure = {"errorText": "blocked by safe_record"}
        self.navigation = navigation

    def is_navigation_request(self) -> bool:
        return self.navigation


class FakeRoute:
    def __init__(self, request=None) -> None:
        self.request = request
        self.aborted_with: str | None = None
        self.continued = False

    async def abort(self, reason: str) -> None:
        self.aborted_with = reason

    async def continue_(self) -> None:
        self.continued = True


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "DELETE"])
async def test_safe_record_keeps_bodyless_write_as_immutable_fact(method: str) -> None:
    ledger = FactLedger(tenant="tenant-a", recording_id="recording-a")
    observer = NetworkObserver(ledger)
    request = FakeRequest(method, "https://example.test/api/items/7")
    route = FakeRoute(request)

    observer.on_request(request)  # Real Playwright order: request event, then route callback.
    await observer.route(route)

    requests = [fact for fact in ledger.snapshot() if isinstance(fact, RequestFact)]
    assert len(requests) == 1
    assert requests[0].method == method
    assert requests[0].request_body is None
    assert requests[0].request_body_present is False
    assert requests[0].payload["intercepted"] is True
    assert route.aborted_with == "blockedbyclient"
    with pytest.raises(ValidationError):
        requests[0].method = "GET"


@pytest.mark.asyncio
async def test_safe_record_fails_closed_when_fact_capacity_is_exhausted() -> None:
    ledger = FactLedger(tenant="tenant-a", recording_id="recording-a", max_facts=1)
    ledger.emit(RecordingFact, kind=FactKind.DIAGNOSTIC, payload={"type": "prefill"})
    observer = NetworkObserver(ledger)
    request = FakeRequest("DELETE", "https://example.test/api/items/7")
    route = FakeRoute(request)

    with pytest.raises(CaptureCapacityExceeded):
        await observer.route(route)
    assert route.aborted_with == "blockedbyclient"


@pytest.mark.asyncio
async def test_safe_record_aborts_before_capacity_limited_diagnostic() -> None:
    ledger = FactLedger(tenant="tenant-a", recording_id="recording-a", max_facts=1)
    observer = NetworkObserver(ledger)
    request = FakeRequest("POST", "https://example.test/api/items")
    route = FakeRoute(request)
    observer.on_request(request)

    with pytest.raises(CaptureCapacityExceeded):
        await observer.route(route)
    assert route.aborted_with == "blockedbyclient"
    request_fact = ledger.snapshot()[0]
    assert isinstance(request_fact, RequestFact)
    assert request_fact.payload["intercepted"] is True


@pytest.mark.asyncio
async def test_cross_origin_fetch_is_captured_without_relaxing_navigation_allowlist() -> None:
    ledger = FactLedger(tenant="tenant-a", recording_id="recording-a")
    observer = NetworkObserver(
        ledger,
        config=NetworkObserverConfig(safe_record=False),
        url_policy=URLSafetyPolicy(allow_private_networks=True),
        navigation_url_policy=URLSafetyPolicy(
            allowed_hosts=("app.example.test",),
            allow_private_networks=True,
        ),
    )
    async def public_dns(_url, _policy):  # noqa: ANN001
        return None

    observer._validate_resolved_target = public_dns  # type: ignore[method-assign]  # noqa: SLF001
    fetch = FakeRequest("GET", "https://api.example.test/v1/options")
    fetch_route = FakeRoute(fetch)
    await observer.route(fetch_route)
    assert fetch_route.continued is True
    assert isinstance(ledger.snapshot()[0], RequestFact)
    assert ledger.snapshot()[0].payload["url_safe"] is True

    navigation = FakeRequest(
        "GET",
        "https://attacker.example.test/phish",
        navigation=True,
    )
    navigation_route = FakeRoute(navigation)
    await observer.route(navigation_route)
    assert navigation_route.aborted_with == "blockedbyclient"
    navigation_fact = next(
        fact for fact in ledger.snapshot()
        if isinstance(fact, RequestFact) and fact.url.endswith("/phish")
    )
    assert navigation_fact.payload["url_safe"] is False


@pytest.mark.asyncio
async def test_private_cross_origin_fetch_is_recorded_then_blocked() -> None:
    ledger = FactLedger(tenant="tenant-a", recording_id="recording-a")
    observer = NetworkObserver(
        ledger,
        config=NetworkObserverConfig(safe_record=False),
        url_policy=URLSafetyPolicy(allow_private_networks=False),
    )
    request = FakeRequest("GET", "http://127.0.0.1/internal")
    route = FakeRoute(request)
    await observer.route(route)
    assert route.aborted_with == "blockedbyclient"
    request_fact = next(fact for fact in ledger.snapshot() if isinstance(fact, RequestFact))
    assert request_fact.payload["url_safe"] is False


@pytest.mark.asyncio
async def test_declared_private_target_can_load_itself_but_not_other_private_hosts() -> None:
    ledger = FactLedger(tenant="tenant-a", recording_id="recording-a")
    observer = NetworkObserver(
        ledger,
        config=NetworkObserverConfig(safe_record=False),
        url_policy=URLSafetyPolicy(
            allow_private_networks=False,
            private_host_allowlist=("10.20.30.40",),
        ),
    )
    same_target = FakeRequest("GET", "http://10.20.30.40/api/options")
    same_route = FakeRoute(same_target)
    await observer.route(same_route)
    assert same_route.continued is True

    pivot = FakeRequest("GET", "http://169.254.169.254/latest/meta-data")
    pivot_route = FakeRoute(pivot)
    await observer.route(pivot_route)
    assert pivot_route.aborted_with == "blockedbyclient"


class EventSource:
    def __init__(self) -> None:
        self.handlers: dict[str, list] = {}

    def on(self, event: str, handler) -> None:
        self.handlers.setdefault(event, []).append(handler)

    def remove_listener(self, event: str, handler) -> None:
        listeners = self.handlers.get(event, [])
        if handler in listeners:
            listeners.remove(handler)

    def emit(self, event: str, *args) -> None:
        for handler in self.handlers.get(event, []):
            handler(*args)


class FakeFrame:
    def __init__(self, *, name: str, url: str, parent_frame=None) -> None:
        self.name = name
        self.url = url
        self.parent_frame = parent_frame


class FakePage(EventSource):
    def __init__(self, url: str, frames=()) -> None:
        super().__init__()
        self.url = url
        self.frames = list(frames)


class FakeContext(EventSource):
    def __init__(self, pages=()) -> None:
        super().__init__()
        self.pages = list(pages)


class RoutableContext(FakeContext):
    def __init__(self, pages=()) -> None:
        super().__init__(pages)
        self.routes: list[tuple[str, object]] = []
        self.bindings: dict[str, object] = {}
        self.init_scripts: list[str] = []

    async def route(self, pattern: str, handler) -> None:
        self.routes.append((pattern, handler))

    async def unroute(self, pattern: str, handler) -> None:
        self.routes.remove((pattern, handler))

    async def expose_binding(self, name: str, handler) -> None:
        if name in self.bindings:
            raise RuntimeError(f"binding {name} already exists")
        self.bindings[name] = handler

    async def add_init_script(self, *, path: str) -> None:
        self.init_scripts.append(path)


@pytest.mark.asyncio
async def test_capture_runtime_pause_resume_keeps_one_binding_and_network_listener() -> None:
    ledger = FactLedger(tenant="tenant-a", recording_id="recording-a")
    context = RoutableContext()
    runtime = CaptureRuntime(ledger)
    await runtime.attach(context)

    context.emit("request", FakeRequest("GET", "https://example.test/api/first"))
    assert len([fact for fact in ledger.snapshot() if isinstance(fact, RequestFact)]) == 1

    await runtime.pause()
    context.emit("request", FakeRequest("GET", "https://example.test/api/frozen"))
    assert len([fact for fact in ledger.snapshot() if isinstance(fact, RequestFact)]) == 1
    frozen_write = FakeRoute(FakeRequest("POST", "https://example.test/api/frozen-write"))
    await context.routes[0][1](frozen_write)
    assert frozen_write.aborted_with == "blockedbyclient"
    assert len([fact for fact in ledger.snapshot() if isinstance(fact, RequestFact)]) == 1

    await runtime.resume(context)
    assert tuple(context.bindings) == ("__danoRecordAction",)
    assert len(context.handlers["request"]) == 1
    assert len(context.routes) == 1
    context.emit("request", FakeRequest("GET", "https://example.test/api/recaptured"))
    assert [
        fact.url for fact in ledger.snapshot() if isinstance(fact, RequestFact)
    ] == [
        "https://example.test/api/first",
        "https://example.test/api/recaptured",
    ]
    await runtime.close()


@pytest.mark.asyncio
async def test_capture_runtime_pause_cancels_work_that_misses_the_drain_deadline(
    monkeypatch,
) -> None:
    ledger = FactLedger(tenant="tenant-a", recording_id="recording-a")
    runtime = CaptureRuntime(ledger)
    release = asyncio.Event()

    async def late_mutation() -> None:
        await release.wait()
        ledger.emit(
            RecordingFact,
            kind=FactKind.DOM_MUTATION,
            payload={"phase": "late"},
        )

    task = runtime.tasks.create(late_mutation())

    async def timed_out_drain(*, timeout=None) -> bool:
        assert timeout == 5.0
        return False

    monkeypatch.setattr(runtime.tasks, "drain", timed_out_drain)
    await runtime.pause()
    release.set()
    await asyncio.sleep(0)

    assert task.cancelled()
    assert not any(fact.kind is FactKind.DOM_MUTATION for fact in ledger.snapshot())
    await runtime.close()


@pytest.mark.asyncio
async def test_runtime_pause_gates_network_before_waiting_for_script_drain(
    monkeypatch,
) -> None:
    ledger = FactLedger(tenant="tenant-a", recording_id="recording-a")
    context = RoutableContext()
    runtime = CaptureRuntime(ledger)
    await runtime.attach(context)
    drain_started = asyncio.Event()
    release_drain = asyncio.Event()

    async def delayed_script_drain(*, timeout=None) -> bool:
        assert timeout == 5.0
        drain_started.set()
        await release_drain.wait()
        return True

    monkeypatch.setattr(runtime.scripts._tasks, "drain", delayed_script_drain)
    pause_task = asyncio.create_task(runtime.pause())
    await drain_started.wait()
    await asyncio.sleep(0)
    context.emit("request", FakeRequest("GET", "https://example.test/api/during-freeze"))
    release_drain.set()
    await pause_task

    assert not any(isinstance(fact, RequestFact) for fact in ledger.snapshot())
    await runtime.close()


@pytest.mark.asyncio
async def test_network_generation_reset_clears_request_identity_caches() -> None:
    ledger = FactLedger(tenant="tenant-a", recording_id="recording-a")
    observer = NetworkObserver(ledger)
    request = FakeRequest("GET", "https://example.test/api/old-generation")
    first = observer.record_request(request)

    await observer.pause()
    observer.reset_generation()
    assert not observer._request_ids
    assert not observer._request_facts
    assert not observer._request_page_ids
    assert not observer._request_action_ids
    assert not observer._failed_requests

    context = RoutableContext()
    await observer.resume(context)
    request.url = "https://example.test/api/current-generation"
    second = observer.record_request(request)

    assert second.request_id != first.request_id
    assert second.url.endswith("/current-generation")
    assert len([fact for fact in ledger.snapshot() if isinstance(fact, RequestFact)]) == 2
    await observer.close()


@pytest.mark.asyncio
async def test_runtime_generation_reset_reenumerates_only_current_page_scripts() -> None:
    class CdpSession(EventSource):
        def __init__(self, context) -> None:
            super().__init__()
            self.context = context
            self.detached = False

        async def send(self, method: str, params=None):
            if method == "Debugger.enable":
                for script_id, url, _source in self.context.current_scripts:
                    self.emit("Debugger.scriptParsed", {"scriptId": script_id, "url": url})
                return {}
            if method == "Debugger.getScriptSource":
                script_id = str((params or {}).get("scriptId") or "")
                source = next(
                    source
                    for current_id, _url, source in self.context.current_scripts
                    if current_id == script_id
                )
                return {"scriptSource": source}
            raise AssertionError(method)

        async def detach(self) -> None:
            self.detached = True

    class CdpContext(RoutableContext):
        def __init__(self, pages=()) -> None:
            super().__init__(pages)
            self.current_scripts = [
                ("old-script", "https://example.test/old.js", "const OLD_ONLY = true;")
            ]
            self.sessions: list[CdpSession] = []

        async def new_cdp_session(self, _page):
            session = CdpSession(self)
            self.sessions.append(session)
            return session

    ledger = FactLedger(tenant="tenant-a", recording_id="recording-a")
    page = FakePage("https://example.test/app")
    context = CdpContext([page])
    runtime = CaptureRuntime(ledger)
    await runtime.attach(context)
    await runtime.tasks.drain()
    await runtime.scripts._tasks.drain()
    assert [script.url for script in runtime.scripts.scripts] == [
        "https://example.test/old.js"
    ]

    await runtime.pause()
    await runtime.reset_generation()
    context.current_scripts = [
        ("current-script", "https://example.test/current.js", "const CURRENT_ONLY = true;")
    ]
    await runtime.resume(context)
    await runtime.scripts._tasks.drain()

    assert [script.url for script in runtime.scripts.scripts] == [
        "https://example.test/current.js"
    ]
    assert context.sessions[0].detached is True
    assert len(context.sessions) == 2
    assert tuple(context.bindings) == ("__danoRecordAction",)
    assert len(context.handlers["request"]) == 1
    assert len(context.routes) == 1
    await runtime.close()


def test_page_popup_and_frame_metadata_are_captured() -> None:
    ledger = FactLedger(tenant="tenant-a", recording_id="recording-a")
    capture = BrowserCapture(ledger)
    main_frame = FakeFrame(name="", url="https://example.test/root")
    child_frame = FakeFrame(
        name="details",
        url="https://example.test/frame?token=secret",
        parent_frame=main_frame,
    )
    page = FakePage("https://example.test/root", frames=[main_frame, child_frame])
    context = FakeContext([page])
    capture.attach_context(context)

    popup = FakePage("https://example.test/popup")
    page.emit("popup", popup)

    page_facts = [fact for fact in ledger.snapshot() if fact.kind is FactKind.PAGE]
    popup_facts = [fact for fact in page_facts if fact.payload.get("event") == "popup_opened"]
    frame_facts = [fact for fact in page_facts if fact.payload.get("event") == "frame_present"]
    assert len(popup_facts) == 1
    assert popup_facts[0].payload["opener_page_id"] == capture.page_id(page)
    assert popup_facts[0].page_id == capture.page_id(popup)
    assert len(frame_facts) == 2
    child = next(fact for fact in frame_facts if fact.payload["name"] == "details")
    assert child.payload["parent_frame_id"] == capture.frame_id(main_frame)
    assert "secret" not in child.payload["url"]


class ClosableContext:
    def __init__(self) -> None:
        self.closed = 0

    async def close(self) -> None:
        self.closed += 1


@pytest.mark.asyncio
async def test_browser_lease_survives_detach_and_validates_resume_token() -> None:
    manager = BrowserSessionManager(lease_seconds=30, cleanup_interval=30)
    context = ClosableContext()
    session, token = await manager.create(
        tenant="tenant-a",
        recording_id="recording-a",
        context=context,
    )
    lease = await manager.detach(tenant="tenant-a", recording_id="recording-a")
    assert lease.attached is False
    assert context.closed == 0
    reopened = await manager.open(
        tenant="tenant-a", recording_id="recording-a", resume_token=token
    )
    assert reopened is session
    assert reopened.attached is True
    await manager.detach(tenant="tenant-a", recording_id="recording-a")
    with pytest.raises(InvalidResumeToken):
        await manager.open(
            tenant="tenant-a", recording_id="recording-a", resume_token="wrong-token"
        )
    session.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert await manager.cleanup_expired() == 1
    assert context.closed == 1
    await manager.close()


def test_redaction_removes_headers_url_and_nested_body_secrets() -> None:
    policy = RedactionPolicy()
    assert policy.redact_headers({"Authorization": "Bearer abcdefghijkl", "X": "ok"}) == {
        "Authorization": "[REDACTED]",
        "X": "ok",
    }
    url = policy.redact_url("https://example.test/api?token=abc&name=visible")
    assert "abc" not in url
    assert "visible" in url
    body = policy.redact_body(
        '{"user":"alice","password":"secret","nested":{"access_token":"jwt"}}',
        "application/json",
    )
    assert body == {
        "user": "alice",
        "password": "[REDACTED]",
        "nested": {"access_token": "[REDACTED]"},
    }


def test_redaction_covers_session_csrf_ticket_form_and_multipart_aliases() -> None:
    policy = RedactionPolicy()
    secrets = {
        "jSessionId": "session-secret-1",
        "auth_cookie": "cookie-secret-2",
        "xsrf": "xsrf-secret-3",
        "csrf": "csrf-secret-4",
        "service_ticket": "ticket-secret-5",
    }
    headers = policy.redact_headers({
        "Cookie": "JSESSIONID=header-secret",
        "X-CSRF-Token": "csrf-header-secret",
    })
    url = policy.redact_url(
        "https://example.test/api?jsessionid=session-secret-1&service_ticket=ticket-secret-5"
    )
    json_body = policy.redact_body(secrets, "application/json")
    form_body = policy.redact_body(
        "jSessionId=session-secret-1&auth_cookie=cookie-secret-2&xsrf=xsrf-secret-3",
        "application/x-www-form-urlencoded",
    )
    boundary = "redaction-boundary"
    multipart = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="csrf"\r\n\r\n'
        "csrf-secret-4\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="service_ticket"\r\n\r\n'
        "ticket-secret-5\r\n"
        f"--{boundary}--\r\n"
    )
    multipart_body = policy.redact_body(
        multipart,
        f"multipart/form-data; boundary={boundary}",
    )

    encoded = repr({
        "headers": headers,
        "url": url,
        "json": json_body,
        "form": form_body,
        "multipart": multipart_body,
    })
    assert all(value not in encoded for value in [
        *secrets.values(),
        "header-secret",
        "csrf-header-secret",
    ])
    assert encoded.count("[REDACTED]") >= 10


def test_diagnostic_redaction_removes_email_phone_and_identity_ids() -> None:
    policy = RedactionPolicy()
    diagnostic = policy.redact_value({
        "message": (
            "login email alice@example.com phone +86 138-0013-8000 "
            "user_id=user-7788 tenantId:tenant-42"
        ),
        "context": {
            "email": "bob@example.com",
            "phone_number": "13900139000",
            "creator_id": "creator-99",
        },
    })

    encoded = repr(diagnostic)
    for plaintext in (
        "alice@example.com", "+86 138-0013-8000", "user-7788", "tenant-42",
        "bob@example.com", "13900139000", "creator-99",
    ):
        assert plaintext not in encoded
    assert encoded.count("[REDACTED]") >= 7


def test_redaction_preserves_canonical_uuid_while_still_redacting_phone() -> None:
    policy = RedactionPolicy()
    decision_uuid = "2458c918-0015-5322-8400-647f9ed27a7a"

    assert policy.redact_text(decision_uuid) == decision_uuid
    assert policy.redact_text(f"decision={decision_uuid}") == f"decision={decision_uuid}"
    assert policy.redact_text("phone=13800138000") == "phone=[REDACTED]"


def test_url_redaction_covers_pii_and_identity_path_segments_without_changing_routes() -> None:
    policy = RedactionPolicy()
    url = policy.redact_url(
        "https://example.test/api/users/user-7788/profile/alice%40example.com/13800138000"
    )

    assert "user-7788" not in url
    assert "alice" not in url
    assert "13800138000" not in url
    assert "/api/users/" in url and "/profile/" in url
    assert policy.redact_url("https://example.test/api/users/me/orders") == (
        "https://example.test/api/users/me/orders"
    )
    assert policy.redact_url("https://example.test/api/v1/orders") == (
        "https://example.test/api/v1/orders"
    )


def test_diagnostic_text_redacts_secret_assignments_and_query_strings() -> None:
    policy = RedactionPolicy()
    plaintext = {
        "access": "access-secret-123",
        "password": "password-secret-456",
        "cookie": "cookie-secret-789",
        "api_key": "api-secret-012",
        "basic": "YmFzaWMtc2VjcmV0",
    }
    message = (
        "request failed https://example.test/cb?access_token=" + plaintext["access"]
        + "&page=2 payload={\"password\":\"" + plaintext["password"]
        + "\",\"api_key\":\"" + plaintext["api_key"]
        + "\"} cookie=" + plaintext["cookie"]
        + " authorization: Basic " + plaintext["basic"]
        + " business_status=visible"
    )

    redacted = policy.redact_text(message)

    assert all(value not in redacted for value in plaintext.values())
    assert "page=2" in redacted
    assert "business_status=visible" in redacted
    assert redacted.count("[REDACTED]") >= 5


class FakeScriptResponse:
    status = 200
    status_text = "OK"
    url = "https://example.test/app.js"

    async def all_headers(self):
        return {"content-type": "text/plain", "content-length": "100"}

    async def body(self):
        raise AssertionError("raw JavaScript must use the script evidence channel")


@pytest.mark.asyncio
async def test_raw_javascript_response_body_never_enters_general_fact_projection() -> None:
    ledger = FactLedger(tenant="tenant-a", recording_id="recording-a")
    fact = await ResponseCollector(ledger).collect(
        FakeScriptResponse(),
        request_id="request-js",
    )
    assert fact.payload["body"] is None
    assert fact.payload["body_present"] is False
    assert fact.payload["body_omitted_reason"] == "script_source_evidence_channel"
