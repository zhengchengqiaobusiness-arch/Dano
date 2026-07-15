"""Native Pi usage aggregation."""

from __future__ import annotations

from typing import Any

from .events import PiUsage


def usage_from_events(events: list[dict[str, Any]]) -> PiUsage:
    usage = PiUsage()
    for event in events:
        if event.get("type") in {"turn_end", "message_end", "prompt_completed"}:
            usage.add(event.get("usage"))
    return usage
