/**
 * 出包运输：按录制合同物化整包，再注入冻结运行时和完整鉴权。
 * Skill 4 只可保留忠实的 SKILL.md；执行器、合同、表单一律覆盖。
 */

import { mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { artifactRoot } from "../skill-package-tools.mjs";
import { validateSkillPackageDir } from "./validator.mjs";
import { authLocalPayload, writeAuthLocalFile } from "./token-store.mjs";
import { hasCredentialHeaders, usableAuthHeaders, baseUrlFromSources } from "../auth-vault.mjs";
import { extractAuthHeadersFromEvidence } from "./auth-resolve.mjs";
import {
  chooseHandbook,
  materializePackageTexts,
  writeMaterializedPackage,
} from "./contract-materialize.mjs";
import { logExport } from "../policy.mjs";

const TEMPLATES = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "templates");

export { extractAuthHeadersFromEvidence };

export function packageSlug(skillId) {
  const action = String(skillId || "").split(".").pop();
  const slug = String(action || skillId || "skill")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64);
  return slug || "skill";
}

export function originFromPath(raw) {
  try {
    return new URL(String(raw || "")).origin;
  } catch {
    return "";
  }
}

export function baseUrlFromContract(draft) {
  return baseUrlFromSources({ draft });
}

function isIsolatedPath(from) {
  return String(from).includes(`${path.sep}.isolated`);
}

function isNestedPackagePath(src, from) {
  const rel = path.relative(src, from);
  if (!rel || rel === ".") return false;
  const top = rel.split(path.sep)[0];
  if (!top || ["scripts", "references", "config"].includes(top)) return false;
  return existsSync(path.join(src, top, "SKILL.md"));
}

async function readText(file) {
  try {
    return await readFile(file, "utf8");
  } catch {
    return null;
  }
}

export async function stripNestedSkillPackages(root) {
  let entries = [];
  try {
    entries = await readdir(root, { withFileTypes: true });
  } catch {
    return [];
  }
  const removed = [];
  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    if (["scripts", "references", "config"].includes(entry.name)) continue;
    const nested = path.join(root, entry.name);
    if (existsSync(path.join(nested, "SKILL.md"))) {
      await rm(nested, { recursive: true, force: true });
      removed.push(entry.name);
    }
  }
  return removed;
}

async function injectFrozenScripts(dest, { tenant, subsystem, baseUrl }) {
  const config = {
    tenant: tenant || "",
    subsystem: subsystem || "oa",
    base_url: baseUrl || "",
    identity_probes: [],
  };
  const scripts = path.join(dest, "scripts");
  await mkdir(scripts, { recursive: true });
  const clientSrc = await readFile(path.join(TEMPLATES, "client.py"), "utf8");
  await writeFile(
    path.join(scripts, "client.py"),
    clientSrc.replace("__CONFIG__", JSON.stringify(config)),
    "utf8",
  );
  for (const name of ["wire_format.py", "format_list.py", "runtime.py", "flow.py"]) {
    await writeFile(
      path.join(scripts, name),
      await readFile(path.join(TEMPLATES, name), "utf8"),
      "utf8",
    );
  }
}

async function materializeExecutablePackage(dest, draft, { tenant, subsystem, baseUrl, skill4Handbook } = {}) {
  if (!Array.isArray(draft?.capabilities) || !draft.capabilities.length) {
    throw new Error("没有可导出的能力，拒绝猜编译");
  }
  const texts = materializePackageTexts(draft);
  const candidates = (Array.isArray(skill4Handbook) ? skill4Handbook : [skill4Handbook]).filter(Boolean);
  const handbook = chooseHandbook(candidates, texts.files["SKILL.md"], texts.contract);
  const handbookSource = candidates.includes(handbook) ? "skill4" : "materialized";
  logExport(`物化 dest=${dest} caps=${texts.contract.capabilities.length} routes=${texts.contract.routes.length} files=${Object.keys(texts.files).length} handbook=${handbookSource}`);
  await writeMaterializedPackage(dest, draft);
  await writeFile(
    path.join(dest, "SKILL.md"),
    handbook,
    "utf8",
  );
  await mkdir(path.join(dest, "config"), { recursive: true });
  await writeFile(path.join(dest, "config", "runtime.json"), `${JSON.stringify({
    tenant: tenant || "",
    subsystem: subsystem || "oa",
    base_url: baseUrl || "",
  }, null, 2)}\n`, "utf8");
  await injectFrozenScripts(dest, { tenant, subsystem, baseUrl });
  return { contract: texts.contract, handbook_source: handbookSource };
}

export async function seedFrozenArtifacts(files, recordingId, { tenant, subsystem, baseUrl, draft } = {}) {
  const dest = artifactRoot(files, recordingId);
  logExport(`预置产物 recording_id=${recordingId} dest=${dest} caps=${Array.isArray(draft?.capabilities) ? draft.capabilities.length : 0} base_url=${baseUrl || "-"}`);
  await mkdir(path.join(dest, "config"), { recursive: true });
  await writeFile(path.join(dest, "config", "runtime.json"), `${JSON.stringify({
    tenant: tenant || "",
    subsystem: subsystem || "oa",
    base_url: baseUrl || "",
  }, null, 2)}\n`, "utf8");
  await writeAuthLocalFile(dest, {});
  if (Array.isArray(draft?.capabilities) && draft.capabilities.length) {
    await materializeExecutablePackage(dest, draft, { tenant, subsystem, baseUrl });
    await writeAuthLocalFile(dest, {});
  } else {
    await injectFrozenScripts(dest, { tenant, subsystem, baseUrl });
  }
  return dest;
}

export async function refreshPackageTransport(dest, {
  tenant,
  subsystem,
  baseUrl,
  authHeaders,
  draft,
} = {}) {
  await stripNestedSkillPackages(dest);
  const runtimeFile = path.join(dest, "config", "runtime.json");
  let runtime = {};
  try {
    runtime = JSON.parse(await readFile(runtimeFile, "utf8"));
  } catch {
    runtime = {};
  }
  const nextBase = String(baseUrl || runtime.base_url || "").trim();
  const nextTenant = tenant || runtime.tenant || "";
  const nextSubsystem = subsystem || runtime.subsystem || "oa";
  if (Array.isArray(draft?.capabilities) && draft.capabilities.length) {
    const existingMd = await readText(path.join(dest, "SKILL.md"));
    await materializeExecutablePackage(dest, draft, {
      tenant: nextTenant,
      subsystem: nextSubsystem,
      baseUrl: nextBase,
      skill4Handbook: existingMd,
    });
  } else {
    await mkdir(path.join(dest, "config"), { recursive: true });
    await writeFile(runtimeFile, `${JSON.stringify({
      tenant: nextTenant,
      subsystem: nextSubsystem,
      base_url: nextBase,
    }, null, 2)}\n`, "utf8");
    await injectFrozenScripts(dest, {
      tenant: nextTenant,
      subsystem: nextSubsystem,
      baseUrl: nextBase,
    });
  }
  const headers = usableAuthHeaders(authHeaders);
  if (hasCredentialHeaders(headers)) {
    await writeAuthLocalFile(dest, headers);
  }
  logExport(`刷新运输 dest=${dest} rematerialize=${Array.isArray(draft?.capabilities) && draft.capabilities.length ? "yes" : "scripts_only"} token=${hasCredentialHeaders(headers) ? "full" : "empty"} base_url=${nextBase || "-"}`);
  return { export_path: dest, base_url: nextBase, token_missing: !hasCredentialHeaders(headers) };
}

export async function packSkill4Artifacts({
  files,
  recordingId,
  outDir,
  skillId,
  tenant,
  subsystem,
  draft,
  authHeaders,
  baseUrl = "",
  validate = true,
  useSkill4Handbook = true,
  existingHandbook = "",
  existingHandbookPath = "",
}) {
  if (!Array.isArray(draft?.capabilities) || !draft.capabilities.length) {
    logExport(`打包拒绝 没有可导出的能力 recording_id=${recordingId || "-"} skill_id=${skillId || "-"}`);
    throw new Error("没有可导出的能力，拒绝猜编译");
  }
  const src = artifactRoot(files, recordingId);
  const fromArtifacts = useSkill4Handbook ? await readText(path.join(src, "SKILL.md")) : "";
  const fromPrevious = useSkill4Handbook
    ? (existingHandbook || (existingHandbookPath ? await readText(path.join(existingHandbookPath, "SKILL.md")) : ""))
    : "";
  const skill4Handbook = useSkill4Handbook
    ? [fromPrevious, fromArtifacts].filter(Boolean)
    : [];
  const slug = packageSlug(skillId);
  const dest = path.join(String(outDir || "").trim() || src, slug);
  logExport(`打包开始 recording_id=${recordingId} skill_id=${skillId} dest=${dest} skill4_md=${skill4Handbook.length ? "yes" : "no"} caps=${draft.capabilities.length} validate=${validate ? "yes" : "no"}`);
  if (path.resolve(dest) !== path.resolve(src)) {
    await mkdir(path.dirname(dest), { recursive: true });
    await rm(dest, { recursive: true, force: true });
    await mkdir(dest, { recursive: true });
  } else {
    await stripNestedSkillPackages(dest);
  }
  const resolvedBase = String(baseUrl || "").trim() || baseUrlFromContract(draft);
  const headers = usableAuthHeaders(authHeaders);
  const packed = await materializeExecutablePackage(dest, draft, {
    tenant,
    subsystem,
    baseUrl: resolvedBase,
    skill4Handbook,
  });
  await writeAuthLocalFile(dest, headers);
  await stripNestedSkillPackages(dest);
  const tokenMissing = !hasCredentialHeaders(headers);
  if (!validate) {
    logExport(`打包跳过校验 dest=${dest} reason=catalog_dump handbook=${packed.handbook_source}`);
    logExport(`打包完成 dest=${dest} token_missing=${tokenMissing} base_url=${resolvedBase || "-"} handbook=${packed.handbook_source}`);
    return {
      slug,
      export_path: dest,
      token_missing: tokenMissing,
      base_url: resolvedBase,
      handbook_source: packed.handbook_source,
    };
  }
  const checked = await validateSkillPackageDir(dest, { sourceDraft: draft });
  const errors = (checked.issues || []).filter((item) => item.severity === "error");
  const warnings = (checked.issues || []).filter((item) => item.severity !== "error");
  logExport(`打包校验 dest=${dest} ok=${checked.ok} errors=${errors.length} warnings=${warnings.length} first=${errors[0]?.code || "-"}:${errors[0]?.message || "-"}`);
  if (!checked.ok) {
    for (const item of errors) {
      logExport(`打包校验错误 ${item.code || "-"} ${item.path || "-"} ${item.message || "-"}`);
    }
    const first = errors[0];
    throw new Error(first?.message || "validate_skill_package 失败");
  }
  logExport(`打包完成 dest=${dest} token_missing=${tokenMissing} base_url=${resolvedBase || "-"} handbook=${packed.handbook_source}`);
  return {
    slug,
    export_path: dest,
    token_missing: tokenMissing,
    base_url: resolvedBase,
    handbook_source: packed.handbook_source,
  };
}

export {
  isIsolatedPath,
  isNestedPackagePath,
};
