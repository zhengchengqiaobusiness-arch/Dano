import test from "node:test";
import assert from "node:assert/strict";
import { projectContractToRequest } from "../src/contract-project.mjs";
import { readPageAsset } from "../src/skill-package-tools.mjs";
import { stripImageFromToolResult, wrapPiToolsForSdk } from "../src/pi-tools.mjs";

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
