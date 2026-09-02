#!/usr/bin/env python3
"""执行 CONTRACT.json 中经过验证的单个原子能力。"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib import error, parse, request


CONTRACT_PATH = Path(__file__).resolve().parents[1] / "references" / "CONTRACT.json"


def load_contract() -> dict[str, Any]:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def parse_json_argument(raw: str) -> Any:
    if raw.startswith("@"):
        return json.loads(Path(raw[1:]).read_text(encoding="utf-8"))
    return json.loads(raw)


def literal_key(json_path: str) -> str | None:
    if json_path == "$":
        return None
    literal = json_path.removeprefix("$.")
    return literal if literal and "." not in literal else None


def path_parts(json_path: str) -> list[str | int]:
    if json_path == "$":
        return []
    return [int(index) if index else name for name, index in re.findall(r"([^\.\[\]]+)|\[(\d+)\]", json_path.removeprefix("$."))]


def get_by_path(value: Any, json_path: str) -> Any:
    key = literal_key(json_path)
    if key is not None:
        return value.get(key) if isinstance(value, dict) else None
    current = value
    for part in path_parts(json_path):
        if isinstance(part, int) and isinstance(current, list) and part < len(current):
            current = current[part]
        elif isinstance(part, str) and isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def set_by_path(target: dict[str, Any], json_path: str, value: Any) -> None:
    key = literal_key(json_path)
    if key is not None:
        target[key] = value
        return
    parts = path_parts(json_path)
    if any(isinstance(part, int) for part in parts):
        raise ValueError(f"暂不支持向数组路径写入字段：{json_path}")
    current = target
    for part in parts[:-1]:
        current = current.setdefault(str(part), {})
    if parts:
        current[str(parts[-1])] = value


def date_to_millis(value: str) -> int:
    raw = value.strip().replace("T", " ")
    if len(raw) == 10:
        raw += " 00:00:00"
    moment = dt.datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone(dt.timedelta(hours=8)))
    return int(moment.timestamp() * 1000)


def normalize_date_string(value: str) -> str:
    raw = value.strip().replace("T", " ")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return f"{raw} 00:00:00"
    return value


def nest_line_items(capability: dict[str, Any], supplied: dict[str, Any]) -> dict[str, Any]:
    prepared = copy.deepcopy(supplied)
    item_fields = [field for field in capability.get("inputForm", []) if "[*]" in field.get("path", "")]
    if not item_fields:
        return prepared
    if isinstance(prepared.get("items"), list):
        return prepared
    item: dict[str, Any] = {}
    for field in item_fields:
        name = field["name"]
        if name in prepared:
            item[name] = prepared.pop(name)
    if item:
        prepared["items"] = [item]
    return prepared


def apply_candidate(field: dict[str, Any], value: Any) -> Any:
    rule = field.get("candidates") or {}
    if rule.get("type") != "static" or value is None:
        return value
    for option in rule.get("values", []):
        if option.get("value") == value or str(option.get("label")) == str(value):
            return option.get("value")
    return value


def fill_computed(prepared: dict[str, Any]) -> dict[str, Any]:
    items = prepared.get("items")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            count = item.get("count")
            price = item.get("productPrice")
            if isinstance(count, (int, float)) and isinstance(price, (int, float)):
                item.setdefault("totalProductPrice", count * price)
            base = item.get("totalProductPrice")
            tax_percent = item.get("taxPercent") or 0
            if isinstance(base, (int, float)):
                item.setdefault("taxPrice", base * float(tax_percent) / 100)
                item.setdefault("totalPrice", base + float(item.get("taxPrice") or 0))
        totals = [item.get("totalPrice") for item in items if isinstance(item, dict) and isinstance(item.get("totalPrice"), (int, float))]
        if totals:
            prepared.setdefault("totalPrice", sum(totals))
        if "discountPercent" in prepared and "totalPrice" in prepared:
            prepared.setdefault("discountPrice", float(prepared["totalPrice"]) * float(prepared.get("discountPercent") or 0) / 100)
    return prepared


def delete_by_path(target: dict[str, Any], json_path: str) -> None:
    key = literal_key(json_path)
    if key is not None:
        target.pop(key, None)
        return
    parts = path_parts(json_path)
    if not parts or any(isinstance(part, int) for part in parts):
        return
    current: Any = target
    for part in parts[:-1]:
        if not isinstance(current, dict):
            return
        current = current.get(str(part))
    if isinstance(current, dict):
        current.pop(str(parts[-1]), None)


def resolve_rule(rule: str) -> Any:
    if rule.startswith("literal:"):
        return json.loads(rule.removeprefix("literal:"))
    if rule.startswith("env:"):
        name = rule.removeprefix("env:").strip()
        if not name or name not in os.environ:
            raise ValueError(f"缺少环境变量：{name}")
        return os.environ[name]
    if rule == "uuid":
        return str(uuid.uuid4())
    if rule == "now:iso":
        return dt.datetime.now(dt.timezone.utc).isoformat()
    raise ValueError(f"无法执行的字段处理规则：{rule}")


def coerce(value: Any, value_type: str, field_path: str, field: dict[str, Any] | None = None) -> Any:
    if value is None:
        return value
    value = apply_candidate(field or {}, value)
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}:\d{2})?", value.strip()):
        if value_type in {"integer", "number"}:
            return date_to_millis(value)
        if value_type == "string":
            return normalize_date_string(value)
    if value_type == "string":
        return value if isinstance(value, str) else str(value)
    if value_type == "integer" and isinstance(value, str) and re.fullmatch(r"[-+]?\d+", value.strip()):
        return int(value)
    if value_type == "number" and isinstance(value, str) and re.fullmatch(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)", value.strip()):
        return float(value)
    if value_type == "boolean" and isinstance(value, str) and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    valid = {
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "array": isinstance(value, list),
        "object": isinstance(value, dict),
        "unknown": True,
    }.get(value_type, True)
    if not valid:
        raise ValueError(f"字段 {field_path} 不能唯一转换为 {value_type}")
    return value


def prepare_input(capability: dict[str, Any], supplied: dict[str, Any]) -> dict[str, Any]:
    prepared = nest_line_items(capability, supplied)
    for field in capability.get("inputForm", []):
        if "[*]" in field["path"]:
            prefix, suffix = field["path"].split("[*].", 1)
            items = get_by_path(prepared, prefix)
            if not isinstance(items, list):
                if field.get("required") and field.get("source") == "caller":
                    raise ValueError(f"缺少调用方必填字段：{field['label']} ({field['path']})")
                continue
            name = suffix.split(".")[-1]
            for item in items:
                if not isinstance(item, dict):
                    continue
                value = item.get(name)
                if value is None and field.get("defaultRule"):
                    value = resolve_rule(field["defaultRule"])
                    item[name] = value
                if value is None and field.get("required") and field.get("source") == "caller":
                    raise ValueError(f"缺少调用方必填字段：{field['label']} ({field['path']})")
                if value is not None:
                    item[name] = coerce(value, field.get("valueType", "unknown"), field["path"], field)
            continue
        value = get_by_path(prepared, field["path"])
        if value is None and field.get("defaultRule"):
            value = resolve_rule(field["defaultRule"])
            set_by_path(prepared, field["path"], value)
        if value is None and field.get("required"):
            if field.get("source") == "caller":
                raise ValueError(f"缺少调用方必填字段：{field['label']} ({field['path']})")
            raise ValueError(f"系统必填字段没有可执行的处理结果：{field['label']} ({field['path']})")
        if value is not None:
            set_by_path(prepared, field["path"], coerce(value, field.get("valueType", "unknown"), field["path"], field))
    return fill_computed(prepared)


def auth_headers() -> dict[str, str]:
    raw = os.environ.get("SKILL_AUTH_HEADERS", "{}")
    headers = json.loads(raw)
    if not isinstance(headers, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in headers.items()):
        raise ValueError("SKILL_AUTH_HEADERS 必须是字符串键值组成的 JSON 对象")
    return {"Accept": "application/json", **headers}


def field_value(capability: dict[str, Any], prepared: dict[str, Any], name: str) -> tuple[Any, str]:
    field = next((item for item in capability.get("inputForm", []) if item.get("name") == name), None)
    field_path = field["path"] if field else f"$.{name}"
    return get_by_path(prepared, field_path), field_path


def build_request(capability: dict[str, Any], prepared: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    url = capability["transport"]["urlTemplate"]
    body = copy.deepcopy(prepared)
    consumed: set[str] = set()

    def replace_placeholder(match: re.Match[str]) -> str:
        name = match.group(1)
        value, field_path = field_value(capability, prepared, name)
        if value is None:
            raise ValueError(f"缺少地址参数：{name}")
        consumed.add(field_path)
        return parse.quote(str(value), safe="")

    split = parse.urlsplit(url)
    path_value = re.sub(r"\{([^}]+)\}", replace_placeholder, split.path)
    query_items: list[tuple[str, Any]] = []
    for key, value in parse.parse_qsl(split.query, keep_blank_values=True):
        match = re.fullmatch(r"\{([^}]+)\}", value)
        if match:
            actual, field_path = field_value(capability, prepared, match.group(1))
            if actual is None:
                continue
            consumed.add(field_path)
            if isinstance(actual, list):
                query_items.extend((key, item) for item in actual)
            else:
                query_items.append((key, actual))
        else:
            query_items.append((key, value))

    method = capability["transport"]["method"].upper()
    if method in {"GET", "HEAD"}:
        for field in capability.get("inputForm", []):
            if field["path"] in consumed:
                continue
            value = get_by_path(prepared, field["path"])
            if value is None:
                continue
            if isinstance(value, list):
                query_items.extend((field["name"], item) for item in value)
            elif not isinstance(value, dict):
                query_items.append((field["name"], value))
    else:
        for field_path in consumed:
            delete_by_path(body, field_path)

    final_url = parse.urlunsplit((split.scheme, split.netloc, path_value, parse.urlencode(query_items, doseq=True), split.fragment))
    headers = auth_headers()
    data = None
    if method not in {"GET", "HEAD"}:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    return final_url, {"method": method, "headers": headers, "data": data}


def assertion_ok(body: Any, assertion: dict[str, Any]) -> tuple[bool, Any]:
    actual = get_by_path(body, assertion["path"])
    if assertion["kind"] == "exists":
        return actual is not None, actual
    if assertion["kind"] == "nonempty":
        return actual not in (None, "", [], {}), actual
    return actual == assertion.get("value"), actual


def response_body(raw: bytes, headers: Any) -> Any:
    if not raw:
        return None
    content_type = str(headers.get("content-type", ""))
    if re.search(r"json|text|javascript|xml|graphql", content_type, re.I):
        text = raw.decode("utf-8", errors="replace")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    output_dir = Path(os.environ.get("SKILL_OUTPUT_DIR", Path.cwd() / "outputs")).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    disposition = str(headers.get("content-disposition", ""))
    match = re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)", disposition, re.I)
    filename = Path(parse.unquote(match.group(1)) if match else f"download-{uuid.uuid4().hex[:8]}.bin").name
    target = output_dir / filename
    target.write_bytes(raw)
    return {"file": str(target), "contentType": content_type, "byteLength": len(raw)}


def execute_capability(capability: dict[str, Any], supplied: dict[str, Any], confirm_write: bool = False) -> dict[str, Any]:
    if capability.get("validation", {}).get("status") != "verified":
        raise ValueError("能力没有通过验证")
    if capability.get("confirmation", {}).get("required") and not confirm_write:
        raise ValueError("写操作需要本次执行的明确确认")
    prepared = prepare_input(capability, supplied)
    url, options = build_request(capability, prepared)
    req = request.Request(url, method=options["method"], headers=options["headers"], data=options["data"])
    try:
        with request.urlopen(req, timeout=30) as response:
            status = response.status
            raw = response.read()
            response_headers = response.headers
    except error.HTTPError as exc:
        status = exc.code
        raw = exc.read()
        response_headers = exc.headers
    body = response_body(raw, response_headers)
    assertions = []
    for item in capability.get("completion", {}).get("assertions", []):
        ok, actual = assertion_ok(body, item)
        assertions.append({**item, "actual": actual, "ok": ok})
    status_ok = status in capability.get("completion", {}).get("acceptedHttpStatuses", [])
    return {"ok": status_ok and all(item["ok"] for item in assertions), "status": status, "body": body, "assertions": assertions}


def main() -> int:
    parser = argparse.ArgumentParser(description="执行一个已验证的原子能力")
    parser.add_argument("--capability", required=True, help="能力编号")
    parser.add_argument("--input", default="{}", help="JSON 字符串，或 @JSON文件")
    parser.add_argument("--confirm-write", action="store_true", help="仅在用户已明确确认本次写操作后使用")
    parser.add_argument("--prepare-only", action="store_true", help="只组装请求，不访问业务系统")
    args = parser.parse_args()
    try:
        contract = load_contract()
        capability = next((item for item in contract["capabilities"] if item["id"] == args.capability), None)
        if capability is None:
            raise ValueError(f"未知能力：{args.capability}")
        supplied = parse_json_argument(args.input)
        if not isinstance(supplied, dict):
            raise ValueError("--input 必须是 JSON 对象")
        if args.prepare_only:
            prepared = prepare_input(capability, supplied)
            url, options = build_request(capability, prepared)
            print(json.dumps({
                "ok": True,
                "prepared": prepared,
                "url": url,
                "method": options["method"],
            }, ensure_ascii=False, indent=2))
            return 0
        result = execute_capability(capability, supplied, args.confirm_write)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 2
    except (ValueError, OSError, json.JSONDecodeError, error.URLError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
