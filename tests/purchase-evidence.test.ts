import test from "node:test";
import assert from "node:assert/strict";
import type { EvidenceEvent } from "../src/domain.js";
import { buildCapabilityCandidates } from "../src/inference/build-candidates.js";
import { validateCapability } from "../src/validation/validator.js";
import { exportableCapabilities } from "../src/inference/export-scope.js";

function purchaseEvents(): EvidenceEvent[] {
  return [{
    id: "ui-search-no", kind: "ui", sessionId: "rec-1", at: "2026-09-02T03:18:01.000Z",
    pageUrl: "http://admin.dianshixinxi.com:90/erp/purchase/order", eventType: "input",
    label: "订单单号", value: "CGDD",
    form: [{ name: "el-id-1-1", label: "订单单号", type: "text", value: "CGDD" }]
  }, {
    id: "ui-search-click", kind: "ui", sessionId: "rec-1", at: "2026-09-02T03:18:02.000Z",
    pageUrl: "http://admin.dianshixinxi.com:90/erp/purchase/order", eventType: "click",
    text: "搜索", label: "搜索"
  }, {
    id: "net-search", kind: "network", sessionId: "rec-1", at: "2026-09-02T03:18:03.000Z",
    correlatedUiEvidenceId: "ui-search-click",
    request: {
      method: "GET",
      url: "http://admin.dianshixinxi.com:90/admin-api/erp/purchase/order/page?no=CGDD&pageNo=1&pageSize=10",
      resourceType: "xhr", headers: {}, query: { no: "CGDD", pageNo: 1, pageSize: 10 }
    },
    response: { status: 200, headers: {}, body: { success: true, data: { list: [], total: 0 } } }
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
      { name: "el-id-2-9", label: "支付订金", type: "number", value: 100 }
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
        supplierId: 12, orderTime: "2026-09-01", remark: "测试采购订单",
        items: [{ productId: 3, count: 5, productPrice: 100, taxPercent: 13, amount: 500, taxPrice: 65 }],
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
  }];
}

test("search and create purchase order verify from mixed manual and recorded evidence", () => {
  const events = purchaseEvents();
  const capabilities = buildCapabilityCandidates(events);
  const search = capabilities.find(item => item.transport.pathTemplate.includes("/purchase/order/page"))!;
  const create = capabilities.find(item => item.transport.pathTemplate.includes("/purchase/order/create"))!;
  const im = capabilities.find(item => item.transport.pathTemplate.includes("/im/conversation/list"))!;
  assert.equal(search.inputForm.find(field => field.name === "no")?.source, "caller");
  assert.equal(search.inputForm.find(field => field.name === "pageNo")?.source, "fixed");
  assert.equal(create.inputForm.find(field => field.name === "remark")?.source, "caller");
  assert.equal(create.inputForm.find(field => field.name === "supplierId")?.source, "caller");
  assert.equal(create.inputForm.find(field => field.name === "productId")?.source, "caller");
  assert.equal(create.inputForm.find(field => field.name === "count")?.source, "caller");
  assert.equal(create.inputForm.find(field => field.name === "amount")?.source, "computed");
  assert.equal(validateCapability(search, events, capabilities).validation.status, "verified");
  assert.equal(validateCapability(create, events, capabilities).validation.status, "verified");
  const verified = capabilities.map(item => validateCapability(item, events, capabilities));
  const exported = exportableCapabilities(verified);
  assert.equal(exported.some(item => item.id === search.id || item.transport.pathTemplate.includes("/purchase/order/page")), true);
  assert.equal(exported.some(item => item.transport.pathTemplate.includes("/purchase/order/create")), true);
  assert.equal(exported.some(item => item.id === im.id || item.transport.pathTemplate.includes("/im/conversation")), false);
});
