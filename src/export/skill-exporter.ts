import path from "node:path";
import { createHash } from "node:crypto";
import { chmod, mkdir, readFile, writeFile } from "node:fs/promises";
import type { CapabilityContract, CapabilityRoute, InputFormField } from "../domain.js";
import { normalizeCatalog } from "../catalog/normalize.js";
import { buildApprovedRoutes } from "../planner/routes.js";
import { writeJson } from "../utils.js";

const operationNames: Record<CapabilityContract["operation"], string> = {
  query: "查询", create: "新建", update: "修改", review: "审核", delete: "删除",
  authenticate: "认证", upload: "上传", download: "下载", action: "业务动作", unknown: "未识别"
};

const sourceNames: Record<InputFormField["source"], string> = {
  caller: "调用方提供", fixed: "固定规则", session: "会话环境", generated: "运行时生成",
  computed: "计算得到", binding: "上游绑定", system: "业务系统处理"
};

function safeCell(value: unknown) {
  return String(value ?? "").replace(/\|/g, "\\|").replace(/\r?\n/g, " ");
}

export function normalizeSkillName(value: string) {
  const ascii = value.normalize("NFKD").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 64);
  return ascii || `business-skill-${createHash("sha256").update(value).digest("hex").slice(0, 8)}`;
}

function inputType(field: InputFormField) {
  if (field.widget === "json") return "textarea";
  if (field.widget === "boolean") return "radio";
  if (field.widget === "select" || field.widget === "multiselect") return "select";
  return "text";
}

function defaultStrategy(field: InputFormField) {
  if (field.valueType === "number" || field.valueType === "integer") return "从当前用户意图提取可唯一转换的数字，不任意使用 0";
  if (field.valueType === "array" || field.valueType === "object") return "根据当前意图生成满足字段 schema 的合法 JSON，不复制录制样本";
  if (field.valueType === "boolean") return "根据当前用户意图选择有证据支持的布尔值；不能确定时先询问";
  if (field.candidates) return "从本次有效候选中选择稳定值；没有语义依据时不猜测";
  return "根据当前用户意图生成简洁、非空且可编辑的业务值，不复制录制样本";
}

function exportedQuestion(field: InputFormField, capabilities: CapabilityContract[]) {
  const candidates = field.candidates;
  const question: Record<string, unknown> = {
    id: field.name,
    question: field.label,
    inputType: inputType(field),
    multiple: field.widget === "multiselect",
    required: field.required,
    defaultStrategy: defaultStrategy(field)
  };
  if (candidates?.type === "static") question.options = candidates.values;
  if (candidates?.type === "capability") {
    const source = capabilities.find(capability => capability.id === candidates.capabilityId);
    question.dataSource = {
      type: "capability",
      capabilityId: candidates.capabilityId,
      endpoint: source?.transport.urlTemplate,
      method: source?.transport.method,
      paramsFrom: candidates.dependsOn || [],
      valuePath: candidates.valuePath,
      labelPath: candidates.labelPath
    };
  }
  return question;
}

function exportedCapability(capability: CapabilityContract, capabilities: CapabilityContract[]) {
  return {
    id: capability.id,
    kind: "atomic",
    title: capability.title,
    description: capability.description,
    operation: capability.operation,
    confidence: capability.confidence,
    transport: capability.transport,
    inputSchema: capability.inputSchema,
    outputSchema: capability.outputSchema,
    inputForm: capability.inputForm,
    inputQuestions: capability.inputForm.filter(field => field.source === "caller").map(field => exportedQuestion(field, capabilities)),
    evidence: capability.evidence,
    sideEffect: capability.sideEffect,
    confirmation: capability.confirmation,
    completion: capability.completion,
    bindings: capability.bindings,
    validation: capability.validation
  };
}

function buildSkillMd(skillName: string, capabilities: CapabilityContract[], routes: CapabilityRoute[]) {
  const actions = capabilities.map(capability => capability.title).slice(0, 8).join("、");
  const description = `使用真实业务操作验证过的原子能力完成${actions || "业务查询和操作"}。当用户用自然语言提出相关查询、新建、修改、审核、删除、认证或文件处理需求时使用；负责选择能力、收集调用方字段、按已确认绑定组合、在歧义和写操作前停下来询问，并依据合同验证结果。`;
  const routeLine = routes.length
    ? "目标包含多个步骤时，只选择 `references/routes/` 中与目标完全匹配的路线。"
    : "当前没有经过确认的组合路线，只能执行单个原子能力。";
  return `---
name: ${skillName}
description: ${JSON.stringify(description)}
---

# ${safeCell(skillName)}

只执行 <code>references/CONTRACT.json</code> 中 <code>validation.status = "verified"</code> 的能力。该文件是能力、字段、绑定、路线和完成条件的唯一事实来源，不补写、合并或猜测其中没有的内容。

## 执行流程

1. 先读 <code>references/CAPABILITIES.md</code>，把用户目标匹配到一个明确的原子能力或组合路线。
2. ${routeLine}
3. 只为选中的能力读取 <code>references/INPUT_FORMS.md</code> 对应小节。仅询问 <code>source = "caller"</code> 且尚未提供的字段；系统处理字段不得询问用户。运行环境提供 <code>ask_user_question</code> 时，把同一阶段缺失字段合并到一次 <code>title + questions[]</code> 提问中，每个 <code>id</code> 必须使用合同里的字段名；否则使用等价的交互输入。
4. 字段有候选规则时，再读取 <code>references/OPTIONS.md</code> 对应小节。动态候选先执行查询能力并展示候选，不能替用户猜选项。
5. 多步执行只能采用合同中 <code>approved: true</code> 的绑定，并严格按路线步骤顺序传值。字段名相似不构成绑定。
6. 新建、修改、审核、删除、上传和其他写动作在输入校验完成后，必须单独展示操作对象、关键输入和影响，再原生调用 <code>ask_user_question</code> 的 <code>{"confirm": true, "formIds": [...]}</code>；确认调用不得混入普通问题。只有返回 <code>confirmed</code> 才继续，规划阶段的同意不算执行确认。
7. 使用 Python 执行：
   <code>python scripts/execute.py --capability &lt;能力编号&gt; --input '&lt;JSON&gt;' [--confirm-write]</code>
8. 列表结果使用 <code>python scripts/format_list.py</code> 整理后再展示；不要把大段原始 JSON 直接交给用户。
9. HTTP 状态和全部完成断言同时满足才算成功。结果不明确时停止并说明，不猜测；写操作结果不明确时不得自动重试。

## 必须停下来询问

- 多个能力或多个业务对象都可能匹配。
- 缺少调用方必填字段，或字段值无法按合同类型唯一转换。
- 动态候选返回多个可行对象，需要用户选择。
- 组合所需绑定不存在、未确认、上游值为空或不唯一。
- 即将执行新建、修改、审核、删除。

## 完成检查

- 选择的每项能力均已验证。
- 只读取和询问当前路线需要的字段。
- 自动传值的每条绑定均为 <code>approved: true</code>。
- 每个写步骤都取得了该次执行的单独确认。
- 每一步都满足自身合同完成条件，最后一步满足用户目标；否则明确报告未完成。
`;
}

function buildCapabilities(capabilities: CapabilityContract[], routes: CapabilityRoute[]) {
  const items = capabilities.map(capability => `## ${safeCell(capability.title)}

- 能力编号：<code>${capability.id}</code>
- 原子类型：${operationNames[capability.operation]}
- 业务含义：${safeCell(capability.description)}
- 写操作确认：${capability.confirmation.required ? "必须" : "不需要"}
- 完成依据：HTTP ${capability.completion.acceptedHttpStatuses.join(" / ")}${capability.completion.assertions?.length ? `，并满足 ${capability.completion.assertions.length} 条返回断言` : ""}
`).join("\n");
  const routeIndex = routes.length
    ? routes.map(route => `- [${safeCell(route.title)}](routes/${route.id}.md)`).join("\n")
    : "- 暂无已确认组合路线。";
  return `# 能力与路线索引

先用本页选择能力。只有确定执行目标后，才读取对应表单、候选规则或路线文件。

${items}

# 已确认组合路线

${routeIndex}
`;
}

function buildInputForms(capabilities: CapabilityContract[]) {
  return `# 输入表单

只读取当前能力的小节。仅向用户询问“调用方提供”的字段；其余字段由合同规则、会话或已确认绑定处理。

需要补充字段时必须原生调用 <code>ask_user_question</code>，不得在普通文本中模拟。把同一阶段字段合并为一次 <code>title + questions[]</code>；<code>questions[].id</code> 使用下表“提问编号”。调用前根据合同中的 <code>defaultStrategy</code> 生成本次非空推荐值，不能复制录制样本。类型或候选转换不唯一时，只重问错误字段；取消后立即停止。

${capabilities.map(capability => `## ${capability.id}

${capability.inputForm.length ? `| 合同路径 | 提问编号 | 业务名称 | 类型 | 来源 | 必填 | 处理方 | 必填依据 |
|---|---|---|---|---|---|---|---|
${capability.inputForm.map(field => `| <code>${safeCell(field.path)}</code> | <code>${safeCell(field.name)}</code> | ${safeCell(field.label)} | ${field.valueType} | ${sourceNames[field.source]} | ${field.required ? "是" : "否"} | ${field.systemHandled ? "系统" : "调用方"} | ${field.requiredBasis} |`).join("\n")}` : "该能力没有观察到输入字段。"}
`).join("\n")}
`;
}

function candidateText(field: InputFormField, capabilities: CapabilityContract[]) {
  const candidates = field.candidates;
  if (!candidates) return "";
  if (candidates.type === "static") {
    return `- <code>${field.path}</code>（${field.label}）：固定候选 ${candidates.values.map(item => `${safeCell(item.label)} = ${safeCell(item.value)}`).join("；")}`;
  }
  const source = capabilities.find(capability => capability.id === candidates.capabilityId);
  return `- <code>${field.path}</code>（${field.label}）：先调用查询能力 <code>${candidates.capabilityId}</code>；dataSource 为 <code>${source?.transport.method || "?"} ${source?.transport.urlTemplate || "?"}</code>；参数来自 ${candidates.dependsOn?.length ? candidates.dependsOn.map(item => `<code>${item}</code>`).join("、") : "当前已收集输入"}；值取 <code>${candidates.valuePath}</code>，显示名称取 <code>${candidates.labelPath}</code>。用户看到名称，接口接收稳定值。`;
}

function buildOptions(capabilities: CapabilityContract[]) {
  const sections = capabilities.flatMap(capability => {
    const fields = capability.inputForm.filter(field => field.candidates);
    return fields.length ? [`## ${capability.id}\n\n${fields.map(field => candidateText(field, capabilities)).join("\n")}`] : [];
  });
  return `# 候选项规则

仅在选中字段具有候选规则时读取。动态候选使用：

\`python scripts/candidates.py --capability <目标能力编号> --field <字段路径> --input '<已收集的 JSON>'\`

${sections.length ? sections.join("\n\n") : "当前没有记录到候选项规则。"}
`;
}

function buildEvidence(capabilities: CapabilityContract[]) {
  return `# 验证证据索引

本页只保留脱敏后的证据引用，不包含请求头、凭据或完整业务返回体。

${capabilities.map(capability => `## ${capability.id}

- 验证状态：${capability.validation.status}
${capability.evidence.map(item => `- ${item.kind === "ui" ? "页面" : "请求"}证据 <code>${item.eventId}</code>，录制 <code>${item.sessionId}</code>，时间 ${item.at}${item.status ? `，HTTP ${item.status}` : ""}`).join("\n")}
`).join("\n")}
`;
}

function buildRoute(route: CapabilityRoute, capabilities: CapabilityContract[]) {
  const byId = new Map(capabilities.map(capability => [capability.id, capability]));
  const bindings = route.approvedBindingIds.flatMap(bindingId => capabilities.flatMap(capability =>
    capability.bindings.filter(binding => binding.id === bindingId).map(binding => ({ binding, targetCapabilityId: capability.id }))
  ));
  return `# ${safeCell(route.title)}

仅当用户目标明确包含本路线的最终操作时使用。所有步骤和传值均来自已确认绑定。

## 执行顺序

${route.steps.map(step => {
  const capability = byId.get(step.capabilityId)!;
  return `${step.order}. 执行 <code>${capability.id}</code>：${safeCell(capability.title)}。${capability.confirmation.required ? "字段校验完成后单独确认，再执行。" : "满足输入条件后执行。"}`;
}).join("\n")}

## 已确认传值

${bindings.map(item => `- <code>${item.binding.fromCapabilityId}${item.binding.fromPath}</code> → <code>${item.targetCapabilityId}${item.binding.toPath}</code>（绑定 <code>${item.binding.id}</code>）`).join("\n") || "- 无自动传值。"}

## 执行约定

- 每一步使用 <code>python scripts/execute.py --capability &lt;能力编号&gt; --input '&lt;本步 JSON&gt;'</code>。
- 上游结果为空或不能唯一确定时停止，展示候选让用户选择。
- 不得用字段名相似代替合同绑定。
- 写步骤必须在该步执行前追加 <code>--confirm-write</code>，且只在用户已明确确认时使用。

## 停止条件

${route.stopConditions.map(condition => `- ${condition}`).join("\n")}

## 完成条件

${route.completion}，并满足 <code>references/CONTRACT.json</code> 中该能力的全部完成断言。
`;
}

export async function exportSkill(outputRoot: string, requestedName: string, allCapabilities: CapabilityContract[]) {
  const capabilities = normalizeCatalog(allCapabilities).filter(capability => capability.validation.status === "verified");
  if (!capabilities.length) throw new Error("没有可导出的已验证能力");
  const unresolved = capabilities.flatMap(capability => capability.inputForm.filter(field =>
    field.required && field.source !== "caller" && field.source !== "binding" && !field.defaultRule
  ).map(field => `${capability.id}:${field.path}`));
  if (unresolved.length) throw new Error(`存在没有处理规则的系统必填字段：${unresolved.join("、")}`);

  const skillName = normalizeSkillName(requestedName);
  const directory = path.join(outputRoot, skillName);
  const referencesDir = path.join(directory, "references");
  const scriptsDir = path.join(directory, "scripts");
  const routesDir = path.join(referencesDir, "routes");
  await mkdir(routesDir, { recursive: true });
  await mkdir(scriptsDir, { recursive: true });

  const routes = buildApprovedRoutes(capabilities);
  await writeFile(path.join(directory, "SKILL.md"), buildSkillMd(skillName, capabilities, routes), "utf8");
  await writeJson(path.join(referencesDir, "CONTRACT.json"), {
    schemaVersion: "2.0",
    skill: skillName,
    generatedAt: new Date().toISOString(),
    policy: {
      ambiguity: "ask-user",
      composition: "approved-bindings-only",
      writes: "explicit-confirmation-at-execution",
      completion: "http-status-and-all-assertions"
    },
    capabilities: capabilities.map(capability => exportedCapability(capability, capabilities)),
    routes
  });
  await writeFile(path.join(referencesDir, "CAPABILITIES.md"), buildCapabilities(capabilities, routes), "utf8");
  await writeFile(path.join(referencesDir, "INPUT_FORMS.md"), buildInputForms(capabilities), "utf8");
  await writeFile(path.join(referencesDir, "OPTIONS.md"), buildOptions(capabilities), "utf8");
  await writeFile(path.join(referencesDir, "EVIDENCE.md"), buildEvidence(capabilities), "utf8");
  for (const route of routes) {
    await writeFile(path.join(routesDir, `${route.id}.md`), buildRoute(route, capabilities), "utf8");
  }

  for (const script of ["execute.py", "candidates.py", "format_list.py"]) {
    const source = await readFile(new URL(`./python/${script}`, import.meta.url), "utf8");
    const target = path.join(scriptsDir, script);
    await writeFile(target, source, "utf8");
    await chmod(target, 0o755);
  }

  return {
    dir: directory,
    count: capabilities.length,
    skillName,
    capabilityIds: capabilities.map(capability => capability.id),
    routeIds: routes.map(route => route.id)
  };
}
