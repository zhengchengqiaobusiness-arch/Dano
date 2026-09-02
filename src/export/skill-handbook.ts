import type { CapabilityContract, CapabilityRoute, InputFormField } from "../domain.js";
import { describeFieldHandling } from "../inference/candidate-sources.js";
import { isCandidateSourceCapability, isPrimaryCapability } from "../inference/export-scope.js";

const operationNames: Record<CapabilityContract["operation"], string> = {
  query: "查询", create: "新建", update: "修改", review: "审核", delete: "删除",
  authenticate: "认证", upload: "上传", download: "下载", action: "业务动作", unknown: "未识别"
};

const sourceNames: Record<InputFormField["source"], string> = {
  caller: "调用方提供", fixed: "固定规则", session: "会话环境", generated: "运行时生成",
  computed: "计算得到", binding: "上游绑定", system: "业务系统处理"
};

const intentByOperation: Partial<Record<CapabilityContract["operation"], string>> = {
  query: "只要查、筛、列出业务单据",
  create: "要新建或提交一笔单据",
  update: "要修改已有单据",
  review: "要审核或批准",
  delete: "要删除单据",
  upload: "要上传附件",
  download: "要下载或导出",
  action: "要执行已验证的业务动作"
};

export function safeCell(value: unknown) {
  return String(value ?? "").replace(/\|/g, "\\|").replace(/\r?\n/g, " ");
}

export function isDateField(field: InputFormField) {
  return /time|date|start|end/i.test(`${field.name} ${field.label}`);
}

export function inputType(field: InputFormField) {
  if (isDateField(field)) return "date";
  if (field.widget === "json") return "textarea";
  if (field.widget === "boolean") return "radio";
  if (field.widget === "select" || field.widget === "multiselect") return "select";
  if (field.widget === "number") return "number";
  return "text";
}

export function classifyExported(capabilities: CapabilityContract[]) {
  const primary = capabilities.filter(isPrimaryCapability);
  const lookups = capabilities.filter(capability =>
    !primary.includes(capability) && isCandidateSourceCapability(capability, capabilities)
  );
  return { primary: primary.length ? primary : capabilities, lookups };
}

function commandOf(capability: CapabilityContract) {
  return `python scripts/execute.py --capability ${capability.id} --input '{...}'${capability.confirmation.required ? " --confirm-write" : ""}`;
}

function defaultStrategy(field: InputFormField) {
  if (field.valueType === "number" || field.valueType === "integer") return "从当前用户意图提取可唯一转换的数字，不任意使用 0";
  if (field.valueType === "array" || field.valueType === "object") return "根据当前意图生成满足字段 schema 的合法 JSON，不复制录制样本";
  if (field.valueType === "boolean") return "根据当前用户意图选择有证据支持的布尔值；不能确定时先询问";
  if (field.candidates) return "从本次有效候选中选择稳定值；没有语义依据时不猜测";
  return "根据当前用户意图生成简洁、非空且可编辑的业务值，不复制录制样本";
}

function recommendedDefault(field: InputFormField, capability: CapabilityContract) {
  if (/^(pageNo|pageSize|pageNum|page|size)$/i.test(field.name) && field.defaultRule?.startsWith("literal:")) {
    return `${field.defaultRule.slice("literal:".length)}（安全默认值）`;
  }
  if (capability.operation === "query") return "无；用户点名才收集";
  if (field.defaultRule?.startsWith("literal:")) return `${field.defaultRule.slice("literal:".length)}（系统补齐）`;
  return "按用户本次意图填写";
}

export function resultPathOf(valuePath: string) {
  return valuePath.replace(/^\$\./, "").replace(/\[\*\]\.[^.]+$/, "").replace(/\[\*\]$/, "");
}

export function dataSourceOf(field: InputFormField, capabilities: CapabilityContract[]) {
  if (field.candidates?.type !== "capability") return undefined;
  const source = capabilities.find(capability => capability.id === field.candidates!.capabilityId);
  return {
    type: "api",
    capabilityId: field.candidates.capabilityId,
    endpoint: source ? `${source.transport.origin}${source.transport.pathTemplate}` : undefined,
    method: source?.transport.method,
    paramsFrom: field.candidates.dependsOn || [],
    valuePath: field.candidates.valuePath,
    labelPath: field.candidates.labelPath,
    resultPath: resultPathOf(field.candidates.valuePath),
    idField: field.candidates.valuePath.split(".").pop(),
    labelField: field.candidates.labelPath.split(".").pop()
  };
}

export function exportedQuestion(field: InputFormField, capabilities: CapabilityContract[]) {
  const question: Record<string, unknown> = {
    id: field.name,
    question: field.label,
    inputType: inputType(field),
    multiple: field.widget === "multiselect",
    required: field.required,
    defaultStrategy: defaultStrategy(field)
  };
  if (isDateField(field)) question.dateFormat = "YYYY-MM-DD";
  if (field.candidates?.type === "static") question.options = field.candidates.values;
  const dataSource = dataSourceOf(field, capabilities);
  if (dataSource) question.dataSource = dataSource;
  return question;
}

function triggerTerms(displayName: string, primary: CapabilityContract[]) {
  const actions = primary.map(capability => capability.title);
  const labels = [...new Set(primary.flatMap(capability =>
    capability.inputForm.filter(field => field.source === "caller").map(field => field.label)
  ))].slice(0, 6);
  const verbs = [...new Set(primary.map(capability => operationNames[capability.operation]))];
  return { actions, labels, verbs };
}

export function skillDescription(displayName: string, capabilities: CapabilityContract[]) {
  const { primary, lookups } = classifyExported(capabilities);
  const { actions, labels, verbs } = triggerTerms(displayName, primary);
  const lookupLabels = lookups.map(capability => capability.title.replace(/^查询/, "")).filter(Boolean);
  const name = displayName || "已验证业务";
  return [
    `办理已验证的${name}：${actions.join("、") || "已录制业务动作"}。`,
    `Use when the user wants to ${verbs.join("、") || "办理"}${name}，或提到${[name, ...labels].join("、")}。`,
    `Do not use for 未列出的编辑、删除、审批${lookupLabels.length ? `；也不要把${lookupLabels.join("、")}当成独立业务动作` : ""}。`
  ].join(" ");
}

function compositionNarrative(displayName: string, primary: CapabilityContract[], lookups: CapabilityContract[], routes: CapabilityRoute[]) {
  const names = primary.map(capability => `「${capability.title}」`).join("、");
  const lookupNames = lookups.map(capability => capability.title.replace(/^查询/, "")).filter(Boolean).join("、");
  return [
    `${displayName}先按用户目标选择一条主能力：${names || "当前已验证动作"}。查询和写操作不要混用。`,
    lookupNames
      ? `${lookupNames}只在填表时作为候选来源，不是独立业务动作，也不要单独向用户交差。`
      : "没有独立的目录查询业务；不要把未列入的接口当成能力。",
    routes.length
      ? "只有用户目标明确落在已确认路线时，才按路线串联，并且只使用 `approved: true` 的绑定传值。"
      : "当前没有已确认组合路线。规划结束后只能执行单一原子操作，不要自行把查询结果填进新建或其他写操作。",
    "规划阶段用自然语言说明要走哪几步；规划结束后，只按下面的可执行约定执行，不要再自由发挥。"
  ].join("\n");
}

function routingRows(primary: CapabilityContract[]) {
  return primary.map(capability => {
    const intent = intentByOperation[capability.operation] || `要执行${capability.title}`;
    return `| ${intent} | ${safeCell(capability.title)} | \`${capability.id}\` |`;
  }).join("\n");
}

function routeIndex(routes: CapabilityRoute[]) {
  if (!routes.length) return "当前没有已确认组合路线。不要自行串联。";
  return routes.map(route =>
    `- [${safeCell(route.title)}](references/routes/${route.id}.md)：仅当用户目标就是这条最终操作时使用。`
  ).join("\n");
}

export function buildSkillMd(
  skillName: string,
  displayName: string,
  capabilities: CapabilityContract[],
  routes: CapabilityRoute[]
) {
  const { primary, lookups } = classifyExported(capabilities);
  const title = displayName || skillName;
  const actions = primary.map(capability => capability.title).join("、");
  const lookupNames = lookups.map(capability => capability.title.replace(/^查询/, "")).filter(Boolean).join("、");
  const description = skillDescription(title, capabilities);
  return `---
name: ${skillName}
description: >
  ${description}
---

# ${safeCell(title)}

把本页当作路由手册，不是业务说明书，也不是代码说明。先判断用户目标，再按需读取一份 reference；不要一开始读完 \`references/\`。

## 何时使用

用户要「${actions || "已验证业务动作"}」时使用，即使没说出 Skill 名或接口名。

## 何时不要使用

- 未列入本手册的编辑、删除、审批、导入导出。
- ${lookupNames ? `把${lookupNames}当成独立业务去查询或交差。` : "把目录、字典或候选列表当成独立业务。"}
- 发明合同里没有的接口、字段、枚举或绑定。

## 能力怎么组合

${compositionNarrative(title, primary, lookups, routes)}

## 可执行约定

规划结束后按下述约定执行。

### 何时走哪条原子操作

| 用户意图 | 原子操作 | 能力编号 |
| --- | --- | --- |
${routingRows(primary)}
${lookups.length ? `| 只要选${lookupNames || "目录值"} | 不走主能力，读 [OPTIONS.md](references/OPTIONS.md) 取候选 | 字段候选，不是业务动作 |` : ""}

查询和写操作不要混用。实体目录不等于业务单据。

### 何时可以按已确认绑定串联

${routeIndex(routes)}

- 传值只使用合同声明的 \`fromPath → toPath\`。
- 上游结果为空或不能唯一确定时停止，展示候选让用户选择。
- 不得用字段名相似代替合同绑定。

### 何时必须停下来问人

- 多个主能力都可能匹配。
- 缺少调用方必填字段，或显示名对不上候选。
- 写操作还没有 \`confirm: true\`。
- 目标超出本手册，或 Prefer HTTP 与 Fallback 都无法完成。

## 收集输入

- 需要补字段时必须原生调用 \`ask_user_question\`，禁止在普通文本里模拟提问。
- 每次回复最多一次；同一阶段字段放进同一个 \`title + questions[]\`。
- \`questions[].id\` 必须与合同字段名逐字一致，禁止翻译或改成 snake_case。
- 查询只收集用户点名的筛选；没有筛选就传空 input。写操作收集全部调用方必填。
- 日期使用 \`inputType: "date"\` 和 \`dateFormat: "YYYY-MM-DD"\`。
- 系统处理、计算、会话和已确认绑定字段不要问用户，但执行时按合同带上，避免看似成功实际缺内容。
- 当前能力的表单读 [INPUT_FORMS.md](references/INPUT_FORMS.md)；候选项读 [OPTIONS.md](references/OPTIONS.md)。

## 执行

- 工作目录必须是本 \`SKILL.md\` 所在目录。
- Prefer HTTP：\`python scripts/execute.py --capability <能力编号> --input '<JSON>'\`。写操作在用户确认后追加 \`--confirm-write\`。
- 写操作先原生调用 \`{"confirm": true, "formIds": ["<answered.formId>"]}\`，只有返回 \`confirmed\` 才执行。
- 认证只走环境变量 \`SKILL_AUTH_HEADERS\`。不要把凭证写进 Skill，也不要绕过脚本直接拼 HTTP。
- Prefer 失败时整段改走 Fallback（内置浏览器按合同 evidence 补录），并写明走了哪条路径。
- 列表结果先用 \`python scripts/format_list.py\` 再展示。

## 质量与完成

- HTTP 状态和合同完成断言都满足才算成功。
- 写操作结果不明确时不要重试。
- 不要把内部 ID 或裸 data 猜成业务编号。
- 未列入的能力不要假装支持。

## 按需读取

- [CAPABILITIES.md](references/CAPABILITIES.md)：选定主能力
- [INPUT_FORMS.md](references/INPUT_FORMS.md)：填写当前能力
- [OPTIONS.md](references/OPTIONS.md)：取字段候选
- [CONTRACT.json](references/CONTRACT.json)：机器事实来源
${routes.length ? "- [routes/](references/routes/)：已确认组合路线\n" : ""}`;
}

export function buildCapabilities(capabilities: CapabilityContract[], routes: CapabilityRoute[]) {
  const { primary, lookups } = classifyExported(capabilities);
  const items = primary.map(capability => {
    const caller = capability.inputForm.filter(field => field.source === "caller" && !/^(pageNo|pageSize|pageNum|page|size)$/i.test(field.name));
    const input = caller.length
      ? capability.operation === "query"
        ? `调用方可提供 ${caller.map(field => field.label).join("、")}；未点名则不收集`
        : `调用方必填 ${caller.filter(field => field.required).map(field => field.label).join("、") || "见当前表单"}`
      : "无需调用方字段";
    return `## ${safeCell(capability.title)}

- 何时用：${intentByOperation[capability.operation] || capability.title}
- 读写：${capability.confirmation.required ? "写" : "读"} · ${operationNames[capability.operation]}
- 能力编号：\`${capability.id}\`
- 输入概况：${input}
- 完成：HTTP ${capability.completion.acceptedHttpStatuses.join(" / ")}
- 表单：[INPUT_FORMS.md](INPUT_FORMS.md#${capability.id})
- 候选：[OPTIONS.md](OPTIONS.md#${capability.id})
`;
  }).join("\n");
  const lookupNote = lookups.length
    ? `字段候选（${lookups.map(capability => capability.title).join("、")}）不是主能力，见 [OPTIONS.md](OPTIONS.md)。`
    : "当前没有字段候选接口。";
  const routeIndexLines = routes.length
    ? routes.map(route => `- [${safeCell(route.title)}](routes/${route.id}.md)`).join("\n")
    : "- 暂无已确认组合路线。";
  return `# 能力索引

先用本页选择一个主能力。只有确定执行目标后，才读取对应表单、候选或路线。

${items}
${lookupNote}

# 已确认组合路线

${routeIndexLines}
`;
}

function callerFieldTable(capability: CapabilityContract, capabilities: CapabilityContract[]) {
  const fields = capability.inputForm.filter(field => field.source === "caller");
  if (!fields.length) return "该能力没有需要调用方提供的字段。";
  return `| 提问编号 | 业务名称 | 控件 | 必填 | 推荐默认值 | 候选 |
|---|---|---|---|---|---|
${fields.map(field => {
    const dataSource = dataSourceOf(field, capabilities);
    let candidate = "自由输入";
    if (field.candidates?.type === "static") {
      candidate = field.candidates.values.map(item => `${item.label}=${String(item.value)}`).join("；");
    } else if (dataSource) {
      candidate = `dataSource: ${JSON.stringify({
        type: "api",
        endpoint: dataSource.endpoint,
        method: dataSource.method,
        idField: dataSource.idField,
        labelField: dataSource.labelField
      })}`;
    } else if (isDateField(field)) {
      candidate = "dateFormat: YYYY-MM-DD";
    }
    return `| \`${safeCell(field.name)}\` | ${safeCell(field.label)} | \`${inputType(field)}\` | ${field.required ? "是" : "否"} | ${safeCell(recommendedDefault(field, capability))} | ${safeCell(candidate)} |`;
  }).join("\n")}`;
}

function systemFieldTable(capability: CapabilityContract) {
  const fields = capability.inputForm.filter(field => field.source !== "caller");
  if (!fields.length) return "";
  return `

系统处理字段不要问用户，执行时按合同带上：

| 合同路径 | 业务名称 | 来源 | 处理方式 |
|---|---|---|---|
${fields.map(field =>
    `| \`${safeCell(field.path)}\` | ${safeCell(field.label)} | ${sourceNames[field.source]} | ${safeCell(describeFieldHandling(field))} |`
  ).join("\n")}`;
}

export function buildInputForms(capabilities: CapabilityContract[]) {
  const { primary } = classifyExported(capabilities);
  return `# 输入表单

只读取当前能力的小节。仅向用户询问“调用方提供”的字段。

需要补充字段时必须原生调用 \`ask_user_question\`，不得在普通文本中模拟。把同一阶段字段合并为一次 \`title + questions[]\`；\`questions[].id\` 使用下表提问编号。调用前按合同 \`defaultStrategy\` 生成本次非空推荐值，不能复制录制样本。类型或候选转换不唯一时，只重问错误字段；取消后立即停止。

${primary.map(capability => `## ${capability.id}

${safeCell(capability.title)} · ${operationNames[capability.operation]} · \`${capability.transport.method} ${capability.transport.pathTemplate}\`
${capability.confirmation.required ? "\n写操作：复述将提交的内容，用户同意后再加 `--confirm-write`。\n" : "\n查询：只收集用户点名的筛选条件。\n"}
${callerFieldTable(capability, capabilities)}${systemFieldTable(capability)}
`).join("\n")}
`;
}

function candidateText(field: InputFormField, capabilities: CapabilityContract[]) {
  const candidates = field.candidates;
  if (!candidates) return "";
  if (candidates.type === "static") {
    return `- \`${field.path}\`（${field.label}）：页面固定枚举，直接选择 ${candidates.values.map(item => `${safeCell(item.label)} = ${safeCell(item.value)}`).join("；")}`;
  }
  const dataSource = dataSourceOf(field, capabilities);
  return `- \`${field.path}\`（${field.label}）：接口候选。用户看显示名，接口收稳定值。dataSource: \`${JSON.stringify({
    type: "api",
    endpoint: dataSource?.endpoint,
    method: dataSource?.method,
    idField: dataSource?.idField,
    labelField: dataSource?.labelField,
    resultPath: dataSource?.resultPath
  })}\``;
}

export function buildOptions(capabilities: CapabilityContract[]) {
  const { primary } = classifyExported(capabilities);
  const sections = primary.flatMap(capability => {
    const fields = capability.inputForm.filter(field => field.candidates);
    return fields.length
      ? [`## ${capability.id}\n\n${fields.map(field => candidateText(field, capabilities)).join("\n")}`]
      : [];
  });
  return `# 候选项规则

仅在当前字段具有候选规则时读取。这些接口只给调用方选值，不是独立业务操作。

动态候选：

\`python scripts/candidates.py --capability <目标能力编号> --field <字段路径> --input '<已收集的 JSON>'\`

${sections.length ? sections.join("\n\n") : "当前没有记录到候选项规则。"}
`;
}

export function buildRoute(route: CapabilityRoute, capabilities: CapabilityContract[]) {
  const byId = new Map(capabilities.map(capability => [capability.id, capability]));
  const bindings = route.approvedBindingIds.flatMap(bindingId => capabilities.flatMap(capability =>
    capability.bindings.filter(binding => binding.id === bindingId).map(binding => ({ binding, targetCapabilityId: capability.id }))
  ));
  const titles = route.steps.map(step => byId.get(step.capabilityId)?.title || step.capabilityId);
  return `# ${safeCell(route.title)}

仅当用户目标明确包含本路线的最终操作时使用。所有步骤和传值均来自已确认绑定。

## 自然语言组合

先${titles.slice(0, -1).join("，再") || "准备输入"}，再${titles.at(-1) || "执行最终操作"}。中间结果只按已确认绑定往下传；不能唯一确定时停下来让用户选。

## 可执行约定

${route.steps.map(step => {
    const capability = byId.get(step.capabilityId)!;
    return `${step.order}. 执行 \`${capability.id}\`：${safeCell(capability.title)}。${capability.confirmation.required ? "字段校验完成后单独确认，再带 `--confirm-write` 执行。" : "满足输入条件后执行。"}`;
  }).join("\n")}

## 已确认传值

${bindings.map(item =>
    `- \`${item.binding.fromCapabilityId}${item.binding.fromPath}\` → \`${item.targetCapabilityId}${item.binding.toPath}\`（绑定 \`${item.binding.id}\`）`
  ).join("\n") || "- 无自动传值。"}

- 每一步使用 \`python scripts/execute.py --capability <能力编号> --input '<本步 JSON>'\`。
- 上游结果为空或不能唯一确定时停止，展示候选让用户选择。
- 不得用字段名相似代替合同绑定。

## 停止条件

${route.stopConditions.map(condition => `- ${condition}`).join("\n")}

## 完成条件

${route.completion}，并满足 \`references/CONTRACT.json\` 中该能力的全部完成断言。
`;
}
