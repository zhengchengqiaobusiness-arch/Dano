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
  for (const [key, spec] of Object.entries(properties)) {
    const param = found.get(key) || {};
    fields.push(normalizeCallerField(key, spec, param, required.has(key) || Boolean(param.required)));
    seen.add(key);
  }
  for (const [key, param] of found) {
    if (seen.has(key) || param?.exposed_to_user === false) continue;
    fields.push(normalizeCallerField(key, {}, param, Boolean(param.required)));
    seen.add(key);
  }
  return fields;
}

function normalizeCallerField(key, spec, param, required) {
  const source = asRecord(param.source);
  const option = asRecord(spec["x-dano-option-source"]);
  const enums = asList(spec["x-enum-options"]).length ? asList(spec["x-enum-options"]) : asList(param.enum_options);
  const endpoint = String(option.source_url || source.source_url || "");
  const childrenField = String(option.children_key || source.children_key || "");
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
  if (Object.prototype.hasOwnProperty.call(param, "default_value") && param.default_value !== undefined && param.default_value !== "") {
    field.default = param.default_value;
  } else if (spec.default !== undefined && spec.default !== "") {
    field.default = spec.default;
  }
  return field;
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
    if (Object.prototype.hasOwnProperty.call(item, "default_value")) {
      out.default_value = item.default_value;
    }
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
  if (field.type === "date") return "按 yyyy-MM-dd 向用户收集真实日期";
  if (field.type === "array") {
    const cols = Object.entries(field.itemProperties).map(([key, item]) => `\`${key}\` ${item.title || key}`);
    const sections = Object.keys(field.sections);
    const bits = [];
    if (sections.length) bits.push(`分区 ${sections.join(" / ")}`);
    if (cols.length) bits.push(`每行列 ${cols.join(" / ")}`);
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
    parts.push("日期格式 yyyy-MM-dd，不能编造今天");
  }
  if (field.type === "array") {
    const cols = Object.entries(field.itemProperties).map(([key, item]) => item.title || key);
    const sections = Object.keys(field.sections);
    if (sections.length) parts.push(`分区 ${sections.join(" / ")}`);
    if (cols.length) parts.push(`行列 ${cols.join(" / ")}`);
    parts.push("行内容必须向用户收集");
  }
  const usable = Object.prototype.hasOwnProperty.call(field, "default")
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
  if (param.source_kind === "constant") return "合同常量；不要向用户要";
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
      const spec = {
        id: field.id,
        question: field.title,
        inputType: fieldControl(field),
        required: field.required,
      };
      if (field.enums.length) spec.options = field.enums.map(enumOption);
      if (field.dataSource) spec.dataSource = field.dataSource;
      if (field.type === "date") spec.dateFormat = "yyyy-MM-dd";
      if (Object.prototype.hasOwnProperty.call(field, "default")) spec.default = field.default;
      if (field.type === "array") {
        const columns = Object.entries(field.itemProperties).map(([key, item]) => ({
          id: key,
          label: item.title || key,
        }));
        spec.columns = columns;
        if (Object.keys(field.sections).length) {
          spec.sections = Object.entries(field.sections).map(([title]) => ({ title, columns }));
        }
      }
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
    "读完本文件后按「选择工作流」选路线，立刻按该能力全部 `caller_fields` 向用户收集。",
    "禁止 ls，禁止先读 `references/`，禁止先翻 `CONTRACT.json`，禁止先跑脚本探路。",
    "每个字段怎么填、哪些默认能用，以本文件「执行协议」表格为准。能力 `input_schema` 和调用方 params 一个都不能漏。",
    "",
    "## 默认值规则",
    "",
    "只允许下面这些默认值，其它一律不准用：",
    "",
    "1. 该字段合同已经写出的 `default`（见各字段「可用默认值」列）",
    "2. 本对话里用户已经确认的值",
    "3. 枚举字段：只能用合同列出的 id",
    "4. 动态字段：只能用本次 `python3 scripts/flow.py --list-options <capability_id> <field>` 返回并被用户选中的 id",
    "",
    "没有可用默认值的字段必须向用户收集真实内容。禁止编造「无」「示例」「今天」「请审批」。",
    "",
    "## 适用场景",
    "",
  ];
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
    "python3 scripts/flow.py --route <route_id> --input-json '{...}' --confirm",
    "python3 scripts/flow.py --list-options <capability_id> <field>",
    "```",
    "",
    "没有 `python3` 再用 `python`。字段收齐并确认后再执行。",
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
      lines.push("", "系统字段（不要向用户要，运行时按能力合同填充）:", "");
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
    "- 没有本包凭证或 401 / 账号未登录 → 停问一次 token，用 `DANO_AUTH_HEADERS` 覆盖后再执行同一条命令；不要改文件，不要再问第二次",
    "- 选项失败或空列表 → 停问",
    "",
    "## 按需读取资源",
    "",
    "默认不要读本包其它文件。",
    "只有提问控件失败或脚本报错时，再读 `references/INPUT_FORMS.md`。",
    "禁止一开始就 ls、读 `CONTRACT.json` / `CAPABILITIES.md` / `OPTIONS.md`。",
    "",
    "## 鉴权",
    "",
    "- 先用本包 `config/auth.local.json` 直接执行",
    "- 过期时停问一次 token，然后：",
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

export function handbookIsFaithful(text, contract) {
  if (!text) return false;
  for (const section of ["立刻办理", "选择工作流", "执行协议", "按需读取资源", "鉴权"]) {
    if (!text.includes(section)) return false;
  }
  if (!text.includes("可用默认值")) return false;
  if (/字段以 references\/CONTRACT|先阅读全部 references/.test(text)) return false;
  for (const cap of contract.capabilities || []) {
    if (!text.includes(cap.capability_id)) return false;
    for (const field of cap.caller_fields || []) {
      if (!text.includes(`\`${field.id}\``)) return false;
      for (const key of Object.keys(field.itemProperties || {})) {
        if (!text.includes(`${field.id}.${key}`) && !text.includes(`\`${key}\``)) return false;
      }
    }
    for (const param of cap.system_params || []) {
      if (param.key && !text.includes(String(param.key))) return false;
    }
    for (const key of schemaPropertyKeys(cap)) {
      if (!text.includes(key)) return false;
    }
  }
  for (const item of contract.routes || []) {
    if (item.route_id && !text.includes(`\`${item.route_id}\``)) return false;
  }
  if ((contract.capabilities || []).some((cap) => (cap.caller_fields || []).some((field) => field.dataSource))) {
    if (!text.includes("--list-options")) return false;
  }
  return true;
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
  }
  return issues;
}
