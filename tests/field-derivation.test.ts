import test from "node:test";
import assert from "node:assert/strict";
import type { EvidenceEvent } from "../src/domain.js";
import { buildCapabilityCandidates } from "../src/inference/build-candidates.js";
import { finalizeCapabilities } from "../src/inference/finalize-capabilities.js";
import { sameValue } from "../src/inference/field-resolver.js";

function events(extra: EvidenceEvent[] = []): EvidenceEvent[] {
  return [{
    id: "ui-form", kind: "ui", sessionId: "s", at: "2026-09-02T00:00:00.000Z",
    pageUrl: "https://example.test/order", eventType: "input",
    form: [
      { name: "productId", label: "产品", type: "select", required: true, value: "苹果" },
      { name: "count", label: "数量", type: "number", required: true, value: 2 },
      { name: "productPrice", label: "单价", type: "number", required: true, value: 10 },
      { name: "unitName", label: "单位", type: "readonly", value: "盒" },
      { name: "requestId", label: "请求号", type: "readonly", value: "550e8400-e29b-41d4-a716-446655440000" }
    ]
  }, {
    id: "ui-submit", kind: "ui", sessionId: "s", at: "2026-09-02T00:00:01.000Z",
    pageUrl: "https://example.test/order", eventType: "click", text: "确定", label: "确定"
  }, {
    id: "net-product", kind: "network", sessionId: "s", at: "2026-09-02T00:00:00.500Z",
    request: { method: "GET", url: "https://example.test/admin-api/product/simple-list", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { success: true, data: [{ id: 9, name: "苹果", unitName: "盒" }] } }
  }, {
    id: "net-create", kind: "network", sessionId: "s", at: "2026-09-02T00:00:02.000Z",
    correlatedUiEvidenceId: "ui-submit",
    request: {
      method: "POST", url: "https://example.test/admin-api/order/create", resourceType: "xhr", headers: {}, query: {},
      body: {
        productId: 9, count: 2, productPrice: 10, unitName: "盒",
        amount: 20, requestId: "550e8400-e29b-41d4-a716-446655440000", token: "xyz"
      }
    },
    response: { status: 200, headers: {}, body: { success: true, data: 1 } }
  }, ...extra];
}

test("sameValue does not treat arbitrary text as boolean true", () => {
  assert.equal(sameValue(true, "盒"), false);
  assert.equal(sameValue(true, "true"), true);
  assert.equal(sameValue(true, 1), true);
  assert.equal(sameValue(3, "3"), true);
});

test("write fields use evidenced derivations and preserve otherwise-unmatched system values", () => {
  const create = buildCapabilityCandidates(events()).find(item => item.transport.pathTemplate.includes("/order/create"))!;
  const unit = create.inputForm.find(field => field.name === "unitName")!;
  const amount = create.inputForm.find(field => field.name === "amount")!;
  const requestId = create.inputForm.find(field => field.name === "requestId")!;
  const token = create.inputForm.find(field => field.name === "token")!;
  const count = create.inputForm.find(field => field.name === "count")!;
  assert.equal(count.source, "caller");
  assert.equal(count.defaultRule, undefined);
  assert.match(unit.defaultRule || "", /^from:.+\.unitName\|via:productId(?:\|fallback:.*)?$/);
  assert.equal(unit.source, "binding");
  assert.equal(amount.defaultRule, "computed:count * productPrice");
  assert.equal(requestId.source, "system");
  assert.equal(requestId.defaultRule, "literal:550e8400-e29b-41d4-a716-446655440000");
  assert.equal(token.defaultRule, undefined);
  assert.doesNotMatch(unit.sourceDetail || "", /录制成功请求写入/);
  assert.doesNotMatch(amount.sourceDetail || "", /literal:20|写入 20/);
});

test("ids and timestamps are not used as formula operands", () => {
  const create = buildCapabilityCandidates([{
    id: "ui-form", kind: "ui", sessionId: "s", at: "2026-09-02T00:00:00.000Z",
    pageUrl: "https://example.test/leave", eventType: "input",
    form: [
      { name: "type", label: "请假类型", type: "select", required: true, value: "事假" },
      { name: "day", label: "请假天数", type: "number", required: true, value: 1 },
      { name: "startTime", label: "开始时间", type: "date", required: true, value: "2026-09-01" },
      { name: "endTime", label: "结束时间", type: "date", required: true, value: "2026-09-02" },
      { name: "leaveBalance", label: "假期余额", type: "readonly", value: 8 }
    ]
  }, {
    id: "ui-submit", kind: "ui", sessionId: "s", at: "2026-09-02T00:00:01.000Z",
    pageUrl: "https://example.test/leave", eventType: "click", text: "提交"
  }, {
    id: "net-balance", kind: "network", sessionId: "s", at: "2026-09-02T00:00:00.400Z",
    request: { method: "GET", url: "https://example.test/admin-api/oa/duty-leave/get-balance?type=2", resourceType: "xhr", headers: {}, query: { type: 2 } },
    response: { status: 200, headers: {}, body: { success: true, data: { leaveBalance: 8 } } }
  }, {
    id: "net-users", kind: "network", sessionId: "s", at: "2026-09-02T00:00:00.500Z",
    request: { method: "GET", url: "https://example.test/admin-api/system/user/page", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { data: { list: [{ id: 174, username: "LSBM", nickname: "LS部门" }] } } }
  }, {
    id: "net-create", kind: "network", sessionId: "s", at: "2026-09-02T00:00:02.000Z",
    correlatedUiEvidenceId: "ui-submit",
    request: {
      method: "POST", url: "https://example.test/admin-api/oa/duty-leave/submit-process", resourceType: "xhr", headers: {}, query: {},
      body: {
        type: 2, day: 1, leaveBalance: 8,
        startTime: 1788192000000, endTime: 1788278400000,
        startUserSelectAssignees: { Activity_0ag2wyz: [174] }
      }
    },
    response: { status: 200, headers: {}, body: { success: true, data: 1 } }
  }]).find(item => item.transport.pathTemplate.includes("submit-process"))!;
  const day = create.inputForm.find(field => field.name === "day")!;
  const balance = create.inputForm.find(field => field.name === "leaveBalance")!;
  const assignees = create.inputForm.find(field => field.name === "startUserSelectAssignees")!;
  assert.equal(day.source, "caller");
  assert.equal(day.defaultRule, "computed:(endTime - startTime) / 86400000");
  assert.doesNotMatch(balance.defaultRule || "", /^computed:.*\btype\b/);
  assert.match(balance.defaultRule || "", /^from:.+leaveBalance/);
  assert.equal(assignees.source, "computed");
  assert.match(assignees.sourceDetail || "", /拼接/);
});

test("the same value in two queries is not a unique from rule", () => {
  const create = buildCapabilityCandidates(events([{
    id: "net-product-2", kind: "network", sessionId: "s", at: "2026-09-02T00:00:00.600Z",
    request: { method: "GET", url: "https://example.test/admin-api/sku/simple-list", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { success: true, data: [{ id: 9, name: "苹果", unitName: "盒" }] } }
  }])).find(item => item.transport.pathTemplate.includes("/order/create"))!;
  const unit = create.inputForm.find(field => field.name === "unitName")!;
  assert.equal(unit.defaultRule?.startsWith("from:") ?? false, false);
  assert.equal(unit.source, "system");
  assert.equal(unit.defaultRule, "literal:盒");
});

test("a shared display value across lookup rows still binds via the selected id", () => {
  const recorded = events().map(event => {
    if (event.id !== "net-product" || event.kind !== "network") return event;
    return {
      ...event,
      response: {
        ...event.response,
        body: {
          success: true,
          data: [
            { id: 9, name: "苹果", unitName: "盒" },
            { id: 10, name: "梨", unitName: "盒" },
            { id: 11, name: "桃", unitName: "盒" }
          ]
        }
      }
    };
  }) as EvidenceEvent[];
  const create = buildCapabilityCandidates(recorded).find(item => item.transport.pathTemplate.includes("/order/create"))!;
  const unit = create.inputForm.find(field => field.name === "unitName")!;
  assert.match(unit.defaultRule || "", /^from:.+\.unitName\|via:productId(?:\|fallback:.*)?$/);
});

test("create does not bind brought-out fields from the page list of existing rows", () => {
  const create = buildCapabilityCandidates(events([{
    id: "net-page", kind: "network", sessionId: "s", at: "2026-09-02T00:00:00.700Z",
    request: {
      method: "GET",
      url: "https://example.test/admin-api/order/page?pageNo=1&pageSize=10",
      resourceType: "xhr",
      headers: {},
      query: { pageNo: 1, pageSize: 10 }
    },
    response: {
      status: 200,
      headers: {},
      body: {
        success: true,
        data: {
          list: [{
            id: 1,
            totalPrice: 20,
            items: [{ id: 88, productId: 9, unitName: "盒", productPrice: 10 }]
          }, {
            id: 2,
            totalPrice: 20,
            items: [{ id: 87, productId: 9, unitName: "盒", productPrice: 10 }]
          }],
          total: 2
        }
      }
    }
  }])).find(item => item.transport.pathTemplate.includes("/order/create"))!;
  const unit = create.inputForm.find(field => field.name === "unitName")!;
  const amount = create.inputForm.find(field => field.name === "amount")!;
  assert.match(unit.defaultRule || "", /^from:.+\.unitName\|via:productId(?:\|fallback:.*)?$/);
  assert.doesNotMatch(unit.defaultRule || "", /order\/page/);
  assert.equal(amount.defaultRule, "computed:count * productPrice");
  assert.doesNotMatch(amount.defaultRule || "", /from:/);
});

test("an unobserved number stays system-owned when no lookup uniquely explains it", () => {
  const recorded = events().map(event => {
    if (event.id === "ui-form" && event.kind === "ui") {
      return {
        ...event,
        form: (event.form || []).filter(field => field.name !== "productPrice")
      };
    }
    if (event.id !== "net-create" || event.kind !== "network") return event;
    return {
      ...event,
      request: {
        ...event.request,
        body: {
          ...(event.request.body as Record<string, unknown>),
          productPrice: 1,
          amount: 2
        }
      }
    };
  });
  const create = buildCapabilityCandidates(recorded).find(item => item.transport.pathTemplate.includes("/order/create"))!;
  const price = create.inputForm.find(field => field.name === "productPrice")!;
  assert.equal(price.source, "system");
  assert.equal(price.defaultRule, "literal:1");
  assert.match(price.sourceDetail || "", /系统执行时原样补齐/);
});

test("single-sample zero and one values use UI meaning, joins, and business formulas instead of coincidences", () => {
  const recorded: EvidenceEvent[] = [{
    id: "ui-purchase-form", kind: "ui", sessionId: "s", at: "2026-09-02T00:00:00.000Z",
    pageUrl: "https://example.test/purchase/order", eventType: "input",
    form: [
      { name: "productId", label: "产品名称", type: "select", required: true, value: "苹果" },
      { label: "产品单价", type: "number", required: true, value: 1 },
      { name: "count", label: "数量", type: "number", required: true, value: 1 },
      { name: "taxPercent", label: "税率（%）", type: "number", value: 1 },
      { label: "单位", type: "readonly", value: "盒" },
      { label: "条码", type: "readonly", value: "0101010101" },
      { label: "库存", type: "readonly", value: 925.5 },
      { label: "金额", type: "readonly", value: 1 },
      { label: "税额", type: "readonly", value: 0.01 },
      { label: "税额合计", type: "readonly", value: 1.01 },
      { label: "优惠率（%）", type: "number", value: 0 },
      { label: "付款优惠", type: "readonly", value: 0 },
      { label: "优惠后金额", type: "readonly", value: 1.01 },
      { label: "支付订金", type: "number", value: 0 }
    ]
  }, {
    id: "ui-purchase-submit", kind: "ui", sessionId: "s", at: "2026-09-02T00:00:01.000Z",
    pageUrl: "https://example.test/purchase/order", eventType: "click", text: "确定", label: "确定", tag: "button"
  }, {
    id: "net-products", kind: "network", sessionId: "s", at: "2026-09-02T00:00:00.200Z",
    request: { method: "GET", url: "https://example.test/admin-api/product/simple-list", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { success: true, data: [{ id: 9, name: "苹果", unitName: "盒", barCode: "0101010101", purchasePrice: 5000 }] } }
  }, {
    id: "net-stock", kind: "network", sessionId: "s", at: "2026-09-02T00:00:00.300Z",
    request: { method: "GET", url: "https://example.test/admin-api/stock/get-count?productId=9", resourceType: "xhr", headers: {}, query: { productId: 9 } },
    response: { status: 200, headers: {}, body: { success: true, data: 925.5 } }
  }, {
    id: "net-unrelated-zero", kind: "network", sessionId: "s", at: "2026-09-02T00:00:00.400Z",
    request: { method: "GET", url: "https://example.test/admin-api/tenant/simple-list", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { success: true, data: [{ id: 1, name: "默认租户", accountCount: 0 }] } }
  }, {
    id: "net-purchase-create", kind: "network", sessionId: "s", at: "2026-09-02T00:00:02.000Z",
    correlatedUiEvidenceId: "ui-purchase-submit",
    request: {
      method: "POST", url: "https://example.test/admin-api/purchase/order/create", resourceType: "xhr", headers: {}, query: {},
      body: {
        discountPercent: 0,
        discountPrice: 0,
        totalPrice: 1.01,
        depositPrice: 0,
        items: [{
          productId: 9,
          productUnitName: "盒",
          productBarCode: "0101010101",
          productPrice: 1,
          stockCount: 925.5,
          count: 1,
          totalProductPrice: 1,
          taxPercent: 1,
          taxPrice: 0.01,
          totalPrice: 1.01
        }]
      }
    },
    response: { status: 200, headers: {}, body: { success: true, data: 172 } }
  }];

  const create = buildCapabilityCandidates(recorded).find(item => item.transport.pathTemplate.includes("/purchase/order/create"))!;
  const byName = (name: string) => create.inputForm.find(field => field.name === name)!;

  assert.equal(byName("productPrice").source, "caller");
  assert.equal(byName("productPrice").label, "产品单价");
  assert.equal(byName("discountPercent").source, "caller");
  assert.equal(byName("discountPercent").label, "优惠率（%）");
  assert.equal(byName("depositPrice").source, "caller");
  assert.equal(byName("depositPrice").label, "支付订金");
  assert.match(byName("productUnitName").defaultRule || "", /^from:.+\.unitName\|via:productId(?:\|fallback:.*)?$/);
  assert.equal(byName("productUnitName").label, "单位");
  assert.match(byName("productBarCode").defaultRule || "", /^from:.+\.barCode\|via:productId(?:\|fallback:.*)?$/);
  assert.equal(byName("productBarCode").label, "条码");
  assert.match(byName("stockCount").defaultRule || "", /^from:.+\.data\|via:productId(?:\|fallback:.*)?$/);
  assert.equal(byName("stockCount").label, "库存");
  assert.equal(byName("totalProductPrice").defaultRule, "computed:count * productPrice");
  assert.equal(byName("totalProductPrice").label, "金额");
  assert.equal(byName("taxPrice").defaultRule, "computed:count * productPrice * taxPercent / 100");
  assert.equal(byName("taxPrice").label, "税额");
  assert.equal(create.inputForm.find(field => field.path === "$.items[*].totalPrice")?.defaultRule, "computed:count * productPrice * (1 + taxPercent / 100)");
  assert.equal(create.inputForm.find(field => field.path === "$.items[*].totalPrice")?.label, "税额合计");
  assert.equal(byName("discountPrice").defaultRule, "computed:sum(items.totalPrice) * discountPercent / 100");
  assert.equal(byName("discountPrice").label, "付款优惠");
  assert.equal(create.inputForm.find(field => field.path === "$.totalPrice")?.defaultRule, "computed:sum(items.totalPrice) - discountPrice");
  assert.equal(create.inputForm.find(field => field.path === "$.totalPrice")?.label, "优惠后金额");
  assert.equal(create.bindings.some(binding => binding.fromPath.endsWith(".accountCount")), false, JSON.stringify(create.bindings));
  assert.equal(create.inputForm.some(field => field.defaultRule?.includes("accountCount")), false, JSON.stringify(create.inputForm));
  assert.equal(create.inputForm.some(field => /taxPercent\s*\/\s*count/.test(field.defaultRule || "")), false, JSON.stringify(create.inputForm));
});

test("semantic UI labels derive a verified amount formula when request keys are unfamiliar", () => {
  const recorded: EvidenceEvent[] = [{
    id: "ui-generic-form", kind: "ui", sessionId: "s", at: "2026-09-02T00:00:00.000Z",
    pageUrl: "https://example.test/biz/form", eventType: "input",
    form: [
      { name: "q", label: "数量", type: "number", required: true, value: 2 },
      { name: "p", label: "产品单价", type: "number", required: true, value: 10 },
      { name: "a", label: "金额", type: "readonly", value: 20 }
    ]
  }, {
    id: "ui-generic-submit", kind: "ui", sessionId: "s", at: "2026-09-02T00:00:01.000Z",
    pageUrl: "https://example.test/biz/form", eventType: "click", text: "保存", label: "保存", tag: "button"
  }, {
    id: "net-generic-create", kind: "network", sessionId: "s", at: "2026-09-02T00:00:02.000Z",
    correlatedUiEvidenceId: "ui-generic-submit",
    request: {
      method: "POST", url: "https://example.test/api/biz/create", resourceType: "xhr", headers: {}, query: {},
      body: { q: 2, p: 10, a: 20 }
    },
    response: { status: 200, headers: {}, body: { success: true, data: 1 } }
  }];

  const create = buildCapabilityCandidates(recorded).find(item => item.transport.pathTemplate.includes("/biz/create"))!;
  assert.equal(create.inputForm.find(field => field.name === "q")?.source, "caller");
  assert.equal(create.inputForm.find(field => field.name === "p")?.source, "caller");
  assert.equal(create.inputForm.find(field => field.name === "a")?.defaultRule, "computed:q * p");
});

test("unmatched write fields preserve the successful request value as system input", () => {
  const recorded: EvidenceEvent[] = [{
    id: "ui-work-report", kind: "ui", sessionId: "s", at: "2026-09-05T01:51:10.000Z",
    pageUrl: "https://example.test/work-report/info", eventType: "input",
    form: [
      { name: "title", label: "标题", type: "text", value: "1" },
      { name: "todayContent", label: "工作总结", type: "textarea", value: "1" }
    ]
  }, {
    id: "ui-submit", kind: "ui", sessionId: "s", at: "2026-09-05T01:51:11.000Z",
    pageUrl: "https://example.test/work-report/info", eventType: "click", text: "确认提交", label: "确认提交"
  }, {
    id: "net-submit", kind: "network", sessionId: "s", at: "2026-09-05T01:51:12.119Z",
    correlatedUiEvidenceId: "ui-submit",
    request: {
      method: "POST", url: "https://example.test/admin-api/oa/work-report/submit", resourceType: "xhr", headers: {}, query: {},
      body: {
        creator: "1",
        attachments: [],
        title: "1",
        todayContent: "1",
        items: [{ content: "1", _X_ROW_KEY: "row_272", sort: 0 }],
        startUserSelectAssignees: {}
      }
    },
    response: { status: 200, headers: {}, body: { code: 0, data: 17, msg: "" } }
  }];

  const create = buildCapabilityCandidates(recorded).find(item => item.transport.pathTemplate.endsWith("/work-report/submit"))!;
  const byPath = (path: string) => create.inputForm.find(field => field.path === path)!;

  assert.equal(byPath("$.title").source, "caller");
  assert.equal(byPath("$.todayContent").source, "caller");
  for (const [path, rule] of [
    ["$.startUserSelectAssignees", "literal:{}"],
    ["$.creator", "literal:\"1\""],
    ["$.attachments", "literal:[]"],
    ["$.items[*]._X_ROW_KEY", "literal:row_272"],
    ["$.items[*].sort", "literal:0"]
  ] as const) {
    const field = byPath(path);
    assert.equal(field.source, "system", `${path}: ${JSON.stringify(field)}`);
    assert.equal(field.systemHandled, true, `${path}: ${JSON.stringify(field)}`);
    assert.equal(field.defaultRule, rule, `${path}: ${JSON.stringify(field)}`);
  }
});

test("an unrelated detail response cannot become a write-field source by equal sample values", () => {
  const recorded: EvidenceEvent[] = [{
    id: "ui-open-submit", kind: "ui", sessionId: "s", at: "2026-09-05T01:51:10.000Z",
    pageUrl: "https://example.test/work-report/info", eventType: "click", text: "提交", label: "提交", tag: "button", role: "button"
  }, {
    id: "net-approval-detail", kind: "network", sessionId: "s", at: "2026-09-05T01:51:10.500Z",
    correlatedUiEvidenceId: "ui-open-submit",
    request: {
      method: "GET", url: "https://example.test/admin-api/bpm/process-instance/get-approval-detail", resourceType: "xhr", headers: {}, query: {}
    },
    response: {
      status: 200, headers: {},
      body: { code: 0, data: { status: -1, activityNodes: [{ candidateUsers: [{ deptId: 103, deptName: "研发部门" }] }] } }
    }
  }, {
    id: "ui-confirm", kind: "ui", sessionId: "s", at: "2026-09-05T01:51:11.000Z",
    pageUrl: "https://example.test/work-report/info", eventType: "click", text: "确认提交", label: "确认提交", tag: "button", role: "button"
  }, {
    id: "net-submit", kind: "network", sessionId: "s", at: "2026-09-05T01:51:12.119Z",
    correlatedUiEvidenceId: "ui-confirm",
    request: {
      method: "POST", url: "https://example.test/admin-api/oa/work-report/submit", resourceType: "xhr", headers: {}, query: {},
      body: { deptId: 103, deptName: "研发部门", processStatus: -1 }
    },
    response: { status: 200, headers: {}, body: { code: 0, data: 17, msg: "" } }
  }];

  const create = buildCapabilityCandidates(recorded).find(item => item.transport.pathTemplate.endsWith("/work-report/submit"))!;
  assert.deepEqual(create.bindings, []);
  for (const [name, rule] of [["deptId", "literal:103"], ["deptName", "literal:研发部门"], ["processStatus", "literal:-1"]] as const) {
    const field = create.inputForm.find(item => item.name === name)!;
    assert.equal(field.source, "system", JSON.stringify(field));
    assert.equal(field.defaultRule, rule, JSON.stringify(field));
  }
});

test("an earlier same-resource detail reload cannot supply unrelated list-query defaults", () => {
  const recorded: EvidenceEvent[] = [{
    id: "net-detail", kind: "network", sessionId: "s", at: "2026-09-05T01:00:00.000Z",
    pageUrl: "https://example.test/work-report/info",
    request: { method: "GET", url: "https://example.test/admin-api/oa/work-report/get?id=20", resourceType: "xhr", headers: {}, query: { id: "20" } },
    response: { status: 200, headers: {}, body: { code: 0, data: { id: 20, creator: "1", reportType: 1 } } }
  }, {
    id: "ui-query", kind: "ui", sessionId: "s", at: "2026-09-05T02:00:00.000Z",
    pageUrl: "https://example.test/work-report/list", eventType: "click", text: "搜索", label: "搜索", tag: "button", role: "button",
    form: [{ name: "billCode", label: "单据编号", type: "text", value: "RB" }]
  }, {
    id: "net-query", kind: "network", sessionId: "s", at: "2026-09-05T02:00:01.000Z",
    pageUrl: "https://example.test/work-report/list", correlatedUiEvidenceId: "ui-query",
    request: {
      method: "GET",
      url: "https://example.test/admin-api/oa/work-report/page?pageNo=1&pageSize=20&billCode=RB&creator=1&reportType=1",
      resourceType: "xhr", headers: {},
      query: { pageNo: "1", pageSize: "20", billCode: "RB", creator: "1", reportType: "1" }
    },
    response: { status: 200, headers: {}, body: { code: 0, data: { list: [], total: 0 } } }
  }];

  const query = buildCapabilityCandidates(recorded).find(item => item.transport.pathTemplate.endsWith("/work-report/page"))!;
  assert.equal(query.inputForm.find(item => item.name === "billCode")?.source, "caller");
  assert.equal(query.inputForm.find(item => item.name === "creator")?.source, "system");
  assert.equal(query.inputForm.find(item => item.name === "creator")?.defaultRule, "literal:\"1\"");
  assert.equal(query.inputForm.find(item => item.name === "reportType")?.source, "system");
  assert.equal(query.inputForm.find(item => item.name === "reportType")?.defaultRule, "literal:\"1\"");
  assert.equal(query.bindings.some(binding => binding.fromCapabilityId.includes("work-report-get")), false, JSON.stringify(query.bindings));
});

test("SPA list fields cannot leak into a write form and disabled controls stay system-owned", () => {
  const recorded: EvidenceEvent[] = [{
    id: "ui-list", kind: "ui", sessionId: "s", at: "2026-09-05T01:00:00.000Z",
    pageUrl: "https://example.test/web/#/oa/workreport/daily-report-list", eventType: "snapshot",
    form: [
      { name: "creator", label: "申请人", type: "select", value: "1", disabled: false },
      { name: "deptId", label: "申请部门", type: "select", value: 103, disabled: false },
      { name: "processStatus", label: "单据状态", type: "select", value: -1, disabled: false },
      { name: "companyId", label: "所属单位", type: "select", value: 101, disabled: false }
    ]
  }, {
    id: "net-list", kind: "network", sessionId: "s", at: "2026-09-05T01:00:01.000Z",
    pageUrl: "https://example.test/web/#/oa/workreport/daily-report-list",
    request: { method: "GET", url: "https://example.test/admin-api/oa/work-report/page?pageNo=1&pageSize=20&creator=1&companyId=101", resourceType: "xhr", headers: {}, query: { pageNo: 1, pageSize: 20, creator: "1", companyId: 101 } },
    response: { status: 200, headers: {}, body: { code: 0, data: { list: [{ id: 7, title: "测试日报" }], total: 1 } } }
  }, {
    id: "ui-save", kind: "ui", sessionId: "s", at: "2026-09-05T01:01:00.000Z",
    pageUrl: "https://example.test/web/#/oa/workreport/work-report-info", eventType: "click",
    text: "保存", label: "保存", tag: "button", role: "button",
    form: [
      { name: "reportType", label: "汇报类型", type: "select", value: 1, disabled: true },
      { name: "reka-v-39-form-item", label: "开始日期", type: "date", value: "2026-09-05", disabled: false },
      { name: "reka-v-41-form-item", label: "结束日期", type: "date", value: "2026-09-05", disabled: false },
      { name: "title", label: "标题", type: "text", value: "测试日报", disabled: false }
    ]
  }, {
    id: "net-save", kind: "network", sessionId: "s", at: "2026-09-05T01:01:01.000Z",
    pageUrl: "https://example.test/web/#/oa/workreport/work-report-info", correlatedUiEvidenceId: "ui-save",
    request: {
      method: "POST", url: "https://example.test/admin-api/oa/work-report/save", resourceType: "xhr", headers: {}, query: {},
      body: { creator: "1", companyId: 101, deptId: 103, processStatus: -1, reportType: 1, startDate: "2026-09-05", endDate: "2026-09-05", title: "测试日报" }
    },
    response: { status: 200, headers: {}, body: { code: 0, data: 8 } }
  }];

  const catalog = finalizeCapabilities(buildCapabilityCandidates(recorded), recorded);
  const create = catalog.find(item => item.transport.pathTemplate.endsWith("/work-report/save"))!;
  const byName = (name: string) => create.inputForm.find(item => item.name === name)!;
  for (const [name, rule] of [["creator", "literal:\"1\""], ["companyId", "literal:101"], ["deptId", "literal:103"], ["processStatus", "literal:-1"], ["reportType", "literal:1"]] as const) {
    assert.equal(byName(name).source, "system", JSON.stringify(byName(name)));
    assert.equal(byName(name).defaultRule, rule, JSON.stringify(byName(name)));
  }
  assert.equal(byName("startDate").source, "caller", JSON.stringify(byName("startDate")));
  assert.equal(byName("startDate").label, "开始日期");
  assert.equal(byName("endDate").source, "caller", JSON.stringify(byName("endDate")));
  assert.equal(byName("endDate").label, "结束日期");
  assert.equal(byName("title").source, "caller");
  assert.notEqual(byName("title").candidates?.type, "capability", JSON.stringify(byName("title")));
});
