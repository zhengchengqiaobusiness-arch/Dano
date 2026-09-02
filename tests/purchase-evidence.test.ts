import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import os from "node:os";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import type { EvidenceEvent } from "../src/domain.js";
import { buildCapabilityCandidates } from "../src/inference/build-candidates.js";
import { finalizeCapabilities } from "../src/inference/finalize-capabilities.js";
import { exportableCapabilities } from "../src/inference/export-scope.js";
import { exportSkill } from "../src/export/skill-exporter.js";

function purchaseEvents(): EvidenceEvent[] {
  return [{
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
    id: "ui-search-in-status", kind: "ui", sessionId: "rec-1", at: "2026-09-02T03:18:01.720Z",
    pageUrl: "http://admin.dianshixinxi.com:90/erp/purchase/order", eventType: "click",
    label: "入库数量", value: "未入库",
    visibleOptions: ["未入库", "部分入库", "全部入库"]
  }, {
    id: "ui-search-return-status", kind: "ui", sessionId: "rec-1", at: "2026-09-02T03:18:01.740Z",
    pageUrl: "http://admin.dianshixinxi.com:90/erp/purchase/order", eventType: "click",
    label: "退货数量", value: "未退货",
    visibleOptions: ["未退货", "部分退货", "全部退货"]
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
        supplierId: 12, accountId: 2, orderTime: "2026-09-01", remark: "测试采购订单",
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
  assert.equal(search.inputForm.find(field => field.name === "inStatus")?.label, "入库数量");
  assert.equal(search.inputForm.find(field => field.name === "returnStatus")?.label, "退货数量");
  assert.equal(search.inputForm.find(field => field.name === "orderTime[1]")?.label, "结束日期");
  assert.equal(search.inputForm.find(field => field.name === "inStatus")?.candidates?.type, "static");
  assert.equal(create.inputForm.find(field => field.name === "remark")?.source, "caller");
  assert.equal(create.inputForm.find(field => field.name === "supplierId")?.source, "caller");
  assert.equal(create.inputForm.find(field => field.name === "accountId")?.source, "caller");
  assert.equal(create.inputForm.find(field => field.name === "productId")?.source, "caller");
  assert.equal(create.inputForm.find(field => field.name === "count")?.source, "caller");
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
    assert.equal(result.skillName, "purchase-order");
    assert.equal(result.primaryCount, 2);
    assert.match(skill, /字段处理原则/);
    assert.match(skill, /新建 purchase\/order/);
    assert.match(skill, /查询 purchase\/order/);
    assert.match(skill, /字段候选接口/);
    assert.doesNotMatch(skill, /auth\/login|conversation\/list|get-permission-info/);
    assert.match(forms, /接口候选|页面固定枚举|后台自动/);
    assert.match(forms, /入库数量/);
    assert.match(forms, /结束日期/);
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
});
