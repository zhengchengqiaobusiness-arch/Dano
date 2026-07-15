import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const pageRecorder = readFileSync(new URL("../src/components/PageRecorder.tsx", import.meta.url), "utf8");
const stableIdentity = readFileSync(
  new URL("../src/features/recording-v3/state/stableIdentity.ts", import.meta.url),
  "utf8",
);

test("V3 workbench mutations carry permanent UUID targets", () => {
  assert.doesNotMatch(pageRecorder, /capability_index/);
  assert.match(pageRecorder, /capability_uuid:\s*capabilityUuid/);
  assert.match(pageRecorder, /step_uuid:\s*stableStepRef\(step\)/);
  assert.match(pageRecorder, /field_uuid:\s*stableFieldKey/);
  assert.match(pageRecorder, /reorder_capabilities", capability_uuids: refs/);
  assert.match(pageRecorder, /reorder_steps[\s\S]{0,180}step_uuids:/);
});

test("V3 React identities do not fall back to mutable names, paths, or indexes", () => {
  assert.doesNotMatch(stableIdentity, /return\s+field\.(?:field_id|path|key|label)/);
  assert.doesNotMatch(stableIdentity, /return\s+capability\.(?:capability_id|name|title|intent|kind)/);
  assert.doesNotMatch(stableIdentity, /unknown-source|unknown-target|legacy-capability/);
  assert.doesNotMatch(pageRecorder, /key=\{[^}\n]*(?:\.path|\.name|\bidx\b|\bindex\b)/);
  const requestKeyBody = pageRecorder.match(/function requestGraphKey\([\s\S]*?\n\}/)?.[0] || "";
  assert.ok(requestKeyBody, "requestGraphKey not found");
  assert.doesNotMatch(requestKeyBody, /request_index|requestGraphSignature|\.path|\.url/);
  assert.doesNotMatch(pageRecorder, /key=\{[^}\n]*compactJson/);
});

test("V3 lifecycle exposes distinct analysis, recapture, cancellation, Pi retry and status commands", () => {
  for (const command of [
    "finalize",
    "reanalyze",
    "recapture",
    "cancel_analysis",
    "retry_pi",
    "publish_request",
    "refresh_flow_spec",
    "analysis_status",
  ]) {
    assert.match(
      `${pageRecorder}\n${readFileSync(new URL("../src/features/recording-v3/hooks/useRecordingChannel.ts", import.meta.url), "utf8")}`,
      new RegExp(`(?:type|operation)\\s*:\\s*["']${command}["']`),
      `missing V3 command: ${command}`,
    );
  }
});

test("publish references the frozen revision instead of resending a mutable FlowSpec", () => {
  const publishMessage = pageRecorder.match(/const publishMessage\s*=\s*\{[\s\S]*?\};/)?.[0] || "";
  assert.ok(publishMessage, "publish message literal not found");
  assert.doesNotMatch(publishMessage, /\bflow_spec\s*:/);
  assert.doesNotMatch(publishMessage, /\buse_flow_spec\s*:/);
  assert.match(publishMessage, /expected_fingerprint\s*:/);
});

test("recapture keeps the current capture until the server confirms a new generation", () => {
  const requestBody = pageRecorder.match(/function recapture\(\)\s*\{[\s\S]*?\n\s*\}/)?.[0] || "";
  assert.ok(requestBody, "recapture handler not found");
  assert.doesNotMatch(requestBody, /setSteps|setReqs|setFields|setPicked|setCands/);

  const confirmedBranch = pageRecorder.match(/m\.type === "recapture_started"[\s\S]{0,500}/)?.[0] || "";
  assert.match(confirmedBranch, /setSteps\(\[\]\)/);
  assert.match(confirmedBranch, /setReqs\(\[\]\)/);
  assert.match(confirmedBranch, /setFields\(\[\]\)/);
});

test("direct invocation is exposed only for a verified publication", () => {
  const directInvokeBlock = pageRecorder.match(/\{result\.ok[^\n]*&&[\s\S]{0,500}>\s*直接调用\s*<\/Button>/)?.[0] || "";
  assert.ok(directInvokeBlock, "direct invocation condition not found");
  assert.match(directInvokeBlock, /resultVerified/);
  assert.match(pageRecorder, /verification_status\s*===\s*["']verified["']/);
  assert.match(pageRecorder, /publication_status\s*===\s*["']published_verified["']/);
});
