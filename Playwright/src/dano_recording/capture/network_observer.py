"""Lossless network facts and safe-record request interception."""

from __future__ import annotations

import inspect
import asyncio
import ipaddress
import json
import socket
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dano_recording.capture.action_transactions import current_action_id
from dano_recording.capture.diagnostics import DiagnosticsObserver
from dano_recording.capture.ledger import FactLedger
from dano_recording.capture.redaction import RedactionPolicy
from dano_recording.capture.response_collector import ResponseCollector, ResponseCollectorConfig
from dano_recording.capture.safety import URLSafetyPolicy, UnsafeURL
from dano_recording.capture.tasks import TaskSupervisor
from dano_recording.domain.facts import FactKind, RecordingFact, RequestFact
from dano_recording.value_evidence import ValueEvidence, ValueEvidenceFactory


def _property(owner: Any, name: str, default: Any = None) -> Any:
    try:
        value = getattr(owner, name, default)
        if callable(value):
            value = value()
        return value
    except Exception:
        return default


@dataclass(frozen=True, slots=True)
class NetworkObserverConfig:
    safe_record: bool = True
    write_methods: frozenset[str] = field(
        default_factory=lambda: frozenset({"POST", "PUT", "PATCH", "DELETE"})
    )
    route_pattern: str = "**/*"
    abort_error_code: str = "blockedbyclient"
    block_unsafe_urls: bool = True
    max_request_body_bytes: int = 1_048_576
    response: ResponseCollectorConfig = field(default_factory=ResponseCollectorConfig)

    def __post_init__(self) -> None:
        if self.max_request_body_bytes < 0:
            raise ValueError("max_request_body_bytes cannot be negative")


class NetworkObserver:
    """Records request/response/failure independently and never filters methods."""

    def __init__(
        self,
        ledger: FactLedger,
        *,
        config: NetworkObserverConfig | None = None,
        redaction: RedactionPolicy | None = None,
        url_policy: URLSafetyPolicy | None = None,
        navigation_url_policy: URLSafetyPolicy | None = None,
        action_id_provider: Callable[[], str | None] = current_action_id,
        page_id_resolver: Callable[[Any], str | None] | None = None,
        frame_id_resolver: Callable[[Any], str | None] | None = None,
        value_evidence_factory: ValueEvidenceFactory | None = None,
        recording_lineage: str | None = None,
        response_started: Callable[[Any, RequestFact], Any] | None = None,
        response_collected: Callable[[Any, RecordingFact], Any] | None = None,
    ) -> None:
        self.ledger = ledger
        self.config = config or NetworkObserverConfig()
        self.redaction = redaction or RedactionPolicy()
        self.url_policy = url_policy
        self.navigation_url_policy = navigation_url_policy or url_policy
        self.action_id_provider = action_id_provider
        self.page_id_resolver = page_id_resolver or (lambda _: None)
        self.frame_id_resolver = frame_id_resolver or (lambda _: None)
        if value_evidence_factory is not None and not str(recording_lineage or "").strip():
            raise ValueError("recording_lineage is required with value_evidence_factory")
        self.value_evidence_factory = value_evidence_factory
        self.recording_lineage = str(recording_lineage or "")
        self.response_started = response_started
        self.response_collected = response_collected
        self.responses = ResponseCollector(
            ledger,
            config=self.config.response,
            redaction=self.redaction,
            value_evidence_factory=value_evidence_factory,
            recording_lineage=recording_lineage,
        )
        self.diagnostics = DiagnosticsObserver(ledger, redaction=self.redaction)
        self.tasks = TaskSupervisor(self._task_error)
        self._request_ids: dict[int, str] = {}
        self._request_facts: dict[int, RequestFact] = {}
        self._request_page_ids: dict[int, str | None] = {}
        self._request_action_ids: dict[int, str | None] = {}
        self._failed_requests: set[int] = set()
        self._attached_contexts: dict[int, tuple[Any, Any, Any, Any, Any]] = {}

    def _capture_value(
        self,
        value: Any,
        *,
        path: str,
        field_name: str | None,
    ) -> tuple[Any, tuple[ValueEvidence, ...]]:
        if self.value_evidence_factory is None:
            return self.redaction.redact_value(value, key=field_name), ()
        return self.value_evidence_factory.capture_tree(
            tenant_scope=self.ledger.tenant,
            recording_lineage=self.recording_lineage,
            value=value,
            root_path=path,
            field_name=field_name,
        )

    @staticmethod
    def _parse_body(post_data: Any, content_type: str) -> Any:
        text = (
            post_data.decode("utf-8", errors="replace")
            if isinstance(post_data, bytes)
            else str(post_data)
        )
        lowered = content_type.casefold()
        if "json" in lowered:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
        if "application/x-www-form-urlencoded" in lowered:
            grouped: dict[str, list[str]] = {}
            for key, value in parse_qsl(text, keep_blank_values=True):
                grouped.setdefault(key, []).append(value)
            return {
                key: values[0] if len(values) == 1 else values
                for key, values in grouped.items()
            }
        return post_data

    def _safe_url(self, raw_url: str) -> tuple[str, tuple[ValueEvidence, ...]]:
        if self.value_evidence_factory is None:
            return self.redaction.redact_url(raw_url), ()
        parts = urlsplit(raw_url)
        safe_pairs: list[tuple[str, Any]] = []
        evidence: list[ValueEvidence] = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            safe, captured = self._capture_value(
                value,
                path=f"query.{key}",
                field_name=key,
            )
            safe_pairs.append((key, safe))
            evidence.extend(captured)
        safe_url = urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(safe_pairs, doseq=True), parts.fragment)
        )
        return self.redaction.redact_url(safe_url), tuple(evidence)

    async def attach(self, context: Any) -> None:
        context_key = id(context)
        if context_key in self._attached_contexts:
            return
        route_handler = self.route
        request_handler = self.on_request
        response_handler = self.on_response
        failure_handler = self.on_request_failed
        route_result = context.route(self.config.route_pattern, route_handler)
        if inspect.isawaitable(route_result):
            await route_result
        context.on("request", request_handler)
        context.on("response", response_handler)
        context.on("requestfailed", failure_handler)
        self._attached_contexts[context_key] = (
            context,
            route_handler,
            request_handler,
            response_handler,
            failure_handler,
        )

    def _request_context(self, request: Any) -> tuple[str | None, str | None, str | None]:
        frame = _property(request, "frame", None)
        page = _property(frame, "page", None) if frame is not None else None
        return (
            self.page_id_resolver(page),
            self.frame_id_resolver(frame),
            self.action_id_provider(),
        )

    def on_request(self, request: Any) -> RequestFact:
        return self.record_request(request)

    def record_request(self, request: Any, *, intercepted: bool = False) -> RequestFact:
        request_key = id(request)
        existing_id = self._request_ids.get(request_key)
        if existing_id is not None:
            existing = self._request_facts.get(request_key)
            if existing is None:
                raise RuntimeError("request correlation points to a missing fact")
            return existing.model_copy(deep=True)

        method = str(_property(request, "method", "GET") or "GET").upper()
        raw_url = str(_property(request, "url", "") or "")
        url, query_evidence = self._safe_url(raw_url)
        raw_headers = dict(_property(request, "headers", {}) or {})
        headers: dict[str, str] = {}
        header_evidence: list[ValueEvidence] = []
        for raw_key, raw_value in raw_headers.items():
            key = str(raw_key)
            safe, captured = self._capture_value(
                raw_value,
                path=f"header.{key}",
                field_name=key,
            )
            headers[key] = str(safe)
            header_evidence.extend(captured)
        headers = self.redaction.redact_headers(headers)
        content_type = next(
            (str(value) for key, value in headers.items() if key.lower() == "content-type"),
            "",
        )
        post_data = _property(request, "post_data", None)
        body_present = post_data is not None
        body: Any | None = None
        body_evidence: tuple[ValueEvidence, ...] = ()
        body_omitted_reason: str | None = None
        if body_present:
            raw_size = len(post_data) if isinstance(post_data, bytes) else len(str(post_data).encode("utf-8"))
            if raw_size <= self.config.max_request_body_bytes:
                if self.value_evidence_factory is not None:
                    body, body_evidence = self._capture_value(
                        self._parse_body(post_data, content_type),
                        path="body",
                        field_name="body",
                    )
                else:
                    body = self.redaction.redact_body(post_data, content_type)
            else:
                body_omitted_reason = "capacity"

        page_id, frame_id, action_id = self._request_context(request)
        expected_interception = self.config.safe_record and method in self.config.write_methods
        redirected_from = _property(request, "redirected_from", None)
        service_worker = _property(request, "service_worker", None)
        payload: dict[str, Any] = {
            "frame_id": frame_id,
            "is_navigation_request": bool(_property(request, "is_navigation_request", False)),
            # Playwright emits ``request`` before invoking the route handler.
            # Since safe_record routing is installed before listeners, this is
            # a deterministic execution-policy fact, not a later mutation.
            "intercepted": bool(intercepted or expected_interception),
            "timing": self.redaction.redact_value(_property(request, "timing", {}) or {}),
            "redirected_from_request_id": self._request_ids.get(id(redirected_from))
            if redirected_from is not None
            else None,
            "redirected_from_url": self.redaction.redact_url(
                str(_property(redirected_from, "url", "") or "")
            )
            if redirected_from is not None
            else None,
            "service_worker_url": self.redaction.redact_url(
                str(_property(service_worker, "url", "") or "")
            )
            if service_worker is not None
            else None,
        }
        if body_omitted_reason:
            payload["request_body_omitted_reason"] = body_omitted_reason
        captured_values = (*query_evidence, *header_evidence, *body_evidence)
        if captured_values:
            payload["request_value_evidence"] = [
                item.model_dump(mode="json", exclude_none=True)
                for item in captured_values
            ]
        is_navigation = bool(_property(request, "is_navigation_request", False))
        policy = self.navigation_url_policy if is_navigation else self.url_policy
        if policy is not None:
            try:
                policy.validate(raw_url)
                payload["url_safe"] = True
            except UnsafeURL as exc:
                payload["url_safe"] = False
                payload["url_safety_reason"] = self.redaction.redact_text(str(exc))
                if self.config.block_unsafe_urls:
                    payload["intercepted"] = True
        fact = self.ledger.emit(
            RequestFact,
            action_id=action_id,
            page_id=page_id,
            method=method,
            url=url,
            resource_type=str(_property(request, "resource_type", "fetch") or "fetch"),
            request_headers=headers,
            request_body=body,
            request_body_present=body_present,
            payload=payload,
            redacted=True,
        )
        self._request_ids[request_key] = fact.request_id
        self._request_facts[request_key] = fact.model_copy(deep=True)
        self._request_page_ids[request_key] = page_id
        self._request_action_ids[request_key] = action_id
        return fact

    def on_response(self, response: Any) -> None:
        request = _property(response, "request", None)
        if request is None:
            self.diagnostics.emit("response_without_request")
            return
        request_fact = self.record_request(request)
        request_key = id(request)
        self.tasks.create(
            self._collect_response(response, request_fact=request_fact, request_key=request_key)
        )

    async def _collect_response(
        self,
        response: Any,
        *,
        request_fact: RequestFact,
        request_key: int,
    ) -> RecordingFact:
        if self.response_started is not None:
            started = self.response_started(response, request_fact)
            if inspect.isawaitable(started):
                await started
        fact = await self.responses.collect(
            response,
            request_id=request_fact.request_id,
            page_id=self._request_page_ids.get(request_key),
            action_id=self._request_action_ids.get(request_key),
        )
        if self.response_collected is not None:
            completed = self.response_collected(response, fact)
            if inspect.isawaitable(completed):
                await completed
        return fact

    def on_request_failed(self, request: Any) -> RecordingFact | None:
        request_key = id(request)
        if request_key in self._failed_requests:
            return None
        request_fact = self.record_request(request)
        failure = _property(request, "failure", None)
        if isinstance(failure, dict):
            error_text = failure.get("errorText") or failure.get("error_text") or str(failure)
        else:
            error_text = failure or "request failed"
        fact = self.ledger.emit(
            RecordingFact,
            kind=FactKind.REQUEST_FAILED,
            action_id=self._request_action_ids.get(request_key),
            page_id=self._request_page_ids.get(request_key),
            payload={
                "request_id": request_fact.request_id,
                "method": request_fact.method,
                "url": request_fact.url,
                "reason": self.redaction.redact_text(str(error_text)),
            },
            redacted=True,
        )
        self._failed_requests.add(request_key)
        return fact

    async def route(self, route: Any, request: Any | None = None) -> None:
        # Playwright Python passes only Route; accepting an explicit request as
        # well keeps the pure unit-test boundary simple.
        request = request if request is not None else _property(route, "request", None)
        if request is None:
            raise RuntimeError("route callback did not provide a request")
        method = str(_property(request, "method", "GET") or "GET").upper()
        should_block = self.config.safe_record and method in self.config.write_methods
        is_navigation = bool(_property(request, "is_navigation_request", False))
        policy = self.navigation_url_policy if is_navigation else self.url_policy
        try:
            request_fact = self.record_request(request, intercepted=should_block)
        except Exception:
            # A storage/capacity failure must not turn safe_record into a live
            # write.  Abort first, then surface the capture failure loudly.
            if should_block:
                result = getattr(route, "abort")(self.config.abort_error_code)
                if inspect.isawaitable(result):
                    await result
            raise
        unsafe_url = (
            self.config.block_unsafe_urls
            and request_fact.payload.get("url_safe") is False
        )
        resolved_unsafe_reason = ""
        if not unsafe_url and self.config.block_unsafe_urls and policy is not None:
            try:
                await self._validate_resolved_target(str(_property(request, "url", "") or ""), policy)
            except UnsafeURL as exc:
                unsafe_url = True
                resolved_unsafe_reason = self.redaction.redact_text(str(exc))
        should_block = should_block or unsafe_url
        if should_block:
            abort = getattr(route, "abort")
            result = abort(self.config.abort_error_code)
            if inspect.isawaitable(result):
                await result
            # Policy enforcement is complete before any optional diagnostic
            # append, so a full/failing ledger can never turn into a live write.
            self.diagnostics.emit(
                "unsafe_url_blocked" if unsafe_url else "safe_record_blocked",
                page_id=request_fact.page_id,
                request_id=request_fact.request_id,
                method=method,
                url=request_fact.url,
                reason=resolved_unsafe_reason or None,
            )
            return
        else:
            continue_method = getattr(route, "continue_", None) or getattr(route, "fallback")
            result = continue_method()
        if inspect.isawaitable(result):
            await result

    async def _validate_resolved_target(self, url: str, policy: URLSafetyPolicy) -> None:
        """Apply the private-network boundary to DNS answers as well as literals."""

        validated = policy.validate(url)
        parts = urlsplit(validated)
        hostname = parts.hostname or ""
        try:
            # Literal addresses are already checked by ``validate`` and need no DNS lookup.
            ipaddress.ip_address(hostname.strip("[]"))
            return
        except ValueError:
            pass
        port = parts.port or (443 if parts.scheme in {"https", "wss"} else 80)
        loop = asyncio.get_running_loop()
        try:
            rows = await loop.getaddrinfo(
                hostname,
                port,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise UnsafeURL("URL host did not resolve safely") from exc
        addresses = tuple(dict.fromkeys(str(row[4][0]) for row in rows if row[4]))
        policy.validate_resolved_addresses(addresses, hostname=hostname)

    def _task_error(self, error: BaseException) -> None:
        self.diagnostics.emit(
            "network_capture_error",
            error_type=type(error).__name__,
            message=str(error),
        )

    async def close(self) -> None:
        bindings, self._attached_contexts = tuple(self._attached_contexts.values()), {}
        for context, route_handler, request_handler, response_handler, failure_handler in bindings:
            remove = getattr(context, "remove_listener", None)
            if remove is not None:
                for event, handler in (
                    ("request", request_handler),
                    ("response", response_handler),
                    ("requestfailed", failure_handler),
                ):
                    try:
                        remove(event, handler)
                    except Exception:
                        pass
            unroute = getattr(context, "unroute", None)
            if unroute is not None:
                try:
                    result = unroute(self.config.route_pattern, route_handler)
                    if inspect.isawaitable(result):
                        await result
                except Exception as exc:
                    self._task_error(exc)
        # Drain response bodies before stopping; only explicit shutdown cancels
        # work that can no longer be persisted.
        drained = await self.tasks.drain(timeout=5.0)
        await self.tasks.close(cancel=not drained)
        await self.diagnostics.close()
