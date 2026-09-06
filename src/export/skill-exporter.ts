import path from "node:path";
import { createHash } from "node:crypto";
import { chmod, mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import type { CapabilityContract, DataBinding, EvidenceEvent, InputFormField } from "../domain.js";
import { normalizeCatalog } from "../catalog/normalize.js";
import { buildApprovedRoutes, collectRouteIssues } from "../planner/routes.js";
import { id, writeJson } from "../utils.js";
import { exportableCapabilities, isPrimaryCapability, pageRoleLabel } from "../inference/export-scope.js";
import { assertExportable } from "../review/catalog-review.js";
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
  const ascii = value.normalize("NFKD").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48);
  if (ascii) return ascii;
  const digest = createHash("sha256").update(value.trim() || "skill").digest("hex").slice(0, 8);
  const primary = capabilities.filter(item => isPrimaryCapability(item, capabilities));
  const resources = [...new Set(primary.map(item => resourceSlugFromPath(item.transport.pathTemplate || "")).filter(Boolean))];
  if (resources.length === 1) return resources[0]!;
  const routes = buildApprovedRoutes(capabilities);
  if (routes.length === 1) {
    const target = capabilities.find(item => item.id === routes[0]!.targetCapabilityId);
    const routeResource = target && resourceSlugFromPath(target.transport.pathTemplate || "");
    if (routeResource) return routeResource;
  }
  return `skill-${digest}`;
}

export function uniqueSkillExportName(slug: string) {
  return `${slug}-${id("sk")}`;
}

export function exportedCapabilityTitle(capability: CapabilityContract, displayName: string, capabilities: CapabilityContract[]) {
  if (capability.editing?.title === "manual") return capability.title;
  const operation = operationNames[capability.operation];
  const baseName = displayName.replace(/管理$/, "");
  const peers = capabilities.filter(item =>
    isPrimaryCapability(item, capabilities) && item.operation === capability.operation
  );
  if (isPrimaryCapability(capability, capabilities) && displayName && peers.length > 1) {
    const kind = pageRoleLabel(capability.transport.pathTemplate);
    const peerKinds = peers.map(item => pageRoleLabel(item.transport.pathTemplate));
    if (kind && peerKinds.filter(item => item === kind).length === 1) {
      return `${operation}${baseName}${kind}`;
    }
    const resource = resourceSlugFromPath(capability.transport.pathTemplate || "");
    return `${operation}${baseName}${kind || resource}`;
  }
  if (/[\u4e00-\u9fff]/.test(capability.title) && !/[a-z]{3,}/i.test(capability.title)) {
    return capability.title;
  }
  if (isPrimaryCapability(capability, capabilities) && displayName) {
    return `${operation}${baseName}`;
  }
  const usedBy = capabilities.flatMap(item => item.inputForm).find(field =>
    field.candidates?.type === "capability" && field.candidates.capabilityId === capability.id
  );
  if (usedBy) return `查询${usedBy.label}`;
  return capability.title;
}

function exportedDescription(capability: CapabilityContract, displayName: string, capabilities: CapabilityContract[]) {
  if (capability.editing?.description === "manual") return capability.description;
  if (isPrimaryCapability(capability, capabilities) && displayName) {
    return `对「${displayName}」执行${operationNames[capability.operation]}。只使用合同里的接口和调用方字段。`;
  }
  const usedBy = capabilities.flatMap(item => item.inputForm).find(field =>
    field.candidates?.type === "capability" && field.candidates.capabilityId === capability.id
  );
  if (usedBy) return `只为选择「${usedBy.label}」提供候选，不是独立业务动作。`;
  return capability.description;
}

function withExportTitles(capabilities: CapabilityContract[], displayName: string) {
  return capabilities.map(capability => ({
    ...capability,
    title: exportedCapabilityTitle(capability, displayName, capabilities),
    description: exportedDescription(capability, displayName, capabilities)
  }));
}

function exportedField(field: InputFormField) {
  const exported: Record<string, unknown> = {
    path: field.path,
    name: field.name,
    label: field.label,
    valueType: field.valueType,
    source: field.source,
    required: field.required,
    systemHandled: field.systemHandled,
    widget: field.widget
  };
  if (field.defaultRule) exported.defaultRule = field.defaultRule;
  if (field.dateFormat) exported.dateFormat = field.dateFormat;
  if (field.dateClock) exported.dateClock = field.dateClock;
  if (field.dateClocks?.length) exported.dateClocks = field.dateClocks;
  if (field.requestFormat) exported.requestFormat = field.requestFormat;
  if (field.sourceDetail) exported.sourceDetail = field.sourceDetail;
  if (field.candidates) exported.candidates = field.candidates;
  return exported;
}

function exportedBinding(binding: DataBinding) {
  return {
    id: binding.id,
    fromCapabilityId: binding.fromCapabilityId,
    fromPath: binding.fromPath,
    toPath: binding.toPath,
    approved: binding.approved
  };
}

function exportedCapability(capability: CapabilityContract, capabilities: CapabilityContract[]) {
  return {
    id: capability.id,
    kind: "atomic",
    role: isPrimaryCapability(capability, capabilities) ? "primary" : "lookup",
    title: capability.title,
    description: capability.description,
    operation: capability.operation,
    transport: capability.transport,
    outputSchema: capability.outputSchema,
    inputForm: capability.inputForm.map(exportedField),
    inputQuestions: (() => {
      const caller = capability.inputForm.filter(field => field.source === "caller");
      return caller.map(field => exportedQuestion(field, capabilities, capability.inputForm, capability));
    })(),
    sideEffect: capability.sideEffect,
    confirmation: capability.confirmation,
    completion: {
      acceptedHttpStatuses: capability.completion.acceptedHttpStatuses,
      ...(capability.completion.requiredOutputPaths?.length
        ? { requiredOutputPaths: capability.completion.requiredOutputPaths }
        : {}),
      ...(capability.completion.assertions?.length ? { assertions: capability.completion.assertions } : {})
    },
    bindings: capability.bindings.filter(binding => binding.approved).map(exportedBinding),
    validation: {
      status: capability.validation.status
    }
  };
}

export async function exportSkill(
  outputRoot: string,
  requestedName: string,
  allCapabilities: CapabilityContract[],
  match: string[] = [],
  events: EvidenceEvent[] = []
) {
  const catalog = normalizeCatalog(allCapabilities);
  const selected = exportableCapabilities(catalog, match);
  if (!selected.length) throw new Error("没有可导出的已验证主能力。下拉和用户分页不是主能力。");
  assertExportable(selected, events);

  const displayName = requestedName.trim() || normalizeSkillName(requestedName, selected);
  const capabilities = withExportTitles(selected, displayName);
  const slug = normalizeSkillName(requestedName, capabilities);
  const skillName = uniqueSkillExportName(slug);
  const directory = path.join(outputRoot, skillName);
  const referencesDir = path.join(directory, "references");
  const scriptsDir = path.join(directory, "scripts");
  await mkdir(outputRoot, { recursive: true });
  await mkdir(directory);
  const routesDir = path.join(referencesDir, "routes");
  await mkdir(routesDir, { recursive: true });
  await mkdir(scriptsDir, { recursive: true });

  const routes = buildApprovedRoutes(capabilities);
  const routeIssues = collectRouteIssues(capabilities);
  const { primary, lookups } = classifyExported(capabilities);
  await writeFile(path.join(directory, "SKILL.md"), buildSkillMd(skillName, displayName, capabilities, routes), "utf8");
  await writeJson(path.join(referencesDir, "CONTRACT.json"), {
    schemaVersion: "2.0",
    skill: skillName,
    policy: {
      ambiguity: "ask-user",
      composition: "approved-bindings-only",
      writes: "explicit-confirmation-at-execution",
      completion: "http-status-and-all-assertions"
    },
    capabilities: capabilities.map(capability => exportedCapability(capability, capabilities)),
    routes,
    ...(routeIssues.length ? { routeIssues } : {})
  });
  await writeFile(path.join(referencesDir, "CAPABILITIES.md"), buildCapabilities(capabilities, routes), "utf8");
  await writeFile(path.join(referencesDir, "INPUT_FORMS.md"), buildInputForms(capabilities), "utf8");
  await writeFile(path.join(referencesDir, "OPTIONS.md"), buildOptions(capabilities), "utf8");
  for (const route of routes) {
    await writeFile(path.join(routesDir, `${route.id}.md`), buildRoute(route, capabilities), "utf8");
  }
  const wantedRoutes = new Set(routes.map(route => `${route.id}.md`));
  for (const file of await readdir(routesDir)) {
    if (!wantedRoutes.has(file)) await rm(path.join(routesDir, file), { force: true });
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
    slug,
    displayName,
    capabilityIds: capabilities.map(capability => capability.id),
    primaryCapabilityIds: primary.map(capability => capability.id),
    lookupCapabilityIds: lookups.map(capability => capability.id),
    routeIds: routes.map(route => route.id)
  };
}
