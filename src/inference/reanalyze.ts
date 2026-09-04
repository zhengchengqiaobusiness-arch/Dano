import type { CapabilityContract, CapabilityEvidenceRef, InputFormField } from "../domain.js";
import { catalogTransportKey } from "../catalog/normalize.js";
import { isPrimaryCapability } from "./export-scope.js";

const WRITE_OPERATIONS = new Set(["create", "update", "review", "delete", "upload", "action"]);

function generatedIdFitsOperation(id: string, operation: CapabilityContract["operation"]) {
  if (operation === "query") return /^(query|ask)-/.test(id);
  return id.startsWith(`${operation}-`);
}

function mergeEvidence(previous: CapabilityEvidenceRef[] = [], incoming: CapabilityEvidenceRef[] = []) {
  const seen = new Set(previous.map(item => item.eventId));
  return [...previous, ...incoming.filter(item => !seen.has(item.eventId))];
}

function mergeVerifiedForm(previous: InputFormField[], incoming: InputFormField[]) {
  const byPath = new Map(previous.map(field => [field.path, { ...field }]));
  for (const field of incoming) if (!byPath.has(field.path)) byPath.set(field.path, { ...field });
  return [...byPath.values()];
}

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => [key, canonical(item)])
  );
}

function sameExecutionContract(
  old: CapabilityContract,
  candidate: CapabilityContract,
  inputForm: InputFormField[],
  bindings: CapabilityContract["bindings"],
  operation: CapabilityContract["operation"]
) {
  const shape = (capability: CapabilityContract, fields: InputFormField[], nextBindings: CapabilityContract["bindings"], nextOperation: CapabilityContract["operation"]) => ({
    operation: nextOperation,
    transport: capability.transport,
    inputSchema: capability.inputSchema,
    outputSchema: capability.outputSchema,
    inputForm: fields,
    bindings: nextBindings.map(binding => ({
      fromCapabilityId: binding.fromCapabilityId,
      fromPath: binding.fromPath,
      toPath: binding.toPath,
      approved: binding.approved,
      approvalSource: binding.approvalSource
    })),
    completion: capability.completion
  });
  return JSON.stringify(canonical(shape(old, old.inputForm, old.bindings || [], old.operation)))
    === JSON.stringify(canonical(shape(candidate, inputForm, bindings, operation)));
}

function additiveOptionalLookupChange(
  old: CapabilityContract,
  candidate: CapabilityContract,
  candidateForm: InputFormField[],
  bindings: CapabilityContract["bindings"],
  operation: CapabilityContract["operation"]
) {
  if (operation !== "query" || old.operation !== operation) return false;
  if (JSON.stringify(canonical(old.transport)) !== JSON.stringify(canonical(candidate.transport))) return false;
  if (JSON.stringify(canonical(old.completion)) !== JSON.stringify(canonical(candidate.completion))) return false;
  const oldByPath = new Map(old.inputForm.map(field => [field.path, field]));
  for (const field of candidateForm) {
    const previous = oldByPath.get(field.path);
    if (!previous) {
      if (field.required || field.source !== "caller" || field.defaultRule) return false;
      continue;
    }
    if (JSON.stringify(canonical(previous)) !== JSON.stringify(canonical(field))) return false;
  }
  const route = (items: CapabilityContract["bindings"]) => items.map(binding => ({
    fromCapabilityId: binding.fromCapabilityId,
    fromPath: binding.fromPath,
    toPath: binding.toPath,
    approved: binding.approved,
    approvalSource: binding.approvalSource
  }));
  return JSON.stringify(canonical(route(old.bindings || []))) === JSON.stringify(canonical(route(bindings)));
}

function applyHumanBindings(inputForm: InputFormField[], bindings: CapabilityContract["bindings"]) {
  for (const binding of bindings.filter(item => item.approved && item.approvalSource === "human")) {
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

function mergeReanalyzedBindings(
  candidate: CapabilityContract,
  old: CapabilityContract
) {
  const valid = (binding: CapabilityContract["bindings"][number]) =>
    binding.fromCapabilityId !== old.id && binding.fromCapabilityId !== candidate.id;
  const oldBindings = (old.bindings || []).filter(valid);
  const human = oldBindings.filter(binding => binding.approvalSource === "human");
  const humanTargets = new Set(human.map(binding => binding.toPath));
  const fresh = (candidate.bindings || []).filter(binding => valid(binding) && !humanTargets.has(binding.toPath));
  const byRoute = new Map<string, CapabilityContract["bindings"][number]>();
  for (const binding of [...fresh, ...human]) {
    byRoute.set(`${binding.fromCapabilityId}|${binding.fromPath}|${binding.toPath}`, binding);
  }
  return [...byRoute.values()];
}

export function reanalyzeIncoming(incoming: CapabilityContract[], existing: CapabilityContract[]): CapabilityContract[] {
  const existingByTransport = new Map(existing.map(capability => [catalogTransportKey(capability), capability]));
  const sessionPrimaryKeys = new Set(
    incoming.filter(capability => isPrimaryCapability(capability, incoming)).map(catalogTransportKey)
  );
  return incoming.map(candidate => {
    const old = existingByTransport.get(catalogTransportKey(candidate));
    if (!old) return candidate;
    const bindings = mergeReanalyzedBindings(candidate, old);
    const operation = old.editing?.operation === "manual" ? old.operation : candidate.operation;
    const sideEffect = WRITE_OPERATIONS.has(operation);
    const manualPaths = new Set(old.editing?.fieldPaths || []);
    const candidateForm =
      candidate.inputForm.map(field => {
        const previous = old.inputForm.find(item => item.path === field.path);
        return previous && manualPaths.has(field.path) ? { ...previous } : { ...field };
      });
    const keepVerified = !sessionPrimaryKeys.has(catalogTransportKey(candidate))
      && old.validation.status === "verified"
      && (sameExecutionContract(old, candidate, candidateForm, bindings, operation)
        || additiveOptionalLookupChange(old, candidate, candidateForm, bindings, operation));
    const inputForm = applyHumanBindings(
      keepVerified ? mergeVerifiedForm(old.inputForm, candidateForm) : candidateForm,
      bindings
    );
    return {
      ...(keepVerified ? old : candidate),
      id: old.editing?.operation === "manual" || generatedIdFitsOperation(old.id, operation) ? old.id : candidate.id,
      title: old.editing?.title === "manual" ? old.title : candidate.title,
      description: old.editing?.description === "manual" ? old.description : candidate.description,
      operation,
      sideEffect,
      confirmation: {
        required: sideEffect,
        reason: sideEffect ? "该操作会改变业务或文件数据" : undefined
      },
      inputForm,
      evidence: keepVerified ? mergeEvidence(old.evidence, candidate.evidence) : candidate.evidence,
      bindings,
      validation: keepVerified
        ? old.validation
        : { version: 2, status: "candidate", checks: [{ name: "reanalyze", ok: false, detail: "录制证据已重新分析，需要再次验证" }] },
      editing: old.editing || candidate.editing
    };
  });
}
