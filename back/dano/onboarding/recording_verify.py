"""Bounded autonomous verification for finalized page recordings."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from urllib.parse import urlparse

from dano.execution.page.recording_field_identity import canonical_wire_path
from dano.execution.page.value_tracing import discover_response_key_maps, discover_value_links


_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_CALLER_OPS = frozenset({"fill", "select", "pick"})
_ENUM_TYPES = frozenset({"enum", "list-enum"})
_REPLAY_TODO_KINDS = frozenset({"dependency", "dependency_candidate", "write_verify", "enum"})
_REPLAY_ISSUE_CODES = frozenset({
    "dependency",
    "dependency_candidate",
    "write_verify",
    "enum",
    "write_readback_missing",
    "dependency_verification_missing",
    "dependency_verification_stale",
    "enum_options_unverified",
})
FLOW_GROUP_KEY = "__flow__"
AUTH_EXPIRED_MESSAGE = "录制登录态已失效，机器验证已阻塞，当前结果不会发布"
REPLAY_SKIPPED_MESSAGE = AUTH_EXPIRED_MESSAGE
_NOISE_PATH_MARKERS = (
    "/tenant/",
    "get-by-website",
    "/im/",
    "online-status",
    "/telemetry",
    "/metrics",
    "/actuator",
)
# machine_repair check_code → python patch, leftover dead_end, or a real Skill op.
MACHINE_REPAIR_DISPOSITION = {
    "capability_validation_failed": "python",
    "unassigned_business_step": "python",
    "unassigned_materialized_step": "python",
    "capability_internal_field_exposed": "python",
    "caller_field_not_compiled": "dead_end",
    "public_execute_anchor_invalid": "dead_end",
    "capability_usage_invalid": "dead_end",
    "request_compilation_failed": "dead_end",
    "dry_run_failed": "dead_end",
    "dynamic_structure_binding_missing": "skill",
    "dynamic_structure_recorded_key_exposed": "dead_end",
    "dynamic_structure_stale_leaf": "dead_end",
}


def normalized_request_path(url_or_path: str) -> str:
    raw = str(url_or_path or "").strip()
    if not raw:
        return ""
    path = urlparse(raw).path if "://" in raw else raw.split("?", 1)[0]
    path = (path or raw.split("?", 1)[0]).strip()
    return path.rstrip("/") or path


def is_replay_issue_code(code: str) -> bool:
    return str(code or "") in _REPLAY_ISSUE_CODES


def replay_auth_failed(status: Any, body: Any) -> bool:
    """True only for login expiry, not an arbitrary token mention in a payload."""
    if status in {401, 403, "401", "403"}:
        return True
    payload = body
    text = ""
    if isinstance(body, str):
        text = body
        try:
            payload = json.loads(body)
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = body
    if isinstance(payload, dict):
        code = payload.get("code")
        if code in {401, 403, "401", "403"}:
            return True
        text = str(payload.get("msg") or payload.get("message") or payload.get("error") or text or "")
    return "未登录" in text or "登录已过期" in text


def _is_noise_path(path: str) -> bool:
    lowered = normalized_request_path(path).lower()
    return any(marker in lowered for marker in _NOISE_PATH_MARKERS)


def _fact_as_replay_request(fact) -> dict[str, Any]:  # noqa: ANN001
    payload = fact.model_dump(mode="json") if hasattr(fact, "model_dump") else dict(fact)
    payload.setdefault("method", getattr(fact, "method", None) or payload.get("method") or "GET")
    payload.setdefault("path", getattr(fact, "path", None) or payload.get("path") or "")
    payload.setdefault("url", getattr(fact, "url", None) or payload.get("url") or payload.get("path") or "")
    payload.setdefault("headers", getattr(fact, "headers", None) or payload.get("headers") or {})
    payload.setdefault("query", getattr(fact, "query", None) or payload.get("query") or {})
    return payload


def select_preflight_probe(spec) -> dict[str, Any] | None:  # noqa: ANN001
    """Pick a capability-closure business GET; never a tenant/public probe."""
    facts_by_id = {
        str(fact.request_id or ""): fact
        for fact in spec.request_facts.requests
        if str(fact.request_id or "")
    }

    def from_request_id(request_id: str, method: str, path: str) -> dict[str, Any] | None:
        if (method or "GET").upper() != "GET" or _is_noise_path(path):
            return None
        fact = facts_by_id.get(request_id)
        if fact is not None and (fact.method or "GET").upper() == "GET" and not _is_noise_path(fact.path or fact.url):
            return _fact_as_replay_request(fact)
        return None

    for capability in spec.capabilities:
        for node in capability.nodes or []:
            if not isinstance(node, dict) or str(node.get("type") or "") != "call":
                continue
            if str(node.get("usage") or "") not in {"execute", "preflight"}:
                continue
            probe = from_request_id(
                str(node.get("request_id") or ""),
                str(node.get("method") or "GET"),
                str(node.get("path") or ""),
            )
            if probe is not None:
                return probe
            step_id = str(node.get("step_id") or "")
            step = next((item for item in spec.steps if item.step_id == step_id), None)
            if step is None:
                continue
            probe = from_request_id(
                str((step.source_meta or {}).get("request_id") or ""),
                step.method,
                step.path or step.url,
            )
            if probe is not None:
                return probe

    for capability in spec.capabilities:
        for step_id in capability.step_ids:
            step = next((item for item in spec.steps if item.step_id == step_id), None)
            if step is None:
                continue
            probe = from_request_id(
                str((step.source_meta or {}).get("request_id") or ""),
                step.method,
                step.path or step.url,
            )
            if probe is not None:
                return probe
    return None


def _same_resource_family(write_path: str, read_path: str) -> bool:
    write = normalized_request_path(write_path)
    read = normalized_request_path(read_path)
    if not write or not read:
        return False
    write_parent = write.rsplit("/", 1)[0]
    read_parent = read.rsplit("/", 1)[0]
    return bool(write_parent and (read.startswith(write_parent) or write.startswith(read_parent)))


def candidate_read_request_ids(spec, write_step) -> list[str]:  # noqa: ANN001
    """Same-resource GET/HEAD reads, excluding tenant/IM/telemetry noise."""
    write_path = str(write_step.path or write_step.url or "")
    write_request_id = str((write_step.source_meta or {}).get("request_id") or "")
    ranked: list[tuple[int, int, str]] = []
    for fact in spec.request_facts.requests:
        method = (fact.method or "GET").upper()
        if method not in {"GET", "HEAD"}:
            continue
        if str(fact.request_id or "") == write_request_id:
            continue
        path = str(fact.path or fact.url or "")
        if _is_noise_path(path):
            continue
        if not _same_resource_family(write_path, path):
            continue
        lowered = normalized_request_path(path).lower()
        name_rank = 0 if lowered.endswith("/get") else 1 if lowered.endswith("/page") else 2
        ranked.append((name_rank, str(fact.request_id or "")))
    ranked.sort()
    return [request_id for _name, request_id in ranked if request_id][:8]


def machine_repair_disposition(check_code: str) -> str:
    return MACHINE_REPAIR_DISPOSITION.get(str(check_code or ""), "dead_end")


def _append_unverified(spec, *, target_kind: str, target_id: str, reason: str) -> None:  # noqa: ANN001
    if not target_kind or not target_id:
        return
    existing = [
        item for item in (spec.meta or {}).get("unverified") or [] if isinstance(item, dict)
    ]
    if any(
        str(item.get("target_kind") or "") == target_kind
        and str(item.get("target_id") or "") == target_id
        for item in existing
    ):
        return
    spec.meta = {
        **(spec.meta or {}),
        "unverified": [
            *existing,
            {
                "target_kind": target_kind,
                "target_id": target_id,
                "reason": reason,
                "actor": "orchestrator",
            },
        ],
    }


def mark_issues_unverified(spec, issues, *, reason: str) -> None:  # noqa: ANN001
    for issue in issues:
        code = str(issue.code or "")
        target = issue.target or {}
        if code in {"write_verify", "write_readback_missing"}:
            _append_unverified(
                spec,
                target_kind="write_verify",
                target_id=str(target.get("step_id") or target.get("target_id") or issue.issue_id),
                reason=reason,
            )
        elif code in {"dependency", "dependency_verification_missing", "dependency_verification_stale"}:
            _append_unverified(
                spec,
                target_kind="dependency",
                target_id=str(target.get("target_id") or issue.issue_id),
                reason=reason,
            )
        elif code == "dependency_candidate":
            _append_unverified(
                spec,
                target_kind="dependency_candidate",
                target_id=str(target.get("target_id") or issue.issue_id),
                reason=reason,
            )
        elif code in {"enum", "enum_options_unverified"}:
            wire = str(target.get("wire_path") or target.get("path") or "")
            step_id = str(target.get("step_id") or "")
            _append_unverified(
                spec,
                target_kind="enum",
                target_id=str(target.get("target_id") or (f"{step_id}:{wire}" if step_id and wire else issue.issue_id)),
                reason=reason,
            )
        else:
            _append_unverified(
                spec,
                target_kind="release_issue",
                target_id=str(issue.issue_id),
                reason=reason,
            )


def assign_unassigned_internal_steps(spec):  # noqa: ANN001, ANN202
    """Attach orphaned steps to a capability as preflight/option_source, else mark internal."""
    current = spec.model_copy(deep=True)
    owned: set[str] = set()
    for capability in current.capabilities:
        owned.update(str(item) for item in capability.step_ids if item)
        owned.update(
            str(node.get("step_id") or "")
            for node in capability.nodes or []
            if isinstance(node, dict) and node.get("step_id")
        )
        owned.update(str(ref.step_id or "") for ref in capability.request_refs if ref.step_id)
    owned.discard("")
    internal_ids = {
        str(item)
        for item in (current.meta or {}).get("internal_step_ids") or []
        if item
    }
    member_sets = [
        (
            capability,
            {
                str(item) for item in capability.step_ids if item
            } | {
                str(node.get("step_id") or "")
                for node in capability.nodes or []
                if isinstance(node, dict) and node.get("step_id")
            },
        )
        for capability in current.capabilities
    ]
    for step in current.steps:
        if step.step_id in owned or step.step_id in internal_ids:
            continue
        request_id = str((step.source_meta or {}).get("request_id") or "")
        assigned = False
        for capability, members in member_sets:
            if any(
                (link.confirmed or (link.meta or {}).get("verified") is True)
                and link.source_step_id == step.step_id
                and link.target_step_id in members
                for link in current.links
            ):
                _attach_capability_step(capability, step, request_id, usage="preflight")
                assigned = True
                break
            if request_id and any(
                str(binding.source_request_id or "") == request_id
                for member_id in members
                for member in current.steps if member.step_id == member_id
                for binding in member.selects
            ):
                _attach_capability_step(capability, step, request_id, usage="option_source")
                assigned = True
                break
        if assigned:
            owned.add(step.step_id)
        else:
            internal_ids.add(step.step_id)
    if internal_ids:
        current.meta = {**(current.meta or {}), "internal_step_ids": sorted(internal_ids)}
    return current


def _attach_capability_step(capability, step, request_id: str, *, usage: str) -> None:  # noqa: ANN001
    if step.step_id not in capability.step_ids:
        capability.step_ids = [*capability.step_ids, step.step_id]
    if not any(str(ref.step_id or "") == step.step_id for ref in capability.request_refs):
        from dano.execution.page.flow_spec_core.models import CapabilityRequestRef

        capability.request_refs = [
            *capability.request_refs,
            CapabilityRequestRef(
                request_id=request_id,
                step_id=step.step_id,
                method=step.method,
                path=step.path or step.url,
                usage=usage,
                origin="repair",
                confirmed=True,
                reason="阶段七按依赖或选项源归属内部步骤",
            ),
        ]
    if usage != "option_source" or step.step_id:
        if not any(
            isinstance(node, dict) and str(node.get("step_id") or "") == step.step_id
            for node in capability.nodes or []
        ):
            capability.nodes = [
                *list(capability.nodes or []),
                {
                    "id": f"{usage}_{step.step_id[:8]}",
                    "type": "call",
                    "usage": usage,
                    "request_id": request_id,
                    "method": step.method,
                    "path": step.path or step.url,
                    "step_id": step.step_id,
                },
            ]


def _field_key(step_id: str, path: str) -> tuple[str, str]:
    return (str(step_id or ""), str(path or "").removeprefix("body.").removeprefix("query."))


def _set_param_from_evidence(
    param,
    source_kind: str,
    *,
    exposed: bool,
    editable: bool,
    reason: str,
) -> None:  # noqa: ANN001
    param.source_kind = source_kind
    param.exposed_to_user = exposed
    param.editable = editable
    if source_kind == "user_input":
        param.category = "user_param"
    elif source_kind in {"previous_response", "page_default", "computed"}:
        param.category = "runtime_var"
        param.exposed_to_user = False
    param.source = {
        **(param.source or {}),
        "kind": source_kind,
        "recorded_evidence": True,
        "reason": reason,
    }
    param.reason = reason
    param.evidence = [
        *list(param.evidence or []),
        {
            "actor": "stage_seven",
            "kind": "recorded_evidence_fix",
            "source_kind": source_kind,
            "reason": reason,
        },
    ]


def _evidence_is_caller_edit(evidence: list[dict[str, Any]]) -> bool:
    return any(
        str(item.get("op") or "") in _CALLER_OPS
        or item.get("editable") is True
        for item in evidence
        if isinstance(item, dict)
    )


def _evidence_is_readonly(evidence: list[dict[str, Any]]) -> bool:
    return any(
        item.get("editable") is False
        or item.get("read_only") is True
        or item.get("disabled") is True
        for item in evidence
        if isinstance(item, dict)
    ) and not _evidence_is_caller_edit(evidence)


def _required_from_evidence(evidence: list[dict[str, Any]]) -> bool | None:
    observed = [
        bool(item.get("required_observed", item.get("required")))
        for item in evidence
        if isinstance(item, dict) and ("required_observed" in item or "required" in item)
    ]
    if not observed:
        return None
    return any(observed)


def apply_recorded_evidence_fixes(spec):  # noqa: ANN001, ANN202
    """Bind unknown fields and attach recorded enums from facts already on the spec.

    Stage 7 only: this does not recompile capabilities or rewrite stage 1–6
    capture. It applies conclusions that the recording already contains so
    the verify/repair loop does not ask Pi to rediscover them.
    """
    from dano.execution.page.recording_live import _field_evidence_candidates

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
            evidence = _field_evidence_candidates(current, step, param)
            binding = bindings_by_wire.get(wire)
            if binding is None:
                binding = next(
                    (
                        item for item in step.selects
                        if item.path == param.path or item.id_path == param.path
                    ),
                    None,
                )
            if param.source_kind == "unknown":
                if binding is not None or _evidence_is_caller_edit(evidence):
                    _set_param_from_evidence(
                        param,
                        "user_input",
                        exposed=True,
                        editable=True,
                        reason="录制已有选择/填写证据，字段由调用方提供",
                    )
                elif wire in projections or _evidence_is_readonly(evidence):
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


def _request_step_id(spec, request_id: str) -> str:  # noqa: ANN001
    for step in spec.steps:
        if str((step.source_meta or {}).get("request_id") or "") == request_id:
            return step.step_id
    usage = (spec.request_facts.usage or {}).get(request_id)
    return str(usage.materialized_step_id or "") if usage is not None else ""


def _candidate_link_id(candidate: dict[str, Any]) -> str:
    signature = "\n".join(
        str(candidate.get(key) or "")
        for key in (
            "source_request_id",
            "source_path",
            "source_collection_path",
            "source_key_path",
            "source_label_path",
            "target_request_id",
            "target_path",
            "target_container_path",
        )
    )
    return f"candidate-{hashlib.sha256(signature.encode('utf-8')).hexdigest()[:12]}"


def _dependency_candidate_todos(spec, skipped: set[tuple[str, str]] | None = None) -> list[dict[str, Any]]:  # noqa: ANN001
    """Promote exact captured value links when no agent-authored link exists yet."""
    rows = [fact.model_dump(mode="json") for fact in spec.request_facts.requests]
    todos: list[dict[str, Any]] = []
    candidates = [
        *discover_value_links(rows),
        *discover_response_key_maps(rows),
    ]
    for candidate in candidates:
        source_request_id = str(candidate.get("source_request_id") or "")
        target_request_id = str(candidate.get("target_request_id") or "")
        source_step_id = _request_step_id(spec, source_request_id)
        target_step_id = _request_step_id(spec, target_request_id)
        if not source_step_id or not target_step_id:
            continue
        if not (
            candidate.get("source_path")
            or candidate.get("source_collection_path")
            or candidate.get("source_key_path")
        ):
            continue
        if not (
            candidate.get("target_path")
            or candidate.get("target_container_path")
        ):
            continue
        reported_source_path = str(
            candidate.get("source_path") or candidate.get("source_collection_path") or ""
        )
        reported_target_path = str(
            candidate.get("target_path") or candidate.get("target_container_path") or ""
        )
        source_path = reported_source_path.removeprefix("response.")
        target_step = next((step for step in spec.steps if step.step_id == target_step_id), None)
        target_path = canonical_wire_path(
            target_step,
            reported_target_path.removeprefix("request."),
        )
        dependency_kind = str(candidate.get("kind") or "value")
        if any(
            link.source_step_id == source_step_id
            and str(link.source_path or "").removeprefix("response.") == source_path
            and link.target_step_id == target_step_id
            and canonical_wire_path(target_step, str(link.target_path or "")) == target_path
            and str(link.kind or "value") == dependency_kind
            for link in spec.links
        ):
            continue
        link_id = _candidate_link_id(candidate)
        if skipped and ("dependency_candidate", link_id) in skipped:
            continue
        todo = {
            "kind": "dependency_candidate",
            "dependency_kind": dependency_kind,
            "target_id": link_id,
            "link_id": link_id,
            "source_step_id": source_step_id,
            "source_request_id": source_request_id,
            "source_path": reported_source_path,
            "target_step_id": target_step_id,
            "target_request_id": target_request_id,
            "target_path": reported_target_path,
            "chain_request_ids": [source_request_id, target_request_id],
            "value_sample": str(candidate.get("value_sample") or "")[:128],
            "occurrences": int(candidate.get("occurrences") or 1),
            "confidence": 0.9,
            "suggested_tool": "submit_recording_repair",
            "completion_ops": ["propose_dependency", "verify_dependency", "confirm_dependency"],
        }
        if dependency_kind == "response_key_map":
            todo.update(
                {
                    "source_collection_path": str(candidate.get("source_collection_path") or ""),
                    "source_key_path": str(candidate.get("source_key_path") or ""),
                    "source_label_path": str(candidate.get("source_label_path") or ""),
                    "target_container_path": str(candidate.get("target_container_path") or ""),
                    "recorded_key_count": int(candidate.get("recorded_key_count") or 0),
                }
            )
        todos.append(todo)
    return todos


def _annotate_stage_seven_todo(todo: dict[str, Any], spec, scope) -> dict[str, Any]:  # noqa: ANN001
    from dano.onboarding.recording_stage_seven import target_signature_for_todo, verification_task_id

    capability_id = str(todo.get("capability_id") or "")
    step_id = str(todo.get("step_id") or todo.get("target_step_id") or "")
    if not capability_id and scope is not None:
        capability_id = scope.capability_for_step(step_id)
    todo["capability_id"] = capability_id
    todo["issue_id"] = str(todo.get("issue_id") or f"{todo.get('kind')}:{todo.get('target_id')}")
    todo["target_signature"] = str(todo.get("target_signature") or target_signature_for_todo(todo))
    todo["suggested_tool"] = str(todo.get("suggested_tool") or todo.get("suggested_executor") or "")
    todo["suggested_executor"] = str(todo.get("suggested_executor") or todo.get("suggested_tool") or "")
    todo["completion_operation"] = str(
        todo.get("completion_operation") or todo.get("completion_op") or ""
    )
    todo["evidence_refs"] = list(todo.get("evidence_refs") or [])
    todo["candidate_request_ids"] = list(
        todo.get("candidate_request_ids")
        or todo.get("candidate_read_request_ids")
        or todo.get("chain_request_ids")
        or []
    )
    if not todo.get("wire_path"):
        todo["wire_path"] = str(todo.get("target_path") or todo.get("path") or "")
    todo["task_id"] = str(
        todo.get("task_id")
        or verification_task_id(
            attempt_id=str(((spec.meta or {}).get("stage_seven") or {}).get("attempt_id") or ""),
            capability_id=capability_id,
            kind=str(todo.get("kind") or ""),
            target_id=str(todo.get("target_id") or ""),
            target_signature=str(todo["target_signature"]),
        )
    )
    return todo


def _capabilities_are_related(spec, source_cap: str, target_cap: str) -> bool:  # noqa: ANN001
    if not source_cap or not target_cap:
        return False
    if source_cap == target_cap:
        return True
    by_id = {capability.capability_id: capability for capability in spec.capabilities}
    source_name = str(getattr(by_id.get(source_cap), "name", "") or "")
    target_name = str(getattr(by_id.get(target_cap), "name", "") or "")
    source_keys = {source_cap, source_name} - {""}
    target_keys = {target_cap, target_name} - {""}
    for relation in spec.capability_relations or []:
        ends = {
            str(getattr(relation, "from_capability", "") or ""),
            str(getattr(relation, "to_capability", "") or ""),
        } - {""}
        if source_keys & ends and target_keys & ends:
            return True
    return False


def verification_todos(spec, scope=None) -> list[dict[str, Any]]:  # noqa: ANN001
    """Return the Stage 7 work queue for the Stage 6 capability closures."""
    from dano.onboarding.recording_stage_seven import build_stage_seven_scope

    current_scope = scope or build_stage_seven_scope(spec)
    internal_ids = {
        str(item) for item in (spec.meta or {}).get("internal_step_ids") or [] if item
    }
    todos: list[dict[str, Any]] = []
    for link in spec.links:
        if (link.meta or {}).get("verified") is True:
            continue
        if link.link_id not in current_scope.link_ids:
            continue
        source_cap = current_scope.capability_for_step(str(link.source_step_id or ""))
        target_cap = current_scope.capability_for_step(str(link.target_step_id or ""))
        if not _capabilities_are_related(spec, source_cap, target_cap):
            continue
        todos.append(
            _annotate_stage_seven_todo(
                {
                    "kind": "dependency",
                    "dependency_kind": str(link.kind or "value"),
                    "target_id": link.link_id,
                    "link_id": link.link_id,
                    "source_step_id": link.source_step_id,
                    "source_request_id": str((link.evidence or {}).get("source_request_id") or ""),
                    "source_path": link.source_path,
                    "target_step_id": link.target_step_id,
                    "target_request_id": str((link.evidence or {}).get("target_request_id") or ""),
                    "target_path": link.target_path,
                    "suggested_tool": "verify_dependency",
                    "suggested_executor": "verify_dependency",
                    "completion_op": "confirm_dependency",
                    "evidence_refs": [
                        value for value in (
                            str((link.meta or {}).get("verification_id") or ""),
                            str((link.evidence or {}).get("source_request_id") or ""),
                        ) if value
                    ],
                },
                spec,
                current_scope,
            )
        )
    for candidate in _dependency_candidate_todos(spec, set()):
        source_id = str(candidate.get("source_step_id") or "")
        target_id = str(candidate.get("target_step_id") or "")
        if source_id not in current_scope.member_step_ids or target_id not in current_scope.member_step_ids:
            continue
        source_cap = current_scope.capability_for_step(source_id)
        target_cap = current_scope.capability_for_step(target_id)
        if not _capabilities_are_related(spec, source_cap, target_cap):
            continue
        todos.append(_annotate_stage_seven_todo(candidate, spec, current_scope))
    for step in spec.steps:
        if (step.method or "").upper() not in _WRITE_METHODS:
            continue
        if step.step_id in internal_ids and step.step_id not in current_scope.member_step_ids:
            continue
        if step.step_id not in current_scope.write_step_ids:
            continue
        fact_check = step.fact_check or {}
        if fact_check.get("verified") is True and fact_check.get("verification_id"):
            continue
        todos.append(
            _annotate_stage_seven_todo(
                {
                    "kind": "write_verify",
                    "target_id": step.step_id,
                    "issue_id": f"write_verify:{step.step_id}",
                    "check_code": "write_verify",
                    "step_id": step.step_id,
                    "message": f"写操作 `{step.step_id}` 还没有回读校验，不能证明提交已生效",
                    "write_request_id": str((step.source_meta or {}).get("request_id") or ""),
                    "candidate_read_request_ids": candidate_read_request_ids(spec, step),
                    "candidate_request_ids": candidate_read_request_ids(spec, step),
                    "suggested_tool": "execute_write_with_verify",
                    "suggested_executor": "execute_write_with_verify",
                    "completion_op": "bind_verify_read",
                },
                spec,
                current_scope,
            )
        )
    from dano.execution.page.capability_validation import _capability_param_enum_issue

    for step in spec.steps:
        if step.step_id not in current_scope.member_step_ids:
            continue
        bindings: dict[str, Any] = {}
        for binding in step.selects:
            stored = str(binding.path or binding.id_path or "")
            if not stored:
                continue
            bindings[stored] = binding
            bindings[canonical_wire_path(step, stored)] = binding
        enum_paths = {
            canonical_wire_path(step, param.path) or param.path
            for param in step.params
            if param.type in _ENUM_TYPES
            and param.source_kind != "api_option"
            and _capability_param_enum_issue(param)
        }
        for stored, binding in list(bindings.items()):
            param = next(
                (
                    item for item in step.params
                    if canonical_wire_path(step, item.path) == canonical_wire_path(step, stored)
                    or item.path == stored
                ),
                None,
            )
            if param is not None and not _capability_param_enum_issue(param):
                continue
            if param is None and (
                binding.enum_confirmed is True
                or (binding.options and not (
                    binding.count and len(binding.options or []) < binding.count
                ))
            ):
                continue
            enum_paths.add(canonical_wire_path(step, stored) or stored)
        seen_enum: set[str] = set()
        for path in sorted(enum_paths):
            wire = canonical_wire_path(step, path) or path
            if wire in seen_enum:
                continue
            seen_enum.add(wire)
            binding = bindings.get(path) or bindings.get(wire)
            verification_id = str(binding.verification_id or "") if binding is not None else ""
            param = next(
                (
                    item for item in step.params
                    if canonical_wire_path(step, item.path) == wire or item.path == path
                ),
                None,
            )
            param_incomplete = bool(param is not None and _capability_param_enum_issue(param))
            binding_incomplete = bool(
                binding is not None
                and param is None
                and (
                    binding.enum_confirmed is not True
                    or (binding.count and len(binding.options or []) < binding.count)
                )
            )
            target_id = f"{step.step_id}:{wire}"
            if not verification_id and (binding_incomplete or param_incomplete):
                completion_op = "attach_enum_options" if binding is not None else "set_param_enum"
                label = str((param.label if param is not None else "") or (param.key if param is not None else "") or path)
                todos.append(
                    _annotate_stage_seven_todo(
                        {
                            "kind": "enum",
                            "target_id": target_id,
                            "issue_id": f"enum:{target_id}",
                            "check_code": "enum",
                            "step_id": step.step_id,
                            "field_id": str(param.field_id or "") if param is not None else "",
                            "wire_path": wire,
                            "path": str(param.path if param is not None else path),
                            "field_label": label,
                            "message": f"字段 `{step.step_id}:{wire}` 的枚举选项还不完整",
                            "source_request_id": str(binding.source_request_id or "")
                            if binding is not None
                            else "",
                            "known_count": len(binding.options or [])
                            if binding is not None
                            else len((param.enum_options if param is not None else None) or []),
                            "expected_count": binding.count if binding is not None else 0,
                            "suggested_tools": (
                                ["browser_snapshot", "browser_click", "replay_request"]
                                if binding is not None
                                else ["get_recording_state", "submit_recording_repair"]
                            ),
                            "suggested_executor": (
                                "replay_request" if binding is not None else "submit_recording_repair"
                            ),
                            "completion_op": completion_op,
                        },
                        spec,
                        current_scope,
                    )
                )
    return todos


def _release_issue_todos(
    spec, existing: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:  # noqa: ANN001
    """Project machine-release blockers into the existing verification queue.

    Release evaluation starts only after a capability has a materialized call
    graph.  Earlier recording drafts deliberately remain governed by the
    capture/semantic-plan todos instead of producing noisy anchor failures.
    """
    if not any(cap.nodes and cap.request_refs for cap in spec.capabilities):
        return [], []

    from dano.execution.page.flow_spec_core.fingerprints import flow_spec_fingerprint
    from dano.onboarding.recording_release import evaluate_recording_release

    decision = evaluate_recording_release(spec)
    issues = [
        issue.to_dict() for capability in decision.capabilities for issue in capability.issues
    ]
    current_fingerprint = flow_spec_fingerprint(spec)
    for issue in (spec.meta or {}).get("release_feedback_issues") or []:
        if not isinstance(issue, dict):
            continue
        bound_fingerprint = str(issue.get("flow_fingerprint") or "")
        if bound_fingerprint and bound_fingerprint != current_fingerprint:
            continue
        issues.append(dict(issue))

    existing_kinds = {str(item.get("kind") or "") for item in existing}
    existing_fields = {
        _field_key(
            str(item.get("step_id") or item.get("target_step_id") or ""),
            str(item.get("wire_path") or item.get("path") or item.get("target_path") or ""),
        ): str(item.get("kind") or "")
        for item in existing
    }
    existing_write_steps = {
        str(item.get("target_id") or item.get("step_id") or "")
        for item in existing
        if str(item.get("kind") or "") == "write_verify"
    }
    covered_codes: set[str] = set()
    if "dependency" in existing_kinds:
        covered_codes.update({"dependency_verification_missing", "dependency_verification_stale"})
    if "write_verify" in existing_kinds:
        covered_codes.add("write_readback_missing")

    unique_issues: dict[str, dict[str, Any]] = {}
    for issue in issues:
        issue_id = str(issue.get("issue_id") or "")
        if issue_id:
            unique_issues[issue_id] = issue
    active_issues = [
        issue
        for issue in unique_issues.values()
        if not (existing and str(issue.get("resolver") or "") == "operator")
    ]

    def _release_covered(issue: dict[str, Any]) -> bool:
        code = str(issue.get("check_code") or "")
        if code in covered_codes:
            return True
        field = _field_key(str(issue.get("step_id") or ""), str(issue.get("wire_path") or ""))
        existing_kind = existing_fields.get(field, "")
        if code == "enum_options_unverified" and existing_kind == "enum":
            return True
        if code == "field_source_unknown" and existing_kind == "enum":
            # A selectable field already queued for enum completion is not also
            # an unknown-source mystery; the same leaf must not appear twice.
            return True
        if code == "write_readback_missing" and str(issue.get("step_id") or "") in existing_write_steps:
            return True
        return False

    todos = [
        {
            "kind": "release_issue",
            "target_id": str(issue.get("issue_id") or ""),
            "issue_id": str(issue.get("issue_id") or ""),
            "check_code": str(issue.get("check_code") or ""),
            "capability_id": str(issue.get("capability_id") or ""),
            "step_id": str(issue.get("step_id") or ""),
            "field_id": str(issue.get("field_id") or ""),
            "wire_path": str(issue.get("wire_path") or ""),
            "resolver": str(issue.get("resolver") or "machine_repair"),
            "evidence_refs": list(issue.get("evidence_refs") or []),
            "suggested_operations": list(issue.get("suggested_operations") or []),
            "message": str(issue.get("message") or ""),
            "suggested_tool": (
                "submit_recording_repair"
                if str(issue.get("resolver") or "") == "machine_repair"
                else "collect_evidence"
                if str(issue.get("resolver") or "") == "collect_evidence"
                else "ask_operator"
                if str(issue.get("resolver") or "") == "operator"
                else "report_external_blocker"
            ),
        }
        for issue in active_issues
        if not _release_covered(issue)
    ]
    return active_issues, todos


def verification_report(spec) -> dict[str, Any]:  # noqa: ANN001
    from dano.onboarding.recording_stage_seven import build_stage_seven_scope, in_scope_unverified

    scope = build_stage_seven_scope(spec)
    todos = verification_todos(spec, scope)
    release_issues, release_todos = _release_issue_todos(spec, todos)
    todos.extend(
        _annotate_stage_seven_todo(item, spec, scope)
        if "target_signature" not in item else item
        for item in release_todos
    )
    unverified = in_scope_unverified(spec, scope)
    confirmed_links = sum(1 for link in spec.links if (link.meta or {}).get("verified") is True)
    writes = [step for step in spec.steps if (step.method or "").upper() in _WRITE_METHODS]
    verified_writes = sum(
        1
        for step in writes
        if (step.fact_check or {}).get("verified") is True
        and (step.fact_check or {}).get("verification_id")
    )
    return {
        "complete": not todos and not unverified,
        "all_verified": not todos and not unverified,
        "todos": todos,
        "release_issues": release_issues,
        "unverified": unverified,
        "confirmed_links": confirmed_links,
        "link_count": len(spec.links),
        "verify_coverage": verified_writes,
        "write_count": len(writes),
    }


def finalize_verification_state(
    spec,
    *,
    rounds: int,
    max_rounds: int,
    errors: list[str] | None = None,
    stop_reason: str = "",
):  # noqa: ANN001, ANN202
    """Checkpoint verification without converting blockers into publishable state."""
    from dano.execution.page.capability_repair import _auto_confirm_ready_capabilities

    current = _consume_dependency_executor_evidence(spec)
    report = verification_report(current)
    final_report = report
    resolvers = {
        str(item.get("resolver") or "")
        for item in final_report.get("release_issues") or []
        if isinstance(item, dict)
    }
    status = (
        "completed"
        if final_report["all_verified"]
        else (
            "waiting_for_operator"
            if "operator" in resolvers
            else "external_blocked"
            if stop_reason == "external_blocked" or "external_blocked" in resolvers
            else stop_reason or "pending"
        )
    )
    current.meta = {
        **(current.meta or {}),
        "verification_run": {
            "complete": final_report["complete"],
            "all_verified": final_report["all_verified"],
            "status": status,
            "stop_reason": stop_reason,
            "rounds": rounds,
            "max_rounds": max_rounds,
            "errors": list(errors or []),
            "summary": {
                **{
                    key: final_report[key]
                    for key in (
                        "confirmed_links",
                        "link_count",
                        "verify_coverage",
                        "write_count",
                    )
                },
                "by_capability": dict((current.meta or {}).get("capability_verification") or {}),
            },
        },
    }
    if final_report["all_verified"]:
        current = _auto_confirm_ready_capabilities(current)
    return current, final_report


def _consume_write_executor_evidence(spec):  # noqa: ANN001, ANN202
    """Bind passed write read-back executions even when the model turn died.

    ``execute_write_with_verify`` is the authoritative executor: once its
    verification record passed, the conclusion must not be lost because the Pi
    turn timed out before submitting ``bind_verify_read``. The op below reuses
    the exact executed subject (step, read request, assertion), so the guarded
    apply path still validates everything.
    """
    from dano.execution.page.flow_spec_core.controlled_edits import apply_flow_edits

    current = spec
    latest_by_step: dict[str, dict[str, Any]] = {}
    for record in list((spec.meta or {}).get("verification_log") or []):
        if not isinstance(record, dict) or record.get("kind") != "write_execute":
            continue
        step_id = str((record.get("subject") or {}).get("write_step_id") or "")
        if step_id:
            latest_by_step[step_id] = record
    for step_id, record in latest_by_step.items():
        if record.get("status") != "passed" or not str(record.get("verification_id") or ""):
            continue
        step = next((item for item in current.steps if item.step_id == step_id), None)
        if step is None:
            continue
        fact_check = step.fact_check or {}
        if fact_check.get("verified") is True and fact_check.get("verification_id"):
            continue
        subject = record.get("subject") or {}
        assertion = subject.get("assertion")
        read_request_id = str(subject.get("verify_request_id") or "")
        if not isinstance(assertion, dict) or not assertion or not read_request_id:
            continue
        try:
            current = apply_flow_edits(
                current,
                [
                    {
                        "op": "bind_verify_read",
                        "write_step_id": step_id,
                        "read_request_id": read_request_id,
                        "verification_id": str(record.get("verification_id") or ""),
                        "assertion": deepcopy(assertion),
                        "actor": "agent",
                    }
                ],
            )
        except ValueError:
            # The guarded op rejects evidence that no longer matches the
            # current draft; such a write stays pending for the next round.
            continue
    return current


def _consume_dependency_executor_evidence(spec):  # noqa: ANN001, ANN202
    """Apply the latest current-signature dependency execution result."""
    from dano.execution.page.flow_spec_core.controlled_edits import apply_flow_edits

    current = spec.model_copy(deep=True)
    # Executor evidence is authoritative even when the Pi turn times out after
    # the tool returns but before it can submit the follow-up operation. Keep
    # only the latest record for the current link signature: a pass confirms
    # the hypothesis; a deterministic failure rejects an unlocked model
    # proposal instead of retrying and publishing the known-bad dependency.
    from dano.execution.page.recording_live import dependency_link_signature

    latest_by_link: dict[str, dict[str, Any]] = {}
    links_by_id = {link.link_id: link for link in current.links}
    for record in list((current.meta or {}).get("verification_log") or []):
        if not isinstance(record, dict) or record.get("kind") != "dependency_execute":
            continue
        subject = record.get("subject") or {}
        link_id = str(subject.get("link_id") or "")
        link = links_by_id.get(link_id)
        if link is None or str(subject.get("signature") or "") != dependency_link_signature(link):
            continue
        latest_by_link[link_id] = record
    for link_id, record in latest_by_link.items():
        verification_id = str(record.get("verification_id") or "")
        if record.get("status") == "failed":
            link = next((item for item in current.links if item.link_id == link_id), None)
            actor = str(
                ((link.meta if link else {}) or {}).get("actor")
                or ((link.evidence if link else {}) or {}).get("actor")
                or ""
            )
            if link is not None and actor == "agent" and not link.locked and not link.confirmed:
                current = apply_flow_edits(
                    current, [{"op": "reject_dependency", "link_id": link_id}]
                )
                current.meta = {
                    **(current.meta or {}),
                    "unverified": [
                        item
                        for item in (current.meta or {}).get("unverified") or []
                        if not (
                            isinstance(item, dict)
                            and str(item.get("target_id") or "") == link_id
                            and str(item.get("target_kind") or "").startswith("dependency")
                        )
                    ],
                }
            continue
        if record.get("status") != "passed" or not verification_id:
            continue
        link = next((item for item in current.links if item.link_id == link_id), None)
        if (
            link is not None
            and link.confirmed
            and (link.meta or {}).get("verified") is True
            and str((link.meta or {}).get("verification_id") or "") == verification_id
        ):
            continue
        try:
            current = apply_flow_edits(
                current,
                [
                    {
                        "op": "confirm_dependency",
                        "link_id": link_id,
                        "verification_id": verification_id,
                    }
                ],
            )
        except ValueError:
            # The guarded op rejects stale evidence whose link signature no
            # longer matches the current draft; such a link must stay pending.
            continue

    current = _consume_write_executor_evidence(current)

    # Capability membership is compiled from the verified dependency graph,
    # not from the model's proposed request_refs.  Dependency verification can
    # therefore expand the executable closure after the original capability
    # plan was compiled.  Refresh that same stored plan here so verified
    # preflight steps do not remain orphaned until another planning turn.
    capability_model = dict((current.meta or {}).get("capability_model") or {})
    semantic_plan = capability_model.get("semantic_plan")
    if isinstance(semantic_plan, dict) and semantic_plan.get("capabilities"):
        from dano.execution.page.capability_compiler import compile_capabilities

        compilation = compile_capabilities(current, semantic_plan)
        if not compilation.errors:
            current = compilation.spec
    return current
