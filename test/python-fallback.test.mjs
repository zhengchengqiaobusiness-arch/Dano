import assert from "node:assert/strict";
import test from "node:test";
import { pythonFallback, spawnClientWithPythonFallback } from "../src/agent/tools.mjs";

test("9009 retries with the other python binary", async () => {
  assert.equal(pythonFallback("python3"), "python");
  const seen = [];
  const run = async (argv) => {
    seen.push(argv[0]);
    return { code: seen.length === 1 ? 9009 : 0, stdout: "ok", stderr: "" };
  };
  const result = await spawnClientWithPythonFallback(["python3", "scripts/client.py", "list"], ".", "", {}, run);
  assert.deepEqual(seen, ["python3", "python"]);
  assert.equal(result.code, 0);
});
