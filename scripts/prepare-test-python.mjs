import { mkdirSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { join } from "node:path";
import { testPythonDirectory, httpxRequirement, validateTestPythonDirectory } from "./test-environment.mjs";

function run(command, args) {
  const result = spawnSync(command, args, { stdio: "inherit" });
  if (result.status !== 0) process.exit(result.status ?? 1);
}
validateTestPythonDirectory();
mkdirSync(testPythonDirectory, { recursive: true, mode: 0o700 });
validateTestPythonDirectory();
run("python3", ["-m", "venv", testPythonDirectory]);
run(join(testPythonDirectory, "bin", "python3"), ["-m", "pip", "install", "--disable-pip-version-check", "--timeout", "30", "--retries", "2", httpxRequirement]);
console.log("Test Python prepared; runtime httpx requirement installed in an isolated venv.");
