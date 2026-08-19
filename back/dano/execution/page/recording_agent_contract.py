"""Stage 4: Skill submission protocol, op gate, and LiveNotebook replay."""
from __future__ import annotations

from typing import Any
import copy
from datetime import datetime, timezone
import hashlib
import json
from dano.execution.page.flow_spec_core.models import (
    FlowSpec,
)
from dano.execution.page.request_capture import (
    normalized_leaf_paths,
)
from dano.execution.page.recording_facts import (
    _compact_repeated_endpoint_observations,
    _request_fact_items,
)
from dano.execution.page.recording_analysis_state import (
    _model_visible_request_facts,
)
from dano.execution.page.flow_spec_core.fingerprints import (
    _stable_json_hash,
)


def _semantic_fact_snapshot(spec: FlowSpec) -> dict[str, Any]:
    """Return the grounded recording state exposed to the Pi recording agent."""
    from dano.execution.page.recording_live import compact_model_payload
    from dano.execution.page.recording_field_identity import canonical_wire_path

    request_facts = _request_fact_items(spec)
    option_sources = _compact_repeated_endpoint_observations(
        copy.deepcopy(spec.request_facts.option_sources or []),
    )
    return {
        "protocol": "dano.recording-semantic-facts.v1",
        "tenant": spec.tenant,
        "subsystem": spec.subsystem,
        "title": spec.title,
        "page_context": dict((spec.meta or {}).get("page_context") or {}),
        "recording_mode": spec.recording_mode,
        "risk_level": spec.risk_level,
        "steps": [
            {
                "step_id": step.step_id,
                "request_id": (step.source_meta or {}).get("request_id"),
                "request_index": (step.source_meta or {}).get("request_index"),
                "method": (step.method or "GET").upper(),
                "path": step.path or step.url,
                "page_id": _step_page_id_from_facts(spec, step),
                "role": (step.source_meta or {}).get("role") or step.semantic_role,
                "sequence": (step.source_meta or {}).get("sequence"),
                "trigger_action_id": (step.source_meta or {}).get("trigger_action_id"),
                "trigger_op": (step.source_meta or {}).get("trigger_op"),
                "trigger_locator": (step.source_meta or {}).get("trigger_locator"),
                "causality_confidence": (step.source_meta or {}).get("causality_confidence"),
                "params": [
                    {
                        "path": param.path,
                        "wire_path": canonical_wire_path(step, param.path),
                        "key": param.key,
                        "label": param.label,
                        "business_type": param.type,
                        "wire_type": param.wire_type,
                        "wire_format": param.wire_format,
                        "category": param.category,
                        "source_kind": param.source_kind,
                        "default_value": _client_redact_sensitive(param.default_value, param.path),
                        "caller_required": _param_requires_caller_input(param),
                        "required_state": str((param.source or {}).get("required_state") or "unknown"),
                        "exposed": bool(param.exposed_to_user),
                        "locked": bool(param.locked),
                        "evidence": _client_redact_sensitive(
                            copy.deepcopy((param.evidence or [])[-10:]),
                            param.path,
                        ),
                    }
                    for param in step.params
                ],
                "response_paths": normalized_leaf_paths(step.response_json),
            }
            for step in spec.steps
        ],
        "links": sorted([
            {
                "source_step_id": link.source_step_id,
                "source_path": link.source_path,
                "target_step_id": link.target_step_id,
                "target_path": link.target_path,
                "confirmed": bool(link.confirmed),
                "confidence": float(link.confidence or 0),
            }
            for link in spec.links
        ], key=lambda item: (
            item["source_step_id"], item["source_path"], item["target_step_id"], item["target_path"]
        )),
        "captured_requests": [
            {
                "request_id": request.get("request_id"),
                "request_index": request.get("request_index"),
                "method": request.get("method"),
                "path": request.get("path") or request.get("url"),
                "page_id": request.get("page_id"),
                "frame_id": request.get("frame_id"),
                "sequence": request.get("sequence"),
                "role": request.get("role"),
                "keep": request.get("keep"),
                "confidence": request.get("confidence"),
                "reason": request.get("reason"),
                "trigger_action_id": request.get("trigger_action_id"),
                "trigger_transaction_id": request.get("trigger_transaction_id"),
                "trigger_event_id": request.get("trigger_event_id"),
                "trigger_op": request.get("trigger_op"),
                "trigger_locator": request.get("trigger_locator"),
                "action_delta_ms": request.get("action_delta_ms"),
                "causality_confidence": request.get("causality_confidence"),
                "query_paths": list(request.get("query_paths") or []),
                "query": compact_model_payload(
                    _client_redact_sensitive(request.get("query") or {}, "query"),
                    max_depth=3,
                    max_items=20,
                    max_string=80,
                ),
                "body_paths": list(request.get("body_paths") or []),
                "state": request.get("state"),
                "materialized_step_id": request.get("materialized_step_id"),
                "used_by_capabilities": list(request.get("used_by_capabilities") or []),
                "observation_count": request.get("observation_count"),
                "request_id_samples": list(request.get("request_id_samples") or []),
                "trigger_event_id_samples": list(request.get("trigger_event_id_samples") or []),
                "trigger_action_id_samples": list(request.get("trigger_action_id_samples") or []),
                "response_schema": compact_model_payload(
                    request.get("response_schema") or {},
                    max_depth=6,
                    max_items=50,
                    max_string=500,
                ),
            }
            for request in _model_visible_request_facts(request_facts)
        ],
        "captured_request_count": len(request_facts),
        "field_evidence_count": len(getattr(spec.request_facts, "field_evidence", []) or []),
        "field_evidence": _client_redact_sensitive(
            compact_model_payload(
                copy.deepcopy((getattr(spec.request_facts, "field_evidence", []) or [])[-120:]),
                max_depth=6,
                max_items=120,
                max_string=800,
            ),
        ),
        "option_source_count": len(spec.request_facts.option_sources or []),
        "option_sources": _client_redact_sensitive(
            compact_model_payload(
                option_sources[-80:],
                max_depth=7,
                max_items=80,
                max_string=800,
            ),
        ),
        "page_event_count": len(spec.request_facts.page_events or []),
        "page_events": [
            {
                key: event.get(key)
                for key in (
                    "event_id", "kind", "action_id", "transaction_id", "op", "locator", "field",
                    "required", "has_value", "observed_at", "page_id", "frame_id", "changes",
                    "option_count", "field_count", "required_fields", "page_context",
                )
                if event.get(key) not in (None, "", [], {})
            }
            for event in (spec.request_facts.page_events or [])[-120:]
            # Raw mutation batches describe framework repaint churn, not field
            # semantics.  Keeping them in the model state added tens of
            # thousands of characters per page without helping matching.
            if isinstance(event, dict) and event.get("kind") != "dom_effect"
        ],
        "manual_constraints": {
            "removed_capabilities": sorted(str(item) for item in ((spec.meta or {}).get("removed_capabilities") or [])),
            "removed_capability_steps": {
                str(name): sorted(str(item) for item in values or [])
                for name, values in sorted(((spec.meta or {}).get("capability_removed_steps") or {}).items())
            },
        },
        "live_notebook": {
            "pending_questions": copy.deepcopy(
                (spec.meta or {}).get("live_pending_questions") or []
            )[-50:],
        },
    }


def _semantic_fact_hash(spec: FlowSpec) -> str:
    # Public names, inferred types and confirmation state are mutable contract
    # decisions.  The generation epoch changes only when the recorded wire
    # facts change, not when an operator renames a field.
    payload = {
        "steps": [
            {
                "step_id": step.step_id,
                "method": (step.method or "GET").upper(),
                "path": step.path or step.url,
                "page_id": _step_page_id_from_facts(spec, step),
                "trigger_locator": (step.source_meta or {}).get("trigger_locator"),
                "param_paths": sorted(param.path for param in step.params),
                "response_paths": normalized_leaf_paths(step.response_json),
            }
            for step in spec.steps
        ],
        "request_ids": sorted(
            (str(fact.request_id or ""), str(fact.request_index if fact.request_index is not None else ""))
            for fact in spec.request_facts.requests or []
        ),
        "field_evidence": getattr(spec.request_facts, "field_evidence", []) or [],
        "option_sources": spec.request_facts.option_sources or [],
        "page_events": spec.request_facts.page_events or [],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


_RECORDING_AGENT_ALLOWED_OPS = {
    "rename_step", "promote_request", "rename_field", "bind_response_source",
    "bind_option_source", "set_loop_source", "set_return_mapping",
    "mark_field_as_system_var", "mark_field_as_identity", "create_capability",
    "reorder_capability_steps", "upsert_capability", "upsert_capability_field",
    "upsert_input_field", "upsert_request_field", "upsert_internal_field",
    "upsert_computed_field", "upsert_output_field", "bind_dependency", "set_map",
    "set_condition", "set_output_mapping", "set_capability_relation",
    "add_request_to_capability", "remove_request_from_capability", "reject_dependency",
    "set_goal", "set_request_role", "set_param_source", "set_param_type", "set_param_required", "set_param_enum",
    "rename_field", "propose_dependency", "add_pitfall",
    "confirm_dependency", "bind_verify_read", "attach_enum_options", "mark_unverified",
}


def _validate_recording_agent_ops(ops: list[dict[str, Any]]) -> None:
    for index, op in enumerate(ops):
        if not isinstance(op, dict):
            raise ValueError(f"recording op[{index}] must be an object")
        kind = str(op.get("op") or "")
        if kind not in _RECORDING_AGENT_ALLOWED_OPS:
            raise ValueError(f"recording op[{index}] is not allowed: {kind or '<empty>'}")


def recording_agent_validation(spec: FlowSpec) -> dict[str, Any]:
    """Return the deterministic validation/repair evidence for Pi tools."""
    current = refresh_review_items(_sync_capability_io_schemas(spec.model_copy(deep=True)))
    report = validate_flow_spec(current)
    structural_valid = bool(report.get("passed"))
    from dano.execution.page.recording_live import recording_agent_evidence_issues
    from dano.onboarding.recording_verify import verification_report
    evidence_issues = recording_agent_evidence_issues(current)
    report["agent_evidence"] = {"ok": not evidence_issues, "issues": evidence_issues}
    verification = verification_report(current)
    report["recording_verification"] = verification
    if evidence_issues:
        report["errors"] = [
            *(report.get("errors") or []),
            *(f"agent evidence missing: {item['kind']} {item['target']}" for item in evidence_issues),
        ]
        report["passed"] = False
    session_audit = dict((current.meta or {}).get("recording_agent_session") or {})
    from dano.execution.page.recording_live import compact_model_payload
    op_results = list(session_audit.get("op_results") or [])
    must_retry = [
        int(item.get("index") or 0)
        for item in op_results
        if str(item.get("status") or "") in {
            "rejected", "rolled_back", "must_retry", "conflict", "version_conflict",
        }
    ]
    unresolved_targets = [
        dict(item.get("requested_target") or {})
        for item in op_results
        if (
            str(item.get("status") or "") == "deferred"
            or (
                str(item.get("status") or "") == "rejected"
                and not item.get("resolved_target")
            )
        )
        and item.get("requested_target")
    ]
    from dano.onboarding.recording_release import evaluate_recording_release
    release_ready = evaluate_recording_release(current).callable_spec is not None
    capability_plan_complete = recording_capability_plan_complete(current)
    capability_model = dict((current.meta or {}).get("capability_model") or {})
    semantic_plan = (
        capability_model.get("semantic_plan")
        if isinstance(capability_model.get("semantic_plan"), dict)
        else {}
    )
    capability_plan_received = capability_plan_complete or bool(current.capabilities) or any(
        isinstance(item, dict) for item in (semantic_plan.get("capabilities") or [])
    )
    capability_retry_reasons = [] if capability_plan_complete else list(dict.fromkeys([
        *list((capability_model.get("proposal_gate") or {}).get("reasons") or []),
        *list((capability_model.get("semantic_coverage") or {}).get("missing") or []),
        *list(capability_model.get("capability_compilation_errors") or []),
    ]))[:20]
    return {
        "flow_version": int((current.meta or {}).get("current_version") or 0),
        "structural_valid": structural_valid,
        "verification_complete": bool(verification.get("all_verified")),
        "release_ready": release_ready,
        "report": compact_model_payload(report, max_depth=6, max_items=40, max_string=500),
        "repair_context": compact_model_payload(
            _flow_autofix_context(current, report), max_depth=6, max_items=40, max_string=500,
        ),
        "op_results": op_results,
        "all_applied": all(
            str(item.get("status") or "") == "applied"
            for item in op_results
        ),
        "capability_plan_received": capability_plan_received,
        "capability_plan_complete": capability_plan_complete,
        "capability_retry_reasons": capability_retry_reasons,
        # capability_plan_received means the Skill boundary was stored.
        # submission_complete is only about the current op batch: rejected /
        # must_retry / rolled_back / version conflict still need a Skill retry.
        "submission_complete": not must_retry,
        "must_retry": must_retry,
        "unresolved_targets": unresolved_targets,
    }


_LIVE_PLAN_BLOCKING_GAPS = frozenset({
    "capability_contracts", "capabilities", "goal_capability_count", "unresolved_blockers",
})


def _live_capability_plan_is_terminal(spec: FlowSpec) -> bool:
    """A stored live Skill plan is complete before request facts become steps.

    Compile-time gaps such as ``request_materialization`` or
    ``field_axis_contract`` belong to freeze, not the live analysis turn.
    ``capability_generation.initial_completed`` is also compile-owned and must
    not discard an already accepted boundary set.
    """
    if spec.steps:
        return False
    model = dict((spec.meta or {}).get("capability_model") or {})
    plan = model.get("semantic_plan") if isinstance(model.get("semantic_plan"), dict) else {}
    capabilities = [
        item for item in (plan.get("capabilities") or [])
        if isinstance(item, dict)
    ]
    if not capabilities:
        return False
    fact_request_ids = {
        str(item.get("request_id") or "")
        for item in _request_fact_items(spec)
        if str(item.get("request_id") or "")
    }
    if not fact_request_ids:
        return False
    coverage = _pre_materialization_semantic_plan_coverage(spec, plan, fact_request_ids)
    return not (set(coverage.get("missing") or []) & _LIVE_PLAN_BLOCKING_GAPS)


def recording_capability_plan_complete(spec: FlowSpec) -> bool:
    """Whether the authoritative semantic boundary plan reached a safe terminal state."""
    meta = spec.meta or {}
    generation = dict(meta.get("capability_generation") or {})
    model = dict(meta.get("capability_model") or {})
    status = str(model.get("status") or "")
    if status in {"awaiting_materialization", "ready"}:
        return True
    if _live_capability_plan_is_terminal(spec):
        return True
    if generation:
        return bool(generation.get("initial_completed"))
    return False


_RECORDING_FIELD_OPS = frozenset({
    "set_param_source", "set_param_type", "set_param_required", "set_param_enum", "rename_field",
    "attach_enum_options",
})


def _recording_requested_target(spec: FlowSpec, operation: dict[str, Any]) -> dict[str, str]:
    kind = str(operation.get("op") or "")
    if kind not in _RECORDING_FIELD_OPS and kind != "propose_dependency":
        return {}
    identifier = str(
        operation.get("request_id")
        or operation.get("target_request_id")
        or operation.get("step_id")
        or operation.get("target_step_id")
        or ""
    )
    wire_path = str(
        operation.get("wire_path")
        or operation.get("path")
        or operation.get("target_path")
        or ""
    ).removeprefix("request.")
    step = next((item for item in spec.steps if item.step_id == identifier), None)
    request_id = str(((step.source_meta if step else {}) or {}).get("request_id") or identifier)
    if step is not None and wire_path:
        try:
            from dano.execution.page.recording_field_identity import FieldRef, resolve_field_ref

            wire_path = resolve_field_ref(
                spec, FieldRef(step_id=step.step_id, wire_path=wire_path),
            ).wire_path
        except ValueError:
            pass
    return {
        **({"request_id": request_id} if request_id else {}),
        **({"wire_path": wire_path} if wire_path else {}),
    }


def _recording_resolved_target(spec: FlowSpec, operation: dict[str, Any]) -> dict[str, str]:
    if str(operation.get("op") or "") not in _RECORDING_FIELD_OPS:
        return {}
    from dano.execution.page.recording_field_identity import FieldRef, resolve_field_ref

    identifier = str(operation.get("step_id") or operation.get("request_id") or "")
    wire_path = str(operation.get("wire_path") or operation.get("path") or "")
    is_step_id = any(item.step_id == identifier for item in spec.steps)
    try:
        resolved = resolve_field_ref(spec, FieldRef(
            step_id=identifier if is_step_id else "",
            request_id="" if is_step_id else identifier,
            wire_path=wire_path,
        ))
    except ValueError:
        return {}
    return {
        "step_id": resolved.step_id,
        "stored_path": resolved.stored_path,
        "wire_path": resolved.wire_path,
    }


def _recording_operation_result(
    spec: FlowSpec,
    operation: dict[str, Any],
    *,
    index: int,
    status: str,
    reason: str = "",
    flow_version_before: int,
    flow_version_after: int = 0,
    allowed_values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if status not in {"applied", "deferred", "rejected", "rolled_back"}:
        status = "rejected"
    result = {
        "index": index,
        "op": str(operation.get("op") or ""),
        "status": status,
        "requested_target": _recording_requested_target(spec, operation),
        "resolved_target": _recording_resolved_target(spec, operation),
        "reason": str(reason or ""),
        "flow_version_before": flow_version_before,
        "flow_version_after": flow_version_after,
    }
    if allowed_values:
        result["allowed_values"] = allowed_values
    return result


_META_ONLY_REPAIR_OPS = frozenset({"mark_unverified", "add_pitfall"})


def _allowed_values_from_exc(exc: BaseException) -> dict[str, Any] | None:
    allowed = getattr(exc, "allowed", None)
    field = str(getattr(exc, "field", "") or "")
    if not allowed:
        return None
    return {"field": field, "allowed": list(allowed)}


async def apply_recording_agent_submission(
    spec: FlowSpec,
    *,
    submission: dict[str, Any],
    mode: str = "plan",
    max_rounds: int = 4,
) -> FlowSpec:
    """Apply one Pi AgentSession plan/repair submission to a FlowSpec.

    The AgentSession owns prompting and conversation state. This core accepts
    only structured output and performs bounded deterministic compilation,
    validation and candidate admission.
    """
    if mode not in {"plan", "repair"}:
        raise ValueError("recording agent mode must be plan or repair")
    if not isinstance(submission, dict):
        raise ValueError("recording agent submission must be an object")
    current = ensure_recorded_goal(spec.model_copy(deep=True))
    flow_version_before = int((current.meta or {}).get("current_version") or 0)
    submitted_ops = list(submission.get("ops") or [])
    _validate_recording_agent_ops(submitted_ops)
    op_results: list[dict[str, Any]] = []
    deferred_field_ops: list[tuple[int, dict[str, Any]]] = []
    residual_ops: list[tuple[int, dict[str, Any]]] = []
    if mode == "plan":
        from dano.execution.page.recording_live import (
            LIVE_RECORDING_AGENT_OPS,
            apply_recording_agent_edit,
        )

        field_ops = {
            "set_param_source", "set_param_type", "set_param_required",
            "set_param_enum", "rename_field",
        }

        for index, operation in enumerate(submitted_ops):
            kind = str(operation.get("op") or "")
            if kind in field_ops:
                deferred_field_ops.append((index, operation))
                continue
            if kind not in LIVE_RECORDING_AGENT_OPS:
                residual_ops.append((index, operation))
                continue
            try:
                outcome = apply_recording_agent_edit(current, operation, record=True)
                op_results.append(_recording_operation_result(
                    current,
                    operation,
                    index=index,
                    status=str(outcome.get("status") or "applied"),
                    reason=str(outcome.get("reason") or ""),
                    flow_version_before=flow_version_before,
                ))
            except (TypeError, ValueError) as exc:
                op_results.append(_recording_operation_result(
                    current,
                    operation,
                    index=index,
                    status="rejected",
                    reason=str(exc),
                    flow_version_before=flow_version_before,
                    allowed_values=_allowed_values_from_exc(exc),
                ))
        submission = copy.deepcopy(submission)
        submission["ops"] = [operation for _index, operation in residual_ops]
    else:
        from dano.execution.page.recording_live import apply_recording_agent_edit

        for index, operation in enumerate(submitted_ops):
            candidate = current.model_copy(deep=True)
            try:
                outcome = apply_recording_agent_edit(candidate, operation, record=True)
                status = str(outcome.get("status") or "applied")
                reason = str(outcome.get("reason") or "")
                if status == "applied":
                    if str(operation.get("op") or "") in _META_ONLY_REPAIR_OPS:
                        current = candidate
                    else:
                        candidate = _auto_confirm_ready_capabilities(
                            refresh_review_items(_sync_capability_io_schemas(candidate)),
                            refresh_machine_owned=True,
                        )
                        expected_grounded_wire_change = bool(
                            str(operation.get("op") or "") == "propose_dependency"
                            and str(operation.get("kind") or operation.get("link_kind") or "")
                            in {"structure", "response_key_map"}
                        )
                        accepted, gate = _semantic_candidate_gate(
                            current,
                            candidate,
                            allow_grounded_wire_change=expected_grounded_wire_change,
                        )
                        if accepted:
                            current = candidate
                        else:
                            status = "rolled_back"
                            reason = ",".join(gate.get("reasons") or []) or "quality gate rejected operation"
                elif status == "deferred":
                    current = candidate
                op_results.append(_recording_operation_result(
                    current,
                    operation,
                    index=index,
                    status=status,
                    reason=reason,
                    flow_version_before=flow_version_before,
                ))
            except (TypeError, ValueError) as exc:
                op_results.append(_recording_operation_result(
                    current,
                    operation,
                    index=index,
                    status="rejected",
                    reason=str(exc),
                    flow_version_before=flow_version_before,
                    allowed_values=_allowed_values_from_exc(exc),
                ))
        submission = copy.deepcopy(submission)
        submission["ops"] = []
    fact_hash = _semantic_fact_hash(current)
    previous_generation = dict((current.meta or {}).get("capability_generation") or {})
    initial_generation = bool(
        (mode == "plan" or not current.capabilities)
        and not (
            previous_generation.get("initial_completed")
            and str(previous_generation.get("fact_hash") or "") == fact_hash
        )
    )

    range_candidate, range_changes = _apply_grounded_indexed_range_names(current)
    range_accepted, range_gate = _semantic_candidate_gate(current, range_candidate)
    if range_changes and range_accepted:
        current = ensure_recorded_goal(range_candidate)
    else:
        range_changes = []

    _normalize_capability_references(current)
    current = refresh_review_items(_sync_capability_io_schemas(current))
    history: list[dict[str, Any]] = []
    # Repair ops are applied exactly once by auto_fix_flow_spec below. Running
    # the planner first when capabilities are absent used to apply the same
    # live ops twice (planner pass + repair pass), duplicating evidence and
    # pitfalls even though the resulting link happened to deduplicate.
    run_planner = mode == "plan"

    for round_idx in range(max_rounds):
        if run_planner:
            current = await orchestrate_flow_capabilities(
                current,
                submission=submission,
                generation_mode="initial" if initial_generation else "optimize",
            )

        # Confirmation readiness is deterministic and must be evaluated before
        # deciding whether semantic Repair is needed. Otherwise every valid
        # first plan appears publish-invalid solely because its newly created
        # capabilities are still drafts, causing a redundant full model call.
        current = _auto_confirm_ready_capabilities(
            _sync_capability_io_schemas(sync_flow_spec_models(current))
        )
        report = validate_flow_spec(current)
        history.append({
            "round": round_idx + 1,
            "stage": "planner" if run_planner else "validator",
            "passed": bool(report.get("passed")),
            "errors": len(report.get("errors") or []),
            "warnings": len(report.get("warnings") or []),
        })
        if mode == "plan":
            break

        current = await auto_fix_flow_spec(
            current,
            repair_ops=list(submission.get("ops") or []),
            max_rounds=1,
            expand_requests=False,
            allow_scope_changes=False,
        )
        fixed_report = validate_flow_spec(current)
        history.append({
            "round": round_idx + 1,
            "stage": "repair",
            "passed": bool(fixed_report.get("passed")),
            "errors": len(fixed_report.get("errors") or []),
            "warnings": len(fixed_report.get("warnings") or []),
        })
        break

    if mode == "plan":
        from dano.execution.page.recording_live import apply_recording_agent_edit

        for index, operation in deferred_field_ops:
            kind = str(operation.get("op") or "")
            try:
                outcome = apply_recording_agent_edit(current, operation, record=True)
                op_results.append(_recording_operation_result(
                    current,
                    operation,
                    index=index,
                    status=str(outcome.get("status") or "applied"),
                    reason=str(outcome.get("reason") or ""),
                    flow_version_before=flow_version_before,
                ))
            except (TypeError, ValueError) as exc:
                op_results.append(_recording_operation_result(
                    current,
                    operation,
                    index=index,
                    status="rejected",
                    reason=str(exc),
                    flow_version_before=flow_version_before,
                    allowed_values=_allowed_values_from_exc(exc),
                ))
        proposal_gate = ((current.meta or {}).get("capability_model") or {}).get("proposal_gate") or {}
        for index, operation in residual_ops:
            rolled_back = proposal_gate.get("accepted") is False
            op_results.append(_recording_operation_result(
                current,
                operation,
                index=index,
                status="rolled_back" if rolled_back else "applied",
                reason=",".join(proposal_gate.get("reasons") or []) if rolled_back else "",
                flow_version_before=flow_version_before,
            ))
        op_results.sort(key=lambda item: int(item["index"]))

    current = _auto_confirm_ready_capabilities(
        _sync_capability_io_schemas(sync_flow_spec_models(current))
    )
    current = _ensure_capability_explanations(
        current,
        ((current.meta or {}).get("capability_model") or {}).get("semantic_plan") or {},
    )
    if not str(current.business_description or "").strip():
        current.business_description = render_business_description(current)
        current.meta = {
            **(current.meta or {}),
            "business_description_source": "deterministic",
        }
    current = refresh_review_items(_sync_capability_io_schemas(current))
    final_coverage = ((current.meta or {}).get("capability_model") or {}).get("semantic_coverage") or {}
    initial_completed = bool(
        previous_generation.get("initial_completed")
        and str(previous_generation.get("fact_hash") or "") == fact_hash
    )
    if initial_generation:
        final_gate = ((current.meta or {}).get("capability_model") or {}).get("proposal_gate") or {}
        initial_completed = bool(
            final_coverage.get("complete")
            and final_gate.get("accepted") is not False
        )
    semantic_plan = ((current.meta or {}).get("capability_model") or {}).get("semantic_plan") or {}
    generation_status = "ready" if initial_completed else "incomplete_agent_plan"
    now = datetime.now(timezone.utc).isoformat()
    final_flow_version = int((current.meta or {}).get("current_version") or 0) + 1
    for item in op_results:
        item["flow_version_after"] = final_flow_version
        item["resolved_target"] = _recording_resolved_target(current, submitted_ops[int(item["index"])])
    current.meta = {
        **(current.meta or {}),
        "capability_generation": {
            "protocol": "dano.capability-generation.v2",
            "fact_hash": fact_hash,
            "initial_completed": initial_completed,
            "semantic_plan_hash": _stable_json_hash(semantic_plan) if semantic_plan else "",
            "generation_epoch": (
                int(previous_generation.get("generation_epoch") or 0)
                + (1 if str(previous_generation.get("fact_hash") or "") != fact_hash else 0)
            ),
            "status": generation_status,
            "last_mode": "initial" if initial_generation else mode,
            "indexed_range_changes": range_changes,
            "indexed_range_gate": range_gate,
            "updated_at": now,
        },
        "recording_agent_session": {
            "mode": mode,
            "generation_mode": "initial" if initial_generation else "optimize",
            "rounds": history,
            "submission_id": str(submission.get("submission_id") or ""),
            "op_results": op_results,
            "updated_at": now,
        },
    }
    return append_flow_version(
        current,
        "recording_agent_submission",
        reason=f"录制 Pi AgentSession 提交: {mode}",
        actor="planner",
    )

_PENDING_FLOW_SPEC_HELPERS = {'_apply_grounded_indexed_range_names': 'dano.execution.page.flow_materialization.field_contracts.common', '_auto_confirm_ready_capabilities': 'dano.execution.page.capability_repair', '_client_redact_sensitive': 'dano.execution.page.flow_client_projection', '_ensure_capability_explanations': 'dano.execution.page.capability_contracts', '_flow_autofix_context': 'dano.execution.page.capability_repair', '_normalize_capability_references': 'dano.execution.page.capability_nodes', '_param_requires_caller_input': 'dano.execution.page.flow_materialization.field_contracts.caller_ownership', '_pre_materialization_semantic_plan_coverage': 'dano.execution.page.capability_semantic', '_semantic_candidate_gate': 'dano.execution.page.capability_semantic', '_step_page_id_from_facts': 'dano.execution.page.capability_refs', '_sync_capability_io_schemas': 'dano.execution.page.capability_io', 'append_flow_version': 'dano.execution.page.flow_spec_core.versioning', 'auto_fix_flow_spec': 'dano.execution.page.capability_repair', 'ensure_recorded_goal': 'dano.execution.page.flow_materialization.builder', 'orchestrate_flow_capabilities': 'dano.execution.page.capability_orchestration', 'refresh_review_items': 'dano.execution.page.flow_materialization.review_items', 'render_business_description': 'dano.execution.page.flow_client_projection', 'sync_flow_spec_models': 'dano.execution.page.flow_materialization.builder', 'validate_flow_spec': 'dano.execution.page.flow_spec_validate'}


def _bind_flow_spec_helpers() -> None:
    import sys
    module_globals = globals()
    for name, owner in _PENDING_FLOW_SPEC_HELPERS.items():
        mod = sys.modules.get(owner)
        if mod is None or not hasattr(mod, name):
            continue
        module_globals[name] = getattr(mod, name)


_bind_flow_spec_helpers()
