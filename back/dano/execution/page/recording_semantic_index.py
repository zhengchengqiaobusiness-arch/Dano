"""Bounded semantic field index for long page recordings.

Raw DOM snapshots may be truncated. Extracted field identity, occurrence,
enum and form-sample facts stay in this index for freeze and live analysis.
"""
from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlparse


def route_identity(item: dict) -> str:
    for key in ("trigger_page_context", "page_context"):
        context = item.get(key)
        if not isinstance(context, dict):
            continue
        path = str(context.get("path") or "").strip()
        if path:
            return path.rstrip("/") or "/"
        url = str(context.get("url") or "").strip()
        if url:
            return urlparse(url).path.rstrip("/") or "/"
    return ""


def field_surface(item: dict) -> str:
    if item.get("in_dialog") is True:
        return "dialog"
    surface = str(item.get("surface") or "").strip().lower()
    if surface in {"dialog", "modal"}:
        return "dialog"
    if surface == "drawer":
        return "drawer"
    control_surface = str(item.get("control_surface") or "").strip().lower()
    if control_surface == "table_inline":
        return "table_inline"
    return "page"


def form_root_identity(item: dict) -> str:
    for key in ("form_root", "dialog_root", "form_id", "dialog_id"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def normalized_wire_path(item: dict) -> str:
    raw = str(item.get("wire_path") or item.get("path") or "").strip()
    return re.sub(r"\[\d+\]", "[]", raw)


def field_identity_id(item: dict) -> str:
    payload = {
        "page_id": str(item.get("page_id") or ""),
        "frame_id": str(item.get("frame_id") or ""),
        "route": route_identity(item),
        "surface": field_surface(item),
        "form_root": form_root_identity(item),
        "table_id": str(item.get("table_id") or ""),
        "column_index": item.get("column_index"),
        "locator": str(item.get("locator") or ""),
        "control_kind": str(item.get("control_kind") or ""),
        "aliases": sorted({
            str(alias) for alias in (item.get("field_aliases") or []) if str(alias or "")
        }),
        "label": str(item.get("label") or item.get("field") or ""),
        "wire_path": normalized_wire_path(item),
    }
    digest = hashlib.sha1(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return f"field-id-{digest}"


def occurrence_id(item: dict) -> str:
    payload = {
        "identity": str(item.get("field_identity_id") or field_identity_id(item)),
        "event_id": str(item.get("event_id") or ""),
        "action_id": str(item.get("action_id") or ""),
        "transaction_id": str(item.get("transaction_id") or ""),
        "observed_at": item.get("observed_at"),
        "request_id": str(item.get("request_id") or ""),
        "row_index": item.get("row_index"),
        "row_identity": str(item.get("row_identity") or ""),
    }
    digest = hashlib.sha1(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]
    return f"field-occ-{digest}"


def stamp_field_identity(item: dict) -> dict:
    item["field_identity_id"] = field_identity_id(item)
    item["occurrence_id"] = occurrence_id(item)
    item["evidence_id"] = str(item.get("evidence_id") or item["occurrence_id"])
    return item


def has_recorded_value(item: dict) -> bool:
    value = item.get("value")
    return value is not None and value != ""


def samples_from_fields(fields: list) -> dict[str, object]:
    out: dict[str, object] = {}
    counters: dict[str, int] = {}
    for field in fields:
        if not isinstance(field, dict):
            continue
        label = str(field.get("label") or field.get("field") or "").strip()
        value = field.get("value")
        if not label or not has_recorded_value({"value": value}):
            continue
        counters[label] = counters.get(label, 0) + 1
        key = label if counters[label] == 1 else f"{label}#{counters[label]}"
        out[key] = value
    return out


def compact_event(event: dict) -> dict:
    return {
        "event_id": event.get("event_id"),
        "kind": event.get("kind"),
        "op": event.get("op"),
        "action_id": event.get("action_id"),
        "transaction_id": event.get("transaction_id"),
        "request_id": event.get("request_id"),
        "field_identity_id": event.get("field_identity_id"),
        "occurrence_id": event.get("occurrence_id") or event.get("evidence_id"),
        "observed_at": event.get("observed_at"),
    }


def control_item_from_step(step: dict) -> dict:
    return {
        "field": str(step.get("field") or ""),
        "label": str(step.get("field") or ""),
        "value": step.get("value"),
        "required": step.get("required"),
        "required_state": step.get("required_state"),
        "required_observed": step.get("required_observed"),
        "field_aliases": list(step.get("field_aliases") or []),
        "control_kind": str(step.get("control_kind") or "unknown"),
        "input_type": str(step.get("input_type") or ""),
        "locator": str(step.get("locator") or ""),
        "page_id": str(step.get("page_id") or ""),
        "frame_id": str(step.get("frame_id") or ""),
        "page_context": dict(step.get("page_context") or {}),
        "op": str(step.get("op") or ""),
        "event_id": str(step.get("event_id") or ""),
        "action_id": str(step.get("action_id") or ""),
        "transaction_id": str(step.get("transaction_id") or ""),
        "observed_at": step.get("observed_at"),
        "in_dialog": bool(step.get("in_dialog")),
        "surface": str(step.get("surface") or ("dialog" if step.get("in_dialog") else "page")),
        "form_root": form_root_identity(step),
        "control_surface": str(step.get("control_surface") or ""),
        "table_id": str(step.get("table_id") or ""),
        "row_index": step.get("row_index"),
        "row_identity": str(step.get("row_identity") or ""),
        "column_index": step.get("column_index"),
        "checked": step.get("checked"),
        "options": step.get("options"),
        "filename": step.get("filename"),
    }


class SemanticFieldIndex:
    def __init__(self) -> None:
        self.occurrences: dict[str, dict] = {}
        self.enums: dict[str, dict] = {}
        self.events: dict[str, dict] = {}
        self.tx_samples: dict[str, dict] = {}
        self.req_samples: dict[str, dict] = {}

    def archive_event(self, event: dict) -> None:
        event_id = str(event.get("event_id") or "")
        if event_id:
            self.events[event_id] = compact_event(event)

    def archive_control(self, item: dict) -> dict:
        stamped = stamp_field_identity(dict(item))
        occ = str(stamped.get("occurrence_id") or "")
        if not occ:
            return stamped
        previous = self.occurrences.get(occ) or {}
        merged = {**previous, **stamped}
        merged["field_aliases"] = list(dict.fromkeys([
            *list(previous.get("field_aliases") or []),
            *list(stamped.get("field_aliases") or []),
        ]))
        if stamped.get("value") in (None, "") and previous.get("value") not in (None, ""):
            merged["value"] = previous["value"]
        self.occurrences[occ] = merged
        return stamped

    def archive_form_snapshot(self, snapshot: dict) -> None:
        fields = [
            item for item in (
                *(snapshot.get("fields") or []),
                *(snapshot.get("output_fields") or []),
            )
            if isinstance(item, dict)
        ]
        context = {
            "page_id": snapshot.get("page_id"),
            "frame_id": snapshot.get("frame_id"),
            "page_context": dict(snapshot.get("page_context") or {}),
            "action_id": snapshot.get("action_id"),
            "transaction_id": snapshot.get("transaction_id"),
            "event_id": snapshot.get("event_id"),
            "observed_at": snapshot.get("observed_at"),
            "op": "snapshot",
        }
        for field in fields:
            self.archive_control({**context, **field, "in_dialog": bool(field.get("in_dialog"))})
        samples = samples_from_fields(fields)
        tx = str(snapshot.get("transaction_id") or snapshot.get("action_id") or "")
        if tx and samples:
            self.tx_samples[tx] = dict(samples)
            action_id = str(snapshot.get("action_id") or "")
            if action_id:
                self.tx_samples[action_id] = dict(samples)
        request_id = str(snapshot.get("request_id") or "")
        if request_id and samples:
            self.req_samples[request_id] = dict(samples)

    def archive_enum_snapshot(self, snapshot: dict) -> None:
        stamped = stamp_field_identity(dict(snapshot))
        identity = str(stamped.get("field_identity_id") or "")
        if not identity:
            return
        previous = self.enums.get(identity) or {}
        merged_options: dict[str, object] = {}
        for option in [*(previous.get("options") or []), *(stamped.get("options") or [])]:
            label = str(option.get("label") if isinstance(option, dict) else option).strip()
            if label:
                merged_options[label] = option
        self.enums[identity] = {
            **previous,
            **stamped,
            "options": list(merged_options.values()),
        }

    def field_evidence(self) -> list[dict]:
        return [
            dict(item)
            for item in self.occurrences.values()
            if isinstance(item, dict) and str(item.get("occurrence_id") or "").startswith("field-occ-")
        ]
