export const RESTORE_AUTOMATIC_VALUE = "__dano_restore_automatic__";

export type JsonRecord = Record<string, any>;

/** Generate a wire-valid v4 UUID even when randomUUID is unavailable. */
export function newWorkbenchUuid(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  const bytes = new Uint8Array(16);
  if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
    crypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0"));
  return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex.slice(6, 8).join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10).join("")}`;
}

export function normalizeV3CapabilityKind(value: unknown): unknown {
  return value === "submit_batch" ? "submit" : value;
}

function normalizeCapabilityReference(value: unknown): unknown {
  return value === "submit_batch" ? "submit" : value;
}

/**
 * Legacy V3 snapshots may still contain the removed synthetic submit_batch
 * operation.  Normalize only capability contract fields at the workbench
 * ingress; request/step identities and arbitrary business values are untouched.
 */
export function normalizeV3FlowSpecSnapshot<T extends JsonRecord>(snapshot: T): T {
  let changed = false;
  const next: JsonRecord = { ...snapshot };

  const action = normalizeV3CapabilityKind(snapshot.action);
  if (action !== snapshot.action) {
    next.action = action;
    changed = true;
  }

  if (Array.isArray(snapshot.capabilities)) {
    next.capabilities = snapshot.capabilities.map((capability: JsonRecord) => {
      if (!capability || typeof capability !== "object") return capability;
      const kind = normalizeV3CapabilityKind(capability.kind);
      const name = normalizeCapabilityReference(capability.name);
      if (kind === capability.kind && name === capability.name) return capability;
      changed = true;
      return { ...capability, kind, name };
    });
  }

  if (Array.isArray(snapshot.capability_relations)) {
    next.capability_relations = snapshot.capability_relations.map((relation: JsonRecord) => {
      if (!relation || typeof relation !== "object") return relation;
      const fromCapability = normalizeCapabilityReference(relation.from_capability);
      const toCapability = normalizeCapabilityReference(relation.to_capability);
      if (fromCapability === relation.from_capability && toCapability === relation.to_capability) return relation;
      changed = true;
      return { ...relation, from_capability: fromCapability, to_capability: toCapability };
    });
  }

  if (snapshot.goal && typeof snapshot.goal === "object" && Array.isArray(snapshot.goal.capabilities)) {
    const capabilities = snapshot.goal.capabilities.map(normalizeCapabilityReference);
    if (capabilities.some((value: unknown, index: number) => value !== snapshot.goal.capabilities[index])) {
      next.goal = { ...snapshot.goal, capabilities };
      changed = true;
    }
  }

  return (changed ? next : snapshot) as T;
}

export interface SourceAxisField {
  key?: string;
  path?: string;
  value?: unknown;
  default_value?: unknown;
  source_request_definition_id?: string;
  source_request_id?: string;
  response_path?: string;
}

export interface SourceAxisProjection {
  sourceBinding: JsonRecord;
  needsConfiguration: boolean;
}

/** Build the single canonical source_binding decision sent by the workbench. */
export function sourceBindingForWorkbench(
  sourceKind: string,
  field: SourceAxisField,
  source: JsonRecord = {},
): SourceAxisProjection {
  if (["user_input", "api_option", "manual_enum", "page_enum", "form_option", "static_enum"].includes(sourceKind)) {
    return { sourceBinding: { kind: "caller" }, needsConfiguration: false };
  }
  if (sourceKind === "previous_response") {
    const requestDefinitionId = source.request_definition_id
      || source.source_request_id
      || field.source_request_definition_id
      || field.source_request_id;
    const responsePath = source.response_path || source.source_path || field.response_path;
    if (requestDefinitionId && responsePath) {
      return {
        sourceBinding: {
          kind: "previous_response",
          request_definition_id: String(requestDefinitionId),
          response_path: String(responsePath),
        },
        needsConfiguration: false,
      };
    }
    return { sourceBinding: { kind: "unknown" }, needsConfiguration: true };
  }
  if (sourceKind === "constant") {
    const value = source.constant ?? source.value ?? field.default_value ?? field.value ?? "";
    return { sourceBinding: { kind: "constant", value }, needsConfiguration: false };
  }
  if (sourceKind === "computed") {
    const expression = source.expression || source.strategy;
    return expression
      ? { sourceBinding: { kind: "derived", expression: String(expression) }, needsConfiguration: false }
      : { sourceBinding: { kind: "unknown" }, needsConfiguration: true };
  }
  if (sourceKind === "request_header") {
    const header = String(source.header || "").trim();
    return {
      sourceBinding: {
        kind: "runtime_context",
        runtime_resolver: header ? `runtime_context.request_headers.${header}` : "runtime_context.request_headers",
      },
      needsConfiguration: !header,
    };
  }
  if (sourceKind === "current_user") {
    return {
      sourceBinding: { kind: "runtime_context", runtime_resolver: "runtime_context.current_user" },
      needsConfiguration: false,
    };
  }
  if (sourceKind === "system_time") {
    return {
      sourceBinding: { kind: "runtime_context", runtime_resolver: "runtime_context.system_time" },
      needsConfiguration: false,
    };
  }
  if (sourceKind === "system_generated") {
    const strategy = String(source.strategy || "uuid");
    return {
      sourceBinding: { kind: "runtime_context", runtime_resolver: `runtime_context.generated.${strategy}` },
      needsConfiguration: !["uuid", "random_string", "random_number"].includes(strategy),
    };
  }
  if (sourceKind === "page_context") {
    const contextKey = String(source.context_key || field.key || "page").trim() || "page";
    return {
      sourceBinding: { kind: "runtime_context", runtime_resolver: `runtime_context.${contextKey}` },
      needsConfiguration: !source.context_key,
    };
  }
  return { sourceBinding: { kind: "unknown" }, needsConfiguration: true };
}

export function issueKind(issue: JsonRecord): string {
  return String(issue.kind || issue.type || "").toLowerCase();
}

export function isContractFaultIssue(issue: JsonRecord): boolean {
  return issueKind(issue) === "contract_fault";
}

export function isAdvisoryIssue(issue: JsonRecord): boolean {
  return issueKind(issue) === "advisory";
}

/** A published result describes one frozen revision, never a later draft. */
export function mutationInvalidatesPublication(message: JsonRecord): boolean {
  return message?.type === "flow_update" || message?.type === "flow_replace";
}
