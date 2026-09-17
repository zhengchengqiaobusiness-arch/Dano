import { execFileSync, spawnSync } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";

const repo = new URL("../../../../../", import.meta.url);
const roots: string[] = [];
afterEach(() => roots.splice(0).forEach(root => rmSync(root, { recursive: true, force: true })));
function fixture(productName: unknown = " 源码助手 ") {
  const root = mkdtempSync(join(tmpdir(), "dano-identity-"));
  roots.push(root);
  mkdirSync(join(root, "scripts"));
  mkdirSync(join(root, "apps/dano/runtime"), { recursive: true });
  mkdirSync(join(root, "deploy"));
  for (const file of ["scripts/deploy-product-identity.mjs", "apps/dano/runtime/product-name.mjs"]) cpSync(new URL(file, repo), join(root, file));
  writeFileSync(join(root, "dano.config.json"), JSON.stringify({ productName }));
  const git = (...args: string[]) => execFileSync("git", args, { cwd: root, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }).trim();
  git("init"); git("add", "."); git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture");
  const sha = git("rev-parse", "HEAD");
  const overlay = (value: unknown) => writeFileSync(join(root, "deploy/docker-compose.product-name.json"), JSON.stringify(value));
  const name = (value: unknown) => overlay({ services: { app: { environment: { DANO_PRODUCT_NAME: value } } } });
  const run = (target = sha) => spawnSync(process.execPath, [join(root, "scripts/deploy-product-identity.mjs"), join(root, "deploy"), target], { encoding: "utf8", env: { ...process.env, DANO_PRODUCT_NAME: "unmanaged-must-not-win", TEST_SECRET: "never-print-this" } });
  return { root, sha, run, name, overlay };
}
describe("secret-safe deployment identity", () => {
  it("reports only the target commit, effective name and source", () => {
    const f = fixture();
    writeFileSync(join(f.root, "deploy/.env"), "TEST_SECRET=never-print-this\n");
    const result = f.run();
    expect(result.status, result.stderr).toBe(0);
    expect(JSON.parse(result.stdout)).toEqual({ productName: "源码助手", source: "dano.config.json", targetSha: f.sha });
    expect(result.stdout + result.stderr).not.toContain("never-print-this");
  });
  it("uses a managed literal override, including Compose-escaped dollars", () => {
    const f = fixture(null);
    f.name(" 覆盖$$助手 ");
    const result = f.run();
    expect(result.status, result.stderr).toBe(0);
    expect(JSON.parse(result.stdout)).toEqual({ productName: "覆盖$助手", source: "managed-overlay", targetSha: f.sha });
  });
  it.each([null, [], {}, { services: null }, { services: { app: { environment: { DANO_PRODUCT_NAME: "name", API_KEY: "never-print-this" } } } }])("rejects invalid overlay shape %j", value => {
    const f = fixture(); f.overlay(value);
    const result = f.run();
    expect(result.status).toBe(1);
    expect(result.stderr).toContain("deploy-system-prompt.mjs set-name");
    expect(result.stdout + result.stderr).not.toContain("never-print-this");
  });
  it.each(["", "  ", "{产品名称}", "{{name}}", "${SECRET}", "$SECRET", "$$${SECRET}", "<%=name%>", "name\nsecret", 42, null])("rejects invalid managed names %j instead of falling back", value => {
    const f = fixture(); f.name(value);
    expect(f.run().status).toBe(1);
  });
  it("rejects a wrong commit and dirty source", () => {
    const f = fixture();
    expect(f.run("b".repeat(40)).status).toBe(1);
    writeFileSync(join(f.root, "dano.config.json"), '{"productName":"dirty"}');
    expect(f.run().status).toBe(1);
  });
  it("rejects parser details from invalid JSON without echoing configuration", () => {
    const f = fixture();
    writeFileSync(join(f.root, "deploy/docker-compose.product-name.json"), 'never-print-this');
    expect(f.run().stderr).not.toContain("never-print-this");
    expect(f.run().status).toBe(1);
  });
  it("binds the container renderer to the preflight name before any SYSTEM mutation", () => {
    const f = fixture();
    const identity = JSON.parse(f.run().stdout);
    const target = join(f.root, "agent"); mkdirSync(target);
    writeFileSync(join(target, "SYSTEM.md"), "preserve-existing");
    const result = spawnSync(process.execPath, [new URL("deploy/system-prompt.mjs", repo).pathname, "sync", "--expected-product-name", identity.productName], {
      encoding: "utf8", env: { ...process.env, DANO_PRODUCT_NAME: "different-runtime-name", DANO_RUNTIME_DIR: f.root, PI_CODING_AGENT_DIR: target },
    });
    expect(result.status).toBe(1);
    expect(readFileSync(join(target, "SYSTEM.md"), "utf8")).toBe("preserve-existing");
    expect(result.stdout + result.stderr).not.toContain("different-runtime-name");
    expect(result.stderr).toContain("PRODUCT_IDENTITY_MISMATCH");
    expect(result.stderr).toContain("deploy-product-identity.mjs");
  });
});
