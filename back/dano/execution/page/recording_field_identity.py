"""Canonical identities for fields captured during page recording.

FlowSpec keeps its historical stored paths (body fields are commonly stored
without a ``body.`` prefix).  Model-facing tools use transport-qualified wire
paths.  This module is the single adapter between those two representations.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_WIRE_PREFIXES = ("body.", "query.", "headers.", "url_path[")
_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class FieldReferenceError(ValueError):
    """A field reference is missing, ambiguous, or contradicts the request."""


class FieldReferenceDeferred(FieldReferenceError):
    """The request exists in facts but has not been materialized as a step."""


@dataclass(frozen=True)
class FieldRef:
    request_id: str = ""
    step_id: str = ""
    wire_path: str = ""


@dataclass(frozen=True)
class ResolvedField:
    step: Any
    param: Any
    request_id: str
    step_id: str
    stored_path: str
    wire_path: str


def canonical_wire_path(step: Any, stored_path: str) -> str:
    """Return the transport-qualified identity for one stored ParamField path."""
    path = str(stored_path or "").removeprefix("request.")
    if not path:
        return ""
    if path.startswith(_WIRE_PREFIXES):
        return path
    method = str(getattr(step, "method", "") or "").upper()
    return f"query.{path}" if method in _READ_METHODS else f"body.{path}"


def stored_container_path(step: Any, wire_path: str) -> str:
    """Translate a qualified collection path to the historical stored form.

    Dynamic body keys are stored without the transport prefix.  Keeping this
    translation beside field resolution prevents dependency code from growing
    a second, subtly different path convention.
    """
    path = str(wire_path or "").removeprefix("request.")
    if not path:
        raise FieldReferenceError("structure field reference requires wire_path")
    method = str(getattr(step, "method", "") or "").upper()
    if path.startswith("body."):
        if method in _READ_METHODS:
            raise FieldReferenceError(
                f"body wire path contradicts {method or 'read'} request: {path}"
            )
        relative = path.removeprefix("body.")
        recorded = [str(getattr(param, "path", "") or "") for param in (getattr(step, "params", None) or [])]
        return path if any(item.startswith(path.rstrip(".*[]")) for item in recorded) else relative
    if path.startswith("query."):
        if method not in _READ_METHODS:
            raise FieldReferenceError(
                f"query wire path contradicts {method or 'write'} request: {path}"
            )
        relative = path.removeprefix("query.")
        recorded = [str(getattr(param, "path", "") or "") for param in (getattr(step, "params", None) or [])]
        return path if any(item.startswith(path.rstrip(".*[]")) for item in recorded) else relative
    if path.startswith(("headers.", "url_path[")):
        return path
    return path


def _step_request_id(step: Any) -> str:
    return str((getattr(step, "source_meta", None) or {}).get("request_id") or "")


def _known_request_id(spec: Any, request_id: str) -> bool:
    facts = getattr(spec, "request_facts", None)
    return bool(
        request_id
        and (
            any(str(getattr(fact, "request_id", "") or "") == request_id for fact in (getattr(facts, "requests", None) or []))
            or request_id in (getattr(facts, "usage", None) or {})
            or request_id in set((getattr(spec, "meta", None) or {}).get("live_request_ids") or [])
        )
    )


def resolve_field_ref(spec: Any, ref: FieldRef) -> ResolvedField:
    """Resolve a model-facing FieldRef to exactly one FlowStep/ParamField.

    Resolution never performs cross-step leaf-name matching.  Legacy
    unqualified paths remain accepted only after the request/step has already
    selected one concrete step.
    """
    step_id = str(ref.step_id or "")
    request_id = str(ref.request_id or "")
    requested_path = str(ref.wire_path or "").removeprefix("request.")
    if not (step_id or request_id):
        raise FieldReferenceError("field reference requires step_id or request_id")
    if not requested_path:
        raise FieldReferenceError("field reference requires wire_path")

    steps = list(getattr(spec, "steps", None) or [])
    step = next((item for item in steps if step_id and str(getattr(item, "step_id", "")) == step_id), None)
    request_step = next((item for item in steps if request_id and _step_request_id(item) == request_id), None)
    if step is not None and request_step is not None and step is not request_step:
        raise FieldReferenceError(
            f"field reference step_id/request_id mismatch: {step_id}:{request_id}"
        )
    step = step or request_step
    if step is None:
        unresolved_request = request_id or step_id
        if _known_request_id(spec, unresolved_request):
            raise FieldReferenceDeferred(
                f"request {unresolved_request} is captured but its canonical step is not materialized yet"
            )
        raise FieldReferenceError(
            f"field target not found: {step_id or request_id}:{requested_path}"
        )

    qualified = requested_path.startswith(_WIRE_PREFIXES)
    candidates = [
        param
        for param in (getattr(step, "params", None) or [])
        if canonical_wire_path(step, str(getattr(param, "path", "") or "")) == requested_path
        or (
            not qualified
            and str(getattr(param, "path", "") or "") == requested_path
        )
    ]
    if not candidates:
        raise FieldReferenceError(
            f"field target not found: {getattr(step, 'step_id', '')}:{requested_path}"
        )
    if len(candidates) != 1:
        stored = sorted(str(getattr(param, "path", "") or "") for param in candidates)
        raise FieldReferenceError(
            f"field target is ambiguous: {getattr(step, 'step_id', '')}:{requested_path} -> {stored}"
        )
    param = candidates[0]
    canonical = canonical_wire_path(step, str(getattr(param, "path", "") or ""))
    return ResolvedField(
        step=step,
        param=param,
        request_id=_step_request_id(step),
        step_id=str(getattr(step, "step_id", "") or ""),
        stored_path=str(getattr(param, "path", "") or ""),
        wire_path=canonical,
    )
