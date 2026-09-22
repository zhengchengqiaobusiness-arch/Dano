/**
 * 消费者包只从录制合同投影。禁止按页面再猜字段，禁止沿用 Skill 4 自编执行器。
 */

import { mkdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { isCallerParam, NEVER_CALLER_SOURCE_KINDS } from "../result-gate.mjs";

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

const HOST_ASK_INPUT_TYPES = new Set([
  "text", "textarea", "date", "radio", "checkbox", "select", "treeSelect",
]);

export function capabilityId(cap) {
  return String(cap?.capability_id || cap?.id || "").trim();
}

export function executeRef(cap, steps = []) {
  const refs = asList(cap?.request_refs);
  const exec = refs.find((ref) => String(ref?.usage || "") === "execute")
    || refs.find((ref) => !String(ref?.usage || "").trim())
    || {};
  const step = asList(steps).find((item) => item.step_id && item.step_id === exec.step_id) || {};
  return {
    method: String(exec.method || step.method || "GET").toUpperCase(),
    path: String(exec.path || step.path || ""),
    step_id: String(exec.step_id || step.step_id || ""),
    params: asList(step.params),
  };
}

function isSystemParam(item) {
  const kind = String(item?.source_kind || "");
  return item?.exposed_to_user === false || NEVER_CALLER_SOURCE_KINDS.has(kind);
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
  for (const [key, spec] of Object.entries(properties)) {
    const param = found.get(key) || {};
    if (!isCallerParam(param)) continue;
    fields.push(normalizeCallerField(key, spec, param, required.has(key) || Boolean(param.required)));
    seen.add(key);
  }
  for (const [key, param] of found) {
    if (seen.has(key) || !isCallerParam(param)) continue;
    fields.push(normalizeCallerField(key, {}, param, Boolean(param.required)));
    seen.add(key);
  }
  return fields;
}

export function isRecordedCalendarSample(value) {
  if (value === "today" || value === "now") return false;
  if (typeof value !== "string") return false;
  return /^\d{4}-\d{2}-\d{2}(?:[T\s].*)?$/.test(value.trim());
}

function copyContractPageDefault(field, spec, param) {
  if (field.type !== "date") return field;
  if (isRecordedCalendarSample(field.default)) delete field.default;
  const candidates = [
    param.page_default,
    spec.page_default,
    spec["x-dano-page-default"],
    asRecord(param.source).page_default,
  ];
  if (candidates.some((value) => String(value || "").trim() === "today")) {
    field.page_default = "today";
  }
  return field;
}

function normalizeCallerField(key, spec, param, required) {
  const source = asRecord(param.source);
  const option = asRecord(spec["x-dano-option-source"]);
  const enums = asList(spec["x-enum-options"]).length ? asList(spec["x-enum-options"]) : asList(param.enum_options);
  const endpoint = String(option.source_url || source.source_url || "");
  const childrenField = String(option.children_key || source.children_key || "").trim();
  const resultPath = String(option.result_path || source.result_path || "").trim();
  const idField = String(option.value_key || source.value_key || "").trim();
  const labelField = String(option.label_key || source.label_key || "").trim();
  const dataSource = endpoint ? {
    type: "api",
    endpoint,
    method: String(option.source_method || source.source_method || "GET").toUpperCase(),
    params: asRecord(option.params || source.params),
    ...(resultPath ? { resultPath } : {}),
    ...(idField ? { idField } : {}),
    ...(labelField ? { labelField } : {}),
    ...(childrenField ? { childrenField } : {}),
  } : null;
  const field = {
    id: String(key),
    title: String(spec.title || param.label || key),
    type: (spec.format === "date" || param.type === "date" || spec.type === "date")
      ? "date"
      : String(spec.type || param.type || "string"),
    required: Boolean(required),
    enums,
    dataSource,
    sections: asRecord(spec["x-dano-section-titles"]),
    itemProperties: asRecord(asRecord(spec.items).properties),
    path: String(param.path || ""),
    source_kind: String(param.source_kind || spec["x-dano-source-kind"] || ""),
    reason: String(param.reason || spec.description || ""),
  };
  const sectionItemTypes = asRecord(spec["x-dano-section-item-types"] || param.section_item_types || source.section_item_types);
  if (Object.keys(sectionItemTypes).length) field.sectionItemTypes = sectionItemTypes;
  const format = String(spec.format || param.format || "").trim();
  if (format) field.format = format;
  const itemType = Number(param.itemType);
  if (Number.isFinite(itemType)) field.itemType = itemType;
  const rawDefault = Object.prototype.hasOwnProperty.call(param, "default_value") && param.default_value !== undefined && param.default_value !== ""
    ? param.default_value
    : (spec.default !== undefined && spec.default !== "" ? spec.default : undefined);
  if (rawDefault !== undefined && !isRecordedCalendarSample(rawDefault)) {
    field.default = rawDefault;
  }
  return copyContractPageDefault(field, spec, param);
}

export function resolveSystemDefault(param) {
  if (!param || typeof param !== "object") return { has: false };
  if (Object.prototype.hasOwnProperty.call(param, "default_value") && param.default_value !== undefined) {
    return { has: true, value: param.default_value };
  }
  for (const key of ["value", "constant_value", "const"]) {
    if (param[key] !== undefined && param[key] !== "") return { has: true, value: param[key] };
  }
  if (String(param.source_kind || "") === "constant" && param.type === "array") {
    return { has: true, value: [] };
  }
  return { has: false };
}

function linkTargetKey(path) {
  const text = String(path || "");
  const body = text.replace(/^body\./, "").replace(/^query\./, "");
  return body.split(".").filter(Boolean).pop() || "";
}

function previousResponseFromLinks(param, links = []) {
  const key = String(param?.key || "");
  const path = String(param?.path || "");
  for (const link of asList(links)) {
    const target = String(link?.target_path || "");
    if (target !== path && linkTargetKey(target) !== key) continue;
    const fromStep = String(link.source_step_id || "").trim();
    const fromPath = String(link.source_path || "").trim();
    if (fromStep || fromPath) return { fromStep, fromPath };
  }
  return { fromStep: "", fromPath: "" };
}

export function systemParams(cap, steps = [], links = []) {
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
    if (item.source_kind === "current_user") {
      const src = asRecord(item.source);
      const identPath = String(src.source_url || src.path || "").trim();
      const resultPath = String(src.result_path || "").trim();
      if (identPath || resultPath) {
        out.identity = {
          method: String(src.source_method || "GET").toUpperCase(),
          path: identPath,
          result_path: resultPath,
        };
      }
    }
    if (item.source_kind === "previous_response") {
      const src = asRecord(item.source);
      const linked = previousResponseFromLinks(item, links);
      const fromStep = String(item.from_step_id || src.from_step_id || linked.fromStep || "").trim();
      const fromPath = String(item.from_path || src.from_path || linked.fromPath || "").trim();
      if (fromStep) out.from_step_id = fromStep;
      if (fromPath) out.from_path = fromPath;
    }
    if (item.source_kind === "generated") {
      const formula = String(asRecord(item.source).formula || item.formula || "").trim();
      if (formula) out.formula = formula;
    }
    return out;
  });
}

function identityPath(raw) {
  const text = String(raw || "").trim();
  if (!text) return "";
  try {
    return new URL(text).pathname || "";
  } catch {
    return text.replace(/^https?:\/\/[^/]+/i, "").split("?")[0];
  }
}

export function identityProbesFromDraft(draft) {
  const probes = [];
  const seen = new Set();
  const add = (method, rawPath) => {
    const path = identityPath(rawPath);
    if (!path || !path.startsWith("/")) return;
    const verb = String(method || "GET").toUpperCase() || "GET";
    const key = `${verb} ${path}`;
    if (seen.has(key)) return;
    seen.add(key);
    probes.push({ method: verb, path });
  };
  for (const cap of asList(draft?.capabilities)) {
    for (const param of asList(cap.system_params)) {
      if (String(param?.source_kind || "") !== "current_user") continue;
      const src = asRecord(param.source || param.identity);
      add(src.source_method || src.method, src.source_url || src.path);
    }
  }
  for (const step of asList(draft?.steps)) {
    for (const param of asList(step.params)) {
      if (String(param?.source_kind || "") !== "current_user") continue;
      const src = asRecord(param.source);
      add(src.source_method, src.source_url);
    }
  }
  return probes;
}

export function buildRoutes(draft) {
  const caps = asList(draft?.capabilities);
  const ids = caps.map(capabilityId).filter(Boolean);
  const indexOf = new Map(ids.map((id, index) => [id, index]));
  const idSet = new Set(ids);
  const edgeKeys = new Set();
  const outgoing = new Map();
  const indegree = new Map();
  const graphNodes = new Set();
  const addEdge = (from, to) => {
    if (!from || !to || from === to) return;
    if (!idSet.has(from) || !idSet.has(to)) return;
    const key = `${from}\0${to}`;
    if (edgeKeys.has(key)) return;
    edgeKeys.add(key);
    graphNodes.add(from);
    graphNodes.add(to);
    if (!outgoing.has(from)) outgoing.set(from, []);
    outgoing.get(from).push(to);
    indegree.set(from, indegree.get(from) || 0);
    indegree.set(to, (indegree.get(to) || 0) + 1);
  };
  for (const rel of asList(draft?.capability_relations)) {
    addEdge(
      String(rel.from_capability || rel.from || "").trim(),
      String(rel.to_capability || rel.to || "").trim(),
    );
  }
  const capIdByStep = new Map();
  for (const cap of caps) {
    const id = capabilityId(cap);
    if (!id) continue;
    for (const stepId of asList(cap.step_ids)) {
      const key = String(stepId || "").trim();
      if (key && !capIdByStep.has(key)) capIdByStep.set(key, id);
    }
    for (const ref of asList(cap.request_refs)) {
      const key = String(ref?.step_id || "").trim();
      if (key && !capIdByStep.has(key)) capIdByStep.set(key, id);
    }
  }
  for (const link of asList(draft?.links)) {
    addEdge(
      capIdByStep.get(String(link.source_step_id || "").trim()) || "",
      capIdByStep.get(String(link.target_step_id || "").trim()) || "",
    );
  }
  const byOriginal = (a, b) => (indexOf.get(a) ?? 0) - (indexOf.get(b) ?? 0);
  const queue = [...graphNodes]
    .filter((id) => (indegree.get(id) || 0) === 0)
    .sort(byOriginal);
  const chain = [];
  const seen = new Set();
  while (queue.length) {
    queue.sort(byOriginal);
    const node = queue.shift();
    if (seen.has(node)) continue;
    seen.add(node);
    chain.push(node);
    for (const next of outgoing.get(node) || []) {
      indegree.set(next, (indegree.get(next) || 0) - 1);
      if ((indegree.get(next) || 0) === 0 && !seen.has(next)) queue.push(next);
    }
  }
  for (const id of ids) {
    if (graphNodes.has(id) && !seen.has(id)) {
      chain.push(id);
      seen.add(id);
    }
  }
  for (const id of ids) {
    if (!seen.has(id)) {
      chain.push(id);
      seen.add(id);
    }
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

function isAutoItemKey(field, key, contract) {
  const needle = `${field.id}.${key}`;
  for (const item of asList(contract?.unresolved)) {
    const name = String(item.field || item.key || "");
    const parts = name.split(".").filter(Boolean);
    if (name === needle) return true;
    if (parts.length >= 2 && parts[parts.length - 2] === field.id && parts[parts.length - 1] === key) return true;
  }
  const reason = String(field.reason || "");
  return new RegExp(`按分区补[^。]*${key}|${key}[^。]*无独立控件|非调用方[^。]*${key}`).test(reason);
}

function stampArrayAutoKeys(field, unresolved) {
  if (field.type !== "array") return field;
  return {
    ...field,
    autoItemKeys: Object.keys(field.itemProperties || {}).filter((key) => (
      isAutoItemKey(field, key, { unresolved })
    )),
  };
}

export function consumerContract(draft) {
  const src = asRecord(draft);
  const steps = stripSamples(asList(src.steps));
  const unresolved = asList(src.unresolved);
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
      caller_fields: callerParams(cap, steps).map((field) => stampArrayAutoKeys(field, unresolved)),
      system_params: systemParams(cap, steps, asList(src.links)),
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
  if (field.dataSource) return field.dataSource.childrenField ? "treeSelect" : "select";
  if (field.enums.length) return "radio";
  if (field.type === "date") return "date";
  if (field.type === "array") return "table";
  if (field.type === "number" || field.type === "integer") return "number";
  if (/文本域|多行/.test(String(field.reason || ""))) return "textarea";
  return "text";
}

export function isWriteCap(cap) {
  const kind = String(cap?.kind || "");
  if (/create|update|delete|submit|write|mutation/i.test(kind)) return true;
  if (/query|read/i.test(kind)) return false;
  return /^(POST|PUT|PATCH|DELETE)$/i.test(String(cap?.execute?.method || ""));
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

function isInheritedField(field) {
  return field.source_kind === "page_default" && field.page_default !== "today";
}

function visibleItemKeys(field, contract) {
  const auto = new Set(asList(field.autoItemKeys).map(String));
  return Object.keys(field.itemProperties || {}).filter((key) => (
    !auto.has(key) && !isAutoItemKey(field, key, contract)
  ));
}

function howToFill(field, contract) {
  if (field.dataSource) return "仅当该字段尚未确定时先 `python3 scripts/flow.py --list-options <capability_id> <field>` 拉候选；说法能唯一对上 label 则直接用 id，否则再让用户选";
  if (field.enums.length) return `按合同枚举选 id：${enumText(field)}。说法能唯一对上 label 或 id 则直接用，不必再问`;
  if (field.source_kind === "selected_record_identity") return "从上一张原始表里能唯一对上说法的一行取合同身份键；用户指向自己时用本包 current_user 主键去对，不要问「你是谁」，不要编造，不要把系统 id 写进 ask";
  if (field.source_kind === "previous_response") return "用本对话上一步已确认结果里的同名字段；runtime 也可按 links 带入";
  if (isInheritedField(field)) return "默认用本对话上一步已确认的同名字段，调用方可改。已确定则写入 default；读能力不进 questions[]，写能力题仍在";
  if (field.autoStamped) return "合同标明自动补齐，不要向用户要";
  if (field.type === "date") {
    return field.page_default === "today"
      ? "按 yyyy-MM-dd 填写；页面日期控件默认当日，可改。说法已唯一解析或上一步同名 id 已确定 → 写入 input-json；写操作仍留在全量确认表 default"
      : "按 yyyy-MM-dd 填写；说法已唯一解析出日期 → 写入 input-json。查询/筛选日期禁止改成今天。读能力不能解析且该日期是调用方必填 → 问该题，不要停；写操作不能解析则省略 default 仍留在全量表";
  }
  if (field.type === "array") {
    const cols = visibleItemKeys(field, contract).map((key) => `\`${key}\` ${(field.itemProperties[key] || {}).title || key}`);
    const sections = Object.keys(field.sections);
    const bits = [];
    if (sections.length) bits.push(`分区 ${sections.join(" / ")}`);
    if (cols.length) bits.push(`每行列 ${cols.join(" / ")}`);
    bits.push("提问用 textarea 收同一字段 id，行格式见冻结提问；runtime 组装回数组");
    bits.push("只问可见列；合同 unresolved 标明自动补的列由 runtime 补");
    return bits.join("；");
  }
  if (field.reason) return field.reason;
  return "向用户收集真实内容，禁止编造";
}

function allowedDefaultText(field, contract) {
  const parts = [];
  if (Object.prototype.hasOwnProperty.call(field, "default")) {
    parts.push(`合同默认 ${JSON.stringify(field.default)}`);
  }
  if (field.enums.length) {
    parts.push(`枚举只能用 ${enumText(field)}；说法唯一匹配即可作为 default`);
  }
  if (field.dataSource) {
    parts.push("只能用本次 `--list-options` 返回并唯一对上或被用户选中的 id");
  }
  if (field.source_kind === "selected_record_identity") {
    parts.push("可用上一张原始表里唯一对上的行的合同身份键");
  }
  if (field.source_kind === "previous_response" || isInheritedField(field)) {
    parts.push("可用本对话上一步已确认的同名字段");
  }
  if (field.type === "date") {
    parts.push(field.page_default === "today"
      ? "页面日期控件默认当日，可改；格式 yyyy-MM-dd；用户已给出日期则用用户日期"
      : "日期格式 yyyy-MM-dd；说法唯一解析则写入 input-json，不进 questions[]，查询周期禁止写成今天");
  }
  if (field.type === "array") {
    const cols = visibleItemKeys(field, contract).map((key) => (field.itemProperties[key] || {}).title || key);
    const sections = Object.keys(field.sections);
    if (sections.length) parts.push(`分区 ${sections.join(" / ")}`);
    if (cols.length) parts.push(`可见列 ${cols.join(" / ")}`);
    parts.push("行内容必须向用户收集；自动补列不要问");
  }
  if (field.autoStamped) {
    return "合同标明自动补齐，不要向用户要";
  }
  const usable = Object.prototype.hasOwnProperty.call(field, "default")
    || field.page_default === "today"
    || field.enums.length
    || field.dataSource
    || field.source_kind === "selected_record_identity"
    || field.source_kind === "previous_response"
    || isInheritedField(field);
  if (!usable) {
    return "无合同默认。本对话已确认的值可用。没有合法来源则向用户收集，禁止编造，禁止空串 default";
  }
  if (!Object.prototype.hasOwnProperty.call(field, "default")) {
    return `无合同默认。${parts.join("；")}。此外可用本对话用户已确认的值。禁止编造`;
  }
  return `${parts.join("；")}。此外可用本对话用户已确认的值。禁止编造`;
}

function askRequired(field) {
  return Boolean(field.required) && field.page_default !== "today";
}

function fieldFillRow(field, contract) {
  return `| \`${field.id}\` | ${field.title} | ${askRequired(field) ? "是" : "否"} | ${fieldControl(field)} | ${howToFill(field, contract)} | ${allowedDefaultText(field, contract)} |`;
}

function askInputType(field) {
  const control = fieldControl(field);
  if (control === "number") return "text";
  if (control === "table") return "textarea";
  return HOST_ASK_INPUT_TYPES.has(control) ? control : "text";
}

function visibleItemTitles(field, contract) {
  return visibleItemKeys(field, contract).map((key) => String((field.itemProperties[key] || {}).title || key));
}

function arrayLineRecipe(field, contract) {
  const titles = visibleItemTitles(field, contract);
  const sections = Object.keys(field.sections || {});
  if (sections.length && titles.length) {
    return `每行一条：分区标题|||${titles.join("|||")}；分区只能是 ${sections.join(" / ")}`;
  }
  if (titles.length) return `每行一条：${titles.join("|||")}`;
  return "每行一条，或提交 JSON 数组";
}

function askQuestion(field, contract) {
  const parts = [field.title];
  if (field.type === "array") {
    parts.push(arrayLineRecipe(field, contract));
  } else if (field.enums.length) {
    parts.push(enumText(field));
  }
  if (field.page_default === "today") {
    parts.push("未选则按当天");
  } else if (askRequired(field)) {
    parts.push("必填");
  }
  return `${parts.join("。")}。`;
}

export function fieldAskSpec(field, contract) {
  const spec = {
    id: field.id,
    question: askQuestion(field, contract),
    inputType: askInputType(field),
    required: askRequired(field),
  };
  if (field.enums.length) spec.options = field.enums.map(enumOption);
  if (field.type === "date") spec.dateFormat = "yyyy-MM-dd";
  if (Object.prototype.hasOwnProperty.call(field, "default")) spec.default = field.default;
  if (field.page_default === "today" && !Object.prototype.hasOwnProperty.call(spec, "default")) {
    spec.default = "today";
  }
  if (spec.inputType === "textarea") spec.fieldAssist = true;
  return spec;
}

export function frozenAskForm(cap, contract) {
  return {
    title: cap.name || cap.capability_id,
    questions: (cap.caller_fields || []).map((field) => fieldAskSpec(field, contract)),
  };
}

function nestedFillRows(field, contract) {
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
    autoStamped: isAutoItemKey(field, key, contract),
    reason: isAutoItemKey(field, key, contract)
      ? "合同标明自动补齐，不要向用户要"
      : "数组行内字段，必须向用户收集真实内容",
  }, contract));
}

function systemFillText(param) {
  if (param.source_kind === "current_user") return "运行时取当前登录用户；不要向用户要";
  if (Object.prototype.hasOwnProperty.call(param, "default_value")) {
    return `合同值 ${JSON.stringify(param.default_value)}；不要向用户要`;
  }
  if (param.source_kind === "constant") return "合同常量；必须带 default_value，由 runtime 自动填";
  if (param.source_kind === "generated") {
    if (param.formula) return `按合同公式 ${param.formula} 生成；不要向用户要`;
    if (Object.prototype.hasOwnProperty.call(param, "default_value")) {
      return `合同值 ${JSON.stringify(param.default_value)}；不要向用户要`;
    }
    return "能力未给出生成规则，runtime 不填";
  }
  return `${param.source_kind || "system"}；不要向用户要`;
}

export function renderInputForms(contract) {
  const lines = [
    "# 调用方表单",
    "",
    "只收集合同 `caller_fields`。系统字段由 `scripts/runtime.py` 按合同填充。",
    "动态字段必须保留完整 `dataSource`。助手仅当该字段尚未确定时运行 `python3 scripts/flow.py --list-options <capability_id> <field>` 拉候选。说法能唯一对上则直接用 id。不要让问句自己裸打选项接口，不要把 dataSource 放进 ask。",
    "可用默认值只允许：合同 `default`、本对话用户已确认的值、说法唯一对上的枚举/选项 id、本次 `--list-options` 选中的 id、写操作日期页面当日、合同标明继承上一步的值。有合法来源必须写入 default。其它一律向用户收集，禁止编造。",
    "",
  ];
  for (const cap of contract.capabilities) {
    lines.push(`## ${cap.name || cap.capability_id}（\`${cap.capability_id}\`）`, "");
    lines.push("| 字段 | 标题 | 必填 | 控件 | 怎么填 | 可用默认值 |", "|---|---|---|---|---|---|");
    for (const field of cap.caller_fields) {
      lines.push(fieldFillRow(field, contract));
      for (const row of nestedFillRows(field, contract)) lines.push(row);
    }
    lines.push("");
    for (const field of cap.caller_fields) {
      lines.push(`### ${field.title}（\`${field.id}\`）`, "");
      const spec = fieldAskSpec(field, contract);
      if (field.dataSource) spec.dataSource = field.dataSource;
      lines.push(`可用默认值：${allowedDefaultText(field, contract)}`, "");
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
    if (isWriteCap(cap)) {
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
      lines.push(fieldFillRow(field, contract));
      for (const row of nestedFillRows(field, contract)) lines.push(row);
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

function renderImmediateHowTo(contract) {
  const defaultRoute = contract.routes.find((item) => item.route_id === "default") || contract.routes[0];
  const steps = asList(defaultRoute?.steps);
  const hasToday = (contract.capabilities || []).some((cap) => (
    isWriteCap(cap) && (cap.caller_fields || []).some((field) => field.page_default === "today")
  ));
  const hasDynamic = (contract.capabilities || []).some((cap) => (
    (cap.caller_fields || []).some((field) => field.dataSource)
  ));
  const multi = (contract.capabilities || []).length > 1 || steps.length > 1;
  const lines = [
    "只读这一节就能办。读完禁止再读本文件，禁止读 `references/`，禁止 ls / cat / 探路。",
    "查询不要确认卡：读能力禁止确认卡。说法、合同身份、上一步原始表能填的立刻执行。合同调用方必填尚未确定 → 必须问缺的那些题（动态字段先 `--list-options` 再 ask），不要停。不要问合同已标 current_user / selected_record 的筛选，也不要把系统栏改口问出来。",
    "写操作必须把该能力「冻结提问」代码块整份拿去调用 `ask_user_question`。questions[] 的 id、question、inputType、options、dateFormat、顺序必须与冻结 JSON 完全相同，禁止删 id，禁止改问句。只允许改各题 default。冻结目录里每一题都要出现（含已有 default 的日期、空的可选、附件），空可选省略 default，禁止编「无 / 暂无 / 请填写」。少一个 id = 违规。用户提交后再弹确认卡，确认后再 `--confirm`。禁止按口头提到的字段瘦表，禁止把标题改成能力里没有的叫法。",
  ];
  if (multi) {
    lines.push("禁止 `--route default` 一次跑完整条链。逐步 `--route <capability_id>`。");
  }
  lines.push(
    "选路：matched = 本包能力里 name / title / intent 被当前说法覆盖到的集合。1 个 → 只跑该原子路线。≥2 个 → 取 `default.steps` 里覆盖全部 matched 的最短连续切片（夹在中间且有已确认 relation/link 的步骤一起跑，切片外不跑）。0 个且已选本 skill → 按 `default.steps` 全程逐步办理。不要为某个业务口令写死 capability_id。",
    "每步先填槽。读能力禁止确认卡。已确定的立刻执行。调用方必填未定必须问缺槽。写能力先按合同把已确定的值写入冻结表对应题的 default，再把整份冻结 questions[] 弹出去（禁止删 id，禁止改问句），提交后再确认。读成功必须先发原始结果表（列以本次返回为准），再进入切片下一步。",
    "读能力已唯一确定的字段不要放进本次 questions[]。还有未定的调用方必填 → 只问缺的那些 id，不要整步都不问、也不要停。写操作冻结 JSON 里每一题都进 questions[]，已确定的写入该题 `default`，不要从确认卡拿掉。有 default 还把已确定的读槽再问一遍 = 违反已确定则不问。",
    "有合法值必须写入该题 `default`（非空）。禁止空字符串，禁止编「无 / 暂无 / 请填写 / 请审批」。读能力无合法值：调用方必填必须问。写能力无合法值：必填省略 default 仍留在表里；可选未提也留在表里（可空）。禁止因为没值就删掉写表的题。",
    "用户说法里带先查再决定是否写：仍按 matched≥2 的切片跑。写步骤是否执行，用当次查询返回的真实列判定用户条件；判定不了再问条件。先查某个业务日期再决定是否写：写入日期继承该查询日；说法里不是业务日期的那部分填对应正文/明细调用方槽，不把已确定的业务日期改成当天。",
  );
  if (hasDynamic) {
    lines.push("动态字段仅当尚未确定时才跑 `cd <本 SKILL.md 所在目录> && python3 scripts/flow.py --list-options <capability_id> <field>`，把返回的 `options` 写进该字段。说法能唯一对上则直接用 id。空列表或失败则停，不准猜第一条。不要把 dataSource 放进 ask_user_question。");
  } else {
    lines.push("不要把 dataSource 放进 ask_user_question。");
  }
  if (hasToday) {
    lines.push("写操作日期：冻结 JSON 的 `default` 是 `today`。调用 ask 前必须先跑 `date +%F`，用这条输出换成当天 yyyy-MM-dd，`required` 保持 false。用户说法已给出日期或上一步同名 id 已确定 → 用那个日期作 default，仍留在全量确认表里，不要改成今天。禁止用合同、INPUT_FORMS、录制样本里的日期冒充当天，禁止自己猜年份。不要改回必填。runtime 也会把未选的写操作日期填成当天。");
  }
  lines.push("查询/筛选周期没有页面当日默认，禁止把查询日期改成今天；说法已唯一解析出日期 → 写入 input-json，读能力不要放进本次 questions[]。不能解析且该日期是调用方必填 → 问该题，不要停。写操作日期即使已从查询继承或说法解析，也留在冻结表里作 default，不要从 questions[] 拿掉。同一能力里成对的起止日期调用方字段：单一日期说法同时填两端（同一天区间）。");
  lines.push("系统字段由 `scripts/runtime.py` 按合同自动填，不要向用户要。用户指向自己（我/替我/本人/当前登录人）时：本包 current_user 能填的同名槽先填，不要问「你是谁」。仍缺的调用方必填再问。selected_record 指向自己时用合同身份主键去上一张原始表匹配，唯一命中直接用，不要用表里其它人名反问。");
  lines.push("问句用冻结 JSON 的 `question` 原文（能力字段 title + 行格式），禁止改问句、禁止改 `inputType`，禁止把内部 path 或属性键写进宿主标签。数组只问可见列表头；合同 unresolved 标明自动补的列由 runtime 补，不要问。");
  return lines;
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
    ...renderImmediateHowTo(contract),
    "查询成功必须回原始结果表：优先原样复制脚本返回的 `table`。没有 `table` 时按返回列表字段画 Markdown 表。列名和单元格必须与原始返回相同，禁止改写成短条，禁止漏行漏列，禁止为某一页发明口径。",
    "这份 JSON 已经按宿主控件投影。能力层的 table 在提问里是同一字段 id 的 textarea；答案由 runtime 组装回数组。读能力已确定则不要弹出冻结提问；缺调用方必填则只问缺的那些题。写操作调用 `ask_user_question` 时必须整份带上该能力冻结 JSON 的 questions[]，禁止删 id，禁止改问句，已确定的值写入 default，没值的题仍出现。",
    "",
    "## 冻结提问",
    "",
    "下面是冻结提问目录。写能力调用时必须整份带上。`id` / `question` / `inputType` / `options` / `dateFormat` 原样用，只改 default，禁止删 id、禁止改问句、禁止改顺序。读能力缺调用方必填时只带尚未确定的题；已确定的不要问。动态字段的 options 必须是本次 `--list-options` 返回值，不要自编表单，不要带 dataSource。",
    "",
  ];
  for (const cap of contract.capabilities) {
    lines.push(`### \`${cap.capability_id}\``, "");
    if ((cap.caller_fields || []).length) {
      lines.push("```json", JSON.stringify(frozenAskForm(cap, contract), null, 2), "```", "");
    } else {
      lines.push("（无调用方字段，本能力不用提问。）", "");
    }
  }
  lines.push(
    "## 默认值规则",
    "",
    "可用默认值只允许下面这些，其它一律不准用：",
    "",
    "1. 该字段合同已经写出的 `default` / `default_value`",
    "2. 本对话里用户已经确认的值",
    "3. 枚举字段：合同列出的 id；说法唯一匹配 label 或 id 即可作为 default",
    "4. 动态字段：本次 `--list-options` 返回并唯一对上或被用户选中的 id",
    "5. 写操作且合同 `page_default=today` 的日期：当天 yyyy-MM-dd（调用前换成当天）；用户已给出日期则用用户日期",
    "6. 合同标明继承上一步，或上一步已确认的同名 id",
    "7. selected_record：上一张原始表里唯一对上的行的合同身份键",
    "",
    "有合法来源必须写入 `default`，禁止空字符串。读能力：已确定的不进本次 questions[]；调用方必填未定必须问，不要停。写能力：冻结 JSON 每一题都进 questions[]，禁止删 id，禁止改问句；没有合法来源就省略 default，题仍在。已唯一确定的值写入 input-json 和写确认表的 default。禁止编造「无」「示例」「请审批」。",
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
    "用户意图只对上某条能力的 `name` / `intent` 时走该原子路线；同时对上多条则按 `default.steps` 切片办理。不要为某个业务口令写死 capability_id。",
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
    "没有 `python3` 再用 `python`。读能力不要确认卡；调用方必填未定必须问缺槽。写操作必须整份带上冻结提问 JSON（禁止删 id，禁止改问句），确认后再执行。",
    "",
  );
  for (const cap of contract.capabilities) {
    const write = isWriteCap(cap);
    lines.push(`### ${cap.name || cap.capability_id}`, "");
    lines.push(`capability_id: \`${cap.capability_id}\``, "");
    if (cap.intent) lines.push(`intent: ${cap.intent}`, "");
    lines.push(`Done when: \`${cap.execute.method} ${cap.execute.path}\` 已返回业务成功。`, "");
    lines.push("调用方字段（目录必须完整；读能力已确定的不进本次 questions[]，未定的调用方必填必须问；写操作冻结 JSON 每一题都进 questions[]，禁止删 id）:", "");
    if (cap.caller_fields.length) {
      lines.push("| 字段 | 标题 | 必填 | 控件 | 怎么填 | 可用默认值 |", "|---|---|---|---|---|---|");
      for (const field of cap.caller_fields) {
        lines.push(fieldFillRow(field, contract));
        for (const row of nestedFillRows(field, contract)) lines.push(row);
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
      lines.push("", "写操作必须整份带上该能力冻结提问 JSON（禁止删 id），再单独确认：`{ \"confirm\": true, \"formIds\": [\"<answered.formId>\"] }`，确认后再 `--confirm`。");
    }
    lines.push("");
  }
  lines.push(
    "## 成功、失败与停止",
    "",
    "- 任一步失败即停",
    "- 查询成功必须回原始结果表：优先原样复制脚本返回的 `table`，列名和单元格与原始返回相同，禁止改写成短条",
    "- 只有脚本 JSON `code=AuthExpired` 才停问一次 token。提问只用 `{\"questions\":[{\"id\":\"token\",\"question\":\"请粘贴新的访问令牌\",\"inputType\":\"text\",\"required\":true}]}`，不要加 title，不要自己补 default。拿到后用 `DANO_AUTH_HEADERS` 覆盖再跑同一条命令；不要改文件，不要再问第二次",
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
  if (!/已确定|不问直接执行|先按合同填槽/.test(text)) reasons.push("缺少已确定则不问");
  if (!/不进本次 questions|不要放进本次 questions/.test(text)) reasons.push("缺少已确定不进 questions");
  if (!/写入该题 `default`|有合法值必须写入|写入 `default`/.test(text)) reasons.push("缺少写入 default");
  if ((contract.capabilities || []).some((cap) => isWriteCap(cap))) {
    if (!/全量|禁止删 id|整份带上/.test(text)) reasons.push("缺少写确认全量表单");
    if (!/禁止改问句/.test(text)) reasons.push("缺少写操作禁止改问句");
    if (/不是每次原样弹出|不要把整张空表当每次必问清单/.test(text)) {
      reasons.push("写手册仍允许瘦表");
    }
  }
  if ((contract.capabilities || []).some((cap) => !isWriteCap(cap))) {
    if (!/不要确认卡|禁止确认卡/.test(text)) reasons.push("缺少读能力不要确认卡");
    if (!/必须问|缺槽|未定必须问/.test(text)) reasons.push("缺少读能力缺槽要问");
    if (/读能力禁止提问|填不满必填槽就停|填不满就停，不要问/.test(text)) {
      reasons.push("读能力缺槽被写成绝对禁止提问");
    }
  }
  if (/不要先查/.test(text)) reasons.push("禁止写不要先查");
  if (/第一次工具调用必须是 ask_user_question/.test(text)) reasons.push("禁止无条件第一次工具必须提问");
  if ((contract.routes || []).some((item) => item.route_id === "default") && (contract.capabilities || []).length > 1) {
    if (!/禁止 `--route default`|禁止 --route default/.test(text)) reasons.push("缺少禁止提前 default");
    if (!/原始结果表/.test(text)) reasons.push("缺少原始结果表");
  }
  const hasToday = (contract.capabilities || []).some((cap) => (
    (cap.caller_fields || []).some((field) => field.page_default === "today")
  ));
  if (hasToday) {
    if (!/换成当天/.test(text)) reasons.push("缺少日期 today 换成当天");
    if (!/"default": "today"/.test(text) && !/"default":"today"/.test(text)) {
      reasons.push("冻结提问缺少日期 default today");
    }
  }
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
    const expectedSys = systemParams(cap, sourceDraft.steps, sourceDraft.links);
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
