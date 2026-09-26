import http from "node:http";
import { fileURLToPath } from "node:url";
import { createReadStream } from "node:fs";
import { readFile, rm } from "node:fs/promises";
import path from "node:path";
import { webDir, skillDir } from "./paths.mjs";
import { attachWebSocket } from "./ws-bridge.mjs";
import { exportSkill } from "./session.mjs";
import { listSkillsPage, removeSkill, setSkillFrozen } from "./skillpack/catalog.mjs";
import {
  maskHeaders, readExportDirectory, readTokenRecord, writebackExportedPackages, writeExportDirectory, writeTokenRecord,
} from "./token-store.mjs";

const CORS = new Set(["http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:19082", "http://localhost:19082"]);

function send(res, status, body, origin) {
  const headers = { "content-type": "application/json; charset=utf-8" };
  if (origin && CORS.has(origin)) {
    headers["access-control-allow-origin"] = origin;
    headers["access-control-allow-headers"] = "content-type";
    headers["access-control-allow-methods"] = "GET,POST,PUT,DELETE,OPTIONS";
  }
  res.writeHead(status, headers);
  res.end(typeof body === "string" ? body : JSON.stringify(body));
}

async function readBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  if (!chunks.length) return {};
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    return {};
  }
}

export function createApp({ startRecordingPi } = {}) {
  const server = http.createServer(async (req, res) => {
    const origin = req.headers.origin || "";
    if (req.method === "OPTIONS") {
      send(res, 204, "", origin);
      return;
    }
    const url = new URL(req.url || "/", "http://127.0.0.1");
    if (req.method === "GET" && url.pathname === "/api/health") {
      send(res, 200, { ok: true, service: "playwright-cabp" }, origin);
      return;
    }
    if (req.method === "POST" && url.pathname === "/api/recordings") {
      send(res, 404, { error: "not_found" }, origin);
      return;
    }
    if (req.method === "GET" && url.pathname === "/v1/skills") {
      const page = Number(url.searchParams.get("page") || 1);
      const pageSize = Number(url.searchParams.get("page_size") || 0);
      send(res, 200, await listSkillsPage(page, pageSize), origin);
      return;
    }
    const skillAction = url.pathname.match(/^\/v1\/skills\/([^/]+)\/(freeze|resume)$/);
    if (req.method === "POST" && skillAction) {
      const skillId = decodeURIComponent(skillAction[1]);
      const item = await setSkillFrozen(skillId, skillAction[2] === "freeze");
      if (!item) {
        send(res, 404, { error: "not_found" }, origin);
        return;
      }
      const removedFolders = [];
      if (skillAction[2] === "freeze" && item.export_path) {
        await rm(item.export_path, { recursive: true, force: true }).catch((error) => {
          console.log(`[skill] freeze cleanup failed ${item.export_path} ${error.message}`);
        });
        removedFolders.push(item.export_path);
        console.log(`[skill] frozen ${skillId} removed=${item.export_path}`);
      } else {
        console.log(`[skill] ${skillAction[2]} ${skillId}`);
      }
      send(res, 200, {
        skill_id: item.skill_id,
        state: item.frozen ? "suspended" : "published",
        frozen: item.frozen,
        removed_folders: removedFolders,
      }, origin);
      return;
    }
    const oneSkill = url.pathname.match(/^\/v1\/skills\/([^/]+)$/);
    if (req.method === "DELETE" && oneSkill) {
      const skillId = decodeURIComponent(oneSkill[1]);
      const removed = await removeSkill(skillId);
      if (removed?.export_path) await rm(removed.export_path, { recursive: true, force: true }).catch(() => {});
      console.log(`[skill] delete ${skillId} ok=${Boolean(removed)}`);
      send(res, removed ? 200 : 404, { deleted: Boolean(removed), skill_id: skillId, removed_folders: removed?.export_path ? [removed.export_path] : [] }, origin);
      return;
    }
    if (req.method === "GET" && url.pathname.startsWith("/v1/skills/")) {
      const id = decodeURIComponent(url.pathname.slice("/v1/skills/".length));
      try {
        const handbook = await readFile(path.join(skillDir(id), "SKILL.md"), "utf8");
        const runtime = JSON.parse(await readFile(path.join(skillDir(id), "config", "runtime.json"), "utf8"));
        send(res, 200, { skill_id: id, handbook: handbook.split(/\n/).slice(0, 200).join("\n"), runtime }, origin);
      } catch {
        send(res, 404, { error: "not_found" }, origin);
      }
      return;
    }
    if (req.method === "GET" && url.pathname === "/v1/settings/token") {
      const rec = await readTokenRecord(url.searchParams.get("tenant") || "", url.searchParams.get("subsystem") || "");
      send(res, 200, {
        tenant: rec.tenant,
        subsystem: rec.subsystem,
        has_token: rec.has_token,
        headers: maskHeaders(rec.headers),
      }, origin);
      return;
    }
    if (req.method === "POST" && url.pathname === "/v1/settings/token") {
      const body = await readBody(req);
      const rec = await writeTokenRecord(body.tenant || "", body.subsystem || "", body.headers || {});
      const writeback = await writebackExportedPackages({ subsystem: rec.subsystem, headers: rec.headers });
      send(res, 200, {
        ok: true,
        tenant: rec.tenant,
        subsystem: rec.subsystem,
        headers: maskHeaders(rec.headers),
        updated_at: rec.updated_at,
        updated_packages: writeback.updated,
      }, origin);
      return;
    }
    if (req.method === "GET" && (url.pathname === "/export/directory" || url.pathname === "/v1/export/directory")) {
      send(res, 200, { out_dir: await readExportDirectory() }, origin);
      return;
    }
    if ((req.method === "PUT" || req.method === "POST") && (url.pathname === "/export/directory" || url.pathname === "/v1/export/directory")) {
      const body = await readBody(req);
      send(res, 200, { out_dir: await writeExportDirectory(body.out_dir || "") }, origin);
      return;
    }
    const exported = url.pathname.match(/^\/v1\/pi-recordings\/([^/]+)\/export-skill$/);
    if (req.method === "POST" && exported) {
      const id = decodeURIComponent(exported[1]);
      if (!id.startsWith("rec_")) {
        send(res, 400, { error: "recording_id" }, origin);
        return;
      }
      const body = await readBody(req);
      const result = await exportSkill(id, body.out_dir);
      send(res, 200, result, origin);
      return;
    }
    if (req.method === "GET" && (url.pathname === "/" || url.pathname === "/index.html")) {
      res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
      createReadStream(path.join(webDir(), "index.html")).pipe(res);
      return;
    }
    send(res, 404, { error: "not_found" }, origin);
  });
  attachWebSocket(server, { startRecordingPi });
  return server;
}

export function listen(server, port = Number(process.env.PORT || 18081)) {
  return new Promise((resolve) => {
    server.listen(port, "127.0.0.1", () => resolve(server.address()));
  });
}

const isMain = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  listen(createApp()).then((address) => {
    console.log(`playwright-cabp http://127.0.0.1:${address.port}`);
  });
}
