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

export async function createTenantWithPassword(
  tenant: string,
  username: string,
  password: string,
): Promise<{ tenant: string; api_key: string }> {
  const { data } = await api.post("/tenants", { tenant, username, password });
  return data;
}

export type TenantSession = { tenant: string; api_key: string };

/** 登录结果:未开两步验证直接给 api_key,已开则给 challenge 走第二步。 */
export type LoginResult =
  | ({ need_totp?: false } & TenantSession)
  | { need_totp: true; challenge: string; expires_in: number };

export async function login(username: string, password: string): Promise<LoginResult> {
  const { data } = await api.post("/auth/login", { username, password });
  return data;
}

/** 两步登录第二步:code 可以是 6 位 TOTP,也可以是备用码。 */
export async function loginTotp(challenge: string, code: string): Promise<TenantSession> {
  const { data } = await api.post("/auth/login/totp", { challenge, code });
  return data;
}

export async function changePassword(
  oldPassword: string,
  newPassword: string,
  code = "",
): Promise<void> {
  await api.post("/auth/change-password", {
    old_password: oldPassword,
    new_password: newPassword,
    code,
  });
}

export type TotpSetup = { secret: string; uri: string; qr_svg_data_uri: string };

export async function totpSetup(): Promise<TotpSetup> {
  const { data } = await api.post("/auth/totp/setup");
  return data;
}

/** 确认绑定,返回一次性备用码(明文仅此一次)。 */
export async function totpActivate(code: string): Promise<string[]> {
  const { data } = await api.post("/auth/totp/activate", { code });
  return data.backup_codes;
}

export async function totpDisable(password: string, code: string): Promise<void> {
  await api.post("/auth/totp/disable", { password, code });
}

export async function regenerateBackupCodes(
  password: string,
  code: string,
): Promise<string[]> {
  const { data } = await api.post("/auth/totp/backup-codes", { password, code });
  return data.backup_codes;
}

export async function listSkills(): Promise<SkillManifest[]> {
  const { data } = await api.get("/v1/skills");
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

// 导出本租户已上架 Skill 为文件式 skill，后端就地写入 out_dir
export type SkillExportMode = "proxy" | "package" | "both";

export async function getExportDirectory(): Promise<string> {
  const { data } = await api.get("/export/directory");
  return String(data?.out_dir || "").trim();
}

export async function saveExportDirectory(out_dir: string): Promise<string> {
  const { data } = await api.put("/export/directory", { out_dir });
  return String(data?.out_dir || out_dir).trim();
}

export async function exportAgentSkills(out_dir: string, mode: SkillExportMode = "package"): Promise<{ out_dir: string; mode: SkillExportMode; count: number; written: string[]; removed_frozen_folders?: string[] }> {
  const { data } = await api.post("/export/agent-skills", { out_dir, mode });
  return data;
}

// ── 运行期 token(录制型 skill 请求鉴权):录制自动抓 → 存 PG;过期前端换一份即可,免重录 ──
export interface RuntimeToken {
  tenant: string;
  subsystem: string;
  has_token: boolean;
  headers: Record<string, string>;   // 后端始终打码
  source?: string;                   // recording / manual / scheduled:*
  updated_at?: string;
}

export async function getRuntimeToken(tenant: string, subsystem: string): Promise<RuntimeToken> {
  const { data } = await api.get("/v1/settings/token", { params: { tenant, subsystem } });
  return data;
}

export interface SaveRuntimeTokenReq {
  tenant: string;
  subsystem: string;
  token?: string;                    // 只换一个头(默认 Authorization),与已存合并
  header_name?: string;
  token_prefix?: string;
  headers?: Record<string, string>;  // 或整组覆盖
}

export async function saveRuntimeToken(req: SaveRuntimeTokenReq): Promise<{ ok: boolean; headers: Record<string, string>; updated_at: string }> {
  const { data } = await api.post("/v1/settings/token", req);
  return data;
}
