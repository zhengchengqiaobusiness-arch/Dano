"""Stage 6: public capability contract views and snapshots."""
from __future__ import annotations

from typing import Any
import hashlib
import json
from dano.execution.page.flow_spec_core.models import (
    FlowCapability,
    FlowLink,
    FlowSpec,
)
from dano.execution.page.flow_materialization.links import (
    _auto_dependency_link_allowed,
    _auto_link_has_grounded_contract,
    _link_is_auto_generated,
    _previous_response_source_step_id,
)
from dano.execution.page.flow_materialization.field_contracts.caller_ownership import (
    _external_capability_input,
)


def executable_flow_links(spec: FlowSpec) -> list[FlowLink]:
    """Return dependencies backed by replay or immutable recording evidence."""
    trusted_verification_ids = {
        str(item.get("verification_id"))
        for item in (spec.meta or {}).get("verification_log") or []
        if isinstance(item, dict)
        and item.get("status") == "passed"
        and item.get("verification_id")
    }
    by_id = {step.step_id: step for step in spec.steps}
    executable: list[FlowLink] = []
    for link in spec.links or []:
        meta = dict(link.meta or {})
        verification_id = str(
            meta.get("verification_id")
            or (link.evidence or {}).get("verification_id")
            or ""
        )
        active = meta.get("active", True) is not False
        machine_verified = bool(
            link.confirmed
            and meta.get("verified") is True
            and verification_id in trusted_verification_ids
        )
        capture_grounded = bool(
            not meta.get("unverified_reason")
            and (
                meta.get("captured_value_match") is True
                or meta.get("captured_structure_match") is True
                or meta.get("captured_record_hydration") is True
            )
        )
        if not (active and (machine_verified or capture_grounded)):
            continue
        if _link_is_auto_generated(link):
            target = by_id.get(link.target_step_id)
            target_param = (
                _resolve_param_reference(target, link.target_path)
                if target is not None else None
            )
            if (
                not _auto_link_has_grounded_contract(spec.steps, link)
                or not _auto_dependency_link_allowed(
                    target_param, link.source_path, link,
                )
            ):
                continue
        executable.append(link)
    return executable


def _capability_confirmation_hash(
    spec: FlowSpec,
    cap: FlowCapability,
    *,
    prepared: bool = False,
) -> str:
    # Hash the same canonical contract shape used by validation/publish. Raw
    # editor state may still have derived fields or schemas pending sync;
    # hashing it directly made an immediate validation look stale.
    canonical = spec if prepared else prepare_flow_spec_for_publish(spec)
    canonical_cap = next(
        (
            item for item in canonical.capabilities
            if item.capability_id == cap.capability_id
        ),
        cap,
    )
    by_id = {step.step_id: step for step in canonical.steps}
    def link_contract(link: FlowLink) -> dict[str, Any]:
        # Verification state proves a dependency; it is not part of the
        # dependency's executable identity. Keep endpoints and transform shape
        # fingerprinted while allowing trusted verification to add its receipt.
        return link.model_dump(exclude={
            "confirmed", "confidence", "reason", "evidence", "meta",
        })

    capability_contract = canonical_cap.model_dump(exclude={
        "confirmed", "confirmation_hash", "status", "requires_human_confirm",
        "confidence", "updated_by",
    })
    capability_contract["dependencies"] = [
        dependency.model_dump(exclude={
            "confirmed", "confidence", "reason", "evidence", "locked",
        })
        for dependency in canonical_cap.dependencies
    ]
    payload = {
        "capability": capability_contract,
        "steps": [
            by_id[sid].model_dump()
            for sid in _capability_node_step_ids(canonical_cap)
            if sid in by_id
        ],
        "links": [
            link_contract(link)
            for link in canonical.links
            if link.source_step_id in set(_capability_node_step_ids(canonical_cap))
            and link.target_step_id in set(_capability_node_step_ids(canonical_cap))
        ],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _capability_execution_contract(spec: FlowSpec, cap: FlowCapability) -> dict[str, Any]:
    by_id = {s.step_id: s for s in spec.steps}
    call_ids = _capability_node_step_ids(cap)
    calls = [
        {
            "step_id": sid,
            "method": by_id[sid].method,
            "path": by_id[sid].path or by_id[sid].url,
            "role": (by_id[sid].source_meta or {}).get("role") or by_id[sid].semantic_role,
            "request_id": (by_id[sid].source_meta or {}).get("request_id"),
            "request_index": (by_id[sid].source_meta or {}).get("request_index"),
        }
        for sid in call_ids
        if sid in by_id
    ]
    final_step = calls[-1]["step_id"] if calls else ""
    foreach_nodes = [
        n for n in _iter_capability_nodes(cap.nodes or [])
        if isinstance(n, dict) and n.get("type") == "foreach"
    ]
    items_field = "entries"
    if foreach_nodes:
        raw_items = str(foreach_nodes[0].get("items") or "input.entries")
        if raw_items.startswith("input."):
            items_field = raw_items.split(".", 1)[1].split(".", 1)[0] or "entries"
    return {
        "protocol": "dano.capability_plan.v1",
        "name": cap.name,
        "kind": cap.kind,
        "nodes": [dict(n) for n in (cap.nodes or [])],
        "call_order": calls,
        "preconditions": [dict(p) for p in (cap.preconditions or []) if isinstance(p, dict)],
        "batch": {
            "enabled": _capability_is_batch(spec, cap),
            "items_field": items_field,
            "mode": "repeat_selected_workflow",
            "merge_base_input": True,
        },
        "return": cap.output_mapping or [{
            "kind": "final_response",
            "step_id": final_step,
            "response_path": "response",
        }],
    }


def _capability_contract_view(
    spec: FlowSpec,
    capability: FlowCapability | None = None,
    *,
    capability_id: str | None = None,
    capability_name: str | None = None,
) -> dict[str, Any]:
    """Build a capability-centric contract view for manifest/runtime consumers."""
    current = ensure_recorded_goal(_sync_capability_io_schemas(sync_flow_spec_models(
        spec.model_copy(deep=True),
    )))
    _normalize_capability_references(current)
    cap = capability.model_copy(deep=True) if capability is not None else _select_flow_capability(
        current,
        capability_id=capability_id,
        capability_name=capability_name,
    )
    if cap is None:
        raise ValueError("capability not found")
    step_by_id = {s.step_id: s for s in current.steps}
    step_ids = [sid for sid in _capability_node_step_ids(cap) if sid in step_by_id]
    steps = [step_by_id[sid] for sid in step_ids]
    return {
        "protocol": "dano.capability_contract.v1",
        "capability_id": cap.capability_id,
        "name": cap.name,
        "title": cap.title,
        "intent": cap.intent,
        "kind": cap.kind,
        "status": cap.status,
        "confirmed": bool(cap.confirmed),
        "confidence": float(cap.confidence or 0.0),
        "requires_human_confirm": bool(cap.requires_human_confirm),
        "step_ids": step_ids,
        "steps": [_capability_step_summary(st) for st in steps],
        "request_refs": [ref.model_dump(exclude_none=True) for ref in (cap.request_refs or [])],
        "input": {
            "schema": dict(cap.input_schema or {}),
            "fields": [_capability_field_summary(f) for f in (cap.inputs or [])],
        },
        "output": {
            "schema": dict(cap.output_schema or {}),
            "fields": [_capability_field_summary(f) for f in (cap.outputs or [])],
            "mapping": [dict(m) for m in (cap.output_mapping or []) if isinstance(m, dict)],
        },
        "fields": {
            "all": [
                _capability_field_summary(f)
                for f in [
                    *(cap.inputs or []),
                    *(cap.request_fields or []),
                    *(cap.internal_fields or []),
                    *(cap.computed_fields or []),
                    *(cap.outputs or []),
                ]
            ],
            "request": [_capability_field_summary(f) for f in (cap.request_fields or [])],
            "internal": [_capability_field_summary(f) for f in (cap.internal_fields or [])],
            "computed": [_capability_field_summary(f) for f in (cap.computed_fields or [])],
        },
        "dependencies": [_capability_dependency_summary(dep) for dep in (cap.dependencies or [])],
        "execution_contract": _capability_execution_contract(current, cap),
        "preconditions": [dict(p) for p in (cap.preconditions or []) if isinstance(p, dict)],
        "caller_responsibilities": list(cap.caller_responsibilities or []),
        "skill_responsibilities": list(cap.skill_responsibilities or []),
    }


def _capability_contract_views(
    spec: FlowSpec,
    *,
    capability_id: str | None = None,
    capability_name: str | None = None,
) -> list[dict[str, Any]]:
    """Return capability contract summaries, optionally scoped to one capability."""
    current = ensure_recorded_goal(_sync_capability_io_schemas(sync_flow_spec_models(
        spec.model_copy(deep=True),
    )))
    _normalize_capability_references(current)
    if capability_id or capability_name:
        cap = _select_flow_capability(current, capability_id=capability_id, capability_name=capability_name)
        if cap is None:
            return []
        return [_capability_contract_view(current, cap)]
    return [_capability_contract_view(current, cap) for cap in (current.capabilities or [])]


def _capability_to_api_dict(spec: FlowSpec, cap: FlowCapability) -> dict[str, Any]:
    out = cap.model_dump(exclude_none=True)
    contract = _capability_execution_contract(spec, cap)
    out["execution_contract"] = contract
    out["workflow_nodes"] = contract["nodes"]
    out["compiled_step_ids"] = [c["step_id"] for c in contract["call_order"]]
    return out


def capability_to_flow_spec_view(
    spec: FlowSpec,
    capability: str | FlowCapability | None = None,
    *,
    capability_id: str | None = None,
    capability_name: str | None = None,
) -> FlowSpec:
    """把单个 capability 编译视图投影成旧 FlowSpec 形态。

    P1 阶段不改变旧全量发布路径；这个视图只用于按能力编译/校验。
    """
    current = ensure_recorded_goal(_sync_capability_io_schemas(sync_flow_spec_models(
        spec.model_copy(deep=True),
    )))
    ref = capability
    if ref is None:
        ref = capability_id or capability_name or ""
    cap = _find_capability_by_ref(current, ref)
    if cap is None:
        raise ValueError(f"capability not found: {ref}")
    by_step = {s.step_id: s for s in current.steps}
    step_ids = [sid for sid in _capability_node_step_ids(cap) if sid in by_step]
    keep = set(step_ids)
    view = current.model_copy(deep=True)
    view.steps = [s for s in view.steps if s.step_id in keep]
    for step in view.steps:
        for param in step.params:
            if not _external_capability_input(param, keep):
                continue
            source = dict(param.source or {})
            source_step_id = _previous_response_source_step_id(param)
            param.category = "user_param"
            param.source_kind = "external_capability_input"
            param.source = {
                "kind": "external_capability_input",
                "source_step_id": source_step_id,
                "response_path": str(
                    source.get("response_path") or source.get("path") or ""
                ),
            }
            param.exposed_to_user = True
            param.editable = True
            param.required = True
            param.default_value = None
            param.reason = "该能力独立调用时由调用方传入上游能力的对应输出值"
    view.links = [
        lk for lk in view.links
        if lk.source_step_id in keep and lk.target_step_id in keep
    ]
    selected_cap = _find_capability_by_ref(view, cap.capability_id) or _find_capability_by_ref(view, cap.name)
    if selected_cap is None:
        selected_cap = cap.model_copy(deep=True)
    selected_cap.nodes = [
        n for n in (selected_cap.nodes or [])
        if not isinstance(n, dict)
        or n.get("type") != "call"
        or str(n.get("step_id") or "") in keep
    ]
    _sync_capability_order(view, selected_cap)
    view.capabilities = [selected_cap]
    view.capability_relations = [
        rel for rel in (view.capability_relations or [])
        if rel.from_capability in {selected_cap.name, selected_cap.capability_id}
        or rel.to_capability in {selected_cap.name, selected_cap.capability_id}
    ]
    view.meta = {
        **(view.meta or {}),
        "compiled_capability": {
            "name": selected_cap.name,
            "capability_id": selected_cap.capability_id,
            "step_ids": selected_cap.step_ids,
        },
    }
    selected_cap.input_schema = _capability_input_schema(
        [
            param
            for step in view.steps
            for param in (step.params or [])
        ],
        set(selected_cap.step_ids or []),
    )
    return sync_capability_scoped_views(view)


def flow_spec_capability_contracts(
    spec: FlowSpec,
    *,
    capability_id: str | None = None,
    capability_name: str | None = None,
) -> list[dict[str, Any]]:
    return _capability_contract_views(
        spec,
        capability_id=capability_id,
        capability_name=capability_name,
    )

_PENDING_FLOW_SPEC_HELPERS = {'_capability_dependency_summary': 'dano.execution.page.capability_contracts', '_capability_field_summary': 'dano.execution.page.capability_contracts', '_capability_input_schema': 'dano.execution.page.capability_io', '_capability_is_batch': 'dano.execution.page.capability_contracts', '_capability_node_step_ids': 'dano.execution.page.capability_refs', '_capability_step_summary': 'dano.execution.page.capability_refs', '_find_capability_by_ref': 'dano.execution.page.capability_contracts', '_iter_capability_nodes': 'dano.execution.page.capability_nodes', '_normalize_capability_references': 'dano.execution.page.capability_nodes', '_resolve_param_reference': 'dano.execution.page.flow_spec_core.controlled_edits', '_select_flow_capability': 'dano.execution.page.capability_nodes', '_sync_capability_io_schemas': 'dano.execution.page.capability_io', '_sync_capability_order': 'dano.execution.page.capability_orchestration', 'sync_capability_scoped_views': 'dano.execution.page.capability_orchestration', 'ensure_recorded_goal': 'dano.execution.page.flow_materialization.builder', 'prepare_flow_spec_for_publish': 'dano.execution.page.flow_release', 'sync_flow_spec_models': 'dano.execution.page.flow_materialization.builder'}


def _bind_flow_spec_helpers() -> None:
    import sys
    module_globals = globals()
    for name, owner in _PENDING_FLOW_SPEC_HELPERS.items():
        mod = sys.modules.get(owner)
        if mod is None or not hasattr(mod, name):
            continue
        module_globals[name] = getattr(mod, name)


_bind_flow_spec_helpers()
