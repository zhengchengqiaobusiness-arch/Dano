"""Initialize split FlowSpec owners without depending on the public facade."""
from __future__ import annotations

import importlib


_OWNER_MODULES = (
    "dano.execution.page.flow_spec_core.models",
    "dano.execution.page.flow_spec_core.fingerprints",
    "dano.execution.page.flow_spec_core.serialization",
    "dano.execution.page.flow_spec_core.versioning",
    "dano.execution.page.flow_spec_core.normalization",
    "dano.execution.page.flow_spec_core.request_contract",
    "dano.execution.page.flow_spec_core.controlled_edits",
    "dano.execution.page.recording_facts",
    "dano.execution.page.recording_analysis_state",
    "dano.execution.page.recording_agent_contract",
    "dano.execution.page.flow_materialization.titles",
    "dano.execution.page.flow_materialization.request_steps",
    "dano.execution.page.flow_materialization.request_usage",
    "dano.execution.page.flow_materialization.links",
    "dano.execution.page.flow_materialization.hydration",
    "dano.execution.page.flow_materialization.response_maps",
    "dano.execution.page.flow_materialization.field_contracts.common",
    "dano.execution.page.flow_materialization.field_contracts.required",
    "dano.execution.page.flow_materialization.field_contracts.caller_ownership",
    "dano.execution.page.flow_materialization.field_contracts.record_identity",
    "dano.execution.page.flow_materialization.field_contracts.option_projection",
    "dano.execution.page.flow_materialization.field_contracts.option_repair",
    "dano.execution.page.flow_materialization.field_contracts.option_sync",
    "dano.execution.page.flow_materialization.field_contracts.computed",
    "dano.execution.page.flow_materialization.field_contracts.create_form",
    "dano.execution.page.flow_materialization.field_contracts.edit_form",
    "dano.execution.page.flow_materialization.field_contracts.query_form",
    "dano.execution.page.flow_materialization.field_contracts.row_command",
    "dano.execution.page.flow_materialization.field_contracts.page_rules",
    "dano.execution.page.flow_materialization.builder",
    "dano.execution.page.flow_materialization.review_items",
    "dano.execution.page.recording_semantic_index",
    "dano.execution.page.capability_kinds",
    "dano.execution.page.capability_identity",
    "dano.execution.page.capability_semantic",
    "dano.execution.page.capability_nodes",
    "dano.execution.page.capability_refs",
    "dano.execution.page.capability_io",
    "dano.execution.page.capability_views",
    "dano.execution.page.capability_validation",
    "dano.execution.page.capability_repair",
    "dano.execution.page.capability_orchestration",
    "dano.execution.page.capability_contracts",
    "dano.execution.page.capability_compiler",
    "dano.execution.page.flow_client_projection",
    "dano.execution.page.flow_release",
    "dano.execution.page.flow_spec_validate",
    "dano.execution.page.recording_live",
)

_bound = False
_binding = False


def bind_owner_runtime() -> None:
    """Import every declared owner, then resolve its explicit compatibility hooks."""
    global _bound, _binding
    if _bound or _binding:
        return
    _binding = True
    try:
        modules = [importlib.import_module(name) for name in _OWNER_MODULES]
        for module in modules:
            binder = getattr(module, "_bind_flow_spec_helpers", None)
            if binder is not None:
                binder()
        _bound = True
    finally:
        _binding = False
