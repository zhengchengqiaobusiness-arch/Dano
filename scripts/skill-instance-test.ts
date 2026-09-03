import { spawnSync } from "node:child_process";
import { readdirSync } from "node:fs";
import { cp, mkdir } from "node:fs/promises";
import path from "node:path";
import { chromium } from "playwright";

function latestSkill(prefix: string) {
  const root = path.resolve("dist/skills");
  const names = readdirSync(root).filter(name => name.startsWith(prefix)).sort();
  const latest = names.at(-1);
  if (!latest) throw new Error(`missing exported skill ${prefix}`);
  return latest;
}

const leaveSkill = latestSkill("oa-duty-leave-sk_");
const purchaseSkill = latestSkill("purchase-order-sk_");

async function refreshProfile(dest: string) {
  const source = path.resolve(".business-skill-studio/live-profile");
  await mkdir(dest, { recursive: true });
  for (const rel of [
    "Default/Network/Cookies",
    "Default/Network/Cookies-journal",
    "Default/Local Storage",
    "Default/Session Storage",
    "Default/Preferences",
    "Local State"
  ]) {
    await mkdir(path.dirname(path.join(dest, rel)), { recursive: true });
    await cp(path.join(source, rel), path.join(dest, rel), { recursive: true, force: true }).catch(() => {});
  }
}

async function authHeaders() {
  const dest = path.resolve(".business-skill-studio/skill-test-profile");
  await refreshProfile(dest);
  const ctx = await chromium.launchPersistentContext(dest, {
    headless: true,
    channel: "chrome",
    viewport: { width: 1280, height: 800 }
  });
  const page = ctx.pages()[0] || await ctx.newPage();
  const pending = page.waitForResponse(response =>
    /\/admin-api\/oa\/duty-leave\/page/.test(response.url()) && response.request().method() === "GET"
  , { timeout: 20000 }).catch(() => undefined);
  await page.goto("http://admin.dianshixinxi.com:90/oa/duty/leave", {
    waitUntil: "domcontentloaded",
    timeout: 45000
  });
  const captured = await pending;
  const fromNetwork = captured?.request().headers() || {};
  const storage = await page.evaluate(() => {
    const dump: Record<string, string> = {};
    for (const key of Object.keys(localStorage)) dump[key] = String(localStorage.getItem(key) || "").slice(0, 80);
    return dump;
  });
  await ctx.close();
  const authorization = fromNetwork.authorization || fromNetwork.Authorization;
  const tenant = fromNetwork["tenant-id"] || fromNetwork["visit-tenant-id"] || "1";
  if (authorization) {
    return { Authorization: authorization, "tenant-id": tenant };
  }
  void storage;
  throw new Error("page did not send a leave query with Authorization");
}

function runSkill(skill: string, capability: string, input: string, extra: string[] = []) {
  const script = path.join("dist", "skills", skill, "scripts", "execute.py");
  const result = spawnSync("python", [script, "--capability", capability, "--input", input, ...extra], {
    encoding: "utf8",
    env: process.env
  });
  const text = `${result.stdout || ""}\n${result.stderr || ""}`;
  const start = text.indexOf("{");
  const parsed = start >= 0 ? JSON.parse(text.slice(start)) : { ok: false, error: text.slice(0, 400) };
  return { exit: result.status, parsed };
}

const headers = await authHeaders();
process.env.SKILL_AUTH_HEADERS = JSON.stringify(headers);

const leaveQuery = runSkill(leaveSkill, "query-get-duty-leave-page", "@.business-skill-studio/tmp-leave-query.json");
const purchaseQuery = runSkill(purchaseSkill, "query-get-purchase-order-page", "@.business-skill-studio/tmp-po-query.json");
const blockedWrite = runSkill(leaveSkill, "create-post-duty-leave-submit-process", "@.business-skill-studio/tmp-leave-create.json");

function brief(result: { parsed: any }) {
  const body = result.parsed.body || {};
  return {
    ok: result.parsed.ok,
    status: result.parsed.status,
    code: body.code,
    msg: body.msg,
    total: body.data && body.data.total,
    assertions: result.parsed.assertions,
    error: result.parsed.error
  };
}

console.log(JSON.stringify({
  leaveQuery: brief(leaveQuery),
  purchaseQuery: brief(purchaseQuery),
  writeBlocked: blockedWrite.parsed.error
}, null, 2));
