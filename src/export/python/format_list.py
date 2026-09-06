#!/usr/bin/env python3
"""把列表型业务结果整理成便于选择的 Markdown 表格。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from execute import get_by_path


SKILL_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = SKILL_ROOT / "references" / "CONTRACT.json"
TECHNICAL_FIELDS = {
    "searchValue", "createBy", "createTime", "updateBy", "updateTime",
    "remark", "params", "delFlag", "tenantId", "ccedList", "sysAttachmentList",
}


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


def schema_array(schema: dict[str, Any], path: str = "$") -> tuple[str, dict[str, Any]] | None:
    if schema.get("type") == "array":
        return path, schema.get("items") or {}
    for name, child in (schema.get("properties") or {}).items():
        if isinstance(child, dict):
            found = schema_array(child, f"{path}.{name}")
            if found:
                return found
    return None


def contract_capability(capability_id: str) -> dict[str, Any]:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8-sig"))
    capability = next((item for item in contract.get("capabilities", []) if item.get("id") == capability_id), None)
    if capability is None:
        raise ValueError(f"未知能力：{capability_id}")
    return capability


def capability_columns(capability: dict[str, Any], item_schema: dict[str, Any]) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    properties = item_schema.get("properties") or {}
    fields = {field.get("name"): field for field in capability.get("inputForm", []) if field.get("name") in properties}
    selected = [name for name in fields if name not in {"page", "pageNo", "pageNum", "pageSize", "size", "limit"}]
    if not selected:
        selected = [name for name in properties if name not in TECHNICAL_FIELDS][:8]
    columns: dict[str, str] = {}
    enums: dict[str, dict[str, str]] = {}
    for name in selected:
        field = fields.get(name) or {}
        label = str(field.get("label") or properties.get(name, {}).get("title") or name)
        if label in columns:
            label = f"{label}（{name}）"
        columns[label] = f"$.{name}"
        candidates = field.get("candidates") or {}
        if candidates.get("type") == "static":
            enums[name] = {
                json.dumps(option.get("value"), ensure_ascii=False, sort_keys=True): str(option.get("label"))
                for option in candidates.get("values", [])
            }
    if not columns:
        raise ValueError("合同没有可展示的列表字段")
    return columns, enums


def execute(capability_id: str, supplied: str) -> Any:
    command = [
        sys.executable,
        str(Path(__file__).with_name("execute.py")),
        "--capability", capability_id,
        "--input", supplied,
    ]
    completed = subprocess.run(command, text=True, encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if completed.returncode != 0:
        raise ValueError((completed.stderr or completed.stdout).strip() or "能力执行失败")
    return json.loads(completed.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description="把 JSON 列表格式化为 Markdown 表格")
    parser.add_argument("--input", default="-", help="JSON 字符串、@文件，或 - 表示标准输入")
    parser.add_argument("--capability", help="直接执行列表能力，并依据合同自动选择业务列和转换固定枚举")
    parser.add_argument("--items-path", default="$", help="列表所在 JSON 路径")
    parser.add_argument("--columns", help='列名到 JSON 路径的对象，例如 {"编号":"$.id"}')
    parser.add_argument("--limit", type=int, default=20, help="最多展示多少项")
    args = parser.parse_args()
    try:
        capability = contract_capability(args.capability) if args.capability else None
        root = execute(args.capability, args.input) if args.capability else read_value(args.input)
        inferred = schema_array(capability.get("outputSchema") or {}) if capability else None
        if capability and not inferred:
            raise ValueError("该能力的输出合同不是列表")
        items_path = f"$.body{inferred[0][1:]}" if inferred else args.items_path
        items = get_by_path(root, items_path)
        if not isinstance(items, list):
            raise ValueError("指定路径不是列表")
        if not items:
            print("无数据")
            return 0
        enums: dict[str, dict[str, str]] = {}
        columns = json.loads(args.columns) if args.columns else None
        if columns is None and capability and inferred:
            columns, enums = capability_columns(capability, inferred[1])
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
            values = []
            for path in columns.values():
                value = get_by_path(item, path)
                name = path.removeprefix("$.")
                value = enums.get(name, {}).get(json.dumps(value, ensure_ascii=False, sort_keys=True), value)
                values.append(cell(value))
            print("| " + " | ".join(values) + " |")
        if len(items) > args.limit:
            print(f"仅展示前 {args.limit} 项，共 {len(items)} 项。")
        return 0
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"格式化失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
