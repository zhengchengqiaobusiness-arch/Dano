import type { CapabilityContract, CapabilityEvidenceRef, InputFormField } from "../domain.js";
import { catalogTransportKey } from "../catalog/normalize.js";
import { isPrimaryCapability } from "./export-scope.js";

const WRITE_OPERATIONS = new Set(["create", "update", "review", "delete", "upload", "action"]);

function mergeEvidence(previous: CapabilityEvidenceRef[] = [], incoming: CapabilityEvidenceRef[] = []) {
  const seen = new Set(previous.map(item => item.eventId));
  return [...previous, ...incoming.filter(item => !seen.has(item.eventId))];
}

function mergeVerifiedForm(previous: InputFormField[], incoming: InputFormField[]) {
  const byPath = new Map(previous.map(field => [field.path, { ...field }]));
  for (const field of incoming) {
    if (!byPath.has(field.path)) byPath.set(field.path, { ...field });
  }
  return [...byPath.values()];
}

function applyApprovedBindings(inputForm: InputFormField[], bindings: CapabilityContract["bindings"]) {
  for (const binding of bindings.filter(item => item.approved)) {
    const field = inputForm.find(item => item.path === binding.toPath);
    if (field) {
      field.source = "binding";
      field.systemHandled = true;
      field.sourceDetail = `由已确认绑定从 ${binding.fromCapabilityId}${binding.fromPath} 提供`;
      field.defaultRule = `from:${binding.fromCapabilityId}:${binding.fromPath}`;
    }
  }
  return inputForm;
}

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
    const keepVerified = old.validation.status === "verified" && !sessionPrimaryKeys.has(catalogTransportKey(candidate));
    const inputForm = applyApprovedBindings(
      keepVerified
        ? mergeVerifiedForm(old.inputForm, candidate.inputForm)
        : candidate.inputForm.map(field => {
          const previous = old.inputForm.find(item => item.path === field.path);
          return previous && manualPaths.has(field.path) ? { ...previous } : { ...field };
        }),
      old.bindings || []
    );
    if (keepVerified) {
      return {
        ...old,
        title: old.editing?.title === "manual" ? old.title : candidate.title,
        description: old.editing?.description === "manual" ? old.description : candidate.description,
        inputForm,
        evidence: mergeEvidence(old.evidence, candidate.evidence),
        bindings: old.bindings || [],
        validation: old.validation,
        editing: old.editing || candidate.editing
      };
    }
    return {
      ...candidate,
      id: old.id,
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
      validation: { version: 2, status: "candidate", checks: [{ name: "reanalyze", ok: false, detail: "录制证据已重新分析，需要再次验证" }] },
      editing: old.editing || candidate.editing
    };
  });
}
