"""Safe SourceMap decoding; sources are data, never executable code."""

from __future__ import annotations

import base64
import hashlib
import inspect
import ipaddress
import json
import asyncio
import socket
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from urllib.parse import unquote_to_bytes, urljoin, urlsplit

from dano_recording.capture.redaction import RedactionPolicy
from dano_recording.capture.safety import URLSafetyPolicy, UnsafeURL
from dano_recording.evidence.loaded_scripts import LoadedScript


class SourceMapStatus(StrEnum):
    LOADED = "loaded"
    MISSING = "missing"
    INVALID = "invalid"
    BLOCKED = "blocked"
    TOO_LARGE = "too_large"


@dataclass(frozen=True, slots=True)
class SourceMapEvidence:
    status: SourceMapStatus
    map_url: str | None = None
    sources: tuple[str, ...] = ()
    names: tuple[str, ...] = ()
    source_contents: tuple[str | None, ...] = field(default=(), repr=False, compare=False)
    error: str | None = None

    def pi_projection(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "map_url": self.map_url,
            # SourceMap metadata is attacker-controlled and may contain source
            # text rather than paths.  Pi receives counts and stable hashes only.
            "source_count": len(self.sources),
            "source_hashes": [
                hashlib.sha256(item.encode("utf-8", errors="replace")).hexdigest()
                for item in self.sources[:1000]
            ],
            "name_count": len(self.names),
            "has_sources_content": any(item is not None for item in self.source_contents),
            "error": "sourcemap_error" if self.error else None,
        }


@dataclass(frozen=True, slots=True)
class SourceMapFetchResult:
    """Redirect-aware result returned by the browser/network fetch boundary."""

    body: str | bytes
    final_url: str
    status: int = 200
    location: str | None = None


class SourceMapLoader:
    def __init__(
        self,
        *,
        url_policy: URLSafetyPolicy | None = None,
        max_map_bytes: int = 10_485_760,
        max_sources: int = 10_000,
        max_source_bytes: int = 5_242_880,
        redaction: RedactionPolicy | None = None,
    ) -> None:
        self.url_policy = url_policy or URLSafetyPolicy(allow_private_networks=True)
        self.max_map_bytes = max_map_bytes
        self.max_sources = max_sources
        self.max_source_bytes = max_source_bytes
        self.redaction = redaction or RedactionPolicy()

    async def _validate_network_target(self, url: str) -> None:
        validated = self.url_policy.validate(url)
        parts = urlsplit(validated)
        hostname = parts.hostname or ""
        try:
            ipaddress.ip_address(hostname.strip("[]"))
            return
        except ValueError:
            pass
        port = parts.port or (443 if parts.scheme.lower() in {"https", "wss"} else 80)
        try:
            rows = await asyncio.get_running_loop().getaddrinfo(
                hostname,
                port,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise UnsafeURL("SourceMap host did not resolve safely") from exc
        addresses = tuple(dict.fromkeys(str(row[4][0]) for row in rows if row[4]))
        self.url_policy.validate_resolved_addresses(addresses, hostname=hostname)

    def parse(self, source_map: str | bytes | None, *, map_url: str | None = None) -> SourceMapEvidence:
        safe_map_url = self.redaction.redact_url(map_url) if map_url else None
        if source_map is None or source_map == b"" or source_map == "":
            return SourceMapEvidence(status=SourceMapStatus.MISSING, map_url=safe_map_url)
        if not isinstance(source_map, (str, bytes)):
            return SourceMapEvidence(
                status=SourceMapStatus.INVALID,
                map_url=safe_map_url,
                error="SourceMap payload must be text or bytes",
            )
        raw = source_map if isinstance(source_map, bytes) else source_map.encode("utf-8", errors="replace")
        if len(raw) > self.max_map_bytes:
            return SourceMapEvidence(
                status=SourceMapStatus.TOO_LARGE,
                map_url=safe_map_url,
                error=f"SourceMap exceeds {self.max_map_bytes} bytes",
            )
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
            return SourceMapEvidence(
                status=SourceMapStatus.INVALID,
                map_url=safe_map_url,
                error=f"invalid SourceMap JSON: {exc}",
            )
        try:
            version = int(data.get("version", 0) or 0) if isinstance(data, dict) else 0
        except (TypeError, ValueError):
            version = 0
        if not isinstance(data, dict) or version != 3:
            return SourceMapEvidence(
                status=SourceMapStatus.INVALID,
                map_url=safe_map_url,
                error="unsupported or missing SourceMap version",
            )
        raw_sources = data.get("sources") or []
        if not isinstance(raw_sources, list) or len(raw_sources) > self.max_sources:
            return SourceMapEvidence(
                status=SourceMapStatus.TOO_LARGE,
                map_url=safe_map_url,
                error="SourceMap source count exceeds capacity",
            )
        source_root = str(data.get("sourceRoot") or "")
        sources = tuple(
            self.redaction.redact_url(
                urljoin(safe_map_url or "", source_root + str(item))
                if safe_map_url
                else source_root + str(item)
            )
            for item in raw_sources
        )
        raw_contents = data.get("sourcesContent") or []
        contents: list[str | None] = []
        if isinstance(raw_contents, list):
            for item in raw_contents[: len(sources)]:
                if item is None:
                    contents.append(None)
                else:
                    text = str(item)
                    encoded = text.encode("utf-8", errors="replace")
                    contents.append(
                        text if len(encoded) <= self.max_source_bytes else encoded[: self.max_source_bytes].decode("utf-8", errors="replace")
                    )
        while len(contents) < len(sources):
            contents.append(None)
        names = data.get("names") or []
        return SourceMapEvidence(
            status=SourceMapStatus.LOADED,
            map_url=safe_map_url,
            sources=sources,
            names=tuple(str(item) for item in names) if isinstance(names, list) else (),
            source_contents=tuple(contents),
        )

    async def load(
        self,
        script: LoadedScript,
        *,
        fetcher: Any | None = None,
    ) -> SourceMapEvidence:
        map_ref = script.source_map_url
        if not map_ref:
            return SourceMapEvidence(status=SourceMapStatus.MISSING)
        if map_ref.startswith("data:"):
            try:
                header, encoded = map_ref.split(",", 1)
                if ";base64" in header.lower():
                    raw = base64.b64decode(encoded, validate=True)
                else:
                    raw = unquote_to_bytes(encoded)
            except (ValueError, UnicodeError) as exc:
                return SourceMapEvidence(
                    status=SourceMapStatus.INVALID,
                    map_url="inline",
                    error=self.redaction.redact_text(f"invalid inline SourceMap: {exc}"),
                )
            return self.parse(raw, map_url="inline")
        if fetcher is None:
            return SourceMapEvidence(
                status=SourceMapStatus.MISSING,
                map_url=self.redaction.redact_url(map_ref),
                error="no SourceMap fetcher configured",
            )
        absolute = urljoin(script.url, map_ref)
        try:
            self.url_policy.validate(absolute)
            await self._validate_network_target(absolute)
        except UnsafeURL as exc:
            return SourceMapEvidence(
                status=SourceMapStatus.BLOCKED,
                map_url=self.redaction.redact_url(absolute),
                error=self.redaction.redact_text(str(exc)),
            )
        if script.url and not self.url_policy.same_origin(absolute, script.url):
            return SourceMapEvidence(
                status=SourceMapStatus.BLOCKED,
                map_url=self.redaction.redact_url(absolute),
                error="cross-origin SourceMap is blocked",
            )
        try:
            result = fetcher(absolute)
            result = await result if inspect.isawaitable(result) else result
        except Exception as exc:
            return SourceMapEvidence(
                status=SourceMapStatus.MISSING,
                map_url=self.redaction.redact_url(absolute),
                error=self.redaction.redact_text(f"SourceMap unavailable: {exc}"),
            )
        if not isinstance(result, SourceMapFetchResult):
            return SourceMapEvidence(
                status=SourceMapStatus.BLOCKED,
                map_url=self.redaction.redact_url(absolute),
                error="SourceMap fetcher did not prove its redirect boundary",
            )
        final_url = str(result.final_url or "")
        try:
            self.url_policy.validate(final_url)
            await self._validate_network_target(final_url)
        except UnsafeURL as exc:
            return SourceMapEvidence(
                status=SourceMapStatus.BLOCKED,
                map_url=self.redaction.redact_url(final_url or absolute),
                error=self.redaction.redact_text(str(exc)),
            )
        if (
            not final_url
            or not self.url_policy.same_origin(final_url, script.url)
            or not self.url_policy.same_origin(final_url, absolute)
            or 300 <= int(result.status) < 400
            or result.location
        ):
            return SourceMapEvidence(
                status=SourceMapStatus.BLOCKED,
                map_url=self.redaction.redact_url(final_url or absolute),
                error="SourceMap redirect or origin change is blocked",
            )
        return self.parse(result.body, map_url=final_url)
