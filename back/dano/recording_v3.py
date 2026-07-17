"""Thin Dano integration loader for the sibling Playwright recording package.

Only this module knows the repository layout. The V3 package remains independently
testable/installable and never imports legacy recording modules.
"""

from __future__ import annotations

import base64
import hashlib
from importlib import import_module
import os
from pathlib import Path
import secrets
import sys
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4


_RECORDING_V3_MIGRATION = "014_recording_v3.sql"
_RECORDING_V3_TABLES = (
    "recording_sessions",
    "recording_facts",
    "recording_revisions",
    "recording_operations",
    "recording_pi_sessions",
    "recording_pi_events",
    "recording_artifacts",
)


def ensure_recording_package() -> Path:
    try:
        installed = import_module("dano_recording")
    except ModuleNotFoundError as exc:
        if exc.name != "dano_recording":
            raise
    else:
        package_file = getattr(installed, "__file__", None)
        if package_file:
            return Path(package_file).resolve().parent
        raise RuntimeError("installed dano_recording package has no filesystem location")

    source = Path(__file__).resolve().parents[2] / "Playwright" / "src"
    if not source.is_dir():
        raise RuntimeError(f"Playwright recording package not found: {source}")
    value = str(source)
    if value not in sys.path:
        sys.path.insert(0, value)
    return source


async def probe_recording_v3_readiness(service: Any) -> None:
    """Fail closed unless the started service owns a live PostgreSQL repository."""

    ensure_recording_package()
    from dano_recording.persistence.postgres import AsyncpgRecordingRepository

    if not getattr(service, "_started", False):
        raise RuntimeError("recording-v3 application has not started")
    service._ensure_available()
    repository = getattr(service, "repository", None)
    if not isinstance(repository, AsyncpgRecordingRepository):
        raise RuntimeError("recording-v3 durable PostgreSQL repository is unavailable")
    pool = getattr(repository, "_pool", None)
    if pool is None:
        raise RuntimeError("recording-v3 PostgreSQL pool is unavailable")
    async with pool.acquire() as connection:
        migration_ready = await connection.fetchval(
            """
            SELECT
                EXISTS (
                    SELECT 1
                    FROM schema_migrations
                    WHERE filename = $1
                )
                AND NOT EXISTS (
                    SELECT 1
                    FROM unnest($2::text[]) AS required(table_name)
                    WHERE to_regclass(required.table_name) IS NULL
                )
            """,
            _RECORDING_V3_MIGRATION,
            list(_RECORDING_V3_TABLES),
        )
    if migration_ready is not True:
        raise RuntimeError("recording-v3 PostgreSQL migration is unavailable")


def _stable_evidence_secret(configured: str, *, key_file: Path) -> bytes:
    """Load a stable HMAC root without ever falling back to process randomness."""

    value = str(configured or "").strip()
    if value:
        try:
            if value.startswith("base64:"):
                secret = base64.b64decode(value.removeprefix("base64:"), validate=True)
            elif value.startswith("hex:"):
                secret = bytes.fromhex(value.removeprefix("hex:"))
            else:
                secret = value.encode("utf-8")
        except (ValueError, TypeError) as exc:
            raise RuntimeError("recording evidence HMAC key is malformed") from exc
        if len(secret) < 32:
            raise RuntimeError("recording evidence HMAC key must contain at least 32 bytes")
        return secret
    key_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(
            key_file,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        descriptor = None
    if descriptor is not None:
        generated = secrets.token_bytes(32)
        try:
            os.write(descriptor, base64.b64encode(generated))
        finally:
            os.close(descriptor)
    try:
        stored = base64.b64decode(key_file.read_bytes(), validate=True)
    except (OSError, ValueError) as exc:
        raise RuntimeError("recording evidence HMAC key file is unavailable or corrupt") from exc
    if len(stored) != 32:
        raise RuntimeError("recording evidence HMAC key file has an invalid length")
    return stored


class _DanoCredentialVault:
    """Synchronous ValueEvidence vault adapter backed by Dano's real Vault."""

    def __init__(self) -> None:
        from dano.infra.vault import VaultClient

        self._client = VaultClient()

    def store_secret(
        self,
        *,
        tenant_scope: str,
        recording_lineage: str,
        value_type: str,
        plaintext: bytes,
        retention: Any,
    ) -> str:
        tenant_key = hashlib.sha256(tenant_scope.encode("utf-8")).hexdigest()[:24]
        lineage_key = hashlib.sha256(recording_lineage.encode("utf-8")).hexdigest()[:24]
        path = f"recording-v3/{tenant_key}/{lineage_key}/{uuid4().hex}"
        return self._client.write_secret(path, {
            "value_b64": base64.b64encode(bytes(plaintext)).decode("ascii"),
            "value_type": str(value_type),
            "retention": str(getattr(retention, "value", retention)),
        })


def _storage_headers(storage_state: dict[str, Any] | None, base_url: str) -> dict[str, str]:
    state = storage_state or {}
    target = urlparse(base_url)
    host = (target.hostname or "").lower()
    target_port = target.port or (443 if target.scheme.lower() == "https" else 80)
    cookies: list[str] = []
    for item in state.get("cookies") or []:
        raw_domain = str(item.get("domain") or "").lower()
        domain = raw_domain.lstrip(".")
        domain_matches = (
            host == domain or (raw_domain.startswith(".") and host.endswith("." + domain))
        )
        if not host or not domain or not domain_matches:
            continue
        if item.get("secure") and target.scheme.lower() != "https":
            continue
        expires = item.get("expires")
        if isinstance(expires, (int, float)) and expires > 0 and expires <= time.time():
            continue
        cookie_path = str(item.get("path") or "/")
        request_path = target.path or "/"
        if not cookie_path.startswith("/"):
            cookie_path = "/"
        path_matches = request_path == cookie_path or (
            request_path.startswith(cookie_path)
            and (cookie_path.endswith("/") or request_path[len(cookie_path):].startswith("/"))
        )
        if not path_matches:
            continue
        if item.get("name") and item.get("value") is not None:
            cookies.append(f"{item['name']}={item['value']}")
    headers: dict[str, str] = {}
    if cookies:
        headers["Cookie"] = "; ".join(cookies)
    for origin in state.get("origins") or []:
        source = urlparse(str(origin.get("origin") or ""))
        source_port = source.port or (443 if source.scheme.lower() == "https" else 80)
        if (
            source.scheme.lower() != target.scheme.lower()
            or (source.hostname or "").lower() != host
            or source_port != target_port
        ):
            continue
        for item in origin.get("localStorage") or []:
            key = str(item.get("name") or "").lower()
            value = str(item.get("value") or "")
            if key in {"authorization", "access_token", "token"} and value:
                headers["Authorization"] = value if value.lower().startswith("bearer ") else f"Bearer {value}"
                return headers
    return headers


def _trusted_headers(
    storage_state: dict[str, Any] | None,
    base_url: str,
    credential_headers: dict[str, Any] | None,
) -> dict[str, str]:
    headers = _storage_headers(storage_state, base_url)
    allowed = {"authorization", "cookie", "x-api-key", "x-auth-token"}
    for key, value in (credential_headers or {}).items():
        if str(key).lower() in allowed and value not in (None, ""):
            headers[str(key)] = str(value)
    return headers


def _trusted_header_provider(
    storage_state: dict[str, Any] | None,
    credential_headers: dict[str, Any] | None,
) -> Callable[[str], dict[str, str]]:
    """Resolve browser cookies for each concrete request URL.

    Flattening a storage state against only ``base_url`` would send a
    path-scoped cookie to every step on the origin.  The runtime asks this
    provider after URL rendering, so RFC-style domain/path/secure/expiry scope
    is applied independently to every request.
    """

    explicit = _trusted_headers(None, "https://credential.invalid/", credential_headers)

    def resolve(url: str) -> dict[str, str]:
        return {**_storage_headers(storage_state, url), **explicit}

    return resolve


async def execute_v3_capability(
    *,
    api_request: dict[str, Any],
    fields: dict[str, Any],
    capability: str,
    confirm: bool,
    dry_run: bool,
    base_url: str,
    storage_state: dict[str, Any] | None,
    credential_headers: dict[str, Any] | None = None,
    runtime_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if api_request.get("recording_engine") != "playwright_v3":
        raise ValueError("V3 runtime requires the exact playwright_v3 asset marker")
    ensure_recording_package()
    from dano_recording.runtime import execute_recording_capability
    from dano.config import get_settings

    return await execute_recording_capability(
        api_request,
        fields,
        capability=capability,
        confirm=confirm,
        dry_run=dry_run,
        base_url=base_url,
        credential_headers=_trusted_header_provider(storage_state, credential_headers),
        runtime_context=runtime_context,
        allow_private_networks=get_settings().recording_allow_private_networks,
    )


async def list_v3_field_options(
    *,
    api_request: dict[str, Any],
    field: str,
    capability: str | None,
    base_url: str,
    storage_state: dict[str, Any] | None,
    credential_headers: dict[str, Any] | None = None,
    runtime_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve V3 options through the same verified/safe runtime as execution."""

    if api_request.get("recording_engine") != "playwright_v3":
        raise ValueError("V3 option listing requires the exact playwright_v3 asset marker")
    ensure_recording_package()
    from dano_recording.runtime import list_recording_field_options
    from dano.config import get_settings

    return await list_recording_field_options(
        api_request,
        field,
        capability=capability,
        base_url=base_url,
        credential_headers=_trusted_header_provider(storage_state, credential_headers),
        runtime_context=runtime_context,
        allow_private_networks=get_settings().recording_allow_private_networks,
    )


def install_recording_v3(app, **kwargs):  # noqa: ANN001, ANN003
    ensure_recording_package()
    from dano_recording.app import install_recording_v3 as install
    from dano_recording.integrations.dano.assets import DanoAssetPublisher

    from dano.config import get_settings

    settings = get_settings()
    kwargs.setdefault(
        "evidence_hmac_secret",
        _stable_evidence_secret(
            settings.recording_evidence_hmac_key,
            key_file=Path(__file__).resolve().parents[1] / ".secrets" / "recording-v3-evidence.key",
        ),
    )
    # Credential capture has no file/database fallback.  Without an actual
    # Vault configuration ValueEvidenceFactory rejects credential plaintext at
    # the trust boundary instead of persisting it elsewhere.
    if settings.vault_token or settings.require_vault:
        kwargs.setdefault("credential_vault", _DanoCredentialVault())
    kwargs.setdefault("asset_writer", DanoAssetPublisher())
    kwargs.setdefault("browser_headless", settings.browser_headless)
    kwargs.setdefault("allow_private_networks", settings.recording_allow_private_networks)
    kwargs.setdefault("persistent_repository_required", True)
    kwargs.setdefault("artifact_root", Path(__file__).resolve().parents[1] / ".recording-v3-artifacts")
    kwargs.setdefault("pi_env", {
        "DANO_PI_API_KEY": settings.pi_api_key,
        "DANO_PI_BASE_URL": settings.pi_base_url,
        "DANO_PI_PROVIDER": settings.pi_provider,
        "DANO_PI_MODEL": settings.pi_model,
        "DANO_PI_SESSION_DIR": str(
            Path(__file__).resolve().parents[1] / ".recording-v3-pi-sessions"
        ),
    })

    return install(app, **kwargs)
