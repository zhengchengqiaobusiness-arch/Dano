import test from "node:test";
import assert from "node:assert/strict";
import type { EvidenceEvent, NetworkEvidence, UiEvidence } from "../src/domain.js";
import { inferOperation } from "../src/inference/heuristics.js";
import { buildCapabilityCandidates } from "../src/inference/build-candidates.js";
import { finalizeCapabilities } from "../src/inference/finalize-capabilities.js";
import { capabilitiesForSession, isPrimaryCapability, summarizeCatalog } from "../src/inference/export-scope.js";
import { reviewCatalog } from "../src/review/catalog-review.js";
import { mergeCatalogByTransport } from "../src/catalog/normalize.js";

const PAGE = "http://10.255.158.85/dopenportal/#/Home";
const CONVERSATION_ID = "29cba714-c6ce-4747-baec-e2c5d37d6868";
const USER_ID = "02025100915013258600000101427669";
const APP_CODE = "591f22581bf8300bf467392c04dde9a7";
const QUERY_TEXT = "123123";

function net(partial: {
  id: string;
  url: string;
  method?: string;
  query?: Record<string, string>;
  body?: unknown;
  response?: unknown;
  correlatedUiEvidenceId?: string;
}): NetworkEvidence {
  return {
    id: partial.id,
    kind: "network",
    sessionId: "chat",
    at: "2026-09-03T10:00:02.000Z",
    pageUrl: PAGE,
    correlatedUiEvidenceId: partial.correlatedUiEvidenceId,
    request: {
      method: partial.method || "POST",
      url: partial.url,
      resourceType: "xhr",
      headers: {},
      query: partial.query || {},
      body: partial.body
    },
    response: { status: 200, headers: {}, body: partial.response }
  };
}

function askEvents(): EvidenceEvent[] {
  const form = [{ label: "和数据智能体聊天", type: "text", value: QUERY_TEXT }];
  const ui: UiEvidence = {
    id: "ui-chat",
    kind: "ui",
    sessionId: "chat",
    at: "2026-09-03T10:00:01.000Z",
    pageUrl: PAGE,
    eventType: "change",
    label: "和数据智能体聊天",
    value: QUERY_TEXT,
    form
  };
  return [
    ui,
    net({
      id: "net-save",
      url: "http://10.255.158.85/appgateway/dataiq/save_dataiq_chat_list",
      body: { user_id: USER_ID, name: QUERY_TEXT },
      response: {
        code: 200,
        data: { conversation_id: CONVERSATION_ID, created_at: 1782891442, name: QUERY_TEXT, user_id: USER_ID },
        msg: "保存成功"
      }
    }),
    net({
      id: "net-appid",
      method: "GET",
      url: `http://10.255.158.85/apigateway/appauth/getappid?appId=rand-app&appName=rand-name&timeStamp=17884322`,
      query: { appId: "rand-app", appName: "rand-name", timeStamp: "17884322" },
      response: { code: 200, msg: "", data: APP_CODE }
    }),
    net({
      id: "net-chat",
      url: "http://10.255.158.85/dataiq/sjws_chat",
      correlatedUiEvidenceId: "ui-chat",
      body: {
        sys_query: QUERY_TEXT,
        wybs: "51e561cb-49e9-4f96-817a-2d0a7e2a4360",
        token: "[REDACTED]",
        appCode: APP_CODE,
        identity: { userid: USER_ID, ryxm: "zhengchengqiao", scope: "city" },
        conversation_id: CONVERSATION_ID
      },
      response: "data: {\"event\":\"answer\",\"answer\":\"ok\"}\n\n"
    }),
    net({
      id: "net-home",
      url: "http://10.255.158.85/appgateway/dopenportal/v1.0/yzzy/getYzzyList",
      body: { pageNo: 1, pageSize: 8, cxlx: "1" },
      response: { code: 200, data: { list: [{ id: "b1", title: "首页块" }], total: 1 } }
    })
  ];
}

test("conversational ask without list/search in the path is still a query", () => {
  const chat = askEvents().find((event): event is NetworkEvidence => event.id === "net-chat")!;
  assert.equal(inferOperation(chat), "query");
  assert.equal(inferOperation({
    ...chat,
    request: { ...chat.request, url: "https://x.test/orders/recalculate", body: {} },
    response: { status: 200, headers: {}, body: { ok: true } },
    correlatedUiEvidenceId: undefined,
    pageUrl: "https://x.test/orders"
  }), "action");
});

test("ask recording produces one query skill and keeps purchase create out of this session", () => {
  const events = askEvents();
  const catalog = buildCapabilityCandidates(events);
  const chat = catalog.find(item => item.transport.pathTemplate.endsWith("/sjws_chat"));
  const save = catalog.find(item => item.transport.pathTemplate.includes("save_dataiq_chat_list"));
  const appid = catalog.find(item => item.transport.pathTemplate.includes("/getappid"));
  const home = catalog.find(item => item.transport.pathTemplate.endsWith("/getYzzyList"));
  assert.ok(chat, `capabilities: ${catalog.map(item => `${item.operation}:${item.transport.pathTemplate}`).join(",")}`);
  assert.equal(chat!.operation, "query");
  assert.equal(save?.operation, "query");
  assert.equal(appid?.operation, "query");
  assert.equal(chat!.inputForm.find(field => field.name === "sys_query")?.source, "caller", JSON.stringify(chat!.inputForm.map(field => `${field.name}:${field.source}:${field.label}:${field.defaultRule || ""}`)));
  assert.match(chat!.inputForm.find(field => field.name === "sys_query")?.label || "", /数据智能体|问数|聊天/);
  assert.equal(chat!.inputForm.find(field => field.name === "wybs")?.source, "system");
  assert.equal(chat!.inputForm.find(field => field.name === "wybs")?.defaultRule, "literal:51e561cb-49e9-4f96-817a-2d0a7e2a4360");
  assert.match(chat!.inputForm.find(field => field.name === "conversation_id")?.defaultRule || "", /from:.+conversation_id/);
  assert.match(chat!.inputForm.find(field => field.name === "appCode")?.defaultRule || "", /from:.+getappid.+/);
  assert.equal(isPrimaryCapability(chat!, catalog), true);
  assert.equal(isPrimaryCapability(save!, catalog), false);
  assert.equal(isPrimaryCapability(appid!, catalog), false);
  if (home) assert.equal(isPrimaryCapability(home, catalog), false);
  assert.equal(summarizeCatalog(catalog).primary.length, 1);
  assert.equal(summarizeCatalog(catalog).primary[0]?.transport.pathTemplate.endsWith("/sjws_chat"), true);

  const verified = finalizeCapabilities(catalog, events);
  const review = reviewCatalog(verified, events);
  assert.equal(review.status, "passed", review.summary);
  assert.equal(review.primaryCount, 1, review.summary);
  assert.match(review.primaryTitles.join("、"), /查询/);

  const purchase = {
    ...verified[0]!,
    id: "create-order",
    operation: "create" as const,
    title: "新建 purchase-order",
    transport: {
      method: "POST",
      urlTemplate: "http://admin.dianshixinxi.com:90/admin-api/erp/purchase-order/create",
      origin: "http://admin.dianshixinxi.com:90",
      pathTemplate: "/admin-api/erp/purchase-order/create"
    },
    inputForm: [{
      path: "$.supplierId", name: "supplierId", label: "供应商", valueType: "string" as const,
      source: "caller" as const, required: true, requiredBasis: "ui-required" as const,
      systemHandled: false, sourceDetail: "页面", widget: "text" as const
    }],
    evidence: [{ eventId: "purchase-net", sessionId: "purchase", kind: "network" as const, at: "2026-09-02T10:00:00.000Z", status: 200 }],
    sideEffect: true,
    confirmation: { required: true },
    validation: {
      version: 2,
      status: "candidate" as const,
      checks: [{ name: "caller-fields-backed-by-ui", ok: false, detail: "存在没有页面输入证据的调用方字段" }]
    }
  };
  const merged = mergeCatalogByTransport(verified, [purchase]);
  const purchaseEvents: EvidenceEvent[] = [{
    id: "purchase-net",
    kind: "network",
    sessionId: "purchase",
    at: "2026-09-02T10:00:00.000Z",
    pageUrl: "http://admin.dianshixinxi.com:90/erp/purchase/order",
    request: { method: "POST", url: "http://admin.dianshixinxi.com:90/admin-api/erp/purchase-order/create", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: {} }
  }];
  const scoped = capabilitiesForSession(merged, [...events, ...purchaseEvents], events);
  assert.equal(scoped.some(item => item.transport.pathTemplate.endsWith("/sjws_chat")), true);
  assert.equal(scoped.some(item => item.transport.pathTemplate.includes("/purchase-order/create")), false);
  const scopedReview = reviewCatalog(scoped, events);
  assert.equal(scopedReview.status, "passed", scopedReview.summary);
  assert.equal(scopedReview.primaryCount, 1, scopedReview.summary);
  assert.equal(reviewCatalog(merged, [...events, ...purchaseEvents]).status, "blocked");
});
