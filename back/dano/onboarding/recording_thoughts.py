"""Turn Pi agent_event rows into lightweight thought frames for the recorder UI."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

SendThought = Callable[[dict[str, Any]], Awaitable[None]]


def thought_from_agent_event(event: dict[str, Any]) -> dict[str, Any] | None:
    delta_type = str(event.get("delta_type") or "")
    delta = str(event.get("delta") or "")
    if delta_type == "thinking_delta" and delta:
        return {"kind": "thinking", "text": delta}
    if delta_type == "text_delta" and delta:
        return {"kind": "text", "text": delta}
    name = str(event.get("event") or "")
    tool = str(event.get("toolName") or "")
    if name == "tool_execution_start" and tool:
        args = str(event.get("tool_args") or "").strip()
        suffix = f" {args}" if args else ""
        return {"kind": "tool", "text": f"准备调用 {tool}{suffix}", "tool": tool}
    if name == "tool_execution_end" and tool:
        ok = event.get("success", True)
        detail = str(event.get("tool_result") or "").strip()
        result = "成功" if ok else "失败"
        extra = f"：{detail}" if detail else ""
        return {"kind": "tool", "text": f"{tool} {result}{extra}", "tool": tool}
    return None


class ThoughtBridge:
    """Coalesce token deltas so the UI streams without a snapshot storm."""

    def __init__(self, send: SendThought, *, flush_s: float = 0.04) -> None:
        self._send = send
        self._flush_s = flush_s
        self._kind = ""
        self._buf = ""
        self._task: asyncio.Task[None] | None = None

    async def push(self, event: dict[str, Any]) -> None:
        payload = thought_from_agent_event(event)
        if payload is None:
            return
        if payload["kind"] in {"text", "thinking"}:
            if self._kind and self._kind != payload["kind"]:
                await self.flush()
            self._kind = str(payload["kind"])
            self._buf += str(payload["text"])
            if self._task is None:
                self._task = asyncio.create_task(self._flush_later())
            return
        await self.flush()
        await self._emit(payload)

    async def flush(self) -> None:
        if not self._buf:
            return
        payload = {"kind": self._kind or "text", "text": self._buf}
        self._kind = ""
        self._buf = ""
        await self._emit(payload)

    async def _flush_later(self) -> None:
        await asyncio.sleep(self._flush_s)
        self._task = None
        await self.flush()

    async def _emit(self, payload: dict[str, Any]) -> None:
        await self._send({"type": "thought", **payload})
