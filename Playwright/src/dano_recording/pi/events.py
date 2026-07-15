"""Pi event and usage contracts.

The recording service persists these events verbatim (after secret redaction) so the
browser can reconnect without losing the Pi timeline.  The models intentionally
mirror Pi's native event names instead of inventing a second cache/retry model.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


class PiUsage(BaseModel):
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    total_tokens: int = 0

    def add(self, raw: dict[str, Any] | None) -> None:
        raw = raw or {}
        self.input += int(raw.get("input") or 0)
        self.output += int(raw.get("output") or 0)
        self.cache_read += int(raw.get("cacheRead") or raw.get("cache_read") or 0)
        self.cache_write += int(raw.get("cacheWrite") or raw.get("cache_write") or 0)
        self.total_tokens += int(raw.get("totalTokens") or raw.get("total_tokens") or 0)


class PiEvent(BaseModel):
    recording_id: str
    session_id: str
    role: Literal["planner", "acceptance", "security", "compliance"]
    event_type: str
    turn: int = 0
    tool_name: str = ""
    tool_call_id: str = ""
    retry_attempt: int = 0
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PiSessionStatus(BaseModel):
    session_id: str
    role: str
    state: str = "idle"
    turn: int = 0
    tool_calls: int = 0
    retries: int = 0
    compactions: int = 0
    usage: PiUsage = Field(default_factory=PiUsage)
    last_error: str = ""
    session_path: str = ""
