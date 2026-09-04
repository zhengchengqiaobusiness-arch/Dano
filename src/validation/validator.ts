import type { CapabilityContract, EvidenceEvent, NetworkEvidence, UiEvidence } from "../domain.js";
import { getByPath } from "../utils.js";
import { fieldHasUiEvidence, flattenRequestValues, requestValueAt, sameValue, staticCandidatesHaveUiEvidence } from "../inference/field-resolver.js";
import { evidenceSample, isExecutableRule } from "../inference/field-derivation.js";
import { pickerFieldsMissingQuery, uncoveredWriteLeaves, unresolvedWriteFields, unsoundFormulaFields } from "../review/catalog-review.js";
import { businessFailureReason, isSuccessfulNetworkEvidence } from "../inference/heuristics.js";

function schemaHasPath(schema: CapabilityContract["inputSchema"], jsonPath: string) {
  const parts = jsonPath.replace(/^\$\.?/, "").split(".").filter(Boolean);
  let current: any = schema;
  for (const rawPart of parts) {
    const wildcard = rawPart.endsWith("[*]");
    const part = wildcard ? rawPart.slice(0, -3) : rawPart;
    if (part) current = current?.properties?.[part];
    if (!current) return false;
    if (wildcard) current = current.items;
  }
  return parts.length > 0 && Boolean(current);
}

export function validateCapability(cap: CapabilityContract, events: EvidenceEvent[], catalog: CapabilityContract[] = []): CapabilityContract {
  const byId = new Map(events.map(e => [e.id, e]));
  const checks: CapabilityContract["validation"]["checks"] = [];

  const networkRefs = cap.evidence
    .map(ref => byId.get(ref.eventId))
    .filter((e): e is NetworkEvidence => e?.kind === "network");

  const uiRefs = cap.evidence
    .map(ref => byId.get(ref.eventId))
    .filter((e): e is UiEvidence => e?.kind === "ui");

  const hasNetwork = networkRefs.length > 0;
  checks.push({
    name: "recorded-network-evidence",
    ok: hasNetwork,
    detail: hasNetwork ? `${networkRefs.length} recorded network event(s)` : "No recorded network evidence"
  });

  const successful = networkRefs.some(isSuccessfulNetworkEvidence);
  const failureReasons = networkRefs.map(businessFailureReason).filter((item): item is string => Boolean(item));
  checks.push({
    name: "successful-response",
    ok: successful,
    detail: successful
      ? "至少一个录制响应同时满足 HTTP 与业务成功条件"
      : `没有成功的录制响应${failureReasons.length ? `：${[...new Set(failureReasons)].join("；")}` : ""}`
  });

  const transportMatches = networkRefs.every(e =>
    e.request.method.toUpperCase() === cap.transport.method.toUpperCase()
  );
  checks.push({
    name: "transport-consistency",
    ok: hasNetwork && transportMatches,
    detail: transportMatches ? "Recorded methods match contract" : "Recorded method mismatch"
  });

  const completionAssertionsBacked = (cap.completion.assertions || []).every(assertion =>
    networkRefs.some(event => {
      if (!isSuccessfulNetworkEvidence(event)) return false;
      const actual = getByPath(event.response!.body, assertion.path);
      if (assertion.kind === "exists") return actual !== undefined;
      if (assertion.kind === "nonempty") return actual !== undefined && actual !== null && actual !== "" && (!Array.isArray(actual) || actual.length > 0);
      return Object.is(actual, assertion.value);
    })
  );
  checks.push({
    name: "completion-assertions-backed-by-evidence",
    ok: completionAssertionsBacked,
    detail: completionAssertionsBacked
      ? `${cap.completion.assertions?.length || 0} completion assertion(s) backed by recorded success evidence`
      : "A completion assertion is not supported by recorded successful evidence"
  });

  const knownOperation = cap.operation !== "unknown";
  checks.push({
    name: "known-operation",
    ok: knownOperation,
    detail: knownOperation ? cap.operation : "Operation is still unknown"
  });

  if (cap.sideEffect) {
    const correlatedUi = uiRefs.length > 0 || networkRefs.some(e => Boolean(e.correlatedUiEvidenceId));
    checks.push({
      name: "write-ui-correlation",
      ok: correlatedUi,
      detail: correlatedUi
        ? "Write operation has correlated real UI evidence"
        : "Write operation lacks correlated UI evidence"
    });
  }

  if (cap.operation === "upload") {
    const multipart = networkRefs.some(event => /multipart\/form-data/i.test(event.request.headers["content-type"] || ""));
    checks.push({
      name: "upload-transport-executable",
      ok: !multipart,
      detail: multipart
        ? "已识别为 multipart 文件上传；当前 Python 执行器不能安全重放，需要修改平台代码后再验证"
        : "上传请求不依赖未实现的 multipart 重放"
    });
  }

  const fieldsComplete = cap.inputForm.every(field =>
    Boolean(field.name && field.label && field.path && field.valueType && field.source && field.requiredBasis && field.sourceDetail)
  );
  checks.push({
    name: "field-metadata-complete",
    ok: fieldsComplete,
    detail: fieldsComplete
      ? `${cap.inputForm.length} 个字段均包含名称、类型、来源、必填依据和处理方`
      : "字段元数据不完整，不能确认调用责任"
  });

  const ownershipConsistent = cap.inputForm.every(field =>
    field.source === "caller" ? !field.systemHandled : field.systemHandled
  );
  checks.push({
    name: "field-ownership-consistent",
    ok: ownershipConsistent,
    detail: ownershipConsistent
      ? "调用方字段与系统处理字段已明确区分"
      : "字段来源与处理方标记冲突"
  });

  const systemFieldsResolvable = cap.inputForm.every(field => {
    if (!field.required || field.source === "caller" || field.source === "computed") return true;
    if (field.source === "binding") return cap.bindings.some(binding => binding.approved && binding.toPath === field.path);
    return Boolean(field.defaultRule && isExecutableRule(field.defaultRule));
  });
  checks.push({
    name: "system-required-fields-resolvable",
    ok: systemFieldsResolvable,
    detail: systemFieldsResolvable
      ? "所有系统必填字段都有可执行处理规则或已确认绑定"
      : "存在没有可执行规则的系统必填字段"
  });

  if (["create", "update", "review", "delete", "upload", "action"].includes(cap.operation)) {
    const unresolved = unresolvedWriteFields(cap);
    checks.push({
      name: "write-field-origins-resolved",
      ok: unresolved.length === 0,
      detail: unresolved.length === 0
        ? "写操作非调用方字段均有唯一来源规则"
        : `无法唯一推断来源：${unresolved.map(field => field.name).join("、")}。已有写请求和带出查询时重新分析绑定，不要重开录制，不要冻录制样本`
    });
    const unsound = unsoundFormulaFields(cap);
    checks.push({
      name: "computed-formula-operands-sound",
      ok: unsound.length === 0,
      detail: unsound.length === 0
        ? "计算公式未使用编号、枚举或时间戳当运算数"
        : `公式不成立：${unsound.map(field => field.name).join("、")}`
    });
    const frozenPickers = pickerFieldsMissingQuery(cap, catalog, events);
    checks.push({
      name: "picker-uses-recorded-query",
      ok: frozenPickers.length === 0,
      detail: frozenPickers.length === 0
        ? "选人/弹窗字段已指向已录制查询"
        : `选人被冻成枚举：${frozenPickers.map(field => field.name).join("、")}`
    });
    const missingKeys = uncoveredWriteLeaves(cap, events);
    checks.push({
      name: "write-request-keys-covered",
      ok: missingKeys.length === 0,
      detail: missingKeys.length === 0
        ? "录制成功请求键均有字段或拼接规则"
        : `请求键无着落：${missingKeys.map(item => item.path).join("、")}`
    });
  }

  const callerFieldsBacked = cap.inputForm
    .filter(field => field.source === "caller")
    .every(field =>
      fieldHasUiEvidence(field, uiRefs)
      || field.candidates?.type === "capability"
      || field.candidates?.type === "static"
      || (() => {
        const value = requestValueAt(evidenceSample(cap, events), field.path);
        const lastTargetAt = networkRefs.map(event => event.at).sort().at(-1);
        return value !== undefined && events.some(event => {
          if (event.kind !== "network" || networkRefs.includes(event) || (lastTargetAt && event.at > lastTargetAt)) return false;
          const input = event.request.body && typeof event.request.body === "object" && !Array.isArray(event.request.body)
            ? { ...event.request.query, ...(event.request.body as Record<string, unknown>) }
            : event.request.query;
          return flattenRequestValues(input).some(item =>
            item.name.toLowerCase() === field.name.toLowerCase() && sameValue(item.value, value)
          );
        });
      })()
    );
  checks.push({
    name: "caller-fields-backed-by-ui",
    ok: callerFieldsBacked,
    detail: callerFieldsBacked
      ? "所有调用方字段均有真实页面输入、候选或先行查询输入证据"
      : "存在没有页面输入证据的调用方字段"
  });

  const bindingStructureValid = cap.bindings.every(binding => {
    const source = catalog.find(item => item.id === binding.fromCapabilityId);
    const targetKnown = cap.inputForm.some(field => field.path === binding.toPath) || schemaHasPath(cap.inputSchema, binding.toPath);
    const sourceKnown = Boolean(source && schemaHasPath(source.outputSchema, binding.fromPath));
    return Boolean(sourceKnown && targetKnown && (!binding.approved || (binding.approvalSource && binding.approvedAt)));
  });

  const candidateRulesValid = cap.inputForm.every(field => {
    const rule = field.candidates;
    if (!rule) return true;
    if (rule.type === "static") {
      return rule.values.length > 0 && staticCandidatesHaveUiEvidence(field, uiRefs);
    }
    const source = catalog.find(item => item.id === rule.capabilityId);
    return Boolean(source && source.operation === "query" && source.validation.status === "verified" &&
      schemaHasPath(source.outputSchema, rule.valuePath) && schemaHasPath(source.outputSchema, rule.labelPath) &&
      (rule.dependsOn || []).every(path => cap.inputForm.some(item => item.path === path || item.name === path)));
  });
  checks.push({
    name: "candidate-rules-backed-by-evidence",
    ok: candidateRulesValid,
    detail: candidateRulesValid
      ? "字段候选规则均由页面选项或已验证查询能力支持"
      : "候选规则缺少页面证据、有效查询来源或返回字段映射"
  });
  checks.push({
    name: "binding-structure-valid",
    ok: bindingStructureValid,
    detail: bindingStructureValid
      ? `${cap.bindings.length} 个绑定均指向已知能力和目标字段`
      : "绑定引用了未知能力、未知目标字段，或缺少确认记录"
  });

  const allOk = checks.every(c => c.ok);

  return {
    ...cap,
    validation: {
      version: 2,
      status: allOk ? "verified" : "candidate",
      checks,
      verifiedAt: allOk ? new Date().toISOString() : undefined
    }
  };
}
