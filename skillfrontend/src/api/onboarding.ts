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
