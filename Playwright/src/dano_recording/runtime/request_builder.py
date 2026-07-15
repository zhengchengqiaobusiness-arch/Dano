"""Compile one V3 recorded operation into an HTTP request."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
import re
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from .conditions import condition_value
from .safety import RuntimePolicy, safe_credential_headers, safe_headers

# Expressions are parsed as property/index paths below; they are never eval'd.
# Bracket-quoted keys let contracts retain spaces, dots and non-ASCII names.
_WHOLE = re.compile(r"^\{\{\s*([^{}]+?)\s*\}\}$")
_PART = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


@dataclass(slots=True)
class BuiltRequest:
    step_uuid: str
    step_id: str
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    query: dict[str, Any] = field(default_factory=dict)
    json_body: Any = None
    form_body: dict[str, Any] | None = None
    content_type: str = "application/json"


CredentialHeaders = Mapping[str, Any] | Callable[[str], Mapping[str, Any]]


class MissingRuntimeCredential(ValueError):
    pass


def _path_tokens(expression: str) -> list[str | int]:
    if not expression or len(expression) > 2_048:
        raise KeyError(expression)
    tokens: list[str | int] = []
    index = 0
    while index < len(expression):
        if expression[index] == ".":
            index += 1
            if index >= len(expression):
                raise KeyError(expression)
            continue
        if expression[index] == "[":
            end = index + 1
            quoted = False
            escaped = False
            while end < len(expression):
                char = expression[end]
                if escaped:
                    escaped = False
                elif char == "\\" and quoted:
                    escaped = True
                elif char == '"':
                    quoted = not quoted
                elif char == "]" and not quoted:
                    break
                end += 1
            if end >= len(expression):
                raise KeyError(expression)
            raw = expression[index + 1:end].strip()
            if raw.isdigit():
                tokens.append(int(raw))
            elif len(raw) >= 2 and raw[0] == raw[-1] == '"':
                try:
                    value = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise KeyError(expression) from exc
                if not isinstance(value, str):
                    raise KeyError(expression)
                tokens.append(value)
            else:
                raise KeyError(expression)
            index = end + 1
            continue
        end = index
        while end < len(expression) and expression[end] not in ".[":
            end += 1
        token = expression[index:end].strip()
        if not token:
            raise KeyError(expression)
        tokens.append(token)
        index = end
    return tokens


def lookup(context: dict[str, Any], expression: str) -> Any:
    node: Any = context
    for token in _path_tokens(expression):
        if isinstance(node, dict) and token in node:
            node = node[token]
        elif isinstance(node, list) and isinstance(token, int) and 0 <= token < len(node):
            node = node[token]
        else:
            raise KeyError(expression)
    return node


def render(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: render(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [render(item, context) for item in value]
    if not isinstance(value, str):
        return value
    whole = _WHOLE.match(value)
    if whole:
        return deepcopy(lookup(context, whole.group(1)))

    def replace(match: re.Match) -> str:
        found = lookup(context, match.group(1))
        if isinstance(found, (dict, list)):
            return json.dumps(found, ensure_ascii=False, separators=(",", ":"))
        return "" if found is None else str(found)

    return _PART.sub(replace, value)


def render_url(value: str, context: dict[str, Any]) -> str:
    """Render only path components; dynamic values cannot inject `/`, `?` or hosts."""

    def replace(match: re.Match) -> str:
        found = lookup(context, match.group(1))
        if isinstance(found, (dict, list)):
            raise ValueError("URL path fields must be scalar")
        return quote("" if found is None else str(found), safe="-._~")

    return _PART.sub(replace, value)


def _delete_path(target: Any, path: str) -> Any:
    raw = path.removeprefix("body.")
    if raw == "$":
        return None
    if raw.startswith("$."):
        raw = raw[2:]
    elif raw.startswith("$["):
        raw = raw[1:]
    try:
        tokens = _path_tokens(raw)
    except KeyError as exc:
        raise ValueError(f"invalid optional wire path: {path}") from exc
    if not tokens:
        return target
    node = target
    for token in tokens[:-1]:
        if isinstance(token, str) and isinstance(node, dict):
            node = node.get(token)
        elif isinstance(token, int) and isinstance(node, list) and 0 <= token < len(node):
            node = node[token]
        else:
            return target
    final = tokens[-1]
    if isinstance(final, str) and isinstance(node, dict):
        node.pop(final, None)
    elif isinstance(final, int) and isinstance(node, list) and 0 <= final < len(node):
        node.pop(final)
    return target


def _prune_missing_caller_bindings(
    step: dict[str, Any],
    fields: dict[str, Any],
    query_template: Any,
    body_template: Any,
    headers: dict[str, Any],
) -> tuple[Any, Any, dict[str, Any]]:
    contracts = [item for item in step.get("fields") or [] if isinstance(item, dict)]
    field_names = {
        str(item.get("field_uuid") or item.get("field_contract_id") or ""):
        str(item.get("public_name") or "")
        for item in contracts
        if (item.get("field_uuid") or item.get("field_contract_id")) and item.get("public_name")
    }
    query = deepcopy(query_template)
    body = deepcopy(body_template)
    clean_headers = dict(headers)
    for contract in contracts:
        name = str(contract.get("public_name") or "")
        provider = contract.get("source_binding") or contract.get("value_provider") or {}
        provider_kind = str(provider.get("kind") or "").lower() if isinstance(provider, dict) else ""
        if provider_kind not in {"caller", "caller_input", "user_input", "option_source"}:
            continue
        if name in fields:
            continue
        required_contract = contract.get("required_contract") or {}
        wire_state = str(required_contract.get("wire_required") or contract.get("wire_required") or "unknown")
        wire_required = wire_state == "true"
        condition = required_contract.get("wire_condition") if isinstance(required_contract, dict) else None
        if wire_required and condition is not None:
            wire_required = condition_value(condition, fields, field_names)
        if wire_required:
            continue
        location = str(contract.get("location") or "")
        path = str(contract.get("wire_path") or "")
        if location == "query" and isinstance(query, dict):
            query.pop(path, None)
        elif location in {"body", "form"}:
            body = _delete_path(body, path)
        elif location == "header":
            clean_headers.pop(path, None)
        elif location == "path":
            raise ValueError(f"optional path field cannot be omitted safely: {name}")
    return query, body, clean_headers


def _merge_query(url: str, query: dict[str, Any]) -> str:
    parts = urlsplit(url)
    pairs = list(parse_qsl(parts.query, keep_blank_values=True))
    for key, value in query.items():
        if isinstance(value, list):
            pairs.extend((key, item) for item in value)
        elif value is not None:
            pairs.append((key, value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(pairs, doseq=True), parts.fragment))


def build_request(
    step: dict,
    *,
    fields: dict[str, Any],
    outputs: dict[str, Any],
    base_url: str,
    policy: RuntimePolicy,
    credential_headers: CredentialHeaders | None = None,
    runtime_context: dict[str, Any] | None = None,
) -> BuiltRequest:
    context = {
        "input": fields,
        "fields": fields,
        "steps": outputs,
        "runtime": dict(runtime_context or {}),
        **fields,
    }
    method = str(step.get("method") or "GET").upper()
    raw_url = str(step.get("url") or step.get("path") or "")
    url = policy.resolve_url(base_url, render_url(raw_url, context))
    query_template: Any = (
        step["query_template"] if "query_template" in step
        else step.get("query") or {}
    )
    raw_headers = safe_headers(step.get("headers"))
    body_template = step.get("body_template", step.get("body"))
    query_template, body_template, raw_headers = _prune_missing_caller_bindings(
        step,
        fields,
        query_template,
        body_template,
        raw_headers,
    )
    query = render(query_template, context)
    if not isinstance(query, dict):
        raise ValueError("query_template must render to an object")
    headers = render(raw_headers, context)
    if not isinstance(headers, dict):
        raise ValueError("headers must render to an object")
    trusted_headers = credential_headers(url) if callable(credential_headers) else credential_headers
    headers.update(safe_credential_headers(dict(trusted_headers or {})))
    available_headers = {str(key).lower() for key, value in headers.items() if value not in (None, "")}
    missing_credentials = [
        str(name) for name in step.get("required_credential_headers") or []
        if str(name).lower() not in available_headers
    ]
    if missing_credentials:
        raise MissingRuntimeCredential(
            "trusted runtime credentials are unavailable for: "
            + ", ".join(missing_credentials)
        )
    header_content_type = next(
        (value for key, value in headers.items() if key.lower() == "content-type"),
        "",
    )
    content_type = str(step.get("content_type") or header_content_type or "application/json")
    body = render(body_template, context) if body_template is not None else None
    form_body = body if "application/x-www-form-urlencoded" in content_type and isinstance(body, dict) else None
    json_body = None if form_body is not None else body
    return BuiltRequest(
        step_uuid=str(step.get("step_uuid") or ""),
        step_id=str(step.get("step_id") or step.get("operation_id") or ""),
        method=method,
        url=_merge_query(url, query),
        headers=headers,
        query=query,
        json_body=json_body,
        form_body=form_body,
        content_type=content_type,
    )
