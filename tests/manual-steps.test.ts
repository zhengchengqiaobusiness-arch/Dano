import test from "node:test";
import assert from "node:assert/strict";
import type { EvidenceEvent, UiEvidence } from "../src/domain.js";
import { buildManualSteps, describeManualStep, renderManualStepsMarkdown } from "../src/record/manual-steps.js";

function ui(partial: Partial<EvidenceEvent> & Pick<Extract<EvidenceEvent, { kind: "ui" }>, "eventType">): EvidenceEvent {
  return {
    id: partial.id || "ui-1",
    kind: "ui",
    sessionId: "s",
    at: partial.at || "2026-09-03T00:00:00.000Z",
    pageUrl: "https://example.test/leave",
    eventType: partial.eventType,
    label: (partial as { label?: string }).label,
    name: (partial as { name?: string }).name,
    selector: (partial as { selector?: string }).selector,
    text: (partial as { text?: string }).text,
    value: (partial as { value?: unknown }).value,
    inputType: (partial as { inputType?: string }).inputType,
    scope: (partial as { scope?: "page" | "dialog" }).scope,
    form: (partial as { form?: UiEvidence["form"] }).form
  } as EvidenceEvent;
}

test("manual steps keep click, fill, choose and submit, and skip snapshots", () => {
  const steps = buildManualSteps([
    ui({ eventType: "snapshot", label: "整页" }),
    ui({ id: "a", eventType: "click", text: "新增", label: "新增" }),
    ui({ id: "b", eventType: "input", label: "请假天数", name: "day", value: "1", selector: "label=请假天数" }),
    ui({ id: "c", eventType: "change", label: "人力审批", value: "LS部门", inputType: "select", scope: "dialog" }),
    ui({ id: "d", eventType: "click", text: "确定", label: "确定" })
  ]);
  assert.deepEqual(steps.map(item => item.action), ["open", "fill", "choose", "submit"]);
  assert.equal(describeManualStep(steps[1]!), "填写「请假天数」，值「1」");
  assert.match(describeManualStep(steps[2]!), /选择「人力审批」/);
  const markdown = renderManualStepsMarkdown(steps, { sessionId: "rec-1", startUrl: "https://example.test/leave" });
  assert.match(markdown, /手动录制步骤/);
  assert.match(markdown, /不要循环点击/);
  assert.match(markdown, /请假天数/);
});

test("consecutive fills on the same control keep the last value", () => {
  const steps = buildManualSteps([
    ui({ id: "a", eventType: "input", label: "备注", value: "甲" }),
    ui({ id: "b", eventType: "input", label: "备注", value: "甲", at: "2026-09-03T00:00:01.000Z" }),
    ui({ id: "c", eventType: "input", label: "备注", value: "乙" })
  ]);
  assert.equal(steps.length, 1);
  assert.equal(steps[0]!.value, "乙");
});

test("css-only clicks are dropped and form labels beat typed values", () => {
  const steps = buildManualSteps([
    ui({
      id: "a",
      eventType: "input",
      label: "办公室",
      value: "办公室",
      selector: "div.arco-form-item-content-wrapper > textarea.arco-textarea",
      form: [{ label: "职能描述", type: "textarea", value: "办公室" }]
    } as EvidenceEvent & { form: Array<{ label: string; type: string; value: string }> }),
    ui({
      id: "b",
      eventType: "click",
      selector: "div.arco-input-wrapper.arco-input-focus > input.arco-input"
    })
  ]);
  assert.equal(steps.length, 1);
  assert.equal(steps[0]!.action, "fill");
  assert.equal(steps[0]!.label, "职能描述");
  const markdown = renderManualStepsMarkdown(steps, {
    sessionId: "rec-1",
    startUrl: "https://example.test/leave",
    form: [{ label: "职能描述", type: "textarea", value: "办公室" }]
  });
  assert.match(markdown, /提交时页面字段/);
  assert.match(markdown, /职能描述：办公室/);
});
