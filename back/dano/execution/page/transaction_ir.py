"""Transaction-level IR for request-captured page skills.

The IR is the stable capture model.  It describes user-facing inputs,
option sources, bindings into the target request body, identity values and
constants before it is compiled back to the legacy ``api_request`` shape.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
from typing import Any


IR_VERSION = "transaction-ir/v1"


def stable_source_id(url: str | None, value_key: str | None = "", label_key: str | None = "") -> str:
    raw = "|".join([url or "", value_key or "", label_key or ""])
    return "src_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


@dataclass
class SourceSpec:
    id: str
    kind: str
    url: str
    value_key: str = ""
    label_key: str = ""
    count: int | None = None
    options: list[dict] = field(default_factory=list)
    option_filter: dict | None = None
    evidence: list[str] = field(default_factory=list)


@dataclass
class InputSpec:
    name: str
    path: str
    type: str = "string"
    required: bool = True
    sample: Any = None
    source_id: str | None = None
    submit_mode: str = "raw"
    confidence: float | None = None
    selected_default: bool = False
    evidence: list[str] = field(default_factory=list)


@dataclass
class BindingSpec:
    input: str
    target_path: str
    mode: str = "direct"
    source_id: str | None = None
    target_key: str | None = None
    item_template: dict | None = None
    expand_fields: list[str] = field(default_factory=list)


@dataclass
class ConstantSpec:
    path: str
    value: Any = None
    reason: str = "captured_constant"


@dataclass
class IdentitySpec:
    path: str
    source: str = ""
    evidence: list[str] = field(default_factory=list)


@dataclass
class StepSpec:
    idx: int
    method: str
    path: str
    role: str = "write"


@dataclass
class TransactionIR:
    version: str = IR_VERSION
    method: str = "POST"
    url: str = ""
    path: str = ""
    inputs: list[InputSpec] = field(default_factory=list)
    sources: list[SourceSpec] = field(default_factory=list)
    bindings: list[BindingSpec] = field(default_factory=list)
    constants: list[ConstantSpec] = field(default_factory=list)
    identity: list[IdentitySpec] = field(default_factory=list)
    derived: list[dict] = field(default_factory=list)
    steps: list[StepSpec] = field(default_factory=list)
    success: dict = field(default_factory=dict)
    capture: dict = field(default_factory=dict)


def _strip_empty(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            vv = _strip_empty(v)
            if vv in (None, "", [], {}):
                continue
            out[k] = vv
        return out
    if isinstance(value, list):
        return [_strip_empty(v) for v in value if _strip_empty(v) not in (None, "", [], {})]
    return value


def ir_to_dict(ir: TransactionIR) -> dict:
    return _strip_empty(asdict(ir))


def request_path(url: str | None) -> str:
    u = str(url or "")
    i = u.find("//")
    if i >= 0:
        j = u.find("/", i + 2)
        u = u[j:] if j >= 0 else "/"
    return u or "/"


def validate_transaction_ir(ir: dict | None) -> list[str]:
    """Validate the IR graph before trusting it as publish-time provenance."""
    if not isinstance(ir, dict):
        return ["ir must be an object"]
    issues: list[str] = []
    if ir.get("version") != IR_VERSION:
        issues.append("version must be transaction-ir/v1")
    input_names: set[str] = set()
    for i, inp in enumerate(ir.get("inputs") or []):
        name = str((inp or {}).get("name") or "")
        path = str((inp or {}).get("path") or "")
        if not name:
            issues.append(f"inputs[{i}].name is required")
        elif name in input_names:
            issues.append(f"inputs[{i}].name duplicates {name}")
        input_names.add(name)
        if not path:
            issues.append(f"inputs[{i}].path is required")
    source_ids: set[str] = set()
    for i, src in enumerate(ir.get("sources") or []):
        sid = str((src or {}).get("id") or "")
        if not sid:
            issues.append(f"sources[{i}].id is required")
        elif sid in source_ids:
            issues.append(f"sources[{i}].id duplicates {sid}")
        source_ids.add(sid)
        if not (src or {}).get("url"):
            issues.append(f"sources[{i}].url is required")
    for i, binding in enumerate(ir.get("bindings") or []):
        name = str((binding or {}).get("input") or "")
        if name and input_names and name not in input_names:
            issues.append(f"bindings[{i}].input references unknown input {name}")
        sid = (binding or {}).get("source_id")
        if sid and source_ids and sid not in source_ids:
            issues.append(f"bindings[{i}].source_id references unknown source {sid}")
        if not (binding or {}).get("target_path"):
            issues.append(f"bindings[{i}].target_path is required")
    for i, item in enumerate(ir.get("derived") or []):
        if item.get("kind") in {"array_count", "mirror"} and not item.get("target_path"):
            issues.append(f"derived[{i}].target_path is required")
    return issues
