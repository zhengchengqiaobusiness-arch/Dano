import type { NetworkEvidence, OperationKind, UiEvidence } from "../domain.js";

const REVIEW = /approve|approval|audit|review|reject|pass|审核|审批|通过|驳回|拒绝|复核/i;
const DELETE = /delete|remove|destroy|删除|移除|作废|注销/i;
const CREATE = /create|add|insert|new|新增|新建|创建|添加/i;
const UPDATE = /update|edit|modify|save|修改|编辑|更新|保存/i;
const QUERY = /query|search|find|list|page|detail|lookup|查询|搜索|列表|详情|检索/i;

export function normalizeUrl(rawUrl: string) {
  const url = new URL(rawUrl);
  const segments = url.pathname.split("/").map(segment => {
    if (/^\d+$/.test(segment)) return "{id}";
    if (/^[0-9a-f]{8}-[0-9a-f-]{27,}$/i.test(segment)) return "{id}";
    if (/^[0-9a-f]{24,}$/i.test(segment)) return "{id}";
    return segment;
  });
  const queryKeys = [...new Set([...url.searchParams.keys()])].sort();
  const suffix = queryKeys.length ? `?${queryKeys.map(k => `${encodeURIComponent(k)}={${k}}`).join("&")}` : "";
  return {
    origin: url.origin,
    pathTemplate: segments.join("/") || "/",
    urlTemplate: `${url.origin}${segments.join("/") || "/"}${suffix}`
  };
}

export function inferOperation(event: NetworkEvidence, ui?: UiEvidence): OperationKind {
  const method = event.request.method.toUpperCase();
  const signal = [
    event.request.url,
    ui?.text,
    ui?.label,
    ui?.name
  ].filter(Boolean).join(" ");

  if (method === "DELETE") return "delete";
  if (REVIEW.test(signal)) return "review";
  if (DELETE.test(signal)) return "delete";
  if (method === "GET" || method === "HEAD") return "query";
  if (QUERY.test(signal) && method === "POST") return "query";
  if (method === "PATCH" || method === "PUT") return "update";
  if (UPDATE.test(signal)) return "update";
  if (CREATE.test(signal)) return "create";
  if (method === "POST") return "create";
  return "unknown";
}

export function operationConfidence(event: NetworkEvidence, ui?: UiEvidence) {
  const operation = inferOperation(event, ui);
  if (operation === "unknown") return 0.35;
  const method = event.request.method.toUpperCase();
  if (method === "GET" || method === "DELETE" || method === "PATCH" || method === "PUT") return 0.92;
  if (ui?.text || ui?.label) return 0.82;
  return 0.68;
}
