import type { EvidenceEvent, UiEvidence } from "../domain.js";

export interface ManualStep {
  order: number;
  at: string;
  action: "click" | "fill" | "choose" | "submit" | "open";
  label?: string;
  name?: string;
  selector?: string;
  value?: unknown;
  text?: string;
  pageUrl?: string;
  scope?: string;
}

function isManualUiEvent(event: EvidenceEvent): event is UiEvidence {
  return event.kind === "ui" && event.eventType !== "snapshot";
}

function actionOf(event: UiEvidence): ManualStep["action"] {
  if (event.eventType === "submit") return "submit";
  if (event.eventType === "input" || event.eventType === "change") {
    if (event.inputType === "select" || event.options?.length || event.visibleOptions?.length) return "choose";
    return "fill";
  }
  const text = `${event.text || ""} ${event.label || ""}`;
  if (/提交|确定|搜索|查询|保存|save|submit|search/i.test(text)) return "submit";
  if (/选择用户|选人|新增|添加|打开/.test(text)) return "open";
  return "click";
}

export function toManualStep(event: UiEvidence, order: number): ManualStep {
  return {
    order,
    at: event.at,
    action: actionOf(event),
    label: event.label,
    name: event.name,
    selector: event.selector,
    value: event.value,
    text: event.text,
    pageUrl: event.pageUrl,
    scope: event.scope
  };
}

function sameStep(left: ManualStep, right: ManualStep) {
  return left.action === right.action
    && left.label === right.label
    && left.name === right.name
    && left.selector === right.selector
    && Object.is(left.value, right.value)
    && left.text === right.text;
}

export function buildManualSteps(events: EvidenceEvent[]): ManualStep[] {
  const steps: ManualStep[] = [];
  for (const event of events) {
    if (!isManualUiEvent(event)) continue;
    if (!event.label && !event.name && !event.text && event.value === undefined && !event.selector) continue;
    const next = toManualStep(event, steps.length + 1);
    const previous = steps.at(-1);
    if (previous && sameStep(previous, next)) {
      previous.at = next.at;
      if (next.value !== undefined) previous.value = next.value;
      continue;
    }
    steps.push({ ...next, order: steps.length + 1 });
  }
  return steps.slice(0, 200);
}

export function describeManualStep(step: ManualStep): string {
  const target = step.label || step.name || step.text || step.selector || "未命名控件";
  const value = step.value === undefined || step.value === "" ? "" : `，值「${String(step.value)}」`;
  const where = step.scope === "dialog" ? "（弹窗内）" : "";
  if (step.action === "fill") return `填写「${target}」${where}${value}`;
  if (step.action === "choose") return `选择「${target}」${where}${value}`;
  if (step.action === "submit") return `点击「${step.text || target}」提交${where}`;
  if (step.action === "open") return `打开「${target}」${where}`;
  return `点击「${step.text || target}」${where}${value}`;
}

export function renderManualStepsMarkdown(
  steps: ManualStep[],
  meta: { sessionId: string; startUrl: string }
) {
  const lines = [
    "# 手动录制步骤",
    "",
    `会话 \`${meta.sessionId}\``,
    `起始页 ${meta.startUrl}`,
    "",
    "页面点不动、自动填表失败或无法复现时，严格按下列已记录步骤操作。不要另找控件，不要循环点击、循环快照或循环 exercise-form。",
    ""
  ];
  if (!steps.length) {
    lines.push("本次没有记录到手动点击或填写。");
    return `${lines.join("\n")}\n`;
  }
  for (const step of steps) {
    lines.push(`${step.order}. ${describeManualStep(step)}`);
    if (step.selector) lines.push(`   - 选择器：\`${step.selector}\``);
    if (step.pageUrl) lines.push(`   - 页面：${step.pageUrl}`);
  }
  return `${lines.join("\n")}\n`;
}
