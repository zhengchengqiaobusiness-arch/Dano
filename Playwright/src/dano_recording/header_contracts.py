"""Deterministic request-header ownership for recording V3."""

from __future__ import annotations

import re
from enum import StrEnum


class HeaderContractKind(StrEnum):
    TRANSPORT_CONSTANT = "transport_constant"
    CREDENTIAL = "credential"
    USER_IDENTITY = "user_identity"
    TENANT_IDENTITY = "tenant_identity"
    BUSINESS_DYNAMIC = "business_dynamic"


_TRANSPORT = frozenset({
    "accept",
    "accept-encoding",
    "accept-language",
    "cache-control",
    "connection",
    "content-length",
    "content-type",
    "host",
    "origin",
    "pragma",
    "referer",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "user-agent",
})
_CREDENTIAL = frozenset({
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "x-auth-token",
    "x-csrf-token",
    "x-xsrf-token",
})
_USER_IDENTITY = re.compile(
    r"(?:^|[-_])(?:user|creator|owner|member|operator|employee)(?:[-_].*)?id$",
    re.I,
)
_TENANT_IDENTITY = re.compile(
    r"(?:^|[-_])(?:tenant|org|organization|workspace)(?:[-_].*)?id$",
    re.I,
)


def classify_header(name: str) -> HeaderContractKind:
    normalized = name.strip().casefold().replace("_", "-")
    if normalized in _CREDENTIAL or any(
        token in normalized for token in ("auth-token", "access-token", "session-token")
    ):
        return HeaderContractKind.CREDENTIAL
    if normalized in _TRANSPORT or normalized.startswith("sec-"):
        return HeaderContractKind.TRANSPORT_CONSTANT
    if _TENANT_IDENTITY.search(normalized):
        return HeaderContractKind.TENANT_IDENTITY
    if _USER_IDENTITY.search(normalized):
        return HeaderContractKind.USER_IDENTITY
    return HeaderContractKind.BUSINESS_DYNAMIC


def trusted_header_resolver(name: str) -> str | None:
    kind = classify_header(name)
    if kind is HeaderContractKind.CREDENTIAL:
        return f"credential_headers.{name}"
    if kind is HeaderContractKind.TENANT_IDENTITY:
        return "runtime_context.current_tenant.id"
    if kind is HeaderContractKind.USER_IDENTITY:
        return "runtime_context.current_user.id"
    return None


__all__ = ["HeaderContractKind", "classify_header", "trusted_header_resolver"]
