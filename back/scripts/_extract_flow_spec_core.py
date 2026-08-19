"""Extract FlowSpec models, serialization and fingerprints into flow_spec_core."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "dano" / "execution" / "page" / "flow_spec.py"
CORE = ROOT / "dano" / "execution" / "page" / "flow_spec_core"

CLASS_RANGES = [
    (71, 106),
    (109, 136),
    (139, 143),
    (167, 170),
    (173, 196),
    (199, 219),
    (222, 250),
    (253, 266),
    (269, 278),
    (281, 292),
    (295, 309),
    (312, 336),
    (339, 350),
    (353, 374),
    (377, 393),
    (396, 433),
    (436, 446),
    (449, 470),
    (23172, 23178),
]


def slice_lines(lines: list[str], start: int, end: int) -> str:
    return "".join(lines[start - 1:end])


def replace_flowspec_validator(block: str) -> str:
    old = '''    @model_validator(mode="after")
    def _sync_derived_models(self) -> "FlowSpec":
        return sync_flow_spec_models(self)
'''
    new = '''    @model_validator(mode="after")
    def _sync_derived_models(self) -> "FlowSpec":
        hook = _SYNC_HOOK
        if hook is None:
            return self
        return hook(self)
'''
    if old not in block:
        raise SystemExit("FlowSpec validator block not found")
    return block.replace(old, new, 1)


def main() -> None:
    original = SRC.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)
    CORE.mkdir(parents=True, exist_ok=True)

    model_blocks = [slice_lines(lines, start, end).rstrip() + "\n\n\n" for start, end in CLASS_RANGES]
    models_src = replace_flowspec_validator("".join(model_blocks)).rstrip() + "\n"
    (CORE / "models.py").write_text(
        '''"""Shared FlowSpec pydantic models. No stage inference lives here."""
from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


_SYNC_HOOK: Callable[["FlowSpec"], "FlowSpec"] | None = None


def register_sync_flow_spec_models(hook: Callable[["FlowSpec"], "FlowSpec"]) -> None:
    """Install the materialized-spec sync used by FlowSpec.model_validate."""
    global _SYNC_HOOK
    _SYNC_HOOK = hook


'''
        + models_src,
        encoding="utf-8",
    )

    release_fn = slice_lines(lines, 17697, 17703)
    (CORE / "serialization.py").write_text(
        '''"""Pure FlowSpec JSON conversion helpers."""
from __future__ import annotations

from typing import Any

from dano.execution.page.flow_spec_core.models import FlowSpec


'''
        + release_fn,
        encoding="utf-8",
    )

    fingerprint_const = slice_lines(lines, 17706, 17711)
    fingerprint_fns = slice_lines(lines, 17714, 17777)
    fingerprint_fns = fingerprint_fns.replace(
        "canonical = FlowSpec.model_validate(flow_spec_release_payload(spec))",
        "from dano.execution.page.flow_spec_core.serialization import flow_spec_release_payload\n"
        "    canonical = FlowSpec.model_validate(flow_spec_release_payload(spec))",
        1,
    )
    (CORE / "fingerprints.py").write_text(
        '''"""Stable FlowSpec fingerprints. Results must match the pre-split hashes."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from dano.execution.page.flow_spec_core.models import FlowSpec


'''
        + fingerprint_const
        + "\n"
        + fingerprint_fns,
        encoding="utf-8",
    )

    (CORE / "__init__.py").write_text(
        '''"""Shared FlowSpec core: models, fingerprints, serialization."""
from dano.execution.page.flow_spec_core.models import (
    CapabilityDependency,
    CapabilityField,
    CapabilityRelation,
    CapabilityRequestRef,
    FlowCapability,
    FlowLink,
    FlowSpec,
    FlowSpecConflictError,
    FlowStep,
    IdentityBinding,
    ParamField,
    RecordedGoal,
    RequestAnalysis,
    RequestFact,
    RequestFacts,
    RequestUsage,
    ReviewItem,
    SelectBinding,
    SystemValue,
    register_sync_flow_spec_models,
)
from dano.execution.page.flow_spec_core.fingerprints import (
    flow_spec_fingerprint,
)
from dano.execution.page.flow_spec_core.serialization import flow_spec_release_payload

__all__ = [
    "CapabilityDependency",
    "CapabilityField",
    "CapabilityRelation",
    "CapabilityRequestRef",
    "FlowCapability",
    "FlowLink",
    "FlowSpec",
    "FlowSpecConflictError",
    "FlowStep",
    "IdentityBinding",
    "ParamField",
    "RecordedGoal",
    "RequestAnalysis",
    "RequestFact",
    "RequestFacts",
    "RequestUsage",
    "ReviewItem",
    "SelectBinding",
    "SystemValue",
    "flow_spec_fingerprint",
    "flow_spec_release_payload",
    "register_sync_flow_spec_models",
]
''',
        encoding="utf-8",
    )

    skip = set()
    for start, end in CLASS_RANGES:
        skip.update(range(start, end + 1))
    skip.update(range(17697, 17703 + 1))
    skip.update(range(17706, 17777 + 1))

    out: list[str] = []
    inserted_models = False
    i = 1
    while i <= len(lines):
        if i == 61 and not inserted_models:
            # After _REQUEST_OBSERVER_KEYS assignment block ends at line 67.
            pass
        if i in skip:
            i += 1
            continue
        out.append(lines[i - 1])
        if i == 67 and not inserted_models:
            out.append(
                "\nfrom dano.execution.page.flow_spec_core.models import (\n"
                "    CapabilityDependency,\n"
                "    CapabilityField,\n"
                "    CapabilityRelation,\n"
                "    CapabilityRequestRef,\n"
                "    FlowCapability,\n"
                "    FlowLink,\n"
                "    FlowSpec,\n"
                "    FlowSpecConflictError,\n"
                "    FlowStep,\n"
                "    IdentityBinding,\n"
                "    ParamField,\n"
                "    RecordedGoal,\n"
                "    RequestAnalysis,\n"
                "    RequestFact,\n"
                "    RequestFacts,\n"
                "    RequestUsage,\n"
                "    ReviewItem,\n"
                "    SelectBinding,\n"
                "    SystemValue,\n"
                "    register_sync_flow_spec_models,\n"
                ")\n"
                "from dano.execution.page.flow_spec_core.serialization import flow_spec_release_payload\n"
                "from dano.execution.page.flow_spec_core.fingerprints import (\n"
                "    _FINGERPRINT_NON_EXECUTABLE_KEYS,\n"
                "    _execution_fingerprint_payload,\n"
                "    _flow_fingerprint,\n"
                "    flow_spec_fingerprint,\n"
                ")\n"
            )
            inserted_models = True
        i += 1

    text = "".join(out)
    marker = "def sync_flow_spec_models(spec: FlowSpec) -> FlowSpec:\n"
    register = (
        marker
        + "    from dano.execution.page.flow_spec_core.models import register_sync_flow_spec_models as _register\n"
        + "    _register(sync_flow_spec_models)\n"
    )
    # Can't register inside the function. Insert after the function ends.
    # We'll append a module-level register call after the function definition
    # by replacing the unique function header with header + later register.
    if marker not in text:
        raise SystemExit("sync_flow_spec_models not found")
    text += (
        "\nregister_sync_flow_spec_models(sync_flow_spec_models)\n"
    )
    SRC.write_text(text, encoding="utf-8")
    print("extracted models/serialization/fingerprints")


if __name__ == "__main__":
    main()
