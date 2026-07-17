"""Application service that owns the complete recording-v3 lifecycle.

Facts, revisions, Pi sessions and release artifacts live behind one tenant-scoped
boundary.  The legacy recorder and Python model clients are deliberately absent.
"""

from __future__ import annotations

import asyncio
import base64
from collections import defaultdict
from collections.abc import Mapping
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import secrets
from typing import Any, Awaitable, Callable, Protocol
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from dano_recording.api.auth import WebSocketTicketManager, hash_token, token_matches
from dano_recording.api.decision_commands import (
    DecisionCommandError,
    apply_edits,
    apply_replacement,
    merge_pi_submission,
    rebase_user_decisions,
    validate_workbench,
)
from dano_recording.api.events import RecordingEventBroker
from dano_recording.api.protocol import CreateRecordingRequest, SessionConnectionResponse
from dano_recording.capture.browser_session import BrowserCapture, BrowserSessionManager
from dano_recording.capture.input_dispatcher import describe_click_target
from dano_recording.capture.ledger import FactLedger
from dano_recording.capture.network_observer import NetworkObserver, NetworkObserverConfig
from dano_recording.capture.redaction import RedactionPolicy
from dano_recording.capture.runtime import CaptureRuntime
from dano_recording.capture.safety import URLSafetyPolicy
from dano_recording.capture_store import CaptureRecordKind, CaptureStore
from dano_recording.compiler.client_projection import compilation_to_workbench
from dano_recording.compiler.fingerprint import content_hash
from dano_recording.compiler.models import RecordingCompilation
from dano_recording.compiler.pipeline import (
    complete_contract_projection,
    compile_recording,
    integrate_compilation_contracts,
    prepare_recording_materials,
)
from dano_recording.domain._base import new_id
from dano_recording.domain.facts import ActionFact, FactKind, RecordingFact, RequestFact
from dano_recording.domain.enums import ChoiceEvidenceSource
from dano_recording.domain.operations import OperationStatus, RecordingOperation
from dano_recording.domain.pi import (
    PiEvent as StoredPiEvent,
    PiRole,
    PiSessionMetadata,
    PiSessionStatus as StoredPiStatus,
)
from dano_recording.domain.recording import RecordingSession, RecordingStatus
from dano_recording.domain.revisions import RecordingArtifact
from dano_recording.evidence.dom_controls import DOMControlCollector
from dano_recording.evidence.bindings import correlate_recording_evidence
from dano_recording.evidence.enum_extractor import EnumExtractor
from dano_recording.evidence.js_ast_worker import JSStaticAnalyzer
from dano_recording.evidence.loaded_scripts import LoadedScript, LoadedScriptCollector
from dano_recording.evidence.provenance import EvidenceRegistry, project_evidence_for_pi
from dano_recording.evidence.runtime_components import RuntimeComponentCollector
from dano_recording.evidence.sourcemaps import SourceMapFetchResult, SourceMapLoader
from dano_recording.field_registry import (
    ControlEvidence,
    FieldAlias,
    FieldAliasKind,
    FieldRegistry,
)
from dano_recording.flow_migration import FlowMigrator
from dano_recording.persistence.memory import InMemoryRecordingRepository
from dano_recording.persistence.repository import (
    RecordingRepository,
    RevisionConflict,
)
from dano_recording.pi.coordinator import PiPlanMode, RecordingPiCoordinator
from dano_recording.pi.sessions import PiSidecarClient, PiUnavailable
from dano_recording.pi_semantic_ops import is_semantic_operation_submission
from dano_recording.publish.review import ReviewCollector
from dano_recording.publish.service import RecordingPublishService
from dano_recording.value_evidence import CredentialVault, ValueEvidenceFactory


EventSender = Callable[[dict[str, Any]], Awaitable[None]]
LifecycleCallback = Callable[[dict[str, Any]], Awaitable[None]]
BrowserLauncher = Callable[[dict[str, Any] | None], Awaitable[tuple[Any, Any]]]


class RecordingUnavailable(RuntimeError):
    """Raised when the configured durable recording store is unavailable."""


class AssetWriter(Protocol):
    async def publish(self, **kwargs: Any) -> dict[str, Any]: ...


class _OwnedBrowser:
    """Close both Playwright's browser process and its driver connection."""

    def __init__(self, browser: Any, owner: Any) -> None:
        self.browser = browser
        self.owner = owner

    def __getattr__(self, name: str) -> Any:
        return getattr(self.browser, name)

    async def close(self) -> None:
        try:
            await self.browser.close()
        finally:
            await self.owner.stop()


@dataclass(slots=True)
class LiveRecording:
    tenant: str
    recording_id: str
    ledger: FactLedger
    lineage_id: UUID
    capture_store: CaptureStore
    field_registry: FieldRegistry
    evidence: EvidenceRegistry = field(default_factory=EvidenceRegistry)
    runtime: CaptureRuntime | None = None
    browser: Any | None = None
    context: Any | None = None
    page: Any | None = None
    capture: BrowserCapture | None = None
    network: NetworkObserver | None = None
    scripts: LoadedScriptCollector | None = None
    frame_task: asyncio.Task[None] | None = None
    analysis_task: asyncio.Task[Any] | None = None
    analysis_state: str = "idle"
    analysis_stage: str = "idle"
    analysis_progress: int = 0
    analysis_operation_id: str | None = None
    capture_generation: int = 0
    persistence_tasks: set[asyncio.Task[Any]] = field(default_factory=set)
    persistence_failures: list[BaseException] = field(default_factory=list)
    background_tasks: set[asyncio.Task[Any]] = field(default_factory=set)
    last_action_id: str | None = None
    reset_sequence: int = 0
    frame_sequence: int = 0
    started: bool = False
    capture_active: bool = False
    capture_end_sequence: int | None = None
    analysis_fact_ids: set[str] = field(default_factory=set)
    analysis_evidence_active: bool = False
    capture_finalized: bool = False
    artifact_hashes: set[tuple[str, str]] = field(default_factory=set)


class RecordingApplication:
    def __init__(
        self,
        *,
        repository: RecordingRepository | None = None,
        asset_writer: AssetWriter | None = None,
        lifecycle_callback: LifecycleCallback | None = None,
        export_callback: Callable[[str], Awaitable[None]] | None = None,
        pi_env: dict[str, str] | None = None,
        browser_launcher: BrowserLauncher | None = None,
        browser_headless: bool = True,
        allow_private_networks: bool = True,
        artifact_root: str | Path | None = None,
        persistent_repository_required: bool = False,
        analysis_timeout_seconds: float = 600.0,
        evidence_hmac_secret: bytes | None = None,
        credential_vault: CredentialVault | None = None,
    ) -> None:
        self.repository: RecordingRepository = repository or InMemoryRecordingRepository()
        self.tickets = WebSocketTicketManager()
        self.events = RecordingEventBroker()
        self.browser_sessions = BrowserSessionManager(lease_seconds=180, max_sessions=64)
        self.browser_launcher = browser_launcher or self._launch_browser
        self.browser_headless = browser_headless
        self.allow_private_networks = allow_private_networks
        self.persistent_repository_required = persistent_repository_required
        self._availability_error: str | None = None
        if analysis_timeout_seconds <= 0:
            raise ValueError("analysis_timeout_seconds must be positive")
        self.analysis_timeout_seconds = float(analysis_timeout_seconds)
        self.redaction = RedactionPolicy()
        configured_evidence_secret = os.environ.get("DANO_RECORDING_EVIDENCE_HMAC_KEY")
        resolved_evidence_secret = evidence_hmac_secret or (
            configured_evidence_secret.encode("utf-8")
            if configured_evidence_secret
            else secrets.token_bytes(32)
        )
        self.value_evidence_factory = ValueEvidenceFactory(
            server_secret=resolved_evidence_secret,
            credential_vault=credential_vault,
            redaction=self.redaction,
        )
        self.artifact_root = Path(artifact_root or Path.cwd() / ".recording-v3-artifacts").resolve()
        self.lifecycle_callback = lifecycle_callback
        self.export_callback = export_callback
        self.live: dict[tuple[str, str], LiveRecording] = {}
        self._recording_tenants: dict[str, str] = {}
        self._resume_tokens: dict[tuple[str, str], str] = {}
        self._socket_counts: dict[tuple[str, str], int] = {}
        self._service_tasks: set[asyncio.Task[Any]] = set()
        # A globally unique operation_id can be resumed after process restart,
        # but must never be claimed twice by tasks in this process.
        self._active_publish_operations: set[str] = set()
        self._started = False
        self.review_collector = ReviewCollector()

        script = Path(__file__).resolve().parent / "pi" / "runtime" / "sidecar.mjs"
        self.pi_client = PiSidecarClient(
            script_path=script,
            tool_handler=self._handle_pi_tool,
            event_handler=self._handle_pi_event,
            env=pi_env,
        )
        self.pi = RecordingPiCoordinator(
            client=self.pi_client,
            state_provider=self._pi_state,
            submission_handler=self._pi_submission,
            event_sink=self._pi_event_sink,
        )
        self.publisher = RecordingPublishService(
            snapshot_provider=self._publish_snapshot,
            review_runner=self.pi.review,
            review_collector=self.review_collector,
            asset_writer=asset_writer or _MissingAssetWriter(),
        )

    async def start(self, *, repository: RecordingRepository | None = None) -> None:
        if repository is not None:
            if self._started and self.live:
                raise RuntimeError("cannot replace the recording repository while sessions are active")
            self.repository = repository
            self._availability_error = None
        elif self.persistent_repository_required and isinstance(
            self.repository,
            InMemoryRecordingRepository,
        ):
            self._availability_error = (
                "recording-v3 durable repository is unavailable; refusing in-memory fallback"
            )
        if self._started:
            return
        self._started = True
        self.browser_sessions.start_cleanup()

    def _ensure_available(self) -> None:
        if self._availability_error:
            raise RecordingUnavailable(self._availability_error)

    async def close(self) -> None:
        if not self._started:
            return
        for task in tuple(self._service_tasks):
            task.cancel()
        if self._service_tasks:
            await asyncio.gather(*self._service_tasks, return_exceptions=True)
        for live in tuple(self.live.values()):
            await self._close_live(live, close_browser=False)
        self.live.clear()
        await self.browser_sessions.close()
        await self.pi_client.close()
        self._started = False

    async def create_session(
        self,
        tenant: str,
        request: CreateRecordingRequest,
        *,
        subject: str = "",
    ) -> SessionConnectionResponse:
        self._ensure_available()
        self._validate_target(request.start_url, request.base_url)
        recording_id = uuid4().hex
        lineage_id = uuid4()
        resume_token = secrets.token_urlsafe(40)
        session = RecordingSession(
            tenant=tenant,
            recording_id=recording_id,
            status=RecordingStatus.CREATED,
            base_url=request.base_url or _origin(request.start_url),
            resume_token_hash=hash_token(resume_token),
            metadata={
                "subsystem": request.subsystem,
                "start_url": request.start_url,
                "base_url": request.base_url or _origin(request.start_url),
                "recording_mode": request.recording_mode,
                "recording_engine": "playwright_v3",
                "lineage_id": str(lineage_id),
                "capture_generation": 0,
            },
        )
        await self.repository.create_session(session)
        self._recording_tenants[recording_id] = tenant
        self._resume_tokens[(tenant, recording_id)] = resume_token
        ticket, expires_at = await self.tickets.issue(
            tenant=tenant,
            recording_id=recording_id,
            subject=subject,
        )
        return SessionConnectionResponse(
            recording_id=recording_id,
            websocket_ticket=ticket,
            ticket_expires_at=expires_at.isoformat(),
            current_revision=0,
            resume_token=resume_token,
            snapshot=None,
            pi_status={},
        )

    async def resume_session(
        self,
        tenant: str,
        recording_id: str,
        resume_token: str,
        *,
        subject: str = "",
    ) -> SessionConnectionResponse:
        self._ensure_available()
        session = await self.repository.get_session(tenant, recording_id)
        if not token_matches(resume_token, session.resume_token_hash):
            raise PermissionError("resume token does not match recording")
        self._recording_tenants[recording_id] = tenant
        self._resume_tokens[(tenant, recording_id)] = resume_token
        browser_available = False
        try:
            await self.browser_sessions.open(
                tenant=tenant,
                recording_id=recording_id,
                resume_token=resume_token,
            )
            browser_available = True
        except (LookupError, PermissionError):
            # The persisted revision remains resumable even when its browser
            # lease has expired or the process was restarted.
            browser_available = False
        if not browser_available:
            stale = self.live.pop((tenant, recording_id), None)
            if stale is not None:
                await self._close_live(stale, close_browser=False)
        revision = await self.repository.get_revision(tenant, recording_id)
        pi_events = await self.repository.list_pi_events(tenant, recording_id)
        ticket, expires_at = await self.tickets.issue(
            tenant=tenant,
            recording_id=recording_id,
            subject=subject,
        )
        snapshot_value: dict[str, Any] | None = None
        if revision is not None:
            lineage_id = self._lineage_id(
                tenant,
                recording_id,
                session=session,
                snapshot=revision.snapshot,
            )
            snapshot_value, _ = self._migrate_flow_snapshot(
                tenant=tenant,
                recording_id=recording_id,
                lineage_id=lineage_id,
                snapshot=revision.snapshot,
            )
            if str(session.metadata.get("lineage_id") or "") != str(lineage_id):
                session = await self.repository.update_session(
                    tenant,
                    recording_id,
                    metadata={"lineage_id": str(lineage_id)},
                )
        snapshot = self._client_snapshot(snapshot_value, browser_available=browser_available) if snapshot_value else None
        if snapshot is not None:
            snapshot["pi_timeline"] = [
                event.model_dump(mode="json") for event in pi_events[-250:]
            ]
        return SessionConnectionResponse(
            recording_id=recording_id,
            websocket_ticket=ticket,
            ticket_expires_at=expires_at.isoformat(),
            current_revision=session.current_revision,
            resume_token=resume_token,
            snapshot=snapshot,
            pi_status=self._client_pi_status(recording_id),
        )

    async def consume_ticket(self, ticket: str, *, recording_id: str) -> str:
        self._ensure_available()
        grant = await self.tickets.consume(ticket, recording_id=recording_id)
        await self.repository.get_session(grant.tenant, recording_id)
        self._recording_tenants[recording_id] = grant.tenant
        return grant.tenant

    @staticmethod
    def _lineage_id(
        tenant: str,
        recording_id: str,
        *,
        session: RecordingSession | None = None,
        snapshot: dict[str, Any] | None = None,
    ) -> UUID:
        for value in (
            (snapshot or {}).get("lineage_id"),
            ((snapshot or {}).get("meta") or {}).get("lineage_id"),
            (session.metadata if session else {}).get("lineage_id"),
        ):
            if value:
                try:
                    return UUID(str(value))
                except (TypeError, ValueError):
                    continue
        # A legacy session has no lineage record.  The fallback is stable
        # across process restarts and is persisted on the next session update.
        return uuid5(NAMESPACE_URL, f"dano-recording:{tenant}:{recording_id}")

    def _migrate_flow_snapshot(
        self,
        *,
        tenant: str,
        recording_id: str,
        lineage_id: UUID,
        snapshot: dict[str, Any],
        registry: FieldRegistry | None = None,
    ) -> tuple[dict[str, Any], FieldRegistry]:
        result = FlowMigrator(
            lineage_id=lineage_id,
            registry=registry,
            value_evidence_factory=self.value_evidence_factory,
            tenant_scope=tenant,
            redaction=self.redaction,
        ).migrate(snapshot)
        value = result.model_dump(mode="json")["snapshot"]
        value["recording_id"] = recording_id
        value["tenant"] = tenant
        value.setdefault("meta", {})["recording_engine"] = "playwright_v3"
        return value, FieldRegistry.from_snapshot(result.registry)

    def _client_snapshot(self, snapshot: dict[str, Any], *, browser_available: bool = True) -> dict[str, Any]:
        spec = deepcopy(snapshot)
        facts = spec.get("request_facts") or {}
        requests = facts.get("requests") if isinstance(facts, dict) else facts
        fields = [param for step in spec.get("steps") or [] for param in step.get("params") or []]
        return {
            "full_spec": spec,
            "check_report": spec.get("validation") or validate_workbench(deepcopy(spec)),
            "steps": [],
            "requests": list(requests or []),
            "fields": fields,
            "action": spec.get("action") or "",
            "title": spec.get("title") or "",
            "start_url": spec.get("start_url") or "",
            "recording_mode": spec.get("recording_mode") or "record_only",
            "browser_available": browser_available,
            "pi_status": self._client_pi_status(str(spec.get("recording_id") or "")),
        }

    def _validate_target(self, start_url: str, base_url: str = "") -> None:
        hosts = tuple(
            dict.fromkeys(
                (urlsplit(value).hostname or "").lower()
                for value in (start_url, base_url)
                if value and urlsplit(value).hostname
            )
        )
        policy = URLSafetyPolicy(
            allowed_hosts=hosts,
            allow_private_networks=self.allow_private_networks,
        )
        policy.validate(start_url)
        if base_url:
            policy.validate(base_url)

    async def _launch_browser(self, storage_state: dict[str, Any] | None) -> tuple[Any, Any]:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError(
                "Playwright browser support is not installed; install dano-back[page] and Chromium"
            ) from exc
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(headless=self.browser_headless)
        context = await browser.new_context(storage_state=storage_state or None)
        # Browser.close does not own the Playwright driver handle; retain it on
        # the browser for deterministic shutdown without leaking processes.
        return _OwnedBrowser(browser, playwright), context

    async def attach_socket(self, tenant: str, recording_id: str, sender: EventSender) -> None:
        self._ensure_available()
        await self.repository.get_session(tenant, recording_id)
        self.events.subscribe(tenant, recording_id, sender)
        key = (tenant, recording_id)
        self._socket_counts[key] = self._socket_counts.get(key, 0) + 1

    async def detach_socket(self, tenant: str, recording_id: str, sender: EventSender) -> None:
        self.events.unsubscribe(tenant, recording_id, sender)
        key = (tenant, recording_id)
        remaining = max(0, self._socket_counts.get(key, 1) - 1)
        if remaining:
            self._socket_counts[key] = remaining
            return
        self._socket_counts.pop(key, None)
        live = self.live.get((tenant, recording_id))
        if live and live.started:
            try:
                lease = await self.browser_sessions.detach(tenant=tenant, recording_id=recording_id)
                await self.repository.update_session(
                    tenant,
                    recording_id,
                    browser_lease_until=lease.expires_at,
                )
            except LookupError:
                pass

    async def handle_message(
        self,
        tenant: str,
        recording_id: str,
        message: dict[str, Any],
        sender: EventSender,
    ) -> bool:
        """Handle one compatibility workbench message; return True to close."""

        try:
            self._ensure_available()
            kind = str(message.get("type") or "")
            if kind == "start":
                await self._start_capture(tenant, recording_id, message)
            elif kind == "input":
                await self._dispatch_input(tenant, recording_id, dict(message.get("event") or {}))
            elif kind == "reset":
                await self._reset_capture(tenant, recording_id)
            elif kind == "choose_request":
                await self._choose_request(tenant, recording_id, int(message.get("idx") or 0))
            elif kind == "finalize":
                await self._start_analysis(tenant, recording_id, message)
            elif kind == "reanalyze":
                await self._start_analysis(
                    tenant,
                    recording_id,
                    {**message, "type": "finalize"},
                )
            elif kind == "recapture":
                await self._recapture_command(tenant, recording_id)
            elif kind == "cancel_analysis":
                await self._cancel_analysis(tenant, recording_id, sender)
            elif kind in {"analysis_status", "get_analysis_status"}:
                await self._send_analysis_status(tenant, recording_id, sender)
            elif kind == "retry_pi":
                session = await self.repository.get_session(tenant, recording_id)
                if session.current_revision < 1:
                    raise DecisionCommandError("finalize the deterministic draft before retrying Pi")
                self._spawn(
                    self._run_initial_pi(tenant, recording_id, session.current_revision),
                    name=f"recording-pi-retry-{recording_id}-{session.current_revision}",
                )
            elif kind == "refresh_flow_spec":
                await self._send_snapshot(tenant, recording_id, sender)
            elif kind in {"flow_update", "flow_replace"}:
                await self._flow_command(tenant, recording_id, message, sender)
            elif kind in {
                "orchestrate_flow",
                "auto_fix_flow",
                "step_naming",
                "business_description",
                "llm_recommendations",
            }:
                self._spawn(
                    self._pi_command(tenant, recording_id, message),
                    name=f"recording-pi-{kind}-{recording_id}",
                )
            elif kind == "publish_request":
                self._spawn(
                    self._publish_command(tenant, recording_id, message),
                    name=f"recording-publish-{recording_id}",
                )
            elif kind == "console_log_upload":
                await self._record_console(tenant, recording_id, message.get("entries"))
            elif kind == "stop":
                await self.stop_recording(tenant, recording_id)
                await sender({"type": "stopped", "recording_id": recording_id})
                return True
            elif kind in {"ping", "keepalive"}:
                await sender({"type": "pong", "recording_id": recording_id})
            else:
                raise DecisionCommandError(f"unsupported recording message: {kind}")
        except RevisionConflict as exc:
            revision = await self.repository.get_revision(tenant, recording_id)
            spec = revision.snapshot if revision else None
            await sender({
                "type": "error",
                "code": "revision_conflict",
                "detail": str(exc),
                "retryable": True,
                "operation": message.get("type"),
                "operation_id": message.get("operation_id"),
                "expected_revision": exc.expected,
                "actual_revision": exc.actual,
                "revision": exc.actual,
                "full_spec": spec,
                "check_report": validate_workbench(deepcopy(spec)) if spec else None,
            })
        except (DecisionCommandError, ValueError, PermissionError) as exc:
            await sender({
                "type": "error",
                "code": "invalid_recording_command",
                "detail": str(exc),
                "retryable": False,
                "operation": message.get("type"),
                "operation_id": message.get("operation_id"),
            })
        except Exception as exc:  # noqa: BLE001 - explicit retryable boundary
            await sender({
                "type": "error",
                "code": "recording_internal_error",
                "detail": str(exc),
                "retryable": True,
                "operation": message.get("type"),
                "operation_id": message.get("operation_id"),
            })
        return False

    async def _start_analysis(
        self,
        tenant: str,
        recording_id: str,
        message: dict[str, Any],
    ) -> None:
        """Start deterministic compilation without blocking the socket reader."""

        live = await self._get_live(tenant, recording_id)
        active = live.analysis_task
        if active is not None and not active.done():
            requested = str(message.get("operation_id") or "")
            if requested and requested == live.analysis_operation_id:
                await self.events.publish(
                    tenant,
                    recording_id,
                    self._analysis_status_payload(live),
                )
                return
            raise DecisionCommandError("recording analysis is already in progress")

        operation, replay = await self._begin_operation(
            tenant,
            recording_id,
            message,
            kind="finalize",
        )
        if replay is not None:
            await self.events.publish(tenant, recording_id, replay)
            return

        live.analysis_state = "running"
        live.analysis_stage = "queued"
        live.analysis_progress = 0
        live.analysis_operation_id = operation.operation_id
        await self.events.publish(tenant, recording_id, {
            **self._analysis_status_payload(live),
            "type": "analysis_started",
        })
        task = self._spawn(
            self._analysis_runner(live, message, operation),
            name=f"recording-analysis-{recording_id}-{operation.operation_id}",
        )
        live.analysis_task = task
        live.background_tasks.add(task)
        task.add_done_callback(live.background_tasks.discard)

    async def _analysis_runner(
        self,
        live: LiveRecording,
        message: dict[str, Any],
        operation: RecordingOperation,
    ) -> None:
        try:
            await asyncio.wait_for(
                self._finalize_command(
                    live.tenant,
                    live.recording_id,
                    message,
                    operation=operation,
                ),
                timeout=self.analysis_timeout_seconds,
            )
            live.analysis_state = "completed"
            live.analysis_stage = "complete"
            live.analysis_progress = 100
            await self.events.publish(
                live.tenant,
                live.recording_id,
                self._analysis_status_payload(live),
            )
        except asyncio.CancelledError:
            live.analysis_state = "cancelled"
            live.analysis_stage = "cancelled"
            try:
                await self.repository.complete_operation(
                    live.tenant,
                    operation.operation_id,
                    error="analysis cancelled",
                )
            except Exception:  # The command may already have reached a terminal state.
                pass
            try:
                await self.repository.update_session(
                    live.tenant,
                    live.recording_id,
                    status=RecordingStatus.DRAFT,
                )
            except Exception:
                pass
            await self.events.publish(live.tenant, live.recording_id, {
                **self._analysis_status_payload(live),
                "type": "analysis_cancelled",
            })
            raise
        except Exception as exc:  # noqa: BLE001 - background command boundary
            live.analysis_state = "failed"
            live.analysis_stage = "failed"
            detail = (
                f"analysis exceeded {self.analysis_timeout_seconds:g} seconds"
                if isinstance(exc, TimeoutError)
                else str(exc)
            )
            try:
                await self.repository.complete_operation(
                    live.tenant,
                    operation.operation_id,
                    error=detail,
                )
            except Exception:  # The inner command may already have completed it.
                pass
            try:
                await self.repository.update_session(
                    live.tenant,
                    live.recording_id,
                    status=RecordingStatus.DRAFT,
                )
            except Exception:
                pass
            await self.events.publish(live.tenant, live.recording_id, {
                **self._analysis_status_payload(live),
                "type": "error",
                "code": "analysis_failed",
                "detail": detail,
                "retryable": True,
                "operation": "finalize",
                "operation_id": operation.operation_id,
            })
        finally:
            if live.analysis_task is asyncio.current_task():
                live.analysis_task = None

    def _analysis_status_payload(self, live: LiveRecording) -> dict[str, Any]:
        return {
            "type": "analysis_status",
            "recording_id": live.recording_id,
            "state": live.analysis_state,
            "stage": live.analysis_stage,
            "progress": live.analysis_progress,
            "operation": "finalize",
            "operation_id": live.analysis_operation_id,
            "capture_generation": live.capture_generation,
        }

    async def _analysis_progress(
        self,
        live: LiveRecording,
        *,
        stage: str,
        progress: int,
    ) -> None:
        live.analysis_stage = stage
        live.analysis_progress = max(live.analysis_progress, min(100, max(0, progress)))
        await self.events.publish(
            live.tenant,
            live.recording_id,
            self._analysis_status_payload(live),
        )

    async def _send_analysis_status(
        self,
        tenant: str,
        recording_id: str,
        sender: EventSender,
    ) -> None:
        live = await self._get_live(tenant, recording_id)
        await sender(self._analysis_status_payload(live))

    async def wait_for_analysis(
        self,
        tenant: str,
        recording_id: str,
        *,
        timeout: float = 60.0,
    ) -> None:
        """Wait for the current background analysis (primarily for adapters/tests)."""

        live = await self._get_live(tenant, recording_id)
        task = live.analysis_task
        if task is not None and not task.done():
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)

    async def _cancel_analysis(
        self,
        tenant: str,
        recording_id: str,
        sender: EventSender | None = None,
    ) -> None:
        live = await self._get_live(tenant, recording_id)
        task = live.analysis_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        elif sender is not None:
            await sender(self._analysis_status_payload(live))

    async def _freeze_capture(self, live: LiveRecording) -> None:
        """Freeze one immutable capture boundary without closing the browser.

        Closing observers drains their in-flight tasks.  The exclusive ledger
        sequence recorded afterwards is then used by every compiler pass, so a
        late callback or unrelated diagnostic can never drift the revision.
        Cancellation waits for this small critical section before propagating.
        """

        if not live.capture_active and live.capture_end_sequence is not None:
            await self.repository.update_session(
                live.tenant,
                live.recording_id,
                metadata={
                    "capture_end_sequence": live.capture_end_sequence,
                    "analysis_fact_ids": sorted(live.analysis_fact_ids),
                },
            )
            return
        live.capture_active = False

        async def finish_freeze() -> None:
            if live.frame_task is not None:
                live.frame_task.cancel()
                await asyncio.gather(live.frame_task, return_exceptions=True)
                live.frame_task = None
            runtime = live.runtime
            if runtime is not None:
                await runtime.pause()
            else:
                seen: set[int] = set()
                for observer in (live.network, live.scripts, live.capture):
                    if observer is None or id(observer) in seen:
                        continue
                    seen.add(id(observer))
                    pause = getattr(observer, "pause", None)
                    if pause is not None:
                        value = pause()
                        if asyncio.iscoroutine(value):
                            await value
            await self._drain_facts(live)
            live.capture_end_sequence = live.ledger.next_sequence
            await self.repository.update_session(
                live.tenant,
                live.recording_id,
                metadata={
                    "capture_end_sequence": live.capture_end_sequence,
                    "analysis_fact_ids": sorted(live.analysis_fact_ids),
                },
            )

        task = asyncio.create_task(
            finish_freeze(),
            name=f"recording-freeze-{live.recording_id}",
        )
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.gather(task, return_exceptions=True)
            raise

    async def _capture_generation_facts(
        self,
        live: LiveRecording,
    ) -> tuple[RecordingFact, ...]:
        rows = await self.repository.list_facts(live.tenant, live.recording_id)
        end = live.capture_end_sequence
        return tuple(
            fact
            for fact in rows
            if fact.sequence >= live.reset_sequence
            and (
                end is None
                or fact.sequence < end
                or fact.fact_id in live.analysis_fact_ids
            )
        )

    async def _recapture_command(self, tenant: str, recording_id: str) -> None:
        live = await self._get_live(tenant, recording_id)
        if live.analysis_task is not None and not live.analysis_task.done():
            await self._cancel_analysis(tenant, recording_id)
        if live.capture_active:
            await self._freeze_capture(live)
        # The generation and replay boundary form one persisted checkpoint.
        # Writing them separately leaves a crash window where restart restores
        # the new generation with the old boundary and reprojects prior facts.
        next_store = live.capture_store.next_generation()
        next_reset_sequence = live.ledger.next_sequence
        await self.repository.update_session(
            tenant,
            recording_id,
            metadata={
                "capture_generation": next_store.capture_generation,
                "reset_sequence": next_reset_sequence,
                "capture_end_sequence": None,
                "analysis_fact_ids": [],
            },
        )
        if live.runtime is not None:
            await live.runtime.reset_generation()
        else:
            if live.network is not None:
                reset_network = getattr(live.network, "reset_generation", None)
                if reset_network is not None:
                    reset_network()
            if live.scripts is not None:
                reset_scripts = getattr(live.scripts, "reset_generation", None)
                if reset_scripts is not None:
                    value = reset_scripts()
                    if asyncio.iscoroutine(value):
                        await value
        # Do not mutate live state until the durable checkpoint succeeds.  A
        # transient repository failure therefore leaves the current capture
        # generation fully usable and retryable.
        live.capture_store = next_store
        live.capture_generation = next_store.capture_generation
        live.capture_finalized = False
        live.capture_end_sequence = None
        live.analysis_fact_ids.clear()
        live.evidence = EvidenceRegistry()
        live.reset_sequence = next_reset_sequence
        live.ledger.emit(
            RecordingFact,
            kind=FactKind.DIAGNOSTIC,
            payload={"type": "recording_reset"},
            redacted=True,
        )
        if live.started:
            await self._resume_capture(live)
        await self.events.publish(tenant, recording_id, {"type": "started", "reset": True})
        await self.events.publish(tenant, recording_id, {
            "type": "recapture_started",
            "recording_id": recording_id,
            "capture_generation": live.capture_generation,
        })

    def _record_fact_in_capture_store(
        self,
        live: LiveRecording,
        fact: RecordingFact,
    ) -> None:
        kind: CaptureRecordKind | None = None
        payload: dict[str, Any]
        if isinstance(fact, ActionFact):
            kind = CaptureRecordKind.ACTION
            payload = {
                "action_type": fact.action_type,
                "label": fact.label,
                "locator": fact.locator,
                **dict(fact.payload),
            }
        elif isinstance(fact, RequestFact):
            kind = (
                CaptureRecordKind.SUBMIT
                if fact.method in {"POST", "PUT", "PATCH", "DELETE"}
                else CaptureRecordKind.RESPONSE
            )
            payload = {
                "request_id": fact.request_id,
                "method": fact.method,
                "url": fact.url,
                "resource_type": fact.resource_type,
                "headers": dict(fact.request_headers),
                "body": fact.request_body if fact.request_body_present else None,
                **dict(fact.payload),
            }
        elif fact.kind is FactKind.DOM_CONTROL:
            kind = CaptureRecordKind.DOM
            payload = dict(fact.payload)
        elif fact.kind is FactKind.DOM_MUTATION:
            kind = CaptureRecordKind.MUTATION
            payload = dict(fact.payload)
        elif fact.kind is FactKind.RESPONSE:
            kind = CaptureRecordKind.RESPONSE
            payload = dict(fact.payload)
        else:
            return
        live.capture_store.append_record(
            kind=kind,
            page_id=fact.page_id or "page:unknown",
            frame_id=str(fact.payload.get("frame_id") or "") or None,
            action_id=fact.action_id,
            observed_at=fact.observed_at,
            record_id=fact.fact_id,
            payload=payload,
        )

    @staticmethod
    def _capture_store_accepts_fact(
        live: LiveRecording,
        fact: RecordingFact,
    ) -> bool:
        """Apply the same immutable boundary to generation-owned raw records."""

        end = live.capture_end_sequence
        if end is None or fact.sequence < end:
            return True
        return (
            live.analysis_evidence_active
            and fact.kind in {FactKind.DOM_CONTROL, FactKind.SCRIPT}
        )

    async def _get_live(self, tenant: str, recording_id: str) -> LiveRecording:
        key = (tenant, recording_id)
        live = self.live.get(key)
        if live is not None:
            return live
        facts = await self.repository.list_facts(tenant, recording_id)
        session = await self.repository.get_session(tenant, recording_id)
        reset_sequence = int(session.metadata.get("reset_sequence") or 0)
        capture_generation = int(session.metadata.get("capture_generation") or 0)
        raw_capture_end = session.metadata.get("capture_end_sequence")
        capture_end_sequence = (
            int(raw_capture_end) if raw_capture_end is not None else None
        )
        analysis_fact_ids = {
            str(fact_id)
            for fact_id in session.metadata.get("analysis_fact_ids") or ()
            if str(fact_id)
        }
        revision = await self.repository.get_revision(tenant, recording_id)
        lineage_id = self._lineage_id(
            tenant,
            recording_id,
            session=session,
            snapshot=revision.snapshot if revision else None,
        )
        if str(session.metadata.get("lineage_id") or "") != str(lineage_id):
            session = await self.repository.update_session(
                tenant,
                recording_id,
                metadata={"lineage_id": str(lineage_id)},
            )
        if revision is not None:
            migrated, field_registry = self._migrate_flow_snapshot(
                tenant=tenant,
                recording_id=recording_id,
                lineage_id=lineage_id,
                snapshot=revision.snapshot,
            )
        else:
            migrated = {}
            field_registry = FieldRegistry(lineage_id)
        capture_payload = migrated.get("capture_store")
        if capture_payload and int(capture_payload.get("capture_generation") or 0) == capture_generation:
            capture_store = CaptureStore.from_snapshot(
                capture_payload,
                redaction=self.redaction,
            )
            if (
                capture_store.tenant_scope != tenant
                or capture_store.recording_id != recording_id
                or capture_store.lineage_id != lineage_id
            ):
                raise ValueError("persisted capture store scope does not match session")
            known_script_hashes = {
                item.content_hash for item in capture_store.snapshot().scripts
            }
            for artifact in await self.repository.list_artifacts(
                tenant,
                recording_id,
                kind="javascript_source",
            ):
                digest = str(artifact.content_hash).removeprefix("sha256:")
                if digest not in known_script_hashes:
                    continue
                source_path = Path(artifact.storage_ref).resolve()
                try:
                    source_path.relative_to(self.artifact_root)
                except ValueError:
                    continue
                if not source_path.is_file():
                    continue
                raw = await asyncio.to_thread(source_path.read_bytes)
                capture_store.restore_script_content(digest, raw)
        else:
            capture_store = CaptureStore(
                tenant_scope=tenant,
                recording_id=recording_id,
                lineage_id=lineage_id,
                capture_generation=capture_generation,
                redaction=self.redaction,
            )
        live_ref: dict[str, LiveRecording] = {}

        def on_append(fact: RecordingFact) -> None:
            owner = live_ref["value"]
            if self._capture_store_accepts_fact(owner, fact):
                self._record_fact_in_capture_store(owner, fact)
            task = asyncio.create_task(
                self._persist_and_emit(owner, fact),
                name=f"recording-fact-{recording_id}-{fact.sequence}",
            )
            owner.persistence_tasks.add(task)
            def track_completed(completed: asyncio.Task[Any]) -> None:
                owner.persistence_tasks.discard(completed)
                if completed.cancelled():
                    owner.persistence_failures.append(
                        RuntimeError("recording fact persistence task was cancelled")
                    )
                    return
                if failure := completed.exception():
                    owner.persistence_failures.append(failure)
            task.add_done_callback(track_completed)

        ledger = FactLedger(
            tenant=tenant,
            recording_id=recording_id,
            initial_facts=facts,
            on_append=on_append,
        )
        live = LiveRecording(
            tenant=tenant,
            recording_id=recording_id,
            ledger=ledger,
            lineage_id=lineage_id,
            capture_store=capture_store,
            field_registry=field_registry,
            capture_generation=capture_store.capture_generation,
            capture_end_sequence=capture_end_sequence,
            analysis_fact_ids=analysis_fact_ids,
        )
        live_ref["value"] = live
        # The ledger intentionally retains immutable history, while every
        # generation-scoped projection starts at the persisted reset boundary.
        live.reset_sequence = reset_sequence
        existing_record_ids = {
            item.record_id for item in capture_store.snapshot().records
        }
        for fact in facts:
            if fact.sequence < reset_sequence:
                continue
            if (
                capture_end_sequence is not None
                and fact.sequence >= capture_end_sequence
                and fact.fact_id not in analysis_fact_ids
            ):
                continue
            if fact.fact_id not in existing_record_ids:
                self._record_fact_in_capture_store(live, fact)
        self.live[key] = live
        return live

    async def _persist_and_emit(self, live: LiveRecording, fact: RecordingFact) -> None:
        await self.repository.append_facts(live.tenant, live.recording_id, (fact,))
        if isinstance(fact, RequestFact):
            request = {
                "request_id": fact.request_id,
                "method": fact.method,
                "url": fact.url,
                "resource_type": fact.resource_type,
                "post_data": fact.request_body,
                "request_body_present": fact.request_body_present,
                "sequence": fact.sequence,
            }
            await self.events.publish(live.tenant, live.recording_id, {"type": "request", "request": request})
            if fact.method in {"POST", "PUT", "PATCH", "DELETE"}:
                fields = _captured_fields(fact)
                candidates = [
                    {
                        "idx": index,
                        "request_id": item.request_id,
                        "method": item.method,
                        "url": item.url,
                        "path": urlsplit(item.url).path or "/",
                    }
                    for index, item in enumerate(
                        value
                        for value in live.ledger.snapshot()
                        if isinstance(value, RequestFact)
                        and value.sequence >= live.reset_sequence
                    )
                ]
                await self.events.publish(live.tenant, live.recording_id, {
                    "type": "request_fields",
                    "request_id": fact.request_id,
                    "method": fact.method,
                    "url": fact.url,
                    "fields": fields,
                    "selects": [],
                    "identity": [],
                    "candidates": candidates,
                    "chosen_idx": max(0, len(candidates) - 1),
                    "suggested_steps": list(range(len(candidates))),
                })

    async def _drain_facts(self, live: LiveRecording) -> None:
        while True:
            tasks = tuple(live.persistence_tasks)
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if live.persistence_failures:
                failure = live.persistence_failures[0]
                raise RuntimeError(f"recording fact persistence failed: {failure}") from failure

            expected = tuple(live.ledger.snapshot())
            expected_ids = {fact.fact_id for fact in expected}
            persisted = await self.repository.list_facts(live.tenant, live.recording_id)
            persisted_ids = {fact.fact_id for fact in persisted}
            missing = expected_ids - persisted_ids
            if missing:
                raise RuntimeError(
                    "recording fact persistence is incomplete; missing immutable facts: "
                    f"{sorted(missing)[:5]}"
                )

            # Give callbacks and browser observers one loop turn. If another
            # fact arrived while reconciling, drain/reconcile it before compile.
            await asyncio.sleep(0)
            current_ids = {fact.fact_id for fact in live.ledger.snapshot()}
            if not live.persistence_tasks and current_ids == expected_ids:
                return

    async def _start_capture(self, tenant: str, recording_id: str, message: dict[str, Any]) -> None:
        session = await self.repository.get_session(tenant, recording_id)
        live = await self._get_live(tenant, recording_id)
        if live.started and live.page is not None:
            if not live.capture_active:
                await self._resume_capture(live, session=session)
            await self.events.publish(tenant, recording_id, {
                "type": "started",
                "revision": session.current_revision,
                "resumed": True,
                "pi_status": self._client_pi_status(recording_id),
            })
            return
        start_url = str(session.metadata.get("start_url") or message.get("start_url") or "")
        self._validate_target(start_url, str(session.metadata.get("base_url") or ""))
        storage_state = _parse_storage_state(message.get("storage_state"))
        browser, context = await self.browser_launcher(storage_state)
        token = self._resume_tokens.get((tenant, recording_id))
        if not token:
            await context.close()
            await browser.close()
            raise PermissionError("resume token is unavailable; resume the session before launching a browser")
        try:
            browser_session, _ = await self.browser_sessions.create(
                tenant=tenant,
                recording_id=recording_id,
                context=context,
                browser=browser,
                resume_token=token,
                metadata={"start_url": start_url},
            )
            live.browser = browser
            live.context = context
            await self._attach_capture_runtime(live, session=session)
            pages = tuple(getattr(context, "pages", ()) or ())
            page = pages[0] if pages else await context.new_page()
            live.page = page
            live.capture.attach_page(page)
            await page.goto(start_url, wait_until="domcontentloaded", timeout=120_000)
        except Exception:
            for observer in ((live.runtime,) if live.runtime else (live.network, live.scripts, live.capture)):
                closer = getattr(observer, "close", None)
                if closer:
                    try:
                        await closer()
                    except Exception:  # noqa: BLE001
                        pass
            closed = await self.browser_sessions.close_session(
                tenant=tenant,
                recording_id=recording_id,
            )
            if not closed:
                for resource in (context, browser):
                    try:
                        await resource.close()
                    except Exception:  # noqa: BLE001
                        pass
            live.browser = live.context = live.page = None
            live.runtime = None
            live.capture = live.network = live.scripts = None
            raise
        self._resume_tokens.pop((tenant, recording_id), None)
        live.started = True
        live.capture_active = True
        live.capture_end_sequence = None
        live.analysis_fact_ids.clear()
        live.capture_finalized = False
        live.frame_task = asyncio.create_task(self._frame_loop(live), name=f"recording-frames-{recording_id}")
        await self.repository.update_session(
            tenant,
            recording_id,
            status=RecordingStatus.RECORDING,
            browser_lease_until=browser_session.lease_until,
        )
        await self.events.publish(tenant, recording_id, {
            "type": "started",
            "revision": session.current_revision,
            "recording_mode": session.metadata.get("recording_mode"),
            "pi_status": self._client_pi_status(recording_id),
        })

    async def _attach_capture_runtime(
        self,
        live: LiveRecording,
        *,
        session: RecordingSession,
    ) -> None:
        if live.context is None:
            raise DecisionCommandError("browser context is unavailable")
        start_url = str(session.metadata.get("start_url") or "")
        safe_record = str(session.metadata.get("recording_mode") or "record_only") != "real_submit"
        navigation_hosts = tuple(dict.fromkeys(
            (urlsplit(value).hostname or "").lower()
            for value in (
                start_url,
                str(session.metadata.get("base_url") or session.base_url or ""),
            )
            if value and urlsplit(value).hostname
        ))
        navigation_policy = URLSafetyPolicy(
            allowed_hosts=navigation_hosts,
            allow_private_networks=self.allow_private_networks,
        )
        resource_policy = URLSafetyPolicy(
            # Fetch/XHR and static resources can legitimately use API/CDN
            # origins. They are all captured, while public-network and URL
            # credential safety still applies.
            allow_private_networks=False,
            private_host_allowlist=(navigation_hosts if self.allow_private_networks else ()),
        )
        runtime = CaptureRuntime(
            live.ledger,
            network_config=NetworkObserverConfig(safe_record=safe_record),
            url_policy=navigation_policy,
            network_url_policy=resource_policy,
            value_evidence_factory=self.value_evidence_factory,
            recording_lineage=str(live.lineage_id),
        )
        try:
            await runtime.attach(live.context)
        except Exception:
            await runtime.close()
            raise
        live.runtime = runtime
        live.capture = runtime.browser
        live.network = runtime.network
        live.scripts = runtime.scripts

    async def _resume_capture(
        self,
        live: LiveRecording,
        *,
        session: RecordingSession | None = None,
    ) -> None:
        if not live.started or live.context is None or live.page is None:
            raise DecisionCommandError("browser session is unavailable; resume it before recapturing")
        if live.capture_active:
            return
        if live.runtime is None:
            session = session or await self.repository.get_session(
                live.tenant,
                live.recording_id,
            )
            await self._attach_capture_runtime(live, session=session)
        else:
            await live.runtime.resume(live.context)
            live.capture = live.runtime.browser
            live.network = live.runtime.network
            live.scripts = live.runtime.scripts
        live.capture_active = True
        live.capture_end_sequence = None
        live.capture_finalized = False
        if live.frame_task is None or live.frame_task.done():
            live.frame_task = asyncio.create_task(
                self._frame_loop(live),
                name=f"recording-frames-{live.recording_id}",
            )
        await self.repository.update_session(
            live.tenant,
            live.recording_id,
            status=RecordingStatus.RECORDING,
            metadata={
                "capture_end_sequence": None,
                "analysis_fact_ids": [],
            },
        )

    async def _frame_loop(self, live: LiveRecording) -> None:
        try:
            while live.started and live.capture_active and live.page is not None:
                data = await live.page.screenshot(type="jpeg", quality=65)
                live.frame_sequence += 1
                await self.events.publish(live.tenant, live.recording_id, {
                    "type": "frame",
                    "seq": live.frame_sequence,
                    "data": base64.b64encode(data).decode("ascii"),
                })
                await asyncio.sleep(0.45)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            await self.events.publish(live.tenant, live.recording_id, {
                "type": "error",
                "code": "frame_stream_failed",
                "detail": str(exc),
                "retryable": True,
            })

    async def _dispatch_input(self, tenant: str, recording_id: str, event: dict[str, Any]) -> None:
        live = await self._get_live(tenant, recording_id)
        if not live.started or not live.capture_active or live.page is None:
            raise DecisionCommandError("browser capture has not started")
        kind = str(event.get("kind") or "")
        action_id = new_id()
        action_window = (
            live.runtime.actions.scope(action_id) if live.runtime is not None else nullcontext()
        )
        trusted_action = {
            "evidence_origin": "server_dispatched",
            "causal_eligible": True,
        }
        locator = ""
        label = kind
        if kind == "click":
            nx = max(0.0, min(1.0, float(event.get("nx") or 0)))
            ny = max(0.0, min(1.0, float(event.get("ny") or 0)))
            viewport = await live.page.evaluate("() => ({width: innerWidth, height: innerHeight})")
            x, y = nx * float(viewport.get("width") or 1280), ny * float(viewport.get("height") or 720)
            locator = f"screen:{nx:.5f},{ny:.5f}"
            label = await describe_click_target(
                live.page,
                x=x,
                y=y,
                redaction=self.redaction,
            )
            live.last_action_id = action_id
            with action_window:
                live.ledger.emit(
                    ActionFact,
                    action_id=action_id,
                    action_type="click",
                    label=label,
                    locator=locator,
                    payload=trusted_action,
                )
                await live.page.mouse.click(x, y)
            step = {"op": "click", "locator": locator, "action_id": action_id}
        elif kind == "text":
            raw_text = str(event.get("text") or "")
            try:
                active = await live.page.evaluate(
                    "() => ({name: document.activeElement?.getAttribute('name') || '', "
                    "input_type: document.activeElement?.getAttribute('type') || ''})"
                )
            except Exception:  # noqa: BLE001
                active = {}
            active = active if isinstance(active, dict) else {}
            field_name = str(active.get("name") or "text")
            safe_text, value_evidence = self.value_evidence_factory.capture_tree(
                tenant_scope=tenant,
                recording_lineage=str(live.lineage_id),
                value=raw_text,
                root_path="action.value",
                field_name=field_name,
            )
            safe_details = {
                "event": "input",
                "name": field_name,
                "input_type": str(active.get("input_type") or ""),
                "value": safe_text,
                "value_evidence": [
                    item.model_dump(mode="json", exclude_none=True)
                    for item in value_evidence
                ],
            }
            live.last_action_id = action_id
            with action_window:
                live.ledger.emit(
                    ActionFact,
                    action_id=action_id,
                    action_type="fill",
                    label="text input",
                    locator="focused",
                    payload={
                        **trusted_action,
                        "frame_id": None,
                        "details": safe_details,
                    },
                )
                await live.page.keyboard.insert_text(raw_text)
            step = {
                "op": "fill",
                "locator": "focused",
                "value": safe_text,
                "value_evidence": safe_details["value_evidence"],
                "action_id": action_id,
            }
        elif kind == "key":
            key = str(event.get("key") or "")
            live.last_action_id = action_id
            with action_window:
                live.ledger.emit(
                    ActionFact,
                    action_id=action_id,
                    action_type="key",
                    label=key,
                    locator="focused",
                    payload=trusted_action,
                )
                await live.page.keyboard.press(key)
            step = {"op": "key", "locator": "focused", "value": key, "action_id": action_id}
        elif kind == "scroll":
            dy = max(-10_000.0, min(10_000.0, float(event.get("dy") or 0)))
            live.last_action_id = action_id
            with action_window:
                live.ledger.emit(
                    ActionFact,
                    action_id=action_id,
                    action_type="scroll",
                    label=str(dy),
                    locator="viewport",
                    payload=trusted_action,
                )
                await live.page.mouse.wheel(0, dy)
            step = {"op": "scroll", "locator": "viewport", "value": dy, "action_id": action_id}
        else:
            raise DecisionCommandError(f"unsupported browser input: {kind}")
        await self.events.publish(tenant, recording_id, {"type": "step", "step": step})

    async def _reset_capture(self, tenant: str, recording_id: str) -> None:
        live = await self._get_live(tenant, recording_id)
        if not live.capture_active:
            raise DecisionCommandError("browser capture is frozen; use recapture before resetting")
        live.reset_sequence = live.ledger.next_sequence
        live.capture_end_sequence = None
        live.ledger.emit(
            RecordingFact,
            kind=FactKind.DIAGNOSTIC,
            payload={"type": "recording_reset"},
            redacted=True,
        )
        await self.repository.update_session(
            tenant,
            recording_id,
            metadata={"reset_sequence": live.reset_sequence},
        )
        await self.events.publish(tenant, recording_id, {"type": "started", "reset": True})

    async def _choose_request(self, tenant: str, recording_id: str, index: int) -> None:
        live = await self._get_live(tenant, recording_id)
        requests = [
            value
            for value in live.ledger.snapshot()
            if isinstance(value, RequestFact) and value.sequence >= live.reset_sequence
        ]
        if index < 0 or index >= len(requests):
            raise DecisionCommandError(f"request index out of range: {index}")
        chosen = requests[index]
        live.ledger.emit(
            RecordingFact,
            kind=FactKind.DIAGNOSTIC,
            action_id=chosen.action_id,
            page_id=chosen.page_id,
            payload={"type": "user_selected_request", "request_id": chosen.request_id, "index": index},
            redacted=True,
        )
        await self.repository.update_session(
            tenant,
            recording_id,
            metadata={"chosen_request_id": chosen.request_id},
        )
        await self.events.publish(tenant, recording_id, {
            "type": "request_fields",
            "request_id": chosen.request_id,
            "method": chosen.method,
            "url": chosen.url,
            "fields": _captured_fields(chosen),
            "selects": [],
            "identity": [],
            "candidates": [
                {
                    "idx": position,
                    "request_id": item.request_id,
                    "method": item.method,
                    "url": item.url,
                    "path": urlsplit(item.url).path or "/",
                }
                for position, item in enumerate(requests)
            ],
            "chosen_idx": index,
            "suggested_steps": list(range(len(requests))),
        })

    async def _collect_evidence(
        self,
        live: LiveRecording,
        compilation: RecordingCompilation,
    ) -> None:
        start_sequence = live.ledger.next_sequence
        live.analysis_evidence_active = True
        try:
            await self._collect_evidence_impl(live, compilation)
        finally:
            for fact in live.ledger.snapshot():
                if (
                    fact.sequence >= start_sequence
                    and fact.kind in {FactKind.DOM_CONTROL, FactKind.SCRIPT}
                ):
                    live.analysis_fact_ids.add(fact.fact_id)
            live.analysis_evidence_active = False
            await self.repository.update_session(
                live.tenant,
                live.recording_id,
                metadata={"analysis_fact_ids": sorted(live.analysis_fact_ids)},
            )

    async def _collect_evidence_impl(
        self,
        live: LiveRecording,
        compilation: RecordingCompilation,
    ) -> None:
        page_id = "page:offline"
        if live.page is not None and live.capture is not None:
            page_id = live.capture.page_id(live.page) or live.capture.attach_page(live.page)
        controls = ()
        clues = ()
        try:
            if live.page is None or live.capture is None:
                raise LookupError("live page evidence is unavailable")
            if live.runtime is not None:
                evidence_snapshot = await live.runtime.collect_page_evidence(live.page)
                controls = evidence_snapshot.get("controls") or ()
                clues = evidence_snapshot.get("runtime_components") or ()
            else:
                controls = await DOMControlCollector(
                    live.ledger,
                    value_evidence_factory=self.value_evidence_factory,
                    recording_lineage=str(live.lineage_id),
                ).collect(
                    live.page,
                    page_id=page_id,
                    frame_id=live.capture.frame_id(getattr(live.page, "main_frame", None)),
                )
                clues = await RuntimeComponentCollector().collect(live.page)
            for clue in clues:
                live.ledger.emit(
                    RecordingFact,
                    kind=FactKind.DOM_CONTROL,
                    page_id=page_id,
                    payload={
                        "type": "runtime_component",
                        "framework": clue.framework,
                        "component_name": clue.component_name,
                        "control_id": clue.control_id,
                        "property_path": clue.property_path,
                        "options": [item.model_dump(mode="json") for item in clue.options],
                        "multiple": clue.multiple,
                        "proofs": list(clue.proofs),
                        "options_sensitive": clue.options_sensitive,
                        "option_count": clue.option_count,
                        "option_runtime_resolver": clue.option_runtime_resolver,
                    },
                    redacted=True,
                )
        except LookupError:
            # Re-analysis after a process/browser restart reuses persisted
            # CaptureStore/script evidence and does not fabricate DOM facts.
            pass
        except Exception as exc:  # noqa: BLE001
            live.ledger.emit(
                RecordingFact,
                kind=FactKind.DIAGNOSTIC,
                page_id=page_id,
                payload={"type": "component_evidence_failed", "message": self.redaction.redact_text(str(exc))},
                redacted=True,
            )

        extractor = EnumExtractor()
        correlated = correlate_recording_evidence(
            controls=controls,
            runtime_clues=clues,
            requests=compilation.requests,
            fields=compilation.field_facts,
        )
        controls_by_id = {control.control_id: control for control in controls}
        for binding in correlated.bindings:
            canonical = live.field_registry.resolve_alias(
                FieldAlias(
                    kind=FieldAliasKind.LEGACY_ID,
                    value=binding.field_contract_id,
                    context="lineage",
                )
            )
            control = controls_by_id.get(str(binding.control_id or ""))
            if canonical is None or control is None:
                continue
            live.field_registry.add_control_evidence(
                ControlEvidence(
                    evidence_id=f"dom:{control.control_id}:{canonical.field_uuid}",
                    field_uuid=canonical.field_uuid,
                    page_id=control.page_id,
                    frame_id=control.frame_id or "frame:main",
                    form_id=control.form_id or f"form:{control.page_id}",
                    control_locator={
                        "control_id": control.control_id,
                        "selector": control.selector,
                        "tag": control.tag,
                        "name": control.name,
                    },
                    label=control.label or None,
                    role=control.role or control.input_type or None,
                    native_control_type=control.input_type or control.tag or None,
                    aria_role=control.role or None,
                    readonly=control.readonly,
                    disabled=control.disabled,
                    required=control.required,
                    initial_value=control.initial_value,
                    initial_value_observed=control.initial_value_observed,
                    initial_value_evidence=control.initial_value_evidence,
                    options_sensitive=control.options_sensitive,
                    option_count=control.option_count,
                    option_runtime_resolver=control.option_runtime_resolver,
                )
            )

        def retain(result: Any) -> None:
            for evidence in result.evidence:
                live.evidence.add_evidence(evidence)
            for suggestion in result.suggestions:
                live.evidence.add_suggestion(suggestion)

        retain(extractor.resolve(correlated.candidates, correlated.bindings))
        scripts: list[LoadedScript] = []
        if live.scripts is not None:
            await live.scripts._tasks.drain()  # ScriptParsed bodies must finish before analysis.
            scripts.extend(live.scripts.scripts)
        else:
            for artifact in live.capture_store.snapshot().scripts:
                try:
                    source = live.capture_store.get_script_content(
                        artifact.content_hash
                    ).decode("utf-8", errors="replace")
                except KeyError:
                    continue
                source_map_url = str(artifact.metadata.get("source_map_url") or "") or None
                scripts.append(
                    LoadedScript(
                        script_id=f"restored:{artifact.content_hash}",
                        url=artifact.urls[0] if artifact.urls else "about:blank",
                        script_hash=artifact.content_hash,
                        byte_size=artifact.size,
                        inline=bool(artifact.metadata.get("inline")),
                        source_map_url=source_map_url,
                        source_reference=artifact.artifact_ref,
                        source=source,
                        truncated=artifact.truncated,
                    )
                )
        if not scripts:
            return
        analyzer = JSStaticAnalyzer()
        map_loader = SourceMapLoader(
            url_policy=URLSafetyPolicy(allow_private_networks=self.allow_private_networks)
        )

        async def fetch_map(url: str) -> SourceMapFetchResult:
            if live.context is None:
                raise RuntimeError("browser context is unavailable")
            map_loader.url_policy.validate(url)
            response = await live.context.request.get(
                url,
                timeout=30_000,
                max_redirects=0,
            )
            headers = response.headers or {}
            return SourceMapFetchResult(
                body=await response.body(),
                final_url=str(getattr(response, "url", "") or url),
                status=int(response.status),
                location=str(headers.get("location") or "") or None,
            )

        for script in scripts:
            if script.source is not None:
                source_bytes = script.source.encode("utf-8", errors="replace")
                analysis_hash = hashlib.sha256(source_bytes).hexdigest()
                script_index = json.dumps({
                    "script_url": script.url,
                    "original_script_hash": script.script_hash,
                    "analysis_hash": analysis_hash,
                    "byte_size": script.byte_size,
                    "captured_byte_size": len(source_bytes),
                    "truncated": script.truncated,
                }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
                await self._save_artifact(
                    live,
                    kind="javascript_index",
                    digest=hashlib.sha256(script_index).hexdigest(),
                    data=script_index,
                    metadata={
                        "script_url": script.url,
                        "original_script_hash": script.script_hash,
                        "analysis_hash": analysis_hash,
                        "byte_size": script.byte_size,
                        "captured_byte_size": len(source_bytes),
                        "truncated": script.truncated,
                    },
                )
                analysis = await analyzer.analyze(
                    script.source,
                    script_url=script.url,
                    script_hash=analysis_hash,
                )
                await self._save_artifact(
                    live,
                    kind="javascript_source",
                    digest=analysis_hash,
                    data=source_bytes,
                    metadata={
                        "script_url": self.redaction.redact_url(script.url),
                        "page_id": page_id,
                        "truncated": script.truncated,
                        "source_map_url": self.redaction.redact_url(script.source_map_url)
                        if script.source_map_url else None,
                    },
                )
                live.capture_store.record_script(
                    url=script.url,
                    content=source_bytes,
                    analysis=analysis.pi_projection(),
                    page_id=page_id,
                    truncated=script.truncated,
                    evidence_ids=(f"script:{analysis_hash}",),
                    artifact_ref=f"recording-artifact:javascript_source:sha256:{analysis_hash}",
                    metadata={
                        "source_map_url": self.redaction.redact_url(script.source_map_url)
                        if script.source_map_url else None,
                        "inline": script.inline,
                    },
                )
                live.capture_store.index_resource(
                    url=script.url,
                    resource_type="script",
                    content_hash=analysis_hash,
                    size=len(source_bytes),
                )
                retain(extractor.resolve(
                    extractor.from_static_analysis(
                        analysis,
                        source_kind=ChoiceEvidenceSource.SCRIPT_STATIC,
                        symbol_bindings=correlated.symbol_bindings,
                    ),
                    correlated.bindings,
                ))
                live.ledger.emit(
                    RecordingFact,
                    kind=FactKind.SCRIPT,
                    page_id=page_id,
                    payload={"type": "static_analysis", **analysis.pi_projection()},
                    redacted=True,
                )
            source_map = await map_loader.load(script, fetcher=fetch_map)
            if source_map.map_url:
                live.capture_store.index_resource(
                    url=source_map.map_url,
                    resource_type="sourcemap",
                    content_hash=hashlib.sha256(
                        json.dumps(
                            source_map.pi_projection(),
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest(),
                )
            for index, source in enumerate(source_map.source_contents):
                if source is None:
                    continue
                raw = source.encode("utf-8", errors="replace")
                source_hash = hashlib.sha256(raw).hexdigest()
                source_url = source_map.sources[index] if index < len(source_map.sources) else ""
                source_url_hash = hashlib.sha256(
                    source_url.encode("utf-8", errors="replace")
                ).hexdigest()
                source_index = json.dumps({
                    "map_url": source_map.map_url,
                    "source_url_hash": source_url_hash,
                    "source_hash": source_hash,
                    "byte_size": len(raw),
                }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
                await self._save_artifact(
                    live,
                    kind="sourcemap_index",
                    digest=hashlib.sha256(source_index).hexdigest(),
                    data=source_index,
                    metadata={
                        "map_url": source_map.map_url,
                        "source_url_hash": source_url_hash,
                        "source_hash": source_hash,
                        "byte_size": len(raw),
                    },
                )
            for analysis in await analyzer.analyze_sourcemap(
                source_map,
                script_hash=script.script_hash,
            ):
                retain(extractor.resolve(
                    extractor.from_static_analysis(
                        analysis,
                        source_kind=ChoiceEvidenceSource.SOURCEMAP,
                        symbol_bindings=correlated.symbol_bindings,
                    ),
                    correlated.bindings,
                ))
                live.ledger.emit(
                    RecordingFact,
                    kind=FactKind.SCRIPT,
                    page_id=page_id,
                    payload={
                        "type": "sourcemap_static_analysis",
                        **analysis.pi_projection(),
                    },
                    redacted=True,
                )
            live.ledger.emit(
                RecordingFact,
                kind=FactKind.SCRIPT,
                page_id=page_id,
                payload={
                    "type": "sourcemap",
                    "script_hash": script.script_hash,
                    **source_map.pi_projection(),
                },
                redacted=True,
            )

    async def _save_artifact(
        self,
        live: LiveRecording,
        *,
        kind: str,
        digest: str,
        data: bytes,
        metadata: dict[str, Any],
    ) -> None:
        identity = (kind, digest)
        if identity in live.artifact_hashes:
            return
        tenant_dir = hashlib.sha256(live.tenant.encode()).hexdigest()[:20]
        target = self.artifact_root / tenant_dir / live.recording_id / kind / f"{digest}.bin"

        def write() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                temporary = target.with_suffix(f".{uuid4().hex}.tmp")
                temporary.write_bytes(data)
                temporary.replace(target)

        await asyncio.to_thread(write)
        session = await self.repository.get_session(live.tenant, live.recording_id)
        await self.repository.save_artifact(RecordingArtifact(
            tenant=live.tenant,
            recording_id=live.recording_id,
            revision=session.current_revision,
            kind=kind,
            content_hash=f"sha256:{digest}",
            storage_ref=str(target),
            metadata={**metadata, "recording_engine": "playwright_v3"},
        ))
        live.artifact_hashes.add(identity)

    async def _begin_operation(
        self,
        tenant: str,
        recording_id: str,
        message: dict[str, Any],
        *,
        kind: str,
    ) -> tuple[RecordingOperation, dict[str, Any] | None]:
        operation_id = str(message.get("operation_id") or uuid4())
        request = {
            key: value for key, value in message.items()
            if key not in {"operation_id", "storage_state"}
        }
        operation = RecordingOperation(
            tenant=tenant,
            operation_id=operation_id,
            recording_id=recording_id,
            kind=kind,
            request_hash=content_hash(request),
        )
        stored, created = await self.repository.register_operation(operation)
        if created:
            return stored, None
        if stored.status is OperationStatus.COMPLETED:
            return stored, deepcopy(stored.result or {})
        if stored.status is OperationStatus.FAILED:
            raise DecisionCommandError(stored.error or f"operation failed: {operation_id}")
        if kind == "publish_request":
            # The external asset transaction may have committed while the
            # local operation-result write was temporarily unavailable.  A
            # server-owned recovery result makes that durable success
            # replayable without ever rewriting the operation as failed.
            session = await self.repository.get_session(tenant, recording_id)
            recovery = (session.metadata.get("published_operation_results") or {}).get(
                operation_id
            )
            if isinstance(recovery, dict):
                try:
                    await self.repository.complete_operation(
                        tenant,
                        operation_id,
                        result=recovery,
                    )
                except Exception:  # noqa: BLE001 - replay remains authoritative
                    pass
                return stored, deepcopy(recovery)
            if operation_id in self._active_publish_operations:
                raise DecisionCommandError(
                    f"operation is already in progress: {operation_id}"
                )
            # No task in this process owns the durable STARTED row.  It is a
            # restart orphan: resume the exact revision publication.  The Dano
            # writer recovers an already committed frozen asset by scope/body
            # fingerprint, so this is safe whether the crash happened before
            # or immediately after the external commit.
            return stored, None
        raise DecisionCommandError(f"operation is already in progress: {operation_id}")

    async def _commit_snapshot(
        self,
        tenant: str,
        recording_id: str,
        *,
        expected_revision: int,
        snapshot: dict[str, Any],
        actor: str,
    ):
        value = deepcopy(snapshot)
        session = await self.repository.get_session(tenant, recording_id)
        lineage_id = self._lineage_id(
            tenant,
            recording_id,
            session=session,
            snapshot=value,
        )
        value, _ = self._migrate_flow_snapshot(
            tenant=tenant,
            recording_id=recording_id,
            lineage_id=lineage_id,
            snapshot=value,
        )
        value["revision"] = expected_revision + 1
        value.setdefault("meta", {})["current_version"] = expected_revision + 1
        value["meta"]["recording_engine"] = "playwright_v3"
        report = validate_workbench(deepcopy(value))
        value["validation"] = report
        revision = await self.repository.commit_revision(
            tenant,
            recording_id,
            expected_revision=expected_revision,
            snapshot=value,
            actor=actor,
        )
        return revision, report

    async def _finalize_command(
        self,
        tenant: str,
        recording_id: str,
        message: dict[str, Any],
        *,
        operation: RecordingOperation | None = None,
    ) -> None:
        if operation is None:
            operation, replay = await self._begin_operation(
                tenant, recording_id, message, kind="finalize"
            )
            if replay is not None:
                await self.events.publish(tenant, recording_id, replay)
                return
        try:
            session = await self.repository.get_session(tenant, recording_id)
            expected = int(message.get("expected_revision", session.current_revision))
            if expected != session.current_revision:
                raise RevisionConflict(expected=expected, actual=session.current_revision)
            live = await self._get_live(tenant, recording_id)
            await self._analysis_progress(live, stage="draining_capture", progress=10)
            await self._freeze_capture(live)
            preliminary_facts = await self._capture_generation_facts(live)
            await self.repository.update_session(tenant, recording_id, status=RecordingStatus.COMPILING)
            previous_revision = await self.repository.get_revision(tenant, recording_id)
            previous_snapshot: dict[str, Any] | None = None
            if previous_revision is not None:
                previous_snapshot, restored_registry = self._migrate_flow_snapshot(
                    tenant=tenant,
                    recording_id=recording_id,
                    lineage_id=live.lineage_id,
                    snapshot=previous_revision.snapshot,
                )
                # User/Pi axis decisions committed while this browser session
                # remained live are authoritative for the next re-analysis.
                live.field_registry = restored_registry
            preliminary = prepare_recording_materials(
                tenant=tenant,
                recording_id=recording_id,
                facts=preliminary_facts,
                source_revision=expected,
            )
            # Immediate wire-only preview: no discarded capability plan and
            # no field inference run.  The final semantic projection replaces
            # this skeleton after evidence collection.
            deterministic_preview = rebase_user_decisions(
                previous_snapshot,
                compilation_to_workbench(preliminary, session),
            )
            deterministic_preview.update({
                "tenant": tenant,
                "recording_id": recording_id,
                "subsystem": session.metadata.get("subsystem") or "",
                "start_url": session.metadata.get("start_url") or "",
                "base_url": session.metadata.get("base_url") or session.base_url,
                "recording_mode": session.metadata.get("recording_mode") or "record_only",
            })
            if str(message.get("action") or "").strip():
                deterministic_preview["action"] = str(message["action"]).strip()
            if str(message.get("title") or "").strip():
                deterministic_preview["title"] = str(message["title"]).strip()
            deterministic_preview.setdefault("meta", {}).update({
                "recording_engine": "playwright_v3",
                "preview": True,
                "source_revision": expected,
                "compilation_hash": preliminary.content_hash,
                "semantic_state": "pending_evidence",
            })
            await self.events.publish(tenant, recording_id, {
                "type": "deterministic_flow",
                "operation": "finalize",
                "operation_id": operation.operation_id,
                "revision": expected,
                "current_revision": expected,
                "preview": True,
                "full_spec": deterministic_preview,
                "flow_spec": deterministic_preview,
            })
            await self._analysis_progress(live, stage="collecting_evidence", progress=35)
            live.analysis_fact_ids.clear()
            await self._collect_evidence(live, preliminary)
            await self._analysis_progress(live, stage="compiling_contracts", progress=70)
            await self._drain_facts(live)
            facts = await self._capture_generation_facts(live)
            base_compilation = prepare_recording_materials(
                tenant=tenant,
                recording_id=recording_id,
                facts=facts,
                source_revision=expected,
            )
            base_contracts = integrate_compilation_contracts(
                base_compilation,
                facts=facts,
                capture_store=live.capture_store,
                field_registry=live.field_registry,
                value_evidence_factory=self.value_evidence_factory,
            )
            # CapabilityPlanner receives the graph made from this exact capture
            # generation.  Only proven response/control back-slices can add an
            # auxiliary request; same-click temporal prefixes are never used.
            compilation = compile_recording(
                tenant=tenant,
                recording_id=recording_id,
                facts=facts,
                source_revision=expected,
                evidence_graph=base_contracts.evidence_graph,
            )
            contracts = complete_contract_projection(
                compilation,
                facts=facts,
                capture_store=live.capture_store,
                contracts=base_contracts,
            )
            deterministic = compilation_to_workbench(
                compilation,
                session,
                contracts,
            )
            spec = rebase_user_decisions(
                previous_snapshot,
                deterministic,
            )

            def merged_evidence(key: str, current: list[dict[str, Any]]) -> list[dict[str, Any]]:
                previous = (
                    list(previous_snapshot.get(key) or [])
                    if previous_snapshot is not None
                    else []
                )
                output: list[dict[str, Any]] = []
                identities: set[str] = set()
                for item in [*previous, *current]:
                    if not isinstance(item, dict):
                        continue
                    identity = str(
                        item.get("evidence_id")
                        or item.get("suggestion_id")
                        or content_hash(item)
                    )
                    if identity in identities:
                        continue
                    identities.add(identity)
                    output.append(deepcopy(item))
                return output

            finalize_edits = []
            if str(message.get("action") or "").strip():
                finalize_edits.append({
                    "op": "update_flow",
                    "field": "action",
                    "value": str(message["action"]).strip(),
                })
            if str(message.get("title") or "").strip():
                finalize_edits.append({
                    "op": "update_flow",
                    "field": "title",
                    "value": str(message["title"]).strip(),
                })
            if finalize_edits:
                spec = apply_edits(spec, finalize_edits)

            canonical_fields = {
                str(item.field_uuid): item for item in contracts.field_registry.fields
            }

            def safe_effective_field(item: Any) -> dict[str, Any]:
                row = item.model_dump(
                    mode="json",
                    exclude={"wire_schema": {"sample"}},
                )
                field_uuid = contracts.field_uuids.get(item.field_contract_id)
                canonical = canonical_fields.get(str(field_uuid or ""))
                enum_decision = next(
                    (
                        decision
                        for axis, decision in (canonical.decisions.items() if canonical else ())
                        if getattr(axis, "value", str(axis)) == "enum_binding"
                    ),
                    None,
                )
                enum_value = enum_decision.value if enum_decision is not None else None
                if (
                    isinstance(enum_value, Mapping)
                    and enum_value.get("static_values_retained") is False
                ):
                    choice = row.get("choice_contract")
                    if isinstance(choice, dict):
                        choice.pop("options", None)
                        choice.pop("typed_options", None)
                return row

            spec.update({
                "tenant": tenant,
                "recording_id": recording_id,
                "subsystem": session.metadata.get("subsystem") or "",
                "start_url": session.metadata.get("start_url") or "",
                "base_url": session.metadata.get("base_url") or session.base_url,
                "recording_mode": session.metadata.get("recording_mode") or "record_only",
                "effective_fields": [
                    safe_effective_field(item) for item in compilation.fields
                ],
                "field_evidence": [
                    item.model_dump(
                        mode="json",
                        exclude={
                            "observed_values": True,
                            "wire_schema": {"sample"},
                        },
                    )
                    for item in compilation.field_facts
                ],
                "enum_evidence": merged_evidence(
                    "enum_evidence",
                    [project_evidence_for_pi(item) for item in live.evidence.all_evidence()],
                ),
                "enum_suggestions": merged_evidence(
                    "enum_suggestions",
                    [project_evidence_for_pi(item) for item in live.evidence.suggestions()],
                ),
                # Rebase may preserve user-facing rows, but these lineage
                # contracts are compiler-owned and always describe the exact
                # capture generation used for this revision.
                "lineage_id": str(live.lineage_id),
                "capture_generation": live.capture_store.capture_generation,
                "capture_store": contracts.capture_store.model_dump(mode="json"),
                "field_registry": contracts.field_registry.model_dump(mode="json"),
                "evidence_graph_summary": contracts.graph_summary(),
                "recording_contract_version": 1,
            })
            spec.setdefault("meta", {}).update({
                "recording_engine": "playwright_v3",
                "compilation_hash": compilation.content_hash,
                "fact_count": len(facts),
            })
            latest_session = await self.repository.get_session(tenant, recording_id)
            if latest_session.current_revision != expected:
                latest_revision = await self.repository.get_revision(tenant, recording_id)
                if latest_revision is None:
                    raise RevisionConflict(
                        expected=expected,
                        actual=latest_session.current_revision,
                    )
                # Workbench edits are allowed while optional evidence analysis
                # runs. Rebase onto the newest immutable revision so no manual
                # axis decision is overwritten by a late compiler result.
                latest_snapshot, _ = self._migrate_flow_snapshot(
                    tenant=tenant,
                    recording_id=recording_id,
                    lineage_id=live.lineage_id,
                    snapshot=latest_revision.snapshot,
                    registry=live.field_registry,
                )
                spec = rebase_user_decisions(latest_snapshot, spec)
                spec.update({
                    "lineage_id": str(live.lineage_id),
                    "capture_generation": live.capture_store.capture_generation,
                    "capture_store": contracts.capture_store.model_dump(mode="json"),
                    "field_registry": contracts.field_registry.model_dump(mode="json"),
                    "evidence_graph_summary": contracts.graph_summary(),
                    "recording_contract_version": 1,
                })
                expected = latest_session.current_revision
            await self._analysis_progress(live, stage="committing_revision", progress=90)
            revision, report = await self._commit_snapshot(
                tenant,
                recording_id,
                expected_revision=expected,
                snapshot=spec,
                actor="deterministic-compiler",
            )
            live.capture_finalized = True
            await self.repository.update_session(tenant, recording_id, status=RecordingStatus.DRAFT)
            response = {
                "type": "flow_spec",
                "operation": "finalize",
                "operation_id": operation.operation_id,
                "revision": revision.revision,
                "current_revision": revision.revision,
                "full_spec": revision.snapshot,
                "flow_spec": revision.snapshot,
                "check_report": report,
                "pi_status": self._client_pi_status(recording_id),
                "analysis_status": {
                    "state": "completed",
                    "stage": "deterministic_complete",
                    "progress": 100,
                },
            }
            await self.repository.complete_operation(
                tenant,
                operation.operation_id,
                result=response,
            )
            await self.events.publish(tenant, recording_id, response)
            self._spawn(
                self._run_initial_pi(tenant, recording_id, revision.revision),
                name=f"recording-pi-initial-{recording_id}-{revision.revision}",
            )
        except Exception as exc:
            await self.repository.complete_operation(
                tenant,
                operation.operation_id,
                error=str(exc),
            )
            raise

    async def _send_snapshot(self, tenant: str, recording_id: str, sender: EventSender) -> None:
        session = await self.repository.get_session(tenant, recording_id)
        revision = await self.repository.get_revision(tenant, recording_id)
        if revision is None:
            await sender({
                "type": "started",
                "revision": 0,
                "current_revision": 0,
                "pi_status": self._client_pi_status(recording_id),
            })
            return
        snapshot, _ = self._migrate_flow_snapshot(
            tenant=tenant,
            recording_id=recording_id,
            lineage_id=self._lineage_id(
                tenant,
                recording_id,
                session=session,
                snapshot=revision.snapshot,
            ),
            snapshot=revision.snapshot,
        )
        await sender({
            "type": "flow_spec",
            "operation": "refresh_flow_spec",
            "revision": session.current_revision,
            "current_revision": session.current_revision,
            "full_spec": snapshot,
            "flow_spec": snapshot,
            "check_report": snapshot.get("validation") or validate_workbench(deepcopy(snapshot)),
            "pi_status": self._client_pi_status(recording_id),
        })

    async def _flow_command(
        self,
        tenant: str,
        recording_id: str,
        message: dict[str, Any],
        sender: EventSender,
    ) -> None:
        kind = str(message.get("type") or "flow_update")
        operation, replay = await self._begin_operation(tenant, recording_id, message, kind=kind)
        if replay is not None:
            await sender(replay)
            return
        try:
            session = await self.repository.get_session(tenant, recording_id)
            expected = int(message.get("expected_revision", session.current_revision))
            if expected != session.current_revision:
                raise RevisionConflict(expected=expected, actual=session.current_revision)
            current = await self.repository.get_revision(tenant, recording_id)
            if current is None:
                raise DecisionCommandError("finalize the capture before editing the workbench")
            current_snapshot, _ = self._migrate_flow_snapshot(
                tenant=tenant,
                recording_id=recording_id,
                lineage_id=self._lineage_id(
                    tenant,
                    recording_id,
                    session=session,
                    snapshot=current.snapshot,
                ),
                snapshot=current.snapshot,
            )
            if kind == "flow_replace":
                next_spec = apply_replacement(current_snapshot, message.get("flow_spec") or {})
            else:
                next_spec = apply_edits(current_snapshot, message.get("edits") or [])
            revision, report = await self._commit_snapshot(
                tenant,
                recording_id,
                expected_revision=expected,
                snapshot=next_spec,
                actor="user",
            )
            response = {
                "type": "flow_spec_updated",
                "operation": kind,
                "operation_id": operation.operation_id,
                "revision": revision.revision,
                "current_revision": revision.revision,
                "full_spec": revision.snapshot,
                "flow_spec": revision.snapshot,
                "check_report": report,
                "pi_status": self._client_pi_status(recording_id),
            }
            await self.repository.complete_operation(
                tenant,
                operation.operation_id,
                result=response,
            )
            await self.events.publish(tenant, recording_id, response)
        except Exception as exc:
            await self.repository.complete_operation(tenant, operation.operation_id, error=str(exc))
            raise

    async def _ensure_pi_sessions(self, tenant: str, recording_id: str) -> None:
        persisted_rows = await self.repository.list_pi_sessions(tenant, recording_id)
        persisted = {
            row.role.value: {
                "session_id": row.pi_session_id,
                "session_path": str(row.metadata.get("session_path") or ""),
            }
            for row in persisted_rows
        }
        statuses = await self.pi.ensure_sessions(recording_id, persisted=persisted)
        model_id = os.environ.get("DANO_PI_MODEL") or self.pi_client.extra_env.get("DANO_PI_MODEL") or "recording-pi"
        self.review_collector.register_server_sessions(
            recording_id,
            {role: status.session_id for role, status in statuses.items()},
            model_id=model_id,
        )
        for role, status in statuses.items():
            await self.repository.save_pi_session(PiSessionMetadata(
                tenant=tenant,
                recording_id=recording_id,
                pi_session_id=status.session_id,
                role=PiRole(role),
                model_id=model_id,
                status=_stored_pi_status(status.state),
                last_revision=(await self.repository.get_session(tenant, recording_id)).current_revision,
                metadata={
                    "session_path": status.session_path,
                    "turn": status.turn,
                    "tool_calls": status.tool_calls,
                    "retries": status.retries,
                    "compactions": status.compactions,
                    "usage": status.usage.model_dump(mode="json"),
                    "last_error": status.last_error,
                },
            ))

    async def _handle_pi_tool(
        self,
        session_id: str,
        tool: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        return await self.pi.handle_tool(session_id, tool, params)

    async def _handle_pi_event(self, message: dict[str, Any]) -> None:
        await self.pi.handle_event(message)

    async def _pi_event_sink(self, recording_id: str, payload: dict[str, Any]) -> None:
        tenant = self._tenant_for(recording_id)
        event = dict(payload.get("event") or {})
        safe_payload = project_evidence_for_pi(event)
        await self.repository.append_pi_event(StoredPiEvent(
            tenant=tenant,
            recording_id=recording_id,
            pi_session_id=str(payload.get("session_id") or ""),
            event_type=str(event.get("type") or "event"),
            turn_index=max(0, int(payload.get("turn") or 0)),
            payload={
                "role": payload.get("role"),
                "event": safe_payload,
                "status": project_evidence_for_pi(payload.get("status") or {}),
            },
        ))
        await self.events.publish(tenant, recording_id, {
            "type": "pi_event",
            "role": payload.get("role"),
            "pi_session_id": payload.get("session_id"),
            "turn": payload.get("turn"),
            "event": safe_payload,
            "pi_status": self._client_pi_status(recording_id),
        })

    async def _pi_state(self, recording_id: str) -> dict[str, Any]:
        tenant = self._tenant_for(recording_id)
        revision = await self.repository.get_revision(tenant, recording_id)
        session = await self.repository.get_session(tenant, recording_id)
        if revision is not None:
            snapshot, _ = self._migrate_flow_snapshot(
                tenant=tenant,
                recording_id=recording_id,
                lineage_id=self._lineage_id(
                    tenant,
                    recording_id,
                    session=session,
                    snapshot=revision.snapshot,
                ),
                snapshot=revision.snapshot,
            )
        else:
            snapshot = {
            "recording_id": recording_id,
            "tenant": tenant,
            "revision": 0,
            "steps": [],
            "capabilities": [],
            "links": [],
            "request_facts": {"requests": []},
            }
        report = snapshot.get("validation") or validate_workbench(deepcopy(snapshot))
        params = [
            {
                key: value for key, value in param.items()
                if key not in {"value", "sample", "sample_value"}
            }
            for step in snapshot.get("steps") or []
            for param in step.get("params") or []
        ]
        enums = [
            {
                "field_contract_id": param.get("field_id"),
                "step_id": param.get("step_id"),
                "wire_path": param.get("path"),
                "choice_contract": param.get("choice_contract"),
                "enum_options": param.get("enum_options"),
                "enum_value_map": param.get("enum_value_map"),
                "source_kind": param.get("source_kind"),
                "evidence": param.get("evidence") or [],
            }
            for param in params
            if param.get("choice_contract") or param.get("enum_options")
        ]
        facts = snapshot.get("request_facts") or {}
        requests = facts.get("requests") if isinstance(facts, dict) else facts
        human_context = _pi_human_context(snapshot, self.redaction)
        flow_target_uuid = str(snapshot.get("lineage_id") or self._lineage_id(
            tenant,
            recording_id,
            session=session,
            snapshot=snapshot,
        ))
        pi_projection = {
            "protocol": "dano.recording-v3.pi-state.v1",
            "recording_id": recording_id,
            "revision": revision.revision if revision else 0,
            "status": session.status.value,
            "target_uuid": flow_target_uuid,
            "flow_target": {"kind": "flow", "target_uuid": flow_target_uuid},
            "goal": human_context["goal"],
            "action": human_context["action"],
            "title": human_context["title"],
            "business_description": human_context["business_description"],
            "steps": [_pi_step(item) for item in snapshot.get("steps") or []],
            "capabilities": project_evidence_for_pi(snapshot.get("capabilities") or []),
            "relations": project_evidence_for_pi(snapshot.get("capability_relations") or []),
            "transactions": project_evidence_for_pi(snapshot.get("transactions") or []),
            "requests": [_pi_request(item) for item in list(requests or [])],
            "user_decisions": deepcopy((snapshot.get("meta") or {}).get("decision_origins") or {}),
            "validation": report,
            "enum_suggestions": project_evidence_for_pi(snapshot.get("enum_suggestions") or []),
            "capture_store": _pi_capture_store(snapshot.get("capture_store") or {}),
            "field_registry": project_evidence_for_pi(snapshot.get("field_registry") or {}),
            "evidence_graph_summary": project_evidence_for_pi(
                snapshot.get("evidence_graph_summary") or {}
            ),
            "js_bindings": _pi_js_bindings(snapshot),
            "raw_javascript_included": False,
        }
        return {
            "pi_projection": project_evidence_for_pi(pi_projection),
            "field_evidence": project_evidence_for_pi(params),
            "enum_evidence": project_evidence_for_pi(enums + list(snapshot.get("enum_evidence") or [])),
            "validation": project_evidence_for_pi(report),
        }

    async def _pi_submission(
        self,
        recording_id: str,
        tool: str,
        submission: dict[str, Any],
    ) -> dict[str, Any]:
        tenant = self._tenant_for(recording_id)
        session = await self.repository.get_session(tenant, recording_id)
        expected = int(submission.get("expected_revision", -1))
        if expected != session.current_revision:
            raise RevisionConflict(expected=expected, actual=session.current_revision)
        if tool == "submit_recording_review":
            return self.review_collector.submit_active(
                recording_id=recording_id,
                revision=expected,
                role=str(submission.get("role") or ""),
                verdict=submission,
            )
        current = await self.repository.get_revision(tenant, recording_id, expected)
        if current is None:
            raise DecisionCommandError("Pi cannot mutate a recording without a deterministic revision")
        if tool != "submit_recording_plan" or not is_semantic_operation_submission(submission):
            raise DecisionCommandError(
                "Pi mutations require one evidence-grounded semantic operation batch"
            )
        payload = dict(submission)
        payload["kind"] = "semantic_operations"
        next_spec = merge_pi_submission(current.snapshot, payload)
        revision, report = await self._commit_snapshot(
            tenant,
            recording_id,
            expected_revision=expected,
            snapshot=next_spec,
            actor="pi:planner",
        )
        await self.events.publish(tenant, recording_id, {
            "type": "flow_spec_updated",
            "operation": "pi_semantic_operations",
            "revision": revision.revision,
            "current_revision": revision.revision,
            "full_spec": revision.snapshot,
            "flow_spec": revision.snapshot,
            "check_report": report,
            "pi_status": self._client_pi_status(recording_id),
        })
        return {
            "accepted": True,
            "revision": revision.revision,
            "content_hash": revision.content_hash,
            "protected_user_decisions": len((revision.snapshot.get("meta") or {}).get("decision_origins") or {}),
        }

    async def _run_initial_pi(self, tenant: str, recording_id: str, revision: int) -> None:
        try:
            await self._ensure_pi_sessions(tenant, recording_id)
            await self.events.publish(tenant, recording_id, {
                "type": "pi_status",
                "available": True,
                "state": "running",
                "pi_status": self._client_pi_status(recording_id),
            })
            await self.pi.plan(recording_id, revision, mode=PiPlanMode.INITIAL)
            await self._ensure_pi_sessions(tenant, recording_id)
            await self.events.publish(tenant, recording_id, {
                "type": "pi_status",
                "available": True,
                "state": "idle",
                "pi_status": self._client_pi_status(recording_id),
            })
        except Exception as exc:  # noqa: BLE001 - deterministic draft remains valid
            await self._record_pi_unavailable(tenant, recording_id, exc)

    async def _pi_command(
        self,
        tenant: str,
        recording_id: str,
        message: dict[str, Any],
    ) -> None:
        kind = str(message.get("type") or "orchestrate_flow")
        operation: RecordingOperation | None = None
        try:
            operation, replay = await self._begin_operation(tenant, recording_id, message, kind=kind)
            if replay is not None:
                await self.events.publish(tenant, recording_id, replay)
                return
            session = await self.repository.get_session(tenant, recording_id)
            expected = int(message.get("expected_revision", session.current_revision))
            if expected != session.current_revision:
                raise RevisionConflict(expected=expected, actual=session.current_revision)
            await self._ensure_pi_sessions(tenant, recording_id)
            await self.events.publish(tenant, recording_id, {
                "type": "pi_status",
                "available": True,
                "state": "running",
                "operation": kind,
                "operation_id": operation.operation_id,
                "pi_status": self._client_pi_status(recording_id),
            })
            if kind == "auto_fix_flow":
                await self.pi.repair(recording_id, expected)
            else:
                mode = {
                    "orchestrate_flow": PiPlanMode.REPLAN,
                    "step_naming": PiPlanMode.STEP_NAMING,
                    "business_description": PiPlanMode.BUSINESS_DESCRIPTION,
                    "llm_recommendations": PiPlanMode.RECOMMENDATIONS,
                }[kind]
                await self.pi.plan(recording_id, expected, mode=mode)
            latest = await self.repository.get_revision(tenant, recording_id)
            if latest is None:
                raise DecisionCommandError("Pi completed without a recording revision")
            report = latest.snapshot.get("validation") or validate_workbench(deepcopy(latest.snapshot))
            response_type = {
                "step_naming": "step_names",
                "business_description": "business_description",
            }.get(kind, "flow_spec_updated")
            response = {
                "type": response_type,
                "operation": kind,
                "operation_id": operation.operation_id,
                "revision": latest.revision,
                "current_revision": latest.revision,
                "full_spec": latest.snapshot,
                "flow_spec": latest.snapshot,
                "description": latest.snapshot.get("business_description") or "",
                "check_report": report,
                "pi_status": self._client_pi_status(recording_id),
            }
            await self.repository.complete_operation(tenant, operation.operation_id, result=response)
            await self.events.publish(tenant, recording_id, response)
        except Exception as exc:  # noqa: BLE001
            if operation is not None:
                await self.repository.complete_operation(tenant, operation.operation_id, error=str(exc))
            await self._record_pi_unavailable(tenant, recording_id, exc, operation=kind, operation_id=message.get("operation_id"))

    async def _record_pi_unavailable(
        self,
        tenant: str,
        recording_id: str,
        error: BaseException,
        *,
        operation: str | None = None,
        operation_id: Any = None,
    ) -> None:
        await self.repository.update_session(
            tenant,
            recording_id,
            metadata={"pi_error": str(error), "pi_retryable": True},
        )
        await self.events.publish(tenant, recording_id, {
            "type": "error",
            "code": "pi_unavailable" if isinstance(error, PiUnavailable) else "pi_failed",
            "detail": f"Pi unavailable; deterministic draft was preserved: {error}",
            "retryable": True,
            "operation": operation,
            "operation_id": operation_id,
            "pi_status": self._client_pi_status(recording_id),
        })

    async def _publish_snapshot(self, recording_id: str, revision: int) -> dict[str, Any]:
        tenant = self._tenant_for(recording_id)
        session = await self.repository.get_session(tenant, recording_id)
        if revision != session.current_revision:
            raise RevisionConflict(expected=revision, actual=session.current_revision)
        value = await self.repository.get_revision(tenant, recording_id, revision)
        if value is None:
            raise DecisionCommandError(f"recording revision does not exist: {revision}")
        snapshot, _ = self._migrate_flow_snapshot(
            tenant=tenant,
            recording_id=recording_id,
            lineage_id=self._lineage_id(
                tenant,
                recording_id,
                session=session,
                snapshot=value.snapshot,
            ),
            snapshot=value.snapshot,
        )
        snapshot["revision"] = value.revision
        return snapshot

    async def _publish_command(
        self,
        tenant: str,
        recording_id: str,
        message: dict[str, Any],
    ) -> None:
        operation: RecordingOperation | None = None
        active_operation_id: str | None = None
        try:
            operation, replay = await self._begin_operation(
                tenant, recording_id, message, kind="publish_request"
            )
            if replay is not None:
                await self.events.publish(tenant, recording_id, replay)
                return
            self._active_publish_operations.add(operation.operation_id)
            active_operation_id = operation.operation_id
            session = await self.repository.get_session(tenant, recording_id)
            expected = int(message.get("expected_revision", session.current_revision))
            if expected != session.current_revision:
                raise RevisionConflict(expected=expected, actual=session.current_revision)
            current = await self.repository.get_revision(tenant, recording_id, expected)
            if current is None:
                raise DecisionCommandError("finalize the capture before publishing")
            current_snapshot, _ = self._migrate_flow_snapshot(
                tenant=tenant,
                recording_id=recording_id,
                lineage_id=self._lineage_id(
                    tenant,
                    recording_id,
                    session=session,
                    snapshot=current.snapshot,
                ),
                snapshot=current.snapshot,
            )
            action = str(message.get("action") or current_snapshot.get("action") or "").strip()
            if not action or not action[0].isalpha() or not action.replace("_", "").isalnum():
                raise DecisionCommandError("action must be an ASCII-style identifier beginning with a letter")
            title = str(message.get("title") or current_snapshot.get("title") or "").strip()
            if action != str(current_snapshot.get("action") or "") or (
                title and title != str(current_snapshot.get("title") or "")
            ):
                raise DecisionCommandError(
                    "publish_metadata_mismatch: sync action/title with flow_update before publishing"
                )
            release_revision = expected
            release_snapshot = current_snapshot
            await self.repository.update_session(tenant, recording_id, status=RecordingStatus.REVIEWING)
            try:
                await self._ensure_pi_sessions(tenant, recording_id)
            except Exception as exc:  # noqa: BLE001
                # Reviewer availability is represented by three server-owned
                # unavailable advisories inside the publication.  It must not
                # become a semantic publish gate.
                self.review_collector.ensure_server_sessions(recording_id)
                await self.events.publish(tenant, recording_id, {
                    "type": "pi_status",
                    "available": False,
                    "state": "unavailable",
                    "operation": "publish_review",
                    "detail": str(exc),
                    "retryable": True,
                    "pi_status": self._client_pi_status(recording_id),
                })
            result = await self.publisher.publish(recording_id, release_revision)
            published = bool(result.get("published"))
            if published:
                # From this point the asset transaction is committed.  Secondary
                # lifecycle/export synchronization must never turn that durable
                # success into a false "publish failed" response.
                sync_warnings: list[str] = []
                try:
                    await self.repository.update_session(
                        tenant, recording_id, status=RecordingStatus.PUBLISHED
                    )
                except Exception as exc:  # noqa: BLE001
                    sync_warnings.append(f"recording status sync pending: {exc}")
                try:
                    await self.repository.save_artifact(RecordingArtifact(
                        tenant=tenant,
                        recording_id=recording_id,
                        revision=release_revision,
                        kind="published_page_script",
                        content_hash=str(result.get("content_hash") or content_hash(result)),
                        storage_ref=str(result.get("asset_id") or ""),
                        metadata={
                            "recording_engine": "playwright_v3",
                            "skill_id": result.get("skill_id"),
                            "version": result.get("version"),
                        },
                    ))
                except Exception as exc:  # noqa: BLE001
                    sync_warnings.append(f"recording artifact index sync pending: {exc}")
                if self.lifecycle_callback:
                    try:
                        await self.lifecycle_callback({
                            "tenant": tenant,
                            "subsystem": release_snapshot.get("subsystem"),
                            "action": action,
                            "skill_id": result.get("skill_id"),
                            "version": int(result.get("version") or 0),
                            "recording_id": recording_id,
                            "revision": release_revision,
                        })
                    except Exception as exc:  # noqa: BLE001
                        sync_warnings.append(f"lifecycle sync pending: {exc}")
                if self.export_callback:
                    try:
                        await self.export_callback(tenant)
                    except Exception as exc:  # noqa: BLE001
                        sync_warnings.append(f"skill export sync pending: {exc}")
                if sync_warnings:
                    try:
                        await self.repository.update_session(
                            tenant,
                            recording_id,
                            status=RecordingStatus.PUBLISHED,
                            metadata={
                                "lifecycle_sync_pending": True,
                                "lifecycle_sync_warnings": sync_warnings,
                            },
                        )
                    except Exception:  # noqa: BLE001
                        pass
                result = {
                    **result,
                    "lifecycle_synced": not sync_warnings,
                    "sync_warnings": sync_warnings,
                }
            else:
                await self.repository.update_session(tenant, recording_id, status=RecordingStatus.DRAFT)
            report = {
                "ok": published,
                **result,
                "full_spec": release_snapshot,
                "check_report": release_snapshot.get("validation") or validate_workbench(deepcopy(release_snapshot)),
            }
            response = {
                "type": "result",
                "operation": "publish_request",
                "operation_id": operation.operation_id,
                "revision": release_revision,
                "current_revision": release_revision,
                "report": report,
                "pi_status": self._client_pi_status(recording_id),
            }
            if published:
                # Asset publication is the commit boundary. Persist a replay
                # copy before terminalizing the local operation; failures below
                # are post-commit sync work and must never roll back to DRAFT.
                post_commit_warnings: list[str] = []
                try:
                    latest = await self.repository.get_session(tenant, recording_id)
                    recovery = dict(latest.metadata.get("published_operation_results") or {})
                    recovery[operation.operation_id] = deepcopy(response)
                    await self.repository.update_session(
                        tenant,
                        recording_id,
                        status=RecordingStatus.PUBLISHED,
                        metadata={
                            "published_operation_results": dict(
                                list(recovery.items())[-50:]
                            )
                        },
                    )
                except Exception as exc:  # noqa: BLE001
                    post_commit_warnings.append(f"publish replay index sync pending: {exc}")
                try:
                    await self.repository.complete_operation(
                        tenant,
                        operation.operation_id,
                        result=response,
                    )
                except Exception as exc:  # noqa: BLE001
                    post_commit_warnings.append(f"publish operation sync pending: {exc}")
                try:
                    await self.events.publish(tenant, recording_id, response)
                except Exception as exc:  # noqa: BLE001
                    post_commit_warnings.append(f"publish result delivery pending: {exc}")
                if post_commit_warnings:
                    report["lifecycle_synced"] = False
                    report.setdefault("sync_warnings", []).extend(post_commit_warnings)
                    try:
                        latest = await self.repository.get_session(tenant, recording_id)
                        recovery = dict(
                            latest.metadata.get("published_operation_results") or {}
                        )
                        recovery[operation.operation_id] = deepcopy(response)
                        await self.repository.update_session(
                            tenant,
                            recording_id,
                            status=RecordingStatus.PUBLISHED,
                            metadata={
                                "published_operation_results": dict(
                                    list(recovery.items())[-50:]
                                ),
                                "lifecycle_sync_pending": True,
                                "lifecycle_sync_warnings": list(
                                    dict.fromkeys([
                                        *latest.metadata.get("lifecycle_sync_warnings", []),
                                        *post_commit_warnings,
                                    ])
                                ),
                            },
                        )
                    except Exception:  # noqa: BLE001
                        pass
                return
            await self.repository.complete_operation(
                tenant,
                operation.operation_id,
                result=response,
            )
            await self.events.publish(tenant, recording_id, response)
        except Exception as exc:  # noqa: BLE001
            if operation is not None:
                await self.repository.complete_operation(tenant, operation.operation_id, error=str(exc))
                try:
                    await self.repository.update_session(
                        tenant, recording_id, status=RecordingStatus.DRAFT
                    )
                except Exception:  # noqa: BLE001
                    pass
            await self.events.publish(tenant, recording_id, {
                "type": "result",
                "operation": "publish_request",
                "operation_id": message.get("operation_id"),
                "report": {
                    "ok": False,
                    "published": False,
                    "stage": "pi_unavailable" if isinstance(exc, PiUnavailable) else "publish",
                    "reason": str(exc),
                    "retryable": isinstance(exc, PiUnavailable),
                },
                "pi_status": self._client_pi_status(recording_id),
            })
        finally:
            if active_operation_id is not None:
                self._active_publish_operations.discard(active_operation_id)

    async def _record_console(self, tenant: str, recording_id: str, entries: Any) -> None:
        live = await self._get_live(tenant, recording_id)
        safe = self.redaction.redact_value(list(entries or [])[:1_000])
        live.ledger.emit(
            RecordingFact,
            kind=FactKind.DIAGNOSTIC,
            payload={"type": "client_console_upload", "entries": safe},
            redacted=True,
        )

    async def stop_recording(self, tenant: str, recording_id: str) -> None:
        live = self.live.pop((tenant, recording_id), None)
        if live is not None:
            await self._close_live(live, close_browser=True)
        await self.repository.update_session(
            tenant,
            recording_id,
            status=RecordingStatus.CLOSED,
            browser_lease_until=None,
        )

    async def _close_live(self, live: LiveRecording, *, close_browser: bool) -> None:
        live.started = False
        live.capture_active = False
        if live.frame_task:
            live.frame_task.cancel()
            await asyncio.gather(live.frame_task, return_exceptions=True)
            live.frame_task = None
        for task in tuple(live.background_tasks):
            task.cancel()
        if live.background_tasks:
            await asyncio.gather(*live.background_tasks, return_exceptions=True)
        if live.runtime:
            await live.runtime.close()
        else:
            if live.network:
                await live.network.close()
            if live.scripts:
                await live.scripts.close()
            if live.capture:
                await live.capture.close()
        await self._drain_facts(live)
        if close_browser:
            closed = await self.browser_sessions.close_session(
                tenant=live.tenant,
                recording_id=live.recording_id,
            )
            if not closed:
                for resource in (live.context, live.browser):
                    closer = getattr(resource, "close", None)
                    if closer:
                        value = closer()
                        if asyncio.iscoroutine(value):
                            await value

    def _spawn(self, awaitable: Awaitable[Any], *, name: str) -> asyncio.Task[Any]:
        task = asyncio.create_task(awaitable, name=name)
        self._service_tasks.add(task)
        task.add_done_callback(self._service_tasks.discard)
        return task

    def _tenant_for(self, recording_id: str) -> str:
        tenant = self._recording_tenants.get(recording_id)
        if not tenant:
            raise PermissionError("recording tenant binding is unavailable")
        return tenant

    def _client_pi_status(self, recording_id: str) -> dict[str, Any]:
        sessions = self.pi.status(recording_id)
        planner = deepcopy(sessions.get("planner") or {})
        if planner:
            planner["status"] = planner.get("state") or "idle"
        return {
            **planner,
            "available": self.pi_client.running,
            "sessions": sessions,
            "reviewers": {
                role: value for role, value in sessions.items() if role != "planner"
            },
        }


class _MissingAssetWriter:
    async def publish(self, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("Dano asset publication is not configured")


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""


def _parse_storage_state(value: Any) -> dict[str, Any] | None:
    if value in (None, "", {}):
        return None
    if isinstance(value, str):
        if len(value.encode("utf-8")) > 2_097_152:
            raise DecisionCommandError("storage_state exceeds 2 MiB")
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise DecisionCommandError("storage_state must be JSON, not a filesystem path") from exc
    if not isinstance(value, dict):
        raise DecisionCommandError("storage_state must be an object")
    allowed = {"cookies", "origins"}
    if set(value) - allowed:
        raise DecisionCommandError("storage_state contains unsupported keys")
    return deepcopy(value)


def _flatten(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        rows: list[tuple[str, Any]] = []
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            rows.extend(_flatten(item, path))
        return rows
    if isinstance(value, list):
        return [(prefix, value)]
    return [(prefix, value)]


def _captured_fields(fact: RequestFact) -> list[dict[str, Any]]:
    rows: list[tuple[str, Any]] = []
    for key, value in fact.query_items:
        rows.append((f"query.{key}", value))
    if fact.request_body is not None:
        rows.extend((f"body.{path}" if path else "body", value) for path, value in _flatten(fact.request_body))
    return [
        {
            "path": path,
            "key": path.rsplit(".", 1)[-1],
            "value": value,
            "suggest_param": True,
            "suggest_name": path.rsplit(".", 1)[-1],
        }
        for path, value in rows
    ]


def _stored_pi_status(value: str) -> StoredPiStatus:
    mapping = {
        "running": StoredPiStatus.RUNNING,
        "idle": StoredPiStatus.IDLE,
        "failed": StoredPiStatus.FAILED,
        "closed": StoredPiStatus.CLOSED,
    }
    return mapping.get(str(value), StoredPiStatus.OPEN)


def _pi_human_context(
    snapshot: dict[str, Any],
    redaction: RedactionPolicy | None = None,
) -> dict[str, Any]:
    """Sanitize free-form user text before it can enter a Pi session."""

    policy = redaction or RedactionPolicy()
    return {
        "goal": policy.redact_value(snapshot.get("goal") or {}),
        "action": policy.redact_text(str(snapshot.get("action") or "")),
        "title": policy.redact_text(str(snapshot.get("title") or "")),
        "business_description": policy.redact_text(
            str(snapshot.get("business_description") or "")
        ),
    }


def _pi_step(step: dict[str, Any]) -> dict[str, Any]:
    return project_evidence_for_pi({
        key: value for key, value in step.items()
        if key not in {"headers", "body", "body_template", "body_source", "response_json", "sample_inputs"}
    })


def _pi_request(request: dict[str, Any]) -> dict[str, Any]:
    return project_evidence_for_pi({
        key: value for key, value in request.items()
        if key not in {"headers", "post_data", "response_json", "request_body", "response_body"}
    })


def _pi_capture_store(value: Any) -> dict[str, Any]:
    """Project structural capture evidence without recorded business payloads."""

    source = deepcopy(value) if isinstance(value, dict) else {}
    source.pop("records", None)
    observations = []
    for raw_observation in source.get("observations") or ():
        if not isinstance(raw_observation, dict):
            continue
        observation = deepcopy(raw_observation)
        for direction in ("request_values", "response_values"):
            evidence_rows = []
            for raw_evidence in observation.get(direction) or ():
                if not isinstance(raw_evidence, dict):
                    continue
                evidence = deepcopy(raw_evidence)
                evidence.pop("redacted_sample", None)
                evidence.pop("value_ref", None)
                evidence_rows.append(evidence)
            observation[direction] = evidence_rows
        # Initiator stacks can contain transient URLs/arguments; graph-level
        # action/page anchors are sufficient for Pi.
        observation.pop("initiator", None)
        observations.append(observation)
    source["observations"] = observations
    source["scripts"] = [
        {
            "script_hash": script.get("content_hash"),
            "size": script.get("size"),
            "truncated": script.get("truncated"),
            "evidence_ids": list(script.get("evidence_ids") or ()),
        }
        for script in source.get("scripts") or ()
        if isinstance(script, dict)
    ]
    return project_evidence_for_pi(source)


def _pi_js_bindings(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Build searchable, source-free static symbol binding summaries."""

    field_uuid_by_contract: dict[str, str] = {}
    for step in snapshot.get("steps") or ():
        if not isinstance(step, dict):
            continue
        for parameter in step.get("params") or ():
            if not isinstance(parameter, dict):
                continue
            contract_id = str(
                parameter.get("field_contract_id") or parameter.get("field_id") or ""
            )
            field_uuid = str(
                parameter.get("field_uuid") or parameter.get("field_id") or ""
            )
            if contract_id and field_uuid:
                field_uuid_by_contract[contract_id] = field_uuid
    request_definition_by_id = {
        str(item.get("request_id") or ""): str(
            item.get("request_definition_id") or ""
        )
        for item in ((snapshot.get("request_facts") or {}).get("requests") or ())
        if isinstance(item, dict)
    }
    enum_rows = [
        item
        for item in [
            *(snapshot.get("enum_evidence") or ()),
            *(snapshot.get("enum_suggestions") or ()),
        ]
        if isinstance(item, dict) and item.get("symbol_path")
    ]
    enum_by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in enum_rows:
        enum_by_symbol[str(item.get("symbol_path"))].append(item)

    bindings: list[dict[str, Any]] = []
    capture_store = snapshot.get("capture_store") or {}
    for script in capture_store.get("scripts") or ():
        if not isinstance(script, dict):
            continue
        analysis = script.get("analysis") or {}
        script_hash = str(
            analysis.get("script_hash") or script.get("content_hash") or ""
        )
        for candidate in analysis.get("candidates") or ():
            if not isinstance(candidate, dict):
                continue
            symbol = str(candidate.get("symbol_path") or "")
            matches = enum_by_symbol.get(symbol) or [{}]
            for evidence in matches:
                contract_id = str(
                    evidence.get("field_contract_id")
                    or evidence.get("field_id")
                    or ""
                )
                request_id = str(evidence.get("request_id") or "")
                row = {
                    "field_uuid": str(
                        evidence.get("field_uuid")
                        or field_uuid_by_contract.get(contract_id)
                        or ""
                    ) or None,
                    "control_uuid": str(
                        evidence.get("control_uuid")
                        or evidence.get("control_id")
                        or ""
                    ) or None,
                    "request_id": request_id or None,
                    "request_definition_id": request_definition_by_id.get(request_id)
                    or None,
                    "wire_path": evidence.get("wire_path"),
                    "symbol": symbol,
                    "script_hash": script_hash or None,
                    "proofs": list(
                        dict.fromkeys(
                            [
                                *[str(item) for item in candidate.get("proofs") or ()],
                                *[str(item) for item in evidence.get("proofs") or ()],
                            ]
                        )
                    ),
                }
                bindings.append({key: value for key, value in row.items() if value is not None})
    unique: dict[str, dict[str, Any]] = {}
    for row in bindings:
        key = json.dumps(row, sort_keys=True, ensure_ascii=False, default=str)
        unique[key] = row
    return list(unique.values())


__all__ = ["RecordingApplication"]
