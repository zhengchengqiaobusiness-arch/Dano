import type { InputFormField, UiEvidence } from "../domain.js";

const GENERATED_NAME = /^(el-id-\d+-\d+|el-[a-z]+-\d+)$/i;
const PAGE_NAME = /^(pageNo|pageSize|pageNum|page|size|current|offset|limit)$/i;
const COMPUTED_NAME = /^(amount|total|taxAmount|taxPrice|taxAmt|discountAmount|discountPrice|payable|paid|sum|totalPrice|totalProductPrice)$/i;
const AUTO_GENERATED = /^(no|orderNo|orderNumber|orderSn)$/i;
const AUTO_FROM_LOOKUP = /^(productUnitName|productBarCode|stockCount|productName)$/i;
const LOOKUP_FIELD = /^(productId|product|supplierId|supplier|accountId|account|creator|creatorId|createUser|createUserId|userId|owner)$/i;
const EDITABLE_LINE_ITEM = /^(count|qty|quantity|productPrice|unitPrice|taxPercent|taxRate|discountPercent|depositPrice|remark|orderTime)$/i;
const LABEL_ALIASES: Array<[RegExp, RegExp]> = [
  [/入库数量|入库状态/, /inStatus|inCount|stockIn/i],
  [/退货数量|退货状态/, /returnStatus|returnCount|refund/i],
  [/结束日期|结束时间/, /orderTime\[1\]|endTime|endDate|\[1\]$/i],
  [/开始日期|开始时间/, /orderTime\[0\]|startTime|startDate|\[0\]$/i],
  [/订单时间|下单时间/, /^(orderTime|orderDate)$/i],
  [/产品名称|^产品$|商品/, /^(productId|product)$/i],
  [/供应商/, /supplier/i],
  [/结算账户|账户/, /account/i],
  [/创建人/, /creator|createUser|creatorId|userId/i],
  [/备注|说明/, /remark|note|comment|memo|desc/i],
  [/^数量$/, /^(count|qty|quantity)$/i],
  [/单价|价格/, /price|unitPrice|productPrice/i],
  [/税率/, /taxPercent|taxRate|^tax$/i],
  [/优惠率/, /discountPercent|discountRate/i],
  [/订金|定金/, /deposit/i],
  [/订单单号|单号/, /^(no|orderNo|orderNumber|orderSn)$/i],
  [/状态/, /^(status)$/i],
  [/单位/, /unit/i],
  [/条码/, /barCode|barcode|bar_code/i],
  [/库存/, /stock/i]
];

export function isGeneratedFieldName(name?: string) {
  return Boolean(name && GENERATED_NAME.test(name));
}

export function isPaginationField(name?: string) {
  return Boolean(name && PAGE_NAME.test(name));
}

export function isLookupField(name?: string) {
  return Boolean(name && LOOKUP_FIELD.test(name));
}

export function isEditableBusinessField(field: Pick<InputFormField, "name">) {
  return isLookupField(field.name) || EDITABLE_LINE_ITEM.test(field.name);
}

export interface UiObservation {
  name?: string;
  label?: string;
  value?: unknown;
  options?: Array<{ value: unknown; label: string }>;
}

function optionsOf(event: UiEvidence, field?: NonNullable<UiEvidence["form"]>[number]) {
  const raw = field?.options?.length
    ? field.options
    : event.options?.length
      ? event.options
      : event.visibleOptions?.map(label => ({ value: label, label }));
  return raw?.filter(item => String(item.label || item.value || "").trim()).slice(0, 200);
}

export function collectUiObservations(events: UiEvidence[]): UiObservation[] {
  const items: UiObservation[] = [];
  for (const event of events) {
    const eventOptions = optionsOf(event);
    if (event.name && !isGeneratedFieldName(event.name)) {
      items.push({ name: event.name, label: event.label, value: event.value, options: eventOptions });
    } else if (event.label) {
      items.push({ name: undefined, label: event.label, value: event.value, options: eventOptions });
    }
    for (const field of event.form || []) {
      const name = field.name && !isGeneratedFieldName(field.name) ? field.name : undefined;
      items.push({ name, label: field.label, value: field.value, options: optionsOf(event, field) });
    }
  }
  return items;
}

export function flattenRequestValues(value: unknown, prefix = "$"): Array<{ path: string; name: string; value: unknown }> {
  if (value === null || value === undefined) return [];
  if (Array.isArray(value)) {
    const first = value.find(item => item !== null && item !== undefined);
    return flattenRequestValues(first, `${prefix}[*]`);
  }
  if (typeof value !== "object") {
    const name = prefix.split(".").pop()?.replace(/\[\*\]$/, "") || prefix;
    return [{ path: prefix, name, value }];
  }
  return Object.entries(value as Record<string, unknown>).flatMap(([key, child]) =>
    flattenRequestValues(child, `${prefix}.${key}`)
  );
}

function sameValue(left: unknown, right: unknown) {
  if (left === undefined || left === null || right === undefined || right === null || right === "") return false;
  if (Object.is(left, right)) return true;
  if (typeof left === "boolean" || typeof right === "boolean") return Boolean(left) === Boolean(right);
  if (typeof left === "number" || typeof right === "number") return Number(left) === Number(right);
  return String(left) === String(right);
}

function isDistinctiveValue(value: unknown) {
  if (value === undefined || value === null || value === "") return false;
  if (typeof value === "boolean") return true;
  if (typeof value === "number" && (value === 0 || value === 1)) return false;
  if (typeof value === "string" && /^(0|0\.0+|1|1\.0+)$/.test(value.trim())) return false;
  return String(value).length > 1;
}

export function labelMatchesName(label: string | undefined, name: string) {
  if (!label) return false;
  return LABEL_ALIASES.some(([labelPattern, namePattern]) => labelPattern.test(label) && namePattern.test(name));
}

export function findObservation(field: InputFormField, requestValue: unknown, observations: UiObservation[]) {
  const byName = observations.find(item => item.name && item.name === field.name);
  if (byName) return byName;
  const byLabel = observations.find(item =>
    Boolean(item.label) && (
      item.label === field.label
      || item.label === field.name
      || labelMatchesName(item.label, field.name)
    )
  );
  if (byLabel) return byLabel;
  if (!isDistinctiveValue(requestValue)) return undefined;
  return observations.find(item => sameValue(item.value, requestValue));
}

export function fieldHasUiEvidence(field: InputFormField, events: UiEvidence[]) {
  return Boolean(findObservation(field, undefined, collectUiObservations(events)));
}

export function staticCandidatesHaveUiEvidence(field: InputFormField, events: UiEvidence[]) {
  if (field.candidates?.type !== "static") return true;
  return collectUiObservations(events).some(item =>
    Boolean(item.options?.length) && (
      (item.name && item.name === field.name)
      || (item.label && (item.label === field.label || labelMatchesName(item.label, field.name)))
    )
  );
}

function literalRule(value: unknown) {
  if (value === undefined || value === null || value === "") return undefined;
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return `literal:${String(value)}`;
  }
  return undefined;
}

function formatHint(field: InputFormField, observation?: UiObservation, requestValue?: unknown) {
  if (/time|date|start|end/i.test(`${field.name} ${field.label}`)) {
    if (typeof requestValue === "number" && observation && typeof observation.value === "string") {
      return "，页面按日期填写，请求使用对应时间戳，不要改成录制当天的固定数字";
    }
    return "，保持页面原始日期格式";
  }
  if (field.valueType === "number" || field.valueType === "integer") return "，保持页面数字格式";
  return "，保持页面原始输入格式";
}

function finalizeUnhandled(field: InputFormField, requestValue: unknown): InputFormField {
  if (AUTO_GENERATED.test(field.name)) {
    return {
      ...field,
      source: "generated",
      systemHandled: true,
      required: false,
      sourceDetail: "后台自动生成。调用方不要手填；漏掉该字段的生成逻辑会导致提交成功但单号或标识为空"
    };
  }
  return {
    ...field,
    source: "system",
    systemHandled: true,
    required: false,
    defaultRule: field.defaultRule || literalRule(requestValue),
    sourceDetail: "后台自动处理。执行器按录制默认补齐；调用方不要手填，也不要漏掉该字段的业务含义，否则会看似提交成功但内容缺失"
  };
}

function asCaller(field: InputFormField, matched: UiObservation | undefined, requestValue: unknown): InputFormField {
  const useStatic = Boolean(matched?.options?.length) && !LOOKUP_FIELD.test(field.name);
  const options = useStatic
    ? matched!.options!.map(item =>
      String(item.label) === String(matched!.value) && requestValue !== undefined && requestValue !== matched!.value
        ? { value: requestValue, label: String(item.label) }
        : { value: item.value, label: String(item.label || item.value) }
    )
    : undefined;
  return {
    ...field,
    label: field.label && field.label !== field.name ? field.label : (matched?.label || field.label),
    source: "caller",
    systemHandled: false,
    widget: options?.length || LOOKUP_FIELD.test(field.name) ? "select" : field.widget,
    candidates: options?.length ? { type: "static", values: options } : field.candidates,
    sourceDetail: options?.length
      ? "页面固定枚举，调用方直接选择，不要写成录制时的固定样本"
      : LOOKUP_FIELD.test(field.name)
        ? "调用方从已录制查询接口选择，不要写死录制样本"
        : `调用方按页面原始格式提供（${field.valueType}）${formatHint(field, matched, requestValue)}，不要改成录制样本`
  };
}

export function resolveFieldOwnership(
  field: InputFormField,
  requestValue: unknown,
  observations: UiObservation[]
): InputFormField {
  if (PAGE_NAME.test(field.name)) {
    return {
      ...field,
      source: "system",
      systemHandled: true,
      required: false,
      defaultRule: literalRule(requestValue) || field.defaultRule || "literal:1",
      sourceDetail: "列表分页由执行器按默认值补齐，调用方可覆盖，不要当成业务主键"
    };
  }
  if (AUTO_FROM_LOOKUP.test(field.name)) {
    return {
      ...field,
      source: "system",
      systemHandled: true,
      required: false,
      sourceDetail: "选择产品或关联对象后由页面自动带出。调用方不要手填；漏掉会导致提交成功但单位、条码或库存为空"
    };
  }
  if (COMPUTED_NAME.test(field.name)) {
    return {
      ...field,
      source: "computed",
      systemHandled: true,
      required: false,
      requiredBasis: "not-observed",
      sourceDetail: "后台根据已填字段自动计算。调用方不要改写；漏传会导致提交成功但金额、税额或合计为空"
    };
  }
  const matched = findObservation(field, requestValue, observations);
  if (matched) return asCaller(field, matched, requestValue);
  if (isEditableBusinessField(field)) return asCaller(field, undefined, requestValue);
  return finalizeUnhandled(field, requestValue);
}

export function requestValueAt(sample: unknown, path: string) {
  return flattenRequestValues(sample).find(item => item.path === path)?.value;
}

export function uiSupportsRequest(event: UiEvidence, sample: unknown) {
  const values = flattenRequestValues(sample);
  if (!values.length) return false;
  return collectUiObservations([event]).some(item =>
    values.some(field =>
      (item.name && item.name === field.name)
      || labelMatchesName(item.label, field.name)
      || (isDistinctiveValue(field.value) && sameValue(item.value, field.value))
    )
  );
}
