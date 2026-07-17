"""Plan public capabilities from proven command transactions.

All captured requests remain in the capture ledger.  A capability references
only a terminal business request and auxiliary requests proven to feed it.
Array payloads are ordinary ``submit`` operations; ``submit_batch`` is accepted
only as a legacy input spelling and is silently normalised.
"""

from __future__ import annotations

import hashlib
import re
from enum import StrEnum
from typing import Any, Iterable

from pydantic import Field, model_validator

from dano_recording.domain._base import FrozenDict, FrozenModel
from dano_recording.domain.capabilities import Capability, CapabilityRisk
from dano_recording.domain.facts import ActionTransaction
from dano_recording.domain.operations import CompiledRequest, RequestDisposition
from dano_recording.evidence_graph import EvidenceGraph


class BusinessTerminalKind(StrEnum):
    LIST_RESULT = "list_result"
    STATE_CHANGE = "state_change"
    RECORD_CHANGE = "record_change"
    FILE_OUTPUT = "file_output"
    CONFIRMATION = "confirmation"


class CapabilityPlanningHint(FrozenModel):
    request_id: str
    terminal_kind: BusinessTerminalKind | None = None
    business_result: bool = False
    independently_triggerable: bool = False
    caller_usable: bool = False
    pure_dependency: bool = False
    operation: str | None = None


class CapabilityPlan(FrozenModel):
    capabilities: tuple[Capability, ...]
    terminal_request_ids: tuple[str, ...]
    unbound_business_requests: tuple[str, ...]
    ignored_request_ids: tuple[str, ...]
    operation_by_capability: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _freeze_operation_index(self) -> "CapabilityPlan":
        object.__setattr__(
            self,
            "operation_by_capability",
            FrozenDict(dict(self.operation_by_capability)),
        )
        return self


_IGNORED_DISPOSITIONS = frozenset(
    {
        RequestDisposition.IDENTITY,
        RequestDisposition.PREFLIGHT,
        RequestDisposition.OPTION_SOURCE,
        RequestDisposition.IGNORED_RESOURCE,
        RequestDisposition.UNSUPPORTED,
        RequestDisposition.SUPPORTING,
    }
)
_MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_DANGEROUS = re.compile(
    r"(?:delete|remove|revoke|withdraw|reject|terminate|cancel|撤回|删除|驳回|终止|作废)",
    re.IGNORECASE,
)
_QUERY = re.compile(r"(?:query|search|list|find|查询|搜索|检索|列表)", re.IGNORECASE)
_EXPORT = re.compile(r"(?:export|download|导出|下载)", re.IGNORECASE)
_APPROVE = re.compile(r"(?:approve|accept|同意|审批通过)", re.IGNORECASE)
_REJECT = re.compile(r"(?:reject|驳回|拒绝)", re.IGNORECASE)
_WITHDRAW = re.compile(r"(?:withdraw|revoke|撤回|撤销)", re.IGNORECASE)
_DELETE = re.compile(r"(?:delete|remove|删除|移除)", re.IGNORECASE)
_SUBMIT = re.compile(
    r"(?:submit[_\- ]?batch|batch[_\- ]?submit|batch|submit|save|create|update|提交|保存|新建|创建|修改|批量)",
    re.IGNORECASE,
)
_BUSINESS_READ = re.compile(
    r"(?:detail|record|result|report|task|application|order|project|详情|记录|结果|报表|任务|申请|订单|项目)",
    re.IGNORECASE,
)
_TECHNICAL = re.compile(
    r"(?:^|/)(?:metrics?|telemetry|analytics|logs?|traces?|heartbeat|health|ping|feature[-_]?flags?|runtime[-_]?config|manifest)(?:/|$)",
    re.IGNORECASE,
)


def normalize_capability_operation(value: str) -> str:
    """Normalise legacy/batch spellings to the final operation vocabulary."""

    compact = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", value.strip().casefold()).strip("_")
    if compact in {"submit_batch", "batch_submit"} or (
        "submit" in compact and "batch" in compact
    ):
        return "submit"
    if "批量" in compact and any(word in compact for word in ("提交", "保存", "新增", "创建")):
        return "submit"
    return compact or "submit"


def _normalise_title(value: str, operation: str) -> str:
    title = re.sub(r"(?i)\bbatch\b", "", value)
    title = title.replace("批量", "")
    title = re.sub(r"\s+", " ", title).strip(" _-")
    if title:
        return title
    return {
        "query": "查询",
        "submit": "提交",
        "withdraw": "撤回",
        "approve": "审批",
        "reject": "驳回",
        "delete": "删除",
        "export": "导出",
    }.get(operation, operation)


def _operation(
    transaction: ActionTransaction,
    request: CompiledRequest,
    hint: CapabilityPlanningHint | None,
) -> str:
    if hint and hint.operation:
        return normalize_capability_operation(hint.operation)
    text = " ".join((transaction.action_label, request.path))
    if request.method == "DELETE" or _DELETE.search(text):
        return "delete"
    if _WITHDRAW.search(text):
        return "withdraw"
    if _REJECT.search(text):
        return "reject"
    if _APPROVE.search(text):
        return "approve"
    if _EXPORT.search(text):
        return "export"
    if request.method == "GET" or _QUERY.search(text):
        return "query"
    if request.method in _MUTATION_METHODS or _SUBMIT.search(text):
        # Lists/arrays are request shapes, not a separate public capability.
        return "submit"
    return normalize_capability_operation(transaction.action_label or request.method)


def _schema_has_collection(schema: dict[str, Any] | None) -> bool:
    if not isinstance(schema, dict):
        return False
    if schema.get("type") == "array":
        return True
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return False
    for key, value in properties.items():
        if str(key).casefold() in {"records", "items", "results", "list", "data"}:
            if isinstance(value, dict) and value.get("type") in {"array", "object"}:
                return True
    return False


def _schema_is_business_object(schema: dict[str, Any] | None) -> bool:
    """Return true for a concrete JSON object, not an HTML/navigation response."""

    return bool(
        isinstance(schema, dict)
        and schema.get("type") == "object"
        and isinstance(schema.get("properties"), dict)
        and schema["properties"]
    )


def _terminal_kind(
    transaction: ActionTransaction,
    request: CompiledRequest,
    hint: CapabilityPlanningHint | None,
) -> BusinessTerminalKind | None:
    if hint is not None:
        if hint.pure_dependency:
            return None
        if hint.terminal_kind is not None:
            return hint.terminal_kind
        if hint.business_result:
            return (
                BusinessTerminalKind.STATE_CHANGE
                if request.method in _MUTATION_METHODS
                else BusinessTerminalKind.CONFIRMATION
            )
    if request.disposition in _IGNORED_DISPOSITIONS:
        return None
    if _TECHNICAL.search(request.path):
        return None
    text = " ".join((transaction.action_label, request.path))
    if _EXPORT.search(text):
        return BusinessTerminalKind.FILE_OUTPUT
    if request.method in _MUTATION_METHODS:
        return (
            BusinessTerminalKind.RECORD_CHANGE
            if request.method in {"POST", "PUT", "PATCH", "DELETE"}
            else BusinessTerminalKind.STATE_CHANGE
        )
    if request.method == "GET" and (
        request.query
        or _schema_has_collection(request.response_schema)
        or _QUERY.search(transaction.action_label)
    ):
        return BusinessTerminalKind.LIST_RESULT
    # A strongly attributed user click followed by a concrete JSON object is a
    # usable read capability even when the application uses opaque routes and
    # icon-only controls.  Technical/resource/identity/option calls were
    # already excluded above; an unattributed background GET cannot enter here.
    if (
        request.method == "GET"
        and transaction.action_id is not None
        and request.disposition
        in {RequestDisposition.MATERIALIZED, RequestDisposition.REVIEW_CANDIDATE}
        and _schema_is_business_object(request.response_schema)
    ):
        return BusinessTerminalKind.CONFIRMATION
    if (
        request.disposition
        in {RequestDisposition.MATERIALIZED, RequestDisposition.REVIEW_CANDIDATE}
        and _BUSINESS_READ.search(text)
    ):
        return BusinessTerminalKind.CONFIRMATION
    return None


def _risk(requests: tuple[CompiledRequest, ...]) -> tuple[CapabilityRisk, bool, bool]:
    dangerous = any(
        request.method == "DELETE" or _DANGEROUS.search(request.path)
        for request in requests
    )
    if dangerous:
        return CapabilityRisk.L4, False, True
    if any(request.method in _MUTATION_METHODS - {"DELETE"} for request in requests):
        return CapabilityRisk.L3, True, True
    return CapabilityRisk.L1, True, False


def _capability_id(transaction_id: str, terminal_request_id: str) -> str:
    digest = hashlib.sha256(
        f"{transaction_id}\0{terminal_request_id}".encode("utf-8")
    ).hexdigest()[:20]
    return f"cap_{digest}"


def _field_id(field: Any) -> str | None:
    value = getattr(field, "field_uuid", None) or getattr(field, "field_contract_id", None)
    return str(value) if value else None


def plan_capabilities(
    transactions: Iterable[ActionTransaction],
    requests: Iterable[CompiledRequest],
    fields: Iterable[Any] = (),
    *,
    evidence_graph: EvidenceGraph | None = None,
    hints: Iterable[CapabilityPlanningHint] = (),
) -> CapabilityPlan:
    transactions = tuple(transactions)
    requests = tuple(requests)
    hints_by_request = {hint.request_id: hint for hint in hints}
    request_by_id = {request.request_id: request for request in requests}
    fields_by_request: dict[str, list[str]] = {}
    for field in fields:
        request_id = str(getattr(field, "request_id", "") or "")
        field_id = _field_id(field)
        if request_id and field_id:
            fields_by_request.setdefault(request_id, []).append(field_id)

    capabilities: list[Capability] = []
    terminal_ids: list[str] = []
    used_request_ids: set[str] = set()
    operation_by_capability: dict[str, str] = {}
    used_names: dict[str, int] = {}

    for transaction in sorted(transactions, key=lambda item: (item.first_sequence, item.transaction_id)):
        scoped = tuple(
            sorted(
                (
                    request_by_id[request_id]
                    for request_id in transaction.request_ids
                    if request_id in request_by_id
                ),
                key=lambda item: (item.sequence, item.request_id),
            )
        )
        terminals = [
            request
            for request in scoped
            if _terminal_kind(transaction, request, hints_by_request.get(request.request_id))
            is not None
        ]
        if not terminals:
            continue

        # Requests that feed a later terminal are dependencies, not separate
        # capabilities.  Without proof, multiple calls from one click remain a
        # single command ending at the latest business result.
        sinks = []
        for candidate in terminals:
            feeds_other = bool(
                evidence_graph
                and any(
                    evidence_graph.has_request_dependency(candidate.request_id, other.request_id)
                    for other in terminals
                    if other.request_id != candidate.request_id
                )
            )
            if not feeds_other:
                sinks.append(candidate)
        terminals = sinks or [terminals[-1]]

        can_split = len(terminals) > 1 and all(
            (
                (hint := hints_by_request.get(request.request_id)) is not None
                and hint.business_result
                and hint.independently_triggerable
                and hint.caller_usable
                and not hint.pure_dependency
            )
            for request in terminals
        )
        selected_terminals = terminals if can_split else [terminals[-1]]

        for terminal in selected_terminals:
            # An action that cannot be proven independently splittable is one
            # command, so every terminal effect belongs to its ordered runtime
            # chain.  Keeping only the last response would turn common
            # ``POST -> refresh GET`` actions into read-only capabilities and
            # leave the write without risk/confirmation ownership.
            command_terminals = (terminal,) if can_split else tuple(terminals)
            command_terminal_ids = {
                request.request_id for request in command_terminals
            }
            dependency_set = {
                dependency_id
                for command_terminal in command_terminals
                for dependency_id in (
                    evidence_graph.request_dependencies(command_terminal.request_id)
                    if evidence_graph is not None
                    else ()
                )
            }
            execution_requests = tuple(
                request
                for request in scoped
                if request.request_id in command_terminal_ids
                or (
                    request.request_id in dependency_set
                    and request.disposition
                    not in {
                        RequestDisposition.IDENTITY,
                        RequestDisposition.PREFLIGHT,
                        RequestDisposition.IGNORED_RESOURCE,
                        RequestDisposition.UNSUPPORTED,
                    }
                    and not _TECHNICAL.search(request.path)
                )
            )
            if terminal not in execution_requests:
                execution_requests += (terminal,)
            execution_requests = tuple(
                sorted(execution_requests, key=lambda item: (item.sequence, item.request_id))
            )

            operation_request = next(
                (
                    request
                    for request in reversed(execution_requests)
                    if request.method in _MUTATION_METHODS
                ),
                terminal,
            )
            hint = hints_by_request.get(operation_request.request_id)
            operation = _operation(transaction, operation_request, hint)
            operation = normalize_capability_operation(operation)
            count = used_names.get(operation, 0)
            used_names[operation] = count + 1
            name = operation if count == 0 else f"{operation}_{count + 1}"
            title = _normalise_title(transaction.action_label, operation)
            risk, enabled, explicit = _risk(execution_requests)
            field_ids = tuple(
                dict.fromkeys(
                    field_id
                    for request in execution_requests
                    for field_id in fields_by_request.get(request.request_id, ())
                )
            )
            capability_id = _capability_id(transaction.transaction_id, terminal.request_id)
            capabilities.append(
                Capability(
                    capability_id=capability_id,
                    transaction_id=transaction.transaction_id,
                    name=name,
                    title=title,
                    operation=operation,
                    request_ids=tuple(request.request_id for request in execution_requests),
                    field_contract_ids=field_ids,
                    risk_level=risk,
                    execution_enabled=enabled,
                    explicit_confirmation=explicit,
                )
            )
            terminal_ids.append(terminal.request_id)
            used_request_ids.update(request.request_id for request in execution_requests)
            operation_by_capability[capability_id] = operation

    def ignored_for_public_planning(request: CompiledRequest) -> bool:
        hint = hints_by_request.get(request.request_id)
        if hint is not None and hint.business_result:
            return False
        return request.disposition in _IGNORED_DISPOSITIONS or bool(
            _TECHNICAL.search(request.path)
        )

    eligible_business = {
        request.request_id
        for request in requests
        if not ignored_for_public_planning(request)
        and (
            request.capability_eligible
            or hints_by_request.get(request.request_id, CapabilityPlanningHint(request_id=request.request_id)).business_result
        )
    }
    ignored = tuple(
        request.request_id
        for request in requests
        if ignored_for_public_planning(request) and request.request_id not in used_request_ids
    )
    return CapabilityPlan(
        capabilities=tuple(capabilities),
        terminal_request_ids=tuple(terminal_ids),
        unbound_business_requests=tuple(
            request.request_id
            for request in requests
            if request.request_id in eligible_business and request.request_id not in used_request_ids
        ),
        ignored_request_ids=ignored,
        operation_by_capability=operation_by_capability,
    )


__all__ = [
    "BusinessTerminalKind",
    "CapabilityPlan",
    "CapabilityPlanningHint",
    "normalize_capability_operation",
    "plan_capabilities",
]
