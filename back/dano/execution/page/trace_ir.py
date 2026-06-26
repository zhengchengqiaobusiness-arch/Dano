"""Trace IR: normalized factual timeline for page recording."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Any


TRACE_IR_VERSION = "trace-ir/v1"


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def trace_hash(trace: dict) -> str:
    return hashlib.sha256(_stable_json({k: v for k, v in trace.items() if k != "trace_hash"}).encode("utf-8")).hexdigest()


@dataclass
class TraceEvent:
    event_id: str
    type: str
    order: int
    evidence_ref: str
    payload: dict = field(default_factory=dict)
    caused_by: list[str] = field(default_factory=list)


@dataclass
class TraceIR:
    version: str = TRACE_IR_VERSION
    capture_id: str = ""
    capture_hash: str = ""
    events: list[TraceEvent] = field(default_factory=list)
    trace_hash: str = ""


def trace_to_dict(trace: TraceIR) -> dict:
    data = asdict(trace)
    data["trace_hash"] = trace_hash(data)
    return data


def event_ref(event_id: str) -> str:
    return f"trace://{event_id}"
