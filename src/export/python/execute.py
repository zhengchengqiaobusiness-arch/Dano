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


def normalize_date_string(value: str, clock: str | None = None) -> str:
    raw = value.strip().replace("T", " ")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        suffix = clock if clock and re.fullmatch(r"\d{2}:\d{2}:\d{2}", str(clock)) else "00:00:00"
        return f"{raw} {suffix}"
    return value


def item_input_key(field: dict[str, Any]) -> str:
    return (field.get("path") or "").removeprefix("$.").replace("[*]", "")


def nest_line_items(capability: dict[str, Any], supplied: dict[str, Any]) -> dict[str, Any]:
    prepared = copy.deepcopy(supplied)
    item_fields = [field for field in capability.get("inputForm", []) if "[*]" in field.get("path", "")]
    if not item_fields:
        return prepared
    if isinstance(prepared.get("items"), list):
        return prepared
    header_names = {field["name"] for field in capability.get("inputForm", []) if "[*]" not in field.get("path", "")}
    item: dict[str, Any] = {}
    for field in item_fields:
        name = field["name"]
        dotted = item_input_key(field)
        if dotted in prepared and dotted != name:
            item[name] = prepared.pop(dotted)
        elif name in prepared and name not in header_names:
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
    match = re.fullmatch(r"from:([^:]+):(.+?)(?:\|via:([A-Za-z_][A-Za-z0-9_]*))?", rule)
    if not match:
        return None
    parsed = {"capabilityId": match.group(1), "fromPath": match.group(2)}
    if match.group(3):
        parsed["via"] = match.group(3)
    return parsed


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
        if not resolve_lookups:
            return None
        return resolve_from(rule, prepared, item, contract)
    if rule:
        return resolve_rule(rule)
    return None


def coerce(value: Any, value_type: str, field_path: str, field: dict[str, Any] | None = None) -> Any:
    if value is None:
        return value
    value = apply_candidate(field or {}, value)
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
            for item in items:
                if not isinstance(item, dict):
                    continue
                value = item.get(name)
                if value is None:
                    if require_missing and field.get("required") and field.get("source") == "caller":
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
                for item in items:
                    if not isinstance(item, dict) or item.get(name) is not None:
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
