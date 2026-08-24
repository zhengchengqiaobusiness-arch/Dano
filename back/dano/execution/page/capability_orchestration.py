"""Stage 6: capability list merge, prune, and orchestration."""
from __future__ import annotations

from typing import Any
import copy
from datetime import datetime, timezone
import re
from dano.execution.page.flow_spec_core.models import (
    CapabilityField,
    CapabilityRelation,
    CapabilityRequestRef,
    FlowCapability,
    FlowSpec,
    RequestAnalysis,
    RequestUsage,
)
from dano.execution.page.request_capture import (
    looks_like_auth_write,
    normalized_leaf_paths,
)
from dano.execution.page.flow_materialization.field_contracts.caller_ownership import (
    _param_exposed_to_caller,
    _param_requires_caller_input,
)
from dano.execution.page.flow_materialization.field_contracts.option_repair import (
    _repair_structural_option_bindings,
)
from dano.execution.page.recording_facts import (
    _request_fact_items,
    _request_path,
)
from dano.execution.page.recording_agent_contract import (
    _validate_recording_agent_ops,
)
from dano.execution.page.flow_materialization.links import (
    rebuild_flow_dependencies,
)
from dano.execution.page.capability_kinds import (
    ALLOWED_CAPABILITY_KINDS,
)
from dano.execution.page.capability_refs import _capability_public_input_step_ids


def sync_capability_scoped_views(spec: FlowSpec) -> FlowSpec:
    """从旧 steps/links/step_ids 派生能力内字段/依赖视图。"""
    if not spec.capabilities:
        return spec
    # Field materialization can discover an API-backed chooser after the
    # semantic capability plan was compiled (notably DOM-only query filters).
    # Keep capability membership in lock-step with that executable source so
    # scoped validation/export retains the source request fact.
    _attach_option_source_memberships(spec)
    by_step = {s.step_id: s for s in spec.steps}
    used_by_request: dict[str, list[str]] = {}
    materialized_by_request: dict[str, str] = {}
    memberships_by_request: dict[str, list[dict[str, Any]]] = {}
    for cap in spec.capabilities:
        previous_step_ids = _capability_scoped_step_ids(cap)
        cap_step_ids = [
            sid for sid in previous_step_ids
            if sid in by_step and _capability_step_allowed(spec, cap, by_step[sid])
        ]
        # ``nodes`` are the executable plan.  Filtering only ``step_ids`` left
        # stale call nodes executable and validation still treated their fields
        # as capability inputs.  Remove every generated call rejected by the
        # scoped membership policy from the node tree as well.
        for removed_step_id in set(previous_step_ids) - set(cap_step_ids):
            cap.nodes = _remove_capability_step_nodes(cap.nodes or [], removed_step_id)
        _sync_capability_order(spec, cap)
        cap_step_ids = list(cap.step_ids)
        step_objs = [by_step[sid] for sid in cap_step_ids]
        # ``_sync_capability_order`` has already rebuilt these memberships and
        # resolved the capability-local public anchor. Rebuilding once more
        # from the stale pre-sync refs would downgrade a shared query anchor
        # back to the step-global ``control_preflight_for_write`` usage.
        cap_name = cap.name or cap.capability_id
        for ref in cap.request_refs:
            if ref.request_id and cap_name:
                used_by_request.setdefault(ref.request_id, [])
                if cap_name not in used_by_request[ref.request_id]:
                    used_by_request[ref.request_id].append(cap_name)
                if ref.step_id:
                    materialized_by_request[ref.request_id] = ref.step_id
                memberships_by_request.setdefault(ref.request_id, []).append({
                    "capability": cap_name,
                    "step_id": ref.step_id,
                    "usage": ref.usage,
                    "origin": ref.origin,
                    "confirmed": ref.confirmed,
                })
        inputs: dict[str, CapabilityField] = {}
        request_fields: list[CapabilityField] = []
        internal_fields: list[CapabilityField] = []
        capability_computed_fields = [
            item.model_copy(deep=True)
            for item in (cap.computed_fields or [])
            if not item.step_id
        ]
        previous_inputs = list(cap.inputs or [])
        old_dependencies = list(cap.dependencies or [])
        request_id_by_step = {ref.step_id: ref.request_id for ref in cap.request_refs}
        public_input_step_ids = set(_capability_public_input_step_ids(cap, by_step))
        for st in step_objs:
            request_id = request_id_by_step.get(st.step_id, "")
            for param in st.params:
                request_fields.append(_capability_field_from_param(st, param, scope="request_field", request_id=request_id))
                if (
                    st.step_id in public_input_step_ids
                    and _param_exposed_to_caller(param, set(cap_step_ids))
                ):
                    key = param.key or param.label or param.path
                    field = _capability_field_from_param(
                        st, param, scope="input", request_id=request_id,
                    )
                    field.required = _param_requires_caller_input(
                        param, set(cap_step_ids),
                    )
                    inputs.setdefault(key, field)
                else:
                    internal_fields.append(_capability_field_from_param(st, param, scope="internal", request_id=request_id))
        # steps/params 是请求字段的唯一真相；能力自身的聚合输入（例如批量 entries）
        # 可以独立存在。任何绑定到 step_id 的能力字段都是派生镜像，不能回写或
        # 覆盖 ParamField，即使旧镜像曾被 locked/confirmed。
        if _capability_is_batch(spec, cap):
            cap.inputs = _capability_inputs_from_top_level_schema(
                cap.input_schema, previous_inputs,
            )
            nested_item_names = set(
                (((cap.input_schema or {}).get("properties") or {}).get("entries") or {}).get("items", {}).get("properties", {})
            )
            for field in request_fields:
                if field.step_id and field.key in nested_item_names:
                    field.exposed_to_caller = False
        else:
            cap.inputs = list(inputs.values())
            existing_names = {field.key or field.path for field in cap.inputs}
            for field in _capability_inputs_from_top_level_schema(
                cap.input_schema, previous_inputs,
            ):
                raw_schema = ((cap.input_schema or {}).get("properties") or {}).get(field.key or field.path)
                if (
                    isinstance(raw_schema, dict)
                    and raw_schema.get("x-dano-capability-owned") is True
                    and (field.key or field.path) not in existing_names
                ):
                    cap.inputs.append(field)
        cap.request_fields = request_fields
        cap.internal_fields = internal_fields
        cap.computed_fields = capability_computed_fields
        derived_dependencies = [
            _capability_dependency_from_link(link)
            for link in spec.links
            if link.source_step_id in cap_step_ids and link.target_step_id in cap_step_ids
        ]
        valid_old_dependencies = [
            item for item in old_dependencies
            if str((item.target or {}).get("step_id") or "") in cap_step_ids
            and _capability_step_param_exists(
                by_step.get(str((item.target or {}).get("step_id") or "")),
                str((item.target or {}).get("path") or ""),
            )
            and (
                bool(str((item.source or {}).get("request_id") or ""))
                or (
                    str((item.source or {}).get("step_id") or "") in cap_step_ids
                    and _capability_response_path_exists(
                        by_step.get(str((item.source or {}).get("step_id") or "")),
                        str((item.source or {}).get("path") or ""),
                    )
                )
            )
        ]
        cap.dependencies = _merge_capability_scoped_dependencies(
            derived_dependencies, valid_old_dependencies,
        )
        derived_outputs = _capability_output_fields(cap)
        cap.outputs = derived_outputs
    for fact in spec.request_facts.requests or []:
        request_id = fact.request_id or ""
        if not request_id:
            continue
        usage = spec.request_facts.usage.get(request_id) or RequestUsage(request_id=request_id)
        usage.used_by_capabilities = list(used_by_request.get(request_id) or [])
        usage.capability_memberships = list(memberships_by_request.get(request_id) or [])
        if materialized_by_request.get(request_id):
            usage.materialized_step_id = materialized_by_request[request_id]
            usage.state = "materialized"
        elif usage.materialized_step_id and any(s.step_id == usage.materialized_step_id for s in spec.steps):
            usage.state = "materialized"
        else:
            usage.materialized_step_id = ""
            usage.state = "captured"
        spec.request_facts.usage[request_id] = usage
    spec.meta = {
        **(spec.meta or {}),
        "capability_scoped_view": {
            "status": "derived",
            "source": "steps+links+request_facts",
            "capability_count": len(spec.capabilities),
        },
    }
    return spec


def _generated_capability_is_protected(capability: FlowCapability) -> bool:
    return bool(
        capability.locked
        or capability.updated_by == "user"
        or any(ref.origin in {"manual", "user"} for ref in capability.request_refs or [])
    )


def _collapse_duplicate_generated_capabilities(spec: FlowSpec) -> None:
    kept: list[FlowCapability] = []
    signature_index: dict[tuple[str, tuple[str, ...]], int] = {}
    for capability in spec.capabilities or []:
        signature = (
            _capability_kind_family(capability.kind),
            tuple(_capability_node_step_ids(capability)),
        )
        if not signature[1]:
            kept.append(capability)
            continue
        existing_index = signature_index.get(signature)
        if existing_index is None:
            signature_index[signature] = len(kept)
            kept.append(capability)
            continue
        existing = kept[existing_index]
        existing_protected = _generated_capability_is_protected(existing)
        incoming_protected = _generated_capability_is_protected(capability)
        if existing_protected and incoming_protected:
            kept.append(capability)
            continue
        if incoming_protected:
            kept[existing_index] = capability
            continue
        if existing_protected:
            continue
        if float(capability.confidence or 0.0) > float(existing.confidence or 0.0):
            kept[existing_index] = capability
    spec.capabilities = kept


def _canonicalize_public_capability_identities(spec: FlowSpec) -> FlowSpec:
    """Atomically align public names and every cross-capability reference."""
    public_names = set(ALLOWED_CAPABILITY_KINDS)
    renamed: dict[str, str] = {}
    for cap in spec.capabilities or []:
        old_name = str(cap.name or "")
        kind = str(cap.kind or "")
        stale_standard_alias = old_name in public_names and old_name != kind
        stale_generated_alias = bool(
            kind in public_names
            and re.fullmatch(r"(?:query_status|list_options|validate_batch|submit_batch|submit)\d*", old_name)
        )
        if kind in public_names and (stale_standard_alias or stale_generated_alias or not old_name):
            cap.name = kind
            if old_name and old_name != kind:
                renamed[old_name] = kind
    if not renamed:
        return spec
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
    if isinstance(spec.goal, dict):
        spec.goal["capabilities"] = list(dict.fromkeys(
            renamed.get(str(name), str(name)) for name in (spec.goal.get("capabilities") or []) if str(name)
        ))
    return spec


def _orchestration_context(
    spec: FlowSpec,
    *,
    validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request_facts = _request_fact_items(spec)
    validation_findings: dict[str, Any] = {}
    try:
        current_validation = validation if validation is not None else validate_flow_spec(spec)
        cap_validation = current_validation.get("capability_validation") or {}
        validation_findings = {
            "errors": list(current_validation.get("errors") or [])[:40],
            "warnings": list(current_validation.get("warnings") or [])[:40],
            "unused_high_confidence_requests": list(cap_validation.get("unused_high_confidence_requests") or [])[:80],
            "capability_internal": cap_validation.get("capability_internal") or {},
            "capability_relations": cap_validation.get("capability_relations") or {},
            "skill_level": cap_validation.get("skill_level") or {},
        }
    except Exception as exc:  # noqa: BLE001
        validation_findings = {"error": str(exc)[:240]}
    return {
        "title": spec.title,
        "business_description": spec.business_description,
        "validation_findings": validation_findings,
        "removed_capabilities": list((spec.meta or {}).get("removed_capabilities") or []),
        "removed_capability_steps": dict((spec.meta or {}).get("capability_removed_steps") or {}),
        "existing_capabilities": [
            {
                "name": cap.name,
                "title": cap.title,
                "intent": cap.intent,
                "kind": cap.kind,
                "step_ids": list(cap.step_ids or []),
                "nodes": list(cap.nodes or []),
                "request_refs": [
                    ref.model_dump(exclude_none=True)
                    for ref in (cap.request_refs or [])
                ],
                "input_schema": cap.input_schema or {},
                "output_schema": cap.output_schema or {},
                "output_mapping": list(cap.output_mapping or []),
                "fields": [
                    _capability_field_summary(field)
                    for field in [

                        *(cap.inputs or []),
                        *(cap.request_fields or []),
                        *(cap.internal_fields or []),
                        *(cap.computed_fields or []),
                        *(cap.outputs or []),
                    ]
                ][:80],
                "dependencies": [dep.model_dump(exclude_none=True) for dep in (cap.dependencies or [])[:80]],
                "confirmed": cap.confirmed,
                "requires_human_confirm": cap.requires_human_confirm,
            }
            for cap in spec.capabilities
        ],
        # Complete compact indexes guarantee that every recorded field and
        # response path participates in planning. Detailed samples below remain
        # bounded so a single huge response cannot exhaust the model context.
        "complete_field_index": {
            st.step_id: [
                {
                    "path": p.path,
                    "key": p.key,
                    "type": p.type,
                    "category": p.category,
                    "source_kind": p.source_kind,
                    "required": bool(p.required),
                }
                for p in (st.params or [])
            ]
            for st in spec.steps
        },
        "complete_response_path_index": {
            st.step_id: normalized_leaf_paths(st.response_json)
            if st.response_json is not None else []
            for st in spec.steps
        },
        "steps": [
            {
                "step_id": st.step_id,
                "name": st.name,
                "method": st.method,
                "path": st.path or st.url,
                "role": (st.source_meta or {}).get("role") or st.semantic_role,
                "param_count": len(st.params or []),
                "params": [
                    {
                        "path": p.path,
                        "key": p.key,
                        "type": p.type,
                        "source_kind": p.source_kind,
                    }
                    for p in (st.params or [])[:80]
                ],
                "response_paths": normalized_leaf_paths(st.response_json, max_paths=80),
            }
            for st in spec.steps
        ],
        "links": [lk.model_dump() for lk in spec.links],
        "captured_requests": [
            {
                "request_index": r.get("request_index"),
                "method": r.get("method"),
                "path": r.get("path") or r.get("url"),
                "role": r.get("role"),
                "confidence": r.get("confidence"),
                "reason": r.get("reason"),
            }
            for r in request_facts[:120]
        ],
        "captured_request_count": len(request_facts),
    }


def _merge_capability_lists_impl(
    existing: list[FlowCapability],
    generated: list[FlowCapability],
    *,
    spec: FlowSpec | None,
    allow_new: bool,
    removed_capabilities: set[str],
    removed_families: set[str],
) -> list[FlowCapability]:
    if not existing:
        return [
            cap for cap in generated
            if cap.name not in removed_capabilities
            and _capability_kind_family(cap.kind) not in removed_families
        ]
    out = [cap.model_copy(deep=True) for cap in existing]
    by_name = {cap.name: cap for cap in out if cap.name}
    generated_family_counts: dict[str, int] = {}
    for candidate in generated:
        family = _capability_kind_family(candidate.kind)
        generated_family_counts[family] = generated_family_counts.get(family, 0) + 1
    for cap in generated:
        if cap.name in removed_capabilities or _capability_kind_family(cap.kind) in removed_families:
            continue
        cur = by_name.get(cap.name)
        if cur is None:
            empty_same_family = [
                item for item in out
                if not _capability_node_step_ids(item)
                and _capability_kind_family(item.kind) == _capability_kind_family(cap.kind)
            ]
            if len(empty_same_family) == 1:
                cur = empty_same_family[0]
        if cur is None:
            family = _capability_kind_family(cap.kind)
            same_family = [
                item for item in out
                if _capability_kind_family(item.kind) == family
            ]
            generated_ids = set(_capability_node_step_ids(cap))
            overlapping = [
                item for item in same_family
                if generated_ids & set(_capability_node_step_ids(item))
            ]
            if overlapping:
                cur = max(
                    overlapping,
                    key=lambda item: len(generated_ids & set(_capability_node_step_ids(item))),
                )
            elif len(same_family) == 1 and generated_family_counts.get(family) == 1:
                # A user-renamed or legacy capability often has a nonstandard
                # name (for example capability_2).  Match the only same-family
                # draft so deterministic re-analysis can repair missing
                # interface membership without creating a duplicate ability.
                cur = same_family[0]
        if cur is None:
            if not allow_new:
                continue
            out.append(cap)
            if cap.name:
                by_name[cap.name] = cap
            continue
        existing_node_keys = {
            (n.get("type"), n.get("step_id"), n.get("id"))
            for n in (cur.nodes or [])
            if isinstance(n, dict)
        }
        for node in cap.nodes or []:
            if not isinstance(node, dict):
                continue
            sid = str(node.get("step_id") or "")
            if sid and _capability_step_was_removed(spec, cur.name, sid):
                continue
            key = (node.get("type"), node.get("step_id"), node.get("id"))
            if key not in existing_node_keys:
                cur.nodes.append(dict(node))
                existing_node_keys.add(key)
        if not cur.input_schema:
            cur.input_schema = cap.input_schema
        if not cur.output_schema:
            cur.output_schema = cap.output_schema
        if not cur.output_mapping:
            cur.output_mapping = cap.output_mapping
        if not cur.preconditions:
            cur.preconditions = cap.preconditions
        if not cur.evidence:
            cur.evidence = cap.evidence
        if not cur.caller_responsibilities:
            cur.caller_responsibilities = cap.caller_responsibilities
        if not cur.skill_responsibilities:
            cur.skill_responsibilities = cap.skill_responsibilities
        cur.confidence = max(float(cur.confidence or 0), float(cap.confidence or 0))
        if not cur.status or cur.status == "draft":
            cur.status = cap.status or "draft"
    return out


def _prune_auth_materializations(spec: FlowSpec) -> None:
    """Repair plans produced before compound auth paths were recognized."""
    auth_step_ids = {
        step.step_id
        for step in spec.steps
        if looks_like_auth_write(step.url or step.path)
    }
    if auth_step_ids:
        spec.steps = [
            step for step in spec.steps if step.step_id not in auth_step_ids
        ]
        spec.links = [
            link for link in spec.links
            if link.source_step_id not in auth_step_ids
            and link.target_step_id not in auth_step_ids
        ]
        _normalize_capability_references(spec)
        spec.meta = {
            **(spec.meta or {}),
            "pruned_auth_step_count": (
                int((spec.meta or {}).get("pruned_auth_step_count") or 0)
                + len(auth_step_ids)
            ),
        }
    for fact in spec.request_facts.requests or []:
        request_id = str(fact.request_id or "")
        if not request_id or not looks_like_auth_write(
            fact.url or fact.path, fact.post_data
        ):
            continue
        analysis = spec.request_facts.analysis.get(request_id) or RequestAnalysis(
            request_id=request_id
        )
        analysis.role = "auth"
        analysis.keep = False
        analysis.reason = "登录/鉴权/令牌刷新请求，只作为身份来源，不进入业务流程"
        analysis.confidence = max(float(analysis.confidence or 0), 0.96)
        spec.request_facts.analysis[request_id] = analysis
        usage = spec.request_facts.usage.get(request_id) or RequestUsage(
            request_id=request_id
        )
        if usage.materialized_step_id in auth_step_ids:
            usage.materialized_step_id = ""
            usage.state = "captured"
            usage.used_by_capabilities = []
            usage.capability_memberships = []
        spec.request_facts.usage[request_id] = usage


def _sync_capability_order(spec: FlowSpec, cap: FlowCapability) -> None:
    """Refresh derived membership views from the executable node plan."""
    by_id = {step.step_id: step for step in spec.steps}
    legacy_refs = list(cap.request_refs or [])
    # Option-source calls belong to the capability's supporting evidence and
    # may be materialized as call nodes so a client can populate choices.  They
    # are not members of the business operation itself.  Treating every call
    # node as executable membership silently rewrote exact option refs to
    # ``preflight`` and exposed their fields as capability inputs.
    cap.step_ids = [
        str(node.get("step_id") or "")
        for node in _iter_capability_nodes(cap.nodes or [])
        if (
            str(node.get("step_id") or "") in by_id
            and str(node.get("usage") or "") != "option_source"
        )
    ]
    cap.step_ids = list(dict.fromkeys(cap.step_ids))
    existing_memberships = {
        ref.step_id: ref for ref in (cap.request_refs or [])
        if ref.usage in {"execute", "preflight"} and ref.step_id
    }
    call_step_ids = set(cap.step_ids)

    option_source_step_ids: set[str] = set()
    option_source_request_ids: set[str] = set()
    option_source_paths: set[str] = set()

    def remember_option_source(source: dict[str, Any]) -> None:
        if not isinstance(source, dict):
            return
        source_step_id = str(source.get("source_step_id") or "")
        source_request_id = str(
            source.get("source_request_id") or source.get("request_id") or ""
        )
        source_path = _request_path({"url": str(source.get("source_url") or "")})
        if source_step_id:
            option_source_step_ids.add(source_step_id)
        if source_request_id:
            option_source_request_ids.add(source_request_id)
        if source_path:
            option_source_paths.add(source_path)

    for step_id in call_step_ids:
        step = by_id.get(step_id)
        if step is None:
            continue
        for binding in step.selects or []:
            remember_option_source({
                "source_request_id": binding.source_request_id,
                "source_url": binding.source_url,
            })
        for param in step.params or []:
            source = param.source or {}
            if param.source_kind == "api_option":
                remember_option_source(source)
            remember_option_source(source.get("option_source") or {})
    for link in spec.links or []:
        if link.target_step_id in call_step_ids:
            remember_option_source((link.value_binding or {}).get("option_source") or {})

    def keep_auxiliary_ref(ref: CapabilityRequestRef) -> bool:
        if ref.usage != "option_source" or ref.origin in {"manual", "user"}:
            return True
        # A confirmed compiler reference is the exact request identity from
        # the saved semantic plan.  It must survive until field-contract repair
        # can bind the corresponding target field; requiring a pre-existing
        # binding here creates a circular dependency and loses the evidence.
        if ref.origin == "compiler" and ref.confirmed:
            return True
        return bool(
            (ref.step_id and ref.step_id in option_source_step_ids)
            or (ref.request_id and ref.request_id in option_source_request_ids)
            or (
                _request_path({"url": ref.path})
                and _request_path({"url": ref.path}) in option_source_paths
            )
        )

    auxiliary_refs = [
        ref for ref in (cap.request_refs or [])
        if (
            (
                ref.usage not in {"execute", "preflight"}
                or not ref.step_id
                # Explicit planner/manual preflight facts need not be executable
                # call nodes. Preserve those references while normalizing the one
                # public execute anchor among actual call nodes.
                or (ref.usage == "preflight" and ref.step_id not in call_step_ids)
            )
            and keep_auxiliary_ref(ref)
        )
    ]
    existing_execute_ids = [
        ref.step_id for ref in legacy_refs
        if ref.usage == "execute" and ref.step_id in call_step_ids
    ]
    evidence_anchor_ids = [
        str(item.get("anchor_step_id") or "")
        for item in (cap.evidence or [])
        if isinstance(item, dict)
        and str(item.get("anchor_step_id") or "") in call_step_ids
    ]
    return_anchor_ids = [
        str(node.get("from") or node.get("source") or "")
        for node in _iter_capability_nodes(cap.nodes or [])
        if isinstance(node, dict)
        and node.get("type") == "return"
        and str(node.get("from") or node.get("source") or "") in call_step_ids
    ]
    anchor_candidates = list(dict.fromkeys(
        existing_execute_ids or evidence_anchor_ids or return_anchor_ids
    ))
    if not anchor_candidates and len(cap.step_ids) == 1:
        anchor_candidates = list(cap.step_ids)
    anchor_step_id = anchor_candidates[0] if len(anchor_candidates) == 1 else ""
    execute_refs: list[CapabilityRequestRef] = []
    for step_id in cap.step_ids:
        ref = _capability_request_ref_from_step(
            spec, by_id[step_id], existing_memberships.get(step_id),
        )
        if anchor_step_id and not cap.locked:
            ref.usage = "execute" if step_id == anchor_step_id else "preflight"
        legacy_ref = next((item for item in legacy_refs if item.step_id == step_id), None)
        if (
            legacy_ref is not None
            and legacy_ref.usage == "preflight"
            and legacy_ref.origin in {"manual", "user"}
        ):
            ref.usage = "preflight"
            ref.origin = legacy_ref.origin
            ref.confirmed = legacy_ref.confirmed
        execute_refs.append(ref)
    cap.request_refs = _ordered_capability_request_refs(execute_refs + auxiliary_refs)


def _prune_empty_capabilities(spec: FlowSpec) -> FlowSpec:
    """能力必须拥有至少一个真实接口调用；枚举字段不能伪装成空业务能力。"""
    step_ids = {step.step_id for step in spec.steps}
    kept: list[FlowCapability] = []
    removed_refs: set[str] = set()
    for cap in spec.capabilities or []:
        actual = [sid for sid in _capability_node_step_ids(cap) if sid in step_ids]
        if actual:
            kept.append(cap)
            continue
        removed_refs.update({str(cap.name or ""), str(cap.capability_id or "")})
    spec.capabilities = kept
    if removed_refs:
        spec.capability_relations = [
            relation for relation in (spec.capability_relations or [])
            if str(relation.from_capability or "") not in removed_refs
            and str(relation.to_capability or "") not in removed_refs
        ]
    return spec


async def orchestrate_flow_capabilities(
    spec: FlowSpec,
    *,
    submission: dict[str, Any],
    generation_mode: str | None = None,
) -> FlowSpec:
    """Apply one structured plan submitted by the Pi recording agent.

    This is deliberately model-free. Pi owns the AgentSession and produces the
    submission; this function only compiles whitelisted operations and applies
    deterministic fact/schema/quality gates.

    Public capability boundaries have exactly one machine-owned producer:
    strict semantic plan -> verified request graph compiler.  Recorder
    heuristics remain facts/candidates and never become a publishable fallback.
    Operator-owned capabilities are preserved by the compiler.
    """
    if not isinstance(submission, dict):
        raise ValueError("recording plan submission must be an object")
    if not isinstance(submission.get("ops", []), list):
        raise ValueError("recording plan ops must be a list")
    _validate_recording_agent_ops(submission.get("ops") or [])
    original = spec.model_copy(deep=True)
    _prune_auth_materializations(original)
    _mark_repeated_write_observations(original)
    # The baseline report is audit-only.  Projection validation preserves its
    # structural findings without rebuilding release-only capability contracts.
    initial_report = validate_flow_spec(original, _projection=True)
    current = _prune_empty_capabilities(original.model_copy(deep=True))
    rebuild_flow_dependencies(current)
    _repair_structural_option_bindings(current)
    capability_model = (current.meta or {}).get("capability_model") or {}
    auto_generated_existing = bool(
        current.capabilities
        and capability_model.get("source")
        and not any(
            cap.locked
            or cap.updated_by == "user"
            or any(ref.origin in {"manual", "user"} for ref in (cap.request_refs or []))
            for cap in current.capabilities
        )
    )
    if auto_generated_existing:
        # Machine-owned definitions are reproducible only from an accepted
        # strict plan. Drop stale deterministic/legacy output before deciding
        # whether a current plan can rebuild it.
        current.capabilities = []
        current.capability_relations = []
    had_existing = bool(current.capabilities)
    initial_generation = auto_generated_existing or generation_mode == "initial" or (generation_mode is None and not had_existing)
    # Optimization is a boundary re-analysis over already materialized steps.
    # It may repair capability membership, but request IDs outside FlowSpec
    # remain unavailable to both deterministic and model planners.
    capability_count_before = len(current.capabilities or [])
    # Do not manufacture a deterministic capability baseline here.  The
    # compiler below preserves explicit operator-owned definitions and replaces
    # every machine-owned definition from the strict plan in one pass.
    current = _prune_empty_capabilities(current)
    source = "strict_plan_pending"
    reason = ""
    semantic_plan: dict[str, Any] = {}
    semantic_coverage: dict[str, Any] = {}
    previous_model = (current.meta or {}).get("capability_model") or {}
    previous_semantic_plan = (
        previous_model.get("semantic_plan")
        if isinstance(previous_model.get("semantic_plan"), dict) else {}
    )
    incremental_review: dict[str, Any] = {}
    proposed_semantic_plan = (
        submission.get("semantic_plan") if isinstance(submission.get("semantic_plan"), dict)
        else (submission.get("plan") if isinstance(submission.get("plan"), dict) else {})
    )
    strict_semantic_submission = bool(
        isinstance(proposed_semantic_plan.get("capabilities"), list)
        and proposed_semantic_plan.get("capabilities")
        and all(
            isinstance(item, dict)
            and item.get("name")
            and item.get("kind")
            and item.get("anchor_step_id")
            and isinstance(item.get("request_refs"), list)
            and item.get("request_refs")
            for item in proposed_semantic_plan.get("capabilities") or []
        )
    )
    # Preserve the exact complete snapshot sent by the Skill.  Later public
    # anchor checks may reject entries, but they must never redefine the goal
    # from eight submitted abilities down to a self-consistent subset of six.
    submitted_semantic_plan = (
        copy.deepcopy(proposed_semantic_plan)
        if strict_semantic_submission
        else copy.deepcopy(previous_model.get("submitted_semantic_plan") or {})
    )
    # Recordings created before the strict anchor/request_refs contract persist
    # the same complete boundary decision as ``step_ids``.  Treat that stored
    # representation as a full replacement during optimize so an obsolete
    # planner-owned aggregate cannot survive beside its replacement abilities.
    previous_strict_plan = bool(
        isinstance(previous_semantic_plan.get("capabilities"), list)
        and previous_semantic_plan.get("capabilities")
        and all(
            isinstance(item, dict)
            and item.get("name")
            and item.get("kind")
            and item.get("anchor_step_id")
            and isinstance(item.get("request_refs"), list)
            and item.get("request_refs")
            for item in previous_semantic_plan.get("capabilities") or []
        )
    )
    fact_request_ids = {
        str(item.get("request_id") or "")
        for item in _request_fact_items(current)
        if str(item.get("request_id") or "")
    }
    # A strict Skill submission is a complete replacement snapshot. Carrying
    # forward an omitted machine-owned boundary made an 8-capability plan
    # materialize as 9 during live analysis. Operator-owned capabilities are
    # preserved separately by the compiler and do not need semantic-plan merge.
    effective_semantic_plan = (
        proposed_semantic_plan
        if strict_semantic_submission
        else previous_semantic_plan if previous_strict_plan else {}
    )
    if current.steps and strict_semantic_submission:
        # A frozen graph may receive later exact request IDs while earlier
        # capabilities already use stable step IDs. Promote those facts before
        # deciding whether the new public anchors are valid.
        _materialize_semantic_plan_request_refs(current, effective_semantic_plan)
    pre_materialization_candidate = bool(
        strict_semantic_submission
        and not current.steps
        and fact_request_ids
        and all(
            str(item.get("anchor_step_id") or "") in fact_request_ids
            and all(
                isinstance(ref, dict)
                and str(ref.get("step_id") or "") in fact_request_ids
                for ref in (item.get("request_refs") or [])
            )
            for item in (effective_semantic_plan.get("capabilities") or [])
            if isinstance(item, dict)
        )
    )
    pre_materialization_coverage = (
        _pre_materialization_semantic_plan_coverage(
            current, effective_semantic_plan, fact_request_ids,
        )
        if pre_materialization_candidate
        else {
            "complete": False,
            "missing": [],
            "covered_steps": 0,
            "total_steps": 0,
            "covered_fields": 0,
            "total_fields": 0,
            "phase": "request_facts",
        }
    )
    live_blocking_gaps = set(pre_materialization_coverage.get("missing") or []) & {
        "capability_contracts", "capabilities", "goal_capability_count",
    }
    pre_materialization_strict_plan = bool(
        pre_materialization_candidate and not live_blocking_gaps
    )
    ignored_non_public_capabilities: list[str] = []
    if strict_semantic_submission and not pre_materialization_strict_plan:
        public_capabilities = [
            item
            for item in (effective_semantic_plan.get("capabilities") or [])
            if isinstance(item, dict)
            and _planned_capability_has_public_anchor(
                current,
                str(item.get("kind") or ""),
                [str(item.get("anchor_step_id") or "")],
            )
        ]
        if public_capabilities:
            ignored_non_public_capabilities = [
                str(item.get("name") or item.get("title") or item.get("anchor_step_id") or "")
                for item in (effective_semantic_plan.get("capabilities") or [])
                if isinstance(item, dict) and item not in public_capabilities
            ]
            if ignored_non_public_capabilities:
                # Compile/validate the full submitted contract so omissions are
                # explicit retry reasons.  Never silently publish the subset.
                pass
    complete_semantic_submission = strict_semantic_submission
    preserved_human_relations: list[CapabilityRelation] = []
    if complete_semantic_submission:
        # A complete re-analysis owns the automatic relation set as well as
        # capability boundaries. Keep operator-confirmed relations, then
        # rebuild every planner suggestion from concrete endpoints below.
        preserved_human_relations = [
            relation.model_copy(deep=True)
            for relation in (original.capability_relations or [])
            if relation.confirmed
            or str((relation.evidence or {}).get("source") or "").lower()
            in {"manual", "user", "operator"}
        ]
        current.capability_relations = []
    if initial_generation or complete_semantic_submission:
        semantic_plan = effective_semantic_plan
        semantic_coverage = _semantic_plan_coverage(current, submission)
    else:
        # Ops-only submissions remain incremental and retain the last accepted
        # complete semantic blueprint.
        semantic_plan = previous_semantic_plan
        semantic_coverage = dict(previous_model.get("semantic_coverage") or {})
    if not initial_generation:
        incremental_review = {
            "reviewed_scope": submission.get("reviewed_scope") or {},
            "unresolved_items": (
                proposed_semantic_plan.get("unresolved_items")
                or submission.get("unresolved_items")
                or []
            ),
            "complete_semantic_submission": complete_semantic_submission,
        }
    # Field/source/required/enum edits are applied through the live operation
    # channel before this function.  The Pi plan is the single semantic producer
    # for capability membership; the compiler only materializes that plan and
    # supplements mechanically grounded dependencies.
    _normalize_capability_references(current)
    if initial_generation:
        current = _repair_generated_capability_contracts(
            current,
        )
    current = _ensure_external_transform_relations(
        _sync_capability_io_schemas(sync_flow_spec_models(current))
    )
    capability_compilation_audit: dict[str, Any] = {}
    capability_compilation_errors: list[str] = []
    partial_safe_compilation = False
    planned_capability_contracts = [
        item for item in (effective_semantic_plan.get("capabilities") or [])
        if isinstance(item, dict)
    ]
    strict_anchor_contract = bool(planned_capability_contracts) and all(
        item.get("name") and item.get("kind") and item.get("anchor_step_id")
        for item in planned_capability_contracts
    )
    if strict_anchor_contract and not pre_materialization_strict_plan and current.steps:
        from dano.execution.page.capability_compiler import compile_capabilities

        compilation = compile_capabilities(current, effective_semantic_plan)
        current = _ensure_external_transform_relations(
            _sync_capability_io_schemas(sync_flow_spec_models(compilation.spec))
        )
        capability_compilation_audit = dict(compilation.audit)
        capability_compilation_errors = list(compilation.errors)
        partial_safe_compilation = bool(
            compilation.capabilities and capability_compilation_errors
        )
        semantic_coverage = _semantic_plan_coverage(
            current,
            {"semantic_plan": effective_semantic_plan},
        )
        if not semantic_coverage.get("complete"):
            capability_compilation_errors.append(
                "strict semantic plan is incomplete: "
                + ", ".join(str(item) for item in semantic_coverage.get("missing") or [])
            )
        if compilation.capabilities:
            source = "verified_request_graph"
    if pre_materialization_strict_plan:
        # Live analysis runs before request facts are materialized into stable
        # FlowStep IDs. Accept and retain a fully fact-addressable strict plan,
        # but do not manufacture provisional capabilities. Finalize retargets
        # request IDs to canonical step IDs and invokes the same compiler once.
        proposal_accepted = True
        proposal_gate = {
            "accepted": True,
            "reasons": [],
            "producer": "verified_request_graph",
            "pending": "request_materialization",
        }
        source = "strict_plan_awaiting_materialization"
    elif strict_anchor_contract and not capability_compilation_errors and current.steps:
        proposal_accepted = True
        proposal_gate = {
            "accepted": True,
            "reasons": [],
            "producer": "verified_request_graph",
        }
    else:
        proposal_accepted = False
        pre_materialization_reasons = list(
            pre_materialization_coverage.get("missing") or []
        ) if pre_materialization_candidate else []
        proposal_gate = {
            "accepted": False,
            "reasons": (
                pre_materialization_reasons
                or (
                    ["capability_compilation_failed"]
                    if capability_compilation_errors
                    else ["strict_semantic_plan_required"]
                )
            ),
            "producer": "verified_request_graph",
        }
    if not proposal_accepted:
        # The compiler validates each public anchor independently. Keep its
        # safely compiled subset when another proposed boundary is malformed;
        # the incomplete generation state still forces Pi to correct the full
        # plan before automatic publishing. Rolling the whole candidate back
        # here made one bad helper/boundary erase unrelated valid abilities.
        if not partial_safe_compilation:
            # A rejected complete snapshot must not erase the last accepted
            # collection. The next Pi batch can retry from that authoritative
            # baseline while newly captured facts remain available on the spec.
            current = _prune_empty_capabilities(original.model_copy(deep=True))
        if previous_strict_plan:
            semantic_plan = previous_semantic_plan
            semantic_coverage = dict(previous_model.get("semantic_coverage") or {})
        source = (
            "strict_plan_partial"
            if partial_safe_compilation
            else "strict_plan_pending" if not strict_anchor_contract
            else "strict_plan_rejected"
        )
        reason = "自动语义 Proposal 未通过单调质量准入: " + ",".join(
            proposal_gate["reasons"]
        )
    if preserved_human_relations:
        valid_capability_refs = {
            ref
            for capability in (current.capabilities or [])
            for ref in (capability.name, capability.capability_id)
            if ref
        }
        for relation in preserved_human_relations:
            if (
                relation.from_capability in valid_capability_refs
                and relation.to_capability in valid_capability_refs
            ):
                _upsert_capability_relation(
                    current, relation.model_dump(exclude_none=True),
                )
    current = _apply_semantic_business_understanding(current, semantic_plan)
    if pre_materialization_strict_plan:
        semantic_plan = copy.deepcopy(effective_semantic_plan)
    elif strict_anchor_contract and not capability_compilation_errors:
        semantic_plan = _complete_semantic_plan_from_spec(current, semantic_plan)
    elif strict_anchor_contract and current.capabilities:
        # Field/relation compilation warnings may keep the package in review,
        # but the latest complete ability boundary snapshot is still the
        # authoritative Stage 6 plan. Never silently restore an older plan.
        semantic_plan = copy.deepcopy(effective_semantic_plan)
    elif previous_strict_plan:
        semantic_plan = copy.deepcopy(previous_semantic_plan)
    else:
        # Keep the last fact-addressable Skill plan. Wiping capabilities here
        # made an incomplete live coverage check erase a complete submitted
        # boundary set, so stage six compiled nothing and publish crashed.
        kept_capabilities = [
            copy.deepcopy(item)
            for item in (effective_semantic_plan.get("capabilities") or [])
            if isinstance(item, dict)
        ]
        unresolved_items = [
            copy.deepcopy(item)
            for item in (semantic_plan.get("unresolved_items") or [])
            if isinstance(item, dict)
        ]
        if not kept_capabilities:
            unresolved_items.append({
                "type": "capability_plan",
                "title": "需要严格能力边界计划",
                "blocking": True,
            })
        semantic_plan = {
            "business_understanding": (
                copy.deepcopy(semantic_plan.get("business_understanding"))
                if isinstance(semantic_plan.get("business_understanding"), dict)
                else {}
            ),
            "capabilities": kept_capabilities,
            "unresolved_items": unresolved_items,
        }
    semantic_coverage = (
        pre_materialization_coverage
        if pre_materialization_strict_plan
        else _semantic_plan_coverage(current, {"semantic_plan": semantic_plan})
    )
    if current.capabilities:
        from dano.execution.page.capability_repair import _auto_confirm_ready_capabilities

        current = _auto_confirm_ready_capabilities(
            current,
            refresh_machine_owned=True,
        )
    caps = list(current.capabilities or [])
    final_report = validate_flow_spec(current)
    final_errors = [
        *list(final_report.get("errors") or []),
        *list((final_report.get("capability_validation") or {}).get("errors") or []),
        *capability_compilation_errors,
    ]
    public_boundaries_valid = bool(caps) and all(
        _planned_capability_has_public_anchor(
            current, capability.kind, list(_capability_node_step_ids(capability)),
        )
        for capability in caps
    )
    generation_ready = bool(
        semantic_coverage.get("complete")
        and public_boundaries_valid
        and not final_errors
        and not ignored_non_public_capabilities
    )
    submitted_items = [
        item for item in (submitted_semantic_plan.get("capabilities") or [])
        if isinstance(item, dict)
    ]
    submitted_names = [str(item.get("name") or "") for item in submitted_items]
    materialized_names = [str(capability.name or "") for capability in caps]
    missing_submitted_names = sorted(set(submitted_names) - set(materialized_names))
    extra_materialized_names = sorted(set(materialized_names) - set(submitted_names))
    if submitted_items and (
        len(caps) != len(submitted_items)
        or missing_submitted_names
        or extra_materialized_names
    ):
        generation_ready = False
    current.meta = {
        **(current.meta or {}),
        "capability_model": {
            "status": (
                "awaiting_materialization"
                if pre_materialization_strict_plan
                else "ready" if generation_ready else "needs_review"
            ),
            "source": source,
            "generated_count": len(caps),
            "reason": reason,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "semantic_plan": semantic_plan,
            "submitted_semantic_plan": submitted_semantic_plan,
            "submitted_count": len(submitted_items),
            "submitted_names": submitted_names,
            "materialized_count": len(caps),
            "materialized_names": materialized_names,
            "missing_submitted_names": missing_submitted_names,
            "extra_materialized_names": extra_materialized_names,
            "semantic_coverage": semantic_coverage,
            "last_incremental_review": incremental_review,
            "proposal_gate": proposal_gate,
            "capability_compilation": capability_compilation_audit,
            "capability_compilation_errors": capability_compilation_errors,
            "ignored_non_public_capabilities": ignored_non_public_capabilities,
        },
        "capability_orchestration_audit": {
            "mode": "initial" if initial_generation else "boundary_reanalysis",
            "checked_steps": len(original.steps),
            "checked_fields": sum(len(step.params or []) for step in original.steps),
            "checked_captured_requests": len(_request_fact_items(original)),
            "before_errors": len(initial_report.get("errors") or []),
            "before_warnings": len(initial_report.get("warnings") or []),
            "after_errors": len(final_report.get("errors") or []),
            "after_warnings": len(final_report.get("warnings") or []),
            "boundary_reanalysis": True,
            "capability_count_before": capability_count_before,
            "capability_count_after": len(caps),
        },
    }
    return append_flow_version(refresh_review_items(current), "orchestrate_flow", reason=f"生成能力编排: {source}")

_PENDING_FLOW_SPEC_HELPERS = {'_apply_semantic_business_understanding': 'dano.execution.page.capability_semantic', '_attach_option_source_memberships': 'dano.execution.page.capability_refs', '_capability_call_step_ids_from_nodes': 'dano.execution.page.capability_refs', '_capability_dependency_from_link': 'dano.execution.page.capability_contracts', '_capability_field_from_param': 'dano.execution.page.capability_contracts', '_capability_field_summary': 'dano.execution.page.capability_contracts', '_capability_inputs_from_top_level_schema': 'dano.execution.page.capability_io', '_capability_is_batch': 'dano.execution.page.capability_contracts', '_capability_kind_family': 'dano.execution.page.capability_kinds', '_capability_node_step_ids': 'dano.execution.page.capability_refs', '_capability_output_fields': 'dano.execution.page.capability_io', '_capability_request_ref_from_step': 'dano.execution.page.capability_refs', '_capability_response_path_exists': 'dano.execution.page.capability_contracts', '_capability_scoped_step_ids': 'dano.execution.page.capability_refs', '_capability_step_allowed': 'dano.execution.page.capability_refs', '_capability_step_param_exists': 'dano.execution.page.capability_contracts', '_capability_step_was_removed': 'dano.execution.page.capability_contracts', '_complete_semantic_plan_from_spec': 'dano.execution.page.capability_semantic', '_ensure_external_transform_relations': 'dano.execution.page.capability_contracts', '_iter_capability_nodes': 'dano.execution.page.capability_nodes', '_mark_repeated_write_observations': 'dano.execution.page.capability_contracts', '_materialize_semantic_plan_request_refs': 'dano.execution.page.flow_materialization.builder', '_merge_capability_scoped_dependencies': 'dano.execution.page.capability_contracts', '_normalize_capability_references': 'dano.execution.page.capability_nodes', '_ordered_capability_request_refs': 'dano.execution.page.capability_refs', '_planned_capability_has_public_anchor': 'dano.execution.page.capability_contracts', '_pre_materialization_semantic_plan_coverage': 'dano.execution.page.capability_semantic', '_remove_capability_step_nodes': 'dano.execution.page.capability_nodes', '_removed_capability_names': 'dano.execution.page.capability_refs', '_repair_generated_capability_contracts': 'dano.execution.page.capability_repair', '_semantic_plan_coverage': 'dano.execution.page.capability_semantic', '_sync_capability_io_schemas': 'dano.execution.page.capability_io', '_upsert_capability_relation': 'dano.execution.page.capability_nodes', 'append_flow_version': 'dano.execution.page.flow_spec_core.versioning', 'refresh_review_items': 'dano.execution.page.flow_materialization.review_items', 'sync_flow_spec_models': 'dano.execution.page.flow_materialization.builder', 'validate_flow_spec': 'dano.execution.page.flow_spec_validate'}


def _bind_flow_spec_helpers() -> None:
    import sys
    module_globals = globals()
    for name, owner in _PENDING_FLOW_SPEC_HELPERS.items():
        mod = sys.modules.get(owner)
        if mod is None or not hasattr(mod, name):
            continue
        module_globals[name] = getattr(mod, name)


_bind_flow_spec_helpers()
