import { api } from "./client";

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

export interface BizTemplate { templateId: string; name: string; type: string; defKey: string; enableFlag: string }
export async function listTemplates(tenant: string, base_url: string, token: string): Promise<BizTemplate[]> {
  const { data } = await api.post("/onboarding/list-templates", { tenant, base_url, token });
  return data.templates;
}

export interface FormField { key: string; label: string; type: string }
export async function templateForm(tenant: string, base_url: string, token: string, template_id: string): Promise<FormField[]> {
  const { data } = await api.post("/onboarding/template-form", { tenant, base_url, token, template_id });
  return data.fields;
}

export interface StartReq {
  tenant: string;
  subsystem: string;
  openapi: unknown;
  deploy: { base_url: string; auth: { kind: string } };
  credentials: { token: string };
  include_tags: string[];
  flows: { flow: string; test_input: Record<string, unknown> }[];
}

export async function startOnboard(req: StartReq): Promise<{ job_id: string }> {
  const { data } = await api.post("/onboarding/start", req);
  return data;
}

export async function getJob(jobId: string): Promise<OnboardJob> {
  const { data } = await api.get(`/onboarding/jobs/${jobId}`);
  return data;
}
