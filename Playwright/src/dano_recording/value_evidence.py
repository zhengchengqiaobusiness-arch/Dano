"""Security boundary for values observed during browser recording.

The recording domain never stores a captured plaintext value directly.  This
module turns values into bounded evidence and sends credentials to an
application-provided encrypted vault.  The resulting :class:`ValueEvidence`
is safe to persist, serialize into a Flow revision, or expose to Pi.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable
from collections import defaultdict, deque

from pydantic import Field, field_validator, model_validator

from dano_recording.capture.redaction import RedactionPolicy
from dano_recording.domain._base import FrozenModel, freeze_json, new_id


class ValueSensitivity(StrEnum):
    CREDENTIAL = "credential"
    IDENTITY = "identity"
    PII = "pii"
    BUSINESS = "business"
    NONE = "none"


class ValueRetention(StrEnum):
    SESSION = "session"
    SHORT_TERM = "short_term"
    PERSISTENT = "persistent"


class ValueEvidence(FrozenModel):
    """A non-secret, typed observation that can cross trust boundaries."""

    evidence_id: str = Field(default_factory=new_id)
    # Location metadata is safe structural information.  Keeping it on the
    # evidence object lets downstream graph construction reuse the HMAC made
    # at the plaintext trust boundary instead of hashing a redaction marker.
    value_path: str | None = None
    field_name: str | None = None
    value_ref: str | None = None
    sensitivity: ValueSensitivity
    value_type: str
    value_length: int | None = Field(default=None, ge=0)
    scoped_hmac: str | None = None
    runtime_resolver: str | None = None
    redacted_sample: Any | None = None
    retention: ValueRetention

    @field_validator("scoped_hmac")
    @classmethod
    def _validate_hmac(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not re.fullmatch(r"hmac-sha256:[0-9a-f]{64}", value):
            raise ValueError("scoped_hmac must be a tagged SHA-256 digest")
        return value

    @model_validator(mode="after")
    def _enforce_secret_boundary(self) -> "ValueEvidence":
        object.__setattr__(self, "redacted_sample", freeze_json(self.redacted_sample))
        if self.sensitivity is ValueSensitivity.CREDENTIAL:
            if not self.value_ref:
                raise ValueError("credential evidence requires an encrypted-vault value_ref")
            if not self.runtime_resolver:
                raise ValueError("credential evidence requires a runtime_resolver")
            if self.redacted_sample is not None:
                raise ValueError("credential evidence cannot carry a sample")
            if self.retention is ValueRetention.PERSISTENT:
                raise ValueError("credential plaintext cannot request persistent retention")
        if self.sensitivity in {ValueSensitivity.IDENTITY, ValueSensitivity.PII}:
            sample = self.redacted_sample
            safely_redacted = sample is None or (
                isinstance(sample, str) and sample.startswith("[REDACTED")
            ) or (
                isinstance(sample, dict) and sample.get("redacted") is True
            )
            if not safely_redacted:
                raise ValueError(
                    f"{self.sensitivity.value} evidence cannot carry a plaintext sample"
                )
        return self


@runtime_checkable
class CredentialVault(Protocol):
    """Adapter for an encrypted credential store.

    Implementations receive plaintext only at this boundary and must return an
    opaque reference.  They must not derive references from the plaintext or
    expose it through ``repr``/serialization.
    """

    def store_secret(
        self,
        *,
        tenant_scope: str,
        recording_lineage: str,
        value_type: str,
        plaintext: bytes,
        retention: ValueRetention,
    ) -> str: ...


_IDENTITY_KEY_RE = re.compile(
    r"(?:^|_)(?:user|tenant|account|member|operator|creator|owner|employee|approver|assignee|reviewer)(?:_|$).*id$|"
    r"^(?:user|tenant|account|member|operator|creator|owner|employee|approver|assignee|reviewer)id$",
    re.IGNORECASE,
)
_PII_KEY_RE = re.compile(
    r"(?:email|e_mail|phone|mobile|telephone|address|full_?name|id_?card|passport|ssn)",
    re.IGNORECASE,
)
_EMAIL_VALUE_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_PHONE_VALUE_RE = re.compile(r"^\+?[0-9][0-9() .-]{6,}[0-9]$")
_CREDENTIAL_COLLECTION_KEYS = {
    "credentials",
    "cookies",
    "secrets",
    "tokens",
    "auth_headers",
}


def canonical_value(value: Any) -> bytes:
    """Return a deterministic, type-preserving representation for HMAC input."""

    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        encoded = json.dumps(str(value), ensure_ascii=False, separators=(",", ":"))
    return encoded.encode("utf-8")


def scoped_value_hmac(
    server_secret: bytes,
    *,
    tenant_scope: str,
    recording_lineage: str,
    value_type: str,
    value: Any,
) -> str:
    """Create a recording-lineage scoped equality fingerprint.

    Components are length-prefixed so distinct tuples cannot collide through
    concatenation.  This proves equality in a scope; it intentionally makes no
    causal claim about where a value came from.
    """

    if not server_secret:
        raise ValueError("server_secret must not be empty")
    components = (
        tenant_scope.encode("utf-8"),
        recording_lineage.encode("utf-8"),
        value_type.encode("utf-8"),
        canonical_value(value),
    )
    message = b"".join(len(item).to_bytes(8, "big") + item for item in components)
    digest = hmac.new(server_secret, message, hashlib.sha256).hexdigest()
    return f"hmac-sha256:{digest}"


def infer_value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, bytes):
        return "bytes"
    if isinstance(value, list | tuple):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__.lower()


def value_length(value: Any) -> int | None:
    if isinstance(value, bytes | str | list | tuple | dict):
        return len(value)
    return None


class ValueEvidenceFactory:
    """Classify and transform plaintext at the capture trust boundary."""

    __slots__ = ("__server_secret", "_vault", "_redaction")

    def __init__(
        self,
        *,
        server_secret: bytes,
        credential_vault: CredentialVault | None = None,
        redaction: RedactionPolicy | None = None,
    ) -> None:
        if not server_secret:
            raise ValueError("server_secret must not be empty")
        self.__server_secret = bytes(server_secret)
        self._vault = credential_vault
        self._redaction = redaction or RedactionPolicy()

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(server_secret=[REDACTED], "
            f"credential_vault={type(self._vault).__name__ if self._vault else None})"
        )

    def classify(
        self,
        *,
        field_name: str | None,
        value: Any,
        explicit: ValueSensitivity | str | None = None,
    ) -> ValueSensitivity:
        if explicit is not None:
            return ValueSensitivity(explicit)
        key = (field_name or "").strip().lower().replace("-", "_")
        if key and (
            self._redaction.is_credential_key(key) or key in _CREDENTIAL_COLLECTION_KEYS
        ):
            return ValueSensitivity.CREDENTIAL
        if key and _IDENTITY_KEY_RE.search(key):
            return ValueSensitivity.IDENTITY
        if key and _PII_KEY_RE.search(key):
            return ValueSensitivity.PII
        if isinstance(value, str):
            stripped = value.strip()
            if _EMAIL_VALUE_RE.fullmatch(stripped) or _PHONE_VALUE_RE.fullmatch(stripped):
                return ValueSensitivity.PII
        if value is None:
            return ValueSensitivity.NONE
        return ValueSensitivity.BUSINESS

    @staticmethod
    def _default_retention(sensitivity: ValueSensitivity) -> ValueRetention:
        if sensitivity is ValueSensitivity.CREDENTIAL:
            return ValueRetention.SESSION
        if sensitivity is ValueSensitivity.PII:
            return ValueRetention.SHORT_TERM
        return ValueRetention.PERSISTENT

    @staticmethod
    def _identity_resolver(field_name: str | None) -> str | None:
        key = (field_name or "").strip().lower().replace("-", "_")
        if "tenant" in key:
            return "runtime_context.current_tenant.id"
        if any(name in key for name in ("user", "creator", "owner", "operator", "employee")):
            return "runtime_context.current_user.id"
        return None

    def _safe_sample(self, value: Any, sensitivity: ValueSensitivity) -> Any | None:
        if sensitivity is ValueSensitivity.CREDENTIAL:
            return None
        if sensitivity is ValueSensitivity.IDENTITY:
            return {"redacted": True, "kind": "identity"}
        if sensitivity is ValueSensitivity.PII:
            return "[REDACTED:PII]"
        if sensitivity is ValueSensitivity.NONE:
            return self._redaction.redact_value(value)
        return self._redaction.redact_value(value)

    def capture(
        self,
        *,
        tenant_scope: str,
        recording_lineage: str,
        value: Any,
        field_name: str | None = None,
        value_type: str | None = None,
        sensitivity: ValueSensitivity | str | None = None,
        runtime_resolver: str | None = None,
        retention: ValueRetention | str | None = None,
        evidence_id: str | None = None,
        value_path: str | None = None,
    ) -> ValueEvidence:
        """Consume plaintext and return only safe, serializable evidence."""

        if not tenant_scope.strip() or not recording_lineage.strip():
            raise ValueError("tenant_scope and recording_lineage must not be blank")
        resolved_type = value_type or infer_value_type(value)
        resolved_sensitivity = self.classify(
            field_name=field_name,
            value=value,
            explicit=sensitivity,
        )
        resolved_retention = (
            ValueRetention(retention)
            if retention is not None
            else self._default_retention(resolved_sensitivity)
        )
        digest = None
        if value is not None:
            digest = scoped_value_hmac(
                self.__server_secret,
                tenant_scope=tenant_scope,
                recording_lineage=recording_lineage,
                value_type=resolved_type,
                value=value,
            )

        value_ref: str | None = None
        resolver = runtime_resolver
        if resolved_sensitivity is ValueSensitivity.CREDENTIAL:
            if resolved_retention is ValueRetention.PERSISTENT:
                raise ValueError("credential plaintext cannot request persistent retention")
            if self._vault is None:
                raise RuntimeError("credential capture requires an encrypted credential vault")
            if isinstance(value, bytes):
                plaintext = value
            elif isinstance(value, str):
                plaintext = value.encode("utf-8")
            else:
                plaintext = canonical_value(value)
            value_ref = self._vault.store_secret(
                tenant_scope=tenant_scope,
                recording_lineage=recording_lineage,
                value_type=resolved_type,
                plaintext=plaintext,
                retention=resolved_retention,
            )
            if not value_ref or not value_ref.strip():
                raise RuntimeError("credential vault returned an empty value_ref")
            if isinstance(value, str) and value and value in value_ref:
                raise RuntimeError("credential vault returned a non-opaque value_ref")
            resolver = resolver or f"credential_store.resolve:{value_ref}"
            if isinstance(value, str) and value and value in resolver:
                raise RuntimeError("credential runtime_resolver contains plaintext")
        elif resolved_sensitivity is ValueSensitivity.IDENTITY:
            resolver = resolver or self._identity_resolver(field_name)

        return ValueEvidence(
            evidence_id=evidence_id or new_id(),
            value_path=value_path,
            field_name=field_name,
            value_ref=value_ref,
            sensitivity=resolved_sensitivity,
            value_type=resolved_type,
            value_length=value_length(value),
            scoped_hmac=digest,
            runtime_resolver=resolver,
            redacted_sample=self._safe_sample(value, resolved_sensitivity),
            retention=resolved_retention,
        )

    @staticmethod
    def _safe_runtime_value(evidence: ValueEvidence) -> Any:
        if evidence.sensitivity in {
            ValueSensitivity.CREDENTIAL,
            ValueSensitivity.IDENTITY,
        } and evidence.runtime_resolver:
            return "{{" + evidence.runtime_resolver + "}}"
        if evidence.sensitivity is ValueSensitivity.CREDENTIAL:
            return "[REDACTED:CREDENTIAL]"
        return evidence.redacted_sample

    def capture_tree(
        self,
        *,
        tenant_scope: str,
        recording_lineage: str,
        value: Any,
        root_path: str,
        field_name: str | None = None,
    ) -> tuple[Any, tuple[ValueEvidence, ...]]:
        """Consume a structured value once and return a safe template + evidence.

        Credentials without a configured vault are omitted from evidence and
        replaced immediately.  Identity and PII equality still uses the HMAC
        made from the original value, while their plaintext never leaves this
        call.
        """

        evidence: list[ValueEvidence] = []

        def visit(item: Any, *, path: str, name: str | None) -> Any:
            if isinstance(item, dict):
                return {
                    str(key): visit(
                        child,
                        path=f"{path}.{key}" if path else str(key),
                        name=str(key),
                    )
                    for key, child in item.items()
                }
            if isinstance(item, list | tuple):
                return [
                    visit(child, path=f"{path}[{index}]", name=name)
                    for index, child in enumerate(item)
                ]
            try:
                captured = self.capture(
                    tenant_scope=tenant_scope,
                    recording_lineage=recording_lineage,
                    value=item,
                    field_name=name,
                    value_path=path,
                )
            except RuntimeError as exc:
                if "credential" not in str(exc).lower():
                    raise
                return "[REDACTED:CREDENTIAL]"
            evidence.append(captured)
            return self._safe_runtime_value(captured)

        safe = visit(value, path=root_path, name=field_name)
        return safe, tuple(evidence)


def contains_plaintext(payload: Any, plaintext: str | bytes) -> bool:
    """Test/audit helper for proving a value does not survive serialization."""

    needle = (
        plaintext.decode("utf-8", errors="replace")
        if isinstance(plaintext, bytes)
        else plaintext
    )
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return needle in serialized


def safe_value_from_evidence(
    value: Any,
    evidence: list[ValueEvidence] | tuple[ValueEvidence, ...],
    *,
    root_path: str,
) -> Any:
    """Overlay boundary-created evidence on a legacy/raw structured value."""

    by_path: dict[str, deque[ValueEvidence]] = defaultdict(deque)
    for item in evidence:
        if item.value_path:
            by_path[item.value_path].append(item)

    def projected(item: ValueEvidence) -> Any:
        return ValueEvidenceFactory._safe_runtime_value(item)

    def visit(item: Any, path: str) -> Any:
        exact = by_path.get(path)
        if not isinstance(item, (dict, list, tuple)) and exact:
            return projected(exact.popleft())
        if isinstance(item, dict):
            return {
                str(key): visit(child, f"{path}.{key}" if path else str(key))
                for key, child in item.items()
            }
        if isinstance(item, list | tuple):
            output = []
            for index, child in enumerate(item):
                indexed_path = f"{path}[{index}]"
                if indexed_path not in by_path and by_path.get(path):
                    output.append(projected(by_path[path].popleft()))
                else:
                    output.append(visit(child, indexed_path))
            return output
        return item

    return visit(value, root_path)


__all__ = [
    "CredentialVault",
    "ValueEvidence",
    "ValueEvidenceFactory",
    "ValueRetention",
    "ValueSensitivity",
    "canonical_value",
    "contains_plaintext",
    "infer_value_type",
    "scoped_value_hmac",
    "safe_value_from_evidence",
]
