/**
 * 出包鉴权：只要完整可用头，不要 [sealed:] 占位符。
 */

import { readFile } from "node:fs/promises";
import path from "node:path";
import {
  collectRecordingAuth,
  readAuthVault,
  vaultFilePath,
  usableAuthHeaders,
  baseUrlFromSources,
} from "../auth-vault.mjs";
import { parseLeadingJson } from "../fs-store.mjs";
import { readTokenRecord } from "./token-store.mjs";
import { logExport } from "../policy.mjs";

export async function readRecordingManifest(files, recordingId) {
  try {
    return parseLeadingJson(await readFile(path.join(files.directory(recordingId), "manifest.json"), "utf8"));
  } catch (error) {
    if (error?.code === "ENOENT") return null;
    throw error;
  }
}

export async function extractAuthHeadersFromEvidence(evidence, recordingId) {
  const files = evidence?.files;
  if (!files || !recordingId) return {};
  const events = await files.readEvidence(recordingId).catch(() => []);
  const vault = await readAuthVault(vaultFilePath(files.directory(recordingId)));
  const manifest = await readRecordingManifest(files, recordingId);
  return collectRecordingAuth({
    events,
    vault,
    startUrl: manifest?.targetUrl || "",
  });
}

export async function resolveExportAuth({
  tenant,
  subsystem,
  requestHeaders,
  evidence,
  recordingId,
  tokenRoot,
} = {}) {
  const fromRequest = usableAuthHeaders(requestHeaders);
  if (Object.keys(fromRequest).length) {
    logExport(`鉴权命中 request header_names=${Object.keys(fromRequest).join(",")} recording_id=${recordingId || "-"}`);
    return { headers: fromRequest, source: "request" };
  }

  const stored = await readTokenRecord(tenant, subsystem, tokenRoot);
  if (stored.has_token) {
    logExport(`鉴权命中 store source=${stored.source || "store"} header_names=${Object.keys(stored.headers).join(",")} tenant=${tenant || "-"}`);
    return { headers: stored.headers, source: stored.source || "store" };
  }

  if (evidence && recordingId) {
    const captured = await extractAuthHeadersFromEvidence(evidence, recordingId);
    if (Object.keys(captured).length) {
      logExport(`鉴权命中 recording header_names=${Object.keys(captured).join(",")} recording_id=${recordingId}`);
      return { headers: captured, source: "recording" };
    }
  }
  logExport(`鉴权落空 request=empty store=empty recording=${evidence && recordingId ? "empty" : "skip"} recording_id=${recordingId || "-"}`);
  return { headers: {}, source: "" };
}

export async function resolveExportBaseUrl({ files, recordingId, draft } = {}) {
  const events = await files.readEvidence(recordingId).catch(() => []);
  const manifest = await readRecordingManifest(files, recordingId);
  const baseUrl = baseUrlFromSources({
    draft,
    events,
    startUrl: manifest?.targetUrl || "",
  });
  logExport(`解析 base_url=${baseUrl || "-"} recording_id=${recordingId || "-"} events=${Array.isArray(events) ? events.length : 0} start=${manifest?.targetUrl || "-"}`);
  return baseUrl;
}

