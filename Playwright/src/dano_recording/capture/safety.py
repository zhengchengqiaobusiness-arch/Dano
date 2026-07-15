"""URL and navigation safety rules shared by capture and evidence loaders."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from fnmatch import fnmatch
from urllib.parse import urljoin, urlsplit


class UnsafeURL(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SafeRecordPolicy:
    """Execution policy only; it never decides whether a request is captured."""

    enabled: bool = True
    write_methods: frozenset[str] = field(
        default_factory=lambda: frozenset({"POST", "PUT", "PATCH", "DELETE"})
    )

    def is_write(self, method: str) -> bool:
        return method.strip().upper() in self.write_methods

    def should_block(self, method: str) -> bool:
        return self.enabled and self.is_write(method)


@dataclass(frozen=True, slots=True)
class URLSafetyPolicy:
    allowed_schemes: frozenset[str] = field(
        default_factory=lambda: frozenset({"http", "https", "ws", "wss"})
    )
    allowed_hosts: tuple[str, ...] = ()
    allow_private_networks: bool = False
    private_host_allowlist: tuple[str, ...] = ()
    allow_url_credentials: bool = False

    def validate(self, url: str, *, base_url: str | None = None) -> str:
        absolute = urljoin(base_url, url) if base_url else url
        try:
            parts = urlsplit(absolute)
            port = parts.port  # Force malformed port validation.
        except (TypeError, ValueError) as exc:
            raise UnsafeURL("malformed URL") from exc
        scheme = parts.scheme.lower()
        if scheme not in self.allowed_schemes:
            raise UnsafeURL(f"URL scheme {scheme or '<missing>'!r} is not allowed")
        hostname = (parts.hostname or "").rstrip(".").lower()
        if not hostname:
            raise UnsafeURL("URL host is required")
        if (parts.username or parts.password) and not self.allow_url_credentials:
            raise UnsafeURL("credentials in URLs are not allowed")
        if self.allowed_hosts and not any(fnmatch(hostname, pattern.lower()) for pattern in self.allowed_hosts):
            raise UnsafeURL(f"host {hostname!r} is outside the recording allowlist")
        try:
            literal = ipaddress.ip_address(hostname.strip("[]"))
        except ValueError:
            literal = None
        if literal is not None and self._is_always_forbidden(literal):
            raise UnsafeURL(f"dangerous local or special address {hostname!r} is not allowed")
        private_allowed = any(
            fnmatch(hostname, pattern.lower()) for pattern in self.private_host_allowlist
        )
        if not self.allow_private_networks and not private_allowed and self._is_local_or_private(hostname):
            raise UnsafeURL(f"private or local host {hostname!r} is not allowed")
        # Preserve the original spelling after validation; callers may need the
        # exact wire URL.  ``port`` is intentionally referenced above.
        del port
        return absolute

    def same_origin(self, candidate: str, origin: str) -> bool:
        try:
            a = urlsplit(self.validate(candidate, base_url=origin))
            b = urlsplit(self.validate(origin))
        except UnsafeURL:
            return False
        return (a.scheme.lower(), a.hostname, a.port or self._default_port(a.scheme)) == (
            b.scheme.lower(),
            b.hostname,
            b.port or self._default_port(b.scheme),
        )

    @staticmethod
    def _default_port(scheme: str) -> int | None:
        return {"http": 80, "https": 443, "ws": 80, "wss": 443}.get(scheme.lower())

    @staticmethod
    def _is_local_or_private(hostname: str) -> bool:
        if hostname == "localhost" or hostname.endswith(".localhost") or hostname.endswith(".local"):
            return True
        try:
            address = ipaddress.ip_address(hostname.strip("[]"))
        except ValueError:
            return False
        return not address.is_global

    @staticmethod
    def _is_always_forbidden(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        """Addresses that are never an enterprise target boundary.

        RFC1918/ULA networks may be explicitly enabled for enterprise apps;
        loopback, link-local metadata, unspecified, multicast and reserved
        ranges remain blocked even when a hostname is allowlisted.
        """

        return bool(
            address.is_loopback
            or address.is_link_local
            or address.is_unspecified
            or address.is_multicast
            or address.is_reserved
        )

    def validate_resolved_addresses(
        self,
        addresses: tuple[str, ...] | list[str],
        *,
        hostname: str = "",
    ) -> None:
        """Reject DNS results that cross the configured private-network boundary."""

        private_allowed = self.allow_private_networks or any(
            fnmatch(hostname.rstrip(".").lower(), pattern.lower())
            for pattern in self.private_host_allowlist
        )
        if not addresses:
            raise UnsafeURL("URL host did not resolve")
        for raw in addresses:
            try:
                address = ipaddress.ip_address(raw)
            except ValueError as exc:
                raise UnsafeURL(f"invalid resolved address {raw!r}") from exc
            if self._is_always_forbidden(address):
                raise UnsafeURL(f"resolved dangerous local or special address {raw!r} is not allowed")
            if not private_allowed and not address.is_global:
                raise UnsafeURL(f"resolved private or local address {raw!r} is not allowed")
