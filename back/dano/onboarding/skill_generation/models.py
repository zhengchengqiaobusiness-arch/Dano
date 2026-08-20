"""Structured request and plan models for recording stage 8."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class PlanningMode(StrEnum):
    DYNAMIC = "dynamic"
    FIXED = "fixed"


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


class RouteExample(BaseModel):
    user_request: str
    route_id: str = ""
    collected_fields: list[str] = Field(default_factory=list)
    capability_sequence: list[str] = Field(default_factory=list)
    bindings: list[RouteBinding] = Field(default_factory=list)
    confirmation_points: list[str] = Field(default_factory=list)
    done_when: str = ""


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
