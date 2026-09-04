import test from "node:test";
import assert from "node:assert/strict";
import { formatProcessLog, pidIsAlive, waitForPidExit } from "../src/process-lifecycle.js";

test("process log names the action, kind, pid and page", () => {
  assert.equal(
    formatProcessLog("OPEN", "pi-rpc", { pid: 12, page: "page_abc" }),
    "OPEN  pi-rpc pid=12 page=page_abc"
  );
  assert.equal(
    formatProcessLog("CLOSE", "playwright-browser", { reason: "clear" }),
    "CLOSE playwright-browser reason=clear"
  );
});

test("current process is alive and a fake pid is not", async () => {
  assert.equal(pidIsAlive(process.pid), true);
  assert.equal(pidIsAlive(0), false);
  assert.equal(await waitForPidExit(0, 50), true);
});
