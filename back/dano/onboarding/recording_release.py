"""Deterministic, atomic release policy for recorded capabilities.

The recorder keeps the complete FlowSpec as a draft.  A recording is released
only when every planned capability has a machine-backed executable contract;
the publisher must never silently turn one recording into a smaller Skill.
Model review may add blockers later, but it cannot change this decision.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any

from dano.execution.page.flow_spec import (
    FlowCapability,
    FlowSpec,
    _auto_confirm_ready_capabilities,
    _api_params,
    _capability_node_step_ids,
    _capability_param_enum_issue,
    _executor_fact_check_is_verified,
    _param_exposed_to_caller,
    dry_run_flow_spec,
    flow_spec_to_api_request,
    prepare_flow_spec_for_publish,
    validate_flow_spec,
)
from dano.execution.page.verification_log import find_verification
from dano.execution.page.recording_live import dependency_link_signature


_LEGAL_USAGES = frozenset({"execute", "preflight", "option_source", "fact_check"})
_READ_METHODS = frozenset({"GET", "HEAD"})
_ISSUE_RESOLVERS = frozenset({
    "machine_repair", "collect_evidence", "operator", "external_blocked",
})


@dataclass(frozen=True)
class ReleaseIssue:
    check_code: str
    message: str
    resolver: str
    capability_id: str = ""
    step_id: str = ""
    field_id: str = ""
    wire_path: str = ""
    evidence_refs: tuple[str, ...] = ()
    suggested_operations: tuple[str, ...] = ()
    issue_id: str = ""

    def __post_init__(self) -> None:
        if self.resolver not in _ISSUE_RESOLVERS:
            raise ValueError(f"unsupported release issue resolver: {self.resolver}")
        if self.issue_id:
            return
        identity = json.dumps({
            "check_code": self.check_code,
            "capability_id": self.capability_id,
            "step_id": self.step_id,
            "field_id": self.field_id,
            "wire_path": self.wire_path,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        object.__setattr__(
            self,
            "issue_id",
            hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "check_code": self.check_code,
            "capability_id": self.capability_id,
            "step_id": self.step_id,
            "field_id": self.field_id,
            "wire_path": self.wire_path,
            "resolver": self.resolver,
            "evidence_refs": list(self.evidence_refs),
            "suggested_operations": list(self.suggested_operations),
            "message": self.message,
        }


@dataclass(frozen=True)
class CapabilityReleaseDecision:
    capability_id: str
    name: str
    passed: bool
    reasons: tuple[str, ...] = ()
    checks: dict[str, bool] = field(default_factory=dict)
    issues: tuple[ReleaseIssue, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "name": self.name,
            "passed": self.passed,
            "reasons": list(self.reasons),
            "checks": dict(self.checks),
            "issues": [item.to_dict() for item in self.issues],
        }


@dataclass(frozen=True)
class ReleaseDecision:
    status: str
    callable_spec: FlowSpec | None
    capabilities: tuple[CapabilityReleaseDecision, ...]
    blocking_reasons: tuple[str, ...] = ()

    @property
    def machine_passed(self) -> bool:
        return self.callable_spec is not None and bool(self.callable_spec.capabilities)

    def to_dict(self, *, include_spec: bool = False) -> dict[str, Any]:
        payload = {
            "protocol": "dano.recording_release.v1",
            "status": self.status,
            "machine_passed": self.machine_passed,
            "capabilities": [item.to_dict() for item in self.capabilities],
            "blocking_reasons": list(self.blocking_reasons),
        }
        if include_spec and self.callable_spec is not None:
            payload["callable_spec"] = self.callable_spec.model_dump(mode="json")
        return payload


def _member_step_ids(capability: FlowCapability) -> list[str]:
    values = [*_capability_node_step_ids(capability)]
    values.extend(
        str(ref.step_id)
        for ref in capability.request_refs
        if ref.step_id and ref.usage in _LEGAL_USAGES
    )
    return list(dict.fromkeys(value for value in values if value))


def _capability_spec(spec: FlowSpec, capability: FlowCapability) -> FlowSpec:
    """Return the exact independently callable closure for one capability."""
    current = spec.model_copy(deep=True)
    member_ids = set(_member_step_ids(capability))
    current.steps = [step for step in current.steps if step.step_id in member_ids]
    current.links = [
        link for link in current.links
        if link.source_step_id in member_ids and link.target_step_id in member_ids
    ]
    selected = next(
        cap for cap in current.capabilities
        if cap.capability_id == capability.capability_id
    )
    current.capabilities = [selected]
    cap_keys = {selected.capability_id, selected.name}
    current.capability_relations = [
        relation for relation in current.capability_relations
        if relation.from_capability in cap_keys and relation.to_capability in cap_keys
    ]
    request_ids = {
        str(step.source_meta.get("request_id") or "") for step in current.steps
    } | {str(ref.request_id or "") for ref in selected.request_refs}
    if current.request_facts is not None:
        current.request_facts.requests = [
            item for item in current.request_facts.requests
            if item.request_id in request_ids
        ]
        current.request_facts.analysis = {
            key: value for key, value in current.request_facts.analysis.items()
            if key in request_ids
        }
        current.request_facts.usage = {
            key: value for key, value in current.request_facts.usage.items()
            if key in request_ids
        }
    return current


def _trim_callable_spec(spec: FlowSpec) -> FlowSpec:
    """Remove draft-only request facts and steps from a callable asset view."""
    current = spec.model_copy(deep=True)
    member_ids = {
        step_id
        for capability in current.capabilities
        for step_id in _member_step_ids(capability)
    }
    current.steps = [step for step in current.steps if step.step_id in member_ids]
    current.links = [
        link for link in current.links
        if link.source_step_id in member_ids and link.target_step_id in member_ids
    ]
    request_ids = {
        str(step.source_meta.get("request_id") or "") for step in current.steps
    } | {
        str(ref.request_id or "")
        for capability in current.capabilities for ref in capability.request_refs
    }
    if current.request_facts is not None:
        current.request_facts.requests = [
            item for item in current.request_facts.requests if item.request_id in request_ids
        ]
        current.request_facts.analysis = {
            key: value for key, value in current.request_facts.analysis.items()
            if key in request_ids
        }
        current.request_facts.usage = {
            key: value for key, value in current.request_facts.usage.items()
            if key in request_ids
        }
    return current


def _passed_verification(spec: FlowSpec, verification_id: str, *, kind: str) -> bool:
    record = find_verification(
        verification_id,
        list((spec.meta or {}).get("verification_log") or []),
    )
    return bool(record and record.get("status") == "passed" and record.get("kind") == kind)


def _unverified_target_ids(spec: FlowSpec, *kinds: str) -> set[str]:
    return {
        str(item.get("target_id") or "")
        for item in (spec.meta or {}).get("unverified") or []
        if isinstance(item, dict) and str(item.get("target_kind") or "") in kinds
    }


def _active_link_issues(spec: FlowSpec, capability_id: str = "") -> list[ReleaseIssue]:
    issues: list[ReleaseIssue] = []
    skipped = _unverified_target_ids(spec, "dependency", "dependency_candidate")
    for link in spec.links:
        if link.link_id in skipped:
            continue
        verification_id = str((link.meta or {}).get("verification_id") or "")
        if not (
            link.confirmed
            and (link.meta or {}).get("verified") is True
            and verification_id
            and _passed_verification(spec, verification_id, kind="dependency_execute")
        ):
            issues.append(ReleaseIssue(
                check_code="dependency_verification_missing",
                message=f"依赖 `{link.link_id}` 缺少 passed dependency_execute 验证",
                resolver="collect_evidence",
                capability_id=capability_id,
                evidence_refs=(verification_id,) if verification_id else (),
                suggested_operations=("verify_dependency", "confirm_dependency"),
            ))
            continue
        record = find_verification(
            verification_id,
            list((spec.meta or {}).get("verification_log") or []),
        )
        subject = dict((record or {}).get("subject") or {})
        if (
            str(subject.get("link_id") or "") != link.link_id
            or str(subject.get("signature") or "") != dependency_link_signature(link)
        ):
            issues.append(ReleaseIssue(
                check_code="dependency_verification_stale",
                message=f"依赖 `{link.link_id}` 的 dependency_execute 验证与当前依赖定义不一致",
                resolver="collect_evidence",
                capability_id=capability_id,
                evidence_refs=(verification_id,) if verification_id else (),
                suggested_operations=("verify_dependency", "confirm_dependency"),
            ))
    return issues


def _recorded_body(step) -> Any:  # noqa: ANN001
    value = step.body_template if step.body_template is not None else step.body_source
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return None
    return value


def _path_value(value: Any, path: str) -> Any:
    current = value
    for token in str(path or "").removeprefix("body.").split("."):
        if not token:
            continue
        if not isinstance(current, dict) or token not in current:
            return None
        current = current[token]
    return current


def _dynamic_structure_issues(spec: FlowSpec, capability: FlowCapability) -> list[ReleaseIssue]:
    issues: list[ReleaseIssue] = []
    steps = {step.step_id: step for step in spec.steps}
    public_names = set((capability.input_schema.get("properties") or {}).keys())
    for link in spec.links:
        if link.kind != "response_key_map":
            continue
        binding = dict(link.value_binding or {})
        input_field = str(binding.get("input_field") or "")
        if binding.get("kind") != "caller_map_by_label" or not input_field:
            issues.append(ReleaseIssue(
                check_code="dynamic_structure_binding_missing",
                message=f"动态结构依赖 `{link.link_id}` 缺少 caller_map_by_label 契约",
                resolver="machine_repair",
                capability_id=capability.capability_id,
                step_id=link.target_step_id,
                wire_path=str(link.target_container_path or link.target_path or ""),
                suggested_operations=("propose_dependency",),
            ))
            continue
        target = steps.get(link.target_step_id)
        container = str(link.target_container_path or link.target_path or "")
        recorded = _path_value(_recorded_body(target), container) if target is not None else None
        recorded_keys = {str(key) for key in recorded} if isinstance(recorded, dict) else set()
        leaked = sorted(recorded_keys & public_names)
        if leaked:
            issues.append(ReleaseIssue(
                check_code="dynamic_structure_recorded_key_exposed",
                message=f"动态结构依赖 `{link.link_id}` 的录制键残留在公共输入: {leaked}",
                resolver="machine_repair",
                capability_id=capability.capability_id,
                step_id=link.target_step_id,
                wire_path=container,
                suggested_operations=(),
            ))
        prefix = container.removeprefix("body.").rstrip(".*") + "."
        stale_fields = [
            param.path for param in (target.params if target is not None else [])
            if str(param.path or "").removeprefix("body.").startswith(prefix)
            and (param.exposed_to_user or param.source_kind != "dynamic_structure")
        ]
        if stale_fields:
            issues.append(ReleaseIssue(
                check_code="dynamic_structure_stale_leaf",
                message=f"动态结构依赖 `{link.link_id}` 仍包含固定录制叶子字段: {stale_fields}",
                resolver="machine_repair",
                capability_id=capability.capability_id,
                step_id=link.target_step_id,
                wire_path=container,
                suggested_operations=(),
            ))
    return issues


def _param_evidence_refs(param) -> tuple[str, ...]:  # noqa: ANN001
    refs: list[str] = []
    for item in param.evidence or []:
        if not isinstance(item, dict):
            continue
        for key in ("verification_id", "event_id", "event_ref", "evidence_ref"):
            value = str(item.get(key) or "")
            if value:
                refs.append(value)
    return tuple(dict.fromkeys(refs))


def _field_issues(spec: FlowSpec, capability: FlowCapability, compiled: dict) -> list[ReleaseIssue]:
    issues: list[ReleaseIssue] = []
    steps = {step.step_id: step for step in spec.steps}
    member_ids = set(_member_step_ids(capability))
    execute_step_ids = {
        str(ref.step_id)
        for ref in capability.request_refs
        if ref.step_id and ref.usage == "execute"
    }
    compiled_names = set(_api_params(compiled))
    compiled_paths = {
        str(field.get("x-flow-path") or "").removeprefix("body.")
        for raw_capability in compiled.get("capabilities") or []
        if isinstance(raw_capability, dict)
        and str(raw_capability.get("capability_id") or "") == capability.capability_id
        for field in ((raw_capability.get("input_schema") or {}).get("properties") or {}).values()
        if isinstance(field, dict) and field.get("x-flow-path")
    }
    is_write = any(
        (steps[step_id].method or "GET").upper() not in _READ_METHODS
        for step_id in execute_step_ids if step_id in steps
    )
    for step_id in member_ids:
        step = steps.get(step_id)
        if step is None:
            continue
        for param in step.params:
            if param.source_kind == "unknown":
                source_kind_hint = str((param.source or {}).get("kind") or "")
                # Fields whose source is genuinely ambiguous but not session-dependent
                # (unresolved / unresolved_query) are routed to the operator so the
                # human can declare "user_input" or "constant" without requiring Pi
                # to collect machine evidence that may not exist.  Session-dependent
                # kinds (selected_entity_id, session_literal, heuristic,
                # readonly_control) need machine evidence — they route to
                # collect_evidence as before.
                _OPERATOR_RESOLVABLE = frozenset({"unresolved", "unresolved_query"})
                resolver = (
                    "operator"
                    if source_kind_hint in _OPERATOR_RESOLVABLE
                    else "collect_evidence"
                )
                issues.append(ReleaseIssue(
                    check_code="field_source_unknown",
                    message=f"字段 `{step_id}:{param.path}` 来源为 unknown",
                    resolver=resolver,
                    capability_id=capability.capability_id,
                    step_id=step_id,
                    field_id=str(param.field_id or ""),
                    wire_path=str(param.path or ""),
                    evidence_refs=_param_evidence_refs(param),
                    suggested_operations=("set_param_source",),
                ))
            normalized_path = str(param.path or "").removeprefix("body.")
            if (
                step_id in execute_step_ids
                and _param_exposed_to_caller(param)
                and str(param.key or param.path) not in compiled_names
                and normalized_path not in compiled_paths
            ):
                issues.append(ReleaseIssue(
                    check_code="caller_field_not_compiled",
                    message=f"调用方字段 `{step_id}:{param.path}` 未编译进实际请求",
                    resolver="machine_repair",
                    capability_id=capability.capability_id,
                    step_id=step_id,
                    field_id=str(param.field_id or ""),
                    wire_path=str(param.path or ""),
                    evidence_refs=_param_evidence_refs(param),
                    suggested_operations=(),
                ))
            if is_write and step_id in execute_step_ids and _param_exposed_to_caller(param):
                state = str((param.source or {}).get("required_state") or "")
                if state not in {"required", "optional"}:
                    issues.append(ReleaseIssue(
                        check_code="required_axis_unconfirmed",
                        message=f"写能力字段 `{step_id}:{param.path}` 的 required 轴未确认",
                        resolver="operator",
                        capability_id=capability.capability_id,
                        step_id=step_id,
                        field_id=str(param.field_id or ""),
                        wire_path=str(param.path or ""),
                        evidence_refs=_param_evidence_refs(param),
                        suggested_operations=("set_param_required",),
                    ))
            enum_issue = _capability_param_enum_issue(param)
            if enum_issue:
                issues.append(ReleaseIssue(
                    check_code="enum_options_unverified",
                    message=f"枚举字段 `{step_id}:{param.path}` {enum_issue}",
                    resolver="collect_evidence",
                    capability_id=capability.capability_id,
                    step_id=step_id,
                    field_id=str(param.field_id or ""),
                    wire_path=str(param.path or ""),
                    evidence_refs=_param_evidence_refs(param),
                    suggested_operations=("set_param_enum",),
                ))
            if param.source_kind == "computed" and (param.source or {}).get("sample_verified") is not True:
                issues.append(ReleaseIssue(
                    check_code="computed_sample_unverified",
                    message=f"计算字段 `{step_id}:{param.path}` 未通过录制样例验证",
                    resolver="collect_evidence",
                    capability_id=capability.capability_id,
                    step_id=step_id,
                    field_id=str(param.field_id or ""),
                    wire_path=str(param.path or ""),
                    evidence_refs=_param_evidence_refs(param),
                    suggested_operations=(),
                ))
    return issues


def _write_verification_issues(spec: FlowSpec, capability: FlowCapability) -> list[ReleaseIssue]:
    steps = {step.step_id: step for step in spec.steps}
    issues: list[ReleaseIssue] = []
    skipped = _unverified_target_ids(spec, "write_verify")
    for step_id in _member_step_ids(capability):
        if step_id in skipped:
            continue
        step = steps.get(step_id)
        if step is None or (step.method or "GET").upper() in _READ_METHODS:
            continue
        if not step.fact_check or not _executor_fact_check_is_verified(spec, step.fact_check):
            issues.append(ReleaseIssue(
                check_code="write_readback_missing",
                message=f"写步骤 `{step_id}` 缺少 passed 写回读验证",
                resolver="collect_evidence",
                capability_id=capability.capability_id,
                step_id=step_id,
                evidence_refs=(str((step.fact_check or {}).get("verification_id") or ""),)
                if (step.fact_check or {}).get("verification_id") else (),
                suggested_operations=("execute_write_with_verify", "bind_verify_read"),
            ))
    return issues


def _evaluate_capability(spec: FlowSpec, capability: FlowCapability) -> CapabilityReleaseDecision:
    scoped = _capability_spec(spec, capability)
    selected = scoped.capabilities[0]
    issues: list[ReleaseIssue] = []
    checks: dict[str, bool] = {}

    execute_refs = [ref for ref in selected.request_refs if ref.usage == "execute"]
    execute_call_ids = {
        str(node.get("step_id") or "")
        for node in selected.nodes
        if isinstance(node, dict) and node.get("type") == "call"
    }
    anchor_ok = len(execute_refs) == 1 and execute_refs[0].step_id in execute_call_ids
    checks["unique_public_anchor"] = anchor_ok
    if not anchor_ok:
        issues.append(ReleaseIssue(
            check_code="public_execute_anchor_invalid",
            message="capability 必须有且仅有一个绑定 call 节点的公共 execute anchor",
            resolver="machine_repair",
            capability_id=selected.capability_id,
            suggested_operations=(),
        ))

    illegal = sorted({ref.usage for ref in selected.request_refs if ref.usage not in _LEGAL_USAGES})
    checks["legal_membership_usage"] = not illegal
    if illegal:
        issues.append(ReleaseIssue(
            check_code="capability_usage_invalid",
            message=f"capability 包含非法 request usage: {illegal}",
            resolver="machine_repair",
            capability_id=selected.capability_id,
            suggested_operations=(),
        ))

    report = validate_flow_spec(scoped)
    checks["capability_validation"] = bool(report.get("passed"))
    if not report.get("passed"):
        issues.extend(ReleaseIssue(
            check_code="capability_validation_failed",
            message=str(item),
            resolver="machine_repair",
            capability_id=selected.capability_id,
            suggested_operations=(),
        ) for item in (report.get("errors") or ["capability_validation failed"]))

    compiled, build_errors = flow_spec_to_api_request(scoped)
    checks["request_compilation"] = compiled is not None and not build_errors
    if compiled is None or build_errors:
        issues.extend(ReleaseIssue(
            check_code="request_compilation_failed",
            message=str(item),
            resolver="machine_repair",
            capability_id=selected.capability_id,
            suggested_operations=(),
        ) for item in (build_errors or ["FlowSpec 无法编译"]))
    else:
        issues.extend(_field_issues(scoped, selected, compiled))

    link_issues = _active_link_issues(scoped, selected.capability_id)
    checks["verified_links"] = not link_issues
    issues.extend(link_issues)
    dynamic_issues = _dynamic_structure_issues(scoped, selected)
    checks["dynamic_structure"] = not dynamic_issues
    issues.extend(dynamic_issues)
    write_issues = _write_verification_issues(scoped, selected)
    checks["write_readback"] = not write_issues
    issues.extend(write_issues)

    dry_run = dry_run_flow_spec(scoped)
    checks["dry_run"] = bool(dry_run.get("ok"))
    if not dry_run.get("ok"):
        dry_run_details = [
            str(value)
            for key in ("build_errors", "self_check", "construct_errors")
            for value in (dry_run.get(key) or [])
            if value
        ]
        dry_run_details.extend(
            f"必填参数 `{value}` 没有录制样例或调用值"
            for value in (dry_run.get("missing_params") or [])
            if value
        )
        fact_check = dry_run.get("fact_check") or {}
        if fact_check.get("passed") is False and fact_check.get("reason"):
            dry_run_details.append(str(fact_check["reason"]))
        issues.append(ReleaseIssue(
            check_code="dry_run_failed",
            message="；".join(dict.fromkeys(dry_run_details)) or "dry run 未通过",
            resolver="machine_repair",
            capability_id=selected.capability_id,
            suggested_operations=(),
        ))

    unique_issues = {
        item.issue_id: item for item in issues if item.message
    }
    issues = list(unique_issues.values())
    reasons = list(dict.fromkeys(item.message for item in issues))
    return CapabilityReleaseDecision(
        capability_id=selected.capability_id,
        name=selected.name,
        passed=not reasons and all(checks.values()),
        reasons=tuple(reasons),
        checks=checks,
        issues=tuple(issues),
    )


def evaluate_recording_release(spec: FlowSpec) -> ReleaseDecision:
    """Evaluate the complete capability plan without mutating the draft."""
    # All release checks must inspect the exact canonical contract consumed by
    # request compilation.  Looking at the pre-sync draft here could reject a
    # field as unconfirmed even though bound recorder evidence had already
    # normalized that same field for execution.
    source = prepare_flow_spec_for_publish(spec)
    decisions = tuple(_evaluate_capability(source, cap) for cap in source.capabilities)
    failed = [item for item in decisions if not item.passed]
    if not decisions or failed:
        return ReleaseDecision(
            status="verification_incomplete",
            callable_spec=None,
            capabilities=decisions,
            blocking_reasons=tuple(
                f"{item.name or item.capability_id}: {reason}"
                for item in decisions for reason in item.reasons
            ) or ("没有通过机器发布闸门的可调用能力",),
        )
    # Reaching this branch means every capability has passed the deterministic
    # executable release checks.  Freeze that machine decision into the
    # candidate so final review does not mistake the planner's pre-verification
    # draft flag for an unresolved operator decision.
    callable_spec = _auto_confirm_ready_capabilities(
        source.model_copy(deep=True), refresh_machine_owned=True,
    )
    callable_spec.meta = {
        **(callable_spec.meta or {}),
        "recording_release": {
            "protocol": "dano.recording_release.v1",
            "status": "ready",
            "released_capabilities": sorted(item.name for item in decisions),
            "draft_only_capabilities": [],
        },
    }
    callable_spec = _trim_callable_spec(callable_spec)
    return ReleaseDecision(
        status="ready",
        callable_spec=callable_spec,
        capabilities=decisions,
        blocking_reasons=(),
    )
