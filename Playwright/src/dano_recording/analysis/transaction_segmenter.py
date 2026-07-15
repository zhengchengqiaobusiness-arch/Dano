"""Group requests by user action without inferring workflow from adjacency."""

from __future__ import annotations

import hashlib

from dano_recording.domain.facts import ActionFact, ActionTransaction, RecordingFact, RequestFact


def _transaction_id(recording_id: str, group_key: str) -> str:
    digest = hashlib.sha256(f"{recording_id}:{group_key}".encode()).hexdigest()[:16]
    return f"txn_{digest}"


def segment_transactions(facts: tuple[RecordingFact, ...]) -> tuple[ActionTransaction, ...]:
    actions = {
        fact.action_id: fact
        for fact in facts
        if isinstance(fact, ActionFact)
        and fact.payload.get("causal_eligible") is True
        and fact.payload.get("evidence_origin") == "server_dispatched"
    }
    requests = sorted(
        (fact for fact in facts if isinstance(fact, RequestFact)),
        key=lambda fact: (fact.sequence, fact.request_id),
    )
    grouped: dict[str, list[RequestFact]] = {}
    for request in requests:
        causal_action_id = request.action_id if request.action_id in actions else None
        # Unattributed requests are deliberately isolated.  Temporal adjacency
        # alone is not proof that two calls form one business capability. A
        # request carrying a page-observed action id is isolated too: page
        # JavaScript cannot promote its own binding call into a causal anchor.
        group_key = causal_action_id or f"unattributed:{request.request_id}"
        grouped.setdefault(group_key, []).append(request)

    transactions: list[ActionTransaction] = []
    for group_key, grouped_requests in grouped.items():
        first = grouped_requests[0]
        causal_action_id = first.action_id if first.action_id in actions else None
        action = actions.get(causal_action_id or "")
        transactions.append(ActionTransaction(
            transaction_id=_transaction_id(first.recording_id, group_key),
            tenant=first.tenant,
            recording_id=first.recording_id,
            action_id=causal_action_id,
            action_label=action.label if action is not None else "",
            request_ids=tuple(request.request_id for request in grouped_requests),
            first_sequence=min(request.sequence for request in grouped_requests),
            last_sequence=max(request.sequence for request in grouped_requests),
        ))
    return tuple(sorted(transactions, key=lambda txn: (txn.first_sequence, txn.transaction_id)))


def request_transaction_index(
    transactions: tuple[ActionTransaction, ...],
) -> dict[str, str]:
    return {
        request_id: transaction.transaction_id
        for transaction in transactions
        for request_id in transaction.request_ids
    }
