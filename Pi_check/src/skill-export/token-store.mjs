/**
 * 本机鉴权文件。不读 back / Postgres。
 */

import { mkdir, readFile, writeFile, readdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { usableAuthHeaders } from "../auth-vault.mjs";
import { logExport } from "../policy.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

function safePart(value) {
  return String(value || "").replace(/[^a-zA-Z0-9._-]+/g, "_") || "unknown";
}

export function tokenStoreDir(root = ROOT) {
  return path.join(root, "data", "tokens");
}

export function tokenFile(tenant, subsystem, root = ROOT) {
  return path.join(tokenStoreDir(root), `${safePart(tenant)}__${safePart(subsystem)}.json`);
}

export function normalizeHeaders(raw) {
  return usableAuthHeaders(raw);
}

export function maskHeaders(headers) {
  const masked = {};
  for (const [key, value] of Object.entries(normalizeHeaders(headers))) {
    const text = String(value);
    masked[key] = text.length <= 8 ? "****" : `${text.slice(0, 4)}…${text.slice(-4)}`;
  }
  return masked;
}

export async function readTokenRecord(tenant, subsystem, root = ROOT) {
  try {
    const raw = JSON.parse(await readFile(tokenFile(tenant, subsystem, root), "utf8"));
    const headers = normalizeHeaders(raw?.headers);
    return {
      tenant: String(raw?.tenant || tenant || ""),
      subsystem: String(raw?.subsystem || subsystem || ""),
      headers,
      source: String(raw?.source || ""),
      updated_at: String(raw?.updated_at || ""),
      has_token: Object.keys(headers).length > 0,
    };
  } catch (error) {
    if (error?.code === "ENOENT") {
      return {
        tenant: String(tenant || ""),
        subsystem: String(subsystem || ""),
        headers: {},
        source: "",
        updated_at: "",
        has_token: false,
      };
    }
    throw error;
  }
}

export async function writeTokenRecord(tenant, subsystem, headers, { source = "manual", root = ROOT, merge = true } = {}) {
  const previous = merge ? await readTokenRecord(tenant, subsystem, root) : { headers: {} };
  const record = {
    tenant: String(tenant || ""),
    subsystem: String(subsystem || ""),
    headers: {
      ...normalizeHeaders(previous.headers),
      ...normalizeHeaders(headers),
    },
    source,
    updated_at: new Date().toISOString(),
  };
  await mkdir(tokenStoreDir(root), { recursive: true });
  await writeFile(tokenFile(tenant, subsystem, root), `${JSON.stringify(record, null, 2)}\n`, "utf8");
  return { ...record, has_token: Object.keys(record.headers).length > 0 };
}

export function authLocalPayload(headers) {
  return { headers: normalizeHeaders(headers) };
}

export async function writeAuthLocalFile(packageDir, headers) {
  const target = path.join(packageDir, "config", "auth.local.json");
  await mkdir(path.dirname(target), { recursive: true });
  await writeFile(target, `${JSON.stringify(authLocalPayload(headers), null, 2)}\n`, "utf8");
  return target;
}

export function exportDirFile(root = ROOT) {
  return path.join(root, "data", "export-dir.json");
}

export async function readExportDirectory(root = ROOT) {
  try {
    const raw = JSON.parse(await readFile(exportDirFile(root), "utf8"));
    return String(raw?.out_dir || "").trim();
  } catch (error) {
    if (error?.code === "ENOENT") return "";
    throw error;
  }
}

export async function writeExportDirectory(outDir, root = ROOT) {
  const next = String(outDir || "").trim();
  await mkdir(path.dirname(exportDirFile(root)), { recursive: true });
  await writeFile(exportDirFile(root), `${JSON.stringify({
    out_dir: next,
    updated_at: new Date().toISOString(),
  }, null, 2)}\n`, "utf8");
  return next;
}

export async function writebackExportedPackages({
  subsystem,
  headers,
  exportRoot,
  catalogRows = [],
} = {}) {
  const wanted = String(subsystem || "").trim();
  const payload = authLocalPayload(headers);
  const updated = [];
  const seen = new Set();
  const candidates = [];
  for (const row of catalogRows) {
    if (wanted && String(row.subsystem || "") !== wanted) continue;
    if (row.export_path) candidates.push(row.export_path);
    if (row.package_dir) candidates.push(row.package_dir);
  }
  if (exportRoot) {
    try {
      const entries = await readdir(exportRoot, { withFileTypes: true });
      for (const entry of entries) {
        if (entry.isDirectory()) candidates.push(path.join(exportRoot, entry.name));
      }
    } catch {
      // 导出根不存在时只回写目录里已知包
    }
  }
  for (const dir of candidates) {
    const resolved = path.resolve(String(dir || ""));
    if (!resolved || seen.has(resolved)) continue;
    seen.add(resolved);
    const runtimeFile = path.join(resolved, "config", "runtime.json");
    try {
      const runtime = JSON.parse(await readFile(runtimeFile, "utf8"));
      if (wanted && String(runtime.subsystem || "") !== wanted) continue;
      await writeAuthLocalFile(resolved, payload.headers);
      updated.push(resolved);
    } catch {
      // 不是消费者包则跳过
    }
  }
  logExport(`回写已导出包 subsystem=${wanted || "-"} candidates=${seen.size} updated=${updated.length}`);
  return { updated };
}
