import type { InputFormField, UiEvidence } from "../domain.js";

const GENERATED_NAME = /^(el-id-\d+-\d+|el-[a-z]+-\d+)$/i;
const PAGE_NAME = /^(pageNo|pageSize|pageNum|page|size|current|offset|limit)$/i;
const COMPUTED_NAME = /^(amount|total|taxAmount|taxPrice|taxAmt|discountAmount|discountPrice|payable|paid|sum)$/i;
const LABEL_ALIASES: Array<[RegExp, RegExp]> = [
  [/备注|说明/, /remark|note|comment|memo|desc/i],
  [/数量/, /^(count|qty|quantity|num|number)$/i],
  [/单价|价格/, /price|unitPrice|productPrice/i],
  [/税率/, /taxPercent|taxRate|^tax$/i],
  [/优惠/, /discountPercent|discountRate|discount/i],
  [/订金|定金/, /deposit/i],
  [/订单时间|下单时间|日期/, /time|date|orderTime/i],
  [/供应商/, /supplier/i],
  [/产品|商品/, /product/i],
  [/账户|结算/, /account/i],
  [/订单单号|单号/, /^(no|orderNo|orderNumber|orderSn)$/i]
];

export function isGeneratedFieldName(name?: string) {
  return Boolean(name && GENERATED_NAME.test(name));
}

export interface UiObservation {
  name?: string;
  label?: string;
  value?: unknown;
}

export function collectUiObservations(events: UiEvidence[]): UiObservation[] {
  const items: UiObservation[] = [];
  for (const event of events) {
    if (event.name && !isGeneratedFieldName(event.name)) {
      items.push({ name: event.name, label: event.label, value: event.value });
    } else if (event.label) {
      items.push({ name: undefined, label: event.label, value: event.value });
    }
    for (const field of event.form || []) {
      const name = field.name && !isGeneratedFieldName(field.name) ? field.name : undefined;
      items.push({ name, label: field.label, value: field.value });
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

function labelMatchesName(label: string | undefined, name: string) {
  if (!label) return false;
  return LABEL_ALIASES.some(([labelPattern, namePattern]) => labelPattern.test(label) && namePattern.test(name));
}

function findObservation(field: InputFormField, requestValue: unknown, observations: UiObservation[]) {
  return observations.find(item => {
    if (item.name && item.name === field.name) return true;
    if (item.label && item.label === field.label) return true;
    if (sameValue(item.value, requestValue)) return true;
    return labelMatchesName(item.label, field.name);
  });
}

export function resolveFieldOwnership(
  field: InputFormField,
  requestValue: unknown,
  observations: UiObservation[]
): InputFormField {
  if (PAGE_NAME.test(field.name) && requestValue !== undefined && requestValue !== null && requestValue !== "") {
    return {
      ...field,
      source: "fixed",
      systemHandled: true,
      required: true,
      defaultRule: `literal:${String(requestValue)}`,
      sourceDetail: "分页参数来自录制请求中的固定值"
    };
  }
  if (COMPUTED_NAME.test(field.name) && !observations.some(item => item.name === field.name || sameValue(item.value, requestValue))) {
    return {
      ...field,
      source: "computed",
      systemHandled: true,
      required: false,
      requiredBasis: "not-observed",
      sourceDetail: "请求中出现但页面未输入，按业务系统计算值处理"
    };
  }
  const matched = findObservation(field, requestValue, observations);
  if (!matched) return field;
  return {
    ...field,
    label: field.label && field.label !== field.name ? field.label : (matched.label || field.label),
    source: "caller",
    systemHandled: false,
    sourceDetail: "真实页面输入或选择已观察到，由调用方提供"
  };
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
      || sameValue(item.value, field.value)
      || labelMatchesName(item.label, field.name)
    )
  );
}
