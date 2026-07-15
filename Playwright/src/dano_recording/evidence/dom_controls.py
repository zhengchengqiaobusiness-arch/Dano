"""DOM control and native-select evidence capture."""

from __future__ import annotations

import hashlib
import inspect
from dataclasses import dataclass
from typing import Any

from dano_recording.capture.ledger import FactLedger
from dano_recording.capture.redaction import RedactionPolicy
from dano_recording.domain.enums import ChoiceOption
from dano_recording.domain.facts import FactKind, RecordingFact
from dano_recording.evidence.option_safety import (
    IDENTITY_OPTION_RESOLVER,
    is_identity_option_collection,
)
from dano_recording.value_evidence import ValueEvidence, ValueEvidenceFactory


_DOM_PROBE = r"""
() => Array.from(document.querySelectorAll('input,select,textarea,button,[role],[aria-label]'))
  .slice(0, 2000)
  .map((el, index) => {
    let optionSource = '';
    let options = [];
    if (el instanceof HTMLSelectElement) {
      optionSource = 'native_select';
      options = Array.from(el.options).map(o => ({label: o.label || o.textContent || '', value: o.value, disabled: !!o.disabled}));
    } else {
      const ownedId = el.getAttribute('aria-controls') || el.getAttribute('aria-owns') || '';
      const owned = ownedId ? document.getElementById(ownedId) : null;
      if (owned) {
        optionSource = 'aria_controls';
        options = Array.from(owned.querySelectorAll('[role="option"]')).map((o, optionIndex) => ({
          label: o.getAttribute('aria-label') || o.textContent || '',
          value: o.getAttribute('data-value') || o.getAttribute('value') || o.id || String(optionIndex),
          disabled: o.getAttribute('aria-disabled') === 'true',
        }));
      }
    }
    const id = el.id || '';
    const name = el.getAttribute('name') || '';
    const testId = el.getAttribute('data-testid') || '';
    const selector = id ? `#${CSS.escape(id)}`
      : testId ? `[data-testid="${CSS.escape(testId)}"]`
      : name ? `${el.tagName.toLowerCase()}[name="${CSS.escape(name)}"]`
      : `${el.tagName.toLowerCase()}:nth-of-type(${index + 1})`;
    return {
      selector,
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute('type') || '',
      role: el.getAttribute('role') || '',
      name,
      label: el.getAttribute('aria-label') || el.getAttribute('placeholder') || '',
      required: !!el.required || el.getAttribute('aria-required') === 'true',
      readonly: !!el.readOnly || el.getAttribute('aria-readonly') === 'true',
      formId: el.form?.id || el.form?.getAttribute('name') || el.form?.getAttribute('action') || 'document',
      initialValue: el instanceof HTMLInputElement
        ? (el.type === 'checkbox' || el.type === 'radio' ? !!el.defaultChecked : el.defaultValue)
        : el instanceof HTMLTextAreaElement
          ? el.defaultValue
          : el instanceof HTMLSelectElement
            ? (el.multiple
                ? Array.from(el.options).filter(o => o.defaultSelected).map(o => o.value)
                : (Array.from(el.options).find(o => o.defaultSelected)?.value ?? ''))
            : undefined,
      multiple: !!el.multiple,
      disabled: !!el.disabled,
      optionSource,
      options,
    };
  })
"""


@dataclass(frozen=True, slots=True)
class DOMControl:
    control_id: str
    page_id: str
    frame_id: str | None
    selector: str
    tag: str
    input_type: str = ""
    role: str = ""
    name: str = ""
    label: str = ""
    required: bool = False
    multiple: bool = False
    disabled: bool = False
    readonly: bool = False
    form_id: str = "document"
    initial_value: Any | None = None
    initial_value_observed: bool = False
    initial_value_evidence: tuple[ValueEvidence, ...] = ()
    option_source: str = ""
    options: tuple[ChoiceOption, ...] = ()
    options_truncated: bool = False
    options_sensitive: bool = False
    option_count: int = 0
    option_runtime_resolver: str | None = None


class DOMControlCollector:
    def __init__(
        self,
        ledger: FactLedger | None = None,
        *,
        redaction: RedactionPolicy | None = None,
        max_controls: int = 2_000,
        max_options_per_control: int = 1_000,
        value_evidence_factory: ValueEvidenceFactory | None = None,
        recording_lineage: str | None = None,
    ) -> None:
        if max_controls < 1 or max_options_per_control < 1:
            raise ValueError("DOM evidence capacities must be positive")
        self.ledger = ledger
        self.redaction = redaction or RedactionPolicy()
        self.max_controls = max_controls
        self.max_options_per_control = max_options_per_control
        if value_evidence_factory is not None and not str(recording_lineage or "").strip():
            raise ValueError("recording_lineage is required with value_evidence_factory")
        self.value_evidence_factory = value_evidence_factory
        self.recording_lineage = str(recording_lineage or "")

    async def collect(
        self,
        frame: Any,
        *,
        page_id: str,
        frame_id: str | None = None,
    ) -> tuple[DOMControl, ...]:
        result = frame.evaluate(_DOM_PROBE)
        snapshot = await result if inspect.isawaitable(result) else result
        return self.from_snapshot(snapshot, page_id=page_id, frame_id=frame_id)

    def from_snapshot(
        self,
        snapshot: Any,
        *,
        page_id: str,
        frame_id: str | None = None,
    ) -> tuple[DOMControl, ...]:
        if not isinstance(snapshot, list):
            return ()
        if self.ledger is not None and len(snapshot) > self.max_controls:
            self.ledger.emit(
                RecordingFact,
                kind=FactKind.DIAGNOSTIC,
                page_id=page_id,
                payload={
                    "type": "dom_control_capacity",
                    "observed": len(snapshot),
                    "captured": self.max_controls,
                    "frame_id": frame_id,
                },
                redacted=True,
            )
        controls: list[DOMControl] = []
        for raw in snapshot[: self.max_controls]:
            if not isinstance(raw, dict):
                continue
            selector = self.redaction.redact_text(str(raw.get("selector") or ""))
            if not selector:
                continue
            digest = hashlib.sha256(
                f"{page_id}\0{frame_id or ''}\0{selector}".encode("utf-8")
            ).hexdigest()[:24]
            raw_options = raw.get("options") or []
            if not isinstance(raw_options, (list, tuple)):
                raw_options = []
            option_context = " ".join(
                str(raw.get(key) or "")
                for key in ("name", "label", "role", "selector")
            )
            options_sensitive = is_identity_option_collection(
                context=option_context,
                options=(item for item in raw_options if isinstance(item, dict)),
            )
            options = tuple(
                ChoiceOption(
                    label=self.redaction.redact_text(str(option.get("label") or "")),
                    value=self.redaction.redact_value(option.get("value")),
                    disabled=bool(option.get("disabled")),
                )
                for option in (
                    [] if options_sensitive else raw_options[: self.max_options_per_control]
                )
                if isinstance(option, dict)
            )
            # Sensitive/person option rows are deliberately omitted, so the
            # retained snapshot can never be described as a complete static
            # domain even when the page happened to expose every row.
            options_truncated = (
                options_sensitive or len(raw_options) > self.max_options_per_control
            )
            name = str(raw.get("name") or "")
            initial_observed = "initialValue" in raw or "initial_value" in raw
            raw_initial = raw.get("initialValue", raw.get("initial_value"))
            initial_evidence: tuple[ValueEvidence, ...] = ()
            if initial_observed and self.value_evidence_factory is not None:
                initial_value, initial_evidence = self.value_evidence_factory.capture_tree(
                    tenant_scope=self.ledger.tenant if self.ledger is not None else "capture",
                    recording_lineage=self.recording_lineage,
                    value=raw_initial,
                    root_path="control.initial_value",
                    field_name=name or "initial_value",
                )
            elif initial_observed:
                initial_value = self.redaction.redact_value(raw_initial, key=name)
            else:
                initial_value = None
            control = DOMControl(
                control_id=f"control_{digest}",
                page_id=page_id,
                frame_id=frame_id,
                selector=selector,
                tag=str(raw.get("tag") or "").lower(),
                input_type=str(raw.get("type") or "").lower(),
                role=str(raw.get("role") or ""),
                name=name,
                label=self.redaction.redact_text(str(raw.get("label") or "")),
                required=bool(raw.get("required")),
                multiple=bool(raw.get("multiple")),
                disabled=bool(raw.get("disabled")),
                readonly=bool(raw.get("readonly")),
                form_id=self.redaction.redact_text(
                    str(raw.get("formId") or raw.get("form_id") or "document")
                ),
                initial_value=initial_value,
                initial_value_observed=initial_observed,
                initial_value_evidence=initial_evidence,
                option_source=str(raw.get("optionSource") or raw.get("option_source") or ""),
                options=options,
                options_truncated=options_truncated,
                options_sensitive=options_sensitive,
                option_count=len(raw_options),
                option_runtime_resolver=(
                    IDENTITY_OPTION_RESOLVER if options_sensitive else None
                ),
            )
            controls.append(control)
            if self.ledger is not None:
                self.ledger.emit(
                    RecordingFact,
                    kind=FactKind.DOM_CONTROL,
                    page_id=page_id,
                    payload={
                        "control_id": control.control_id,
                        "frame_id": frame_id,
                        "selector": selector,
                        "tag": control.tag,
                        "input_type": control.input_type,
                        "role": control.role,
                        "name": control.name,
                        "label": control.label,
                        "required": control.required,
                        "multiple": control.multiple,
                        "disabled": control.disabled,
                        "readonly": control.readonly,
                        "form_id": control.form_id,
                        "initial_value": control.initial_value,
                        "initial_value_observed": control.initial_value_observed,
                        "initial_value_evidence": [
                            item.model_dump(mode="json", exclude_none=True)
                            for item in control.initial_value_evidence
                        ],
                        "option_source": control.option_source,
                        "options": [option.model_dump(mode="json") for option in options],
                        "options_truncated": options_truncated,
                        "options_sensitive": options_sensitive,
                        "option_count": len(raw_options),
                        "option_runtime_resolver": control.option_runtime_resolver,
                    },
                    redacted=True,
                )
        return tuple(controls)
