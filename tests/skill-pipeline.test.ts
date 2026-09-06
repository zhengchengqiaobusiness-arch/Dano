import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
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
import { exportedQuestion } from "../src/export/skill-handbook.js";
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
      "references/OPTIONS.md", "references/routes/route-review-order.md",
      "scripts/execute.py", "scripts/candidates.py", "scripts/format_list.py"
    ]) await stat(path.join(result.dir, relative));
    await assert.rejects(stat(path.join(result.dir, "references", "PLAYBOOK.md")));
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
    const route = await readFile(path.join(result.dir, "references", "routes", "route-review-order.md"), "utf8");
    assert.match(skill, /ask_user_question/);
    assert.match(skill, /每次模型响应最多原生调用一次/);
    assert.match(skill, /同一阶段所有缺少的调用方字段.*title \+ questions\[\]/);
    assert.match(skill, /questions\[\]\.id.*调用方字段名/);
    assert.match(skill, /非空.*default/);
    assert.match(skill, /approved: true/);
    assert.match(skill, /SKILL_AUTH_HEADERS/);
    assert.match(skill, /Use when/);
    assert.match(skill, /## Workflow/);
    assert.match(skill, /## Atomic capabilities/);
    assert.match(skill, /## Fast path/);
    assert.match(skill, /python scripts\/format_list\.py --capability find-orders --input '\{\}'/);
    assert.match(skill, /## Composed workflows/);
    assert.match(skill, /## Output and failures/);
    assert.match(skill, /## Boundaries/);
    assert.match(skill, /401\/403/);
    assert.match(skill, /## Progressive references/);
    assert.match(skill, /INPUT_FORMS\.md/);
    assert.doesNotMatch(skill, /PLAYBOOK\.md/);
    assert.equal((skill.match(/python scripts\/execute\.py --capability/g) || []).length, 1);
    assert.ok(skill.split(/\r?\n/).length < 100);
    assert.doesNotMatch(skill, /生成器实现|TypeScript|execute\.mjs|src\/export|录制样本当成默认查询条件|执行器/);
    assert.doesNotMatch(skill, /### 查询销售订单[\s\S]*参数名/);
    assert.match(capabilities, /查询销售订单/);
    assert.match(capabilities, /审核销售订单/);
    assert.doesNotMatch(capabilities, /scripts\/execute\.py|ask_user_question/);
    assert.match(forms, /ask_user_question/);
    assert.match(forms, /每次模型响应最多原生调用一次/);
    assert.match(forms, /comment/);
    assert.doesNotMatch(forms, /执行器/);
    assert.match(route, /## Sequence/);
    assert.match(route, /## Run/);
    assert.match(route, /--route route-review-order/);
    assert.match(route, /find-orders/);
    const contract = JSON.parse(await readFile(path.join(result.dir, "references", "CONTRACT.json"), "utf8"));
    assert.equal(contract.schemaVersion, "2.0");
    assert.equal(contract.generatedAt, undefined);
    assert.equal(contract.routes.length, 1);
    assert.equal(contract.capabilities.every((item: any) => item.validation.status === "verified"), true);
    assert.deepEqual(contract.capabilities.find((item: any) => item.id === "find-orders")?.outputSchema, query.outputSchema);
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
    assert.equal(path.dirname(first.directory), path.join(temporary, "dist"));
    assert.equal(first.directory.includes(`${path.sep}staging${path.sep}`), false);
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

test("exported list fast path executes once and formats contract fields and enums", async () => {
  const requests: string[] = [];
  const server = http.createServer((request, response) => {
    requests.push(request.url || "");
    response.setHeader("content-type", "application/json");
    response.end(JSON.stringify({
      rows: [{ searchValue: "", billType: "duty_leave", billCode: "Q-1", leaveType: "busy", status: 0, startTime: "2026-09-07 09:00:00", reason: "测试" }],
      total: 1
    }));
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  const origin = `http://127.0.0.1:${typeof address === "object" && address ? address.port : 0}`;
  const query = verifiedCapability("find-leave");
  query.title = "查询请假申请";
  query.transport = { method: "GET", origin, pathTemplate: "/leave", urlTemplate: `${origin}/leave` };
  query.outputSchema = {
    type: "object",
    properties: {
      rows: {
        type: "array",
        items: {
          type: "object",
          properties: {
            searchValue: { type: "string" },
            billType: { type: "string" },
            billCode: { type: "string" },
            leaveType: { type: "string" },
            status: { type: "integer" },
            startTime: { type: "string" },
            reason: { type: "string" }
          }
        }
      },
      total: { type: "integer" }
    }
  };
  query.inputForm = [
    {
      path: "$.billType", name: "billType", label: "业务类型", valueType: "string", source: "system",
      required: false, requiredBasis: "not-observed", systemHandled: true, sourceDetail: "系统固定", widget: "text",
      defaultRule: 'literal:"duty_leave"'
    },
    {
      path: "$.billCode", name: "billCode", label: "单据编号", valueType: "string", source: "caller",
      required: false, requiredBasis: "not-observed", systemHandled: false, sourceDetail: "查询条件", widget: "text"
    },
    {
      path: "$.leaveType", name: "leaveType", label: "请假类型", valueType: "string", source: "caller",
      required: false, requiredBasis: "not-observed", systemHandled: false, sourceDetail: "查询条件", widget: "select",
      candidates: { type: "static", values: [{ value: "busy", label: "事假" }] }
    },
    {
      path: "$.status", name: "status", label: "流程状态", valueType: "integer", source: "caller",
      required: false, requiredBasis: "not-observed", systemHandled: false, sourceDetail: "查询条件", widget: "select",
      candidates: { type: "static", values: [{ value: 0, label: "未提交" }] }
    }
  ];
  const create = verifiedCapability("create-leave", "create");
  create.inputForm = [
    {
      path: "$.startTime", name: "startTime", label: "开始时间", valueType: "string", source: "caller",
      required: true, requiredBasis: "ui-required", systemHandled: false, sourceDetail: "页面字段", widget: "date"
    },
    {
      path: "$.reason", name: "reason", label: "事由", valueType: "string", source: "caller",
      required: true, requiredBasis: "ui-required", systemHandled: false, sourceDetail: "页面字段", widget: "textarea"
    }
  ];
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-skill-fast-list-"));
  try {
    const exported = await exportSkill(temporary, "请假申请", [query, create]);
    const { stdout } = await execFileAsync("python", [
      path.join(exported.dir, "scripts", "format_list.py"),
      "--capability", query.id,
      "--input", "{}"
    ]);
    assert.deepEqual(requests, ["/leave?billType=duty_leave"]);
    assert.match(stdout, /\| 单据编号 \| 请假类型 \| 流程状态 \|/);
    assert.match(stdout, /\| Q-1 \| 事假 \| 未提交 \|/);
    assert.doesNotMatch(stdout, /searchValue/);
    assert.doesNotMatch(stdout, /billType|业务类型/);
    assert.match(stdout, /开始时间/);
    assert.match(stdout, /事由/);
  } finally {
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("object-array caller fields use the Dano table question contract", () => {
  const capability = verifiedCapability("review-order", "review");
  capability.inputSchema = {
    type: "object",
    properties: {
      items: {
        type: "array",
        title: "工作内容；验收结果",
        items: {
          type: "object",
          properties: {
            content: { type: "string", title: "工作内容" },
            result: {
              type: "string",
              title: "验收结果",
              "x-dano-section-titles": {
                工作内容: "完成结果",
                验收结果: "验收结论"
              }
            },
            progress: {
              type: "number",
              title: "完成进度",
              "x-dano-section-titles": { 工作内容: "完成进度" }
            }
          }
        }
      }
    }
  };
  const items = {
    path: "$.items",
    name: "items",
    label: "工作内容；验收结果",
    valueType: "array" as const,
    source: "caller" as const,
    required: true,
    requiredBasis: "ui-required" as const,
    systemHandled: false,
    sourceDetail: "页面表格由调用方填写",
    widget: "json" as const
  };
  capability.inputForm = [items];

  const question = exportedQuestion(items, [capability], capability.inputForm, capability);

  assert.equal(question.inputType, "table");
  assert.deepEqual(question.columns, [
    { id: "content", label: "工作内容", type: "string" },
    { id: "result", label: "验收结果", type: "string" },
    { id: "progress", label: "完成进度", type: "number" }
  ]);
  assert.deepEqual(question.sections, [
    {
      title: "工作内容",
      columns: [
        { id: "content", label: "工作内容", type: "string" },
        { id: "result", label: "完成结果", type: "string" },
        { id: "progress", label: "完成进度", type: "number" }
      ]
    },
    {
      title: "验收结果",
      columns: [
        { id: "content", label: "工作内容", type: "string" },
        { id: "result", label: "验收结论", type: "string" }
      ]
    }
  ]);
});

test("exported executor preserves month precision and encodes recorded rich text", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-skill-formats-"));
  const create = verifiedCapability("create-seal", "create");
  create.title = "新建用印";
  create.inputSchema = {
    type: "object",
    properties: { month: { type: "string" }, useInfo: { type: "string" } },
    required: ["month", "useInfo"]
  };
  create.inputForm = [{
    path: "$.month", name: "month", label: "统计月份", valueType: "string", source: "caller",
    required: true, requiredBasis: "ui-required", systemHandled: false, sourceDetail: "保持页面月份精度", widget: "date",
    dateFormat: "YYYY-MM"
  }, {
    path: "$.useInfo", name: "useInfo", label: "使用描述", valueType: "string", source: "caller",
    required: true, requiredBasis: "ui-required", systemHandled: false, sourceDetail: "系统编码为 HTML", widget: "textarea",
    requestFormat: "html"
  }];
  try {
    const exported = await exportSkill(temporary, "用印申请", [create]);
    const { stdout } = await execFileAsync("python", [
      path.join(exported.dir, "scripts", "execute.py"),
      "--capability", create.id,
      "--input", JSON.stringify({ month: "2026-10", useInfo: "第一行\n第二行" }),
      "--prepare-only"
    ]);
    assert.deepEqual(JSON.parse(stdout).prepared, {
      month: "2026-10",
      useInfo: "<p>第一行</p><p>第二行</p>"
    });
    const contract = JSON.parse(await readFile(path.join(exported.dir, "references", "CONTRACT.json"), "utf8"));
    const fields = contract.capabilities[0].inputForm;
    assert.equal(fields.find((item: any) => item.name === "month").dateFormat, "YYYY-MM");
    assert.equal(fields.find((item: any) => item.name === "useInfo").requestFormat, "html");
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
});

test("exported executor preserves caller datetime precision", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-skill-datetime-"));
  const create = verifiedCapability("create-duty-leave", "create");
  create.inputSchema = {
    type: "object",
    properties: { startTime: { type: "string" }, endTime: { type: "string" } },
    required: ["startTime", "endTime"]
  };
  create.inputForm = ["startTime", "endTime"].map(name => ({
    path: `$.${name}`,
    name,
    label: name === "startTime" ? "开始时间" : "结束时间",
    valueType: "string" as const,
    source: "caller" as const,
    required: true,
    requiredBasis: "ui-required" as const,
    systemHandled: false,
    sourceDetail: "保持页面日期时间精度",
    widget: "date" as const,
    dateFormat: "YYYY-MM-DD HH:mm" as const
  }));
  try {
    const exported = await exportSkill(temporary, "请假申请", [create]);
    const { stdout } = await execFileAsync("python", [
      path.join(exported.dir, "scripts", "execute.py"),
      "--capability", create.id,
      "--input", JSON.stringify({ startTime: "2026-10-19 00:00", endTime: "2026-10-20 00:00" }),
      "--prepare-only"
    ]);
    assert.deepEqual(JSON.parse(stdout).prepared, {
      startTime: "2026-10-19 00:00:00",
      endTime: "2026-10-20 00:00:00"
    });
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
});

test("exported executor queries dynamic options before sending a display name", async () => {
  const requests: string[] = [];
  const server = http.createServer((request, response) => {
    requests.push(request.url || "");
    response.setHeader("content-type", "application/json");
    response.end(JSON.stringify(request.url?.startsWith("/types")
      ? { data: [{ value: 1, label: "日报" }, { value: 2, label: "周报" }] }
      : { code: 200, data: [] }));
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  const origin = `http://127.0.0.1:${typeof address === "object" && address ? address.port : 0}`;
  const source = verifiedCapability("report-types");
  source.role = "lookup";
  source.transport = { method: "GET", origin, pathTemplate: "/types", urlTemplate: `${origin}/types` };
  source.outputSchema = {
    type: "object",
    properties: { data: { type: "array", items: { type: "object", properties: { value: { type: "integer" }, label: { type: "string" } } } } }
  };
  const target = verifiedCapability("query-reports");
  target.role = "primary";
  target.transport = { method: "GET", origin, pathTemplate: "/reports", urlTemplate: `${origin}/reports` };
  target.inputSchema = { type: "object", properties: { reportType: { type: "integer" } } };
  target.inputForm = [{
    path: "$.reportType", name: "reportType", label: "汇报类型", valueType: "integer", source: "caller",
    required: false, requiredBasis: "not-observed", systemHandled: false, sourceDetail: "显示名称转接口值", widget: "select",
    candidates: { type: "capability", capabilityId: source.id, valuePath: "$.data[*].value", labelPath: "$.data[*].label" }
  }];
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-skill-dynamic-candidate-"));
  try {
    const exported = await exportSkill(temporary, "汇报查询", [target, source]);
    await execFileAsync("python", [
      path.join(exported.dir, "scripts", "execute.py"),
      "--capability", target.id,
      "--input", JSON.stringify({ reportType: "周报" })
    ]);
    assert.deepEqual(requests, ["/types", "/reports?reportType=2"]);
  } finally {
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("exported executor runs an approved query-to-write route with one command", async () => {
  const requests: Array<{ url: string; body?: unknown }> = [];
  let queryRows = [{ id: "order-7" }];
  const server = http.createServer((request, response) => {
    const chunks: Buffer[] = [];
    request.on("data", chunk => chunks.push(Buffer.from(chunk)));
    request.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf8");
      requests.push({ url: request.url || "", ...(raw ? { body: JSON.parse(raw) } : {}) });
      response.setHeader("content-type", "application/json");
      response.end(JSON.stringify(request.url === "/find-orders"
        ? { data: queryRows }
        : { success: true, reviewedId: "order-7" }));
    });
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  const origin = `http://127.0.0.1:${typeof address === "object" && address ? address.port : 0}`;
  const query = verifiedCapability("find-orders");
  query.transport = { method: "GET", origin, pathTemplate: "/find-orders", urlTemplate: `${origin}/find-orders` };
  const review = verifiedCapability("review-order", "review");
  review.transport = { method: "POST", origin, pathTemplate: "/review-order", urlTemplate: `${origin}/review-order` };
  review.inputSchema = {
    type: "object",
    properties: { orderId: { type: "string" }, comment: { type: "string" } },
    required: ["orderId", "comment"]
  };
  review.inputForm.push({
    path: "$.comment", name: "comment", label: "审核意见", valueType: "string", source: "caller",
    required: true, requiredBasis: "ui-required", systemHandled: false, sourceDetail: "由调用方提供", widget: "text"
  });
  review.bindings.push({
    id: "bind-order", fromCapabilityId: query.id, fromPath: "$.data[*].id", toPath: "$.orderId",
    confidence: 1, evidenceIds: [], approved: true, approvalSource: "human", approvedAt: "2026-09-01T00:00:00.000Z"
  });
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-skill-route-"));
  try {
    const exported = await exportSkill(temporary, "销售订单审核", [query, review]);
    const script = path.join(exported.dir, "scripts", "execute.py");
    await assert.rejects(execFileAsync("python", [
      script, "--route", "route-review-order", "--input", JSON.stringify({ "review-order": { comment: "同意" } })
    ]), /明确确认/);
    assert.equal(requests.length, 0);
    queryRows = [{ id: "order-7" }, { id: "order-8" }];
    await assert.rejects(execFileAsync("python", [
      script, "--route", "route-review-order", "--input", JSON.stringify({ "review-order": { comment: "同意" } }), "--confirm-write"
    ]), /无法唯一确定/);
    assert.deepEqual(requests, [{ url: "/find-orders" }]);
    requests.length = 0;
    queryRows = [{ id: "order-7" }];
    const { stdout } = await execFileAsync("python", [
      script, "--route", "route-review-order", "--input", JSON.stringify({ "review-order": { comment: "同意" } }), "--confirm-write"
    ]);
    const result = JSON.parse(stdout);
    assert.equal(result.ok, true);
    assert.deepEqual(result.steps.map((item: { capabilityId: string }) => item.capabilityId), ["find-orders", "review-order"]);
    assert.deepEqual(requests, [
      { url: "/find-orders" },
      { url: "/review-order", body: { comment: "同意", orderId: "order-7" } }
    ]);
  } finally {
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("detail question ids do not overwrite same-named header fields", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-skill-detail-questions-"));
  const create = verifiedCapability("create-reimburse", "create");
  create.role = "primary";
  create.inputSchema = { type: "object", properties: { billType: { type: "string" }, items: { type: "array" } } };
  create.inputForm = [{
    path: "$.billType", name: "billType", label: "业务类型", valueType: "string", source: "system",
    required: false, requiredBasis: "not-observed", systemHandled: true, sourceDetail: "页面无同名输入",
    widget: "text", defaultRule: "literal:reimburse"
  }, {
    path: "$.items", name: "items", label: "明细", valueType: "array", source: "system",
    required: false, requiredBasis: "not-observed", systemHandled: true, sourceDetail: "录制成功请求模板",
    widget: "json", defaultRule: 'literal:[{"billType":"0","billCount":"1"}]'
  }, {
    path: "$.items[*].billType", name: "billType", label: "票据类型", valueType: "string", source: "caller",
    required: false, requiredBasis: "not-observed", systemHandled: false, sourceDetail: "页面固定枚举",
    widget: "select", candidates: { type: "static", values: [{ label: "车船票", value: "0" }] }
  }, {
    path: "$.items[*].billCount", name: "billCount", label: "票据张数", valueType: "string", source: "caller",
    required: false, requiredBasis: "not-observed", systemHandled: false, sourceDetail: "页面同名字段",
    widget: "number"
  }];
  try {
    const exported = await exportSkill(temporary, "报销申请", [create]);
    const contract = JSON.parse(await readFile(path.join(exported.dir, "references", "CONTRACT.json"), "utf8"));
    const target = contract.capabilities.find((item: { id: string }) => item.id === create.id);
    assert.deepEqual(target.inputQuestions.map((item: { id: string }) => item.id), ["items.billType", "billCount"]);
    const { stdout } = await execFileAsync("python", [
      path.join(exported.dir, "scripts", "execute.py"),
      "--capability", create.id,
      "--input", JSON.stringify({ "items.billType": "车船票", billCount: "2" }),
      "--prepare-only"
    ]);
    assert.deepEqual(JSON.parse(stdout).prepared, {
      billType: "reimburse",
      items: [{ billType: "0", billCount: "2" }]
    });
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
});
