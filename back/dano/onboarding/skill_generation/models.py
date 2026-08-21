"""Structured request and plan models for recording stage 8."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class PlanningMode(StrEnum):
    DYNAMIC = "dynamic"
    FIXED = "fixed"


class InputSourceKind(StrEnum):
    USER = "user"
    FIXED_VALUE = "fixed_value"
    SYSTEM_CONTEXT = "system_context"
    CONFIRMED_BINDING = "confirmed_binding"


class CompositionMode(StrEnum):
    ATOMIC = "atomic"
    BOUND = "bound"
    HANDOFF = "handoff"
    INDEPENDENT = "independent"


class SkillGenerationRequest(BaseModel):
    title: str = ""
    business_description: str = ""
    planning_mode: PlanningMode = PlanningMode.DYNAMIC
    example_requests: list[str] = Field(default_factory=list)
    success_criteria: str = ""
    forbidden_actions: str = ""
    out_dir: str = ""
    require_stage_seven: bool | None = None


class UnusedCapability(BaseModel):
    capability_id: str
    name: str = ""
    title: str = ""
    reason: str


class RouteBinding(BaseModel):
    from_capability: str = ""
    from_output: str = ""
    to_capability: str = ""
    to_input: str = ""
    source: Literal["user_input", "capability_output", "fixed_value", "system_value"] = "capability_output"
    from_step: str = ""
    to_step: str = ""
    transform_owner: str = ""
    source_selector: str = ""
    target_path: str = ""
    fixed_value: Any = None


class StepInputSource(BaseModel):
    field: str
    source: InputSourceKind
    from_step_key: str = ""
    notes: str = ""


class HumanCheckpoint(BaseModel):
    after_step: str = ""
    before_step: str = ""
    required_fields: list[str] = Field(default_factory=list)
    prompt: str = ""
    choice_source: Literal["previous_result", "dynamic_options", "free_text", "combined"] = "previous_result"
    selection_mode: Literal["single", "multiple", "text", "date"] = "single"
    resume_when: str = "用户已选定有效目标并通过输入校验"
    on_cancel: str = "停止并报告未执行"


class RouteStep(BaseModel):
    step_key: str
    capability_id: str
    input_sources: list[StepInputSource] = Field(default_factory=list)
    bindings: list[RouteBinding] = Field(default_factory=list)
    checkpoint: HumanCheckpoint | None = None
    confirm_before_execute: bool = False
    done_when: str = ""
    on_failure: str = ""


class IntentBranch(BaseModel):
    branch_id: str
    trigger: str
    capability_sequence: list[str] = Field(default_factory=list)
    mutation: Literal["read", "write", "mixed"] = "read"
    preconditions: list[str] = Field(default_factory=list)
    done_when: str = ""
    unresolved: list[str] = Field(default_factory=list)
    source: Literal["description", "example", "confirmed_relation"] = "description"
    independent: bool = False
    target_given: bool = False
    conflicting: bool = False


class RouteExample(BaseModel):
    user_request: str
    route_id: str = ""
    collected_fields: list[str] = Field(default_factory=list)
    capability_sequence: list[str] = Field(default_factory=list)
    bindings: list[RouteBinding] = Field(default_factory=list)
    confirmation_points: list[str] = Field(default_factory=list)
    done_when: str = ""
    input_origins: list[str] = Field(default_factory=list)
    auto_bound_fields: list[str] = Field(default_factory=list)
    ask_at: list[str] = Field(default_factory=list)
    confirm_at: list[str] = Field(default_factory=list)
    on_cancel: str = ""
    on_empty_or_ambiguous: str = ""
    on_unknown_write_result: str = ""


class SkillRoute(BaseModel):
    route_id: str
    name: str
    when_to_use: str
    capability_sequence: list[str] = Field(default_factory=list)
    step_ids: list[str] = Field(default_factory=list)
    required_user_inputs: list[str] = Field(default_factory=list)
    bindings: list[RouteBinding] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    requires_confirmation: bool = False
    done_when: str = ""
    failure_behavior: str = ""
    examples: list[RouteExample] = Field(default_factory=list)
    steps: list[RouteStep] = Field(default_factory=list)
    checkpoints: list[HumanCheckpoint] = Field(default_factory=list)
    composition_mode: CompositionMode = CompositionMode.ATOMIC

    @model_validator(mode="after")
    def derive_sequence_from_steps(self) -> SkillRoute:
        if self.steps:
            self.capability_sequence = [step.capability_id for step in self.steps]
            self.step_ids = [step.step_key for step in self.steps]
            derived_bindings = [binding for step in self.steps for binding in step.bindings]
            if derived_bindings:
                self.bindings = derived_bindings
            derived_checks = [step.checkpoint for step in self.steps if step.checkpoint is not None]
            if derived_checks and not self.checkpoints:
                self.checkpoints = derived_checks
            if any(step.confirm_before_execute for step in self.steps):
                self.requires_confirmation = True
        return self


class SkillPlan(BaseModel):
    source_flow_fingerprint: str
    planning_mode: PlanningMode
    summary: str = ""
    trigger_phrases: list[str] = Field(default_factory=list)
    selected_capability_ids: list[str] = Field(default_factory=list)
    unused_capabilities: list[UnusedCapability] = Field(default_factory=list)
    routes: list[SkillRoute] = Field(default_factory=list)
    safety_rules: list[str] = Field(default_factory=list)
    clarification_questions: list[str] = Field(default_factory=list)
    composition_summary: str = ""
    composition_notes: list[str] = Field(default_factory=list)
    intent_branches: list[IntentBranch] = Field(default_factory=list)


class SkillGenerationResult(BaseModel):
    status: Literal["planned", "needs_clarification", "generation_failed"]
    plan: SkillPlan | None = None
    errors: list[str] = Field(default_factory=list)
    clarification_questions: list[str] = Field(default_factory=list)


def generation_request_fingerprint(
    *,
    result_id: str,
    stage_seven_fingerprint: str,
    request: SkillGenerationRequest,
) -> str:
    payload = {
        "result_id": str(result_id or ""),
        "stage7_fingerprint": str(stage_seven_fingerprint or ""),
        "title": str(request.title or "").strip(),
        "business_description": str(request.business_description or "").strip(),
        "planning_mode": str(request.planning_mode),
        "examples": [str(item).strip() for item in request.example_requests if str(item).strip()],
        "success_criteria": str(request.success_criteria or "").strip(),
        "forbidden_actions": str(request.forbidden_actions or "").strip(),
        "require_stage_seven": request.require_stage_seven,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
