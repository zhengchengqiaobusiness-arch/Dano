import test from "node:test";
import assert from "node:assert/strict";
import type { EvidenceEvent } from "../src/domain.js";
import { materializeHttpRequest } from "../src/execution/http-executor.js";
import { buildCapabilityCandidates } from "../src/inference/build-candidates.js";

const recordedBody = {
  creator: "1",
  creatorName: "43543",
  companyId: 101,
  companyName: "广州分公司",
  deptId: 103,
  deptName: "研发部门",
  processStatus: -1,
  createTime: "2026-09-05T01:51:12.119Z",
  reportType: 1,
  startDate: "2026-09-05",
  endDate: "2026-09-05",
  attachments: [],
  title: "1",
  todayContent: "1",
  planContent: "1",
  issueContent: "1",
  remark: "1",
  items: [{ content: "1", progress: 20, itemType: 1, _X_ROW_KEY: "row_272", sort: 0 }],
  startUserSelectAssignees: {}
};

function workReportEvents(): EvidenceEvent[] {
  return [{
    id: "ui-form", kind: "ui", sessionId: "work-report", at: "2026-09-05T01:51:10.000Z",
    pageUrl: "https://ruoyioffice.com/web/#/oa/workreport/work-report-info", eventType: "input",
    form: [
      { name: "reportType", label: "汇报类型", type: "select", value: "日报", options: [{ value: 1, label: "日报" }] },
      { name: "title", label: "标题", type: "text", value: "1" },
      { name: "todayContent", label: "工作总结", type: "textarea", value: "1" },
      { name: "planContent", label: "工作计划", type: "textarea", value: "1" },
      { name: "issueContent", label: "问题/协调事项", type: "textarea", value: "1" },
      { name: "remark", label: "备注", type: "textarea", value: "1" },
      { name: "content", label: "工作内容", type: "text", value: "1" },
      { name: "progress", label: "完成进度", type: "number", value: 20 }
    ]
  }, {
    id: "ui-confirm", kind: "ui", sessionId: "work-report", at: "2026-09-05T01:51:11.000Z",
    pageUrl: "https://ruoyioffice.com/web/#/oa/workreport/work-report-info", eventType: "click",
    text: "确认提交", label: "确认提交", tag: "button", role: "button"
  }, {
    id: "net-submit", kind: "network", sessionId: "work-report", at: "2026-09-05T01:51:12.119Z",
    pageUrl: "https://ruoyioffice.com/web/#/oa/workreport/work-report-info",
    correlatedUiEvidenceId: "ui-confirm",
    request: {
      method: "POST", url: "https://ruoyioffice.com/admin-api/oa/work-report/submit",
      resourceType: "xhr", headers: {}, query: {}, body: recordedBody
    },
    response: { status: 200, headers: {}, body: { code: 0, data: 17, msg: "" } }
  }];
}

test("work report keeps caller fields editable and reproduces every system field from the successful request", () => {
  const create = buildCapabilityCandidates(workReportEvents())
    .find(item => item.transport.pathTemplate.endsWith("/oa/work-report/submit"))!;
  const request = materializeHttpRequest(create, {
    reportType: 1,
    title: "1",
    todayContent: "1",
    planContent: "1",
    issueContent: "1",
    remark: "1",
    items: [{ content: "1", progress: 20 }]
  });

  assert.deepEqual(request.body, recordedBody);
  assert.deepEqual(create.bindings, []);
  assert.equal(create.inputForm.find(field => field.path === "$.startUserSelectAssignees")?.defaultRule, "literal:{}");
  assert.equal(create.inputForm.find(field => field.path === "$.items[*]._X_ROW_KEY")?.defaultRule, "literal:row_272");
  assert.equal(create.inputForm.find(field => field.path === "$.title")?.source, "caller");
  assert.equal(create.inputForm.find(field => field.path === "$.items[*].progress")?.source, "caller");
});
