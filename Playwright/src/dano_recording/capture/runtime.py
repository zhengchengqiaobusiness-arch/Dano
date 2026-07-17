"""Composition root for a single recording browser's capture observers."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import inspect
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from dano_recording.capture.action_transactions import ActionTracker
from dano_recording.capture.browser_session import BrowserCapture
from dano_recording.capture.input_dispatcher import InputDispatcher
from dano_recording.capture.ledger import FactLedger
from dano_recording.capture.network_observer import NetworkObserver, NetworkObserverConfig
from dano_recording.capture.redaction import RedactionPolicy
from dano_recording.capture.safety import URLSafetyPolicy
from dano_recording.capture.screencast import ScreenshotCollector, ScreenshotStore
from dano_recording.capture.tasks import TaskSupervisor
from dano_recording.domain.facts import FactKind, RecordingFact
from dano_recording.evidence.loaded_scripts import LoadedScriptCollector, SourceStore
from dano_recording.evidence.dom_controls import DOMControlCollector
from dano_recording.evidence.runtime_components import RuntimeComponentCollector
from dano_recording.value_evidence import ValueEvidenceFactory


@dataclass(frozen=True, slots=True)
class _SecuredMutation:
    """Python-only marker; page dictionaries can never opt into this path."""

    payload: dict[str, Any]


class CaptureRuntime:
    """Wire page, network, diagnostics, input, screenshot, and script capture.

    CDP script collection is best-effort (for example Firefox has no Chromium
    CDP session); network and user-action facts remain available when it safely
    degrades.
    """

    def __init__(
        self,
        ledger: FactLedger,
        *,
        network_config: NetworkObserverConfig | None = None,
        redaction: RedactionPolicy | None = None,
        url_policy: URLSafetyPolicy | None = None,
        network_url_policy: URLSafetyPolicy | None = None,
        source_store: SourceStore | None = None,
        screenshot_store: ScreenshotStore | None = None,
        value_evidence_factory: ValueEvidenceFactory | None = None,
        recording_lineage: str | None = None,
    ) -> None:
        self.ledger = ledger
        self.redaction = redaction or RedactionPolicy()
        self.url_policy = url_policy
        self.value_evidence_factory = value_evidence_factory
        self.recording_lineage = str(recording_lineage or "")
        self.actions = ActionTracker()
        self.browser = BrowserCapture(ledger, redaction=self.redaction)
        self.network = NetworkObserver(
            ledger,
            config=network_config,
            redaction=self.redaction,
            url_policy=network_url_policy or url_policy,
            navigation_url_policy=url_policy,
            action_id_provider=self.actions.current,
            page_id_resolver=self.browser.page_id,
            frame_id_resolver=self.browser.frame_id,
            value_evidence_factory=value_evidence_factory,
            recording_lineage=recording_lineage,
            response_started=self._response_started,
            response_collected=self._response_collected,
        )
        self.input = InputDispatcher(
            ledger,
            redaction=self.redaction,
            url_policy=url_policy,
            action_tracker=self.actions,
            value_evidence_factory=value_evidence_factory,
            recording_lineage=recording_lineage,
        )
        self.screenshots = ScreenshotCollector(ledger, store=screenshot_store)
        self.scripts = LoadedScriptCollector(
            ledger,
            source_store=source_store,
            redaction=self.redaction,
        )
        self.dom_controls = DOMControlCollector(
            ledger,
            redaction=self.redaction,
            value_evidence_factory=value_evidence_factory,
            recording_lineage=recording_lineage,
        )
        self.runtime_components = RuntimeComponentCollector(redaction=self.redaction)
        self.tasks = TaskSupervisor(self._optional_capture_error)
        self._contexts: set[int] = set()
        self._context_objects: dict[int, Any] = {}
        self._listeners: list[tuple[Any, str, Any]] = []
        self._prepared_pages: set[int] = set()
        self._response_control_snapshots: dict[
            str, dict[str, _SecuredMutation]
        ] = {}
        self._closed = False
        self._paused = False
        self._script_reenumeration_required = False
        self._browser_scripts = tuple(
            Path(__file__).resolve().parents[1] / "_resources" / "browser" / name
            for name in ("recorder.js", "component_probe.js", "mutation_observer.js")
        )

    async def attach(self, context: Any) -> None:
        if self._closed:
            raise RuntimeError("capture runtime is closed")
        if id(context) in self._contexts:
            return
        try:
            await self._install_browser_hooks(context)
        except Exception as exc:
            self._optional_capture_error(exc)
        self.browser.attach_context(context)
        await self.network.attach(context)

        def attach_page(page: Any) -> None:
            if self._closed:
                return
            self._attach_runtime_page(context, page)

        context.on("page", attach_page)
        self._listeners.append((context, "page", attach_page))
        for page in tuple(getattr(context, "pages", ()) or ()):
            attach_page(page)
        self._contexts.add(id(context))
        self._context_objects[id(context)] = context

    def _attach_runtime_page(self, context: Any, page: Any) -> None:
        page_id = self.browser.attach_page(page)
        page_key = id(page)
        if self._paused or page_key in self._prepared_pages:
            return
        self._prepared_pages.add(page_key)
        self.tasks.create(self._prepare_page(context, page, page_id=page_id))

    async def _install_browser_hooks(self, context: Any) -> None:
        async def receive_action(source: Any, payload: Any) -> None:
            if self._closed or self._paused:
                return
            if not isinstance(payload, dict):
                return
            if isinstance(source, dict):
                page = source.get("page")
                frame = source.get("frame")
            else:
                page = getattr(source, "page", None)
                frame = getattr(source, "frame", None)
            fact = self.input.record_observed(
                action_type=str(payload.get("event") or "browser_event"),
                page_id=self.browser.page_id(page),
                frame_id=self.browser.frame_id(frame),
                locator=str(payload.get("selector") or "") or None,
                details=payload,
            )
            # Drain after recording the causal action.  The browser hook is
            # invoked during event dispatch, so yielding one task turn lets
            # synchronous framework mutations enter the observer queue first.
            self.tasks.create(
                self._drain_mutations(
                    frame,
                    page_id=self.browser.page_id(page),
                    frame_id=self.browser.frame_id(frame),
                    action_id=fact.action_id,
                    phase="after_action",
                )
            )

        exposed = context.expose_binding("__danoRecordAction", receive_action)
        if inspect.isawaitable(exposed):
            await exposed
        for script_path in self._browser_scripts:
            added = context.add_init_script(path=str(script_path))
            if inspect.isawaitable(added):
                await added

    async def _response_collected(
        self,
        response: Any,
        fact: RecordingFact,
    ) -> None:
        request = getattr(response, "request", None)
        request = request() if callable(request) else request
        frame = getattr(request, "frame", None) if request is not None else None
        frame = frame() if callable(frame) else frame
        page = getattr(frame, "page", None) if frame is not None else None
        page = page() if callable(page) else page
        # Response handlers commonly update controlled inputs in a microtask or
        # timer.  Advance one browser event-loop turn before comparing state.
        if frame is not None and getattr(frame, "evaluate", None) is not None:
            advanced = frame.evaluate(
                "() => new Promise(resolve => setTimeout(resolve, 0))"
            )
            if inspect.isawaitable(advanced):
                await advanced
        request_id = str(fact.payload.get("request_id") or "")
        before = self._response_control_snapshots.pop(request_id, {})
        after = await self._snapshot_control_values(frame)
        changed = [
            _SecuredMutation({
                **row.payload,
                "mutation_type": "property_state",
                "attribute_name": "value",
            })
            for key, row in after.items()
            if key not in before
            or self._snapshot_fingerprints(before[key])
            != self._snapshot_fingerprints(row)
        ]
        await self._drain_mutations(
            frame,
            page_id=fact.page_id or self.browser.page_id(page),
            frame_id=self.browser.frame_id(frame),
            action_id=fact.action_id,
            phase="after_response",
            request_id=request_id or None,
            supplemental_rows=changed,
        )

    async def _response_started(self, response: Any, request_fact: Any) -> None:
        request = getattr(response, "request", None)
        request = request() if callable(request) else request
        frame = getattr(request, "frame", None) if request is not None else None
        frame = frame() if callable(frame) else frame
        self._response_control_snapshots[request_fact.request_id] = (
            await self._snapshot_control_values(frame)
        )

    @staticmethod
    def _snapshot_fingerprints(row: _SecuredMutation) -> tuple[str, ...]:
        payload = row.payload
        return tuple(
            sorted(
                str(item.get("scoped_hmac") or "")
                for item in payload.get("value_evidence") or ()
                if isinstance(item, dict) and item.get("scoped_hmac")
            )
        ) + (str(payload.get("checked")),)

    async def _snapshot_control_values(
        self,
        frame: Any,
    ) -> dict[str, _SecuredMutation]:
        if frame is None or getattr(frame, "evaluate", None) is None:
            return {}
        probe = r"""
        () => Array.from(document.querySelectorAll('input,select,textarea,[contenteditable=true]'))
          .slice(0, 500)
          .map((el, index) => {
            const id = el.id || '';
            const testId = el.getAttribute('data-testid') || '';
            const name = el.getAttribute('name') || '';
            const selector = id ? `#${CSS.escape(id)}`
              : testId ? `[data-testid="${CSS.escape(testId)}"]`
              : name ? `${el.tagName.toLowerCase()}[name="${CSS.escape(name)}"]`
              : `${el.tagName.toLowerCase()}:nth-of-type(${index + 1})`;
            const inputType = el.getAttribute('type') || '';
            const sensitive = inputType.toLowerCase() === 'password'
              || /password|passwd|secret|token/i.test(name);
            return {
              selector, name, inputType,
              controlTag: el.tagName.toLowerCase(),
              role: el.getAttribute('role') || null,
              value: sensitive ? undefined : ('value' in el ? el.value : el.textContent),
              checked: 'checked' in el ? Boolean(el.checked) : undefined,
              sensitive,
            };
          })
        """
        try:
            result = frame.evaluate(probe)
            rows = await result if inspect.isawaitable(result) else result
        except Exception as exc:
            self._optional_capture_error(exc)
            return {}
        secured: dict[str, _SecuredMutation] = {}
        for raw_row in rows if isinstance(rows, list) else ():
            if not isinstance(raw_row, dict):
                continue
            row = _SecuredMutation(
                self._secure_mutation_row(
                    raw_row,
                    evidence_origin="server_snapshot",
                    causal_eligible=True,
                )
            )
            key = str(row.payload.get("selector") or row.payload.get("name") or "")
            if key:
                secured[key] = row
        return secured

    def _secure_mutation_row(
        self,
        raw_row: dict[str, Any],
        *,
        evidence_origin: str = "page_observed",
        causal_eligible: bool = False,
    ) -> dict[str, Any]:
        row = {
            # These values are constructed in Python and never copied from the
            # page dictionary. MutationObserver drain rows are page-controlled
            # hints; only an explicit server snapshot can become causal proof.
            "evidence_origin": evidence_origin,
            "causal_eligible": causal_eligible,
            "mutation_type": str(
                raw_row.get("mutationType")
                or raw_row.get("mutation_type")
                or "unknown"
            )[:32],
            "attribute_name": str(
                raw_row.get("attributeName")
                or raw_row.get("attribute_name")
                or ""
            )[:128] or None,
            "tag": str(raw_row.get("tag") or "")[:64],
            "control_tag": str(
                raw_row.get("controlTag") or raw_row.get("control_tag") or ""
            )[:64] or None,
            "selector": self.redaction.redact_text(
                str(raw_row.get("selector") or "")[:512]
            ),
            "name": self.redaction.redact_text(str(raw_row.get("name") or "")[:256]),
            "input_type": str(
                raw_row.get("inputType") or raw_row.get("input_type") or ""
            )[:64],
            "role": str(raw_row.get("role") or "")[:128] or None,
            "checked": bool(raw_row.get("checked"))
            if raw_row.get("checked") is not None else None,
            "timestamp": raw_row.get("timestamp"),
        }
        # Reserved evidence fields supplied by page JavaScript are ignored.
        # Only the raw value crosses this method and the server creates the
        # authoritative ValueEvidence.
        if "value" in raw_row:
            if self.value_evidence_factory is not None:
                safe, evidence = self.value_evidence_factory.capture_tree(
                    tenant_scope=self.ledger.tenant,
                    recording_lineage=self.recording_lineage,
                    value=raw_row.get("value"),
                    root_path="mutation.value",
                    field_name=str(raw_row.get("name") or "value"),
                )
                row["value"] = safe
                row["value_evidence"] = [
                    item.model_dump(mode="json", exclude_none=True)
                    for item in evidence
                ]
            else:
                row["value"] = self.redaction.redact_value(
                    raw_row.get("value"), key=str(raw_row.get("name") or "value")
                )
        return row

    async def _drain_mutations(
        self,
        frame: Any,
        *,
        page_id: str | None,
        frame_id: str | None,
        action_id: str | None,
        phase: str,
        request_id: str | None = None,
        supplemental_rows: list[_SecuredMutation] | None = None,
    ) -> RecordingFact | None:
        if frame is None:
            return None
        evaluate = getattr(frame, "evaluate", None)
        if evaluate is None:
            return None
        try:
            raw = evaluate(
                "() => typeof globalThis.__danoDrainMutations === 'function' "
                "? globalThis.__danoDrainMutations().slice(0, 200) : []"
            )
            rows = await raw if inspect.isawaitable(raw) else raw
        except Exception as exc:
            self._optional_capture_error(exc)
            return None
        if not isinstance(rows, list):
            rows = []
        mutations: list[dict[str, Any]] = []
        for raw_row in rows[:200]:
            if not isinstance(raw_row, dict):
                continue
            mutations.append(
                self._secure_mutation_row(
                    raw_row,
                    evidence_origin="page_observed",
                    causal_eligible=False,
                )
            )
        mutations.extend(
            deepcopy(item.payload) for item in (supplemental_rows or [])[:200]
        )
        if not mutations:
            return None
        return self.ledger.emit(
            RecordingFact,
            kind=FactKind.DOM_MUTATION,
            action_id=action_id,
            page_id=page_id,
            payload={
                "frame_id": frame_id,
                "phase": phase,
                "request_id": request_id,
                "mutations": mutations,
            },
            redacted=True,
        )

    async def _prepare_page(self, context: Any, page: Any, *, page_id: str) -> None:
        # Init scripts cover future documents; install once in an already-loaded
        # document as well so a resumed lease starts capturing immediately.
        targets = tuple(getattr(page, "frames", ()) or ()) or (page,)
        for target in targets:
            for script_path in self._browser_scripts:
                try:
                    added = target.add_script_tag(path=str(script_path))
                    if inspect.isawaitable(added):
                        await added
                except Exception as exc:
                    self._optional_capture_error(exc)
        try:
            await self.scripts.attach_cdp(context, page, page_id=page_id)
        except Exception as exc:
            self._optional_capture_error(exc)

    async def collect_page_evidence(self, page: Any) -> dict[str, Any]:
        """Collect current DOM/select and component clues on demand."""

        page_id = self.browser.page_id(page) or self.browser.attach_page(page)
        controls = []
        components = []
        for frame in tuple(getattr(page, "frames", ()) or ()):
            frame_id = self.browser.frame_id(frame)
            if frame_id is None:
                self.browser.frame_event(frame, page_id=page_id, event="evidence_snapshot")
                frame_id = self.browser.frame_id(frame)
            try:
                frame_controls = await self.dom_controls.collect(
                    frame,
                    page_id=page_id,
                    frame_id=frame_id,
                )
                controls.extend(frame_controls)
            except Exception as exc:
                self._optional_capture_error(exc)
                frame_controls = ()
            try:
                frame_components = await self.runtime_components.collect(frame)
                ids_by_selector: dict[str, set[str]] = {}
                for control in frame_controls:
                    ids_by_selector.setdefault(control.selector, set()).add(control.control_id)
                for clue in frame_components:
                    matching = ids_by_selector.get(clue.selector or "", set())
                    if clue.control_id is None and len(matching) == 1:
                        clue = replace(clue, control_id=next(iter(matching)))
                    components.append(clue)
            except Exception as exc:
                self._optional_capture_error(exc)
        return {"controls": tuple(controls), "runtime_components": tuple(components)}

    def _optional_capture_error(self, error: BaseException) -> None:
        self.browser.diagnostics.emit(
            "optional_evidence_unavailable",
            error_type=type(error).__name__,
            message=str(error),
        )

    async def pause(self) -> None:
        """Freeze live observers without removing the context binding."""

        if self._closed or self._paused:
            return
        self._paused = True
        self.browser.pause()
        # Gate both observers before either bounded drain can wait. Otherwise a
        # slow script body leaves a window where fresh network requests enter
        # the generation after freeze has already begun.
        await asyncio.gather(self.scripts.pause(), self.network.pause())
        if not await self.tasks.drain(timeout=5.0):
            await self.tasks.cancel_pending()

    async def resume(self, context: Any) -> None:
        """Resume the same observer objects; never expose a duplicate binding."""

        if self._closed:
            raise RuntimeError("capture runtime is closed")
        if not self._paused:
            return
        self._paused = False
        self.browser.resume()
        self.scripts.resume()
        await self.network.resume(context)
        if self._script_reenumeration_required:
            for owner in tuple(self._context_objects.values()):
                for page in tuple(getattr(owner, "pages", ()) or ()):
                    if id(page) not in self._prepared_pages:
                        continue
                    try:
                        await self.scripts.attach_cdp(
                            owner,
                            page,
                            page_id=self.browser.page_id(page),
                        )
                    except Exception as exc:  # noqa: BLE001 - optional evidence
                        self._optional_capture_error(exc)
            self._script_reenumeration_required = False
        for owner in tuple(self._context_objects.values()):
            for page in tuple(getattr(owner, "pages", ()) or ()):
                self._attach_runtime_page(owner, page)

    async def reset_generation(self) -> None:
        """Reset generation-owned observer caches while retaining browser hooks."""

        if self._closed:
            raise RuntimeError("capture runtime is closed")
        if not self._paused:
            raise RuntimeError("capture runtime must be paused before generation reset")
        self.network.reset_generation()
        self._response_control_snapshots.clear()
        await self.scripts.reset_generation()
        current_pages = {
            id(page)
            for owner in self._context_objects.values()
            for page in tuple(getattr(owner, "pages", ()) or ())
        }
        self._prepared_pages.intersection_update(current_pages)
        self._script_reenumeration_required = True

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        listeners, self._listeners = self._listeners, []
        for emitter, event, handler in listeners:
            remove = getattr(emitter, "remove_listener", None)
            if remove is not None:
                try:
                    remove(event, handler)
                except Exception:
                    pass
        self._contexts.clear()
        self._context_objects.clear()
        drained = await self.tasks.drain(timeout=5.0)
        await self.tasks.close(cancel=not drained)
        await self.network.close()
        await self.scripts.close()
        await self.browser.close()

    async def __aenter__(self) -> "CaptureRuntime":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()
