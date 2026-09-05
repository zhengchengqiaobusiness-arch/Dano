import type { CapabilityContract, InputFormField, JsonSchema } from "../domain.js";

function fieldName(path: string) {
  return path.replace(/^\$\.?/, "").split(".").filter(Boolean).at(-1) || path;
}

function schemaAtPath(schema: JsonSchema, fieldPath: string): JsonSchema | undefined {
  const parts = fieldPath.replace(/^\$\.?/, "").split(".").filter(Boolean);
  let current: JsonSchema | undefined = schema;
  for (const part of parts) current = current?.properties?.[part];
  return current;
}

function valueType(schema: JsonSchema | undefined): InputFormField["valueType"] {
  const raw = Array.isArray(schema?.type) ? schema.type.find(type => type !== "null") : schema?.type;
  return ["string", "number", "integer", "boolean", "array", "object"].includes(String(raw))
    ? raw as InputFormField["valueType"]
    : "unknown";
}

export function normalizeField(field: Partial<InputFormField> & Pick<InputFormField, "path" | "label" | "required" | "widget">, schema?: JsonSchema): InputFormField {
  const source = field.source || "system";
  return {
    path: field.path,
    name: field.name || fieldName(field.path),
    label: field.label,
    valueType: field.valueType || valueType(schema),
    source,
    required: Boolean(field.required),
    requiredBasis: field.requiredBasis || (field.required ? "observed-always" : "not-observed"),
    systemHandled: field.systemHandled ?? source !== "caller",
    sourceDetail: field.sourceDetail || (source === "caller"
      ? "由调用方提供"
      : "未记录到用户输入来源，按系统处理字段管理"),
    widget: field.widget,
    defaultRule: field.defaultRule,
    dateClock: field.dateClock,
    dateClocks: field.dateClocks,
    candidates: field.candidates
  };
}

export function normalizeCapability(capability: CapabilityContract): CapabilityContract {
  const descriptionLooksGenerated = /^Observed\s|^已从真实操作观察到/.test(capability.description || "");
  const editing = capability.editing
    ? {
        ...capability.editing,
        fieldPaths: capability.editing.fieldPaths || (capability.editing.fields === "manual" ? capability.inputForm.map(field => field.path) : undefined)
      }
    : {
        title: descriptionLooksGenerated ? "generated" as const : "manual" as const,
        description: descriptionLooksGenerated ? "generated" as const : "manual" as const,
        operation: "generated" as const,
        fields: "generated" as const
      };
  const validation = capability.validation?.version === 2
    ? capability.validation
    : {
        version: 2,
        status: "candidate" as const,
        checks: [{ name: "validation-version", ok: false, detail: "能力需要按当前字段与绑定规则重新验证" }]
      };
  return {
    ...capability,
    kind: "atomic",
    inputForm: (capability.inputForm || []).map(field => normalizeField(field, schemaAtPath(capability.inputSchema, field.path))),
    bindings: capability.bindings || [],
    validation,
    editing
  };
}

export function normalizeCatalog(capabilities: CapabilityContract[]) {
  return capabilities.map(normalizeCapability);
}

export function catalogTransportKey(capability: CapabilityContract) {
  return `${capability.transport.method}|${capability.transport.pathTemplate}`;
}

export function mergeCatalogByTransport(incoming: CapabilityContract[], existing: CapabilityContract[]) {
  const incomingKeys = new Set(incoming.map(catalogTransportKey));
  return [...incoming, ...existing.filter(item => !incomingKeys.has(catalogTransportKey(item)))];
}
