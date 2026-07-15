"""Normalisation for bounded React/Vue/Angular component clues."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any

from dano_recording.capture.redaction import RedactionPolicy
from dano_recording.domain.enums import ChoiceOption
from dano_recording.evidence.option_safety import (
    IDENTITY_OPTION_RESOLVER,
    is_identity_option_collection,
)


@dataclass(frozen=True, slots=True)
class RuntimeComponentClue:
    framework: str
    component_name: str
    property_path: str
    control_id: str | None = None
    selector: str | None = None
    options: tuple[ChoiceOption, ...] = ()
    multiple: bool | None = None
    proofs: tuple[str, ...] = ()
    options_sensitive: bool = False
    option_count: int = 0
    option_runtime_resolver: str | None = None


class RuntimeComponentCollector:
    def __init__(
        self,
        *,
        redaction: RedactionPolicy | None = None,
        max_clues: int = 1_000,
        max_options: int = 1_000,
    ) -> None:
        if max_clues < 1 or max_options < 1:
            raise ValueError("runtime evidence capacities must be positive")
        self.redaction = redaction or RedactionPolicy()
        self.max_clues = max_clues
        self.max_options = max_options

    async def collect(self, page: Any) -> tuple[RuntimeComponentClue, ...]:
        # This is a trusted bundled probe, not downloaded application code.
        result = page.evaluate("() => globalThis.__danoProbeComponents ? globalThis.__danoProbeComponents() : []")
        snapshot = await result if inspect.isawaitable(result) else result
        return self.from_snapshot(snapshot)

    def from_snapshot(self, snapshot: Any) -> tuple[RuntimeComponentClue, ...]:
        if not isinstance(snapshot, list):
            return ()
        clues: list[RuntimeComponentClue] = []
        for raw in snapshot[: self.max_clues]:
            if not isinstance(raw, dict):
                continue
            raw_options = raw.get("options") or []
            if not isinstance(raw_options, (list, tuple)):
                raw_options = []
            option_context = " ".join(
                str(raw.get(key) or "")
                for key in (
                    "component_name", "name", "property_path", "selector"
                )
            )
            options_sensitive = is_identity_option_collection(
                context=option_context,
                options=(item for item in raw_options if isinstance(item, dict)),
            )
            options = tuple(
                ChoiceOption(
                    label=self.redaction.redact_text(str(option.get("label") or option.get("name") or "")),
                    value=self.redaction.redact_value(
                        option.get("value", option.get("id", option.get("key")))
                    ),
                    disabled=bool(option.get("disabled")),
                )
                for option in ([] if options_sensitive else raw_options[: self.max_options])
                if isinstance(option, dict)
            )
            clues.append(
                RuntimeComponentClue(
                    framework=str(raw.get("framework") or "unknown"),
                    component_name=str(raw.get("component_name") or raw.get("name") or ""),
                    control_id=str(raw["control_id"]) if raw.get("control_id") else None,
                    selector=str(raw["selector"]) if raw.get("selector") else None,
                    property_path=str(raw.get("property_path") or ""),
                    options=options,
                    multiple=raw.get("multiple") if isinstance(raw.get("multiple"), bool) else None,
                    proofs=tuple(
                        self.redaction.redact_text(str(item))
                        for item in (raw.get("proofs") or ())
                    ),
                    options_sensitive=options_sensitive,
                    option_count=len(raw_options),
                    option_runtime_resolver=(
                        IDENTITY_OPTION_RESOLVER if options_sensitive else None
                    ),
                )
            )
        return tuple(clues)
