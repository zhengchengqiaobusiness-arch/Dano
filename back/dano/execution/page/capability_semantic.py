"""Stage 6: semantic plan coverage and business understanding."""
from __future__ import annotations

from typing import Any
import copy
import hashlib
import json
import re
from dano.execution.page.flow_spec_core.models import (
    FlowSpec,
    FlowStep,
    ParamField,
)
from dano.execution.page.flow_materialization.field_contracts.common import (
    _field_source_configuration_advice,
)
from dano.execution.page.flow_materialization.request_usage import (
    _materialized_step_id_for_request,
)
from dano.execution.page.recording_facts import (
    _request_fact_items,
)
from dano.execution.page.flow_spec_core.fingerprints import (
    _stable_json_hash,
)


def _step_is_write_preflight(step: FlowStep | None) -> bool:
    """Return whether a read exists only to hydrate a later write."""
    if step is None or str(step.method or "GET").upper() != "GET":
        return False
    source_meta = step.source_meta or {}
    return bool(
        source_meta.get("control_preflight_for_write")
        or source_meta.get("record_hydration_for_write_ids")
        or source_meta.get("control_preflight_for_write_ids")
    )


def _required_public_action_request_ids(spec: FlowSpec) -> set[str]:
    """Return the recorded requests that each require a public capability.

    Writes remain mandatory even when recorder action metadata is incomplete.
    A read is public only when analysis classified it as a business read and it
    has its own user action/transaction. Reads caused by a write action are
    refresh/fact-check traffic, while ``read_context`` requests are preflight
    hydration for a later command rather than separate caller abilities.
    """
    facts = _request_fact_items(spec)
    step_by_id = {str(step.step_id or ""): step for step in spec.steps}

    def materialized_step(item: dict[str, Any]) -> FlowStep | None:
        return step_by_id.get(_materialized_step_id_for_request(spec, item))

    def action_key(item: dict[str, Any]) -> str:
        return str(
            item.get("trigger_transaction_id")
            or item.get("trigger_action_id")
            or ""
        ).strip()

    write_action_keys = {
        action_key(item)
        for item in facts
        if _eligible_business_write_fact(item) and action_key(item)
    }
    required = {
        str(item.get("request_id") or "")
        for item in facts
        if _eligible_business_write_fact(item) and str(item.get("request_id") or "")
    }
    required.update(
        str(item.get("request_id") or "")
        for item in facts
        if (
            item.get("keep")
            and str(item.get("role") or "") == "business_get"
            and str(item.get("request_id") or "")
            and action_key(item)
            and action_key(item) not in write_action_keys
            and not _step_is_write_preflight(materialized_step(item))
        )
    )
    return required


def _semantic_plan_execute_request_ids(
    spec: FlowSpec,
    semantic_plan: dict[str, Any],
) -> set[str]:
    request_id_by_step_id = {
        str(step.step_id): str((step.source_meta or {}).get("request_id") or "")
        for step in spec.steps
        if str(step.step_id or "")
    }
    execute_ids: set[str] = set()
    for capability in semantic_plan.get("capabilities") or []:
        if not isinstance(capability, dict):
            continue
        for ref in capability.get("request_refs") or []:
            if not isinstance(ref, dict) or str(ref.get("usage") or "") != "execute":
                continue
            request_id = str(ref.get("request_id") or "")
            step_id = str(ref.get("step_id") or "")
            resolved = request_id or request_id_by_step_id.get(step_id, "") or step_id
            if resolved:
                execute_ids.add(resolved)
    return execute_ids


def _semantic_plan_coverage(spec: FlowSpec, result: dict[str, Any]) -> dict[str, Any]:
    plan = result.get("semantic_plan") or result.get("plan")
    if not isinstance(plan, dict):
        return {
            "complete": False,
            "missing": ["semantic_plan"],
            "covered_steps": 0,
            "covered_fields": 0,
        }
    step_by_id = {step.step_id: step for step in spec.steps}
    allowed_usages = {"execute", "preflight", "option_source", "fact_check"}

    capability_items = [
        item for item in (plan.get("capabilities") or []) if isinstance(item, dict)
    ]
    referenced_step_ids = {
        str(ref.get("step_id") or "")
        for capability in capability_items
        for ref in (
            capability.get("request_refs")
            if isinstance(capability.get("request_refs"), list)
            else []
        )
        if isinstance(ref, dict) and str(ref.get("step_id") or "")
    }

    # Public ability count is the number of distinct recorded business actions,
    # not the number of HTTP writes. One click can execute several preflight/
    # write requests, while two independently anchored actions must never be
    # merged merely because their URLs share a domain.
    required_fields = [
        (step.step_id, param)
        for step in spec.steps
        if step.step_id in referenced_step_ids
        for param in step.params
    ]

    def field_contract_complete(param: ParamField) -> bool:
        public_label = str(param.label or param.key or "").strip()
        wire_leaf = str(param.key or param.path or "").split(".")[-1]
        wire_style_public_id = bool(
            param.exposed_to_user
            and public_label == str(param.key or "").strip()
            and public_label.isascii()
            and re.search(r"(?:Id|IDs?|_ids?)$", wire_leaf)
        )
        return bool(
            str(param.path or "").strip()
            and public_label
            and not wire_style_public_id
            and str(param.type or "").strip().lower() not in {"", "unknown"}
            and str(param.category or "").strip().lower() not in {"", "unknown"}
            and bool(str(param.source_kind or "").strip())
            and _field_source_configuration_advice(param) is None
            and isinstance(param.required, bool)
        )

    covered_fields = {
        (step_id, param.path)
        for step_id, param in required_fields
        if field_contract_complete(param)
    }
    field_axis_gaps = [
        {
            "step_id": step_id,
            "path": str(param.path or ""),
            "axes": [
                "name"
                if (
                    param.exposed_to_user
                    and str(param.label or "").strip() == str(param.key or "").strip()
                    and str(param.label or "").isascii()
                    and re.search(r"(?:Id|IDs?|_ids?)$", str(param.key or param.path or "").split(".")[-1])
                ) else "contract"
            ],
        }
        for step_id, param in required_fields
        if (step_id, param.path) not in covered_fields
    ]
    covered_steps: set[str] = set()
    names: set[str] = set()
    anchors: set[str] = set()
    capability_contract_invalid = False
    for capability in capability_items:
        name = str(capability.get("name") or "").strip()
        title = str(capability.get("title") or "").strip()
        kind = str(capability.get("kind") or "").strip()
        anchor_step_id = str(capability.get("anchor_step_id") or "").strip()
        refs = capability.get("request_refs")
        valid_refs = bool(
            isinstance(refs, list)
            and refs
            and all(
                isinstance(ref, dict)
                and str(ref.get("step_id") or "") in step_by_id
                and str(ref.get("usage") or "") in allowed_usages
                for ref in refs
            )
        )
        anchor_ref = bool(
            valid_refs
            and any(
                str(ref.get("step_id") or "") == anchor_step_id
                and str(ref.get("usage") or "") == "execute"
                for ref in refs
            )
        )
        valid = bool(
            name and title and kind in ALLOWED_CAPABILITY_KINDS
            and anchor_step_id in step_by_id
            and valid_refs and anchor_ref
            and name not in names and anchor_step_id not in anchors
            and _planned_capability_has_public_anchor(spec, kind, [anchor_step_id])
        )
        if not valid:
            capability_contract_invalid = True
            continue
        names.add(name)
        anchors.add(anchor_step_id)
        covered_steps.add(anchor_step_id)
    missing: list[str] = []
    if any(
        _eligible_business_write_fact(item)
        and not _materialized_step_id_for_request(spec, item)
        for item in _request_fact_items(spec)
    ):
        missing.append("request_materialization")
    if len(covered_fields) != len(required_fields):
        missing.append("field_axis_contract")
    if not capability_items:
        missing.append("capabilities")
    elif capability_contract_invalid:
        missing.append("capability_contracts")
    missing_public_action_request_ids = sorted(
        _required_public_action_request_ids(spec)
        - _semantic_plan_execute_request_ids(spec, plan)
    )
    if missing_public_action_request_ids:
        missing.append("public_action_coverage")
    understanding = plan.get("business_understanding")
    if not isinstance(understanding, dict) or not any(
        str(understanding.get(key) or "").strip()
        for key in ("business_name", "summary", "intent", "object", "purpose")
    ):
        missing.append("business_understanding")
    unresolved_items = plan.get("unresolved_items", [])
    if not isinstance(unresolved_items, list) or any(
        not isinstance(item, dict)
        or item.get("blocking") is True
        or str(item.get("severity") or "").strip().lower()
        in {"high", "critical", "blocker", "error"}
        for item in (unresolved_items if isinstance(unresolved_items, list) else [])
    ):
        missing.append("unresolved_blockers")
    return {
        "complete": not missing,
        "missing": missing,
        "covered_steps": len(covered_steps),
        "total_steps": len(capability_items),
        "covered_fields": len(covered_fields),
        "total_fields": len(required_fields),
        "field_axis_gaps": field_axis_gaps,
        "missing_public_action_request_ids": missing_public_action_request_ids,
    }


def _pre_materialization_semantic_plan_coverage(
    spec: FlowSpec,
    semantic_plan: dict[str, Any],
    fact_request_ids: set[str],
) -> dict[str, Any]:
    """Validate a strict live plan before request facts become FlowSteps."""
    capability_items = [
        item for item in semantic_plan.get("capabilities") or []
        if isinstance(item, dict)
    ]
    missing: list[str] = []
    names: set[str] = set()
    anchors: set[str] = set()
    for capability in capability_items:
        name = str(capability.get("name") or "").strip()
        title = str(capability.get("title") or "").strip()
        kind = str(capability.get("kind") or "").strip()
        anchor = str(capability.get("anchor_step_id") or "").strip()
        refs = capability.get("request_refs")
        execute_refs = [
            ref for ref in (refs if isinstance(refs, list) else [])
            if isinstance(ref, dict) and str(ref.get("usage") or "") == "execute"
        ]
        valid = bool(
            name and title and kind in ALLOWED_CAPABILITY_KINDS
            and anchor in fact_request_ids
            and name not in names and anchor not in anchors
            and isinstance(refs, list) and refs
            and all(
                isinstance(ref, dict)
                and str(ref.get("step_id") or "") in fact_request_ids
                and str(ref.get("usage") or "")
                in {"execute", "preflight", "option_source", "fact_check"}
                for ref in (refs or [])
            )
            and len(execute_refs) == 1
            and str(execute_refs[0].get("step_id") or "") == anchor
        )
        if not valid:
            missing.append("capability_contracts")
            break
        names.add(name)
        anchors.add(anchor)

    if not capability_items:
        missing.append("capabilities")
    missing_public_action_request_ids = sorted(
        _required_public_action_request_ids(spec)
        - _semantic_plan_execute_request_ids(spec, semantic_plan)
    )
    if missing_public_action_request_ids:
        missing.append("public_action_coverage")
    from dano.execution.page.recording_live import _recording_goal_contract

    expected_count = int(_recording_goal_contract(spec).get("expected_count") or 0)
    if expected_count and len(capability_items) != expected_count:
        missing.append("goal_capability_count")
    understanding = semantic_plan.get("business_understanding")
    if not isinstance(understanding, dict) or not any(
        str(understanding.get(key) or "").strip()
        for key in ("business_name", "summary", "intent", "object", "purpose")
    ):
        missing.append("business_understanding")
    unresolved_items = semantic_plan.get("unresolved_items", [])
    if not isinstance(unresolved_items, list) or any(
        not isinstance(item, dict)
        or item.get("blocking") is True
        or str(item.get("severity") or "").strip().lower()
        in {"high", "critical", "blocker", "error"}
        for item in (unresolved_items if isinstance(unresolved_items, list) else [])
    ):
        missing.append("unresolved_blockers")
    missing = list(dict.fromkeys(missing))
    return {
        "complete": not missing,
        "missing": missing,
        "covered_steps": len(anchors),
        "total_steps": expected_count or len(capability_items),
        "covered_fields": 0,
        "total_fields": 0,
        "phase": "request_facts",
        "missing_public_action_request_ids": missing_public_action_request_ids,
    }


def _apply_semantic_business_understanding(
    spec: FlowSpec,
    semantic_plan: dict[str, Any],
) -> FlowSpec:
    """Apply Skill-authored business identity without inventing titles."""
    understanding = semantic_plan.get("business_understanding")
    understanding = understanding if isinstance(understanding, dict) else {}
    title_source = str((spec.meta or {}).get("title_source") or "")
    model_title = _clean_page_business_candidate(
        understanding.get("business_name") or understanding.get("object") or ""
    )
    page_title = _page_context_business_name(spec)
    if title_source != "user":
        if model_title:
            spec.title = model_title
            spec.meta = {**(spec.meta or {}), "title_source": "semantic_plan"}
        elif _is_technical_business_title(spec.title) and page_title:
            spec.title = page_title
            spec.meta = {**(spec.meta or {}), "title_source": "page_context"}
    description_source = str((spec.meta or {}).get("business_description_source") or "")
    proposed_description = str(
        understanding.get("summary") or understanding.get("intent") or ""
    ).strip()
    if description_source != "user" and proposed_description:
        spec.business_description = proposed_description
        spec.meta = {**(spec.meta or {}), "business_description_source": "semantic_plan"}
    return _ensure_capability_explanations(spec, semantic_plan)


def _complete_semantic_plan_from_spec(
    spec: FlowSpec,
    proposed: dict[str, Any] | None,
) -> dict[str, Any]:
    """Persist the same strict semantic contract exposed by the Pi tool."""
    proposed_plan = copy.deepcopy(proposed) if isinstance(proposed, dict) else {}
    plan: dict[str, Any] = {}
    understanding = proposed_plan.get("business_understanding")
    if not isinstance(understanding, dict):
        understanding = {}
    business_name = str(
        _clean_page_business_candidate(understanding.get("business_name"))
        or ("" if _is_technical_business_title(spec.title) else spec.title)
        or _page_context_business_name(spec)
        or ""
    ).strip()
    if _is_technical_business_title(str(understanding.get("business_name") or "")):
        understanding["business_name"] = business_name
    else:
        understanding.setdefault("business_name", business_name)
    plan["business_understanding"] = understanding

    def strict_refs(raw_refs: Any) -> list[dict[str, str]]:
        refs: list[dict[str, str]] = []
        for raw in raw_refs if isinstance(raw_refs, list) else []:
            if not isinstance(raw, dict):
                continue
            step_id = str(raw.get("step_id") or "")
            usage = str(raw.get("usage") or "")
            if (
                step_id
                and any(step.step_id == step_id for step in spec.steps)
                and usage in {"execute", "preflight", "option_source", "fact_check"}
                and {"step_id": step_id, "usage": usage} not in refs
            ):
                refs.append({"step_id": step_id, "usage": usage})
        return refs

    capability_by_name: dict[str, dict[str, Any]] = {}
    for raw in proposed_plan.get("capabilities") or []:
        if not isinstance(raw, dict) or not str(raw.get("name") or ""):
            continue
        item = {
            "name": str(raw.get("name") or ""),
            "title": str(raw.get("title") or raw.get("name") or ""),
            "kind": str(raw.get("kind") or ""),
            "anchor_step_id": str(raw.get("anchor_step_id") or ""),
            "request_refs": strict_refs(raw.get("request_refs")),
        }
        capability_by_name[item["name"]] = item
    for capability in spec.capabilities or []:
        if capability.name in capability_by_name:
            continue
        refs = strict_refs([
            ref.model_dump(exclude_none=True) for ref in (capability.request_refs or [])
        ])
        candidate_ids = [
            ref["step_id"] for ref in refs if ref["usage"] == "execute"
        ] or list(_capability_node_step_ids(capability))
        anchor_step_id = next((
            step_id for step_id in reversed(candidate_ids)
            if _planned_capability_has_public_anchor(spec, capability.kind, [step_id])
        ), "")
        if not anchor_step_id:
            continue
        if not any(
            ref["step_id"] == anchor_step_id and ref["usage"] == "execute"
            for ref in refs
        ):
            refs.append({"step_id": anchor_step_id, "usage": "execute"})
        capability_by_name[capability.name] = {
            "name": capability.name,
            "title": capability.title or capability.name,
            "kind": capability.kind,
            "anchor_step_id": anchor_step_id,
            "request_refs": refs,
        }
    plan["capabilities"] = list(capability_by_name.values())
    plan["unresolved_items"] = [
        copy.deepcopy(item)
        for item in (proposed_plan.get("unresolved_items") or [])
        if isinstance(item, dict)
    ]
    return plan


def _semantic_mutable_context(
    spec: FlowSpec,
    *,
    validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Current contract delta; immutable request facts live in the prompt prefix."""
    context = _orchestration_context(spec, validation=validation)
    for key in (
        "complete_field_index", "complete_response_path_index", "steps",
        "links", "captured_requests",
    ):
        context.pop(key, None)
    findings = context.get("validation_findings") or {}
    context["validation_findings"] = {
        "errors": list(findings.get("errors") or [])[:30],
        "warnings": list(findings.get("warnings") or [])[:30],
        "unused_high_confidence_requests": list(findings.get("unused_high_confidence_requests") or [])[:40],
    }
    previous_model = (spec.meta or {}).get("capability_model") or {}
    previous_plan = previous_model.get("semantic_plan")
    if isinstance(previous_plan, dict) and previous_plan:
        context["accepted_semantic_plan_hash"] = _stable_json_hash(previous_plan)
    generation_state = (spec.meta or {}).get("capability_generation") or {}
    context["generation_state"] = {
        key: generation_state.get(key)
        for key in ("protocol", "initial_completed", "semantic_plan_hash", "generation_epoch", "status")
        if generation_state.get(key) not in (None, "")
    }
    return context


def _semantic_wire_hash(spec: FlowSpec) -> str:
    """Hash executable interface identity while excluding public field names."""
    payload = [
        {
            "step_id": step.step_id,
            "method": (step.method or "GET").upper(),
            "path": step.path or step.url,
            "content_type": step.content_type,
            "param_paths": sorted((param.path, param.wire_type or "") for param in step.params),
        }
        for step in spec.steps
    ]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _semantic_candidate_gate(
    before: FlowSpec,
    candidate: FlowSpec,
    *,
    allow_screenshot_query_additions: bool = False,
    allow_grounded_wire_change: bool = False,
) -> tuple[bool, dict[str, Any]]:
    """Admit an automatic proposal only when executable quality is monotonic."""
    before_report = validate_flow_spec(before)
    after_report = validate_flow_spec(candidate)

    def generation_findings(report: dict[str, Any], key: str) -> list[str]:
        """Validation used to police generated proposals, not operator publish."""
        capability_report = report.get("capability_validation") or {}
        return list(dict.fromkeys(str(item) for item in [
            *(report.get(key) or []),
            *(capability_report.get(key) or []),
        ] if item))

    before_error_list = generation_findings(before_report, "errors")
    after_error_list = generation_findings(after_report, "errors")

    def error_signature(message: str) -> str:
        # Public titles/field names are expected semantic improvements. Error
        # identity must not change merely because a backticked display label did.
        return re.sub(r"`[^`]+`", "`<target>`", message)

    before_errors = {error_signature(item) for item in before_error_list}
    after_errors = {error_signature(item) for item in after_error_list}
    reasons: list[str] = []
    new_error_signatures = after_errors - before_errors
    new_errors = sorted(
        item for item in after_error_list if error_signature(item) in new_error_signatures
    )
    # During the first semantic generation a generic baseline may be split into
    # several explicit capabilities.  The same expected "not confirmed" error
    # then appears once per capability; that is not a new error class and must
    # not reject an otherwise valid split.  Incremental optimization remains
    # strict because its capability scope is locked.
    if new_errors:
        reasons.append("new_validation_errors")
    before_warning_list = generation_findings(before_report, "warnings")
    after_warning_list = generation_findings(after_report, "warnings")
    if (
        _semantic_wire_hash(before) != _semantic_wire_hash(candidate)
        and not allow_grounded_wire_change
        and not (
            allow_screenshot_query_additions
            and _only_grounded_screenshot_query_params_added(before, candidate)
        )
    ):
        reasons.append("wire_contract_changed")
    # Full validation already compiles and dry-runs each prepared candidate.
    # Reuse that exact result instead of compiling both models a second time.
    before_dry = before_report.get("dry_run")
    if not isinstance(before_dry, dict):
        before_dry = dry_run_flow_spec(before)
    after_dry = after_report.get("dry_run")
    if not isinstance(after_dry, dict):
        after_dry = dry_run_flow_spec(candidate)
    grounded_required_fields = {
        param.key
        for step in candidate.steps
        for param in step.params
        if param.required
        and any(
            isinstance(evidence, dict)
            and (
                evidence.get("required") is True
                or (
                    evidence.get("source") == "manual_edit"
                    and evidence.get("field") == "required"
                    and evidence.get("value") is True
                )
            )
            for evidence in (param.evidence or [])
        )
    }
    missing_after = set(after_dry.get("missing_params") or [])
    required_input_only = bool(
        missing_after
        and missing_after.issubset(grounded_required_fields)
        and not after_dry.get("build_errors")
        and not after_dry.get("self_check")
        and not after_dry.get("construct_errors")
        and bool((after_dry.get("fact_check") or {}).get("passed"))
    )
    if (
        bool(before_dry.get("ok"))
        and not bool(after_dry.get("ok"))
        and not required_input_only
    ):
        reasons.append("dry_run_regressed")
    audit = {
        "accepted": not reasons,
        "reasons": reasons,
        "new_errors": new_errors[:40],
        "before_errors": len(before_error_list),
        "after_errors": len(after_error_list),
        "before_warnings": len(before_warning_list),
        "after_warnings": len(after_warning_list),
        "before_dry_ok": bool(before_dry.get("ok")),
        "after_dry_ok": bool(after_dry.get("ok")),
        "boundary_reanalysis": True,
    }
    return not reasons, audit

_PENDING_FLOW_SPEC_HELPERS = {'_capability_node_step_ids': 'dano.execution.page.capability_refs', '_clean_page_business_candidate': 'dano.execution.page.capability_contracts', '_eligible_business_write_fact': 'dano.execution.page.capability_contracts', '_ensure_capability_explanations': 'dano.execution.page.capability_contracts', '_is_technical_business_title': 'dano.execution.page.capability_contracts', '_only_grounded_screenshot_query_params_added': 'dano.execution.page.capability_contracts', '_orchestration_context': 'dano.execution.page.capability_orchestration', '_page_context_business_name': 'dano.execution.page.capability_contracts', '_planned_capability_has_public_anchor': 'dano.execution.page.capability_contracts', 'ALLOWED_CAPABILITY_KINDS': 'dano.execution.page.capability_kinds', 'dry_run_flow_spec': 'dano.execution.page.flow_spec_core.request_contract', 'validate_flow_spec': 'dano.execution.page.flow_spec_validate'}


def _bind_flow_spec_helpers() -> None:
    import sys
    module_globals = globals()
    for name, owner in _PENDING_FLOW_SPEC_HELPERS.items():
        mod = sys.modules.get(owner)
        if mod is None or not hasattr(mod, name):
            continue
        module_globals[name] = getattr(mod, name)


_bind_flow_spec_helpers()
