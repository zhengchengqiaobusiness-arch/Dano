"""Replay captured HTTP requests through Dano's existing request executor."""
from __future__ import annotations

from copy import deepcopy
import json
import re
from time import perf_counter
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from dano.execution.page.flow_spec import _client_redact_sensitive
from dano.execution.page.request_capture import execute_api_request, extract_auth_headers
from dano.execution.page.verification_log import record_verification


_INLINE_SECRET_RE = re.compile(
    r"(?i)\b(Bearer|Basic|Token)\s+[A-Za-z0-9._~+/-]{8,}|\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
)
_SECRET_QUERY_HINTS = ("token", "secret", "password", "authorization", "session", "credential")


def _redact(node):  # noqa: ANN001, ANN202
    node = _client_redact_sensitive(node)
    if isinstance(node, dict):
        return {key: _redact(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_redact(value) for value in node]
    if isinstance(node, str):
        return _INLINE_SECRET_RE.sub("***", node)
    return node


def _redact_url(url: str) -> str:
    parsed = urlparse(url)
    query = [
        (key, "***" if any(hint in key.casefold() for hint in _SECRET_QUERY_HINTS) else value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
    ]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def _deep_merge(original, patch):  # noqa: ANN001, ANN202
    if isinstance(original, dict) and isinstance(patch, dict):
        merged = deepcopy(original)
        for key, value in patch.items():
            merged[key] = _deep_merge(merged.get(key), value)
        return merged
    return deepcopy(patch)


def _request_body(request: dict):  # noqa: ANN202
    raw = request.get("post_data")
    if isinstance(raw, (dict, list)):
        return deepcopy(raw)
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        if "form-urlencoded" in str(request.get("content_type") or "").casefold():
            return dict(parse_qsl(raw, keep_blank_values=True))
        return raw


def _replace_url_path(url: str, path: str, base_url: str) -> str:
    if path.startswith(("http://", "https://")):
        return path
    absolute = url or base_url
    parsed = urlparse(absolute)
    if parsed.scheme and parsed.netloc:
        normalized = "/" + path.lstrip("/")
        return urlunparse(parsed._replace(path=normalized))
    return (base_url or "").rstrip("/") + "/" + path.lstrip("/")


async def replay_request(
    request: dict,
    *,
    overrides: dict | None = None,
    auth_headers: dict,
    base_url: str = "",
    storage_state: dict | None = None,
) -> dict:
    """Replay one captured request and return only redacted evidence."""
    if not isinstance(request, dict):
        raise TypeError("request must be an object")
    if not isinstance(auth_headers, dict):
        raise TypeError("auth_headers must be an object")
    overrides = dict(overrides or {})
    unknown = sorted(set(overrides) - {"url_path", "query", "body", "headers"})
    if unknown:
        raise ValueError(f"unsupported replay overrides: {','.join(unknown)}")

    url = str(request.get("url") or request.get("path") or "")
    if "url_path" in overrides:
        if not isinstance(overrides["url_path"], str):
            raise TypeError("overrides.url_path must be a string")
        url = _replace_url_path(url, overrides["url_path"], base_url)
    parsed = urlparse(url)
    query = request.get("query")
    if not isinstance(query, dict):
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query = _deep_merge(query, overrides.get("query") or {})

    body = _request_body(request)
    if "body" in overrides:
        body = _deep_merge(body if isinstance(body, dict) else {}, overrides.get("body"))
    raw_body = body if isinstance(body, str) else None
    content_type = str(
        request.get("content_type")
        or next((value for key, value in (request.get("headers") or {}).items() if str(key).casefold() == "content-type"), "")
        or "application/json"
    )
    headers = extract_auth_headers(request.get("headers"))
    headers.update(auth_headers)
    headers.update(overrides.get("headers") or {})
    api_request = {
        "method": str(request.get("method") or "GET").upper(),
        "url": url,
        "path": parsed.path or str(request.get("path") or ""),
        "query_template": query,
        "body_template": body if isinstance(body, (dict, list)) else None,
        "raw_body": raw_body,
        "content_type": content_type,
        "auth_headers": headers,
    }
    started = perf_counter()
    result = await execute_api_request(
        api_request,
        {},
        base_url=base_url,
        storage_state=storage_state,
        send=True,
    )
    elapsed_ms = round((perf_counter() - started) * 1000, 3)
    safe_result = _redact({**result, "url": _redact_url(str(result.get("url") or url))})
    subject = {
        "request_id": str(request.get("request_id") or ""),
        "request_index": request.get("index", request.get("request_index")),
        "method": api_request["method"],
        "path": urlparse(url).path,
    }
    verification_id = record_verification(
        kind="replay_read" if api_request["method"] in {"GET", "HEAD"} else "write_execute",
        subject=subject,
        evidence={"elapsed_ms": elapsed_ms, "result": safe_result},
    )
    return {
        "ok": bool(safe_result.get("ok")),
        "status": safe_result.get("status"),
        "response": safe_result.get("response"),
        "elapsed_ms": elapsed_ms,
        "replay_id": verification_id,
        "verification_id": verification_id,
    }


def _response_leaves(node, path: str = ""):  # noqa: ANN001, ANN202
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _response_leaves(value, f"{path}.{key}" if path else str(key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _response_leaves(value, f"{path}[{index}]")
    elif path:
        yield path, node


async def perturb_replay(
    chain: list[dict],
    *,
    perturb: dict,
    auth_headers: dict,
    base_url: str = "",
) -> dict:
    """Replay a chain once, perturbing its first request, and diff responses."""
    if not chain:
        raise ValueError("chain must contain at least one request")
    replays: list[dict] = []
    linked_paths: list[dict] = []
    replay_verification_ids: list[str] = []
    for index, request in enumerate(chain):
        replay = await replay_request(
            request,
            overrides=perturb if index == 0 else None,
            auth_headers=auth_headers,
            base_url=base_url,
        )
        replays.append(replay)
        replay_verification_ids.append(replay["verification_id"])
        before = dict(_response_leaves(request.get("response_json")))
        after = dict(_response_leaves(replay.get("response")))
        for path in sorted(set(before) | set(after)):
            if before.get(path) != after.get(path):
                linked_paths.append({
                    "request_id": str(request.get("request_id") or f"req_{index}"),
                    "path": f"response.{path}",
                    "before": _redact(before.get(path)),
                    "after": _redact(after.get(path)),
                })
    subject = {
        "chain_request_ids": [str(request.get("request_id") or f"req_{index}") for index, request in enumerate(chain)],
        "linked_paths": [{"request_id": item["request_id"], "path": item["path"]} for item in linked_paths],
    }
    verification_id = record_verification(
        kind="perturb_link",
        subject=subject,
        evidence={"replays": replays, "linked_paths": linked_paths},
    )
    return {
        "replays": replays,
        "linked_paths": linked_paths,
        "verification_id": verification_id,
        "verification_ids": [*replay_verification_ids, verification_id],
    }
