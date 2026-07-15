"""Deterministic request classification.

Classification never filters a request.  It assigns exactly one disposition;
the compiler ledger retains the request regardless of that disposition.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

from dano_recording.domain.facts import RequestFact
from dano_recording.domain.operations import RequestAnalysis, RequestDisposition


_RESOURCE_EXTENSIONS = {
    ".avif", ".bmp", ".css", ".eot", ".gif", ".ico", ".jpeg", ".jpg",
    ".map", ".mp3", ".mp4", ".ogg", ".png", ".svg", ".ttf", ".wav",
    ".webm", ".webp", ".woff", ".woff2",
}
_RESOURCE_TYPES = {"font", "image", "media", "stylesheet"}
_IDENTITY_PATH = re.compile(
    r"(?:^|/)(?:auth|login|logout|session|token|current[-_]?user|profile|whoami|me)(?:/|$)",
    re.IGNORECASE,
)
_OPTION_PATH = re.compile(
    r"(?:^|/)(?:options?|dictionaries|dict|enums?|lookups?|choices?|selectors?)(?:/|$)",
    re.IGNORECASE,
)
_MUTATION_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _looks_like_option_rows(value: Any) -> bool:
    if not isinstance(value, list) or not value:
        return False
    if all(not isinstance(item, (dict, list)) for item in value):
        return len(value) <= 500
    if not all(isinstance(item, dict) for item in value):
        return False
    keys = {str(key).lower() for item in value[:20] for key in item}
    return bool(keys & {"label", "name", "title", "text"}) and bool(
        keys & {"value", "id", "code", "key"}
    )


def _has_option_payload(value: Any) -> bool:
    if _looks_like_option_rows(value):
        return True
    if not isinstance(value, dict):
        return False
    for key in ("options", "items", "choices", "records", "list", "data"):
        if key in value and _looks_like_option_rows(value[key]):
            return True
    return False


def classify_request(request: RequestFact) -> RequestAnalysis:
    method = request.method.upper()
    path = request.path
    suffix = PurePosixPath(path.lower()).suffix

    if method == "OPTIONS":
        return RequestAnalysis(
            request_id=request.request_id,
            disposition=RequestDisposition.PREFLIGHT,
            reason="CORS/browser preflight",
        )
    # Mutations take precedence over URL suffix and browser resource labels.
    # A bodyless write or an upload ending in .png is still a business command.
    if method in _MUTATION_METHODS:
        return RequestAnalysis(
            request_id=request.request_id,
            disposition=RequestDisposition.MATERIALIZED,
            reason="state-changing request",
        )
    if request.resource_type.lower() in _RESOURCE_TYPES or suffix in _RESOURCE_EXTENSIONS:
        return RequestAnalysis(
            request_id=request.request_id,
            disposition=RequestDisposition.IGNORED_RESOURCE,
            reason="non-API page resource",
        )
    if _IDENTITY_PATH.search(path):
        return RequestAnalysis(
            request_id=request.request_id,
            disposition=RequestDisposition.IDENTITY,
            reason="identity/session request retained outside public capabilities",
            confidence=0.95,
        )
    if method == "GET" and (_OPTION_PATH.search(path) or _has_option_payload(request.response_body)):
        return RequestAnalysis(
            request_id=request.request_id,
            disposition=RequestDisposition.OPTION_SOURCE,
            reason="enumeration/choice evidence source",
            confidence=0.9,
        )
    # Repeated query keys and blank query values are preserved by RequestFact.
    if request.query_items:
        return RequestAnalysis(
            request_id=request.request_id,
            disposition=RequestDisposition.MATERIALIZED,
            reason="query-bearing business request",
            confidence=0.95,
        )
    if method == "GET" and request.resource_type.lower() in {"fetch", "xhr", "document"}:
        return RequestAnalysis(
            request_id=request.request_id,
            disposition=RequestDisposition.REVIEW_CANDIDATE,
            reason="API-like read retained as an explicit capability candidate",
            confidence=0.75,
        )
    if method in {"HEAD", "TRACE"}:
        return RequestAnalysis(
            request_id=request.request_id,
            disposition=RequestDisposition.SUPPORTING,
            reason="supporting transport request",
            confidence=0.9,
        )
    return RequestAnalysis(
        request_id=request.request_id,
        disposition=RequestDisposition.UNSUPPORTED,
        reason=f"unsupported method/resource combination: {method}/{request.resource_type}",
        confidence=0.6,
    )


def classify_requests(requests: tuple[RequestFact, ...]) -> tuple[RequestAnalysis, ...]:
    return tuple(classify_request(request) for request in requests)
