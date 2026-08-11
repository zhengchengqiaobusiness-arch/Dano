"""Replay captured HTTP requests through Dano's existing request executor."""
from __future__ import annotations

from copy import deepcopy
import asyncio
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


def _application_ok(response: object) -> bool:
    if not isinstance(response, dict):
        return True
    if isinstance(response.get("success"), bool):
        return bool(response["success"])
    if isinstance(response.get("ok"), bool):
        return bool(response["ok"])
    for key in ("code", "statusCode", "status_code"):
        if key not in response:
            continue
        value = response.get(key)
        return value in {0, 200, "0", "200", "OK", "ok", "success", "SUCCESS"}
    return True


def _replay_outcome(result: dict, *, execution_error: str = "") -> tuple[str, bool, str]:
    status_code = result.get("status")
    try:
        numeric_status = int(status_code) if status_code is not None else None
    except (TypeError, ValueError):
        numeric_status = None
    application_ok = _application_ok(result.get("response"))
    if execution_error:
        return "inconclusive", False, execution_error
    if numeric_status is not None and numeric_status >= 400:
        return "failed", False, f"HTTP {numeric_status}"
    if not application_ok:
        return "failed", False, "application result indicates failure"
    if not bool(result.get("ok")):
        reason = str(result.get("error") or "").strip()
        return (
            "failed" if numeric_status is not None else "inconclusive",
            False,
            reason or (f"HTTP {numeric_status}" if numeric_status is not None else "request result is unavailable"),
        )
    return "passed", True, ""


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
    execution_error = ""
    try:
        result = await execute_api_request(
            api_request,
            {},
            base_url=base_url,
            storage_state=storage_state,
            send=True,
        )
    except Exception as exc:  # noqa: BLE001
        execution_error = f"{type(exc).__name__}: {exc}"
        result = {"ok": False, "status": None, "response": None, "error": execution_error, "url": url}
    elapsed_ms = round((perf_counter() - started) * 1000, 3)
    safe_result = _redact({**result, "url": _redact_url(str(result.get("url") or url))})
    subject = {
        "request_id": str(request.get("request_id") or ""),
        "request_index": request.get("index", request.get("request_index")),
        "method": api_request["method"],
        "path": urlparse(url).path,
    }
    verification_status, passed, failure_reason = _replay_outcome(
        safe_result, execution_error=execution_error,
    )
    verification_id = record_verification(
        kind="replay_read" if api_request["method"] in {"GET", "HEAD"} else "write_execute",
        subject=subject,
        status=verification_status,
        evidence={
            "elapsed_ms": elapsed_ms,
            "status_code": safe_result.get("status"),
            "application_ok": _application_ok(safe_result.get("response")),
            "result": safe_result,
        },
        failure_reason=failure_reason,
    )
    return {
        "ok": passed,
        "status": safe_result.get("status"),
        "application_ok": _application_ok(safe_result.get("response")),
        "verification_status": verification_status,
        "failure_reason": failure_reason,
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
    replay_statuses = [str(item.get("verification_status") or "inconclusive") for item in replays]
    verification_status = (
        "failed" if "failed" in replay_statuses
        else "inconclusive" if "inconclusive" in replay_statuses
        else "passed"
    )
    verification_id = record_verification(
        kind="perturb_link",
        subject=subject,
        status=verification_status,
        evidence={"replays": replays, "linked_paths": linked_paths},
        failure_reason=(
            "one or more replay requests failed"
            if verification_status == "failed"
            else "one or more replay requests were inconclusive"
            if verification_status == "inconclusive"
            else ""
        ),
    )
    return {
        "replays": replays,
        "linked_paths": linked_paths,
        "verification_id": verification_id,
        "verification_ids": [*replay_verification_ids, verification_id],
    }


def _set_nested_value(target: dict, path: str, value: object) -> dict:
    updated = deepcopy(target)
    tokens = re.findall(r"[^.\[\]]+", str(path or ""))
    if not tokens:
        raise ValueError("target wire path is empty")
    current: object = updated
    for token in tokens[:-1]:
        if not isinstance(current, dict):
            raise ValueError("target wire path crosses a non-object value")
        child = current.get(token)
        if not isinstance(child, dict):
            child = {}
            current[token] = child
        current = child
    if not isinstance(current, dict):
        raise ValueError("target wire path parent is not an object")
    current[tokens[-1]] = deepcopy(value)
    return updated


def _dependency_override(request: dict, wire_path: str, value: object) -> dict:
    path = str(wire_path or "").removeprefix("request.")
    if path.startswith("body."):
        return {"body": _set_nested_value({}, path.removeprefix("body."), value)}
    if path.startswith("query."):
        return {"query": {path.removeprefix("query."): deepcopy(value)}}
    if path.startswith("headers."):
        return {"headers": {path.removeprefix("headers."): deepcopy(value)}}
    match = re.fullmatch(r"url_path\[(\d+)\]", path)
    if match:
        parsed = urlparse(str(request.get("url") or request.get("path") or ""))
        parts = [part for part in parsed.path.split("/") if part]
        index = int(match.group(1))
        if index >= len(parts):
            raise ValueError("url_path target index is outside the captured path")
        parts[index] = str(value)
        return {"url_path": "/" + "/".join(parts)}
    raise ValueError(f"unsupported dependency target wire path: {path}")


async def verify_dependency(
    spec,
    link_id: str,
    captured_requests: list[dict],
    *,
    auth_headers: dict,
    base_url: str = "",
    storage_state: dict | None = None,
) -> dict:
    """Execute one proposed FlowLink without accepting model-supplied paths."""
    link = next((item for item in spec.links if item.link_id == str(link_id or "")), None)
    if link is None:
        raise ValueError("dependency link does not exist")
    from dano.execution.page.recording_live import dependency_link_signature

    step_by_id = {step.step_id: step for step in spec.steps}
    source_step = step_by_id.get(link.source_step_id)
    target_step = step_by_id.get(link.target_step_id)
    source_request_id = str(
        (link.evidence or {}).get("source_request_id")
        or ((source_step.source_meta if source_step else {}) or {}).get("request_id")
        or ""
    )
    target_request_id = str(
        (link.evidence or {}).get("target_request_id")
        or ((target_step.source_meta if target_step else {}) or {}).get("request_id")
        or ""
    )
    request_by_id = {
        str(item.get("request_id") or ""): item
        for item in captured_requests if isinstance(item, dict)
    }
    source_request = request_by_id.get(source_request_id)
    target_request = request_by_id.get(target_request_id)
    if source_request is None or target_request is None:
        raise ValueError("dependency endpoints are not present in captured request facts")

    link_kind = str((link.meta or {}).get("kind") or "value")
    signature = dependency_link_signature(link)
    subject = {
        "link_id": link.link_id,
        "signature": signature,
        "kind": link_kind,
        "source_request_id": source_request_id,
        "target_request_id": target_request_id,
    }
    source = await replay_request(
        source_request,
        auth_headers=auth_headers,
        base_url=base_url,
        storage_state=storage_state,
    )
    verification_ids = [source["verification_id"]]
    failure_status = str(source.get("verification_status") or "inconclusive")
    failure_reason = str(source.get("failure_reason") or "source replay did not pass")
    evidence: dict = {"source": source, "target": None}
    if source.get("verification_status") == "passed":
        try:
            if link_kind in {"structure", "response_key_map"}:
                meta = dict(link.meta or {})
                collection_path = str(meta.get("source_collection_path") or link.source_path or "").removeprefix("response.")
                collection = _assertion_value(source.get("response"), collection_path)
                if not isinstance(collection, list) or not collection:
                    raise ValueError("structure source collection is missing or empty")
                key_path = str(meta.get("source_key_path") or "id")
                keys = [_assertion_value(item, key_path) for item in collection]
                if any(value in (None, "") for value in keys) or len(set(map(str, keys))) != len(keys):
                    raise ValueError("structure source keys are missing or duplicated")
                container_path = str(meta.get("target_container_path") or link.target_path or "")
                stored_body = _request_body(target_request)
                recorded_container = _assertion_value(
                    stored_body,
                    container_path.removeprefix("request.").removeprefix("body."),
                )
                if not isinstance(recorded_container, dict) or len(recorded_container) != len(keys):
                    raise ValueError("dynamic key count does not match target value slot count")
                slots = list(recorded_container.values())
                injected_value = {str(key): deepcopy(slots[index]) for index, key in enumerate(keys)}
                override = _dependency_override(target_request, container_path, injected_value)
                evidence.update({
                    "source_collection_path": collection_path,
                    "source_keys": [str(value) for value in keys],
                    "target_value_slots": len(slots),
                    "injected_value": injected_value,
                })
            else:
                source_path = str(link.source_path or "").removeprefix("response.")
                extracted_value = _assertion_value(source.get("response"), source_path)
                if extracted_value is None:
                    raise ValueError("dependency source_path did not resolve in the replay response")
                override = _dependency_override(target_request, str(link.target_path or ""), extracted_value)
                injected_value = deepcopy(extracted_value)
                evidence.update({
                    "source_path": source_path,
                    "extracted_value": _redact(extracted_value),
                    "injected_value": _redact(injected_value),
                    "injection_equal": injected_value == extracted_value,
                })
            target = await replay_request(
                target_request,
                overrides=override,
                auth_headers=auth_headers,
                base_url=base_url,
                storage_state=storage_state,
            )
            verification_ids.append(target["verification_id"])
            evidence["target"] = target
            failure_status = str(target.get("verification_status") or "inconclusive")
            failure_reason = str(target.get("failure_reason") or "target replay did not pass")
            if target.get("verification_status") == "passed":
                failure_status = "passed"
                failure_reason = ""
        except ValueError as exc:
            failure_status = "failed"
            failure_reason = str(exc)
    status = failure_status if failure_status in {"passed", "failed", "inconclusive"} else "inconclusive"
    verification_id = record_verification(
        kind="dependency_execute",
        subject=subject,
        status=status,
        evidence=evidence,
        failure_reason=failure_reason,
    )
    return {
        "ok": status == "passed",
        "status": status,
        "failure_reason": failure_reason,
        "link_id": link.link_id,
        "signature": signature,
        "verification_id": verification_id,
        "verification_ids": [*verification_ids, verification_id],
        "evidence": evidence,
    }


def _assertion_value(response: object, path: str):  # noqa: ANN202
    current = response
    for token in re.findall(r"[^.\[\]]+", str(path or "")):
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            return None
    return current


_ASSERTION_KEYS = frozenset({
    "path", "response_path", "operator", "equals", "value",
    "equals_input", "input_path", "verify_records_min_count",
    "collection_path", "where", "min_matches",
})
_RECORD_COLLECTION_KEYS = ("list", "records", "items", "rows", "content")


def _record_count(value: object) -> int:
    if isinstance(value, list):
        return len(value)
    if not isinstance(value, dict):
        raise ValueError("verify_records_min_count target is not a record collection")
    for key in _RECORD_COLLECTION_KEYS:
        collection = value.get(key)
        if isinstance(collection, list):
            return len(collection)
    for wrapper in ("data", "result"):
        nested = value.get(wrapper)
        if isinstance(nested, (dict, list)):
            try:
                return _record_count(nested)
            except ValueError:
                pass
    raise ValueError("verify_records_min_count could not find a concrete list/records/items/rows/content collection")


def _validate_assertion_contract(assertion: dict) -> None:
    if not isinstance(assertion, dict) or not assertion:
        raise ValueError("assertion must be a non-empty object")
    unknown = sorted(set(assertion) - _ASSERTION_KEYS)
    if unknown:
        raise ValueError(f"unsupported assertion keys: {', '.join(unknown)}")
    if "collection_path" in assertion or "where" in assertion or "min_matches" in assertion:
        if not str(assertion.get("collection_path") or ""):
            raise ValueError("collection assertion requires collection_path")
        where = assertion.get("where")
        if not isinstance(where, dict) or not where:
            raise ValueError("collection assertion requires a non-empty where object")
        minimum = assertion.get("min_matches")
        if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1:
            raise ValueError("collection assertion min_matches must be a positive integer")
        incompatible = set(assertion) & {
            "path", "response_path", "operator", "equals", "value",
            "equals_input", "input_path", "verify_records_min_count",
        }
        if incompatible:
            raise ValueError("collection assertion cannot be combined with scalar assertion operators")
        for field_path, condition in where.items():
            if not str(field_path or "") or not isinstance(condition, dict):
                raise ValueError("collection assertion where entries must be field-path objects")
            if set(condition) not in ({"equals_input"}, {"equals"}):
                raise ValueError("collection assertion conditions support only equals_input or equals")
        return
    operator = str(assertion.get("operator") or "")
    if operator and operator not in {"equals", "eq", "not_equals", "ne", "contains", "exists", "truthy"}:
        raise ValueError(f"unsupported assertion operator: {operator}")


def evaluate_assertion(response: object, assertion: dict, inputs: dict) -> dict:
    """Evaluate the small deterministic assertion contract used by write verification."""
    _validate_assertion_contract(assertion)
    if "collection_path" in assertion:
        collection_path = str(assertion["collection_path"])
        collection = _assertion_value(response, collection_path)
        if not isinstance(collection, list):
            raise ValueError("collection_path does not resolve to a list")
        where = dict(assertion["where"])
        matches = 0
        for record in collection:
            if not isinstance(record, dict):
                continue
            matched = True
            for field_path, condition in where.items():
                actual_value = _assertion_value(record, str(field_path))
                expected_value = (
                    _assertion_value(inputs, str(condition["equals_input"]))
                    if "equals_input" in condition
                    else condition.get("equals")
                )
                if actual_value != expected_value:
                    matched = False
                    break
            if matched:
                matches += 1
        minimum = int(assertion["min_matches"])
        return {
            "passed": matches >= minimum,
            "collection_path": collection_path,
            "operator": "collection_where",
            "actual": matches,
            "expected": minimum,
            "where": deepcopy(where),
        }
    path = str(assertion.get("path") or assertion.get("response_path") or "")
    actual = _assertion_value(response, path) if path else response
    if "verify_records_min_count" in assertion:
        minimum = assertion["verify_records_min_count"]
        if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 0:
            raise ValueError("verify_records_min_count must be a non-negative integer")
        incompatible = set(assertion) & {
            "operator", "equals", "value", "equals_input", "input_path",
        }
        if incompatible:
            raise ValueError("verify_records_min_count cannot be combined with other assertion operators")
        count = _record_count(actual)
        return {
            "passed": count >= minimum,
            "path": path,
            "operator": "records_min_count",
            "actual": count,
            "expected": minimum,
        }
    expected = assertion.get("equals", assertion.get("value"))
    input_path = str(assertion.get("equals_input") or assertion.get("input_path") or "")
    if input_path:
        expected = _assertion_value(inputs, input_path)
    operator = str(assertion.get("operator") or ("equals" if "equals" in assertion or "value" in assertion or input_path else "truthy"))
    if operator in {"equals", "eq"}:
        passed = actual == expected
    elif operator in {"not_equals", "ne"}:
        passed = actual != expected
    elif operator == "contains":
        passed = expected in actual if isinstance(actual, (str, list, tuple, set, dict)) else False
    elif operator == "exists":
        passed = actual is not None
    elif operator == "truthy":
        passed = bool(actual)
    else:
        raise ValueError(f"unsupported assertion operator: {operator}")
    return {
        "passed": bool(passed),
        "path": path,
        "operator": operator,
        "actual": _redact(actual),
        "expected": _redact(expected),
    }


async def execute_write_with_verify(
    write_request: dict,
    verify_request: dict,
    *,
    write_step_id: str,
    inputs: dict,
    assertion: dict,
    auth_headers: dict,
    cleanup_request: dict | None = None,
    base_url: str = "",
    storage_state: dict | None = None,
    settle_ms: int = 250,
) -> dict:
    """Execute a real write, read it back, assert the result, then optionally clean up."""
    # Reject an unknown assertion before the irreversible write request.
    _validate_assertion_contract(assertion)
    write = await replay_request(
        write_request,
        overrides={"body": inputs} if inputs else None,
        auth_headers=auth_headers,
        base_url=base_url,
        storage_state=storage_state,
    )
    subject = {
        "write_step_id": str(write_step_id),
        "write_request_id": str(write_request.get("request_id") or ""),
        "verify_request_id": str(verify_request.get("request_id") or ""),
        "cleanup_request_id": str((cleanup_request or {}).get("request_id") or ""),
        "assertion": deepcopy(assertion),
    }
    if write.get("verification_status") != "passed":
        status = str(write.get("verification_status") or "inconclusive")
        verification_id = record_verification(
            kind="write_execute",
            subject=subject,
            status=status if status in {"failed", "inconclusive"} else "failed",
            evidence={"passed": False, "write": write, "verify": None, "assertion": None, "cleanup": None},
            failure_reason=str(write.get("failure_reason") or "write request failed"),
        )
        return {
            "ok": False,
            "write": write,
            "verify": None,
            "assertion": None,
            "cleanup": None,
            "verification_id": verification_id,
            "verification_ids": [write["verification_id"], verification_id],
            "verify_verification_id": "",
        }
    if settle_ms:
        await asyncio.sleep(max(0, min(int(settle_ms), 5000)) / 1000)
    cleanup = None
    verify = None
    check = None
    verify_read_id = ""
    verification_status = "inconclusive"
    failure_reason = ""
    try:
        verify = await replay_request(
            verify_request,
            auth_headers=auth_headers,
            base_url=base_url,
            storage_state=storage_state,
        )
        if verify.get("verification_status") != "passed":
            verification_status = str(verify.get("verification_status") or "inconclusive")
            failure_reason = str(verify.get("failure_reason") or "verify request failed")
            check = {"passed": False, "reason": failure_reason}
        else:
            try:
                check = evaluate_assertion(verify.get("response"), assertion, inputs)
            except ValueError as exc:
                check = {"passed": False, "reason": str(exc)}
            verification_status = "passed" if check["passed"] else "failed"
            failure_reason = "" if check["passed"] else str(check.get("reason") or "read-back assertion failed")
        verify_read_id = record_verification(
            kind="verify_read",
            subject={
                "write_step_id": str(write_step_id),
                "verify_request_id": str(verify_request.get("request_id") or ""),
                "assertion": deepcopy(assertion),
            },
            status=verification_status,
            evidence={"passed": bool(verify.get("ok") and check["passed"]), "verify": verify, "assertion": check},
            failure_reason=failure_reason,
        )
    finally:
        if cleanup_request is not None:
            cleanup = await replay_request(
                cleanup_request,
                auth_headers=auth_headers,
                base_url=base_url,
                storage_state=storage_state,
            )
    verification_id = record_verification(
        kind="write_execute",
        subject=subject,
        status=verification_status,
        evidence={"passed": bool(write.get("ok") and verify.get("ok") and check["passed"]), "write": write, "verify": verify, "assertion": check, "cleanup": cleanup},
        failure_reason=failure_reason,
    )
    return {
        "ok": verification_status == "passed",
        "write": write,
        "verify": verify,
        "assertion": check,
        "cleanup": cleanup,
        "verification_id": verification_id,
        "verification_ids": [
            write["verification_id"],
            verify["verification_id"],
            verify_read_id,
            *([cleanup["verification_id"]] if cleanup else []),
            verification_id,
        ],
        "verify_verification_id": verify_read_id,
    }
