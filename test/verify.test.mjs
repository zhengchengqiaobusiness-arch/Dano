import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { writeSkillFile } from "../src/skillpack/files.mjs";
import { actionEvidenceSummary, verifySkill } from "../src/skillpack/verify.mjs";
import { skillDir } from "../src/paths.mjs";
import { appendEvidence } from "../src/evidence/store.mjs";
import { GUIDE_NAMES } from "../src/agent/guides.mjs";
import { applyVerifyToRecording, publicSessionStatus } from "../src/session.mjs";

const recording = { id: "rec_verify", tenant: "", subsystem: "app", skillId: "app.rec_verify", startUrl: "http://127.0.0.1/" };

async function useData() {
  process.env.CABP_DATA = await mkdtemp(path.join(os.tmpdir(), "cabp-verify-"));
}

test("verify reports each code", async () => {
  await useData();
  const dir = skillDir(recording.skillId);
  let result = await verifySkill(dir, recording.id, recording);
  assert.ok(result.errors.some((item) => item.code === "missing_file"));

  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "Bearer abc.def-token\n");
  await writeFile(path.join(dir, "scripts", "client.py"), "");
  await writeFile(path.join(dir, "references/api.md"), "");
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
  result = await verifySkill(dir, recording.id, recording);
  assert.ok(result.errors.some((item) => item.code === "secret_in_source"));

  await writeFile(path.join(dir, "SKILL.md"), "python scripts/client.py list\n");
  result = await verifySkill(dir, recording.id, recording);
  assert.ok(result.errors.some((item) => item.code === "command_not_run"));

  await writeFile(path.join(dir, "references/api.md"), "## 字段\n- page_name: p\n- evidence_ids: missing_id\n\n## 未解决\n- field: pendingField\n\n## 已验证读命令\n");
  await writeFile(path.join(dir, "SKILL.md"), "python scripts/client.py list\npendingField\n");
  result = await verifySkill(dir, recording.id, recording);
  assert.ok(result.errors.some((item) => item.code === "evidence_missing"));

  await writeSkillFile(recording.id, recording.skillId, "SKILL.md", "python scripts/client.py list\n");
  await writeFile(path.join(dir, "SKILL.md"), "python scripts/client.py list\nchanged\n");
  result = await verifySkill(dir, recording.id, recording);
  assert.ok(result.errors.some((item) => item.code === "handbook_rewritten"));
});

test("templates and writes are not re-run, filled names stay on the result", async () => {
  await useData();
  const dir = skillDir(recording.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\n---\npython scripts/client.py list-statistics <deptId>\npython scripts/client.py submit-report {\"a\":1}\npython3 scripts/client.py dept-list\n标题\n工作内容\n");
  await writeFile(path.join(dir, "scripts/client.py"), "def list_statistics():\n    method = \"GET\"\n\ndef submit_report():\n    method = \"POST\"\n\ndef dept_list():\n    return \"/admin-api/system/dept/simple-list\"\n");
  await writeFile(path.join(dir, "references/api.md"), "");
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
  await appendEvidence(recording.id, {
    kind: "verify",
    ok: true,
    argv: ["python", "scripts/client.py", "list-statistics", "104"],
    summary: "python scripts/client.py list-statistics 104",
  });
  await appendEvidence(recording.id, {
    kind: "verify",
    ok: true,
    argv: ["python", "scripts/client.py", "dept-list"],
    summary: "python scripts/client.py dept-list",
  });
  await appendEvidence(recording.id, { kind: "action", summary: "fill:textbox \"标题\"|textbox \"备注\"|textbox \"请输入工作内容\"" });
  await appendEvidence(recording.id, {
    kind: "network",
    summary: "GET /admin-api/system/dept/simple-list",
    body: {
      method: "GET",
      path: "/admin-api/system/dept/simple-list",
      response_body: JSON.stringify({ data: [{ id: 1, name: "甲" }, { id: 2, name: "乙" }] }),
    },
  });
  await appendEvidence(recording.id, {
    kind: "network",
    summary: "GET /admin-api/system/dict-type/list-all-simple",
    body: {
      method: "GET",
      path: "/admin-api/system/dict-type/list-all-simple",
      response_body: JSON.stringify({ data: [{ id: 1, name: "甲" }, { id: 2, name: "乙" }] }),
    },
  });
  const result = await verifySkill(dir, recording.id, recording);
  assert.equal(result.errors.some((item) => item.command && item.command.includes("list-statistics")), false);
  assert.equal(result.errors.some((item) => item.command && item.command.includes("submit-report")), false);
  assert.equal(result.errors.some((item) => item.code === "caller_field_missing" || item.code === "live_options_missing"), false);
  assert.ok(result.filled.some((label) => label.includes("标题")));
  assert.ok(result.filled.some((label) => label.includes("工作内容")));
  assert.ok(result.requests.some((item) => item.path === "/admin-api/system/dept/simple-list"));
});

test("verify returns the request keys and filled names without judging them", async () => {
  await useData();
  const shape = { id: "rec_verify_shape", tenant: "", subsystem: "app", skillId: "app.rec_verify_shape", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\n---\npython scripts/client.py show-config\n| 今日工作内容 | 是 |\n| 协调事项 | 否 |\n");
  await writeFile(path.join(dir, "scripts/client.py"), 'def show_config():\n    payload = {"subsystem": "oa", "owner": "1", "ownerName": "", "groupId": 103}\n    page = 20\n');
  await writeFile(path.join(dir, "references/api.md"), "## 未解决\n- path: /api/noise\n\nfiles[].kind\n");
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
  await appendEvidence(shape.id, {
    kind: "network",
    summary: "GET /api/session",
    body: { method: "GET", path: "/api/session", response_body: JSON.stringify({ data: { id: 1, groupId: 103, name: "甲", zone: "oa", pageSize: 20 } }) },
  });
  await appendEvidence(shape.id, {
    kind: "network",
    summary: "GET /api/options",
    body: { method: "GET", path: "/api/options", response_body: JSON.stringify({ data: [{ id: 10, name: "甲" }, { id: 11, name: "乙" }] }) },
  });
  await appendEvidence(shape.id, {
    kind: "network",
    summary: "GET /api/noise",
    body: { method: "GET", path: "/api/noise", response_body: JSON.stringify({ data: [{ id: 3, name: "丙" }, { id: 4, name: "丁" }] }) },
  });
  await appendEvidence(shape.id, {
    kind: "network",
    summary: "POST /api/upload",
    body: { method: "POST", path: "/api/upload", response_body: JSON.stringify({ data: "https://files.example/a.bin" }) },
  });
  await appendEvidence(shape.id, {
    kind: "network",
    summary: "POST /api/save",
    body: {
      method: "POST",
      path: "/api/save",
      post_data: JSON.stringify({ owner: "1", ownerName: "甲", groupId: 103, lines: [{ kind: 1, text: "甲" }] }),
      response_body: JSON.stringify({ data: 1 }),
    },
  });
  await appendEvidence(shape.id, { kind: "action", summary: "fill:textbox \"请输入协调事项\"|textbox \"请输入工作内容\"" });
  await appendEvidence(shape.id, { kind: "action", summary: "click", body: { uploaded: true, clicked: "button \"附件\"" } });
  const result = await verifySkill(dir, shape.id, shape);
  const judged = new Set(["caller_field_missing", "caller_field_optional", "caller_arg_missing", "sample_literal", "live_options_missing", "upload_not_written", "key_omitted", "follow_get_missing", "unresolved_adopted", "extra_required"]);
  assert.equal(result.errors.some((item) => judged.has(item.code)), false);
  assert.ok(result.filled.some((label) => label.includes("工作内容")));
  assert.ok(result.requests.some((item) => item.path === "/api/save" && item.keys.includes("owner")));
});

test("a key counts only inside the function that cites the path", async () => {
  await useData();
  const shape = { id: "rec_verify_gate", tenant: "", subsystem: "app", skillId: "app.rec_verify_gate", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\n---\n| 工作内容 | 是 |\n");
  await writeFile(path.join(dir, "scripts/client.py"), [
    "def save_item():",
    "    path = \"/api/save\"",
    "    if args.owner:",
    "        body[\"owner\"] = args.owner",
    "",
    "def list_items():",
    "    return item.get(\"state\")",
    "",
    "def dict_rows():",
    "    return \"/api/dict\"",
    "",
    "def main():",
    "    if args.command == \"dict-rows\":",
    "        dict_rows()",
    "    sub.add_parser(\"dict-rows\").add_argument(\"--kind\", required=True, help=\"类别\")",
    "    sub.add_parser(\"save\").add_argument(\"--title\", required=True, help=\"汇报标题\")",
  ].join("\n"));
  await writeFile(path.join(dir, "references/api.md"), "## 未解决\n- path: /api/dict\n");
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
  await appendEvidence(shape.id, {
    kind: "network",
    summary: "POST /api/save",
    body: {
      method: "POST",
      path: "/api/save",
      post_data: JSON.stringify({ owner: "甲", state: 2 }),
      response_body: JSON.stringify({ data: 41 }),
    },
  });
  await appendEvidence(shape.id, {
    kind: "network",
    summary: "GET /api/item",
    body: { method: "GET", path: "/api/item", query: "id=41", response_body: JSON.stringify({ data: { id: 41 } }) },
  });
  await appendEvidence(shape.id, {
    kind: "network",
    summary: "GET /api/dict",
    body: { method: "GET", path: "/api/dict", query: "", response_body: JSON.stringify({ data: {} }) },
  });
  await appendEvidence(shape.id, { kind: "action", summary: "fill:textbox \"请输入工作内容\"" });
  const result = await verifySkill(dir, shape.id, shape);
  assert.ok(result.errors.some((item) => item.code === "key_not_written" && item.key === "state"));
  assert.equal(result.errors.some((item) => item.key === "owner"), false);
  assert.equal(result.errors.some((item) => ["key_omitted", "follow_get_missing", "unresolved_adopted", "extra_required", "caller_arg_missing"].includes(item.code)), false);
  assert.ok(result.filled.some((label) => label.includes("工作内容")));
  assert.ok(result.requests.some((item) => item.path === "/api/save" && item.keys.includes("state") && item.keys.includes("owner")));
});

test("a click that writes a file is recorded as an upload, and a credential request must be replayed", async () => {
  assert.equal(actionEvidenceSummary("click", { uploaded: true, clicked: "button \"上传\"" }), "upload:button \"上传\"");
  assert.equal(actionEvidenceSummary("fill", { filled: ["textbox \"标题\""] }), "fill:textbox \"标题\"");
  await useData();
  const shape = { id: "rec_verify_cred", tenant: "", subsystem: "app", skillId: "app.rec_verify_cred", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\ndescription: sample\n---\n");
  await writeFile(path.join(dir, "scripts/client.py"), "import json\n");
  await writeFile(path.join(dir, "references/api.md"), "");
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
  await appendEvidence(shape.id, {
    kind: "network",
    summary: "POST /session/refresh",
    body: {
      method: "POST",
      path: "/session/refresh",
      response_body: JSON.stringify({ data: { accessToken: "abc12345", refreshToken: "ref12345" } }),
    },
  });
  const missing = await verifySkill(dir, shape.id, shape);
  assert.ok(missing.errors.some((item) => item.code === "credential_not_used"));
  assert.ok(missing.requests.some((item) => item.issues_credential === true));
  await writeFile(path.join(dir, "scripts/client.py"), "import json\ncredential = auth['credential']\n");
  const used = await verifySkill(dir, shape.id, shape);
  assert.equal(used.errors.some((item) => item.code === "credential_not_used"), false);
});

test("a script that imports outside the standard library cannot be called", async () => {
  await useData();
  const shape = { id: "rec_verify_stdlib", tenant: "", subsystem: "app", skillId: "app.rec_verify_stdlib", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\ndescription: sample\n---\n");
  await writeFile(path.join(dir, "scripts/client.py"), "import json\nimport urllib.request\ntry:\n    import requests\nexcept ImportError:\n    requests = None\n");
  await writeFile(path.join(dir, "references/api.md"), "");
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
  const blocked = await verifySkill(dir, shape.id, shape);
  assert.ok(blocked.errors.some((item) => item.code === "import_not_available" && item.name === "requests"));
  await writeFile(path.join(dir, "scripts/client.py"), "import json\nimport urllib.request\n");
  const allowed = await verifySkill(dir, shape.id, shape);
  assert.equal(allowed.errors.some((item) => item.code === "import_not_available"), false);
});

test("a GET command listed in the module docstring is still a read that must be run", async () => {
  await useData();
  const shape = { id: "rec_verify_docstring_read", tenant: "", subsystem: "app", skillId: "app.rec_verify_docstring_read", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\ndescription: sample\n---\npython scripts/client.py departments\npython scripts/client.py create --confirm\n");
  await writeFile(path.join(dir, "scripts/client.py"), [
    '"""python scripts/client.py departments',
    "python scripts/client.py create",
    '"""',
    'PATH_LIST = "/api/list"',
    'PATH_SAVE = "/api/save"',
    "def cmd_departments():",
    '    call("GET", PATH_LIST)',
    "def cmd_create():",
    '    call("POST", PATH_SAVE)',
    '    body["title"] = args.title',
  ].join("\n"));
  await writeFile(path.join(dir, "references/api.md"), "");
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{\"Authorization\":\"Bearer tok\"}}\n");
  await seedGuides(shape.id);
  await appendEvidence(shape.id, {
    kind: "network",
    summary: "GET /api/list",
    body: { method: "GET", path: "/api/list", query: "t=1" },
  });
  await appendEvidence(shape.id, {
    kind: "network",
    summary: "POST /api/save",
    body: { method: "POST", path: "/api/save", post_data: JSON.stringify({ title: "a" }) },
  });
  const result = await verifySkill(dir, shape.id, shape);
  assert.ok(result.errors.some((item) => item.code === "command_not_run" && String(item.command || "").includes("departments")));
});

test("a write-only skill is not forced to invent a GET", async () => {
  await useData();
  const shape = { id: "rec_verify_writeonly", tenant: "", subsystem: "app", skillId: "app.rec_verify_writeonly", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: save-item\ndescription: 调用方保存一条记录\n---\npython scripts/client.py save --confirm\n");
  await writeFile(path.join(dir, "scripts/client.py"), "def save():\n    method = \"POST\"\n    path = \"/api/save\"\n    body[\"title\"] = args.title\n");
  await writeFile(path.join(dir, "references/api.md"), "## 字段\n- page_name: 标题\n- evidence_ids: req_save\n");
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
  await appendEvidence(shape.id, {
    id: "req_save",
    kind: "network",
    summary: "POST /api/save",
    body: {
      method: "POST",
      path: "/api/save",
      post_data: JSON.stringify({ title: "hello" }),
      response_body: JSON.stringify({ data: 1 }),
    },
  });
  const result = await verifySkill(dir, shape.id, shape);
  assert.equal(result.errors.some((item) => item.code === "command_not_run"), false);
  assert.equal(result.errors.some((item) => item.code === "follow_get_missing"), false);
});

async function seedGuides(recordingId) {
  for (const name of GUIDE_NAMES) {
    await appendEvidence(recordingId, { kind: "guide", summary: name, body: name });
  }
}

test("a later successful run is not auth_expired, and a GET subcommand is not a write because its name sits near a POST path", async () => {
  await useData();
  const shape = { id: "rec_verify_auth_latest", tenant: "", subsystem: "app", skillId: "app.rec_verify_auth_latest", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\ndescription: sample\n---\npython scripts/client.py whoami\npython scripts/client.py statistics\n");
  await writeFile(path.join(dir, "scripts/client.py"), [
    "STATS = \"/api/stats\"",
    "SAVE = \"/api/save\"",
    "def cmd_whoami():",
    "    return True",
    "def cmd_statistics():",
    "    request(STATS)",
    "    params[\"deptId\"] = args.dept_id",
    "def cmd_submit():",
    "    request(SAVE)",
    "    body[\"title\"] = args.title",
    "credential = auth['credential']",
  ].join("\n"));
  await writeFile(path.join(dir, "references/api.md"), "");
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{\"Authorization\":\"Bearer tok\"}}\n");
  await seedGuides(shape.id);
  await appendEvidence(shape.id, {
    kind: "network",
    summary: "POST /api/save",
    body: { method: "POST", path: "/api/save", post_data: JSON.stringify({ title: "a" }), response_body: JSON.stringify({ data: { accessToken: "abc12345", refreshToken: "ref12345" } }) },
  });
  await appendEvidence(shape.id, {
    kind: "network",
    summary: "GET /api/stats",
    body: { method: "GET", path: "/api/stats", query: "deptId=1" },
  });
  await appendEvidence(shape.id, {
    kind: "verify",
    ok: false,
    argv: ["python", "scripts/client.py", "whoami"],
    stdout: "401 账号未登录",
  });
  await appendEvidence(shape.id, {
    kind: "verify",
    ok: true,
    argv: ["python", "scripts/client.py", "whoami"],
  });
  await appendEvidence(shape.id, {
    kind: "verify",
    ok: true,
    argv: ["python", "scripts/client.py", "statistics"],
  });
  const result = await verifySkill(dir, shape.id, shape);
  assert.equal(result.errors.some((item) => item.code === "auth_expired"), false);
  assert.equal(result.errors.some((item) => item.code === "command_not_run"), false);
  assert.equal(result.status, "skill_ready");
});

test("only the latest auth stop is auth_expired and does not fail the structure", async () => {
  await useData();
  const shape = { id: "rec_verify_auth_stop", tenant: "", subsystem: "app", skillId: "app.rec_verify_auth_stop", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\ndescription: sample\n---\npython scripts/client.py whoami\n");
  await writeFile(path.join(dir, "scripts/client.py"), "credential = auth['credential']\n");
  await writeFile(path.join(dir, "references/api.md"), "");
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{\"Authorization\":\"Bearer tok\"}}\n");
  await seedGuides(shape.id);
  await appendEvidence(shape.id, {
    kind: "network",
    summary: "POST /session/refresh",
    body: { method: "POST", path: "/session/refresh", response_body: JSON.stringify({ data: { accessToken: "abc12345", refreshToken: "ref12345" } }) },
  });
  await appendEvidence(shape.id, {
    kind: "verify",
    ok: true,
    argv: ["python", "scripts/client.py", "whoami"],
  });
  await appendEvidence(shape.id, {
    kind: "verify",
    ok: false,
    argv: ["python", "scripts/client.py", "whoami"],
    stdout: "{\"ok\": false, \"error\": \"refresh-token failed: 400 无效的刷新令牌\"}",
  });
  const result = await verifySkill(dir, shape.id, shape);
  assert.equal(result.errors.some((item) => item.code === "command_not_run"), false);
  assert.ok(result.errors.every((item) => item.code === "auth_expired"));
  assert.equal(result.status, "skill_written_needs_auth");
  const rec = { status: "recording", finished: false, verify: null, skillDir: "" };
  applyVerifyToRecording(rec, result, dir);
  assert.equal(rec.status, "skill_ready");
  assert.equal(rec.finished, true);
  assert.equal(publicSessionStatus(rec), "skill_ready");
  const failed = { status: "recording", finished: false };
  applyVerifyToRecording(failed, { status: "verify_failed", errors: [{ code: "command_not_run" }] });
  assert.equal(failed.status, "recording");
  assert.equal(failed.finished, false);
  assert.equal(publicSessionStatus(failed), "recording");
  assert.equal(publicSessionStatus({ status: "verify_failed" }), "failed");
  assert.equal(publicSessionStatus({ status: "waiting_operator" }), "recording");
});

test("a later script crash is not_invocable even if an earlier run stopped on 401", async () => {
  await useData();
  const shape = { id: "rec_verify_crash_latest", tenant: "", subsystem: "app", skillId: "app.rec_verify_crash_latest", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\ndescription: sample\n---\npython scripts/client.py departments\n");
  await writeFile(path.join(dir, "scripts/client.py"), "credential = auth['credential']\n");
  await writeFile(path.join(dir, "references/api.md"), "");
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{\"Authorization\":\"Bearer tok\"}}\n");
  await seedGuides(shape.id);
  await appendEvidence(shape.id, {
    kind: "verify",
    ok: false,
    argv: ["python", "scripts/client.py", "departments"],
    stdout: "{\"msg\": \"您的账号已经超时，请重新登录\", \"code\": 401}",
  });
  await appendEvidence(shape.id, {
    kind: "verify",
    ok: false,
    argv: ["python", "scripts/client.py", "departments"],
    stderr: "Traceback (most recent call last):\n  File \"scripts/client.py\", line 1, in <module>\nAttributeError: 'Namespace' object has no attribute 'func'",
  });
  const result = await verifySkill(dir, shape.id, shape);
  assert.ok(result.errors.some((item) => item.code === "not_invocable"));
  assert.equal(result.errors.some((item) => item.code === "auth_expired"), false);
  assert.equal(result.status, "verify_failed");
});
