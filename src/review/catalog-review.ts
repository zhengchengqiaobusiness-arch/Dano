import type { CapabilityContract, EvidenceEvent, ReviewFinding, ReviewNext, ReviewReport, ReviewStage } from "../domain.js";
import { evidenceSample, isAssembledObjectField, isExecutableRule, parseComputedRule, unsoundComputedOperands } from "../inference/field-derivation.js";
import { queryCandidateForField } from "../inference/candidate-sources.js";
import { flattenRequestValues, isPaginationField, looksPickerField } from "../inference/field-resolver.js";
import { capabilitiesForSession, isCandidateSourceCapability, isNoiseCapability, sessionCatalogSlice, summarizeCatalog } from "../inference/export-scope.js";

const WRITE_OPERATIONS = new Set(["create", "update", "review", "delete", "upload", "action"]);
const NEXT_RANK: Record<ReviewNext, number> = { "re-record": 0, "re-analyze": 1, manual: 2, export: 3 };
const NEXT_LABEL: Record<ReviewNext, string> = {
  "re-record": "回到页面补录",
  "re-analyze": "补证据后重新分析再验证",
  manual: "需要人工改目录或平台后再验证",
  export: "可以导出"
};

const CHECK_GUIDANCE: Record<string, { stage: ReviewStage; next: ReviewNext; hint: string }> = {
  "recorded-network-evidence": { stage: "record", next: "re-record", hint: "回到页面重新操作，直到该请求出现在本次录制里" },
  "successful-response": { stage: "record", next: "re-record", hint: "回到页面提交到成功，不要在失败或弹窗未关时停止" },
  "write-ui-correlation": { stage: "record", next: "re-record", hint: "写操作必须点页面按钮提交，不要只抓到后台请求" },
  "caller-fields-backed-by-ui": { stage: "record", next: "re-record", hint: "把该字段在页面上填一遍或选出选项，再分析" },
  "transport-consistency": { stage: "analyze", next: "re-analyze", hint: "只分析本次录制，不要混进其它会话" },
  "completion-assertions-backed-by-evidence": { stage: "analyze", next: "re-analyze", hint: "完成条件必须来自成功响应，重新分析" },
  "known-operation": { stage: "analyze", next: "re-analyze", hint: "重新识别操作类型；对不上就停，不要猜" },
  "field-metadata-complete": { stage: "analyze", next: "re-analyze", hint: "重新分析字段名称、类型和来源" },
  "field-ownership-consistent": { stage: "analyze", next: "re-analyze", hint: "重新划分调用方字段和系统字段" },
  "system-required-fields-resolvable": { stage: "analyze", next: "re-analyze", hint: "给系统必填字段补唯一来源规则，不要冻录制样本" },
  "write-field-origins-resolved": { stage: "analyze", next: "re-analyze", hint: "本会话已有写请求和带出查询时，重新分析按 via 绑定，不要重开录制；不能把录制值写成固定值" },
  "computed-formula-operands-sound": { stage: "analyze", next: "re-analyze", hint: "公式不能用编号、枚举或时间戳当运算数；重新分析" },
  "picker-uses-recorded-query": { stage: "analyze", next: "re-analyze", hint: "选人/弹窗必须暴露已录制查询，不要把当前页冻成枚举" },
  "write-request-keys-covered": { stage: "analyze", next: "re-analyze", hint: "录制成功请求里的键都要有字段或拼接规则，不要丢父对象" },
  "candidate-rules-backed-by-evidence": { stage: "analyze", next: "re-analyze", hint: "枚举必须来自页面选项或已验证查询" },
  "binding-structure-valid": { stage: "analyze", next: "re-analyze", hint: "绑定必须指向已录制能力和已知字段" },
  "upload-transport-executable": { stage: "validate", next: "manual", hint: "multipart 上传当前不能重放，需要改平台后再验证" }
};

export function fieldOriginResolved(capability: CapabilityContract, field: CapabilityContract["inputForm"][number]) {
  if (field.source === "caller" || field.source === "binding") return true;
  if (isPaginationField(field.name)) return true;
  if (isAssembledObjectField(field, capability.inputForm)) return true;
  if (field.defaultRule && isExecutableRule(field.defaultRule)) return true;
  return false;
}

export function unsoundFormulaFields(capability: CapabilityContract) {
  return capability.inputForm.filter(field => {
    const expr = parseComputedRule(field.defaultRule || "");
    return Boolean(expr && unsoundComputedOperands(expr, capability.inputForm).length);
  });
}

export function pickerFieldsMissingQuery(capability: CapabilityContract, catalog: CapabilityContract[], events: EvidenceEvent[] = []) {
  return capability.inputForm.filter(field => {
    if (field.source !== "caller" || !looksPickerField(field)) return false;
    if (field.candidates?.type === "capability") return false;
    return Boolean(queryCandidateForField(field, catalog.filter(item => item.id !== capability.id), events));
  });
}

export function uncoveredWriteLeaves(capability: CapabilityContract, events: EvidenceEvent[] = []) {
  if (!WRITE_OPERATIONS.has(capability.operation) || !events.length) return [];
  const sample = evidenceSample(capability, events);
  if (!sample || typeof sample !== "object") return [];
  return flattenRequestValues(sample).filter(item => {
    const value = item.value;
    if (value !== null && typeof value === "object" && !Array.isArray(value)) return false;
    if (Array.isArray(value) && value.some(entry => entry && typeof entry === "object")) return false;
    return !capability.inputForm.some(field =>
      field.path === item.path
      || item.path === `${field.path}[*]`
      || item.path.startsWith(`${field.path}.`)
      || item.path.startsWith(`${field.path}[`)
    );
  });
}

export function unresolvedWriteFields(capability: CapabilityContract) {
  if (!WRITE_OPERATIONS.has(capability.operation)) return [];
  return capability.inputForm.filter(field => !fieldOriginResolved(capability, field));
}

function worstNext(findings: ReviewFinding[]): ReviewNext {
  if (!findings.length) return "export";
  return findings.slice().sort((left, right) => NEXT_RANK[left.next] - NEXT_RANK[right.next])[0]!.next;
}

function findingFromCheck(capability: CapabilityContract, check: CapabilityContract["validation"]["checks"][number]): ReviewFinding {
  const guide = CHECK_GUIDANCE[check.name] || { stage: "validate" as const, next: "re-analyze" as const, hint: check.detail };
  return {
    code: check.name,
    severity: "block",
    stage: guide.stage,
    next: guide.next,
    capabilityId: capability.id,
    capabilityTitle: capability.title,
    message: `${check.detail}。${guide.hint}`
  };
}

export function reviewCatalog(capabilities: CapabilityContract[], events: EvidenceEvent[] = []): ReviewReport {
  const { primary, lookups } = summarizeCatalog(capabilities);
  const neededLookups = lookups.filter(item => isCandidateSourceCapability(item, capabilities) && !isNoiseCapability(item));
  const findings: ReviewFinding[] = [];

  if (!primary.length) {
    findings.push({
      code: "no-primary-capability",
      severity: "block",
      stage: "analyze",
      next: "re-record",
      message: "没有识别到本页主能力（查询/新建/修改/审核/删除）。用户分页、产品下拉、库存带出不是主能力。请回到页面把业务操作录完整。"
    });
  }

  for (const capability of [...primary, ...neededLookups]) {
    if (capability.validation.status === "verified") continue;
    const failed = capability.validation.checks.filter(check => !check.ok);
    if (failed.length) {
      findings.push(...failed.map(check => findingFromCheck(capability, check)));
      continue;
    }
    findings.push({
      code: "not-verified",
      severity: "block",
      stage: "validate",
      next: "re-analyze",
      capabilityId: capability.id,
      capabilityTitle: capability.title,
      message: "尚未通过验证。先验证再导出，不要把候选入口径当成已通过。"
    });
  }

  for (const capability of primary.filter(item => WRITE_OPERATIONS.has(item.operation))) {
    for (const field of unresolvedWriteFields(capability)) {
      if (findings.some(item => item.capabilityId === capability.id && item.code === "write-field-origins-resolved" && item.fieldPath === field.path)) {
        continue;
      }
      findings.push({
        code: "write-field-origins-resolved",
        severity: "block",
        stage: "analyze",
        next: "re-analyze",
        capabilityId: capability.id,
        capabilityTitle: capability.title,
        fieldPath: field.path,
        message: `字段「${field.label}」(${field.name}) 不能唯一对应到页面输入、其它接口或计算公式。已有写请求和带出查询时重新分析绑定，不要重开录制，不要把录制样本冻成固定值。`
      });
    }
    for (const field of unsoundFormulaFields(capability)) {
      const expr = parseComputedRule(field.defaultRule || "") || "";
      findings.push({
        code: "computed-formula-operands-sound",
        severity: "block",
        stage: "analyze",
        next: "re-analyze",
        capabilityId: capability.id,
        capabilityTitle: capability.title,
        fieldPath: field.path,
        message: `字段「${field.label}」的公式 ${expr} 用了编号、枚举或时间戳当运算数。重新分析，不要把碰巧相等写成计算。`
      });
    }
    for (const field of pickerFieldsMissingQuery(capability, capabilities, events)) {
      findings.push({
        code: "picker-uses-recorded-query",
        severity: "block",
        stage: "analyze",
        next: "re-analyze",
        capabilityId: capability.id,
        capabilityTitle: capability.title,
        fieldPath: field.path,
        message: `字段「${field.label}」是选人/弹窗，但被冻成了页面枚举。应暴露已录制查询给调用方拉选项。`
      });
    }
    for (const leaf of uncoveredWriteLeaves(capability, events)) {
      findings.push({
        code: "write-request-keys-covered",
        severity: "block",
        stage: "analyze",
        next: "re-analyze",
        capabilityId: capability.id,
        capabilityTitle: capability.title,
        fieldPath: leaf.path,
        message: `录制成功请求键 ${leaf.path} 没有对应字段或拼接规则。补齐后重新分析，最终请求不能缺键。`
      });
    }
  }

  const next = worstNext(findings);
  const verifiedPrimary = primary.filter(item => item.validation.status === "verified");
  const status = findings.length === 0 && verifiedPrimary.length === primary.length && primary.length > 0 ? "passed" : "blocked";
  const primaryTitles = primary.map(item => item.title);
  const lookupTitles = neededLookups.map(item => item.title);
  const lines = [
    status === "passed"
      ? `审核通过，可以导出。通过标准：本页主能力均已验证，写字段均有唯一来源规则，公式不用编号/枚举/时间戳做运算，选人暴露已录制查询，请求键均有着落，用到的候选查询可用。`
      : `审核未通过，不能导出。下一步：${NEXT_LABEL[next]}。先收齐下面全部失败项，按阶段归堆后只补录一次、只分析一次、只验证一次；禁止发现一条就回头重验。`,
    `主能力 ${primary.length} 项${primaryTitles.length ? `（${primaryTitles.join("、")}）` : ""}；字段候选 ${neededLookups.length} 个${lookupTitles.length ? `（${lookupTitles.join("、")}）` : ""}。下拉、用户分页、IM、登录不是主能力。`
  ];
  if (findings.length) {
    lines.push("处理顺序：先一次性处理全部补录项，再一次性重新分析，最后只验证一次。");
    lines.push(...findings.map(item => `- ${item.capabilityTitle || item.code}：${item.message}`));
  }
  return {
    status,
    next: status === "passed" ? "export" : next,
    primaryCount: primary.length,
    lookupCount: neededLookups.length,
    verifiedPrimaryCount: verifiedPrimary.length,
    primaryTitles,
    lookupTitles,
    findings,
    summary: lines.join("\n")
  };
}

export function reviewNextLabel(next: ReviewNext) {
  return NEXT_LABEL[next];
}

export function reviewSession(
  catalog: CapabilityContract[],
  allEvents: EvidenceEvent[],
  sessionEvents: EvidenceEvent[]
) {
  const scoped = sessionCatalogSlice(catalog, allEvents, sessionEvents);
  return {
    capabilities: scoped,
    review: reviewCatalog(scoped, allEvents)
  };
}

export function assertExportable(capabilities: CapabilityContract[], events: EvidenceEvent[] = []) {
  const review = reviewCatalog(capabilities, events);
  if (review.status !== "passed") {
    throw new Error(review.summary);
  }
  return review;
}
