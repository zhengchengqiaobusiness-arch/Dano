"""Stage 8 Skill Generation Module.

The public surface stays small: a generation request goes in, a validated
SkillPlan or a clarification/failure result comes out. Model text is never
treated as an executable fact.
"""

from dano.onboarding.skill_generation.models import (
    CompositionMode,
    HumanCheckpoint,
    PlanningMode,
    RouteStep,
    SkillGenerationRequest,
    SkillGenerationResult,
    SkillPlan,
    SkillRoute,
    generation_request_fingerprint,
)
from dano.onboarding.skill_generation.planner import generate_skill_plan, propose_deterministic_plan
from dano.onboarding.skill_generation.validate import plan_to_contract_payload, validate_skill_plan
from dano.onboarding.skill_generation.export import (
    SkillExportError,
    SkillExportOutcome,
    export_recording_skill,
)

__all__ = [
    "CompositionMode",
    "HumanCheckpoint",
    "PlanningMode",
    "RouteStep",
    "SkillExportError",
    "SkillExportOutcome",
    "SkillGenerationRequest",
    "SkillGenerationResult",
    "SkillPlan",
    "SkillRoute",
    "export_recording_skill",
    "generate_skill_plan",
    "generation_request_fingerprint",
    "plan_to_contract_payload",
    "propose_deterministic_plan",
    "validate_skill_plan",
]
