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

function requestInput(event: NetworkEvidence) {
  const method = event.request.method.toUpperCase();
  if (["GET", "HEAD"].includes(method)) return event.request.query;
  if (event.request.body && typeof event.request.body === "object") return event.request.body;
  return event.request.query;
}

function uiFieldToForm(field: NonNullable<UiEvidence["form"]>[number], prefix = "$."): InputFormField | undefined {
  if (!field?.name) return undefined;
  const type = field.type || "text";
  const widget: InputFormField["widget"] =
    type === "number" ? "number" :
    type === "checkbox" ? "boolean" :
    type === "select-one" || type === "select" ? "select" :
    type === "select-multiple" ? "multiselect" : "text";
  const options = field.options?.map(o => ({ value: o.value, label: o.label || o.value }));
  return {
    path: `${prefix}${field.name}`,
    label: field.label || field.name,
    required: Boolean(field.required),
    widget,
    candidates: options?.length ? { type: "static", values: options } : undefined
  };
}


function schemaFieldsToForm(schema: any): InputFormField[] {
  if (!schema || schema.type !== "object" || !schema.properties) return [];
  const required = new Set<string>(schema.required || []);
  return Object.entries(schema.properties).map(([name, raw]: [string, any]) => {
    const type = Array.isArray(raw?.type) ? raw.type.find((t: string) => t !== "null") : raw?.type;
    const widget: InputFormField["widget"] =
      type === "number" || type === "integer" ? "number" :
      type === "boolean" ? "boolean" :
      type === "array" ? "multiselect" :
      type === "object" ? "json" :
      Array.isArray(raw?.enum) ? "select" : "text";
    return {
      path: `$.${name}`,
      label: raw?.title || name,
      required: required.has(name),
      widget,
      candidates: Array.isArray(raw?.enum)
        ? { type: "static", values: raw.enum.map((value: unknown) => ({ value, label: String(value) })) }
        : undefined
    };
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
  const network = events.filter((e): e is NetworkEvidence => e.kind === "network" && Boolean(e.response));
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

    for (const event of group) {
      inputSchema = mergeSchemas(inputSchema, schemaFromValue(requestInput(event)));
      outputSchema = mergeSchemas(outputSchema, schemaFromValue(event.response?.body));
      const correlated = event.correlatedUiEvidenceId ? uiById.get(event.correlatedUiEvidenceId) : undefined;
      for (const field of correlated?.form || []) {
        const mapped = uiFieldToForm(field);
        if (mapped) forms.set(mapped.path, mapped);
      }
      if (correlated?.name) {
        const observed = correlated.options?.length
          ? correlated.options.map(o => ({ value: o.value, label: o.label || o.value }))
          : correlated.visibleOptions?.length
            ? correlated.visibleOptions.map(label => ({ value: label, label }))
            : undefined;
        const path = `$.${correlated.name}`;
        const existing = forms.get(path);
        forms.set(path, {
          path,
          label: correlated.label || existing?.label || correlated.name,
          required: existing?.required || false,
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
      title: ui?.text || ui?.label || `${operation} ${normalized.pathTemplate}`,
      description: `Observed ${operation} operation via ${method} ${normalized.pathTemplate}. Edit this business description after review.`,
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
        for (const field of inferred) if (!forms.has(field.path)) forms.set(field.path, field);
        return [...forms.values()];
      })(),
      evidence,
      sideEffect: ["create", "update", "review", "delete"].includes(operation),
      confirmation: {
        required: ["create", "update", "review", "delete"].includes(operation),
        reason: ["create", "update", "review", "delete"].includes(operation)
          ? `${operation} changes business data`
          : undefined
      },
      completion: inferCompletion(group, operation),
      bindings: [],
      validation: {
        status: "candidate",
        checks: []
      },
      generated: {
        source: "heuristic",
        generatedAt: new Date().toISOString()
      }
    });
  }

  return result;
}
