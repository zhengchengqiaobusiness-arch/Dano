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
import { RecordingFiles } from "./fs-store.mjs";
import { EvidenceStore } from "./evidence-store.mjs";
import { ResultGate } from "./result-gate.mjs";
import { RecordingController, displayedCapabilityCount } from "./recording-controller.mjs";
import { createLivePiSession } from "./pi-session.mjs";
import { createPlaywrightBrowser } from "./browser-capture.mjs";
import { ResultsCatalog } from "./results-catalog.mjs";
import { attachFrontendBridge } from "./frontend-bridge.mjs";

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
const listeners = new Map();

const files = new RecordingFiles(path.join(ROOT, "data"));
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
      json(res, 200, { ok: true, notice: PI_ONLY_NOTICE });
      return;
    }
    if (req.method === "GET" && url.pathname === "/v1/recording-results") {
      json(res, 200, catalog.list(url.searchParams.get("subsystem") || ""));
      return;
    }
    const oneResult = url.pathname.match(/^\/v1\/recording-results\/([^/]+)$/);
    if (req.method === "GET" && oneResult) {
      const detail = catalog.detail(oneResult[1]);
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
    json(res, 404, { error: "not found" });
  } catch (error) {
    json(res, 500, {
      error: error.message || String(error),
      publicMessage: publicFailureMessage(),
      notice: PI_ONLY_NOTICE,
    });
  }
});

attachFrontendBridge(server, { controller, catalog });

if (process.env.PI_CHECK_NO_LISTEN !== "1") {
  server.listen(PORT, "127.0.0.1", () => {
    logPiOnly(PI_ONLY_NOTICE);
    logPiOnly(`internal listener 127.0.0.1:${PORT}`);
    logPiOnly("existing PageRecorder still connects to the 8077 gateway; this process never starts the old recorder");
  });
}

export { server, controller, files, evidence, gate, catalog, ROOT };
