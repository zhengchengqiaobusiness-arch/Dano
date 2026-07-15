"""Bounded response-body collection as independent immutable facts."""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from dano_recording.capture.ledger import FactLedger
from dano_recording.capture.redaction import RedactionPolicy
from dano_recording.domain.facts import FactKind, RecordingFact
from dano_recording.value_evidence import ValueEvidenceFactory


async def _resolve(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def _call_or_value(owner: Any, name: str, default: Any = None) -> Any:
    value = getattr(owner, name, default)
    if callable(value):
        value = value()
    return await _resolve(value)


@dataclass(frozen=True, slots=True)
class ResponseCollectorConfig:
    capture_body: bool = True
    max_body_bytes: int = 1_048_576
    text_content_types: tuple[str, ...] = (
        "application/json",
        "application/problem+json",
        "text/",
        "application/xml",
        "application/x-www-form-urlencoded",
    )

    def __post_init__(self) -> None:
        if self.max_body_bytes < 0:
            raise ValueError("max_body_bytes cannot be negative")


class ResponseCollector:
    def __init__(
        self,
        ledger: FactLedger,
        *,
        config: ResponseCollectorConfig | None = None,
        redaction: RedactionPolicy | None = None,
        value_evidence_factory: ValueEvidenceFactory | None = None,
        recording_lineage: str | None = None,
    ) -> None:
        self.ledger = ledger
        self.config = config or ResponseCollectorConfig()
        self.redaction = redaction or RedactionPolicy()
        if value_evidence_factory is not None and not str(recording_lineage or "").strip():
            raise ValueError("recording_lineage is required with value_evidence_factory")
        self.value_evidence_factory = value_evidence_factory
        self.recording_lineage = str(recording_lineage or "")

    async def collect(
        self,
        response: Any,
        *,
        request_id: str,
        page_id: str | None = None,
        action_id: str | None = None,
    ) -> RecordingFact:
        collection_errors: list[str] = []
        try:
            status = int(await _call_or_value(response, "status", 0) or 0)
        except Exception as exc:
            status = 0
            collection_errors.append(f"status: {exc}")
        try:
            raw_headers = await _call_or_value(response, "all_headers", None)
            if raw_headers is None:
                raw_headers = await _call_or_value(response, "headers", {})
        except Exception as exc:
            raw_headers = {}
            collection_errors.append(f"headers: {exc}")
        headers = self.redaction.redact_headers(dict(raw_headers or {}))
        content_type = next(
            (str(value) for key, value in headers.items() if key.lower() == "content-type"),
            "",
        )
        try:
            response_url = str(await _call_or_value(response, "url", ""))
        except Exception as exc:
            response_url = ""
            collection_errors.append(f"url: {exc}")
        try:
            status_text = str(await _call_or_value(response, "status_text", ""))
        except Exception as exc:
            status_text = ""
            collection_errors.append(f"status_text: {exc}")
        try:
            request = await _call_or_value(response, "request", None)
            resource_type = str(await _call_or_value(request, "resource_type", "")) if request else ""
        except Exception as exc:
            resource_type = ""
            collection_errors.append(f"resource_type: {exc}")
        payload: dict[str, Any] = {
            "request_id": request_id,
            "url": self.redaction.redact_url(response_url),
            "status": status,
            "status_text": self.redaction.redact_text(status_text),
            "headers": headers,
            "body_present": False,
            "body": None,
        }
        if collection_errors:
            payload["collection_errors"] = [
                self.redaction.redact_text(error) for error in collection_errors
            ]
        if self.config.capture_body and status not in {101, 204, 205, 304}:
            declared_length = next(
                (str(value) for key, value in headers.items() if key.lower() == "content-length"),
                "",
            )
            try:
                too_large = int(declared_length) > self.config.max_body_bytes
            except (TypeError, ValueError):
                too_large = False
            if too_large:
                payload["body_size"] = int(declared_length)
                payload["body_omitted_reason"] = "capacity"
            else:
                await self._add_body(
                    response,
                    content_type=content_type,
                    response_url=response_url,
                    resource_type=resource_type,
                    payload=payload,
                )
        return self.ledger.emit(
            RecordingFact,
            kind=FactKind.RESPONSE,
            action_id=action_id,
            page_id=page_id,
            payload=payload,
            redacted=True,
        )

    async def _add_body(
        self,
        response: Any,
        *,
        content_type: str,
        response_url: str,
        resource_type: str,
        payload: dict[str, Any],
    ) -> None:
        lowered = content_type.lower()
        path = urlsplit(response_url).path.lower()
        script_resource = resource_type.lower() in {"script", "worker", "sharedworker"}
        script_path = path.endswith((".js", ".mjs", ".cjs", ".jsx"))
        sourcemap_path = path.endswith((".map", ".js.map", ".mjs.map"))
        if (
            "javascript" in lowered
            or "ecmascript" in lowered
            or "source-map" in lowered
            or script_resource
            or script_path
            or sourcemap_path
        ):
            # Script bytes belong to the hashed script-evidence channel.  They
            # must not enter ordinary request facts that can be projected to Pi.
            payload["body_omitted_reason"] = (
                "sourcemap_evidence_channel" if sourcemap_path else "script_source_evidence_channel"
            )
            return
        if not any(token in content_type.lower() for token in self.config.text_content_types):
            payload["body_omitted_reason"] = "non_text_content_type"
            return
        try:
            raw = await _call_or_value(response, "body", b"")
        except Exception as exc:  # Playwright raises when a body is unavailable.
            payload["body_omitted_reason"] = "unavailable"
            payload["body_error"] = self.redaction.redact_text(str(exc))
            return
        if raw is None:
            return
        raw_bytes = raw if isinstance(raw, bytes) else str(raw).encode("utf-8", errors="replace")
        payload["body_size"] = len(raw_bytes)
        if len(raw_bytes) > self.config.max_body_bytes:
            payload["body_omitted_reason"] = "capacity"
            return
        if not raw_bytes:
            payload["body_present"] = True
            payload["body"] = ""
            return
        text = raw_bytes.decode("utf-8", errors="replace")
        if "json" in content_type.lower():
            try:
                parsed: Any = json.loads(text)
            except json.JSONDecodeError:
                parsed = text
        else:
            parsed = text
        if self.value_evidence_factory is not None:
            safe, evidence = self.value_evidence_factory.capture_tree(
                tenant_scope=self.ledger.tenant,
                recording_lineage=self.recording_lineage,
                value=parsed,
                root_path="response",
                field_name="response",
            )
            payload["body"] = safe
            if evidence:
                payload["response_value_evidence"] = [
                    item.model_dump(mode="json", exclude_none=True)
                    for item in evidence
                ]
        elif "json" in content_type.lower():
            payload["body"] = self.redaction.redact_value(parsed)
        else:
            payload["body"] = self.redaction.redact_text(str(parsed))
        payload["body_present"] = True
