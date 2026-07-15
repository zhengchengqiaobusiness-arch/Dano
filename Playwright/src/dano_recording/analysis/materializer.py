"""Losslessly project request facts into the compiler ledger."""

from __future__ import annotations

from typing import Any

from dano_recording.domain.facts import ActionTransaction, RequestFact
from dano_recording.domain.operations import (
    CompiledRequest,
    RequestAnalysis,
    RequestDisposition,
)


_CAPABILITY_DISPOSITIONS = {
    RequestDisposition.MATERIALIZED,
    RequestDisposition.REVIEW_CANDIDATE,
}


def infer_json_schema(value: Any, *, _depth: int = 0) -> dict[str, Any]:
    """Infer a bounded structural response schema from observed JSON."""

    if _depth >= 12:
        return {}
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int) and not isinstance(value, bool):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, list):
        observed = [infer_json_schema(item, _depth=_depth + 1) for item in value[:20]]
        unique: list[dict[str, Any]] = []
        for schema in observed:
            if schema not in unique:
                unique.append(schema)
        items = unique[0] if len(unique) == 1 else ({"anyOf": unique} if unique else {})
        return {"type": "array", "items": items}
    if isinstance(value, dict):
        return {
            "type": "object",
            "properties": {
                str(key): infer_json_schema(item, _depth=_depth + 1)
                for key, item in value.items()
            },
        }
    return {}


def materialize_requests(
    requests: tuple[RequestFact, ...],
    analyses: tuple[RequestAnalysis, ...],
    transactions: tuple[ActionTransaction, ...],
) -> tuple[CompiledRequest, ...]:
    analysis_by_id = {analysis.request_id: analysis for analysis in analyses}
    transaction_by_request = {
        request_id: transaction.transaction_id
        for transaction in transactions
        for request_id in transaction.request_ids
    }
    result: list[CompiledRequest] = []
    for request in sorted(requests, key=lambda item: (item.sequence, item.request_id)):
        analysis = analysis_by_id.get(request.request_id)
        if analysis is None:
            raise ValueError(f"request {request.request_id} has no disposition")
        transaction_id = transaction_by_request.get(request.request_id)
        if transaction_id is None:
            raise ValueError(f"request {request.request_id} has no action transaction")
        result.append(CompiledRequest(
            tenant=request.tenant,
            recording_id=request.recording_id,
            request_id=request.request_id,
            transaction_id=transaction_id,
            sequence=request.sequence,
            method=request.method,
            url=request.url,
            path=request.path,
            query=request.query_items,
            headers=dict(request.request_headers),
            body=request.request_body,
            body_present=request.request_body_present,
            response_status=request.response_status,
            response_body=request.response_body,
            response_schema=(
                infer_json_schema(request.response_body)
                if request.response_body is not None else None
            ),
            disposition=analysis.disposition,
            disposition_reason=analysis.reason,
            capability_eligible=analysis.disposition in _CAPABILITY_DISPOSITIONS,
        ))
    return tuple(result)
