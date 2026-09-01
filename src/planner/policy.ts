import type { CapabilityContract, ExecutionPlan } from "../domain.js";

export interface PolicyDecision {
  ok: boolean;
  needsUserQuestion: boolean;
  question?: string;
  writeSteps: number[];
  errors: string[];
}

export function applyPlanPolicy(plan: ExecutionPlan, capabilities: CapabilityContract[]): PolicyDecision {
  const byId = new Map(capabilities.map(c => [c.id, c]));
  const errors: string[] = [];
  const writeSteps: number[] = [];

  for (let index = 0; index < plan.steps.length; index++) {
    const step = plan.steps[index]!;
    const cap = byId.get(step.capabilityId);
    if (!cap) {
      errors.push(`Step ${index + 1}: unknown capability ${step.capabilityId}`);
      continue;
    }
    if (cap.validation.status !== "verified") {
      errors.push(`Step ${index + 1}: capability ${cap.id} is not verified`);
    }
    if (cap.sideEffect || cap.confirmation.required) writeSteps.push(index);

    for (const binding of step.bindings) {
      if (binding.fromStep >= index) {
        errors.push(`Step ${index + 1}: binding must reference an earlier step`);
        continue;
      }
      const fromCap = byId.get(plan.steps[binding.fromStep]?.capabilityId || "");
      const approved = cap.bindings.some(b =>
        b.approved &&
        b.fromCapabilityId === fromCap?.id &&
        b.fromPath === binding.fromPath &&
        b.toPath === binding.toPath
      );
      if (!approved) {
        errors.push(
          `Step ${index + 1}: unapproved binding ${fromCap?.id || "?"}:${binding.fromPath} -> ${cap.id}:${binding.toPath}`
        );
      }
    }
  }

  if (plan.needsUserQuestion) {
    return {
      ok: false,
      needsUserQuestion: true,
      question: plan.question || "The plan needs clarification.",
      writeSteps,
      errors
    };
  }

  return {
    ok: errors.length === 0,
    needsUserQuestion: errors.length > 0,
    question: errors.length ? errors[0] : undefined,
    writeSteps,
    errors
  };
}
