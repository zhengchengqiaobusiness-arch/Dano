/**
 * 两条导出路径：
 * 录制页「产出 Skill」每次新建 Skill 4 会话。禁止因磁盘已有 SKILL.md 而跳过 Skill 4。
 * 目录页快速导出不开 Skill 4、不校验；沿用已发布或产物里的 Skill 4 手册，运输层只重写合同/脚本/token。
 */

import { readdir, rm } from "node:fs/promises";
import path from "node:path";
import { createHash } from "node:crypto";
import { artifactRoot } from "../skill-package-tools.mjs";
import { readGeneratorGuides } from "./read-guides.mjs";
import { packSkill4Artifacts, seedFrozenArtifacts } from "./pack.mjs";
import { resolveExportAuth, resolveExportBaseUrl, extractAuthHeadersFromEvidence } from "./auth-resolve.mjs";
import { writeTokenRecord, writebackExportedPackages, readTokenRecord, tokenStoreDir } from "./token-store.mjs";
import { skillManifestFromExport, upsertExportedSkill, listExportedSkills, getExportedSkill, getExportedSkillByRecording } from "./skill-catalog.mjs";
import { logExport } from "../policy.mjs";
import { usableAuthHeaders } from "../auth-vault.mjs";

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
  const started = Date.now();
  const capIds = (overlayDraft?.capabilities || []).map((item) => item.capability_id || item.id).filter(Boolean);
  logExport(`1/9 开始 recording_id=${recordingId || "-"} title=${title || "-"} tenant=${tenant || "-"} subsystem=${subsystem || "-"} overlay=${overlayDraft ? "yes" : "no"} overlay_caps=${capIds.length || 0} out_dir=${outDir || "-"} timeout_ms=${timeoutMs}`, started);
  const guides = await readGeneratorGuides();
  if (!guides.ok) {
    logExport(`失败 规范缺失 ${guides.error}`, started);
    return { status: "export_failed", errors: [guides.error], clarification_questions: [] };
  }
  logExport(`2/9 规范就绪 files=${guides.files?.length || 0} dir=${guides.dir || "-"}`, started);
  const saved = await files.readDraft(recordingId);
  const latest = contractEnvelope(overlayDraft || saved?.draft || saved || {});
  const latestIds = latest.capabilities.map((item) => item.capability_id || item.id).filter(Boolean);
  if (!latest.capabilities.length) {
    logExport(`失败 没有可导出的能力 recording_id=${recordingId || "-"} disk_draft=${saved ? "yes" : "no"}`, started);
    return { status: "export_failed", errors: ["没有可导出的能力"], clarification_questions: [] };
  }
  logExport(`3/9 合同就绪 caps=${latest.capabilities.length} ids=${latestIds.join(",") || "-"} steps=${latest.steps.length} links=${latest.links.length} relations=${latest.capability_relations.length} unresolved=${latest.unresolved.length} source=${overlayDraft ? "overlay" : "disk"}`, started);
  await files.writeDraft(recordingId, {
    recording_id: recordingId,
    saved_at: new Date().toISOString(),
    draft: latest,
    title: title || latest.title || "",
  });
  logExport("3/9 合同已落盘", started);
  const auth = await resolveAuthHeaders({
    tenant,
    subsystem,
    requestHeaders: authHeaders,
    evidence,
    recordingId,
  });
  const baseUrl = await resolveExportBaseUrl({ files, recordingId, draft: latest });
  logExport(`4/9 鉴权 source=${auth.source || "missing"} header_names=${Object.keys(auth.headers).join(",") || "-"} token=${Object.keys(auth.headers).length ? "full" : "empty"} base_url=${baseUrl || "-"}`, started);
  if (Object.keys(auth.headers).length) {
    if (tenant) {
      await writeTokenRecord(tenant, subsystem, auth.headers, { source: auth.source || "recording" });
      logExport(`4/9 已写 token 仓库 tenant=${tenant} subsystem=${subsystem}`, started);
    }
    const catalog = await listExportedSkills(files).catch(() => []);
    const written = await writebackExportedPackages({
      subsystem,
      headers: auth.headers,
      exportRoot: outDir,
      catalogRows: catalog,
    });
    logExport(`4/9 回写已导出包 catalog=${catalog.length} updated=${written.updated?.length || 0}`, started);
  }
  const artifacts = artifactRoot(files, recordingId);
  logExport(`5/9 清空预置产物 dest=${artifacts}`, started);
  await rm(artifacts, { recursive: true, force: true });
  await seedFrozenArtifacts(files, recordingId, { tenant, subsystem, baseUrl, draft: latest });
  logExport("5/9 预置产物完成", started);

  let submitted = null;
  const toolMod = await import("../pi-tools.mjs");
  if (typeof toolMod.createExportToolHost !== "function") {
    logExport("失败 当前 sidecar 没有 createExportToolHost", started);
    return { status: "export_failed", errors: ["当前 sidecar 没有 createExportToolHost"], clarification_questions: [] };
  }
  const tools = toolMod.createExportToolHost({
    files,
    recordingId,
    draft: latest,
    onSubmit: (payload) => {
      submitted = payload;
      logExport(`7/9 Skill4提交回调 ok=${payload.ok} skill_id=${payload.skill_id || "-"} routes=${payload.routes?.length || 0} errors=${JSON.stringify(payload.errors || [])}`, started);
    },
  });
  logExport(`6/9 开 Skill4 会话 timeout_ms=${timeoutMs}`, started);
  let session;
  try {
    if (!createExportSession) {
      const mod = await import("../pi-session.mjs");
      createExportSession = mod.createExportPiSession;
    }
    if (typeof createExportSession !== "function") {
      throw new Error("当前 sidecar 没有 createExportPiSession，无法开 Skill 4");
    }
    session = await createExportSession({
      recording: { id: `${recordingId}-export-${Date.now()}` },
      tools,
    });
  } catch (error) {
    logExport(`失败 Skill4 无法启动 ${error.message || error}`, started);
    return {
      status: "export_failed",
      errors: [error.message || String(error)],
      clarification_questions: [],
    };
  }
  logExport(`6/9 Skill4 会话已创建 session=${session.sessionId || "-"}`, started);
  try {
    await session.beginSkillExport({
      title: title || latest.title || "",
      timeoutMs,
      hasExport: () => Boolean(submitted?.ok),
    });
  } catch (error) {
    if (!submitted?.ok) {
      logExport(`失败 Skill4 ${error.message || error}`, started);
      await session.close?.({ reason: "export_failed" }).catch(() => {});
      return {
        status: "export_failed",
        errors: [error.message || String(error)],
        clarification_questions: [],
      };
    }
    logExport("7/9 Skill4 超时但已提交，继续打包", started);
  }
  await session.close?.({ reason: "exported" }).catch(() => {});
  if (!submitted?.ok) {
    logExport(`失败 Skill4 未提交 errors=${JSON.stringify(submitted?.errors || [])}`, started);
    return {
      status: "export_failed",
      errors: submitted?.errors?.length ? submitted.errors : ["Skill 4 没有 submit_skill_export"],
      clarification_questions: [],
    };
  }
  logExport(`7/9 Skill4 已提交 skill_id=${submitted.skill_id || "-"} routes=${submitted.routes?.length || 0}`, started);

  const previous = existingSkillId
    || (await getExportedSkillByRecording(files, recordingId))?.name
    || "";
  const skillId = stableSkillId({
    subsystem,
    recordingId,
    title,
    existing: previous || submitted.skill_id,
  });
  logExport(`8/9 准备打包 skill_id=${skillId} previous=${previous || "-"} submitted_id=${submitted.skill_id || "-"}`, started);
  if (tenant && Object.keys(auth.headers).length) {
    await writeTokenRecord(tenant, subsystem, auth.headers, { source: auth.source || "recording" });
  }
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
    });
  } catch (error) {
    logExport(`失败 打包 skill_id=${skillId} ${error.message || error}`, started);
    return { status: "export_failed", errors: [error.message || String(error)], skill_id: skillId };
  }
  logExport(`8/9 已打包 path=${packed.export_path} token_missing=${packed.token_missing} base_url=${packed.base_url || baseUrl || "-"}`, started);

  logExport(`9/9 写入目录 skill_id=${skillId}`, started);
  const manifest = await upsertExportedSkill(files, skillManifestFromExport({
    skillId,
    title: title || latest.title || skillId,
    description: submitted.description || "",
    tenant,
    subsystem,
    action: skillId.split(".").slice(1).join("."),
    recordingId,
    resultId: resultId || recordingId,
    exportPath: packed.export_path,
    draft: latest,
  }));
  logExport(`完成 skill_id=${skillId} version=${manifest.version || "-"} path=${packed.export_path}`, started);

  return {
    status: "exported",
    skill_id: skillId,
    skill_name: manifest.title,
    version: manifest.version,
    export_path: packed.export_path,
    token_missing: packed.token_missing,
    routes: submitted.routes || [],
    errors: [],
    catalog_item: manifest,
  };
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
  logExport(`快速导出 开始 recording_id=${recordingId || "-"} title=${title || "-"} tenant=${tenant || "-"} subsystem=${subsystem || "-"} out_dir=${outDir || "-"}`, started);
  const saved = await files.readDraft(recordingId);
  const latest = contractEnvelope(overlayDraft || saved?.draft || saved || {});
  const latestIds = latest.capabilities.map((item) => item.capability_id || item.id).filter(Boolean);
  if (!latest.capabilities.length) {
    logExport(`快速导出失败 没有可导出的能力 recording_id=${recordingId || "-"} disk_draft=${saved ? "yes" : "no"}`, started);
    return { status: "export_failed", errors: ["没有可导出的能力"], clarification_questions: [] };
  }
  logExport(`快速导出 合同 caps=${latest.capabilities.length} ids=${latestIds.join(",") || "-"} source=${overlayDraft ? "overlay" : "disk"}`, started);
  const auth = await resolveAuthHeaders({
    tenant,
    subsystem,
    requestHeaders: authHeaders,
    evidence,
    recordingId,
  });
  const baseUrl = await resolveExportBaseUrl({ files, recordingId, draft: latest });
  logExport(`快速导出 鉴权 source=${auth.source || "missing"} header_names=${Object.keys(auth.headers).join(",") || "-"} token=${Object.keys(auth.headers).length ? "full" : "empty"} base_url=${baseUrl || "-"}`, started);
  if (tenant && Object.keys(auth.headers).length) {
    await writeTokenRecord(tenant, subsystem, auth.headers, { source: auth.source || "recording" });
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
      validate: false,
      useSkill4Handbook: true,
      existingHandbookPath: previousRow?.export_path || previousRow?.package_dir || "",
    });
  } catch (error) {
    logExport(`快速导出失败 打包 skill_id=${skillId} ${error.message || error}`, started);
    return { status: "export_failed", errors: [error.message || String(error)], skill_id: skillId };
  }
  logExport(`快速导出 已写包 path=${packed.export_path} token_missing=${packed.token_missing} validate=no skill4_session=no handbook=${packed.handbook_source || "-"}`, started);
  const manifest = await upsertExportedSkill(files, skillManifestFromExport({
    skillId,
    title: title || latest.title || previousRow?.title || skillId,
    description: previousRow?.description || "",
    tenant: tenant || previousRow?.tenant || "",
    subsystem,
    action: skillId.split(".").slice(1).join("."),
    recordingId,
    resultId: resultId || previousRow?.result_id || recordingId,
    exportPath: packed.export_path,
    draft: latest,
  }));
  logExport(`快速导出完成 skill_id=${skillId} version=${manifest.version || "-"} path=${packed.export_path}`, started);
  return {
    status: "exported",
    skill_id: skillId,
    skill_name: manifest.title,
    version: manifest.version,
    export_path: packed.export_path,
    token_missing: packed.token_missing,
    routes: [],
    errors: [],
    catalog_item: manifest,
  };
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
    logExport(`目录快速导出 ${index + 1}/${rows.length} name=${row.name || "-"} recording_id=${recordingId || "-"}`, started);
    if (!recordingId) {
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
    if (!Object.keys(headers).length) {
      logExport(`启动回写 录制无可用头 recording_id=${recordingId}`, started);
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
    const stored = tenant ? await readTokenRecord(tenant, subsystem, tokenRoot) : { has_token: false, headers: {}, source: "" };
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
