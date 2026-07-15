"""Correlate immutable request/response/failure facts into a read-only projection."""

from __future__ import annotations

from dano_recording.domain.facts import FactKind, RecordingFact, RequestFact


def correlate_request_lifecycle(
    facts: tuple[RecordingFact, ...],
) -> tuple[RequestFact, ...]:
    """Return enriched request copies without mutating captured facts."""

    ordered = sorted(facts, key=lambda fact: (fact.sequence, fact.fact_id))
    requests = {
        fact.request_id: fact
        for fact in ordered
        if isinstance(fact, RequestFact)
    }
    for fact in ordered:
        if fact.kind not in {FactKind.RESPONSE, FactKind.REQUEST_FAILED}:
            continue
        request_id = str(fact.payload.get("request_id") or "")
        request = requests.get(request_id)
        if request is None:
            # The validator will still retain the orphan fact in persistence;
            # no synthetic request is fabricated without method/URL evidence.
            continue
        if fact.kind is FactKind.RESPONSE:
            requests[request_id] = request.model_copy(update={
                "response_status": fact.payload.get("status"),
                "response_headers": fact.payload.get("headers") or {},
                "response_body": fact.payload.get("body")
                    if fact.payload.get("body_present") else None,
            }, deep=True)
        else:
            requests[request_id] = request.model_copy(update={
                "failed_reason": str(fact.payload.get("reason") or "request failed"),
            }, deep=True)
    return tuple(sorted(requests.values(), key=lambda item: (item.sequence, item.request_id)))
