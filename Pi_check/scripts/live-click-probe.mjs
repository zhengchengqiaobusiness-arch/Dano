/**
 * 对真实业务页做机械点击探测：snapshot → choose → fill → click。
 * 不推断能力，只验证真实鼠标点得动、点得快。
 */

import { createPlaywrightBrowser, attachBlobSaver } from "../src/browser-capture.mjs";

const targetUrl = process.argv[2] || "http://boot.dianshixinxi.com:90/oa/duty/dutyLeaveApply?billType=duty_leave";
const events = [];
let blobN = 0;
const appendEvidence = attachBlobSaver(async (kind, payload) => {
  const event = { seq: events.length + 1, kind, payload };
  events.push(event);
  return event;
}, {
  writeBlob: async (_id, bytes) => {
    blobN += 1;
    return { blobId: `blob_${blobN}`, byteLength: bytes?.byteLength || Buffer.from(bytes || []).length };
  },
}, "probe");

function timed(label, work) {
  const started = Date.now();
  return Promise.resolve(work()).then(
    (value) => {
      const ms = Date.now() - started;
      console.log(`OK  ${ms.toString().padStart(5)}ms  ${label}`);
      return { ok: true, ms, value, label };
    },
    (error) => {
      const ms = Date.now() - started;
      console.log(`ERR ${ms.toString().padStart(5)}ms  ${label}  ${error?.message || error}`);
      return { ok: false, ms, error: error?.message || String(error), label };
    },
  );
}

const browser = await createPlaywrightBrowser({
  recording: {
    id: "probe_live_click",
    targetUrl,
    viewport: { width: 1440, height: 900 },
  },
  appendEvidence,
});

const report = {
  url: targetUrl,
  pageUrl: browser.livePage()?.url() || "",
  steps: [],
};
try {
  report.pageUrl = browser.livePage()?.url() || "";
  if (/login/i.test(report.pageUrl)) {
    throw new Error(`落到登录页：${report.pageUrl}`);
  }
  const shot1 = await timed("snapshot 列表页", () => browser.inspect());
  report.steps.push(shot1);
  const snap = shot1.value || {};
  console.log(`    title=${snap.title || ""} controls=${snap.controls?.length || 0} actions=${(snap.actions || []).map((item) => item.label).join(",")}`);
  console.log(`    selectors=${(snap.controls || []).map((item) => item.selector).filter(Boolean).join(" | ")}`);

  const typeSelector = (snap.controls || []).find((item) => /请假类型|请选择请假类型/.test(`${item.label || ""}${item.placeholder || ""}${item.selector || ""}`))?.selector
    || "placeholder=请选择请假类型";
  const billSelector = (snap.controls || []).find((item) => /单据编号/.test(`${item.label || ""}${item.placeholder || ""}`))?.selector
    || "placeholder=请输入单据编号";
  const searchSelector = (snap.actions || []).find((item) => item.label === "搜索")?.selector
    || 'role=button[name="搜索"]';
  const createSelector = (snap.actions || []).find((item) => item.label === "新增")?.selector
    || 'role=button[name="新增"]';
  console.log(`    searchSelector=${searchSelector} createSelector=${createSelector}`);
  console.log(`    searchActions=${JSON.stringify((snap.actions || []).filter((item) => item.label === "搜索"))}`);

  const openType = await timed(`click ${typeSelector}`, () => browser.actBySelector({
    selector: typeSelector,
    action: "click",
  }));
  report.steps.push(openType);
  if (openType.value && openType.value.ok === false) throw new Error(openType.value.error || "点开下拉失败");
  console.log(`    panel=${openType.value?.panel || "none"} options=${(openType.value?.options || []).slice(0, 8).join(",")}`);

  const chooseType = await timed(`choose ${typeSelector} → 事假`, () => browser.actBySelector({
    selector: typeSelector,
    action: "choose",
    text: "事假",
  }));
  report.steps.push(chooseType);
  if (chooseType.value && chooseType.value.ok === false) {
    throw new Error(chooseType.value.error || "choose 失败");
  }

  const fillBill = await timed(`fill ${billSelector}`, () => browser.actBySelector({
    selector: billSelector,
    action: "fill",
    text: "RQSQD2026",
  }));
  report.steps.push(fillBill);
  if (fillBill.value && fillBill.value.ok === false) throw new Error(fillBill.value.error || "fill 失败");

  const search = await timed(`click ${searchSelector}`, () => browser.actBySelector({
    selector: searchSelector,
    action: "click",
  }));
  report.steps.push(search);
  if (search.value && search.value.ok === false) throw new Error(search.value.error || "搜索失败");
  await browser.livePage()?.waitForTimeout(800);

  const create = await timed(`click ${createSelector}`, () => browser.actBySelector({
    selector: createSelector,
    action: "click",
  }));
  report.steps.push(create);
  if (create.value && create.value.ok === false) throw new Error(create.value.error || "新增失败");
  await browser.livePage()?.waitForTimeout(1200);

  const shot2 = await timed("snapshot 新增表单", () => browser.inspect());
  report.steps.push(shot2);
  report.formUrl = browser.livePage()?.url() || "";
  const form = shot2.value || {};
  console.log(`    formUrl=${report.formUrl}`);
  console.log(`    form controls=${(form.controls || []).map((item) => item.selector || item.label).join(" | ")}`);
  console.log(`    form actions=${(form.actions || []).map((item) => item.label).join(",")}`);

  if (!/form|add|新增/i.test(report.formUrl) && !(form.controls || []).some((item) => /事由|开始时间/.test(`${item.label || ""}${item.placeholder || ""}`))) {
    throw new Error("点击新增后没有进入表单页");
  }

  const formType = (form.controls || []).find((item) => /请假类型/.test(`${item.label || ""}${item.placeholder || ""}`))?.selector
    || "placeholder=请选择请假类型";
  const days = (form.controls || []).find((item) => /天数/.test(`${item.label || ""}${item.placeholder || ""}`))?.selector
    || "placeholder=请输入天数";
  const reason = (form.controls || []).find((item) => /事由/.test(`${item.label || ""}${item.placeholder || ""}`))?.selector
    || "placeholder=请输入事由";

  const chooseForm = await timed(`choose 表单 ${formType} → 病假`, () => browser.actBySelector({
    selector: formType,
    action: "choose",
    text: "病假",
  }));
  report.steps.push(chooseForm);
  if (chooseForm.value && chooseForm.value.ok === false) throw new Error(chooseForm.value.error || "表单 choose 失败");

  const fillDays = await timed(`fill ${days}`, () => browser.actBySelector({
    selector: days,
    action: "fill",
    text: "1",
  }));
  report.steps.push(fillDays);
  if (fillDays.value && fillDays.value.ok === false) throw new Error(fillDays.value.error || "填天数失败");

  const fillReason = await timed(`fill ${reason}`, () => browser.actBySelector({
    selector: reason,
    action: "fill",
    text: "探测事由",
  }));
  report.steps.push(fillReason);
  if (fillReason.value && fillReason.value.ok === false) throw new Error(fillReason.value.error || "填事由失败");

  const slow = report.steps.filter((item) => item.ms > 4000);
  const failed = report.steps.filter((item) => !item.ok || item.value?.ok === false);
  report.ok = failed.length === 0 && slow.length === 0;
  report.slow = slow.map((item) => `${item.label}:${item.ms}ms`);
  report.failed = failed.map((item) => item.label);
  console.log(JSON.stringify({
    ok: report.ok,
    pageUrl: report.pageUrl,
    formUrl: report.formUrl,
    stepMs: report.steps.map((item) => `${item.label}=${item.ms}`),
    slow: report.slow,
    failed: report.failed,
  }, null, 2));
  if (!report.ok) process.exitCode = 1;
} catch (error) {
  console.error(`PROBE_FAIL ${error?.message || error}`);
  process.exitCode = 1;
} finally {
  await browser.close().catch(() => {});
}
