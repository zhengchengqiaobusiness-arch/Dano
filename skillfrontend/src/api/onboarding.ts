import { api } from "./client";

export interface Category { tag: string; count: number }
export interface ActionInfo { name: string; method: string; endpoint: string; tags: string[]; summary: string; required: string[] }
export interface PreviewResp { template: string | null; business_action_count: number; categories: Category[]; actions: ActionInfo[] }
export interface OnboardEvent {
  type: string; ts?: number; flow?: string; reasons?: string[]; asset_id?: string | null;
  flows?: string[]; index?: number; total?: number; ok?: boolean; rejections?: number;
  iter?: number; strategy?: string; fixing?: boolean; lines?: number;
  gate?: string; passed?: boolean; detail?: string; role?: string; model?: string;
  route?: string; attempt?: number;
  // pi 单一路径事件:阶段标记 + 每个工具调用(parse_spec/draft_connector/draft_workflow/sandbox/publish…)
  phase?: string; note?: string; tool?: string; action?: string; dur_s?: number;
  summary?: Record<string, unknown>; error?: string;
  // 页面直驱富进度(page_session_open/observe/act 推送):看 Agent 操作页面 + 截图 + 登录状态
  event?: string; url?: string; at_login?: boolean; errors?: string[];
  op?: string; field?: string; fields?: number; dry?: boolean; screenshot?: string;
  // 三维审核结果(request_review 推送):成果验收/漏洞检测/合规审核
  all_passed?: boolean; reviews?: { role: string; passed: boolean }[];
}
export interface OnboardJob { job_id: string; status: string; events: OnboardEvent[]; report: { published_skills?: string[]; status?: string } | null; error: string | null }

// 手动导入方式一:直接写 swagger 地址,后端代取(浏览器跨域/自签证书拉不了)。
export async function fetchSwaggerByUrl(url: string, token: string) {
  const { data } = await api.post("/onboarding/fetch-swagger", { url, token });
  return data;
}

export async function preview(openapi: unknown): Promise<PreviewResp> {
  const { data } = await api.post("/onboarding/preview", { openapi });
  return data;
}

// 业务模板:查目标 OA 真实的流程模板清单(请假/报销/出差…),当"类别"给用户选。
export interface BizTemplate { templateId: string; name: string; type: string; defKey: string; enableFlag: string }
export async function listTemplates(base_url: string, token: string): Promise<BizTemplate[]> {
  const { data } = await api.post("/onboarding/list-templates", { base_url, token });
  return data.templates;
}

// 某模板的动态表单字段(供预填 values 骨架,报销/出差不用猜字段)
export interface FormField { key: string; label: string; type: string }
export async function templateForm(base_url: string, token: string, template_id: string): Promise<FormField[]> {
  const { data } = await api.post("/onboarding/template-form", { base_url, token, template_id });
  return data.fields;
}

// 平台自动「找出合适的流程」:复合流程 + 连接器(读/写)提案,供前端确认后生成。
export interface ProposedFlow {
  flow: string; title: string; kind: "composite" | "connector"; write: boolean;
  actions: string[]; method: string; endpoint: string; required: string[];
  suggested_test_input: Record<string, unknown>; reason: string; tags?: string[]; selected: boolean;
}
export async function discoverFlows(openapi: unknown, include_tags: string[], subsystem: string): Promise<ProposedFlow[]> {
  const { data } = await api.post("/onboarding/discover-flows", { openapi, include_tags, subsystem });
  return data.flows;
}

export interface StartReq {
  tenant: string;
  subsystem: string;
  openapi: unknown;
  deploy: { base_url: string; auth: { kind: string } };
  credentials: { token: string };
  include_tags: string[];
  flows: { flow: string; test_input: Record<string, unknown> }[];
  max_read_flows: number | null;
}

export async function startOnboard(req: StartReq): Promise<{ job_id: string }> {
  const { data } = await api.post("/onboarding/start", req);
  return data;
}

export async function getJob(jobId: string): Promise<OnboardJob> {
  const { data } = await api.get(`/onboarding/jobs/${jobId}`);
  return data;
}

// ── 页面型系统接入(流程8,无 API):侦察 → 改字段映射 → 生成发布 ──
export interface PageField {
  tag: string; type: string; name: string; id: string; placeholder: string; label: string; required: boolean;
}
export interface PageStep {
  op: string; locator?: string | null; field?: string | null; value?: string | null;
  required?: boolean; optional_step?: boolean; doc?: string | null;
}
export interface PageScoutResp {
  start_url: string; dom_fingerprint: string; fields: PageField[];
  buttons: { text: string }[]; submit_locator: string | null; suggested_steps: PageStep[];
}
export interface ReviewVerdict { role: string; model: string; passed: boolean; reasons: string[] }
export interface PageOnboardReport {
  ok: boolean; stage?: string; action?: string; risk_level?: string; mode?: string;
  asset_id?: string | null; reason?: string; verdicts?: ReviewVerdict[]; detail?: unknown;
}

export interface PageScoutReq {
  tenant: string; subsystem: string; start_url: string;
  deploy?: Record<string, unknown>; credentials?: Record<string, string>; headless?: boolean;
}
export async function scoutPage(req: PageScoutReq): Promise<PageScoutResp> {
  const { data } = await api.post("/onboarding/page/scout", req);
  return data;
}

export interface PageOnboardReq extends PageScoutReq {
  action: string; title?: string; success_marker?: string | null;
  sample_inputs?: Record<string, unknown>; steps?: PageStep[]; dom_fingerprint?: string;
}
export async function onboardPage(req: PageOnboardReq): Promise<PageOnboardReport> {
  const { data } = await api.post("/onboarding/page", req);
  return data;
}

// pi 自主驱动:给个地址,pi 自己侦察→建体→回放→评审→发布
export interface PagePiReq {
  tenant: string; subsystem: string; start_url: string; action_hint?: string;
  deploy?: Record<string, unknown>; credentials?: Record<string, string>; timeout_s?: number;
}
export interface PagePiReport {
  pi_status?: string; published_skills?: string[]; tool_events?: number;
  final_text?: string; error?: string;
}
export async function onboardPagePi(req: PagePiReq): Promise<PagePiReport> {
  const { data } = await api.post("/onboarding/page/pi", req);
  return data;
}

// 页面直驱(Agent 自主):给 URL + 一句话业务目标 + 测试登录态,Agent 自己操作真实页面跑通 → 结晶为 Skill。
// 异步:返回 job_id,前端轮询 getJob 看 Agent 逐步操作(observe/act)+ 最终结果。
export interface PageAgentReq {
  tenant: string; subsystem: string; start_url: string; goal: string; action?: string;
  deploy?: Record<string, unknown>; credentials?: Record<string, string>; timeout_s?: number;
}
export async function startPageAgent(req: PageAgentReq): Promise<{ job_id: string }> {
  const { data } = await api.post("/onboarding/page-agent", req);
  return data;
}

// 方式A:导入 Playwright codegen 录制脚本 → 解析步骤 → 建体 → 回放 → 发布
export interface PageImportReq {
  tenant: string; subsystem: string; codegen: string; action: string;
  title?: string; success_marker?: string | null; start_url?: string;
  deploy?: Record<string, unknown>; credentials?: Record<string, string>;
  sample_inputs?: Record<string, unknown>;
}
export interface PageImportReport extends PageOnboardReport {
  parsed_steps?: number; sample_inputs?: Record<string, unknown>;
}
export async function onboardPageImport(req: PageImportReq): Promise<PageImportReport> {
  const { data } = await api.post("/onboarding/page/import", req);
  return data;
}
