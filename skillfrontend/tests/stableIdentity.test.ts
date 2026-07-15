import assert from "node:assert/strict";
import test from "node:test";
import * as identityHelpers from "../src/features/recording-v3/state/stableIdentity.ts";

import {
  stableCapabilityRef,
  stableFieldKey,
  stableIssueKey,
  stableRelationKey,
} from "../src/features/recording-v3/state/stableIdentity.ts";

test("step render identities use step_uuid instead of mutable step_id", () => {
  const stableStepRef = (identityHelpers as Record<string, unknown>).stableStepRef as
    | ((step: Record<string, unknown>) => string)
    | undefined;
  assert.equal(typeof stableStepRef, "function");
  assert.equal(
    stableStepRef?.({ step_uuid: "step-uuid", step_id: "old-step-id" }),
    stableStepRef?.({ step_uuid: "step-uuid", step_id: "new-step-id" }),
  );
});

test("V3 identities prefer immutable UUIDs and ignore mutable labels", () => {
  assert.equal(
    stableFieldKey("legacy-step", { field_uuid: "field-uuid", path: "old.path", key: "old" }),
    stableFieldKey("renamed-step", { field_uuid: "field-uuid", path: "new.path", key: "new" }),
  );
  assert.equal(
    stableCapabilityRef({ capability_uuid: "capability-uuid", name: "old-name" }),
    stableCapabilityRef({ capability_uuid: "capability-uuid", name: "new-name" }),
  );
  assert.equal(
    stableRelationKey({ relation_id: "relation-uuid", from_capability: "old-name" }),
    stableRelationKey({ relation_id: "relation-uuid", from_capability: "new-name" }),
  );
  assert.equal(
    stableIssueKey("contract", { id: "issue-uuid", message: "old message" }, "old target"),
    stableIssueKey("contract", { id: "issue-uuid", message: "new message" }, "new target"),
  );
});

test("missing V3 UUIDs receive opaque keys that survive immutable object spreads", () => {
  const field = { path: "body.mutable", key: "mutable" };
  const fieldKey = stableFieldKey("mutable-step", field);
  assert.equal(stableFieldKey("renamed-step", { ...field, path: "body.renamed", key: "renamed" }), fieldKey);
  assert.doesNotMatch(fieldKey, /mutable|body|step/i);
  assert.notEqual(stableFieldKey("mutable-step", { path: field.path }), fieldKey);

  const capability = { name: "mutable-capability", title: "Mutable title" };
  const capabilityKey = stableCapabilityRef(capability);
  assert.equal(stableCapabilityRef({ ...capability, name: "renamed" }), capabilityKey);
  assert.doesNotMatch(capabilityKey, /mutable|title/i);
  assert.notEqual(stableCapabilityRef({ name: capability.name }), capabilityKey);

  const relation = { from_capability: "mutable-source", to_capability: "mutable-target" };
  const relationKey = stableRelationKey(relation);
  assert.equal(stableRelationKey({ ...relation, from_capability: "renamed-source" }), relationKey);
  assert.doesNotMatch(relationKey, /mutable|source|target/i);
  assert.notEqual(stableRelationKey({ from_capability: relation.from_capability, to_capability: relation.to_capability }), relationKey);

  const issue = { source: "mutable-source", message: "mutable message" };
  const issueKey = stableIssueKey("mutable-group", issue, "mutable target");
  assert.equal(stableIssueKey("renamed-group", { ...issue, message: "renamed" }, "renamed target"), issueKey);
  assert.doesNotMatch(issueKey, /mutable|message|source|target|group/i);
  assert.notEqual(stableIssueKey("mutable-group", { source: issue.source, message: issue.message }, "mutable target"), issueKey);
});

test("request, mapping and precondition identities never use mutable content or indexes", () => {
  const helpers = identityHelpers as Record<string, unknown>;
  const stableRequestKey = helpers.stableRequestKey as
    | ((request: Record<string, unknown>) => string)
    | undefined;
  const stableMappingKey = helpers.stableMappingKey as
    | ((mapping: Record<string, unknown>) => string)
    | undefined;
  const stablePreconditionKey = helpers.stablePreconditionKey as
    | ((precondition: Record<string, unknown>) => string)
    | undefined;
  assert.equal(typeof stableRequestKey, "function");
  assert.equal(typeof stableMappingKey, "function");
  assert.equal(typeof stablePreconditionKey, "function");

  assert.equal(
    stableRequestKey?.({ request_id: "request-uuid", request_index: 1, path: "/old" }),
    stableRequestKey?.({ request_id: "request-uuid", request_index: 999, path: "/new" }),
  );
  assert.notEqual(
    stableRequestKey?.({ observation_id: "observation-uuid" }),
    stableRequestKey?.({ request_definition_id: "observation-uuid" }),
  );

  const stableRequestMutationTarget = helpers.stableRequestMutationTarget as
    | ((request: Record<string, unknown>) => Record<string, string> | null)
    | undefined;
  assert.equal(typeof stableRequestMutationTarget, "function");
  assert.deepEqual(
    stableRequestMutationTarget?.({
      request_definition_id: "definition-uuid",
      observation_id: "observation-uuid",
      request_id: "legacy-request-id",
      request_index: 999,
    }),
    { request_definition_id: "definition-uuid" },
  );
  assert.deepEqual(
    stableRequestMutationTarget?.({ observation_id: "observation-uuid", request_index: 1 }),
    { observation_id: "observation-uuid" },
  );
  assert.deepEqual(
    stableRequestMutationTarget?.({ request_id: "request-id", request_index: 1 }),
    { request_id: "request-id" },
  );
  assert.equal(stableRequestMutationTarget?.({ request_index: 1 }), null);

  const request = { request_index: 7, method: "POST", path: "/mutable" };
  const requestKey = stableRequestKey?.(request);
  assert.equal(stableRequestKey?.({ ...request, request_index: 8, path: "/renamed" }), requestKey);
  assert.doesNotMatch(String(requestKey), /idx:[78]|mutable|renamed|post/i);

  const mapping = { source: "mutable.source", target: "mutable.target" };
  const mappingKey = stableMappingKey?.(mapping);
  assert.equal(stableMappingKey?.({ ...mapping, source: "renamed" }), mappingKey);
  assert.doesNotMatch(String(mappingKey), /mutable|source|target|renamed/i);

  const precondition = { expression: "mutable == true", label: "mutable label" };
  const preconditionKey = stablePreconditionKey?.(precondition);
  assert.equal(stablePreconditionKey?.({ ...precondition, expression: "renamed" }), preconditionKey);
  assert.doesNotMatch(String(preconditionKey), /mutable|expression|label|renamed/i);
});
