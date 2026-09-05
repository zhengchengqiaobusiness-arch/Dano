import type { CapabilityContract, EvidenceEvent, OperationKind, ReviewFinding, ReviewNext, ReviewReport, ReviewStage } from "../domain.js";
import { evidenceSample, isAssembledCollectionField, isAssembledObjectField, isExecutableRule, parseComputedRule, unsoundComputedOperands } from "../inference/field-derivation.js";
import { queryCandidateForField } from "../inference/candidate-sources.js";
import { flattenRequestValues, isPaginationField, looksPickerField, realFieldName, uiNameMatches } from "../inference/field-resolver.js";
import { capabilitiesForSession, isCandidateSourceCapability, isNoiseCapability, sessionCatalogSlice, summarizeCatalog } from "../inference/export-scope.js";
import { inferUiOperationIntent, isSuccessfulNetworkEvidence } from "../inference/heuristics.js";
import { applyReviewActionPolicy } from "./review-action.js";

const WRITE_OPERATIONS = new Set(["create", "update", "review", "delete", "upload", "action"]);
const OPERATION_LABEL: Partial<Record<OperationKind, string>> = {
  query: "查询",
  create: "新建",
  update: "修改",
  review: "审核",
  delete: "删除",
  authenticate: "登录",
  upload: "上传",
  download: "下载",
  action: "操作"
};
const NEXT_RANK: Record<ReviewNext, number> = { "re-record": 0, "re-analyze": 1, manual: 2, export: 3 };
const NEXT_LABEL: Record<ReviewNext, string> = {
  "re-record": "回到页面补录",
  "re-analyze": "根据已有成功证据重新分析再验证，不要重新录制",
  manual: "需要人工改目录或平台后再验证",
  export: "可以导出"
};
const BUSINESS_DETAIL_COLLECTION = /(detail|item|line|entry|row|明细|清单)/i;
const NON_BUSINESS_COLLECTION = /(attach|upload|file|image|img|cced|copy|approv|audit)/i;

const CHECK_GUIDANCE: Record<string, { stage: ReviewStage; next: ReviewNext; hint: string }> = {
  "recorded-network-evidence": { stage: "record", next: "re-record", hint: "回到页面重新操作，直到该请求出现在本次录制里" },
  "successful-response": { stage: "record", next: "re-record", hint: "回到页面提交到成功，不要在失败或弹窗未关时停止" },
  "write-ui-correlation": { stage: "record", next: "re-record", hint: "写操作必须点页面按钮提交，不要只抓到后台请求" },
  "caller-fields-backed-by-ui": { stage: "analyze", next: "re-analyze", hint: "按本次成功请求重新划分字段来源，不要为了对字段再录一遍" },
  "transport-consistency": { stage: "analyze", next: "re-analyze", hint: "只分析本次录制，不要混进其它会话" },
  "completion-assertions-backed-by-evidence": { stage: "analyze", next: "re-analyze", hint: "完成条件必须来自成功响应，重新分析" },
  "known-operation": { stage: "analyze", next: "re-analyze", hint: "重新识别操作类型；对不上就停，不要猜" },
  "field-metadata-complete": { stage: "analyze", next: "re-analyze", hint: "重新分析字段名称、类型和来源" },
  "field-ownership-consistent": { stage: "analyze", next: "re-analyze", hint: "重新划分调用方字段和系统字段" },
  "system-required-fields-resolvable": { stage: "analyze", next: "re-analyze", hint: "按真实因果来源处理；无来源时由系统原样补齐录制成功请求值" },
  "write-field-origins-resolved": { stage: "analyze", next: "re-analyze", hint: "重新分析真实因果关系；无来源字段使用录制成功请求原值，不要制造绑定" },
  "computed-formula-operands-sound": { stage: "analyze", next: "re-analyze", hint: "公式不能用编号、枚举或时间戳当运算数；重新分析" },
  "picker-uses-recorded-query": { stage: "analyze", next: "re-analyze", hint: "选人/弹窗必须暴露已录制查询，不要把当前页冻成枚举" },
  "write-request-keys-covered": { stage: "analyze", next: "re-analyze", hint: "录制成功请求里的键都要有字段或拼接规则，不要丢父对象" },
  "candidate-rules-backed-by-evidence": { stage: "analyze", next: "re-analyze", hint: "枚举必须来自页面选项或已验证查询" },
  "binding-structure-valid": { stage: "analyze", next: "re-analyze", hint: "绑定必须指向已录制能力和已知字段" },
  "upload-transport-executable": { stage: "validate", next: "manual", hint: "multipart 上传当前不能重放，需要改平台后再验证" }
};

function coveredByCollectionTemplate(capability: CapabilityContract, field: CapabilityContract["inputForm"][number]) {
  if (!/整表|各行/.test(field.sourceDetail || "")) return false;
  const match = /^(.*)\[\*\]\./.exec(field.path);
  if (!match) return false;
  const parent = capability.inputForm.find(item => item.path === match[1]);
  return Boolean(parent?.defaultRule && isExecutableRule(parent.defaultRule) && parent.defaultRule.startsWith("literal:"));
}

export function fieldOriginResolved(capability: CapabilityContract, field: CapabilityContract["inputForm"][number]) {
  if (field.source === "caller" || field.source === "binding") return true;
  if (isPaginationField(field.name)) return true;
  if (isAssembledObjectField(field, capability.inputForm)) return true;
  if (isAssembledCollectionField(field, capability.inputForm)) return true;
  if (coveredByCollectionTemplate(capability, field)) return true;
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

function completeCoverageUiFields(capability: CapabilityContract, events: EvidenceEvent[]) {
  const evidenceIds = new Set(capability.evidence.map(ref => ref.eventId));
  const uiById = new Map(events.filter((event): event is Extract<EvidenceEvent, { kind: "ui" }> => event.kind === "ui").map(event => [event.id, event]));
  const submissions = events.filter((event): event is Extract<EvidenceEvent, { kind: "network" }> =>
    event.kind === "network"
    && evidenceIds.has(event.id)
    && Boolean(event.correlatedUiEvidenceId)
    && isSuccessfulNetworkEvidence(event)
  ).filter(event => {
    if (capability.operation !== "query") return true;
    const ui = uiById.get(event.correlatedUiEvidenceId!);
    return ui && inferUiOperationIntent(ui.text || ui.label || "", ui.pageUrl) === "query";
  }).sort((left, right) => left.at.localeCompare(right.at));
  return submissions.flatMap(event => uiById.get(event.correlatedUiEvidenceId!)?.form || []);
}

function emptyCoverageValue(value: unknown) {
  if (value === undefined || value === null) return true;
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value !== "string") return false;
  const text = value.replace(/<[^>]+>/g, "").replace(/&nbsp;|&#160;/gi, " ").trim();
  return !text || /^(请选择|请输入|请填写|please (select|enter|choose))/i.test(text);
}

function emptyChooserWithoutOptions(field: NonNullable<Extract<EvidenceEvent, { kind: "ui" }>["form"]>[number]) {
  const type = String(field.type || "");
  if (!/select|picker|chooser|dropdown|cascader/i.test(type)) return false;
  if ((field.options || []).length) return false;
  return (field.visibleOptions || []).some(item => /暂无数据|无数据|no data/i.test(String(item)));
}

function queryFilterSubmittedEmpty(
  capability: CapabilityContract,
  field: { name?: string },
  requestLeaves: Array<{ name: string; value: unknown }>
) {
  if (capability.operation !== "query") return false;
  const name = realFieldName(field.name);
  if (!name) return false;
  const leaves = requestLeaves.filter(item => uiNameMatches(name, item.name));
  return leaves.length > 0 && leaves.every(item => emptyCoverageValue(item.value));
}

function blankCompleteCoverageFields(capability: CapabilityContract, events: EvidenceEvent[]) {
  type CoverageField = NonNullable<Extract<EvidenceEvent, { kind: "ui" }>["form"]>[number];
  const requestLeaves = flattenRequestValues(evidenceSample(capability, events));
  const requestNames = new Set(requestLeaves.map(item => item.name));
  const coverage = new Map<string, { field: CoverageField; filled: boolean }>();
  for (const field of completeCoverageUiFields(capability, events)) {
    const label = String(field.label || field.name || "").trim();
    const type = String(field.type || "");
    if (!label || /upload|file|readonly|hidden/i.test(type) || /附件|上传/.test(label)) continue;
    if (emptyChooserWithoutOptions(field)) continue;
    const name = realFieldName(field.name);
    if (name && ![...requestNames].some(requestName => uiNameMatches(name, requestName))) continue;
    const key = `${label}:${field.rangeIndex ?? ""}`;
    const previous = coverage.get(key);
    coverage.set(key, {
      field: previous?.field || field,
      filled: Boolean(
        previous?.filled
        || !emptyCoverageValue(field.value)
        || queryFilterSubmittedEmpty(capability, field, requestLeaves)
      )
    });
  }
  return [...coverage.values()].filter(item => !item.filled).map(item => item.field);
}

function emptyBusinessCollections(capability: CapabilityContract, events: EvidenceEvent[]) {
  if (!WRITE_OPERATIONS.has(capability.operation)) return [];
  const sample = evidenceSample(capability, events);
  return flattenRequestValues(sample).filter(item =>
    Array.isArray(item.value)
    && item.value.length === 0
    && BUSINESS_DETAIL_COLLECTION.test(`${item.name} ${item.path}`)
    && !NON_BUSINESS_COLLECTION.test(`${item.name} ${item.path}`)
  );
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

export function reviewCatalog(
  capabilities: CapabilityContract[],
  events: EvidenceEvent[] = [],
  expectedOperations: OperationKind[] = [],
  completeFieldCoverage = false
): ReviewReport {
  const { primary, lookups } = summarizeCatalog(capabilities);
  const neededLookups = lookups.filter(item => isCandidateSourceCapability(item, primary) && !isNoiseCapability(item));
  const rawFindings: ReviewFinding[] = [];

  if (!primary.length) {
    rawFindings.push({
      code: "no-primary-capability",
      severity: "block",
      stage: "analyze",
      next: "re-record",
      message: "没有识别到本页主能力（查询/新建/修改/审核/删除）。用户分页、产品下拉、库存带出不是主能力。请回到页面把业务操作录完整。"
    });
  }

  const actualOperations = new Set(primary.map(item => item.operation));
  for (const operation of [...new Set(expectedOperations)].filter(item => item !== "unknown")) {
    if (actualOperations.has(operation)) continue;
    const label = OPERATION_LABEL[operation] || operation;
    rawFindings.push({
      code: "missing-expected-operation",
      severity: "block",
      stage: "record",
      next: "re-record",
      message: `本次录制要求包含「${label}」，但没有找到对应且可验证的主能力。请回到该页面真实完成一次${label}并取得成功响应后再导出。`
    });
  }

  if (completeFieldCoverage) {
    for (const capability of primary) {
      for (const field of blankCompleteCoverageFields(capability, events)) {
        const label = String(field.label || field.name || "字段");
        rawFindings.push({
          code: "complete-field-coverage",
          severity: "block",
          stage: "record",
          next: "re-record",
          capabilityId: capability.id,
          capabilityTitle: capability.title,
          fieldPath: field.name,
          message: `本次要求覆盖全部可操作字段，但提交「${capability.title}」时字段「${label}」仍为空。自动返回该页面补齐全部字段后重新提交；附件和上传控件除外。`
        });
      }
      for (const collection of emptyBusinessCollections(capability, events)) {
        rawFindings.push({
          code: "complete-field-coverage",
          severity: "block",
          stage: "record",
          next: "re-record",
          capabilityId: capability.id,
          capabilityTitle: capability.title,
          fieldPath: collection.path,
          message: `本次要求覆盖全部可操作字段，但写请求中的业务明细集合 ${collection.path} 仍为空。自动展开页面里的添加/新增明细区域，至少真实填写并提交一行；附件和上传集合除外。`
        });
      }
    }
  }

  for (const capability of [...primary, ...neededLookups]) {
    for (const binding of capability.bindings.filter(item => item.fromCapabilityId === capability.id)) {
      rawFindings.push({
        code: "binding-structure-valid",
        severity: "block",
        stage: "analyze",
        next: "re-analyze",
        capabilityId: capability.id,
        capabilityTitle: capability.title,
        fieldPath: binding.toPath,
        message: `字段 ${binding.toPath} 错误地从当前能力自身 ${binding.fromPath} 取值，执行时会形成循环依赖。请依据原始录制证据重新分析。`
      });
    }
    if (capability.validation.status === "verified") continue;
    const failed = capability.validation.checks.filter(check => !check.ok);
    if (failed.length) {
      rawFindings.push(...failed.map(check => findingFromCheck(capability, check)));
      continue;
    }
    rawFindings.push({
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
      if (rawFindings.some(item => item.capabilityId === capability.id && item.code === "write-field-origins-resolved" && item.fieldPath === field.path)) {
        continue;
      }
      rawFindings.push({
        code: "write-field-origins-resolved",
        severity: "block",
        stage: "analyze",
        next: "re-analyze",
        capabilityId: capability.id,
        capabilityTitle: capability.title,
        fieldPath: field.path,
        message: `字段「${field.label}」(${field.name}) 没有可执行来源，且录制成功请求中没有可安全透传的原值。重新分析真实证据；不要制造绑定或把问题交给用户猜。`
      });
    }
    for (const field of unsoundFormulaFields(capability)) {
      const expr = parseComputedRule(field.defaultRule || "") || "";
      rawFindings.push({
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
      rawFindings.push({
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
      rawFindings.push({
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

  const findings = applyReviewActionPolicy(rawFindings, capabilities);
  const next = worstNext(findings);
  const verifiedPrimary = primary.filter(item => item.validation.status === "verified");
  const status = findings.length === 0 && verifiedPrimary.length === primary.length && primary.length > 0 ? "passed" : "blocked";
  const primaryTitles = primary.map(item => item.title);
  const lookupTitles = neededLookups.map(item => item.title);
  const blockedLead = next === "re-record"
    ? `审核未通过，不能导出。下一步：${NEXT_LABEL[next]}。仅当缺少要求的主操作或其成功响应、或全字段覆盖仍有可填写空字段/空明细时才开新录制。`
    : `审核未通过，不能导出。下一步：${NEXT_LABEL[next]}。禁止为字段归属或候选查询再开一轮录制；不要进入补录循环，结果相同则停止。`;
  const lines = [
    status === "passed"
      ? `审核通过，可以导出。通过标准：本页主能力均已验证，写字段均有唯一来源规则，公式不用编号/枚举/时间戳做运算，选人暴露已录制查询，请求键均有着落，用到的候选查询可用。`
      : blockedLead,
    `主能力 ${primary.length} 项${primaryTitles.length ? `（${primaryTitles.join("、")}）` : ""}；字段候选 ${neededLookups.length} 个${lookupTitles.length ? `（${lookupTitles.join("、")}）` : ""}。下拉、用户分页、IM、登录不是主能力。`
  ];
  if (findings.length) {
    lines.push(next === "re-record"
      ? "处理顺序：先补齐缺失的主操作成功证据，再分析一次、验证一次。"
      : "处理顺序：不要补录。不要对同一审核结果再分析或再录；停止并报告本页未通过原因。");
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
  sessionEvents: EvidenceEvent[],
  expectedOperations: OperationKind[] = [],
  completeFieldCoverage = false
) {
  const scoped = sessionCatalogSlice(catalog, allEvents, sessionEvents);
  return {
    capabilities: scoped,
    review: reviewCatalog(scoped, allEvents, expectedOperations, completeFieldCoverage)
  };
}

export function assertExportable(capabilities: CapabilityContract[], events: EvidenceEvent[] = []) {
  const review = reviewCatalog(capabilities, events);
  if (review.status !== "passed") {
    throw new Error(review.summary);
  }
  return review;
}
