import { api } from "./client";

// 与后端 catalog/manifest.SkillManifest 对齐
export interface SkillManifest {
  name: string;            // skill_id,如 A-OA.submit_leave
  subsystem: string;
  action: string;
  title: string;
  business?: string;       // 所属业务(同业务多操作 → 目录里归为一组)
  description: string;
  integration: string;     // adapter / workflow / api / page
  risk_level: string;      // L1..L5
  requires_confirmation: boolean;
  parameters: JSONSchema;  // 输入 JSON Schema
  output_schema?: Record<string, unknown>;
  page?: PageSkillView | null;   // 页面型 Skill 专属(详情可视化)
}

export interface PageStepView {
  op: string; locator?: string | null; value_from?: string | null;
  assert_visible?: boolean; optional?: boolean;
}
export interface PageSkillView {
  start_url?: string; success_marker?: string | null; steps?: PageStepView[];
}

export interface JSONSchema {
  type?: string;
  properties?: Record<string, JSONSchemaProperty>;
  required?: string[];
  additionalProperties?: boolean;
}

export interface JSONSchemaProperty {
  type?: string;
  format?: string;
  description?: string;
  label?: string;
  enum?: unknown[];
  "x-options-source"?: boolean;
  "x-options"?: unknown[];
  "x-options-truncated"?: boolean;
  "x-submit-mode"?: "value" | string;
  "x-option-label"?: string;
  "x-option-value"?: string;
}

export interface ToolOption {
  label: string;
  value: string;
}

export interface ToolOptionsResponse {
  field: string;
  count: number;
  options: ToolOption[];
  submit_mode?: string;
  note?: string;
}

// 与后端 TaskOutcome 对齐(部分字段)
export interface TaskOutcome {
  task_id: string;
  state: string;           // completed / cancelled / needs_input / rejected / failed ...
  message: string;
  skill_id?: string;
  exec_result?: { structured_output?: Record<string, unknown>; [k: string]: unknown } | null;
  audit?: Record<string, unknown>;
}

export interface FunctionTool {
  type: "function";
  function: { name: string; description: string; parameters: JSONSchema };
}

export async function createTenant(tenant: string): Promise<{ tenant: string; api_key: string }> {
  const { data } = await api.post("/tenants", { tenant });
  return data;
}

export async function listSkills(): Promise<SkillManifest[]> {
  const { data } = await api.get("/v1/skills");
  return data;
}

export async function getSkill(skillId: string): Promise<SkillManifest> {
  const { data } = await api.get(`/v1/skills/${encodeURIComponent(skillId)}`);
  return data;
}

export async function invokeSkill(
  skillId: string,
  input: Record<string, unknown>,
  confirm: boolean,
): Promise<TaskOutcome> {
  const { data } = await api.post(`/v1/skills/${encodeURIComponent(skillId)}/invoke`, {
    input,
    confirm,
  });
  return data;
}

export async function listSkillOptions(skillId: string, field: string): Promise<ToolOptionsResponse> {
  const toolName = skillId.split(".").join("__");
  const { data } = await api.post("/v1/tools/options", { name: toolName, field });
  return data;
}

export async function listTools(): Promise<FunctionTool[]> {
  const { data } = await api.get("/v1/tools");
  return data;
}

export async function deleteSkill(skillId: string): Promise<{ deleted: number }> {
  const { data } = await api.delete(`/v1/skills/${encodeURIComponent(skillId)}`);
  return data;
}

// 导出本租户已上架 Skill 为 pi 文件式 skill(.agents/skills/),后端就地写入 out_dir
export async function exportAgentSkills(out_dir: string): Promise<{ out_dir: string; count: number; written: string[] }> {
  const { data } = await api.post("/export/agent-skills", { out_dir });
  return data;
}

// ── 运行期 token(页面型 skill 抓请求路径鉴权):录制自动抓 → 存 PG;过期前端换一份即可,免重录 ──
export interface RuntimeToken {
  tenant: string;
  subsystem: string;
  has_token: boolean;
  headers: Record<string, string>;   // 默认打码;reveal=true 才明文
  source?: string;                   // recording(录制自动抓)/ manual(手动刷新)
  updated_at?: string;
}

export async function getRuntimeToken(tenant: string, subsystem: string, reveal = false): Promise<RuntimeToken> {
  const { data } = await api.get("/settings/token", { params: { tenant, subsystem, reveal } });
  return data;
}

export interface PutRuntimeTokenReq {
  tenant: string;
  subsystem: string;
  token?: string;                    // 只换一个头(默认 Authorization),与已存合并
  header_name?: string;
  token_prefix?: string;
  headers?: Record<string, string>;  // 或整组覆盖
}

export async function putRuntimeToken(req: PutRuntimeTokenReq): Promise<{ ok: boolean; headers: Record<string, string>; updated_at: string }> {
  const { data } = await api.put("/settings/token", req);
  return data;
}
