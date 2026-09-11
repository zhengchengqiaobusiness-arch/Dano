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


def iter_row_lists(value):
    if isinstance(value, list):
        if value and all(isinstance(item, dict) for item in value):
            yield value
            return
        for item in value:
            yield from iter_row_lists(item)
        return
    if isinstance(value, dict):
        for item in value.values():
            yield from iter_row_lists(item)


def extract_rows(payload):
    lists = list(iter_row_lists(payload))
    if not lists:
        return []
    return max(lists, key=len)


def format_result(payload) -> str:
    return format_rows(extract_rows(payload))


def attach_tables(payload):
    if not isinstance(payload, dict):
        return payload
    for item in payload.get("results") or []:
        if not isinstance(item, dict):
            continue
        result = item.get("result")
        table = format_result(result)
        if not table or table == "无数据":
            continue
        item["table"] = table
        if isinstance(result, dict):
            result["table"] = table
    return payload


def main(argv=None) -> int:
    raw = sys.stdin.read() if argv is None else None
    payload = json.loads(raw or argv or "[]")
    if isinstance(payload, dict) and "results" in payload:
        print(json.dumps(attach_tables(payload), ensure_ascii=False, indent=2))
        return 0
    if isinstance(payload, dict) and payload.get("rows") is not None:
        print(format_rows(payload.get("rows"), payload.get("columns")))
        return 0
    print(format_result(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
