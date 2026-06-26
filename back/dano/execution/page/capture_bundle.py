"""CaptureBundle: raw facts captured during page recording.

This layer intentionally does not infer business meaning. It keeps the facts
needed to rebuild Trace IR and re-run dataflow analysis without asking the user
to record the page again.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Any
from uuid import uuid4


CAPTURE_BUNDLE_VERSION = "capture-bundle/v1"


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def content_hash(obj: Any) -> str:
    return hashlib.sha256(_stable_json(obj).encode("utf-8")).hexdigest()


def _storage_summary(storage_state: dict | None) -> dict:
    """Redact credential values while preserving enough evidence topology."""
    if not storage_state:
        return {}
    origins = []
    for o in storage_state.get("origins") or []:
        origins.append({
            "origin": o.get("origin"),
            "local_storage_keys": sorted([it.get("name") for it in (o.get("localStorage") or []) if it.get("name")]),
        })
    cookies = sorted([c.get("name") for c in (storage_state.get("cookies") or []) if c.get("name")])
    return {"cookie_names": cookies, "origins": origins}


def _request_fact(r: dict, idx: int, kind: str) -> dict:
    body = r.get("post_data")
    resp = r.get("response_json") if "response_json" in r else r.get("json")
    fact = {
        "id": f"{kind}-{idx}",
        "method": (r.get("method") or ("GET" if kind == "read" else "POST")).upper(),
        "url": r.get("url") or "",
        "content_type": r.get("content_type") or "",
        "status": r.get("status"),
        "body_hash": content_hash(body) if body is not None else "",
        "response_hash": content_hash(resp) if resp is not None else "",
        "has_body": body is not None,
        "has_response": resp is not None,
        "count": r.get("count"),
    }
    return fact


@dataclass
class CaptureBundle:
    version: str = CAPTURE_BUNDLE_VERSION
    capture_id: str = field(default_factory=lambda: "capture-" + uuid4().hex[:10])
    start_url: str = ""
    ui_steps: list[dict] = field(default_factory=list)
    samples: dict = field(default_factory=dict)
    required_labels: list[str] = field(default_factory=list)
    writes: list[dict] = field(default_factory=list)
    reads: list[dict] = field(default_factory=list)
    storage: dict = field(default_factory=dict)
    evidence_hash: str = ""


def build_capture_bundle(*, start_url: str = "", steps: list[dict] | None = None,
                         writes: list[dict] | None = None, reads: list[dict] | None = None,
                         storage_state: dict | None = None, samples: dict | None = None,
                         required_labels: set | list | None = None,
                         capture_id: str | None = None) -> dict:
    bundle = CaptureBundle(
        capture_id=capture_id or "capture-" + uuid4().hex[:10],
        start_url=start_url or "",
        ui_steps=list(steps or []),
        samples=dict(samples or {}),
        required_labels=sorted(list(required_labels or [])),
        writes=[_request_fact(r, i, "write") for i, r in enumerate(writes or [])],
        reads=[_request_fact(r, i, "read") for i, r in enumerate(reads or [])],
        storage=_storage_summary(storage_state),
    )
    data = asdict(bundle)
    data["evidence_hash"] = content_hash({k: v for k, v in data.items() if k != "evidence_hash"})
    return data


def raw_writes(bundle: dict) -> list[dict]:
    return [w.get("raw") for w in (bundle or {}).get("writes") or [] if isinstance(w.get("raw"), dict)]


def raw_reads(bundle: dict) -> list[dict]:
    return [r.get("raw") for r in (bundle or {}).get("reads") or [] if isinstance(r.get("raw"), dict)]
