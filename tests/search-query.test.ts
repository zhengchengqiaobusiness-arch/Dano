import test from "node:test";
import assert from "node:assert/strict";
import type { EvidenceEvent, NetworkEvidence, UiEvidence } from "../src/domain.js";
import { inferOperation } from "../src/inference/heuristics.js";
import { buildCapabilityCandidates } from "../src/inference/build-candidates.js";
import { finalizeCapabilities } from "../src/inference/finalize-capabilities.js";
import { isPrimaryCapability, summarizeCatalog } from "../src/inference/export-scope.js";
import { reviewCatalog } from "../src/review/catalog-review.js";

function net(partial: {
  id: string;
  url: string;
  body?: unknown;
  response?: unknown;
  pageUrl?: string;
  correlatedUiEvidenceId?: string;
}): NetworkEvidence {
  return {
    id: partial.id,
    kind: "network",
    sessionId: "s",
    at: "2026-09-03T09:00:02.000Z",
    pageUrl: partial.pageUrl ?? "http://10.255.158.85/dopenportal/#/Home",
    correlatedUiEvidenceId: partial.correlatedUiEvidenceId,
    request: {
      method: "POST",
      url: partial.url,
      resourceType: "xhr",
      headers: {},
      query: {},
      body: partial.body
    },
    response: { status: 200, headers: {}, body: partial.response }
  };
}

function searchEvents(): EvidenceEvent[] {
  const form = [
    { label: "请输入关键字", type: "search", value: "123", required: false },
    { label: "资源类型", type: "select", value: "全部", options: [{ value: "0", label: "全部" }, { value: "1", label: "数据资源" }] },
    { label: "排序方式", type: "select", value: "按相关度", options: [{ value: "0", label: "按相关度" }, { value: "1", label: "按浏览量" }] }
  ];
  const ui: UiEvidence = {
    id: "ui-keyword",
    kind: "ui",
    sessionId: "s",
    at: "2026-09-03T09:00:01.000Z",
    pageUrl: "http://10.255.158.85/dopenportal/#/Home",
    eventType: "change",
    label: "请输入关键字",
    value: "123",
    form
  };
  const click: UiEvidence = {
    id: "ui-search",
    kind: "ui",
    sessionId: "s",
    at: "2026-09-03T09:00:01.500Z",
    pageUrl: "http://10.255.158.85/dopenportal/#/Home",
    eventType: "click",
    text: "搜索",
    label: "搜索",
    form
  };
  return [
    ui,
    click,
    net({
      id: "net-hot",
      url: "http://10.255.158.85/appgateway/dopenportal/v1.0/zhcx/public/getHotWords",
      pageUrl: "http://10.255.158.85/dopenportal/#/Home/Search",
      body: {},
      response: { success: true, data: ["社会", "旅游", "税务"] }
    }),
    net({
      id: "net-search",
      url: "http://10.255.158.85/appgateway/dopenportal/v1.0/zhcx/public/getAllZy",
      pageUrl: "http://10.255.158.85/dopenportal/#/Home",
      correlatedUiEvidenceId: "ui-keyword",
      body: { gjz: "123", pageNo: 1, pageSize: 10, pxfs: "0", zylx: "0" },
      response: {
        success: true,
        data: {
          total: 19,
          list: [
            { id: "r1", title: "123456789", zylx: "数据资源", ssbmmc: "市民政局" },
            { id: "r2", title: "sxcs_徐州总工会场景", zylx: "应用场景", ssbmmc: "市总工会" }
          ]
        }
      }
    }),
    net({
      id: "net-fetch",
      url: "http://example.test/api/v2/resource/fetchItems",
      pageUrl: "http://example.test/#/portal/home",
      correlatedUiEvidenceId: "ui-keyword",
      body: { keyword: "123", pageNum: 1, pageSize: 10 },
      response: {
        code: 0,
        data: {
          rows: [
            { id: "a1", title: "样例资源", name: "样例资源" }
          ]
        }
      }
    })
  ];
}

test("POST search without list/search in the path is still a query", () => {
  const search = searchEvents().find((event): event is NetworkEvidence => event.id === "net-search")!;
  const fetch = searchEvents().find((event): event is NetworkEvidence => event.id === "net-fetch")!;
  assert.equal(inferOperation(search), "query");
  assert.equal(inferOperation(fetch), "query");
  assert.equal(inferOperation({
    ...search,
    request: { ...search.request, url: "https://x.test/orders/recalculate", body: {} },
    response: { status: 200, headers: {}, body: { ok: true } },
    correlatedUiEvidenceId: undefined,
    pageUrl: "https://x.test/orders"
  }), "unknown");
});

test("keyword search with companion requests becomes a reviewable query skill", () => {
  const events = searchEvents().filter(event => event.id !== "net-fetch");
  const clustered = buildCapabilityCandidates(events);
  const catalog = finalizeCapabilities(clustered, events);
  const search = catalog.find(item => item.transport.pathTemplate.endsWith("/getAllZy"));
  assert.ok(search, `capabilities: ${catalog.map(item => `${item.operation}:${item.transport.pathTemplate}`).join(",")}`);
  assert.equal(search!.operation, "query");
  assert.equal(search!.inputForm.find(field => field.name === "gjz")?.source, "caller", JSON.stringify(search!.inputForm.map(field => `${field.name}:${field.source}:${field.label}`)));
  assert.match(search!.inputForm.find(field => field.name === "gjz")?.label || "", /关键字|搜索/);
  assert.equal(isPrimaryCapability(search!, catalog), true);
  assert.equal(summarizeCatalog(catalog).primary.some(item => item.transport.pathTemplate.endsWith("/getAllZy")), true);

  const verified = catalog;
  const review = reviewCatalog(verified, events);
  assert.equal(review.primaryCount > 0, true, review.summary);
  assert.equal(review.status, "passed", review.summary);
  assert.equal(review.primaryTitles.some(title => /查询/.test(title)), true, review.summary);

  const withPurchase = [
    ...verified,
    {
      ...verified[0]!,
      id: "create-order",
      operation: "create" as const,
      title: "新建 purchase-order",
      transport: { method: "POST", urlTemplate: "https://x/erp/purchase-order/create", origin: "https://x", pathTemplate: "/erp/purchase-order/create" },
      inputForm: [],
      evidence: [],
      sideEffect: true,
      confirmation: { required: true },
      validation: { version: 2, status: "verified" as const, checks: [] }
    },
    {
      ...verified[0]!,
      id: "query-order",
      operation: "query" as const,
      title: "查询 purchase-order",
      transport: { method: "GET", urlTemplate: "https://x/erp/purchase-order/page", origin: "https://x", pathTemplate: "/erp/purchase-order/page" },
      inputForm: [{ path: "$.no", name: "no", label: "单号", valueType: "string" as const, source: "caller" as const, required: false, requiredBasis: "not-observed" as const, systemHandled: false, sourceDetail: "页面", widget: "text" as const }],
      evidence: [],
      sideEffect: false,
      confirmation: { required: false },
      validation: { version: 2, status: "verified" as const, checks: [] }
    }
  ];
  const searchAgain = withPurchase.find(item => item.transport.pathTemplate.endsWith("/getAllZy"))!;
  assert.equal(isPrimaryCapability(searchAgain, withPurchase), true);
});
