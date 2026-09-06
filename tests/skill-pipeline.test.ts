import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import os from "node:os";
import { mkdtemp, readFile, rm, stat } from "node:fs/promises";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import type { CapabilityContract, EvidenceEvent } from "../src/domain.js";
import { buildCapabilityCandidates } from "../src/inference/build-candidates.js";
import { finalizeCapabilities } from "../src/inference/finalize-capabilities.js";
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

test("does not promote request fields that share a leftover UI value or have no UI match", () => {
  const events: EvidenceEvent[] = [{
    id: "ui-1", kind: "ui", sessionId: "session", at: "2026-09-01T00:00:00.000Z", pageUrl: "https://example.test/create",
    eventType: "submit", form: [{ label: "数量", type: "number", value: "1" }]
  }, {
    id: "network-1", kind: "network", sessionId: "session", at: "2026-09-01T00:00:01.000Z", correlatedUiEvidenceId: "ui-1",
    request: { method: "POST", url: "https://example.test/items/create", resourceType: "xhr", headers: {}, query: {}, body: { qty: 1, amount: 1, token: "xyz" } },
    response: { status: 200, headers: {}, body: { success: true, data: { id: 1 } } }
  }];
  const capability = finalizeCapabilities(buildCapabilityCandidates(events), events)[0]!;
  assert.equal(capability.inputForm.find(field => field.name === "qty")?.source, "system");
  assert.equal(capability.inputForm.find(field => field.name === "amount")?.source, "system");
  assert.equal(capability.inputForm.find(field => field.name === "token")?.source, "system");
  assert.match(capability.inputForm.find(field => field.name === "amount")?.defaultRule || "", /^literal:1$/);
  assert.match(capability.inputForm.find(field => field.name === "token")?.defaultRule || "", /^literal:/);
});

test("binds create-page enums, prompt twins, and picker assignees from recorded lists", () => {
  const events: EvidenceEvent[] = [{
    id: "ui-form", kind: "ui", sessionId: "session", at: "2026-09-01T00:00:00.000Z",
    pageUrl: "https://example.test/oa/duty/leaveapply/create", eventType: "snapshot",
    form: [
      { label: "请假类型", type: "select", value: "病假", required: true },
      { label: "请输入项目编码", type: "text", value: "P-1" },
      { label: "请输入项目名称", type: "text", value: "项目甲" },
      { label: "所属项目", type: "text", value: "项目甲" },
      { label: "请假天数", type: "number", value: "1", required: true },
      { label: "领导审批", type: "picker", value: "管理员" },
      { label: "人力审批", type: "picker", value: "LS部门", required: true }
    ]
  }, {
    id: "ui-submit", kind: "ui", sessionId: "session", at: "2026-09-01T00:00:01.000Z",
    pageUrl: "https://example.test/oa/duty/leaveapply/create", eventType: "click", text: "提交"
  }, {
    id: "net-dict", kind: "network", sessionId: "session", at: "2026-09-01T00:00:00.100Z",
    request: { method: "GET", url: "https://example.test/admin-api/system/dict-data/simple-list", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { data: [
      { dictType: "oa_duty_leave_type", value: "1", label: "病假" },
      { dictType: "oa_duty_leave_type", value: "2", label: "事假" }
    ] } }
  }, {
    id: "net-users", kind: "network", sessionId: "session", at: "2026-09-01T00:00:00.200Z",
    request: { method: "GET", url: "https://example.test/admin-api/system/user/page", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { data: { list: [
      { id: 174, username: "LSBM", nickname: "LS部门" },
      { id: 1, username: "admin", nickname: "管理员" }
    ] } } }
  }, {
    id: "net-submit", kind: "network", sessionId: "session", at: "2026-09-01T00:00:02.000Z",
    pageUrl: "https://example.test/oa/duty/leave", correlatedUiEvidenceId: "ui-submit",
    request: {
      method: "POST", url: "https://example.test/admin-api/oa/duty-leave/submit-process", resourceType: "xhr",
      headers: {}, query: {},
      body: { type: 1, day: 1, projectCode: "P-1", projectName: "项目甲", billType: "oa_duty_leave", processDefKey: "oa_duty_leave", startUserSelectAssignees: { Activity_0ag2wyz: [174] } }
    },
    response: { status: 200, headers: {}, body: { code: 0, data: 99 } }
  }];
  const verified = finalizeCapabilities(buildCapabilityCandidates(events), events);
  const capability = verified.find(item => item.transport.pathTemplate.includes("submit-process"))!;
  assert.ok(["create", "unknown"].includes(capability.operation));
  const names = capability.inputForm.map(field => field.name);
  for (const key of ["type", "day", "projectCode", "projectName", "billType", "processDefKey", "startUserSelectAssignees"]) {
    assert.equal(names.includes(key), true, `missing ${key}`);
  }
  assert.equal(capability.inputForm.find(field => field.name === "projectCode")?.source, "caller");
  assert.match(capability.inputForm.find(field => field.name === "projectCode")?.label || "", /项目编码/);
  assert.match(capability.inputForm.find(field => field.name === "billType")?.defaultRule || "", /^literal:/);
  assert.ok(["system", "computed"].includes(capability.inputForm.find(field => field.name === "startUserSelectAssignees")?.source || ""));
});

test("binds a leftover request field only when its value uniquely matches leftover UI", () => {
  const events: EvidenceEvent[] = [{
    id: "ui-1", kind: "ui", sessionId: "session", at: "2026-09-01T00:00:00.000Z", pageUrl: "https://example.test/create",
    eventType: "submit", form: [{ label: "备注", type: "textarea", value: "hello" }]
  }, {
    id: "network-1", kind: "network", sessionId: "session", at: "2026-09-01T00:00:01.000Z", correlatedUiEvidenceId: "ui-1",
    request: { method: "POST", url: "https://example.test/items/create", resourceType: "xhr", headers: {}, query: {}, body: { remark: "hello", token: "xyz" } },
    response: { status: 200, headers: {}, body: { success: true, data: { id: 1 } } }
  }];
  const capability = finalizeCapabilities(buildCapabilityCandidates(events), events)[0]!;
  assert.equal(capability.inputForm.find(field => field.name === "remark")?.source, "caller");
  assert.equal(capability.inputForm.find(field => field.name === "remark")?.label, "备注");
  assert.equal(capability.inputForm.find(field => field.name === "token")?.source, "system");
  assert.match(capability.inputForm.find(field => field.name === "token")?.defaultRule || "", /^literal:/);
});

test("distinguishes caller fields from unresolved system fields", () => {
  const events: EvidenceEvent[] = [{
    id: "ui-1", kind: "ui", sessionId: "session", at: "2026-09-01T00:00:00.000Z", pageUrl: "https://example.test/orders",
    eventType: "submit", name: "customerId", form: [{ name: "customerId", label: "客户", type: "text", required: true }]
  }, {
    id: "network-1", kind: "network", sessionId: "session", at: "2026-09-01T00:00:01.000Z", correlatedUiEvidenceId: "ui-1",
    request: { method: "POST", url: "https://example.test/orders/search", resourceType: "xhr", headers: {}, query: {}, body: { customerId: "c1", tenantId: "t1" } },
    response: { status: 200, headers: {}, body: { success: true, data: [] } }
  }];
  const capability = finalizeCapabilities(buildCapabilityCandidates(events), events)[0]!;
  const customer = capability.inputForm.find(field => field.name === "customerId")!;
  const tenant = capability.inputForm.find(field => field.name === "tenantId")!;
  assert.equal(customer.source, "caller");
  assert.equal(customer.systemHandled, false);
  assert.equal(tenant.source, "system");
  assert.equal(tenant.systemHandled, true);
  assert.match(tenant.defaultRule || "", /^literal:/);
  assert.match(tenant.sourceDetail, /系统默认|原样补齐|未观察到用户输入/);
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
    const again = await exportSkill(temporary, "销售订单审核", [query, review]);
    assert.match(result.skillName, /^review-order-sk_/);
    assert.match(again.skillName, /^review-order-sk_/);
    assert.notEqual(result.skillName, again.skillName);
    assert.notEqual(result.dir, again.dir);
    for (const relative of [
      "SKILL.md", "references/CONTRACT.json", "references/CAPABILITIES.md", "references/INPUT_FORMS.md",
      "references/OPTIONS.md", "references/PLAYBOOK.md", "references/routes/route-review-order.md",
      "scripts/execute.py", "scripts/candidates.py", "scripts/format_list.py"
    ]) await stat(path.join(result.dir, relative));
    await assert.rejects(stat(path.join(result.dir, "references", "EVIDENCE.md")));
    await assert.rejects(stat(path.join(result.dir, "references", "reference.md")));
    await execFileAsync("python", ["-m", "py_compile",
      path.join(result.dir, "scripts", "execute.py"), path.join(result.dir, "scripts", "candidates.py"), path.join(result.dir, "scripts", "format_list.py")
    ]);
    if (process.env.SKILL_QUICK_VALIDATE) {
      await execFileAsync("python", [process.env.SKILL_QUICK_VALIDATE, result.dir]);
    }
    const skill = await readFile(path.join(result.dir, "SKILL.md"), "utf8");
    const capabilities = await readFile(path.join(result.dir, "references", "CAPABILITIES.md"), "utf8");
    const forms = await readFile(path.join(result.dir, "references", "INPUT_FORMS.md"), "utf8");
    const playbook = await readFile(path.join(result.dir, "references", "PLAYBOOK.md"), "utf8");
    const route = await readFile(path.join(result.dir, "references", "routes", "route-review-order.md"), "utf8");
    assert.match(skill, /Prefer HTTP/);
    assert.match(skill, /ask_user_question/);
    assert.match(skill, /approved: true/);
    assert.match(skill, /前置/);
    assert.match(skill, /SKILL_AUTH_HEADERS/);
    assert.match(skill, /Use when/);
    assert.match(skill, /何时使用/);
    assert.match(skill, /何时不要使用/);
    assert.match(skill, /## 路由/);
    assert.match(skill, /能力怎么组合/);
    assert.match(skill, /何时走哪条原子操作/);
    assert.match(skill, /何时可以按已确认绑定串联/);
    assert.match(skill, /何时必须停下来问人/);
    assert.match(skill, /失败处理/);
    assert.match(skill, /401\/403/);
    assert.match(skill, /按需读取/);
    assert.match(skill, /INPUT_FORMS\.md/);
    assert.match(skill, /PLAYBOOK\.md/);
    assert.match(skill, /用户：「/);
    assert.ok(skill.split(/\r?\n/).length < 500);
    assert.doesNotMatch(skill, /生成器实现|TypeScript|execute\.mjs|src\/export|录制样本当成默认查询条件|执行器/);
    assert.doesNotMatch(skill, /### 查询销售订单[\s\S]*参数名/);
    assert.match(capabilities, /查询销售订单/);
    assert.match(capabilities, /审核销售订单/);
    assert.match(forms, /ask_user_question/);
    assert.match(forms, /comment/);
    assert.doesNotMatch(forms, /执行器/);
    assert.match(playbook, /规划例子/);
    assert.match(playbook, /无数据/);
    assert.match(playbook, /format_list/);
    assert.match(playbook, /<br>/);
    assert.match(route, /自然语言组合/);
    assert.match(route, /可执行约定/);
    assert.match(route, /find-orders/);
    const contract = JSON.parse(await readFile(path.join(result.dir, "references", "CONTRACT.json"), "utf8"));
    assert.equal(contract.schemaVersion, "2.0");
    assert.equal(contract.generatedAt, undefined);
    assert.equal(contract.routes.length, 1);
    assert.equal(contract.capabilities.every((item: any) => item.validation.status === "verified"), true);
    assert.equal(contract.capabilities.every((item: any) => item.confidence === undefined && item.evidence === undefined), true);
    assert.equal(contract.capabilities.find((item: any) => item.id === "review-order")?.role, "primary");
    const reviewContract = contract.capabilities.find((item: any) => item.id === "review-order");
    assert.equal(reviewContract.inputQuestions[0].id, "comment");
    assert.match(reviewContract.inputQuestions[0].defaultStrategy, /未见过的值/);
    assert.equal(reviewContract.inputForm[0].sourceDetail, "来自已确认的查询结果");
    assert.equal(reviewContract.inputForm[0].requiredBasis, undefined);
    assert.equal(reviewContract.completion.note, undefined);
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
    assert.match(first.name, /^orders-sk_/);
    await library.setFrozen(first.name, true, true);
    await assert.rejects(() => library.invocation(first.name, "查询订单"), /已冻结/);
    const second = await library.export("orders", capabilities, true);
    assert.equal(second.version, 2);
    assert.notEqual(second.name, first.name);
    assert.notEqual(second.directory, first.directory);
    await stat(first.directory);
    await stat(second.directory);
    await assert.rejects(() => library.delete(second.name, false), /明确确认/);
    const deleted = await library.delete(second.name, true);
    assert.equal(deleted.status, "deleted");
    assert.ok(deleted.recoverableFrom);
    await stat(deleted.recoverableFrom!);
    const remaining = await library.list();
    assert.equal(remaining.length, 1);
    assert.equal(remaining[0]!.name, first.name);
    assert.equal(remaining[0]!.status, "frozen");
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
});

test("managed export versions Chinese display names as one Skill family", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-skill-chinese-family-"));
  const library = new SkillLibrary(path.join(temporary, "dist"), path.join(temporary, "data"));
  const capabilities = [verifiedCapability("find-orders")];
  try {
    const first = await library.export("销售订单", capabilities, true);
    const second = await library.export("销售订单", capabilities, true);
    assert.equal(first.version, 1);
    assert.equal(second.version, 2);
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
});

test("exported executor preserves a recorded date-only string", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-skill-date-only-"));
  const create = verifiedCapability("create-visit", "create");
  create.title = "新建来访";
  create.inputSchema = { type: "object", properties: { visitDate: { type: "string" } }, required: ["visitDate"] };
  create.inputForm = [{
    path: "$.visitDate", name: "visitDate", label: "来访日期", valueType: "string", source: "caller",
    required: true, requiredBasis: "ui-required", systemHandled: false, sourceDetail: "保持页面原始日期格式", widget: "date"
  }];
  try {
    const exported = await exportSkill(temporary, "来访登记", [create]);
    const { stdout } = await execFileAsync("python", [
      path.join(exported.dir, "scripts", "execute.py"),
      "--capability", create.id,
      "--input", JSON.stringify({ visitDate: "2026-09-04" }),
      "--prepare-only"
    ]);
    assert.equal(JSON.parse(stdout).prepared.visitDate, "2026-09-04");
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
});

test("exported executor resolves a dynamic candidate label before a write", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-skill-dynamic-candidate-"));
  const lookup = verifiedCapability("query-seals");
  lookup.role = "lookup";
  lookup.outputSchema = { type: "object", properties: { data: { type: "array", items: { type: "object", properties: { id: { type: "string" }, name: { type: "string" } } } } } };
  const create = verifiedCapability("create-seal", "create");
  create.role = "primary";
  create.inputSchema = { type: "object", properties: { sealId: { type: "string" } }, required: ["sealId"] };
  create.inputForm = [{
    path: "$.sealId", name: "sealId", label: "选择公章", valueType: "string", source: "caller",
    required: true, requiredBasis: "ui-required", systemHandled: false, sourceDetail: "来自已验证查询",
    widget: "select", candidates: { type: "capability", capabilityId: lookup.id, valuePath: "$.data[*].id", labelPath: "$.data[*].name" }
  }];
  try {
    const exported = await exportSkill(temporary, "用印申请", [lookup, create]);
    const script = path.join(exported.dir, "scripts", "execute.py");
    const probe = [
      "import importlib.util,json,sys",
      "spec=importlib.util.spec_from_file_location('skill_execute',sys.argv[1])",
      "mod=importlib.util.module_from_spec(spec)",
      "spec.loader.exec_module(mod)",
      "mod.execute_capability=lambda *_args,**_kwargs:{'ok':True,'body':{'data':[{'id':'seal-1','name':'合同章'}]}}",
      "contract=mod.load_contract()",
      "target=next(x for x in contract['capabilities'] if x['id']=='create-seal')",
      "print(json.dumps(mod.prepare_input(target,{'sealId':'合同章'},contract),ensure_ascii=False))"
    ].join(";");
    const { stdout } = await execFileAsync("python", ["-c", probe, script]);
    assert.equal(JSON.parse(stdout).sealId, "seal-1");
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
});
