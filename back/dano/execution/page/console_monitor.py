"""Step D5 · console 错误监控。

提供 ConsoleEntry 数据类、过滤/汇总函数、可移植的"是否相关错误"判断。
不依赖外部 logger,纯数据处理,易测。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ConsoleEntry:
    """一条 console 日志(浏览器/前端上报)。"""
    type: str                          # log / info / warning / error
    text: str
    url: str = ""
    ts: float | None = None
    line: int | None = None
    column: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "text": self.text, "url": self.url,
                "ts": self.ts, "line": self.line, "column": self.column}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ConsoleEntry":
        return cls(type=str(d.get("type", "log")), text=str(d.get("text", "")),
                   url=str(d.get("url", "")), ts=d.get("ts"),
                   line=d.get("line"), column=d.get("column"))


# 已知浏览器/前端噪声(命中 → 不上报)
_NOISE_PATTERNS = (
    "favicon.ico",
    "Download the React DevTools",
    "[HMR]", "[vite]",
    "webpack-dev-server",
    "[rc-collapse] `children` will be removed",
)


def is_relevant_error(type_: str, text: str) -> bool:
    """判断一条 console 日志是否值得后端关注。

    规则: type 必须是 error;命中噪声模式 → False。
    """
    if type_ != "error":
        return False
    if not text:
        return False
    for pat in _NOISE_PATTERNS:
        if pat in text:
            return False
    return True


def filter_errors(entries: list[ConsoleEntry]) -> list[ConsoleEntry]:
    return [e for e in entries if e.type == "error"]


def summarize_console_logs(entries: list[ConsoleEntry], *, max_sample_len: int = 200) -> dict[str, Any]:
    """汇总:总数/错误数/警告数/第一条错误的截断文本。"""
    total = len(entries)
    errors = [e for e in entries if e.type == "error"]
    warnings = [e for e in entries if e.type == "warning"]
    sample = (errors[0].text or "")[:max_sample_len] if errors else ""
    return {"total": total, "errors": len(errors), "warnings": len(warnings), "sample": sample}
