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
