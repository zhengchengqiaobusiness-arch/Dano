"""Build a Skill export view without mutating the stage-7 FlowSpec."""

from __future__ import annotations

from dano.execution.page.flow_spec_core.models import FlowSpec
from dano.onboarding.skill_generation.catalog import capability_ref


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
    return view
