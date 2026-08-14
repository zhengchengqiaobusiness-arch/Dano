"""Deterministic release policy for recorded callable capabilities.

The recorder keeps the complete FlowSpec as a draft.  This module derives a
separate callable view containing only capabilities whose executable contract
is backed by machine evidence.  Model review may add blockers later, but it
cannot change this decision.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

from dano.execution.page.flow_spec import (
    FlowCapability,
    FlowSpec,
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


@dataclass(frozen=True)
class CapabilityReleaseDecision:
    capability_id: str
    name: str
    passed: bool
    reasons: tuple[str, ...] = ()
    checks: dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "name": self.name,
            "passed": self.passed,
            "reasons": list(self.reasons),
            "checks": dict(self.checks),
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


def _active_link_errors(spec: FlowSpec) -> list[str]:
    errors: list[str] = []
    for link in spec.links:
        verification_id = str((link.meta or {}).get("verification_id") or "")
        if not (
            link.confirmed
            and (link.meta or {}).get("verified") is True
            and verification_id
            and _passed_verification(spec, verification_id, kind="dependency_execute")
        ):
            errors.append(f"依赖 `{link.link_id}` 缺少 passed dependency_execute 验证")
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
            errors.append(f"依赖 `{link.link_id}` 的 dependency_execute 验证与当前依赖定义不一致")
    return errors


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


def _dynamic_structure_errors(spec: FlowSpec, capability: FlowCapability) -> list[str]:
    errors: list[str] = []
    steps = {step.step_id: step for step in spec.steps}
    public_names = set((capability.input_schema.get("properties") or {}).keys())
    for link in spec.links:
        if link.kind != "response_key_map":
            continue
        binding = dict(link.value_binding or {})
        input_field = str(binding.get("input_field") or "")
        if binding.get("kind") != "caller_map_by_label" or not input_field:
            errors.append(f"动态结构依赖 `{link.link_id}` 缺少 caller_map_by_label 契约")
            continue
        target = steps.get(link.target_step_id)
        container = str(link.target_container_path or link.target_path or "")
        recorded = _path_value(_recorded_body(target), container) if target is not None else None
        recorded_keys = {str(key) for key in recorded} if isinstance(recorded, dict) else set()
        leaked = sorted(recorded_keys & public_names)
        if leaked:
            errors.append(
                f"动态结构依赖 `{link.link_id}` 的录制键残留在公共输入: {leaked}"
            )
        prefix = container.removeprefix("body.").rstrip(".*") + "."
        stale_fields = [
            param.path for param in (target.params if target is not None else [])
            if str(param.path or "").removeprefix("body.").startswith(prefix)
            and (param.exposed_to_user or param.source_kind != "dynamic_structure")
        ]
        if stale_fields:
            errors.append(
                f"动态结构依赖 `{link.link_id}` 仍包含固定录制叶子字段: {stale_fields}"
            )
    return errors


def _field_errors(spec: FlowSpec, capability: FlowCapability, compiled: dict) -> list[str]:
    errors: list[str] = []
    steps = {step.step_id: step for step in spec.steps}
    member_ids = set(_member_step_ids(capability))
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
        for step_id in member_ids if step_id in steps
    )
    for step_id in member_ids:
        step = steps.get(step_id)
        if step is None:
            continue
        for param in step.params:
            if param.source_kind == "unknown":
                errors.append(f"字段 `{step_id}:{param.path}` 来源为 unknown")
            normalized_path = str(param.path or "").removeprefix("body.")
            if (
                _param_exposed_to_caller(param)
                and str(param.key or param.path) not in compiled_names
                and normalized_path not in compiled_paths
            ):
                errors.append(f"调用方字段 `{step_id}:{param.path}` 未编译进实际请求")
            if is_write and _param_exposed_to_caller(param):
                state = str((param.source or {}).get("required_state") or "")
                if state not in {"required", "optional"}:
                    errors.append(f"写能力字段 `{step_id}:{param.path}` 的 required 轴未确认")
            enum_issue = _capability_param_enum_issue(param)
            if enum_issue:
                errors.append(f"枚举字段 `{step_id}:{param.path}` {enum_issue}")
            if param.source_kind == "computed" and (param.source or {}).get("sample_verified") is not True:
                errors.append(f"计算字段 `{step_id}:{param.path}` 未通过录制样例验证")
    return errors


def _write_verification_errors(spec: FlowSpec, capability: FlowCapability) -> list[str]:
    steps = {step.step_id: step for step in spec.steps}
    errors: list[str] = []
    for step_id in _member_step_ids(capability):
        step = steps.get(step_id)
        if step is None or (step.method or "GET").upper() in _READ_METHODS:
            continue
        if not step.fact_check or not _executor_fact_check_is_verified(spec, step.fact_check):
            errors.append(f"写步骤 `{step_id}` 缺少 passed 写回读验证")
    return errors


def _evaluate_capability(spec: FlowSpec, capability: FlowCapability) -> CapabilityReleaseDecision:
    scoped = _capability_spec(spec, capability)
    selected = scoped.capabilities[0]
    reasons: list[str] = []
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
        reasons.append("capability 必须有且仅有一个绑定 call 节点的公共 execute anchor")

    illegal = sorted({ref.usage for ref in selected.request_refs if ref.usage not in _LEGAL_USAGES})
    checks["legal_membership_usage"] = not illegal
    if illegal:
        reasons.append(f"capability 包含非法 request usage: {illegal}")

    report = validate_flow_spec(scoped)
    checks["capability_validation"] = bool(report.get("passed"))
    if not report.get("passed"):
        reasons.extend(str(item) for item in report.get("errors") or ["capability_validation failed"])

    compiled, build_errors = flow_spec_to_api_request(scoped)
    checks["request_compilation"] = compiled is not None and not build_errors
    if compiled is None or build_errors:
        reasons.extend(str(item) for item in build_errors or ["FlowSpec 无法编译"])
    else:
        reasons.extend(_field_errors(scoped, selected, compiled))

    link_errors = _active_link_errors(scoped)
    checks["verified_links"] = not link_errors
    reasons.extend(link_errors)
    dynamic_errors = _dynamic_structure_errors(scoped, selected)
    checks["dynamic_structure"] = not dynamic_errors
    reasons.extend(dynamic_errors)
    write_errors = _write_verification_errors(scoped, selected)
    checks["write_readback"] = not write_errors
    reasons.extend(write_errors)

    dry_run = dry_run_flow_spec(scoped)
    checks["dry_run"] = bool(dry_run.get("ok"))
    if not dry_run.get("ok"):
        reasons.append("dry run 未通过")

    reasons = list(dict.fromkeys(reason for reason in reasons if reason))
    return CapabilityReleaseDecision(
        capability_id=selected.capability_id,
        name=selected.name,
        passed=not reasons and all(checks.values()),
        reasons=tuple(reasons),
        checks=checks,
    )


def evaluate_recording_release(spec: FlowSpec) -> ReleaseDecision:
    """Evaluate and derive the callable subset without mutating the draft."""
    # All release checks must inspect the exact canonical contract consumed by
    # request compilation.  Looking at the pre-sync draft here could reject a
    # field as unconfirmed even though bound recorder evidence had already
    # normalized that same field for execution.
    source = prepare_flow_spec_for_publish(spec)
    decisions = tuple(_evaluate_capability(source, cap) for cap in source.capabilities)
    passed_ids = {item.capability_id for item in decisions if item.passed}
    if not passed_ids:
        return ReleaseDecision(
            status="verification_incomplete",
            callable_spec=None,
            capabilities=decisions,
            blocking_reasons=tuple(
                f"{item.name or item.capability_id}: {reason}"
                for item in decisions for reason in item.reasons
            ) or ("没有通过机器发布闸门的可调用能力",),
        )
    callable_spec = source.model_copy(deep=True)
    callable_spec.capabilities = [
        cap for cap in callable_spec.capabilities if cap.capability_id in passed_ids
    ]
    retained = {cap.capability_id for cap in callable_spec.capabilities} | {
        cap.name for cap in callable_spec.capabilities
    }
    callable_spec.capability_relations = [
        relation for relation in callable_spec.capability_relations
        if relation.from_capability in retained and relation.to_capability in retained
    ]
    failed = [item for item in decisions if not item.passed]
    callable_spec.meta = {
        **(callable_spec.meta or {}),
        "recording_release": {
            "protocol": "dano.recording_release.v1",
            "status": "partial" if failed else "ready",
            "released_capabilities": sorted(
                item.name for item in decisions if item.passed
            ),
            "draft_only_capabilities": sorted(item.name for item in failed),
        },
    }
    callable_spec = _trim_callable_spec(callable_spec)
    return ReleaseDecision(
        status="partial" if failed else "ready",
        callable_spec=callable_spec,
        capabilities=decisions,
        blocking_reasons=tuple(
            f"{item.name or item.capability_id}: {reason}"
            for item in failed for reason in item.reasons
        ),
    )
