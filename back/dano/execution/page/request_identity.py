"""Shared composite request identity matching for recording stages 5 and 6."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, TypeVar
from urllib.parse import parse_qs, urlparse


T = TypeVar("T")


def normalized_request_path(url_or_path: Any) -> str:
    raw = str(url_or_path or "").strip()
    if not raw:
        return ""
    path = urlparse(raw).path if "://" in raw else raw.split("?", 1)[0]
    path = (path or raw.split("?", 1)[0]).strip()
    if path and not path.startswith("/"):
        path = "/" + path
    return path.rstrip("/") or path


def request_query_signature(
    url_or_query: str | dict | None,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if isinstance(url_or_query, dict):
        items = {
            str(key): tuple(str(item) for item in (value if isinstance(value, list) else [value]))
            for key, value in url_or_query.items()
        }
        return tuple(sorted((key, items[key]) for key in items))
    raw = str(url_or_query or "")
    query = raw.split("?", 1)[1] if "?" in raw else urlparse(raw).query
    parsed = parse_qs(query, keep_blank_values=True) if query else {}
    return tuple(sorted((key, tuple(values)) for key, values in parsed.items()))


def request_body_signature(value: Any) -> str:
    if value in (None, "", {}, []):
        return ""
    if isinstance(value, str):
        text = value.strip()
        if text[:1] in {"{", "["}:
            try:
                value = json.loads(text)
            except (TypeError, ValueError):
                pass
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def request_identity_is_strong(source: dict[str, Any]) -> bool:
    """A route alone is never an occurrence identity."""
    if str(source.get("request_id") or source.get("source_request_id") or ""):
        return True
    if str(source.get("step_id") or source.get("source_step_id") or ""):
        return True
    url = str(source.get("source_url") or source.get("url") or source.get("path") or "")
    method = str(source.get("source_method") or source.get("method") or "")
    if not normalized_request_path(url) or not method:
        return False
    return any(
        source.get(key) not in (None, "", {}, [])
        for key in (
            "page_id", "frame_id", "transaction_id", "trigger_transaction_id",
            "source_transaction_id", "action_id", "trigger_action_id",
            "source_action_id", "query", "body", "source_body", "content_type",
            "source_content_type", "request_index", "index", "sequence",
        )
    ) or "?" in url


def request_composite_signature(source: dict[str, Any]) -> str:
    if not request_identity_is_strong(source):
        return ""
    request_id = str(source.get("request_id") or source.get("source_request_id") or "")
    if request_id:
        return f"id:{request_id}"
    payload = {
        "method": str(source.get("source_method") or source.get("method") or "").upper(),
        "path": normalized_request_path(
            source.get("source_url") or source.get("url") or source.get("path")
        ),
        "host": urlparse(str(source.get("source_url") or source.get("url") or "")).netloc.casefold(),
        "page_id": str(source.get("page_id") or ""),
        "frame_id": str(source.get("frame_id") or ""),
        "transaction_id": str(source.get("transaction_id") or source.get("trigger_transaction_id") or source.get("source_transaction_id") or ""),
        "action_id": str(source.get("action_id") or source.get("trigger_action_id") or source.get("source_action_id") or ""),
        "query": request_query_signature(
            source.get("query") if "query" in source else source.get("source_url") or source.get("url")
        ),
        "body": request_body_signature(
            source.get("source_body") if "source_body" in source else source.get("body", source.get("post_data"))
        ),
        "content_type": str(source.get("source_content_type") or source.get("content_type") or "").casefold(),
        "request_index": source.get("request_index", source.get("index", source.get("sequence"))),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return "composite:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def request_identity_matches(source: dict[str, Any], candidate: dict[str, Any]) -> bool:
    """Return true only when every identity component supplied by source matches."""
    if not request_identity_is_strong(source):
        return False
    expected_request = str(source.get("request_id") or source.get("source_request_id") or "")
    if expected_request and expected_request != str(candidate.get("request_id") or ""):
        return False
    expected_step = str(source.get("step_id") or source.get("source_step_id") or "")
    if expected_step and expected_step != str(candidate.get("step_id") or ""):
        return False

    source_url = str(source.get("source_url") or source.get("url") or source.get("path") or "")
    candidate_url = str(candidate.get("url") or candidate.get("path") or "")
    if source_url:
        source_path = normalized_request_path(source_url)
        if not source_path or source_path != normalized_request_path(candidate_url):
            return False
        source_host = urlparse(source_url).netloc.casefold() if "://" in source_url else ""
        candidate_host = (
            urlparse(candidate_url).netloc.casefold()
            if "://" in candidate_url
            else str(candidate.get("host") or "").casefold()
        )
        if source_host and source_host != candidate_host:
            return False

    source_method = str(source.get("source_method") or source.get("method") or "").upper()
    if source_method and source_method != str(candidate.get("method") or "GET").upper():
        return False
    for key in ("page_id", "frame_id"):
        expected = str(source.get(key) or "")
        if expected and expected != str(candidate.get(key) or ""):
            return False
    expected_transaction = str(
        source.get("transaction_id")
        or source.get("trigger_transaction_id")
        or source.get("source_transaction_id")
        or ""
    )
    candidate_transaction = str(
        candidate.get("transaction_id") or candidate.get("trigger_transaction_id") or ""
    )
    if expected_transaction and expected_transaction != candidate_transaction:
        return False
    expected_action = str(
        source.get("action_id") or source.get("trigger_action_id") or source.get("source_action_id") or ""
    )
    candidate_action = str(candidate.get("action_id") or candidate.get("trigger_action_id") or "")
    if expected_action and expected_action != candidate_action:
        return False

    query_provided = "?" in source_url or "query" in source
    source_query = source_url if "?" in source_url else source.get("query")
    candidate_query = candidate.get("query") if "query" in candidate else candidate_url
    if query_provided and request_query_signature(source_query) != request_query_signature(candidate_query):
        return False
    body_key = "source_body" if "source_body" in source else "body"
    if body_key in source and source.get(body_key) is not None:
        candidate_body = candidate.get("body", candidate.get("post_data"))
        if request_body_signature(source.get(body_key)) != request_body_signature(candidate_body):
            return False
    source_content_type = str(source.get("source_content_type") or source.get("content_type") or "")
    if source_content_type and source_content_type.casefold() != str(candidate.get("content_type") or "").casefold():
        return False
    return True


def unique_request_identity_match(
    source: dict[str, Any],
    candidates: Iterable[tuple[T, dict[str, Any]]],
) -> T | None:
    if not request_identity_is_strong(source):
        return None
    matches = [item for item, identity in candidates if request_identity_matches(source, identity)]
    return matches[0] if len(matches) == 1 else None
