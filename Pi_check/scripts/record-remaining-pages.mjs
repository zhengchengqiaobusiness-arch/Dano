/**
 * 从已盘点路由继续录未跑过的业务页。每场写进度，中断后可接着跑。
 */

import { spawn } from "node:child_process";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const actions = path.join(ROOT, "scripts", "list-page-actions.json");
const progressFile = path.join(ROOT, "data", "matrix-progress.json");
const limit = Number(process.env.RECORD_LIMIT || 0);

function toOpenUrl(page) {
  let url = String(page.url || "");
  if (url.includes("ruoyioffice.com/web/") && !url.includes("/web/#/")) {
    url = url.replace("https://ruoyioffice.com/web/", "https://ruoyioffice.com/web/#/");
  }
  return url;
}

function skipText(text) {
  return /字典|监控|缓存|Swagger|代码生成|表单构建|数据监控|服务监控|在线用户|定时任务|相关字典|系统工具|系统监控|http:\/\//i.test(
    text,
  );
}

function skipUrl(url) {
  return /\/http:\/\//i.test(url);
}

const BASE = process.env.PI_CHECK_URL || "http://127.0.0.1:18080";

async function sidecarHealthy() {
  try {
    const response = await fetch(`${BASE}/health`);
    const payload = await response.json();
    return response.ok && payload?.ok === true;
  } catch {
    return false;
  }
}

async function waitSidecar(timeoutMs = 120000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await sidecarHealthy()) return;
    await new Promise((resolve) => setTimeout(resolve, 2000));
  }
  throw new Error(`Pi_check 未就绪: ${BASE}/health`);
}

function wanted(text, site) {
  if (site === "boot") return /日常办公OA|人事管理HRM|工作空间/.test(text);
  if (site === "ruoyioffice") return /^OA \//.test(text) || /流程中心 \/ 任务管理|工作台 \/ 我的首页/.test(text);
  return false;
}

async function loadRoutes(file, site) {
  const payload = JSON.parse(await readFile(file, "utf8"));
  return (payload.pages || [])
    .filter((page) => wanted(page.text, site) && !skipText(page.text))
    .map((page) => ({
      site,
      text: page.text,
      url: toOpenUrl(page),
    }));
}

async function loadProgress() {
  try {
    return JSON.parse(await readFile(progressFile, "utf8"));
  } catch {
    return { done: [], results: [] };
  }
}

const already = new Set([
  "http://boot.dianshixinxi.com:90/oa/duty/dutyLeaveApply?billType=duty_leave",
  "http://boot.dianshixinxi.com:90/oa/notice/notice",
  "http://boot.dianshixinxi.com:90/oa/meetingroom/meetingroomApply?billType=meetingroom_apply",
  "http://boot.dianshixinxi.com:90/oa/seal/sealApply?billType=seal_apply",
  "http://boot.dianshixinxi.com:90/hrm/staffView/book",
  "http://admin.dianshixinxi.com:90/oa/duty/leave",
  "http://admin.dianshixinxi.com:90/oa/seal/seal-apply",
  "http://admin.dianshixinxi.com:90/oa/common/hotel-apply",
  "https://ruoyioffice.com/web/#/oa/seal/seal-apply-list",
  "https://ruoyioffice.com/web/#/bpm/oa/leave",
]);

const pages = [
  ...(await loadRoutes(path.join(ROOT, "data", "routes-ruoyi-boot.dianshixinxi.com_90.json"), "boot")),
  ...(await loadRoutes(path.join(ROOT, "data", "routes-yudao-ruoyioffice.com.json"), "ruoyioffice")),
];
const progress = await loadProgress();
for (const url of progress.done || []) already.add(url);
const queue = pages.filter((page) => page.url && !already.has(page.url) && !skipUrl(page.url));
const planned = limit > 0 ? queue.slice(0, limit) : queue;
console.log(`queued=${planned.length} skipped=${already.size} totalCandidates=${pages.length}`);

function runOne(url) {
  return new Promise((resolve) => {
    const child = spawn(
      process.execPath,
      [
        path.join(ROOT, "scripts", "record-page-flow.mjs"),
        url,
        "请将我接下来在页面中实际完成的每项业务操作分别生成一个可调用能力。",
        actions,
      ],
      { cwd: ROOT, stdio: ["ignore", "pipe", "pipe"] },
    );
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
      process.stdout.write(chunk);
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
      process.stderr.write(chunk);
    });
    child.on("close", (code) => resolve({ code, stdout, stderr }));
  });
}

function parseResult(stdout) {
  const line = String(stdout || "")
    .split(/\r?\n/)
    .reverse()
    .find((item) => item.startsWith("RESULT_JSON "));
  if (!line) return null;
  try {
    return JSON.parse(line.slice("RESULT_JSON ".length));
  } catch {
    return null;
  }
}

function connectionLost(ran) {
  const text = `${ran.stdout || ""}\n${ran.stderr || ""}`;
  return /ECONNREFUSED|ECONNRESET|fetch failed/i.test(text);
}

await mkdir(path.dirname(progressFile), { recursive: true });
await waitSidecar();
for (const page of planned) {
  console.log(`\n=== ${page.site} ${page.text} ===\n${page.url}\n`);
  await waitSidecar();
  let ran = await runOne(page.url);
  if (ran.code !== 0 && connectionLost(ran)) {
    console.log("sidecar dropped, waiting then retry once");
    await waitSidecar();
    ran = await runOne(page.url);
  }
  const parsed = parseResult(ran.stdout);
  const row = {
    ...page,
    exitCode: ran.code,
    recordingId: parsed?.id || "",
    capabilityCount: parsed?.capabilityCount ?? null,
    unresolved: parsed?.unresolved || [],
    finishedAt: new Date().toISOString(),
  };
  progress.results = (progress.results || []).filter((item) => item.url !== page.url);
  progress.results.push(row);
  if (row.exitCode === 0 && row.recordingId) {
    if (!progress.done.includes(page.url)) progress.done.push(page.url);
  } else {
    progress.done = (progress.done || []).filter((url) => url !== page.url);
  }
  await writeFile(progressFile, `${JSON.stringify(progress, null, 2)}\n`);
  console.log(
    `${row.exitCode === 0 ? "ok" : "fail"} ${row.capabilityCount ?? "-"}caps ${row.recordingId || "-"} ${row.text}`,
  );
}
console.log(`progress ${progressFile} done=${progress.done.length}`);
