import type { CapabilityContract } from "../domain.js";
import { catalogTransportKey } from "../catalog/normalize.js";
import { isPrimaryCapability } from "./export-scope.js";

const WRITE_OPERATIONS = new Set(["create", "update", "review", "delete", "upload", "action"]);

export function reanalyzeIncoming(incoming: CapabilityContract[], existing: CapabilityContract[]): CapabilityContract[] {
  const existingByTransport = new Map(existing.map(capability => [catalogTransportKey(capability), capability]));
  const sessionPrimaryKeys = new Set(
    incoming.filter(capability => isPrimaryCapability(capability, incoming)).map(catalogTransportKey)
  );
  return incoming.map(candidate => {
    const old = existingByTransport.get(catalogTransportKey(candidate));
    if (!old) return candidate;
    const operation = old.editing?.operation === "manual" ? old.operation : candidate.operation;
    const sideEffect = WRITE_OPERATIONS.has(operation);
    const manualPaths = new Set(old.editing?.fieldPaths || []);
    const inputForm = candidate.inputForm.map(field => {
      const previous = old.inputForm.find(item => item.path === field.path);
      return previous && manualPaths.has(field.path) ? { ...previous } : { ...field };
    });
    for (const binding of old.bindings.filter(item => item.approved)) {
      const field = inputForm.find(item => item.path === binding.toPath);
      if (field) {
        field.source = "binding";
        field.systemHandled = true;
        field.sourceDetail = `由已确认绑定从 ${binding.fromCapabilityId}${binding.fromPath} 提供`;
        field.defaultRule = undefined;
      }
    }
    const keepVerified = old.validation.status === "verified" && !sessionPrimaryKeys.has(catalogTransportKey(candidate));
    return {
      ...candidate,
      id: old.editing?.operation === "manual" ? old.id : candidate.id,
      title: old.editing?.title === "manual" ? old.title : candidate.title,
      description: old.editing?.description === "manual" ? old.description : candidate.description,
      operation,
      sideEffect,
      confirmation: {
        required: sideEffect,
        reason: sideEffect ? "该操作会改变业务或文件数据" : undefined
      },
      inputForm,
      bindings: old.bindings || [],
      validation: keepVerified
        ? old.validation
        : { version: 2, status: "candidate", checks: [{ name: "reanalyze", ok: false, detail: "录制证据已重新分析，需要再次验证" }] },
      editing: old.editing || candidate.editing
    };
  });
}
