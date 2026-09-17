import { spawn, spawnSync } from "node:child_process";
import { chmodSync, existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

const script = new URL("../../../../../scripts/deploy-system-prompt.mjs", import.meta.url).pathname;
const roots: string[] = [];
let healthServer: ReturnType<typeof spawn>;
let healthBase: string;
const healthRoot = mkdtempSync(join(tmpdir(), "dano-system-health-"));
const healthFailure = join(healthRoot, "failed");
beforeAll(async () => {
  healthServer = spawn(process.execPath, ["--input-type=module", "-e", "import http from 'node:http'; import fs from 'node:fs'; const s=http.createServer((q,r)=>{r.writeHead(fs.existsSync(process.argv[1])?502:200);r.end('ok')});s.listen(0,'127.0.0.1',()=>console.log('http://127.0.0.1:'+s.address().port))", healthFailure]);
  healthBase = await new Promise<string>(resolve => healthServer.stdout!.once("data", data => resolve(data.toString().trim())));
});
afterAll(() => { healthServer.kill(); rmSync(healthRoot, { recursive: true, force: true }); });
afterEach(() => roots.splice(0).forEach(root => rmSync(root, { recursive: true, force: true })));
function fixture() {
  const root = mkdtempSync(join(tmpdir(), "dano-system-repair-"));
  roots.push(root);
  writeFileSync(join(root, ".env"), "DANO_TEST=1\n");
  const fake = join(root, "compose");
  writeFileSync(fake, `#!${process.execPath}
import fs from 'node:fs';
const args = process.argv.slice(2);
if(process.env.TEST_BREAK_ROUTE && args.includes('--force-recreate') && !fs.existsSync(process.env.TEST_ONCE)) {
 fs.writeFileSync(process.env.TEST_ROUTE_MARKER, '502'); fs.writeFileSync(process.env.TEST_ONCE, 'failed');
}
if(args.includes('restore')) fs.rmSync(process.env.TEST_ROUTE_MARKER, {force:true});
fs.appendFileSync(process.env.TEST_LOG, JSON.stringify(args)+'\\n');
if(process.env.TEST_FAIL && args.includes(process.env.TEST_FAIL) && !fs.existsSync(process.env.TEST_ONCE)) {
 fs.writeFileSync(process.env.TEST_ONCE, 'failed'); console.error('sensitive output must not escape'); process.exit(1);
}
`);
  chmodSync(fake, 0o755);
  const env = { ...process.env, DANO_COMPOSE: fake, DANO_DEPLOY_DIR: root,
    DANO_DEPLOY_LOCK_PATH: join(root, "deploy.lock"), DANO_SMOKE_BASE_URL: healthBase, TEST_ROUTE_MARKER: healthFailure, TEST_LOG: join(root, "calls"), TEST_ONCE: join(root, "once") };
  return { root, env, run: (args: string[], extra = {}) => spawnSync(process.execPath, [script, ...args], { env: { ...env, ...extra }, encoding: "utf8" }),
    calls: () => readFileSync(join(root, "calls"), "utf8").trim().split("\n").map(line => JSON.parse(line) as string[]) };
}
describe("supported SYSTEM configuration repair", () => {
  it("backs up, syncs and checks before app-only recreate, health and cleanup", () => {
    const f = fixture();
    const result = f.run(["set-name", "配置$助手"]);
    expect(result.status, result.stderr).toBe(0);
    const calls = f.calls();
    const actions = calls.filter(a => a.includes("./deploy/system-prompt.mjs")).map(a => a[a.indexOf("./deploy/system-prompt.mjs") + 1]);
    expect(actions).toEqual(["backup", "sync", "check", "check"]);
    const switchIndex = calls.findIndex(a => a.includes("up"));
    expect(calls[switchIndex - 1]).toContain("check");
    expect(calls[switchIndex].slice(-6)).toEqual(["up", "-d", "--no-build", "--no-deps", "--force-recreate", "app"]);
    expect(calls.some(a => a.slice(-4).join(" ") === "nginx nginx -s reload")).toBe(true);
    expect(calls.every(a => !a.includes("down"))).toBe(true);
    const transaction = result.stdout.match(/acceptance: ([a-f0-9-]+)/)![1];
    expect(existsSync(join(f.root, `.system-rollback-${transaction}`, "transaction.json"))).toBe(true);
    expect(f.run(["repair"]).status).toBe(1);
    expect(f.run(["accept", transaction]).status).toBe(0);
    expect(existsSync(join(f.root, `.system-rollback-${transaction}`))).toBe(false);
    expect(readFileSync(join(f.root, "docker-compose.product-name.json"), "utf8")).toContain("配置$$助手");
  });
  it.each(["sync", "check", "--force-recreate"])("rolls back when %s fails without leaking child output", phase => {
    const f = fixture();
    const old = JSON.stringify({ services: { app: { environment: { DANO_PRODUCT_NAME: "旧助手" } } } });
    writeFileSync(join(f.root, "docker-compose.product-name.json"), old, { mode: 0o600 });
    const result = f.run(["set-name", "新助手"], { TEST_FAIL: phase });
    expect(result.status).toBe(1);
    expect(result.stderr).toContain("rolled back; app healthy");
    expect(result.stderr + result.stdout).not.toContain("sensitive output");
    expect(readFileSync(join(f.root, "docker-compose.product-name.json"), "utf8")).toBe(old);
    const calls = f.calls();
    expect(calls.some(a => a.includes("restore"))).toBe(true);
    const firstSwitch = calls.findIndex(a => a.includes("up"));
    if (phase !== "--force-recreate") expect(firstSwitch).toBe(-1);
  });
  it("cannot bypass synchronization in repair mode", () => {
    const f = fixture();
    expect(f.run(["repair"]).status).toBe(0);
    expect(f.calls().some(a => a.includes("sync"))).toBe(true);
    expect(f.calls().some(a => a.includes("restart"))).toBe(true);
    expect(f.calls().some(a => a.includes("up"))).toBe(false);
    expect(existsSync(join(f.root, "docker-compose.product-name.json"))).toBe(false);
  });
  it("retains rollback through browser rejection and restores it explicitly", () => {
    const f = fixture();
    const result = f.run(["set-name", "新助手"]);
    expect(result.status).toBe(0);
    const transaction = result.stdout.match(/acceptance: ([a-f0-9-]+)/)![1];
    expect(f.run(["rollback", transaction]).status).toBe(0);
    expect(existsSync(join(f.root, "docker-compose.product-name.json"))).toBe(false);
    expect(f.calls().some(a => a.includes("restore"))).toBe(true);
  });
  it("rolls back when container health passes but nginx still returns 502", () => {
    const f = fixture();
    const result = f.run(["set-name", "新助手"], { TEST_BREAK_ROUTE: "1" });
    expect(result.status).toBe(1);
    expect(result.stderr).toContain("rolled back; app healthy");
    expect(f.calls().some(a => a.includes("restore"))).toBe(true);
  }, 20_000);
  it("cleans incomplete backup preparation so the next repair can retry", () => {
    const f = fixture();
    const result = f.run(["repair"], { TEST_FAIL: "backup" });
    expect(result.status).toBe(1);
    expect(f.calls().some(a => a.includes("discard"))).toBe(true);
    expect(f.calls().some(a => a.includes("restart") || a.includes("up"))).toBe(false);
    expect(f.run(["repair"]).status).toBe(0);
  });
  it("fails closed when another deployment owns the same lock", async () => {
    const f = fixture();
    const holder = spawn("python3", ["-c", "import fcntl,sys; f=open(sys.argv[1],'w'); fcntl.flock(f,fcntl.LOCK_EX); print('ready',flush=True); sys.stdin.read()", f.env.DANO_DEPLOY_LOCK_PATH]);
    try {
      await new Promise<void>(resolve => holder.stdout.once("data", () => resolve()));
      expect(f.run(["repair"]).status).toBe(1);
      expect(existsSync(join(f.root, "calls"))).toBe(false);
    } finally { holder.stdin.end(); }
  });
});
