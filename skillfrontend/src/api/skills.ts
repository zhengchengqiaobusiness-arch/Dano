import { api } from "./client";

// 与后端 catalog/manifest.SkillManifest 对齐
export interface SkillManifest {
  name: string;            // skill_id,如 A-OA.submit_leave
  subsystem: string;
  action: string;
  title: string;
  description: string;
  integration: string;     // adapter / workflow / api / page
  risk_level: string;      // L1..L5
  requires_confirmation: boolean;
  parameters: JSONSchema;  // 输入 JSON Schema
  output_schema?: Record<string, unknown>;
}

export interface JSONSchema {
  type?: string;
  properties?: Record<string, { type?: string; description?: string }>;
  required?: string[];
  additionalProperties?: boolean;
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

export async function deleteSkill(skillId: string): Promise<{ deleted: number }> {
  const { data } = await api.delete(`/v1/skills/${encodeURIComponent(skillId)}`);
  return data;
}

// 导出本租户已上架 Skill 为 pi 文件式 skill(.agents/skills/),后端就地写入 out_dir
export async function exportAgentSkills(out_dir: string): Promise<{ out_dir: string; count: number; written: string[] }> {
  const { data } = await api.post("/export/agent-skills", { out_dir });
  return data;
}
