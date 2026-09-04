import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { mkdtemp, rm } from "node:fs/promises";
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
  assert.equal(recordingStopReadiness(evidence, ["create"]).ready, true);
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
