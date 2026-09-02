import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import os from "node:os";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import type { EvidenceEvent } from "../src/domain.js";

const execFileAsync = promisify(execFile);
import { buildCapabilityCandidates } from "../src/inference/build-candidates.js";
import { finalizeCapabilities } from "../src/inference/finalize-capabilities.js";
import { exportableCapabilities } from "../src/inference/export-scope.js";
import { relatedEvidence } from "../src/inference/related-evidence.js";
import { exportSkill } from "../src/export/skill-exporter.js";

function purchaseEvents(): EvidenceEvent[] {
  return [{
    id: "ui-page-snapshot", kind: "ui", sessionId: "rec-1", at: "2026-09-02T03:17:50.000Z",
    pageUrl: "http://admin.dianshixinxi.com:90/erp/purchase/order", eventType: "snapshot",
    form: [
      { name: "no", label: "订单单号", type: "text" },
      { name: "productId", label: "产品", type: "select" },
      { label: "开始日期", type: "text" },
      { label: "结束日期", type: "text" },
      { name: "supplierId", label: "供应商", type: "select" },
      { name: "creator", label: "创建人", type: "select" },
      { name: "status", label: "状态", type: "select" },
      { name: "remark", label: "备注", type: "textarea" },
      { name: "inStatus", label: "入库数量", type: "select" },
      { name: "returnStatus", label: "退货数量", type: "select" }
    ]
  }, {
    id: "ui-search-no", kind: "ui", sessionId: "rec-1", at: "2026-09-02T03:18:01.000Z",
    pageUrl: "http://admin.dianshixinxi.com:90/erp/purchase/order", eventType: "input",
    label: "订单单号", value: "CGDD",
    form: [{ name: "el-id-1-1", label: "订单单号", type: "text", value: "CGDD" }]
  }, {
    id: "ui-search-product", kind: "ui", sessionId: "rec-1", at: "2026-09-02T03:18:01.400Z",
    pageUrl: "http://admin.dianshixinxi.com:90/erp/purchase/order", eventType: "click",
    label: "产品", value: "苹果电脑",
    visibleOptions: ["苹果电脑", "华为matebook14"]
  }, {
    id: "ui-search-status", kind: "ui", sessionId: "rec-1", at: "2026-09-02T03:18:01.700Z",
    pageUrl: "http://admin.dianshixinxi.com:90/erp/purchase/order", eventType: "click",
    label: "状态", value: "未审核",
    visibleOptions: ["未审核", "已审核"]
  }, {
    id: "ui-search-status-span", kind: "ui", sessionId: "rec-1", at: "2026-09-02T03:18:01.710Z",
    pageUrl: "http://admin.dianshixinxi.com:90/erp/purchase/order", eventType: "click",
    text: "请选择状态",
    visibleOptions: ["未审核", "已审核"]
  }, {
    id: "ui-search-in-status", kind: "ui", sessionId: "rec-1", at: "2026-09-02T03:18:01.720Z",
    pageUrl: "http://admin.dianshixinxi.com:90/erp/purchase/order", eventType: "click",
    label: "入库数量", value: "未入库",
    visibleOptions: ["未入库", "部分入库", "全部入库"]
  }, {
    id: "ui-search-return-status", kind: "ui", sessionId: "rec-1", at: "2026-09-02T03:18:01.740Z",
    pageUrl: "http://admin.dianshixinxi.com:90/erp/purchase/order", eventType: "click",
    label: "退货数量", value: "未退货",
    visibleOptions: ["未退货", "部分退货", "全部退货"],
    form: [
      { name: "el-id-1-1", label: "订单单号", type: "text", value: "CGDD" },
      { name: "el-id-1-2", label: "备注", type: "textarea", value: "" },
      { name: "el-id-1-3", label: "开始日期", type: "text", value: "2026-09-01" },
      { name: "el-id-1-4", label: "退货数量", type: "select", value: "未退货" }
    ]
  }, {
    id: "ui-search-start", kind: "ui", sessionId: "rec-1", at: "2026-09-02T03:18:01.760Z",
    pageUrl: "http://admin.dianshixinxi.com:90/erp/purchase/order", eventType: "input",
    label: "开始日期", value: "2026-09-01"
  }, {
    id: "ui-search-end", kind: "ui", sessionId: "rec-1", at: "2026-09-02T03:18:01.780Z",
    pageUrl: "http://admin.dianshixinxi.com:90/erp/purchase/order", eventType: "input",
    label: "结束日期", value: "2026-09-02"
  }, {
    id: "ui-search-click", kind: "ui", sessionId: "rec-1", at: "2026-09-02T03:18:02.000Z",
    pageUrl: "http://admin.dianshixinxi.com:90/erp/purchase/order", eventType: "click",
    text: "搜索", label: "搜索"
  }, {
    id: "net-search-empty", kind: "network", sessionId: "rec-1", at: "2026-09-02T03:18:00.500Z",
    request: {
      method: "GET",
      url: "http://admin.dianshixinxi.com:90/admin-api/erp/purchase/order/page?pageNo=1&pageSize=10",
      resourceType: "xhr", headers: {}, query: { pageNo: 1, pageSize: 10 }
    },
    response: { status: 200, headers: {}, body: { success: true, data: { list: [], total: 0 } } }
  }, {
    id: "net-search", kind: "network", sessionId: "rec-1", at: "2026-09-02T03:18:03.000Z",
    correlatedUiEvidenceId: "ui-search-click",
    request: {
      method: "GET",
      url: "http://admin.dianshixinxi.com:90/admin-api/erp/purchase/order/page?no=CGDD&productId=3&status=10&inStatus=0&returnStatus=0&orderTime[0]=2026-09-01&orderTime[1]=2026-09-02&pageNo=1&pageSize=10",
      resourceType: "xhr", headers: {}, query: {
        no: "CGDD", productId: 3, status: 10, inStatus: 0, returnStatus: 0,
        "orderTime[0]": "2026-09-01", "orderTime[1]": "2026-09-02", pageNo: 1, pageSize: 10
      }
    },
    response: { status: 200, headers: {}, body: { success: true, data: { list: [], total: 0 } } }
  }, {
    id: "net-product", kind: "network", sessionId: "rec-1", at: "2026-09-02T03:18:01.500Z",
    correlatedUiEvidenceId: "ui-search-product",
    request: {
      method: "GET", url: "http://admin.dianshixinxi.com:90/admin-api/erp/product/simple-list",
      resourceType: "xhr", headers: {}, query: {}
    },
    response: { status: 200, headers: {}, body: { success: true, data: [{ id: 3, name: "苹果电脑" }] } }
  }, {
    id: "net-supplier", kind: "network", sessionId: "rec-1", at: "2026-09-02T03:18:01.600Z",
    request: {
      method: "GET", url: "http://admin.dianshixinxi.com:90/admin-api/erp/supplier/simple-list",
      resourceType: "xhr", headers: {}, query: {}
    },
    response: { status: 200, headers: {}, body: { success: true, data: [{ id: 12, name: "泉源鱼家" }] } }
  }, {
    id: "net-account", kind: "network", sessionId: "rec-1", at: "2026-09-02T03:19:11.000Z",
    request: {
      method: "GET", url: "http://admin.dianshixinxi.com:90/admin-api/erp/account/simple-list",
      resourceType: "xhr", headers: {}, query: {}
    },
    response: { status: 200, headers: {}, body: { success: true, data: [{ id: 2, name: "公司基本户" }] } }
  }, {
    id: "ui-create-form", kind: "ui", sessionId: "rec-1", at: "2026-09-02T03:19:10.000Z",
    pageUrl: "http://admin.dianshixinxi.com:90/erp/purchase/order", eventType: "input",
    label: "备注", value: "测试采购订单",
    form: [
      { name: "el-id-2-1", label: "订单时间", type: "text", value: "2026-09-01" },
      { name: "el-id-2-2", label: "供应商", type: "text", value: "泉源鱼家" },
      { name: "el-id-2-3", label: "备注", type: "textarea", value: "测试采购订单" },
      { name: "el-id-2-4", label: "产品名称", type: "text", value: "苹果电脑" },
      { name: "el-id-2-5", label: "数量", type: "number", value: 5 },
      { name: "el-id-2-6", label: "产品单价", type: "number", value: 100 },
      { name: "el-id-2-7", label: "税率", type: "number", value: 13 },
      { name: "el-id-2-8", label: "优惠率", type: "number", value: 5 },
      { name: "el-id-2-9", label: "支付订金", type: "number", value: 100 },
      { name: "el-id-2-10", label: "结算账户", type: "text", value: "公司基本户" },
      { name: "el-id-2-11", label: "单位", type: "text", value: "台" },
      { name: "el-id-2-12", label: "条码", type: "text", value: "0101010101" },
      { name: "el-id-2-13", label: "库存", type: "number", value: 925.5 }
    ]
  }, {
    id: "ui-create-submit", kind: "ui", sessionId: "rec-1", at: "2026-09-02T03:19:12.000Z",
    pageUrl: "http://admin.dianshixinxi.com:90/erp/purchase/order", eventType: "click",
    text: "确定", label: "确定"
  }, {
    id: "net-create", kind: "network", sessionId: "rec-1", at: "2026-09-02T03:19:13.000Z",
    correlatedUiEvidenceId: "ui-create-submit",
    request: {
      method: "POST",
      url: "http://admin.dianshixinxi.com:90/admin-api/erp/purchase/order/create",
      resourceType: "xhr", headers: {}, query: {},
      body: {
        supplierId: 12, accountId: 2, orderTime: 1788192000000, remark: "测试采购订单",
        items: [{
          productId: 3, productUnitName: "台", productBarCode: "0101010101", stockCount: 925.5,
          count: 5, productPrice: 100, taxPercent: 13, amount: 500, taxPrice: 65, totalProductPrice: 500, totalPrice: 565
        }],
        discountPercent: 5, depositPrice: 100, discountPrice: 28.25, totalPrice: 536.75
      }
    },
    response: { status: 200, headers: {}, body: { success: true, data: 88 } }
  }, {
    id: "net-im", kind: "network", sessionId: "rec-1", at: "2026-09-02T03:18:04.000Z",
    request: {
      method: "GET", url: "http://admin.dianshixinxi.com:90/admin-api/im/conversation/list",
      resourceType: "xhr", headers: {}, query: {}
    },
    response: { status: 200, headers: {}, body: { success: true, data: [] } }
  }, {
    id: "net-login", kind: "network", sessionId: "rec-1", at: "2026-09-02T03:10:00.000Z",
    request: {
      method: "POST", url: "http://admin.dianshixinxi.com:90/admin-api/system/auth/login",
      resourceType: "xhr", headers: {}, query: {},
      body: { username: "admin", password: "x" }
    },
    response: { status: 200, headers: {}, body: { success: true, data: { accessToken: "t" } } }
  }];
}

test("search and create purchase order verify from mixed manual and recorded evidence", () => {
  const events = purchaseEvents();
  const capabilities = buildCapabilityCandidates(events);
  const search = capabilities.find(item => item.transport.pathTemplate.includes("/purchase/order/page"))!;
  const create = capabilities.find(item => item.transport.pathTemplate.includes("/purchase/order/create"))!;
  const im = capabilities.find(item => item.transport.pathTemplate.includes("/im/conversation/list"))!;
  const searchCaps = capabilities.filter(item => item.transport.pathTemplate.includes("/purchase/order/page"));
  assert.equal(searchCaps.length, 1);
  assert.match(search.title, /查询/);
  assert.match(create.title, /新建/);
  assert.equal(search.inputForm.find(field => field.name === "no")?.source, "caller");
  assert.equal(search.inputForm.find(field => field.name === "pageNo")?.source, "system");
  assert.equal(search.inputForm.find(field => field.name === "pageNo")?.required, false);
  assert.equal(search.inputForm.find(field => field.name === "status")?.candidates?.type, "static");
  assert.equal(search.inputForm.find(field => field.name === "status")?.candidates?.values?.find(item => item.label === "未审核")?.value, 10);
  assert.equal(search.inputForm.find(field => field.name === "inStatus")?.label, "入库数量");
  assert.equal(search.inputForm.find(field => field.name === "returnStatus")?.label, "退货数量");
  assert.equal(search.inputForm.find(field => field.name === "inStatus")?.candidates?.values?.find(item => item.label === "未入库")?.value, 0);
  assert.equal(search.inputForm.find(field => field.name === "returnStatus")?.candidates?.values?.find(item => item.label === "未退货")?.value, 0);
  assert.notEqual(search.inputForm.find(field => field.name === "no")?.candidates?.type, "static");
  assert.notEqual(search.inputForm.find(field => field.name === "remark")?.candidates?.type, "static");
  assert.notEqual(search.inputForm.find(field => field.name === "orderTime[0]")?.candidates?.type, "static");
  assert.notEqual(search.inputForm.find(field => field.name === "no")?.widget, "select");
  assert.equal(search.inputForm.find(field => field.name === "status")?.widget, "select");
  assert.equal(search.inputForm.find(field => field.name === "orderTime[1]")?.label, "结束日期");
  assert.equal(search.inputForm.find(field => field.name === "inStatus")?.candidates?.type, "static");
  assert.equal(create.inputForm.find(field => field.name === "remark")?.source, "caller");
  assert.equal(create.inputForm.find(field => field.name === "supplierId")?.source, "caller");
  assert.equal(create.inputForm.find(field => field.name === "accountId")?.source, "caller");
  assert.equal(create.inputForm.find(field => field.name === "productId")?.source, "caller");
  assert.equal(create.inputForm.find(field => field.name === "count")?.source, "caller");
  assert.equal(create.inputForm.find(field => field.name === "count")?.label, "数量");
  assert.equal(create.inputForm.find(field => field.name === "productPrice")?.label, "产品单价");
  assert.notEqual(create.inputForm.find(field => field.name === "count")?.label, "入库数量");
  assert.notEqual(create.inputForm.find(field => field.name === "productPrice")?.candidates?.type, "capability");
  assert.equal(create.inputForm.find(field => field.name === "amount")?.source, "computed");
  assert.equal(create.inputForm.find(field => field.name === "totalPrice")?.source, "computed");
  assert.equal(create.inputForm.find(field => field.name === "productUnitName")?.source, "system");
  assert.equal(create.inputForm.find(field => field.name === "productBarCode")?.source, "system");
  assert.equal(create.inputForm.find(field => field.name === "stockCount")?.source, "system");
  assert.equal(create.inputForm.find(field => field.name === "productId")?.defaultRule, undefined);
  const verified = finalizeCapabilities(capabilities, events);
  const verifiedSearch = verified.find(item => item.transport.pathTemplate.includes("/purchase/order/page"))!;
  const verifiedCreate = verified.find(item => item.transport.pathTemplate.includes("/purchase/order/create"))!;
  assert.equal(verifiedSearch.validation.status, "verified");
  assert.equal(verifiedCreate.validation.status, "verified");
  assert.equal(verifiedSearch.inputForm.find(field => field.name === "productId")?.candidates?.type, "capability");
  assert.equal(verifiedCreate.inputForm.find(field => field.name === "supplierId")?.candidates?.type, "capability");
  assert.equal(verifiedCreate.inputForm.find(field => field.name === "productId")?.candidates?.type, "capability");
  assert.notEqual(verifiedCreate.inputForm.find(field => field.name === "productPrice")?.candidates?.type, "capability");
  assert.equal(verifiedCreate.inputForm.find(field => field.name === "productPrice")?.widget, "number");
  assert.equal(verifiedCreate.inputForm.find(field => field.name === "discountPrice")?.label, "付款优惠");
  assert.equal(verifiedCreate.inputForm.find(field => field.name === "totalProductPrice")?.label, "金额");
  const exported = exportableCapabilities(verified);
  assert.equal(exported.some(item => item.transport.pathTemplate.includes("/purchase/order/page")), true);
  assert.equal(exported.some(item => item.transport.pathTemplate.includes("/purchase/order/create")), true);
  assert.equal(exported.some(item => item.transport.pathTemplate.includes("/product/simple-list")), true);
  assert.equal(exported.some(item => item.transport.pathTemplate.includes("/supplier/simple-list")), true);
  assert.equal(exported.some(item => item.transport.pathTemplate.includes("/account/simple-list")), true);
  assert.equal(exported.some(item => item.transport.pathTemplate.includes("/stock/get-count")), false);
  assert.equal(exported.filter(item => ["query", "create"].includes(item.operation) && item.transport.pathTemplate.includes("/purchase/order")).length, 2);
  assert.equal(exported.some(item => item.id === im.id || item.transport.pathTemplate.includes("/im/conversation")), false);
  assert.equal(exported.some(item => item.transport.pathTemplate.includes("/auth/login")), false);
});

test("create form filled minutes before submit still verifies", () => {
  const events = purchaseEvents().map(event => {
    if (event.id !== "ui-create-form") return event;
    return { ...event, at: "2026-09-02T03:17:10.000Z" };
  });
  const verified = finalizeCapabilities(buildCapabilityCandidates(events), events);
  const create = verified.find(item => item.transport.pathTemplate.includes("/purchase/order/create"))!;
  assert.equal(create.validation.status, "verified");
  assert.equal(create.inputForm.find(field => field.name === "productId")?.source, "caller");
  assert.equal(create.inputForm.find(field => field.name === "count")?.source, "caller");
});

test("exported purchase skill keeps API candidates and omits background polls", async () => {
  const events = purchaseEvents();
  const verified = finalizeCapabilities(buildCapabilityCandidates(events), events);
  const temporary = await mkdtemp(path.join(os.tmpdir(), "purchase-skill-"));
  try {
    const result = await exportSkill(temporary, "采购订单管理", verified);
    const skill = await readFile(path.join(result.dir, "SKILL.md"), "utf8");
    const forms = await readFile(path.join(result.dir, "references", "INPUT_FORMS.md"), "utf8");
    const options = await readFile(path.join(result.dir, "references", "OPTIONS.md"), "utf8");
    const capabilities = await readFile(path.join(result.dir, "references", "CAPABILITIES.md"), "utf8");
    const playbook = await readFile(path.join(result.dir, "references", "PLAYBOOK.md"), "utf8");
    assert.equal(result.skillName, "purchase-order");
    assert.equal(result.primaryCount, 2);
    assert.match(skill, /新建采购订单/);
    assert.match(skill, /查询采购订单/);
    assert.match(skill, /字段候选/);
    assert.match(skill, /前置/);
    assert.match(skill, /SKILL_AUTH_HEADERS/);
    assert.match(skill, /何时使用/);
    assert.match(skill, /何时不要使用/);
    assert.match(skill, /## 路由/);
    assert.match(skill, /能力怎么组合/);
    assert.match(skill, /何时走哪条原子操作/);
    assert.match(skill, /失败处理/);
    assert.match(skill, /ask_user_question/);
    assert.match(skill, /用户：「/);
    assert.doesNotMatch(skill, /auth\/login|conversation\/list|get-permission-info/);
    assert.doesNotMatch(skill, /产品单价[\s\S]{0,80}product\/simple-list/);
    assert.doesNotMatch(skill, /订单单号[\s\S]{0,80}未退货/);
    assert.doesNotMatch(skill, /订单时间[\s\S]{0,80}泉源鱼家/);
    assert.doesNotMatch(skill, /### 查询采购订单[\s\S]*参数名/);
    assert.doesNotMatch(skill, /net_mtjq|rec_mtjq|生成器实现|TypeScript|执行器/);
    assert.doesNotMatch(capabilities, /## 查询产品名称/);
    assert.match(forms, /接口候选|页面固定枚举|后台自动|dataSource/);
    assert.match(forms, /入库数量/);
    assert.match(forms, /结束日期/);
    assert.match(forms, /YYYY-MM-DD/);
    assert.match(forms, /数量/);
    assert.doesNotMatch(forms, /执行器/);
    assert.match(options, /dataSource/);
    assert.match(options, /product\/simple-list/);
    assert.match(options, /resultPath/);
    assert.match(options, /不是独立业务操作/);
    assert.match(playbook, /规划例子/);
    assert.match(playbook, /无数据/);
    assert.match(playbook, /format_list/);
    const contract = JSON.parse(await readFile(path.join(result.dir, "references", "CONTRACT.json"), "utf8"));
    assert.equal(contract.generatedAt, undefined);
    assert.equal(contract.capabilities.every((item: { confidence?: unknown; evidence?: unknown }) =>
      item.confidence === undefined && item.evidence === undefined
    ), true);
    const query = contract.capabilities.find((item: { operation: string; transport: { pathTemplate: string } }) =>
      item.operation === "query" && item.transport.pathTemplate.includes("/purchase/order/page")
    );
    const create = contract.capabilities.find((item: { operation: string }) => item.operation === "create");
    const statusField = query.inputForm.find((field: { name: string }) => field.name === "status");
    assert.equal(statusField?.candidates?.type, "static", JSON.stringify(statusField));
    const statusValue = statusField?.candidates?.values?.find((item: { label: string }) => item.label === "未审核")?.value;
    assert.equal(statusValue, 10, JSON.stringify(statusField?.candidates));
    const queryPrepared = await execFileAsync("python", [
      path.join(result.dir, "scripts", "execute.py"),
      "--capability", query.id,
      "--input", JSON.stringify({ "orderTime[0]": "2026-09-01", "orderTime[1]": "2026-09-02", status: "未审核" }),
      "--prepare-only"
    ]);
    const queryBody = JSON.parse(queryPrepared.stdout);
    assert.match(queryBody.url, /orderTime%5B0%5D=2026-09-01\+00%3A00%3A00/);
    assert.equal(queryBody.prepared.status, 10);
    const createPrepared = await execFileAsync("python", [
      path.join(result.dir, "scripts", "execute.py"),
      "--capability", create.id,
      "--input", JSON.stringify({
        supplierId: 12, accountId: 2, orderTime: "2026-09-02", remark: "测试",
        discountPercent: 5, depositPrice: 100, productId: 3, count: 5, productPrice: 100, taxPercent: 13
      }),
      "--prepare-only"
    ]);
    const createBody = JSON.parse(createPrepared.stdout);
    assert.equal(createBody.prepared.items[0].productId, 3);
    assert.equal(createBody.prepared.items[0].count, 5);
    assert.equal(typeof createBody.prepared.orderTime, "number");
    assert.equal(createBody.prepared.orderTime, 1788278400000);
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
});

test("same-page recordings complete query date fields missing from the latest search", () => {
  const latest = purchaseEvents().filter(event => event.sessionId === "rec-1");
  const extra: EvidenceEvent[] = [{
    id: "net-search-dates", kind: "network", sessionId: "rec-old", at: "2026-09-01T03:18:03.000Z",
    request: {
      method: "GET",
      url: "http://admin.dianshixinxi.com:90/admin-api/erp/purchase/order/page?pageNo=1&pageSize=10&orderTime[0]=2026-09-01 00:00:00&orderTime[1]=2026-09-30 00:00:00",
      resourceType: "xhr", headers: {},
      query: { pageNo: 1, pageSize: 10, "orderTime[0]": "2026-09-01 00:00:00", "orderTime[1]": "2026-09-30 00:00:00" }
    },
    response: { status: 200, headers: {}, body: { success: true, data: { list: [], total: 0 } } }
  }, {
    id: "ui-old-page", kind: "ui", sessionId: "rec-old", at: "2026-09-01T03:18:01.000Z",
    pageUrl: "http://admin.dianshixinxi.com:90/erp/purchase/order", eventType: "input",
    label: "开始日期", value: "2026-09-01"
  }];
  const merged = relatedEvidence(latest, [...latest, ...extra]);
  const search = buildCapabilityCandidates(merged).find(item => item.transport.pathTemplate.includes("/purchase/order/page"))!;
  assert.equal(search.inputForm.some(field => field.name === "orderTime[0]"), true);
  assert.equal(search.inputForm.find(field => field.name === "orderTime[0]")?.label, "开始日期");
});
