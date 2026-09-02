#!/usr/bin/env python3
"""把列表型业务结果整理成便于选择的 Markdown 表格。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from execute import get_by_path


def read_value(source: str) -> Any:
    if source == "-":
        return json.load(sys.stdin)
    if source.startswith("@"):
        return json.loads(Path(source[1:]).read_text(encoding="utf-8"))
    return json.loads(source)


def cell(value: Any) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    return str(value if value is not None else "").replace("|", "\\|").replace("\r\n", "<br>").replace("\n", "<br>")


def main() -> int:
    parser = argparse.ArgumentParser(description="把 JSON 列表格式化为 Markdown 表格")
    parser.add_argument("--input", default="-", help="JSON 字符串、@文件，或 - 表示标准输入")
    parser.add_argument("--items-path", default="$", help="列表所在 JSON 路径")
    parser.add_argument("--columns", help='列名到 JSON 路径的对象，例如 {"编号":"$.id"}')
    parser.add_argument("--limit", type=int, default=20, help="最多展示多少项")
    args = parser.parse_args()
    try:
        root = read_value(args.input)
        items = get_by_path(root, args.items_path)
        if not isinstance(items, list):
            raise ValueError("指定路径不是列表")
        if not items:
            print("无数据")
            return 0
        columns = json.loads(args.columns) if args.columns else None
        if columns is None:
            first = items[0]
            if not isinstance(first, dict):
                columns = {"值": "$"}
            else:
                columns = {key: f"$.{key}" for key in list(first)[:8]}
        if not isinstance(columns, dict) or not columns:
            raise ValueError("--columns 必须是非空 JSON 对象")
        headers = list(columns)
        print("| " + " | ".join(cell(header) for header in headers) + " |")
        print("|" + "|".join("---" for _ in headers) + "|")
        for item in items[: max(0, args.limit)]:
            print("| " + " | ".join(cell(get_by_path(item, path)) for path in columns.values()) + " |")
        if len(items) > args.limit:
            print(f"仅展示前 {args.limit} 项，共 {len(items)} 项。")
        return 0
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"格式化失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
