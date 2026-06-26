"""Normalize CaptureBundle facts into Trace IR events."""

from __future__ import annotations

from urllib.parse import urlparse

from dano.execution.page.trace_ir import TraceEvent, TraceIR, trace_to_dict


def _path(url: str | None) -> str:
    u = str(url or "")
    if not u:
        return ""
    try:
        p = urlparse(u)
        return (p.path or "/") + (("?" + p.query) if p.query else "")
    except Exception:  # noqa: BLE001
        return u


def _ui_event(step: dict, order: int) -> TraceEvent:
    eid = f"evt-ui-{order:04d}"
    return TraceEvent(
        event_id=eid,
        type="ui." + str(step.get("op") or "action"),
        order=order,
        evidence_ref=f"capture://ui_steps/{order}",
        payload={
            "op": step.get("op"),
            "field": step.get("field"),
            "locator": step.get("locator"),
            "has_value": step.get("value") not in (None, ""),
            "required": bool(step.get("required")),
        },
    )


def _network_event(kind: str, fact: dict, order: int, caused_by: list[str]) -> TraceEvent:
    eid = f"evt-{kind}-{order:04d}"
    return TraceEvent(
        event_id=eid,
        type=f"network.{kind}",
        order=order,
        evidence_ref=f"capture://{kind}s/{fact.get('id')}",
        caused_by=list(caused_by),
        payload={
            "method": fact.get("method"),
            "url": fact.get("url"),
            "path": _path(fact.get("url")),
            "content_type": fact.get("content_type"),
            "status": fact.get("status"),
            "has_body": bool(fact.get("has_body")),
            "has_response": bool(fact.get("has_response")),
            "body_hash": fact.get("body_hash"),
            "response_hash": fact.get("response_hash"),
            "count": fact.get("count"),
        },
    )


def normalize_capture_bundle(bundle: dict) -> dict:
    events: list[TraceEvent] = []
    for i, step in enumerate((bundle or {}).get("ui_steps") or []):
        events.append(_ui_event(step, len(events)))
    ui_tail = [events[-1].event_id] if events else []
    for fact in (bundle or {}).get("reads") or []:
        events.append(_network_event("read", fact, len(events), ui_tail))
    for fact in (bundle or {}).get("writes") or []:
        events.append(_network_event("write", fact, len(events), ui_tail))
    return trace_to_dict(TraceIR(
        capture_id=(bundle or {}).get("capture_id", ""),
        capture_hash=(bundle or {}).get("evidence_hash", ""),
        events=events,
    ))


def event_for_request(trace_ir: dict | None, req: dict | None, kind: str = "write") -> str:
    """Return a trace evidence ref for a captured network event.

    Prefer URL + body hash so repeated POSTs to the same endpoint can still be
    tied back to the specific captured request chosen for this transaction.
    """
    if not trace_ir or not req:
        return ""
    from dano.execution.page.capture_bundle import content_hash
    url = req.get("url")
    body_hash = content_hash(req.get("post_data")) if req.get("post_data") is not None else ""
    for e in trace_ir.get("events") or []:
        payload = e.get("payload") or {}
        if e.get("type") != f"network.{kind}" or payload.get("url") != url:
            continue
        if body_hash and payload.get("body_hash") and payload.get("body_hash") != body_hash:
            continue
        return "trace://" + str(e.get("event_id"))
    return ""


def event_for_url(trace_ir: dict | None, url: str | None, kind: str = "write") -> str:
    if not trace_ir or not url:
        return ""
    return event_for_request(trace_ir, {"url": url}, kind)
