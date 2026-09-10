/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 *
 * 独立控制面。只展示录制状态、PI 状态、失败信息和 PI 最终结果。
 */

import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { PI_ONLY_NOTICE, assertNeverStartLegacy, logPiOnly, publicFailureMessage } from "./policy.mjs";
import { readPiModelEnv } from "./pi-model.mjs";
import { RecordingFiles } from "./fs-store.mjs";
import { EvidenceStore } from "./evidence-store.mjs";
import { ResultGate } from "./result-gate.mjs";
import { RecordingController, displayedCapabilityCount } from "./recording-controller.mjs";
import { createLivePiSession } from "./pi-session.mjs";
import { createPlaywrightBrowser } from "./browser-capture.mjs";
import { ResultsCatalog } from "./results-catalog.mjs";
import { attachFrontendBridge } from "./frontend-bridge.mjs";
import {
  exportRecordingSkill,
  reexportCatalogSkills,
  listExportedSkills,
  getExportedSkill,
  setExportedSkillFrozen,
  removeExportedSkill,
  readTokenRecord,
  writeTokenRecord,
  writebackExportedPackages,
  maskHeaders,
  hydrateAuthFromRecordings,
  readExportDirectory,
  writeExportDirectory,
} from "./skill-export/index.mjs";
import { composeAuthHeader } from "./auth-vault.mjs";

assertNeverStartLegacy();

process.on("uncaughtException", (error) => {
  logPiOnly(`uncaughtException ${error?.stack || error?.message || error}`);
});
process.on("unhandledRejection", (error) => {
  logPiOnly(`unhandledRejection ${error?.stack || error?.message || error}`);
});

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const PUBLIC = path.join(ROOT, "src", "public");
const PORT = Number(process.env.PI_CHECK_PORT || 18080);
const piBoot = readPiModelEnv();
logPiOnly(`启动时 PI 凭证 key_set=${Boolean(piBoot.apiKey)} model=${piBoot.modelId || "(empty)"} baseUrl=${piBoot.baseUrl ? "set" : "(none)"} provider=${piBoot.provider || "(empty)"}`);
const listeners = new Map();

const files = new RecordingFiles(process.env.PI_CHECK_DATA_DIR || path.join(ROOT, "data"));
const evidence = new EvidenceStore(files, {
  onEvent(recordingId, event) {
    const set = listeners.get(recordingId);
    if (!set) return;
    for (const send of set) send(event);
  },
});
const gate = new ResultGate(files);
const catalog = new ResultsCatalog(files);
const controller = new RecordingController({
  files,
  evidence,
  gate,
  createPi: createLivePiSession,
  createBrowser: createPlaywrightBrowser,
});

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
};

function json(res, status, payload) {
  res.writeHead(status, { "content-type": "application/json; charset=utf-8" });
  res.end(`${JSON.stringify(payload)}\n`);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      const text = Buffer.concat(chunks).toString("utf8");
      if (!text) {
        resolve({});
        return;
      }
      try {
        resolve(JSON.parse(text));
      } catch (error) {
        reject(error);
      }
    });
    req.on("error", reject);
  });
}

async function sendResult(res, recordingId) {
  const payload = await controller.result(recordingId);
  json(res, 200, {
    ...payload,
    notice: PI_ONLY_NOTICE,
    capabilityCount: displayedCapabilityCount(payload.result, payload.session),
  });
}

const server = createServer(async (req, res) => {
  try {
    const url = new URL(req.url || "/", `http://${req.headers.host || "127.0.0.1"}`);
    const quietGet = req.method === "GET" && (
      url.pathname === "/health"
      || url.pathname === "/api/health"
      || url.pathname === "/v1/skills"
      || url.pathname === "/v1/pi-recordings"
      || url.pathname === "/v1/recording-results"
    );
    if (!quietGet) {
      logPiOnly(`[HTTP] ${req.method} ${url.pathname}`);
    }
    if (req.method === "GET" && (url.pathname === "/" || url.pathname === "/index.html")) {
      const html = await readFile(path.join(PUBLIC, "index.html"));
      res.writeHead(200, { "content-type": MIME[".html"] });
      res.end(html);
      return;
    }
    if (req.method === "GET" && url.pathname.startsWith("/static/")) {
      const file = path.basename(url.pathname);
      const ext = path.extname(file);
      const body = await readFile(path.join(PUBLIC, file));
      res.writeHead(200, { "content-type": MIME[ext] || "application/octet-stream" });
      res.end(body);
      return;
    }
    if (req.method === "GET" && (url.pathname === "/api/health" || url.pathname === "/health")) {
      json(res, 200, { ok: true, notice: PI_ONLY_NOTICE, export_catalog: true });
      return;
    }
    if (req.method === "GET" && (url.pathname === "/v1/recording-results" || url.pathname === "/v1/pi-recordings")) {
      json(res, 200, await catalog.listPublished(url.searchParams.get("subsystem") || ""));
      return;
    }
    const oneResult = url.pathname.match(/^\/v1\/(?:recording-results|pi-recordings)\/([^/]+)$/);
    if (req.method === "GET" && oneResult) {
      const detail = await catalog.detailOf(oneResult[1]);
      if (!detail) {
        json(res, 404, { error: "not found" });
        return;
      }
      json(res, 200, detail);
      return;
    }
    if (req.method === "DELETE" && oneResult) {
      catalog.remove(oneResult[1]);
      json(res, 200, { ok: true });
      return;
    }
    const exportSkill = url.pathname.match(/^\/v1\/(?:recording-results|pi-recordings)\/([^/]+)\/export-skill$/);
    if (req.method === "POST" && exportSkill) {
      const body = await readBody(req);
      const recordingId = String(body.recording_id || exportSkill[1] || "").trim();
      const title = String(body.title || "");
      const tenant = String(body.tenant || "");
      const capCount = Array.isArray(body.draft?.capabilities) ? body.draft.capabilities.length : 0;
      if (body.out_dir) await writeExportDirectory(body.out_dir);
      logPiOnly(`[出包] 收到请求 path=${url.pathname} recording_id=${recordingId || "-"} title=${title || "-"} tenant=${tenant || "-"} subsystem=${body.subsystem || "oa"} caps=${capCount} out_dir=${body.out_dir || "-"} existing_skill_id=${body.existing_skill_id || body.skill_id || "-"} overlay=${body.draft ? "yes" : "no"}`);
      if (!recordingId.startsWith("rec_")) {
        logPiOnly(`[出包] 拒绝 缺少 recording_id path_id=${exportSkill[1]}`);
        json(res, 400, { status: "export_failed", errors: ["缺少 recording_id，无法按最新能力导出"] });
        return;
      }
      const started = Date.now();
      const outcome = await exportRecordingSkill({
        files,
        evidence,
        recordingId,
        resultId: String(body.result_id || exportSkill[1] || ""),
        tenant,
        subsystem: String(body.subsystem || "oa"),
        title,
        outDir: String(body.out_dir || ""),
        draft: body.draft && typeof body.draft === "object" ? body.draft : null,
        authHeaders: body.auth_headers || body.headers || null,
        existingSkillId: String(body.existing_skill_id || body.skill_id || ""),
      });
      const elapsed = Date.now() - started;
      if (outcome.status === "exported") {
        logPiOnly(`[出包] 成功 recording_id=${recordingId} skill_id=${outcome.skill_id || "-"} version=${outcome.version || "-"} path=${outcome.export_path || "-"} ${elapsed}ms`);
      } else {
        logPiOnly(`[出包] 失败 recording_id=${recordingId} ${elapsed}ms errors=${JSON.stringify(outcome.errors || [])}`);
      }
      json(res, outcome.status === "exported" ? 200 : 409, outcome);
      return;
    }
    if (req.method === "GET" && url.pathname === "/v1/skills") {
      const includeFrozen = url.searchParams.get("include_frozen") !== "0";
      const items = await listExportedSkills(files, { includeFrozen });
      const page = Number(url.searchParams.get("page") || 0);
      const pageSize = Number(url.searchParams.get("page_size") || 0);
      if (page > 0 && pageSize > 0) {
        const start = (page - 1) * pageSize;
        json(res, 200, {
          items: items.slice(start, start + pageSize),
          total: items.length,
          page,
          page_size: pageSize,
        });
        return;
      }
      json(res, 200, items);
      return;
    }
    if (req.method === "POST" && url.pathname === "/v1/skills/export") {
      const body = await readBody(req);
      if (body.out_dir) await writeExportDirectory(body.out_dir);
      logPiOnly(`[出包] 收到目录快速导出 tenant=${body.tenant || "-"} out_dir=${body.out_dir || "-"} drafts=${body.drafts && typeof body.drafts === "object" ? Object.keys(body.drafts).length : 0}`);
      const outcome = await reexportCatalogSkills({
        files,
        evidence,
        outDir: String(body.out_dir || ""),
        tenant: String(body.tenant || ""),
        authHeaders: body.auth_headers || body.headers || null,
        drafts: body.drafts && typeof body.drafts === "object" ? body.drafts : null,
      });
      json(res, outcome.errors?.length && !outcome.count ? 409 : 200, outcome);
      return;
    }
    const putDraft = url.pathname.match(/^\/v1\/recording-results\/([^/]+)\/draft$/);
    if (req.method === "PUT" && putDraft) {
      const body = await readBody(req);
      const recordingId = String(body.recording_id || putDraft[1] || "").trim();
      const draft = body.draft && typeof body.draft === "object" ? body.draft : body;
      if (!recordingId.startsWith("rec_")) {
        json(res, 400, { error: "缺少 recording_id" });
        return;
      }
      await files.writeDraft(recordingId, {
        recording_id: recordingId,
        saved_at: new Date().toISOString(),
        draft,
        title: String(body.title || draft.title || ""),
      });
      json(res, 200, { ok: true, recording_id: recordingId });
      return;
    }
    const freezeSkill = url.pathname.match(/^\/v1\/skills\/([^/]+)\/freeze$/);
    if (req.method === "POST" && freezeSkill) {
      const item = await setExportedSkillFrozen(files, decodeURIComponent(freezeSkill[1]), true);
      if (!item) {
        json(res, 404, { error: "not found" });
        return;
      }
      json(res, 200, { skill_id: item.name, state: "suspended" });
      return;
    }
    const resumeSkill = url.pathname.match(/^\/v1\/skills\/([^/]+)\/resume$/);
    if (req.method === "POST" && resumeSkill) {
      const item = await setExportedSkillFrozen(files, decodeURIComponent(resumeSkill[1]), false);
      if (!item) {
        json(res, 404, { error: "not found" });
        return;
      }
      json(res, 200, { skill_id: item.name, state: "published" });
      return;
    }
    const oneSkill = url.pathname.match(/^\/v1\/skills\/([^/]+)$/);
    if (req.method === "GET" && oneSkill) {
      const item = await getExportedSkill(files, decodeURIComponent(oneSkill[1]));
      if (!item) {
        json(res, 404, { error: "not found" });
        return;
      }
      json(res, 200, item);
      return;
    }
    if (req.method === "DELETE" && oneSkill) {
      const removed = await removeExportedSkill(files, decodeURIComponent(oneSkill[1]));
      json(res, removed ? 200 : 404, { deleted: removed, skill_id: decodeURIComponent(oneSkill[1]) });
      return;
    }
    if (req.method === "GET" && url.pathname === "/v1/settings/token") {
      const rec = await readTokenRecord(url.searchParams.get("tenant") || "", url.searchParams.get("subsystem") || "");
      json(res, 200, {
        tenant: rec.tenant,
        subsystem: rec.subsystem,
        has_token: rec.has_token,
        headers: rec.headers,
        source: rec.source,
        updated_at: rec.updated_at,
      });
      return;
    }
    if (req.method === "GET" && (url.pathname === "/v1/export/directory" || url.pathname === "/export/directory")) {
      json(res, 200, { out_dir: await readExportDirectory() });
      return;
    }
    if ((req.method === "PUT" || req.method === "POST") && (url.pathname === "/v1/export/directory" || url.pathname === "/export/directory")) {
      const body = await readBody(req);
      json(res, 200, { out_dir: await writeExportDirectory(body.out_dir || body.directory || "") });
      return;
    }
    if (req.method === "POST" && url.pathname === "/v1/settings/token") {
      const body = await readBody(req);
      const headers = { ...(body.headers || {}) };
      if (!Object.keys(headers).length && body.token) {
        const headerName = String(body.header_name || "Authorization");
        headers[headerName] = composeAuthHeader(headerName, body.token_prefix ?? "Bearer ", body.token);
      }
      const rec = await writeTokenRecord(body.tenant || "", body.subsystem || "", headers, { source: "manual" });
      const catalog = await listExportedSkills(files);
      const exportRoot = String(body.out_dir || "").trim() || await readExportDirectory();
      const writeback = await writebackExportedPackages({
        subsystem: rec.subsystem,
        headers: rec.headers,
        exportRoot,
        catalogRows: catalog,
      });
      json(res, 200, {
        ok: true,
        tenant: rec.tenant,
        subsystem: rec.subsystem,
        headers: maskHeaders(rec.headers),
        updated_at: rec.updated_at,
        updated_packages: writeback.updated,
      });
      return;
    }
    if (req.method === "GET" && url.pathname === "/api/recordings") {
      json(res, 200, { recordings: controller.list(), notice: PI_ONLY_NOTICE });
      return;
    }
    if (req.method === "POST" && url.pathname === "/api/recordings") {
      const body = await readBody(req);
      const session = await controller.start({
        targetUrl: body.targetUrl,
        goal: body.goal,
      });
      json(res, 201, session);
      return;
    }
    const events = url.pathname.match(/^\/api\/recordings\/([^/]+)\/events$/);
    if (req.method === "GET" && events) {
      const recordingId = events[1];
      res.writeHead(200, {
        "content-type": "text/event-stream; charset=utf-8",
        "cache-control": "no-cache",
        connection: "keep-alive",
      });
      const send = (event) => {
        res.write(`data: ${JSON.stringify(event)}\n\n`);
      };
      if (!listeners.has(recordingId)) listeners.set(recordingId, new Set());
      listeners.get(recordingId).add(send);
      send({ type: "status", session: controller.view(recordingId) });
      req.on("close", () => {
        listeners.get(recordingId)?.delete(send);
      });
      return;
    }
    const sessionInject = url.pathname.match(/^\/api\/recordings\/([^/]+)\/session$/);
    if (req.method === "POST" && sessionInject) {
      const body = await readBody(req);
      const view = await controller.applySession(sessionInject[1], body);
      json(res, 200, { ok: true, session: view, notice: PI_ONLY_NOTICE });
      return;
    }
    const steer = url.pathname.match(/^\/api\/recordings\/([^/]+)\/steer$/);
    if (req.method === "POST" && steer) {
      const body = await readBody(req);
      const view = await controller.steer(steer[1], body.text || body.message || "");
      json(res, 200, { ok: true, session: view, notice: PI_ONLY_NOTICE });
      return;
    }
    const act = url.pathname.match(/^\/api\/recordings\/([^/]+)\/act$/);
    if (req.method === "POST" && act) {
      const body = await readBody(req);
      const view = await controller.act(act[1], body);
      json(res, 200, { ok: true, session: view, notice: PI_ONLY_NOTICE });
      return;
    }
    const stop = url.pathname.match(/^\/api\/recordings\/([^/]+)\/stop$/);
    if (req.method === "POST" && stop) {
      try {
        const payload = await controller.stop(stop[1]);
        const view = payload.session || {};
        await catalog.remember({
          recordingId: stop[1],
          action: view.action || stop[1],
          title: view.title || "",
          goal: view.goal || "",
          result: payload.result,
          evidenceCount: view.evidenceCount || 0,
          subsystem: "",
        });
        json(res, 200, {
          ...payload,
          notice: PI_ONLY_NOTICE,
          capabilityCount: displayedCapabilityCount(payload.result, payload.session),
        });
      } catch {
        json(res, 409, {
          error: publicFailureMessage(),
          session: controller.view(stop[1]),
          result: null,
          capabilityCount: 0,
        });
      }
      return;
    }
    const cancel = url.pathname.match(/^\/api\/recordings\/([^/]+)\/cancel$/);
    if (req.method === "POST" && cancel) {
      try {
        await controller.cancel(cancel[1]);
      } catch {
        // 取消必然失败并保持无结果
      }
      json(res, 409, {
        error: publicFailureMessage(),
        session: controller.view(cancel[1]),
        result: null,
        capabilityCount: 0,
      });
      return;
    }
    const result = url.pathname.match(/^\/api\/recordings\/([^/]+)\/result$/);
    if (req.method === "GET" && result) {
      await sendResult(res, result[1]);
      return;
    }
    const one = url.pathname.match(/^\/api\/recordings\/([^/]+)$/);
    if (req.method === "GET" && one) {
      json(res, 200, controller.view(one[1]));
      return;
    }
    logPiOnly(`[HTTP] 404 ${req.method} ${url.pathname}`);
    json(res, 404, { error: "not found" });
  } catch (error) {
    logPiOnly(`[HTTP] 500 ${req.method} ${req.url || ""} ${error?.stack || error?.message || error}`);
    json(res, 500, {
      error: error.message || String(error),
      publicMessage: publicFailureMessage(),
      notice: PI_ONLY_NOTICE,
    });
  }
});

server.requestTimeout = 0;
server.headersTimeout = 0;
server.timeout = 0;

attachFrontendBridge(server, { controller, catalog });

if (process.env.PI_CHECK_NO_LISTEN !== "1") {
  server.listen(PORT, "127.0.0.1", () => {
    logPiOnly(PI_ONLY_NOTICE);
    logPiOnly(`internal listener 127.0.0.1:${PORT}`);
    logPiOnly("existing PageRecorder still connects to the 8077 gateway; this process never starts the old recorder");
    logPiOnly("[出包] 启动回写排队");
    readExportDirectory().then((exportRoot) => hydrateAuthFromRecordings({ files, evidence, exportRoot })).then((result) => {
      logPiOnly(`[出包] 启动回写返回 updated=${result?.updated?.length || 0} recovered=${result?.recovered || 0}`);
    }).catch((error) => {
      logPiOnly(`[出包] 回写完整 token 失败 ${error?.message || error}`);
    });
  });
}

export { server, controller, files, evidence, gate, catalog, ROOT };
