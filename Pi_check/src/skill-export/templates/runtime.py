#!/usr/bin/env python3
"""Execute recorded capabilities from references/CONTRACT.json. Do not rewrite."""

from __future__ import annotations

import json
from datetime import datetime, timezone
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


def current_user():
    result = client.http_json("GET", "/admin-api/system/auth/get-permission-info")
    payload = result.get("data")
    if not isinstance(payload, dict):
        raise client.AuthExpired("无法读取当前登录用户")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    user = data.get("user") if isinstance(data.get("user"), dict) else {}
    dept = user.get("dept") if isinstance(user.get("dept"), dict) else {}
    info = {
        "creator": user.get("id") if user.get("id") is not None else user.get("userId"),
        "creatorName": user.get("nickname") or user.get("username") or "",
        "deptId": user.get("deptId") if user.get("deptId") is not None else dept.get("id"),
        "deptName": dept.get("name") or user.get("deptName") or "",
        "companyId": user.get("companyId") if user.get("companyId") is not None else data.get("companyId"),
        "companyName": user.get("companyName") or data.get("companyName") or "",
    }
    if info["creator"] in (None, ""):
        raise client.AuthExpired("当前登录用户缺少 id，请提供 token")
    return info


def _system_value(param, user):
    kind = str(param.get("source_kind") or "")
    key = str(param.get("key") or "")
    if kind == "current_user":
        if key in user and user[key] not in (None, ""):
            return user[key]
        raise client.AuthExpired(f"当前登录用户缺少 {key}")
    if "default_value" in param:
        return param.get("default_value")
    if kind == "constant" and param.get("type") == "array":
        return []
    if key == "createTime":
        now = datetime.now(timezone.utc)
        return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
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


def _rows_from_lines(field, text):
    cols = list((field.get("itemProperties") or {}).keys()) or ["content"]
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
    return _assemble_items(field, cleaned)


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


def build_request(cap, inputs, user):
    query = {}
    body = {}
    method = str((cap.get("execute") or {}).get("method") or "GET").upper()
    required = []
    for field in cap.get("caller_fields") or []:
        key = field.get("id")
        value = inputs.get(key)
        if field.get("required") and value in (None, ""):
            required.append(key)
            continue
        if value in (None, ""):
            continue
        if field.get("type") == "array":
            value = _parse_caller_array(field, value)
            if value in (None, "", []):
                if field.get("required"):
                    required.append(key)
                continue
        slot, name = _payload_slot(field.get("path"), method)
        target = query if slot == "query" else body
        target[name or key] = value
    if required:
        raise RuntimeError(f"缺少必填字段: {', '.join(required)}")
    for param in cap.get("system_params") or []:
        key = str(param.get("key") or "")
        value = _system_value(param, user)
        if value is None:
            if param.get("required"):
                raise RuntimeError(f"系统字段 {key} 无法按合同填充（常量缺少 default_value，且不是可推断的运行时字段）")
            continue
        slot, name = _payload_slot(param.get("path"), method)
        target = query if slot == "query" else body
        target[name or key] = value
    return query, body


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
    return any(token in str(cap.get("kind") or "") for token in ("create", "update", "delete", "submit", "write"))


def execute_capability(capability_id, inputs=None, confirm=False):
    cap = capability(load_contract(), capability_id)
    if _is_write(cap) and not confirm:
        raise RuntimeError("写操作需要 --confirm")
    needs_user = any(str(item.get("source_kind") or "") == "current_user" for item in cap.get("system_params") or [])
    user = current_user() if needs_user else {}
    query, body = build_request(cap, dict(inputs or {}), user)
    exec_ref = cap.get("execute") or {}
    method = str(exec_ref.get("method") or "GET").upper()
    path = str(exec_ref.get("path") or "")
    if not path:
        raise RuntimeError(f"{capability_id} 没有执行路径")
    extra = None
    if user.get("deptId") not in (None, ""):
        extra = {"current-dept-id": str(user["deptId"])}
    return client.http_json(
        method,
        path,
        query=query or None,
        body=body if method != "GET" else None,
        extra_headers=extra,
    )


def run_route(route_id, inputs=None, confirm=False):
    contract = load_contract()
    chosen = route(contract, route_id)
    context = dict(inputs or {})
    results = []
    for step in chosen.get("steps") or []:
        cap = capability(contract, step)
        result = execute_capability(step, context, confirm=confirm if _is_write(cap) else False)
        results.append({"capability_id": step, "result": result})
        if not result.get("ok"):
            return {"ok": False, "error": f"{step} 失败", "results": results}
    return {"ok": True, "route": chosen.get("route_id"), "results": results}
