/**
 * 按矩阵对多个叶子页做真实录制。一场接一场，不并行抢浏览器。
 */

import { spawn } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const actions = process.argv[2] || path.join(ROOT, "scripts", "list-page-actions.json");
const pages = [
  {
    site: "dianshi-boot",
    text: "日常办公OA / 假勤申请 / 请假申请",
    url: "http://boot.dianshixinxi.com:90/oa/duty/dutyLeaveApply?billType=duty_leave",
  },
  {
    site: "dianshi-boot",
    text: "日常办公OA / 通知公告 / 通知公告",
    url: "http://boot.dianshixinxi.com:90/oa/notice/notice",
  },
  {
    site: "dianshi-boot",
    text: "日常办公OA / 会议室申请 / 会议室申请列表",
    url: "http://boot.dianshixinxi.com:90/oa/meetingroom/meetingroomApply?billType=meetingroom_apply",
  },
  {
    site: "dianshi-boot",
    text: "日常办公OA / 公章申请 / 公章使用",
    url: "http://boot.dianshixinxi.com:90/oa/seal/sealApply?billType=seal_apply",
  },
  {
    site: "dianshi-boot",
    text: "人事管理HRM / 员工管理 / 人员档案",
    url: "http://boot.dianshixinxi.com:90/hrm/staffView/book",
  },
  {
    site: "dianshi-admin",
    text: "OA办公 / 假勤管理 / 请假申请",
    url: "http://admin.dianshixinxi.com:90/oa/duty/leave",
  },
  {
    site: "dianshi-admin",
    text: "OA办公 / 公章使用 / 公章使用",
    url: "http://admin.dianshixinxi.com:90/oa/seal/seal-apply",
  },
  {
    site: "ruoyioffice",
    text: "印章申请列表",
    url: "https://ruoyioffice.com/web/#/oa/seal/seal-apply-list",
  },
  {
    site: "ruoyioffice",
    text: "请假申请",
    url: "https://ruoyioffice.com/web/#/bpm/oa/leave",
  },
];

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

const results = [];
for (const page of pages) {
  console.log(`\n=== ${page.site} ${page.text} ===\n${page.url}\n`);
  const ran = await runOne(page.url);
  let parsed = null;
  const match = ran.stdout.match(/\{[\s\S]*"id":\s*"rec_[^"]+"[\s\S]*\}/);
  if (match) {
    try {
      parsed = JSON.parse(match[0]);
    } catch {
      parsed = null;
    }
  }
  results.push({
    ...page,
    exitCode: ran.code,
    recordingId: parsed?.id || "",
    capabilityCount: parsed?.capabilityCount ?? null,
    unresolved: parsed?.unresolved || [],
  });
}

const outFile = path.join(ROOT, "data", "matrix-recordings.json");
await mkdir(path.dirname(outFile), { recursive: true });
await writeFile(outFile, `${JSON.stringify({ capturedAt: new Date().toISOString(), results }, null, 2)}\n`);
console.log(`\nwrote ${outFile}`);
for (const row of results) {
  console.log(
    `${row.exitCode === 0 ? "ok" : "fail"} ${row.capabilityCount ?? "-"}caps ${row.recordingId || "-"} ${row.text}`,
  );
}
