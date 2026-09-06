import type { CapabilityContract, CapabilityRoute, InputFormField, JsonSchema } from "../domain.js";
import { describeFieldHandling } from "../inference/candidate-sources.js";
import { isCandidateSourceCapability, isPrimaryCapability, pageRoleLabel } from "../inference/export-scope.js";

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

export function intentForCapability(capability: CapabilityContract) {
  if (capability.operation === "query") {
    const role = pageRoleLabel(capability.transport.pathTemplate);
    if (role === "统计") return "只要看统计、汇总或分析";
    if (role === "详情") return "只要看一笔单据详情";
  }
  return intentByOperation[capability.operation] || `要执行${capability.title}`;
}

export function safeCell(value: unknown) {
  return String(value ?? "").replace(/\|/g, "\\|").replace(/\r?\n/g, " ");
}

export function isDateField(field: InputFormField) {
  return field.widget === "date";
}

function dateFormat(field: InputFormField) {
  return field.dateFormat || "YYYY-MM-DD";
}

export function inputType(field: InputFormField) {
  if (field.widget === "date" || isDateField(field)) return "date";
  if (field.widget === "textarea" || field.widget === "json") return "textarea";
  if (field.widget === "boolean") return "radio";
  if (field.widget === "select" || field.widget === "multiselect") return "select";
  if (field.widget === "number") return "number";
  return "text";
}

export function classifyExported(capabilities: CapabilityContract[]) {
  const primary = capabilities.filter(item => isPrimaryCapability(item, capabilities));
  const lookups = capabilities.filter(capability =>
    !primary.includes(capability) && isCandidateSourceCapability(capability, capabilities)
  );
  return { primary, lookups };
}

function defaultStrategy(field: InputFormField) {
  if (isDateField(field) && field.valueType === "array") return `按页面顺序提供日期数组，每项格式 ${dateFormat(field)}，不复制未见过的值`;
  if (isDateField(field)) return `根据当前请求和当前日期推导，格式 ${dateFormat(field)}，不复制未见过的值`;
  if (field.valueType === "number" || field.valueType === "integer") return "从当前用户意图提取可唯一转换的数字，不任意使用 0";
  if (field.valueType === "array" || field.valueType === "object") return "根据当前意图生成满足字段 schema 的合法 JSON，不复制未见过的值";
  if (field.valueType === "boolean") return "根据当前用户意图选择有证据支持的布尔值；不能确定时先询问";
  if (field.candidates) return "从本次有效候选中选择稳定值；没有语义依据时不猜测";
  return "根据当前用户意图生成简洁、非空且可编辑的业务值，不复制未见过的值";
}

function recommendedDefault(field: InputFormField, capability: CapabilityContract) {
  if (/^(pageNo|pageSize|pageNum|page|size)$/i.test(field.name) && field.defaultRule?.startsWith("literal:")) {
    return `${field.defaultRule.slice("literal:".length)}（安全默认值）`;
  }
  if (capability.operation === "query") return "无；用户点名才收集";
  if (field.source === "caller" && field.defaultRule?.startsWith("computed:")) {
    return `页面按 ${field.defaultRule.slice("computed:".length)} 自动计算，调用方可改`;
  }
  if (field.defaultRule?.startsWith("computed:")) return `按 ${field.defaultRule.slice("computed:".length)} 计算`;
  if (field.defaultRule?.startsWith("copy:")) return `拷贝 ${field.defaultRule.slice("copy:".length)}`;
  if (field.defaultRule?.startsWith("from:")) {
    return field.sourceDetail
      ? `${field.sourceDetail}（${field.defaultRule}）`
      : field.defaultRule;
  }
  if (field.defaultRule?.startsWith("literal:")) return `${field.defaultRule.slice("literal:".length)}（系统按成功请求原值自动补齐）`;
  return "按用户本次意图填写";
}

function exportHandling(field: InputFormField) {
  const handling = describeFieldHandling(field);
  const rule = field.defaultRule && !handling.includes(field.defaultRule) ? `；规则 ${field.defaultRule}` : "";
  return `${handling}${rule}`
    .replaceAll("由执行器按默认值补齐", "由系统按安全默认值补齐")
    .replaceAll("执行器按录制默认补齐", "系统按安全默认值补齐")
    .replaceAll("执行器", "系统")
    .replaceAll("不要改成录制样本", "不要编造未见过的值")
    .replaceAll("不要写死录制样本", "不要编造未见过的值");
}

export function resultPathOf(valuePath: string) {
  return valuePath.replace(/^\$\./, "").replace(/\[\*\]\.[^.]+$/, "").replace(/\[\*\]$/, "");
}

function schemaHas(schema: JsonSchema | undefined, dotted: string) {
  let current: JsonSchema | undefined = schema;
  for (const part of dotted.split(".").filter(Boolean)) {
    current = current?.properties?.[part];
    if (!current) return false;
  }
  return true;
}

function lookupQueryHints(source: CapabilityContract | undefined) {
  if (!source) return {};
  const hints: {
    searchParam?: string;
    pageParam?: string;
    pageSizeParam?: string;
    pageSize?: number;
    totalPath?: string;
    params?: Record<string, unknown>;
  } = {};
  const params: Record<string, unknown> = {};
  for (const field of source.inputForm) {
    if (/^(pageNo|page|pageNum|pageIndex)$/i.test(field.name)) hints.pageParam = field.name;
    if (/^(pageSize|size|limit)$/i.test(field.name)) {
      hints.pageSizeParam = field.name;
      if (field.defaultRule?.startsWith("literal:")) {
        const raw = field.defaultRule.slice("literal:".length);
        const parsed = Number(raw.replace(/^"|"$/g, ""));
        if (Number.isFinite(parsed) && parsed > 0) hints.pageSize = parsed;
      }
    }
    if (/^(keyword|query|search|name|q)$/i.test(field.name)) hints.searchParam = field.name;
    if (field.source === "fixed" && field.defaultRule?.startsWith("literal:")) {
      try {
        params[field.name] = JSON.parse(field.defaultRule.slice("literal:".length));
      } catch {
        params[field.name] = field.defaultRule.slice("literal:".length);
      }
    }
  }
  if (Object.keys(params).length) hints.params = params;
  if (schemaHas(source.outputSchema, "data.total")) hints.totalPath = "data.total";
  else if (schemaHas(source.outputSchema, "total")) hints.totalPath = "total";
  return hints;
}

export function dataSourceOf(field: InputFormField, capabilities: CapabilityContract[]) {
  const candidates = field.candidates;
  if (candidates?.type !== "capability") return undefined;
  const source = capabilities.find(capability => capability.id === candidates.capabilityId);
  const hints = lookupQueryHints(source);
  return {
    type: "api" as const,
    capabilityId: candidates.capabilityId,
    endpoint: source ? `${source.transport.origin}${source.transport.pathTemplate}` : undefined,
    method: source?.transport.method,
    paramsFrom: candidates.dependsOn || [],
    valuePath: candidates.valuePath,
    labelPath: candidates.labelPath,
    resultPath: resultPathOf(candidates.valuePath),
    idField: candidates.valuePath.split(".").pop(),
    labelField: candidates.labelPath.split(".").pop(),
    ...hints
  };
}

export function questionKey(field: InputFormField, siblings: InputFormField[] = []) {
  if (/\[[0-9]+\]/.test(field.path || "")) return field.path.replace(/^\$\./, "");
  const clashes = siblings.filter(item => item.name === field.name);
  if (clashes.length <= 1) return field.name;
  return field.path.replace(/^\$\./, "").replace(/\[\*\]/g, "");
}

export function exportedQuestion(field: InputFormField, capabilities: CapabilityContract[], siblings: InputFormField[] = []) {
  const question: Record<string, unknown> = {
    id: questionKey(field, siblings),
    question: (() => {
      const hint = /页面未唯一对应：(.+)$/.exec(field.sourceDetail || "")?.[1];
      return hint && field.label === field.name ? `${field.label}（${hint}）` : field.label;
    })(),
    inputType: inputType(field),
    multiple: field.valueType === "array" || field.widget === "multiselect",
    required: field.required,
    defaultStrategy: defaultStrategy(field)
  };
  if (isDateField(field)) question.dateFormat = dateFormat(field);
  if (field.dateClocks?.length) question.dateClocks = field.dateClocks;
  if (field.candidates?.type === "static") question.options = field.candidates.values;
  const dataSource = publishedDataSource(field, capabilities);
  if (dataSource) question.dataSource = dataSource;
  return question;
}

function mentionableLabel(field: InputFormField) {
  const label = field.label?.trim();
  if (!label || label === field.name) return undefined;
  if (!/[\u4e00-\u9fff]/.test(label)) return undefined;
  if (/[\[\]{}.]/.test(label)) return undefined;
  return label;
}

export function skillDescriptionLines(displayName: string, capabilities: CapabilityContract[]) {
  const { primary, lookups } = classifyExported(capabilities);
  const actions = primary.map(capability => capability.title);
  const labels = [...new Set(primary.flatMap(capability =>
    capability.inputForm.filter(field => field.source === "caller").map(mentionableLabel).filter((item): item is string => Boolean(item))
  ))].slice(0, 6);
  const verbs = [...new Set(primary.map(capability => operationNames[capability.operation]))];
  const lookupLabels = lookups.map(capability => capability.title.replace(/^查询/, "")).filter(Boolean);
  const name = (displayName || "已验证业务").replace(/管理$/, "") || "已验证业务";
  return [
    `办理已验证的${name}：${actions.join("、") || "已录制业务动作"}。`,
    `Use when the user wants to ${verbs.join("、") || "办理"}${name}，or mentions ${[name, ...labels].join("、")}.`,
    `Do not use for 未列出的编辑、删除、审批${lookupLabels.length ? `，or treat ${lookupLabels.join("、")} as standalone business actions` : ""}.`
  ];
}

function routingRows(primary: CapabilityContract[]) {
  return primary.map(capability => {
    const intent = intentForCapability(capability);
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
  const description = skillDescriptionLines(title, capabilities).join("\n  ");
  return `---
name: ${skillName}
description: >
  ${description}
---

# ${safeCell(title)}

把用户目标路由到最小的已验证能力或组合路线，并通过本目录脚本执行。\`references/CONTRACT.json\` 是接口、字段、枚举、绑定和完成条件的唯一事实来源。

## Workflow

1. 从下表选择唯一原子能力。目标不唯一时，只读 [CAPABILITIES.md](references/CAPABILITIES.md) 的相关行；仍不唯一再问用户。
2. 调用方已给出合同字段且类型明确时直接执行。需要把业务名称映射为字段、补字段或生成写操作确认表单时，只读 [INPUT_FORMS.md](references/INPUT_FORMS.md) 的对应能力小节。
3. 仅当当前字段需要枚举或接口候选时，运行 \`python scripts/candidates.py --capability <能力编号> --field <字段路径> --input '<JSON>'\`；处理规则见 [OPTIONS.md](references/OPTIONS.md)。显示名由脚本转换为真实接口值。
4. 查询只传用户明确提供的筛选条件。写操作把本阶段调用方字段合并为一次原生 \`ask_user_question\`，再调用 \`{"confirm":true,"formIds":["<answered.formId>"]}\`；只有 \`confirmed\` 才执行。
5. 从 Skill 根目录运行下述命令。认证由外部同名运行时凭据提供；也可显式使用 \`SKILL_AUTH_HEADERS\` 或 \`SKILL_AUTH_FILE\`。凭据不得进入 Skill、合同或对话。
6. HTTP 状态与合同全部完成断言同时满足才算完成；写操作结果不明时不重试。

## Atomic capabilities

| 用户意图 | 原子操作 | 能力编号 |
| --- | --- | --- |
${routingRows(primary)}
${lookups.length ? `| 只为字段选择${lookupNames || "目录值"} | 运行候选脚本，不作为最终业务动作 | 字段候选 |` : ""}

原子命令：

\`python scripts/execute.py --capability <能力编号> --input '<JSON>'\`

写操作在确认后追加 \`--confirm-write\`。

## Composed workflows

${routeIndex(routes)}

组合只允许使用路线文档和合同中 \`approved: true\` 的绑定。没有路线时只能执行单一原子能力；不得凭字段名相似自行串联。上游结果为空或不能唯一确定时停止并让用户选择。

## Output and failures

- 列表结果交给 \`python scripts/format_list.py\`；无数据输出「无数据」，单元格换行使用 \`<br>\`。
- 非列表只展示合同完成条件中的业务字段，不把内部 ID 或裸 \`data\` 猜成业务编号。
- 401/403：停止并提示更新外部运行时凭据。网络或合同断言失败：报告真实错误；只读操作由用户决定是否重试，写操作不自动重试。
- 字段类型或候选不能唯一转换时，只对错误字段重新调用 \`ask_user_question\`。\`cancelled\` 后立即停止。

## Boundaries

- 仅支持：${actions || "合同列出的已验证业务动作"}。
- 不支持未列出的编辑、删除、审批、导入导出；${lookupNames ? `${lookupNames}只作为字段候选。` : "目录、字典和候选列表不是最终业务动作。"}
- 只通过本目录脚本执行，不直接拼接口，不发明字段、枚举、绑定或成功结果。

## Progressive references

- [CAPABILITIES.md](references/CAPABILITIES.md)：仅在能力边界不明确时读取相关行。
- [INPUT_FORMS.md](references/INPUT_FORMS.md)：仅在需要映射或补充当前能力输入时读取对应小节。
- [OPTIONS.md](references/OPTIONS.md)：仅在当前字段需要候选时读取。
${routes.length ? "- [routes/](references/routes/)：仅在用户目标命中已确认组合路线时读取一个路线文件。\n" : ""}- [CONTRACT.json](references/CONTRACT.json)：脚本使用的机器事实来源；普通调用无需预读。
`;
}

export function buildCapabilities(capabilities: CapabilityContract[], routes: CapabilityRoute[]) {
  const { primary, lookups } = classifyExported(capabilities);
  const rows = primary.map(capability => {
    const caller = capability.inputForm.filter(field => field.source === "caller" && !/^(pageNo|pageSize|pageNum|page|size)$/i.test(field.name));
    const input = caller.length
      ? capability.operation === "query"
        ? `${caller.map(field => field.label).join("、")}（按需筛选）`
        : caller.map(field => `${field.label}${field.required ? "*" : ""}`).join("、")
      : "无需调用方字段";
    const output = [
      `HTTP ${capability.completion.acceptedHttpStatuses.join("/")}`,
      ...(capability.completion.requiredOutputPaths || []).map(item => item),
      ...(capability.completion.assertions || []).map(item => `${item.path} ${item.kind}`)
    ].join("；");
    return `| ${safeCell(intentForCapability(capability))} | ${safeCell(capability.title)} | \`${capability.id}\` | ${capability.confirmation.required ? "写" : "读"} | ${safeCell(input)} | ${safeCell(output)} |`;
  }).join("\n");
  const lookupNote = lookups.length
    ? `字段候选（${lookups.map(capability => capability.title).join("、")}）只服务于输入选择，不作为最终业务动作。`
    : "当前没有字段候选接口。";
  const routeIndexLines = routes.length
    ? routes.map(route => `- [${safeCell(route.title)}](routes/${route.id}.md)`).join("\n")
    : "- 暂无已确认组合路线。";
  return `# 能力索引

仅在 \`SKILL.md\` 无法唯一确定能力时读取本页。星号表示写操作必填字段；具体控件和默认规则只在 [INPUT_FORMS.md](INPUT_FORMS.md) 定义。

| 何时用 | 能力 | 编号 | 读写 | 调用方输入概况 | 完成概况 |
| --- | --- | --- | --- | --- | --- |
${rows}

${lookupNote}

## 已确认组合路线

${routeIndexLines}
`;
}

function publishedDataSource(field: InputFormField, capabilities: CapabilityContract[]) {
  const dataSource = dataSourceOf(field, capabilities);
  if (!dataSource) return undefined;
  const published: Record<string, unknown> = {
    type: "api",
    endpoint: dataSource.endpoint,
    method: dataSource.method,
    params: dataSource.params || {},
    resultPath: dataSource.resultPath,
    idField: dataSource.idField,
    labelField: dataSource.labelField
  };
  if (dataSource.searchParam) published.searchParam = dataSource.searchParam;
  if (dataSource.pageParam) published.pageParam = dataSource.pageParam;
  if (dataSource.pageSizeParam) published.pageSizeParam = dataSource.pageSizeParam;
  if (dataSource.pageSize) published.pageSize = dataSource.pageSize;
  if (dataSource.totalPath) published.totalPath = dataSource.totalPath;
  if (dataSource.paramsFrom?.length) published.paramsFrom = dataSource.paramsFrom;
  return published;
}

function callerFieldTable(capability: CapabilityContract, capabilities: CapabilityContract[]) {
  const fields = capability.inputForm.filter(field => field.source === "caller");
  if (!fields.length) return "该能力没有需要调用方提供的字段。";
  return `| 提问编号 | 业务名称 | 控件 | 必填 | 推荐默认值 | 候选 |
|---|---|---|---|---|---|
${fields.map(field => {
    const dataSource = publishedDataSource(field, capabilities);
    let candidate = field.requestFormat === "html" ? "富文本：调用方传文本，系统按真实请求格式编码为 HTML" : "自由输入";
    if (field.candidates?.type === "static") {
      candidate = "页面固定枚举；值见 OPTIONS.md 的同能力小节";
    } else if (dataSource) {
      candidate = `dataSource: ${JSON.stringify(dataSource)}`;
    } else if (isDateField(field)) {
      candidate = field.dateClock
        ? `dateFormat: ${dateFormat(field)}，请求补 ${field.dateClock}`
        : `dateFormat: ${dateFormat(field)}`;
      if (field.sourceDetail && /毫秒|时间戳|dateClock|YYYY-MM-DD/.test(field.sourceDetail)) {
        candidate = `${candidate}；${field.sourceDetail.replaceAll("执行器", "系统")}`;
      }
    } else if (field.widget === "select" || field.widget === "multiselect") {
      candidate = "无固定候选";
    }
    const hint = /页面未唯一对应：(.+)$/.exec(field.sourceDetail || "")?.[1];
    const label = hint && field.label === field.name ? `${field.label}（${hint}）` : field.label;
    return `| \`${safeCell(questionKey(field, capability.inputForm))}\` | ${safeCell(label)} | \`${inputType(field)}\` | ${field.required ? "是" : "否"} | ${safeCell(recommendedDefault(field, capability))} | ${safeCell(candidate)} |`;
  }).join("\n")}`;
}

function systemFieldTable(capability: CapabilityContract) {
  const fields = capability.inputForm.filter(field => field.source !== "caller");
  if (!fields.length) return "";
  return `

系统处理字段不要问用户。存在唯一因果证据时，按其它接口带出、请求内计算、字段拷贝、会话或生成规则处理；没有因果来源时，按录制成功请求中的原始值和原始类型自动补齐，不要为了凑来源制造关联。查询里没有默认值的筛选不要编造。

| 合同路径 | 业务名称 | 来源 | 处理方式 |
|---|---|---|---|
${fields.map(field =>
    `| \`${safeCell(field.path)}\` | ${safeCell(field.label)} | ${sourceNames[field.source]} | ${safeCell(exportHandling(field))} |`
  ).join("\n")}`;
}

export function buildInputForms(capabilities: CapabilityContract[]) {
  const { primary } = classifyExported(capabilities);
  return `# 输入表单

只读取当前能力的小节。这里是提问和系统补值的唯一说明；候选取值规则只在 [OPTIONS.md](OPTIONS.md) 定义。

需要补充字段时必须原生调用 \`ask_user_question\`，不得在普通文本中模拟。把同一阶段字段合并为一次 \`title + questions[]\`；\`questions[].id\` 使用下表提问编号。调用前按合同 \`defaultStrategy\` 生成本次非空推荐值，不能复制未见过的值。类型或候选转换不唯一时，只重问错误字段；\`cancelled\` 后立即停止。

${primary.map(capability => `## ${capability.id}

${safeCell(capability.title)} · ${operationNames[capability.operation]}
${capability.confirmation.required ? "\n写操作：字段齐备后调用 `{\"confirm\":true,\"formIds\":[\"<answered.formId>\"]}`，得到 `confirmed` 再加 `--confirm-write`。\n" : "\n查询：只收集用户点名的筛选条件。\n"}
${callerFieldTable(capability, capabilities)}${systemFieldTable(capability)}
`).join("\n")}
`;
}

function candidateText(field: InputFormField) {
  const candidates = field.candidates;
  if (!candidates) return "";
  if (candidates.type === "static") {
    return `- \`${field.path}\`（${field.label}）：页面固定枚举；${candidates.values.map(item => `${safeCell(item.label)} = ${safeCell(item.value)}`).join("；")}`;
  }
  return `- \`${field.path}\`（${field.label}）：运行候选命令；从已验证能力 \`${candidates.capabilityId}\` 的 \`${candidates.labelPath}\` 显示名称，并把唯一匹配的 \`${candidates.valuePath}\` 交给接口。`;
}

export function buildOptions(capabilities: CapabilityContract[]) {
  const { primary } = classifyExported(capabilities);
  const sections = primary.flatMap(capability => {
    const fields = capability.inputForm.filter(field => field.candidates);
    return fields.length
      ? [`## ${capability.id}\n\n${fields.map(field => candidateText(field)).join("\n")}`]
      : [];
  });
  return `# 候选项规则

仅在当前字段具有候选规则时读取。这是候选获取与“显示名 → 接口值”转换的唯一说明；表单控件和 dataSource 在 [INPUT_FORMS.md](INPUT_FORMS.md)。

对静态和动态候选统一运行：

\`python scripts/candidates.py --capability <目标能力编号> --field <字段路径> --input '<已收集的 JSON>'\`

- 向用户展示 \`label\`，向业务接口传 \`value\`；脚本会把唯一匹配的显示名转换为真实值。
- 无匹配或多匹配时只重问当前字段，不传显示名、不猜数字。
- 动态候选的请求参数和返回路径以 INPUT_FORMS 中的 dataSource 为准。

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
