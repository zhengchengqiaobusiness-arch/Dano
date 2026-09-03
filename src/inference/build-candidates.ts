import type {
  CapabilityContract,
  EvidenceEvent,
  InputFormField,
  NetworkEvidence,
  UiEvidence
} from "../domain.js";
import { inferOperation, normalizeUrl, operationConfidence } from "./heuristics.js";
import { attachCatalogDerivations } from "./field-derivation.js";
import { assignUniqueFromSamples, bindByUniqueMatching, bindLeftoverFields, collectUiObservations, finalizeCallerFields, flattenRequestValues, owningFormEvent, promoteUnboundFillable, recordedLists, relatedUiEvents, requestValueAt, resolveFieldOwnership, sameFormShape } from "./field-resolver.js";
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
  if (kind === "select-one" || kind === "select" || kind === "combobox") return "select";
  if (kind === "textarea") return "textarea";
  if (kind === "date" || kind === "datetime" || kind === "daterange") return "date";
  return "text";
}

function uiFieldToForm(field: NonNullable<UiEvidence["form"]>[number]): InputFormField | undefined {
  if (!field?.name) return undefined;
  const type = field.type || "text";
  const widget = widgetFromUiType(type);
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
      return schemaFieldsToForm(raw, path, false);
    }
    if (type === "array" && raw?.items?.type === "object" && raw.items.properties) {
      return schemaFieldsToForm(raw.items, `${path}[*]`, false);
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
  const successful = group.filter(g => g.response && g.response.status >= 200 && g.response.status < 400);
  const acceptedHttpStatuses = [...new Set(successful.map(g => g.response!.status))];
  const assertions: NonNullable<CapabilityContract["completion"]["assertions"]> = [];
  const sample = successful.find(g => g.response?.body && typeof g.response.body === "object")?.response?.body as any;
  if (sample && typeof sample === "object" && !Array.isArray(sample)) {
    if (sample.success === true) assertions.push({ path: "$.success", kind: "equals", value: true });
    else if (sample.ok === true) assertions.push({ path: "$.ok", kind: "equals", value: true });
    if (sample.code === 0) assertions.push({ path: "$.code", kind: "equals", value: 0 });
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

  for (const event of network) {
    const normalized = normalizeUrl(event.request.url);
    const ui = event.correlatedUiEvidenceId ? uiById.get(event.correlatedUiEvidenceId) : undefined;
    const operation = inferOperation(event, ui);
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
            required: mapped.required === true || previous.required === true,
            requiredBasis: mapped.required || previous.required ? "ui-required" : previous.requiredBasis,
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
        const lists = recordedLists(network);
        for (const field of inferred) {
          const seen = observed.get(field.path);
          const merged = seen ? {
            ...field,
            ...seen,
            required: seen.required === true,
            requiredBasis: seen.required ? "ui-required" : "not-observed",
            candidates: seen.candidates || field.candidates
          } : field;
          forms.set(field.path, resolveFieldOwnership(merged, requestValueAt(sample, merged.path), observations, lists, sample));
        }
        for (const [fieldPath, field] of observed) {
          const isTransportInput = normalized.urlTemplate.includes(`{${field.name}}`);
          const isDirectInteraction = directUiNames.has(field.name);
          if (!forms.has(fieldPath) && (isTransportInput || isDirectInteraction)) {
            forms.set(fieldPath, resolveFieldOwnership(field, requestValueAt(sample, field.path), observations, lists, sample));
          }
        }
        const samples = group.map(requestInput);
        return finalizeCallerFields(
          promoteUnboundFillable(
            bindByUniqueMatching(
              assignUniqueFromSamples(
                bindLeftoverFields([...forms.values()], observations, sample, lists, owner),
                observations,
                samples,
                lists
              ),
              observations,
              sample,
              lists
            ),
            observations,
            sample
          ),
          observations,
          sample,
          lists,
          owner
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

  return attachCatalogDerivations(result, events);
}
