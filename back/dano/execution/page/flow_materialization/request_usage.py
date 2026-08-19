"""Stage 5: request-fact to materialized-step identity indexes."""
from __future__ import annotations

from typing import Any
from dano.execution.page.flow_spec_core.models import (
    FlowSpec,
    FlowStep,
    RequestAnalysis,
    RequestFact,
    RequestUsage,
)
from dano.execution.page.request_capture import (
    extract_auth_headers,
)
from dano.execution.page.recording_facts import (
    _attach_request_role,
    _business_filter_count,
    _page_enum_options_from_request_facts,
    _path_from_url,
    _preread_candidate_score,
    _request_fact_key,
    _request_fact_key_from_entry,
    _request_fact_signature_key,
    _request_path,
    classify_network_request,
)
from dano.execution.page.flow_materialization.request_steps import (
    _build_step_from_capture,
    _merge_duplicate_step_contract,
    _request_url_with_query,
    _step_contract_richness,
)


def _mark_request_materialized(
    spec: FlowSpec,
    entry: dict[str, Any],
    *,
    materialized_step_id: str = "",
) -> None:
    request_id = _request_fact_key(entry)
    usage = spec.request_facts.usage.get(request_id) or RequestUsage(request_id=request_id)
    usage.state = "materialized" if materialized_step_id else usage.state or "captured"
    if materialized_step_id:
        usage.materialized_step_id = materialized_step_id
    spec.request_facts.usage[request_id] = usage


def _upgrade_materialized_query_facts(spec: FlowSpec) -> None:
    """Replace an initial pagination request with the richer searched instance."""
    manually_assigned_steps = {
        ref.step_id
        for cap in (spec.capabilities or [])
        for ref in (cap.request_refs or [])
        if ref.step_id and ref.origin in {"manual", "user"}
    }
    fact_rows = [
        fact.model_dump(exclude_none=True)
        for fact in (spec.request_facts.requests or [])
    ]
    for step in spec.steps:
        if (step.method or "GET").upper() not in {"GET", "HEAD"} or step.step_id in manually_assigned_steps:
            continue
        if any(
            _param_has_manual_contract(param)
            for param in (step.params or [])
            if str(param.path or "").startswith("query.")
        ):
            continue
        current_query = (step.source_meta or {}).get("query")
        current = {
            "method": step.method,
            "url": step.url or step.path,
            "index": (step.source_meta or {}).get("request_index"),
        }
        # An explicitly empty derived query must not mask the real query string
        # already present in the materialized URL. Doing so made this pass
        # rebuild the same request as a "richer" candidate and discard all DOM
        # names, required evidence and numeric constraints.
        if isinstance(current_query, dict) and current_query:
            current["query"] = dict(current_query)
        current_path = _request_path(current)
        candidates: list[tuple[RequestFact, RequestAnalysis | None, dict[str, Any], str]] = []
        for fact, raw in zip(spec.request_facts.requests or [], fact_rows):
            if (fact.method or "GET").upper() != (step.method or "GET").upper():
                continue
            if _request_path(raw) != current_path:
                continue
            analysis = spec.request_facts.analysis.get(fact.request_id or "")
            role = str(analysis.role if analysis is not None else raw.get("role") or "")
            if role not in {"business_get", "read_context"}:
                # Re-evaluate recordings made before business searches were
                # distinguished from option lists. The raw request fact stays
                # authoritative; only its derived role is refreshed.
                refreshed = classify_network_request(raw, trace=fact_rows)
                if refreshed.get("role") != "business_get":
                    continue
                role = "business_get"
            candidates.append((fact, analysis, raw, role))
        if not candidates:
            continue
        fact, analysis, best, best_role = max(
            candidates, key=lambda item: _preread_candidate_score(item[2]),
        )
        if _business_filter_count(best) <= _business_filter_count(current):
            continue
        step.url = _request_url_with_query(best)
        step.path = _path_from_url(step.url)
        step.response_json = fact.response_json
        if fact.headers:
            step.headers = extract_auth_headers(fact.headers)
        old_query_params = [
            param for param in (step.params or [])
            if str(param.path or "").startswith("query.")
        ]
        non_query_params = [
            param for param in (step.params or [])
            if not str(param.path or "").startswith("query.")
        ]
        grounded_request = {
            **best,
            "request_id": fact.request_id,
            "request_index": fact.request_index,
            "response_json": fact.response_json,
        }
        grounded_role = {
            "role": best_role,
            "keep": True,
            "reason": analysis.reason if analysis is not None else "",
            "confidence": analysis.confidence if analysis is not None else 0.0,
            "evidence": analysis.evidence if analysis is not None else {},
        }
        rebuilt = _build_step_from_capture(
            _attach_request_role(grounded_request, grounded_role),
            reads=[],
            samples={},
            storage_state=None,
            required_labels=set(),
            page_enum_options=_page_enum_options_from_request_facts(spec.request_facts),
            step_index=0,
            field_evidence=list(getattr(spec.request_facts, "field_evidence", []) or []),
        )
        rebuilt_query_params = [
            param for param in rebuilt.params
            if str(param.path or "").startswith("query.")
        ]
        step.params = [*non_query_params, *rebuilt_query_params]
        step.selects = [
            binding for binding in (step.selects or [])
            if not str(binding.path or binding.id_path or "").startswith("query.")
        ] + [
            binding for binding in rebuilt.selects
            if str(binding.path or binding.id_path or "").startswith("query.")
        ]
        for param in old_query_params:
            step.sample_inputs.pop(str(param.key or ""), None)
        step.sample_inputs.update({
            param.key: param.value for param in rebuilt_query_params
            if param.key and param.value not in (None, "")
        })
        for usage in spec.request_facts.usage.values():
            if usage.materialized_step_id == step.step_id:
                usage.materialized_step_id = ""
                usage.state = "captured"
        step.source_meta = {
            **(step.source_meta or {}),
            "url": step.url,
            "query": dict(fact.query or {}),
            "request_id": fact.request_id,
            "request_index": fact.request_index,
            "response_status": fact.response_status,
            "role": best_role or (step.source_meta or {}).get("role"),
            "confidence": analysis.confidence if analysis else (step.source_meta or {}).get("confidence"),
            "query_fact_upgraded": True,
        }


def _retarget_step_references(spec: FlowSpec, replacements: dict[str, str]) -> None:
    if not replacements:
        return

    def replace(value: Any) -> Any:
        return replacements.get(str(value or ""), value)

    def retarget_nodes(nodes: list[dict[str, Any]]) -> None:
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            for key in ("step_id", "from", "source"):
                if key in node:
                    node[key] = replace(node.get(key))
            for child_key in ("children", "steps", "then", "else", "otherwise"):
                if isinstance(node.get(child_key), list):
                    retarget_nodes(node[child_key])

    for link in spec.links or []:
        source_step_id = replace(link.source_step_id)
        target_step_id = replace(link.target_step_id)
        if source_step_id != link.source_step_id or target_step_id != link.target_step_id:
            from dano.execution.page.recording_live import invalidate_dependency_verification

            invalidate_dependency_verification(link, "依赖步骤已重定向，需要重新验证")
        link.source_step_id = source_step_id
        link.target_step_id = target_step_id
    for item in spec.review_items or []:
        item.target = {
            key: replace(value) if key in {"step_id", "source_step_id", "target_step_id"} else value
            for key, value in (item.target or {}).items()
        }
    for capability in spec.capabilities or []:
        retarget_nodes(capability.nodes or [])
        capability.step_ids = list(dict.fromkeys(replace(step_id) for step_id in capability.step_ids or []))
        for ref in capability.request_refs or []:
            ref.step_id = replace(ref.step_id)
        for field_name in (
            "inputs", "request_fields", "internal_fields", "computed_fields", "outputs",
        ):
            for field in getattr(capability, field_name) or []:
                field.step_id = replace(field.step_id)
        for dependency in capability.dependencies or []:
            if "step_id" in (dependency.source or {}):
                dependency.source["step_id"] = replace(dependency.source.get("step_id"))
            if "step_id" in (dependency.target or {}):
                dependency.target["step_id"] = replace(dependency.target.get("step_id"))
        for mapping in capability.output_mapping or []:
            if isinstance(mapping, dict):
                for key in ("step_id", "from", "source"):
                    if key in mapping:
                        mapping[key] = replace(mapping.get(key))
        for evidence in capability.evidence or []:
            if isinstance(evidence, dict) and "anchor_step_id" in evidence:
                evidence["anchor_step_id"] = replace(evidence.get("anchor_step_id"))
    for usage in (spec.request_facts.usage or {}).values():
        usage.materialized_step_id = replace(usage.materialized_step_id)
        for membership in usage.capability_memberships or []:
            if isinstance(membership, dict) and "step_id" in membership:
                membership["step_id"] = replace(membership.get("step_id"))
    for evidence in getattr(spec.request_facts, "field_evidence", []) or []:
        if isinstance(evidence, dict) and "step_id" in evidence:
            evidence["step_id"] = replace(evidence.get("step_id"))

    capability_model = (spec.meta or {}).get("capability_model") or {}
    semantic_plan = capability_model.get("semantic_plan") if isinstance(capability_model, dict) else None
    if isinstance(semantic_plan, dict):
        for capability in semantic_plan.get("capabilities") or []:
            if not isinstance(capability, dict):
                continue
            if "anchor_step_id" in capability:
                capability["anchor_step_id"] = replace(capability.get("anchor_step_id"))
            for ref in capability.get("request_refs") or []:
                if isinstance(ref, dict) and "step_id" in ref:
                    ref["step_id"] = replace(ref.get("step_id"))


def _canonicalize_materialized_request_identities(spec: FlowSpec) -> None:
    """One captured request identity may own only one materialized FlowStep."""
    grouped: dict[str, list[FlowStep]] = {}
    for step in spec.steps:
        meta = step.source_meta or {}
        request_id = str(meta.get("request_id") or "").strip()
        request_index = meta.get("request_index")
        identity = f"id:{request_id}" if request_id else (
            f"idx:{request_index}" if request_index is not None else ""
        )
        if identity:
            grouped.setdefault(identity, []).append(step)

    replacements: dict[str, str] = {}
    removed_ids: set[str] = set()
    for duplicates in grouped.values():
        if len(duplicates) < 2:
            continue
        canonical = max(duplicates, key=_step_contract_richness)
        for duplicate in duplicates:
            if duplicate is canonical:
                continue
            _merge_duplicate_step_contract(canonical, duplicate)
            replacements[duplicate.step_id] = canonical.step_id
            removed_ids.add(duplicate.step_id)
    if not removed_ids:
        return
    spec.steps = [step for step in spec.steps if step.step_id not in removed_ids]
    _retarget_step_references(spec, replacements)
    _collapse_duplicate_generated_capabilities(spec)
    spec.meta = {
        **(spec.meta or {}),
        "deduped_request_identity_count": (
            int((spec.meta or {}).get("deduped_request_identity_count") or 0) + len(removed_ids)
        ),
    }


def _materialized_step_id_for_request(spec: FlowSpec, entry: dict[str, Any]) -> str:
    """Resolve only exact request identity; duplicate paths are distinct facts."""
    step_ids = {step.step_id for step in spec.steps}
    usage_id = str(entry.get("materialized_step_id") or "")
    if usage_id in step_ids:
        return usage_id
    request_key = _request_fact_key_from_entry(entry)
    if request_key.startswith(("id:", "idx:")):
        return next(
            (step.step_id for step in spec.steps if _step_request_key(step) == request_key),
            "",
        )
    signature = _request_fact_signature_key(entry)
    matches = [
        step.step_id for step in spec.steps
        if _step_request_signature_key(step) == signature
    ]
    return matches[0] if len(matches) == 1 else ""

_PENDING_FLOW_SPEC_HELPERS = {'_collapse_duplicate_generated_capabilities': 'dano.execution.page.capability_orchestration', '_param_has_manual_contract': 'dano.execution.page.flow_materialization.field_contracts.common', '_step_request_key': 'dano.execution.page.capability_refs', '_step_request_signature_key': 'dano.execution.page.capability_contracts'}


def _bind_flow_spec_helpers() -> None:
    import sys
    module_globals = globals()
    for name, owner in _PENDING_FLOW_SPEC_HELPERS.items():
        mod = sys.modules.get(owner)
        if mod is None or not hasattr(mod, name):
            continue
        module_globals[name] = getattr(mod, name)


_bind_flow_spec_helpers()
