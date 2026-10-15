import assert from "node:assert/strict";
import http from "node:http";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { closeBrowser, getPage, openBrowser } from "../src/browser/session.mjs";
import { overlayControlFacts, takeSnapshot } from "../src/browser/snapshot.mjs";
import { runAction } from "../src/browser/actions.mjs";
import { latestCredentialHeaders, listNetwork } from "../src/browser/network.mjs";
import { requestIndexRow } from "../src/skillpack/request-keys.mjs";
import { writeRuntimeConfig } from "../src/skillpack/files.mjs";
import { ASK_PROTOCOL, verifySkill } from "../src/skillpack/verify.mjs";
import { GUIDE_NAMES } from "../src/agent/guides.mjs";
import { appendEvidence } from "../src/evidence/store.mjs";
import { skillDir } from "../src/paths.mjs";
import { withData } from "./helpers.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const shapesDir = path.join(here, "fixtures", "shapes");
const fixtureDir = path.join(here, "fixtures");

function publishedRef(line) {
  const match = String(line || "").match(/\[ref=([^\]]+)\]/) || String(line || "").match(/\bref=(f\d+:e\d+@\d+)/);
  return match ? match[1] : "";
}

function lineWith(text, needle) {
  return String(text || "").split(/\n/).find((line) => line.includes(needle) && /\[ref=/.test(line)) || "";
}

function headerOf(headers, name) {
  const want = String(name || "").toLowerCase();
  return Object.entries(headers || {}).find(([key]) => String(key).toLowerCase() === want)?.[1] || "";
}

function jsonReply(res, extra = {}) {
  res.writeHead(200, { "content-type": "application/json", ...extra });
  res.end("{}");
  return true;
}

async function serve({ file, dir = shapesDir, api, rewrite } = {}) {
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, "http://127.0.0.1");
    if (api && await api(req, res, url)) return;
    if (file) {
      let body = await readFile(path.join(dir, file), "utf8");
      if (rewrite) body = rewrite(body);
      res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
      res.end(body);
      return;
    }
    res.writeHead(404);
    res.end("");
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  return { server, origin, url: `${origin}/` };
}

async function withSite(id, site, run) {
  try {
    await openBrowser({ recordingId: id, url: site.url, storageState: null, viewport: { width: 900, height: 700 } });
    await getPage(id).waitForLoadState("load").catch(() => {});
    return await run();
  } finally {
    await closeBrowser(id).catch(() => {});
    await new Promise((resolve) => site.server.close(resolve));
  }
}

async function seedGuides(recordingId) {
  for (const name of GUIDE_NAMES) {
    await appendEvidence(recordingId, { kind: "guide", summary: name, body: name });
  }
}

test("JSON interface with Bearer keeps page query keys and authorization", { timeout: 60000 }, async () => {
  await withData(async () => {
    const site = await serve({
      file: "json-bearer.html",
      api: (req, res, url) => url.pathname === "/api/items" ? jsonReply(res) : false,
    });
    const id = "rec_shape_bearer";
    await withSite(id, site, async () => {
      const snap = await takeSnapshot(id, { budgetMs: 4000 });
      const clicked = await runAction(id, { action: "click", ref: publishedRef(lineWith(snap.text, "query")) });
      assert.equal(clicked.ok, true);
      const row = listNetwork(id).find((item) => item.path === "/api/items");
      assert.ok(row);
      assert.ok(row.action_id);
      assert.ok(requestIndexRow(row).keys.includes("page") || row.keys.includes("page"));
      assert.ok(headerOf(latestCredentialHeaders(id), "authorization"));
    });
  });
});

test("cookie session with CSRF keeps form keys and csrf header", { timeout: 60000 }, async () => {
  await withData(async () => {
    const site = await serve({
      file: "csrf-form.html",
      api: (req, res, url) => url.pathname === "/api/items" ? jsonReply(res) : false,
    });
    const id = "rec_shape_csrf";
    await withSite(id, site, async () => {
      const snap = await takeSnapshot(id, { budgetMs: 4000 });
      const nameRef = publishedRef(lineWith(snap.text, "name"));
      await runAction(id, { action: "fill", ref: nameRef, text: "item" });
      const afterFill = await takeSnapshot(id, { budgetMs: 4000 });
      const saved = await runAction(id, { action: "click", ref: publishedRef(lineWith(afterFill.text, "save")) });
      assert.equal(saved.ok, true);
      const row = listNetwork(id).find((item) => item.method === "POST" && item.path === "/api/items");
      assert.ok(row);
      assert.ok(row.keys.includes("name"));
      assert.ok(row.empty?.includes("note"));
      assert.ok(headerOf(latestCredentialHeaders(id), "x-csrf-token"));
    });
  });
});

test("multipart upload fires a request", { timeout: 60000 }, async () => {
  await withData(async () => {
    const site = await serve({
      file: "multipart.html",
      api: (req, res, url) => url.pathname === "/api/items" ? jsonReply(res) : false,
    });
    const id = "rec_shape_multipart";
    await withSite(id, site, async () => {
      const snap = await takeSnapshot(id, { budgetMs: 4000 });
      const fileLine = lineWith(snap.text, "file") || lineWith(snap.text, "Choose") || lineWith(snap.text, "Browse");
      const fileRef = publishedRef(fileLine);
      assert.ok(fileRef);
      const uploaded = await runAction(id, { action: "upload", ref: fileRef });
      assert.equal(uploaded.ok, true);
      const row = listNetwork(id).find((item) => item.method === "POST" && item.path === "/api/items");
      assert.ok(row);
      assert.ok(row.action_id);
    });
  });
});

test("two GraphQL operations on one path stay as separate unfolder rows", { timeout: 60000 }, async () => {
  await withData(async () => {
    const site = await serve({
      file: "graphql.html",
      api: (req, res, url) => url.pathname === "/graphql" ? jsonReply(res) : false,
    });
    const id = "rec_shape_graphql";
    await withSite(id, site, async () => {
      const snap = await takeSnapshot(id, { budgetMs: 4000 });
      const listed = await runAction(id, { action: "click", ref: publishedRef(lineWith(snap.text, "list items")) });
      assert.equal(listed.ok, true);
      const after = listed.snapshot?.text || (await takeSnapshot(id, { budgetMs: 4000 })).text;
      const saved = await runAction(id, { action: "click", ref: publishedRef(lineWith(after, "save item")) });
      assert.equal(saved.ok, true);
      const rows = listNetwork(id).filter((item) => item.path === "/graphql");
      assert.equal(rows.length, 2);
      assert.notEqual(rows[0].id, rows[1].id);
    });
  });
});

test("english combobox without placeholder attribute uses class text as empty", { timeout: 60000 }, async () => {
  await withData(async () => {
    const site = await serve({ file: "english-placeholder.html" });
    const id = "rec_shape_placeholder";
    await withSite(id, site, async () => {
      await takeSnapshot(id, { budgetMs: 4000 });
      const facts = await overlayControlFacts(id);
      const kind = facts.find((row) => /kind/i.test(String(row.name || "")));
      const status = facts.find((row) => /status/i.test(String(row.name || "")));
      assert.equal(kind?.empty, true);
      assert.equal(status?.empty, false);
    });
  });
});

test("wizard step two controls appear and step one fields are not verify errors", { timeout: 60000 }, async () => {
  await withData(async () => {
    const site = await serve({ file: "wizard.html" });
    const id = "rec_shape_wizard";
    const shape = { id, tenant: "", subsystem: "app", skillId: `app.${id}`, startUrl: site.url };
    await withSite(id, site, async () => {
      const first = await takeSnapshot(id, { budgetMs: 4000 });
      const stepOne = await overlayControlFacts(id);
      assert.ok(stepOne.some((row) => /name/i.test(String(row.name || ""))));
      const next = await runAction(id, { action: "click", ref: publishedRef(lineWith(first.text, "Next")) });
      assert.equal(next.ok, true);
      const stepTwo = next.overlay_fields || await overlayControlFacts(id);
      assert.ok(stepTwo.some((row) => /kind/i.test(String(row.name || ""))));

      const dir = skillDir(shape.skillId);
      await mkdir(path.join(dir, "scripts"), { recursive: true });
      await mkdir(path.join(dir, "references"), { recursive: true });
      await mkdir(path.join(dir, "config"), { recursive: true });
      await writeFile(path.join(dir, "SKILL.md"), [
        "---",
        "name: save-item",
        "description: save one item",
        "---",
        JSON.stringify({ title: "save", questions: [{ id: "name", question: "name", inputType: "text", required: true, default: " " }] }),
        "python scripts/client.py save --confirm",
        ASK_PROTOCOL,
        "",
      ].join("\n"));
      await writeFile(path.join(dir, "scripts/client.py"), "def save():\n    method = \"POST\"\n    path = \"/api/items\"\n    body[\"name\"] = args.name\n");
      await writeFile(path.join(dir, "references/api.md"), "## 字段\n- page_name: name\n- source: caller\n\n## 绑定\n\n## 未解决\n\n## 已验证读命令\n");
      await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
      await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{\"Authorization\":\"Bearer tok\"}}\n");
      await seedGuides(id);
      await appendEvidence(id, {
        kind: "action",
        summary: "click:Next",
        body: {
          overlay_editable: stepOne.map((row) => ({ name: row.name, role: row.role, ref: row.ref })),
          requests: [],
        },
      });
      const result = await verifySkill(dir, id, shape);
      assert.equal(result.errors.some((item) => item.field === "name"), false);
    });
  });
});

test("api on another origin is written into runtime bases", { timeout: 60000 }, async () => {
  await withData(async () => {
    const api = await serve({
      api: (req, res, url) => {
        if (req.method === "OPTIONS") {
          res.writeHead(204, {
            "access-control-allow-origin": "*",
            "access-control-allow-headers": "*",
            "access-control-allow-methods": "*",
          });
          res.end();
          return true;
        }
        if (url.pathname === "/api/items") {
          res.writeHead(200, { "content-type": "application/json", "access-control-allow-origin": "*" });
          res.end("{}");
          return true;
        }
        return false;
      },
    });
    const site = await serve({
      file: "cross-origin.html",
      rewrite: (html) => html.replaceAll("__API__", api.origin),
    });
    const id = "rec_shape_bases";
    try {
      await openBrowser({ recordingId: id, url: site.url, storageState: null, viewport: { width: 900, height: 700 } });
      await getPage(id).waitForLoadState("load").catch(() => {});
      const snap = await takeSnapshot(id, { budgetMs: 4000 });
      const clicked = await runAction(id, { action: "click", ref: publishedRef(lineWith(snap.text, "query")) });
      assert.equal(clicked.ok, true);
      const row = listNetwork(id).find((item) => item.path === "/api/items");
      assert.ok(row);
      const written = await writeRuntimeConfig({
        id,
        tenant: "",
        subsystem: "app",
        startUrl: site.url,
      }, "app.rec_shape_bases");
      const host = new URL(api.origin).host;
      assert.equal(written.runtime.bases[host], api.origin);
    } finally {
      await closeBrowser(id).catch(() => {});
      await new Promise((resolve) => site.server.close(resolve));
      await new Promise((resolve) => api.server.close(resolve));
    }
  });
});

test("iframe form refs start with f1 and the click keeps action_id", { timeout: 60000 }, async () => {
  await withData(async () => {
    const site = await serve({
      dir: fixtureDir,
      file: "iframe-form.html",
      api: async (req, res, url) => {
        if (req.method === "POST" && url.pathname === "/api/save") return jsonReply(res);
        if (url.pathname === "/inner.html") {
          const body = await readFile(path.join(fixtureDir, "inner.html"));
          res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
          res.end(body);
          return true;
        }
        return false;
      },
    });
    const id = "rec_shape_iframe";
    await withSite(id, site, async () => {
      const snap = await takeSnapshot(id, { budgetMs: 4000 });
      const inner = snap.text.split(/\n/).filter((line) => line.includes("f1:") && /提交/.test(line) && /\[ref=/.test(line));
      const ref = publishedRef(inner[1] || inner[0]);
      assert.match(ref, /^f1:/);
      const clicked = await runAction(id, { action: "click", ref });
      assert.equal(clicked.ok, true);
      const row = listNetwork(id).find((item) => item.path === "/api/save");
      assert.ok(row);
      assert.ok(row.action_id);
    });
  });
});

test("export click stores binary type size and filename", { timeout: 60000 }, async () => {
  await withData(async () => {
    const payload = Buffer.alloc(2048, 1);
    const site = await serve({
      file: "export.html",
      api: (req, res, url) => {
        if (url.pathname !== "/api/export") return false;
        res.writeHead(200, {
          "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          "content-disposition": "attachment; filename*=UTF-8''%E6%8A%A5%E8%A1%A8.xlsx",
          "content-length": String(payload.length),
        });
        res.end(payload);
        return true;
      },
    });
    const id = "rec_shape_export";
    await withSite(id, site, async () => {
      const snap = await takeSnapshot(id, { budgetMs: 4000 });
      const clicked = await runAction(id, { action: "click", ref: publishedRef(lineWith(snap.text, "export")) });
      assert.equal(clicked.ok, true);
      const row = listNetwork(id, { all: true }).find((item) => item.path === "/api/export");
      assert.ok(row);
      assert.equal(row.binary?.size, 2048);
      assert.equal(row.binary?.filename, "报表.xlsx");
    });
  });
});

test("debounced search keeps action time facts", { timeout: 60000 }, async () => {
  await withData(async () => {
    const site = await serve({
      file: "debounce.html",
      api: (req, res, url) => url.pathname === "/api/items" ? jsonReply(res) : false,
    });
    const id = "rec_shape_debounce";
    await withSite(id, site, async () => {
      const snap = await takeSnapshot(id, { budgetMs: 4000 });
      const filled = await runAction(id, { action: "fill", ref: publishedRef(lineWith(snap.text, "search")), text: "ab" });
      assert.equal(filled.ok, true);
      await new Promise((resolve) => setTimeout(resolve, 800));
      const row = listNetwork(id).find((item) => item.path === "/api/items");
      assert.ok(row);
      assert.equal(row.action_id || "", "");
      assert.ok(row.after_action);
      assert.ok(row.after_ms >= 600);
    });
  });
});

test("multipart records file_field and unreadable body or keys", { timeout: 60000 }, async () => {
  await withData(async () => {
    const site = await serve({
      file: "multipart.html",
      api: (req, res, url) => url.pathname === "/api/items" ? jsonReply(res) : false,
    });
    const id = "rec_shape_multipart_keys";
    await withSite(id, site, async () => {
      const snap = await takeSnapshot(id, { budgetMs: 4000 });
      const fileLine = lineWith(snap.text, "file") || lineWith(snap.text, "Choose") || lineWith(snap.text, "Browse");
      const uploaded = await runAction(id, { action: "upload", ref: publishedRef(fileLine) });
      assert.equal(uploaded.ok, true);
      const row = listNetwork(id).find((item) => item.method === "POST" && item.path === "/api/items");
      assert.ok(row);
      const gotKeys = (row.keys || []).includes("file") || (row.keys || []).includes("name");
      assert.ok(gotKeys || (row.post_data_unreadable === "multipart" && uploaded.file_field?.name));
    });
  });
});

test.todo("服务端渲染页面：等用户决定第 8 节 8.6");
