"""Stage-seven verification contract: fail-closed, recoverable, evidence-only.

This module is the single source of truth for Stage 7 status, fingerprints,
scope, publishability, checkpoints and task identity. Workflow/runtime/release
must not invent a second publish gate.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from dano.execution.page.flow_spec_core.models import FlowCapability, FlowSpec, FlowStep


STAGE_SEVEN_PROTOCOL = "dano.recording.stage7.v2"
STAGE_SIX_CONTRACT_CHANGED = "stage_six_contract_changed"
VERIFICATION_UNRESOLVED = "verification_unresolved"
WRITE_OUTCOME_UNKNOWN = "write_outcome_unknown"
AUTH_BLOCKED_MESSAGE = "录制登录态已失效，机器验证已阻塞，当前结果不会发布"
NETWORK_BLOCKED_MESSAGE = "目标服务不可达或回放超时，机器验证已阻塞，当前结果不会发布"
PREFLIGHT_HEALTHY_STATUSES = frozenset({"healthy", "refreshed", "not_applicable"})
_LEGAL_USAGES = frozenset({"execute", "preflight", "option_source", "fact_check"})
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_READ_METHODS = frozenset({"GET", "HEAD"})
_NOISE_PATH_MARKERS = (
    "/tenant/",
    "get-by-website",
    "/im/",
    "online-status",
    "/telemetry",
    "/metrics",
    "/actuator",
    "/login",
    "/auth/",
    "/config",
)
_SECRET_PATTERN = re.compile(
    r"(authorization|cookie|token|storage_state|bearer\s+\S+)",
    re.IGNORECASE,
)
_VOLATILE_META_KEYS = frozenset({
    "stage_seven",
    "versions",
    "current_version",
    "verification_run",
})
_CALLER_OPS = frozenset({"fill", "select", "pick", "upload"})
_NON_CALLER_HINTS = frozenset({
    "session", "csrf", "cookie", "authorization", "token",
    "created_by", "updated_by", "create_time", "update_time",
    "creator", "updater", "tenant_id", "trace_id",
})


class StageSevenStatus(StrEnum):
    RUNNING = "running"
    WAITING_OPERATOR = "waiting_operator"
    BLOCKED_EXTERNAL = "blocked_external"
    INCOMPLETE = "incomplete"
    VERIFIED = "verified"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StageSevenPreflightStatus(StrEnum):
    HEALTHY = "healthy"
    REFRESHED = "refreshed"
    NOT_APPLICABLE = "not_applicable"
    BLOCKED_AUTH = "blocked_auth"
    BLOCKED_NETWORK = "blocked_network"


class StageSixContractChanged(ValueError):
    """Stage 7 mutated a protected Stage 6 public capability contract."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_spec(spec: FlowSpec | dict[str, Any]) -> FlowSpec:
    if isinstance(spec, FlowSpec):
        return spec
    return FlowSpec.model_validate(spec)


def _as_dict(spec: FlowSpec | dict[str, Any]) -> dict[str, Any]:
    if isinstance(spec, FlowSpec):
        return spec.model_dump(mode="json")
    return dict(spec)


def stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def redact_external_blocker_text(text: str) -> str:
    cleaned = _SECRET_PATTERN.sub("[redacted]", str(text or ""))
    if len(cleaned) > 240:
        return cleaned[:240]
    return cleaned


def _public_execute_anchor(capability: FlowCapability) -> dict[str, str]:
    refs = [ref for ref in capability.request_refs if ref.usage == "execute"]
    if refs:
        ref = refs[0]
        return {
            "step_id": str(ref.step_id or ""),
            "request_id": str(ref.request_id or ""),
            "method": str(ref.method or ""),
            "path": str(ref.path or ""),
        }
    for node in capability.nodes or []:
        if not isinstance(node, dict):
            continue
        if str(node.get("type") or "") != "call":
            continue
        if str(node.get("usage") or "execute") != "execute":
            continue
        return {
            "step_id": str(node.get("step_id") or ""),
            "request_id": str(node.get("request_id") or ""),
            "method": str(node.get("method") or ""),
            "path": str(node.get("path") or ""),
        }
    return {"step_id": "", "request_id": "", "method": "", "path": ""}


def _semantic_plan_boundaries(spec: FlowSpec) -> list[dict[str, Any]]:
    model = dict((spec.meta or {}).get("capability_model") or {})
    plan = model.get("semantic_plan") if isinstance(model.get("semantic_plan"), dict) else {}
    rows: list[dict[str, Any]] = []
    for item in list(plan.get("capabilities") or []):
        if not isinstance(item, dict):
            continue
        rows.append({
            "name": str(item.get("name") or ""),
            "title": str(item.get("title") or ""),
            "kind": str(item.get("kind") or ""),
            "intent": str(item.get("intent") or ""),
            "anchor_step_id": str(item.get("anchor_step_id") or ""),
        })
    return rows


def protected_contract_payload(spec: FlowSpec | dict[str, Any]) -> dict[str, Any]:
    current = _as_spec(spec)
    return {
        "capabilities": [
            {
                "capability_id": capability.capability_id,
                "name": capability.name,
                "title": capability.title,
                "intent": capability.intent,
                "kind": capability.kind,
                "execute_anchor": _public_execute_anchor(capability),
            }
            for capability in current.capabilities
        ],
        "capability_order": [capability.capability_id for capability in current.capabilities],
        "semantic_plan": _semantic_plan_boundaries(current),
    }


def protected_contract_fingerprint(spec: FlowSpec | dict[str, Any]) -> str:
    return stable_hash(protected_contract_payload(spec))


def baseline_fingerprint(spec: FlowSpec | dict[str, Any]) -> str:
    payload = _as_dict(spec)
    meta = dict(payload.get("meta") or {})
    for key in _VOLATILE_META_KEYS:
        meta.pop(key, None)
    payload["meta"] = meta
    return stable_hash(payload)


def working_fingerprint(spec: FlowSpec | dict[str, Any]) -> str:
    payload = _as_dict(spec)
    meta = dict(payload.get("meta") or {})
    for key in _VOLATILE_META_KEYS:
        meta.pop(key, None)
    payload["meta"] = meta
    return stable_hash(payload)


def assert_stage_six_contract_preserved(
    baseline: FlowSpec | dict[str, Any],
    working: FlowSpec | dict[str, Any],
) -> None:
    expected = protected_contract_fingerprint(baseline)
    actual = protected_contract_fingerprint(working)
    if expected != actual:
        raise StageSixContractChanged("stage 7 changed a protected stage 6 public capability contract")


@dataclass(frozen=True)
class StageSevenScope:
    capability_ids: tuple[str, ...]
    execute_step_ids: dict[str, tuple[str, ...]]
    member_step_ids: frozenset[str]
    preflight_step_ids: frozenset[str]
    option_source_step_ids: frozenset[str]
    option_source_request_ids: frozenset[str]
    fact_check_step_ids: frozenset[str]
    fact_check_request_ids: frozenset[str]
    link_ids: frozenset[str]
    write_step_ids: frozenset[str]
    enum_targets: frozenset[str]
    protected_contract: dict[str, Any]
    baseline_fingerprint: str
    members_by_capability: dict[str, frozenset[str]] = field(default_factory=dict)

    def contains_step(self, step_id: str) -> bool:
        return str(step_id or "") in self.member_step_ids

    def capability_for_step(self, step_id: str) -> str:
        needle = str(step_id or "")
        for capability_id, members in self.members_by_capability.items():
            if needle in members:
                return capability_id
        return ""


@dataclass(frozen=True)
class StageSevenVerdict:
    protocol: str
    attempt_id: str
    revision: int
    status: StageSevenStatus
    publishable: bool
    all_verified: bool
    baseline_fingerprint: str
    protected_contract_fingerprint: str
    working_fingerprint: str
    preflight: dict[str, Any]
    issues: tuple[dict[str, Any], ...]
    unverified: tuple[dict[str, Any], ...]
    capability_results: dict[str, Any]
    verification_summary: dict[str, Any]
    release_status: str
    callable_capability_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol": self.protocol,
            "attempt_id": self.attempt_id,
            "revision": self.revision,
            "status": str(self.status),
            "publishable": self.publishable,
            "all_verified": self.all_verified,
            "baseline_fingerprint": self.baseline_fingerprint,
            "protected_contract_fingerprint": self.protected_contract_fingerprint,
            "working_fingerprint": self.working_fingerprint,
            "preflight": dict(self.preflight),
            "issues": [dict(item) for item in self.issues],
            "unverified": [dict(item) for item in self.unverified],
            "capability_results": dict(self.capability_results),
            "verification_summary": dict(self.verification_summary),
            "release_status": self.release_status,
            "callable_capability_ids": list(self.callable_capability_ids),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> StageSevenVerdict | None:
        if not isinstance(payload, dict) or not payload:
            return None
        try:
            status = StageSevenStatus(str(payload.get("status") or StageSevenStatus.RUNNING))
        except ValueError:
            status = StageSevenStatus.INCOMPLETE
        return cls(
            protocol=str(payload.get("protocol") or STAGE_SEVEN_PROTOCOL),
            attempt_id=str(payload.get("attempt_id") or ""),
            revision=int(payload.get("revision") or 0),
            status=status,
            publishable=bool(payload.get("publishable")),
            all_verified=bool(payload.get("all_verified")),
            baseline_fingerprint=str(payload.get("baseline_fingerprint") or ""),
            protected_contract_fingerprint=str(payload.get("protected_contract_fingerprint") or ""),
            working_fingerprint=str(payload.get("working_fingerprint") or ""),
            preflight=dict(payload.get("preflight") or {}),
            issues=tuple(dict(item) for item in (payload.get("issues") or []) if isinstance(item, dict)),
            unverified=tuple(
                dict(item) for item in (payload.get("unverified") or []) if isinstance(item, dict)
            ),
            capability_results=dict(payload.get("capability_results") or {}),
            verification_summary=dict(payload.get("verification_summary") or {}),
            release_status=str(payload.get("release_status") or ""),
            callable_capability_ids=tuple(
                str(item) for item in (payload.get("callable_capability_ids") or [])
            ),
        )


def preflight_status_of(preflight: dict[str, Any] | None) -> StageSevenPreflightStatus:
    payload = dict(preflight or {})
    raw = str(payload.get("status") or "").strip()
    if raw:
        try:
            return StageSevenPreflightStatus(raw)
        except ValueError:
            pass
    if payload.get("blocked_auth") or payload.get("auth_failed") and payload.get("skip_replay"):
        return StageSevenPreflightStatus.BLOCKED_AUTH
    if payload.get("blocked_network") or payload.get("error"):
        error = str(payload.get("error") or "")
        if error in {"TimeoutError", "ConnectionError", "OSError", "SSLError", "ConnectTimeout"}:
            return StageSevenPreflightStatus.BLOCKED_NETWORK
        if payload.get("blocked_network"):
            return StageSevenPreflightStatus.BLOCKED_NETWORK
    if payload.get("skipped") or payload.get("status") == "not_applicable":
        return StageSevenPreflightStatus.NOT_APPLICABLE
    if payload.get("refreshed"):
        return StageSevenPreflightStatus.REFRESHED
    if payload.get("ok") and not payload.get("auth_failed"):
        return StageSevenPreflightStatus.HEALTHY
    if payload.get("auth_failed"):
        return StageSevenPreflightStatus.BLOCKED_AUTH
    return StageSevenPreflightStatus.NOT_APPLICABLE


def classify_preflight_error(exc: BaseException | str) -> StageSevenPreflightStatus:
    name = type(exc).__name__ if isinstance(exc, BaseException) else str(exc or "")
    if name in {"TimeoutError", "CancelledError"}:
        return StageSevenPreflightStatus.BLOCKED_NETWORK
    lowered = name.lower()
    if any(token in lowered for token in ("dns", "connection", "connect", "ssl", "tls", "timeout")):
        return StageSevenPreflightStatus.BLOCKED_NETWORK
    return StageSevenPreflightStatus.BLOCKED_NETWORK


def _step_request_id(step: FlowStep) -> str:
    return str((step.source_meta or {}).get("request_id") or "")


def _is_noise_path(path: str) -> bool:
    lowered = str(path or "").lower()
    return any(marker in lowered for marker in _NOISE_PATH_MARKERS)


def _usage_ids(capability: FlowCapability, usage: str) -> tuple[set[str], set[str]]:
    step_ids: set[str] = set()
    request_ids: set[str] = set()
    for ref in capability.request_refs:
        if ref.usage != usage:
            continue
        if ref.step_id:
            step_ids.add(str(ref.step_id))
        if ref.request_id:
            request_ids.add(str(ref.request_id))
    for node in capability.nodes or []:
        if not isinstance(node, dict) or str(node.get("usage") or "") != usage:
            continue
        if node.get("step_id"):
            step_ids.add(str(node.get("step_id") or ""))
        if node.get("request_id"):
            request_ids.add(str(node.get("request_id") or ""))
    return step_ids, request_ids


def build_stage_seven_scope(
    spec: FlowSpec | dict[str, Any],
    *,
    baseline: FlowSpec | dict[str, Any] | None = None,
) -> StageSevenScope:
    """Exact Stage 6 capability closures used by Stage 7 verification."""

    current = _as_spec(spec)
    source = _as_spec(baseline) if baseline is not None else current
    steps = {step.step_id: step for step in current.steps}
    internal_ids = {
        str(item)
        for item in (current.meta or {}).get("internal_step_ids") or []
        if item
    }
    execute_step_ids: dict[str, tuple[str, ...]] = {}
    members_by_capability: dict[str, frozenset[str]] = {}
    all_members: set[str] = set()
    preflight_ids: set[str] = set()
    option_step_ids: set[str] = set()
    option_request_ids: set[str] = set()
    fact_step_ids: set[str] = set()
    fact_request_ids: set[str] = set()
    write_ids: set[str] = set()
    enum_targets: set[str] = set()

    for capability in source.capabilities:
        members: set[str] = set()
        execute_ids, execute_requests = _usage_ids(capability, "execute")
        preflight, preflight_requests = _usage_ids(capability, "preflight")
        option_steps, option_requests = _usage_ids(capability, "option_source")
        fact_steps, fact_requests = _usage_ids(capability, "fact_check")
        members.update(execute_ids)
        members.update(preflight)
        members.update(option_steps)
        members.update(fact_steps)
        members.update(str(item) for item in capability.step_ids if item)
        members.discard("")
        # Recurse upstream reads that have exact evidence into this capability.
        changed = True
        while changed:
            changed = False
            for link in current.links:
                target_id = str(link.target_step_id or "")
                source_id = str(link.source_step_id or "")
                if target_id not in members or source_id in members:
                    continue
                if source_id in internal_ids and source_id not in members:
                    continue
                source_step = steps.get(source_id)
                if source_step is None:
                    continue
                if (source_step.method or "GET").upper() in _WRITE_METHODS:
                    continue
                evidence = link.evidence if isinstance(link.evidence, dict) else {}
                exact = bool(
                    link.confirmed
                    or (link.meta or {}).get("verified") is True
                    or evidence.get("source_request_id")
                    or evidence.get("source_path")
                    or link.source_path
                )
                if not exact:
                    continue
                members.add(source_id)
                preflight.add(source_id)
                changed = True
        for item in list(capability.inputs or []) + list(capability.request_fields or []):
            source_request = str((item.source or {}).get("request_id") or item.request_id or "")
            if source_request:
                option_request_ids.add(source_request)
        execute_step_ids[capability.capability_id] = tuple(sorted(execute_ids))
        members_by_capability[capability.capability_id] = frozenset(members)
        all_members.update(members)
        preflight_ids.update(preflight)
        option_step_ids.update(option_steps)
        option_request_ids.update(option_requests)
        fact_step_ids.update(fact_steps)
        fact_request_ids.update(fact_requests | execute_requests | preflight_requests)
        for step_id in members:
            step = steps.get(step_id)
            if step is None:
                continue
            if (step.method or "GET").upper() in _WRITE_METHODS:
                write_ids.add(step_id)
            for param in step.params:
                if str(param.type or "") in {"enum", "list-enum"}:
                    enum_targets.add(f"{step_id}:{param.path}")

    link_ids = {
        link.link_id
        for link in current.links
        if link.source_step_id in all_members and link.target_step_id in all_members
    }
    return StageSevenScope(
        capability_ids=tuple(capability.capability_id for capability in source.capabilities),
        execute_step_ids=execute_step_ids,
        member_step_ids=frozenset(all_members),
        preflight_step_ids=frozenset(preflight_ids),
        option_source_step_ids=frozenset(option_step_ids),
        option_source_request_ids=frozenset(option_request_ids),
        fact_check_step_ids=frozenset(fact_step_ids),
        fact_check_request_ids=frozenset(fact_request_ids),
        link_ids=frozenset(link_ids),
        write_step_ids=frozenset(write_ids),
        enum_targets=frozenset(enum_targets),
        protected_contract=protected_contract_payload(source),
        baseline_fingerprint=baseline_fingerprint(source),
        members_by_capability=members_by_capability,
    )


def in_scope_unverified(
    spec: FlowSpec | dict[str, Any],
    scope: StageSevenScope,
) -> list[dict[str, Any]]:
    current = _as_spec(spec)
    rows: list[dict[str, Any]] = []
    for item in (current.meta or {}).get("unverified") or []:
        if not isinstance(item, dict):
            continue
        target_id = str(item.get("target_id") or "")
        kind = str(item.get("target_kind") or "")
        step_id = target_id.split(":", 1)[0] if ":" in target_id else target_id
        if kind in {"write_verify", "enum"} and step_id not in scope.member_step_ids:
            continue
        if kind in {"dependency", "dependency_candidate"} and target_id not in scope.link_ids:
            if not any(target_id == link.link_id for link in current.links if link.link_id in scope.link_ids):
                if step_id and step_id not in scope.member_step_ids:
                    continue
        if kind == "release_issue" and target_id.startswith("unassigned:"):
            continue
        rows.append(dict(item))
    return rows


def callable_covers_stage_six(baseline: FlowSpec, callable_spec: FlowSpec | None) -> bool:
    if callable_spec is None:
        return False
    expected = [capability.capability_id for capability in baseline.capabilities]
    actual = [capability.capability_id for capability in callable_spec.capabilities]
    return bool(expected) and actual == expected


def compute_publishable(
    *,
    status: StageSevenStatus,
    all_verified: bool,
    unverified: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    preflight: StageSevenPreflightStatus | str,
    release_status: str,
    callable_spec: FlowSpec | None,
    baseline: FlowSpec | dict[str, Any],
    working: FlowSpec | dict[str, Any],
    working_fp: str,
    rechecked_fp: str,
) -> bool:
    """Single publish gate. Empty issue lists are never sufficient."""

    preflight_status = (
        preflight
        if isinstance(preflight, StageSevenPreflightStatus)
        else preflight_status_of({"status": preflight})
    )
    baseline_spec = _as_spec(baseline)
    working_spec = _as_spec(working)
    return (
        status == StageSevenStatus.VERIFIED
        and all_verified is True
        and not list(unverified)
        and preflight_status in {
            StageSevenPreflightStatus.HEALTHY,
            StageSevenPreflightStatus.REFRESHED,
            StageSevenPreflightStatus.NOT_APPLICABLE,
        }
        and release_status == "ready"
        and callable_covers_stage_six(baseline_spec, callable_spec)
        and protected_contract_fingerprint(baseline_spec) == protected_contract_fingerprint(working_spec)
        and working_fp == rechecked_fp
        and bool(working_fp)
    )


def derive_stage_seven_status(
    *,
    preflight: StageSevenPreflightStatus,
    all_verified: bool,
    publish_ready: bool,
    waiting_operator: bool,
    cancelled: bool,
    failed: bool,
    contract_changed: bool,
    budget_or_stall: bool,
    running: bool = False,
) -> StageSevenStatus:
    if cancelled:
        return StageSevenStatus.CANCELLED
    if failed:
        return StageSevenStatus.FAILED
    if preflight in {
        StageSevenPreflightStatus.BLOCKED_AUTH,
        StageSevenPreflightStatus.BLOCKED_NETWORK,
    }:
        return StageSevenStatus.BLOCKED_EXTERNAL
    if contract_changed:
        return StageSevenStatus.INCOMPLETE
    if waiting_operator:
        return StageSevenStatus.WAITING_OPERATOR
    if all_verified and publish_ready:
        return StageSevenStatus.VERIFIED
    if budget_or_stall:
        return StageSevenStatus.INCOMPLETE
    if running:
        return StageSevenStatus.RUNNING
    return StageSevenStatus.INCOMPLETE


def evaluate_stage_seven_verdict(
    *,
    baseline: FlowSpec | dict[str, Any],
    working: FlowSpec | dict[str, Any],
    scope: StageSevenScope,
    verification_report: dict[str, Any],
    preflight: dict[str, Any],
    release,
    attempt_id: str,
    revision: int,
    waiting_operator: bool = False,
    cancelled: bool = False,
    failed: bool = False,
    budget_or_stall: bool = False,
    running: bool = False,
    extra_issues: list[dict[str, Any]] | None = None,
) -> StageSevenVerdict:
    from dano.onboarding.recording_release import ReleaseDecision

    baseline_spec = _as_spec(baseline)
    working_spec = _as_spec(working)
    preflight_status = preflight_status_of(preflight)
    unverified = in_scope_unverified(working_spec, scope)
    report = dict(verification_report or {})
    todos = list(report.get("todos") or [])
    all_verified = (not todos) and (not unverified) and report.get("all_verified") is True
    contract_changed = False
    try:
        assert_stage_six_contract_preserved(baseline_spec, working_spec)
    except StageSixContractChanged:
        contract_changed = True
        all_verified = False
    extra = [dict(item) for item in extra_issues or [] if isinstance(item, dict)]
    if any(
        str(item.get("check_code") or item.get("issue_id") or "") == STAGE_SIX_CONTRACT_CHANGED
        for item in extra
    ):
        contract_changed = True
        all_verified = False
    decision: ReleaseDecision | None = release if isinstance(release, ReleaseDecision) else None
    release_status = str(getattr(decision, "status", "") or (release or {}).get("status") or "")
    callable_spec = getattr(decision, "callable_spec", None)
    working_fp = working_fingerprint(working_spec)
    publish_ready = (
        all_verified
        and not contract_changed
        and release_status == "ready"
        and callable_covers_stage_six(baseline_spec, callable_spec)
        and preflight_status.value in PREFLIGHT_HEALTHY_STATUSES
    )
    status = derive_stage_seven_status(
        preflight=preflight_status,
        all_verified=all_verified,
        publish_ready=publish_ready,
        waiting_operator=waiting_operator,
        cancelled=cancelled,
        failed=failed,
        contract_changed=contract_changed,
        budget_or_stall=budget_or_stall,
        running=running and not publish_ready,
    )
    publishable = compute_publishable(
        status=status,
        all_verified=all_verified,
        unverified=unverified,
        preflight=preflight_status,
        release_status=release_status,
        callable_spec=callable_spec,
        baseline=baseline_spec,
        working=working_spec,
        working_fp=working_fp,
        rechecked_fp=working_fp,
    )
    issues = [
        dict(item)
        for item in (report.get("release_issues") or [])
        if isinstance(item, dict)
    ]
    for item in extra:
        if item not in issues:
            issues.append(item)
    if contract_changed and not any(
        str(item.get("check_code") or item.get("issue_id") or "") == STAGE_SIX_CONTRACT_CHANGED
        for item in issues
    ):
        issues.append({
            "issue_id": STAGE_SIX_CONTRACT_CHANGED,
            "check_code": STAGE_SIX_CONTRACT_CHANGED,
            "message": "阶段 7 改变了阶段 6 的公开能力契约，已回滚该修补并阻塞发布",
            "resolver": "machine_repair",
        })
    if not todos and unverified:
        issues.append({
            "issue_id": VERIFICATION_UNRESOLVED,
            "check_code": VERIFICATION_UNRESOLVED,
            "message": "仍有范围内未证明项，不能发布",
            "resolver": "collect_evidence",
        })
    capability_results = dict((working_spec.meta or {}).get("capability_verification") or {})
    preflight_payload = {
        **dict(preflight or {}),
        "status": preflight_status.value,
    }
    return StageSevenVerdict(
        protocol=STAGE_SEVEN_PROTOCOL,
        attempt_id=attempt_id,
        revision=revision,
        status=status,
        publishable=publishable,
        all_verified=all_verified,
        baseline_fingerprint=scope.baseline_fingerprint or baseline_fingerprint(baseline_spec),
        protected_contract_fingerprint=protected_contract_fingerprint(baseline_spec),
        working_fingerprint=working_fp,
        preflight=preflight_payload,
        issues=tuple(issues),
        unverified=tuple(unverified),
        capability_results=capability_results,
        verification_summary={
            "todo_count": len(todos),
            "unverified_count": len(unverified),
            "all_verified": all_verified,
            "release_status": release_status,
        },
        release_status=release_status,
        callable_capability_ids=tuple(
            capability.capability_id for capability in (callable_spec.capabilities if callable_spec is not None else [])
        ),
    )


def verification_task_id(
    *,
    attempt_id: str,
    capability_id: str,
    kind: str,
    target_id: str,
    target_signature: str,
) -> str:
    return stable_hash({
        "attempt_id": attempt_id,
        "capability_id": capability_id,
        "kind": kind,
        "target_id": target_id,
        "target_signature": target_signature,
    })[:32]


def lookup_verification_task(
    spec: FlowSpec | dict[str, Any],
    *,
    task_id: str,
    target_signature: str,
) -> dict[str, Any] | None:
    current = _as_spec(spec)
    latest: dict[str, Any] | None = None
    for record in list((current.meta or {}).get("verification_log") or []):
        if not isinstance(record, dict):
            continue
        subject = dict(record.get("subject") or {})
        if str(subject.get("task_id") or record.get("task_id") or "") != task_id:
            continue
        latest = dict(record)
        latest_signature = str(subject.get("signature") or subject.get("target_signature") or "")
        latest["_signature_match"] = latest_signature == target_signature
    return latest


def should_skip_passed_task(
    spec: FlowSpec | dict[str, Any],
    *,
    task_id: str,
    target_signature: str,
) -> dict[str, Any] | None:
    record = lookup_verification_task(spec, task_id=task_id, target_signature=target_signature)
    if record is None:
        return None
    if record.get("status") == "passed" and record.get("_signature_match"):
        return record
    return None


def write_outcome_unknown(record: dict[str, Any] | None) -> bool:
    if not isinstance(record, dict):
        return False
    if record.get("status") == WRITE_OUTCOME_UNKNOWN:
        return True
    if str((record.get("subject") or {}).get("outcome") or "") == WRITE_OUTCOME_UNKNOWN:
        return True
    return record.get("status") not in {"passed", "failed"} and record.get("kind") == "write_execute"


def target_signature_for_todo(todo: dict[str, Any]) -> str:
    return stable_hash({
        "kind": todo.get("kind"),
        "target_id": todo.get("target_id"),
        "step_id": todo.get("step_id") or todo.get("target_step_id"),
        "link_id": todo.get("link_id"),
        "wire_path": todo.get("wire_path") or todo.get("path") or todo.get("target_path"),
        "source_step_id": todo.get("source_step_id"),
        "source_path": todo.get("source_path"),
        "write_request_id": todo.get("write_request_id"),
        "candidate_request_ids": todo.get("candidate_read_request_ids") or todo.get("candidate_request_ids"),
    })


def _has_positive_caller_evidence(evidence: list[Any], *, param: Any, step: FlowStep) -> bool:
    rows = [item for item in evidence if isinstance(item, dict)]
    if any(
        str(item.get("op") or "") in _CALLER_OPS
        or (
            item.get("editable") is True
            and item.get("disabled") is not True
            and item.get("readOnly") is not True
            and item.get("read_only") is not True
        )
        for item in rows
    ):
        return True
    if any(str(item.get("selected_record") or item.get("record_identity") or "") for item in rows):
        return True
    method = (step.method or "GET").upper()
    path = str(param.path or "")
    if method in _READ_METHODS and path.startswith("query.") and rows:
        if any(item.get("filter") is True or item.get("query_filter") is True for item in rows):
            return True
    return False


def _looks_non_caller_field(param: Any) -> bool:
    blob = " ".join(
        str(value or "")
        for value in (
            getattr(param, "key", ""),
            getattr(param, "path", ""),
            getattr(param, "source_kind", ""),
            ((getattr(param, "source", None) or {}) or {}).get("kind"),
        )
    ).lower()
    return any(token in blob for token in _NON_CALLER_HINTS)


def apply_stage_seven_recorded_evidence_fixes(spec: FlowSpec) -> FlowSpec:
    """Stage-7-only evidence fixes. Empty recorded values stay unknown."""

    from dano.execution.page.recording_live import _field_evidence_candidates
    from dano.execution.page.recording_field_identity import canonical_wire_path
    from dano.onboarding.recording_verify import (
        _ENUM_TYPES,
        _evidence_is_caller_edit,
        _evidence_is_readonly,
        _required_from_evidence,
        _set_param_from_evidence,
    )

    current = spec.model_copy(deep=True)
    for step in current.steps:
        projections: dict[str, str] = {}
        bindings_by_wire: dict[str, Any] = {}
        for binding in step.selects:
            wire = canonical_wire_path(step, str(binding.path or binding.id_path or ""))
            if wire:
                bindings_by_wire[wire] = binding
            for target_path in (binding.field_projections or {}):
                projections[canonical_wire_path(step, str(target_path))] = str(target_path)
        for param in step.params:
            if param.locked:
                continue
            wire = canonical_wire_path(step, param.path)
            evidence = [
                *list(_field_evidence_candidates(current, step, param) or []),
                *[item for item in (param.evidence or []) if isinstance(item, dict)],
            ]
            binding = bindings_by_wire.get(wire)
            if binding is None:
                binding = next(
                    (
                        item for item in step.selects
                        if item.path == param.path or item.id_path == param.path
                    ),
                    None,
                )
            caller_evidence = _evidence_is_caller_edit(evidence) or _has_positive_caller_evidence(
                evidence, param=param, step=step,
            )
            is_projection = wire in projections or (
                binding is not None
                and bool(binding.field_projections)
                and not binding.options
            )
            if param.source_kind in {
                "previous_response", "session", "page_default", "computed", "constant", "generated",
            }:
                param.exposed_to_user = False
            elif param.source_kind == "unknown":
                if _looks_non_caller_field(param):
                    param.exposed_to_user = False
                elif caller_evidence:
                    _set_param_from_evidence(
                        param,
                        "user_input",
                        exposed=True,
                        editable=True,
                        reason="录制已有选择/填写证据，字段由调用方提供",
                    )
                    param.required = False
                elif is_projection or _evidence_is_readonly(evidence):
                    _set_param_from_evidence(
                        param,
                        "previous_response",
                        exposed=False,
                        editable=False,
                        reason="录制已有只读投影证据，提交时从选中行带出",
                    )
                elif any(str(item.get("binding_status") or "") == "bound" for item in evidence):
                    _set_param_from_evidence(
                        param,
                        "page_default",
                        exposed=False,
                        editable=False,
                        reason="录制页面已带出该值，无人改动",
                    )
                else:
                    param.exposed_to_user = False
            if (
                not caller_evidence
                and (is_projection or _evidence_is_readonly(evidence))
                and param.source_kind not in {
                    "session", "page_default", "computed", "constant", "generated",
                }
            ):
                _set_param_from_evidence(
                    param,
                    "previous_response",
                    exposed=False,
                    editable=False,
                    reason="录制已有只读投影证据，提交时从选中行带出",
                )
            if binding is not None and binding.options and not param.enum_options:
                param.enum_options = list(binding.options)
                if binding.option_map and not param.enum_value_map:
                    param.enum_value_map = dict(binding.option_map)
                if param.type not in _ENUM_TYPES:
                    param.type = "list-enum" if binding.multi else "enum"
            required = _required_from_evidence(evidence)
            if (
                required is not None
                and str((param.source or {}).get("required_state") or "")
                not in {"required", "optional"}
            ):
                param.required = required
                param.source = {
                    **(param.source or {}),
                    "required_state": "required" if required else "optional",
                }
    return current


def normalize_stage_seven_working_copy(
    baseline_spec: FlowSpec | dict[str, Any],
    working_spec: FlowSpec | dict[str, Any],
    scope: StageSevenScope | None = None,
) -> FlowSpec:
    """Idempotent Stage 7 working-copy normalization."""

    from dano.onboarding.recording_verify import (
        assign_unassigned_internal_steps,
        finalize_verification_state,
    )

    baseline = _as_spec(baseline_spec)
    current = _as_spec(working_spec).model_copy(deep=True)
    current_scope = scope or build_stage_seven_scope(current, baseline=baseline)
    current = finalize_verification_state(current, rounds=0, max_rounds=0)[0]
    current = apply_stage_seven_recorded_evidence_fixes(current)
    current = assign_unassigned_internal_steps(current)
    try:
        assert_stage_six_contract_preserved(baseline, current)
    except StageSixContractChanged:
        current.meta = {
            **(current.meta or {}),
            "stage_seven": {
                **dict((current.meta or {}).get("stage_seven") or {}),
                "contract_changed": True,
            },
        }
    current.meta = {
        **(current.meta or {}),
        "stage_seven": {
            **dict((current.meta or {}).get("stage_seven") or {}),
            "scope_capability_ids": list(current_scope.capability_ids),
        },
    }
    return current


def new_attempt_id() -> str:
    return uuid4().hex


def checkpoint_dict(
    *,
    attempt_id: str,
    revision: int,
    status: StageSevenStatus | str,
    baseline: FlowSpec | dict[str, Any],
    working: FlowSpec | dict[str, Any],
    verdict: StageSevenVerdict | None = None,
    preflight: dict[str, Any] | None = None,
    capability_attempts: dict[str, Any] | None = None,
    operator_answers: dict[str, str] | None = None,
) -> dict[str, Any]:
    working_spec = _as_spec(working)
    baseline_spec = _as_spec(baseline)
    report = dict((working_spec.meta or {}).get("verification_run") or {})
    payload_verdict = verdict.to_dict() if verdict is not None else {}
    return {
        "protocol": STAGE_SEVEN_PROTOCOL,
        "attempt_id": attempt_id,
        "revision": int(revision),
        "status": str(status),
        "baseline_fingerprint": baseline_fingerprint(baseline_spec),
        "protected_contract_fingerprint": protected_contract_fingerprint(baseline_spec),
        "working_fingerprint": working_fingerprint(working_spec),
        "working_flow_spec": working_spec.model_dump(mode="json"),
        "verification_report": report,
        "preflight": dict(preflight or payload_verdict.get("preflight") or {}),
        "issues": list(payload_verdict.get("issues") or []),
        "unverified": list((working_spec.meta or {}).get("unverified") or []),
        "capability_results": dict((working_spec.meta or {}).get("capability_verification") or {}),
        "capability_attempts": dict(capability_attempts or {}),
        "operator_answers": dict(operator_answers or {}),
        "verdict": payload_verdict,
        "updated_at": _now_iso(),
    }


def apply_stage_seven_checkpoint_patch(
    body: dict[str, Any],
    *,
    expected_attempt_id: str,
    expected_revision: int,
    checkpoint: dict[str, Any],
) -> dict[str, Any] | None:
    """Optimistic merge into the recording-result JSON body. None = conflict."""

    existing = body.get("stage_seven") if isinstance(body.get("stage_seven"), dict) else None
    if existing:
        if str(existing.get("attempt_id") or "") != str(expected_attempt_id or ""):
            return None
        if int(existing.get("revision") or 0) != int(expected_revision):
            return None
        next_revision = int(expected_revision) + 1
    else:
        if int(expected_revision) not in {0}:
            return None
        next_revision = 0
    stored = dict(checkpoint)
    stored["revision"] = next_revision
    next_body = dict(body)
    next_body["stage_seven"] = stored
    next_body["machine_verification_status"] = str(stored.get("status") or "")
    next_body["stage_seven_attempt_id"] = str(stored.get("attempt_id") or "")
    next_body["stage_seven_updated_at"] = str(stored.get("updated_at") or "")
    next_body["stage_seven_fingerprint"] = str(stored.get("working_fingerprint") or "")
    if stored.get("status") != StageSevenStatus.VERIFIED:
        next_body["skill_plan_valid"] = False
        if next_body.get("skill_export_status") in {"exported", "succeeded"}:
            next_body["skill_needs_reexport"] = True
    return next_body


def client_checkpoint_projection(checkpoint: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(checkpoint, dict) or not checkpoint:
        return None
    return {
        "protocol": checkpoint.get("protocol") or STAGE_SEVEN_PROTOCOL,
        "attempt_id": checkpoint.get("attempt_id"),
        "revision": checkpoint.get("revision"),
        "status": checkpoint.get("status"),
        "baseline_fingerprint": checkpoint.get("baseline_fingerprint"),
        "working_fingerprint": checkpoint.get("working_fingerprint"),
        "preflight": dict(checkpoint.get("preflight") or {}),
        "issues": list(checkpoint.get("issues") or []),
        "unverified": list(checkpoint.get("unverified") or []),
        "capability_results": dict(checkpoint.get("capability_results") or {}),
        "updated_at": checkpoint.get("updated_at"),
        "publishable": bool((checkpoint.get("verdict") or {}).get("publishable")),
    }


def load_resumable_working_spec(
    body: dict[str, Any],
    *,
    reset_stage_seven: bool = False,
) -> tuple[dict[str, Any], dict[str, Any] | None, str]:
    """Return (draft, checkpoint, block_reason). block_reason is non-empty on mismatch."""

    baseline = body.get("flow_spec")
    if not isinstance(baseline, dict):
        return {}, None, "录制结果没有完整 FlowSpec"
    if reset_stage_seven:
        return dict(baseline), None, ""
    checkpoint = body.get("stage_seven") if isinstance(body.get("stage_seven"), dict) else None
    if not checkpoint:
        return dict(baseline), None, ""
    stored_baseline = str(checkpoint.get("baseline_fingerprint") or "")
    actual_baseline = baseline_fingerprint(baseline)
    if stored_baseline and stored_baseline != actual_baseline:
        return dict(baseline), checkpoint, "阶段 7 检查点与当前阶段 6 基线不一致，已阻塞自动恢复"
    working = checkpoint.get("working_flow_spec")
    if isinstance(working, dict) and working:
        return dict(working), checkpoint, ""
    return dict(baseline), checkpoint, ""


def resumable_checkpoint_status(checkpoint: dict[str, Any] | None) -> bool:
    if not isinstance(checkpoint, dict):
        return False
    return str(checkpoint.get("status") or "") in {
        StageSevenStatus.INCOMPLETE,
        StageSevenStatus.BLOCKED_EXTERNAL,
        StageSevenStatus.FAILED,
        StageSevenStatus.WAITING_OPERATOR,
        StageSevenStatus.RUNNING,
        StageSevenStatus.CANCELLED,
    }
