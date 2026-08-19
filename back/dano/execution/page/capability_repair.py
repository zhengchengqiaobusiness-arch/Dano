"""Stage 6: deterministic capability repair and autofix."""
from __future__ import annotations

from typing import Any
import copy
from datetime import datetime, timezone
import re
from dano.execution.page.flow_spec_core.models import (
    FlowCapability,
    FlowSpec,
)
from dano.execution.page.request_capture import (
    as_list_payload,
    normalized_leaf_paths,
)
from dano.execution.page.recording_field_identity import (
    FieldRef,
    resolve_field_ref,
)
from dano.execution.page.recording_live import (
    LIVE_RECORDING_AGENT_OPS,
)
from dano.execution.page.recording_facts import (
    _WRITE_METHODS,
    _request_fact_items,
    _request_path,
)
from dano.execution.page.flow_materialization.links import (
    _auto_dependency_target_allowed,
    rebuild_flow_dependencies,
)
from dano.execution.page.flow_materialization.request_steps import (
    _entry_sequence,
)
from dano.execution.page.flow_spec_core.fingerprints import (
    _flow_fingerprint,
)
from dano.execution.page.flow_materialization.field_contracts.option_repair import (
    _repair_structural_option_bindings,
)
from dano.execution.page.recording_agent_contract import (
    _validate_recording_agent_ops,
)


def _repair_generated_capability_contracts(
    spec: FlowSpec,
    *,
    repair_option_bindings: bool = True,
) -> FlowSpec:
    """Deterministically repair only Planner-generated capability contracts."""
    _normalize_capability_references(spec)
    _apply_mechanical_field_contracts(spec)
    rebuild_flow_dependencies(spec)
    if repair_option_bindings:
        _repair_structural_option_bindings(spec)
    by_id = {step.step_id: step for step in spec.steps}
    renamed: dict[str, str] = {}
    for cap in spec.capabilities or []:
        old_name = cap.name
        was_generated_duplicate = bool(re.fullmatch(r"submit_batch\d+", str(cap.name or "")))
        needed_batch_audit = cap.kind in {"submit_batch", "validate_batch"}
        _normalize_generated_capability_semantics(spec, cap)
        if not cap.locked:
            for mapping in cap.output_mapping or []:
                if not isinstance(mapping, dict):
                    continue
                name = str(mapping.get("name") or "")
                if not name or re.fullmatch(r"(?:output|result)(?:_?\d+)?", name, re.I):
                    mapping["name"] = "result"
        if old_name and cap.name and old_name != cap.name:
            renamed[old_name] = cap.name
        if cap.locked or (not cap.evidence and not was_generated_duplicate and not needed_batch_audit):
            continue
        cap.nodes = _sanitize_capability_nodes(spec, cap)
        cap.nodes = [
            node for node in (cap.nodes or [])
            if not (
                isinstance(node, dict)
                and node.get("type") == "condition"
                and not any(
                    isinstance(node.get(key), list) and node.get(key)
                    for key in ("then", "else", "otherwise", "children", "steps")
                )
            )
        ]
        cap_step_ids = set(cap.step_ids or [])
        valid_mapping: list[dict[str, Any]] = []
        for mapping in cap.output_mapping or []:
            if not isinstance(mapping, dict):
                continue
            step_id = str(mapping.get("step_id") or mapping.get("from") or "")
            path = str(mapping.get("response_path") or mapping.get("path") or mapping.get("field") or "response")
            if step_id not in cap_step_ids or not _capability_response_path_exists(by_id.get(step_id), path):
                continue
            valid_mapping.append(dict(mapping))
        if cap.kind == "query_status" and cap_step_ids:
            query_steps = [by_id[sid] for sid in cap.step_ids if sid in by_id]
            semantic_mapping = _query_output_mappings(query_steps)
            if any(str(item.get("response_path") or "") not in {"", "response"} for item in semantic_mapping):
                valid_mapping = semantic_mapping
        if not valid_mapping and cap_step_ids:
            final = next((step for step in reversed(spec.steps) if step.step_id in cap_step_ids), None)
            if final is not None:
                valid_mapping = [{
                    "kind": "final_response",
                    "name": "result",
                    "step_id": final.step_id,
                    "response_path": "response",
                }]
        cap.output_mapping = valid_mapping
    if renamed:
        for relation in spec.capability_relations or []:
            relation.from_capability = renamed.get(relation.from_capability, relation.from_capability)
            relation.to_capability = renamed.get(relation.to_capability, relation.to_capability)
        for step in spec.steps:
            for param in step.params or []:
                source = param.source or {}
                source_capability = str(source.get("source_capability") or "")
                if source_capability in renamed:
                    param.source = {
                        **source,
                        "source_capability": renamed[source_capability],
                    }
    _canonicalize_public_capability_identities(spec)
    spec = _prune_empty_capabilities(spec)
    _attach_option_source_memberships(spec)
    valid_refs = {
        ref
        for cap in spec.capabilities or []
        for ref in (str(cap.name or ""), str(cap.capability_id or ""))
        if ref
    }
    cap_by_ref = {
        ref: cap
        for cap in spec.capabilities or []
        for ref in (str(cap.name or ""), str(cap.capability_id or ""))
        if ref
    }
    spec.capability_relations = [
        relation
        for relation in (spec.capability_relations or [])
        if relation.from_capability in valid_refs
        and relation.to_capability in valid_refs
        and not (
            relation.to_input in {"entries", "items"}
            and (cap_by_ref.get(relation.to_capability) is not None)
            and cap_by_ref[relation.to_capability].kind not in {"submit_batch", "validate_batch"}
        )
    ]
    return spec


def _planner_patch_edits(
    spec: FlowSpec,
    edits: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Accept only edits grounded in already materialized FlowSpec facts."""
    existing_steps = {step.step_id for step in spec.steps}
    step_by_id = {step.step_id: step for step in spec.steps}
    cap_by_name = {cap.name: cap for cap in spec.capabilities if cap.name}
    safe: list[dict[str, Any]] = []
    scope_ops = {
        "add_request_step", "add_candidate_step", "promote_request",
        "add_capability", "create_capability", "remove_capability",
        "reject_dependency",
    }
    for raw in edits or []:
        edit = dict(raw)
        op = str(edit.get("op") or "")
        if op in scope_ops:
            continue
        if op == "remove_request_from_capability":
            if not edit.get("_semantic_boundary_reconcile"):
                continue
            cap_name = str(
                edit.get("capability_name") or edit.get("capability") or ""
            )
            step_id = str(edit.get("step_id") or "")
            target = cap_by_name.get(cap_name)
            planner_managed = bool(
                target is not None
                and not target.locked
                and target.updated_by != "user"
                and not any(
                    ref.origin in {"manual", "user"}
                    for ref in (target.request_refs or [])
                )
            )
            if (
                not planner_managed
                or step_id not in set(_capability_node_step_ids(target))
            ):
                continue
        if op == "add_request_to_capability":
            # Planner 只能重组已经在字段/接口工作台物化的步骤，不能用 request_id
            # 或 request_index 从捕获事实库静默拉入新接口。
            step_id = str(edit.get("step_id") or "")
            if not step_id or step_id not in existing_steps:
                continue
            if (step_by_id[step_id].source_meta or {}).get(
                "duplicate_observation_of"
            ):
                continue
            cap_name = str(edit.get("capability_name") or edit.get("capability") or "")
            if _capability_step_was_removed(spec, cap_name, step_id):
                continue
            target_cap = cap_by_name.get(cap_name)
            current_owners = [
                cap for cap in spec.capabilities
                if step_id in set(_capability_node_step_ids(cap))
            ]
            if target_cap is not None and current_owners and target_cap not in current_owners:
                target_ids = set(_capability_node_step_ids(target_cap))
                linked = any(
                    {link.source_step_id, link.target_step_id} & {step_id}
                    and ({link.source_step_id, link.target_step_id} - {step_id}) & target_ids
                    for link in spec.links
                )
                explicit_owners = {
                    str(value) for value in (
                        (step_by_id[step_id].source_meta or {}).get("control_preflight_for_write_ids") or []
                    ) if str(value)
                }
                if not linked and not (explicit_owners & target_ids):
                    continue
        if op == "upsert_capability":
            payload = dict(edit.get("capability") or {})
            name = str(edit.get("capability_name") or edit.get("capability") or edit.get("name") or "")
            if payload:
                name = str(payload.get("name") or name)
                # Re-analysis may introduce a real new public boundary, but it
                # may not restore a capability explicitly removed by the user.
                if name in _removed_capability_names(spec):
                    continue
                for key in ("step_ids", "request_refs", "nodes"):
                    payload.pop(key, None)
                edit["capability"] = payload
        if op == "update_capability" and str(edit.get("field") or "") in {"step_ids", "nodes", "request_refs"}:
            continue
        if op in {"add", "bind_dependency"}:
            confidence = float(edit.get("confidence") or (edit.get("link") or {}).get("confidence") or 0.0)
            if confidence < 0.95:
                continue
            if op == "add":
                link = dict(edit.get("link") or {})
                source_step_id = str(link.get("source_step_id") or "")
                source_path = str(link.get("source_path") or "")
                target_step_id = str(link.get("target_step_id") or "")
                target_path = str(link.get("target_path") or "")
                scoped_cap = None
            else:
                source = dict(edit.get("source") or {})
                target = dict(edit.get("target") or {})
                source_step_id = str(source.get("step_id") or edit.get("source_step_id") or "")
                source_path = str(source.get("path") or edit.get("source_path") or "")
                target_step_id = str(target.get("step_id") or edit.get("target_step_id") or "")
                target_path = str(target.get("path") or edit.get("target_path") or "")
                cap_name = str(edit.get("capability_name") or edit.get("capability") or "")
                scoped_cap = cap_by_name.get(cap_name)
            if "[" in source_path:
                # Planner proposals cannot turn an arbitrary collection row
                # into a scalar field dependency.
                continue
            source_step = step_by_id.get(source_step_id)
            target_step = step_by_id.get(target_step_id)
            target_param = next((
                param for param in (target_step.params if target_step else [])
                if _strip_body_prefix(param.path) == _strip_body_prefix(target_path)
            ), None)
            if source_step is None or target_param is None:
                continue
            if not _capability_response_path_exists(source_step, source_path):
                continue
            if target_param.locked or not _auto_dependency_target_allowed(target_param):
                continue
            if target_param.category == "user_param" or target_param.source_kind == "user_input":
                continue
            if scoped_cap is not None:
                scoped_ids = set(_capability_node_step_ids(scoped_cap))
                if source_step_id not in scoped_ids or target_step_id not in scoped_ids:
                    continue
        safe.append(edit)
    return safe


def _flow_autofix_context(spec: FlowSpec, report: dict[str, Any]) -> dict[str, Any]:
    request_facts = _request_fact_items(spec)
    cap_validation = report.get("capability_validation") or {}
    recorded_field_evidence = _client_redact_sensitive(
        copy.deepcopy((getattr(spec.request_facts, "field_evidence", []) or [])[-500:]),
    )
    recorded_option_sources = _client_redact_sensitive(
        copy.deepcopy((spec.request_facts.option_sources or [])[:120]),
    )
    recorded_page_events = _client_redact_sensitive(
        copy.deepcopy((spec.request_facts.page_events or [])[-300:]),
    )
    admitted_request_ids: set[str] = set()
    admitted_paths: set[str] = set()
    for source in spec.request_facts.option_sources or []:
        if not isinstance(source, dict):
            continue
        if source.get("kind") == "api_response":
            admitted_request_ids.add(str(source.get("request_id") or ""))
            admitted_paths.add(_request_path({"url": str(source.get("path") or "")}))
        elif source.get("kind") == "page_enum_options":
            for option in (source.get("options") or {}).values():
                if not isinstance(option, dict):
                    continue
                admitted_request_ids.update(str(value) for value in (option.get("source_request_ids") or []) if value)
                admitted_paths.add(_request_path({"url": str(option.get("source_url") or "")}))
    admitted_paths.update(
        _request_path({"url": binding.source_url})
        for step in spec.steps for binding in (step.selects or []) if binding.source_url
    )
    admitted_request_ids.discard("")
    admitted_paths.discard("")
    option_sources: list[dict[str, Any]] = []
    for fact in (spec.request_facts.requests or []):
        if (fact.method or "").upper() != "GET":
            continue
        if (
            str(fact.request_id or "") not in admitted_request_ids
            and _request_path({"url": fact.path or fact.url}) not in admitted_paths
        ):
            continue
        items = as_list_payload(fact.response_json)
        if not items:
            continue
        option_sources.append({
            "request_id": fact.request_id,
            "request_index": fact.request_index,
            "path": fact.path or fact.url,
            "sample_items": items[:20],
            "count": len(items),
        })
        if len(option_sources) >= 30:
            break
    return {
        "title": spec.title,
        "goal": spec.goal,
        "errors": list(report.get("errors") or [])[:40],
        "warnings": list(report.get("warnings") or [])[:40],
        "suggestions": list(report.get("suggestions") or [])[:80],
        "capability_validation": report.get("capability_validation") or {},
        "capability_findings": {
            "unused_high_confidence_requests": list(cap_validation.get("unused_high_confidence_requests") or [])[:80],
            "capability_internal": cap_validation.get("capability_internal") or {},
            "capability_relations": cap_validation.get("capability_relations") or {},
            "skill_level": cap_validation.get("skill_level") or {},
        },
        "steps": [
            {
                "step_id": st.step_id,
                "name": st.name,
                "method": st.method,
                "path": st.path or st.url,
                "params": [
                    {
                        "path": p.path,
                        "key": p.key,
                        "label": p.label,
                        "value": p.value,
                        "type": p.type,
                        "source_kind": p.source_kind,
                        "exposed_to_user": p.exposed_to_user,
                        "reason": p.reason,
                        "enum_options": list(p.enum_options or [])[:30],
                        "enum_value_map": dict(p.enum_value_map or {}),
                        "evidence": list(p.evidence or [])[:10],
                    }
                    for p in (st.params or [])[:60]
                ],
                "response_paths": normalized_leaf_paths(st.response_json, max_paths=80),
                "selects": [sel.model_dump(exclude_none=True) for sel in (st.selects or [])[:20]],
            }
            for st in spec.steps
        ],
        "capabilities": [
            {
                **cap.model_dump(exclude_none=True),
                "contract": _capability_execution_contract(spec, cap),
            }
            for cap in spec.capabilities
        ],
        "request_facts": [
            {
                "request_id": r.get("request_id"),
                "request_index": r.get("request_index"),
                "method": r.get("method"),
                "path": r.get("path") or r.get("url"),
                "role": r.get("role"),
                "confidence": r.get("confidence"),
                "reason": r.get("reason"),
            }
            for r in request_facts[:120]
        ],
        "recorded_field_evidence": recorded_field_evidence,
        "recorded_option_sources": recorded_option_sources,
        "page_events": recorded_page_events,
        "candidate_option_sources": option_sources,
    }


def _autofix_ops_to_edits(
    spec: FlowSpec,
    ops: list[dict[str, Any]],
    *,
    allow_scope_changes: bool = True,
) -> list[dict[str, Any]]:
    from dano.execution.page.recording_live import LIVE_RECORDING_AGENT_OPS

    edits: list[dict[str, Any]] = []
    cap_by_name = {c.name: idx for idx, c in enumerate(spec.capabilities or []) if c.name}
    step_by_id = {step.step_id: step for step in spec.steps}

    def locked_param(step_id: str, path: str) -> bool:
        from dano.execution.page.recording_field_identity import FieldRef, FieldReferenceError, resolve_field_ref

        try:
            param = resolve_field_ref(spec, FieldRef(
                step_id=step_id if step_id in step_by_id else "",
                request_id="" if step_id in step_by_id else step_id,
                wire_path=path,
            )).param
        except FieldReferenceError:
            param = None
        # Automatic edits use the stored request path as identity.  Treat an
        # unmatched path as unavailable instead of falling back to a name/leaf.
        return param is None or bool(param.locked)

    for op in ops or []:
        if not isinstance(op, dict):
            continue
        kind = str(op.get("op") or "")
        if kind == "rename_step":
            step_id = str(op.get("step_id") or "")
            name = str(op.get("name") or op.get("title") or "").strip()
            if step_id in step_by_id and name:
                edits.append({"op": "update", "step_id": step_id, "field": "name", "value": name})
        elif kind == "promote_request":
            if not allow_scope_changes:
                continue
            edits.append({
                "op": "add_request_step",
                "request_id": str(op.get("request_id") or ""),
                "request_index": op.get("request_index"),
            })
        elif kind == "rename_field":
            step_id = str(op.get("step_id") or "")
            path = str(op.get("path") or "")
            label = str(op.get("label") or "").strip()
            if step_id and path and label and not locked_param(step_id, path):
                edits.append({"op": "update", "step_id": step_id, "param_path": path, "field": "key", "value": label})
        elif kind == "bind_response_source":
            source_step = str(op.get("source_step") or "")
            target_step = str(op.get("target_step") or "")
            source_path = str(op.get("source_path") or "")
            target_path = str(op.get("target_path") or "")
            if source_step and target_step and source_path and target_path and not locked_param(target_step, target_path):
                edits.append({
                    "op": "add",
                    "link": {
                        "source_step_id": source_step,
                        "source_path": source_path,
                        "target_step_id": target_step,
                        "target_path": target_path,
                        "confirmed": False,
                        "confidence": float(op.get("confidence") or 0.75),
                        "reason": str(op.get("reason") or "一键修正建议的上游响应绑定"),
                    },
                })
        elif kind == "bind_option_source":
            target_step = str(op.get("target_step") or op.get("target_step_id") or "")
            target_path = str(op.get("target_path") or op.get("path") or "")
            source_step = str(op.get("source_step") or op.get("source_step_id") or "")
            source_url = str(op.get("source_url") or "")
            if target_step and target_path and (source_step or source_url) and not locked_param(target_step, target_path):
                edits.append({
                    "op": "bind_option_source",
                    "target_step": target_step,
                    "target_path": target_path,
                    "source_step": source_step,
                    "source_url": source_url,
                    "value_key": str(op.get("value_key") or ""),
                    "label_key": str(op.get("label_key") or ""),
                    "id_path": str(op.get("id_path") or ""),
                    "options": op.get("options") if isinstance(op.get("options"), list) else None,
                    "option_map": op.get("option_map") if isinstance(op.get("option_map"), dict) else None,
                    "multi": bool(op.get("multi")),
                })
        elif kind == "set_loop_source":
            cap_name = str(op.get("capability") or op.get("name") or "")
            if cap_name in cap_by_name:
                edits.append({
                    "op": "set_loop_source",
                    "capability_index": cap_by_name[cap_name],
                    "items": str(op.get("items") or op.get("source") or "input.entries"),
                })
        elif kind == "set_return_mapping":
            cap_name = str(op.get("capability") or op.get("name") or "")
            if cap_name in cap_by_name:
                edits.append({
                    "op": "set_return_mapping",
                    "capability_index": cap_by_name[cap_name],
                    "mapping": op.get("mapping") if isinstance(op.get("mapping"), list) else op.get("mapping"),
                    "step_id": op.get("step_id"),
                    "response_path": op.get("response_path") or op.get("path"),
                })
        elif kind == "mark_field_as_system_var":
            step_id = str(op.get("step_id") or "")
            path = str(op.get("path") or "")
            if step_id and path and not locked_param(step_id, path):
                edits.extend([
                    {"op": "update", "step_id": step_id, "param_path": path, "field": "category", "value": "runtime_var"},
                    {"op": "update", "step_id": step_id, "param_path": path, "field": "source_kind", "value": "unknown"},
                    {"op": "update", "step_id": step_id, "param_path": path, "field": "exposed_to_user", "value": False},
                ])
        elif kind == "mark_field_as_identity":
            step_id = str(op.get("step_id") or "")
            path = str(op.get("path") or "")
            source = str(op.get("source") or "current_user")
            if step_id and path and not locked_param(step_id, path):
                edits.extend([
                    {"op": "update", "step_id": step_id, "param_path": path, "field": "category", "value": "runtime_var"},
                    {"op": "update", "step_id": step_id, "param_path": path, "field": "source_kind", "value": source},
                    {"op": "update", "step_id": step_id, "param_path": path, "field": "exposed_to_user", "value": False},
                ])
        elif kind == "create_capability":
            if not allow_scope_changes:
                continue
            if str(op.get("name") or "") in _removed_capability_names(spec):
                continue
            raw = {
                "name": op.get("name"),
                "title": op.get("title") or op.get("name"),
                "intent": op.get("intent") or "",
                "kind": op.get("kind") or "submit",
                "step_ids": op.get("step_ids") if isinstance(op.get("step_ids"), list) else [],
                "nodes": op.get("nodes") if isinstance(op.get("nodes"), list) else [],
                "confidence": float(op.get("confidence") or 0.7),
                "requires_human_confirm": True,
            }
            if raw["name"]:
                edits.append({"op": "add_capability", "capability": raw})
        elif kind == "reorder_capability_steps":
            cap_name = str(op.get("capability") or op.get("name") or "")
            step_ids = op.get("step_ids")
            if cap_name in cap_by_name and isinstance(step_ids, list):
                edits.append({
                    "op": "reorder_capability_steps",
                    "capability_index": cap_by_name[cap_name],
                    "step_ids": [str(x) for x in step_ids],
                })
        elif kind in {
            "upsert_capability",
            "upsert_capability_field",
            "upsert_input_field",
            "upsert_request_field",
            "upsert_internal_field",
            "upsert_computed_field",
            "upsert_output_field",
            "bind_dependency",
            "set_map",
            "set_condition",
            "set_output_mapping",
            "set_capability_relation",
            "add_request_to_capability",
            "remove_request_from_capability",
        }:
            if not allow_scope_changes and kind in {
                "upsert_capability", "add_request_to_capability", "remove_request_from_capability",
            }:
                continue
            cap_name = str(op.get("capability") or op.get("capability_name") or op.get("name") or "")
            edit = {k: v for k, v in op.items() if k != "op"}
            edit["op"] = kind
            if cap_name in cap_by_name:
                edit["capability_index"] = cap_by_name[cap_name]
            elif kind not in {"set_capability_relation", "upsert_capability"}:
                if not cap_name:
                    continue
                edit["capability_name"] = cap_name
            if "field" in op and isinstance(op.get("field"), dict):
                edit["field_data"] = op.get("field")
                edit.pop("field", None)
            edits.append(edit)
        elif kind == "reject_dependency":
            link_id = str(op.get("link_id") or "")
            source_step = str(op.get("source_step") or op.get("source_step_id") or "")
            source_path = str(op.get("source_path") or "")
            target_step = str(op.get("target_step") or op.get("target_step_id") or "")
            target_path = str(op.get("target_path") or "")
            if link_id or all([source_step, source_path, target_step, target_path]):
                edits.append({
                    "op": "reject_dependency",
                    "link_id": link_id,
                    "source_step": source_step,
                    "source_path": source_path,
                    "target_step": target_step,
                    "target_path": target_path,
                })
        elif kind in LIVE_RECORDING_AGENT_OPS:
            edits.append({**op, "actor": "agent"})
    return edits


def _auto_fix_target_capability_name(spec: FlowSpec) -> str:
    caps = list(spec.capabilities or [])
    for kind in ("submit_batch", "submit", "query_status", "list_options", "validate_batch"):
        cap = next((c for c in caps if c.kind == kind and c.name), None)
        if cap is not None:
            return cap.name
    return caps[0].name if caps else "submit_batch"


def _auto_fix_target_capability_for_request(spec: FlowSpec, item: dict[str, Any]) -> str:
    """Choose the capability that should own a newly promoted captured request."""
    caps = list(spec.capabilities or [])
    if not caps:
        return "submit_batch"
    role = str(item.get("role") or "")
    method = str(item.get("method") or "").upper()
    seq = _entry_sequence(item)

    def cap_score(cap: FlowCapability) -> float:
        score = 0.0
        if cap.kind in {"submit_batch", "submit"}:
            if role in {"submit_anchor", "business_write"} or method in _WRITE_METHODS:
                score += 90
            elif role in {"business_get", "read_context"}:
                score += 45
            elif role == "read_option":
                score += 20
        elif cap.kind == "query_status":
            if role in {"business_get", "read_context"} and method not in _WRITE_METHODS:
                score += 75
        elif cap.kind == "list_options":
            if role == "read_option":
                score += 85
        elif cap.kind == "validate_batch":
            if role in {"business_get", "read_context"}:
                score += 55

        start, end = _capability_sequence_window(spec, cap)
        if seq is not None and start is not None and end is not None:
            if start <= seq <= end:
                score += 35
            elif seq < start:
                distance = start - seq
                score += max(0, 24 - min(distance, 24))
            else:
                distance = seq - end
                score += max(0, 16 - min(distance, 16))
        if cap.confirmed:
            score += 3
        score += float(cap.confidence or 0)
        return score

    best = max(caps, key=cap_score)
    if best.name:
        return best.name
    return _auto_fix_target_capability_name(spec)


def _deterministic_capability_repair_edits(spec: FlowSpec, report: dict[str, Any]) -> list[dict[str, Any]]:
    """P2 能力级确定性修复。

    这层只补“结构必需但可确定”的编排内容，语义判断仍交给 Pi/人工：
    - submit_batch 缺 foreach 时补 input.entries 循环；
    - 批量写接口必填字段缺 map 时补 item.<key> -> step.path；
    - 缺 output_mapping 时补最后一个 call 的 response。
    """
    edits: list[dict[str, Any]] = []
    step_by_id = {s.step_id: s for s in spec.steps}
    for cap in spec.capabilities or []:
        if not cap.name or (cap.confirmed and cap.locked):
            continue
        cap_step_ids = _capability_node_step_ids(cap)
        cap_steps = [step_by_id[sid] for sid in cap_step_ids if sid in step_by_id]
        if not cap_steps:
            continue
        flat_nodes = _iter_capability_nodes(cap.nodes or [])
        has_foreach = any(n.get("type") == "foreach" for n in flat_nodes if isinstance(n, dict))
        is_batch = _capability_is_batch(spec, cap)
        if is_batch and not has_foreach:
            edits.append({"op": "set_loop_source", "capability_name": cap.name, "items": "input.entries"})

        existing_map_targets = {
            str(n.get("target") or "")
            for n in flat_nodes
            if isinstance(n, dict) and n.get("type") == "map"
        }
        if is_batch:
            for st in cap_steps:
                if (st.method or "").upper() not in _WRITE_METHODS and not _looks_batch_step(st):
                    continue
                for param in st.params or []:
                    if not param.required:
                        continue
                    target = f"{st.step_id}.{param.path}"
                    if target in existing_map_targets:
                        continue
                    key = param.key or _strip_body_prefix(param.path).split(".")[-1].strip("[]") or "value"
                    if param.category == "runtime_var" and param.source_kind == "previous_response":
                        continue
                    edits.append({
                        "op": "set_map",
                        "capability_name": cap.name,
                        "node": {
                            "id": f"map_{re.sub(r'[^a-zA-Z0-9_]+', '_', key).strip('_') or 'field'}",
                            "source": f"item.{key}",
                            "target": target,
                        },
                    })
                    existing_map_targets.add(target)

        if not cap.output_mapping:
            final = next((st for st in reversed(cap_steps) if (st.method or "").upper() in _WRITE_METHODS), cap_steps[-1])
            edits.append({
                "op": "set_output_mapping",
                "capability_name": cap.name,
                "mapping": [{
                    "kind": "final_response",
                    "step_id": final.step_id,
                    "response_path": "response",
                    "name": "result",
                }],
            })
    return edits


async def auto_fix_flow_spec(
    spec: FlowSpec,
    *,
    repair_ops: list[dict[str, Any]],
    max_rounds: int = 3,
    expand_requests: bool = True,
    allow_scope_changes: bool | None = None,
) -> FlowSpec:
    """Apply Pi-submitted repair operations through deterministic gates."""
    if not isinstance(repair_ops, list) or any(not isinstance(op, dict) for op in repair_ops):
        raise ValueError("recording repair ops must be a list of objects")
    _validate_recording_agent_ops(repair_ops)
    current = spec.model_copy(deep=True)
    if allow_scope_changes is None:
        allow_scope_changes = expand_requests
    _normalize_capability_references(current)
    from dano.onboarding.recording_verify import assign_unassigned_internal_steps

    current = assign_unassigned_internal_steps(current)
    history: list[dict[str, Any]] = []
    for round_idx in range(max_rounds):
        report = validate_flow_spec(current)
        edits: list[dict[str, Any]] = []
        preflight_rejected_edits: list[dict[str, Any]] = []
        cap_report = report.get("capability_validation") or {}
        edits.extend(_deterministic_capability_repair_edits(current, report))
        for item in (cap_report.get("unused_high_confidence_requests") or []) if expand_requests else []:
            role = item.get("role") or ""
            if role not in {"submit_anchor", "business_write", "business_get", "read_context", "read_option"}:
                continue
            if not current.capabilities and not current.steps:
                edits.append({
                    "op": "add_request_step",
                    "request_id": item.get("request_id") or "",
                    "request_index": item.get("request_index"),
                })
                continue
            if not current.capabilities:
                continue
            edits.append({
                "op": "add_capability_step",
                "capability_name": _auto_fix_target_capability_for_request(current, item),
                "request_id": item.get("request_id") or "",
                "request_index": item.get("request_index"),
            })
        if round_idx == 0 and repair_ops:
            agent_edits: list[dict[str, Any]] = []
            for repair_op in repair_ops:
                translated = _autofix_ops_to_edits(
                    current,
                    [repair_op],
                    allow_scope_changes=bool(allow_scope_changes),
                )
                if translated:
                    agent_edits.extend(translated)
                    continue
                kind = str(repair_op.get("op") or "")
                step_id = str(repair_op.get("step_id") or repair_op.get("target_step") or "")
                path = str(repair_op.get("path") or repair_op.get("target_path") or "")
                error = (
                    f"param not found or unavailable: {step_id}:{path}"
                    if kind == "rename_field"
                    else "repair operation is not applicable to the current flow"
                )
                preflight_rejected_edits.append({
                    "op": kind,
                    "step_id": step_id,
                    "path": path,
                    "error": error,
                })
            if not allow_scope_changes:
                agent_edits = _planner_patch_edits(current, agent_edits)
            edits.extend(agent_edits)
        if not edits:
            history.append({
                "round": round_idx,
                "applied": 0,
                **({"rejected_edits": preflight_rejected_edits[:50]} if preflight_rejected_edits else {}),
                "remaining_errors": len(report.get("errors") or []),
            })
            break
        before = _flow_fingerprint(current)
        candidate = current.model_copy(deep=True)
        applied_edits: list[dict[str, Any]] = []
        rejected_edits: list[dict[str, Any]] = list(preflight_rejected_edits)
        # Pi 可能给出一个已经被前序编辑删除/改名的字段。单条坏 patch 不应让
        # 整个“自动修复”请求失败；按顺序应用，保留成功项并把拒绝原因回显。
        for edit in edits:
            try:
                candidate = apply_flow_edits(candidate, [{**edit, "actor": "repair"}])
                applied_edits.append(edit)
            except Exception as exc:  # noqa: BLE001
                rejected_edits.append({
                    "op": str(edit.get("op") or ""),
                    "step_id": str(edit.get("step_id") or ""),
                    "path": str(edit.get("param_path") or edit.get("path") or edit.get("target_path") or ""),
                    "error": str(exc)[:300],
                })
        if not applied_edits:
            history.append({
                "round": round_idx,
                "applied": 0,
                "changed": False,
                "rejected_edits": rejected_edits[:50],
                "remaining_errors": len(report.get("errors") or []),
            })
            break
        candidate.meta = {
            **(candidate.meta or {}),
            "auto_fix": {
                "round": round_idx + 1,
                "last_edits": applied_edits[:50],
                "rejected_edits": rejected_edits[:50],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        }
        candidate = _sync_capability_io_schemas(candidate)
        if allow_scope_changes:
            # Explicit repair may promote recorded requests and needs another
            # round to finish their fields/dependencies. Preserve that existing
            # workflow; the strict semantic plan/optimization path below never
            # enables scope expansion.
            accepted, gate = True, {
                "accepted": True,
                "reasons": [],
                "scope_expansion_round": True,
            }
        else:
            accepted, gate = _semantic_candidate_gate(current, candidate)
        if not accepted:
            history.append({
                "round": round_idx,
                "applied": 0,
                "changed": False,
                "proposal_rejected": True,
                "proposal_gate": gate,
                **({"rejected_edits": rejected_edits[:50]} if rejected_edits else {}),
            })
            break
        current = candidate
        after = _flow_fingerprint(current)
        history.append({
            "round": round_idx,
            "applied": len(applied_edits),
            "changed": before != after,
            **({"rejected_edits": rejected_edits[:50]} if rejected_edits else {}),
        })
        if before == after:
            break
        if validate_flow_spec(current).get("passed"):
            break
    current.meta = {**(current.meta or {}), "auto_fix_history": history}
    current = _repair_generated_capability_contracts(current)
    current = _sync_capability_io_schemas(current)
    return append_flow_version(refresh_review_items(current), "auto_fix", reason="一键自动修正")


def _auto_confirm_ready_capabilities(
    spec: FlowSpec,
    *,
    refresh_machine_owned: bool = False,
) -> FlowSpec:
    """置信度超过 70% 的能力默认采纳，低置信能力仍可人工采纳。"""
    _normalize_capability_references(spec)
    verification_complete = bool(((spec.meta or {}).get("verification_run") or {}).get("complete"))
    for cap in spec.capabilities or []:
        if cap.confirmed:
            # Planner confirmation is automatic. Verification may append a
            # verified readback/fact_check to the same executable contract, so
            # refresh that machine-owned fingerprint after verification. User
            # confirmations remain immutable and still detect later changes.
            if (
                (verification_complete or refresh_machine_owned)
                and not cap.locked
                and cap.updated_by in {"planner", "repair", "agent", "system"}
            ):
                cap.confirmation_hash = _capability_confirmation_hash(spec, cap)
            continue
        if not (verification_complete or refresh_machine_owned) and float(cap.confidence or 0) <= 0.7:
            continue
        cap.confirmed = True
        cap.requires_human_confirm = False
        cap.status = "confirmed"
        cap.updated_by = "planner"
        cap.confirmation_hash = _capability_confirmation_hash(spec, cap)
    return spec


_PENDING_FLOW_SPEC_HELPERS = ('_apply_mechanical_field_contracts', '_attach_option_source_memberships', '_canonicalize_public_capability_identities', '_capability_confirmation_hash', '_capability_execution_contract', '_capability_is_batch', '_capability_node_step_ids', '_capability_response_path_exists', '_capability_sequence_window', '_capability_step_was_removed', '_client_redact_sensitive', '_iter_capability_nodes', '_looks_batch_step', '_normalize_capability_references', '_normalize_generated_capability_semantics', '_prune_empty_capabilities', '_query_output_mappings', '_removed_capability_names', '_sanitize_capability_nodes', '_semantic_candidate_gate', '_strip_body_prefix', '_sync_capability_io_schemas', 'append_flow_version', 'apply_flow_edits', 'refresh_review_items', 'validate_flow_spec',)


def _bind_flow_spec_helpers() -> None:
    import sys
    _flow_spec = sys.modules.get("dano.execution.page.flow_spec")
    if _flow_spec is None or not hasattr(_flow_spec, "to_flow_spec"):
        return
    module_globals = globals()
    for name in _PENDING_FLOW_SPEC_HELPERS:
        if hasattr(_flow_spec, name):
            module_globals[name] = getattr(_flow_spec, name)
