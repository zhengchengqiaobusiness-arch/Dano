import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import type { CapabilityContract, EvidenceEvent, NetworkEvidence, UiEvidence } from "../src/domain.js";
import { BrowserRecorder, recordingStopReadiness } from "../src/browser/recorder.js";
import { buildCapabilityCandidates } from "../src/inference/build-candidates.js";
import { reviewCatalog } from "../src/review/catalog-review.js";
import { validateCapability } from "../src/validation/validator.js";

const at = (second: number) => `2026-09-04T16:07:${String(second).padStart(2, "0")}.000Z`;

function ui(id: string, second: number, text: string, pageUrl: string, form: UiEvidence["form"] = []): UiEvidence {
  return {
    id,
    kind: "ui",
    sessionId: "ruoyi",
    at: at(second),
    pageUrl,
    eventType: "click",
    text,
    label: text,
    form
  };
}

function network(
  id: string,
  second: number,
  url: string,
  correlatedUiEvidenceId: string,
  body: Record<string, unknown>,
  responseBody: Record<string, unknown>
): NetworkEvidence {
  return {
    id,
    kind: "network",
    sessionId: "ruoyi",
    at: at(second),
    pageUrl: "https://ruoyioffice.com/web/#/oa/seal/seal-apply-info?t=1",
    correlatedUiEvidenceId,
    request: {
      method: "POST",
      url,
      resourceType: "xhr",
      headers: { "content-type": "application/json" },
      query: {},
      body
    },
    response: { status: 200, headers: {}, body: responseBody }
  };
}

function capability(event: NetworkEvidence): CapabilityContract {
  return {
    id: "create-seal",
    kind: "atomic",
    title: "新增印章申请",
    description: "新增印章申请",
    operation: "create",
    confidence: 1,
    transport: {
      method: "POST",
      origin: "https://ruoyioffice.com",
      pathTemplate: "/admin-api/oa/seal-apply-bill/submit",
      urlTemplate: "https://ruoyioffice.com/admin-api/oa/seal-apply-bill/submit"
    },
    inputSchema: { type: "object", properties: {} },
    outputSchema: { type: "object", properties: {} },
    inputForm: [],
    evidence: [{ eventId: event.id, sessionId: event.sessionId, kind: "network", at: event.at, status: event.response?.status }],
    sideEffect: true,
    confirmation: { required: true },
    completion: { acceptedHttpStatuses: [200] },
    bindings: [],
    validation: { version: 2, status: "candidate", checks: [] },
    generated: { source: "heuristic", generatedAt: at(0) }
  };
}

test("HTTP 200 with a failed business envelope is not successful evidence", () => {
  const failed = network(
    "net-failed",
    59,
    "https://ruoyioffice.com/admin-api/oa/seal-apply-bill/submit",
    "ui-submit",
    { sealId: 0, sealNo: "" },
    { code: 400, msg: "请求参数不正确:印章编号不能为空" }
  );
  const validated = validateCapability(capability(failed), [failed]);
  const success = validated.validation.checks.find(check => check.name === "successful-response");
  assert.equal(success?.ok, false, JSON.stringify(validated.validation.checks));
});

test("record stop keeps the live session open until every expected operation has business-success evidence", () => {
  const list = "https://ruoyioffice.com/web/#/oa/seal/seal-apply-list";
  const form = "https://ruoyioffice.com/web/#/oa/seal/seal-apply-info?t=1";
  const failed = network(
    "net-failed",
    21,
    "https://ruoyioffice.com/admin-api/oa/seal-apply-bill/submit",
    "ui-submit",
    { sealId: 0, sealNo: "" },
    { code: 400, msg: "请求参数不正确:印章编号不能为空" }
  );
  const evidence: EvidenceEvent[] = [ui("ui-add", 10, "新增", list), ui("ui-submit", 20, "确认提交", form), failed];
  const blocked = recordingStopReadiness(evidence, ["create"]);
  assert.equal(blocked.ready, false);
  assert.deepEqual(blocked.missingOperations, ["create"]);
  assert.match(blocked.message, /印章编号不能为空/);
  failed.response = { status: 200, headers: {}, body: { code: 200, data: 91, msg: "操作成功" } };
  const ready = recordingStopReadiness(evidence, ["create"]);
  assert.equal(ready.ready, true);
  assert.equal(ready.contractReview.status, "passed");
  assert.equal(ready.contractReview.primaryCount, 1);
});

test("a generic save or submit inherits the active create or update form intent", () => {
  const list = "https://ruoyioffice.com/web/#/oa/seal/seal-apply-list";
  const form = "https://ruoyioffice.com/web/#/oa/seal/seal-apply-info?t=1";
  const createEvents: EvidenceEvent[] = [
    ui("ui-add", 10, "新增", list),
    ui("ui-submit", 20, "确认提交", form, [{ name: "sealName", label: "印章", type: "picker", value: "合同专用章" }]),
    network(
      "net-create",
      21,
      "https://ruoyioffice.com/admin-api/oa/seal-apply-bill/submit",
      "ui-submit",
      { sealId: 12, sealNo: "SZ0002", sealName: "合同专用章" },
      { code: 200, data: 91, msg: "操作成功" }
    )
  ];
  const create = buildCapabilityCandidates(createEvents).find(item => item.transport.pathTemplate.endsWith("/submit"));
  assert.equal(create?.operation, "create");
  assert.deepEqual(create?.completion.assertions, [{ path: "$.code", kind: "equals", value: 200 }, { path: "$.data", kind: "nonempty" }]);

  const updateEvents: EvidenceEvent[] = [
    ui("ui-edit", 30, "编辑", list),
    ui("ui-save", 40, "保存", form, [{ name: "sealName", label: "印章", type: "picker", value: "合同专用章" }]),
    network(
      "net-update",
      41,
      "https://ruoyioffice.com/admin-api/oa/seal-apply-bill/save",
      "ui-save",
      { id: 91, sealId: 12, sealNo: "SZ0002", sealName: "合同专用章" },
      { code: 200, data: true, msg: "操作成功" }
    )
  ];
  const update = buildCapabilityCandidates(updateEvents).find(item => item.transport.pathTemplate.endsWith("/save"));
  assert.equal(update?.operation, "update");
});

test("one create form keeps its intent across multiple successful write requests until it returns to the list", () => {
  const list = "https://example.test/web/#/orders";
  const form = "https://example.test/web/#/orders/info?t=1";
  const events: EvidenceEvent[] = [
    ui("ui-add", 10, "新增", list),
    ui("ui-applicant", 18, "申请人", form),
    {
      ...network("net-applicant", 19, "https://example.test/api/users/options", "ui-applicant", { pageNo: 1, search: "张" }, { code: 200, data: { list: [{ id: 7, name: "张三" }] } }),
      request: {
        method: "POST",
        url: "https://example.test/api/users/options",
        resourceType: "xhr",
        headers: { "content-type": "application/json" },
        query: {},
        body: { pageNo: 1, search: "张" }
      }
    },
    ui("ui-save", 20, "保存", form),
    network("net-save", 21, "https://example.test/api/orders/save", "ui-save", { title: "合同" }, { code: 200, data: 91 }),
    ui("ui-confirm", 22, "确认提交", form),
    network("net-submit", 23, "https://example.test/api/orders/submit", "ui-confirm", { id: 91 }, { code: 200, data: true }),
    ui("ui-list-again", 24, "page", list),
    ui("ui-custom", 25, "确认申请", list),
    network("net-custom", 26, "https://example.test/api/orders/custom-action", "ui-custom", { id: 91 }, { code: 200 })
  ];
  const capabilities = buildCapabilityCandidates(events);
  assert.equal(capabilities.find(item => item.transport.pathTemplate.endsWith("/users/options"))?.operation, "query");
  assert.equal(capabilities.find(item => item.transport.pathTemplate.endsWith("/save"))?.operation, "create");
  assert.equal(capabilities.find(item => item.transport.pathTemplate.endsWith("/submit"))?.operation, "create");
  assert.equal(capabilities.find(item => item.transport.pathTemplate.endsWith("/custom-action"))?.operation, "action");
});

test("complete-field coverage keeps valid earlier submissions instead of letting a later reset erase them", () => {
  const pageUrl = "https://ruoyioffice.com/web/#/oa/seal/seal-apply-list";
  const query: CapabilityContract = {
    ...capability(network("unused", 0, "https://ruoyioffice.com/admin-api/oa/seal-apply-bill/page", "ui-full", {}, { code: 200 })),
    id: "query-seal",
    title: "查询印章申请",
    operation: "query",
    sideEffect: false,
    confirmation: { required: false },
    transport: {
      method: "GET",
      origin: "https://ruoyioffice.com",
      pathTemplate: "/admin-api/oa/seal-apply-bill/page",
      urlTemplate: "https://ruoyioffice.com/admin-api/oa/seal-apply-bill/page"
    },
    evidence: [
      { eventId: "net-full", sessionId: "ruoyi", kind: "network", at: at(11), status: 200 },
      { eventId: "net-reset", sessionId: "ruoyi", kind: "network", at: at(21), status: 200 }
    ],
    validation: { version: 2, status: "verified", checks: [] }
  };
  const queryNetwork = (id: string, second: number, correlatedUiEvidenceId: string): NetworkEvidence => ({
    id,
    kind: "network",
    sessionId: "ruoyi",
    at: at(second),
    pageUrl,
    correlatedUiEvidenceId,
    request: {
      method: "GET",
      url: "https://ruoyioffice.com/admin-api/oa/seal-apply-bill/page?pageNo=1",
      resourceType: "xhr",
      headers: {},
      query: { pageNo: 1 }
    },
    response: { status: 200, headers: {}, body: { code: 200, data: { list: [] } } }
  });
  const events: EvidenceEvent[] = [
    ui("ui-full", 10, "查询", pageUrl, [
      { name: "billCode", label: "单据编号", type: "text", value: "TEST001" },
      { name: "sealNo", label: "印章", type: "picker", value: "合同专用章" }
    ]),
    queryNetwork("net-full", 11, "ui-full"),
    ui("ui-reset", 20, "查询", pageUrl, [
      { name: "billCode", label: "单据编号", type: "text", value: "" },
      { name: "sealNo", label: "印章", type: "picker", value: "" }
    ]),
    queryNetwork("net-reset", 21, "ui-reset")
  ];
  const review = reviewCatalog([query], events, ["query"], true);
  assert.equal(review.status, "passed", review.summary);
});

test("ordinary document fields stay fillable while a select-like seal input is chosen instead of typed", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "ruoyi-form-regression-"));
  let submitCount = 0;
  const server = http.createServer((request, response) => {
    if (request.url === "/api/seal" && request.method === "POST") {
      submitCount += 1;
      response.setHeader("content-type", "application/json; charset=utf-8");
      response.end(JSON.stringify(submitCount === 1
        ? { code: 400, msg: "请求参数不正确:印章编号不能为空" }
        : { code: 200, data: 91, msg: "操作成功" }));
      return;
    }
    response.setHeader("content-type", "text/html; charset=utf-8");
    response.end(`<!doctype html><html><body>
      <form>
        <div class="form-item"><label>文件标题</label><input name="documentTitle"></div>
        <div class="form-item"><label>文件类型</label><input name="documentType"></div>
        <div class="form-item"><label>文件份数</label><input name="documentCount" type="number"></div>
        <div class="form-item"><label>印章</label><input id="seal" name="sealName" placeholder="请选择印章" value="样例-请选择印章"></div>
        <div class="form-item"><label>上传附件</label><input name="attachment" type="file"></div>
        <button id="submit" type="button">提交</button>
      </form>
      <div id="options" role="listbox" style="display:none"><div role="option" data-value="12">合同专用章</div></div>
      <script>
        const seal = document.getElementById("seal");
        const options = document.getElementById("options");
        seal.addEventListener("click", () => { options.style.display = "block"; });
        options.querySelector("[role=option]").addEventListener("click", event => {
          seal.value = event.target.textContent;
          seal.dispatchEvent(new Event("change", { bubbles: true }));
          options.style.display = "none";
        });
        document.getElementById("submit").addEventListener("click", () => fetch("/api/seal", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ sealName: seal.value })
        }));
      </script>
    </body></html>`);
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const recorder = new BrowserRecorder({
    rootDir: temporary,
    dataDir: path.join(temporary, "data"),
    recordingsDir: path.join(temporary, "data", "recordings"),
    catalogDir: path.join(temporary, "data", "catalog"),
    profileDir: path.join(temporary, "profile"),
    maxResponseBytes: 32_768,
    headless: true,
    openaiModel: "test"
  });
  try {
    await recorder.start(`http://127.0.0.1:${address.port}/`, "ruoyi-fields");
    const before: any = await recorder.control({ action: "snapshot" });
    assert.equal(before.formFields.find((field: any) => field.label === "文件标题")?.kind, "text", JSON.stringify(before.formFields));
    assert.equal(before.formFields.find((field: any) => field.label === "文件类型")?.kind, "text", JSON.stringify(before.formFields));
    assert.equal(before.formFields.find((field: any) => field.label === "文件份数")?.kind, "number", JSON.stringify(before.formFields));
    assert.equal(before.formFields.find((field: any) => field.label === "印章")?.kind, "picker", JSON.stringify(before.formFields));
    assert.equal(before.formFields.find((field: any) => field.label === "上传附件")?.skip, true, JSON.stringify(before.formFields));
    const result: any = await recorder.control({ action: "exercise-form" });
    assert.equal(result.ok, true, JSON.stringify(result));
    assert.match(String(result.formFields.find((field: any) => field.label === "文件标题")?.value || ""), /^样例-/);
    assert.equal(result.formFields.find((field: any) => field.label === "印章")?.value, "合同专用章");
    const submitted: any = await recorder.control({ action: "submit-form" });
    assert.equal(submitted.ok, true, JSON.stringify(submitted));
    assert.equal(submitted.automaticAttempts, 2, JSON.stringify(submitted));
    assert.equal(submitCount, 2);
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("submit-form continues through a newly opened confirmation dialog and records the real write", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "nested-submit-regression-"));
  let submittedOpinion = "";
  const server = http.createServer((request, response) => {
    if (request.url === "/api/submit" && request.method === "POST") {
      let raw = "";
      request.on("data", chunk => { raw += chunk; });
      request.on("end", () => {
        submittedOpinion = String(JSON.parse(raw || "{}").opinion || "");
        response.setHeader("content-type", "application/json; charset=utf-8");
        response.end(JSON.stringify({ code: 200, data: 91, msg: "操作成功" }));
      });
      return;
    }
    response.setHeader("content-type", "text/html; charset=utf-8");
    response.end(`<!doctype html><html><body>
      <form><div class="form-item"><label>文件标题</label><input name="title" value="合同"></div><button id="open" type="button">提交</button></form>
      <div id="dialog" role="dialog" style="display:none">
        <div class="form-item"><label>提交意见</label><input id="opinion" name="opinion"></div>
        <button id="confirm" type="button">确认提交</button>
      </div>
      <script>
        const dialog = document.getElementById("dialog");
        document.getElementById("open").addEventListener("click", () => { dialog.style.display = "block"; });
        document.getElementById("confirm").addEventListener("click", () => fetch("/api/submit", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ opinion: document.getElementById("opinion").value })
        }));
      </script>
    </body></html>`);
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const recorder = new BrowserRecorder({
    rootDir: temporary,
    dataDir: path.join(temporary, "data"),
    recordingsDir: path.join(temporary, "data", "recordings"),
    catalogDir: path.join(temporary, "data", "catalog"),
    profileDir: path.join(temporary, "profile"),
    maxResponseBytes: 32_768,
    headless: true,
    openaiModel: "test"
  });
  try {
    await recorder.start(`http://127.0.0.1:${address.port}/`, "nested-submit");
    const startedAt = Date.now();
    const submitted: any = await recorder.control({ action: "submit-form" });
    assert.equal(submitted.ok, true, JSON.stringify(submitted));
    assert.equal(submitted.submitStages, 2, JSON.stringify(submitted));
    assert.match(submittedOpinion, /^样例-/);
    assert.ok(Date.now() - startedAt < 9_000, `nested submit should not wait for a request timeout: ${Date.now() - startedAt}ms`);
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("generic resource chooser with several filters selects a real radio row and returns to the owning form", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "generic-resource-chooser-"));
  const server = http.createServer((_request, response) => {
    response.setHeader("content-type", "text/html; charset=utf-8");
    response.end(`<!doctype html><html><body>
      <form class="arco-form">
        <div class="arco-form-item">
          <label class="arco-form-item-label">车辆</label>
          <div class="arco-input-wrapper"><input id="vehicle" readonly placeholder="请选择车辆"></div>
        </div>
        <button type="button">搜索</button>
      </form>
      <div id="picker" class="arco-modal" role="dialog" style="display:none;position:fixed;inset:20px;background:white">
        <div class="arco-modal-title">选择车辆</div>
        <div class="arco-form-item"><label class="arco-form-item-label">车牌号</label><input placeholder="请输入车牌号"></div>
        <div class="arco-form-item"><label class="arco-form-item-label">品牌型号</label><input placeholder="请输入品牌型号"></div>
        <div class="arco-form-item"><label class="arco-form-item-label">车型</label><input role="combobox" readonly placeholder="请选择车型"></div>
        <div class="arco-form-item"><label class="arco-form-item-label">车辆分类</label><input role="combobox" readonly placeholder="请选择车辆分类"></div>
        <table><tbody><tr><td><input id="vehicle-radio" type="radio" name="vehicle-row" value="鲁AB13555"></td><td>鲁AB13555</td><td>腾势D9</td></tr></tbody></table>
        <button id="confirm" type="button">确 认</button>
      </div>
      <script>
        const field = document.getElementById("vehicle");
        const picker = document.getElementById("picker");
        field.addEventListener("click", () => { picker.style.display = "block"; });
        document.getElementById("confirm").addEventListener("click", () => {
          const selected = document.querySelector('input[name="vehicle-row"]:checked');
          if (!selected) return;
          field.value = selected.value;
          field.dispatchEvent(new Event("input", { bubbles: true }));
          field.dispatchEvent(new Event("change", { bubbles: true }));
          picker.style.display = "none";
        });
      </script>
    </body></html>`);
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const recorder = new BrowserRecorder({
    rootDir: temporary,
    dataDir: path.join(temporary, "data"),
    recordingsDir: path.join(temporary, "data", "recordings"),
    catalogDir: path.join(temporary, "data", "catalog"),
    profileDir: path.join(temporary, "profile"),
    maxResponseBytes: 32_768,
    headless: true,
    openaiModel: "test"
  });
  try {
    await recorder.start(`http://127.0.0.1:${address.port}/`, "generic-resource-chooser", undefined, ["query"], true);
    const before: any = await recorder.control({ action: "snapshot" });
    assert.equal(before.formFields.find((field: any) => field.label === "车辆")?.kind, "picker", JSON.stringify(before.formFields));
    const result: any = await recorder.control({ action: "exercise-form" });
    assert.equal(result.ok, true, JSON.stringify(result));
    assert.equal(result.scope, "page", JSON.stringify(result));
    assert.equal(result.formFields.find((field: any) => field.label === "车辆")?.value, "鲁AB13555", JSON.stringify(result.formFields));
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("complete field coverage cannot be bypassed with direct single-field actions", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "whole-form-enforcement-"));
  const server = http.createServer((_request, response) => {
    response.setHeader("content-type", "text/html; charset=utf-8");
    response.end(`<!doctype html><html><body>
      <form class="el-form">
        <div class="el-form-item"><label class="el-form-item__label">单据编号</label><input name="billCode" placeholder="请输入单据编号"></div>
        <div class="el-form-item"><label class="el-form-item__label">申请人</label><input name="applicant" placeholder="请输入申请人"></div>
        <button type="button">搜索</button>
      </form>
    </body></html>`);
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const recorder = new BrowserRecorder({
    rootDir: temporary,
    dataDir: path.join(temporary, "data"),
    recordingsDir: path.join(temporary, "data", "recordings"),
    catalogDir: path.join(temporary, "data", "catalog"),
    profileDir: path.join(temporary, "profile"),
    maxResponseBytes: 32_768,
    headless: true,
    openaiModel: "test"
  });
  try {
    await recorder.start(`http://127.0.0.1:${address.port}/`, "whole-form-enforcement", undefined, ["query"], true);
    const direct: any = await recorder.control({ action: "fill", selector: "placeholder=请输入单据编号", value: "ONLY-ONE" });
    assert.equal(direct.ok, false, JSON.stringify(direct));
    assert.equal(direct.requiresWholeForm, true, JSON.stringify(direct));
    const afterDirect: any = await recorder.control({ action: "snapshot" });
    assert.equal(afterDirect.formFields.find((field: any) => field.label === "单据编号")?.value, "", JSON.stringify(afterDirect.formFields));
    const exercised: any = await recorder.control({ action: "exercise-form" });
    assert.equal(exercised.ok, true, JSON.stringify(exercised));
    assert.equal(exercised.formFields.every((field: any) => field.skip || field.disabled || field.filled), true, JSON.stringify(exercised.formFields));
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("complete field coverage requires one whole-form pass and touches prefilled conditions", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "whole-form-prefilled-"));
  const server = http.createServer((_request, response) => {
    response.setHeader("content-type", "text/html; charset=utf-8");
    response.end(`<!doctype html><html><body>
      <form class="el-form">
        <div class="el-form-item"><label class="el-form-item__label">关键字</label><input name="keyword" value="默认关键字" placeholder="请输入关键字"></div>
        <div class="el-form-item"><label class="el-form-item__label">申请人</label><input name="applicant" value="默认申请人" placeholder="请输入申请人"></div>
        <button type="button">搜索</button>
      </form>
    </body></html>`);
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const recorder = new BrowserRecorder({
    rootDir: temporary,
    dataDir: path.join(temporary, "data"),
    recordingsDir: path.join(temporary, "data", "recordings"),
    catalogDir: path.join(temporary, "data", "catalog"),
    profileDir: path.join(temporary, "profile"),
    maxResponseBytes: 32_768,
    headless: true,
    openaiModel: "test"
  });
  try {
    await recorder.start(`http://127.0.0.1:${address.port}/`, "whole-form-prefilled", undefined, [], true);
    const before: any = await recorder.stopReadiness();
    assert.equal(before.ready, false, JSON.stringify(before));
    assert.equal(before.nextAction.action, "exercise-form", JSON.stringify(before));

    const directClick: any = await recorder.control({ action: "click", selector: "placeholder=请输入关键字" });
    assert.equal(directClick.requiresWholeForm, true, JSON.stringify(directClick));

    const exercised: any = await recorder.control({ action: "exercise-form" });
    assert.equal(exercised.ok, true, JSON.stringify(exercised));
    assert.deepEqual(new Set((exercised.filled || []).map((field: any) => field.label)), new Set(["关键字", "申请人"]));
    assert.equal(exercised.recordingAudit.ready, true, JSON.stringify(exercised));
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("menu controls use their own accessible name instead of the preceding menu item label", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "menu-selector-regression-"));
  const server = http.createServer((_request, response) => {
    response.setHeader("content-type", "text/html; charset=utf-8");
    response.end(`<!doctype html><html><body><nav role="menu">
      <a role="menuitem" href="/first">第一页</a>
      <a role="menuitem" href="/second">第二页</a>
      <a role="menuitem" href="/third">第三页</a>
      <div style="display:none"><a role="menuitem" href="/collapsed">折叠页</a></div>
    </nav>
    <header><input name="globalSearch" placeholder="全局搜索"><button type="button">搜索</button></header>
    <main><div class="table-toolbar"><button type="button">新增</button></div></main>
    <div class="pager-footer"><span>共 2 条记录</span><div role="combobox" aria-label="共 2 条记录">20条/页</div></div>
    <div data-slot="form-item"><label data-slot="form-label" for="category-control">系统分类</label>
      <div data-slot="form-control" class="ant-select" name="category"><div class="ant-select-selector">
        <input id="category-control" role="combobox" readonly><span class="ant-select-selection-placeholder">请选择系统分类</span>
      </div></div>
    </div>
    <div data-slot="form-item"><label data-slot="form-label" for="bill-control">单据编号</label>
      <div data-slot="form-control"><input id="bill-control" name="billCode" placeholder="请输入单据编号"><button type="button" class="clear-icon"></button></div>
    </div>
    </body></html>`);
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const recorder = new BrowserRecorder({
    rootDir: temporary,
    dataDir: path.join(temporary, "data"),
    recordingsDir: path.join(temporary, "data", "recordings"),
    catalogDir: path.join(temporary, "data", "catalog"),
    profileDir: path.join(temporary, "profile"),
    maxResponseBytes: 32_768,
    headless: true,
    openaiModel: "test"
  });
  try {
    await recorder.start(`http://127.0.0.1:${address.port}/`, "menu-selectors");
    const snapshot: any = await recorder.control({ action: "snapshot" });
    const menu = snapshot.controls.filter((control: any) => control.role === "menuitem");
    assert.deepEqual(menu.map((control: any) => control.selector), [
      'role=menuitem[name="第一页"]',
      'role=menuitem[name="第二页"]',
      'role=menuitem[name="第三页"]'
    ]);
    assert.deepEqual(snapshot.navigationInventory.map((item: any) => item.label), ["第一页", "第二页", "第三页", "折叠页"]);
    assert.equal(snapshot.formFields.some((field: any) => /条记录|条\/页/.test(`${field.label} ${field.value}`)), false, JSON.stringify(snapshot.formFields));
    assert.equal(snapshot.formFields.some((field: any) => field.name === "globalSearch" || field.label === "全局搜索"), false, JSON.stringify(snapshot.formFields));
    assert.equal(snapshot.formFields.some((field: any) => field.label === "系统分类" && field.kind === "select"), true, JSON.stringify(snapshot.formFields));
    assert.equal(snapshot.formFields.filter((field: any) => /单据编号/.test(field.label)).length, 1, JSON.stringify(snapshot.formFields));
    assert.equal(snapshot.availableOperations.includes("query"), false, JSON.stringify(snapshot.operationInventory));
    assert.equal(snapshot.availableOperations.includes("create"), true, JSON.stringify(snapshot.operationInventory));
    assert.equal(snapshot.operationInventory.some((item: any) => item.operation === "create" && item.label === "新增"), true, JSON.stringify(snapshot.operationInventory));
    await assert.rejects(() => recorder.control({ action: "submit-form" }), /No submit\/search button/);
    await recorder.control({ action: "fill", selector: "placeholder=请输入单据编号", value: "BILL-1" });
    const afterFill: any = await recorder.control({ action: "snapshot" });
    assert.equal(afterFill.formFields.some((field: any) => field.label === "单据编号" && field.name === "billCode"), true, JSON.stringify(afterFill.formFields));
    assert.equal(afterFill.formFields.some((field: any) => field.label === "请输入单据编号"), false, JSON.stringify(afterFill.formFields));
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("nested component form items do not duplicate the same physical date-range controls", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "nested-form-item-regression-"));
  const server = http.createServer((_request, response) => {
    response.setHeader("content-type", "text/html; charset=utf-8");
    response.end(`<!doctype html><html><body><form class="ant-form">
      <div class="ant-form-item">
        <div class="ant-form-item-label"><label>发起时间</label></div>
        <div class="ant-form-item-control">
          <div data-slot="form-item">
            <div class="ant-picker ant-picker-range" data-field="createTime">
              <input id="generated-start" placeholder="开始时间">
              <input id="generated-end" placeholder="结束时间">
            </div>
          </div>
        </div>
      </div>
      <button type="button">搜索</button>
    </form></body></html>`);
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const recorder = new BrowserRecorder({
    rootDir: temporary,
    dataDir: path.join(temporary, "data"),
    recordingsDir: path.join(temporary, "data", "recordings"),
    catalogDir: path.join(temporary, "data", "catalog"),
    profileDir: path.join(temporary, "profile"),
    maxResponseBytes: 32_768,
    headless: true,
    openaiModel: "test"
  });
  try {
    await recorder.start(`http://127.0.0.1:${address.port}/`, "nested-form-items");
    const snapshot: any = await recorder.control({ action: "snapshot" });
    const dates = snapshot.formFields.filter((field: any) => field.kind === "date");
    assert.equal(dates.length, 2, JSON.stringify(snapshot.formFields));
    assert.deepEqual(dates.map((field: any) => field.label), ["开始时间", "结束时间"]);
    assert.deepEqual(dates.map((field: any) => field.rangeIndex), [0, 1]);
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("complete page coverage follows grounded menu URLs and blocks stop until every discovered page is visited", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "page-coverage-regression-"));
  const server = http.createServer((request, response) => {
    if ((request.url || "").startsWith("/api/")) {
      response.setHeader("content-type", "application/json; charset=utf-8");
      response.end('{"code":0,"data":{"list":[],"total":0}}');
      return;
    }
    response.setHeader("content-type", "text/html; charset=utf-8");
    const current = request.url === "/second" ? "第二页" : "第一页";
    const pageKey = request.url === "/second" ? "second" : "first";
    response.end(`<!doctype html><html><head><title>${current}</title></head><body>
      <nav role="menu">
        <a role="menuitem" href="/">第一页</a>
        <a role="menuitem" href="/second">第二页</a>
      </nav>
      <main><h1>${current}</h1><button id="search">搜索</button></main>
      <script>document.getElementById("search").addEventListener("click", () => fetch("/api/${pageKey}/page?pageNo=1"));</script>
    </body></html>`);
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const recorder = new BrowserRecorder({
    rootDir: temporary,
    dataDir: path.join(temporary, "data"),
    recordingsDir: path.join(temporary, "data", "recordings"),
    catalogDir: path.join(temporary, "data", "catalog"),
    profileDir: path.join(temporary, "profile"),
    maxResponseBytes: 32_768,
    headless: true,
    openaiModel: "test"
  });
  try {
    await recorder.start(`http://127.0.0.1:${address.port}/`, "page-coverage", undefined, ["query"], false, true);
    const first: any = await recorder.control({ action: "snapshot" });
    assert.deepEqual(first.navigationCoverage, {
      discovered: 2,
      visited: 1,
      remaining: 1,
      unvisited: [{ label: "第二页", selector: 'role=menuitem[name="第二页"]', url: `http://127.0.0.1:${address.port}/second`, current: false }]
    });
    const invented: any = await recorder.control({ action: "goto", url: `http://127.0.0.1:${address.port}/invented` });
    assert.equal(invented.ok, false, JSON.stringify(invented));
    assert.equal(invented.requiresGroundedNavigation, true, JSON.stringify(invented));
    assert.equal(invented.followManualSteps, false, JSON.stringify(invented));
    assert.equal((await recorder.state() as any).url, `http://127.0.0.1:${address.port}/`);
    const blocked: any = await recorder.stopReadiness();
    assert.equal(blocked.ready, false, JSON.stringify(blocked));
    assert.equal(blocked.missingPages.length, 1, JSON.stringify(blocked));
    const firstSearch: any = await recorder.control({ action: "click", selector: 'role=button[name="搜索"]' });
    assert.equal(firstSearch.recordingAudit.ready, false, JSON.stringify(firstSearch));
    assert.equal(firstSearch.recordingAudit.nextAction.action, "next-page", JSON.stringify(firstSearch));
    assert.equal(firstSearch.recordingAudit.missingPages.length, 1, JSON.stringify(firstSearch));
    const next: any = await recorder.control({ action: "next-page" });
    assert.equal(next.ok, true, JSON.stringify(next));
    assert.equal(next.snapshot.title, "第二页", JSON.stringify(next));
    assert.equal(next.navigationCoverage.remaining, 0, JSON.stringify(next));
    const missingSecondQuery: any = await recorder.stopReadiness();
    assert.equal(missingSecondQuery.ready, false, JSON.stringify(missingSecondQuery));
    assert.deepEqual(missingSecondQuery.missingPageOperations.map((item: any) => ({ url: item.url, operations: item.operations })), [
      { url: `http://127.0.0.1:${address.port}/second`, operations: ["query"] }
    ]);
    const secondSearch: any = await recorder.control({ action: "click", selector: 'role=button[name="搜索"]' });
    assert.equal(secondSearch.recordingAudit.ready, true, JSON.stringify(secondSearch));
    assert.equal(secondSearch.recordingAudit.nextAction.action, "record-stop", JSON.stringify(secondSearch));
    const ready: any = await recorder.stopReadiness();
    assert.equal(ready.ready, true, JSON.stringify(ready));
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("a stable page snapshot clears a transient query control instead of trapping page coverage", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "transient-query-regression-"));
  const server = http.createServer((request, response) => {
    response.setHeader("content-type", "text/html; charset=utf-8");
    response.end(`<!doctype html><html><body>
      <nav role="menu"><a role="menuitem" href="/">首页</a><a role="menuitem" href="/second">第二页</a></nav>
      <main><button id="search">搜索</button><button id="hide" onclick="document.getElementById('search').remove()">切换视图</button></main>
    </body></html>`);
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const recorder = new BrowserRecorder({
    rootDir: temporary,
    dataDir: path.join(temporary, "data"),
    recordingsDir: path.join(temporary, "data", "recordings"),
    catalogDir: path.join(temporary, "data", "catalog"),
    profileDir: path.join(temporary, "profile"),
    maxResponseBytes: 32_768,
    headless: true,
    openaiModel: "test"
  });
  try {
    await recorder.start(`http://127.0.0.1:${address.port}/`, "transient-query", undefined, ["query"], false, true);
    const first: any = await recorder.control({ action: "snapshot" });
    assert.equal(first.availableOperations.includes("query"), true, JSON.stringify(first.operationInventory));
    await recorder.control({ action: "click", selector: "#hide" });
    const stable: any = await recorder.control({ action: "snapshot" });
    assert.equal(stable.availableOperations.includes("query"), false, JSON.stringify(stable.operationInventory));
    const next: any = await recorder.control({ action: "next-page" });
    assert.equal(next.ok, true, JSON.stringify(next));
    assert.match(next.target.url, /\/second$/);
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("recording start reloads one stuck application splash instead of exposing it as a clickable business page", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "startup-splash-regression-"));
  let visits = 0;
  const server = http.createServer((_request, response) => {
    response.setHeader("content-type", "text/html; charset=utf-8");
    visits += 1;
    response.end(visits === 1
      ? '<!doctype html><html><head><title>业务系统</title></head><body><div class="loading"><div class="title">业务系统加载中</div></div></body></html>'
      : '<!doctype html><html><head><title>业务系统</title></head><body><nav><a role="menuitem" href="/list">业务列表</a></nav><main><button>搜索</button></main></body></html>');
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const recorder = new BrowserRecorder({
    rootDir: temporary,
    dataDir: path.join(temporary, "data"),
    recordingsDir: path.join(temporary, "data", "recordings"),
    catalogDir: path.join(temporary, "data", "catalog"),
    profileDir: path.join(temporary, "profile"),
    maxResponseBytes: 32_768,
    headless: true,
    openaiModel: "test"
  });
  try {
    await recorder.start(`http://127.0.0.1:${address.port}/`, "startup-splash");
    const snapshot: any = await recorder.control({ action: "snapshot" });
    assert.deepEqual(snapshot.navigationInventory.map((item: any) => item.label), ["业务列表"]);
    assert.match(snapshot.text, /搜索/);
    assert.equal(snapshot.controls.some((control: any) => control.text === "业务系统加载中"), false);
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("readonly controlled date ranges are selected through the real picker and reach the query request", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "controlled-date-range-"));
  const iso = (offset: number) => {
    const date = new Date();
    date.setDate(date.getDate() + offset);
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
  };
  const dates = [iso(0), iso(1), iso(2), iso(3)];
  let query = "";
  const server = http.createServer((request, response) => {
    if ((request.url || "").startsWith("/api/page")) {
      query = request.url || "";
      response.setHeader("content-type", "application/json; charset=utf-8");
      response.end('{"code":0,"data":{"list":[],"total":0}}');
      return;
    }
    response.setHeader("content-type", "text/html; charset=utf-8");
    response.end(`<!doctype html><html><body>
      <form class="ant-form">
        <div class="ant-form-item">
          <label class="ant-form-item-label">发起时间</label>
          <div class="ant-picker ant-picker-range">
            <div class="ant-picker-input"><input id="start" readonly placeholder="开始时间"></div>
            <div class="ant-picker-input"><input id="end" readonly placeholder="结束时间"></div>
          </div>
        </div>
        <button id="search" type="button">搜索</button>
      </form>
      <div id="calendar" class="ant-picker-dropdown" style="display:none;position:fixed;inset:40px;background:white">
        <table><tbody>${dates.map(value => `<tr><td class="ant-picker-cell" title="${value}"><div class="ant-picker-cell-inner">${Number(value.slice(-2))}</div></td></tr>`).join("")}</tbody></table>
        <button id="calendar-ok" type="button">确 定</button>
      </div>
      <script>
        const state = { start: "", end: "" };
        let active = "start";
        let pending = "";
        const calendar = document.getElementById("calendar");
        const start = document.getElementById("start");
        const end = document.getElementById("end");
        document.querySelector(".ant-picker-range").addEventListener("click", event => {
          active = event.target.id === "end" ? "end" : "start";
          calendar.style.display = "block";
        });
        calendar.addEventListener("click", event => {
          const cell = event.target.closest("td[title]");
          if (!cell) return;
          pending = cell.title;
        });
        document.getElementById("calendar-ok").addEventListener("click", () => {
          state[active] = pending;
          document.getElementById(active).value = pending;
          calendar.style.display = "none";
        });
        document.getElementById("search").addEventListener("click", () => {
          if (!state.start || !state.end) { start.value = state.start; end.value = state.end; }
          fetch("/api/page?start=" + encodeURIComponent(state.start) + "&end=" + encodeURIComponent(state.end));
        });
      </script>
    </body></html>`);
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const recorder = new BrowserRecorder({
    rootDir: temporary,
    dataDir: path.join(temporary, "data"),
    recordingsDir: path.join(temporary, "data", "recordings"),
    catalogDir: path.join(temporary, "data", "catalog"),
    profileDir: path.join(temporary, "profile"),
    maxResponseBytes: 32_768,
    headless: true,
    openaiModel: "test"
  });
  try {
    await recorder.start(`http://127.0.0.1:${address.port}/`, "controlled-date-range", undefined, ["query"], true);
    const exercised: any = await recorder.control({ action: "exercise-form" });
    assert.equal(exercised.ok, true, JSON.stringify(exercised));
    const submitted: any = await recorder.control({ action: "submit-form" });
    assert.equal(submitted.ok, true, JSON.stringify(submitted));
    assert.match(query, /start=\d{4}-\d{2}-\d{2}&end=\d{4}-\d{2}-\d{2}/, query);
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("business account configuration with login-name and password fields is not treated as an application login page", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-account-login-"));
  const server = http.createServer((_request, response) => {
    response.setHeader("content-type", "text/html; charset=utf-8");
    response.end(`<!doctype html><html><head><title>邮箱账号设置</title></head><body>
      <main><h1>邮箱账号设置</h1><form class="ant-form">
        <label>邮箱地址<input name="email"></label>
        <label>登录名<input name="loginName"></label>
        <label>授权码/密码<input name="password" type="password"></label>
        <button type="button">保存</button>
      </form></main>
    </body></html>`);
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const recorder = new BrowserRecorder({
    rootDir: temporary,
    dataDir: path.join(temporary, "data"),
    recordingsDir: path.join(temporary, "data", "recordings"),
    catalogDir: path.join(temporary, "data", "catalog"),
    profileDir: path.join(temporary, "profile"),
    maxResponseBytes: 32_768,
    headless: true,
    openaiModel: "test"
  });
  try {
    await recorder.start(`http://127.0.0.1:${address.port}/mail/account`, "mail-account");
    assert.equal((await recorder.loginPageState()).detected, false);
    const snapshot: any = await recorder.control({ action: "snapshot" });
    assert.equal(snapshot.loginRequired, undefined, JSON.stringify(snapshot));
    assert.equal(snapshot.formFields.some((field: any) => /授权码|密码/.test(String(field.label || ""))), true, JSON.stringify(snapshot.formFields));
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("exercise-form fills spinbutton and labeled sort fields with numbers not 样例 text", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "numeric-sample-"));
  let posted: Record<string, unknown> | undefined;
  const server = http.createServer((request, response) => {
    if (request.url === "/api/car/create" && request.method === "POST") {
      const chunks: Buffer[] = [];
      request.on("data", chunk => chunks.push(Buffer.from(chunk)));
      request.on("end", () => {
        posted = JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
        response.setHeader("content-type", "application/json; charset=utf-8");
        response.end(JSON.stringify({ code: 0, data: 1 }));
      });
      return;
    }
    response.setHeader("content-type", "text/html; charset=utf-8");
    response.end(`<!doctype html><html><body>
      <div role="dialog">
        <label>*车牌号<input placeholder="请输入车牌号" name="carNo"></label>
        <label>车座<input role="spinbutton" placeholder="请输入车座" name="seatNum"></label>
        <div class="el-input-number"><label>显示顺序<input placeholder="请输入显示顺序" name="sort"></label></div>
        <button type="button" id="save">确认</button>
      </div>
      <script>
        document.getElementById("save").addEventListener("click", () => {
          fetch("/api/car/create", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({
              carNo: document.querySelector("[name=carNo]").value,
              seatNum: document.querySelector("[name=seatNum]").value,
              sort: document.querySelector("[name=sort]").value
            })
          });
        });
      </script>
    </body></html>`);
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const recorder = new BrowserRecorder({
    rootDir: temporary,
    dataDir: path.join(temporary, "data"),
    recordingsDir: path.join(temporary, "data", "recordings"),
    catalogDir: path.join(temporary, "data", "catalog"),
    profileDir: path.join(temporary, "profile"),
    maxResponseBytes: 32_768,
    headless: true,
    openaiModel: "test"
  });
  try {
    await recorder.start(`http://127.0.0.1:${address.port}/`, "numeric-sample", undefined, ["create"], true);
    const exercised: any = await recorder.control({ action: "exercise-form" });
    assert.equal(exercised.ok, true, JSON.stringify(exercised));
    const byLabel = (label: string) => (exercised.formFields || []).find((field: any) => field.label === label);
    assert.match(String(byLabel("*车牌号")?.value || ""), /样例/, JSON.stringify(exercised.formFields));
    assert.equal(String(byLabel("车座")?.value || ""), "1", JSON.stringify(exercised.formFields));
    assert.equal(String(byLabel("显示顺序")?.value || ""), "1", JSON.stringify(exercised.formFields));
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("exercise-form expands and fills every visible business detail section in one pass", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "work-report-repeatables-"));
  const server = http.createServer((_request, response) => {
    response.setHeader("content-type", "text/html; charset=utf-8");
    response.end(`<!doctype html><html><body>
      <form class="ant-form">
        <label>工作总结<textarea name="todayContent" placeholder="请输入工作总结"></textarea></label>
        <section aria-label="已完成工作">
          <h2>已完成工作</h2>
          <button id="add-work" type="button">添加工作项</button>
          <div id="work-rows"></div>
        </section>
        <section aria-label="工作计划">
          <h2>工作计划</h2>
          <button id="add-plan" type="button">添加计划项</button>
          <div id="plan-rows"></div>
        </section>
        <section aria-label="附件信息">
          <h2>附件信息</h2>
          <button id="upload" type="button">上传附件</button>
        </section>
        <button type="submit">保存</button>
      </form>
      <script>
        document.getElementById("add-work").addEventListener("click", () => {
          if (document.querySelector("[name='items[0].content']")) return;
          document.getElementById("work-rows").innerHTML = '<label>工作内容<input name="items[0].content" placeholder="请输入工作内容"></label>';
        });
        document.getElementById("add-plan").addEventListener("click", () => {
          if (document.querySelector("[name='items[1].content']")) return;
          document.getElementById("plan-rows").innerHTML = '<label>计划内容<input name="items[1].content" placeholder="请输入计划内容"></label>';
        });
      </script>
    </body></html>`);
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const recorder = new BrowserRecorder({
    rootDir: temporary,
    dataDir: path.join(temporary, "data"),
    recordingsDir: path.join(temporary, "data", "recordings"),
    catalogDir: path.join(temporary, "data", "catalog"),
    profileDir: path.join(temporary, "profile"),
    maxResponseBytes: 32_768,
    headless: true,
    openaiModel: "test"
  });
  try {
    await recorder.start(`http://127.0.0.1:${address.port}/`, "work-report-repeatables", undefined, ["create"], true);
    const exercised: any = await recorder.control({ action: "exercise-form" });
    assert.equal(exercised.ok, true, JSON.stringify(exercised));
    assert.equal((exercised.formFields || []).some((field: any) => field.name === "items[0].content" && field.filled), true, JSON.stringify(exercised.formFields));
    assert.equal((exercised.formFields || []).some((field: any) => field.name === "items[1].content" && field.filled), true, JSON.stringify(exercised.formFields));
    assert.equal((exercised.formFields || []).some((field: any) => /附件/.test(field.label || "")), false, JSON.stringify(exercised.formFields));
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("HTTP 200 business code 401 pauses for login immediately without consuming three repair attempts", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-login-expired-"));
  let queryCount = 0;
  const server = http.createServer((request, response) => {
    if (request.url === "/api/query" && request.method === "POST") {
      queryCount += 1;
      response.setHeader("content-type", "application/json; charset=utf-8");
      response.end(JSON.stringify({ code: 401, msg: "账号未登录" }));
      return;
    }
    response.setHeader("content-type", "text/html; charset=utf-8");
    response.end(`<!doctype html><html><body><form>
      <div class="form-item"><label>关键词</label><input name="keyword" value="合同"></div>
      <button type="button" onclick="fetch('/api/query',{method:'POST',headers:{'content-type':'application/json'},body:'{}'})">搜索</button>
    </form></body></html>`);
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const recorder = new BrowserRecorder({
    rootDir: temporary,
    dataDir: path.join(temporary, "data"),
    recordingsDir: path.join(temporary, "data", "recordings"),
    catalogDir: path.join(temporary, "data", "catalog"),
    profileDir: path.join(temporary, "profile"),
    maxResponseBytes: 32_768,
    headless: true,
    openaiModel: "test"
  });
  try {
    await recorder.start(`http://127.0.0.1:${address.port}/`, "business-login-expired");
    const result: any = await recorder.control({ action: "submit-form" });
    assert.equal(result.ok, false, JSON.stringify(result));
    assert.equal(result.stopped, true, JSON.stringify(result));
    assert.equal(result.loginRequired, true, JSON.stringify(result));
    assert.equal(result.automaticAttempts, 1, JSON.stringify(result));
    assert.match(result.reason, /登录状态失效/);
    assert.equal(queryCount, 1);
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("a transient 401 cleared by refresh-token does not pause later clicks", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "refresh-clears-login-"));
  let definitionCount = 0;
  const server = http.createServer((request, response) => {
    if (request.url === "/admin-api/bpm/process-definition/get") {
      definitionCount += 1;
      response.setHeader("content-type", "application/json; charset=utf-8");
      if (definitionCount === 1) {
        response.end(JSON.stringify({ code: 401, msg: "账号未登录" }));
        return;
      }
      response.end(JSON.stringify({ code: 0, data: { id: "bpm_1" } }));
      return;
    }
    if (request.url === "/admin-api/system/auth/refresh-token" && request.method === "POST") {
      response.setHeader("content-type", "application/json; charset=utf-8");
      response.end(JSON.stringify({ code: 0, data: { accessToken: "next" } }));
      return;
    }
    if (request.url === "/admin-api/oa/trip/page") {
      response.setHeader("content-type", "application/json; charset=utf-8");
      response.end(JSON.stringify({ code: 0, data: { list: [], total: 0 } }));
      return;
    }
    response.setHeader("content-type", "text/html; charset=utf-8");
    response.end(`<!doctype html><html><body><main>
      <button type="button" id="open" onclick="
        fetch('/admin-api/bpm/process-definition/get').then(r => r.json()).then(body => {
          if (body.code === 401) {
            return fetch('/admin-api/system/auth/refresh-token', {method:'POST'}).then(() => fetch('/admin-api/bpm/process-definition/get'));
          }
        })
      ">办理</button>
      <button type="button" id="search" onclick="fetch('/admin-api/oa/trip/page')">搜索</button>
    </main></body></html>`);
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const recorder = new BrowserRecorder({
    rootDir: temporary,
    dataDir: path.join(temporary, "data"),
    recordingsDir: path.join(temporary, "data", "recordings"),
    catalogDir: path.join(temporary, "data", "catalog"),
    profileDir: path.join(temporary, "profile"),
    maxResponseBytes: 32_768,
    headless: true,
    openaiModel: "test"
  });
  try {
    await recorder.start(`http://127.0.0.1:${address.port}/`, "refresh-clears-login");
    const first: any = await recorder.control({ action: "click", selector: 'role=button[name="办理"]' });
    assert.equal(first.loginRequired, undefined, JSON.stringify(first));
    assert.notEqual(first.stopped, true, JSON.stringify(first));
    const second: any = await recorder.control({ action: "click", selector: 'role=button[name="搜索"]' });
    assert.equal(second.loginRequired, undefined, JSON.stringify(second));
    assert.notEqual(second.stopped, true, JSON.stringify(second));
    assert.equal(definitionCount >= 2, true, `definitionCount=${definitionCount}`);
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("a direct business click also pauses immediately when its response says login expired", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "click-login-expired-"));
  let requestCount = 0;
  const server = http.createServer((request, response) => {
    if (request.url === "/api/action") {
      requestCount += 1;
      response.setHeader("content-type", "application/json; charset=utf-8");
      response.end(JSON.stringify({ code: "401", message: "登录已过期" }));
      return;
    }
    response.setHeader("content-type", "text/html; charset=utf-8");
    response.end(`<!doctype html><html><body><main>
      <button type="button" onclick="fetch('/api/action')">办理</button>
    </main></body></html>`);
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const recorder = new BrowserRecorder({
    rootDir: temporary,
    dataDir: path.join(temporary, "data"),
    recordingsDir: path.join(temporary, "data", "recordings"),
    catalogDir: path.join(temporary, "data", "catalog"),
    profileDir: path.join(temporary, "profile"),
    maxResponseBytes: 32_768,
    headless: true,
    openaiModel: "test"
  });
  try {
    await recorder.start(`http://127.0.0.1:${address.port}/`, "click-login-expired");
    const result: any = await recorder.control({ action: "click", selector: 'role=button[name="办理"]' });
    const recorded = await readFile(path.join(temporary, "data", "recordings", recorder.activeSession()!.id, "events.jsonl"), "utf8");
    assert.equal(requestCount, 1);
    assert.equal(result.stopped, true, JSON.stringify({ result, recorded }));
    assert.equal(result.loginRequired, true, JSON.stringify(result));
    assert.match(result.reason, /登录状态失效/);
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});
