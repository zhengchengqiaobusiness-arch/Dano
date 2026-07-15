"""Runtime safety rules independent from the legacy page executor."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from dano_recording.capture.safety import URLSafetyPolicy


DANGEROUS_METHODS = {"DELETE", "PATCH", "PUT", "POST"}

_UNTRUSTED_CREDENTIAL_HEADERS = {
    "authorization",
    "cookie",
    "proxy-authorization",
    "x-api-key",
    "x-auth-token",
}


@dataclass(frozen=True, slots=True)
class RuntimePolicy:
    recorded_origin: str = ""
    allow_http: bool = False
    allow_private_networks: bool = False

    def _network_policy(self, hostname: str) -> URLSafetyPolicy:
        return URLSafetyPolicy(
            allowed_schemes=frozenset({"http", "https"}),
            allowed_hosts=(hostname,),
            allow_private_networks=self.allow_private_networks,
            private_host_allowlist=(hostname,) if self.allow_private_networks else (),
        )

    def resolve_url(self, base_url: str, value: str) -> str:
        url = value if value.startswith(("http://", "https://")) else urljoin(base_url.rstrip("/") + "/", value.lstrip("/"))
        parsed = urlparse(url)
        if parsed.scheme not in ({"https"} if not self.allow_http else {"http", "https"}):
            raise ValueError("recorded request URL uses a forbidden scheme")
        expected = urlparse(self.recorded_origin or base_url)
        expected_port = expected.port or (443 if expected.scheme.lower() == "https" else 80)
        parsed_port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        if expected.hostname and (
            parsed.scheme.lower() != expected.scheme.lower()
            or (parsed.hostname or "").lower() != expected.hostname.lower()
            or parsed_port != expected_port
        ):
            raise ValueError("recorded request cannot change origin at runtime")
        if parsed.username or parsed.password:
            raise ValueError("credentials in request URL are forbidden")
        return self._network_policy((parsed.hostname or "").lower()).validate(url)

    def validate_resolved_addresses(self, url: str, addresses: list[str] | tuple[str, ...]) -> None:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").rstrip(".").lower()
        self._network_policy(hostname).validate_resolved_addresses(
            addresses,
            hostname=hostname,
        )


def capability_requires_confirmation(capability: dict, steps: list[dict]) -> bool:
    if capability.get("explicit_confirmation") or capability.get("requires_confirmation"):
        return True
    if str(capability.get("risk_level") or "").upper() in {"L3", "L4", "L5"}:
        return True
    return any(str(step.get("method") or "GET").upper() in DANGEROUS_METHODS for step in steps)


def safe_headers(headers: dict | None) -> dict[str, str]:
    """Drop browser-only, routing and credential headers from an asset.

    Published recordings are untrusted declarative data.  Authentication is
    injected separately from Dano's tenant-scoped runtime credential store.
    """
    denied = {
        "host", "content-length", "connection", "origin", "referer", "sec-fetch-site",
        "sec-fetch-mode", "sec-fetch-dest", "transfer-encoding", "upgrade",
        *tuple(_UNTRUSTED_CREDENTIAL_HEADERS),
    }
    return {
        str(key): str(value)
        for key, value in (headers or {}).items()
        if str(key).lower() not in denied and value is not None
    }


def safe_credential_headers(headers: dict | None) -> dict[str, str]:
    """Credentials come from Dano's trusted store, never from the published draft."""
    allowed = _UNTRUSTED_CREDENTIAL_HEADERS - {"proxy-authorization"}
    return {
        str(key): str(value)
        for key, value in (headers or {}).items()
        if str(key).lower() in allowed and value not in (None, "")
    }


def safe_response_headers(headers: dict | None) -> dict[str, str]:
    """Return useful response metadata without leaking rotated credentials."""

    denied = {
        "set-cookie",
        "set-cookie2",
        "authorization",
        "proxy-authenticate",
        "www-authenticate",
    }
    return {
        str(key): str(value)
        for key, value in (headers or {}).items()
        if str(key).lower() not in denied and value is not None
    }
