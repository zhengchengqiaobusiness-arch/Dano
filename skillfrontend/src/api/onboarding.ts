import { api } from "./client";

export interface Category { tag: string; count: number }
export interface ActionInfo { name: string; method: string; endpoint: string; tags: string[]; summary: string; required: string[] }
export interface PreviewResp { template: string | null; business_action_count: number; categories: Category[]; actions: ActionInfo[] }
export interface OnboardEvent { type: string; flow?: string; reasons?: string[]; asset_id?: string | null; flows?: string[]; index?: number; total?: number; ok?: boolean; rejections?: number }
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
