import { spawnSync } from "node:child_process";
import { dirname, delimiter, isAbsolute } from "node:path";
import { inspectTestEnvironment } from "./test-environment.mjs";

const args = process.argv.slice(2);
const containerIndex = args.indexOf("--container");
const requireContainer = containerIndex >= 0;
if (requireContainer) args.splice(containerIndex, 1);
const environment = inspectTestEnvironment();
if (requireContainer && !environment.container) {
  console.error("Container tests require a Linux Docker/Podman container; run pnpm test locally for portable tests.");
  process.exit(1);
}
if (!environment.python) {
  console.error("Test preflight: Python httpx is missing. Run pnpm test:prepare once, then rerun the tests. No tests started.");
  process.exit(1);
}
if (environment.container && spawnSync("bwrap", ["--version"], { stdio: "ignore" }).status !== 0) {
  console.error("Container test preflight: bubblewrap is missing. Use the Dano runtime image. No tests started.");
  process.exit(1);
}
console.log(`[test-environment] profile=${environment.profile} platform=${environment.platform} python-httpx=ready agent-config=isolated container-only=${environment.container ? "included" : "excluded"}`);
const result = spawnSync("pnpm", ["exec", "vitest", "run", ...args], {
  stdio: "inherit",
  env: { ...process.env, PATH: isAbsolute(environment.python) ? `${dirname(environment.python)}${delimiter}${process.env.PATH ?? ""}` : process.env.PATH, DANO_TEST_PROFILE: environment.profile },
});
process.exit(result.status ?? 1);
