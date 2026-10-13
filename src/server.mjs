import http from "node:http";
import { fileURLToPath } from "node:url";
import { createReadStream } from "node:fs";
import { readFile, rm } from "node:fs/promises";
import path from "node:path";
import { webDir, skillDir } from "./paths.mjs";
import { attachWebSocket } from "./ws-bridge.mjs";
import { exportCatalogSkills, exportSkill, loadRecording } from "./session.mjs";
import { exportedFolderToRemove, freezeExportFolder, getSkill, listSkillsPage, removeSkill, setSkillFrozen } from "./skillpack/catalog.mjs";
import {
  maskHeaders, persistExportDirectory, readTokenRecord, resolveExportDirectory, writebackExportedPackages, writeTokenRecord,
} from "./token-store.mjs";
import { AuthError, changePassword, getTenantByKey, login, registerTenant } from "./auth/service.mjs";

const CORS = new Set(["http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:19082", "http://localhost:19082"]);

function send(res, status, body, origin) {
  const headers = { "content-type": "application/json; charset=utf-8" };
  if (origin && CORS.has(origin)) {
    headers["access-control-allow-origin"] = origin;
    headers["access-control-allow-headers"] = "content-type, x-tenant-key";
    headers["access-control-allow-methods"] = "GET,POST,PUT,DELETE,OPTIONS";
  }
  res.writeHead(status, headers);
  res.end(typeof body === "string" ? body : JSON.stringify(body));
}

async function requireTenant(req, res, origin) {
  const key = String(req.headers["x-tenant-key"] || "");
  if (!key) {
    send(res, 401, { detail: "缺少 X-Tenant-Key" }, origin);
    return null;
  }
  const rec = await getTenantByKey(key);
  if (!rec) {
    send(res, 401, { detail: "X-Tenant-Key 无效" }, origin);
    return null;
  }
  return rec;
}

function sendAuthError(res, error, origin) {
  if (error instanceof AuthError) {
    send(res, error.status, { detail: error.detail }, origin);
    return true;
  }
  return false;
}

function tokenHeadersFromBody(body) {
  const headers = {};
  for (const [key, value] of Object.entries(body?.headers && typeof body.headers === "object" ? body.headers : {})) {
    if (value) headers[key] = String(value);
  }
  if (!Object.keys(headers).length && body?.token) {
    const name = String(body.header_name || "Authorization").trim() || "Authorization";
    const prefix = body.token_prefix == null ? "Bearer " : String(body.token_prefix);
    headers[name] = `${prefix}${body.token}`;
  }
  return headers;
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
    if (req.method === "POST" && url.pathname === "/tenants") {
      const body = await readBody(req);
      try {
        send(res, 200, await registerTenant(body), origin);
      } catch (error) {
        if (!sendAuthError(res, error, origin)) throw error;
      }
      return;
    }
    if (req.method === "POST" && url.pathname === "/auth/login") {
      const body = await readBody(req);
      try {
        send(res, 200, await login(body.username, body.password), origin);
      } catch (error) {
        if (!sendAuthError(res, error, origin)) throw error;
      }
      return;
    }
    if (req.method === "POST" && url.pathname === "/auth/change-password") {
      const body = await readBody(req);
      try {
        await changePassword(req.headers["x-tenant-key"] || "", body.old_password, body.new_password);
        send(res, 200, { status: "ok" }, origin);
      } catch (error) {
        if (!sendAuthError(res, error, origin)) throw error;
      }
      return;
    }
    if (req.method === "POST" && url.pathname === "/api/recordings") {
      send(res, 404, { error: "not_found" }, origin);
      return;
    }
    if (req.method === "GET" && url.pathname === "/v1/skills") {
      const rec = await requireTenant(req, res, origin);
      if (!rec) return;
      const page = Number(url.searchParams.get("page") || 1);
      const pageSize = Number(url.searchParams.get("page_size") || 0);
      const includeFrozen = url.searchParams.get("include_frozen") !== "0";
      send(res, 200, await listSkillsPage(page, pageSize, { tenant: rec.tenant, includeFrozen }), origin);
      return;
    }
    if (req.method === "POST" && url.pathname === "/v1/skills/export") {
      const rec = await requireTenant(req, res, origin);
      if (!rec) return;
      const body = await readBody(req);
      const result = await exportCatalogSkills(body.out_dir || body.directory || "", { tenant: rec.tenant });
      send(res, 200, result, origin);
      return;
    }
    const skillAction = url.pathname.match(/^\/v1\/skills\/([^/]+)\/(freeze|resume)$/);
    if (req.method === "POST" && skillAction) {
      const rec = await requireTenant(req, res, origin);
      if (!rec) return;
      const skillId = decodeURIComponent(skillAction[1]);
      const item = await setSkillFrozen(skillId, skillAction[2] === "freeze", rec.tenant);
      if (!item) {
        send(res, 404, { detail: `本公司无此 Skill: ${skillId}` }, origin);
        return;
      }
      const removedFolders = [];
      const exportPath = skillAction[2] === "freeze" ? freezeExportFolder(item) : "";
      if (exportPath) {
        try {
          await rm(exportPath, { recursive: true, force: true });
          removedFolders.push(exportPath);
          console.log(`[skill] frozen ${skillId} removed=${exportPath}`);
        } catch (error) {
          console.log(`[skill] freeze cleanup failed ${exportPath} ${error.message}`);
        }
      } else {
        console.log(`[skill] ${skillAction[2]} ${skillId}`);
      }
      send(res, 200, {
        skill_id: item.name || item.skill_id,
        state: item.frozen ? "suspended" : "published",
        frozen: item.frozen,
        removed_folders: removedFolders,
      }, origin);
      return;
    }
    const oneSkill = url.pathname.match(/^\/v1\/skills\/([^/]+)$/);
    if (req.method === "DELETE" && oneSkill) {
      const rec = await requireTenant(req, res, origin);
      if (!rec) return;
      const skillId = decodeURIComponent(oneSkill[1]);
      const removed = await removeSkill(skillId, rec.tenant);
      if (!removed) {
        send(res, 404, { detail: `本公司无此 Skill: ${skillId}`, deleted: false, skill_id: skillId, removed_folders: [] }, origin);
        return;
      }
      const exportPath = exportedFolderToRemove(removed);
      if (exportPath) await rm(exportPath, { recursive: true, force: true }).catch(() => {});
      console.log(`[skill] delete ${skillId} ok=true`);
      send(res, 200, {
        deleted: true,
        skill_id: skillId,
        removed_folders: exportPath ? [exportPath] : [],
      }, origin);
      return;
    }
    if (req.method === "GET" && url.pathname.startsWith("/v1/skills/")) {
      const rec = await requireTenant(req, res, origin);
      if (!rec) return;
      const id = decodeURIComponent(url.pathname.slice("/v1/skills/".length));
      const item = await getSkill(id, rec.tenant);
      if (!item) {
        send(res, 404, { error: "not_found" }, origin);
        return;
      }
      try {
        const handbook = await readFile(path.join(skillDir(item.skill_id || id), "SKILL.md"), "utf8");
        const runtime = JSON.parse(await readFile(path.join(skillDir(item.skill_id || id), "config", "runtime.json"), "utf8"));
        send(res, 200, { skill_id: item.skill_id || id, handbook: handbook.split(/\n/).slice(0, 200).join("\n"), runtime }, origin);
      } catch {
        send(res, 404, { error: "not_found" }, origin);
      }
      return;
    }
    if (req.method === "GET" && (url.pathname === "/v1/settings/token" || url.pathname === "/v1/settings/token/raw")) {
      const rec = await requireTenant(req, res, origin);
      if (!rec) return;
      const wanted = String(url.searchParams.get("tenant") || rec.tenant);
      if (wanted !== rec.tenant) {
        send(res, 403, { detail: "不能读取其他租户的 token" }, origin);
        return;
      }
      const token = await readTokenRecord(rec.tenant, url.searchParams.get("subsystem") || "");
      send(res, 200, {
        tenant: token.tenant,
        subsystem: token.subsystem,
        has_token: token.has_token,
        headers: url.pathname.endsWith("/raw") ? token.headers : maskHeaders(token.headers),
        source: token.source || "",
        updated_at: token.updated_at || "",
      }, origin);
      return;
    }
    if (req.method === "POST" && url.pathname === "/v1/settings/token") {
      const rec = await requireTenant(req, res, origin);
      if (!rec) return;
      const body = await readBody(req);
      const wanted = String(body.tenant || rec.tenant);
      if (wanted !== rec.tenant) {
        send(res, 403, { detail: "不能修改其他租户的 token" }, origin);
        return;
      }
      const incoming = tokenHeadersFromBody(body);
      if (!Object.keys(incoming).length) {
        send(res, 400, { detail: "需提供 headers 或 token" }, origin);
        return;
      }
      const previous = await readTokenRecord(rec.tenant, body.subsystem || "");
      const token = await writeTokenRecord(rec.tenant, body.subsystem || "", { ...previous.headers, ...incoming });
      const writeback = await writebackExportedPackages({
        subsystem: token.subsystem,
        headers: token.headers,
        tenant: rec.tenant,
      });
      send(res, 200, {
        ok: true,
        tenant: token.tenant,
        subsystem: token.subsystem,
        headers: maskHeaders(token.headers),
        updated_at: token.updated_at,
        updated_packages: writeback.updated,
      }, origin);
      return;
    }
    if (req.method === "GET" && (url.pathname === "/export/directory" || url.pathname === "/v1/export/directory")) {
      const rec = await requireTenant(req, res, origin);
      if (!rec) return;
      send(res, 200, { out_dir: await resolveExportDirectory() }, origin);
      return;
    }
    if ((req.method === "PUT" || req.method === "POST") && (url.pathname === "/export/directory" || url.pathname === "/v1/export/directory")) {
      const rec = await requireTenant(req, res, origin);
      if (!rec) return;
      const body = await readBody(req);
      send(res, 200, { out_dir: await persistExportDirectory(body.out_dir || body.directory || "") }, origin);
      return;
    }
    const exported = url.pathname.match(/^\/v1\/pi-recordings\/([^/]+)\/export-skill$/);
    if (req.method === "POST" && exported) {
      const id = decodeURIComponent(exported[1]);
      if (!id.startsWith("rec_")) {
        send(res, 400, { error: "recording_id" }, origin);
        return;
      }
      const key = String(req.headers["x-tenant-key"] || "");
      if (key) {
        const rec = await getTenantByKey(key);
        if (!rec) {
          send(res, 401, { detail: "X-Tenant-Key 无效" }, origin);
          return;
        }
        const recording = await loadRecording(id);
        if (recording?.tenant && recording.tenant !== rec.tenant) {
          send(res, 404, { detail: `本公司无此 Skill: ${id}` }, origin);
          return;
        }
      }
      const body = await readBody(req);
      const result = await exportSkill(id, body.out_dir);
      send(res, 200, result, origin);
      return;
    }
    if (req.method === "GET" && (url.pathname === "/" || url.pathname === "/index.html")) {
      const stream = createReadStream(path.join(webDir(), "index.html"));
      stream.on("error", () => {
        if (!res.headersSent) send(res, 404, { error: "not_found" }, origin);
        else res.destroy();
      });
      stream.once("open", () => {
        res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
        stream.pipe(res);
      });
      return;
    }
    send(res, 404, { error: "not_found" }, origin);
  });
  attachWebSocket(server, { startRecordingPi });
  return server;
}

export function listen(server, port = Number(process.env.PORT || 18081), host = process.env.HOST || "127.0.0.1") {
  return new Promise((resolve) => {
    server.listen(port, host, () => resolve(server.address()));
  });
}

const isMain = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  listen(createApp()).then((address) => {
    console.log(`playwright-cabp http://127.0.0.1:${address.port}`);
  });
}
