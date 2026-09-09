import test from "node:test";
import assert from "node:assert/strict";
import { mkdir, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { projectContractToRequest } from "../src/contract-project.mjs";
import { readGeneratorGuides, readPageAsset } from "../src/skill-package-tools.mjs";
import { stripImageFromToolResult, wrapPiToolsForSdk } from "../src/pi-tools.mjs";

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const REQUIRED_GUIDES = [
  "skill-generator-ask-user-question-guide.md",
  "skill-generator-auth-and-token.md",
  "skill-generator-workflow.md",
  "skill-generator-live-options.md",
];

const draft = {
  capabilities: [{
    capability_id: "cap_search",
    request_refs: [{ step_id: "step_search", usage: "execute" }],
  }],
  steps: [{
    step_id: "step_search",
    method: "GET",
    path: "/api/items",
    params: [
      { key: "keyword", path: "query.keyword", exposed_to_user: true, required: true },
      { key: "pageNo", path: "query.pageNo", exposed_to_user: false, default_value: 1 },
    ],
  }],
};

test("投影只按合同填键，缺键失败且不补", () => {
  const miss = projectContractToRequest(draft, "cap_search", {});
  assert.equal(miss.ok, false);
  assert.deepEqual(miss.missing, ["keyword"]);
  assert.equal(Object.keys(miss.query || {}).length, 0);
  const ok = projectContractToRequest(draft, "cap_search", { keyword: "请假" });
  assert.equal(ok.ok, true);
  assert.equal(ok.query.keyword, "请假");
  assert.equal(ok.query.pageNo, 1);
  assert.equal(ok.body.createTime, undefined);
});

test("非本场同源前端拒绝", async () => {
  const result = await readPageAsset({
    evidence: { files: { readEvidence: async () => [{ payload: { url: "https://a.example/app.js" } }] } },
    recordingId: "rec_x",
    url: "https://other.example/app.js",
    targetUrl: "https://a.example/home",
  });
  assert.equal(result.found, false);
});

test("as_image 才把图像放进工具 content", async () => {
  const defineTool = (spec) => spec;
  const host = {
    async control_in_app_browser({ as_image } = {}) {
      return {
        url: "http://x",
        __image: true,
        as_image: Boolean(as_image),
        mimeType: "image/png",
        data: "AAAA",
      };
    },
  };
  const tools = wrapPiToolsForSdk(host, defineTool, {
    String: () => ({}),
    Integer: () => ({}),
    Boolean: () => ({}),
    Object: () => ({}),
    Optional: (item) => item,
    Array: () => ({}),
  });
  const shot = tools.find((item) => item.name === "control_in_app_browser");
  const withImage = await shot.execute("1", { action: "screenshot", as_image: true });
  assert.equal(withImage.content[1].type, "image");
  assert.equal(withImage.content[1].mimeType, "image/png");
  const stripped = stripImageFromToolResult({
    __image: true,
    data: "AAAA",
    mimeType: "image/png",
    url: "http://x",
  });
  assert.equal(stripped.data, undefined);
  assert.equal(stripped.image_in_conversation, false);
});

test("read_generator_guides 能读到 doc 下全部规范", async () => {
  const previous = process.env.DANO_SKILL_REFERENCE_DIR;
  process.env.DANO_SKILL_REFERENCE_DIR = "doc";
  try {
    const result = await readGeneratorGuides();
    assert.equal(result.ok, true, result.error);
    const names = new Set(result.files.map((item) => item.path.split("/").pop()));
    for (const name of REQUIRED_GUIDES) {
      assert.ok(names.has(name), name);
    }
    assert.ok(result.files.every((item) => item.content && item.content.length > 20));
  } finally {
    if (previous === undefined) delete process.env.DANO_SKILL_REFERENCE_DIR;
    else process.env.DANO_SKILL_REFERENCE_DIR = previous;
  }
});

test("read_generator_guides 目录空则失败", async () => {
  const empty = path.join(REPO_ROOT, "Pi_check", "tests", ".tmp-empty-guides");
  await rm(empty, { recursive: true, force: true });
  await mkdir(empty, { recursive: true });
  const previous = process.env.DANO_SKILL_REFERENCE_DIR;
  process.env.DANO_SKILL_REFERENCE_DIR = "Pi_check/tests/.tmp-empty-guides";
  try {
    const result = await readGeneratorGuides();
    assert.equal(result.ok, false);
    assert.match(String(result.error || ""), /没有 Markdown|缺少必要规范/);
  } finally {
    await rm(empty, { recursive: true, force: true });
    if (previous === undefined) delete process.env.DANO_SKILL_REFERENCE_DIR;
    else process.env.DANO_SKILL_REFERENCE_DIR = previous;
  }
});
