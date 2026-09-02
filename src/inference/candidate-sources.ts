import type { CapabilityContract, CandidateRule, InputFormField } from "../domain.js";

const LOOKUPS: Array<{ field: RegExp; label: RegExp; path: RegExp }> = [
  { field: /^(productId|product)$/i, label: /^(产品名称|产品|商品)$/, path: /\/product\//i },
  { field: /^(supplierId|supplier)$/i, label: /^(供应商)$/, path: /\/supplier\//i },
  { field: /^(accountId|account)$/i, label: /^(结算账户|账户)$/, path: /\/account\//i },
  { field: /^(creator|creatorId|createUser|createUserId|userId)$/i, label: /^(创建人)$/, path: /\/user\/simple-list|\/user\/list/i }
];
const NOT_LOOKUP = /price|count|percent|qty|amount|total|tax|stock|unit|barCode|name$/i;

function schemaNode(schema: any, jsonPath: string): any {
  const parts = jsonPath.replace(/^\$\.?/, "").split(".").filter(Boolean);
  let current = schema;
  for (const raw of parts) {
    const wildcard = raw.endsWith("[*]");
    const part = wildcard ? raw.slice(0, -3) : raw;
    if (part) current = current?.properties?.[part];
    if (!current) return undefined;
    if (wildcard) current = current.items;
  }
  return current;
}

function listPaths(schema: CapabilityContract["outputSchema"]): { valuePath: string; labelPath: string } | undefined {
  const arrays = ["$.data[*]", "$.data.list[*]", "$.list[*]"];
  const valueKeys = ["id", "value", "code"];
  const labelKeys = ["name", "label", "nickname", "title"];
  for (const prefix of arrays) {
    const item = schemaNode(schema, prefix);
    const properties = item?.properties || {};
    const valueKey = valueKeys.find(key => properties[key]);
    const labelKey = labelKeys.find(key => properties[key]);
    if (valueKey && labelKey) {
      return { valuePath: `${prefix}.${valueKey}`, labelPath: `${prefix}.${labelKey}` };
    }
  }
  return undefined;
}

function lookupFor(field: InputFormField, catalog: CapabilityContract[]) {
  const byName = LOOKUPS.find(item => item.field.test(field.name));
  const byLabel = !byName && !NOT_LOOKUP.test(field.name)
    ? LOOKUPS.find(item => item.label.test(field.label || ""))
    : undefined;
  const rule = byName || byLabel;
  if (!rule) return undefined;
  return catalog.find(capability =>
    capability.operation === "query"
    && capability.validation.status === "verified"
    && rule.path.test(capability.transport.pathTemplate)
  );
}

export function attachCandidateSources(catalog: CapabilityContract[]): CapabilityContract[] {
  return catalog.map(capability => ({
    ...capability,
    inputForm: capability.inputForm.map(field => {
      if (field.source !== "caller") return field;
      if (field.candidates?.type === "static") return field;
      const source = lookupFor(field, catalog);
      const paths = source ? listPaths(source.outputSchema) : undefined;
      if (!source || !paths) return field;
      const candidates: CandidateRule = {
        type: "capability",
        capabilityId: source.id,
        valuePath: paths.valuePath,
        labelPath: paths.labelPath
      };
      return {
        ...field,
        widget: "select",
        candidates,
        sourceDetail: `调用方从已录制查询接口选择，不要写死录制样本。接口 ${source.transport.method} ${source.transport.pathTemplate}，值 ${paths.valuePath}，显示 ${paths.labelPath}`
      };
    })
  }));
}

export function describeFieldHandling(field: InputFormField) {
  if (field.candidates?.type === "capability") {
    return `接口候选：先调 ${field.candidates.capabilityId}，调用方选显示名，提交 ${field.candidates.valuePath}`;
  }
  if (field.candidates?.type === "static") {
    return `页面固定枚举，调用方直接选择：${field.candidates.values.map(item => `${item.label}=${String(item.value)}`).join("；")}`;
  }
  if (field.source === "caller") return `调用方按页面原始格式输入（${field.valueType}），不要改成其它类型`;
  if (field.source === "computed") return `后台自动计算，调用方不要漏传或改写，否则会提交成功但金额/税额等为空`;
  if (field.source === "generated") return `后台自动生成，调用方不要手填`;
  if (field.source === "fixed") return `系统补齐默认值 ${field.defaultRule || ""}，调用方可覆盖`;
  if (field.source === "session") return `会话环境自动提供 ${field.defaultRule || ""}`;
  if (field.source === "binding") return field.sourceDetail;
  return `后台自动处理：${field.sourceDetail}`;
}
