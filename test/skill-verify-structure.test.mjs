import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { GUIDE_NAMES } from "../src/agent/guides.mjs";
import { nextRecordingPrompt, factsForContinue, continueIncludesTree, continueCountAfterSkillTool } from "../src/agent/pi-session.mjs";
import { appendEvidence, writeInitialGoal } from "../src/evidence/store.mjs";
import { verifySkill } from "../src/skillpack/verify.mjs";
import { writeSkillFile } from "../src/skillpack/files.mjs";

async function withDataRoot(run) {
  const root = await mkdtemp(path.join(os.tmpdir(), "cabp-verify-"));
  const previous = process.env.CABP_DATA;
  process.env.CABP_DATA = root;
  try {
    return await run(root);
  } finally {
    if (previous === undefined) delete process.env.CABP_DATA;
    else process.env.CABP_DATA = previous;
    await rm(root, { recursive: true, force: true });
  }
}

test("a three-file skill that cites evidence paths and credential passes structural verify", async () => {
  await withDataRoot(async (root) => {
    const recordingId = "rec_structure1";
    const skillId = "app.rec_structure1";
    const dir = path.join(root, "skills", skillId);
    await writeInitialGoal(recordingId, { page_url: "http://127.0.0.1/", goal_text: "列出记录" });
    for (const name of GUIDE_NAMES) {
      await appendEvidence(recordingId, { kind: "guide", summary: name, body: name, body_missing: false });
    }
    await appendEvidence(recordingId, {
      id: "req_1",
      kind: "network",
      summary: "GET /api/items",
      body: {
        method: "GET",
        path: "/api/items",
        query: "deptId=1",
        post_data: "",
        response_body: "{\"data\":[]}",
      },
    });
    await appendEvidence(recordingId, {
      id: "req_2",
      kind: "network",
      summary: "POST /auth/refresh",
      body: {
        method: "POST",
        path: "/auth/refresh",
        query: "refreshToken=r1",
        post_data: "",
        response_body: JSON.stringify({ accessToken: "a1", refreshToken: "r2" }),
      },
    });
    await appendEvidence(recordingId, {
      kind: "verify",
      ok: true,
      argv: ["python", "scripts/client.py", "list"],
      summary: "python scripts/client.py list",
      stdout: "{}",
      stderr: "",
      body: "{}",
    });
    await mkdir(path.join(dir, "scripts"), { recursive: true });
    await mkdir(path.join(dir, "references"), { recursive: true });
    await mkdir(path.join(dir, "config"), { recursive: true });
    await writeFile(path.join(dir, "SKILL.md"), [
      "---",
      "name: list-items",
      "description: 列出本页记录",
      "---",
      "python scripts/client.py list",
      "",
    ].join("\n"));
    await writeFile(path.join(dir, "scripts/client.py"), [
      "def list_items():",
      "    credential = auth[\"credential\"]",
      "    accessToken = payload[\"accessToken\"]",
      "    refreshToken = payload[\"refreshToken\"]",
      "    headers[\"Authorization\"] = accessToken",
      "    path = \"/api/items\"",
      "    return get(path, deptId=deptId)",
      "",
    ].join("\n"));
    await writeFile(path.join(dir, "references/api.md"), [
      "GET /api/items",
      "deptId comes from the caller.",
      "evidence req_1",
      "",
    ].join("\n"));
    await writeFile(path.join(dir, "config/runtime.json"), "{\"base_url\":\"http://127.0.0.1\"}\n");
    await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
    const result = await verifySkill(dir, recordingId, { skillId });
    assert.equal(result.errors.some((item) => item.code === "field_unaccounted"), false);
    assert.equal(result.errors.some((item) => item.code === "credential_not_used"), false);
    assert.deepEqual(result.errors, []);
    assert.equal(result.ok, true);
  });
});

test("a credential-issuing response requires the issued field names in the client", async () => {
  await withDataRoot(async (root) => {
    const recordingId = "rec_issued_missing";
    const skillId = "app.rec_issued_missing";
    const dir = path.join(root, "skills", skillId);
    await writeInitialGoal(recordingId, { page_url: "http://127.0.0.1/", goal_text: "列出记录" });
    for (const name of GUIDE_NAMES) {
      await appendEvidence(recordingId, { kind: "guide", summary: name, body: name, body_missing: false });
    }
    await appendEvidence(recordingId, {
      id: "req_1",
      kind: "network",
      summary: "GET /api/items",
      body: { method: "GET", path: "/api/items", query: "", post_data: "", response_body: "{}" },
    });
    await appendEvidence(recordingId, {
      id: "req_2",
      kind: "network",
      summary: "POST /auth/refresh",
      body: {
        method: "POST",
        path: "/auth/refresh",
        query: "refreshToken=r1",
        post_data: "",
        response_body: JSON.stringify({ accessToken: "a1", refreshToken: "r2" }),
      },
    });
    await appendEvidence(recordingId, {
      kind: "verify",
      ok: true,
      argv: ["python", "scripts/client.py", "list"],
      summary: "python scripts/client.py list",
      stdout: "{}",
      stderr: "",
      body: "{}",
    });
    await mkdir(path.join(dir, "scripts"), { recursive: true });
    await mkdir(path.join(dir, "references"), { recursive: true });
    await mkdir(path.join(dir, "config"), { recursive: true });
    await writeFile(path.join(dir, "SKILL.md"), "---\nname: list-items\ndescription: 列出本页记录\n---\npython scripts/client.py list\n");
    await writeFile(path.join(dir, "scripts/client.py"), "def list_items():\n    credential = auth[\"credential\"]\n    return get('/api/items')\n");
    await writeFile(path.join(dir, "references/api.md"), "GET /api/items\n");
    await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
    await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
    const result = await verifySkill(dir, recordingId, { skillId });
    assert.equal(result.errors.some((item) => item.code === "key_not_written" && item.key === "accessToken"), true);
    assert.equal(result.errors.some((item) => item.code === "key_not_written" && item.key === "refreshToken"), true);
  });
});

test("verify does not demand a ## 字段 table", async () => {
  await withDataRoot(async (root) => {
    const recordingId = "rec_structure2";
    const skillId = "app.rec_structure2";
    const dir = path.join(root, "skills", skillId);
    await writeInitialGoal(recordingId, { page_url: "http://127.0.0.1/", goal_text: "列出记录" });
    for (const name of GUIDE_NAMES) {
      await appendEvidence(recordingId, { kind: "guide", summary: name, body: name, body_missing: false });
    }
    await appendEvidence(recordingId, {
      id: "req_1",
      kind: "network",
      summary: "GET /api/items",
      body: { method: "GET", path: "/api/items", query: "deptId=1", post_data: "", response_body: "{}" },
    });
    await appendEvidence(recordingId, {
      kind: "verify",
      ok: true,
      argv: ["python", "scripts/client.py", "list"],
      summary: "python scripts/client.py list",
      stdout: "{}",
      stderr: "",
      body: "{}",
    });
    await mkdir(path.join(dir, "scripts"), { recursive: true });
    await mkdir(path.join(dir, "references"), { recursive: true });
    await mkdir(path.join(dir, "config"), { recursive: true });
    await writeFile(path.join(dir, "SKILL.md"), "---\nname: list-items\ndescription: 列出本页记录\n---\npython scripts/client.py list\n");
    await writeFile(path.join(dir, "scripts/client.py"), "def list_items():\n    return get('/api/items', deptId=deptId)\n");
    await writeFile(path.join(dir, "references/api.md"), "## GET /api/items\n\n- request_key: deptId\n");
    await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
    await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
    const result = await verifySkill(dir, recordingId, { skillId });
    assert.equal(result.errors.some((item) => item.code === "field_unaccounted"), false);
    assert.deepEqual(result.errors, []);
  });
});

test("verify does not fail the handbook for keys the client already writes", async () => {
  await withDataRoot(async (root) => {
    const recordingId = "rec_structure3";
    const skillId = "app.rec_structure3";
    const dir = path.join(root, "skills", skillId);
    await writeInitialGoal(recordingId, { page_url: "http://127.0.0.1/", goal_text: "列出记录" });
    for (const name of GUIDE_NAMES) {
      await appendEvidence(recordingId, { kind: "guide", summary: name, body: name, body_missing: false });
    }
    await appendEvidence(recordingId, {
      id: "req_1",
      kind: "network",
      summary: "GET /api/items",
      body: { method: "GET", path: "/api/items", query: "deptId=1&pageNo=1", post_data: "", response_body: "{}" },
    });
    await appendEvidence(recordingId, {
      kind: "verify",
      ok: true,
      argv: ["python", "scripts/client.py", "list"],
      summary: "python scripts/client.py list",
      stdout: "{}",
      stderr: "",
      body: "{}",
    });
    await mkdir(path.join(dir, "scripts"), { recursive: true });
    await mkdir(path.join(dir, "references"), { recursive: true });
    await mkdir(path.join(dir, "config"), { recursive: true });
    await writeFile(path.join(dir, "SKILL.md"), "---\nname: list-items\ndescription: 列出本页记录\n---\npython scripts/client.py list\n");
    await writeFile(path.join(dir, "scripts/client.py"), "def list_items():\n    return get('/api/items', deptId=deptId, pageNo=pageNo)\n");
    await writeFile(path.join(dir, "references/api.md"), "GET /api/items\n");
    await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
    await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
    const result = await verifySkill(dir, recordingId, { skillId });
    assert.equal(result.errors.some((item) => item.code === "key_not_written" && item.file === "references/api.md"), false);
    assert.deepEqual(result.errors, []);
  });
});

test("verify accepts keys that sit next to a path table", async () => {
  await withDataRoot(async (root) => {
    const recordingId = "rec_structure_table";
    const skillId = "app.rec_structure_table";
    const dir = path.join(root, "skills", skillId);
    await writeInitialGoal(recordingId, { page_url: "http://127.0.0.1/", goal_text: "列出记录" });
    for (const name of GUIDE_NAMES) {
      await appendEvidence(recordingId, { kind: "guide", summary: name, body: name, body_missing: false });
    }
    await appendEvidence(recordingId, {
      id: "req_1",
      kind: "network",
      summary: "GET /api/items",
      body: { method: "GET", path: "/api/items", query: "deptId=1", post_data: "", response_body: "{}" },
    });
    await appendEvidence(recordingId, {
      kind: "verify",
      ok: true,
      argv: ["python", "scripts/client.py", "list"],
      summary: "python scripts/client.py list",
      stdout: "{}",
      stderr: "",
      body: "{}",
    });
    await mkdir(path.join(dir, "scripts"), { recursive: true });
    await mkdir(path.join(dir, "references"), { recursive: true });
    await mkdir(path.join(dir, "config"), { recursive: true });
    await writeFile(path.join(dir, "SKILL.md"), "---\nname: list-items\ndescription: 列出本页记录\n---\npython scripts/client.py list\n");
    await writeFile(path.join(dir, "scripts/client.py"), [
      "PATHS = {\"list\": \"/api/items\"}",
      "def list_items():",
      "    return get(PATHS[\"list\"], params={\"deptId\": dept_id})",
      "",
    ].join("\n"));
    await writeFile(path.join(dir, "references/api.md"), "GET /api/items\n");
    await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
    await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
    const result = await verifySkill(dir, recordingId, { skillId });
    assert.equal(result.errors.some((item) => item.code === "key_not_written"), false, JSON.stringify(result.errors));
    assert.deepEqual(result.errors, []);
  });
});

test("verify does not reject a pathname that appeared in a recorded response", async () => {
  await withDataRoot(async (root) => {
    const recordingId = "rec_structure_upload_url";
    const skillId = "app.rec_structure_upload_url";
    const dir = path.join(root, "skills", skillId);
    await writeInitialGoal(recordingId, { page_url: "http://127.0.0.1/", goal_text: "新增一条记录" });
    for (const name of GUIDE_NAMES) {
      await appendEvidence(recordingId, { kind: "guide", summary: name, body: name, body_missing: false });
    }
    await appendEvidence(recordingId, {
      id: "req_1",
      kind: "network",
      summary: "POST /api/items",
      body: { method: "POST", path: "/api/items", query: "", post_data: "{\"title\":\"甲\"}", response_body: "{}" },
    });
    await appendEvidence(recordingId, {
      id: "req_2",
      kind: "network",
      summary: "POST /admin-api/infra/file/upload",
      body: {
        method: "POST",
        path: "/admin-api/infra/file/upload",
        url: "http://127.0.0.1/admin-api/infra/file/upload",
        query: "",
        post_data: "",
        response_body: "{\"data\":\"http://127.0.0.1/admin-api/infra/file/29/get/20261002/x/attachment.png\"}",
      },
    });
    await mkdir(path.join(dir, "scripts"), { recursive: true });
    await mkdir(path.join(dir, "references"), { recursive: true });
    await mkdir(path.join(dir, "config"), { recursive: true });
    await writeFile(path.join(dir, "SKILL.md"), [
      "---",
      "name: create-item",
      "description: 新增一条记录",
      "---",
      "python scripts/client.py create --title <title> --confirm",
      "",
    ].join("\n"));
    await writeFile(path.join(dir, "scripts/client.py"), [
      "def create(title, confirm=False):",
      "    if not confirm:",
      "        return {\"preview\": True}",
      "    return post(\"/api/items\", title=title)",
      "",
    ].join("\n"));
    await writeFile(path.join(dir, "references/api.md"), [
      "POST /api/items",
      "POST /admin-api/infra/file/upload",
      "file /admin-api/infra/file/29/get/20261002/x/attachment.png",
      "",
    ].join("\n"));
    await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
    await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
    const result = await verifySkill(dir, recordingId, { skillId });
    assert.equal(result.errors.some((item) => item.code === "path_not_in_evidence"), false, JSON.stringify(result.errors));
    assert.deepEqual(result.errors, []);
  });
});

test("a write-only skill with --confirm and recorded POST passes without a GET command", async () => {
  await withDataRoot(async (root) => {
    const recordingId = "rec_structure_write";
    const skillId = "app.rec_structure_write";
    const dir = path.join(root, "skills", skillId);
    await writeInitialGoal(recordingId, { page_url: "http://127.0.0.1/", goal_text: "新增一条记录" });
    for (const name of GUIDE_NAMES) {
      await appendEvidence(recordingId, { kind: "guide", summary: name, body: name, body_missing: false });
    }
    await appendEvidence(recordingId, {
      id: "req_1",
      kind: "network",
      summary: "POST /api/items",
      body: { method: "POST", path: "/api/items", query: "", post_data: "{\"title\":\"甲\"}", response_body: "{}" },
    });
    await mkdir(path.join(dir, "scripts"), { recursive: true });
    await mkdir(path.join(dir, "references"), { recursive: true });
    await mkdir(path.join(dir, "config"), { recursive: true });
    await writeFile(path.join(dir, "SKILL.md"), [
      "---",
      "name: create-item",
      "description: 新增一条记录",
      "---",
      "python scripts/client.py create --title <title> --confirm",
      "",
    ].join("\n"));
    await writeFile(path.join(dir, "scripts/client.py"), [
      "def create(title, confirm=False):",
      "    if not confirm:",
      "        return {\"preview\": True}",
      "    return post(\"/api/items\", title=title)",
      "",
    ].join("\n"));
    await writeFile(path.join(dir, "references/api.md"), "POST /api/items\n");
    await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
    await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
    const result = await verifySkill(dir, recordingId, { skillId });
    assert.equal(result.errors.some((item) => item.code === "command_not_run"), false, JSON.stringify(result.errors));
    assert.deepEqual(result.errors, []);
    assert.equal(result.ok, true);
  });
});

test("cited GET paths still require a read command when there is no recorded write", async () => {
  await withDataRoot(async (root) => {
    const recordingId = "rec_structure_read_missing";
    const skillId = "app.rec_structure_read_missing";
    const dir = path.join(root, "skills", skillId);
    await writeInitialGoal(recordingId, { page_url: "http://127.0.0.1/", goal_text: "列出记录" });
    for (const name of GUIDE_NAMES) {
      await appendEvidence(recordingId, { kind: "guide", summary: name, body: name, body_missing: false });
    }
    await appendEvidence(recordingId, {
      id: "req_1",
      kind: "network",
      summary: "GET /api/items",
      body: { method: "GET", path: "/api/items", query: "", post_data: "", response_body: "{}" },
    });
    await mkdir(path.join(dir, "scripts"), { recursive: true });
    await mkdir(path.join(dir, "references"), { recursive: true });
    await mkdir(path.join(dir, "config"), { recursive: true });
    await writeFile(path.join(dir, "SKILL.md"), "---\nname: list-items\ndescription: 列出本页记录\n---\n");
    await writeFile(path.join(dir, "scripts/client.py"), "def list_items():\n    return get('/api/items')\n");
    await writeFile(path.join(dir, "references/api.md"), "GET /api/items\n");
    await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
    await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
    const result = await verifySkill(dir, recordingId, { skillId });
    assert.equal(result.errors.some((item) => item.code === "command_not_run"), true, JSON.stringify(result.errors));
    assert.equal(result.ok, false);
  });
});

test("continue after a finished prompt still asks for the three files", () => {
  const text = nextRecordingPrompt({ finished: false, paused: false, progressed: false, continues: 7, verifyErrors: [] });
  assert.match(text, /Skill 还没产出/);
  assert.equal(nextRecordingPrompt({ finished: false, paused: false, progressed: false, continues: 8, verifyErrors: [] }), null);
});

test("continue packet keeps request facts and drops the tree after any round", () => {
  const afterClick = factsForContinue({
    goal: { goal_text: "列出记录" },
    requests: [{ path: "/api/items" }],
    filled: ["标题"],
    clicked: ["提交"],
    commands: ["python scripts/client.py list"],
    ran: [],
    snapshot: { evidence_id: "ev_1", epoch: 2, text: "- complementary\n- button \"提交\"" },
    index: [{ kind: "network", summary: "HEAD /web/" }],
  });
  assert.equal(afterClick.snapshot.text, undefined);
  assert.equal(afterClick.snapshot.evidence_id, "ev_1");
  assert.equal(afterClick.requests[0].path, "/api/items");
  assert.equal(afterClick.clicked[0], "提交");
  assert.deepEqual(afterClick.commands, ["python scripts/client.py list"]);
  assert.deepEqual(afterClick.ran, []);
  assert.equal(afterClick.index, undefined);
  const afterGuide = factsForContinue({ snapshot: { text: "- button" }, index: [{ summary: "click" }] });
  assert.equal(afterGuide.snapshot.text, undefined);
  assert.equal(afterGuide.index, undefined);
  assert.deepEqual(afterGuide.commands, []);
  assert.deepEqual(afterGuide.ran, []);
  assert.equal(continueIncludesTree("read_guide"), false);
  assert.equal(continueIncludesTree("write_skill_file"), false);
  assert.equal(continueIncludesTree("browser_act click"), true);
  assert.equal(continueIncludesTree("read_page_asset"), true);
});

test("the first skill tool resets the continue budget", () => {
  assert.equal(continueCountAfterSkillTool(7, { skillTool: true, enteredSkill: false }), 0);
  assert.equal(continueCountAfterSkillTool(7, { skillTool: true, enteredSkill: true }), 7);
  assert.equal(continueCountAfterSkillTool(7, { skillTool: false, enteredSkill: false }), 7);
});

test("writing SKILL.md returns the python command lines found in that file", async () => {
  await withDataRoot(async () => {
    const backtick = await writeSkillFile("rec_cmd_empty", "app.rec_cmd_empty", "SKILL.md", [
      "---",
      "name: x",
      "description: y",
      "---",
      "- `statistics --dept-id 1`",
      "",
    ].join("\n"));
    assert.deepEqual(backtick.commands, []);
    const written = await writeSkillFile("rec_cmd", "app.rec_cmd", "SKILL.md", [
      "---",
      "name: x",
      "description: y",
      "---",
      "python scripts/client.py statistics --dept-id <id>",
      "python scripts/client.py submit --confirm",
      "",
    ].join("\n"));
    assert.deepEqual(written.commands, [
      "python scripts/client.py statistics --dept-id <id>",
      "python scripts/client.py submit --confirm",
    ]);
  });
});

test("verify returns action-linked requests when the recording tagged actions", async () => {
  await withDataRoot(async (root) => {
    const recordingId = "rec_shown_linked";
    const skillId = "app.rec_shown_linked";
    const dir = path.join(root, "skills", skillId);
    await writeInitialGoal(recordingId, { page_url: "http://127.0.0.1/", goal_text: "列出记录" });
    for (const name of GUIDE_NAMES) {
      await appendEvidence(recordingId, { kind: "guide", summary: name, body: name, body_missing: false });
    }
    await appendEvidence(recordingId, {
      id: "req_boot",
      kind: "network",
      summary: "GET /api/boot",
      body: { method: "GET", path: "/api/boot", query: "", post_data: "", response_body: "{}" },
    });
    await appendEvidence(recordingId, {
      id: "req_1",
      kind: "network",
      summary: "GET /api/items",
      body: { method: "GET", path: "/api/items", query: "deptId=1", post_data: "", response_body: "{}", action_id: "act_1" },
    });
    await appendEvidence(recordingId, {
      kind: "verify",
      ok: true,
      argv: ["python", "scripts/client.py", "list"],
      summary: "python scripts/client.py list",
      stdout: "{}",
      stderr: "",
      body: "{}",
    });
    await mkdir(path.join(dir, "scripts"), { recursive: true });
    await mkdir(path.join(dir, "references"), { recursive: true });
    await mkdir(path.join(dir, "config"), { recursive: true });
    await writeFile(path.join(dir, "SKILL.md"), "---\nname: list-items\ndescription: 列出本页记录\n---\npython scripts/client.py list\n");
    await writeFile(path.join(dir, "scripts/client.py"), "def list_items():\n    return get('/api/items', deptId=deptId)\n");
    await writeFile(path.join(dir, "references/api.md"), "GET /api/items\n");
    await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
    await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
    const result = await verifySkill(dir, recordingId, { skillId });
    assert.deepEqual(result.errors, []);
    assert.equal(result.requests.some((row) => row.path === "/api/boot"), false);
    assert.equal(result.requests.some((row) => row.path === "/api/items"), true);
  });
});

test("continue prompt does not force a read command on a write-only recording", () => {
  const text = nextRecordingPrompt({ finished: false, paused: false, progressed: true, continues: 0, verifyErrors: [] });
  assert.match(text, /不要发明没录到的读接口/);
  assert.equal(text.includes("跑读命令，再 verify_skill"), false);
  assert.equal(text.includes("各占一行必填"), false);
});

async function writeListedSkill(dir, recordingId) {
  for (const name of GUIDE_NAMES) {
    await appendEvidence(recordingId, { kind: "guide", summary: name, body: name, body_missing: false });
  }
  await appendEvidence(recordingId, {
    id: "req_1",
    kind: "network",
    summary: "GET /api/items",
    body: { method: "GET", path: "/api/items", query: "deptId=1", post_data: "", response_body: "{}" },
  });
  await appendEvidence(recordingId, {
    kind: "verify",
    ok: false,
    argv: ["python", "scripts/client.py", "list"],
    summary: "python scripts/client.py list",
    stdout: "",
    stderr: "HTTP 401",
    body: "",
  });
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: list-items\ndescription: 列出本页记录\n---\npython scripts/client.py list\n");
  await writeFile(path.join(dir, "scripts/client.py"), "def list_items():\n    return get('/api/items', deptId=deptId)\n");
  await writeFile(path.join(dir, "references/api.md"), "GET /api/items\n");
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
}

test("a 401 read without auth headers is needs-auth, not a failed skill", async () => {
  await withDataRoot(async (root) => {
    const recordingId = "rec_auth_missing";
    const skillId = "app.rec_auth_missing";
    const dir = path.join(root, "skills", skillId);
    await writeInitialGoal(recordingId, { page_url: "http://127.0.0.1/", goal_text: "列出记录" });
    await writeListedSkill(dir, recordingId);
    await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
    const result = await verifySkill(dir, recordingId, { skillId });
    assert.equal(result.errors.some((item) => item.code === "auth_expired"), false, JSON.stringify(result.errors));
    assert.deepEqual(result.errors, []);
    assert.equal(result.status, "skill_written_needs_auth");
    assert.equal(result.ok, true);
  });
});

test("a 401 read with auth headers is auth_expired", async () => {
  await withDataRoot(async (root) => {
    const recordingId = "rec_auth_dead";
    const skillId = "app.rec_auth_dead";
    const dir = path.join(root, "skills", skillId);
    await writeInitialGoal(recordingId, { page_url: "http://127.0.0.1/", goal_text: "列出记录" });
    await writeListedSkill(dir, recordingId);
    await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{\"Authorization\":\"Bearer test-token\"}}\n");
    const result = await verifySkill(dir, recordingId, { skillId });
    assert.equal(result.errors.some((item) => item.code === "auth_expired"), true, JSON.stringify(result.errors));
    assert.equal(result.ok, false);
  });
});
