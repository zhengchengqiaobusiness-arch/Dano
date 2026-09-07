#!/usr/bin/env python3
"""按合同读取静态候选，或调用已验证查询能力生成动态候选。"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from execute import execute_capability, get_by_path, load_contract, parse_json_argument


def extract_many(root: Any, json_path: str) -> list[Any]:
    tokens = [token for token in json_path.removeprefix("$.").split(".") if token]
    values = [root]
    for token in tokens:
        wildcard = token.endswith("[*]")
        key = token[:-3] if wildcard else token
        next_values: list[Any] = []
        for value in values:
            child = value.get(key) if key and isinstance(value, dict) else value
            if wildcard and isinstance(child, list):
                next_values.extend(child)
            elif not wildcard and child is not None:
                next_values.append(child)
        values = next_values
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description="读取字段候选项")
    parser.add_argument("--capability", required=True, help="目标能力编号")
    parser.add_argument("--field", required=True, help="目标字段路径")
    parser.add_argument("--input", default="{}", help="候选查询所需 JSON，或 @JSON文件")
    args = parser.parse_args()
    try:
        contract = load_contract()
        target = next((item for item in contract["capabilities"] if item["id"] == args.capability), None)
        if target is None:
            raise ValueError(f"未知能力：{args.capability}")
        field = next((item for item in target.get("inputForm", []) if item["path"] == args.field), None)
        if field is None or not field.get("candidates"):
            raise ValueError("该字段没有候选规则")
        rule = field["candidates"]
        if rule["type"] == "static":
            result = {"field": args.field, "source": "static", "candidates": rule["values"]}
            identifiers = [item["value"] for item in rule["values"]]
        else:
            source = next((item for item in contract["capabilities"] if item["id"] == rule["capabilityId"]), None)
            if source is None or source.get("operation") != "query" or source.get("validation", {}).get("status") != "verified":
                raise ValueError("动态候选来源不是已验证的查询能力")
            supplied = parse_json_argument(args.input)
            response = execute_capability(source, supplied)
            if not response["ok"]:
                raise ValueError("动态候选查询未满足完成条件")
            values = extract_many(response["body"], rule["valuePath"])
            identifiers = [get_by_path(item, rule["matchPath"]) if rule.get("matchPath") else item for item in values]
            template = rule.get("valueTemplate") or {}
            if template.get("type") == "object":
                mapped_values: list[Any] = []
                for candidate in values:
                    if not isinstance(candidate, dict):
                        mapped_values.append(candidate)
                        continue
                    mapped_values.append({
                        key: (
                            extract_many(candidate, mapping.get("sourcePath") or "$")[0]
                            if "sourcePath" in mapping and extract_many(candidate, mapping.get("sourcePath") or "$")
                            else mapping.get("literal")
                        )
                        for key, mapping in (template.get("properties") or {}).items()
                    })
                values = mapped_values
            labels = extract_many(response["body"], rule["labelPath"])
            result = {
                "field": args.field,
                "source": source["id"],
                "candidates": [{"value": value, "label": str(labels[index] if index < len(labels) else value)} for index, value in enumerate(values)],
            }
        options = []
        for identifier, candidate in zip(identifiers, result["candidates"]):
            if type(identifier) not in (str, int, float):
                identifier = candidate["label"]
            if any(str(option["id"]) == str(identifier) for option in options):
                raise ValueError("候选编号或显示名不唯一，无法生成安全的表单选项")
            options.append({"id": identifier, "label": str(candidate["label"])})
        result["options"] = options
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
