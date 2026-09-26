import { mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { dataRoot, tokenDir } from "./paths.mjs";
import { hasCredentialHeaders, usableAuthHeaders } from "./auth-vault.mjs";

function safePart(value) {
  return String(value || "").replace(/[^\w.-]+/g, "_") || "_";
}

export function tokenFile(tenant, subsystem, root = dataRoot()) {
  return path.join(root === dataRoot() ? tokenDir() : path.join(root, "tokens"), `${safePart(tenant)}__${safePart(subsystem)}.json`);
}

export function maskHeaders(headers) {
  const masked = {};
  for (const [key, value] of Object.entries(usableAuthHeaders(headers))) {
    const text = String(value);
    masked[key] = text.length < 8 ? "****" : `${text.slice(0, 4)}…${text.slice(-4)}`;
  }
  return masked;
}

export async function readTokenRecord(tenant, subsystem) {
  try {
    const raw = JSON.parse(await readFile(tokenFile(tenant, subsystem), "utf8"));
    const headers = usableAuthHeaders(raw?.headers);
    return {
      tenant: String(raw?.tenant || tenant || ""),
      subsystem: String(raw?.subsystem || subsystem || ""),
      headers,
      has_token: hasCredentialHeaders(headers),
      updated_at: String(raw?.updated_at || ""),
    };
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
    return { tenant: String(tenant || ""), subsystem: String(subsystem || ""), headers: {}, has_token: false, updated_at: "" };
  }
}

export async function writeTokenRecord(tenant, subsystem, headers) {
  const file = tokenFile(tenant, subsystem);
  await mkdir(path.dirname(file), { recursive: true });
  const payload = {
    tenant: String(tenant || ""),
    subsystem: String(subsystem || ""),
    headers: usableAuthHeaders(headers),
    updated_at: new Date().toISOString(),
  };
  await writeFile(file, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  return { ...payload, has_token: hasCredentialHeaders(payload.headers) };
}

export async function writeAuthLocalFile(packageDir, headers, credential = null) {
  const target = path.join(packageDir, "config", "auth.local.json");
  await mkdir(path.dirname(target), { recursive: true });
  const payload = { headers: usableAuthHeaders(headers) };
  if (credential?.url && credential?.method) {
    payload.credential = { method: String(credential.method), url: String(credential.url) };
  }
  await writeFile(target, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  return target;
}

export async function writebackExportedPackages({ subsystem, headers }) {
  const wanted = String(subsystem || "").trim();
  const updated = [];
  const roots = [path.join(dataRoot(), "export")];
  for (const root of roots) {
    let entries = [];
    try {
      entries = await readdir(root, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      if (!entry.isDirectory()) continue;
      const dir = path.join(root, entry.name);
      try {
        const runtime = JSON.parse(await readFile(path.join(dir, "config", "runtime.json"), "utf8"));
        if (wanted && String(runtime.subsystem || "") !== wanted) continue;
        await writeAuthLocalFile(dir, headers);
        updated.push(dir);
      } catch {
        // 没有 runtime 的目录不是已导出包
      }
    }
  }
  return { updated };
}

export function exportDirFile() {
  return path.join(dataRoot(), "export-dir.json");
}

export async function readExportDirectory() {
  try {
    const raw = JSON.parse(await readFile(exportDirFile(), "utf8"));
    return String(raw?.out_dir || "").trim();
  } catch (error) {
    if (error?.code === "ENOENT") return "";
    throw error;
  }
}

export async function writeExportDirectory(outDir) {
  const next = String(outDir || "").trim();
  await mkdir(path.dirname(exportDirFile()), { recursive: true });
  await writeFile(exportDirFile(), `${JSON.stringify({ out_dir: next, updated_at: new Date().toISOString() }, null, 2)}\n`, "utf8");
  return next;
}
