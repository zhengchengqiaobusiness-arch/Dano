import type { CapabilityContract, ExecutionPlan } from "../domain.js";

function score(goal: string, cap: CapabilityContract) {
  const haystack = `${cap.id} ${cap.title} ${cap.description} ${cap.operation}`.toLowerCase();
  const tokens = goal.toLowerCase().split(/[^\p{Letter}\p{Number}]+/u).filter(t => t.length >= 2);
  return tokens.reduce((n, token) => n + (haystack.includes(token) ? 1 : 0), 0);
}

export function fallbackPlan(goal: string, capabilities: CapabilityContract[]): ExecutionPlan {
  const verified = capabilities.filter(c => c.validation.status === "verified");
  const ranked = verified
    .map(cap => ({ cap, score: score(goal, cap) }))
    .filter(x => x.score > 0)
    .sort((a, b) => b.score - a.score);

  if (!ranked.length) {
    return {
      goal,
      steps: [],
      needsUserQuestion: true,
      question: "没有找到足够明确的已验证能力。你希望执行哪个业务操作？",
      completion: "No execution until a verified target is selected."
    };
  }

  if (ranked.length > 1 && ranked[0]!.score === ranked[1]!.score) {
    return {
      goal,
      steps: [],
      needsUserQuestion: true,
      question: `目标有歧义：${ranked.slice(0, 3).map(x => x.cap.title).join(" / ")}。请选择一个。`,
      completion: "No execution until ambiguity is resolved."
    };
  }

  return {
    goal,
    steps: [{
      capabilityId: ranked[0]!.cap.id,
      input: {},
      bindings: [],
      reason: "Best verified deterministic text match."
    }],
    needsUserQuestion: false,
    completion: "The selected capability must satisfy its contract completion criteria."
  };
}
