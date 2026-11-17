import { chmodSync, mkdtempSync, mkdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, expect, it } from "vitest";

const runner = new URL("../../../../../scripts/run-tests.mjs", import.meta.url);
const roots: string[] = [];
afterEach(() => roots.splice(0).forEach(root => rmSync(root, { recursive: true, force: true })));
function fixture(pythonReady: boolean) {
  const root = mkdtempSync(join(tmpdir(), "dano-test-preflight-")); roots.push(root);
  const bin = join(root, "bin"); mkdirSync(bin);
  symlinkSync(process.execPath, join(bin, "node"));
  const log = join(root, "vitest-call.json");
  for (const [name, content] of Object.entries({
    python3: `#!/bin/sh\nexit ${pythonReady ? 0 : 1}\n`,
    bwrap: "#!/bin/sh\nexit 0\n",
    pnpm: `#!${process.execPath}\nrequire('node:fs').writeFileSync(${JSON.stringify(log)}, JSON.stringify({args:process.argv.slice(2),path:process.env.PATH,profile:process.env.DANO_TEST_PROFILE}));\n`,
  })) { const file = join(bin, name); writeFileSync(file, content); chmodSync(file, 0o755); }
  const run = (...args: string[]) => spawnSync(process.execPath, [runner.pathname, ...args], {
    encoding: "utf8", env: { ...process.env, PATH: bin, DANO_TEST_PYTHON_DIR: join(root, "missing-venv") },
  });
  return { run, log, bin };
}

it("rejects missing Python dependencies before invoking Vitest and gives the preparation command", () => {
  const f = fixture(false); const result = f.run();
  expect(result.status).toBe(1);
  expect(result.stderr).toContain("pnpm test:prepare");
  expect(result.stdout).not.toContain("[test-environment]");
});

it("passes requested test files and a declared profile after a successful environment preflight", () => {
  const f = fixture(true); const result = f.run("example.test.ts");
  expect(result.status, result.stderr).toBe(0);
  const call = JSON.parse(readFileSync(f.log, "utf8"));
  expect(call.args).toEqual(["exec", "vitest", "run", "example.test.ts"]);
  expect(["local", "container"]).toContain(call.profile);
  expect(call.path).toBe(f.bin);
});
