"""Typed browser input dispatch with action facts emitted before execution."""

from __future__ import annotations

import inspect
import re
from typing import Any

from dano_recording.capture.action_transactions import ActionScope, ActionTracker
from dano_recording.capture.diagnostics import DiagnosticsObserver
from dano_recording.capture.ledger import FactLedger
from dano_recording.capture.redaction import RedactionPolicy
from dano_recording.capture.safety import URLSafetyPolicy
from dano_recording.domain._base import new_id
from dano_recording.domain.facts import ActionFact
from dano_recording.value_evidence import ValueEvidence, ValueEvidenceFactory


async def _await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


_CLICK_TARGET_SCRIPT = r"""
({x, y}) => {
  const hit = document.elementFromPoint(x, y);
  if (!hit) return null;
  const target = hit.closest?.(
    "button,a,input,select,textarea,[role='button'],[role='link']," +
    "[role='menuitem'],[aria-label],[aria-labelledby],[name]"
  ) || hit;
  const clipped = (value) => String(value || "").replace(/\s+/g, " ").trim().slice(0, 256);
  const labelledBy = clipped(target.getAttribute?.("aria-labelledby"));
  const labelledText = labelledBy
    ? labelledBy.split(/\s+/).map((id) => clipped(document.getElementById(id)?.textContent)).filter(Boolean).join(" ")
    : "";
  const associatedLabel = Array.from(target.labels || [])
    .map((label) => clipped(label.innerText || label.textContent)).filter(Boolean).join(" ");
  const inputType = clipped(target.getAttribute?.("type")).toLowerCase();
  const buttonValue = ["button", "submit", "reset"].includes(inputType)
    ? clipped(target.value)
    : "";
  return {
    aria_label: clipped(target.getAttribute?.("aria-label")),
    labelled_text: clipped(labelledText),
    associated_label: clipped(associatedLabel),
    title: clipped(target.getAttribute?.("title")),
    visible_text: clipped(target.innerText || target.textContent),
    button_value: buttonValue,
    name: clipped(target.getAttribute?.("name")),
  };
}
"""


async def describe_click_target(
    page: Any,
    *,
    x: float,
    y: float,
    redaction: RedactionPolicy | None = None,
) -> str:
    """Read a bounded business label for a server-dispatched coordinate click.

    Page text is only descriptive evidence.  It never supplies the action id or
    the causal trust flags, which remain owned by the Python dispatch boundary.
    Input values are deliberately excluded except for button-like controls.
    """

    policy = redaction or RedactionPolicy()
    try:
        raw = await _await(page.evaluate(_CLICK_TARGET_SCRIPT, {"x": x, "y": y}))
    except Exception:  # noqa: BLE001 - an unavailable label must not block the click
        return ""
    if not isinstance(raw, dict):
        return ""
    for key in (
        "aria_label",
        "labelled_text",
        "associated_label",
        "title",
        "visible_text",
        "button_value",
        "name",
    ):
        candidate = re.sub(r"\s+", " ", str(raw.get(key) or "")).strip()
        if not candidate:
            continue
        candidate = policy.redact_text(candidate)[:160].strip()
        if candidate and candidate != policy.replacement:
            return candidate
    return ""


class InputDispatcher:
    """Dispatch a small allowlist of Playwright operations.

    There is no generic JavaScript/evaluate operation here.  Every attempted
    action remains an immutable fact even when the browser call fails.
    """

    _TARGET_ACTIONS = {
        "click",
        "fill",
        "type",
        "press",
        "select_option",
        "check",
        "uncheck",
        "hover",
        "focus",
    }

    def __init__(
        self,
        ledger: FactLedger,
        *,
        redaction: RedactionPolicy | None = None,
        url_policy: URLSafetyPolicy | None = None,
        action_tracker: ActionTracker | None = None,
        value_evidence_factory: ValueEvidenceFactory | None = None,
        recording_lineage: str | None = None,
    ) -> None:
        self.ledger = ledger
        self.redaction = redaction or RedactionPolicy()
        self.url_policy = url_policy or URLSafetyPolicy(allow_private_networks=True)
        self.action_tracker = action_tracker or ActionTracker()
        if value_evidence_factory is not None and not str(recording_lineage or "").strip():
            raise ValueError("recording_lineage is required with value_evidence_factory")
        self.value_evidence_factory = value_evidence_factory
        self.recording_lineage = str(recording_lineage or "")
        self.diagnostics = DiagnosticsObserver(ledger, redaction=self.redaction)

    def _secure_action_details(self, details: dict[str, Any]) -> dict[str, Any]:
        reserved = {
            "value_evidence", "value_ref", "scoped_hmac", "runtime_resolver",
            "evidence_id", "retention", "redacted_sample", "sensitivity",
            # Trust is assigned by this Python boundary. A page may call the
            # public Playwright binding directly, so payload fields with these
            # names can never opt an observed event into causal evidence.
            "evidence_origin", "causal_eligible", "trusted", "trust",
        }
        trusted_input = {
            key: value for key, value in details.items()
            if str(key).casefold() not in reserved
        }
        clean = self.redaction.redact_value(trusted_input)
        if self.value_evidence_factory is None:
            return clean
        field_name = str(
            trusted_input.get("name")
            or trusted_input.get("fieldName")
            or trusted_input.get("field_name")
            or trusted_input.get("autocomplete")
            or "value"
        )
        captured: list[ValueEvidence] = []
        for key in ("value", "text", "options"):
            if key not in trusted_input:
                continue
            safe, evidence = self.value_evidence_factory.capture_tree(
                tenant_scope=self.ledger.tenant,
                recording_lineage=self.recording_lineage,
                value=trusted_input[key],
                root_path=f"action.{key}",
                field_name=field_name,
            )
            clean[key] = safe
            captured.extend(evidence)
        if captured:
            clean["value_evidence"] = [
                item.model_dump(mode="json", exclude_none=True)
                for item in captured
            ]
        return clean

    async def dispatch(
        self,
        page: Any,
        command: dict[str, Any],
        *,
        page_id: str | None = None,
        frame_id: str | None = None,
    ) -> Any:
        action_type = str(command.get("type") or "").strip().lower()
        if action_type not in self._TARGET_ACTIONS | {"navigate", "reload", "go_back", "go_forward"}:
            raise ValueError(f"unsupported browser action: {action_type!r}")
        action_id = str(command.get("action_id") or new_id())
        locator = str(command.get("locator") or command.get("selector") or "") or None
        label = self.redaction.redact_text(str(command.get("label") or ""))
        details = self._safe_command_details(command)
        self._record_action(
            action_id=action_id,
            action_type=action_type,
            label=label,
            locator=locator,
            page_id=page_id,
            frame_id=frame_id,
            details=details,
            evidence_origin="server_dispatched",
            causal_eligible=True,
        )
        try:
            with self.action_tracker.scope(action_id), ActionScope(action_id):
                if action_type == "navigate":
                    url = self.url_policy.validate(str(command.get("url") or ""))
                    return await _await(page.goto(url, **dict(command.get("options") or {})))
                if action_type in {"reload", "go_back", "go_forward"}:
                    return await _await(getattr(page, action_type)(**dict(command.get("options") or {})))
                if locator is None:
                    raise ValueError(f"{action_type} requires a locator")
                target = page.locator(locator)
                method = getattr(target, action_type)
                args, kwargs = self._operation_arguments(action_type, command)
                return await _await(method(*args, **kwargs))
        except Exception as exc:
            self.diagnostics.emit(
                "action_failed",
                page_id=page_id,
                action_id=action_id,
                action_type=action_type,
                locator=locator,
                error_type=type(exc).__name__,
                message=str(exc),
            )
            raise

    def record_observed(
        self,
        *,
        action_type: str,
        page_id: str | None,
        frame_id: str | None,
        locator: str | None = None,
        label: str = "",
        details: dict[str, Any] | None = None,
        action_id: str | None = None,
    ) -> ActionFact:
        """Record a weak page observation without opening a causal window.

        ``__danoRecordAction`` is intentionally treated as page-controlled: an
        application can invoke any binding visible in its JavaScript realm.
        The observation remains useful for UI reconstruction, but it cannot
        attach a following request or mutation to a strong user-action edge.
        """

        action_id = action_id or new_id()
        raw_details = dict(details or {})
        sensitive = bool(raw_details.get("sensitive")) or str(
            raw_details.get("inputType") or raw_details.get("input_type") or ""
        ).lower() == "password"
        clean_details = self._secure_action_details(raw_details)
        if sensitive and "value" in clean_details:
            clean_details["value"] = self.redaction.replacement
        fact = self._record_action(
            action_id=action_id,
            action_type=action_type.strip().lower() or "unknown",
            label=self.redaction.redact_text(label),
            locator=self.redaction.redact_text(locator) if locator else None,
            page_id=page_id,
            frame_id=frame_id,
            details=clean_details,
            evidence_origin="page_observed",
            causal_eligible=False,
        )
        return fact

    def _record_action(
        self,
        *,
        action_id: str,
        action_type: str,
        label: str,
        locator: str | None,
        page_id: str | None,
        frame_id: str | None,
        details: dict[str, Any],
        evidence_origin: str,
        causal_eligible: bool,
    ) -> ActionFact:
        return self.ledger.emit(
            ActionFact,
            action_id=action_id,
            page_id=page_id,
            action_type=action_type,
            label=label,
            locator=locator,
            payload={
                "frame_id": frame_id,
                "details": details,
                "evidence_origin": evidence_origin,
                "causal_eligible": causal_eligible,
            },
            redacted=True,
        )

    def _safe_command_details(self, command: dict[str, Any]) -> dict[str, Any]:
        excluded = {"action_id", "type", "label", "locator", "selector"}
        details = {key: value for key, value in command.items() if key not in excluded}
        locator = str(command.get("locator") or command.get("selector") or "").lower()
        if command.get("sensitive") or "password" in locator or "passwd" in locator:
            for key in ("value", "text", "options"):
                if key in details:
                    details[key] = self.redaction.replacement
        return self._secure_action_details(details)

    @staticmethod
    def _operation_arguments(action_type: str, command: dict[str, Any]) -> tuple[list[Any], dict[str, Any]]:
        kwargs = dict(command.get("options") or {})
        if action_type in {"fill", "type"}:
            return [str(command.get("value") or "")], kwargs
        if action_type == "press":
            return [str(command.get("key") or "")], kwargs
        if action_type == "select_option":
            return [command.get("value")], kwargs
        return [], kwargs
