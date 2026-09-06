/**
 * 文件级说明：操作类型的主流判断已交给 `.pi/skills/judge-primary-capability`。
 * 本文件只保留 HTTP 成功/登录失败、URL 模板，以及精确按钮文案 / 分页问数字段等取证原语。
 * 旧正则引擎全文见 `heuristics.ts.bak`。
 */
import type { EvidenceEvent, NetworkEvidence, OperationKind, UiEvidence } from "../domain.js";

const REVIEW = /approve|approval|audit|review|reject|pass|审核|审批|通过|驳回|拒绝|复核/i;
const DELETE = /delete|remove|destroy|删除|移除|作废|注销/i;
const CREATE = /create|add|insert|new|新增|新建|创建|添加/i;
const UPDATE = /update|edit|modify|save|修改|编辑|更新|保存/i;
const QUERY = /query|search|find|list|page|detail|lookup|查询|搜索|列表|详情|检索/i;
const AUTHENTICATE = /(?:^|[\/_-])(login|logout|sign-in|signout|refresh-token|captcha)(?:[\/?_-]|$)|登录|登出|退出登录|验证码/i;
const UPLOAD = /upload|import|上传|导入/i;
const DOWNLOAD = /download|export|下载|导出/i;
const PAGING_KEY = /^(pageNo|pageNum|page|pageSize|pageIndex|current|size|limit|offset)$/i;
export const SEARCH_KEY = /^(gjz|gjc|keyword|keyWord|keywords|search|searchKey|searchText|queryKey|q|query)$/i;
export const ASK_KEY = /^(sys_query|userQuery|question|prompt|queryText|askText)$/i;
const READ_LAST = /^(get|select|query|search|find|list|page|load|fetch)/i;
const READ_SUFFIX = /(?:List|Page|Search|Query|Find)$/;

const UI_QUERY = /^(搜索|查询|检索|查看|详情|预览|search|query|find|view|detail)$/i;
const UI_CREATE = /^(新增|新建|创建|创建申请|添加|add|new|create)(?:\S*)$/i;
const UI_UPDATE = /^(修改|编辑|更新|设计表单|绑定流程|modify|edit|update)(?:\S*)$/i;
const UI_DELETE = /^(删除|移除|作废|注销|delete|remove|destroy)(?:\S*)$/i;
const UI_REVIEW = /^(审核|审批|提交审批|通过|驳回|拒绝|复核|approve|approval|audit|review|reject|pass)(?:\S*)$/i;
const UI_DOWNLOAD = /^(导出|下载|export|download)(?:\S*)$/i;
const UI_ACTION = /^(撤销|撤回|签章|反馈|跟踪|重新计算|recalculate|withdraw|revoke|sign)(?:\S*)$/i;
const UI_COMMIT = /^(保存|提交|确定|申请|save|submit|confirm|apply)$/i;
const FORM_COMMIT = /^(?:(?:确认)?(?:保存|提交)(?:草稿|申请|审批)?|保存并提交|提交申请|确定|确认|申请|save|submit|confirm|apply)$/i;
const BUSINESS_FAILURE = /失败|错误|异常|不能为空|不正确|无权限|未登录|登录失效|无效|拒绝|invalid|error|fail(?:ed|ure)?|denied|required|unauthori[sz]ed|forbidden/i;
const FAILURE_CODE = /^(?:fail(?:ed|ure)?|error|invalid|denied|unauthori[sz]ed|forbidden)$/i;
const LOGIN_FAILURE = /未登录|请(?:先|重新)?登录|登录(?:失效|过期|超时)|会话(?:失效|过期|超时)|(?:access|auth|refresh)[-_ ]?token(?:\s+is)?\s*(?:invalid|expired)|unauthenticated|login\s*(?:required|expired)/i;

export function inferUiOperationIntent(text?: string, _pageUrl = ""): OperationKind | undefined {
  const label = String(text || "").replace(/\s+/g, "").replace(/^[^A-Za-z0-9\u4e00-\u9fff]+/, "");
  if (!label || /^(重置|取消|关闭|返回|reset|cancel|close|back)$/i.test(label)) return undefined;
  if (/^(搜索|查询|检索|search|query)$/i.test(label)) return "query";
  if (/^(新增|新建|创建|add|create)$/i.test(label)) return "create";
  if (/^(修改|编辑|更新|edit|update)$/i.test(label)) return "update";
  if (/^(删除|delete|remove)$/i.test(label)) return "delete";
  if (/^(审核|审批|通过|驳回|approve|reject)$/i.test(label)) return "review";
  if (/^(导出|下载|export|download)$/i.test(label)) return "download";
  if (/^(保存|提交|确定|save|submit|confirm)$/i.test(label)) return undefined;
  return undefined;
}

function nonemptyError(value: unknown) {
  if (value === undefined || value === null || value === false || value === "") return false;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === "object") return Object.keys(value as Record<string, unknown>).length > 0;
  return true;
}

export function businessResponseFailureReason(response?: { status: number; body?: unknown }): string | undefined {
  if (!response) return "没有响应";
  if (response.status < 200 || response.status >= 400) return `HTTP ${response.status}`;
  const body = response.body;
  if (body === false) return "响应值为 false";
  if (typeof body === "string") return BUSINESS_FAILURE.test(body) ? body.slice(0, 500) : undefined;
  if (!body || typeof body !== "object" || Array.isArray(body)) return undefined;
  const record = body as Record<string, unknown>;
  const message = [record.msg, record.message, record.detail]
    .filter(value => typeof value === "string")
    .join(" ");
  if (record.success === false) return "success=false";
  if (record.ok === false) return "ok=false";
  for (const key of ["code", "statusCode", "errorCode"]) {
    const value = record[key];
    const numeric = typeof value === "number" ? value : typeof value === "string" && /^\d+$/.test(value.trim()) ? Number(value) : undefined;
    if (numeric !== undefined && numeric >= 400) return `${key}=${String(value)}${message ? `：${message}` : ""}`;
    if (typeof value === "string" && FAILURE_CODE.test(value.trim())) return `${key}=${value}`;
  }
  for (const key of ["error", "errors", "errorMessage", "error_description"]) {
    if (nonemptyError(record[key])) return `${key}=${typeof record[key] === "string" ? record[key] : "非空"}`;
  }
  return BUSINESS_FAILURE.test(message) ? message : undefined;
}

export function authenticationFailureReason(response?: { status: number; body?: unknown }): string | undefined {
  if (!response) return undefined;
  if (response.status === 401) return "HTTP 401：登录状态无效";
  const body = response.body;
  if (typeof body === "string") return LOGIN_FAILURE.test(body) ? body.slice(0, 500) : undefined;
  if (!body || typeof body !== "object" || Array.isArray(body)) return undefined;
  const record = body as Record<string, unknown>;
  const message = [record.msg, record.message, record.detail, record.error_description]
    .filter(value => typeof value === "string")
    .join(" ");
  for (const key of ["code", "statusCode", "errorCode"]) {
    const value = record[key];
    const numeric = typeof value === "number" ? value : typeof value === "string" && /^\d+$/.test(value.trim()) ? Number(value) : undefined;
    if (numeric === 401) return `${key}=${String(value)}${message ? `：${message}` : ""}`;
  }
  return LOGIN_FAILURE.test(message) ? message : undefined;
}

export function businessFailureReason(event: NetworkEvidence): string | undefined {
  return businessResponseFailureReason(event.response);
}

export function isSuccessfulNetworkEvidence(event: NetworkEvidence) {
  return businessFailureReason(event) === undefined;
}

export function isTriggeredOperationEvidence(
  event: NetworkEvidence,
  operation: OperationKind,
  evidenceById: Map<string, EvidenceEvent>
) {
  if (!event.correlatedUiEvidenceId) return false;
  const ui = evidenceById.get(event.correlatedUiEvidenceId);
  if (ui?.kind !== "ui") return false;
  const explicitIntent = inferUiOperationIntent(ui.text || ui.label || "", ui.pageUrl);
  if (explicitIntent) return explicitIntent === operation;
  if (["create", "update", "review", "action"].includes(operation) && isFormCommit(ui)) return true;
  return inferOperation(event, ui) === operation;
}

export function hasSuccessfulOperationEvidence(
  events: NetworkEvidence[],
  operation: OperationKind,
  evidenceById: Map<string, EvidenceEvent>,
  requireTriggered = false
) {
  const triggered = events.filter(event => isTriggeredOperationEvidence(event, operation, evidenceById));
  if (requireTriggered || triggered.length) return triggered.some(isSuccessfulNetworkEvidence);
  return events.some(isSuccessfulNetworkEvidence);
}

function isFormCommit(ui?: UiEvidence) {
  const label = String(ui?.text || ui?.label || "").replace(/\s+/g, "").replace(/^[^A-Za-z0-9\u4e00-\u9fff]+/, "");
  return FORM_COMMIT.test(label);
}

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

export function inferOperation(event: NetworkEvidence, ui?: UiEvidence, activeFormIntent?: OperationKind): OperationKind {
  const method = event.request.method.toUpperCase();
  const endpoint = new URL(event.request.url).pathname;
  if (AUTHENTICATE.test(endpoint)) return "authenticate";
  if (method === "DELETE") return "delete";
  if (method === "GET" || method === "HEAD") return "query";
  if (method === "PATCH" || method === "PUT") return "update";
  const uiIntent = inferUiOperationIntent(ui?.text || ui?.label, ui?.pageUrl || event.pageUrl || "");
  if (uiIntent && uiIntent !== "action") return uiIntent;
  if ((activeFormIntent === "create" || activeFormIntent === "update") && isFormCommit(ui)) return activeFormIntent;
  if (uiIntent) return uiIntent;
  if (method === "POST" && looksRecordedQuery(event)) return "query";
  if (method === "POST" && isFormCommit(ui)) {
    const haystack = `${event.request.url} ${ui?.pageUrl || ""} ${ui?.text || ""} ${ui?.label || ""}`;
    if (CREATE.test(haystack)) return "create";
    if (UPDATE.test(haystack)) return "update";
    return "action";
  }
  if (method === "POST") return "unknown";
  return "unknown";
}

export function operationConfidence(event: NetworkEvidence, ui?: UiEvidence, activeFormIntent?: OperationKind) {
  const operation = inferOperation(event, ui, activeFormIntent);
  if (operation === "unknown") return 0.35;
  const method = event.request.method.toUpperCase();
  if (method === "GET" || method === "DELETE" || method === "PATCH" || method === "PUT") return 0.92;
  if (ui?.text || ui?.label) return 0.82;
  return 0.68;
}
