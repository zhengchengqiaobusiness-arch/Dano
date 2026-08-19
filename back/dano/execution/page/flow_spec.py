"""Compatibility facade for FlowSpec public imports.

This module re-exports stable public names from their owner modules.
It contains no stage business logic.
"""
from __future__ import annotations

import importlib
import sys

from dano.execution.page.capability_kinds import (
    ALLOWED_CAPABILITY_KINDS,
    READ_CAPABILITY_KINDS,
    WRITE_CAPABILITY_KINDS,
)
from dano.execution.page.capability_orchestration import (
    orchestrate_flow_capabilities,
    sync_capability_scoped_views,
)
from dano.execution.page.capability_repair import (
    auto_fix_flow_spec,
)
from dano.execution.page.capability_views import (
    capability_to_flow_spec_view,
    executable_flow_links,
    flow_spec_capability_contracts,
)
from dano.execution.page.flow_client_projection import (
    flow_spec_to_client,
    flow_spec_to_summary,
    render_business_description,
)
from dano.execution.page.flow_materialization.builder import (
    ensure_recorded_goal,
    sync_flow_spec_models,
    to_flow_spec,
)
from dano.execution.page.flow_materialization.links import (
    rebuild_flow_dependencies,
)
from dano.execution.page.flow_materialization.request_steps import (
    promote_request_to_step,
)
from dano.execution.page.flow_materialization.review_items import (
    build_review_items,
    refresh_review_items,
)
from dano.execution.page.flow_release import (
    prepare_flow_release_candidate,
    prepare_flow_spec_for_publish,
)
from dano.execution.page.flow_spec_core.controlled_edits import (
    apply_client_flow_patch,
    apply_flow_edits,
)
from dano.execution.page.flow_spec_core.fingerprints import (
    flow_spec_fingerprint,
)
from dano.execution.page.flow_spec_core.models import (
    CapabilityDependency,
    CapabilityField,
    CapabilityRelation,
    CapabilityRequestRef,
    FlowCapability,
    FlowLink,
    FlowSpec,
    FlowSpecConflictError,
    FlowStep,
    IdentityBinding,
    ParamField,
    RecordedGoal,
    RequestAnalysis,
    RequestFact,
    RequestFacts,
    RequestUsage,
    ReviewItem,
    SelectBinding,
    SystemValue,
    register_sync_flow_spec_models,
)
from dano.execution.page.flow_spec_core.request_contract import (
    compile_capability_to_api_request,
    dry_run_flow_spec,
    flow_spec_required_params,
    flow_spec_to_api_request,
    flow_spec_user_params,
)
from dano.execution.page.flow_spec_core.serialization import (
    flow_spec_release_payload,
)
from dano.execution.page.flow_spec_core.versioning import (
    append_flow_version,
    ensure_flow_version,
)
from dano.execution.page.flow_spec_validate import (
    validate_flow_spec,
)
from dano.execution.page.recording_agent_contract import (
    apply_recording_agent_submission,
    recording_agent_validation,
    recording_capability_plan_complete,
)
from dano.execution.page.recording_analysis_state import (
    recording_agent_state,
)
from dano.execution.page.recording_facts import (
    classify_network_request,
)

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

__all__ = (
    "ALLOWED_CAPABILITY_KINDS",
    "CapabilityDependency",
    "CapabilityField",
    "CapabilityRelation",
    "CapabilityRequestRef",
    "FlowCapability",
    "FlowLink",
    "FlowSpec",
    "FlowSpecConflictError",
    "FlowStep",
    "IdentityBinding",
    "ParamField",
    "READ_CAPABILITY_KINDS",
    "RecordedGoal",
    "RequestAnalysis",
    "RequestFact",
    "RequestFacts",
    "RequestUsage",
    "ReviewItem",
    "SelectBinding",
    "SystemValue",
    "WRITE_CAPABILITY_KINDS",
    "append_flow_version",
    "apply_client_flow_patch",
    "apply_flow_edits",
    "apply_recording_agent_submission",
    "auto_fix_flow_spec",
    "build_review_items",
    "capability_to_flow_spec_view",
    "classify_network_request",
    "compile_capability_to_api_request",
    "dry_run_flow_spec",
    "ensure_flow_version",
    "ensure_recorded_goal",
    "executable_flow_links",
    "flow_spec_capability_contracts",
    "flow_spec_fingerprint",
    "flow_spec_release_payload",
    "flow_spec_required_params",
    "flow_spec_to_api_request",
    "flow_spec_to_client",
    "flow_spec_to_summary",
    "flow_spec_user_params",
    "orchestrate_flow_capabilities",
    "prepare_flow_release_candidate",
    "prepare_flow_spec_for_publish",
    "promote_request_to_step",
    "rebuild_flow_dependencies",
    "recording_agent_state",
    "recording_agent_validation",
    "recording_capability_plan_complete",
    "refresh_review_items",
    "render_business_description",
    "sync_capability_scoped_views",
    "sync_flow_spec_models",
    "to_flow_spec",
    "validate_flow_spec",
)

for _owner_module in _OWNER_MODULES:
    importlib.import_module(_owner_module)

register_sync_flow_spec_models(sync_flow_spec_models)

for _name, _extracted in list(sys.modules.items()):
    if (
        isinstance(_name, str)
        and _name.startswith("dano.execution.page.")
        and hasattr(_extracted, "_bind_flow_spec_helpers")
    ):
        _extracted._bind_flow_spec_helpers()
