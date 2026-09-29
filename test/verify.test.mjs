import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, utimes, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { writeSkillFile } from "../src/skillpack/files.mjs";
import { authHeaderOverlay, rebuildsCredentialQuery } from "../src/skillpack/request-keys.mjs";
import { actionEvidenceSummary, authBlocksRun, commandAuthFailed, filledLabels, labelTexts, readCommands, sourceHasSecret, verifySkill, writeOnDefault } from "../src/skillpack/verify.mjs";
import { skillDir } from "../src/paths.mjs";
import { appendEvidence } from "../src/evidence/store.mjs";
import { unactedGoalControls } from "../src/browser/snapshot.mjs";

const recording = { id: "rec_verify", tenant: "", subsystem: "app", skillId: "app.rec_verify", startUrl: "http://127.0.0.1/" };

async function useData() {
  process.env.CABP_DATA = await mkdtemp(path.join(os.tmpdir(), "cabp-verify-"));
}

test("a business 401 is not a successful command", () => {
  assert.equal(commandAuthFailed("{\n  \"code\": 401,\n  \"msg\": \"账号未登录\"\n}\n"), true);
  assert.equal(commandAuthFailed("{\n  \"code\": 0,\n  \"data\": [{\"id\": 401}]\n}\n"), false);
  assert.equal(commandAuthFailed("HTTP/1.1 401 Unauthorized"), true);
});

test("a dead credential blocks the next command until the file changes", () => {
  assert.equal(authBlocksRun("", "10"), false);
  assert.equal(authBlocksRun("10", "10"), true);
  assert.equal(authBlocksRun("10", "11"), false);
  assert.equal(authBlocksRun("10", "10", "1", "1"), true);
  assert.equal(authBlocksRun("10", "10", "2", "1"), false);
});

test("environment headers overlay the file headers", () => {
  const replaced = "env_headers = os.environ.get(\"DANO_AUTH_HEADERS\")\nif env_headers:\n    return json.loads(env_headers)\nreturn auth[\"headers\"]\n";
  const overlaid = "headers = dict(auth.get(\"headers\") or {})\nenv_headers = os.environ.get(\"DANO_AUTH_HEADERS\")\nif env_headers:\n    headers.update(json.loads(env_headers))\nreturn headers\n";
  assert.equal(authHeaderOverlay(replaced).replaces, true);
  assert.equal(authHeaderOverlay("return auth[\"headers\"]").missing, true);
  assert.equal(authHeaderOverlay(overlaid).replaces, false);
  assert.equal(authHeaderOverlay(overlaid).missing, false);
  const inline = "headers = dict(auth[\"headers\"])\nheaders.update(json.loads(os.environ[\"DANO_AUTH_HEADERS\"]))\nreturn headers\n";
  assert.equal(authHeaderOverlay(inline).missing, false);
  assert.equal(authHeaderOverlay(inline).replaces, false);
  assert.equal(authHeaderOverlay("print('ok')").needed, false);
  const wrapped = "env_headers = os.environ.get(\"DANO_AUTH_HEADERS\")\nif env_headers:\n    return {\"headers\": json.loads(env_headers)}\nreturn auth.get(\"headers\") or {}\n";
  assert.equal(authHeaderOverlay(wrapped).replaces, true);
  const assigned = "env_headers = os.environ.get(\"DANO_AUTH_HEADERS\")\nif env_headers:\n    auth[\"headers\"] = json.loads(env_headers)\nreturn auth.get(\"headers\") or {}\n";
  assert.equal(authHeaderOverlay(assigned).replaces, true);
  const replacedName = "headers = dict(auth[\"headers\"])\nenv_headers = os.environ.get(\"DANO_AUTH_HEADERS\")\nheaders = json.loads(env_headers)\nreturn headers\n";
  assert.equal(authHeaderOverlay(replacedName).replaces, true);
});

test("a write runs on the default path only when no subcommand selects it", () => {
  const bare = [
    "class Client:",
    "    def create_item(self):",
    "        urllib.request.Request(url, data=data, method='POST')",
    "def main():",
    "    if len(sys.argv) > 1 and sys.argv[1] == '--show-config':",
    "        return",
    "    try:",
    "        Client().create_item()",
  ].join("\n");
  const chosen = [
    "class Client:",
    "    def create_item(self):",
    "        urllib.request.Request(url, data=data, method='POST')",
    "def main():",
    "    command = sys.argv[1]",
    "    if command == 'show-config':",
    "        return",
    "    elif command == 'create-item':",
    "        Client().create_item()",
  ].join("\n");
  const parsed = [
    "def create_item():",
    "    api_request('/items/create', method='POST')",
    "def main():",
    "    args = parser.parse_args()",
    "    if args.action == 'show-config':",
    "        return",
    "    elif args.action == 'create':",
    "        create_item()",
  ].join("\n");
  const parsedOpen = [
    "def create_item():",
    "    api_request('/items/create', method='POST')",
    "def main():",
    "    args = parser.parse_args()",
    "    create_item()",
  ].join("\n");
  assert.equal(writeOnDefault(bare), true);
  assert.equal(writeOnDefault(chosen), false);
  assert.equal(writeOnDefault(parsed), false);
  assert.equal(writeOnDefault(parsedOpen), true);
  assert.equal(writeOnDefault("def create_item():\n    method = 'POST'\n"), false);
});

test("show-config with dashes is not a business read", async () => {
  await useData();
  const shape = { id: "rec_verify_config_only", tenant: "", subsystem: "app", skillId: "app.rec_verify_config_only", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\ndescription: sample\n---\npython scripts/client.py --show-config\n");
  await writeFile(path.join(dir, "scripts/client.py"), "def main():\n    return \"/items/list\"\n");
  await writeFile(path.join(dir, "references/api.md"), "");
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
  await appendEvidence(shape.id, {
    kind: "verify",
    ok: true,
    argv: ["python", "scripts/client.py", "--show-config"],
    summary: "python scripts/client.py --show-config",
  });
  const result = await verifySkill(dir, shape.id, shape);
  assert.ok(result.errors.some((item) => item.code === "command_not_run" && !item.command));
});

test("rebuilding the credential query with split is not a replay", () => {
  const rebuilt = "auth['credential']['url'] = auth['credential']['url'].split('?')[0] + '?token=' + new_token";
  const replaced = "credential['url'] = credential['url'].replace(old, new)";
  const parsed = [
    "def refresh(credential):",
    "    parsed = urllib.parse.urlparse(credential['url'])",
    "    qs = urllib.parse.parse_qs(parsed.query)",
    "    credential['url'] = urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(qs, doseq=True)))",
    "def list_items(params):",
    "    return urllib.parse.urlencode(params)",
  ].join("\n");
  assert.equal(rebuildsCredentialQuery(rebuilt), true);
  assert.equal(rebuildsCredentialQuery(replaced), false);
  assert.equal(rebuildsCredentialQuery(parsed), true);
  assert.equal(rebuildsCredentialQuery("# auth['credential']['url'] = x.split('?')[0] + '?a=' + b"), false);
});

test("a query credential copied into the script is a secret, a variable name is not", () => {
  assert.equal(sourceHasSecret("refresh_token = auth_config.get(\"refreshToken\", \"\")"), false);
  assert.equal(sourceHasSecret("url = base + \"/refresh-token?refreshToken=\" + refresh_token"), false);
  assert.equal(sourceHasSecret("refreshToken=abcdefghijklmnopqrstuvwxyz"), true);
});

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
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\n---\npython scripts/client.py list-statistics <deptId>\npython scripts/client.py submit-report {\"a\":1}\npython scripts/client.py create <json_file>\npython3 scripts/client.py dept-list\n标题\n工作内容\n");
  await writeFile(path.join(dir, "scripts/client.py"), "def list_statistics():\n    method = \"GET\"\n\ndef submit_report():\n    method = \"POST\"\n\ndef create_item():\n    return _request(\"POST\", \"/items\")\n\ndef dept_list():\n    return \"/admin-api/system/dept/simple-list\"\n");
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
  assert.equal(result.errors.some((item) => item.command && item.command.includes("create")), false);
  assert.equal(result.errors.some((item) => item.code === "caller_field_missing" || item.code === "live_options_missing"), false);
  assert.ok(result.filled.some((label) => label.includes("标题")));
  assert.ok(result.errors.some((item) => item.code === "filled_not_written" && item.field.includes("备注")));
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

test("an adopted path must cite an evidence id that exists", async () => {
  await useData();
  const shape = { id: "rec_verify_trace", tenant: "", subsystem: "app", skillId: "app.rec_verify_trace", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\ndescription: sample\n---\n");
  await writeFile(path.join(dir, "scripts/client.py"), "def save():\n    return \"/api/save\"\n");
  await writeFile(path.join(dir, "references/api.md"), "/api/save\n");
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
  const saved = await appendEvidence(shape.id, { kind: "network", summary: "POST /api/save", body: { method: "POST", path: "/api/save", post_data: "{\"title\":\"a\"}" } });
  const missing = await verifySkill(dir, shape.id, shape);
  assert.ok(missing.errors.some((item) => item.code === "evidence_missing" && item.path === "/api/save"));
  await writeFile(path.join(dir, "references/api.md"), `/api/save\nevidence_ids: ${saved.id}\n`);
  const traced = await verifySkill(dir, shape.id, shape);
  assert.equal(traced.errors.some((item) => item.code === "evidence_missing" && item.path === "/api/save"), false);
});

test("one run of a subcommand covers extra sample arguments, and the handbook needs a frontmatter block", async () => {
  await useData();
  const shape = { id: "rec_verify_cmd", tenant: "", subsystem: "app", skillId: "app.rec_verify_cmd", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "name: sample\ndescription: sample\npython scripts/client.py --list\npython scripts/client.py --list --category 示例\n");
  await writeFile(path.join(dir, "scripts/client.py"), "import json\n");
  await writeFile(path.join(dir, "references/api.md"), "");
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
  await appendEvidence(shape.id, { kind: "verify", ok: true, argv: ["python", "scripts/client.py", "--list"], summary: "python scripts/client.py --list" });
  const open = await verifySkill(dir, shape.id, shape);
  assert.ok(open.errors.some((item) => item.code === "not_invocable" && item.hint.includes("---")));
  assert.equal(open.errors.some((item) => item.command && item.command.includes("--category")), false);
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\ndescription: sample\n---\npython scripts/client.py --list\npython scripts/client.py --list --category 示例\n");
  const closed = await verifySkill(dir, shape.id, shape);
  assert.equal(closed.errors.some((item) => item.code === "not_invocable"), false);
});

test("a click that writes a file is recorded as an upload, and a credential request must be replayed", async () => {
  assert.equal(actionEvidenceSummary("click", { uploaded: true, clicked: "button \"上传\"" }), "upload:button \"上传\"");
  assert.equal(actionEvidenceSummary("click", { clicked: "button \"编辑\"", row: "中性笔" }), "click:button \"编辑\" row=\"中性笔\"");
  assert.equal(actionEvidenceSummary("click", { clicked: "button \"确定\"", popup: "提示" }), "click:button \"确定\" popup=\"提示\"");
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
  await writeFile(
    path.join(dir, "config/auth.local.json"),
    `${JSON.stringify({ headers: {}, credential: { method: "POST", url: "https://example.test/session/refresh?refreshToken=ref-old" } })}\n`,
  );
  const shaped = await verifySkill(dir, shape.id, shape);
  const credError = shaped.errors.find((item) => item.code === "credential_not_used");
  assert.deepEqual(credError.auth_file, { method: "POST", path: "/session/refresh", query: true });
  assert.equal(JSON.stringify(shaped).includes("ref-old"), false);
  await writeFile(path.join(dir, "scripts/client.py"), "import json\n# replay the credential by calling a fixed path\n");
  const comment = await verifySkill(dir, shape.id, shape);
  assert.ok(comment.errors.some((item) => item.code === "credential_not_used"));
  await writeFile(path.join(dir, "scripts/client.py"), "import json\n\"\"\"credential = auth['credential']\"\"\"\n");
  const docstring = await verifySkill(dir, shape.id, shape);
  assert.ok(docstring.errors.some((item) => item.code === "credential_not_used"));
  await writeFile(path.join(dir, "scripts/client.py"), "import json\ncredential = auth['credential']\nRequest(credential['url'], method=credential['method'])\n");
  const used = await verifySkill(dir, shape.id, shape);
  assert.equal(used.errors.some((item) => item.code === "credential_not_used"), false);
  await writeFile(
    path.join(dir, "scripts/client.py"),
    "import json\ncredential = auth['credential']\nRequest(credential['url'], method=credential['method'])\nurl = base + \"/session/refresh\"\n",
  );
  const rebuilt = await verifySkill(dir, shape.id, shape);
  assert.ok(rebuilt.errors.some((item) => item.code === "credential_not_used" && item.hint.includes("path") && item.hint.includes("查询值")));
  assert.ok(rebuilt.errors.some((item) => item.code === "evidence_missing" && item.path === "/session/refresh" && item.hint.includes("查询值")));
});

test("a write-only handbook still has to run a config check", async () => {
  await useData();
  const shape = { id: "rec_verify_write_only", tenant: "", subsystem: "app", skillId: "app.rec_verify_write_only", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  const client = [
    "import json, os, urllib.request",
    "def create_leave():",
    "    urllib.request.Request(base + \"/items/create\", data=b'{}', method='POST')",
    "def get_auth_headers(auth):",
    "    headers = dict(auth['headers'])",
    "    env_headers = os.environ.get('DANO_AUTH_HEADERS')",
    "    if env_headers:",
    "        headers.update(json.loads(env_headers))",
    "    return headers",
  ].join("\n");
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\ndescription: sample\n---\npython scripts/client.py create-leave --name <名称>\n");
  await writeFile(path.join(dir, "scripts/client.py"), client);
  await writeFile(path.join(dir, "references/api.md"), "req_1 POST /items/create\n");
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
  await appendEvidence(shape.id, {
    kind: "network",
    summary: "POST /items/create",
    body: { method: "POST", path: "/items/create", post_data: "{\"name\":\"甲\"}" },
  });
  const writeOnly = await verifySkill(dir, shape.id, shape);
  assert.ok(writeOnly.errors.some((item) => item.code === "command_not_run" && !item.command && String(item.hint).includes("show-config")));
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\ndescription: sample\n---\npython scripts/client.py show-config\npython scripts/client.py create-leave --name <名称>\n");
  const unread = await verifySkill(dir, shape.id, shape);
  assert.ok(unread.errors.some((item) => item.code === "command_not_run" && String(item.command || "").includes("show-config")));
  assert.equal(unread.errors.some((item) => item.code === "command_not_run" && String(item.command || "").includes("create-leave")), false);
  await appendEvidence(shape.id, {
    kind: "verify",
    ok: true,
    argv: ["python", "scripts/client.py", "show-config"],
    summary: "python scripts/client.py show-config",
  });
  const ran = await verifySkill(dir, shape.id, shape);
  assert.equal(ran.errors.some((item) => item.code === "command_not_run"), false);
});

test("a config check in the same function as a write is still a config check", async () => {
  await useData();
  const shape = { id: "rec_verify_config_same_fn", tenant: "", subsystem: "app", skillId: "app.rec_verify_config_same_fn", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  const client = [
    "import json, os, urllib.request",
    "def main():",
    "    if sys.argv[1] == 'show-config':",
    "        return",
    "    if sys.argv[1] == 'submit':",
    "        urllib.request.Request(base + \"/items/create\", data=b'{}', method='POST')",
  ].join("\n");
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\ndescription: sample\n---\npython scripts/client.py show-config\npython scripts/client.py submit\n");
  await writeFile(path.join(dir, "scripts/client.py"), client);
  await writeFile(path.join(dir, "references/api.md"), "req_1 POST /items/create\n");
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
  await appendEvidence(shape.id, {
    kind: "network",
    summary: "POST /items/create",
    body: { method: "POST", path: "/items/create", post_data: "{}" },
  });
  const unread = await verifySkill(dir, shape.id, shape);
  assert.ok(unread.errors.some((item) => item.code === "command_not_run" && String(item.command || "").includes("show-config")));
  assert.equal(unread.errors.some((item) => item.code === "command_not_run" && !item.command), false);
  await appendEvidence(shape.id, {
    kind: "verify",
    ok: true,
    argv: ["python", "scripts/client.py", "show-config"],
    summary: "python scripts/client.py show-config",
  });
  const ran = await verifySkill(dir, shape.id, shape);
  assert.equal(ran.errors.some((item) => item.code === "command_not_run"), false);
});

test("a script that never mentions an evidence path is not callable", async () => {
  await useData();
  const shape = { id: "rec_verify_no_path", tenant: "", subsystem: "app", skillId: "app.rec_verify_no_path", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\ndescription: sample\n---\npython scripts/client.py show-config\n");
  await writeFile(path.join(dir, "scripts/client.py"), "def open_form():\n    return {\"action\": \"open\"}\n");
  await writeFile(path.join(dir, "references/api.md"), "open the page\n");
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
  await appendEvidence(shape.id, {
    kind: "network",
    summary: "GET /items/get",
    body: { method: "GET", path: "/items/get" },
  });
  const missing = await verifySkill(dir, shape.id, shape);
  assert.ok(missing.errors.some((item) => item.code === "path_not_in_evidence" && item.file === "scripts/client.py" && !item.path));
  await writeFile(path.join(dir, "scripts/client.py"), "def get_item():\n    return urllib.request.Request(base + \"/items/get\")\n");
  const cited = await verifySkill(dir, shape.id, shape);
  assert.equal(cited.errors.some((item) => item.code === "path_not_in_evidence" && !item.path), false);
});

test("a read command is not skipped because its name sits near a write path", async () => {
  await useData();
  const shape = { id: "rec_verify_read_near_write", tenant: "", subsystem: "app", skillId: "app.rec_verify_read_near_write", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\ndescription: sample\n---\n- `python scripts/client.py get <id>`\n- `python scripts/client.py create <json>`\n");
  await writeFile(path.join(dir, "scripts/client.py"), "def get_item(item_id):\n    return api_request(\"GET\", \"/items/get\", params={\"id\": item_id})\n\ndef create_item():\n    return api_request(\"POST\", \"/items/create\", data={})\n");
  await writeFile(path.join(dir, "references/api.md"), "/items/get req_1\n/items/create req_2\n");
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
  await appendEvidence(shape.id, {
    kind: "network",
    summary: "POST /items/create",
    body: { method: "POST", path: "/items/create", post_data: "{\"name\":\"甲\"}" },
  });
  await appendEvidence(shape.id, {
    kind: "network",
    summary: "GET /items/get",
    body: { method: "GET", path: "/items/get", query: "id=1" },
  });
  const result = await verifySkill(dir, shape.id, shape);
  assert.ok(result.errors.some((item) => item.code === "command_not_run" && String(item.command || "").includes("get")));
  assert.equal(result.errors.some((item) => item.code === "command_not_run" && String(item.command || "").includes("create")), false);
});

test("a handbook command inside a markdown bullet still counts", () => {
  assert.deepEqual(readCommands("- `python scripts/client.py list` - 列出\npython scripts/client.py get <id>\n"), [
    "python scripts/client.py list",
    "python scripts/client.py get <id>",
  ]);
});

test("a leading required mark is not part of the field name", () => {
  assert.ok(labelTexts('textbox "* 备注"').includes("备注"));
  assert.ok(labelTexts('spinbutton "* 数量"').includes("数量"));
  assert.deepEqual(labelTexts('textbox "备注"'), ["备注"]);
  assert.deepEqual(labelTexts('textbox "名称" popup="提示" row="甲"'), ["名称"]);
  assert.equal(labelTexts('spinbutton "数量 确定"', ["确定"]).includes("数量"), false);
  assert.ok(labelTexts('spinbutton "* 数量 查看填写说明"', ["查看填写说明"]).includes("数量"));
  assert.deepEqual(filledLabels([{ kind: "action", summary: 'fill:textbox "名称" row="甲 | 乙"|textbox "备注"' }]), [
    'textbox "名称" row="甲 | 乙"',
    'textbox "备注"',
  ]);
});

test("a different key set is not the baseline when verify builds the request index", async () => {
  await useData();
  const shape = { id: "rec_verify_shape_keys", tenant: "", subsystem: "app", skillId: "app.rec_verify_shape_keys", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\ndescription: sample\n---\n");
  await writeFile(path.join(dir, "scripts/client.py"), "import json\n");
  await writeFile(path.join(dir, "references/api.md"), "");
  await appendEvidence(shape.id, { kind: "network", summary: "POST /save", body: { method: "POST", path: "/save", post_data: "{\"title\":\"a\"}" } });
  await appendEvidence(shape.id, { kind: "network", summary: "POST /save", body: { method: "POST", path: "/save", post_data: "{\"title\":\"b\",\"id\":\"1\"}" } });
  await appendEvidence(shape.id, { kind: "network", summary: "POST /save", body: { method: "POST", path: "/save", post_data: "{\"title\":\"c\"}" } });
  const result = await verifySkill(dir, shape.id, shape);
  const rows = result.requests.filter((row) => row.path === "/save");
  assert.equal(rows.length, 2);
  const created = rows.find((row) => !row.keys.includes("id"));
  const updated = rows.find((row) => row.keys.includes("id"));
  assert.deepEqual(created.changed_keys, ["title"]);
  assert.equal(updated.changed_keys, undefined);
});

test("a cited request stays closed until its body has been opened", async () => {
  await useData();
  const shape = { id: "rec_verify_opened", tenant: "", subsystem: "app", skillId: "app.rec_verify_opened", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\ndescription: sample\n---\n");
  await writeFile(path.join(dir, "scripts/client.py"), "import json\n");
  const saved = await appendEvidence(shape.id, {
    kind: "network",
    summary: "POST /save",
    body: { method: "POST", path: "/save", post_data: "{\"title\":\"甲\"}" },
  });
  const credential = await appendEvidence(shape.id, {
    kind: "network",
    summary: "POST /session/refresh",
    body: {
      method: "POST",
      path: "/session/refresh",
      response_body: JSON.stringify({ data: { accessToken: "abc12345", refreshToken: "ref12345" } }),
    },
  });
  await writeFile(path.join(dir, "references/api.md"), `${saved.id}\n${credential.id}\n`);
  const closed = await verifySkill(dir, shape.id, shape);
  assert.ok(closed.errors.some((item) => item.code === "evidence_not_opened" && item.id === saved.id));
  assert.equal(closed.errors.some((item) => item.code === "evidence_not_opened" && item.id === credential.id), false);
  await appendEvidence(shape.id, { kind: "read", summary: saved.id, body_missing: true });
  const opened = await verifySkill(dir, shape.id, shape);
  assert.equal(opened.errors.some((item) => item.code === "evidence_not_opened"), false);
});

test("a command already run is returned when the handbook has no matching line", async () => {
  await useData();
  const shape = { id: "rec_verify_ran", tenant: "", subsystem: "app", skillId: "app.rec_verify_ran", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\ndescription: sample\n---\n| 数量 | 是 |\n");
  await writeFile(path.join(dir, "scripts/client.py"), "import json\n");
  await writeFile(path.join(dir, "references/api.md"), "");
  await appendEvidence(shape.id, {
    kind: "verify",
    ok: false,
    argv: ["python", "scripts/client.py", "list"],
    summary: "python scripts/client.py list",
  });
  const result = await verifySkill(dir, shape.id, shape);
  const error = result.errors.find((item) => item.code === "command_not_run");
  assert.ok(error.ran.includes("python scripts/client.py list"));
});

test("the handbook names the field without a button suffix copied from the snapshot", async () => {
  await useData();
  const shape = { id: "rec_verify_suffix", tenant: "", subsystem: "app", skillId: "app.rec_verify_suffix", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\ndescription: sample\n---\n| 数量 | 是 |\n");
  await writeFile(path.join(dir, "scripts/client.py"), "import json\n");
  await writeFile(path.join(dir, "references/api.md"), "");
  await appendEvidence(shape.id, {
    kind: "snapshot",
    summary: "snapshot",
    body: { text: '- button "查看填写说明" ref=f0:e1@1\n- spinbutton "* 数量 查看填写说明" ref=f0:e2@1\n' },
  });
  await appendEvidence(shape.id, { kind: "action", summary: 'fill:spinbutton "* 数量 查看填写说明" popup="dialog"' });
  const result = await verifySkill(dir, shape.id, shape);
  assert.equal(result.errors.some((item) => item.code === "filled_not_written"), false);
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

test("a zero-exit login failure is not a finished read", async () => {
  await useData();
  const shape = { id: "rec_verify_auth", tenant: "", subsystem: "app", skillId: "app.rec_verify_auth", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\ndescription: sample\n---\n- `python scripts/client.py list`\n");
  await writeFile(path.join(dir, "scripts/client.py"), "import json\nprint('ok')\n");
  await writeFile(path.join(dir, "references/api.md"), "");
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
  await appendEvidence(shape.id, {
    kind: "verify",
    ok: true,
    argv: ["python", "scripts/client.py", "list"],
    stdout: "{\"code\":401,\"msg\":\"账号未登录\"}\n",
    summary: "python scripts/client.py list",
  });
  const rejected = await verifySkill(dir, shape.id, shape);
  assert.ok(rejected.errors.some((item) => item.code === "auth_expired"));
  assert.equal(rejected.status, "verify_failed");
  assert.equal(rejected.errors.some((item) => item.code === "command_not_run" && String(item.command || "").includes("list")), false);
  await appendEvidence(shape.id, {
    kind: "verify",
    ok: true,
    argv: ["python", "scripts/client.py", "list"],
    stdout: "{\"code\":0,\"data\":[{\"id\":401}]}\n",
    summary: "python scripts/client.py list",
  });
  const accepted = await verifySkill(dir, shape.id, shape);
  assert.equal(accepted.errors.some((item) => item.code === "auth_expired"), false);
  assert.equal(accepted.errors.some((item) => item.code === "command_not_run" && String(item.command || "").includes("list")), false);
});

test("a login failure against an older script does not freeze the current script", async () => {
  await useData();
  const shape = { id: "rec_verify_auth_retry", tenant: "", subsystem: "app", skillId: "app.rec_verify_auth_retry", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  const client = path.join(dir, "scripts", "client.py");
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\ndescription: sample\n---\n- `python scripts/client.py list`\n");
  await writeFile(client, "import json\nprint('old')\n");
  await writeFile(path.join(dir, "references/api.md"), "");
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
  await appendEvidence(shape.id, {
    kind: "verify",
    ok: false,
    argv: ["python", "scripts/client.py", "list"],
    stdout: "{\"code\":401,\"msg\":\"账号未登录\"}\n",
    summary: "python scripts/client.py list",
  });
  const failed = await verifySkill(dir, shape.id, shape);
  assert.ok(failed.errors.some((item) => item.code === "auth_expired"));
  const later = new Date(Date.now() + 2000);
  await utimes(client, later, later);
  const rewritten = await verifySkill(dir, shape.id, shape);
  assert.equal(rewritten.errors.some((item) => item.code === "auth_expired"), false);
  assert.ok(rewritten.errors.some((item) => item.code === "auth_unproven"));
  assert.equal(rewritten.status, "verify_failed");
});

test("a goal control still on the latest snapshot has to be acted before the skill is ready", async () => {
  const snapshot = [
    "epoch 1 snap 2",
    "goal_exact",
    "- button \"提 交\" ref=f0:e9@2",
    "- button \"保存\" [disabled] ref=f0:e8@2",
    "- button \"上传\"",
    "fields",
    "- button \"别的\" ref=f0:e1@2",
  ].join("\n");
  assert.deepEqual(unactedGoalControls(snapshot, []), ["- button \"提 交\""]);
  assert.deepEqual(unactedGoalControls(snapshot, ["click:button \"提 交\""]), []);
  assert.deepEqual(unactedGoalControls("epoch 1 snap 2\nfields\n- button \"提 交\" ref=f0:e9@2\n", []), []);
  await useData();
  const shape = { id: "rec_verify_goal", tenant: "", subsystem: "app", skillId: "app.rec_verify_goal", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\ndescription: sample\n---\n");
  await writeFile(path.join(dir, "scripts/client.py"), "import json\n");
  await writeFile(path.join(dir, "references/api.md"), "");
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
  await appendEvidence(shape.id, { kind: "snapshot", summary: "snapshot", body: { text: snapshot } });
  const open = await verifySkill(dir, shape.id, shape);
  assert.ok(open.errors.some((item) => item.code === "goal_not_acted" && item.name === "- button \"提 交\""));
  await appendEvidence(shape.id, { kind: "action", summary: "click:button \"提 交\"", body: { ok: true } });
  const done = await verifySkill(dir, shape.id, shape);
  assert.equal(done.errors.some((item) => item.code === "goal_not_acted"), false);
});
