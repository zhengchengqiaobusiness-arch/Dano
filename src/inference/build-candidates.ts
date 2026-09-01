import type {
  CapabilityContract,
  EvidenceEvent,
  InputFormField,
  NetworkEvidence,
  UiEvidence
} from "../domain.js";
import { inferOperation, normalizeUrl, operationConfidence } from "./heuristics.js";
import { mergeSchemas, schemaFromValue } from "../schema.js";
import { slugify } from "../utils.js";

const QUERY_LIKE_UI = /query|search|find|list|page|detail|lookup|查询|搜索|列表|详情|检索/i;
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
  return `$.${name.replace(/\[([^\]]+)\]/g, ".$1").replace(/^\./, "")}`;
}

function valueType(raw: any): InputFormField["valueType"] {
  const type = Array.isArray(raw?.type) ? raw.type.find((item: string) => item !== "null") : raw?.type;
  return ["string", "number", "integer", "boolean", "array", "object"].includes(type) ? type : "unknown";
}

function uiFieldToForm(field: NonNullable<UiEvidence["form"]>[number]): InputFormField | undefined {
  if (!field?.name) return undefined;
  const type = field.type || "text";
  const widget: InputFormField["widget"] =
    type === "number" ? "number" :
    type === "checkbox" ? "boolean" :
    type === "select-one" || type === "select" ? "select" :
    type === "select-multiple" ? "multiselect" : "text";
  const options = field.options?.map(o => ({ value: o.value, label: o.label || o.value }));
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
      return schemaFieldsToForm(raw, path, isRequired);
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
      required: isRequired,
      requiredBasis: isRequired ? "observed-always" : "not-observed",
      systemHandled: true,
      sourceDetail: "请求中观察到该字段，但未观察到用户输入；默认由业务系统处理",
      widget,
      candidates: Array.isArray(raw?.enum)
        ? { type: "static", values: raw.enum.map((value: unknown) => ({ value, label: String(value) })) }
        : undefined
    }];
  });
}


function inferCompletion(group: NetworkEvidence[], operation: string) {
  const successful = group.filter(g => g.response && g.response.status >= 200 && g.response.status < 400);
  const acceptedHttpStatuses = [...new Set(successful.map(g => g.response!.status))];
  const assertions: NonNullable<CapabilityContract["completion"]["assertions"]> = [];
  const sample = successful.find(g => g.response?.body && typeof g.response.body === "object")?.response?.body as any;
  if (sample && typeof sample === "object" && !Array.isArray(sample)) {
    if (sample.success === true) assertions.push({ path: "$.success", kind: "equals", value: true });
    else if (sample.ok === true) assertions.push({ path: "$.ok", kind: "equals", value: true });
    if (operation === "create") {
      if (sample.id !== undefined && sample.id !== null) assertions.push({ path: "$.id", kind: "nonempty" });
      else if (sample.data?.id !== undefined && sample.data?.id !== null) assertions.push({ path: "$.data.id", kind: "nonempty" });
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

  for (const event of network) {
    const normalized = normalizeUrl(event.request.url);
    const ui = event.correlatedUiEvidenceId ? uiById.get(event.correlatedUiEvidenceId) : undefined;
    const operation = inferOperation(event, ui);
    const key = `${operation}|${event.request.method.toUpperCase()}|${normalized.urlTemplate}`;
    const list = groups.get(key) || [];
    list.push(event);
    groups.set(key, list);
  }

  const result: CapabilityContract[] = [];
  const usedIds = new Set<string>();

  for (const group of groups.values()) {
    const first = group[0]!;
    const normalized = normalizeUrl(first.request.url);
    const ui = first.correlatedUiEvidenceId ? uiById.get(first.correlatedUiEvidenceId) : undefined;
    const operation = inferOperation(first, ui);
    const method = first.request.method.toUpperCase();
    const baseTitle = `${operation}-${method.toLowerCase()}-${normalized.pathTemplate.split("/").filter(Boolean).slice(-2).join("-") || "root"}`;
    let capId = slugify(baseTitle);
    let n = 2;
    while (usedIds.has(capId)) capId = `${slugify(baseTitle)}-${n++}`;
    usedIds.add(capId);

    let inputSchema = {};
    let outputSchema = {};
    const forms = new Map<string, InputFormField>();
    const directUiNames = new Set<string>();

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
            required: mapped.required || previous.required,
            requiredBasis: mapped.required ? "ui-required" : previous.requiredBasis,
            candidates: mapped.candidates || previous.candidates
          } : mapped);
        }
      }
      if (correlated?.name) {
        directUiNames.add(correlated.name);
        const observed = correlated.options?.length
          ? correlated.options.map(o => ({ value: o.value, label: o.label || o.value }))
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
      if (event.correlatedUiEvidenceId) {
        const correlated = uiById.get(event.correlatedUiEvidenceId);
        if (correlated) refs.push({
          eventId: correlated.id,
          sessionId: correlated.sessionId,
          kind: "ui",
          at: correlated.at
        });
      }
      return refs;
    });

    result.push({
      id: capId,
      kind: "atomic",
      title: ((operation !== "query" || QUERY_LIKE_UI.test(`${ui?.text || ""} ${ui?.label || ""}`)) && (ui?.text || ui?.label)) || `${operation} ${normalized.pathTemplate}`,
      description: `已从真实操作观察到“${OPERATION_NAMES[operation]}”能力。请结合业务含义核对并修改本描述。`,
      operation,
      confidence: operationConfidence(first, ui),
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
        for (const field of inferred) forms.set(field.path, observed.get(field.path) ? {
          ...field,
          ...observed.get(field.path),
          required: observed.get(field.path)!.required || field.required,
          requiredBasis: observed.get(field.path)!.required ? observed.get(field.path)!.requiredBasis : field.requiredBasis,
          candidates: observed.get(field.path)!.candidates || field.candidates
        } : field);
        for (const [fieldPath, field] of observed) {
          const isTransportInput = normalized.urlTemplate.includes(`{${field.name}}`);
          const isDirectInteraction = directUiNames.has(field.name);
          if (!forms.has(fieldPath) && (isTransportInput || isDirectInteraction)) forms.set(fieldPath, field);
        }
        return [...forms.values()];
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
