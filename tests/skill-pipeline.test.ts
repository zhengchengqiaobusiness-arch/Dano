import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import os from "node:os";
import { mkdtemp, readFile, rm, stat } from "node:fs/promises";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import type { CapabilityContract, EvidenceEvent } from "../src/domain.js";
import { buildCapabilityCandidates } from "../src/inference/build-candidates.js";
import { validateCapability } from "../src/validation/validator.js";
import { buildApprovedRoutes } from "../src/planner/routes.js";
import { exportSkill } from "../src/export/skill-exporter.js";
import { SkillLibrary } from "../src/catalog/skill-library.js";

const execFileAsync = promisify(execFile);

function verifiedCapability(id: string, operation: CapabilityContract["operation"] = "query"): CapabilityContract {
  const sideEffect = operation !== "query";
  return {
    id,
    kind: "atomic",
    title: id === "find-orders" ? "查询销售订单" : "审核销售订单",
    description: id === "find-orders" ? "按条件查询销售订单并返回候选" : "审核选中的销售订单",
    operation,
    confidence: 1,
    transport: { method: sideEffect ? "POST" : "GET", urlTemplate: `https://example.test/${id}`, origin: "https://example.test", pathTemplate: `/${id}` },
    inputSchema: sideEffect ? { type: "object", properties: { orderId: { type: "string" } }, required: ["orderId"] } : { type: "object", properties: {} },
    outputSchema: id === "find-orders"
      ? { type: "object", properties: { data: { type: "array", items: { type: "object", properties: { id: { type: "string" } } } } } }
      : { type: "object", properties: { success: { type: "boolean" } } },
    inputForm: sideEffect ? [{
      path: "$.orderId", name: "orderId", label: "订单编号", valueType: "string", source: "binding",
      required: true, requiredBasis: "manual", systemHandled: true, sourceDetail: "来自已确认的查询结果",
      widget: "text"
    }] : [],
    evidence: [{ eventId: `${id}-network`, sessionId: "session", kind: "network", at: "2026-09-01T00:00:00.000Z", status: 200 }],
    sideEffect,
    confirmation: { required: sideEffect },
    completion: { acceptedHttpStatuses: [200] },
    bindings: [],
    validation: { version: 2, status: "verified", checks: [] },
    generated: { source: "heuristic", generatedAt: "2026-09-01T00:00:00.000Z" }
  };
}

test("distinguishes caller fields from unresolved system fields", () => {
  const events: EvidenceEvent[] = [{
    id: "ui-1", kind: "ui", sessionId: "session", at: "2026-09-01T00:00:00.000Z", pageUrl: "https://example.test/orders",
    eventType: "submit", name: "customerId", form: [{ name: "customerId", label: "客户", type: "text", required: true }]
  }, {
    id: "network-1", kind: "network", sessionId: "session", at: "2026-09-01T00:00:01.000Z", correlatedUiEvidenceId: "ui-1",
    request: { method: "POST", url: "https://example.test/orders/search", resourceType: "xhr", headers: {}, query: {}, body: { customerId: "c1", tenantId: "t1" } },
    response: { status: 200, headers: {}, body: { success: true, data: [] } }
  }];
  const capability = buildCapabilityCandidates(events)[0]!;
  const customer = capability.inputForm.find(field => field.name === "customerId")!;
  const tenant = capability.inputForm.find(field => field.name === "tenantId")!;
  assert.equal(customer.source, "caller");
  assert.equal(customer.systemHandled, false);
  assert.equal(tenant.source, "system");
  assert.equal(tenant.systemHandled, true);
  assert.match(tenant.sourceDetail, /后台自动处理/);
  const validated = validateCapability(capability, events, [capability]);
  assert.equal(validated.validation.status, "verified");
});

test("builds composition routes only from approved bindings and in dependency order", () => {
  const query = verifiedCapability("find-orders");
  const review = verifiedCapability("review-order", "review");
  review.bindings.push({
    id: "bind-order", fromCapabilityId: query.id, fromPath: "$.data[*].id", toPath: "$.orderId",
    confidence: 1, evidenceIds: [], approved: true, approvalSource: "human", approvedAt: "2026-09-01T00:00:00.000Z"
  });
  review.bindings.push({
    id: "not-approved", fromCapabilityId: query.id, fromPath: "$.data[*].id", toPath: "$.orderId",
    confidence: .5, evidenceIds: [], approved: false
  });
  const routes = buildApprovedRoutes([review, query]);
  assert.equal(routes.length, 1);
  assert.deepEqual(routes[0]!.steps.map(step => step.capabilityId), ["find-orders", "review-order"]);
  assert.deepEqual(routes[0]!.approvedBindingIds, ["bind-order"]);
});

test("exports a progressively disclosed Python Skill package", async () => {
  const query = verifiedCapability("find-orders");
  const review = verifiedCapability("review-order", "review");
  review.inputForm.push({
    path: "$.comment", name: "comment", label: "审核意见", valueType: "string", source: "caller",
    required: false, requiredBasis: "manual", systemHandled: false, sourceDetail: "由调用方提供",
    widget: "text"
  });
  review.bindings.push({
    id: "bind-order", fromCapabilityId: query.id, fromPath: "$.data[*].id", toPath: "$.orderId",
    confidence: 1, evidenceIds: [], approved: true, approvalSource: "human", approvedAt: "2026-09-01T00:00:00.000Z"
  });
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-skill-export-"));
  try {
    const result = await exportSkill(temporary, "销售订单审核", [query, review]);
    assert.equal(result.skillName, "review-order");
    for (const relative of [
      "SKILL.md", "references/CONTRACT.json", "references/reference.md", "references/CAPABILITIES.md", "references/INPUT_FORMS.md",
      "references/OPTIONS.md", "scripts/execute.py", "scripts/candidates.py", "scripts/format_list.py"
    ]) await stat(path.join(result.dir, relative));
    await execFileAsync("python", ["-m", "py_compile",
      path.join(result.dir, "scripts", "execute.py"), path.join(result.dir, "scripts", "candidates.py"), path.join(result.dir, "scripts", "format_list.py")
    ]);
    if (process.env.SKILL_QUICK_VALIDATE) {
      await execFileAsync("python", [process.env.SKILL_QUICK_VALIDATE, result.dir]);
    }
    const skill = await readFile(path.join(result.dir, "SKILL.md"), "utf8");
    assert.match(skill, /Prefer HTTP/);
    assert.match(skill, /ask_user_question/);
    assert.match(skill, /approved: true/);
    assert.match(skill, /Python/);
    assert.doesNotMatch(skill, /生成器实现|TypeScript|execute\.mjs/);
    const contract = JSON.parse(await readFile(path.join(result.dir, "references", "CONTRACT.json"), "utf8"));
    assert.equal(contract.schemaVersion, "2.0");
    assert.equal(contract.routes.length, 1);
    assert.equal(contract.capabilities.every((item: any) => item.validation.status === "verified"), true);
    const reviewContract = contract.capabilities.find((item: any) => item.id === "review-order");
    assert.equal(reviewContract.inputQuestions[0].id, "comment");
    assert.match(reviewContract.inputQuestions[0].defaultStrategy, /不复制录制样本/);
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
});

test("manages export versions, freezing and recoverable deletion", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-skill-library-"));
  const library = new SkillLibrary(path.join(temporary, "dist"), path.join(temporary, "data"));
  const capabilities = [verifiedCapability("find-orders")];
  try {
    await assert.rejects(() => library.export("orders", capabilities, false), /明确确认/);
    const first = await library.export("orders", capabilities, true);
    assert.equal(first.version, 1);
    await library.setFrozen(first.name, true, true);
    await assert.rejects(() => library.export("orders", capabilities, true), /冻结/);
    await library.setFrozen(first.name, false, true);
    const second = await library.export("orders", capabilities, true);
    assert.equal(second.version, 2);
    await assert.rejects(() => library.delete(second.name, false), /明确确认/);
    const deleted = await library.delete(second.name, true);
    assert.equal(deleted.status, "deleted");
    assert.ok(deleted.recoverableFrom);
    await stat(deleted.recoverableFrom!);
    assert.equal((await library.list()).length, 0);
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
});
