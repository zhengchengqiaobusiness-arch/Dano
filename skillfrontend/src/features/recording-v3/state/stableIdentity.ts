const OPAQUE_V3_ID = Symbol("dano.recording-v3.opaque-id");
const frozenOpaqueIds = new WeakMap<object, string>();

interface OpaqueIdentityCarrier {
  [OPAQUE_V3_ID]?: string;
}

function newOpaqueId(kind: string): string {
  const value = typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `missing-${kind}-uuid:${value}`;
}

/**
 * V3 projections are required to provide permanent UUIDs. A missing UUID is a
 * data-contract fault, never permission to reuse a mutable label/path/index as
 * React identity. The enumerable symbol survives ordinary immutable spreads,
 * while JSON and wire payloads remain untouched.
 */
export function opaqueV3Identity(value: object, kind: string): string {
  const carrier = value as OpaqueIdentityCarrier;
  if (carrier[OPAQUE_V3_ID]) return carrier[OPAQUE_V3_ID]!;
  const frozen = frozenOpaqueIds.get(value);
  if (frozen) return frozen;
  const identity = newOpaqueId(kind);
  if (Object.isExtensible(value)) {
    Object.defineProperty(value, OPAQUE_V3_ID, {
      value: identity,
      enumerable: true,
      configurable: false,
      writable: false,
    });
  } else {
    frozenOpaqueIds.set(value, identity);
  }
  return identity;
}

export interface StableFieldIdentity {
  field_uuid?: string;
  field_id?: string;
  lineage_id?: string;
  path?: string;
  key?: string;
  label?: string;
}

export interface StableStepIdentity {
  step_uuid?: string;
  step_id?: string;
}

export interface StableCapabilityIdentity {
  capability_uuid?: string;
  capability_id?: string;
  name?: string;
  title?: string;
  intent?: string;
  kind?: string;
}

export interface StableRelationIdentity {
  relation_uuid?: string;
  relation_id?: string;
  from_capability?: string;
  from_output?: string;
  to_capability?: string;
  to_input?: string;
  type?: string;
  mode?: string;
}

export interface StableRequestIdentity {
  request_id?: string;
  observation_id?: string;
  request_definition_id?: string;
  request_index?: number | string | null;
  method?: string;
  path?: string;
  url?: string;
}

export interface StableMappingIdentity {
  mapping_uuid?: string;
}

export interface StablePreconditionIdentity {
  precondition_uuid?: string;
}

export function stableFieldKey(_step: string | StableStepIdentity, field: StableFieldIdentity): string {
  return field.field_uuid || opaqueV3Identity(field, "field");
}

export function stableStepRef(step: StableStepIdentity): string {
  return step.step_uuid || opaqueV3Identity(step, "step");
}

export function stableCapabilityRef(capability: StableCapabilityIdentity): string {
  return capability.capability_uuid || opaqueV3Identity(capability, "capability");
}

export function capabilityAnchorId(capability: StableCapabilityIdentity): string {
  return `capability-${stableCapabilityRef(capability).replace(/[^a-zA-Z0-9_-]+/g, "-")}`;
}

export function stableRelationKey(relation: StableRelationIdentity): string {
  return relation.relation_uuid || relation.relation_id || opaqueV3Identity(relation, "relation");
}

export function stableRequestKey(request: StableRequestIdentity): string {
  if (request.request_id) return `request-id:${request.request_id}`;
  if (request.observation_id) return `observation-id:${request.observation_id}`;
  if (request.request_definition_id) return `request-definition-id:${request.request_definition_id}`;
  return opaqueV3Identity(request, "request");
}

/**
 * Build a request mutation reference from immutable identity only. In V3 the
 * numeric request index is presentation state; sending it on a write is a
 * contract error and can also target a different observation after recapture.
 */
export function stableRequestMutationTarget(
  request: StableRequestIdentity,
): { request_definition_id: string } | { observation_id: string } | { request_id: string } | null {
  if (request.request_definition_id) {
    return { request_definition_id: String(request.request_definition_id) };
  }
  if (request.observation_id) return { observation_id: String(request.observation_id) };
  if (request.request_id) return { request_id: String(request.request_id) };
  return null;
}

export function stableMappingKey(mapping: StableMappingIdentity): string {
  return mapping.mapping_uuid || opaqueV3Identity(mapping, "mapping");
}

export function stablePreconditionKey(precondition: StablePreconditionIdentity): string {
  return precondition.precondition_uuid || opaqueV3Identity(precondition, "precondition");
}

export function stableIssueKey(
  _group: string,
  issue: { id?: string; issue_id?: string; fingerprint?: string; source?: string; message?: string; target?: Record<string, unknown> },
  _targetLabel: string,
): string {
  return issue.issue_id || issue.id || issue.fingerprint || opaqueV3Identity(issue, "issue");
}
