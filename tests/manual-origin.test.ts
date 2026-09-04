import test from "node:test";
import assert from "node:assert/strict";
import type { EvidenceEvent, UiFieldSnapshot } from "../src/domain.js";
import { buildCapabilityCandidates } from "../src/inference/build-candidates.js";
import { finalizeCapabilities } from "../src/inference/finalize-capabilities.js";
import { reviewCatalog } from "../src/review/catalog-review.js";

const DEPT_ID = "02021060111315890400000101001840";
const ORG_ID = "02025101709392014400000101445343";
const SYS_ID = "02021060111315890400000101001809";
const SYS_NAME = "中共徐州市委机构编制委员会办公室_共享交换数据服务应用";

const PAGE_FIELDS: UiFieldSnapshot[] = [
  { label: "部门名称", type: "select", value: "徐州市教育局", required: true },
  { label: "一级内设机构", type: "select", value: "123", required: true },
  { label: "二级内设机构", type: "text", value: "123123123", required: true },
  { label: "职能描述", type: "textarea", value: "办公室", required: true },
  { label: "联系人", type: "text", value: "123123123", required: true },
  { label: "联系方式", type: "text", value: "13212341234", required: true },
  { label: "职能清单", type: "text", value: "123123", required: true },
  { label: "所属系统", type: "select", value: SYS_NAME, required: true },
  { label: "编目状态", type: "select", value: "" }
];

const CREATE_BODY = {
  ssbmId: DEPT_ID,
  bmId: ORG_ID,
  qzms: "办公室",
  csmc: "123",
  ercsmc: "123123123",
  lxr: "123123123",
  lxfs: "13212341234",
  ssbmmc: "徐州市教育局",
  ywsxList: [{
    ywsxmc: "123123",
    yyxtid: SYS_ID,
    ssxts: "",
    catalogStatus: "",
    yyxtmc: SYS_NAME,
    tableHcommentList: [],
    ywsxKbList: []
  }],
  ywsxKbList: []
};

function namedForm(fields: UiFieldSnapshot[]): UiFieldSnapshot[] {
  const names: Record<string, string> = {
    一级内设机构: "csmc",
    二级内设机构: "ercsmc",
    职能描述: "qzms",
    联系人: "lxr",
    联系方式: "lxfs",
    部门名称: "ssbmmc",
    职能清单: "ywsxmc",
    所属系统: "yyxtid",
    编目状态: "catalogStatus"
  };
  return fields.map(field => ({ ...field, name: names[field.label || ""] || field.name }));
}

function qzqdEvents(style: "auto" | "manual"): EvidenceEvent[] {
  const form = style === "auto" ? namedForm(PAGE_FIELDS) : PAGE_FIELDS;
  const events: EvidenceEvent[] = [{
    id: "ui-form",
    kind: "ui",
    sessionId: "s",
    at: "2026-09-03T08:00:00.000Z",
    pageUrl: "http://10.255.158.85/#/dcensusn/dataCensus/authResp",
    eventType: style === "auto" ? "snapshot" : "input",
    label: style === "manual" ? "办公室" : "职能描述",
    value: "办公室",
    selector: style === "manual" ? "div.arco-form-item-content-wrapper > textarea.arco-textarea" : "label=职能描述",
    form
  }, {
    id: "ui-submit",
    kind: "ui",
    sessionId: "s",
    at: "2026-09-03T08:00:08.000Z",
    pageUrl: "http://10.255.158.85/#/dcensusn/dataCensus/authResp",
    eventType: "click",
    text: "确定",
    label: "确定",
    form
  }, {
    id: "net-list",
    kind: "network",
    sessionId: "s",
    at: "2026-09-03T08:00:01.000Z",
    request: {
      method: "POST",
      url: "http://10.255.158.85/appgateway/dcensus/v1.0/qzqdsl/getQzqdSlList",
      resourceType: "xhr",
      headers: {},
      query: {},
      body: { ssbmId: ORG_ID, pageNum: 1, pageSize: 10, csmc: "" }
    },
    response: {
      status: 200,
      headers: {},
      body: {
        success: true,
        data: {
          rows: [{
            id: "02026081714223400500000101539124",
            ssbmId: ORG_ID,
            ssbmmc: "徐州市政府办公室",
            csmc: "222",
            qzms: "222",
            lxr: "222",
            lxfs: "15266666666",
            ercsmc: "222"
          }, {
            id: "02026081714223400500000101539125",
            ssbmId: ORG_ID,
            ssbmmc: "徐州市政府办公室",
            csmc: "人事科",
            qzms: "人事",
            lxr: "李四",
            lxfs: "15266666667",
            ercsmc: "秘书科"
          }]
        }
      }
    }
  }, {
    id: "ui-dept",
    kind: "ui",
    sessionId: "s",
    at: "2026-09-03T08:00:01.500Z",
    pageUrl: "http://10.255.158.85/#/dcensusn/dataCensus/authResp",
    eventType: "click",
    label: "徐州市政府办公室",
    text: "徐州市教育局",
    name: DEPT_ID
  }, {
    id: "net-sys",
    kind: "network",
    sessionId: "s",
    at: "2026-09-03T08:00:02.000Z",
    request: {
      method: "POST",
      url: "http://10.255.158.85/appgateway/dcensus/v1.0/xxxt/getXxxtListByBm",
      resourceType: "xhr",
      headers: {},
      query: {},
      body: { bmId: ORG_ID }
    },
    response: {
      status: 200,
      headers: {},
      body: {
        success: true,
        data: [
          { id: SYS_ID, xtmc: SYS_NAME },
          { id: "02021060111315890400000101001899", xtmc: "其它应用系统" }
        ]
      }
    }
  }, {
    id: "net-create",
    kind: "network",
    sessionId: "s",
    at: "2026-09-03T08:00:09.000Z",
    pageUrl: "http://10.255.158.85/#/dcensusn/dataCensus/authResp",
    correlatedUiEvidenceId: "ui-submit",
    request: {
      method: "POST",
      url: "http://10.255.158.85/appgateway/dcensus/v1.0/qzqdsl/createQzqdSl",
      resourceType: "xhr",
      headers: {},
      query: {},
      body: CREATE_BODY
    },
    response: { status: 200, headers: {}, body: { success: true, data: true } }
  }];
  return events;
}

function createCapability(events: EvidenceEvent[]) {
  return buildCapabilityCandidates(events).find(item => item.transport.pathTemplate.includes("createQzqdSl"))!;
}

function fieldMap(capability: ReturnType<typeof createCapability>) {
  return Object.fromEntries(capability.inputForm.map(field => [field.name, field]));
}

function originsOf(capability: ReturnType<typeof createCapability>) {
  return Object.fromEntries(capability.inputForm.map(field => [
    field.name,
    `${field.source}:${field.label}:${field.defaultRule || ""}`
  ]));
}

test("manual form snapshots without request names still bind Chinese labels and pass review", () => {
  const events = qzqdEvents("manual");
  const created = createCapability(events);
  const fields = fieldMap(created);
  assert.equal(created.operation, "create", JSON.stringify(originsOf(created)));
  assert.equal(fields.csmc?.source, "caller");
  assert.equal(fields.csmc?.label, "一级内设机构");
  assert.equal(fields.qzms?.source, "caller");
  assert.equal(fields.qzms?.label, "职能描述");
  assert.equal(fields.lxr?.source, "caller");
  assert.equal(fields.lxr?.label, "联系人");
  assert.equal(fields.lxfs?.source, "caller");
  assert.equal(fields.lxfs?.label, "联系方式");
  assert.equal(fields.ercsmc?.source, "caller");
  assert.equal(fields.ercsmc?.label, "二级内设机构");
  assert.equal(fields.ywsxmc?.source, "caller");
  assert.equal(fields.ywsxmc?.label, "职能清单");
  assert.equal(fields.ssbmmc?.label, "部门名称");
  assert.ok(fields.yyxtid?.source === "caller" || fields.yyxtid?.defaultRule?.startsWith("from:"), fields.yyxtid?.sourceDetail);
  assert.equal(fields.yyxtid?.label, "所属系统");
  const catalog = finalizeCapabilities(buildCapabilityCandidates(events), events);
  const verified = catalog.find(item => item.transport.pathTemplate.includes("createQzqdSl"))!;
  const after = fieldMap(verified);
  assert.equal(after.catalogStatus?.defaultRule, "literal:\"\"");
  assert.equal(after.ssxts?.defaultRule, "literal:\"\"");
  assert.match(after.yyxtmc?.defaultRule || "", /^from:.+\|via:yyxtid$/, JSON.stringify(after.yyxtmc));
  assert.equal(after.yyxtmc?.source, "binding");
  assert.ok(after.ssbmmc?.defaultRule?.startsWith("from:") || after.ssbmmc?.source === "caller", after.ssbmmc?.sourceDetail);
  assert.ok(after.bmId?.defaultRule?.startsWith("from:") || after.bmId?.source === "caller", after.bmId?.sourceDetail);
  assert.ok(after.ssbmId?.defaultRule?.startsWith("from:") || after.ssbmId?.source === "caller", after.ssbmId?.sourceDetail);
  const review = reviewCatalog(catalog, events);
  assert.equal(review.status, "passed", review.summary);
});

test("automatic named fills and manual unlabeled form snapshots bind the same caller fields", () => {
  const auto = fieldMap(createCapability(qzqdEvents("auto")));
  const manual = fieldMap(createCapability(qzqdEvents("manual")));
  for (const name of ["csmc", "ercsmc", "qzms", "lxr", "lxfs", "ywsxmc", "yyxtid"]) {
    assert.equal(manual[name]?.source, auto[name]?.source, name);
    assert.equal(manual[name]?.label, auto[name]?.label, name);
  }
});
