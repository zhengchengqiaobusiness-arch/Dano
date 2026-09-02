import path from "node:path";
import { createHash } from "node:crypto";
import { chmod, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import type { CapabilityContract } from "../domain.js";
import { normalizeCatalog } from "../catalog/normalize.js";
import { buildApprovedRoutes } from "../planner/routes.js";
import { writeJson } from "../utils.js";
import { exportableCapabilities, isPrimaryCapability } from "../inference/export-scope.js";
import {
  buildCapabilities,
  buildInputForms,
  buildOptions,
  buildRoute,
  buildSkillMd,
  classifyExported,
  exportedQuestion
} from "./skill-handbook.js";

const operationNames: Record<CapabilityContract["operation"], string> = {
  query: "查询", create: "新建", update: "修改", review: "审核", delete: "删除",
  authenticate: "认证", upload: "上传", download: "下载", action: "业务动作", unknown: "未识别"
};

const PATH_SKIP = new Set(["admin-api", "api", "erp", "system", "page", "create", "update", "delete", "simple-list", "list", "get", "query", "save"]);

export function resourceSlugFromPath(pathTemplate: string) {
  const parts = pathTemplate.split("/").filter(part => part && !PATH_SKIP.has(part));
  return (parts.slice(-2).join("-") || parts[0] || "").replace(/-page$/, "");
}

export function normalizeSkillName(value: string, capabilities: CapabilityContract[] = []) {
  const ascii = value.normalize("NFKD").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 64);
  if (ascii) return ascii;
  const primary = capabilities.filter(isPrimaryCapability);
  const fromPath = resourceSlugFromPath((primary[0] || capabilities[0])?.transport.pathTemplate || "");
  return fromPath || `business-skill-${createHash("sha256").update(value).digest("hex").slice(0, 8)}`;
}

function exportedTitle(capability: CapabilityContract, displayName: string, capabilities: CapabilityContract[]) {
  if (capability.editing?.title === "manual") return capability.title;
  if (/[\u4e00-\u9fff]/.test(capability.title) && !/[a-z]{3,}/i.test(capability.title)) {
    return capability.title;
  }
  if (isPrimaryCapability(capability) && displayName) {
    return `${operationNames[capability.operation]}${displayName.replace(/管理$/, "")}`;
  }
  const usedBy = capabilities.flatMap(item => item.inputForm).find(field =>
    field.candidates?.type === "capability" && field.candidates.capabilityId === capability.id
  );
  if (usedBy) return `查询${usedBy.label}`;
  return capability.title;
}

function exportedDescription(capability: CapabilityContract, displayName: string) {
  if (capability.editing?.description === "manual") return capability.description;
  if (isPrimaryCapability(capability) && displayName) {
    return `对「${displayName}」执行${operationNames[capability.operation]}。只使用合同里的接口和调用方字段。`;
  }
  return capability.description;
}

function withExportTitles(capabilities: CapabilityContract[], displayName: string) {
  return capabilities.map(capability => ({
    ...capability,
    title: exportedTitle(capability, displayName, capabilities),
    description: exportedDescription(capability, displayName)
  }));
}

function exportedCapability(capability: CapabilityContract, capabilities: CapabilityContract[]) {
  return {
    id: capability.id,
    kind: "atomic",
    role: isPrimaryCapability(capability) ? "primary" : "lookup",
    title: capability.title,
    description: capability.description,
    operation: capability.operation,
    confidence: capability.confidence,
    transport: capability.transport,
    inputSchema: capability.inputSchema,
    outputSchema: capability.outputSchema,
    inputForm: capability.inputForm,
    inputQuestions: capability.inputForm.filter(field => field.source === "caller").map(field => exportedQuestion(field, capabilities)),
    evidence: capability.evidence.filter(item => item.kind === "network").slice(0, 2),
    sideEffect: capability.sideEffect,
    confirmation: capability.confirmation,
    completion: capability.completion,
    bindings: capability.bindings,
    validation: {
      status: capability.validation.status,
      verifiedAt: capability.validation.verifiedAt
    }
  };
}

export async function exportSkill(outputRoot: string, requestedName: string, allCapabilities: CapabilityContract[]) {
  const selected = exportableCapabilities(normalizeCatalog(allCapabilities));
  if (!selected.length) throw new Error("没有可导出的已验证能力");
  const unresolved = selected.flatMap(capability => capability.inputForm.filter(field =>
    field.required && field.source !== "caller" && field.source !== "binding" && !field.defaultRule
  ).map(field => `${capability.id}:${field.path}`));
  if (unresolved.length) throw new Error(`存在没有处理规则的系统必填字段：${unresolved.join("、")}`);

  const displayName = requestedName.trim() || normalizeSkillName(requestedName, selected);
  const capabilities = withExportTitles(selected, displayName);
  const skillName = normalizeSkillName(requestedName, capabilities);
  const directory = path.join(outputRoot, skillName);
  const referencesDir = path.join(directory, "references");
  const scriptsDir = path.join(directory, "scripts");
  const routesDir = path.join(referencesDir, "routes");
  await mkdir(routesDir, { recursive: true });
  await mkdir(scriptsDir, { recursive: true });

  const routes = buildApprovedRoutes(capabilities);
  const { primary, lookups } = classifyExported(capabilities);
  await writeFile(path.join(directory, "SKILL.md"), buildSkillMd(skillName, displayName, capabilities, routes), "utf8");
  await writeJson(path.join(referencesDir, "CONTRACT.json"), {
    schemaVersion: "2.0",
    skill: skillName,
    generatedAt: new Date().toISOString(),
    policy: {
      ambiguity: "ask-user",
      composition: "approved-bindings-only",
      writes: "explicit-confirmation-at-execution",
      completion: "http-status-and-all-assertions"
    },
    capabilities: capabilities.map(capability => exportedCapability(capability, capabilities)),
    routes
  });
  await writeFile(path.join(referencesDir, "CAPABILITIES.md"), buildCapabilities(capabilities, routes), "utf8");
  await writeFile(path.join(referencesDir, "INPUT_FORMS.md"), buildInputForms(capabilities), "utf8");
  await writeFile(path.join(referencesDir, "OPTIONS.md"), buildOptions(capabilities), "utf8");
  for (const route of routes) {
    await writeFile(path.join(routesDir, `${route.id}.md`), buildRoute(route, capabilities), "utf8");
  }
  await rm(path.join(referencesDir, "EVIDENCE.md"), { force: true });
  await rm(path.join(referencesDir, "reference.md"), { force: true });

  for (const script of ["execute.py", "candidates.py", "format_list.py"]) {
    const source = await readFile(new URL(`./python/${script}`, import.meta.url), "utf8");
    const target = path.join(scriptsDir, script);
    await writeFile(target, source, "utf8");
    await chmod(target, 0o755);
  }

  return {
    dir: directory,
    count: capabilities.length,
    primaryCount: primary.length,
    lookupCount: lookups.length,
    skillName,
    displayName,
    capabilityIds: capabilities.map(capability => capability.id),
    primaryCapabilityIds: primary.map(capability => capability.id),
    lookupCapabilityIds: lookups.map(capability => capability.id),
    routeIds: routes.map(route => route.id)
  };
}
