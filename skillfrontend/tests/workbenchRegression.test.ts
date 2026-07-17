import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  isAdvisoryIssue,
  isContractFaultIssue,
  mutationInvalidatesPublication,
  newWorkbenchUuid,
  normalizeV3FlowSpecSnapshot,
  sourceBindingForWorkbench,
} from "../src/features/recording-v3/state/workbenchContracts.ts";

const pageRecorder = readFileSync(new URL("../src/components/PageRecorder.tsx", import.meta.url), "utf8");

function functionBody(name: string, nextName: string): string {
  const start = pageRecorder.indexOf(`function ${name}(`);
  const end = pageRecorder.indexOf(`function ${nextName}(`, start + 1);
  assert.ok(start >= 0 && end > start, `${name} body not found`);
  return pageRecorder.slice(start, end);
}

test("type and classification edits own only their selected workbench axis", () => {
  const typeBody = functionBody("updateParamType", "updateParamCategory");
  assert.match(typeBody, /paramEdit\([^\n]*"business_type", value\)/);
  assert.doesNotMatch(typeBody, /enum_options|source_kind|source_binding|links|exposed_to_user/);

  const categoryBody = functionBody("updateParamCategory", "updateParamSourceKind");
  assert.match(categoryBody, /patchLocalParams\(stepId, p, \{ category \}\)/);
  assert.match(categoryBody, /paramEdit\(stepId, p, "category", category\)/);
  assert.doesNotMatch(categoryBody, /source_kind|source_binding|links|exposed_to_user|need_human_confirm/);
  assert.match(pageRecorder, /function categoryHasManualOverride[\s\S]{0,160}axisHasManualOverride\(p, "classification"\)/);
  assert.doesNotMatch(pageRecorder, /SOURCE_OPTIONS_BY_CATEGORY/);
  assert.doesNotMatch(pageRecorder, /与当前分类不一致|与分类不匹配/);
});

test("manual V3 entities always receive wire-valid UUIDs", () => {
  assert.match(newWorkbenchUuid(), /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  assert.equal((pageRecorder.match(/newWorkbenchUuid\(\)/g) || []).length, 4);
  assert.doesNotMatch(pageRecorder, /`(?:step|field|capability)-\$\{Date\.now\(\)\}/);
});

test("source edit is atomic and clears stale enum state when leaving or replacing its source", () => {
  const body = functionBody("updateParamSourceKind", "updateRuntimeSourceDetail");
  assert.match(body, /sourceAxisEdit\(stepId, p, sourceKind, nextSource\)/);
  assert.match(body, /leavingEnumSource/);
  assert.match(body, /replacingEnumSource/);
  assert.match(body, /clearsEnumProjection/);
  assert.match(body, /"enum_binding", null/);
  assert.match(body, /"enum_options", null/);
  assert.match(body, /"enum_value_map", null/);
  assert.doesNotMatch(body, /paramEdit\(stepId, p, "category"/);
});

test("canonical source decisions cover caller, dependency and runtime context", () => {
  assert.deepEqual(
    sourceBindingForWorkbench("user_input", { key: "status" }).sourceBinding,
    { kind: "caller" },
  );
  assert.deepEqual(
    sourceBindingForWorkbench("previous_response", { key: "id" }, {
      request_definition_id: "request-uuid",
      response_path: "records[0].id",
    }).sourceBinding,
    { kind: "previous_response", request_definition_id: "request-uuid", response_path: "records[0].id" },
  );
  assert.deepEqual(
    sourceBindingForWorkbench("current_user", { key: "creatorId" }).sourceBinding,
    { kind: "runtime_context", runtime_resolver: "runtime_context.current_user" },
  );
});

test("legacy submit_batch is normalized only in capability contract fields", () => {
  const original = {
    action: "submit_batch",
    steps: [{ step_id: "submit_batch", value: "submit_batch" }],
    capabilities: [{ name: "submit_batch", kind: "submit_batch", step_ids: ["submit_batch"] }],
    capability_relations: [{ from_capability: "query", to_capability: "submit_batch" }],
    goal: { capabilities: ["submit_batch"] },
  };
  const normalized = normalizeV3FlowSpecSnapshot(original);
  assert.equal(normalized.action, "submit");
  assert.deepEqual(normalized.capabilities, [{ name: "submit", kind: "submit", step_ids: ["submit_batch"] }]);
  assert.equal(normalized.capability_relations[0].to_capability, "submit");
  assert.deepEqual(normalized.goal.capabilities, ["submit"]);
  assert.deepEqual(normalized.steps, original.steps, "request/step identity and business values must not be rewritten");
  assert.equal(original.capabilities[0].kind, "submit_batch", "ingress normalization must not mutate the source snapshot");
  assert.doesNotMatch(pageRecorder, /value:\s*"submit_batch"/);
});

test("issue controls distinguish advisory from non-ignorable ContractFault", () => {
  assert.equal(isAdvisoryIssue({ kind: "advisory" }), true);
  assert.equal(isContractFaultIssue({ type: "contract_fault" }), true);
  assert.match(pageRecorder, /isAdvisoryIssue\(item\)[\s\S]{0,300}忽略/);
  assert.match(pageRecorder, /isContractFaultIssue\(issue\)[\s\S]{0,160}不能忽略/);
});

test("ignore actions are advisory-only and risk totals reflect actual severity", () => {
  const resolveBody = functionBody("resolveReview", "reviewSuggestionEdits");
  assert.match(resolveBody, /resolved\s*&&\s*\(!issue\s*\|\|\s*!isAdvisoryIssue\(issue\)\)/);
  const bulkBody = functionBody("bulkReview", "addStep");
  assert.match(bulkBody, /\.filter\(\(item\)\s*=>\s*isAdvisoryIssue/);
  assert.doesNotMatch(bulkBody, /!isContractFaultIssue/);
  assert.match(pageRecorder, /const highCount = reviewItems\.filter\([\s\S]{0,140}\)\.length/);
  assert.match(pageRecorder, /isAdvisoryIssue\(item as unknown as Record<string, any>\)[\s\S]{0,160}忽略/);
  assert.match(pageRecorder, /disabled=\{!advisoryCount\}[\s\S]{0,80}全部忽略/);
});

test("accepting review suggestions uses canonical source decisions and never hides a fixed ContractFault", () => {
  const reviewBody = functionBody("reviewSuggestionEdits", "applyReviewSuggestion");
  assert.match(reviewBody, /sourceAxisEdit\(tgt\.step_id, param, sourceKind, source\)/);
  assert.doesNotMatch(reviewBody, /targetParamEdit\([^\n]*["']source_kind["']/);
  assert.match(reviewBody, /isAdvisoryIssue[\s\S]*fingerprint:\s*item\.fingerprint/);
  assert.doesNotMatch(reviewBody, /if \(!edits\.some[\s\S]*resolve_review/);

  const llmBody = functionBody("applyLlmSuggestion", "refreshLlmRecommendations");
  assert.match(llmBody, /sourceAxisEdit\(targetStepId, targetParam, "previous_response", source\)/);
  assert.match(llmBody, /sourceAxisEdit\(targetStepId, targetParam, suggestion\.source_kind, source\)/);
  assert.doesNotMatch(llmBody, /targetParamEdit\([^\n]*["']source_kind["']/);
});

test("issue location uses controlled UUID expansion and exact request, step and field anchors", () => {
  assert.match(pageRecorder, /activeKey=\{expandedCapabilityKeys\}/);
  assert.match(pageRecorder, /activeKey=\{expandedStepKeys\[capKey\] \|\| \[\]\}/);
  assert.match(pageRecorder, /id=\{fieldAnchorId\(step, p\)\}/);
  assert.match(pageRecorder, /id=\{requestAnchorId\(req\)\}/);
  assert.match(pageRecorder, /fieldUuid[\s\S]{0,500}stableFieldKey/);
  assert.match(pageRecorder, /!matchedCapability\s*&&\s*!!\(matchedRequest \|\| matchedStepRequest \|\| matchedStep\)/);
  assert.match(pageRecorder, /requestAnimationFrame\(\(\) => requestAnimationFrame/);
});

test("draft and publication share one status region without claiming an unpublished draft was published", () => {
  assert.match(pageRecorder, /当前草稿校验通过（尚未发布）/);
  assert.match(pageRecorder, /发布失败：/);
  assert.doesNotMatch(pageRecorder, /发布校验通过/);
  assert.equal((pageRecorder.match(/\{result && \(/g) || []).length, 1);
  assert.match(pageRecorder, /result\?\.ok && publicationVerified/);
});

test("analysis failure removes only the uncommitted deterministic preview", () => {
  const handler = functionBody("handleRecordingEvent", "restoreSessionSnapshot");
  assert.match(
    handler,
    /m\.code === "analysis_failed"\s*&&\s*flowSpecRef\.current\?\.meta\?\.preview === true[\s\S]{0,520}flowSpecRef\.current = null;[\s\S]{0,120}setFlowSpec\(null\);[\s\S]{0,120}setCheckReport\(null\);/,
  );
  assert.doesNotMatch(handler, /m\.code === "analysis_failed"[\s\S]{0,240}resetEditorState\(\)/);
});

test("capability boundary status is not confused with runtime write confirmation", () => {
  assert.match(
    pageRecorder,
    /const unconfirmedCapabilities = capabilities\.filter\(\(cap\) => !cap\.confirmed\)\.length;/,
  );
  assert.doesNotMatch(
    pageRecorder,
    /const unconfirmedCapabilities[^;]*requires_human_confirm/,
  );
});

test("any workbench mutation immediately invalidates the prior publication result", () => {
  assert.equal(mutationInvalidatesPublication({ type: "flow_update" }), true);
  assert.equal(mutationInvalidatesPublication({ type: "flow_replace" }), true);
  assert.equal(mutationInvalidatesPublication({ type: "analysis_status" }), false);
  const sendBody = functionBody("send", "clearFrame");
  assert.match(sendBody, /mutationInvalidatesPublication\(obj\)[\s\S]{0,100}setResult\(null\)/);
  assert.match(sendBody, /setResult\(null\)[\s\S]{0,80}setCheckReport\(null\)/);
  assert.match(sendBody, /review_items\?\.length[\s\S]{0,140}review_items:\s*\[\]/);
});

test("recapture is serialized against capture, analysis, optimization and publication", () => {
  const body = functionBody("recapture", "cancelAnalysis");
  assert.match(body, /captureBusyRef\.current/);
  assert.match(body, /analysisBusyRef\.current/);
  assert.match(body, /publishBusyRef\.current/);
  assert.match(body, /optimizationBusy/);
  assert.match(pageRecorder, /loading=\{captureBusy\}\s+disabled=\{analysisBusy \|\| publishBusy \|\| captureBusy \|\| optimizationBusy\}/);
});

test("publication is serialized against analysis, recapture and semantic optimization", () => {
  const publishBody = functionBody("publishRequest", "performPublishRequest");
  assert.match(
    publishBody,
    /publishBusyRef\.current\s*\|\|\s*analysisBusyRef\.current\s*\|\|\s*captureBusyRef\.current\s*\|\|\s*optimizationBusy/,
  );
  assert.match(
    pageRecorder,
    /loading=\{publishBusy\}\s+disabled=\{analysisBusy \|\| captureBusy \|\| optimizationBusy\}/,
  );
});

test("automatic repair cannot overlap another semantic operation", () => {
  const repairBody = functionBody("autoFixFlow", "addCapability");
  assert.match(repairBody, /captureBusy\s*\|\|\s*optimizationBusy/);
  assert.match(
    pageRecorder,
    /loading=\{autoFixBusy\}\s+disabled=\{analysisBusy \|\| publishBusy \|\| captureBusy \|\| optimizationBusy\}/,
  );
});

test("capability orchestration cannot overlap another semantic operation", () => {
  const orchestrationBody = functionBody("orchestrateFlow", "autoFixFlow");
  assert.match(orchestrationBody, /captureBusy\s*\|\|\s*optimizationBusy/);
  assert.match(
    pageRecorder,
    /loading=\{optimizationBusy\}\s+disabled=\{analysisBusy \|\| publishBusy \|\| captureBusy \|\| optimizationBusy\}/,
  );
});
