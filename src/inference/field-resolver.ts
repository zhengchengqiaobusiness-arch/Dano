import type { InputFormField, UiEvidence } from "../domain.js";

const GENERATED_NAME = /^(el-id-\d+-\d+|el-[a-z]+-\d+)$/i;
const PAGE_NAME = /^(pageNo|pageSize|pageNum|page|size|current|offset|limit)$/i;
const COMPUTED_NAME = /^(amount|total|taxAmount|taxPrice|taxAmt|discountAmount|discountPrice|payable|paid|sum|totalPrice|totalProductPrice)$/i;
const AUTO_GENERATED = /^(no|orderNo|orderNumber|orderSn)$/i;
const AUTO_FROM_LOOKUP = /^(productUnitName|productBarCode|stockCount|productName)$/i;
const LOOKUP_FIELD = /^(productId|product|supplierId|supplier|accountId|account|creator|creatorId|createUser|createUserId|userId|owner)$/i;
const NEVER_STATIC = /time|date|price|count|percent|qty|remark|^no$|amount|total/i;
const EDITABLE_LINE_ITEM = /^(count|qty|quantity|productPrice|unitPrice|taxPercent|taxRate|discountPercent|depositPrice|remark|orderTime)/i;
const NAME_LABELS: Array<[RegExp, string]> = [
  [/inStatus/i, "入库数量"],
  [/inCount/i, "入库数量"],
  [/returnStatus/i, "退货数量"],
  [/returnCount/i, "退货数量"],
  [/orderTime\[1\]/, "结束日期"],
  [/orderTime\[0\]/, "开始日期"],
  [/^orderTime$/i, "订单时间"],
  [/^(count|qty|quantity)$/i, "数量"],
  [/totalProductPrice/i, "金额"],
  [/^(productPrice|unitPrice)$/i, "产品单价"],
  [/taxPercent|taxRate/i, "税率"],
  [/discountPercent/i, "优惠率"],
  [/depositPrice/i, "支付订金"],
  [/discountPrice/i, "付款优惠"],
  [/taxPrice/i, "税额"],
  [/^totalPrice$/i, "优惠后金额"],
  [/productUnitName/i, "单位"],
  [/productBarCode/i, "条码"],
  [/stockCount/i, "库存"],
  [/^status$/i, "状态"],
  [/^(no|orderNo)$/i, "订单单号"],
  [/supplier/i, "供应商"],
  [/^productId$|^product$/i, "产品"],
  [/account/i, "结算账户"],
  [/creator/i, "创建人"],
  [/remark/i, "备注"]
];
const LABEL_ALIASES: Array<[RegExp, RegExp]> = [
  [/入库状态/, /inStatus|stockIn/i],
  [/入库数量/, /inCount|inStatus/i],
  [/退货状态/, /returnStatus|refund/i],
  [/退货数量/, /returnCount|returnStatus/i],
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
  [/^状态$/, /^status$/i],
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
  type?: string;
  options?: Array<{ value: unknown; label: string }>;
}

function optionsOf(event: UiEvidence, field?: NonNullable<UiEvidence["form"]>[number]) {
  const raw = field
    ? field.options
    : event.options?.length
      ? event.options
      : event.visibleOptions?.map(label => ({ value: label, label }));
  return raw?.filter(item => String(item.label || item.value || "").trim()).slice(0, 200);
}

function eventLabel(event: UiEvidence) {
  if (event.label) return event.label;
  const match = String(event.text || "").match(/^请选择(.+)$/);
  return match?.[1];
}

function sharesToken(label: string, option: string) {
  for (let index = 0; index < label.length - 1; index += 1) {
    const token = label.slice(index, index + 2);
    if (/^[\u4e00-\u9fff]{2}$/.test(token) && option.includes(token)) return true;
  }
  return false;
}

export function displayLabelFor(name: string, fallback?: string) {
  return NAME_LABELS.find(([pattern]) => pattern.test(name))?.[1] || fallback || name;
}

export function collectUiObservations(events: UiEvidence[]): UiObservation[] {
  const items: UiObservation[] = [];
  for (const event of events) {
    const eventOptions = optionsOf(event);
    const label = eventLabel(event);
    const controlType = eventOptions?.length || event.role === "combobox" ? "select" : (event.inputType || event.role);
    if (event.name && !isGeneratedFieldName(event.name)) {
      items.push({ name: event.name, label, value: event.value, type: controlType, options: eventOptions });
    } else if (label) {
      items.push({ name: undefined, label, value: event.value, type: controlType, options: eventOptions });
    } else if (eventOptions?.length) {
      const matches = (event.form || []).filter(field =>
        field.label && eventOptions.some(item => sharesToken(field.label!, String(item.label || item.value)))
      );
      if (matches.length === 1) {
        items.push({ name: matches[0]!.name, label: matches[0]!.label, value: matches[0]!.value, type: matches[0]!.type, options: eventOptions });
      }
    }
    for (const field of event.form || []) {
      const name = field.name && !isGeneratedFieldName(field.name) ? field.name : undefined;
      items.push({ name, label: field.label, value: field.value, type: field.type, options: optionsOf(event, field) });
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

function preferredStatusLabel(name: string) {
  if (/inStatus/i.test(name)) return /入库数量|入库状态/;
  if (/returnStatus/i.test(name)) return /退货数量|退货状态/;
  if (/^status$/i.test(name)) return /^状态$/;
  return undefined;
}

function rankObservations(field: InputFormField, items: UiObservation[]) {
  const wanted = preferredStatusLabel(field.name);
  if (wanted) {
    return items.find(item => item.options?.length && wanted.test(item.label || ""))
      || items.find(item => wanted.test(item.label || ""))
      || items.find(item => item.options?.length)
      || items[0];
  }
  return items.find(item => item.options?.length) || items[0];
}

export function findObservation(field: InputFormField, requestValue: unknown, observations: UiObservation[]) {
  const byName = observations.filter(item => item.name && item.name === field.name);
  const byLabel = observations.filter(item =>
    Boolean(item.label) && (
      item.label === field.label
      || item.label === field.name
      || labelMatchesName(item.label, field.name)
    )
  );
  const matched = [...byName, ...byLabel];
  if (matched.length) {
    const ranked = rankObservations(field, matched);
    const typed = matched.find(item => item.type && (item.label === ranked?.label || item.name === ranked?.name || item.name === field.name));
    return ranked && typed && !ranked.type ? { ...ranked, type: typed.type } : ranked;
  }
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
    if (typeof requestValue === "number") {
      return "，页面按 YYYY-MM-DD 填写，执行器转成当天 00:00 的毫秒时间戳";
    }
    if (typeof requestValue === "string" && /00:00:00/.test(String(requestValue))) {
      return "，页面按 YYYY-MM-DD 填写，请求使用 YYYY-MM-DD 00:00:00";
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

function preferredLabel(field: InputFormField, matched?: UiObservation) {
  const named = displayLabelFor(field.name);
  const ui = matched?.label && matched.label !== field.name ? matched.label : undefined;
  const wanted = preferredStatusLabel(field.name);
  if (wanted) {
    if (ui && wanted.test(ui)) return ui;
    return named || ui || field.label;
  }
  return ui || (field.label && field.label !== field.name ? field.label : undefined) || named || field.name;
}

function asCaller(field: InputFormField, matched: UiObservation | undefined, requestValue: unknown, observations: UiObservation[] = []): InputFormField {
  const selected = matched?.value
    || observations.find(item => item.label === matched?.label && item.value !== undefined && item.value !== "")?.value;
  const controlType = `${matched?.type || ""}`;
  const looksSelect = /select|combobox/i.test(controlType);
  const looksText = /text|textarea|search|date|number|tel|email/i.test(controlType) && !looksSelect;
  const useStatic = Boolean(matched?.options?.length)
    && !LOOKUP_FIELD.test(field.name)
    && !NEVER_STATIC.test(field.name)
    && !looksText;
  const options = useStatic
    ? matched!.options!.map(item =>
      selected !== undefined && String(item.label) === String(selected) && requestValue !== undefined && requestValue !== selected
        ? { value: requestValue, label: String(item.label) }
        : { value: item.value, label: String(item.label || item.value) }
    )
    : undefined;
  return {
    ...field,
    label: preferredLabel(field, matched),
    source: "caller",
    systemHandled: false,
    widget: LOOKUP_FIELD.test(field.name)
      ? "select"
      : NEVER_STATIC.test(field.name)
        ? (field.widget === "select" ? "text" : field.widget)
        : options?.length || looksSelect
          ? "select"
          : looksText && field.widget === "select" ? "text" : field.widget,
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
      label: displayLabelFor(field.name, field.label),
      source: "system",
      systemHandled: true,
      required: false,
      sourceDetail: "选择产品或关联对象后由页面自动带出。调用方不要手填；漏掉会导致提交成功但单位、条码或库存为空"
    };
  }
  if (COMPUTED_NAME.test(field.name)) {
    return {
      ...field,
      label: displayLabelFor(field.name, field.label),
      source: "computed",
      systemHandled: true,
      required: false,
      requiredBasis: "not-observed",
      sourceDetail: "后台根据已填字段自动计算。调用方不要改写；漏传会导致提交成功但金额、税额或合计为空"
    };
  }
  const matched = findObservation(field, requestValue, observations);
  if (matched) return asCaller(field, matched, requestValue, observations);
  if (isEditableBusinessField(field)) return asCaller(field, undefined, requestValue, observations);
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
