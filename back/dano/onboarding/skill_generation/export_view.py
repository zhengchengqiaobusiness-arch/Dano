"""Build a Skill export view without mutating the stage-7 FlowSpec."""

from __future__ import annotations

from dano.execution.page.flow_materialization.field_contracts.caller_ownership import (
    _param_exposed_to_caller,
)
from dano.execution.page.flow_spec_core.models import FlowSpec
from dano.execution.page.flow_spec_core.request_contract import _param_is_dynamic_array_leaf
from dano.onboarding.skill_generation.catalog import capability_ref

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_UNRESOLVED_SOURCE_KINDS = frozenset({"", "unknown", "ambiguous"})


def list_unconfirmed_write_fields(spec: FlowSpec) -> list[str]:
    """List write fields whose source is still unknown. Do not guess a caller/system split."""

    found: list[str] = []
    for step in spec.steps or []:
        if str(step.method or "").upper() not in _WRITE_METHODS:
            continue
        for param in step.params or []:
            if str(param.path or "").startswith(("query.", "path.")):
                continue
            if _param_is_dynamic_array_leaf(step, param):
                continue
            if _param_exposed_to_caller(param):
                continue
            if str(param.source_kind or "unknown").strip().lower() not in _UNRESOLVED_SOURCE_KINDS:
                continue
            label = str(param.label or param.key or param.path or "").strip()
            step_name = str(step.name or step.step_id or "").strip()
            found.append(f"{step_name}:{label}" if step_name else label)
    return [item for item in found if item]


def promote_unconfirmed_write_fields(spec: FlowSpec) -> FlowSpec:
    """Compatibility no-op. Incomplete fields must block export, not be guessed as caller input."""

    return spec.model_copy(deep=True)


def build_export_view(spec: FlowSpec, selected_capability_ids: list[str]) -> FlowSpec:
    """Return a copy that publicly exposes only the selected capabilities."""

    selected = {str(item) for item in selected_capability_ids if str(item)}
    view = spec.model_copy(deep=True)
    kept = [
        cap for cap in view.capabilities
        if capability_ref(cap) in selected or cap.name in selected or cap.capability_id in selected
    ]
    if not kept:
        raise ValueError("导出视图没有所选能力")
    kept_ids = {capability_ref(cap) for cap in kept} | {cap.name for cap in kept} | {cap.capability_id for cap in kept}
    view.capabilities = kept
    view.capability_relations = [
        relation for relation in view.capability_relations or []
        if relation.from_capability in kept_ids and relation.to_capability in kept_ids
    ]
    step_ids: set[str] = set()
    for cap in kept:
        step_ids.update(str(item) for item in (cap.step_ids or []) if str(item))
        for ref in cap.request_refs or []:
            if ref.step_id:
                step_ids.add(str(ref.step_id))
        for node in cap.nodes or []:
            if isinstance(node, dict) and node.get("step_id"):
                step_ids.add(str(node.get("step_id")))
    if step_ids:
        view.steps = [step for step in view.steps if step.step_id in step_ids]
        view.links = [
            link for link in view.links
            if link.source_step_id in step_ids and link.target_step_id in step_ids
        ]
    return promote_unconfirmed_write_fields(view)
