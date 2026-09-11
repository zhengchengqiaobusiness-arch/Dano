/**
 * 消费者包只从录制合同投影。禁止按页面再猜字段，禁止沿用 Skill 4 自编执行器。
 */

import { mkdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";

function asList(value) {
  return Array.isArray(value) ? value : [];
}

function asRecord(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function stripSamples(value) {
  if (Array.isArray(value)) return value.map(stripSamples);
  if (!value || typeof value !== "object") return value;
  const out = {};
  for (const [key, item] of Object.entries(value)) {
    if (["sample_value", "sample", "fingerprint", "recorded_value"].includes(key)) continue;
    out[key] = stripSamples(item);
  }
  return out;
}

const SYSTEM_KINDS = new Set(["constant", "current_user", "unresolved"]);
const HOST_ASK_INPUT_TYPES = new Set([
  "text", "textarea", "date", "radio", "checkbox", "select", "treeSelect",
]);

export function capabilityId(cap) {
  return String(cap?.capability_id || cap?.id || "").trim();
}

export function executeRef(cap, steps = []) {
  const refs = asList(cap?.request_refs);
  const exec = refs.find((ref) => String(ref?.usage || "") === "execute") || refs[0] || {};
  const step = asList(steps).find((item) => item.step_id && item.step_id === exec.step_id)
    || asList(steps).find((item) => asList(cap?.step_ids).includes(item.step_id)
      && String(item.method || "")
      && !/option|simple-list|dict-data/i.test(String(item.path || item.name || "")))
    || {};
  return {
    method: String(exec.method || step.method || "GET").toUpperCase(),
    path: String(exec.path || step.path || ""),
    step_id: String(exec.step_id || step.step_id || ""),
    params: asList(step.params),
  };
}

function isSystemParam(item) {
  const kind = String(item?.source_kind || "");
  return item?.exposed_to_user === false || SYSTEM_KINDS.has(kind);
}

function capabilitySteps(cap, steps = []) {
  const ids = new Set(asList(cap?.step_ids).map(String));
  const exec = executeRef(cap, steps);
  if (exec.step_id) ids.add(String(exec.step_id));
  return asList(steps).filter((item) => ids.has(String(item.step_id)));
}

function paramsByKey(cap, steps = []) {
  const found = new Map();
  for (const step of capabilitySteps(cap, steps)) {
    for (const item of asList(step.params)) {
      const key = String(item?.key || "");
      if (key && !found.has(key)) found.set(key, item);
    }
  }
  for (const item of asList(executeRef(cap, steps).params)) {
    const key = String(item?.key || "");
    if (key) found.set(key, item);
  }
  return found;
}

export function schemaPropertyKeys(cap) {
  return Object.keys(asRecord(asRecord(cap?.input_schema).properties));
}

export function callerParams(cap, steps = []) {
  const schema = asRecord(cap?.input_schema);
  const properties = asRecord(schema.properties);
  const required = new Set(asList(schema.required).map(String));
  const found = paramsByKey(cap, steps);
  const fields = [];
  const seen = new Set();
  const write = /create|update|delete|submit|write/i.test(String(cap?.kind || ""));
  for (const [key, spec] of Object.entries(properties)) {
    const param = found.get(key) || {};
    fields.push(withPageDateDefault(normalizeCallerField(key, spec, param, required.has(key) || Boolean(param.required)), write));
    seen.add(key);
  }
  for (const [key, param] of found) {
    if (seen.has(key) || param?.exposed_to_user === false) continue;
    fields.push(withPageDateDefault(normalizeCallerField(key, {}, param, Boolean(param.required)), write));
    seen.add(key);
  }
  return fields;
}

function withPageDateDefault(field, _write) {
  if (field.type === "date" && !Object.prototype.hasOwnProperty.call(field, "default")) {
    field.page_default = "today";
  }
  return field;
}

function normalizeCallerField(key, spec, param, required) {
  const source = asRecord(param.source);
  const option = asRecord(spec["x-dano-option-source"]);
  const enums = asList(spec["x-enum-options"]).length ? asList(spec["x-enum-options"]) : asList(param.enum_options);
  const endpoint = String(option.source_url || source.source_url || "");
  const childrenField = String(option.children_key || source.children_key || (endpoint ? "children" : ""));
  const dataSource = endpoint ? {
    type: "api",
    endpoint,
    method: String(option.source_method || source.source_method || "GET").toUpperCase(),
    params: asRecord(option.params || source.params),
    resultPath: String(option.result_path || source.result_path || "data"),
    idField: String(option.value_key || source.value_key || "id"),
    labelField: String(option.label_key || source.label_key || "name"),
    ...(childrenField ? { childrenField } : {}),
  } : null;
  const field = {
    id: String(key),
    title: String(spec.title || param.label || key),
    type: String(spec.type || param.type || "string"),
    required: Boolean(required),
    enums,
    dataSource,
    sections: asRecord(spec["x-dano-section-titles"]),
    itemProperties: asRecord(asRecord(spec.items).properties),
    path: String(param.path || ""),
    source_kind: String(param.source_kind || spec["x-dano-source-kind"] || ""),
    reason: String(param.reason || spec.description || ""),
  };
  const itemType = itemTypeFromReason(param.reason || spec.description);
  if (itemType != null) field.itemType = itemType;
  if (Object.prototype.hasOwnProperty.call(param, "default_value") && param.default_value !== undefined && param.default_value !== "") {
    field.default = param.default_value;
  } else if (spec.default !== undefined && spec.default !== "") {
    field.default = spec.default;
  }
  return field;
}

function itemTypeFromReason(reason) {
  const matched = String(reason || "").match(/itemType\s*=\s*(\d+)/i);
  if (!matched) return undefined;
  const value = Number(matched[1]);
  return Number.isFinite(value) ? value : undefined;
}

function coerceWireValue(type, raw) {
  let text = String(raw ?? "").trim();
  if ((text.startsWith("\"") && text.endsWith("\"")) || (text.startsWith("'") && text.endsWith("'"))) {
    text = text.slice(1, -1);
  }
  if (type === "number" || type === "integer") {
    const n = Number(text);
    return Number.isFinite(n) ? n : undefined;
  }
  if (type === "boolean") return text === "true" || text === "1";
  if (type === "array") {
    if (text === "[]" || /空数组/i.test(text)) return [];
    return undefined;
  }
  return text || undefined;
}

function valueFromCapabilityReason(param) {
  const reason = String(param?.reason || "");
  const key = String(param?.key || "");
  const type = String(param?.type || "");
  if (!reason) return undefined;
  if (type === "array" && /空数组|empty array|\[\s*\]/i.test(reason)) return [];
  if (key) {
    const escaped = key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const keyed = reason.match(new RegExp(`${escaped}\\s*=\\s*(-?\\d+(?:\\.\\d+)?|"[^"]+"|'[^']+'|[A-Za-z0-9._-]+)`));
    if (keyed) return coerceWireValue(type, keyed[1]);
  }
  const fixedNum = reason.match(/固定为\s*(-?\d+)/);
  if (fixedNum && (type === "number" || type === "integer" || !type)) return Number(fixedNum[1]);
  return undefined;
}

export function resolveSystemDefault(param) {
  if (!param || typeof param !== "object") return { has: false };
  if (Object.prototype.hasOwnProperty.call(param, "default_value") && param.default_value !== undefined) {
    return { has: true, value: param.default_value };
  }
  for (const key of ["value", "constant_value", "const"]) {
    if (param[key] !== undefined && param[key] !== "") return { has: true, value: param[key] };
  }
  if (String(param.source_kind || "") === "constant") {
    const fromReason = valueFromCapabilityReason(param);
    if (fromReason !== undefined) return { has: true, value: fromReason };
    if (param.type === "array") return { has: true, value: [] };
  }
  return { has: false };
}

export function systemParams(cap, steps = []) {
  const callerKeys = new Set(callerParams(cap, steps).map((item) => item.id));
  return executeRef(cap, steps).params.filter((item) => isSystemParam(item) && !callerKeys.has(String(item.key))).map((item) => {
    const out = {
      key: item.key,
      path: item.path,
      type: item.type,
      source_kind: item.source_kind,
      required: Boolean(item.required),
    };
    const resolved = resolveSystemDefault(item);
    if (resolved.has) out.default_value = resolved.value;
    return out;
  });
}

export function buildRoutes(draft) {
  const caps = asList(draft?.capabilities);
  const ids = caps.map(capabilityId).filter(Boolean);
  const relations = asList(draft?.capability_relations);
  const incoming = new Set(relations.map((item) => String(item.to_capability || item.to || "")));
  const nextOf = new Map();
  for (const rel of relations) {
    const from = String(rel.from_capability || rel.from || "");
    const to = String(rel.to_capability || rel.to || "");
    if (from && to) nextOf.set(from, to);
  }
  let start = ids.find((id) => !incoming.has(id)) || ids[0] || "";
  const chain = [];
  const seen = new Set();
  while (start && !seen.has(start)) {
    chain.push(start);
    seen.add(start);
    start = nextOf.get(start) || "";
  }
  if (!chain.length) chain.push(...ids);
  const routes = [{
    route_id: "default",
    title: "默认完整办理",
    steps: chain,
  }];
  for (const cap of caps) {
    const id = capabilityId(cap);
    if (!id) continue;
    routes.push({
      route_id: id,
      title: `仅${cap.name || cap.title || id}`,
      steps: [id],
    });
  }
  return routes;
}

export function consumerContract(draft) {
  const src = asRecord(draft);
  const steps = stripSamples(asList(src.steps));
  const capabilities = asList(src.capabilities).map((cap) => {
    const exec = executeRef(cap, steps);
    return stripSamples({
      capability_id: capabilityId(cap),
      name: cap.name || cap.title || "",
      title: cap.title || cap.name || "",
      kind: cap.kind || "",
      intent: cap.intent || "",
      request_refs: asList(cap.request_refs),
      step_ids: asList(cap.step_ids),
      input_schema: cap.input_schema || { type: "object", properties: {} },
      execute: { method: exec.method, path: exec.path, step_id: exec.step_id },
      caller_fields: callerParams(cap, steps),
      system_params: systemParams(cap, steps),
    });
  });
  return {
    title: src.title || capabilities.map((item) => item.name).filter(Boolean).join("、") || "本页办理",
    capabilities,
    steps,
    links: stripSamples(asList(src.links)),
    capability_relations: stripSamples(asList(src.capability_relations)),
    unresolved: stripSamples(asList(src.unresolved)),
    routes: buildRoutes(src),
  };
}

function fieldControl(field) {
  if (field.dataSource) return "treeSelect";
  if (field.enums.length) return "radio";
  if (field.type === "date") return "date";
  if (field.type === "array") return "table";
  if (field.type === "number" || field.type === "integer") return "number";
  return "text";
}

function isWriteCap(cap) {
  return /create|update|delete|submit|write/i.test(String(cap?.kind || ""));
}

function enumOption(item) {
  if (item && typeof item === "object") {
    return { id: item.value ?? item.id, label: item.label || String(item.value ?? item.id) };
  }
  return { id: item, label: String(item) };
}

function enumText(field) {
  return field.enums.map(enumOption).map((item) => `${item.label}=${item.id}`).join(" / ");
}

function howToFill(field) {
  if (field.dataSource) return "先 `python3 scripts/flow.py --list-options <capability_id> <field>` 拉候选，再让用户选 id";
  if (field.enums.length) return `按合同枚举选 id：${enumText(field)}`;
  if (field.source_kind === "selected_record_identity") return "填本对话已确认的选中记录 id；不要编造";
  if (field.source_kind === "previous_response") return "用本对话上一步已确认结果里的同名字段";
  if (field.type === "date") {
    return field.page_default === "today"
      ? "按 yyyy-MM-dd 填写；页面日期控件默认当日，可改"
      : "按 yyyy-MM-dd 向用户收集真实周期";
  }
  if (field.type === "array") {
    const cols = Object.entries(field.itemProperties).map(([key, item]) => `\`${key}\` ${item.title || key}`);
    const sections = Object.keys(field.sections);
    const bits = [];
    if (sections.length) bits.push(`分区 ${sections.join(" / ")}`);
    if (cols.length) bits.push(`每行列 ${cols.join(" / ")}`);
    bits.push("提问用 textarea 收同一字段 id，行格式见冻结提问；runtime 组装回数组");
    bits.push("行内容必须向用户收集");
    return bits.join("；");
  }
  if (field.reason) return field.reason;
  return "向用户收集真实内容，禁止编造";
}

function allowedDefaultText(field) {
  const parts = [];
  if (Object.prototype.hasOwnProperty.call(field, "default")) {
    parts.push(`合同默认 ${JSON.stringify(field.default)}`);
  }
  if (field.enums.length) {
    parts.push(`枚举只能用 ${enumText(field)}`);
  }
  if (field.dataSource) {
    parts.push("只能用本次 `--list-options` 返回并被用户选中的 id");
  }
  if (field.source_kind === "selected_record_identity") {
    parts.push("可用本对话已确认的选中记录 id");
  }
  if (field.source_kind === "previous_response") {
    parts.push("可用本对话上一步已确认结果中的同名字段");
  }
  if (field.type === "date") {
    parts.push(field.page_default === "today"
      ? "页面日期控件默认当日，可改；格式 yyyy-MM-dd"
      : "日期格式 yyyy-MM-dd，必须向用户收集真实周期");
  }
  if (field.type === "array") {
    const cols = Object.entries(field.itemProperties).map(([key, item]) => item.title || key);
    const sections = Object.keys(field.sections);
    if (sections.length) parts.push(`分区 ${sections.join(" / ")}`);
    if (cols.length) parts.push(`行列 ${cols.join(" / ")}`);
    parts.push("行内容必须向用户收集");
  }
  const usable = Object.prototype.hasOwnProperty.call(field, "default")
    || field.page_default === "today"
    || field.enums.length
    || field.dataSource
    || field.source_kind === "selected_record_identity"
    || field.source_kind === "previous_response";
  if (!usable) {
    return "无可用默认值；必须向用户收集真实内容。禁止编造";
  }
  if (!Object.prototype.hasOwnProperty.call(field, "default")) {
    return `无合同默认。${parts.join("；")}。此外可用本对话用户已确认的值。禁止编造`;
  }
  return `${parts.join("；")}。此外可用本对话用户已确认的值。禁止编造`;
}

function fieldFillRow(field) {
  return `| \`${field.id}\` | ${field.title} | ${field.required ? "是" : "否"} | ${fieldControl(field)} | ${howToFill(field)} | ${allowedDefaultText(field)} |`;
}

function askInputType(field) {
  const control = fieldControl(field);
  if (control === "number") return "text";
  if (control === "table") return "textarea";
  if (/文本域|textarea/i.test(String(field.reason || ""))) return "textarea";
  return HOST_ASK_INPUT_TYPES.has(control) ? control : "text";
}

function arrayLineRecipe(field) {
  const cols = Object.keys(field.itemProperties || {});
  const sections = Object.keys(field.sections || {});
  if (sections.length && cols.length) {
    return `每行一条：分区标题|||${cols.join("|||")}；分区只能是 ${sections.join(" / ")}`;
  }
  if (cols.length) return `每行一条：${cols.join("|||")}`;
  return "每行一条，或提交 JSON 数组";
}

function askQuestion(field) {
  const parts = [field.title];
  if (field.type === "array") {
    parts.push(Object.keys(field.sections || {}).length
      ? "能力层是分区表，宿主 ask_user_question 没有 table，本字段用 textarea 收同一 id"
      : "能力层是表格，宿主 ask_user_question 没有 table，本字段用 textarea 收同一 id");
    parts.push(arrayLineRecipe(field));
    parts.push("也可提交 JSON 数组。没有行则留空。不要改 id，不要拆多轮");
  } else if (field.dataSource) {
    parts.push("从本次 `--list-options` 返回的候选里选 id，不要编造");
  } else if (field.enums.length) {
    parts.push(`按合同枚举选 id：${enumText(field)}`);
  } else if (field.type === "date") {
    parts.push(field.page_default === "today"
      ? "按 yyyy-MM-dd 填写；页面日期控件默认当日，可改"
      : "按 yyyy-MM-dd 填写真实周期");
  } else if (field.reason) {
    parts.push(String(field.reason).replace(/。+$/, ""));
  } else {
    parts.push("向用户收集真实内容，禁止编造");
  }
  if (field.page_default === "today") {
    parts.push("未选则按当天，不要因此卡住");
  } else {
    parts.push(field.required ? "必填，不要占位句" : "可选；没有就留空，不要占位句");
  }
  return `${parts.join("。")}。`;
}

export function fieldAskSpec(field) {
  const spec = {
    id: field.id,
    question: askQuestion(field),
    inputType: askInputType(field),
    required: Boolean(field.required) && field.page_default !== "today",
  };
  if (field.enums.length) spec.options = field.enums.map(enumOption);
  if (field.type === "date") spec.dateFormat = "yyyy-MM-dd";
  if (Object.prototype.hasOwnProperty.call(field, "default")) spec.default = field.default;
  if (spec.inputType === "textarea") spec.fieldAssist = true;
  return spec;
}

export function frozenAskForm(cap) {
  return {
    title: cap.name || cap.capability_id,
    questions: (cap.caller_fields || []).map(fieldAskSpec),
  };
}

function nestedFillRows(field) {
  return Object.entries(field.itemProperties || {}).map(([key, spec]) => fieldFillRow({
    id: `${field.id}.${key}`,
    title: spec.title || key,
    required: false,
    type: String(spec.type || "string"),
    enums: [],
    dataSource: null,
    itemProperties: {},
    sections: {},
    source_kind: "",
    reason: "数组行内字段，必须向用户收集真实内容",
  }));
}

function systemFillText(param) {
  if (param.source_kind === "current_user") return "运行时取当前登录用户；不要向用户要";
  if (Object.prototype.hasOwnProperty.call(param, "default_value")) {
    return `合同值 ${JSON.stringify(param.default_value)}；不要向用户要`;
  }
  if (param.source_kind === "constant") return "合同常量；必须带 default_value，由 runtime 自动填";
  if (String(param.key) === "createTime") return "运行时生成当前时间；不要向用户要";
  return `${param.source_kind || "system"}；不要向用户要`;
}

export function renderInputForms(contract) {
  const lines = [
    "# 调用方表单",
    "",
    "只收集合同 `caller_fields`。系统字段由 `scripts/runtime.py` 按合同填充。",
    "动态字段必须保留完整 `dataSource`。助手先运行 `python3 scripts/flow.py --list-options <capability_id> <field>` 拉候选，再提问。不要让问句自己裸打选项接口。",
    "可用默认值只允许：合同 `default`、本对话用户已确认的值、本次 `--list-options` 选中的 id、合同枚举 id。其它一律向用户收集，禁止编造。",
    "",
  ];
  for (const cap of contract.capabilities) {
    lines.push(`## ${cap.name || cap.capability_id}（\`${cap.capability_id}\`）`, "");
    lines.push("| 字段 | 标题 | 必填 | 控件 | 怎么填 | 可用默认值 |", "|---|---|---|---|---|---|");
    for (const field of cap.caller_fields) {
      lines.push(fieldFillRow(field));
      for (const row of nestedFillRows(field)) lines.push(row);
    }
    lines.push("");
    for (const field of cap.caller_fields) {
      lines.push(`### ${field.title}（\`${field.id}\`）`, "");
      const spec = fieldAskSpec(field);
      if (field.dataSource) spec.dataSource = field.dataSource;
      lines.push(`可用默认值：${allowedDefaultText(field)}`, "");
      lines.push("```json", JSON.stringify(spec, null, 2), "```", "");
      if (field.type === "array" && Object.keys(field.sections).length) {
        const sampleRow = (title) => {
          const row = { section: title };
          for (const [key, item] of Object.entries(field.itemProperties)) {
            row[key] = item.type === "number" || item.type === "integer" ? null : "";
          }
          return row;
        };
        lines.push("各分区行放进同一个数组，并用 `section` 标明分区标题。下面只是结构，行内容必须向用户收集：", "");
        lines.push("```json");
        lines.push(JSON.stringify({
          [field.id]: Object.keys(field.sections).map(sampleRow),
        }, null, 2));
        lines.push("```", "");
      }
    }
    const write = /create|update|delete|submit|write/i.test(cap.kind || "");
    if (write) {
      lines.push("写操作收集完毕后必须单独确认：`{ \"confirm\": true, \"formIds\": [\"<answered.formId>\"] }`。", "");
    }
  }
  return `${lines.join("\n")}\n`;
}

export function renderCapabilities(contract) {
  const lines = ["# 业务能力索引", "", "与录制合同同一份能力，禁止增删改字段。", ""];
  for (const cap of contract.capabilities) {
    lines.push(`## ${cap.name || cap.capability_id}`, "");
    lines.push(`- capability_id: \`${cap.capability_id}\``);
    lines.push(`- 类型: ${cap.kind || "—"}`);
    lines.push(`- 执行: \`${cap.execute.method} ${cap.execute.path}\``);
    if (cap.intent) lines.push(`- 说明: ${cap.intent}`);
    lines.push("", "调用方字段（与能力 `input_schema` 及调用方 params 一致，不准漏）:", "");
    lines.push("| 字段 | 标题 | 必填 | 控件 | 怎么填 | 可用默认值 |", "|---|---|---|---|---|---|");
    for (const field of cap.caller_fields) {
      lines.push(fieldFillRow(field));
      for (const row of nestedFillRows(field)) lines.push(row);
    }
    if (cap.system_params.length) {
      lines.push("", "系统字段（不要向调用方要）:", "");
      lines.push("| 字段 | 来源 | 可用默认值 |", "|---|---|---|");
      for (const param of cap.system_params) {
        lines.push(`| \`${param.key}\` | ${param.source_kind || "system"} | ${systemFillText(param)} |`);
      }
    }
    lines.push("");
  }
  return `${lines.join("\n")}\n`;
}

export function renderOptions(contract) {
  const lines = [
    "# 活选项",
    "",
    "选项接口和业务接口走同一套 `config/auth.local.json`。",
    "",
    "```text",
    "python3 scripts/flow.py --list-options <capability_id> <field>",
    "```",
    "",
  ];
  for (const cap of contract.capabilities) {
    for (const field of cap.caller_fields) {
      if (!field.dataSource) continue;
      lines.push(`## ${cap.capability_id}.${field.id}`, "");
      lines.push("```json", JSON.stringify(field.dataSource, null, 2), "```", "");
      lines.push("失败或空列表则停问，不得用录制样本冒充。", "");
    }
  }
  if (!contract.capabilities.some((cap) => cap.caller_fields.some((field) => field.dataSource))) {
    lines.push("本合同没有动态选项。", "");
  }
  return `${lines.join("\n")}\n`;
}

export function renderSkillMd(contract) {
  const title = contract.title || "本页办理";
  const names = contract.capabilities.map((item) => item.name).filter(Boolean);
  const intents = contract.capabilities.map((item) => item.intent || item.name).filter(Boolean);
  const defaultRoute = contract.routes.find((item) => item.route_id === "default") || contract.routes[0];
  const lines = [
    "---",
    `name: ${title}`,
    `description: ${intents.join("；") || names.join("、") || title}。用户意图对上某条能力的 name / intent 时走该原子路线，否则走 default。`,
    "---",
    "",
    "## 立刻办理",
    "",
    "只读这一节就能办。读完禁止再读本文件，禁止读 `references/`，禁止 ls / cat / 探路。",
    "查询不要确认卡：表单提交后立刻执行。写操作才弹确认卡，确认后再 `--confirm`。",
    "default：先查询（不确认）→ 只回统计概览 → 立刻问写表单（要确认）。中间不要重读，不要第二次 `--list-options`。",
    "日期：口令里有就用口令；没选就按当天。runtime 会把未选日期填成当天，不要为日期卡住提交。",
    "查询回复只要统计概览（应填 / 已填 / 未填 / 填写率）和已填写人员；未填写超过 8 人只报人数，除非用户要明细。",
    "禁止改 `inputType`，禁止增删字段，禁止自己补非日期 `default`，禁止拆成多轮问卷。",
    "该路线若有动态字段（执行协议写了 `--list-options`）：先且只跑 `cd <本 SKILL.md 所在目录> && python3 scripts/flow.py --list-options <capability_id> <field>`，把返回的 `options`（已展平的 id/label）写进该字段，再复制冻结 JSON 调用 `ask_user_question`。",
    "不要把 dataSource 放进 ask_user_question。宿主会打聊天站点相对路径，拉不到业务树。INPUT_FORMS 里的 dataSource 只给脚本用。",
    "没有动态字段时，第一次工具就是冻结提问。",
    "用户说法对上某能力 name / intent（填写、新增、提交等同义）走该原子路线，对不上走 default。不要为某个业务口令写死 capability_id。",
    "这份 JSON 已经按宿主控件投影。能力层的 table 在提问里是同一字段 id 的 textarea；答案由 runtime 组装回数组。",
    "除日期外，JSON 里没有 `default` 就不要加。不要编「请填写」「暂无」「请审批」。用户交回占位句视为未填，按同一张冻结表再问。",
    "系统字段由 `scripts/runtime.py` 按合同自动填，不要向用户要，不要让用户去补合同缺省。",
    "",
    "## 冻结提问",
    "",
    "`default` 路线按默认链依次提问并执行；原子路线只用该能力这一份。字段 id / inputType 原样复制；动态字段的 options 必须是本次 `--list-options` 返回值，不要自编表单，不要带 dataSource。",
    "",
  ];
  for (const cap of contract.capabilities) {
    lines.push(`### \`${cap.capability_id}\``, "");
    if ((cap.caller_fields || []).length) {
      lines.push("```json", JSON.stringify(frozenAskForm(cap), null, 2), "```", "");
    } else {
      lines.push("（无调用方字段，本能力不用提问。）", "");
    }
  }
  lines.push(
    "## 默认值规则",
    "",
    "只允许下面这些默认值，其它一律不准用：",
    "",
    "1. 该字段合同已经写出的 `default`（见各字段「可用默认值」列）",
    "2. 本对话里用户已经确认的值",
    "3. 枚举字段：只能用合同列出的 id",
    "4. 动态字段：只能用本次 `python3 scripts/flow.py --list-options <capability_id> <field>` 返回并被用户选中的 id",
    "",
    "没有可用默认值的字段必须向用户收集真实内容。禁止编造「无」「示例」「请审批」。日期页面默认当日不算编造。",
    "系统常量必须带合同值，由 runtime 自动填。",
    "",
    "## 适用场景",
    "",
  );
  if (names.length) {
    for (const cap of contract.capabilities) {
      lines.push(`- ${cap.name || cap.capability_id}${cap.intent ? `：${cap.intent}` : ""}`);
    }
  } else {
    lines.push("- 按录制能力办理本页事务");
  }
  lines.push(
    "",
    "## 不适用场景",
    "",
    "- 合同未声明的能力",
    "",
    "## 选择工作流",
    "",
    "默认完整办理：按 `default` 路线依次执行合同能力。",
  );
  if (defaultRoute?.steps?.length) {
    lines.push("", `默认链：${defaultRoute.steps.join(" → ")}`);
  }
  lines.push(
    "",
    "用户意图对上某条能力的 `name` / `intent` 时，走该能力原子路线；对不上再走 `default`。不要为某个业务口令写死 capability_id。",
    "",
    "| 路线 | 说明 |",
    "|---|---|",
  );
  for (const item of contract.routes) {
    lines.push(`| \`${item.route_id}\` | ${item.title} |`);
  }
  lines.push(
    "",
    "## 组合与交接规则",
    "",
    "- 已确认绑定按 CONTRACT.links 自动带值",
    "- 人手交接停问，不准猜传值",
    "- 原子路线只跑一个 capability_id",
    "",
    "## 执行协议",
    "",
    "只通过本包脚本调用，禁止助手自己拼 HTTP，禁止改 `scripts/runtime.py` / `scripts/flow.py` / `scripts/client.py`。",
    "",
    "```text",
    "cd <本 SKILL.md 所在目录> && python3 scripts/flow.py --route <route_id> --input-json '{...}' --confirm",
    "cd <本 SKILL.md 所在目录> && python3 scripts/flow.py --list-options <capability_id> <field>",
    "```",
    "",
    "工作目录不是本包。选定路线后立刻 `cd` 到本 SKILL.md 所在目录再跑脚本。禁止 ls / cat / 探路。",
    "没有 `python3` 再用 `python`。查询字段收齐后立刻执行，不要确认卡。写操作字段收齐后必须单独确认再执行。",
    "",
  );
  for (const cap of contract.capabilities) {
    const write = isWriteCap(cap);
    lines.push(`### ${cap.name || cap.capability_id}`, "");
    lines.push(`capability_id: \`${cap.capability_id}\``, "");
    if (cap.intent) lines.push(`intent: ${cap.intent}`, "");
    lines.push(`Done when: \`${cap.execute.method} ${cap.execute.path}\` 已返回业务成功。`, "");
    lines.push("调用方字段（必须全部按能力收集，不准合并、省略或漏列）:", "");
    if (cap.caller_fields.length) {
      lines.push("| 字段 | 标题 | 必填 | 控件 | 怎么填 | 可用默认值 |", "|---|---|---|---|---|---|");
      for (const field of cap.caller_fields) {
        lines.push(fieldFillRow(field));
        for (const row of nestedFillRows(field)) lines.push(row);
      }
    } else {
      lines.push("- （无调用方字段）");
    }
    if (cap.system_params.length) {
      lines.push("", "系统字段（不要向用户要，runtime 按能力自动填）:", "");
      lines.push("| 字段 | 来源 | 可用默认值 |", "|---|---|---|");
      for (const param of cap.system_params) {
        lines.push(`| \`${param.key}\` | ${param.source_kind || "system"} | ${systemFillText(param)} |`);
      }
    }
    if (write) {
      lines.push("", "写操作收集完毕后必须单独确认：`{ \"confirm\": true, \"formIds\": [\"<answered.formId>\"] }`，确认后再 `--confirm`。");
    }
    lines.push("");
  }
  lines.push(
    "## 成功、失败与停止",
    "",
    "- 任一步失败即停",
    "- 查询成功只回统计概览和已填写人员，不要把未填写长名单整表贴进对话",
    "- 没有本包凭证或 401 / 账号未登录 → 停问一次 token。提问只用 `{\"questions\":[{\"id\":\"token\",\"question\":\"请粘贴新的访问令牌\",\"inputType\":\"text\",\"required\":true}]}`，不要加 title，不要自己补 default。拿到后用 `DANO_AUTH_HEADERS` 覆盖再跑同一条命令；不要改文件，不要再问第二次",
    "- 选项失败或空列表 → 停问",
    "",
    "## 按需读取资源",
    "",
    "默认不要读本包其它文件。读完本 SKILL.md 一次后禁止再读本文件。",
    "只有提问控件失败或脚本报错时，再读 `references/INPUT_FORMS.md`。",
    "禁止一开始就 ls、读 `CONTRACT.json` / `CAPABILITIES.md` / `OPTIONS.md`。",
    "",
    "## 鉴权",
    "",
    "- 先用本包 `config/auth.local.json` 直接执行",
    "- 过期时停问一次 token（只要 questions 数组，不要同时传 title 和 question），然后：",
    "",
    "```text",
    "DANO_AUTH_HEADERS='{\"Authorization\":\"Bearer <token>\"}' python3 scripts/flow.py --route <route_id> --input-json '{...}' --confirm",
    "```",
    "",
    "- 已设置的 `DANO_AUTH_HEADERS` 覆盖本地过期头，其它头仍用本地文件",
    "- 只通过 `scripts/flow.py` / `scripts/client.py` 带出完整头",
    "- 不要把 token 写入手册或对话，不要改 `auth.local.json`",
    "",
  );
  return `${lines.join("\n")}\n`;
}

export function renderRouteDocs(contract) {
  const files = {};
  for (const item of contract.routes) {
    files[`references/routes/${item.route_id}.md`] = [
      `# ${item.title}`,
      "",
      `route_id: \`${item.route_id}\``,
      "",
      "步骤:",
      ...item.steps.map((id, index) => `${index + 1}. \`${id}\``),
      "",
    ].join("\n");
  }
  return files;
}

export function materializePackageTexts(draft) {
  const contract = consumerContract(draft);
  return {
    contract,
    files: {
      "SKILL.md": renderSkillMd(contract),
      "references/CONTRACT.json": `${JSON.stringify(contract, null, 2)}\n`,
      "references/INPUT_FORMS.md": renderInputForms(contract),
      "references/CAPABILITIES.md": renderCapabilities(contract),
      "references/OPTIONS.md": renderOptions(contract),
      ...renderRouteDocs(contract),
    },
  };
}

export function handbookUnfaithfulReasons(text, contract) {
  if (!text) return ["手册为空"];
  const reasons = [];
  for (const section of ["立刻办理", "冻结提问", "选择工作流", "执行协议", "按需读取资源", "鉴权"]) {
    if (!text.includes(section)) reasons.push(`缺少章节 ${section}`);
  }
  if (!text.includes("可用默认值")) reasons.push("缺少可用默认值");
  if (!text.includes("ask_user_question")) reasons.push("缺少冻结提问 ask_user_question");
  if (/"inputType"\s*:\s*"table"/.test(text)) reasons.push("提问里写了 inputType table");
  if (/字段以 references\/CONTRACT|先阅读全部 references/.test(text)) reasons.push("写了禁止文案");
  for (const cap of contract.capabilities || []) {
    if (!text.includes(cap.capability_id)) reasons.push(`缺少能力 ${cap.capability_id}`);
    for (const field of cap.caller_fields || []) {
      if (!text.includes(`\`${field.id}\``)) reasons.push(`缺少字段标记 ${field.id}`);
      if (!text.includes(`"id": "${field.id}"`) && !text.includes(`"id":"${field.id}"`)) {
        reasons.push(`冻结提问缺少 ${field.id}`);
      }
      for (const key of Object.keys(field.itemProperties || {})) {
        if (!text.includes(`${field.id}.${key}`) && !text.includes(`\`${key}\``)) {
          reasons.push(`缺少列 ${field.id}.${key}`);
        }
      }
    }
    for (const param of cap.system_params || []) {
      if (param.key && !text.includes(String(param.key))) reasons.push(`缺少系统字段 ${param.key}`);
      if (Object.prototype.hasOwnProperty.call(param, "default_value")) {
        const needle = `合同值 ${JSON.stringify(param.default_value)}`;
        if (!text.includes(needle)) reasons.push(`缺少 ${needle}`);
      }
    }
    for (const key of schemaPropertyKeys(cap)) {
      if (!text.includes(key)) reasons.push(`缺少 schema 字段 ${key}`);
    }
  }
  for (const item of contract.routes || []) {
    if (item.route_id && !text.includes(`\`${item.route_id}\``)) reasons.push(`缺少路线 ${item.route_id}`);
  }
  if ((contract.capabilities || []).some((cap) => (cap.caller_fields || []).some((field) => field.dataSource))) {
    if (!text.includes("--list-options")) reasons.push("缺少 --list-options");
    if (!text.includes("不要把 dataSource 放进 ask")) reasons.push("缺少不要把 dataSource 放进 ask");
    if (/"dataSource"\s*:/.test(text)) reasons.push("SKILL.md 里写了 dataSource JSON");
  }
  if (!/禁止再读本文件|不要再读本文件/.test(text)) reasons.push("缺少不要再读本文件");
  if (!/确认卡/.test(text)) reasons.push("缺少确认卡规则");
  return reasons;
}

export function handbookIsFaithful(text, contract) {
  return handbookUnfaithfulReasons(text, contract).length === 0;
}

export function chooseHandbook(skill4Text, materializedText, contract) {
  const texts = Array.isArray(skill4Text) ? skill4Text : [skill4Text];
  for (const text of texts) {
    if (handbookIsFaithful(text, contract)) return text;
  }
  return materializedText;
}

export async function writeMaterializedPackage(dest, draft) {
  const { files, contract } = materializePackageTexts(draft);
  await rm(path.join(dest, "references", "routes"), { recursive: true, force: true });
  for (const [rel, content] of Object.entries(files)) {
    const target = path.join(dest, rel);
    await mkdir(path.dirname(target), { recursive: true });
    await writeFile(target, content, "utf8");
  }
  return contract;
}

export function contractFidelityIssues(packageContract, sourceDraft) {
  const issues = [];
  if (!sourceDraft || !asList(sourceDraft.capabilities).length) return issues;
  const sourceIds = asList(sourceDraft.capabilities).map(capabilityId).filter(Boolean);
  const packed = new Map(asList(packageContract?.capabilities).map((cap) => [capabilityId(cap), cap]));
  for (const id of sourceIds) {
    if (!packed.has(id)) issues.push(`缺少能力 ${id}`);
  }
  for (const cap of asList(sourceDraft.capabilities)) {
    const id = capabilityId(cap);
    const got = packed.get(id);
    if (!got) continue;
    const expected = callerParams(cap, sourceDraft.steps).map((item) => item.id);
    const seen = new Set(asList(got.caller_fields).map((item) => item.id));
    for (const field of expected) {
      if (!seen.has(field)) issues.push(`${id} 缺少调用方字段 ${field}`);
    }
    for (const key of schemaPropertyKeys(cap)) {
      if (!seen.has(key)) issues.push(`${id} 缺少能力字段 ${key}`);
    }
    const exec = executeRef(cap, sourceDraft.steps);
    if (exec.path && String(got.execute?.path || "") !== exec.path) {
      issues.push(`${id} 执行路径不一致`);
    }
    const expectedSys = systemParams(cap, sourceDraft.steps);
    const seenSys = new Map(asList(got.system_params).map((item) => [item.key, item]));
    for (const param of expectedSys) {
      const gotParam = seenSys.get(param.key);
      if (!gotParam) issues.push(`${id} 缺少系统字段 ${param.key}`);
      else if (Object.prototype.hasOwnProperty.call(param, "default_value")
        && !Object.prototype.hasOwnProperty.call(gotParam, "default_value")) {
        issues.push(`${id} 系统常量 ${param.key} 缺少 default_value`);
      }
    }
  }
  return issues;
}
