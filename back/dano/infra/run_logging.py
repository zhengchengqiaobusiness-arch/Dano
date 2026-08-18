"""Deep per-run diagnostics. Callers only need bind / clear / emit."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import sys
import threading
import traceback
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

ALLOWED_STATUSES = frozenset({
    "started", "progress", "succeeded", "warning", "failed", "cancelled", "skipped",
})
IDENTITY_KEYS = (
    "run_id", "recording_id", "action", "tenant", "subsystem",
    "skill_id", "asset_id", "capability_id", "request_id", "flow_version",
)
ASSOCIATION_KEYS = (
    "call_id", "tool", "input_summary", "output_summary",
    "flow_version_before", "flow_version_after",
    "span_id", "parent_span_id",
    "batch_id", "batch_reason", "since_seq", "next_seq", "has_more", "iteration",
)
LEVEL_RANKS = {
    "debug": 10,
    "info": 20,
    "warning": 30,
    "error": 40,
    "exception": 50,
}
STAGE_LABELS = {
    "system": "系统",
    "analysis": "分析",
    "plan": "计划",
    "recording": "录制",
    "batch": "分析",
    "freeze": "录制",
    "workflow": "录制",
    "tool": "工具",
    "verification": "验证",
    "publish": "发布",
    "lifecycle": "发布",
    "export": "导出",
    "end": "结束",
    "complete": "结束",
    "pi": "分析",
}
SENSITIVE_KEY_RE = re.compile(
    r"(authorization|cookie|set-cookie|token|access_token|refresh_token|"
    r"password|secret|storage_state|session)",
    re.I,
)
SECRET_TEXT_RE = re.compile(
    r"(?i)\b(Bearer|Basic|Token)\s+[A-Za-z0-9._~+/=-]{6,}"
    r"|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
    r"|(?:Cookie|Set-Cookie)\s*[:=]\s*[^\s;]+"
    r"|(?:password|passwd|pwd)\s*[:=]\s*\S+",
)
REPO_ROOT = Path(__file__).resolve().parents[3]

_CONTEXT: ContextVar[dict[str, Any]] = ContextVar("dano_run_context", default={})
_NOTES: ContextVar[dict[str, Any]] = ContextVar("dano_run_notes", default={})
_SEQUENCES: dict[str, int] = {}
_SEQ_LOCK = threading.Lock()
_WRITE_FAILED = False
_WRITE_LOCK = threading.Lock()

_MULTILINE_EVENTS = frozenset({
    "recording.run.summary",
    "recording.run.completed",
    "recording.skill.loaded",
    "recording.skill.applied",
    "recording.batch.completed",
    "recording.batch.failed",
    "recording.publish.started",
    "recording.publish.asset_succeeded",
    "recording.export.completed",
    "recording.export.failed",
    "skill.package.export.failed",
    "gateway.ready",
})


def bind_run_context(**fields: Any) -> None:
    """Merge identity fields into the current run context."""
    current = dict(_CONTEXT.get() or {})
    for key, value in fields.items():
        if value is None or value == "":
            continue
        current[key] = value
    _CONTEXT.set(current)
    try:
        import structlog

        structlog.contextvars.bind_contextvars(
            **{key: current[key] for key in IDENTITY_KEYS if key in current}
        )
    except Exception:  # noqa: BLE001 - logging must not break callers
        pass


def clear_run_context() -> None:
    _CONTEXT.set({})
    _NOTES.set({})
    try:
        import structlog

        structlog.contextvars.clear_contextvars()
    except Exception:  # noqa: BLE001
        pass


def current_run_context() -> dict[str, Any]:
    return dict(_CONTEXT.get() or {})


def note_run_fact(**fields: Any) -> None:
    """Remember facts for the final run summary without changing business APIs."""
    notes = dict(_NOTES.get() or {})
    for key, value in fields.items():
        if value is not None:
            notes[key] = value
    _NOTES.set(notes)


def current_run_notes() -> dict[str, Any]:
    return dict(_NOTES.get() or {})


def emit_run_event(
    event: str,
    *,
    stage: str = "",
    status: str = "progress",
    summary: str = "",
    level: str = "info",
    duration_ms: int | float | None = None,
    details: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
    next_action: str = "",
    visibility: str = "console",
    **fields: Any,
) -> dict[str, Any]:
    record = _build_record(
        event=event,
        stage=stage,
        status=status,
        summary=summary,
        level=level,
        duration_ms=duration_ms,
        details=details,
        error=error,
        next_action=next_action,
        extra=fields,
    )
    _persist(record)
    if _console_visible(record, visibility):
        _print_console(record)
    return record


def emit_run_exception(
    event: str,
    exc: BaseException,
    *,
    stage: str = "",
    status: str = "failed",
    summary: str = "",
    code: str = "",
    cause: str = "",
    artifact_refs: list[str] | None = None,
    details: dict[str, Any] | None = None,
    next_action: str = "",
    **fields: Any,
) -> dict[str, Any]:
    return emit_run_event(
        event,
        stage=stage,
        status=status,
        summary=summary or str(exc),
        level="exception",
        details=details,
        error={
            "code": code or _reason_code(str(exc)),
            "type": type(exc).__name__,
            "message": _redact_text(str(exc)),
            "cause": _redact_text(cause),
            "traceback": _redact_text("".join(traceback.format_exception(exc))),
            "artifact_refs": list(artifact_refs or []),
        },
        next_action=next_action,
        **fields,
    )


def request_log_fields(
    *,
    method: str = "",
    path: str = "",
    status: int | None = None,
    body: Any = None,
    headers: dict[str, Any] | None = None,
) -> dict[str, Any]:
    del headers
    payload = _json_bytes(body)
    return {
        "method": method,
        "path": path,
        "status": status,
        "body_size": len(payload),
        "body_field_paths": _field_paths(body),
        "body_hash": hashlib.sha256(payload).hexdigest()[:16] if payload else "",
    }


def response_log_fields(
    *,
    status: int | None = None,
    content_type: str = "",
    body: Any = None,
) -> dict[str, Any]:
    payload = _json_bytes(body)
    top_level_keys: list[str] = []
    list_count = 0
    if isinstance(body, dict):
        top_level_keys = [str(key) for key in body]
        list_count = sum(1 for value in body.values() if isinstance(value, list))
    elif isinstance(body, list):
        list_count = len(body)
    return {
        "status": status,
        "content_type": content_type,
        "response_size": len(payload),
        "top_level_keys": top_level_keys,
        "list_count": list_count,
        "response_hash": hashlib.sha256(payload).hexdigest()[:16] if payload else "",
    }


def value_locator(
    *,
    wire_path: str = "",
    value: Any = None,
    source_kind: str = "",
    evidence_ref: str = "",
) -> dict[str, Any]:
    text = "" if value is None else str(value)
    return {
        "wire_path": wire_path,
        "value_type": type(value).__name__,
        "value_length": len(text),
        "value_hash": hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16],
        "source_kind": source_kind,
        "evidence_ref": evidence_ref,
    }


def persist_structlog_event(event_dict: dict[str, Any]) -> None:
    """Write leftover structlog events into the same JSONL layout."""
    context = current_run_context()
    record = {
        "timestamp": _timestamp(),
        "sequence": _next_sequence(str(event_dict.get("run_id") or context.get("run_id") or "")),
        "level": str(event_dict.get("level") or "info").lower(),
        "event": str(event_dict.get("event") or "log"),
        "stage": str(event_dict.get("stage") or ""),
        "status": _normalize_status(str(event_dict.get("status") or "progress")),
        "summary": str(event_dict.get("summary") or event_dict.get("event") or ""),
        "duration_ms": event_dict.get("duration_ms"),
        "source": event_dict.get("source") or _caller(),
        "details": _redact({
            key: value
            for key, value in event_dict.items()
            if key not in {
                "timestamp", "level", "event", "stage", "status", "summary",
                "duration_ms", "source", "error", "next_action", "exception",
            }
        }),
        "error": event_dict.get("error"),
        "next_action": str(event_dict.get("next_action") or ""),
    }
    for key in IDENTITY_KEYS:
        value = event_dict.get(key, context.get(key))
        if value not in (None, ""):
            record[key] = value
    _persist(record)


def reset_logging_state_for_tests() -> None:
    clear_run_context()
    with _SEQ_LOCK:
        _SEQUENCES.clear()
    global _WRITE_FAILED
    _WRITE_FAILED = False


def log_root() -> Path:
    override = os.environ.get("DANO_LOG_ROOT")
    if override:
        return Path(override)
    return REPO_ROOT / ".runtime" / "logs"


def _build_record(
    *,
    event: str,
    stage: str,
    status: str,
    summary: str,
    level: str,
    duration_ms: int | float | None,
    details: dict[str, Any] | None,
    error: dict[str, Any] | None,
    next_action: str,
    extra: dict[str, Any],
) -> dict[str, Any]:
    context = current_run_context()
    merged = {**dict(details or {}), **{
        key: value for key, value in extra.items()
        if key not in IDENTITY_KEYS and key not in ASSOCIATION_KEYS
    }}
    record: dict[str, Any] = {
        "timestamp": _timestamp(),
        "sequence": _next_sequence(str(extra.get("run_id") or context.get("run_id") or "")),
        "level": str(level or "info").lower(),
        "event": event,
        "stage": stage,
        "status": _normalize_status(status),
        "summary": _redact_text(summary or event),
        "duration_ms": None if duration_ms is None else int(duration_ms),
        "source": _caller(),
        "details": _redact(merged),
        "error": _redact(error) if error else None,
        "next_action": next_action,
    }
    for key in IDENTITY_KEYS:
        value = extra.get(key, context.get(key))
        if value not in (None, ""):
            record[key] = value
    for key in ASSOCIATION_KEYS:
        value = extra.get(key)
        if value is not None:
            record[key] = _redact(value)
    return record


def _persist(record: dict[str, Any]) -> None:
    global _WRITE_FAILED
    try:
        path = _jsonl_path(str(record.get("run_id") or ""))
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, default=str)
        with _WRITE_LOCK:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except Exception as exc:  # noqa: BLE001 - disk failure must not reach business
        if not _WRITE_FAILED:
            _WRITE_FAILED = True
            sys.stderr.write(f"dano run logging persist failed: {exc}\n")


def _jsonl_path(run_id: str) -> Path:
    day = datetime.now().strftime("%Y-%m-%d")
    root = log_root()
    if run_id:
        return root / "runs" / day / f"{run_id}.jsonl"
    return root / "system" / f"{day}.jsonl"


def _next_sequence(run_id: str) -> int:
    key = run_id or "system"
    with _SEQ_LOCK:
        current = _SEQUENCES.get(key, 0) + 1
        _SEQUENCES[key] = current
        return current


def _normalize_status(status: str) -> str:
    value = str(status or "").strip().lower()
    return value if value in ALLOWED_STATUSES else "progress"


def _timestamp() -> str:
    now = datetime.now().astimezone()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc).astimezone()
    return now.isoformat(timespec="milliseconds")


def _caller() -> dict[str, Any]:
    frame = inspect.currentframe()
    try:
        while frame is not None:
            filename = frame.f_code.co_filename
            if "run_logging.py" not in filename.replace("\\", "/"):
                module = inspect.getmodule(frame)
                return {
                    "module": getattr(module, "__name__", Path(filename).stem),
                    "function": frame.f_code.co_name,
                    "line": frame.f_lineno,
                }
            frame = frame.f_back
    finally:
        del frame
    return {"module": "dano.infra.run_logging", "function": "emit_run_event", "line": 0}


def _console_visible(record: dict[str, Any], visibility: str) -> bool:
    if visibility == "detail" and LEVEL_RANKS.get(record["level"], 20) < LEVEL_RANKS["warning"]:
        return False
    threshold = os.environ.get("DANO_LOG_LEVEL", "INFO").lower()
    return LEVEL_RANKS.get(record["level"], 20) >= LEVEL_RANKS.get(threshold, 20)


def _print_console(record: dict[str, Any]) -> None:
    stamp = _console_time(record.get("timestamp"))
    level = str(record.get("level") or "info").upper()
    if level == "EXCEPTION":
        level = "ERROR"
    stage = _stage_label(record)
    summary = str(record.get("summary") or record.get("event") or "")
    headline = f"{stamp} {level:<5} [{stage}] {summary}"
    extras = _headline_extras(record)
    if extras:
        headline = f"{headline} | {extras}"
    lines = [headline]
    if _use_multiline(record):
        lines.extend(f"  {key}: {value}" for key, value in _console_fields(record))
    sys.stdout.write("\n".join(lines) + "\n")


def _console_time(timestamp: Any) -> str:
    text = str(timestamp or "")
    if "T" in text:
        return text.split("T", 1)[1][:8]
    return datetime.now().strftime("%H:%M:%S")


def _stage_label(record: dict[str, Any]) -> str:
    stage = str(record.get("stage") or "")
    if stage in STAGE_LABELS:
        return STAGE_LABELS[stage]
    event = str(record.get("event") or "")
    for prefix, label in (
        ("gateway", "系统"),
        ("recording.skill", "分析"),
        ("recording.analysis", "分析"),
        ("recording.batch", "分析"),
        ("recording.pi", "分析"),
        ("agent_tool", "计划" if "plan" in event else "工具"),
        ("recording.publish", "发布"),
        ("recording.lifecycle", "发布"),
        ("recording.export", "导出"),
        ("skill.package", "导出"),
        ("recording.run", "结束"),
        ("recording.verification", "验证"),
        ("recording.freeze", "录制"),
        ("recording.workflow", "录制"),
    ):
        if event.startswith(prefix):
            return label
    return stage or "系统"


def _headline_extras(record: dict[str, Any]) -> str:
    details = record.get("details") if isinstance(record.get("details"), dict) else {}
    pairs: list[str] = []
    mapping = (
        ("run", record.get("run_id")),
        ("recording", record.get("recording_id")),
        ("action", record.get("action")),
        ("skill", record.get("skill_id") or details.get("skill_id") or details.get("skill_name")),
        ("asset", record.get("asset_id") or details.get("asset_id")),
        ("batch", record.get("batch_id") or details.get("batch_id")),
        ("call", record.get("call_id") or details.get("call_id")),
        ("code", (record.get("error") or {}).get("code") if isinstance(record.get("error"), dict) else details.get("code")),
    )
    for key, value in mapping:
        if value not in (None, ""):
            pairs.append(f"{key}={_short(value)}")
    return " ".join(pairs[:6])


def _use_multiline(record: dict[str, Any]) -> bool:
    if record.get("level") in {"error", "exception"}:
        return True
    event = str(record.get("event") or "")
    return event in _MULTILINE_EVENTS or event.endswith(".summary")


def _console_fields(record: dict[str, Any]) -> list[tuple[str, Any]]:
    details = record.get("details") if isinstance(record.get("details"), dict) else {}
    error = record.get("error") if isinstance(record.get("error"), dict) else {}
    preferred = [
        ("run_id", record.get("run_id")),
        ("recording_id", record.get("recording_id")),
        ("skill", details.get("skill_name") or record.get("skill_id")),
        ("skill_id", record.get("skill_id") or details.get("skill_id")),
        ("phase", details.get("analysis_phase") or details.get("phase")),
        ("sha256", details.get("skill_sha256") or details.get("sha256")),
        ("call_id", record.get("call_id") or details.get("call_id")),
        ("flow_version_before", record.get("flow_version_before") or details.get("flow_version_before")),
        ("submitted_capabilities", details.get("submitted_capability_count")),
        ("operations", details.get("submitted_operation_count")),
        ("flow_version", _version_arrow(record, details)),
        ("capabilities", details.get("capability_count")),
        ("capability_names", details.get("capability_names")),
        ("unresolved", details.get("unresolved_count") if "unresolved_count" in details else details.get("submitted_unresolved_count")),
        ("rejected_operations", details.get("rejected_count")),
        ("batch_id", record.get("batch_id") or details.get("batch_id")),
        ("requests_before", details.get("request_count_before")),
        ("requests_after", details.get("request_count_after")),
        ("reason", record.get("batch_reason") or details.get("batch_reason") or details.get("reason")),
        ("asset_id", record.get("asset_id") or details.get("asset_id")),
        ("asset_version", details.get("asset_version")),
        ("lifecycle", details.get("lifecycle") or details.get("lifecycle_state")),
        ("code", error.get("code")),
        ("canonical_contract_present", details.get("canonical_contract_present")),
        ("package_written", details.get("package_written")),
        ("output_directory", details.get("output_directory") or details.get("export_directory")),
        ("cause", error.get("cause")),
        ("next_action", record.get("next_action")),
        ("asset_status", details.get("asset_status")),
        ("skill_package_status", details.get("skill_package_status")),
        ("exported_count", details.get("exported_count")),
        ("failed_stage", details.get("failed_stage")),
        ("root_cause", details.get("root_cause")),
        ("total_duration", details.get("total_duration")),
        ("full_log", details.get("full_log")),
        ("pid", details.get("pid")),
        ("db", details.get("db")),
        ("address", details.get("address")),
        ("duration", _duration_text(record.get("duration_ms"))),
        ("traceback", "已写入当前 run JSONL" if error.get("traceback") else None),
    ]
    return [(key, value) for key, value in preferred if value not in (None, "", [])]


def _version_arrow(record: dict[str, Any], details: dict[str, Any]) -> Any:
    before = record.get("flow_version_before", details.get("flow_version_before"))
    after = record.get("flow_version_after", details.get("flow_version_after"))
    if before not in (None, "") and after not in (None, ""):
        return f"{before} → {after}"
    return after if after not in (None, "") else details.get("flow_version")


def _duration_text(duration_ms: Any) -> str | None:
    if duration_ms in (None, ""):
        return None
    try:
        value = int(duration_ms)
    except (TypeError, ValueError):
        return None
    if value >= 60_000:
        minutes, rest = divmod(value, 60_000)
        seconds = rest / 1000
        return f"{minutes}m{seconds:.0f}s" if seconds else f"{minutes}m"
    if value >= 1000:
        return f"{value / 1000:.1f}s"
    return f"{value}ms"


def _short(value: Any) -> str:
    text = str(value)
    return text if len(text) <= 36 else text[:24] + "..."


def _redact(value: Any, key: str = "") -> Any:
    if SENSITIVE_KEY_RE.search(str(key or "")):
        return "<redacted>"
    if isinstance(value, dict):
        return {str(item_key): _redact(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_redact(item, key) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _redact_text(value: str) -> str:
    return SECRET_TEXT_RE.sub("<redacted>", value)


def _json_bytes(value: Any) -> bytes:
    if value is None:
        return b""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    try:
        return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True).encode("utf-8")
    except Exception:  # noqa: BLE001
        return str(value).encode("utf-8", "replace")


def _field_paths(value: Any, prefix: str = "") -> list[str]:
    if isinstance(value, dict):
        paths: list[str] = []
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            paths.append(path)
            paths.extend(_field_paths(item, path)[:20])
            if len(paths) >= 40:
                break
        return paths[:40]
    if isinstance(value, list) and value:
        return _field_paths(value[0], f"{prefix}[]" if prefix else "[]")[:40]
    return []


def _reason_code(message: str) -> str:
    text = message.casefold()
    if "canonical published capability contract" in text:
        return "CANONICAL_CAPABILITY_CONTRACT_MISSING"
    if "invalid published flowspec" in text:
        return "INVALID_PUBLISHED_FLOWSPEC"
    if "skill package validation failed" in text:
        return "SKILL_PACKAGE_VALIDATION_FAILED"
    if "not written" in text:
        return "REQUESTED_SKILL_PACKAGE_NOT_WRITTEN"
    return "UNEXPECTED_ERROR"


def new_span_id(prefix: str = "span") -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


def new_call_id() -> str:
    return f"call-{uuid4().hex[:8]}"
