/**
 * 对照一场录制的证据和 PI result。只报事实偏差，不改写 result。
 */

import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { buildEvidenceIndex } from "../src/evidence-index.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const recordingId = process.argv[2];
if (!recordingId) {
  console.error("usage: node scripts/audit-recording-result.mjs rec_xxx");
  process.exit(1);
}

const dir = path.join(ROOT, "data", recordingId);
const events = (await readFile(path.join(dir, "evidence.jsonl"), "utf8"))
  .split(/\r?\n/)
  .filter(Boolean)
  .map((line) => JSON.parse(line));
const result = JSON.parse(await readFile(path.join(dir, "pi-result.json"), "utf8"));
const index = buildEvidenceIndex(events);

const clicks = index.items.filter((item) => item.kind === "interaction");
const writes = index.items.filter(
  (item) =>
    item.kind === "network_request" &&
    /POST|PUT|PATCH|DELETE/i.test(item.method || "") &&
    !/refresh-token|login/i.test(item.path || ""),
);
const caps = Array.isArray(result.capabilities) ? result.capabilities : [];
const unresolved = Array.isArray(result.unresolved) ? result.unresolved : [];
const findings = [];

const clickTexts = clicks.map((item) => String(item.text || "")).filter(Boolean);
const actionWords = ["搜索", "查询", "新增", "提交", "保存", "查看", "详情", "撤回", "撤销", "删除", "导出", "编辑", "修改"];
for (const word of actionWords) {
  if (!clickTexts.some((text) => text.includes(word))) continue;
  const covered =
    caps.some((cap) => `${cap.title || ""}${cap.name || ""}`.includes(word)) ||
    unresolved.some((item) => JSON.stringify(item).includes(word));
  if (!covered) findings.push(`点击了「${word}」但没有对应能力或 unresolved`);
}

for (const cap of caps) {
  const refs = Array.isArray(cap.request_refs) ? cap.request_refs : [];
  for (const ref of refs) {
    const pathValue = String(ref.path || "");
    if (
      ref.usage === "option_source" &&
      /attachment|approval|process-instance|process-definition|permission|getRouters/i.test(pathValue)
    ) {
      findings.push(`${cap.capability_id} 把 ${pathValue} 标成了 option_source`);
    }
  }
}

const steps = Array.isArray(result.steps) ? result.steps : [];
for (const step of steps) {
  const params = Array.isArray(step.params) ? step.params : [];
  if (/GET/i.test(step.method || "") && /\/get(?:\?|$)/i.test(step.path || "") || /\/get$/i.test(step.path || "")) {
    for (const param of params) {
      if (String(param.path || "").startsWith("body.") && param.source_kind === "previous_response") {
        findings.push(`${step.step_id} 把详情响应展示字段写成了 ${param.path}`);
      }
    }
  }
  for (const param of params) {
    if (
      param.source?.options_complete === false &&
      Array.isArray(param.enum_options) &&
      param.enum_options.length
    ) {
      findings.push(`${step.step_id}.${param.key} 未打开下拉却编了 enum_options`);
    }
  }
}

const report = {
  recordingId,
  interactions: clicks.map((item) => item.text).filter(Boolean),
  writeRequests: writes.map((item) => `${item.method} ${item.path}`),
  capabilities: caps.map((cap) => `${cap.capability_id} ${cap.title || cap.name}`),
  unresolvedCount: unresolved.length,
  findings,
};
console.log(JSON.stringify(report, null, 2));
if (findings.length) process.exitCode = 2;
