"""Central redaction policy for browser facts, diagnostics, and evidence."""

from __future__ import annotations

import json
import hashlib
from email import policy as email_policy
from email.parser import BytesParser
from pathlib import PurePath
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit


_JWT_RE = re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_EMAIL_RE = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])")
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d ()-]{7,}\d)(?!\w)")
_CANONICAL_OPAQUE_ID_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"|[0-9A-HJKMNP-TV-Z]{26}"
    r"|[0-9a-fA-F]{24,128}"
    r")(?![A-Za-z0-9])"
)
_IDENTITY_ASSIGNMENT_RE = re.compile(
    r"(?i)\b((?:user|employee|creator|owner|operator|tenant)[_-]?id\s*[:=]\s*)"
    r"(?:[\"']?)[A-Za-z0-9._:@/-]{2,}(?:[\"']?)"
)
_AUTH_SCHEME_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(authorization[\"']?\s*[:=]\s*[\"']?(?:basic|bearer)\s+)"
    r"([^&,\s\"'}\]]+)"
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b("
    r"access[_-]?token|refresh[_-]?token|id[_-]?token|client[_-]?secret|"
    r"password|passwd|pwd|api[_-]?key|token|session(?:id)?|cookie|credential|"
    r"authorization|csrf(?:[_-]?token)?|xsrf(?:[_-]?token)?|"
    r"auth[_-]?ticket|login[_-]?ticket|service[_-]?ticket"
    r")\b([\"']?\s*[:=]\s*[\"']?)([^&,\s\"'}\]]+)"
)
_IDENTITY_PATH_COLLECTIONS = frozenset({
    "user", "users", "employee", "employees", "member", "members",
    "approver", "approvers", "assignee", "assignees", "reviewer", "reviewers",
    "tenant", "tenants", "owner", "owners", "creator", "creators",
    "operator", "operators",
})
_SAFE_IDENTITY_PATH_VALUES = frozenset({
    "me", "self", "current", "search", "options", "list", "lookup", "resolve",
})


@dataclass(frozen=True, slots=True)
class RedactionPolicy:
    """Conservative, deterministic secret removal.

    Redaction is key-aware and recursive.  It intentionally runs before facts
    are appended, so neither persistence nor Pi projections see credentials.
    """

    replacement: str = "[REDACTED]"
    max_string_length: int = 16_384
    sensitive_headers: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                "authorization",
                "proxy-authorization",
                "cookie",
                "set-cookie",
                "x-api-key",
                "x-auth-token",
                "x-csrf-token",
                "x-xsrf-token",
                "csrf-token",
                "xsrf-token",
            }
        )
    )
    sensitive_keys: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                "password",
                "passwd",
                "pwd",
                "secret",
                "client_secret",
                "token",
                "access_token",
                "refresh_token",
                "id_token",
                "api_key",
                "apikey",
                "session",
                "sessionid",
                "cookie",
                "credential",
                "auth",
                "authorization",
                "signature",
                "sig",
                "csrf",
                "xsrf",
                "csrf_token",
                "xsrf_token",
                "auth_ticket",
                "login_ticket",
                "service_ticket",
                "email",
                "email_address",
                "phone",
                "phone_number",
                "mobile",
                "mobile_phone",
                "user_id",
                "employee_id",
                "creator_id",
                "owner_id",
                "operator_id",
                "tenant_id",
            }
        )
    )
    credential_keys: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                "password", "passwd", "pwd", "secret", "client_secret",
                "token", "access_token", "refresh_token", "id_token",
                "api_key", "apikey", "session", "sessionid", "cookie",
                "credential", "auth", "authorization", "signature", "sig",
                "csrf", "xsrf", "csrf_token", "xsrf_token", "auth_ticket",
                "login_ticket", "service_ticket",
            }
        )
    )

    @staticmethod
    def _normalise_key(key: object) -> str:
        return str(key).strip().lower().replace("-", "_")

    def is_sensitive_key(self, key: object) -> bool:
        normalised = self._normalise_key(key)
        if normalised in self.sensitive_keys or self.is_credential_key(normalised):
            return True
        return False

    def is_credential_key(self, key: object) -> bool:
        """Distinguish credentials from PII/identity redaction aliases."""

        normalised = self._normalise_key(key)
        if normalised in self.credential_keys:
            return True
        if any(
            normalised.endswith(suffix)
            for suffix in ("_password", "_passwd", "_secret", "_token", "_api_key")
        ):
            return True
        compact = normalised.replace("_", "")
        return any(
            compact.endswith(suffix)
            for suffix in (
                "password", "passwd", "secret", "token", "apikey",
                "session", "sessionid", "cookie", "credential", "signature",
                "csrftoken", "xsrftoken", "authticket", "loginticket",
                "serviceticket",
            )
        )

    def _redact_path(self, path: str) -> str:
        parts = path.split("/")
        output: list[str] = []
        previous = ""
        for raw in parts:
            decoded = unquote(raw)
            normalised = decoded.strip().casefold()
            sensitive_text = self.redact_text(decoded) != decoded
            identity_value = (
                previous in _IDENTITY_PATH_COLLECTIONS
                and bool(normalised)
                and normalised not in _SAFE_IDENTITY_PATH_VALUES
            )
            output.append(self.replacement if sensitive_text or identity_value else raw)
            previous = normalised
        return "/".join(output)

    def redact_headers(self, headers: dict[str, Any] | None) -> dict[str, str]:
        output: dict[str, str] = {}
        for raw_key, raw_value in (headers or {}).items():
            key = str(raw_key)
            if key.strip().lower() in self.sensitive_headers:
                output[key] = self.replacement
            else:
                output[key] = self.redact_text(str(raw_value))
        return output

    def redact_url(self, url: str) -> str:
        try:
            parts = urlsplit(url)
            query = []
            for key, value in parse_qsl(parts.query, keep_blank_values=True):
                query.append((key, self.replacement if self.is_sensitive_key(key) else self.redact_text(value)))
            # Userinfo is never useful evidence and can contain credentials.
            host = parts.hostname or ""
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            if parts.port:
                host = f"{host}:{parts.port}"
            fragment = parts.fragment
            if "=" in fragment:
                fragment = urlencode(
                    [
                        (
                            key,
                            self.replacement
                            if self.is_sensitive_key(key)
                            else self.redact_text(value),
                        )
                        for key, value in parse_qsl(fragment, keep_blank_values=True)
                    ],
                    doseq=True,
                )
            else:
                fragment = self.redact_text(fragment)
            return urlunsplit((
                parts.scheme,
                host,
                self._redact_path(parts.path),
                urlencode(query, doseq=True),
                fragment,
            ))
        except (TypeError, ValueError):
            return "[INVALID_URL]"

    def redact_text(self, value: str) -> str:
        value = _AUTH_SCHEME_ASSIGNMENT_RE.sub(
            lambda match: f"{match.group(1)}{self.replacement}",
            value,
        )
        value = _BEARER_RE.sub(f"Bearer {self.replacement}", value)
        value = _JWT_RE.sub(self.replacement, value)
        value = _SECRET_ASSIGNMENT_RE.sub(
            lambda match: f"{match.group(1)}{match.group(2)}{self.replacement}",
            value,
        )
        value = _EMAIL_RE.sub(self.replacement, value)
        # Numeric UUID segments can look like a formatted phone number.  Hide
        # canonical opaque identifiers only for the phone-recognition pass,
        # then restore them before identity-assignment redaction runs.
        opaque_identifiers: list[str] = []

        def protect_opaque_identifier(match: re.Match[str]) -> str:
            opaque_identifiers.append(match.group(0))
            return f"\x00DANO_OPAQUE_ID_{len(opaque_identifiers) - 1}\x00"

        value = _CANONICAL_OPAQUE_ID_RE.sub(protect_opaque_identifier, value)
        value = _PHONE_RE.sub(
            lambda match: (
                self.replacement
                if 10 <= sum(
                    character.isdigit() for character in match.group(0)
                ) <= 15
                else match.group(0)
            ),
            value,
        )
        for index, identifier in enumerate(opaque_identifiers):
            value = value.replace(f"\x00DANO_OPAQUE_ID_{index}\x00", identifier)
        value = _IDENTITY_ASSIGNMENT_RE.sub(
            lambda match: f"{match.group(1)}{self.replacement}",
            value,
        )
        if len(value) > self.max_string_length:
            return value[: self.max_string_length] + "...[TRUNCATED]"
        return value

    @staticmethod
    def contains_credential_text(value: str) -> bool:
        """Detect bounded credential literals embedded in diagnostic/free text."""

        return bool(
            _JWT_RE.search(value)
            or _BEARER_RE.search(value)
            or _AUTH_SCHEME_ASSIGNMENT_RE.search(value)
            or _SECRET_ASSIGNMENT_RE.search(value)
        )

    def redact_value(self, value: Any, *, key: object | None = None, _depth: int = 0) -> Any:
        if key is not None and self.is_sensitive_key(key):
            return self.replacement
        if key is not None and self._normalise_key(key) in {"url", "uri", "href"} and isinstance(value, str):
            return self.redact_url(value)
        if _depth > 32:
            return "[MAX_DEPTH]"
        if isinstance(value, dict):
            return {
                str(item_key): self.redact_value(item_value, key=item_key, _depth=_depth + 1)
                for item_key, item_value in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self.redact_value(item, _depth=_depth + 1) for item in value]
        if isinstance(value, bytes):
            return self.redact_text(value.decode("utf-8", errors="replace"))
        if isinstance(value, str):
            return self.redact_text(value)
        return value

    def _redact_multipart(self, body: bytes | str, content_type: str) -> Any:
        raw = body if isinstance(body, bytes) else body.encode("utf-8", errors="replace")
        safe_content_type = content_type.splitlines()[0].strip()
        try:
            message = BytesParser(policy=email_policy.default).parsebytes(
                (
                    f"Content-Type: {safe_content_type}\r\n"
                    "MIME-Version: 1.0\r\n\r\n"
                ).encode("ascii", errors="strict")
                + raw
            )
            if not message.is_multipart():
                raise ValueError("multipart body has no MIME parts")
            output: dict[str, Any] = {}
            for part in message.iter_parts():
                name = str(part.get_param("name", header="content-disposition") or "")
                if not name:
                    continue
                payload = part.get_payload(decode=True) or b""
                filename = part.get_filename()
                if self.is_sensitive_key(name):
                    clean: Any = self.replacement
                elif filename is not None:
                    # File bytes never become recording facts. Only bounded,
                    # non-secret metadata and a content digest are retained.
                    basename = PurePath(str(filename).replace("\\", "/")).name
                    clean = {
                        "filename": self.redact_text(basename),
                        "content_type": self.redact_text(
                            str(part.get_content_type() or "application/octet-stream")
                        ),
                        "size": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }
                else:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        text = payload.decode(charset, errors="replace")
                    except LookupError:
                        text = payload.decode("utf-8", errors="replace")
                    clean = self.redact_text(text)
                if name not in output:
                    output[name] = clean
                elif isinstance(output[name], list):
                    output[name].append(clean)
                else:
                    output[name] = [output[name], clean]
            return output
        except Exception:
            # Parsing failure is fail-closed: never persist a fallback copy of
            # the raw multipart payload.
            return "[MULTIPART_BODY_UNAVAILABLE]"

    def redact_body(self, body: Any, content_type: str = "") -> Any:
        if body is None:
            return None
        if isinstance(body, (dict, list, tuple)):
            return self.redact_value(body)
        text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)
        lower_content_type = content_type.lower()
        if "multipart/form-data" in content_type.lower() and isinstance(body, (bytes, str)):
            return self._redact_multipart(body, content_type)

        if "json" in lower_content_type or text.lstrip().startswith(("{", "[")):
            try:
                return self.redact_value(json.loads(text))
            except (json.JSONDecodeError, TypeError):
                pass
        if "application/x-www-form-urlencoded" in lower_content_type:
            pairs = parse_qsl(text, keep_blank_values=True)
            output: dict[str, Any] = {}
            for item_key, item_value in pairs:
                clean = (
                    self.replacement
                    if self.is_sensitive_key(item_key)
                    else self.redact_text(item_value)
                )
                if item_key not in output:
                    output[item_key] = clean
                elif isinstance(output[item_key], list):
                    output[item_key].append(clean)
                else:
                    output[item_key] = [output[item_key], clean]
            return output
        return self.redact_text(text)
