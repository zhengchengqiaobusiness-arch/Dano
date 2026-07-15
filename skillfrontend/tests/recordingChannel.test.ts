import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import * as channelModule from "../src/features/recording-v3/state/mutationReplay.ts";

const channelSource = readFileSync(
  new URL("../src/features/recording-v3/hooks/useRecordingChannel.ts", import.meta.url),
  "utf8",
);
const pageRecorderSource = readFileSync(
  new URL("../src/components/PageRecorder.tsx", import.meta.url),
  "utf8",
);

test("an unknown network outcome replays the exact mutation envelope", () => {
  const replayUnknownMutation = (channelModule as Record<string, unknown>).replayUnknownMutation as
    | ((message: Record<string, unknown>) => Record<string, unknown>)
    | undefined;
  assert.equal(typeof replayUnknownMutation, "function");
  const active = {
    type: "flow_update",
    operation_id: "operation-1",
    expected_revision: 17,
    edits: [{ op: "update_flow", field: "title", value: "A" }],
  };
  assert.deepEqual(replayUnknownMutation?.(active), active);
});

test("an explicit revision conflict retries with a fresh operation envelope", () => {
  const retryAfterRevisionConflict = (channelModule as Record<string, unknown>).retryAfterRevisionConflict as
    | ((message: Record<string, unknown>) => Record<string, unknown>)
    | undefined;
  assert.equal(typeof retryAfterRevisionConflict, "function");
  const retry = retryAfterRevisionConflict?.({
    type: "flow_update",
    operation_id: "failed-operation",
    expected_revision: 17,
    edits: [{ op: "update_flow", field: "title", value: "A" }],
  });
  assert.deepEqual(retry, {
    type: "flow_update",
    edits: [{ op: "update_flow", field: "title", value: "A" }],
  });
});

test("every callback waiting on the same flow sync is drained in registration order", () => {
  const drainFlowSyncCallbacks = (channelModule as Record<string, unknown>).drainFlowSyncCallbacks as
    | ((callbacks: Array<() => void>) => void)
    | undefined;
  assert.equal(typeof drainFlowSyncCallbacks, "function");
  const calls: string[] = [];
  const callbacks = [
    () => calls.push("publish"),
    () => calls.push("refresh"),
  ];
  drainFlowSyncCallbacks?.(callbacks);
  assert.deepEqual(calls, ["publish", "refresh"]);
  assert.deepEqual(callbacks, []);
});

test("operation watchdog expires once and can be cleared or re-armed with a fake clock", async () => {
  let lifecycleModule: Record<string, unknown> = {};
  try {
    lifecycleModule = await import(
      new URL("../src/features/recording-v3/state/operationWatchdog.ts", import.meta.url).href
    ) as Record<string, unknown>;
  } catch {
    // The assertion below is the intentional red phase when the watchdog does
    // not exist yet; keep module-loading errors from hiding the real contract.
  }
  const OperationWatchdog = lifecycleModule.OperationWatchdog as
    | (new (
      scheduler: {
        now(): number;
        setTimeout(callback: () => void, delay: number): number;
        clearTimeout(handle: number): void;
      },
      onTimeout: (operation: string) => void,
    ) => {
      arm(operation: string, timeout: number): void;
      clear(operation: string): void;
      clearAll(): void;
      isArmed(operation: string): boolean;
    })
    | undefined;
  assert.equal(typeof OperationWatchdog, "function");
  if (!OperationWatchdog) return;

  let now = 0;
  let nextHandle = 1;
  const tasks = new Map<number, { at: number; callback: () => void }>();
  const expired: string[] = [];
  const scheduler = {
    now: () => now,
    setTimeout(callback: () => void, delay: number) {
      const handle = nextHandle++;
      tasks.set(handle, { at: now + delay, callback });
      return handle;
    },
    clearTimeout(handle: number) {
      tasks.delete(handle);
    },
  };
  const advance = (milliseconds: number) => {
    now += milliseconds;
    for (const [handle, task] of [...tasks].sort((a, b) => a[1].at - b[1].at)) {
      if (task.at > now) continue;
      tasks.delete(handle);
      task.callback();
    }
  };

  const watchdog = new OperationWatchdog(scheduler, (operation) => expired.push(operation));
  watchdog.arm("analysis", 100);
  advance(99);
  assert.deepEqual(expired, []);
  watchdog.arm("analysis", 50);
  advance(49);
  assert.deepEqual(expired, []);
  advance(1);
  assert.deepEqual(expired, ["analysis"]);
  assert.equal(watchdog.isArmed("analysis"), false);

  watchdog.arm("capture", 20);
  watchdog.clear("capture");
  advance(20);
  assert.deepEqual(expired, ["analysis"]);
  watchdog.arm("publish", 20);
  watchdog.clearAll();
  advance(20);
  assert.deepEqual(expired, ["analysis"]);
});

test("disconnect and timeout paths cannot leave V3 loading or publish recovery unbounded", () => {
  const disconnected = pageRecorderSource.match(/onDisconnected:\s*\(willReconnect\)\s*=>\s*\{[\s\S]*?\n\s*\},/)?.[0] || "";
  assert.ok(disconnected, "onDisconnected callback not found");
  assert.doesNotMatch(disconnected, /if\s*\(willReconnect\)\s*return/);
  for (const reset of [
    "updateCaptureBusy(false)",
    "updateAnalysisBusy(false)",
    "updatePublishBusy(false)",
    "clearFlowOperation()",
    "setNamingBusy(false)",
    "setDescBusy(false)",
    "setLlmBusy(false)",
  ]) {
    assert.match(disconnected, new RegExp(reset.replace(/[()]/g, "\\$&")), `disconnect misses ${reset}`);
  }
  assert.match(channelSource, /PUBLISH_RECOVERY_TIMEOUT_MS/);
  assert.match(channelSource, /hasPendingPublish/);
  assert.match(pageRecorderSource, /channel\.hasPendingPublish\(\)/);
  assert.match(pageRecorderSource, /ensureOperationWatchdog\("semantic"\)/);
});
