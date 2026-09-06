/**
 * 文件级说明：字段归属 / 同义词 / leftover 绑定的主流判断已交给 `.pi/skills/infer-field-contract`。
 * 本文件保留摊平请求、值比较、UI 观察收集，供校验和执行器使用。
 * bind* 系列不再做业务猜测。旧引擎全文见 `field-resolver.ts.bak`。
 */
import type { EvidenceEvent, InputFormField, NetworkEvidence, UiEvidence } from "../domain.js";
import { ASK_KEY } from "./heuristics.js";
import { clockFromEpoch, dateDay, isDateOnly, isIsoInstant, recordedClock, recordedDateFormat } from "./date-format.js";

const GENERATED_NAME = /^(el-id-\d+|el-[a-z]+-\d+|reka-v-\d+-form-item|input-\d+|select-\d+|aria-id|:r[0-9a-z]+$)/i;
const PAGE_NAME = /^(pageNo|pageSize|pageNum|page|size|current|offset|limit)$/i;

export function isGeneratedFieldName(name?: string) {
  return Boolean(name && GENERATED_NAME.test(name));
}

export function isPaginationField(name?: string) {
  return Boolean(name && PAGE_NAME.test(name));
}

export function uiNameMatches(uiName: string | undefined, requestName: string) {
  if (!uiName || isGeneratedFieldName(uiName)) return false;
  if (uiName === requestName) return true;
  return requestName.startsWith(`${uiName}[`);
}

export function realFieldName(name?: string) {
  return name && !isGeneratedFieldName(name) ? name : undefined;
}

export interface UiObservation {
  name?: string;
  label?: string;
  value?: unknown;
  type?: string;
  required?: boolean;
  disabled?: boolean;
  options?: Array<{ value: unknown; label: string }>;
  rangeIndex?: number;
}

export interface RecordedList {
  rows: Array<Record<string, unknown>>;
}

function optionsOf(event: UiEvidence, field?: NonNullable<UiEvidence["form"]>[number]) {
  const raw = field
    ? field.options
    : event.options?.length
      ? event.options
      : event.visibleOptions?.map(label => ({ value: label, label }));
  return raw?.filter(item => String(item.label || item.value || "").trim()).slice(0, 200);
}

function emptyPromptLabel(text?: string) {
  const raw = String(text || "").replace(/^样例-/, "");
  const stripped = raw.replace(/^(请选择|请输入|请填写|please select|please enter|please choose|select)\s*/i, "");
  return stripped && stripped !== raw ? stripped : undefined;
}

function observationLabel(item: { name?: string; label?: string; value?: unknown }) {
  const label = String(item.label || "").trim();
  const prompt = emptyPromptLabel(typeof item.value === "string" ? item.value : "");
  if (prompt && (!label || /^\d+$/.test(label) || pollutedLabel({ label, value: item.value }))) return prompt;
  return item.label;
}

function eventLabel(event: UiEvidence) {
  return event.label || emptyPromptLabel(event.text);
}

function labelTokens(label?: string) {
  return String(label || "").split(/\s+/).filter(token => token && !/^\d+(?:\.\d+)?$/.test(token));
}

function chooserRowDisplay(event: UiEvidence, label: string) {
  if (event.eventType !== "click" || event.scope !== "dialog") return undefined;
  if (event.tag === "button" || event.role === "button" || event.inputType === "button" || event.inputType === "submit") {
    return undefined;
  }
  if (event.value !== undefined && event.value !== "") return undefined;
  return labelTokens(label)[0];
}

export function flattenRequestValues(value: unknown, prefix = "$"): Array<{ path: string; name: string; value: unknown }> {
  if (value === undefined) return [];
  const name = prefix.split(".").pop()?.replace(/\[\*\]$/, "") || prefix;
  if (value === null || typeof value !== "object") return [{ path: prefix, name, value }];
  if (Array.isArray(value)) {
    if (!value.length) return [{ path: prefix, name, value }];
    const objects = value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item));
    if (objects.length) {
      const keys = [...new Set(objects.flatMap(item => Object.keys(item)))];
      const children = keys.flatMap(key => {
        const first = objects.find(item => item[key] !== undefined);
        return flattenRequestValues(first?.[key], `${prefix}[*].${key}`);
      });
      return [{ path: prefix, name, value }, ...children];
    }
    const first = value.find(item => item !== null && item !== undefined);
    const children = flattenRequestValues(first, `${prefix}[*]`);
    return [{ path: prefix, name, value }, ...children];
  }
  const entries = Object.entries(value as Record<string, unknown>);
  if (!entries.length) {
    return [{ path: prefix, name, value }];
  }
  return entries.flatMap(([key, child]) =>
    flattenRequestValues(child, `${prefix}.${key}`)
  );
}

export function richTextPlain(value: unknown) {
  if (typeof value !== "string" || !/<\/?[a-z][^>]*>/i.test(value)) return undefined;
  return value
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<\/(?:p|div|li|tr|h[1-6])\s*>/gi, "\n")
    .replace(/<[^>]+>/g, "")
    .replace(/&nbsp;|&#160;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&quot;/gi, '"')
    .replace(/&#(?:39|x27);/gi, "'")
    .replace(/&#(\d+);/g, (_match, code) => String.fromCodePoint(Number(code)))
    .replace(/&#x([0-9a-f]+);/gi, (_match, code) => String.fromCodePoint(Number.parseInt(code, 16)))
    .replace(/\s+/g, " ")
    .trim();
}

export function sameValue(left: unknown, right: unknown): boolean {
  if (left === undefined || left === null || right === undefined || right === null || right === "") return false;
  if (Object.is(left, right)) return true;
  if (Array.isArray(left) || Array.isArray(right)) {
    if (!Array.isArray(left) || !Array.isArray(right) || left.length !== right.length) return false;
    return left.every((item, index) => Object.is(item, right[index]) || sameValue(item, right[index]));
  }
  if (typeof left === "boolean" || typeof right === "boolean") {
    const asBoolean = (value: unknown) => {
      if (typeof value === "boolean") return value;
      if (value === 1 || value === "1" || value === "true") return true;
      if (value === 0 || value === "0" || value === "false") return false;
      return undefined;
    };
    const leftBoolean = asBoolean(left);
    const rightBoolean = asBoolean(right);
    return leftBoolean !== undefined && rightBoolean !== undefined && leftBoolean === rightBoolean;
  }
  const leftDay = dateDay(left);
  const rightDay = dateDay(right);
  if (leftDay && rightDay && leftDay === rightDay) {
    const leftClock = recordedClock(left) || clockFromEpoch(left);
    const rightClock = recordedClock(right) || clockFromEpoch(right);
    if (leftClock && rightClock) return leftClock === rightClock;
  }
  if (typeof left === "number" || typeof right === "number") return Number(left) === Number(right);
  const leftRich = richTextPlain(left);
  const rightRich = richTextPlain(right);
  if (leftRich !== undefined || rightRich !== undefined) {
    const plain = (value: unknown, rich?: string) => rich ?? String(value).replace(/\s+/g, " ").trim();
    return plain(left, leftRich) === plain(right, rightRich);
  }
  return String(left) === String(right);
}

function isDistinctiveValue(value: unknown) {
  if (value === undefined || value === null || value === "") return false;
  if (typeof value === "boolean") return true;
  if (typeof value === "number") return false;
  const text = String(value).trim();
  if (/^-?\d+(\.\d+)?$/.test(text)) return text.length >= 7;
  return text.length > 0;
}

function looksDateControl(item: Pick<UiObservation, "type" | "label" | "name"> | Pick<InputFormField, "name" | "label" | "widget">) {
  const widget = "widget" in item ? item.widget : undefined;
  const type = "type" in item ? item.type : undefined;
  const label = `${item.name || ""} ${item.label || ""}`;
  if (/时间|日期/.test(label) || /date|time/i.test(label)) return true;
  if (widget === "date" || type === "date" || type === "datetime" || type === "daterange") return true;
  return /datepicker|datetime|daterange|date-editor/i.test(`${type || ""} ${widget || ""}`);
}

function isPromptLabel(label?: string) {
  return /^(请输入|请选择|请填写|please (select|enter|choose))/i.test(String(label || "").trim());
}

function labelsEquivalent(left?: string, right?: string) {
  if (!left || !right) return false;
  if (left === right) return true;
  const normalize = (value: string) => value.replace(/^\*+/, "").replace(/[：:]\s*$/, "").trim();
  if (normalize(left) === normalize(right)) return true;
  const strippedLeft = emptyPromptLabel(left) || left;
  const strippedRight = emptyPromptLabel(right) || right;
  return normalize(strippedLeft) === normalize(strippedRight);
}

function preferLabeledObservation(items: UiObservation[]) {
  const business = items.filter(item => item.label && !isPromptLabel(item.label));
  const businessLabels = new Set(business.map(item => item.label));
  if (businessLabels.size === 1) return business[0];
  const prompts = items.filter(item => isPromptLabel(item.label));
  const promptLabels = new Set(prompts.map(item => item.label));
  return promptLabels.size === 1 ? prompts[0] : undefined;
}

function preferSpecificObservation(field: InputFormField, items: UiObservation[]) {
  if (items.length <= 1) return items[0];
  const named = items.filter(item => uiNameMatches(item.name, field.name));
  if (named.length === 1) return named[0];
  const promptHits = items.filter(item => {
    const specific = emptyPromptLabel(item.label);
    if (!specific && !isPromptLabel(item.label)) return false;
    return sameSynonymGroup(field, { name: specific || item.label, label: specific || item.label });
  });
  if (promptHits.length === 1) return promptHits[0];
  const synonym = items.filter(item => sameSynonymGroup(field, item));
  if (synonym.length === 1) return synonym[0];
  return preferLabeledObservation(items);
}

const SYNONYM_GROUPS = [
  /\b(?:start|begin|from)\s*(?:date|time)?\b|开始日期|开始时间|起始日期|起始时间/i,
  /\b(?:end|finish|to)\s*(?:date|time)?\b|结束日期|结束时间|截止日期|截止时间/i,
  /\b(?:count|qty|quantity)\b|数量/i,
  /\bseat\s*(?:count|num(?:ber)?)?\b|座位数|车座/i,
  /\b(?:user|person|people)\s*count\b|人数/i,
  /(?:^|[^a-z])price(?:$|[^a-z])|单价|售价/i,
  /taxpercent|tax_percent|税率/i,
  /(?:actual)?days?\b|天数/i,
  /remark|memo|comment|备注|说明|qzms|职能描述/i,
  /reason|原因/i,
  /type|类型/i,
  /level|等级/i,
  /catalogStatus|编目状态/i,
  /status|状态|结果/i,
  /assignee|approver|Activity_|审批|人员/i,
  /lxr|联系人/i,
  /lxfs|lxdh|mobile|phone|联系方式|电话/i,
  /ercsmc|二级内设|二级处室/i,
  /csmc|一级内设|内设机构|处室名称/i,
  /ssbmmc|所属部门|部门名称/i,
  /yyxtid|yyxtmc|ssxts|所属系统|应用系统/i,
  /ywsxmc|职能清单/i,
  /gjz|gjc|keyword|keyWord|keywords|searchKey|searchText|queryKey|(?:^|[^a-z])q(?:$|[^a-z])|(?:^|[^a-z])query(?:$|[^a-z])|(?:^|[^a-z])search(?:$|[^a-z])|关键字|关键词|搜索/i,
  /sys_query|userQuery|queryText|askText|(?:^|[^a-z])prompt(?:$|[^a-z])|(?:^|[^a-z])question(?:$|[^a-z])|问数|智能体聊天|聊天内容/i,
  /code|编码/i,
  /name|名称/i,
  /category|classify|classification|分类|类别/i,
  /processDefinition|processDefKey|definitionKey|所属流程|流程定义/i,
  /balance|remaining|remain|surplus|stock|inventory|quota|余额|剩余|库存/i,
  /progress|进度|完成率/i
];

function fieldText(field: { name?: string; label?: string }) {
  return `${field.name || ""} ${field.label || ""}`.replace(/([a-z])([A-Z])/g, "$1 $2");
}

export function sameSynonymGroup(field: { name?: string; label?: string }, item: { name?: string; label?: string }) {
  const left = fieldText(field);
  const right = fieldText(item);
  return SYNONYM_GROUPS.some(group => group.test(left) && group.test(right));
}

const SEMANTIC_CONCEPTS = [
  ["supplier", /\b(?:supplier|vendor)\b|供应商|供货商/i],
  ["account", /\baccount\b|账户|账号/i],
  ["product", /\b(?:product|goods)\b|商品|产品/i],
  ["discount", /\bdiscount\b|优惠|折扣/i],
  ["deposit", /\bdeposit\b|订金|定金/i],
  ["tax", /\btax\b|税/i],
  ["percent", /\b(?:percent|percentage|rate)\b|百分比|率/i],
  ["total", /\btotal\b|合计|总计|总额|总数|优惠后/i],
  ["unit-price", /\b(?:unit|product)\s+price\b|单价/i],
  ["money", /\b(?:price|amount|amt)\b|金额|税额|单价|价格|价款|货款|付款|订金|定金/i],
  ["count", /\b(?:count|qty|quantity)\b|数量|总数|人数|人次|(?:张|件|份|台|套|次)数/i],
  ["document", /\b(?:bill|invoice|receipt|document)\b|票据|发票|单据/i],
  ["item", /\b(?:item|fee|line)\b|款项|明细/i],
  ["room", /\broom\b|房间/i],
  ["person", /\b(?:user|person|people)\b|人员|人数|入住人/i],
  ["type", /\btype\b|类型/i],
  ["level", /\blevel\b|等级/i],
  ["seat", /\bseat\b|座位|车座/i],
  ["stock", /\b(?:stock|inventory)\b|库存/i],
  ["unit", /\bunit\b|单位/i],
  ["barcode", /\bbar\s*code\b|条码/i],
  ["remark", /\b(?:remark|memo|comment)\b|备注|说明/i],
  ["order", /\border\b|订单/i],
  ["time", /\b(?:time|date)\b|时间|日期/i]
] as const;

export type SemanticConcept = (typeof SEMANTIC_CONCEPTS)[number][0];

export function semanticConcepts(value: { name?: string; label?: string }): Set<SemanticConcept> {
  const text = fieldText(value).replace(/([a-z])([A-Z])/g, "$1 $2");
  return new Set(SEMANTIC_CONCEPTS.filter(([, pattern]) => pattern.test(text)).map(([name]) => name));
}

export function semanticLabelScore(field: InputFormField, item: UiObservation) {
  const target = semanticConcepts(field);
  const observed = semanticConcepts(item);
  if (!target.size || [...target].some(concept => !observed.has(concept))) return 0;
  return target.size * 10 - Math.max(0, observed.size - target.size);
}

function readonlySemanticScore(field: InputFormField, item: UiObservation) {
  const target = semanticConcepts(field);
  const observed = semanticConcepts(item);
  const overlap = [...target].filter(concept => observed.has(concept)).length;
  if (!overlap) return 0;
  let score = overlap * 10 - (target.size - overlap) * 2 - (observed.size - overlap);
  if (target.has("total")) {
    if (field.path.includes("[*]") && observed.has("tax")) score += 5;
    if (!field.path.includes("[*]") && observed.has("discount")) score += 5;
  }
  return score;
}

export function nameTokens(text: string) {
  return String(text || "")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/[^A-Za-z0-9\u4e00-\u9fff]+/g, " ")
    .toLowerCase()
    .split(/\s+/)
    .filter(token => token.length >= 2 && !/^(id|the|and|for|get|set|my|data|list|page|info|code|key)$/i.test(token));
}

function looksQuantityField(field: { name?: string; label?: string }) {
  return /day|count|qty|quantity|price|amount|total|balance|percent|stock|数量|天数|金额|单价|折扣|库存|余额|税率/i.test(fieldText(field));
}

function looksChoiceObservation(item: UiObservation) {
  return /select|combobox|picker/i.test(item.type || "");
}

function looksTextObservation(item: Pick<UiObservation, "type">) {
  return /text|textarea|search|tel|email/i.test(item.type || "")
    && !/select|combobox|picker/i.test(item.type || "");
}

function eventLooksChoice(event: UiEvidence) {
  if (/select|combobox|listbox/i.test(`${event.role || ""} ${event.tag || ""} ${event.inputType || ""}`)) return true;
  if (/text|textarea|search|tel|email|date|number|password/i.test(event.inputType || "")) return false;
  const labeled = (event.form || []).find(field =>
    field.label === event.label
    || Boolean(event.name && field.name === event.name)
    || Boolean(event.text && (field.label === event.text || field.label === emptyPromptLabel(event.text)))
  );
  if (labeled && looksTextObservation({ type: labeled.type }) && !looksChoiceObservation({ type: labeled.type })) {
    return false;
  }
  return Boolean((event.options?.length || event.visibleOptions?.length) && event.eventType !== "input");
}

function looksChoiceField(field: InputFormField) {
  return field.widget === "select" || field.widget === "multiselect"
    || /type|status|result|kind|category|dict|assignee|user|Activity_/i.test(field.name);
}

export function pickerEntity(value: { name?: string; label?: string; path?: string }) {
  const pathName = value.path?.split(".").pop()?.replace(/\[\*\]$/, "") || "";
  const text = fieldText({ name: value.name || pathName, label: value.label });
  if (/dept|department|部门|组织机构/.test(text) && !/creator|userId|userIds|人员|创建人|选人|审批人/.test(text)) return "dept";
  if (/\brole\b|角色/.test(text)) return "role";
  if (/\bpost\b|岗位|职位/.test(text)) return "post";
  if (/人员|创建人|选人|审批人|审批(?!结果|状态)|assignee|approver|creator|Activity_|userId|userIds|UserSelect/i.test(text)) return "user";
  return undefined;
}

export function looksPickerField(
  field: Pick<InputFormField, "name" | "label" | "widget">,
  observation?: Pick<UiObservation, "type">
) {
  if (/审批结果|审批状态|processStatus|approveStatus|auditStatus|approvalStatus/i.test(`${field.name || ""} ${field.label || ""}`)) {
    return false;
  }
  if (/picker/i.test(`${observation?.type || ""} ${field.widget || ""}`)) return true;
  return pickerEntity(field) === "user";
}

export function looksDirectoryPicker(field: Pick<InputFormField, "name" | "label" | "widget" | "path">) {
  const entity = pickerEntity(field);
  return entity === "dept" || entity === "role" || entity === "post";
}

export function preferRequestValueType(
  schemaType: InputFormField["valueType"] | undefined,
  uiType: InputFormField["valueType"] | undefined
): InputFormField["valueType"] {
  if (schemaType && schemaType !== "unknown" && schemaType !== "string") return schemaType;
  return uiType || schemaType || "unknown";
}

export function coerceCandidateValue(value: unknown, valueType: InputFormField["valueType"]) {
  if ((valueType === "integer" || valueType === "number") && typeof value === "string" && /^-?\d+(?:\.\d+)?$/.test(value.trim())) {
    return valueType === "integer" ? Number.parseInt(value, 10) : Number(value);
  }
  return value;
}

function looksQuantityObservation(item: UiObservation) {
  return /number/.test(item.type || "");
}

function actualPair(text: string) {
  return /actual|实际/i.test(text);
}

function scalarRequestValue(value: unknown) {
  return Array.isArray(value) && value.length === 1 ? value[0] : value;
}

const PROCESS_COMMENT_LABEL = /提交意见|审批意见|办理意见|审核意见/;
const PROCESS_COMMENT_FIELD = /^(reason|comment|opinion|approvalComment|form_item_reason)$|提交意见|审批意见|办理意见|审核意见/i;

function isProcessCommentObservation(item: Pick<UiObservation, "name" | "label">) {
  return PROCESS_COMMENT_LABEL.test(String(item.label || item.name || ""));
}

function hasProcessCommentField(fields: InputFormField[]) {
  return fields.some(field => PROCESS_COMMENT_FIELD.test(`${field.name} ${field.label || ""}`));
}

function observationCanJoinList(item: UiObservation) {
  if (isProcessCommentObservation(item)) return false;
  return !looksTextObservation(item) || looksChoiceObservation(item);
}

function observationCompatible(
  field: InputFormField,
  item: UiObservation,
  value: unknown,
  lists: RecordedList[]
) {
  value = scalarRequestValue(value);
  if (value === undefined || value === null || value === "") return false;
  if (isProcessCommentObservation(item) && !PROCESS_COMMENT_FIELD.test(`${field.name} ${field.label || ""}`)) {
    return false;
  }
  if (looksTextObservation(item) && looksPickerField(field) && !uiNameMatches(item.name, field.name)) {
    return false;
  }
  const choiceObs = looksChoiceObservation(item);
  const quantityField = looksQuantityField(field);
  const quantityObs = looksQuantityObservation(item);
  if (quantityField && choiceObs && !sameValue(item.value, value) && !dateDay(value)) return false;
  if (looksChoiceField(field) && quantityObs && !choiceObs) return false;
  if (quantityField && quantityObs && actualPair(fieldText(field)) !== actualPair(fieldText(item))) return false;
  if (quantityField && quantityObs && sameValue(item.value, value) && !sameSynonymGroup(field, item)) return false;
  if (!observationMatchesValue(item, value, lists)) return false;
  if (sameValue(item.value, value) || (dateDay(value) && dateDay(item.value) === dateDay(value))) return true;
  return sameSynonymGroup(field, item);
}

function looksReadonly(item: Pick<UiObservation, "type" | "disabled">) {
  return item.disabled === true || /readonly|disabled/i.test(item.type || "");
}


function looksIdentityToken(value?: string) {
  const text = String(value || "").trim();
  return text.length >= 10 && (/^\d+$/.test(text) || /^[0-9a-f]{8}-[0-9a-f-]{20,}$/i.test(text) || /^[0-9a-f]{24,}$/i.test(text));
}

export function collectUiObservations(events: UiEvidence[]): UiObservation[] {
  const items: UiObservation[] = [];
  for (const event of events) {
    const choiceEvent = eventLooksChoice(event);
    const eventOptions = choiceEvent ? optionsOf(event) : undefined;
    const label = eventLabel(event);
    const controlType = choiceEvent ? "select" : (event.inputType || event.role);
    const eventField = (event.form || []).find(field =>
      Boolean(event.name && field.name === event.name)
      || Boolean(label && field.label === label)
    );
    const actionControl = (event.eventType === "click" || event.eventType === "submit")
      && (event.tag === "button" || event.tag === "a" || event.role === "button" || event.role === "link" || event.inputType === "button" || event.inputType === "submit");
    if (looksIdentityToken(event.name) && (event.value === undefined || event.value === "")) {
      items.push({
        name: undefined,
        label: label && label !== event.name ? label : event.text,
        value: event.name,
        type: controlType || (choiceEvent ? "select" : undefined),
        disabled: eventField?.disabled,
        options: eventOptions
      });
      if (event.text && event.text !== event.name && event.text !== label) {
        items.push({ name: undefined, label: event.text, value: event.text, type: controlType || (choiceEvent ? "select" : undefined), disabled: eventField?.disabled });
      }
    } else if (event.name && !isGeneratedFieldName(event.name)) {
      items.push({ name: event.name, label: observationLabel({ name: event.name, label, value: event.value }), value: event.value, type: controlType, disabled: eventField?.disabled, options: eventOptions });
    } else if (label && !actionControl) {
      const picked = chooserRowDisplay(event, label);
      items.push({
        name: undefined,
        label: observationLabel({ label, value: picked ?? event.value }),
        value: picked ?? event.value,
        type: picked ? "select" : controlType,
        disabled: eventField?.disabled,
        options: eventOptions
      });
    } else if (eventOptions?.length) {
      const matches = (event.form || []).filter(field =>
        field.label && eventOptions.some(item => String(item.label || item.value).includes(field.label!))
      );
      if (matches.length === 1) {
        items.push({
          name: realFieldName(matches[0]!.name),
          label: matches[0]!.label,
          value: matches[0]!.value,
          type: matches[0]!.type,
          required: matches[0]!.required,
          disabled: matches[0]!.disabled,
          options: eventOptions,
          rangeIndex: matches[0]!.rangeIndex
        });
      }
    }
    for (const field of event.form || []) {
      if (field.label === "字段") continue;
      items.push({
        name: realFieldName(field.name),
        label: observationLabel(field),
        value: field.value,
        type: field.type,
        required: field.required,
        disabled: field.disabled,
        options: optionsOf(event, field),
        rangeIndex: field.rangeIndex
      });
    }
  }
  return items;
}

function mergeObservations(items: UiObservation[]) {
  if (!items.length) return undefined;
  const texts = items.filter(looksTextObservation);
  const choices = items.filter(looksChoiceObservation);
  const structured = items.filter(item => /^(?:date|datetime|daterange|time|number|checkbox|boolean)$/i.test(item.type || ""));
  const hasValue = (item: UiObservation) => item.value !== undefined && item.value !== "";
  const textHasEvidence = texts.some(hasValue);
  const choiceHasEvidence = choices.some(item => hasValue(item) || Boolean(item.options?.length));
  const structuredHasEvidence = structured.some(hasValue);
  const pool = texts.length && choices.length
    ? (choiceHasEvidence ? choices : textHasEvidence ? texts : choices)
    : texts.length && structured.length && structuredHasEvidence
      ? structured
    : items;
  const named = pool.find(item => item.name);
  return pool.reduce((best, item) => ({
    name: best.name || item.name,
    label: named?.label || best.label || item.label,
    value: best.value !== undefined && best.value !== "" ? best.value : item.value,
    type: looksTextObservation(best) || looksTextObservation(item)
      ? (looksTextObservation(best) ? best.type : item.type)
      : (best.type || item.type),
    required: best.required === true || item.required === true,
    disabled: best.disabled === false || item.disabled === false
      ? false
      : (best.disabled === true || item.disabled === true ? true : undefined),
    options: looksTextObservation(best) || looksTextObservation(item)
      ? undefined
      : ((item.options?.length || 0) > (best.options?.length || 0) ? item.options : best.options),
    rangeIndex: best.rangeIndex ?? item.rangeIndex
  }));
}

function rowIdentity(row: Record<string, unknown>) {
  for (const key of ["id", "value", "code", "key", "dictValue", "dictCode"]) {
    const value = row[key];
    if (value !== undefined && value !== null && value !== "") return value;
  }
  return undefined;
}

function rowDisplay(row: Record<string, unknown>) {
  for (const key of ["name", "label", "title", "dictLabel", "nickname", "text", "billCode", "documentNo", "applyNo", "orderNo", "xtmc", "yymc", "bmmc", "ssbmmc", "yyxtmc", "mc", "csmc"]) {
    const value = row[key];
    if (value !== undefined && value !== null && value !== "") return value;
  }
  return undefined;
}

function rowDisplayCandidates(row: Record<string, unknown>) {
  const values = new Set<string>();
  const primary = rowDisplay(row);
  if (primary !== undefined && primary !== null && primary !== "") values.add(String(primary));
  for (const key of ["billCode", "documentNo", "applyNo", "orderNo"]) {
    const value = row[key];
    if (value !== undefined && value !== null && value !== "") values.add(String(value));
  }
  const username = row.username ?? row.userName;
  const nickname = row.nickname;
  if (username !== undefined && username !== null && username !== "") values.add(String(username));
  if (nickname !== undefined && nickname !== null && nickname !== "") values.add(String(nickname));
  if (username && nickname) {
    values.add(`${username} ${nickname}`);
    values.add(`${nickname} ${username}`);
  }
  return [...values];
}

function listLabelFor(row: Record<string, unknown>, requestValue: unknown) {
  if (!sameValue(rowIdentity(row), requestValue)) return undefined;
  return rowDisplay(row);
}

function isJoinableList(rows: Record<string, unknown>[]) {
  const displayById = new Map<string, string>();
  for (const row of rows) {
    const id = rowIdentity(row);
    const display = rowDisplay(row);
    if (id === undefined || id === null || id === "" || display === undefined) continue;
    const key = String(id);
    const previous = displayById.get(key);
    if (previous !== undefined && previous !== String(display)) return false;
    displayById.set(key, String(display));
  }
  return displayById.size > 0;
}

function listSignature(list: RecordedList) {
  return list.rows
    .map(row => `${String(rowIdentity(row) ?? "")}:${String(rowDisplay(row) ?? "")}`)
    .sort()
    .join("|");
}

function uniqueLists(lists: RecordedList[]) {
  return [...new Map(lists.map(list => [listSignature(list), list])).values()];
}

function namesOf(list: RecordedList) {
  return new Set(list.rows.flatMap(row => rowDisplayCandidates(row)));
}

function listsMatchingIdentity(lists: RecordedList[], display: string, requestValue: unknown) {
  if (requestValue === undefined || requestValue === null || requestValue === "") return [];
  return lists.filter(list => list.rows.some(row =>
    sameValue(rowIdentity(row), requestValue) && rowDisplayCandidates(row).some(item => sameValue(item, display))
  ));
}

function listForObservation(item: UiObservation, lists: RecordedList[], requestValue?: unknown) {
  const optionLabels = (item.options || []).map(option => String(option.label || "")).filter(Boolean);
  const display = item.value === undefined || item.value === "" ? undefined : String(item.value);
  const named = uniqueLists(lists);
  if (optionLabels.length >= 2) {
    const byOptions = named.filter(list => optionLabels.every(label => namesOf(list).has(label)));
    if (byOptions.length === 1) return byOptions[0];
    if (display && byOptions.length > 1) {
      const byValue = listsMatchingIdentity(byOptions, display, requestValue);
      if (byValue.length) return byValue[0];
    }
  }
  if (display) {
    const byDisplay = named.filter(list => namesOf(list).has(display));
    if (byDisplay.length === 1) return byDisplay[0];
    const byValue = listsMatchingIdentity(byDisplay, display, requestValue);
    if (byValue.length) return byValue[0];
  }
  return undefined;
}

function splitTypedRows(rows: Record<string, unknown>[]) {
  if (!rows.length || rows.some(row => row.dictType === undefined || row.dictType === null || row.dictType === "")) {
    return isJoinableList(rows) ? [rows] : [];
  }
  const groups = new Map<string, Record<string, unknown>[]>();
  for (const row of rows) {
    const key = String(row.dictType);
    const list = groups.get(key) || [];
    list.push(row);
    groups.set(key, list);
  }
  return [...groups.values()].filter(isJoinableList);
}

function arraysFromBody(body: unknown): unknown[][] {
  if (!body || typeof body !== "object") return [];
  const obj = body as Record<string, unknown>;
  const data = obj.data;
  const nested = data && typeof data === "object" && !Array.isArray(data) ? data as Record<string, unknown> : undefined;
  return [data, nested?.list, nested?.rows, nested?.records, obj.list, obj.rows, obj.records, obj.result, body]
    .filter((item): item is unknown[] => Array.isArray(item) && item.some(entry => entry && typeof entry === "object" && !Array.isArray(entry)));
}

export function recordedLists(events: EvidenceEvent[]): RecordedList[] {
  const lists: RecordedList[] = [];
  for (const event of events) {
    const body = event.kind === "network" ? event.response?.body : undefined;
    if (!body || typeof body !== "object") continue;
    for (const raw of arraysFromBody(body)) {
      const rows = raw.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item));
      for (const group of splitTypedRows(rows)) lists.push({ rows: group });
    }
  }
  return lists;
}

function requestValueIsShared(sample: unknown, fieldPath: string, value: unknown) {
  if (value === undefined || value === null || value === "") return false;
  return flattenRequestValues(sample).some(item =>
    item.path !== fieldPath
    && !PAGE_NAME.test(item.name)
    && sameValue(item.value, value)
  );
}

export function findObservation(
  field: InputFormField,
  requestValue: unknown,
  observations: UiObservation[],
  lists: RecordedList[] = [],
  sample?: unknown
) {
  const byName = observations.filter(item => uiNameMatches(item.name, field.name));
  if (byName.length) {
    const seedLabel = byName.find(item => item.label)?.label || field.label;
    const related = observations.filter(item => {
      if (uiNameMatches(item.name, field.name)) return true;
      if (!item.label || !seedLabel) return false;
      if (item.label !== seedLabel && item.label !== field.label && !labelsEquivalent(item.label, seedLabel) && !labelsEquivalent(item.label, field.label)) {
        return false;
      }
      return !item.name || uiNameMatches(item.name, field.name);
    });
    const direct = mergeObservations(related.length ? related : byName);
    if (direct) return expandObservation(direct, observations);
  }

  if (field.label && field.label !== field.name) {
    const related = observations.filter(item =>
      item.label === field.label
      || item.label === field.name
      || labelsEquivalent(item.label, field.label)
    );
    const names = new Set(related.map(item => item.name).filter((name): name is string => Boolean(name)));
    const scoped = field.name && names.size > 1
      ? related.filter(item => !item.name || uiNameMatches(item.name, field.name))
      : related;
    const direct = mergeObservations(scoped);
    if (direct) return expandObservation(direct, observations);
  }

  if (isDistinctiveValue(requestValue) || ASK_KEY.test(field.name || "")) {
    const exact = observations.filter(item => sameValue(item.value, requestValue));
    const preferred = preferSpecificObservation(field, exact);
    if (preferred) return expandObservation(preferred, observations);
    if (exact.length === 1) return expandObservation(exact[0], observations);
    const labels = new Set(exact.map(item => item.label).filter(Boolean));
    if (exact.length > 1 && labels.size === 1) return expandObservation(exact[0], observations);
  }

  if (requestValue !== undefined && requestValue !== null && requestValue !== "" && !requestValueIsShared(sample, field.path, requestValue)) {
    const hits = observations.filter(item => {
      if (!observationCanJoinList(item)) return false;
      const list = listForObservation(item, lists, requestValue);
      return Boolean(list && list.rows.some(row =>
        sameValue(rowIdentity(row), requestValue) && rowDisplayCandidates(row).some(display => sameValue(display, item.value))
      ));
    });
    const unique = [...new Map(hits.map(item => [item.label || item.name || "", item])).values()];
    if (unique.length === 1) return expandObservation(unique[0], observations);
  }

  const day = dateDay(requestValue);
  if (day) {
    const byDay = observations.filter(item => dateDay(item.value) === day);
    if (byDay.length === 1) return expandObservation(byDay[0], observations);
    const dayLabels = new Set(byDay.map(item => item.label).filter(Boolean));
    if (byDay.length > 1 && dayLabels.size === 1) return expandObservation(byDay[0], observations);
  }

  return undefined;
}

export function fieldHasUiEvidence(field: InputFormField, events: UiEvidence[]) {
  const observations = collectUiObservations(events);
  if (findObservation(field, undefined, observations)) return true;
  if (field.valueType !== "array" || field.widget !== "date") return false;
  const labels = field.label.split(/\s*(?:\/|、|至)\s*/).filter(Boolean);
  return labels.length >= 2 && labels.every(label =>
    observations.some(item => looksDateControl(item) && labelsEquivalent(item.label, label))
  );
}

export function collectionRowHasUiEvidence(
  field: InputFormField,
  fields: InputFormField[],
  events: UiEvidence[]
) {
  const parsed = parseCollectionLeafPath(field.path);
  if (!parsed || parsed.index === "*") return false;
  return fields.some(other => {
    if (other.path === field.path || other.source !== "caller") return false;
    const sibling = parseCollectionLeafPath(other.path);
    return Boolean(
      sibling
      && sibling.prefix === parsed.prefix
      && sibling.index === parsed.index
      && fieldHasUiEvidence(other, events)
    );
  });
}

export function staticCandidatesHaveUiEvidence(field: InputFormField, events: UiEvidence[]) {
  if (field.candidates?.type !== "static") return true;
  const labels = new Set(field.candidates.values.map(item => String(item.label)));
  return collectUiObservations(events).some(item => {
    const same = uiNameMatches(item.name, field.name)
      || Boolean(item.label) && (item.label === field.label || item.label === field.name || labelsEquivalent(item.label, field.label));
    if (!same) return false;
    if (item.options?.some(option => labels.has(String(option.label)))) return true;
    return item.value !== undefined && item.value !== "" && labels.has(String(item.value));
  });
}

export function isEditableBusinessField(field: Pick<InputFormField, "name" | "label">, events: UiEvidence[] = []) {
  if (!events.length) return false;
  return Boolean(findObservation(field as InputFormField, undefined, collectUiObservations(events)));
}

function literalRule(value: unknown) {
  if (value === undefined || value === null || value === "") return undefined;
  if (typeof value === "string") return `literal:${value}`;
  try {
    return `literal:${JSON.stringify(value)}`;
  } catch {
    return undefined;
  }
}

function formatHint(field: InputFormField, requestValue?: unknown) {
  if (/time|date|start|end/i.test(`${field.name} ${field.label}`) || field.widget === "date") {
    const clock = recordedClock(requestValue);
    if (typeof requestValue === "number") {
      return `，页面按 YYYY-MM-DD 填写，执行器转成当天 ${clock || "00:00:00"} 的毫秒时间戳`;
    }
    if (clock) {
      return `，页面按 YYYY-MM-DD 填写，请求使用 YYYY-MM-DD ${clock}`;
    }
    return "，保持页面原始日期格式";
  }
  if (field.valueType === "number" || field.valueType === "integer") return "，保持页面数字格式";
  return "，保持页面原始输入格式";
}

function finalizeUnhandled(field: InputFormField): InputFormField {
  return {
    ...field,
    source: "system",
    systemHandled: true,
    required: false,
    defaultRule: undefined,
    sourceDetail: "请求中出现但未能唯一对应到页面控件；分析阶段将优先核对因果来源，无来源时由系统原样补齐成功请求值"
  };
}

function widgetFromObservation(field: InputFormField, matched?: UiObservation): InputFormField["widget"] {
  const type = `${matched?.type || ""}`.toLowerCase();
  if (/textarea/.test(type)) return "textarea";
  if (matched && looksDateControl({ ...field, type: matched.type, label: matched.label || field.label, name: matched.name || field.name })) {
    return "date";
  }
  if (/select|combobox|picker/.test(type) && !looksTextObservation(matched || {})) return "select";
  if (matched?.options?.length && !looksTextObservation(matched) && !looksDateControl(matched || field)) return "select";
  if (/number/.test(type)) return "number";
  if (/checkbox|switch|boolean/.test(type)) return "boolean";
  if (/^(text|input|search|email|tel|url|password)$/.test(type)) return "text";
  return field.widget;
}

function displayLabel(label?: string, rangeIndex?: number) {
  if (!label) return label;
  if (/^(开始|结束)(日期|时间)-\d+$/.test(label) || /^(start|end)[- ]?(date|time)-\d+$/i.test(label)) {
    return label.replace(/-\d+$/, "");
  }
  if (rangeIndex === undefined) return label;
  const stripped = label.replace(/-(\d+)$/, "");
  if (stripped !== label && /开始|结束|start|end/i.test(stripped)) return stripped;
  return label;
}

function observedLabel(field: InputFormField, matched?: UiObservation) {
  const refined = matched ? observationLabel(matched) : undefined;
  const fromMatch = displayLabel(refined || matched?.label, matched?.rangeIndex);
  if (fromMatch && isPromptLabel(fromMatch)) {
    if (field.label && !isPromptLabel(field.label) && field.label !== field.name) return field.label;
    return emptyPromptLabel(fromMatch) || fromMatch;
  }
  if (fromMatch && /^\d+$/.test(fromMatch)) {
    const prompt = emptyPromptLabel(typeof matched?.value === "string" ? matched.value : "");
    if (prompt) return prompt;
  }
  if (fromMatch && fromMatch !== field.name) return fromMatch;
  if (field.label && isPromptLabel(field.label)) return emptyPromptLabel(field.label) || field.label;
  if (field.label && field.label !== field.name) return field.label;
  return field.name;
}

function staticFromList(list: RecordedList | undefined): Array<{ value: unknown; label: string }> | undefined {
  if (!list || list.rows.length < 2 || list.rows.length > 40) return undefined;
  const values = list.rows
    .map<{ value: unknown; label: string } | undefined>(row => {
      const label = rowDisplay(row);
      const value = rowIdentity(row);
      if (label === undefined || value === undefined || value === null || value === "") return undefined;
      return { value, label: String(label) };
    })
    .filter((item): item is { value: unknown; label: string } => item !== undefined);
  return values.length >= 2 ? values : undefined;
}

function asCaller(
  field: InputFormField,
  matched: UiObservation | undefined,
  requestValue: unknown,
  observations: UiObservation[] = [],
  lists: RecordedList[] = []
): InputFormField {
  const selected = matched?.value
    || observations.find(item => item.label === matched?.label && item.value !== undefined && item.value !== "")?.value;
  const looksText = /text|textarea|search|date|number|tel|email/i.test(`${matched?.type || ""}`)
    && !/select|combobox|picker/i.test(`${matched?.type || ""}`);
  const quantityObs = looksQuantityObservation(matched || {});
  const picker = looksPickerField(field, matched);
  const useStatic = Boolean(matched?.options?.length)
    && !looksText
    && !looksDateControl(matched || field)
    && !quantityObs
    && !picker;
  const options = looksText || looksDateControl(matched || field) || quantityObs || picker
    ? undefined
    : useStatic
      ? matched!.options!.map(item =>
        selected !== undefined && String(item.label) === String(selected) && requestValue !== undefined && requestValue !== selected
          ? { value: coerceCandidateValue(requestValue, field.valueType), label: String(item.label) }
          : { value: coerceCandidateValue(item.value, field.valueType), label: String(item.label || item.value) }
      )
      : staticFromList(matched ? listForObservation(matched, lists, requestValue) : undefined);
  const clock = recordedClock(requestValue);
  const choiceWidget = field.valueType === "array" ? "multiselect" : "select";
  const widget = looksDateControl(matched || field)
    ? "date"
    : looksText
      ? widgetFromObservation(field, { ...matched, options: undefined })
      : options?.length || picker || /select|combobox|picker/i.test(`${matched?.type || ""}`)
        ? choiceWidget
        : widgetFromObservation(field, matched);
  const dateHasTime = widget === "date" && (
    /datetime|time/.test(String(matched?.type || "").toLowerCase())
    || (typeof matched?.value === "string" && /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}/.test(matched.value.trim()))
  );
  return {
    ...field,
    label: observedLabel(field, matched),
    source: "caller",
    required: matched?.required === true,
    requiredBasis: matched?.required === true ? "ui-required" : "not-observed",
    systemHandled: false,
    widget,
    candidates: options?.length ? { type: "static", values: options } : field.candidates,
    dateFormat: widget === "date"
      ? recordedDateFormat(matched?.value, dateHasTime) || recordedDateFormat(requestValue) || field.dateFormat
      : undefined,
    dateClock: widget === "date" ? (dateHasTime ? undefined : clock || field.dateClock) : field.dateClock,
    sourceDetail: options?.length
      ? "页面固定枚举，调用方直接选择，不要写成录制时的固定样本"
      : picker
        ? "调用方从已录制查询接口选择，不要把弹窗当前页冻成页面枚举"
        : `调用方按页面原始格式提供（${field.valueType}）${formatHint(field, requestValue)}，不要改成录制样本`
  };
}

function asReadonly(field: InputFormField, matched: UiObservation | undefined): InputFormField {
  return {
    ...field,
    label: observedLabel(field, matched),
    source: "system",
    systemHandled: true,
    required: false,
    defaultRule: undefined,
    sourceDetail: "页面只读展示，由选择其它字段后自动带出。调用方不要手填"
  };
}

export function resolveFieldOwnership(
  field: InputFormField,
  requestValue: unknown,
  observations: UiObservation[],
  lists: RecordedList[] = [],
  sample?: unknown
): InputFormField {
  void requestValue;
  void observations;
  void lists;
  void sample;
  return field;
}

function expandObservation(hit: UiObservation | undefined, observations: UiObservation[]) {
  if (!hit?.label) return hit;
  const same = observations.filter(item => item.label === hit.label);
  const names = new Set(same.map(item => item.name).filter((name): name is string => Boolean(name)));
  let scoped = same;
  if (hit.name && names.size > 1) {
    scoped = same.filter(item => !item.name || item.name === hit.name);
  }
  const values = scoped
    .map(item => item.value)
    .filter(value => value !== undefined && value !== "");
  if (values.length > 1 && hit.value !== undefined && hit.value !== "") {
    scoped = scoped.filter(item => item.value === undefined || item.value === "" || sameValue(item.value, hit.value));
  }
  return mergeObservations(scoped) || hit;
}

function uniqueByLabel(items: UiObservation[]) {
  const merged = new Map<string, UiObservation>();
  for (const item of items) {
    if (!item.label) continue;
    const previous = merged.get(item.label);
    merged.set(item.label, previous ? mergeObservations([previous, item])! : item);
  }
  return [...merged.values()];
}

function observationMatchesValue(item: UiObservation, value: unknown, lists: RecordedList[]) {
  if (sameValue(item.value, value)) return true;
  const day = dateDay(value);
  if (day && dateDay(item.value) === day) return true;
  if (!observationCanJoinList(item)) return false;
  const list = listForObservation(item, lists, value);
  return Boolean(list && list.rows.some(row =>
    sameValue(rowIdentity(row), value)
    && (item.value === undefined || item.value === "" || rowDisplayCandidates(row).some(display => sameValue(display, item.value)))
  ));
}

function isReadonlyBound(field: InputFormField) {
  return field.source === "system" && /只读展示/.test(field.sourceDetail || "");
}

function leftoverCallerLabels(fields: InputFormField[]) {
  return new Set(fields.filter(field => field.source === "caller").map(field => field.label));
}

function leftoverEditable(fields: InputFormField[], observations: UiObservation[]) {
  const callerLabels = leftoverCallerLabels(fields);
  const keepComments = hasProcessCommentField(fields);
  return uniqueByLabel(observations.filter(item =>
    item.label
    && !callerLabels.has(item.label)
    && !looksReadonly(item)
    && (keepComments || !isProcessCommentObservation(item))
  ));
}

function bindObservation(
  field: InputFormField,
  matched: UiObservation,
  requestValue: unknown,
  observations: UiObservation[],
  lists: RecordedList[]
) {
  const full = expandObservation(matched, observations) || matched;
  return looksReadonly(full)
    ? asReadonly(field, full)
    : asCaller(field, full, requestValue, observations, lists);
}

export function bindBySemanticLabel(
  fields: InputFormField[],
  observations: UiObservation[],
  sample: unknown,
  lists: RecordedList[] = []
): InputFormField[] {
  void observations;
  void sample;
  void lists;
  return fields;
}

function nameIndex(name: string) {
  const match = name.match(/\[(\d+)\]$/);
  return match ? Number(match[1]) : undefined;
}

function isDateSlot(field: Pick<UiObservation, "type" | "label" | "name">) {
  if (/date|time|picker/i.test(field.type || "")) return true;
  if (/select|combobox|number|readonly|checkbox|textarea/i.test(field.type || "")) return false;
  return /时间|日期/.test(`${field.label || ""} ${field.name || ""}`);
}

function adjacentDateSlots(owner?: UiEvidence) {
  if (!owner?.form?.length) return [];
  const slots: UiObservation[][] = [];
  let current: UiObservation[] = [];
  for (const field of owner.form) {
    if (field.label && isDateSlot(field)) {
      current.push({
        name: field.name,
        label: field.label,
        value: field.value,
        type: field.type,
        required: field.required,
        disabled: field.disabled,
        options: field.options,
        rangeIndex: field.rangeIndex
      });
    } else if (current.length) {
      if (current.length >= 2) slots.push(current);
      current = [];
    }
  }
  if (current.length >= 2) slots.push(current);
  return slots;
}

function bindRepeatedDateRange(
  fields: InputFormField[],
  owner: UiEvidence | undefined,
  observations: UiObservation[],
  sample: unknown,
  lists: RecordedList[]
) {
  const bound = new Map<string, InputFormField>();
  for (const field of fields) {
    if (field.valueType !== "array" || PAGE_NAME.test(field.name) || isReadonlyBound(field)) continue;
    const values = requestValueAt(sample, field.path);
    if (!Array.isArray(values) || values.length < 2 || !values.every(value => Boolean(dateDay(value)))) continue;

    // Some clients serialize a date range as repeated query keys
    // (`createTime=a&createTime=b`) instead of indexed keys. The request then
    // has one array field while the page still has two real date controls.
    // Bind the array only when one adjacent control group matches every
    // recorded request value in order. This is causal evidence, not a name or
    // equal-value guess, and therefore also works with generated control ids.
    const slots = adjacentDateSlots(owner).find(group =>
      group.length === values.length
      && group.every((slot, index) => observationMatchesValue(slot, values[index], lists))
    );
    if (!slots) continue;
    const labels = slots.map(slot => displayLabel(slot.label, slot.rangeIndex)).filter((label): label is string => Boolean(label));
    if (new Set(labels).size !== values.length) continue;
    const clocks = values.map(value => recordedClock(value)).filter((clock): clock is string => Boolean(clock));
    bound.set(field.path, {
      ...field,
      label: labels.join(" / "),
      source: "caller",
      required: slots.some(slot => slot.required === true),
      requiredBasis: slots.some(slot => slot.required === true) ? "ui-required" : "not-observed",
      systemHandled: false,
      widget: "date",
      candidates: undefined,
      defaultRule: undefined,
      dateClock: undefined,
      dateClocks: clocks.length === values.length ? clocks : undefined,
      sourceDetail: `页面由「${labels.join("、")}」${values.length}个日期控件组成；调用方按页面顺序提供 ${field.valueType}${clocks.length === values.length ? `，请求时间分别使用 ${clocks.join("、")}` : ""}，不要写成录制样本`
    });
  }
  if (!bound.size) return fields;
  return fields.map(field => bound.get(field.path) || field);
}

function bindIndexedDateRange(
  fields: InputFormField[],
  owner: UiEvidence | undefined,
  observations: UiObservation[],
  sample: unknown,
  lists: RecordedList[],
  includeCaller = false
) {
  const indexed = fields.filter(field =>
    !PAGE_NAME.test(field.name)
    && !isReadonlyBound(field)
    && looksDateControl(field)
    && nameIndex(field.name) !== undefined
  );
  if (indexed.length < 2) return fields;
  const byStem = new Map<string, InputFormField[]>();
  for (const field of indexed) {
    const stem = field.name.replace(/\[\d+\]$/, "");
    byStem.set(stem, [...(byStem.get(stem) || []), field]);
  }
  const bound = new Map<string, InputFormField>();
  for (const group of byStem.values()) {
    if (group.length < 2) continue;
    const sorted = [...group].sort((left, right) => (nameIndex(left.name) ?? 0) - (nameIndex(right.name) ?? 0));
    const byRange = observations.filter(item =>
      item.rangeIndex !== undefined && item.label && isDateSlot(item) && !looksReadonly(item)
    );
    const rangeSlots = new Map<number, UiObservation>();
    for (const item of byRange) {
      if (!rangeSlots.has(item.rangeIndex!)) rangeSlots.set(item.rangeIndex!, expandObservation(item, observations) || item);
    }
    const slots = rangeSlots.size === sorted.length
      ? sorted.map(field => rangeSlots.get(nameIndex(field.name)!))
      : adjacentDateSlots(owner).find(item => item.length === sorted.length);
    if (!slots || slots.some(item => !item)) continue;
    sorted.forEach((field, index) => {
      const eligible = includeCaller
        ? field.source === "caller" && field.label === field.name
        : field.source !== "caller";
      if (!eligible) return;
      bound.set(field.path, asCaller(field, slots[index]!, requestValueAt(sample, field.path), observations, lists));
    });
  }
  if (!bound.size) return fields;
  return fields.map(field => bound.get(field.path) || field);
}

export function bindLeftoverFields(
  fields: InputFormField[],
  observations: UiObservation[],
  sample: unknown,
  lists: RecordedList[] = [],
  owner?: UiEvidence
): InputFormField[] {
  void observations;
  void sample;
  void lists;
  void owner;
  return fields;
}

export function assignUniqueRemaining(
  fields: InputFormField[],
  observations: UiObservation[],
  sample: unknown,
  lists: RecordedList[] = []
): InputFormField[] {
  void observations;
  void sample;
  void lists;
  return fields;
}

export function assignUniqueFromSamples(
  fields: InputFormField[],
  observations: UiObservation[],
  samples: unknown[],
  lists: RecordedList[] = []
) {
  void observations;
  void samples;
  void lists;
  return fields;
}

function uniqueAssignment<TField, TObs>(
  fields: TField[],
  observations: TObs[],
  compatible: (field: TField, observation: TObs) => boolean
) {
  const matches: number[][] = [];
  const walk = (index: number, used: Set<number>, current: number[]) => {
    if (index === fields.length) {
      matches.push([...current]);
      return;
    }
    for (let obsIndex = 0; obsIndex < observations.length; obsIndex++) {
      if (used.has(obsIndex) || !compatible(fields[index]!, observations[obsIndex]!)) continue;
      used.add(obsIndex);
      current.push(obsIndex);
      walk(index + 1, used, current);
      current.pop();
      used.delete(obsIndex);
      if (matches.length > 1) return;
    }
  };
  walk(0, new Set(), []);
  return matches.length === 1 ? matches[0] : undefined;
}

export function bindByUniqueMatching(
  fields: InputFormField[],
  observations: UiObservation[],
  sample: unknown,
  lists: RecordedList[] = []
): InputFormField[] {
  void observations;
  void sample;
  void lists;
  return fields;
}

function unlabeledCallers(fields: InputFormField[]) {
  return fields.filter(field => field.source === "caller" && field.label === field.name && !PAGE_NAME.test(field.name));
}

function bindUnlabeledCallers(
  fields: InputFormField[],
  observations: UiObservation[],
  sample: unknown,
  lists: RecordedList[]
) {
  const unlabeled = unlabeledCallers(fields);
  const leftoverObs = leftoverEditable(fields, observations);
  if (leftoverObs.length === 1 && unlabeled.length === 1) {
    const field = unlabeled[0]!;
    const observation = leftoverObs[0]!;
    const value = requestValueAt(sample, field.path);
    if (!observationCompatible(field, observation, value, lists)) return fields;
    return fields.map(item =>
      item.path === field.path
        ? asCaller(item, observation, value, observations, lists)
        : item
    );
  }
  const dateObs = leftoverObs.filter(looksDateControl);
  const dateFields = unlabeled.filter(looksDateControl);
  const dateLabels = new Set(dateObs.map(item => item.label));
  if (dateObs.length && dateFields.length && dateLabels.size === 1) {
    return fields.map(field =>
      dateFields.some(item => item.path === field.path)
        ? asCaller(field, dateObs[0], requestValueAt(sample, field.path), observations, lists)
        : field
    );
  }
  return fields;
}

function leftoverForField(
  field: InputFormField,
  leftover: UiObservation[],
  sample: unknown,
  lists: RecordedList[]
) {
  if (field.widget === "select" && field.valueType !== "number" && field.valueType !== "integer") {
    const selects = leftover.filter(item => /select|combobox|picker/.test(item.type || ""));
    if (selects.length) return selects;
  }
  const value = requestValueAt(sample, field.path);
  const matching = leftover.filter(item => observationMatchesValue(item, value, lists));
  return matching.length ? matching : leftover;
}

function attachUnresolvedHints(
  fields: InputFormField[],
  observations: UiObservation[],
  sample: unknown,
  lists: RecordedList[]
) {
  const leftover = leftoverEditable(fields, observations);
  if (!leftover.length) return fields;
  return fields.map(field => {
    if (field.source !== "caller" || field.label !== field.name || PAGE_NAME.test(field.name)) return field;
    if (/页面未唯一对应/.test(field.sourceDetail || "")) return field;
    const hints = leftoverForField(field, leftover, sample, lists);
    if (!hints.length) return field;
    const names = hints.map(item => `${item.label}${item.required ? "（必填）" : ""}`).join("、");
    return {
      ...field,
      sourceDetail: `${field.sourceDetail}。页面未唯一对应：${names}`
    };
  });
}

function enrichFromObservations(
  field: InputFormField,
  observations: UiObservation[],
  sample: unknown,
  lists: RecordedList[]
) {
  if (field.source !== "caller" || field.label === field.name) return field;
  const matched = expandObservation({ label: field.label }, observations);
  if (!matched) return field;
  const next = asCaller(field, matched, requestValueAt(sample, field.path), observations, lists);
  return {
    ...next,
    candidates: field.candidates?.type === "capability" ? field.candidates : next.candidates || field.candidates,
    sourceDetail: field.candidates?.type === "capability" ? field.sourceDetail : next.sourceDetail
  };
}

function looksInvariantConstant(field: InputFormField, value: unknown) {
  if (Array.isArray(value) && value.length === 0 && /attachment|file/i.test(field.name)) return true;
  if (typeof value !== "string" || /^[a-f0-9]{16,}$/i.test(value)) return false;
  return /^[a-z][a-z0-9_]*$/i.test(value) && /Type|Key|Code|Def/i.test(field.name);
}

function leftoverExplainsValue(
  field: InputFormField,
  value: unknown,
  observations: UiObservation[],
  fields: InputFormField[]
) {
  if (value === undefined || value === null || value === "") return false;
  return leftoverEditable(fields, observations).some(item =>
    uiNameMatches(item.name, field.name)
    || item.options?.some(option => sameValue(option.value, value) || sameValue(option.label, value))
  );
}

function applyInvariantDefaults(
  fields: InputFormField[],
  sample: unknown,
  observations: UiObservation[] = []
): InputFormField[] {
  return fields.map(field => {
    if (field.source === "caller" || field.defaultRule) return field;
    const value = requestValueAt(sample, field.path);
    if (!looksInvariantConstant(field, value)) return field;
    if (leftoverExplainsValue(field, value, observations, fields)) return field;
    return {
      ...field,
      defaultRule: literalRule(value),
      sourceDetail: "请求中观察到的系统常量，由系统按该值补齐，调用方不要手填"
    };
  });
}

function pollutedLabel(item: UiObservation) {
  const label = String(item.label || "");
  const value = item.value === undefined || item.value === null ? "" : String(item.value);
  return !label || label === "字段" || (value && label === value);
}

export function bindByLabelAffinity(
  fields: InputFormField[],
  observations: UiObservation[],
  sample: unknown,
  lists: RecordedList[] = []
): InputFormField[] {
  void observations;
  void sample;
  void lists;
  return fields;
}

export function bindByRecordedOptions(
  fields: InputFormField[],
  observations: UiObservation[],
  sample: unknown,
  lists: RecordedList[] = []
): InputFormField[] {
  void observations;
  void sample;
  void lists;
  return fields;
}

const QUALIFIER_LABEL: Record<string, string> = {
  code: "编码",
  name: "名称",
  type: "类型",
  status: "状态",
  key: "键",
  time: "时间",
  date: "日期"
};

function lastNameToken(name: string) {
  const parts = String(name || "")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/[^A-Za-z0-9\u4e00-\u9fff]+/g, " ")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  return parts.at(-1) || "";
}

function qualifySharedLabel(shared: string, field: InputFormField, siblings: InputFormField[]) {
  const token = lastNameToken(field.name).toLowerCase();
  if (!token) return undefined;
  const others = siblings.filter(item => item.path !== field.path).map(item => lastNameToken(item.name).toLowerCase());
  if (!others.length || others.includes(token)) return undefined;
  const zh = QUALIFIER_LABEL[token];
  if (!zh) return `${shared}（${lastNameToken(field.name)}）`;
  if (shared.includes(zh)) return shared;
  const stem = shared.replace(/^(所属|相关|对应)/, "").replace(/(信息|资料)$/, "");
  if (stem && stem !== shared) return `${stem}${zh}`;
  return `${shared}${zh}`;
}

function chooserIdentityAndDisplay(group: InputFormField[], sample: unknown, lists: RecordedList[]) {
  for (const list of lists) {
    for (const row of list.rows) {
      const identity = rowIdentity(row);
      const displays = rowDisplayCandidates(row);
      const idField = group.find(field => sameValue(requestValueAt(sample, field.path), identity));
      const displayField = group.find(field => displays.some(display => sameValue(requestValueAt(sample, field.path), display)));
      if (idField && displayField && idField.path !== displayField.path) return { idField, displayField };
    }
  }
  return undefined;
}

function promoteChooserIdentities(
  fields: InputFormField[],
  sample: unknown,
  lists: RecordedList[]
): InputFormField[] {
  if (!lists.length) return fields;
  return fields.map(field => {
    if (field.source === "caller" || PAGE_NAME.test(field.name) || isReadonlyBound(field)) return field;
    const value = requestValueAt(sample, field.path);
    if (value === undefined || value === null || value === "") return field;
    const display = fields.find(other =>
      other.path !== field.path
      && other.source === "caller"
      && lists.some(list => list.rows.some(row =>
        sameValue(rowIdentity(row), value)
        && rowDisplayCandidates(row).some(item => sameValue(item, requestValueAt(sample, other.path)))
      ))
    );
    if (!display) return field;
    return asCaller(field, {
      name: field.name,
      label: display.label,
      value,
      type: "select"
    }, value, [], lists);
  });
}

function asSharedCompanion(field: InputFormField, label: string, detail: string, defaultRule?: string): InputFormField {
  return {
    ...field,
    source: "system",
    systemHandled: true,
    required: false,
    requiredBasis: "not-observed",
    defaultRule,
    candidates: undefined,
    widget: field.widget === "date" ? "text" : field.widget,
    sourceDetail: detail.replace("{label}", label)
  };
}

function sharedLabelKey(label: string) {
  return String(label).replace(/^\*+/, "").replace(/[：:]\s*$/, "").trim();
}

function refineSharedCallerLabels(
  fields: InputFormField[],
  observations: UiObservation[],
  sample: unknown,
  lists: RecordedList[] = []
): InputFormField[] {
  const callers = fields.filter(field => field.source === "caller" && field.label);
  const groups = new Map<string, InputFormField[]>();
  for (const field of callers) {
    const key = sharedLabelKey(field.label!);
    groups.set(key, [...(groups.get(key) || []), field]);
  }
  const relabel = new Map<string, string>();
  const derivedCompanions = new Map<string, InputFormField>();
  for (const [label, group] of groups) {
    if (group.length < 2) continue;
    const used = new Set<string>();
    for (const field of group) {
      const value = requestValueAt(sample, field.path);
      const better = observations.filter(item => {
        if (!item.label || sharedLabelKey(item.label) === label || used.has(item.label)) return false;
        const specific = emptyPromptLabel(item.label);
        const matchesValue = value !== undefined && value !== null && value !== ""
          && (sameValue(item.value, value) || Boolean(dateDay(value) && dateDay(item.value) === dateDay(value)));
        const matchesName = uiNameMatches(item.name, field.name)
          || Boolean(specific && sameSynonymGroup(field, { name: specific, label: specific }));
        return (matchesValue || matchesName) && (isPromptLabel(item.label) || matchesName);
      });
      const unique = [...new Map(better.map(item => [item.label || "", item])).values()];
      if (unique.length === 1) {
        relabel.set(field.path, observedLabel(field, unique[0]));
        if (unique[0]!.label) used.add(unique[0]!.label);
      }
    }
    const still = group.filter(field => sharedLabelKey(relabel.get(field.path) || field.label || "") === label);
    if (still.length > 1) {
      const dateOnly = still.filter(field => isDateOnly(requestValueAt(sample, field.path)));
      const instants = still.filter(field => isIsoInstant(requestValueAt(sample, field.path)));
      if (dateOnly.length && instants.length) {
        for (const field of instants) {
          derivedCompanions.set(field.path, asSharedCompanion(
            field,
            label,
            "页面只有日期控件；该时间戳由系统按提交时刻补齐，调用方不要手填",
            "now:iso"
          ));
        }
      }
      const chooser = chooserIdentityAndDisplay(still.filter(field => !derivedCompanions.has(field.path)), sample, lists);
      if (chooser) {
        derivedCompanions.set(chooser.displayField.path, asSharedCompanion(
          chooser.displayField,
          label,
          "页面与「{label}」共用一次行选择；该展示字段由已选行自动带出，调用方不要重复输入"
        ));
      }
      const leftover = still.filter(field => !derivedCompanions.has(field.path));
      const explicitlyNamed = leftover.filter(field => observations.some(item => item.name && uiNameMatches(item.name, field.name)));
      const candidateBacked = leftover.filter(field => Boolean(field.candidates));
      const stableIds = leftover.filter(field => /(?:^|[._\[])id(?:\]|$)|id$/i.test(field.name));
      const canonical = explicitlyNamed.length === 1
        ? explicitlyNamed[0]
        : candidateBacked.length === 1
          ? candidateBacked[0]
          : stableIds.length === 1
            ? stableIds[0]
            : undefined;
      for (const field of leftover) {
        if (field === canonical) continue;
        const candidates = canonical?.candidates?.type === "static" ? canonical.candidates.values : [];
        const canonicalValue = canonical ? requestValueAt(sample, canonical.path) : undefined;
        const fieldValue = requestValueAt(sample, field.path);
        const selected = candidates.find(option => sameValue(option.value, canonicalValue));
        if (selected && sameValue(selected.label, fieldValue)) {
          derivedCompanions.set(field.path, asSharedCompanion(
            field,
            label,
            "页面与「{label}」共用一个选择器；该伴随字段由已选项自动带出，调用方不要重复输入"
          ));
          continue;
        }
        const qualified = qualifySharedLabel(label, field, leftover);
        if (qualified && qualified !== label) relabel.set(field.path, qualified);
      }
    }
  }
  if (!relabel.size && !derivedCompanions.size) return fields;
  return fields.map(field => {
    const companion = derivedCompanions.get(field.path);
    if (companion) return companion;
    return relabel.has(field.path) ? { ...field, label: relabel.get(field.path)! } : field;
  });
}

export function finalizeCallerFields(
  fields: InputFormField[],
  observations: UiObservation[],
  sample: unknown,
  lists: RecordedList[] = [],
  owner?: UiEvidence
): InputFormField[] {
  void observations;
  void sample;
  void lists;
  void owner;
  return fields;
}

export function promoteUnboundFillable(
  fields: InputFormField[],
  observations: UiObservation[],
  sample: unknown
): InputFormField[] {
  void observations;
  void sample;
  return fields;
}

export function parseCollectionLeafPath(path: string) {
  const starred = /^(.*)\[\*\]\.(.+)$/.exec(path);
  if (starred) return { prefix: starred[1]!, index: "*" as const, key: starred[2]!.split(".")[0]! };
  const indexed = /^(.*)\[(\d+)\]\.(.+)$/.exec(path);
  if (indexed) return { prefix: indexed[1]!, index: Number(indexed[2]), key: indexed[3]!.split(".")[0]! };
  return undefined;
}

export function isCollectionMetadataKey(key: string) {
  return /^(itemType|_X_ROW_KEY|_X_ID|rowKey|row_key|sort|index)$/i.test(key);
}

export function requestValueAt(sample: unknown, path: string): unknown {
  const indexed = parseCollectionLeafPath(path);
  if (indexed && indexed.index !== "*") {
    const rows = collectionRowsAt(sample, indexed.prefix);
    return rows[indexed.index]?.[indexed.key];
  }
  const items = flattenRequestValues(sample);
  const exact = items.find(item => item.path === path);
  if (exact) return exact.value;
  const starred = items.filter(item => item.path === `${path}[*]`);
  if (starred.length === 1) return starred[0]!.value;
  if (starred.length > 1) return starred.map(item => item.value);
  return undefined;
}

export function collectionRowsAt(sample: unknown, collectionPath: string): Record<string, unknown>[] {
  const value = requestValueAt(sample, collectionPath);
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item));
}

export function collectionLeafPresentOnEveryRow(sample: unknown, leafPath: string) {
  const match = /^(.*)\[\*\]\.(.+)$/.exec(leafPath);
  if (!match) return true;
  const rows = collectionRowsAt(sample, match[1]!);
  if (!rows.length) return false;
  const key = match[2]!.split(".")[0]!;
  return rows.every(row => Object.prototype.hasOwnProperty.call(row, key));
}

export function collectionLeafUniform(sample: unknown, leafPath: string) {
  const match = /^(.*)\[\*\]\.(.+)$/.exec(leafPath);
  if (!match) return true;
  const rows = collectionRowsAt(sample, match[1]!);
  if (rows.length <= 1) return true;
  const key = match[2]!.split(".")[0]!;
  if (!rows.every(row => Object.prototype.hasOwnProperty.call(row, key))) return false;
  const first = rows[0]![key];
  return rows.every(row => Object.is(row[key], first) || sameValue(row[key], first));
}

function collectionDiscriminator(rows: Array<Record<string, unknown>>) {
  for (const key of ["itemType", "lineType", "rowType", "kind", "category"]) {
    if (!rows.every(row => row[key] !== undefined && row[key] !== null && row[key] !== "")) continue;
    if (new Set(rows.map(row => String(row[key]))).size > 1) return key;
  }
  return undefined;
}

function collectionKeySetsDiffer(rows: Array<Record<string, unknown>>) {
  const sets = rows.map(row => Object.keys(row).filter(key => !/^(_X_ROW_KEY|_X_ID|rowKey|sort)$/i.test(key)).sort().join("\0"));
  return new Set(sets).size > 1;
}

function distinctLeafLabels(observations: UiObservation[], key: string) {
  const labels = new Set(
    observations
      .filter(item => !looksReadonly(item) && item.label && !/^\d+$/.test(item.label) && (!item.name || item.name === key))
      .map(item => item.label!)
  );
  return labels.size > 1;
}

export function collectionIsSectioned(
  sample: unknown,
  collectionPath: string,
  observations: UiObservation[] = []
) {
  const rows = collectionRowsAt(sample, collectionPath);
  if (rows.length < 2) return false;
  if (collectionDiscriminator(rows) || collectionKeySetsDiffer(rows)) return true;
  const keys = [...new Set(rows.flatMap(row => Object.keys(row)))].filter(key => !isCollectionMetadataKey(key));
  return keys.some(key => distinctLeafLabels(observations, key));
}

function takeRowObservation(
  row: Record<string, unknown>,
  key: string,
  observations: UiObservation[],
  used: Set<UiObservation>
) {
  const value = row[key];
  const unused = observations.filter(item => !used.has(item) && !looksReadonly(item) && (!item.name || item.name === key));
  const byValue = unused.filter(item => observationMatchesValue(item, value, []));
  if (byValue.length === 1) return byValue[0];
  if (byValue.length > 1 && new Set(byValue.map(item => item.label)).size === 1) return byValue[0];
  const named = unused.filter(item => item.name === key);
  if (named.length === 1 && (value === undefined || value === null || value === "" || observationMatchesValue(named[0]!, value, []))) {
    return named[0];
  }
  return undefined;
}

function nextLeftoverObservation(
  key: string,
  observations: UiObservation[],
  used: Set<UiObservation>
) {
  return observations.find(item =>
    !used.has(item)
    && !looksReadonly(item)
    && item.label
    && !/^\d+$/.test(item.label)
    && (item.name === key || (!item.name && sameSynonymGroup({ name: key }, item)))
  );
}

function indexedCallerField(
  base: InputFormField,
  prefix: string,
  index: number,
  key: string,
  observation: UiObservation | undefined,
  value: unknown,
  observations: UiObservation[]
): InputFormField {
  const path = `${prefix}[${index}].${key}`;
  const next = {
    ...base,
    path,
    name: key,
    required: false,
    requiredBasis: "not-observed" as const
  };
  return observation
    ? asCaller(next, observation, value, observations, [])
    : {
      ...next,
      source: "caller",
      systemHandled: false,
      required: false,
      sourceDetail: "同一明细行已有页面输入，该业务列由调用方按行填写，不要套用其它行"
    };
}

export function splitSectionedCollectionFields(
  fields: InputFormField[],
  observations: UiObservation[],
  sample: unknown
): InputFormField[] {
  const parents = fields.filter(field =>
    field.valueType === "array"
    && collectionIsSectioned(sample, field.path, observations)
  );
  if (!parents.length) return fields;
  const used = new Set<UiObservation>();
  const replacements = new Map<string, InputFormField[]>();
  for (const parent of parents) {
    const rows = collectionRowsAt(sample, parent.path);
    const keys = [...new Set(rows.flatMap(row => Object.keys(row)))];
    const businessKeys = keys.filter(key => !isCollectionMetadataKey(key));
    for (const key of businessKeys) {
      const base = fields.find(field => field.path === `${parent.path}[*].${key}`)
        || fields.find(field => field.name === key && field.path.startsWith(`${parent.path}[`));
      if (!base) continue;
      const indexed: InputFormField[] = [];
      for (let index = 0; index < rows.length; index++) {
        const row = rows[index]!;
        if (!Object.prototype.hasOwnProperty.call(row, key)) continue;
        const observation = takeRowObservation(row, key, observations, used)
          || nextLeftoverObservation(key, observations, used);
        if (observation) used.add(observation);
        indexed.push(indexedCallerField(base, parent.path, index, key, observation, row[key], observations));
      }
      if (indexed.length) replacements.set(`${parent.path}[*].${key}`, indexed);
    }
  }
  if (!replacements.size) return fields;
  return fields.flatMap(field => replacements.get(field.path) || [field]);
}

export function attachOptionalNamedFilters(
  fields: InputFormField[],
  observations: UiObservation[],
  sample: unknown,
  query = false,
  owner?: UiEvidence
): InputFormField[] {
  void observations;
  void sample;
  void query;
  void owner;
  return fields;
}

function formNames(form: NonNullable<UiEvidence["form"]>) {
  return form.map(field => realFieldName(field.name)).filter((name): name is string => Boolean(name));
}

function formLabels(form: NonNullable<UiEvidence["form"]>) {
  return form.map(field => field.label).filter((label): label is string => Boolean(label));
}

function nameOverlap(form: NonNullable<UiEvidence["form"]>, requestNames: string[]) {
  return formNames(form).filter(name => requestNames.some(requestName => uiNameMatches(name, requestName))).length;
}

function valueOverlap(form: NonNullable<UiEvidence["form"]>, sample: unknown) {
  const values = flattenRequestValues(sample);
  return form.filter(field =>
    field.value !== undefined && field.value !== null && field.value !== ""
    && values.some(item =>
      sameValue(item.value, field.value)
      && (isDistinctiveValue(item.value) || ASK_KEY.test(item.name) || ASK_KEY.test(field.name || ""))
    )
  ).length;
}

export function owningFormEvent(
  event: NetworkEvidence,
  uiEvents: UiEvidence[],
  sample: unknown
): UiEvidence | undefined {
  const requestNames = flattenRequestValues(sample).map(item => item.name);
  const correlated = event.correlatedUiEvidenceId
    ? uiEvents.find(item => item.id === event.correlatedUiEvidenceId)
    : undefined;
  if (correlated?.form?.length) return correlated;
  const at = Date.parse(event.at);
  const candidates = uiEvents
    .filter(item => item.sessionId === event.sessionId && Date.parse(item.at) <= at && Boolean(item.form?.length))
    .sort((left, right) => Date.parse(right.at) - Date.parse(left.at));

  let best: UiEvidence | undefined;
  let bestScore = -1;
  for (const item of candidates) {
    const named = formNames(item.form!);
    const names = nameOverlap(item.form!, requestNames);
    const values = valueOverlap(item.form!, sample);
    if (names === 0 && values === 0 && item.id !== correlated?.id) continue;
    const union = named.length + requestNames.length - names;
    const jaccard = union > 0 ? names / union : 0;
    const recency = 1 / (1 + (at - Date.parse(item.at)) / 60_000);
    const extra = Math.max(0, named.length - names);
    const write = /^(POST|PUT|PATCH|DELETE)$/i.test(event.request.method);
    const dialogPenalty = !write && item.scope === "dialog" ? 8 : 0;
    const score = jaccard * 10 + values * 2 + recency * 0.1 - extra * 2 - dialogPenalty;
    if (score > bestScore) {
      bestScore = score;
      best = item;
    }
  }
  return best || (correlated?.form?.length ? correlated : undefined);
}

function belongsToOwningForm(
  item: UiEvidence,
  owner: UiEvidence | undefined,
  ownerLabels: Set<string>,
  ownerNames: Set<string>,
  correlatedId?: string
) {
  if (owner && item.id === owner.id) return true;
  if (correlatedId && item.id === correlatedId) return true;
  if (item.form?.length && owner?.form?.length) {
    const labels = [...new Set(formLabels(item.form))];
    const extra = labels.filter(label => !ownerLabels.has(label)).length;
    const overlap = labels.filter(label => ownerLabels.has(label)).length;
    return labels.length > 0 && extra === 0 && overlap > 0;
  }
  const asked = emptyPromptLabel(item.text);
  return Boolean(
    (item.label && ownerLabels.has(item.label))
    || (item.name && ownerNames.has(item.name))
    || (asked && ownerLabels.has(asked))
  );
}

export function sameFormShape(left?: UiEvidence, right?: UiEvidence) {
  if (!left?.form?.length || !right?.form?.length) return false;
  const labels = new Set(formLabels(left.form));
  const other = formLabels(right.form);
  const overlap = other.filter(label => labels.has(label)).length;
  const union = new Set([...labels, ...other]).size;
  return union > 0 && overlap / union >= 0.6;
}

function distinctiveRequestTokens(sample: unknown) {
  return new Set(
    flattenRequestValues(sample)
      .filter(item =>
        isDistinctiveValue(item.value)
        || ASK_KEY.test(item.name) && item.value !== undefined && item.value !== null && String(item.value).trim() !== ""
      )
      .map(item => String(item.value))
  );
}

function eventMatchesRequest(item: UiEvidence, sample: unknown) {
  const tokens = distinctiveRequestTokens(sample);
  if (!tokens.size) return false;
  const candidates = [item.name, item.value, item.text, item.label, ...(item.form || []).map(field => field.value)];
  return candidates.some(value => {
    if (value === undefined || value === null || value === "") return false;
    if (tokens.has(String(value))) return true;
    return String(value).split(/\s+/).some(token => tokens.has(token));
  });
}

function formFitsRequest(form: NonNullable<UiEvidence["form"]>, sample: unknown) {
  const requestNames = flattenRequestValues(sample).map(item => item.name);
  const named = formNames(form);
  const overlap = nameOverlap(form, requestNames);
  const values = valueOverlap(form, sample);
  if (overlap === 0 && values === 0) return false;
  const extraNamed = named.filter(name =>
    !requestNames.some(requestName => uiNameMatches(name, requestName) || uiNameMatches(requestName, name))
  );
  return extraNamed.length < 2 || overlap === named.length;
}

export function relatedUiEvents(
  event: NetworkEvidence,
  uiById: Map<string, UiEvidence>,
  sample: unknown
): UiEvidence[] {
  const uiEvents = [...uiById.values()];
  const owner = owningFormEvent(event, uiEvents, sample);
  const ownerLabels = new Set(owner?.form ? formLabels(owner.form) : []);
  const ownerNames = new Set(owner?.form ? formNames(owner.form) : []);
  const pageKey = (raw?: string) => {
    if (!raw) return "";
    try {
      const parsed = new URL(raw);
      // A hash route is the real page identity in an SPA. Treating only the
      // origin/path as the page key mixes list, detail and edit forms that all
      // live under `/web/`.
      return `${parsed.origin}${parsed.pathname}${parsed.hash.split("?")[0]}`;
    } catch {
      return raw.split("?", 1)[0] || raw;
    }
  };
  const ownerPage = pageKey(owner?.pageUrl);
  const at = Date.parse(event.at);
  return uiEvents
    .filter(item => {
      if (item.sessionId !== event.sessionId) return false;
      if (Date.parse(item.at) > at + 500) return false;
      if (ownerPage && pageKey(item.pageUrl) !== ownerPage && item.id !== event.correlatedUiEvidenceId) return false;
      return belongsToOwningForm(item, owner, ownerLabels, ownerNames, event.correlatedUiEvidenceId)
        || eventMatchesRequest(item, sample)
        || Boolean(item.form?.length && formFitsRequest(item.form, sample));
    })
    .sort((left, right) => Date.parse(right.at) - Date.parse(left.at));
}
