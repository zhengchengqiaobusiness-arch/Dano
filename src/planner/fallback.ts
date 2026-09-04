import type { CapabilityContract, ExecutionPlan } from "../domain.js";
import { buildApprovedRoutes } from "./routes.js";

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

  const selected = ranked[0]!.cap;
  const route = buildApprovedRoutes(verified).find(item => item.targetCapabilityId === selected.id && item.steps.length);
  const ordered = route?.steps.map(step => verified.find(capability => capability.id === step.capabilityId)!) || [selected];
  const steps = ordered.map((capability, index) => ({
    capabilityId: capability.id,
    input: {},
    bindings: capability.bindings.filter(binding => binding.approved).map(binding => ({
      fromStep: ordered.findIndex(item => item.id === binding.fromCapabilityId),
      fromPath: binding.fromPath,
      toPath: binding.toPath
    })),
    reason: route ? `执行已确认路线的第 ${index + 1} 步` : "最明确的已验证原子能力匹配"
  }));
  const missing = ordered.flatMap(capability => capability.inputForm
    .filter(field => field.source === "caller" && field.required)
    .map(field => `${capability.title}：${field.label}`));

  return {
    goal,
    steps,
    needsUserQuestion: missing.length > 0,
    question: missing.length ? `还需要以下必填信息：${missing.join("；")}` : undefined,
    completion: route?.completion || "所选原子能力必须满足合同完成条件。"
  };
}
