import type { JsonSchema } from "./domain.js";

export function schemaFromValue(value: unknown, depth = 0): JsonSchema {
  if (depth > 5) return {};
  if (value === null) return { type: ["null"] };
  if (Array.isArray(value)) {
    const nonNull = value.find(v => v !== null);
    return {
      type: "array",
      items: nonNull === undefined ? {} : schemaFromValue(nonNull, depth + 1)
    };
  }
  switch (typeof value) {
    case "string":
      return { type: "string" };
    case "number":
      return { type: Number.isInteger(value) ? "integer" : "number" };
    case "boolean":
      return { type: "boolean" };
    case "object": {
      const entries = Object.entries(value as Record<string, unknown>);
      return {
        type: "object",
        properties: Object.fromEntries(entries.map(([k, v]) => [k, schemaFromValue(v, depth + 1)])),
        required: entries.filter(([, v]) => v !== undefined && v !== null).map(([k]) => k),
        additionalProperties: true
      };
    }
    default:
      return {};
  }
}

export function mergeSchemas(a: JsonSchema, b: JsonSchema): JsonSchema {
  if (!a.type) return b;
  if (!b.type) return a;
  if (JSON.stringify(a.type) !== JSON.stringify(b.type)) return {};
  if (a.type === "object" && b.type === "object") {
    const keys = new Set([
      ...Object.keys(a.properties || {}),
      ...Object.keys(b.properties || {})
    ]);
    const properties: Record<string, JsonSchema> = {};
    for (const key of keys) {
      const av = a.properties?.[key];
      const bv = b.properties?.[key];
      properties[key] = av && bv ? mergeSchemas(av, bv) : (av || bv || {});
    }
    const reqA = new Set(a.required || []);
    const reqB = new Set(b.required || []);
    return {
      type: "object",
      properties,
      required: [...reqA].filter(k => reqB.has(k)),
      additionalProperties: true
    };
  }
  if (a.type === "array" && b.type === "array") {
    return { type: "array", items: mergeSchemas(a.items || {}, b.items || {}) };
  }
  return a;
}
