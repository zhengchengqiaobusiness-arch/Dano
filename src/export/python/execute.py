#!/usr/bin/env python3
"""执行 CONTRACT.json 中经过验证的单个原子能力。"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import html
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib import error, parse, request


SKILL_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = SKILL_ROOT / "references" / "CONTRACT.json"


def load_contract() -> dict[str, Any]:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8-sig"))


def parse_json_argument(raw: str) -> Any:
    if raw.startswith("@"):
        return json.loads(Path(raw[1:]).read_text(encoding="utf-8-sig"))
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
    current: Any = target
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        nxt = None if last else parts[index + 1]
        if isinstance(current, list) and isinstance(part, int):
            while len(current) <= part:
                current.append({} if last or not isinstance(nxt, int) else [])
            if last:
                current[part] = value
                return
            current = current[part]
            continue
        if last:
            current[part] = value
            return
        if not isinstance(current, dict) or part not in current or current[part] is None:
            current[part] = [] if isinstance(nxt, int) else {}
        current = current[part]


def date_to_millis(value: str) -> int:
    raw = value.strip().replace("T", " ")
    if len(raw) == 10:
        raw += " 00:00:00"
    moment = dt.datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone(dt.timedelta(hours=8)))
    return int(moment.timestamp() * 1000)


def normalize_date_string(value: str, clock: str | None = None) -> str:
    raw = value.strip().replace("T", " ")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        if clock and re.fullmatch(r"\d{2}:\d{2}:\d{2}", str(clock)):
            return f"{raw} {clock}"
        return raw
    return value


def item_input_key(field: dict[str, Any]) -> str:
    return (field.get("path") or "").removeprefix("$.").replace("[*]", "")


def parse_literal_rule(rule: Any) -> Any:
    if not isinstance(rule, str) or not rule.startswith("literal:"):
        return None
    raw = rule[len("literal:"):]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def collection_template_rows(capability: dict[str, Any], prefix: str) -> list[dict[str, Any]] | None:
    for field in capability.get("inputForm", []):
        if field.get("path") != prefix:
            continue
        value = parse_literal_rule(field.get("defaultRule"))
        if isinstance(value, list) and all(isinstance(item, dict) for item in value):
            return value
    return None


def parse_collection_leaf_path(path: str) -> dict[str, Any] | None:
    starred = re.fullmatch(r"(.*)\[\*\]\.(.+)", path)
    if starred:
        return {"prefix": starred.group(1), "index": "*", "key": starred.group(2).split(".")[0]}
    indexed = re.fullmatch(r"(.*)\[(\d+)\]\.(.+)", path)
    if indexed:
        return {"prefix": indexed.group(1), "index": int(indexed.group(2)), "key": indexed.group(3).split(".")[0]}
    return None


def collection_field_input_keys(field: dict[str, Any], siblings: list[dict[str, Any]]) -> list[str]:
    path = field.get("path") or ""
    keys = {path, path.removeprefix("$.")}
    name = field.get("name")
    if name and sum(1 for item in siblings if item.get("name") == name) <= 1:
        keys.add(name)
    return list(keys)


def apply_collection_templates(capability: dict[str, Any], prepared: dict[str, Any]) -> None:
    fields = capability.get("inputForm", [])
    for field in fields:
        path = field.get("path") or ""
        parsed = parse_collection_leaf_path(path)
        if parsed:
            continue
        template = collection_template_rows(capability, path)
        if not template:
            continue
        current = get_by_path(prepared, path)
        rows = copy.deepcopy(template)
        if isinstance(current, list) and current:
            rows = []
            for index, row in enumerate(template):
                overlay = current[index] if index < len(current) else None
                next_row = copy.deepcopy(row)
                if isinstance(overlay, dict):
                    next_row.update(overlay)
                rows.append(next_row)
            if len(current) > len(template):
                rows.extend(copy.deepcopy(item) if isinstance(item, dict) else item for item in current[len(template):])
        header_names = {
            item.get("name")
            for item in fields
            if not parse_collection_leaf_path(item.get("path") or "") and item.get("path") != path
        }
        for child in fields:
            leaf = parse_collection_leaf_path(child.get("path") or "")
            if not leaf or leaf["prefix"] != path:
                continue
            for key in collection_field_input_keys(child, fields):
                if key not in prepared:
                    continue
                value = prepared[key]
                if leaf["index"] == "*":
                    for row in rows:
                        if isinstance(row, dict) and leaf["key"] in row:
                            row[leaf["key"]] = value
                elif leaf["index"] < len(rows) and isinstance(rows[leaf["index"]], dict) and leaf["key"] in rows[leaf["index"]]:
                    rows[leaf["index"]][leaf["key"]] = value
                if key != child.get("name") or child.get("name") not in header_names:
                    prepared.pop(key, None)
        for key, value in list(prepared.items()):
            if key in header_names or key == field.get("name"):
                continue
            if not any(isinstance(row, dict) and key in row for row in rows):
                continue
            for row in rows:
                if isinstance(row, dict) and key in row:
                    row[key] = value
            prepared.pop(key, None)
        set_by_path(prepared, path, rows)


def nest_line_items(capability: dict[str, Any], supplied: dict[str, Any]) -> dict[str, Any]:
    prepared = copy.deepcopy(supplied)
    fields = capability.get("inputForm", [])
    item_fields = [field for field in fields if parse_collection_leaf_path(field.get("path") or "")]
    if not item_fields:
        return prepared
    if isinstance(prepared.get("items"), list):
        return prepared
    for field in item_fields:
        for key in collection_field_input_keys(field, fields):
            if key not in prepared:
                continue
            if get_by_path(prepared, field["path"]) is None:
                set_by_path(prepared, field["path"], prepared[key])
    return prepared


def apply_candidate(field: dict[str, Any], value: Any) -> Any:
    rule = field.get("candidates") or {}
    if rule.get("type") != "static" or value is None:
        return value
    for option in rule.get("values", []):
        if option.get("value") == value or str(option.get("label")) == str(value):
            return option.get("value")
    return value


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


def apply_capability_candidates(
    capability: dict[str, Any],
    prepared: dict[str, Any],
    contract: dict[str, Any] | None,
    resolve_lookups: bool,
) -> None:
    if not resolve_lookups or not contract:
        return
    for field in capability.get("inputForm", []):
        rule = field.get("candidates") or {}
        if rule.get("type") != "capability":
            continue
        path = field.get("path") or ""
        leaf = parse_collection_leaf_path(path)
        rows = get_by_path(prepared, leaf["prefix"]) if leaf else None
        present = any(isinstance(row, dict) and row.get(leaf["key"]) is not None for row in rows or []) if leaf else get_by_path(prepared, path) is not None
        if not present:
            continue
        source = next((item for item in contract.get("capabilities", []) if item.get("id") == rule.get("capabilityId")), None)
        if not source or source.get("operation") != "query" or source.get("validation", {}).get("status") != "verified":
            raise ValueError(f"字段 {field.get('label')} 的候选查询不存在或未验证")
        source_input: dict[str, Any] = {}
        for dependency in rule.get("dependsOn") or []:
            target = next((item for item in capability.get("inputForm", []) if item.get("path") == dependency or item.get("name") == dependency), None)
            value = get_by_path(prepared, target.get("path")) if target else get_by_path(prepared, dependency)
            if value is not None:
                source_input[(target or {}).get("name") or dependency.removeprefix("$.").split(".")[-1]] = value
        response = execute_capability(source, source_input, False)
        if not response.get("ok"):
            raise ValueError(f"字段 {field.get('label')} 的候选查询失败")
        values = extract_many(response.get("body"), rule["valuePath"])
        labels = extract_many(response.get("body"), rule["labelPath"])
        choices = [{"value": item, "label": str(labels[index] if index < len(labels) else item)} for index, item in enumerate(values)]

        def resolve(value: Any) -> Any:
            matches = [item for item in choices if same_join(item["value"], value) or item["label"] == str(value)]
            if len(matches) != 1:
                raise ValueError(f"字段 {field.get('label')} 的候选值不存在或不唯一：{value}")
            return matches[0]["value"]

        if leaf:
            for row in rows or []:
                if isinstance(row, dict) and row.get(leaf["key"]) is not None:
                    row[leaf["key"]] = resolve(row[leaf["key"]])
        else:
            set_by_path(prepared, path, resolve(get_by_path(prepared, path)))


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
        raw = rule.removeprefix("literal:")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
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


def eval_computed(expr: str, prepared: dict[str, Any], item: dict[str, Any] | None = None) -> Any:
    scope = dict(prepared)
    if item:
        scope.update(item)

    def replace_sum(match: re.Match[str]) -> str:
        name = match.group(1)
        items = prepared.get("items")
        if not isinstance(items, list):
            raise ValueError(f"无法计算 sum(items.{name})")
        return str(sum(float(row.get(name) or 0) for row in items if isinstance(row, dict)))

    text = re.sub(r"sum\(items\.([A-Za-z_][A-Za-z0-9_]*)\)", replace_sum, expr)

    def replace_name(match: re.Match[str]) -> str:
        name = match.group(0)
        if name not in scope or scope[name] is None:
            raise ValueError(f"计算缺少字段：{name}")
        return str(scope[name])

    text = re.sub(r"[A-Za-z_][A-Za-z0-9_]*", replace_name, text)
    if not re.fullmatch(r"[\d\.\seE+\-*/()]+", text):
        raise ValueError(f"非法计算公式：{expr}")
    return eval(text, {"__builtins__": {}}, {})


def parse_from_rule(rule: str) -> dict[str, str] | None:
    match = re.fullmatch(r"from:([^:]+):(.+?)(?:\|via:([A-Za-z_][A-Za-z0-9_]*))?(?:\|fallback:(.*))?", rule)
    if not match:
        return None
    parsed = {"capabilityId": match.group(1), "fromPath": match.group(2)}
    if match.group(3):
        parsed["via"] = match.group(3)
    if match.group(4) is not None:
        parsed["fallback"] = match.group(4)
    return parsed


def parse_fallback_value(raw: str | None) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def same_join(left: Any, right: Any) -> bool:
    if left is None or right is None or left == "" or right == "":
        return False
    if left == right:
        return True
    return str(left) == str(right)


def extract_joined(body: Any, json_path: str, via_value: Any = None) -> Any:
    if "[*]." in json_path:
        prefix, suffix = json_path.split("[*].", 1)
        rows = get_by_path(body, prefix)
        if not isinstance(rows, list):
            return None
        name = suffix.split(".")[-1]
        matched = [
            row.get(name)
            for row in rows
            if isinstance(row, dict) and (
                via_value is None
                or same_join(row.get("id"), via_value)
                or same_join(row.get("value"), via_value)
                or same_join(row.get("code"), via_value)
            )
        ]
        matched = [item for item in matched if item is not None]
        if len(matched) == 1:
            return matched[0]
        if via_value is None and matched and len({str(item) for item in matched}) == 1:
            return matched[0]
        return None
    return get_by_path(body, json_path)


def resolve_from(rule: str, prepared: dict[str, Any], item: dict[str, Any] | None, contract: dict[str, Any] | None) -> Any:
    parsed = parse_from_rule(rule)
    if not parsed or not contract:
        return None
    source = next((item for item in contract.get("capabilities", []) if item.get("id") == parsed["capabilityId"]), None)
    if not source:
        raise ValueError(f"带出查询不存在：{parsed['capabilityId']}")
    via = parsed.get("via")
    via_value = (item or {}).get(via) if via else None
    if via and via_value is None:
        via_value = prepared.get(via)
    if via and via_value is None:
        return None
    result = execute_capability(source, {via: via_value} if via else {}, False)
    if not result.get("ok"):
        raise ValueError(f"带出查询失败：{parsed['capabilityId']}")
    return extract_joined(result.get("body"), parsed["fromPath"], via_value)


def resolve_field_rule(
    field: dict[str, Any],
    prepared: dict[str, Any],
    item: dict[str, Any] | None,
    contract: dict[str, Any] | None,
    resolve_lookups: bool,
) -> Any:
    rule = field.get("defaultRule") or ""
    if rule.startswith("copy:"):
        name = rule.removeprefix("copy:")
        if item is not None and item.get(name) is not None:
            return item[name]
        return prepared.get(name)
    if rule.startswith("computed:"):
        return eval_computed(rule.removeprefix("computed:"), prepared, item)
    if rule.startswith("from:"):
        parsed = parse_from_rule(rule)
        if resolve_lookups:
            value = resolve_from(rule, prepared, item, contract)
            if value is not None:
                return value
        return parse_fallback_value(parsed.get("fallback") if parsed else None)
    if rule:
        return resolve_rule(rule)
    return None


def coerce(value: Any, value_type: str, field_path: str, field: dict[str, Any] | None = None) -> Any:
    if value is None:
        return value
    value = apply_candidate(field or {}, value)
    if (field or {}).get("richText") and isinstance(value, str) and value and not re.search(r"</?[a-z][^>]*>", value, re.I):
        value = "".join(f"<p>{html.escape(line) if line else '<br>'}</p>" for line in value.splitlines())
    date_clocks = (field or {}).get("dateClocks") or []
    if value_type == "array" and isinstance(value, list) and len(date_clocks) == len(value):
        return [
            normalize_date_string(item, date_clocks[index])
            if isinstance(item, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}:\d{2})?", item.strip())
            else item
            for index, item in enumerate(value)
        ]
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}:\d{2})?", value.strip()):
        if value_type in {"integer", "number"}:
            return date_to_millis(value)
        if value_type == "string":
            return normalize_date_string(value, (field or {}).get("dateClock"))
    if value_type == "string":
        return value if isinstance(value, str) else str(value)
    if value_type == "integer":
        if isinstance(value, bool):
            pass
        elif isinstance(value, int):
            return value
        elif isinstance(value, float) and value.is_integer():
            return int(value)
        elif isinstance(value, str) and re.fullmatch(r"[-+]?\d+", value.strip()):
            return int(value)
    if value_type == "number" and isinstance(value, str) and re.fullmatch(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)", value.strip()):
        return float(value)
    if value_type == "boolean" and isinstance(value, str) and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if value_type == "array" and not isinstance(value, list):
        return [value]
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


def hoist_named_fields(capability: dict[str, Any], supplied: dict[str, Any]) -> dict[str, Any]:
    prepared = copy.deepcopy(supplied)
    item_names = {field["name"] for field in capability.get("inputForm", []) if "[*]" in field.get("path", "")}
    for field in capability.get("inputForm", []):
        name = field.get("name")
        path = field.get("path") or ""
        if not name or "[*]" in path or name in item_names or name not in prepared or get_by_path(prepared, path) is not None:
            continue
        if literal_key(path) == name:
            continue
        value = prepared.pop(name)
        set_by_path(prepared, path, value)
    return prepared


def coerce_present_fields(capability: dict[str, Any], prepared: dict[str, Any], require_missing: bool) -> None:
    for field in capability.get("inputForm", []):
        if "[*]" in field["path"]:
            prefix, suffix = field["path"].split("[*].", 1)
            items = get_by_path(prepared, prefix)
            if not isinstance(items, list):
                if require_missing and field.get("required") and field.get("source") == "caller":
                    raise ValueError(f"缺少调用方必填字段：{field['label']} ({field['path']})")
                continue
            name = suffix.split(".")[-1]
            templates = collection_template_rows(capability, prefix)
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                value = item.get(name)
                if value is None:
                    if require_missing and field.get("required") and field.get("source") == "caller":
                        if templates is not None and index < len(templates) and name not in templates[index]:
                            continue
                        raise ValueError(f"缺少调用方必填字段：{field['label']} ({field['path']})")
                    continue
                item[name] = coerce(value, field.get("valueType", "unknown"), field["path"], field)
            continue
        value = get_by_path(prepared, field["path"])
        if value is None:
            if not require_missing:
                continue
            if field.get("required"):
                if field.get("source") == "caller":
                    raise ValueError(f"缺少调用方必填字段：{field['label']} ({field['path']})")
                raise ValueError(f"系统必填字段没有可执行的处理结果：{field['label']} ({field['path']})")
            continue
        set_by_path(prepared, field["path"], coerce(value, field.get("valueType", "unknown"), field["path"], field))


def prepare_input(
    capability: dict[str, Any],
    supplied: dict[str, Any],
    contract: dict[str, Any] | None = None,
    resolve_lookups: bool = True,
) -> dict[str, Any]:
    prepared = hoist_named_fields(capability, nest_line_items(capability, supplied))
    apply_collection_templates(capability, prepared)
    apply_capability_candidates(capability, prepared, contract, resolve_lookups)
    coerce_present_fields(capability, prepared, False)
    changed = True
    while changed:
        changed = False
        for field in capability.get("inputForm", []):
            if "[*]" in field["path"]:
                prefix, suffix = field["path"].split("[*].", 1)
                items = get_by_path(prepared, prefix)
                if not isinstance(items, list):
                    continue
                name = suffix.split(".")[-1]
                templates = collection_template_rows(capability, prefix)
                for index, item in enumerate(items):
                    if not isinstance(item, dict) or item.get(name) is not None:
                        continue
                    if templates is not None and index < len(templates) and name not in templates[index]:
                        continue
                    if not field.get("defaultRule"):
                        continue
                    try:
                        value = resolve_field_rule(field, prepared, item, contract, resolve_lookups)
                    except ValueError:
                        value = None
                    if value is not None:
                        item[name] = coerce(value, field.get("valueType", "unknown"), field["path"], field)
                        changed = True
                continue
            value = get_by_path(prepared, field["path"])
            if value is not None or not field.get("defaultRule"):
                continue
            try:
                value = resolve_field_rule(field, prepared, None, contract, resolve_lookups)
            except ValueError:
                value = None
            if value is not None:
                set_by_path(prepared, field["path"], coerce(value, field.get("valueType", "unknown"), field["path"], field))
                changed = True
    coerce_present_fields(capability, prepared, True)
    return prepared


def checked_headers(value: Any, source: str) -> dict[str, str]:
    if not isinstance(value, dict) or not all(isinstance(key, str) and isinstance(item, str) for key, item in value.items()):
        raise ValueError(f"{source} 必须是字符串键值组成的 JSON 对象")
    return value


def default_auth_file() -> Path:
    return SKILL_ROOT.parent.parent / "credentials" / f"{SKILL_ROOT.name}.json"


def url_origin(url: str) -> str:
    parsed = parse.urlsplit(url)
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname
    if scheme not in {"http", "https"} or not hostname:
        raise ValueError(f"不支持从 URL 读取运行时凭据：{url}")
    host = f"[{hostname}]" if ":" in hostname else hostname
    port = parsed.port
    if port is not None and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    return f"{scheme}://{host}"


def auth_headers(url: str) -> dict[str, str]:
    raw = os.environ.get("SKILL_AUTH_HEADERS", "").strip()
    if raw:
        headers = checked_headers(json.loads(raw), "SKILL_AUTH_HEADERS")
        return {"Accept": "application/json", **headers}

    configured_file = os.environ.get("SKILL_AUTH_FILE", "").strip()
    auth_file = Path(configured_file or default_auth_file())
    if not auth_file.exists():
        if configured_file:
            raise ValueError(f"SKILL_AUTH_FILE 不存在：{auth_file}")
        return {"Accept": "application/json"}
    profile = json.loads(auth_file.read_text(encoding="utf-8-sig"))
    origins = profile.get("origins") if isinstance(profile, dict) else None
    if not isinstance(origins, dict):
        raise ValueError("Skill 运行时凭据文件缺少 origins 对象")
    origin = url_origin(url)
    headers = checked_headers(origins.get(origin, {}), f"{auth_file} 中 {origin} 的认证头")
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
    headers = auth_headers(final_url)
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
    prepared = prepare_input(capability, supplied, load_contract())
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
            prepared = prepare_input(capability, supplied, contract, resolve_lookups=False)
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
