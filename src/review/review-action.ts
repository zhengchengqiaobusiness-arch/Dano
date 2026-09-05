import type { CapabilityContract, ReviewFinding, ReviewNext } from "../domain.js";
import { evidencePageKey, isPrimaryCapability } from "../inference/export-scope.js";

const MAJOR_RECORD_CODES = new Set([
  "no-primary-capability",
  "missing-expected-operation",
  "complete-field-coverage"
]);

const PRIMARY_EVIDENCE_CODES = new Set([
  "recorded-network-evidence",
  "successful-response",
  "write-ui-correlation"
]);

export function isMajorEvidenceGap(
  finding: ReviewFinding,
  primaries: CapabilityContract[] = []
) {
  if (MAJOR_RECORD_CODES.has(finding.code)) return true;
  if (!PRIMARY_EVIDENCE_CODES.has(finding.code)) return false;
  if (!finding.capabilityId) return primaries.length === 0;
  return primaries.some(item => item.id === finding.capabilityId);
}

export function applyReviewActionPolicy(
  findings: ReviewFinding[],
  catalog: CapabilityContract[] = []
): ReviewFinding[] {
  const primaries = catalog.filter(item => isPrimaryCapability(item, catalog));
  return findings.map(finding => {
    if (finding.next !== "re-record") return finding;
    if (isMajorEvidenceGap(finding, primaries)) return finding;
    return {
      ...finding,
      stage: "analyze",
      next: "re-analyze" as ReviewNext,
      message: `${finding.message} 本次已有主操作成功证据，这是分析/合同问题，不要重新录制。`
    };
  });
}

export function reviewFindingSignature(findings: Array<{ code: string; capabilityId?: string; fieldPath?: string }>) {
  return findings
    .map(item => `${item.code}:${item.capabilityId || ""}:${item.fieldPath || ""}`)
    .sort()
    .join("|");
}

export function sameReviewPage(left?: string, right?: string) {
  return Boolean(left && right && evidencePageKey(left) === evidencePageKey(right));
}

export function rerecordBlockedMessage(next: ReviewNext) {
  if (next === "manual") {
    return "上次审核需要改平台或目录，不能靠再录一遍解决。不要 business_skill_record_start。";
  }
  return "上次审核未要求补录。禁止对同一业务页 business_skill_record_start；请对已有会话 analyze 后 validate。同一审核结果不要循环。";
}
