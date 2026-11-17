import { existsSync, lstatSync, readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { isAbsolute, join } from "node:path";
import { tmpdir } from "node:os";

export const testPythonDirectory = process.env.DANO_TEST_PYTHON_DIR?.trim()
  || join(tmpdir(), `dano-test-python-${process.getuid?.() ?? "user"}`);
export const httpxRequirement = readFileSync(new URL("../deploy/python-requirements.txt", import.meta.url), "utf8")
  .split(/\r?\n/).find(line => /^httpx==\S+$/.test(line));
if (!httpxRequirement) throw new Error("Missing runtime httpx requirement");

export function validateTestPythonDirectory() {
  if (!isAbsolute(testPythonDirectory)) throw new Error("DANO_TEST_PYTHON_DIR must be absolute");
  let stat;
  try { stat = lstatSync(testPythonDirectory); } catch (error) {
    if (error.code === "ENOENT") return;
    throw error;
  }
  if (!stat.isDirectory() || (process.getuid && stat.uid !== process.getuid()) || (stat.mode & 0o022))
    throw new Error("Test Python directory must be private and owned by the current user");
}

export function inspectTestEnvironment(env = process.env, platform = process.platform) {
  validateTestPythonDirectory();
  const container = platform === "linux" && (existsSync("/.dockerenv") || existsSync("/run/.containerenv"));
  const candidates = [join(testPythonDirectory, "bin", "python3"), "python3"];
  const python = candidates.find(command => spawnSync(command, ["-c", "import httpx, sys; sys.exit(httpx.__version__ != sys.argv[1])", httpxRequirement.split("==")[1]], { env, stdio: "ignore", timeout: 10_000 }).status === 0);
  return { platform, container, python, profile: container ? "container" : "local" };
}
