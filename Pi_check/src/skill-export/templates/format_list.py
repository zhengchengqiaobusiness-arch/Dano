#!/usr/bin/env python3
"""Stable Markdown table formatter for list results."""

from __future__ import annotations

import json
import sys


def cell(value) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", "<br>")


def format_rows(rows, columns=None) -> str:
    if not rows:
        return "无数据"
    if not isinstance(rows, list):
        rows = [rows]
    if columns is None:
        keys = []
        for row in rows:
            if isinstance(row, dict):
                for key in row:
                    if key not in keys:
                        keys.append(key)
        columns = [{"id": key, "label": key} for key in keys]
    if not columns:
        return "无数据"
    header = "| " + " | ".join(cell(col.get("label") or col.get("id")) for col in columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    lines = [header, sep]
    for row in rows:
        data = row if isinstance(row, dict) else {"value": row}
        lines.append("| " + " | ".join(cell(data.get(col.get("id"))) for col in columns) + " |")
    return "\n".join(lines)


def main(argv=None) -> int:
    raw = sys.stdin.read() if argv is None else None
    payload = json.loads(raw or argv or "[]")
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    columns = payload.get("columns") if isinstance(payload, dict) else None
    print(format_rows(rows, columns))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
