import test from "node:test";
import assert from "node:assert/strict";
import { applyPlanPolicy } from "../src/planner/policy.js";
import type { CapabilityContract, ExecutionPlan } from "../src/domain.js";

const base: CapabilityContract = {
  id: "search",
  title: "Search",
  description: "Search",
  operation: "query",
  confidence: 1,
  transport: { method: "GET", urlTemplate: "https://x.test/search?q={q}", origin: "https://x.test", pathTemplate: "/search" },
  inputSchema: {},
  outputSchema: {},
  inputForm: [],
  evidence: [],
  sideEffect: false,
  confirmation: { required: false },
  completion: { acceptedHttpStatuses: [200] },
  bindings: [],
  validation: { status: "verified", checks: [] },
  generated: { source: "heuristic", generatedAt: new Date().toISOString() }
};

test("rejects unapproved automatic bindings", () => {
  const update: CapabilityContract = {
    ...base,
    id: "update",
    operation: "update",
    sideEffect: true,
    confirmation: { required: true },
    transport: { ...base.transport, method: "PATCH", urlTemplate: "https://x.test/items/{id}" }
  };
  const plan: ExecutionPlan = {
    goal: "find and update",
    needsUserQuestion: false,
    completion: "done",
    steps: [
      { capabilityId: "search", input: {}, bindings: [], reason: "find" },
      { capabilityId: "update", input: {}, bindings: [{ fromStep: 0, fromPath: "$.id", toPath: "$.id" }], reason: "update" }
    ]
  };
  const decision = applyPlanPolicy(plan, [base, update]);
  assert.equal(decision.ok, false);
  assert.match(decision.errors[0] || "", /unapproved binding/);
  assert.deepEqual(decision.writeSteps, [1]);
});
