import type { CapabilityContract, CapabilityRoute, InputFormField, JsonSchema } from "../domain.js";
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
  if (isDateField(field)) return "根据当前请求和当前日期推导，格式 YYYY-MM-DD，不复制未见过的值";
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
  if (field.defaultRule?.startsWith("from:")) return "从已录制查询带出";
  if (field.defaultRule?.startsWith("literal:")) return `${field.defaultRule.slice("literal:".length)}（系统默认，不是录制样本）`;
  return "按用户本次意图填写";
}

function exportHandling(field: InputFormField) {
  return describeFieldHandling(field)
    .replaceAll("由执行器按默认值补齐", "由系统按安全默认值补齐")
    .replaceAll("执行器按录制默认补齐", "系统按安全默认值补齐")
    .replaceAll("执行器转成当天 00:00 的毫秒时间戳", "按当天开始时间提交")
    .replaceAll("执行器", "系统")
    .replaceAll("不要改成录制样本", "不要编造未见过的值")
    .replaceAll("不要写死录制样本", "不要编造未见过的值")
    .replaceAll("按录制默认", "按安全默认值")
    .replaceAll("已录制查询接口", "已验证查询接口");
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

function hasArrayType(schema: JsonSchema | undefined) {
  if (!schema?.type) return false;
  return schema.type === "array" || (Array.isArray(schema.type) && schema.type.includes("array"));
}

function itemsPathOf(schema?: JsonSchema) {
  if (schemaHas(schema, "data.list")) return "$.data.list";
  if (hasArrayType(schema?.properties?.data)) return "$.data";
  if (schemaHas(schema, "list")) return "$.list";
  return undefined;
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
  if (field.candidates?.type !== "capability") return undefined;
  const source = capabilities.find(capability => capability.id === field.candidates!.capabilityId);
  const hints = lookupQueryHints(source);
  return {
    type: "api" as const,
    capabilityId: field.candidates.capabilityId,
    endpoint: source ? `${source.transport.origin}${source.transport.pathTemplate}` : undefined,
    method: source?.transport.method,
    paramsFrom: field.candidates.dependsOn || [],
    valuePath: field.candidates.valuePath,
    labelPath: field.candidates.labelPath,
    resultPath: resultPathOf(field.candidates.valuePath),
    idField: field.candidates.valuePath.split(".").pop(),
    labelField: field.candidates.labelPath.split(".").pop(),
    ...hints
  };
}

export function questionKey(field: InputFormField, siblings: InputFormField[] = []) {
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
    multiple: field.widget === "multiselect",
    required: field.required,
    defaultStrategy: defaultStrategy(field)
  };
  if (isDateField(field)) question.dateFormat = "YYYY-MM-DD";
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
    "规划阶段用自然语言说明要走哪几步；规划结束后，只按下面的可执行约定执行，不要再混合推理。"
  ].join("\n");
}

function planningExamples(primary: CapabilityContract[], routes: CapabilityRoute[]) {
  const query = primary.find(capability => capability.operation === "query");
  const write = primary.find(capability => capability.confirmation.required);
  const blocks: string[] = [];
  if (query) {
    blocks.push(`用户：「${query.title.replace(/^查询/, "查一下")}」
规划：单一原子操作「${query.title}」。只收集用户点名的筛选。
执行：读 [INPUT_FORMS.md](references/INPUT_FORMS.md#${query.id}) → 原生 \`ask_user_question\` → \`python scripts/execute.py --capability ${query.id} --input '...'\` → \`python scripts/format_list.py\`。
停止：多个主能力都像，或显示名对不上候选。`);
  }
  if (write) {
    blocks.push(`用户：「${write.title}」
规划：单一原子操作「${write.title}」。这是写操作，先齐字段再确认。
执行：读 [INPUT_FORMS.md](references/INPUT_FORMS.md#${write.id}) 与 [OPTIONS.md](references/OPTIONS.md#${write.id}) → 原生问人 → \`{"confirm":true,"formIds":["<answered.formId>"]}\` → \`python scripts/execute.py --capability ${write.id} --input '...' --confirm-write\`。
停止：缺必填、候选对不上、用户未确认或 \`cancelled\`。`);
  }
  if (query && write && !routes.length) {
    blocks.push(`用户：「用刚才查到的那条接着${write.title}」
规划：当前没有 \`approved: true\` 路线，不能把查询结果自行填进写操作。
执行：停下来问人。请用户明确是继续只查，还是按写操作表单重新收集字段。`);
  }
  for (const route of routes) {
    blocks.push(`用户目标落在「${route.title}」
规划：走已确认路线，按步骤串联，只传合同绑定。
执行：读 [routes/${route.id}.md](references/routes/${route.id}.md)，每步独立执行。
停止：上一步结果为空或不能唯一确定。`);
  }
  return blocks.join("\n\n");
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

function routeByIntent(primary: CapabilityContract[], lookups: CapabilityContract[]) {
  const query = primary.filter(capability => capability.operation === "query");
  const writes = primary.filter(capability => capability.confirmation.required);
  const others = primary.filter(capability => !query.includes(capability) && !writes.includes(capability));
  const lookupNames = lookups.map(capability => capability.title.replace(/^查询/, "")).filter(Boolean).join("、");
  const sections: string[] = [];
  if (query.length) {
    sections.push(`### 查、筛、列单据

当用户只要看已有单据时走这里。选定后先读 [CAPABILITIES.md](references/CAPABILITIES.md)，再读该能力在 [INPUT_FORMS.md](references/INPUT_FORMS.md) 的小节。

${query.map(capability => `- ${capability.title}：\`${capability.id}\``).join("\n")}`);
  }
  if (writes.length) {
    sections.push(`### 写单据

当用户要新建、修改、审核或删除时走这里。选定后读表单和候选；执行前必须确认。

${writes.map(capability => `- ${capability.title}：\`${capability.id}\``).join("\n")}`);
  }
  if (others.length) {
    sections.push(`### 其他已验证动作

${others.map(capability => `- ${capability.title}：\`${capability.id}\``).join("\n")}`);
  }
  if (lookups.length) {
    sections.push(`### 只是选一个名称

当用户只要选${lookupNames || "目录值"}时，不要走主能力。读 [OPTIONS.md](references/OPTIONS.md) 取候选。`);
  }
  return sections.join("\n\n");
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

把本页当作路由手册。先判断用户目标，再按需读取一份 reference；不要一开始读完 \`references/\`。这不是业务说明书，也不解释代码怎么实现。

## 前置

- 工作目录必须是本 \`SKILL.md\` 所在目录。
- 认证只走环境变量 \`SKILL_AUTH_HEADERS\`。不要把凭证写进对话或文件。
- Prefer HTTP：用本目录 \`scripts/\` 执行。不要绕过脚本直接拼请求。

## 何时使用

用户要「${actions || "已验证业务动作"}」时使用，即使没说出 Skill 名或接口名。

## 何时不要使用

- 未列入本手册的编辑、删除、审批、导入导出。
- ${lookupNames ? `把${lookupNames}当成独立业务去查询或交差。` : "把目录、字典或候选列表当成独立业务。"}
- 发明合同里没有的接口、字段、枚举或绑定。

## 路由

先判断用户要哪一类事，再打开对应文件。不要同时打开全部 reference。

${routeByIntent(primary, lookups)}

## 能力怎么组合

${compositionNarrative(title, primary, lookups, routes)}

具体规划例子见 [PLAYBOOK.md](references/PLAYBOOK.md)。

${planningExamples(primary, routes)}

## 可执行约定

规划结束后按下述约定执行，不要再混合推理。

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
- 写操作还没有 \`confirm: true\`，或用户返回 \`cancelled\`。
- 目标超出本手册，或 Prefer HTTP 与 Fallback 都无法完成。

## 收集输入

需要补字段时原生调用 \`ask_user_question\`。控件、默认策略和 \`dataSource\` 以当前能力在 [INPUT_FORMS.md](references/INPUT_FORMS.md) 的小节为准。

## 执行与输出

- Prefer：\`python scripts/execute.py --capability <能力编号> --input '<JSON>'\`。写操作在确认后追加 \`--confirm-write\`。
- 写操作先调用 \`{"confirm": true, "formIds": ["<answered.formId>"]}\`，只有 \`confirmed\` 才执行。
- 列表先 \`python scripts/format_list.py\`：无数据输出「无数据」；表头、分隔行、数据行之间不插空行；单元格换行使用 \`<br>\`。
- 非列表只展示合同完成条件里的业务字段，不要把内部 ID 或裸 data 猜成业务编号。
- Prefer 失败时整段改走 Fallback（内置浏览器按已验证合同补录），并写明走了哪条路径。
- 结果样例和列表列选择见 [PLAYBOOK.md](references/PLAYBOOK.md)。

## 质量与完成

- HTTP 状态和合同完成断言都满足才算成功。
- 写操作结果不明确时不要重试。
- 未列入的能力不要假装支持。

## 失败处理

| 情况 | 处理 |
| --- | --- |
| 缺鉴权或 401/403 | 停止。请用户提供当前会话的 \`SKILL_AUTH_HEADERS\`，不要猜 token。 |
| 超时或网络失败 | Prefer 整段改走 Fallback，并写明路径。不要重试写操作。 |
| HTTP 已返回但断言不满足 | 按合同失败处理；展示关键错误字段，不要编造成功。 |
| \`ask_user_question\` 为 \`cancelled\` | 立即停止当前流程。 |
| 字段类型或候选不唯一 | 只重问错误字段。 |

细则见 [PLAYBOOK.md](references/PLAYBOOK.md)。

## 安全边界

- 不发明接口、字段、枚举或绑定。
- 不把凭证、Cookie 或 secret-bearing headers 写入 Skill 或对话记录。
- 不绕过脚本直接访问业务系统。
- 不确定时停下来问人，不要把不确定内容说成确定事实。

## 按需读取

- [CAPABILITIES.md](references/CAPABILITIES.md)：选定主能力
- [INPUT_FORMS.md](references/INPUT_FORMS.md)：填写当前能力
- [OPTIONS.md](references/OPTIONS.md)：取字段候选
- [PLAYBOOK.md](references/PLAYBOOK.md)：规划例子、输出样例、失败细则
- [CONTRACT.json](references/CONTRACT.json)：机器事实来源
${routes.length ? "- [routes/](references/routes/)：已确认组合路线\n" : ""}`;
}

export function buildPlaybook(displayName: string, capabilities: CapabilityContract[], routes: CapabilityRoute[]) {
  const { primary } = classifyExported(capabilities);
  const query = primary.find(capability => capability.operation === "query");
  const write = primary.find(capability => capability.confirmation.required);
  const itemsPath = query ? itemsPathOf(query.outputSchema) : undefined;
  const listCommand = itemsPath
    ? `\`python scripts/format_list.py --input '-' --items-path '${itemsPath}'\``
    : "`python scripts/format_list.py --input '-' --items-path '<合同输出中的列表路径>'`";
  return `# ${safeCell(displayName)}：规划、输出与失败

只在规划举例、整理结果或处理失败时读取。不要用本页代替路由手册。

## 规划例子

${planningExamples(primary, routes)}

规划结束后，回到 \`SKILL.md\` 的可执行约定执行。

## 输出约定

### 列表

${listCommand}

- 无数据时输出「无数据」。
- 表头、分隔行、数据行之间不插空行。
- 单元格换行使用 \`<br>\`；不要把内部 ID 单独当成业务编号。
${query ? `- 「${query.title}」优先展示用户能辨认的业务列。` : ""}

### 写操作

${write ? `「${write.title}」成功后只报告：已提交、合同完成条件已满足、用户能核对的关键字段。不要倾倒完整返回体。` : "写操作成功后只报告完成条件和可核对字段。"}
失败时报告 HTTP 状态和合同未满足的断言，不要重试。

## 失败细则

- 缺 \`SKILL_AUTH_HEADERS\`、401、403：停止并请用户提供当前已登录会话头。
- 超时、连接失败：写明改走 Fallback；读操作可整段改走，写操作不自动重试。
- 业务返回失败字段：展示合同里存在的错误信息，不编造成功。
- \`invalid_question_arguments\` 且可重试：一次修正全部 issue 后替换提问。
- \`cancelled\` 或不可重试失败：停止当前流程，等待用户下一条消息。
`;
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
- 执行：\`python scripts/execute.py --capability ${capability.id} --input '{...}'${capability.confirmation.required ? " --confirm-write" : ""}\`
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
    let candidate = "自由输入";
    if (field.candidates?.type === "static") {
      candidate = field.candidates.values.map(item => `${item.label}=${String(item.value)}`).join("；");
    } else if (dataSource) {
      candidate = `dataSource: ${JSON.stringify(dataSource)}`;
    } else if (isDateField(field)) {
      candidate = field.dateClock
        ? `dateFormat: YYYY-MM-DD，请求补 ${field.dateClock}`
        : "dateFormat: YYYY-MM-DD";
    } else if (field.widget === "select" || field.widget === "multiselect") {
      candidate = "无固定候选";
    }
    const hint = /页面未唯一对应：(.+)$/.exec(field.sourceDetail || "")?.[1];
    const label = hint && field.label === field.name ? `${field.label}（${hint}）` : field.label;
    return `| \`${safeCell(questionKey(field, fields))}\` | ${safeCell(label)} | \`${inputType(field)}\` | ${field.required ? "是" : "否"} | ${safeCell(recommendedDefault(field, capability))} | ${safeCell(candidate)} |`;
  }).join("\n")}`;
}

function systemFieldTable(capability: CapabilityContract) {
  const fields = capability.inputForm.filter(field => field.source !== "caller");
  if (!fields.length) return "";
  return `

系统处理字段不要问用户。处理方式以录制证据里唯一成立的来源为准，可能是其它接口带出、请求内计算、字段拷贝、系统默认、会话或生成等。不要把某次录制样本当成固定业务值。查询里没有默认值的筛选不要编造。

| 合同路径 | 业务名称 | 来源 | 处理方式 |
|---|---|---|---|
${fields.map(field =>
    `| \`${safeCell(field.path)}\` | ${safeCell(field.label)} | ${sourceNames[field.source]} | ${safeCell(exportHandling(field))} |`
  ).join("\n")}`;
}

export function buildInputForms(capabilities: CapabilityContract[]) {
  const { primary } = classifyExported(capabilities);
  return `# 输入表单

只读取当前能力的小节。仅向用户询问“调用方提供”的字段。

需要补充字段时必须原生调用 \`ask_user_question\`，不得在普通文本中模拟。把同一阶段字段合并为一次 \`title + questions[]\`；\`questions[].id\` 使用下表提问编号。调用前按合同 \`defaultStrategy\` 生成本次非空推荐值，不能复制未见过的值。类型或候选转换不唯一时，只重问错误字段；\`cancelled\` 后立即停止。

${primary.map(capability => `## ${capability.id}

${safeCell(capability.title)} · ${operationNames[capability.operation]} · \`${capability.transport.method} ${capability.transport.pathTemplate}\`
${capability.confirmation.required ? "\n写操作：字段齐备后调用 `{\"confirm\":true,\"formIds\":[\"<answered.formId>\"]}`，得到 `confirmed` 再加 `--confirm-write`。\n" : "\n查询：只收集用户点名的筛选条件。\n"}
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
  const dataSource = publishedDataSource(field, capabilities);
  return `- \`${field.path}\`（${field.label}）：接口候选。用户看显示名，接口收稳定值。dataSource: \`${JSON.stringify(dataSource)}\``;
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

dataSource 必须声明 \`type\`、\`endpoint\`、\`method\`、\`params\`、\`resultPath\`、\`idField\`、\`labelField\`。没有观察到固定入参时 \`params\` 写 \`{}\`。只有候选接口本身带搜索或分页字段时，再补充 \`searchParam\`、\`pageParam\`、\`pageSizeParam\`、\`pageSize\`、\`totalPath\`。不要编造未观察到的参数名。

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
