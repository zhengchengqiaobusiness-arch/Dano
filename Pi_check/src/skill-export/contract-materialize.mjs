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

export function callerParams(cap, steps = []) {
  const exec = executeRef(cap, steps);
  const schema = asRecord(cap?.input_schema);
  const properties = asRecord(schema.properties);
  const required = new Set(asList(schema.required).map(String));
  const systemKeys = new Set(exec.params.filter(isSystemParam).map((item) => String(item.key)));
  if (Object.keys(properties).length) {
    return Object.entries(properties)
      .filter(([key]) => !systemKeys.has(key))
      .map(([key, spec]) => {
        const param = exec.params.find((item) => String(item.key) === key) || {};
        return normalizeCallerField(key, spec, param, required.has(key) || Boolean(param.required));
      });
  }
  return exec.params
    .filter((item) => !isSystemParam(item))
    .map((item) => normalizeCallerField(item.key, {}, item, Boolean(item.required)));
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
  return {
    id: String(key),
    title: String(spec.title || param.label || key),
    type: String(spec.type || param.type || "string"),
    required: Boolean(required),
    enums,
    dataSource,
    sections: asRecord(spec["x-dano-section-titles"]),
    itemProperties: asRecord(asRecord(spec.items).properties),
    path: String(param.path || ""),
  };
}

export function systemParams(cap, steps = []) {
  return executeRef(cap, steps).params.filter(isSystemParam).map((item) => {
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
  if (/content|意见|备注|总结|计划|问题/i.test(field.title)) return "textarea";
  return "text";
}

export function renderInputForms(contract) {
  const lines = [
    "# 调用方表单",
    "",
    "只收集合同 `caller_fields`。系统字段由 `scripts/runtime.py` 按合同填充。",
    "动态字段必须保留完整 `dataSource`。助手先运行 `python scripts/flow.py --list-options <capability_id> <field>` 拉候选，再提问。不要让问句自己裸打选项接口。",
    "",
  ];
  for (const cap of contract.capabilities) {
    lines.push(`## ${cap.name || cap.capability_id}（\`${cap.capability_id}\`）`, "");
    lines.push("| 字段 | 标题 | 控件 | 必填 |", "|---|---|---|---|");
    for (const field of cap.caller_fields) {
      lines.push(`| \`${field.id}\` | ${field.title} | ${fieldControl(field)} | ${field.required ? "是" : "否"} |`);
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
      if (field.enums.length) spec.options = field.enums.map((item) => (
        item && typeof item === "object"
          ? { id: item.value ?? item.id, label: item.label || String(item.value ?? item.id) }
          : { id: item, label: String(item) }
      ));
      if (field.dataSource) spec.dataSource = field.dataSource;
      if (field.type === "date") spec.dateFormat = "yyyy-MM-dd";
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
      lines.push("```json", JSON.stringify(spec, null, 2), "```", "");
      if (field.type === "array" && Object.keys(field.sections).length) {
        lines.push("各分区行放进同一个数组，并用 `section` 标明分区标题，例如：", "");
        lines.push("```json");
        lines.push(JSON.stringify({
          [field.id]: Object.keys(field.sections).map((title) => ({
            section: title,
            content: "",
            ...(field.itemProperties.progress ? { progress: 0 } : {}),
          })),
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
    lines.push("", "调用方字段:", "");
    lines.push("| 字段 | 标题 | 必填 |", "|---|---|---|");
    for (const field of cap.caller_fields) {
      lines.push(`| \`${field.id}\` | ${field.title} | ${field.required ? "是" : "否"} |`);
    }
    if (cap.system_params.length) {
      lines.push("", "系统字段（不要向调用方要）:", "");
      lines.push("| 字段 | 来源 |", "|---|---|");
      for (const param of cap.system_params) {
        lines.push(`| \`${param.key}\` | ${param.source_kind || "system"} |`);
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
    "python scripts/flow.py --list-options <capability_id> <field>",
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
  const defaultRoute = contract.routes.find((item) => item.route_id === "default") || contract.routes[0];
  const lines = [
    "---",
    `name: ${title}`,
    `description: ${names.join("、") || title}。按本页录制能力办理，字段以 references/CONTRACT.json 为准。`,
    "---",
    "",
    "## 适用场景",
    "",
  ];
  if (names.length) {
    for (const name of names) lines.push(`- ${name}`);
  } else {
    lines.push("- 按录制能力办理本页事务");
  }
  lines.push(
    "",
    "## 不适用场景",
    "",
    "- 合同未声明的编辑、删除、审批或其它页面能力",
    "",
    "## 选择工作流",
    "",
    "默认完整办理：按 `default` 路线依次执行合同能力。",
  );
  if (defaultRoute?.steps?.length) {
    lines.push("", `默认链：${defaultRoute.steps.join(" → ")}`);
  }
  lines.push("", "| 路线 | 说明 |", "|---|---|");
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
    "python scripts/flow.py --route default --input-json '{...}' --confirm",
    "python scripts/flow.py --list-options <capability_id> <field>",
    "```",
    "",
  );
  for (const cap of contract.capabilities) {
    const write = /create|update|delete|submit|write/i.test(cap.kind || "");
    lines.push(`### ${cap.name || cap.capability_id}`, "");
    lines.push(`Done when: \`${cap.execute.method} ${cap.execute.path}\` 已返回业务成功。`, "");
    lines.push("调用方字段（必须全部按合同收集，不准合并或省略）:", "");
    for (const field of cap.caller_fields) {
      const extra = field.dataSource ? "；先 `--list-options` 再提问" : "";
      lines.push(`- \`${field.id}\` ${field.title}${field.required ? "（必填）" : ""}${extra}`);
    }
    if (!cap.caller_fields.length) lines.push("- （无调用方字段）");
    if (write) lines.push("", "写入必须确认后再 `--confirm`。");
    lines.push("");
  }
  lines.push(
    "## 成功、失败与停止",
    "",
    "- 任一步失败即停",
    "- 没有本包凭证或 401 / 账号未登录 → 停问要 token",
    "- 选项失败或空列表 → 停问",
    "",
    "## 按需读取资源",
    "",
    "- 字段和控件：`references/INPUT_FORMS.md`",
    "- 能力与执行路径：`references/CAPABILITIES.md` / `references/CONTRACT.json`",
    "- 活选项：`references/OPTIONS.md`",
    "",
    "## 鉴权",
    "",
    "- 使用 `config/auth.local.json` 或环境变量 `DANO_AUTH_HEADERS`",
    "- 只通过 `scripts/flow.py` / `scripts/client.py` 带出完整头",
    "- 不要把 token 写入手册或对话",
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
  for (const section of ["选择工作流", "执行协议", "按需读取资源", "鉴权"]) {
    if (!text.includes(section)) return false;
  }
  for (const cap of contract.capabilities || []) {
    if (!text.includes(cap.capability_id)) return false;
    for (const field of cap.caller_fields || []) {
      if (!text.includes(field.id)) return false;
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
  return handbookIsFaithful(skill4Text, contract) ? skill4Text : materializedText;
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
    const exec = executeRef(cap, sourceDraft.steps);
    if (exec.path && String(got.execute?.path || "") !== exec.path) {
      issues.push(`${id} 执行路径不一致`);
    }
  }
  return issues;
}
