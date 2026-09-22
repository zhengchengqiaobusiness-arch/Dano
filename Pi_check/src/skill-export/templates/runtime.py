#!/usr/bin/env python3
"""Execute recorded capabilities from references/CONTRACT.json. Do not rewrite."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import client

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "references" / "CONTRACT.json"


def load_contract():
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def capability(contract, capability_id):
    wanted = str(capability_id or "")
    for item in contract.get("capabilities") or []:
        if str(item.get("capability_id") or "") == wanted:
            return item
    raise RuntimeError(f"合同没有能力: {wanted}")


def route(contract, route_id):
    wanted = str(route_id or "default")
    for item in contract.get("routes") or []:
        if str(item.get("route_id") or "") == wanted:
            return item
    raise RuntimeError(f"合同没有路线: {wanted}")


def _payload_slot(path, method="GET"):
    text = str(path or "")
    if text.startswith("query."):
        return "query", text[6:]
    if text.startswith("body."):
        return "body", text[5:]
    if str(method).upper() == "GET":
        return "query", text
    return "body", text


def _probe_path(raw):
    text = str(raw or "").strip()
    if not text:
        return ""
    if "://" in text:
        try:
            from urllib.parse import urlparse
            return urlparse(text).path or ""
        except Exception:
            return text.split("?", 1)[0]
    return text.split("?", 1)[0]


def _identity_spec(param):
    ident = param.get("identity") if isinstance(param.get("identity"), dict) else {}
    src = param.get("source") if isinstance(param.get("source"), dict) else {}
    return {
        "method": str(ident.get("method") or src.get("source_method") or "GET").upper() or "GET",
        "path": ident.get("path") or src.get("source_url") or src.get("path") or "",
        "result_path": ident.get("result_path") or src.get("result_path") or "",
    }


def _identity_payload(param=None, cache=None):
    spec = _identity_spec(param or {})
    probes = []
    path = _probe_path(spec.get("path"))
    if path:
        probes.append((spec.get("method") or "GET", path))
    else:
        for probe in list(getattr(client, "CONFIG", {}).get("identity_probes") or []):
            probe_path = _probe_path((probe or {}).get("path"))
            method = str((probe or {}).get("method") or "GET").upper() or "GET"
            if probe_path:
                probes.append((method, probe_path))
    last_error = None
    seen = set()
    for method, path in probes:
        key = f"{method} {path}"
        if key in seen:
            continue
        seen.add(key)
        if cache is not None and key in cache:
            return cache[key]
        try:
            result = client.http_json(method, path)
            payload = result.get("data") if isinstance(result, dict) else result
            if cache is not None:
                cache[key] = payload
            return payload
        except Exception as exc:
            last_error = exc
    raise client.AuthExpired(str(last_error) if last_error else "没有身份探针，无法读取当前登录用户")


def current_user():
    return {"_payload": _identity_payload()}


def _is_http_wrapper(result):
    return isinstance(result, dict) and "data" in result and (
        "ok" in result or "status" in result or "method" in result or "url" in result
    )


def _http_body(result):
    if _is_http_wrapper(result):
        return result.get("data")
    return result


def _link_value(result, source_path):
    body = _http_body(result)
    path = str(source_path or "").removeprefix("response.").removeprefix("$.")
    if "[]" in path:
        list_path, _, field = path.partition("[]")
        list_path = list_path.rstrip(".")
        field = field.lstrip(".")
        rows = client.get_path(body, list_path) if list_path else body
        if not isinstance(rows, list):
            return None
        values = []
        for row in rows:
            if field:
                if isinstance(row, dict) and row.get(field) not in (None, ""):
                    values.append(row.get(field))
            elif row not in (None, ""):
                values.append(row)
        if len(values) == 1:
            return values[0]
        return None
    return client.get_path(body, path)


def _target_key(target_path):
    text = str(target_path or "")
    if text.startswith("query.") or text.startswith("body."):
        return text.split(".", 1)[1].split(".", 1)[0]
    return text.split(".")[-1]


def _previous_from_links(param, links=None):
    key = str((param or {}).get("key") or "")
    path = str((param or {}).get("path") or "")
    for link in links or []:
        target = str((link or {}).get("target_path") or "")
        if target != path and _target_key(target) != key:
            continue
        from_step = str((link or {}).get("source_step_id") or "").strip()
        from_path = str((link or {}).get("source_path") or "").strip()
        if from_step or from_path:
            return from_step, from_path
    return "", ""


def apply_links(contract, source_step_id, result, context):
    filled = dict(context or {})
    source = str(source_step_id or "")
    for link in contract.get("links") or []:
        if str((link or {}).get("source_step_id") or "") != source:
            continue
        value = _link_value(result, (link or {}).get("source_path"))
        key = _target_key((link or {}).get("target_path"))
        if key and value not in (None, "") and filled.get(key) in (None, ""):
            filled[key] = value
    return filled


def _generated_value(param):
    src = param.get("source") if isinstance(param.get("source"), dict) else {}
    formula = str(param.get("formula") or src.get("formula") or "").strip().lower()
    if formula in {"today", "date", "yyyy-mm-dd"}:
        return _page_today()
    if formula in {"now", "datetime", "iso"}:
        return datetime.now().isoformat(timespec="seconds")
    if "default_value" in param:
        return param.get("default_value")
    return None


def _system_value(param, user, inputs=None, step_results=None, identity_cache=None, links=None):
    kind = str(param.get("source_kind") or "")
    key = str(param.get("key") or "")
    inputs = inputs or {}
    if kind not in ("current_user", "constant", "generated") and inputs.get(key) not in (None, ""):
        return inputs.get(key)
    if kind == "current_user":
        spec = _identity_spec(param)
        pointer = str(spec.get("result_path") or "")
        root = _identity_payload(param, identity_cache)
        if pointer and root is not None:
            value = client.get_path(root, pointer)
            if value not in (None, ""):
                return value
        raise client.AuthExpired(f"当前登录用户缺少 {key}")
    if kind == "previous_response":
        src = param.get("source") if isinstance(param.get("source"), dict) else {}
        linked_step, linked_path = _previous_from_links(param, links)
        from_step = str(param.get("from_step_id") or src.get("from_step_id") or linked_step or "")
        from_path = param.get("from_path") or src.get("from_path") or linked_path
        prior = (step_results or {}).get(from_step)
        if prior is not None:
            value = _link_value(prior, from_path)
            if value not in (None, ""):
                return value
        return None
    if kind == "generated":
        return _generated_value(param)
    if "default_value" in param:
        return param.get("default_value")
    if kind == "constant" and param.get("type") == "array":
        return []
    return None


_PLACEHOLDERS = {"", "无", "暂无", "没有", "请填写", "请审批", "n/a", "none", "null"}


def _is_placeholder(value):
    return str(value or "").strip().lower() in _PLACEHOLDERS


def _cell_value(spec, raw):
    text = str(raw or "").strip()
    if _is_placeholder(text):
        return None
    kind = str((spec or {}).get("type") or "")
    if kind in ("number", "integer"):
        try:
            number = float(text)
        except ValueError:
            return text
        return int(number) if kind == "integer" or number.is_integer() else number
    return text


def _visible_item_keys(field):
    props = field.get("itemProperties") or {}
    cols = list(props.keys()) or ["content"]
    auto = {str(key) for key in (field.get("autoItemKeys") or [])}
    visible = [key for key in cols if key not in auto]
    return visible or cols


def _rows_from_lines(field, text):
    cols = _visible_item_keys(field)
    sections = list((field.get("sections") or {}).keys())
    props = field.get("itemProperties") or {}
    rows = []
    for line in str(text or "").splitlines():
        raw = line.strip()
        if not raw or _is_placeholder(raw):
            continue
        parts = [part.strip() for part in raw.split("|||")]
        row = {}
        if sections and parts and parts[0] in sections:
            row["section"] = parts[0]
            parts = parts[1:]
        for index, key in enumerate(cols):
            if index >= len(parts):
                break
            value = _cell_value(props.get(key), parts[index])
            if value is not None:
                row[key] = value
        if any(key != "section" and row.get(key) not in (None, "") for key in row):
            rows.append(row)
    return rows


def _parse_caller_array(field, value):
    if isinstance(value, str):
        text = value.strip()
        if not text or _is_placeholder(text):
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            value = parsed
        elif isinstance(parsed, dict):
            value = [parsed]
        else:
            value = _rows_from_lines(field, text)
    if not isinstance(value, list):
        return value
    cleaned = []
    for row in value:
        if not isinstance(row, dict):
            continue
        if _is_placeholder(row.get("content")):
            continue
        cleaned.append(row)
    return _stamp_item_type(field, _assemble_items(field, cleaned))


def _item_type(field):
    raw = field.get("itemType") if isinstance(field, dict) else None
    if raw not in (None, ""):
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    return None


def _stamp_item_type(field, value):
    wanted = _item_type(field)
    if wanted is None or not isinstance(value, list):
        return value
    stamped = []
    for row in value:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        if item.get("itemType") in (None, ""):
            item["itemType"] = wanted
        stamped.append(item)
    return stamped


def _assign_payload(target, name, value):
    key = name or ""
    if key and isinstance(target.get(key), list) and isinstance(value, list):
        target[key] = target[key] + value
        return
    target[key] = value


def _assemble_items(field, value):
    sections = list((field.get("sections") or {}).keys())
    if not sections or not isinstance(value, list):
        return value
    assembled = []
    for row in value:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        if item.get("itemType") not in (None, ""):
            assembled.append(item)
            continue
        section = item.pop("section", None) or item.pop("sectionTitle", None)
        if section in sections:
            item["itemType"] = sections.index(section) + 1
        assembled.append(item)
    return assembled


def _page_today():
    return datetime.now().date().isoformat()


def fill_caller_defaults(cap, inputs):
    filled = dict(inputs or {})
    for field in cap.get("caller_fields") or []:
        key = field.get("id")
        if not key or filled.get(key) not in (None, ""):
            continue
        if field.get("page_default") == "today":
            filled[key] = _page_today()
    return filled


def _is_file_field(field):
    if str((field or {}).get("format") or "").lower() == "binary":
        return True
    return str((field or {}).get("type") or "").lower() in {"file", "binary"}


def _step_by_id(contract, step_id):
    wanted = str(step_id or "")
    for item in contract.get("steps") or []:
        if str(item.get("step_id") or "") == wanted:
            return item
    return {}


def _refs_to_run(cap):
    refs = [item for item in (cap.get("request_refs") or []) if isinstance(item, dict)]
    order = {"preflight": 0, "execute": 1}
    refs.sort(key=lambda item: (
        int(item.get("sequence") or 0) or 0,
        order.get(str(item.get("usage") or ""), 9),
    ))
    return [
        item for item in refs
        if str(item.get("usage") or "execute") in {"preflight", "execute"}
    ]


def _fields_for_step(cap, step):
    keys = {
        str(item.get("key") or "")
        for item in (step.get("params") or [])
        if item.get("key")
    }
    if not keys:
        return list(cap.get("caller_fields") or [])
    return [field for field in (cap.get("caller_fields") or []) if str(field.get("id") or "") in keys]


def _step_wants_files(cap, step):
    fields = _fields_for_step(cap, step) if step else []
    if any(_is_file_field(field) for field in fields):
        return True
    for param in (step or {}).get("params") or []:
        if _is_file_field(param):
            return True
    return False


def build_request(cap, inputs, user, step=None, step_results=None, identity_cache=None, links=None):
    query = {}
    body = {}
    files = {}
    method = str((step or cap.get("execute") or {}).get("method") or (cap.get("execute") or {}).get("method") or "GET").upper()
    required = []
    inputs = fill_caller_defaults(cap, inputs)
    fields = _fields_for_step(cap, step) if step is not None else list(cap.get("caller_fields") or [])
    for field in fields:
        key = field.get("id")
        value = inputs.get(key)
        if field.get("required") and value in (None, ""):
            required.append(key)
            continue
        if value in (None, ""):
            continue
        if _is_file_field(field):
            files[key] = value
            continue
        if field.get("type") == "array":
            value = _parse_caller_array(field, value)
            if value in (None, "", []):
                if field.get("required"):
                    required.append(key)
                continue
        slot, name = _payload_slot(field.get("path"), method)
        target = query if slot == "query" else body
        _assign_payload(target, name or key, value)
    if required:
        raise RuntimeError(f"缺少必填字段: {', '.join(required)}")
    system = list(cap.get("system_params") or [])
    if step is not None and str((step.get("step_id") or "")) != str((cap.get("execute") or {}).get("step_id") or ""):
        system = [
            item for item in (step.get("params") or [])
            if item.get("exposed_to_user") is False or str(item.get("source_kind") or "") in {
                "current_user", "constant", "generated", "previous_response", "selected_record_identity",
            }
        ]
    for param in system:
        key = str(param.get("key") or "")
        value = _system_value(param, user, inputs=inputs, step_results=step_results, identity_cache=identity_cache, links=links)
        if value is None:
            if param.get("required"):
                raise RuntimeError(f"系统字段 {key} 无法按合同填充（常量缺少 default_value，且不是可推断的运行时字段）")
            continue
        slot, name = _payload_slot(param.get("path"), method)
        target = query if slot == "query" else body
        _assign_payload(target, name or key, value)
    return query, body, files


def list_field_options(capability_id, field_id):
    cap = capability(load_contract(), capability_id)
    for field in cap.get("caller_fields") or []:
        if str(field.get("id") or "") != str(field_id):
            continue
        binding = field.get("dataSource")
        if not binding:
            raise RuntimeError(f"{capability_id}.{field_id} 不是动态选项")
        return client.list_options(binding)
    raise RuntimeError(f"合同没有字段 {capability_id}.{field_id}")


def _is_write(cap):
    kind = str(cap.get("kind") or "")
    if any(token in kind.lower() for token in ("create", "update", "delete", "submit", "write", "mutation")):
        return True
    if any(token in kind.lower() for token in ("query", "read")):
        return False
    method = str((cap.get("execute") or {}).get("method") or "").upper()
    return method in {"POST", "PUT", "PATCH", "DELETE"}


def execute_capability(capability_id, inputs=None, confirm=False, contract=None):
    contract = contract or load_contract()
    cap = capability(contract, capability_id)
    if _is_write(cap) and not confirm:
        raise RuntimeError("写操作需要 --confirm")
    user = {}
    identity_cache = {}
    context = dict(inputs or {})
    step_results = {}
    last = None
    refs = _refs_to_run(cap)
    if not refs:
        refs = [{
            "usage": "execute",
            "method": (cap.get("execute") or {}).get("method"),
            "path": (cap.get("execute") or {}).get("path"),
            "step_id": (cap.get("execute") or {}).get("step_id"),
        }]
    for ref in refs:
        step = _step_by_id(contract, ref.get("step_id"))
        method = str(ref.get("method") or step.get("method") or (cap.get("execute") or {}).get("method") or "GET").upper()
        path = str(ref.get("path") or step.get("path") or (cap.get("execute") or {}).get("path") or "")
        if not path:
            raise RuntimeError(f"{capability_id} 没有执行路径")
        query, body, files = build_request(
            cap,
            context,
            user,
            step=step if step else None,
            step_results=step_results,
            identity_cache=identity_cache,
            links=contract.get("links"),
        )
        if str(ref.get("usage") or "") == "preflight" and _step_wants_files(cap, step) and not files:
            continue
        last = client.http_json(
            method,
            path,
            query=query or None,
            body=None if method == "GET" and not files else (body or None),
            files=files or None,
            content_type="multipart/form-data" if files else "application/json",
        )
        step_id = str(ref.get("step_id") or "")
        if step_id:
            step_results[step_id] = last
            context = apply_links(contract, step_id, last, context)
        if not last.get("ok"):
            return last
    if last is None:
        raise RuntimeError(f"{capability_id} 没有执行路径")
    return last


def run_route(route_id, inputs=None, confirm=False, contract=None):
    contract = contract or load_contract()
    chosen = route(contract, route_id)
    context = dict(inputs or {})
    results = []
    for step in chosen.get("steps") or []:
        cap = capability(contract, step)
        result = execute_capability(
            step,
            context,
            confirm=confirm if _is_write(cap) else False,
            contract=contract,
        )
        results.append({"capability_id": step, "result": result})
        exec_id = str((cap.get("execute") or {}).get("step_id") or "")
        if exec_id:
            context = apply_links(contract, exec_id, result, context)
        if not result.get("ok"):
            return {"ok": False, "error": f"{step} 失败", "results": results}
    return {"ok": True, "route": chosen.get("route_id"), "results": results}
