/**
 * 录制页「产出 Skill」与目录重导：按最新能力合同直接物化整包，默认不开 Skill 4。
 * 手册、合同、脚本都以当次草稿为准，禁止沿用旧 SKILL.md 挡住新字段。
 * 不校验能力对不对，禁止另编字段、接口或身份，禁止把规范文件打进消费者包。
 */

import { readdir } from "node:fs/promises";
import path from "node:path";
import { createHash } from "node:crypto";
import { readGeneratorGuides } from "./read-guides.mjs";
import { packSkill4Artifacts } from "./pack.mjs";
import { consumerContract } from "./contract-materialize.mjs";
import { resolveExportAuth, resolveExportBaseUrl, extractAuthHeadersFromEvidence } from "./auth-resolve.mjs";
import { writeTokenRecord, writebackExportedPackages, readTokenRecord, tokenStoreDir, listTenantTokenRecords, pickTenantCredential } from "./token-store.mjs";
import { skillManifestFromExport, upsertExportedSkill, listExportedSkills, getExportedSkill, getExportedSkillByRecording } from "./skill-catalog.mjs";
import { logExport } from "../policy.mjs";
import { hasCredentialHeaders, usableAuthHeaders } from "../auth-vault.mjs";

export function stableSkillId({ subsystem = "oa", recordingId = "", title = "", existing = "" } = {}) {
  const existingText = String(existing || "").trim();
  const rec = String(recordingId || "").trim();
  const recSlug = rec.replace(/_/g, "-");
  if (existingText.includes(".")) {
    const action = existingText.split(".").pop();
    const stalePlanner = rec.startsWith("rec_")
      && /^action_[a-f0-9]{8,}$/i.test(action)
      && action !== rec
      && action !== recSlug;
    if (!stalePlanner) return existingText;
  }
  const action = rec
    || `action_${createHash("sha1").update(String(title || "skill")).digest("hex").slice(0, 12)}`;
  return `${String(subsystem || "oa").replace(/[^a-zA-Z0-9_-]+/g, "-") || "oa"}.${action}`;
}

function contractEnvelope(draft) {
  const src = draft && typeof draft === "object" ? draft : {};
  return {
    title: src.title || "",
    capabilities: Array.isArray(src.capabilities) ? src.capabilities : [],
    steps: Array.isArray(src.steps) ? src.steps : [],
    links: Array.isArray(src.links) ? src.links : [],
    capability_relations: Array.isArray(src.capability_relations) ? src.capability_relations : [],
    unresolved: Array.isArray(src.unresolved) ? src.unresolved : [],
  };
}

async function resolveAuthHeaders(opts) {
  return resolveExportAuth(opts);
}

async function packLatestDraft({
  files,
  evidence,
  recordingId,
  resultId = "",
  tenant = "",
  subsystem = "oa",
  title = "",
  outDir = "",
  overlayDraft = null,
  authHeaders = null,
  existingSkillId = "",
  packArtifacts = packSkill4Artifacts,
  persistDraft = false,
  writebackCatalog = false,
  started = Date.now(),
  label = "导出",
} = {}) {
  const capIds = (overlayDraft?.capabilities || []).map((item) => item.capability_id || item.id).filter(Boolean);
  const guides = await readGeneratorGuides({ includeContent: false });
  if (!guides.ok) {
    logExport(`失败 规范缺失 ${guides.error}`, started);
    return { status: "export_failed", errors: [guides.error], clarification_questions: [] };
  }
  logExport(`${label} 规范就绪 files=${guides.files?.length || 0} dir=${guides.dir || "-"}`, started);
  const saved = await files.readDraft(recordingId);
  const latest = contractEnvelope(overlayDraft || saved?.draft || saved || {});
  const latestIds = latest.capabilities.map((item) => item.capability_id || item.id).filter(Boolean);
  if (!latest.capabilities.length) {
    logExport(`失败 没有可导出的能力 recording_id=${recordingId || "-"} disk_draft=${saved ? "yes" : "no"}`, started);
    return { status: "export_failed", errors: ["没有可导出的能力"], clarification_questions: [] };
  }
  logExport(`${label} 合同 caps=${latest.capabilities.length} ids=${latestIds.join(",") || "-"} overlay_caps=${capIds.length} source=${overlayDraft ? "overlay" : "disk"}`, started);
  if (persistDraft) {
    await files.writeDraft(recordingId, {
      recording_id: recordingId,
      saved_at: new Date().toISOString(),
      draft: latest,
      title: title || latest.title || "",
    });
    logExport(`${label} 合同已落盘`, started);
  }
  const auth = await resolveAuthHeaders({
    tenant,
    subsystem,
    requestHeaders: authHeaders,
    evidence,
    recordingId,
  });
  const baseUrl = await resolveExportBaseUrl({ files, recordingId, draft: latest });
  logExport(`${label} 鉴权 source=${auth.source || "missing"} header_names=${Object.keys(auth.headers).join(",") || "-"} token=${hasCredentialHeaders(auth.headers) ? "full" : "empty"} base_url=${baseUrl || "-"}`, started);
  if (hasCredentialHeaders(auth.headers)) {
    if (tenant) {
      await writeTokenRecord(tenant, subsystem, auth.headers, { source: auth.source || "recording" });
    }
    if (writebackCatalog) {
      const catalog = await listExportedSkills(files).catch(() => []);
      const written = await writebackExportedPackages({
        subsystem,
        headers: auth.headers,
        exportRoot: outDir,
        catalogRows: catalog,
      });
      logExport(`${label} 回写已导出包 catalog=${catalog.length} updated=${written.updated?.length || 0}`, started);
    }
  }
  const previousRow = existingSkillId
    ? await getExportedSkill(files, existingSkillId)
    : await getExportedSkillByRecording(files, recordingId);
  const previous = existingSkillId || previousRow?.name || "";
  const skillId = stableSkillId({
    subsystem,
    recordingId,
    title,
    existing: previous,
  });
  const projected = consumerContract(latest);
  let packed;
  try {
    packed = await packArtifacts({
      files,
      recordingId,
      outDir: outDir || path.join(files.directory(recordingId), "exported"),
      skillId,
      tenant,
      subsystem,
      draft: latest,
      authHeaders: auth.headers,
      baseUrl,
      validate: true,
      useSkill4Handbook: false,
    });
  } catch (error) {
    logExport(`失败 打包 skill_id=${skillId} ${error.message || error}`, started);
    return { status: "export_failed", errors: [error.message || String(error)], skill_id: skillId };
  }
  logExport(`${label} 已打包 path=${packed.export_path} token_missing=${packed.token_missing} handbook=${packed.handbook_source || "materialized"}`, started);
  const description = projected.capabilities
    .map((item) => item.intent || item.name)
    .filter(Boolean)
    .join("；") || previousRow?.description || "";
  const manifest = await upsertExportedSkill(files, skillManifestFromExport({
    skillId,
    title: title || latest.title || previousRow?.title || skillId,
    description,
    tenant: tenant || previousRow?.tenant || "",
    subsystem,
    action: skillId.split(".").slice(1).join("."),
    recordingId,
    resultId: resultId || previousRow?.result_id || recordingId,
    exportPath: packed.export_path,
    draft: latest,
  }));
  logExport(`${label}完成 skill_id=${skillId} version=${manifest.version || "-"} path=${packed.export_path}`, started);
  return {
    status: "exported",
    skill_id: skillId,
    skill_name: manifest.title,
    version: manifest.version,
    export_path: packed.export_path,
    token_missing: packed.token_missing,
    routes: projected.routes || [],
    errors: [],
    catalog_item: manifest,
  };
}

export async function exportRecordingSkill({
  files,
  evidence,
  recordingId,
  resultId = "",
  tenant = "",
  subsystem = "oa",
  title = "",
  outDir = "",
  draft: overlayDraft = null,
  authHeaders = null,
  existingSkillId = "",
  createExportSession = null,
  packArtifacts = packSkill4Artifacts,
  timeoutMs = Number(process.env.PI_EXPORT_TIMEOUT_MS || 900000),
} = {}) {
  void createExportSession;
  void timeoutMs;
  const started = Date.now();
  logExport(`开始 recording_id=${recordingId || "-"} title=${title || "-"} tenant=${tenant || "-"} skill4=no`, started);
  return packLatestDraft({
    files,
    evidence,
    recordingId,
    resultId,
    tenant,
    subsystem,
    title,
    outDir,
    overlayDraft,
    authHeaders,
    existingSkillId,
    packArtifacts,
    persistDraft: true,
    writebackCatalog: true,
    started,
    label: "出包",
  });
}

export async function dumpRecordingSkill({
  files,
  evidence,
  recordingId,
  resultId = "",
  tenant = "",
  subsystem = "oa",
  title = "",
  outDir = "",
  draft: overlayDraft = null,
  authHeaders = null,
  existingSkillId = "",
  packArtifacts = packSkill4Artifacts,
} = {}) {
  const started = Date.now();
  logExport(`快速导出 开始 recording_id=${recordingId || "-"} title=${title || "-"} skill4=no`, started);
  return packLatestDraft({
    files,
    evidence,
    recordingId,
    resultId,
    tenant,
    subsystem,
    title,
    outDir,
    overlayDraft,
    authHeaders,
    existingSkillId,
    packArtifacts,
    persistDraft: Boolean(overlayDraft),
    writebackCatalog: false,
    started,
    label: "快速导出",
  });
}

export async function reexportCatalogSkills({
  files,
  evidence,
  outDir,
  tenant,
  authHeaders,
  drafts = null,
  exportOne = dumpRecordingSkill,
} = {}) {
  const started = Date.now();
  const rows = await listExportedSkills(files, { includeFrozen: false });
  const overlay = drafts && typeof drafts === "object" && !Array.isArray(drafts) ? drafts : {};
  logExport(`目录快速导出开始 count=${rows.length} tenant=${tenant || "-"} out_dir=${outDir || "-"} overlay=${Object.keys(overlay).length}`, started);
  const written = [];
  const errors = [];
  for (const [index, row] of rows.entries()) {
    const recordingId = String(row.recording_id || "").trim();
    logExport(`目录快速导出 ${index + 1}/${rows.length} name=${row.name || "-"} recording_id=${recordingId || "-"} source=${row.source || "-"}`, started);
    if (!recordingId) {
      if (row.source === "imported") {
        const existingPath = String(row.export_path || row.package_dir || "").trim();
        if (existingPath) written.push(existingPath);
        logExport(`目录快速导出 ${row.name} source=imported 已在磁盘，跳过重导`, started);
        continue;
      }
      errors.push(`${row.name}: 缺少 recording_id`);
      logExport(`目录快速导出跳过 ${row.name} 缺少 recording_id`, started);
      continue;
    }
    const latestDraft = overlay[recordingId] && typeof overlay[recordingId] === "object"
      ? overlay[recordingId]
      : null;
    const outcome = await exportOne({
      files,
      evidence,
      recordingId,
      resultId: row.result_id,
      tenant: tenant || "",
      subsystem: row.subsystem,
      title: row.title,
      outDir,
      draft: latestDraft,
      authHeaders,
      existingSkillId: row.name || row.skill_id || "",
    });
    if (outcome.status === "exported") {
      written.push(outcome.export_path);
      logExport(`目录快速导出成功 ${row.name} path=${outcome.export_path}`, started);
    } else {
      errors.push(`${row.name}: ${(outcome.errors || []).join("; ") || "导出失败"}`);
      logExport(`目录快速导出失败 ${row.name} errors=${(outcome.errors || []).join("; ") || "导出失败"}`, started);
    }
  }
  logExport(`目录快速导出结束 written=${written.length} errors=${errors.length}`, started);
  return {
    out_dir: outDir,
    mode: "dump",
    count: written.length,
    written,
    errors,
  };
}

export async function hydrateAuthFromRecordings({ files, evidence, exportRoot = "", tokenRoot } = {}) {
  const started = Date.now();
  const ev = evidence || { files };
  const ids = await files.listRecordingIds();
  logExport(`启动回写开始 recordings=${ids.length} export_root=${exportRoot || "-"}`, started);
  const found = [];
  for (const recordingId of ids) {
    const headers = usableAuthHeaders(await extractAuthHeadersFromEvidence(ev, recordingId));
    if (!hasCredentialHeaders(headers)) {
      logExport(`启动回写 录制无可用凭证 recording_id=${recordingId}`, started);
      continue;
    }
    const draft = (await files.readDraft(recordingId).catch(() => null))?.draft || {};
    const baseUrl = await resolveExportBaseUrl({ files, recordingId, draft });
    found.push({ recordingId, headers, baseUrl, draft });
    logExport(`启动回写 录制有证 recording_id=${recordingId} header_names=${Object.keys(headers).join(",")} base_url=${baseUrl || "-"} caps=${Array.isArray(draft.capabilities) ? draft.capabilities.length : 0}`, started);
  }
  const catalog = await listExportedSkills(files).catch(() => []);
  logExport(`启动回写 目录 ${catalog.length} 条 有证=${found.length}`, started);
  const updated = [];
  for (const row of catalog) {
    const tenant = String(row.tenant || "").trim();
    const subsystem = row.subsystem || "oa";
    let stored = tenant ? await readTokenRecord(tenant, subsystem, tokenRoot) : { has_token: false, headers: {}, source: "" };
    if (!stored.has_token && tenant) {
      const fallback = pickTenantCredential(await listTenantTokenRecords(tenant, tokenRoot), subsystem);
      if (fallback) {
        stored = await writeTokenRecord(tenant, subsystem, fallback.headers, {
          source: fallback.source || "manual",
          root: tokenRoot,
        });
      }
    }
    if (stored.has_token) {
      const written = await writebackExportedPackages({
        subsystem,
        headers: stored.headers,
        exportRoot,
        catalogRows: [row],
      });
      updated.push(...written.updated);
      logExport(`启动回写用仓库 name=${row.name || "-"} source=${stored.source || "store"} dests=${written.updated.length}`, started);
      continue;
    }
    const hit = found.find((item) => item.recordingId === row.recording_id);
    if (!hit) {
      logExport(`启动回写跳过 无匹配录制 name=${row.name || "-"} recording_id=${row.recording_id || "-"}`, started);
      continue;
    }
    if (tenant) {
      await writeTokenRecord(tenant, subsystem, hit.headers, { source: "recording", root: tokenRoot });
    }
    const written = await writebackExportedPackages({
      subsystem,
      headers: hit.headers,
      exportRoot,
      catalogRows: [row],
    });
    updated.push(...written.updated);
    logExport(`启动回写用录制 name=${row.name || "-"} dests=${written.updated.length}`, started);
  }
  let tokenNames = [];
  try {
    tokenNames = await readdir(tokenStoreDir(tokenRoot));
  } catch {
    tokenNames = [];
  }
  for (const name of tokenNames) {
    const match = String(name).match(/^(.+)__(.+)\.json$/);
    if (!match) continue;
    const rec = await readTokenRecord(match[1], match[2], tokenRoot);
    if (rec.has_token) continue;
    const rows = catalog
      .filter((item) => String(item.subsystem || "oa") === match[2] && item.recording_id)
      .sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
    const hit = rows
      .map((row) => found.find((item) => item.recordingId === row.recording_id))
      .find(Boolean) || found.at(-1);
    if (!hit) continue;
    await writeTokenRecord(match[1], match[2], hit.headers, { source: "recording", root: tokenRoot });
    logExport(`启动回写补仓库 token ${name}`, started);
  }
  logExport(`启动回写完成 updated=${updated.length} recovered=${found.length}`, started);
  return { updated, recovered: found.length };
}
