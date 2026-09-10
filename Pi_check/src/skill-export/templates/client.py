#!/usr/bin/env python3
"""Frozen HTTP + auth. Official API: auth_headers, http_json, list_options. Do not rewrite."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None

CONFIG = json.loads(r'''__CONFIG__''')
ROOT = Path(__file__).resolve().parents[1]
BASE_URL = str(CONFIG.get("base_url") or os.environ.get("DANO_BUSINESS_BASE_URL") or "").rstrip("/")


class AuthExpired(RuntimeError):
    def __init__(self, message="token 已过期，请提供 token"):
        super().__init__(message)
        self.code = "AuthExpired"


def _json_object(raw, label):
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be a JSON object")
    return value


def _usable_headers(raw):
    if not isinstance(raw, dict):
        return {}
    headers = {}
    for key, value in raw.items():
        name = str(key).strip()
        text = str(value).strip()
        if not name or not text:
            continue
        if text.startswith("[sealed:") or text.startswith("****") or "…" in text:
            continue
        headers[name] = text
    return headers


def _local_headers():
    path = ROOT / "config" / "auth.local.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    headers = data.get("headers") if isinstance(data, dict) else {}
    return _usable_headers(headers)


def auth_headers():
    local = _local_headers()
    if local:
        return local
    raw = os.environ.get("DANO_AUTH_HEADERS")
    if raw:
        env_headers = _usable_headers(_json_object(raw, "DANO_AUTH_HEADERS"))
        if env_headers:
            return env_headers
    raise AuthExpired("没有可用 token。请在页面更新凭证，或写入 config/auth.local.json")


def has_auth_headers():
    try:
        return bool(auth_headers())
    except AuthExpired:
        return False


def get_path(node, path):
    text = str(path or "").removeprefix("response.").removeprefix("$.")
    if text in {"", "$", "response"}:
        return node
    tokens = [token for token in re.split(r"\.|\[|\]", text) if token]
    current = node
    for token in tokens:
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            return None
    return current


def _login_expired(status, data):
    if int(status or 0) == 401:
        return True
    text = json.dumps(data, ensure_ascii=False) if not isinstance(data, str) else data
    return "账号未登录" in text or "not login" in text.casefold()


def _business_ok(data, rule):
    if isinstance(data, dict) and rule and rule.get("field") in data:
        return str(data[rule["field"]]) in {str(value) for value in rule.get("ok_values") or []}
    if isinstance(data, dict):
        for key in ("code", "status", "errcode", "errCode", "resultCode"):
            if key in data and not isinstance(data[key], (dict, list)):
                return str(data[key]).casefold() in {"200", "0", "00000", "true", "success", "ok", "1"}
        if "success" in data:
            return bool(data["success"])
    return True


def _request_json(method, target, *, headers, query, body, content_type, timeout=30):
    method = str(method or "GET").upper()
    if httpx is not None:
        kwargs = {"params": query or None, "headers": headers, "timeout": timeout}
        if body is not None:
            if "form-urlencoded" in str(content_type).casefold():
                kwargs["data"] = body
            else:
                kwargs["json"] = body
        response = httpx.request(method, target, **kwargs)
        try:
            data = response.json()
        except ValueError:
            data = response.text
        return int(response.status_code), data, str(response.url), response.is_success

    from urllib.error import HTTPError
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen

    url = target
    if query:
        encoded = urlencode({key: value for key, value in query.items() if value is not None})
        url = f"{target}{'&' if '?' in target else '?'}{encoded}"
    payload = None
    hdrs = dict(headers or {})
    if body is not None:
        if "form-urlencoded" in str(content_type).casefold():
            payload = urlencode(body).encode() if isinstance(body, dict) else str(body).encode()
            hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
        else:
            payload = json.dumps(body, ensure_ascii=False).encode()
            hdrs.setdefault("Content-Type", "application/json")
    req = Request(url, data=payload, headers=hdrs, method=method)
    try:
        with urlopen(req, timeout=timeout) as response:
            raw = response.read()
            status = int(response.getcode() or 0)
            final = str(response.geturl() or url)
    except HTTPError as exc:
        raw = exc.read()
        status = int(exc.code or 0)
        final = url
    text = raw.decode("utf-8", errors="replace")
    try:
        data = json.loads(text)
    except ValueError:
        data = text
    return status, data, final, 200 <= status < 300


def http_json(method, path="", *, url="", query=None, body=None, extra_headers=None, content_type="application/json", success_rule=None):
    target = url or path
    if not str(target).startswith(("http://", "https://")):
        if not BASE_URL:
            raise RuntimeError("缺少 base_url：请填写 config/runtime.json 或 DANO_BUSINESS_BASE_URL")
        target = urljoin(BASE_URL + "/", str(target).lstrip("/"))
    if isinstance(query, dict):
        query = {
            key: json.dumps(value, ensure_ascii=False, separators=(",", ":")) if isinstance(value, (dict, list)) else value
            for key, value in query.items()
        }
    headers = dict(auth_headers())
    if extra_headers:
        for key, value in extra_headers.items():
            if value in (None, ""):
                continue
            headers[str(key)] = str(value)
    status, data, final, success = _request_json(
        method,
        target,
        headers=headers,
        query=query,
        body=body,
        content_type=content_type,
    )
    if _login_expired(status, data):
        raise AuthExpired("token 已过期，请提供 token")
    return {
        "ok": success and _business_ok(data, success_rule),
        "status": status,
        "data": data,
        "method": str(method).upper(),
        "url": final,
    }


def flatten_options(rows, id_field="id", label_field="label", children_field=""):
    options = []
    for item in rows or []:
        if not isinstance(item, dict):
            continue
        ident = item.get(id_field)
        if ident in (None, ""):
            ident = item.get("id")
        if ident in (None, ""):
            kids = item.get(children_field) if children_field else None
            if isinstance(kids, list):
                options.extend(flatten_options(kids, id_field, label_field, children_field))
            continue
        options.append({
            "id": ident,
            "label": item.get(label_field) or item.get("name") or ident,
        })
        kids = item.get(children_field) if children_field else None
        if isinstance(kids, list):
            options.extend(flatten_options(kids, id_field, label_field, children_field))
    return options


def list_options(binding, values=None):
    values = dict(values or {})
    endpoint = str((binding or {}).get("endpoint") or "")
    if not endpoint:
        raise RuntimeError("dataSource.endpoint is required")
    method = str((binding or {}).get("method") or "GET").upper()
    query = dict((binding or {}).get("params") or {})
    search_param = str((binding or {}).get("searchParam") or (binding or {}).get("search_param") or "")
    if search_param and values.get(search_param) not in (None, ""):
        query[search_param] = values[search_param]
    result = http_json(method, endpoint, query=query if method == "GET" else None, body=query if method != "GET" else None)
    rows = get_path(result.get("data"), (binding or {}).get("resultPath") or "data")
    if not isinstance(rows, list):
        rows = []
    options = flatten_options(
        rows,
        str((binding or {}).get("idField") or "id"),
        str((binding or {}).get("labelField") or "label"),
        str((binding or {}).get("childrenField") or ""),
    )
    if not options:
        raise RuntimeError("选项接口没有返回可用候选，不要用录制样本冒充")
    return options


def show_config():
    payload = {
        "tenant": CONFIG.get("tenant") or "",
        "subsystem": CONFIG.get("subsystem") or "",
        "base_url": BASE_URL,
        "has_auth_headers": has_auth_headers(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if "--show-config" in sys.argv:
        try:
            show_config()
        except AuthExpired as exc:
            print(json.dumps({"error": str(exc), "code": "AuthExpired"}, ensure_ascii=False))
            raise SystemExit(2) from exc
        raise SystemExit(0)
    if "--list-options" in sys.argv:
        idx = sys.argv.index("--list-options")
        raw = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else ""
        try:
            binding = json.loads(raw) if raw.startswith("{") else {"endpoint": raw}
            print(json.dumps(list_options(binding), ensure_ascii=False, indent=2))
        except AuthExpired as exc:
            print(json.dumps({"error": str(exc), "code": "AuthExpired"}, ensure_ascii=False))
            raise SystemExit(2) from exc
        raise SystemExit(0)
    print(json.dumps({"error": "use --show-config, --list-options, or import this module"}, ensure_ascii=False))
    raise SystemExit(1)
