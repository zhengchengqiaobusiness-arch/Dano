import { api } from "./client";

// 与后端 catalog/manifest.SkillManifest 对齐
export interface SkillManifest {
  name: string;            // skill_id,如 A-OA.submit_leave
  subsystem: string;
  action: string;
  title: string;
  business?: string;       // 所属业务(同业务多操作 → 目录里归为一组)
  description: string;
  integration: string;     // workflow / api / page
  risk_level: string;      // L1..L5
  requires_confirmation: boolean;
  verification_status?: string;
  verification_basis?: string;
  recording_mode?: string;
  created_at?: string;
  lifecycle_state?: string;
  frozen?: boolean;
  call_metadata?: SkillCallMetadata;
  parameters: JSONSchema;  // 输入 JSON Schema
  output_schema?: Record<string, unknown>;
}

export type JSONSchemaValue = string | number | boolean | null;
export interface JSONSchemaEnumOption {
  label?: string;
  value?: JSONSchemaValue;
  disabled?: boolean;
  [key: string]: unknown;
}

export interface SkillFieldCallMetadata {
  type?: string;
  format?: string;
  enum_options?: Array<JSONSchemaValue | JSONSchemaEnumOption>;
  enum_value_map?: Record<string, JSONSchemaValue>;
  options_source?: string;
  enum_source?: string;
  enum_confirmed?: boolean;
  [key: string]: unknown;
}

export interface SkillCallMetadata {
  recording_mode?: string;
  verification_status?: string;
  verification_basis?: string;
  fields?: Record<string, SkillFieldCallMetadata>;
  [key: string]: unknown;
}

export interface JSONSchema {
  type?: string;
  description?: string;
  format?: string;
  enum?: JSONSchemaValue[];
  "x-options"?: JSONSchemaValue[];
  "x-enum-options"?: Array<JSONSchemaValue | JSONSchemaEnumOption>;
  "x-enum-value-map"?: Record<string, JSONSchemaValue>;
  "x-options-source"?: boolean;
  properties?: Record<string, JSONSchema>;
  items?: JSONSchema;
  required?: string[];
  additionalProperties?: boolean | JSONSchema;
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

export async function listTools(): Promise<FunctionTool[]> {
  const { data } = await api.get("/v1/tools");
  return data;
}

export async function deleteSkill(skillId: string): Promise<{ deleted: number; removed_folders?: string[] }> {
  const { data } = await api.delete(`/v1/skills/${encodeURIComponent(skillId)}`);
  return data;
}

export async function freezeSkill(skillId: string): Promise<{ skill_id: string; state: string; removed_folders?: string[] }> {
  const { data } = await api.post(`/v1/skills/${encodeURIComponent(skillId)}/freeze`);
  return data;
}

export async function resumeSkill(skillId: string): Promise<{ skill_id: string; state: string }> {
  const { data } = await api.post(`/v1/skills/${encodeURIComponent(skillId)}/resume`);
  return data;
}

// 导出本租户已上架 Skill 为 pi 文件式 skill(.agents/skills/),后端就地写入 out_dir
export async function exportAgentSkills(out_dir: string): Promise<{ out_dir: string; count: number; written: string[]; removed_frozen_folders?: string[] }> {
  const { data } = await api.post("/export/agent-skills", { out_dir });
  return data;
}

export async function listFieldOptions(toolName: string, field: string): Promise<{ field: string; options: Array<JSONSchemaValue | JSONSchemaEnumOption>; count: number; note?: string }> {
  const { data } = await api.post("/v1/tools/options", { name: toolName, field });
  return data;
}

// ── 运行期 token(录制型 skill 请求鉴权):录制自动抓 → 存 PG;过期前端换一份即可,免重录 ──
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
