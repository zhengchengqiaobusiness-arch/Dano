/**
 * 文件级说明：本文件只按 method+path 聚类证据并摊出请求键。
 * 字段归属和主能力由 Pi Skill 判断；精确同名/独值拼接在 `pi-skill-runtime`。
 * 旧绑定流水线见 `build-candidates.ts.bak`。
 */
import type {
  CapabilityContract,
  EvidenceEvent,
  InputFormField,
  NetworkEvidence,
  UiEvidence
} from "../domain.js";
import { inferOperation, inferUiOperationIntent, isSuccessfulNetworkEvidence, normalizeUrl, operationConfidence } from "./heuristics.js";
import {
  collectUiObservations,
  flattenRequestValues,
  owningFormEvent,
  preferRequestValueType,
  relatedUiEvents,
  sameFormShape,
  splitSectionedCollectionFields,
  uiNameMatches
} from "./field-resolver.js";
import { mergeSchemas, schemaFromValue } from "../schema.js";
import { slugify } from "../utils.js";

const OPERATION_NAMES: Record<CapabilityContract["operation"], string> = {
  query: "查询", create: "新建", update: "修改", review: "审核", delete: "删除",
  authenticate: "认证", upload: "上传", download: "下载", action: "业务动作", unknown: "未识别"
};

function requestInput(event: NetworkEvidence) {
  const method = event.request.method.toUpperCase();
  if (["GET", "HEAD"].includes(method)) return event.request.query;
  if (event.request.body && typeof event.request.body === "object") return event.request.body;
  return event.request.query;
}

function isStudioInternal(event: NetworkEvidence) {
  try {
    const url = new URL(event.request.url);
    const studioPort = String(process.env.BSS_PORT || "4310");
    return ["127.0.0.1", "localhost"].includes(url.hostname) && url.port === studioPort;
  } catch {
    return false;
  }
}

function jsonPathForName(name: string) {
  if (name.startsWith("$.")) return name;
  return `$.${name}`;
}

function valueType(raw: any): InputFormField["valueType"] {
  const type = Array.isArray(raw?.type) ? raw.type.find((item: string) => item !== "null") : raw?.type;
  return ["string", "number", "integer", "boolean", "array", "object"].includes(type) ? type : "unknown";
}

function widgetFromUiType(type: string): InputFormField["widget"] {
  const kind = type.toLowerCase();
  if (kind === "number") return "number";
  if (kind === "checkbox" || kind === "boolean") return "boolean";
  if (kind === "select-multiple" || kind === "multiselect") return "multiselect";
  if (kind === "select-one" || kind === "select" || kind === "combobox" || kind === "picker") return "select";
  if (kind === "textarea") return "textarea";
  if (kind === "date" || kind === "datetime" || kind === "daterange") return "date";
  return "text";
}

function observationLabel(label?: string) {
  const raw = String(label || "").trim().replace(/^(请输入|请选择|请填写)\s*/, "");
  return raw || undefined;
}

function applyNamedObservations(fields: InputFormField[], observations: ReturnType<typeof collectUiObservations>) {
  return fields.map(field => {
    const named = observations.find(item => uiNameMatches(item.name, field.name));
    if (!named) return field;
    const options = named.options?.length
      ? named.options.map(item => ({ value: item.value, label: String(item.label || item.value) }))
      : undefined;
    const disabled = named.disabled === true || /readonly|disabled/i.test(String(named.type || ""));
    return {
      ...field,
      label: observationLabel(named.label) || field.label,
      source: disabled ? field.source : "caller",
      systemHandled: disabled ? field.systemHandled : false,
      required: named.required === true || field.required,
      requiredBasis: named.required === true ? "ui-required" as const : field.requiredBasis,
      widget: named.type ? widgetFromUiType(named.type) : field.widget,
      candidates: options?.length ? { type: "static" as const, values: options } : field.candidates,
      sourceDetail: disabled
        ? "页面只读展示，由选择其它字段后自动带出。调用方不要手填"
        : "页面同名控件，由调用方提供"
    };
  });
}

function uiFieldToForm(field: NonNullable<UiEvidence["form"]>[number]): InputFormField | undefined {
  if (!field?.name) return undefined;
  const type = field.type || "text";
  const widget = widgetFromUiType(type);
  const options = field.options?.map(o => ({ value: o.value, label: String(o.label || o.value) }));
  return {
    path: jsonPathForName(field.name),
    name: field.name,
    label: field.label || field.name,
    valueType: type === "number" ? "number" : type === "checkbox" ? "boolean" : type === "select-multiple" ? "array" : "string",
    source: "caller",
    required: Boolean(field.required),
    requiredBasis: field.required ? "ui-required" : "not-observed",
    systemHandled: false,
    sourceDetail: "真实页面表单中观察到，由调用方提供",
    widget,
    candidates: options?.length ? { type: "static", values: options } : undefined
  };
}

function schemaFieldsToForm(schema: any, prefix = "$", parentRequired = true): InputFormField[] {
  if (!schema || schema.type !== "object" || !schema.properties) return [];
  const required = new Set<string>(schema.required || []);
  return Object.entries(schema.properties).flatMap(([name, raw]: [string, any]) => {
    const type = valueType(raw);
    const path = `${prefix}.${name}`;
    const isRequired = parentRequired && required.has(name);
    if (type === "object" && raw?.properties) {
      const children = schemaFieldsToForm(raw, path, false);
      return [{
        path,
        name,
        label: raw?.title || name,
        valueType: "object",
        source: "system",
        required: false,
        requiredBasis: "not-observed",
        systemHandled: true,
        sourceDetail: `由子字段 ${children.map(item => item.name).join("、")} 按路径拼接，调用方不要手填`,
        widget: "json"
      }, ...children];
    }
    if (type === "array" && raw?.items?.type === "object" && raw.items.properties) {
      const children = schemaFieldsToForm(raw.items, `${path}[*]`, false);
      return [{
        path,
        name,
        label: raw?.title || name,
        valueType: "array",
        source: "system",
        required: false,
        requiredBasis: "not-observed",
        systemHandled: true,
        sourceDetail: `明细按录制成功请求的整表原样补齐；调用方只覆盖有页面输入的单元格`,
        widget: "json"
      }, ...children];
    }
    const widget: InputFormField["widget"] =
      type === "number" || type === "integer" ? "number" :
      type === "boolean" ? "boolean" :
      type === "array" ? "multiselect" :
      type === "object" ? "json" :
      Array.isArray(raw?.enum) ? "select" : "text";
    return [{
      path,
      name,
      label: raw?.title || name,
      valueType: type,
      source: "system",
      required: false,
      requiredBasis: "not-observed",
      systemHandled: true,
      sourceDetail: "请求中观察到该字段，但未观察到用户输入；默认由业务系统处理",
      widget,
      candidates: Array.isArray(raw?.enum)
        ? { type: "static", values: raw.enum.map((value: unknown) => ({ value, label: String(value) })) }
        : undefined
    }];
  });
}


function unionUrl(group: NetworkEvidence[]) {
  const first = normalizeUrl(group[0]!.request.url);
  const keys = new Set<string>();
  for (const event of group) {
    try {
      for (const key of new URL(event.request.url).searchParams.keys()) keys.add(key);
    } catch {
      // ignore malformed recorded URLs
    }
  }
  const suffix = [...keys].sort().map(key => `${encodeURIComponent(key)}={${key}}`).join("&");
  return {
    origin: first.origin,
    pathTemplate: first.pathTemplate,
    urlTemplate: `${first.origin}${first.pathTemplate}${suffix ? `?${suffix}` : ""}`
  };
}

function pickUi(group: NetworkEvidence[], uiById: Map<string, UiEvidence>) {
  const items = group
    .map(event => event.correlatedUiEvidenceId ? uiById.get(event.correlatedUiEvidenceId) : undefined)
    .filter((item): item is UiEvidence => Boolean(item));
  return items.find(item => /搜索|查询|新增|新建|确定|保存|提交/.test(`${item.text || ""} ${item.label || ""}`)) || items[0];
}

function capabilityTitle(operation: CapabilityContract["operation"], ui: UiEvidence | undefined, pathTemplate: string) {
  const skip = new Set(["admin-api", "erp", "system", "page", "create", "update", "delete", "simple-list", "list", "get", "query", "submit-process", "start-process"]);
  const resource = pathTemplate.split("/").filter(part => part && !skip.has(part)).slice(-2).join("/") || pathTemplate;
  if (operation === "create") return `新建 ${resource}`;
  if (operation === "update") return `修改 ${resource}`;
  if (operation === "delete") return `删除 ${resource}`;
  if (operation === "review") return `审核 ${resource}`;
  if (operation === "query") return `查询 ${resource}`;
  if (operation === "authenticate") return ui?.text || ui?.label || `认证 ${resource}`;
  return ui?.text || ui?.label || `${OPERATION_NAMES[operation]} ${resource}`;
}

function inferCompletion(group: NetworkEvidence[], operation: string) {
  const successful = group.filter(isSuccessfulNetworkEvidence);
  const acceptedHttpStatuses = [...new Set(successful.map(g => g.response!.status))];
  const assertions: NonNullable<CapabilityContract["completion"]["assertions"]> = [];
  const sample = successful.find(g => g.response?.body && typeof g.response.body === "object")?.response?.body as any;
  if (sample && typeof sample === "object" && !Array.isArray(sample)) {
    if (sample.success === true) assertions.push({ path: "$.success", kind: "equals", value: true });
    else if (sample.ok === true) assertions.push({ path: "$.ok", kind: "equals", value: true });
    if (["string", "number", "boolean"].includes(typeof sample.code)) {
      assertions.push({ path: "$.code", kind: "equals", value: sample.code });
    }
    if (operation === "create") {
      if (sample.id !== undefined && sample.id !== null) assertions.push({ path: "$.id", kind: "nonempty" });
      else if (sample.data && typeof sample.data === "object" && !Array.isArray(sample.data) && sample.data.id !== undefined && sample.data.id !== null) {
        assertions.push({ path: "$.data.id", kind: "nonempty" });
      } else if (sample.data !== undefined && sample.data !== null && sample.data !== "" && (typeof sample.data === "number" || typeof sample.data === "string")) {
        assertions.push({ path: "$.data", kind: "nonempty" });
      }
    }
  }
  return {
    acceptedHttpStatuses: acceptedHttpStatuses.length ? acceptedHttpStatuses : [200],
    assertions,
    note: "Success requires an accepted HTTP status plus every declared evidence-derived assertion."
  };
}

export function buildCapabilityCandidates(events: EvidenceEvent[]): CapabilityContract[] {
  const uiById = new Map(events.filter((e): e is UiEvidence => e.kind === "ui").map(e => [e.id, e]));
  const network = events.filter((e): e is NetworkEvidence =>
    e.kind === "network" && Boolean(e.response) && ["xhr", "fetch"].includes(e.request.resourceType) && !isStudioInternal(e)
  );
  const groups = new Map<string, NetworkEvidence[]>();
  type ActiveFormIntent = {
    intent: "create" | "update";
    entryPageUrl: string;
    formPageUrl?: string;
    successSeen?: boolean;
  };
  const activeIntentBySession = new Map<string, ActiveFormIntent>();
  const formIntentByNetworkId = new Map<string, "create" | "update">();
  const cancelled = /^(重置|取消|关闭|返回|reset|cancel|close|back)$/i;

  for (const event of events) {
    if (event.kind === "ui") {
      const label = String(event.text || event.label || "").replace(/\s+/g, "");
      const intent = inferUiOperationIntent(label, event.pageUrl);
      if (intent === "create" || intent === "update") {
        activeIntentBySession.set(event.sessionId, { intent, entryPageUrl: event.pageUrl });
      }
      else if (cancelled.test(label)) activeIntentBySession.delete(event.sessionId);
      else {
        const active = activeIntentBySession.get(event.sessionId);
        if (active && event.pageUrl !== active.entryPageUrl) active.formPageUrl = event.pageUrl;
        else if (active?.successSeen && active.formPageUrl && event.pageUrl === active.entryPageUrl) {
          activeIntentBySession.delete(event.sessionId);
        }
      }
      continue;
    }
    if (event.kind !== "network") continue;
    const active = activeIntentBySession.get(event.sessionId);
    if (active) formIntentByNetworkId.set(event.id, active.intent);
    const correlated = event.correlatedUiEvidenceId ? uiById.get(event.correlatedUiEvidenceId) : undefined;
    if (active && inferOperation(event, correlated, active.intent) === active.intent && isSuccessfulNetworkEvidence(event)) {
      active.successSeen = true;
    }
  }

  for (const event of network) {
    const normalized = normalizeUrl(event.request.url);
    const ui = event.correlatedUiEvidenceId ? uiById.get(event.correlatedUiEvidenceId) : undefined;
    const operation = inferOperation(event, ui, formIntentByNetworkId.get(event.id));
    const key = `${operation}|${event.request.method.toUpperCase()}|${normalized.pathTemplate}`;
    const list = groups.get(key) || [];
    list.push(event);
    groups.set(key, list);
  }

  const result: CapabilityContract[] = [];
  const usedIds = new Set<string>();

  for (const group of groups.values()) {
    const first = group[0]!;
    const normalized = unionUrl(group);
    const ui = pickUi(group, uiById);
    const activeFormIntent = formIntentByNetworkId.get(first.id);
    const operation = inferOperation(first, ui, activeFormIntent);
    const method = first.request.method.toUpperCase();
    const baseTitle = `${operation}-${method.toLowerCase()}-${normalized.pathTemplate.split("/").filter(Boolean).slice(-2).join("-") || "root"}`;
    let capId = slugify(baseTitle);
    let n = 2;
    while (usedIds.has(capId)) capId = `${slugify(baseTitle)}-${n++}`;
    usedIds.add(capId);

    let inputSchema = {};
    let outputSchema = {};
    const forms = new Map<string, InputFormField>();

    for (const event of group) {
      inputSchema = mergeSchemas(inputSchema, schemaFromValue(requestInput(event)));
      outputSchema = mergeSchemas(outputSchema, schemaFromValue(event.response?.body));
      const correlated = event.correlatedUiEvidenceId ? uiById.get(event.correlatedUiEvidenceId) : undefined;
      for (const field of correlated?.form || []) {
        const mapped = uiFieldToForm(field);
        if (mapped) {
          const previous = forms.get(mapped.path);
          forms.set(mapped.path, previous ? {
            ...previous,
            ...mapped,
            required: mapped.required === true || previous.required === true,
            requiredBasis: mapped.required || previous.required ? "ui-required" : previous.requiredBasis,
            candidates: mapped.candidates || previous.candidates
          } : mapped);
        }
      }
      if (correlated?.name) {
        const observed = correlated.options?.length
          ? correlated.options.map(o => ({ value: o.value, label: String(o.label || o.value) }))
          : correlated.visibleOptions?.length
            ? correlated.visibleOptions.map(label => ({ value: label, label }))
            : undefined;
        const path = jsonPathForName(correlated.name);
        const existing = forms.get(path);
        forms.set(path, {
          path,
          name: correlated.name,
          label: correlated.label || existing?.label || correlated.name,
          valueType: existing?.valueType || (correlated.inputType === "number" ? "number" : "string"),
          source: "caller",
          required: existing?.required || false,
          requiredBasis: existing?.requiredBasis || "not-observed",
          systemHandled: false,
          sourceDetail: "真实页面交互中观察到，由调用方提供",
          widget: observed?.length ? "select" : (existing?.widget || "text"),
          candidates: observed?.length ? { type: "static", values: observed } : existing?.candidates
        });
      }
    }

    const evidence = group.flatMap(event => {
      const refs: CapabilityContract["evidence"] = [{
        eventId: event.id,
        sessionId: event.sessionId,
        kind: "network",
        at: event.at,
        status: event.response?.status
      }];
      const sample = requestInput(event);
      const nearby = relatedUiEvents(event, uiById, sample);
      if (event.correlatedUiEvidenceId) {
        const correlated = uiById.get(event.correlatedUiEvidenceId);
        if (correlated) nearby.unshift(correlated);
      }
      const seen = new Set<string>();
      for (const item of nearby) {
        if (seen.has(item.id)) continue;
        seen.add(item.id);
        refs.push({
          eventId: item.id,
          sessionId: item.sessionId,
          kind: "ui",
          at: item.at
        });
      }
      return refs;
    });

    result.push({
      id: capId,
      kind: "atomic",
      title: capabilityTitle(operation, ui, normalized.pathTemplate),
      description: `已从真实操作观察到“${OPERATION_NAMES[operation]}”能力。请结合业务含义核对并修改本描述。`,
      operation,
      confidence: operationConfidence(first, ui, activeFormIntent),
      transport: {
        method,
        urlTemplate: normalized.urlTemplate,
        origin: normalized.origin,
        pathTemplate: normalized.pathTemplate
      },
      inputSchema,
      outputSchema,
      inputForm: (() => {
        const inferred = schemaFieldsToForm(inputSchema);
        const observed = new Map(forms);
        forms.clear();
        const requestSize = (event: NetworkEvidence) => flattenRequestValues(requestInput(event)).length;
        const ownerSource = group.reduce((best, event) =>
          requestSize(event) > requestSize(best) ? event : best
        , first);
        const sample = requestInput(ownerSource);
        const owner = owningFormEvent(ownerSource, [...uiById.values()], sample);
        const nearbyUi = group.flatMap(event => {
          const other = owningFormEvent(event, [...uiById.values()], requestInput(event));
          if (event !== ownerSource && !sameFormShape(owner, other)) return [];
          return relatedUiEvents(event, uiById, requestInput(event));
        });
        const observations = collectUiObservations(nearbyUi);
        const merged = inferred.map(field => {
          const seen = observed.get(field.path);
          if (!seen) return field;
          return {
            ...field,
            ...seen,
            valueType: preferRequestValueType(field.valueType, seen.valueType),
            required: seen.required === true,
            requiredBasis: seen.required ? "ui-required" as const : "not-observed" as const,
            widget: preferRequestValueType(field.valueType, seen.valueType) === "array"
              && (seen.widget === "select" || seen.widget === "text")
              ? "multiselect"
              : seen.widget || field.widget,
            candidates: seen.candidates || field.candidates
          };
        });
        const byPath = new Map(merged.map(field => [field.path, field]));
        for (const [fieldPath, field] of observed) {
          if (byPath.has(fieldPath)) continue;
          if (normalized.urlTemplate.includes(`{${field.name}}`)) byPath.set(fieldPath, field);
        }
        return splitSectionedCollectionFields(
          applyNamedObservations([...byPath.values()], observations),
          observations,
          sample
        );
      })(),
      evidence,
      sideEffect: ["create", "update", "review", "delete", "upload", "action"].includes(operation),
      confirmation: {
        required: ["create", "update", "review", "delete", "upload", "action"].includes(operation),
        reason: ["create", "update", "review", "delete", "upload", "action"].includes(operation)
          ? `${OPERATION_NAMES[operation]}会改变业务或文件数据`
          : undefined
      },
      completion: inferCompletion(group, operation),
      bindings: [],
      validation: {
        version: 2,
        status: "candidate",
        checks: []
      },
      generated: {
        source: "heuristic",
        generatedAt: new Date().toISOString()
      },
      editing: {
        title: "generated",
        description: "generated",
        operation: "generated",
        fields: "generated"
      }
    });
  }

  return result;
}
