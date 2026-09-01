import OpenAI from "openai";
import { zodTextFormat } from "openai/helpers/zod";
import { z } from "zod";
import type { CapabilityContract, ExecutionPlan } from "../domain.js";

const Refinement = z.object({
  title: z.string().min(1).max(120),
  description: z.string().min(1).max(1200),
  operation: z.enum(["query", "create", "update", "review", "delete", "unknown"]),
  confidence: z.number().min(0).max(1),
  evidenceIds: z.array(z.string()).min(1),
  rationale: z.string().max(1000)
});

const Plan = z.object({
  goal: z.string(),
  steps: z.array(z.object({
    capabilityId: z.string(),
    input: z.record(z.string(), z.unknown()),
    bindings: z.array(z.object({
      fromStep: z.number().int().nonnegative(),
      fromPath: z.string(),
      toPath: z.string()
    })),
    reason: z.string()
  })),
  needsUserQuestion: z.boolean(),
  question: z.string().optional(),
  completion: z.string()
});

export class OpenAIReasoner {
  readonly model: string;
  private readonly client?: OpenAI;

  constructor(model = process.env.OPENAI_MODEL || "gpt-5.5") {
    this.model = model;
    if (process.env.OPENAI_API_KEY) {
      this.client = new OpenAI({
        apiKey: process.env.OPENAI_API_KEY,
        baseURL: process.env.OPENAI_BASE_URL || undefined
      });
    }
  }

  available() {
    return Boolean(this.client);
  }

  async refineCapability(capability: CapabilityContract): Promise<CapabilityContract> {
    if (!this.client) return capability;
    const allowedEvidence = new Set(capability.evidence.map(e => e.eventId));

    const response = await this.client.responses.parse({
      model: this.model,
      instructions: [
        "You refine an evidence-backed business capability.",
        "Do not invent endpoints, fields, options, response fields, or evidence.",
        "Classify only the observed behavior. For ambiguous POST requests, use UI text and the recorded transport semantics.",
        "Every evidenceIds item must be copied from the provided capability.",
        "Write a concise business-facing title and description."
      ].join("\n"),
      input: JSON.stringify(capability),
      text: { format: zodTextFormat(Refinement, "capability_refinement") }
    });

    const parsed = response.output_parsed;
    if (!parsed) return capability;
    if (!parsed.evidenceIds.every(id => allowedEvidence.has(id))) return capability;

    return {
      ...capability,
      title: parsed.title,
      description: parsed.description,
      operation: parsed.operation,
      confidence: Math.max(capability.confidence, parsed.confidence),
      sideEffect: capability.sideEffect || ["create", "update", "review", "delete"].includes(parsed.operation),
      confirmation: {
        required: capability.confirmation.required || ["create", "update", "review", "delete"].includes(parsed.operation),
        reason: capability.confirmation.reason || (["create", "update", "review", "delete"].includes(parsed.operation)
          ? `${parsed.operation} changes business data`
          : undefined)
      },
      generated: {
        source: "openai",
        model: this.model,
        generatedAt: new Date().toISOString()
      }
    };
  }

  async plan(goal: string, capabilities: CapabilityContract[]): Promise<ExecutionPlan | undefined> {
    if (!this.client) return undefined;
    const verified = capabilities.filter(c => c.validation.status === "verified");
    const response = await this.client.responses.parse({
      model: this.model,
      instructions: [
        "Plan only with the verified capability IDs provided.",
        "Never invent a capability, field, binding, or result.",
        "Automatic data flow is allowed only when the capability catalog contains an approved binding.",
        "If a required value is missing, multiple targets match, or a result would be ambiguous, set needsUserQuestion=true and ask one focused question.",
        "Writes may be planned but are not confirmed by the plan.",
        "Completion means each step satisfies its contract completion criteria."
      ].join("\n"),
      input: JSON.stringify({ goal, capabilities: verified }),
      text: { format: zodTextFormat(Plan, "execution_plan") }
    });
    return response.output_parsed ?? undefined;
  }
}
