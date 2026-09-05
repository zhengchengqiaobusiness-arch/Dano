import test from "node:test";
import assert from "node:assert/strict";
import type { EvidenceEvent } from "../src/domain.js";
import { buildCapabilityCandidates } from "../src/inference/build-candidates.js";
import { validateCapability } from "../src/validation/validator.js";

const PAGE = "https://example.test/web/#/oa/supply/supply-apply-info";
const LIST_URL = "https://example.test/admin-api/oa/supply/page?pageNo=1&pageSize=20";
const SUBMIT_URL = "https://example.test/admin-api/oa/supply-apply-bill/submit";

const form = [
  { label: "*领用日期", type: "date", value: "2026-09-05" },
  { name: "useType", label: "*使用类型", type: "select", value: "个人使用", options: [{ value: 1, label: "个人使用" }] },
  { name: "pickupMethod", label: "*领取方式", type: "select", value: "自取", options: [{ value: 1, label: "自取" }] },
  { name: "applyReason", label: "*申请事由", type: "textarea", value: "日常办公用品领用" },
  { name: "remark", label: "备注", type: "textarea", value: "" }
];

function chooserEvents(): EvidenceEvent[] {
  return [{
    id: "ui-add", kind: "ui", sessionId: "chooser", at: "2026-09-05T16:33:47.000Z",
    pageUrl: PAGE, eventType: "click", selector: 'role=button[name="添加办公用品"]',
    tag: "button", text: "添加办公用品", label: "添加办公用品", inputType: "button",
    scope: "page", value: "", form
  }, {
    id: "net-list", kind: "network", sessionId: "chooser", at: "2026-09-05T16:33:47.200Z",
    pageUrl: PAGE, correlatedUiEvidenceId: "ui-add",
    request: { method: "GET", url: LIST_URL, resourceType: "xhr", headers: {}, query: { pageNo: "1", pageSize: "20" } },
    response: {
      status: 200, headers: {},
      body: { code: 0, data: { list: [{ id: 33, name: "打印机", managementType: 2, stockQuantity: 0 }], total: 1 } }
    }
  }, {
    id: "ui-row", kind: "ui", sessionId: "chooser", at: "2026-09-05T16:33:47.400Z",
    pageUrl: PAGE, eventType: "click", selector: "label=打印机 借用品 0",
    tag: "span", text: "", label: "打印机 借用品 0", scope: "dialog", value: "", form: []
  }, {
    id: "ui-reason", kind: "ui", sessionId: "chooser", at: "2026-09-05T16:33:52.000Z",
    pageUrl: PAGE, eventType: "change", name: "applyReason", label: "*申请事由",
    tag: "textarea", value: "日常办公用品领用", form
  }, {
    id: "ui-submit", kind: "ui", sessionId: "chooser", at: "2026-09-05T16:34:16.000Z",
    pageUrl: PAGE, eventType: "click", selector: 'role=button[name="确认提交"]',
    tag: "button", text: "确认提交", label: "确认提交", form
  }, {
    id: "net-submit", kind: "network", sessionId: "chooser", at: "2026-09-05T16:34:16.200Z",
    pageUrl: PAGE, correlatedUiEvidenceId: "ui-submit",
    request: {
      method: "POST", url: SUBMIT_URL, resourceType: "xhr", headers: {}, query: {},
      body: {
        createTime: "2026-09-05T16:33:38.812Z",
        applyDate: "2026-09-05",
        useType: 1,
        pickupMethod: 1,
        applyReason: "日常办公用品领用",
        remark: "",
        items: [{ supplyId: 33, supplyName: "打印机", applyQuantity: 1 }]
      }
    },
    response: { status: 200, headers: {}, body: { code: 0, data: 14 } }
  }];
}

test("a process comment leftover does not steal creator, and a chooser display promotes the row id", () => {
  const page = "https://example.test/web/#/oa/trip/trip-reimburse-info";
  const recorded: EvidenceEvent[] = [{
    id: "net-page", kind: "network", sessionId: "reimburse", at: "2026-09-05T17:37:00.000Z",
    pageUrl: "https://example.test/web/#/oa/trip/trip-reimburse-list",
    request: {
      method: "GET",
      url: "https://example.test/admin-api/oa/travel-reimburse/page?pageNo=1&pageSize=20&creator=1",
      resourceType: "xhr", headers: {},
      query: { pageNo: "1", pageSize: "20", creator: "1" }
    },
    response: { status: 200, headers: {}, body: { code: 0, data: { list: [], total: 0 } } }
  }, {
    id: "net-approved", kind: "network", sessionId: "reimburse", at: "2026-09-05T17:38:10.000Z",
    pageUrl: page,
    request: { method: "GET", url: "https://example.test/admin-api/oa/business-trip/get-approved-list", resourceType: "xhr", headers: {}, query: {} },
    response: {
      status: 200, headers: {},
      body: { code: 0, data: [{ id: 12, billCode: "OA109-2026090600003", creator: "1", reimburseStatus: 0, totalDays: 2 }] }
    }
  }, {
    id: "net-dict", kind: "network", sessionId: "reimburse", at: "2026-09-05T17:38:11.000Z",
    pageUrl: page,
    request: { method: "GET", url: "https://example.test/admin-api/system/dict-data/type?type=bpm_process_instance_result", resourceType: "xhr", headers: {}, query: { type: "bpm_process_instance_result" } },
    response: {
      status: 200, headers: {},
      body: { code: 0, data: [{ dictValue: 1, dictLabel: "同意" }, { dictValue: 2, dictLabel: "不同意" }] }
    }
  }, {
    id: "ui-form", kind: "ui", sessionId: "reimburse", at: "2026-09-05T17:38:40.000Z",
    pageUrl: page, eventType: "change",
    form: [
      { name: "tripBillCode", label: "关联出差申请", type: "text", value: "OA109-2026090600003" },
      { name: "tripReason", label: "*出差事由", type: "textarea", value: "样例-请输入出差事由" }
    ]
  }, {
    id: "ui-comment", kind: "ui", sessionId: "reimburse", at: "2026-09-05T17:39:40.000Z",
    pageUrl: page, eventType: "input", name: "form_item_reason", label: "提交意见",
    tag: "textarea", inputType: "textarea", value: "同意",
    form: [{ name: "form_item_reason", label: "提交意见", type: "textarea", value: "同意" }]
  }, {
    id: "ui-save", kind: "ui", sessionId: "reimburse", at: "2026-09-05T17:39:49.000Z",
    pageUrl: page, eventType: "click", text: "保存", label: "保存", tag: "button", role: "button",
    form: [
      { name: "tripBillCode", label: "关联出差申请", type: "text", value: "OA109-2026090600003" },
      { name: "tripReason", label: "*出差事由", type: "textarea", value: "样例-请输入出差事由" }
    ]
  }, {
    id: "net-save", kind: "network", sessionId: "reimburse", at: "2026-09-05T17:39:49.200Z",
    pageUrl: page, correlatedUiEvidenceId: "ui-save",
    request: {
      method: "POST", url: "https://example.test/admin-api/oa/travel-reimburse/save",
      resourceType: "xhr", headers: {}, query: {},
      body: {
        creator: "1",
        attachments: [],
        tripId: 12,
        tripBillCode: "OA109-2026090600003",
        tripReason: "样例-请输入出差事由"
      }
    },
    response: { status: 200, headers: {}, body: { code: 0, data: 7 } }
  }];

  const catalog = buildCapabilityCandidates(recorded);
  const create = catalog.find(item => item.transport.pathTemplate.endsWith("/travel-reimburse/save"))!;
  const query = catalog.find(item => item.transport.pathTemplate.endsWith("/travel-reimburse/page"))!;
  const byName = (name: string) => create.inputForm.find(field => field.name === name);
  assert.notEqual(byName("creator")?.label, "提交意见", JSON.stringify(byName("creator")));
  assert.notEqual(byName("creator")?.source, "caller", JSON.stringify(byName("creator")));
  assert.equal(byName("tripId")?.source, "caller", JSON.stringify(byName("tripId")));
  assert.notEqual(byName("tripBillCode")?.source, "caller", JSON.stringify(byName("tripBillCode")));
  assert.equal(byName("attachments")?.source, "system", JSON.stringify(byName("attachments")));
  assert.equal(byName("attachments")?.defaultRule, "literal:[]", JSON.stringify(byName("attachments")));
  const queryCreator = query.inputForm.find(field => field.name === "creator");
  assert.notEqual(queryCreator?.source, "binding", JSON.stringify(queryCreator));
});

test("a dialog row click backs the write collection id and does not ask for a second createTime", () => {
  const catalog = buildCapabilityCandidates(chooserEvents());
  const create = catalog.find(item => item.transport.pathTemplate.endsWith("/oa/supply-apply-bill/submit"));
  assert.ok(create, catalog.map(item => item.id).join(","));
  const byName = (name: string) => create!.inputForm.find(field => field.name === name);

  assert.equal(byName("applyDate")?.source, "caller");
  assert.equal(byName("createTime")?.source, "system");
  assert.equal(byName("createTime")?.defaultRule, "now:iso");
  assert.equal(byName("supplyId")?.source, "caller", JSON.stringify(byName("supplyId")));
  assert.notEqual(byName("supplyName")?.source, "caller", JSON.stringify(byName("supplyName")));

  const validated = validateCapability(create!, chooserEvents(), catalog);
  const check = validated.validation.checks.find(item => item.name === "caller-fields-backed-by-ui");
  assert.equal(check?.ok, true, JSON.stringify(validated.validation.checks.filter(item => !item.ok)));
});
