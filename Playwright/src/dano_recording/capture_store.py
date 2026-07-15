"""Complete, lossless-by-observation capture storage for recording V3.

Request *definitions* are deduplicated by method, normalized path and schemas;
individual calls are never deduplicated.  Raw values do not cross this module's
public boundary: network payloads are represented by :class:`ValueEvidence`.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from enum import StrEnum
from threading import RLock
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid5

from pydantic import Field, field_validator, model_validator

from dano_recording.capture.redaction import RedactionPolicy
from dano_recording.domain._base import FrozenModel, freeze_json, new_id, utc_now
from dano_recording.value_evidence import ValueEvidence


_UUID_SEGMENT = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_INTEGER_SEGMENT = re.compile(r"^-?[0-9]+$")
_CREDENTIAL_COLLECTION_KEYS = {
    "credentials",
    "cookies",
    "secrets",
    "tokens",
    "auth_headers",
}


def normalize_request_path(url_or_path: str) -> str:
    """Normalize observed record identifiers without discarding route shape."""

    parsed = urlsplit(url_or_path)
    path = parsed.path if parsed.scheme or parsed.netloc else url_or_path.split("?", 1)[0]
    segments: list[str] = []
    for raw in path.split("/"):
        segment = unquote(raw)
        if _UUID_SEGMENT.fullmatch(segment):
            segments.append("{uuid}")
        elif _INTEGER_SEGMENT.fullmatch(segment):
            segments.append("{id}")
        else:
            segments.append(segment)
    normalized = "/".join(segments)
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized or "/"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def _sanitize_schema(
    value: Any,
    redaction: RedactionPolicy,
    *,
    property_name: str | None = None,
) -> Any:
    """Keep structural schema information while removing embedded samples.

    Captured OpenAPI/JSON-schema fragments often contain ``example`` or
    ``default`` values.  Those are observations, not schema, and may contain
    credentials.  Enum domains remain available for non-sensitive fields.
    """

    if isinstance(value, dict):
        output: dict[str, Any] = {}
        normalized_property = (property_name or "").strip().lower().replace("-", "_")
        sensitive_property = bool(
            normalized_property
            and (
                redaction.is_sensitive_key(normalized_property)
                or normalized_property in _CREDENTIAL_COLLECTION_KEYS
            )
        )
        for raw_key, item in value.items():
            key = str(raw_key)
            lowered = key.lower()
            if lowered in {"example", "examples", "default", "const", "sample", "samples"}:
                output[key] = "[REDACTED:SCHEMA_SAMPLE]"
                continue
            if lowered == "properties" and isinstance(item, dict):
                output[key] = {
                    str(name): _sanitize_schema(child, redaction, property_name=str(name))
                    for name, child in item.items()
                }
                continue
            if lowered == "enum" and sensitive_property:
                output[key] = ["[REDACTED:SCHEMA_ENUM]"]
                continue
            output[key] = _sanitize_schema(item, redaction, property_name=property_name)
        return output
    if isinstance(value, list | tuple):
        return [_sanitize_schema(item, redaction, property_name=property_name) for item in value]
    if isinstance(value, bytes):
        return redaction.redact_text(value.decode("utf-8", errors="replace"))
    if isinstance(value, str):
        return redaction.redact_text(value)
    return value


def request_definition_fingerprint(
    *,
    method: str,
    normalized_path: str,
    request_schema: dict[str, Any],
    response_schema: dict[str, Any],
) -> str:
    material = _canonical_json(
        {
            "method": method.strip().upper(),
            "normalized_path": normalized_path,
            "request_schema": request_schema,
            "response_schema": response_schema,
        }
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


class CaptureRecordKind(StrEnum):
    ACTION = "action"
    DOM = "dom"
    RESPONSE = "response"
    MUTATION = "mutation"
    SUBMIT = "submit"


class RequestDefinition(FrozenModel):
    request_definition_id: UUID
    method: str
    normalized_path: str
    request_schema: dict[str, Any]
    response_schema: dict[str, Any]
    fingerprint: str

    @field_validator("method")
    @classmethod
    def _method_upper(cls, value: str) -> str:
        value = value.strip().upper()
        if not value:
            raise ValueError("method must not be blank")
        return value

    @model_validator(mode="after")
    def _validate_definition(self) -> "RequestDefinition":
        object.__setattr__(self, "request_schema", freeze_json(self.request_schema))
        object.__setattr__(self, "response_schema", freeze_json(self.response_schema))
        expected = request_definition_fingerprint(
            method=self.method,
            normalized_path=self.normalized_path,
            request_schema=self.request_schema,
            response_schema=self.response_schema,
        )
        if self.fingerprint != expected:
            raise ValueError("request definition fingerprint does not match its contract")
        return self


class NetworkObservation(FrozenModel):
    observation_id: str = Field(default_factory=new_id)
    request_definition_id: UUID
    page_id: str
    frame_id: str | None = None
    action_id: str | None = None
    started_at: datetime
    finished_at: datetime
    initiator: dict[str, Any] = Field(default_factory=dict)
    request_schema: dict[str, Any]
    response_schema: dict[str, Any]
    request_values: tuple[ValueEvidence, ...] = ()
    response_values: tuple[ValueEvidence, ...] = ()
    status: int = Field(ge=0, le=599)
    business_request: bool = True

    @model_validator(mode="after")
    def _validate_observation(self) -> "NetworkObservation":
        if self.started_at.tzinfo is None:
            object.__setattr__(self, "started_at", self.started_at.replace(tzinfo=timezone.utc))
        if self.finished_at.tzinfo is None:
            object.__setattr__(self, "finished_at", self.finished_at.replace(tzinfo=timezone.utc))
        if self.finished_at < self.started_at:
            raise ValueError("finished_at cannot precede started_at")
        object.__setattr__(self, "initiator", freeze_json(self.initiator))
        object.__setattr__(self, "request_schema", freeze_json(self.request_schema))
        object.__setattr__(self, "response_schema", freeze_json(self.response_schema))
        return self


class CaptureRecord(FrozenModel):
    record_id: str = Field(default_factory=new_id)
    kind: CaptureRecordKind
    page_id: str
    frame_id: str | None = None
    action_id: str | None = None
    observed_at: datetime = Field(default_factory=utc_now)
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _freeze_payload(self) -> "CaptureRecord":
        object.__setattr__(self, "payload", freeze_json(self.payload))
        return self


class ScriptArtifact(FrozenModel):
    content_hash: str
    urls: tuple[str, ...]
    size: int = Field(ge=0)
    page_ids: tuple[str, ...] = ()
    truncated: bool = False
    evidence_ids: tuple[str, ...] = ()
    # Opaque content-addressed handle.  It is never a filesystem path and raw
    # script bytes remain outside snapshots/Pi projections.
    artifact_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    analysis: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _freeze_analysis(self) -> "ScriptArtifact":
        object.__setattr__(self, "analysis", freeze_json(self.analysis))
        object.__setattr__(self, "metadata", freeze_json(self.metadata))
        return self


class ResourceIndex(FrozenModel):
    url: str
    resource_type: str
    content_hash: str | None = None
    size: int | None = Field(default=None, ge=0)


class CaptureStoreSnapshot(FrozenModel):
    tenant_scope: str
    recording_id: str
    lineage_id: UUID
    capture_generation: int = Field(ge=0)
    request_definitions: tuple[RequestDefinition, ...]
    observations: tuple[NetworkObservation, ...]
    unbound_business_requests: tuple[str, ...]
    records: tuple[CaptureRecord, ...] = ()
    scripts: tuple[ScriptArtifact, ...] = ()
    resources: tuple[ResourceIndex, ...] = ()


class CaptureStore:
    """Recording-generation-scoped evidence owner."""

    def __init__(
        self,
        *,
        tenant_scope: str,
        recording_id: str,
        lineage_id: UUID | str,
        capture_generation: int = 0,
        redaction: RedactionPolicy | None = None,
    ) -> None:
        if not tenant_scope.strip() or not recording_id.strip():
            raise ValueError("tenant_scope and recording_id must not be blank")
        if capture_generation < 0:
            raise ValueError("capture_generation cannot be negative")
        self.tenant_scope = tenant_scope
        self.recording_id = recording_id
        self.lineage_id = UUID(str(lineage_id))
        self.capture_generation = capture_generation
        self._redaction = redaction or RedactionPolicy()
        self._definitions_by_fingerprint: dict[str, RequestDefinition] = {}
        self._definitions_by_id: dict[UUID, RequestDefinition] = {}
        self._observations: dict[str, NetworkObservation] = {}
        self._unbound: set[str] = set()
        self._records: dict[str, CaptureRecord] = {}
        self._script_content: dict[str, bytes] = {}
        self._scripts: dict[str, ScriptArtifact] = {}
        self._resources: dict[tuple[str, str], ResourceIndex] = {}
        self._lock = RLock()

    def register_request_definition(
        self,
        *,
        method: str,
        url_or_path: str,
        request_schema: dict[str, Any],
        response_schema: dict[str, Any],
    ) -> RequestDefinition:
        method = method.strip().upper()
        normalized_path = normalize_request_path(url_or_path)
        request_schema = _sanitize_schema(request_schema, self._redaction)
        response_schema = _sanitize_schema(response_schema, self._redaction)
        fingerprint = request_definition_fingerprint(
            method=method,
            normalized_path=normalized_path,
            request_schema=request_schema,
            response_schema=response_schema,
        )
        with self._lock:
            existing = self._definitions_by_fingerprint.get(fingerprint)
            if existing is not None:
                return existing.model_copy(deep=True)
            definition = RequestDefinition(
                request_definition_id=uuid5(self.lineage_id, fingerprint),
                method=method,
                normalized_path=normalized_path,
                request_schema=request_schema,
                response_schema=response_schema,
                fingerprint=fingerprint,
            )
            self._definitions_by_fingerprint[fingerprint] = definition
            self._definitions_by_id[definition.request_definition_id] = definition
            return definition.model_copy(deep=True)

    def append_observation(self, observation: NetworkObservation) -> NetworkObservation:
        with self._lock:
            definition = self._definitions_by_id.get(observation.request_definition_id)
            if definition is None:
                raise ValueError("observation references an unknown request definition")
            if (
                definition.request_schema != observation.request_schema
                or definition.response_schema != observation.response_schema
            ):
                raise ValueError("observation schemas do not match its request definition")
            existing = self._observations.get(observation.observation_id)
            if existing is not None:
                if existing != observation:
                    raise ValueError("network observations are immutable")
                return existing.model_copy(deep=True)
            self._observations[observation.observation_id] = observation.model_copy(deep=True)
            if observation.business_request:
                self._unbound.add(observation.observation_id)
            return observation.model_copy(deep=True)

    def record_network_call(
        self,
        *,
        method: str,
        url_or_path: str,
        page_id: str,
        started_at: datetime,
        finished_at: datetime,
        status: int,
        request_schema: dict[str, Any],
        response_schema: dict[str, Any],
        request_values: Iterable[ValueEvidence] = (),
        response_values: Iterable[ValueEvidence] = (),
        initiator: dict[str, Any] | None = None,
        frame_id: str | None = None,
        action_id: str | None = None,
        business_request: bool = True,
        observation_id: str | None = None,
    ) -> NetworkObservation:
        definition = self.register_request_definition(
            method=method,
            url_or_path=url_or_path,
            request_schema=request_schema,
            response_schema=response_schema,
        )
        safe_request_schema = _sanitize_schema(request_schema, self._redaction)
        safe_response_schema = _sanitize_schema(response_schema, self._redaction)
        observation = NetworkObservation(
            observation_id=observation_id or new_id(),
            request_definition_id=definition.request_definition_id,
            page_id=page_id,
            frame_id=frame_id,
            action_id=action_id,
            started_at=started_at,
            finished_at=finished_at,
            initiator=self._redaction.redact_value(initiator or {}),
            request_schema=safe_request_schema,
            response_schema=safe_response_schema,
            request_values=tuple(item.model_copy(deep=True) for item in request_values),
            response_values=tuple(item.model_copy(deep=True) for item in response_values),
            status=status,
            business_request=business_request,
        )
        return self.append_observation(observation)

    def bind_observation(self, observation_id: str) -> None:
        with self._lock:
            if observation_id not in self._observations:
                raise KeyError(observation_id)
            self._unbound.discard(observation_id)

    def list_unbound_business_requests(self) -> tuple[NetworkObservation, ...]:
        with self._lock:
            return tuple(
                self._observations[item].model_copy(deep=True)
                for item in sorted(self._unbound)
            )

    def append_record(
        self,
        *,
        kind: CaptureRecordKind | str,
        page_id: str,
        payload: dict[str, Any],
        frame_id: str | None = None,
        action_id: str | None = None,
        observed_at: datetime | None = None,
        record_id: str | None = None,
    ) -> CaptureRecord:
        record = CaptureRecord(
            record_id=record_id or new_id(),
            kind=CaptureRecordKind(kind),
            page_id=page_id,
            frame_id=frame_id,
            action_id=action_id,
            observed_at=observed_at or datetime.now(timezone.utc),
            payload=self._redaction.redact_value(payload),
        )
        with self._lock:
            existing = self._records.get(record.record_id)
            if existing is not None and existing != record:
                raise ValueError("capture records are immutable")
            self._records[record.record_id] = record.model_copy(deep=True)
            return record.model_copy(deep=True)

    def record_script(
        self,
        *,
        url: str,
        content: bytes | str,
        analysis: dict[str, Any] | None = None,
        page_id: str | None = None,
        truncated: bool = False,
        evidence_ids: Iterable[str] = (),
        artifact_ref: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ScriptArtifact:
        raw = content if isinstance(content, bytes) else content.encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        safe_url = self._redaction.redact_url(url)
        with self._lock:
            existing = self._scripts.get(digest)
            urls = tuple(sorted(set((*existing.urls, safe_url)))) if existing else (safe_url,)
            page_ids = tuple(
                sorted(
                    set(
                        (*existing.page_ids, *((page_id,) if page_id else ()))
                        if existing else ((page_id,) if page_id else ())
                    )
                )
            )
            merged_analysis = dict(existing.analysis) if existing else {}
            merged_analysis.update(self._redaction.redact_value(analysis or {}))
            merged_metadata = dict(existing.metadata) if existing else {}
            merged_metadata.update(self._redaction.redact_value(metadata or {}))
            artifact = ScriptArtifact(
                content_hash=digest,
                urls=urls,
                size=len(raw),
                page_ids=page_ids,
                truncated=bool(truncated or (existing.truncated if existing else False)),
                evidence_ids=tuple(
                    sorted(
                        set(
                            (*existing.evidence_ids, *tuple(str(item) for item in evidence_ids))
                            if existing else tuple(str(item) for item in evidence_ids)
                        )
                    )
                ),
                artifact_ref=artifact_ref or (existing.artifact_ref if existing else None),
                metadata=merged_metadata,
                analysis=merged_analysis,
            )
            self._script_content.setdefault(digest, raw)
            self._scripts[digest] = artifact
            return artifact.model_copy(deep=True)

    def restore_script_content(self, content_hash: str, content: bytes) -> None:
        """Restore local-only bytes after validating the content address."""

        if hashlib.sha256(content).hexdigest() != content_hash:
            raise ValueError("restored script bytes do not match content_hash")
        with self._lock:
            if content_hash not in self._scripts:
                raise KeyError(content_hash)
            self._script_content[content_hash] = bytes(content)

    def get_script_content(self, content_hash: str) -> bytes:
        """Return local analysis bytes; snapshots/Pi projections omit them."""

        with self._lock:
            return bytes(self._script_content[content_hash])

    def index_resource(
        self,
        *,
        url: str,
        resource_type: str,
        content_hash: str | None = None,
        size: int | None = None,
    ) -> ResourceIndex:
        item = ResourceIndex(
            url=self._redaction.redact_url(url),
            resource_type=resource_type,
            content_hash=content_hash,
            size=size,
        )
        with self._lock:
            self._resources[(item.resource_type, item.url)] = item
            return item.model_copy(deep=True)

    def snapshot(self) -> CaptureStoreSnapshot:
        """Return a Pi-safe snapshot; plaintext script bodies are intentionally absent."""

        with self._lock:
            return CaptureStoreSnapshot(
                tenant_scope=self.tenant_scope,
                recording_id=self.recording_id,
                lineage_id=self.lineage_id,
                capture_generation=self.capture_generation,
                request_definitions=tuple(
                    item.model_copy(deep=True)
                    for item in sorted(
                        self._definitions_by_id.values(),
                        key=lambda value: str(value.request_definition_id),
                    )
                ),
                observations=tuple(
                    item.model_copy(deep=True)
                    for item in sorted(
                        self._observations.values(),
                        key=lambda value: (value.started_at, value.observation_id),
                    )
                ),
                unbound_business_requests=tuple(sorted(self._unbound)),
                records=tuple(
                    item.model_copy(deep=True)
                    for item in sorted(
                        self._records.values(),
                        key=lambda value: (value.observed_at, value.record_id),
                    )
                ),
                scripts=tuple(
                    item.model_copy(deep=True)
                    for item in sorted(self._scripts.values(), key=lambda value: value.content_hash)
                ),
                resources=tuple(
                    item.model_copy(deep=True)
                    for item in sorted(
                        self._resources.values(),
                        key=lambda value: (value.resource_type, value.url),
                    )
                ),
            )

    def next_generation(self) -> "CaptureStore":
        """Create an empty store for a true re-capture of the same lineage."""

        return CaptureStore(
            tenant_scope=self.tenant_scope,
            recording_id=self.recording_id,
            lineage_id=self.lineage_id,
            capture_generation=self.capture_generation + 1,
            redaction=self._redaction,
        )

    @classmethod
    def from_snapshot(
        cls,
        snapshot: CaptureStoreSnapshot | dict[str, Any],
        *,
        redaction: RedactionPolicy | None = None,
    ) -> "CaptureStore":
        """Restore persisted evidence for re-analysis without re-capturing."""

        parsed = (
            snapshot
            if isinstance(snapshot, CaptureStoreSnapshot)
            else CaptureStoreSnapshot.model_validate(snapshot)
        )
        store = cls(
            tenant_scope=parsed.tenant_scope,
            recording_id=parsed.recording_id,
            lineage_id=parsed.lineage_id,
            capture_generation=parsed.capture_generation,
            redaction=redaction,
        )
        with store._lock:
            for definition in parsed.request_definitions:
                if definition.fingerprint in store._definitions_by_fingerprint:
                    raise ValueError("duplicate request definition fingerprint in snapshot")
                if definition.request_definition_id in store._definitions_by_id:
                    raise ValueError("duplicate request_definition_id in snapshot")
                store._definitions_by_fingerprint[definition.fingerprint] = (
                    definition.model_copy(deep=True)
                )
                store._definitions_by_id[definition.request_definition_id] = (
                    definition.model_copy(deep=True)
                )
            for observation in parsed.observations:
                definition = store._definitions_by_id.get(observation.request_definition_id)
                if definition is None:
                    raise ValueError("snapshot observation references an unknown definition")
                if (
                    definition.request_schema != observation.request_schema
                    or definition.response_schema != observation.response_schema
                ):
                    raise ValueError("snapshot observation schema differs from its definition")
                if observation.observation_id in store._observations:
                    raise ValueError("duplicate observation_id in snapshot")
                store._observations[observation.observation_id] = observation.model_copy(
                    deep=True
                )
            unknown_unbound = set(parsed.unbound_business_requests) - set(store._observations)
            if unknown_unbound:
                raise ValueError("snapshot contains unknown unbound business requests")
            non_business = {
                observation_id
                for observation_id in parsed.unbound_business_requests
                if not store._observations[observation_id].business_request
            }
            if non_business:
                raise ValueError("non-business observations cannot be marked unbound")
            store._unbound = set(parsed.unbound_business_requests)
            store._records = {
                item.record_id: item.model_copy(deep=True) for item in parsed.records
            }
            if len(store._records) != len(parsed.records):
                raise ValueError("duplicate capture record in snapshot")
            store._scripts = {
                item.content_hash: item.model_copy(deep=True) for item in parsed.scripts
            }
            if len(store._scripts) != len(parsed.scripts):
                raise ValueError("duplicate script hash in snapshot")
            store._resources = {
                (item.resource_type, item.url): item.model_copy(deep=True)
                for item in parsed.resources
            }
            if len(store._resources) != len(parsed.resources):
                raise ValueError("duplicate resource index in snapshot")
        return store


__all__ = [
    "CaptureRecord",
    "CaptureRecordKind",
    "CaptureStore",
    "CaptureStoreSnapshot",
    "NetworkObservation",
    "RequestDefinition",
    "ResourceIndex",
    "ScriptArtifact",
    "normalize_request_path",
    "request_definition_fingerprint",
]
