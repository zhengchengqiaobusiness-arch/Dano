export type OperationKind = "query" | "create" | "update" | "review" | "delete" | "authenticate" | "upload" | "download" | "action" | "unknown";

export type JsonSchema = {
  type?: string | string[];
  title?: string;
  description?: string;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  items?: JsonSchema;
  enum?: unknown[];
  additionalProperties?: boolean;
  [key: string]: unknown;
};

export interface UiFieldSnapshot {
  name?: string;
  label?: string;
  type?: string;
  value?: unknown;
  required?: boolean;
  options?: Array<{ value: string; label: string }>;
}

export interface UiEvidence {
  id: string;
  kind: "ui";
  sessionId: string;
  at: string;
  pageUrl: string;
  eventType: "click" | "input" | "change" | "submit";
  selector?: string;
  tag?: string;
  role?: string;
  text?: string;
  label?: string;
  name?: string;
  inputType?: string;
  value?: unknown;
  options?: Array<{ value: string; label: string }>;
  visibleOptions?: string[];
  form?: UiFieldSnapshot[];
}

export interface NetworkEvidence {
  id: string;
  kind: "network";
  sessionId: string;
  at: string;
  pageUrl?: string;
  correlatedUiEvidenceId?: string;
  request: {
    method: string;
    url: string;
    resourceType: string;
    headers: Record<string, string>;
    query: Record<string, string | string[]>;
    body?: unknown;
  };
  response?: {
    status: number;
    headers: Record<string, string>;
    body?: unknown;
    truncated?: boolean;
  };
  failure?: string;
}

export type EvidenceEvent = UiEvidence | NetworkEvidence;

export interface CandidateRuleStatic {
  type: "static";
  values: Array<{ value: unknown; label: string }>;
}

export interface CandidateRuleCapability {
  type: "capability";
  capabilityId: string;
  valuePath: string;
  labelPath: string;
  dependsOn?: string[];
}

export type CandidateRule = CandidateRuleStatic | CandidateRuleCapability;

export type FieldSource =
  | "caller"
  | "fixed"
  | "session"
  | "generated"
  | "computed"
  | "binding"
  | "system";

export type FieldRequiredBasis = "ui-required" | "observed-always" | "not-observed" | "manual";

export interface InputFormField {
  path: string;
  name: string;
  label: string;
  valueType: "string" | "number" | "integer" | "boolean" | "array" | "object" | "unknown";
  source: FieldSource;
  required: boolean;
  requiredBasis: FieldRequiredBasis;
  systemHandled: boolean;
  sourceDetail: string;
  widget: "text" | "number" | "boolean" | "select" | "multiselect" | "json";
  defaultRule?: string;
  candidates?: CandidateRule;
}

export interface CapabilityEvidenceRef {
  eventId: string;
  sessionId: string;
  kind: "ui" | "network";
  at: string;
  status?: number;
}

export interface DataBinding {
  id: string;
  fromCapabilityId: string;
  fromPath: string;
  toPath: string;
  confidence: number;
  evidenceIds: string[];
  approved: boolean;
  approvalSource?: "human" | "evidence";
  approvedAt?: string;
  note?: string;
}

export interface CapabilityContract {
  id: string;
  kind?: "atomic";
  title: string;
  description: string;
  operation: OperationKind;
  confidence: number;
  transport: {
    method: string;
    urlTemplate: string;
    origin: string;
    pathTemplate: string;
  };
  inputSchema: JsonSchema;
  outputSchema: JsonSchema;
  inputForm: InputFormField[];
  evidence: CapabilityEvidenceRef[];
  sideEffect: boolean;
  confirmation: {
    required: boolean;
    reason?: string;
  };
  completion: {
    acceptedHttpStatuses: number[];
    requiredOutputPaths?: string[];
    assertions?: Array<{
      path: string;
      kind: "exists" | "nonempty" | "equals";
      value?: unknown;
    }>;
    note?: string;
  };
  bindings: DataBinding[];
  validation: {
    version?: number;
    status: "candidate" | "verified" | "rejected";
    checks: Array<{ name: string; ok: boolean; detail: string }>;
    verifiedAt?: string;
  };
  generated: {
    source: "heuristic" | "openai";
    model?: string;
    generatedAt: string;
  };
  editing?: {
    title: "generated" | "manual";
    description: "generated" | "manual";
    operation?: "generated" | "manual";
    fields: "generated" | "manual";
    fieldPaths?: string[];
    updatedAt?: string;
  };
}

export interface CapabilityRouteStep {
  order: number;
  capabilityId: string;
  bindingIds: string[];
}

export interface CapabilityRoute {
  id: string;
  title: string;
  targetCapabilityId: string;
  steps: CapabilityRouteStep[];
  approvedBindingIds: string[];
  stopConditions: string[];
  completion: string;
}

export type SkillLifecycleStatus = "active" | "frozen" | "deleted";

export interface SkillRecord {
  id: string;
  name: string;
  displayName: string;
  directory: string;
  version: number;
  status: SkillLifecycleStatus;
  capabilityIds: string[];
  routeIds: string[];
  exportedAt: string;
  updatedAt: string;
  frozenAt?: string;
  deletedAt?: string;
  recoverableFrom?: string;
}

export interface PlanStep {
  capabilityId: string;
  input: Record<string, unknown>;
  bindings: Array<{
    fromStep: number;
    fromPath: string;
    toPath: string;
  }>;
  reason: string;
}

export interface ExecutionPlan {
  goal: string;
  steps: PlanStep[];
  needsUserQuestion: boolean;
  question?: string;
  completion: string;
}

export interface RecordingSession {
  id: string;
  name: string;
  startedAt: string;
  stoppedAt?: string;
  startUrl: string;
  eventsFile: string;
}
