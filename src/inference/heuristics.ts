import type { NetworkEvidence, OperationKind, UiEvidence } from "../domain.js";

const REVIEW = /approve|approval|audit|review|reject|pass|审核|审批|通过|驳回|拒绝|复核/i;
const DELETE = /delete|remove|destroy|删除|移除|作废|注销/i;
const CREATE = /create|add|insert|new|新增|新建|创建|添加/i;
const UPDATE = /update|edit|modify|save|修改|编辑|更新|保存/i;
const QUERY = /query|search|find|list|page|detail|lookup|查询|搜索|列表|详情|检索/i;
const AUTHENTICATE = /(?:^|[\/_-])(login|logout|sign-in|signout|refresh-token|captcha)(?:[\/?_-]|$)|登录|登出|退出登录|验证码/i;
const UPLOAD = /upload|import|上传|导入/i;
const DOWNLOAD = /download|export|下载|导出/i;
const PAGING_KEY = /^(pageNo|pageNum|page|pageSize|pageIndex|current|size|limit|offset)$/i;
const SEARCH_KEY = /^(gjz|gjc|keyword|keyWord|keywords|search|searchKey|searchText|queryKey|q|query)$/i;
export const ASK_KEY = /^(sys_query|userQuery|question|prompt|queryText|askText)$/i;
const READ_LAST = /^(get|select|query|search|find|list|page|load|fetch)/i;
const READ_SUFFIX = /(?:List|Page|Search|Query|Find)$/;

function requestParams(event: NetworkEvidence) {
  const query = event.request.query && typeof event.request.query === "object" ? event.request.query : {};
  const body = event.request.body;
  const fromBody = body && typeof body === "object" && !Array.isArray(body) ? body as Record<string, unknown> : {};
  return { ...query, ...fromBody };
}

function looksCollectionBody(body: unknown): boolean {
  if (Array.isArray(body)) return body.every(item => item == null || typeof item === "object") && body.some(item => item && typeof item === "object");
  if (!body || typeof body !== "object") return false;
  const record = body as Record<string, unknown>;
  const data = "data" in record ? record.data : "result" in record ? record.result : record;
  if (Array.isArray(data)) {
    return data.length === 0 || data.some(item => item && typeof item === "object");
  }
  if (data && typeof data === "object") {
    return ["list", "rows", "records", "items", "content"].some(key => Array.isArray((data as Record<string, unknown>)[key]));
  }
  return false;
}

function hasAskValue(event: NetworkEvidence) {
  return Object.entries(requestParams(event)).some(([key, value]) =>
    ASK_KEY.test(key) && typeof value === "string" && value.trim().length > 0
  );
}

export function looksRecordedQuery(event: NetworkEvidence) {
  const last = new URL(event.request.url).pathname.split("/").filter(Boolean).pop() || "";
  const keys = Object.keys(requestParams(event));
  const paging = keys.some(key => PAGING_KEY.test(key));
  const search = keys.some(key => SEARCH_KEY.test(key));
  const readPath = READ_LAST.test(last) || READ_SUFFIX.test(last) || /(?:list|page|search|query|find)$/i.test(last);
  const collection = looksCollectionBody(event.response?.body);
  if (hasAskValue(event)) return true;
  if (collection && (paging || search || readPath)) return true;
  return paging && search;
}

function looksScalarLookup(event: NetworkEvidence) {
  const last = new URL(event.request.url).pathname.split("/").filter(Boolean).pop() || "";
  if (!READ_LAST.test(last) && !READ_SUFFIX.test(last) && !/(?:^|[\/_-])(get|fetch|load|query|find)(?:[\/_-]|$)/i.test(last)) {
    return false;
  }
  const body = event.response?.body;
  if (!body || typeof body !== "object" || Array.isArray(body) || looksCollectionBody(body)) return false;
  const data = (body as { data?: unknown; result?: unknown }).data ?? (body as { result?: unknown }).result;
  if (data === undefined || data === null || Array.isArray(data)) return false;
  return typeof data === "object";
}

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
  const endpoint = new URL(event.request.url).pathname;
  const actionSignal = [
    event.request.url,
    ui?.text,
    ui?.label,
    ui?.name
  ].filter(Boolean).join(" ");
  const pageSignal = [event.pageUrl, ui?.pageUrl].filter(Boolean).join(" ");

  if (method === "DELETE") return "delete";
  if (AUTHENTICATE.test(endpoint)) return "authenticate";
  if (UPLOAD.test(endpoint)) return "upload";
  if (DOWNLOAD.test(endpoint)) return "download";
  if (method === "GET" || method === "HEAD") return "query";
  if (CREATE.test(endpoint) || /submit-process|start-process|startProcess/i.test(endpoint)) return "create";
  if (CREATE.test(actionSignal)) return "create";
  if (REVIEW.test(actionSignal) || (REVIEW.test(pageSignal) && REVIEW.test(actionSignal))) return "review";
  if (DELETE.test(actionSignal)) return "delete";
  if (method === "POST" && (QUERY.test(actionSignal) || looksRecordedQuery(event) || looksScalarLookup(event))) return "query";
  if (method === "PATCH" || method === "PUT") return "update";
  if (UPDATE.test(endpoint)) return "update";
  if (UPDATE.test(actionSignal)) return "update";
  if (method === "POST") return "action";
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
