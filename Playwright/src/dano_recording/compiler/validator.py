"""Deterministic compiler invariants."""

from __future__ import annotations

from collections import Counter

from dano_recording.compiler.models import CompilationIssue, IssueSeverity, ValidationReport
from dano_recording.domain.capabilities import Capability
from dano_recording.domain.facts import RequestFact
from dano_recording.domain.operations import CompiledRequest, RequestAnalysis, RequestDisposition


def validate_compilation(
    captured_requests: tuple[RequestFact, ...],
    analyses: tuple[RequestAnalysis, ...],
    compiled_requests: tuple[CompiledRequest, ...],
    capabilities: tuple[Capability, ...],
) -> ValidationReport:
    issues: list[CompilationIssue] = []
    captured_ids = [request.request_id for request in captured_requests]
    analysis_ids = [analysis.request_id for analysis in analyses]
    compiled_ids = [request.request_id for request in compiled_requests]

    for request_id, count in Counter(captured_ids).items():
        if count != 1:
            issues.append(CompilationIssue(
                code="duplicate_request_fact",
                message=f"request {request_id} was captured {count} times",
                severity=IssueSeverity.ERROR,
                request_id=request_id,
            ))
    for request_id in captured_ids:
        if analysis_ids.count(request_id) != 1:
            issues.append(CompilationIssue(
                code="missing_request_disposition",
                message=f"request {request_id} must have exactly one disposition",
                severity=IssueSeverity.ERROR,
                request_id=request_id,
            ))
        if compiled_ids.count(request_id) != 1:
            issues.append(CompilationIssue(
                code="request_silently_dropped",
                message=f"request {request_id} must have exactly one compiler ledger row",
                severity=IssueSeverity.ERROR,
                request_id=request_id,
            ))

    captured_by_id = {request.request_id: request for request in captured_requests}
    compiled_by_id = {request.request_id: request for request in compiled_requests}
    for request_id, captured in captured_by_id.items():
        compiled = compiled_by_id.get(request_id)
        if compiled is None:
            continue
        if captured.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if compiled.body_present != captured.request_body_present:
                issues.append(CompilationIssue(
                    code="body_presence_changed",
                    message=f"body presence changed while compiling {request_id}",
                    severity=IssueSeverity.ERROR,
                    request_id=request_id,
                ))
        if compiled.query != captured.query_items:
            issues.append(CompilationIssue(
                code="query_not_lossless",
                message=f"query items changed while compiling {request_id}",
                severity=IssueSeverity.ERROR,
                request_id=request_id,
            ))

    membership = Counter(
        request_id
        for capability in capabilities
        for request_id in capability.request_ids
    )
    for request in compiled_requests:
        if request.capability_eligible and membership[request.request_id] > 1:
            issues.append(CompilationIssue(
                code="capability_membership_invalid",
                message=(
                    f"eligible request {request.request_id} belongs to "
                    f"{membership[request.request_id]} capabilities"
                ),
                severity=IssueSeverity.ERROR,
                request_id=request.request_id,
            ))
        if request.capability_eligible and membership[request.request_id] == 0:
            issues.append(CompilationIssue(
                code="unbound_business_request",
                message=(
                    f"eligible request {request.request_id} is retained for review "
                    "but has no proven capability dependency"
                ),
                severity=IssueSeverity.WARNING,
                request_id=request.request_id,
            ))
        if not request.capability_eligible and membership[request.request_id]:
            issues.append(CompilationIssue(
                code="non_business_request_exposed",
                message=f"{request.disposition.value} request was exposed as a capability",
                severity=IssueSeverity.ERROR,
                request_id=request.request_id,
            ))
        if request.disposition is RequestDisposition.UNSUPPORTED:
            issues.append(CompilationIssue(
                code="unsupported_request_retained",
                message=f"request {request.request_id} is retained but needs explicit review",
                severity=IssueSeverity.WARNING,
                request_id=request.request_id,
            ))

    return ValidationReport(
        passed=not any(issue.severity is IssueSeverity.ERROR for issue in issues),
        issues=tuple(issues),
    )
