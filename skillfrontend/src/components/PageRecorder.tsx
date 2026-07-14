import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  ConfigProvider,
  Collapse,
  Empty,
  Form,
  Input,
  List,
  Modal,
  Row,
  Select,
  Segmented,
  Space,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import {
  ApiOutlined,
  BranchesOutlined,
  DeleteOutlined,
  FileTextOutlined,
  LinkOutlined,
  PlusOutlined,
  ReloadOutlined,
  RobotOutlined,
  SaveOutlined,
  SettingOutlined,
  UpOutlined,
  DownOutlined,
} from "@ant-design/icons";
import { useNavigate } from "react-router-dom";

interface RecStep { op: string; locator?: string; field?: string; value?: string; required?: boolean; options?: any[] }
interface RecReq { method: string; url: string; has_body?: boolean; json?: boolean }
interface RecField {
  path: string; key: string; value: string; suggest_param: boolean; suggest_name: string;
  type?: string; required?: boolean; confidence?: number; confidence_tier?: string; name_source?: string;
  system_value?: boolean;
}
interface RecCand { idx: number; method: string; path: string }
interface RecSelect {
  path: string; source_url: string; value_key: string; label_key: string; label: string;
  count: number; multi?: boolean; options?: string[]; option_map?: Record<string, any>;
  enum_source?: string; enum_confirmed?: boolean;
}
interface RecIdentity { path: string; source: string }

interface FlowParam {
  path: string; key: string; label?: string; value: string; type: string; required: boolean; name_source?: string;
  page_required?: boolean | null; required_source?: string;
  category?: string; source_kind?: string; source?: any; reason?: string;
  exposed_to_user?: boolean; need_human_confirm?: boolean; editable?: boolean; confidence?: number;
  // 系统化:enum_options 兼容 list[string] 与 list[{label, value}];label→value 表由后端 enum_value_map 提供
  enum_options?: Array<string | { label: string; value: any }> | null;
  enum_value_map?: Record<string, any> | null;
}
interface FlowSelectBinding {
  param?: string; path?: string; source_url?: string; value_key?: string; label_key?: string;
  source_method?: string; source_headers?: Record<string, string>; source_body?: any;
  source_content_type?: string; source_role?: string; source_request_id?: string;
  options?: Array<string | { label: string; value: any }> | null; count?: number; multi?: boolean;
  option_map?: Record<string, any> | null;
  enum_source?: string | null; enum_confirmed?: boolean | null;
  id_path?: string | null;
}
interface FlowStepData {
  step_id: string; name: string; method: string; url: string; path: string; risk_level: string;
  params: FlowParam[]; selects?: FlowSelectBinding[]; identity?: any[];
  source_meta?: { role?: string; [k: string]: any }; semantic_role?: string;
  content_type?: string; body_source?: string; backup_body_source?: string; headers?: Record<string, string>;
  sample_inputs?: Record<string, string>; response_json?: any; success_rule?: any; fact_check?: any;
}
interface FlowLinkData {
  link_id: string; source_step_id: string; source_path: string;
  target_step_id: string; target_path: string;
  confirmed?: boolean; confidence?: number; param_name?: string | null; reason?: string;
}
interface FlowCapabilityFieldData {
  field_id?: string; scope?: string; display_name?: string; path?: string; key?: string; type?: string;
  required?: boolean; request_id?: string; request_index?: number | string | null; step_id?: string;
  source_kind?: string; source?: any; exposed_to_caller?: boolean; confidence?: number;
  confirmed?: boolean; locked?: boolean; evidence?: any[];
}
interface FlowCapabilityDependencyData {
  dependency_id?: string; type?: string; source?: Record<string, any>; target?: Record<string, any>;
  confidence?: number; confirmed?: boolean; locked?: boolean; reason?: string; evidence?: Record<string, any>;
}
interface FlowCapabilityData {
  name?: string; title?: string; intent?: string; kind?: string; capability_id?: string;
  request_refs?: FlowCapabilityRequestRefData[];
  step_ids?: string[];
  fields?: FlowCapabilityFieldData[];
  inputs?: FlowCapabilityFieldData[];
  request_fields?: FlowCapabilityFieldData[];
  internal_fields?: FlowCapabilityFieldData[];
  computed_fields?: FlowCapabilityFieldData[];
  outputs?: FlowCapabilityFieldData[];
  dependencies?: FlowCapabilityDependencyData[];
  nodes?: Array<Record<string, any>>;
  input_schema?: Record<string, any>;
  output_schema?: Record<string, any>;
  output_mapping?: Array<Record<string, any>>;
  preconditions?: Array<Record<string, any>>;
  confirmed?: boolean; confidence?: number; requires_human_confirm?: boolean;
  evidence?: Array<Record<string, any>>;
  caller_responsibilities?: string[]; skill_responsibilities?: string[];
  status?: string; locked?: boolean; updated_by?: string;
}
type CapabilityUsage = "execute" | "option_source" | "fact_check" | "preflight";
interface FlowCapabilityRequestRefData {
  request_id?: string; request_index?: number | string | null; step_id?: string;
  role?: string; method?: string; path?: string; sequence?: number | string | null;
  confidence?: number; reason?: string; usage?: CapabilityUsage; origin?: string;
  pinned?: boolean; confirmed?: boolean;
}
interface FlowCapabilityRelationData {
  relation_id?: string; type?: string; mode?: string;
  from_capability?: string; from_output?: string;
  to_capability?: string; to_input?: string;
  transform_owner?: string; requires_user_confirmation?: boolean;
  confidence?: number; confirmed?: boolean; reason?: string;
}
interface ReviewItemData {
  id: string; type: string; severity: string; title: string; reason: string;
  current_guess?: string; suggested_action?: string; resolved?: boolean; confidence?: number;
  target?: { kind?: string; step_id?: string; path?: string; link_id?: string; [k: string]: any };
  llm_suggestions?: Array<{
    action: "bind_previous_response" | "set_runtime_source" | "ask_human";
    confidence?: number; reason?: string;
    source_step_id?: string; source_path?: string;
    target_step_id?: string; target_path?: string; source_kind?: string;
  }>;
}
interface RequestRoleData {
  index?: number; method: string; path: string; role: string; keep: boolean;
  reason: string; confidence?: number;
}
interface RequestGraphEntry {
  request_index?: number | string | null; request_id?: string; method?: string; url?: string; path?: string; role?: string;
  keep?: boolean; reason?: string; confidence?: number; response_status?: number | null;
  response_json?: any; response_schema?: any; evidence?: any;
  page_id?: string | null; frame_id?: string | null; sequence?: number | string | null;
  state?: string; materialized_step_id?: string;
  used_by_capabilities?: string[];
  headers?: Record<string, string>; post_data?: any; content_type?: string;
  query?: Record<string, any>;
  occurrence_count?: number;
}
interface FlowSpecData {
  flow_id: string; title: string; business_description?: string;
  steps: FlowStepData[]; links: FlowLinkData[]; capabilities?: FlowCapabilityData[];
  capability_relations?: FlowCapabilityRelationData[];
  risk_level: string; review_items?: ReviewItemData[];
  request_facts?: {
    requests?: RequestGraphEntry[];
    diagnostics?: any[];
    page_events?: any[];
    option_sources?: any[];
    analysis?: Record<string, RequestRoleData & Record<string, any>>;
    usage?: Record<string, { request_id?: string; materialized_step_id?: string; state?: string; used_by_capabilities?: string[] }>;
  };
  meta?: {
    request_roles?: RequestRoleData[];
    capability_model?: { status?: string; source?: string; generated_count?: number };
    capability_generation?: {
      protocol?: string; status?: string; initial_completed?: boolean; last_mode?: string;
      last_cache_hit?: boolean; application_cache_hit?: boolean; model_calls?: number; model_cache_hits?: number;
      provider_cache_hits?: number; model_cache_rate?: number;
      indexed_range_changes?: any[]; [k: string]: any;
    };
    recording_pi_loop?: { mode?: "plan" | "repair"; updated_at?: string; [k: string]: any };
    request_graph?: {
      all_requests?: RequestGraphEntry[];
      selected_steps?: RequestGraphEntry[];
      candidate_reads?: RequestGraphEntry[];
      filtered_requests?: RequestGraphEntry[];
    };
    versions?: Array<{ version: number; action: string; reason?: string; created_at?: string; summary?: any }>;
    current_version?: number;
    current_fingerprint?: string;
  };
}
interface FlowCheckReport {
  passed?: boolean; errors?: string[]; warnings?: string[];
  dry_run?: {
    ok?: boolean; mode?: string; stage?: string; request_count?: number;
    missing_params?: string[]; self_check?: string[]; build_errors?: string[];
    fact_check?: { configured?: boolean; passed?: boolean; reason?: string; missing?: string[] };
  };
  review_items?: ReviewItemData[];
  review_summary?: { total?: number; high?: number; medium?: number; low?: number };
  api_preview?: { workflow_steps?: number; method?: string; path?: string; params?: string[]; required?: string[] };
  capability_preview?: Array<Record<string, any>>;
  capability_validation?: {
    passed?: boolean; errors?: string[]; warnings?: string[];
    capabilities?: Array<Record<string, any>>;
    checked_requests?: Array<Record<string, any>>;
    checked_manual_requests?: Array<Record<string, any>>;
    unused_high_confidence_requests?: Array<Record<string, any>>;
  };
  issue_groups?: Record<string, Array<{
    severity?: string; message?: string; source?: string; target?: Record<string, any>;
    audience?: "operator" | "internal"; actionable?: boolean; blocking?: boolean; auto_fixable?: boolean;
  }>>;
}
interface FlowOperationReport {
  operation?: "plan" | "repair" | "replan";
  changed?: boolean;
  changes?: Record<string, number>;
  summary?: string;
  cache_hit?: boolean;
  model_calls?: number;
  model_errors?: string[];
  errors_before?: number;
  errors_after?: number;
  warnings_before?: number;
  warnings_after?: number;
}
interface RecResult {
  ok?: boolean; action?: string; risk_level?: string; mode?: string; reason?: string;
  status?: string; warnings?: string[]; review_notes?: string[]; clarifications?: string[];
  recording_mode?: string; verification_status?: string; verification_basis?: string; skill_id?: string; asset_id?: string;
  api?: { method?: string; path?: string; params?: string[] };
  check_report?: FlowCheckReport;
}
type RecordingMode = "real_submit" | "record_only";
type RecorderConnectionState = "idle" | "connecting" | "connected" | "reconnecting" | "disconnected";

interface RecorderFrameMeta {
  frameWidth?: number;
  frameHeight?: number;
  viewportWidth?: number;
  viewportHeight?: number;
  deviceScaleFactor?: number;
}

const STATUS_META: Record<string, { color: string; label: string }> = {
  verified: { color: "success", label: "已验证" },
  partially_verified: { color: "warning", label: "部分验证" },
  needs_clarification: { color: "warning", label: "待澄清" },
  unsupported: { color: "default", label: "不支持" },
  rejected: { color: "error", label: "已拒绝" },
};

const KEYMAP: Record<string, string> = {
  Enter: "Enter", Backspace: "Backspace", Tab: "Tab", Delete: "Delete",
  ArrowLeft: "ArrowLeft", ArrowRight: "ArrowRight", ArrowUp: "ArrowUp", ArrowDown: "ArrowDown",
  Escape: "Escape", Home: "Home", End: "End", PageUp: "PageUp", PageDown: "PageDown",
};
const SAFE_COMBO_KEYS = new Set(["a", "c", "x", "z", "y", "Enter", "Backspace"]);
const MOD_ORDER = ["Control", "Meta", "Alt", "Shift"];

function recorderKeyName(e: React.KeyboardEvent<HTMLInputElement>): string | null {
  if (e.key === "Control" || e.key === "Shift" || e.key === "Alt" || e.key === "Meta") return null;
  if (e.altKey) return null;
  if (!e.ctrlKey && !e.metaKey && !e.shiftKey) return KEYMAP[e.key] || null;
  const base = e.key.length === 1 ? e.key.toLowerCase() : e.key;
  if (e.shiftKey && !e.ctrlKey && !e.metaKey) {
    if (base !== "Tab" && base !== "Enter") return null;
    return `Shift+${base}`;
  }
  if (!SAFE_COMBO_KEYS.has(base)) return null;
  const normalizedBase = base.length === 1 ? base.toUpperCase() : base;
  const mods: string[] = [];
  if (e.ctrlKey) mods.push("Control");
  if (e.metaKey) mods.push("Meta");
  const ordered = MOD_ORDER.filter((m) => mods.includes(m));
  const key = [...ordered, normalizedBase].join("+");
  return key;
}
const CATEGORY_OPTIONS = [
  { label: "用户参数", value: "user_param" },
  { label: "运行期变量", value: "runtime_var" },
  { label: "系统常量", value: "system_const" },
];
// 来源按"由谁/什么注入"归类：
//   用户侧: 用户输入
//   活接口侧: api_option / page_enum / form_option(运行期拉接口取)
//   静态枚举侧: manual_enum / static_enum(已固化在表单上)
//   上游链侧: previous_response(本能力内 step 响应)
//   系统侧: current_user / system_time / request_header / page_context / constant
const SOURCE_KIND_OPTIONS = [
  { label: "待配置", value: "unknown" },
  { label: "用户输入", value: "user_input" },
  { label: "接口候选", value: "api_option" },
  // 页面/接口快照/人工枚举在 UI 只保留一个入口，后端仍保留真实 provenance。
  { label: "枚举候选", value: "manual_enum" },
  { label: "上游响应", value: "previous_response" },
  { label: "请求头", value: "request_header" },
  { label: "当前用户", value: "current_user" },
  { label: "系统时间", value: "system_time" },
  { label: "系统生成值", value: "system_generated" },
  { label: "系统计算值", value: "computed" },
  { label: "调用上下文", value: "page_context" },
  { label: "固定值", value: "constant" },
];
const OPTION_SOURCE_KINDS = ["api_option", "page_enum", "form_option", "static_enum", "manual_enum"];
const ENUM_SOURCE_KINDS = ["page_enum", "form_option", "static_enum", "manual_enum"];
const RUNTIME_SUPPLIED_SOURCE_KINDS = new Set([
  "previous_response", "current_user", "storage", "cookie", "page_context",
  "request_header", "system_time", "system_generated", "computed", "constant", "loop_item",
]);

function paramExposedToCaller(p: FlowParam) {
  return p.category === "user_param"
    && p.exposed_to_user !== false
    && !RUNTIME_SUPPLIED_SOURCE_KINDS.has(p.source_kind || "");
}

function paramRequiredFromCaller(p: FlowParam) {
  return !!p.required && paramExposedToCaller(p);
}
// 三类分类 × 各自允许的来源，避免出现"用户参数 + 固定值"这种语义不一致组合。
const SOURCE_OPTIONS_BY_CATEGORY: Record<string, Array<{ label: string; value: string }>> = {
  user_param: SOURCE_KIND_OPTIONS.filter((x) =>
    ["user_input", "api_option" , "manual_enum", ].includes(x.value)
  ),
  // 运行期变量由执行环境注入；先允许保持“待配置”，不能静默写死成上游响应。
  runtime_var: SOURCE_KIND_OPTIONS.filter((x) =>
    ["unknown", "previous_response", "page_context", "current_user", "system_time", "system_generated", "computed", "request_header"].includes(x.value)
  ),
  system_const: SOURCE_KIND_OPTIONS.filter((x) =>
    ["constant"].includes(x.value)
  ),
};
const PARAM_TYPE_LABELS: Record<string, string> = {
  string: "文本",
  number: "数字",
  boolean: "布尔",
  datetime: "日期时间",
  date: "日期",
  enum: "单选枚举",
  array: "数组",
  object: "对象",
  "list-enum": "多选枚举",
  single_enum: "单选枚举",
  multi_enum: "多选枚举",
  text: "文本",
};
const PARAM_TYPE_OPTIONS = ["string", "number", "boolean", "datetime", "date", "enum", "array", "object", "list-enum"]
  .map((x) => ({ label: PARAM_TYPE_LABELS[x] || x, value: x }));
const CAPABILITY_KIND_OPTIONS = [
  { label: "状态查询", value: "query_status" },
  { label: "选项列表", value: "list_options" },
  { label: "批量校验", value: "validate_batch" },
  { label: "批量提交", value: "submit_batch" },
  { label: "提交", value: "submit" },
];
const CAPABILITY_USAGE_OPTIONS: Array<{ label: string; value: CapabilityUsage }> = [
  { label: "执行", value: "execute" },
  { label: "选项来源", value: "option_source" },
  { label: "事实核查", value: "fact_check" },
  { label: "前置检查", value: "preflight" },
];
const STEP_ROLE_OPTIONS = [
  "submit_anchor", "business_write", "business_get", "read_context", "read_option", "auth", "noise",
].map((x) => ({ label: x, value: x }));
const RISK_OPTIONS = ["L1", "L2", "L3", "L4"].map((x) => ({ label: x, value: x }));
const CT_OPTIONS = ["application/json", "application/x-www-form-urlencoded", "multipart/form-data", "text/plain"]
  .map((x) => ({ label: x, value: x }));

function fallbackStepName(method: string, path: string) {
  const seg = (path || "").split("/").filter(Boolean).pop() || "default";
  return `${(method || "POST").toUpperCase()}_${seg}`;
}
function stripHost(url: string) {
  return (url || "").replace(/^https?:\/\/[^/]+/, "");
}
function purePath(url: string) {
  const raw = stripHost(url || "");
  return raw.split("?", 1)[0] || raw || "/";
}
function splitUrlQuery(url?: string) {
  const raw = url || "";
  const idx = raw.indexOf("?");
  return {
    base: idx >= 0 ? raw.slice(0, idx) : raw,
    query: idx >= 0 ? raw.slice(idx + 1) : "",
  };
}
function queryToLines(url?: string) {
  const query = splitUrlQuery(url).query;
  if (!query) return "";
  return query.split("&").filter(Boolean).map((part) => {
    const [k, ...rest] = part.split("=");
    const value = rest.join("=");
    try {
      return `${decodeURIComponent(k || "")}=${decodeURIComponent(value || "")}`;
    } catch {
      return part;
    }
  }).join("\n");
}
function mergeUrlQuery(url: string | undefined, lines: string) {
  const { base } = splitUrlQuery(url);
  const parts = lines.split(/[\n&]/).map((x) => x.trim()).filter(Boolean).map((line) => {
    const [k, ...rest] = line.split("=");
    const key = k.trim();
    const val = rest.join("=").trim();
    if (!key) return "";
    return `${encodeURIComponent(key)}=${encodeURIComponent(val)}`;
  }).filter(Boolean);
  return parts.length ? `${base || ""}?${parts.join("&")}` : (base || "");
}
function stripBodyPrefix(path: string) {
  return path?.startsWith("body.") ? path.slice(5) : path;
}

function newRecordingActionName() {
  let uuid: string;
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    uuid = crypto.randomUUID();
  } else if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    uuid = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
  } else {
    uuid = `${Date.now().toString(16)}${Math.random().toString(16).slice(2).padEnd(16, "0")}`;
  }
  return `action_${uuid.replace(/-/g, "").toLowerCase()}`;
}

function positiveNumber(...values: unknown[]) {
  for (const value of values) {
    const number = Number(value);
    if (Number.isFinite(number) && number > 0) return number;
  }
  return undefined;
}

function frameMetaFromMessage(message: any): RecorderFrameMeta {
  const frame = message?.frame || message?.frame_meta || message?.metadata || message?.meta || {};
  const viewport = message?.viewport || frame?.viewport || {};
  return {
    frameWidth: positiveNumber(message?.frame_width, message?.width, frame?.frame_width, frame?.width),
    frameHeight: positiveNumber(message?.frame_height, message?.height, frame?.frame_height, frame?.height),
    viewportWidth: positiveNumber(message?.viewport_width, viewport?.width, frame?.viewport_width),
    viewportHeight: positiveNumber(message?.viewport_height, viewport?.height, frame?.viewport_height),
    deviceScaleFactor: positiveNumber(message?.device_scale_factor, message?.dpr, viewport?.deviceScaleFactor, frame?.device_scale_factor),
  };
}

function domAnchorPart(value: unknown) {
  return String(value ?? "").replace(/[^a-zA-Z0-9_-]+/g, "-").replace(/^-+|-+$/g, "") || "item";
}
function popupContainer(node?: HTMLElement) {
  return document.body;
}
function optionLabel(options: Array<{ label: string; value: string }>, value: string) {
  return options.find((o) => o.value === value)?.label || value;
}
function normalizeSourceKindForUi(sourceKind?: string | null) {
  return ENUM_SOURCE_KINDS.includes(sourceKind || "") ? "manual_enum" : (sourceKind || "");
}
function sourceOptionsForCategory(category?: string) {
  return SOURCE_OPTIONS_BY_CATEGORY[category || "user_param"] || SOURCE_KIND_OPTIONS;
}
function defaultSourceForCategory(category: string, current?: string) {
  const options = sourceOptionsForCategory(category);
  const normalized = normalizeSourceKindForUi(current);
  if (normalized && options.some((x) => x.value === normalized)) return normalized;
  if (category === "runtime_var") return "unknown";
  if (category === "system_const") return "constant";
  return "user_input";
}
function sourceDescriptor(sourceKind: string, p: FlowParam, current?: Record<string, any>) {
  const path = p.path;
  const previous = current || {};
  if (sourceKind === "unknown") return {};
  if (sourceKind === "user_input") return { kind: "sample", path };
  if (sourceKind === "constant") return { kind: "constant", path, manual: true };
  if (sourceKind === "page_context") return {
    kind: "page_context",
    context_key: previous.context_key || p.key || stripBodyPrefix(path).split(".").pop() || "",
    path,
    manual: true,
  };
  if (sourceKind === "request_header") return {
    kind: "request_header",
    header: previous.header || "",
    path,
    manual: true,
  };
  if (sourceKind === "system_time") return { kind: "system_time", path, manual: true };
  if (sourceKind === "system_generated") return {
    kind: "system_generated",
    strategy: previous.strategy || "uuid",
    path,
    manual: true,
  };
  if (sourceKind === "computed") return {
    ...previous,
    kind: "computed",
    path,
    manual: true,
  };
  if (sourceKind === "current_user") return { kind: "current_user", path, manual: true };
  if (sourceKind === "previous_response" && (previous.step_id || previous.response_path)) {
    return { ...previous, kind: "previous_response", path };
  }
  return { kind: sourceKind, path, manual: true };
}
function sourceNeedsConfiguration(sourceKind: string, source?: Record<string, any>) {
  if (sourceKind === "unknown") return true;
  if (sourceKind === "request_header") return !source?.header;
  if (sourceKind === "page_context") return !source?.context_key;
  if (sourceKind === "previous_response") return !(source?.step_id && (source?.response_path || source?.path));
  if (sourceKind === "system_generated") return !["uuid", "random_string", "random_number"].includes(source?.strategy || "");
  if (sourceKind === "computed") return !(source?.strategy && source?.start_field && source?.end_field);
  return false;
}
function sourceSelectOptionsForParam(p: FlowParam) {
  const options = sourceOptionsForCategory(p.category);
  const current = normalizeSourceKindForUi(p.source_kind);
  if (!current || options.some((item) => item.value === current)) return options;
  return [
    { label: `${optionLabel(SOURCE_KIND_OPTIONS, current)}（与当前分类不一致）`, value: current },
    ...options,
  ];
}
function NativeSelect({
  value,
  options,
  onChange,
  width = 140,
  disabled = false,
}: {
  value?: string;
  options: Array<{ label: string; value: string }>;
  onChange: (value: string) => void;
  width?: number | string;
  disabled?: boolean;
}) {
  const [local, setLocal] = useState(value || "");
  useEffect(() => setLocal(value || ""), [value]);
  const safeOptions = uniqueOptions(options);
  return (
    <select
      value={local}
      disabled={disabled}
      onChange={(e) => {
        setLocal(e.target.value);
        onChange(e.target.value);
      }}
      style={{
        width,
        height: 32,
        border: "1px solid #d9d9d9",
        borderRadius: 6,
        padding: "0 26px 0 8px",
        background: disabled ? "#f5f5f5" : "#fff",
        color: disabled ? "#999" : "#111",
        fontSize: 14,
      }}
    >
      {safeOptions.map((opt) => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
    </select>
  );
}
function uniqueOptions(options: Array<{ label: string; value: string }>) {
  const seen = new Set<string>();
  const out: Array<{ label: string; value: string }> = [];
  for (const opt of options || []) {
    const value = String(opt.value ?? "");
    if (seen.has(value)) continue;
    seen.add(value);
    out.push({ label: opt.label, value });
  }
  return out;
}
function EditableText({
  value,
  onSave,
  width = 180,
  placeholder = "",
}: {
  value?: string;
  onSave: (value: string) => void;
  width?: number | string;
  placeholder?: string;
}) {
  const [local, setLocal] = useState(value || "");
  useEffect(() => setLocal(value || ""), [value]);
  function save() {
    const next = local.trim();
    if (next !== (value || "")) onSave(next);
  }
  return (
    <Input
      value={local}
      placeholder={placeholder}
      style={{ width }}
      onChange={(e) => setLocal(e.target.value)}
      onBlur={save}
      onPressEnter={(e) => e.currentTarget.blur()}
    />
  );
}
function ComboInput({
  value,
  options,
  onChange,
  width = 260,
  disabled = false,
  placeholder = "",
}: {
  value?: string;
  options: Array<{ label: string; value: string }>;
  onChange: (value: string) => void;
  width?: number | string;
  disabled?: boolean;
  placeholder?: string;
}) {
  const [local, setLocal] = useState(value || "");
  const listIdRef = useRef(`combo_${Math.random().toString(36).slice(2, 10)}`);
  useEffect(() => setLocal(value || ""), [value]);
  return (
    <>
      <Input
        value={local}
        list={listIdRef.current}
        disabled={disabled}
        placeholder={placeholder}
        style={{ width }}
        onChange={(e) => {
          setLocal(e.target.value);
          onChange(e.target.value);
        }}
        onBlur={() => onChange(local.trim())}
        onPressEnter={(e) => e.currentTarget.blur()}
      />
      <datalist id={listIdRef.current}>
        {uniqueOptions(options).filter((opt) => opt.value).map((opt) => (
          <option key={opt.value} value={opt.value}>{opt.label}</option>
        ))}
      </datalist>
    </>
  );
}
function EnumValueInput({
  value,
  options,
  onSave,
  width = "100%",
}: {
  value?: string;
  options: Array<{ label: string; value: string }>;
  onSave: (value: string) => void;
  width?: number | string;
}) {
  const [local, setLocal] = useState(value || "");
  const listIdRef = useRef(`enum_${Math.random().toString(36).slice(2, 10)}`);
  useEffect(() => setLocal(value || ""), [value]);
  function save() {
    const next = local.trim();
    if (next !== (value || "")) onSave(next);
  }
  return (
    <>
      <Input
        value={local}
        list={listIdRef.current}
        placeholder="选择或输入枚举值"
        style={{ width }}
        onChange={(e) => setLocal(e.target.value)}
        onBlur={save}
        onPressEnter={(e) => e.currentTarget.blur()}
      />
      <datalist id={listIdRef.current}>
        {uniqueOptions(options).map((opt) => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
      </datalist>
    </>
  );
}
function EditableComboInput({
  value,
  options,
  onSave,
  width = "100%",
  placeholder = "",
}: {
  value?: string;
  options: Array<{ label: string; value: string }>;
  onSave: (value: string) => void;
  width?: number | string;
  placeholder?: string;
}) {
  const [local, setLocal] = useState(value || "");
  const listIdRef = useRef(`edit_combo_${Math.random().toString(36).slice(2, 10)}`);
  useEffect(() => setLocal(value || ""), [value]);
  function save() {
    const next = local.trim();
    if (next !== (value || "")) onSave(next);
  }
  return (
    <>
      <Input
        value={local}
        list={listIdRef.current}
        placeholder={placeholder}
        style={{ width }}
        onChange={(e) => setLocal(e.target.value)}
        onBlur={save}
        onPressEnter={(e) => e.currentTarget.blur()}
      />
      <datalist id={listIdRef.current}>
        {uniqueOptions(options).map((opt) => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
      </datalist>
    </>
  );
}
function EditableTextArea({
  value,
  onSave,
  rows = 3,
  placeholder = "",
}: {
  value?: string;
  onSave: (value: string) => void;
  rows?: number;
  placeholder?: string;
}) {
  const [local, setLocal] = useState(value || "");
  useEffect(() => setLocal(value || ""), [value]);
  function save() {
    if (local !== (value || "")) onSave(local);
  }
  return (
    <Input.TextArea
      rows={rows}
      value={local}
      placeholder={placeholder}
      onChange={(e) => setLocal(e.target.value)}
      onBlur={save}
    />
  );
}
function FieldControl({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 0 }}>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>{label}</Typography.Text>
      {children}
    </div>
  );
}
function PathText({ value, maxWidth = 520 }: { value?: string; maxWidth?: number | string }) {
  return (
    <Typography.Text
      code
      title={value || ""}
      style={{
        display: "inline-block",
        maxWidth,
        overflow: "hidden",
        textOverflow: "ellipsis",
        whiteSpace: "nowrap",
        verticalAlign: "middle",
      }}
    >
      {value || ""}
    </Typography.Text>
  );
}
function leafPaths(node: any, prefix = ""): string[] {
  const out: string[] = [];
  if (node == null) return out;
  if (Array.isArray(node)) {
    node.forEach((v, i) => out.push(...leafPaths(v, `${prefix}[${i}]`)));
    return out;
  }
  if (typeof node === "object") {
    Object.entries(node).forEach(([k, v]) => out.push(...leafPaths(v, prefix ? `${prefix}.${k}` : k)));
    return out;
  }
  return prefix ? [prefix] : [];
}
function leafPathValues(node: any, prefix = ""): Array<{ path: string; value: string }> {
  const out: Array<{ path: string; value: string }> = [];
  if (node == null) return out;
  if (Array.isArray(node)) {
    node.forEach((v, i) => out.push(...leafPathValues(v, `${prefix}[${i}]`)));
    return out;
  }
  if (typeof node === "object") {
    Object.entries(node).forEach(([k, v]) => out.push(...leafPathValues(v, prefix ? `${prefix}.${k}` : k)));
    return out;
  }
  if (prefix) out.push({ path: prefix, value: String(node) });
  return out;
}
function severityColor(sev?: string) {
  return sev === "high" ? "error" : sev === "medium" ? "warning" : "default";
}
function requestGraphPath(req: RequestGraphEntry) {
  return (req.path || stripHost(req.url || "") || "").split("?", 1)[0];
}
function requestGraphSignature(req: RequestGraphEntry) {
  return `${(req.method || "GET").toUpperCase()} ${requestGraphPath(req)}`;
}
function requestGraphKey(req: RequestGraphEntry) {
  if (req.request_id) return `id:${req.request_id}`;
  if (req.request_index != null) return `idx:${String(req.request_index)}`;
  return `sig:${requestGraphSignature(req)}`;
}
function requestQueryValues(req: RequestGraphEntry) {
  if (req.query && Object.keys(req.query).length) return req.query;
  const raw = String(req.url || "");
  const queryText = raw.includes("?") ? raw.slice(raw.indexOf("?") + 1) : "";
  const values: Record<string, string[]> = {};
  new URLSearchParams(queryText).forEach((value, key) => {
    values[key] = [...(values[key] || []), value];
  });
  return values;
}
function isPaginationQueryKey(key: string) {
  return /^(?:page(?:no|num|number|index|size)?|current|limit|offset|rows?)$/i.test(key.replace(/[._-]/g, ""));
}
function requestBusinessFilterCount(req: RequestGraphEntry) {
  return Object.entries(requestQueryValues(req)).filter(([key, value]) =>
    !isPaginationQueryKey(key) && (Array.isArray(value) ? value : [value]).some((item) => String(item ?? "").trim())
  ).length;
}
function requestQueryFieldCount(req: RequestGraphEntry) {
  return Object.keys(requestQueryValues(req)).length;
}
function richerRequestFact(candidate: RequestGraphEntry, current: RequestGraphEntry) {
  const candidateScore = [requestBusinessFilterCount(candidate), requestQueryFieldCount(candidate), candidate.response_json != null ? 1 : 0];
  const currentScore = [requestBusinessFilterCount(current), requestQueryFieldCount(current), current.response_json != null ? 1 : 0];
  for (let idx = 0; idx < candidateScore.length; idx += 1) {
    if (candidateScore[idx] !== currentScore[idx]) return candidateScore[idx] > currentScore[idx];
  }
  return Number(candidate.sequence ?? candidate.request_index ?? 0) > Number(current.sequence ?? current.request_index ?? 0);
}
function requestDisplayPath(req: RequestGraphEntry) {
  const base = req.path || stripHost(req.url || "");
  if (String(base || "").includes("?") || !requestQueryFieldCount(req)) return base;
  const query = new URLSearchParams();
  Object.entries(requestQueryValues(req)).forEach(([key, value]) => {
    (Array.isArray(value) ? value : [value]).forEach((item) => query.append(key, String(item ?? "")));
  });
  return `${base}?${query.toString()}`;
}
function isApiLikeRequest(req: RequestGraphEntry) {
  const path = (req.path || stripHost(req.url || "") || "").split("?", 1)[0].toLowerCase();
  if (!path) return false;
  if (/\.(?:css|js|mjs|map|png|jpe?g|gif|svg|ico|webp|woff2?|ttf|eot|html?|txt|xml)$/i.test(path)) return false;
  if (["noise", "auth"].includes(req.role || "")) return false;
  const role = req.role || "";
  if (["submit_anchor", "business_write", "business_get", "read_context", "read_option"].includes(role)) return true;
  if (req.response_json != null) return true;
  return /^\/?(?:api|admin-api|appgateway|gsgl|oa|bpm|system|workflow|process|v1|v2)\b/i.test(path);
}
function allCapturedRequests(spec?: FlowSpecData | null) {
  const facts = spec?.request_facts;
  const graph = spec?.meta?.request_graph || {};
  const factSource = (facts?.requests || []).map((req) => {
    const key = requestGraphKey(req);
    const analysis = (facts?.analysis?.[req.request_id || key] || facts?.analysis?.[key] || {}) as Partial<RequestRoleData & Record<string, any>>;
    const usage = facts?.usage?.[req.request_id || key] || facts?.usage?.[key] || {};
    return {
      ...req,
      role: req.role || analysis.role,
      keep: req.keep ?? analysis.keep,
      reason: req.reason || analysis.reason,
      confidence: typeof req.confidence === "number" ? req.confidence : analysis.confidence,
      state: req.state || usage.state,
      materialized_step_id: req.materialized_step_id || usage.materialized_step_id,
      used_by_capabilities: Array.from(new Set([
        ...(req.used_by_capabilities || []),
        ...(usage.used_by_capabilities || []),
      ].filter(Boolean))),
    };
  });
  const source = factSource.length ? factSource : graph.all_requests?.length ? graph.all_requests : [
    ...(graph.selected_steps || []),
    ...(graph.candidate_reads || []),
  ];
  const selectedSigs = new Set((graph.selected_steps || []).map(requestGraphSignature));
  const stepSigs = new Set((spec?.steps || []).map((s) => `${(s.method || "").toUpperCase()} ${purePath(s.path || s.url || "")}`));
  const stepReqKeys = new Set((spec?.steps || []).flatMap((s) => {
    const meta = s.source_meta || {};
    const out: string[] = [];
    if (meta.request_id) out.push(`id:${meta.request_id}`);
    if (meta.request_index != null) out.push(`idx:${String(meta.request_index)}`);
    return out;
  }));
  const selectedRank = (req: RequestGraphEntry) => (
    selectedSigs.has(requestGraphSignature(req)) ||
    stepSigs.has(`${(req.method || "").toUpperCase()} ${purePath(req.path || req.url || "")}`) ||
    stepReqKeys.has(requestGraphKey(req))
  ) ? 0 : 1;
  const sorted = source
    .filter(isApiLikeRequest)
    .filter((req, idx, arr) => arr.findIndex((x) => requestGraphKey(x) === requestGraphKey(req)) === idx)
    .sort((a, b) => selectedRank(a) - selectedRank(b) || requestRoleRank(a) - requestRoleRank(b) || (b.confidence ?? 0) - (a.confidence ?? 0) || Number(a.request_index ?? 0) - Number(b.request_index ?? 0));
  const grouped = new Map<string, RequestGraphEntry>();
  for (const req of sorted) {
    const signature = requestGraphSignature(req);
    const current = grouped.get(signature);
    if (!current) {
      grouped.set(signature, { ...req, occurrence_count: 1 });
      continue;
    }
    current.occurrence_count = (current.occurrence_count || 1) + 1;
    current.used_by_capabilities = Array.from(new Set([
      ...(current.used_by_capabilities || []),
      ...(req.used_by_capabilities || []),
    ]));
    if (richerRequestFact(req, current)) {
      grouped.set(signature, {
        ...req,
        occurrence_count: current.occurrence_count,
        used_by_capabilities: current.used_by_capabilities,
      });
    }
  }
  return Array.from(grouped.values());
}
function requestRoleRank(req: RequestGraphEntry) {
  const role = req.role || "";
  if (["submit_anchor", "business_write"].includes(role)) return 0;
  if (role === "business_get") return 1;
  if (role === "read_context") return 2;
  if (role === "read_option") return 3;
  return 9;
}
function requestOptionValue(req: RequestGraphEntry) {
  return requestGraphKey(req);
}
function findCapturedRequest(spec: FlowSpecData | null | undefined, key?: string) {
  if (!key) return undefined;
  return allCapturedRequests(spec).find((req) => requestOptionValue(req) === key);
}
function stepRequestSignature(step: FlowStepData) {
  return `${(step.method || "").toUpperCase()} ${purePath(step.path || step.url)}`;
}
const CAPABILITY_NODE_CHILD_KEYS = ["children", "steps", "then", "else", "otherwise"] as const;
function capabilityNodeStepIds(nodes?: Array<Record<string, any>>) {
  const ordered: string[] = [];
  const seen = new Set<string>();
  const visit = (items: any) => {
    if (!Array.isArray(items)) return;
    for (const node of items) {
      if (!node || typeof node !== "object") continue;
      if (String(node.type || "") === "call") {
        const stepId = String(node.step_id || "").trim();
        if (stepId && !seen.has(stepId)) {
          seen.add(stepId);
          ordered.push(stepId);
        }
      }
      for (const key of CAPABILITY_NODE_CHILD_KEYS) visit(node[key]);
    }
  };
  visit(nodes || []);
  return ordered;
}
function capabilityActualStepIds(cap?: FlowCapabilityData | null) {
  const ordered = capabilityNodeStepIds(cap?.nodes);
  const seen = new Set(ordered);
  for (const raw of cap?.step_ids || []) {
    const stepId = String(raw || "").trim();
    if (!stepId || seen.has(stepId)) continue;
    seen.add(stepId);
    ordered.push(stepId);
  }
  return ordered;
}
function capabilityRequestRefForStep(cap: FlowCapabilityData | null | undefined, stepId: string) {
  return (cap?.request_refs || []).find((ref) => ref.step_id === stepId);
}
function capabilityUsageLabel(usage?: string) {
  return optionLabel(CAPABILITY_USAGE_OPTIONS, usage || "execute");
}
function capturedRequestSteps(spec: FlowSpecData | null | undefined, req: RequestGraphEntry) {
  const signature = requestGraphSignature(req);
  const exact = (spec?.steps || []).filter((step) => {
    const meta = step.source_meta || {};
    return (req.request_id && String(meta.request_id || "") === String(req.request_id)) ||
      (req.request_index != null && String(meta.request_index ?? "") === String(req.request_index));
  });
  if (req.request_id || req.request_index != null) return exact;
  return (spec?.steps || []).filter((step) => {
    return stepRequestSignature(step) === signature;
  });
}
function capturedRequestCapabilityNames(spec: FlowSpecData | null | undefined, req: RequestGraphEntry) {
  const requestStepIds = new Set(capturedRequestSteps(spec, req).map((step) => step.step_id));
  const names = (spec?.capabilities || [])
    .filter((cap) => capabilityActualStepIds(cap).some((stepId) => requestStepIds.has(stepId)))
    .map((cap) => String(cap.title || cap.name || cap.capability_id || "").trim())
    .filter(Boolean);
  return Array.from(new Set(names));
}
function isCapturedRequestFieldCandidate(spec: FlowSpecData | null | undefined, req: RequestGraphEntry) {
  if (req.role === "read_option") return true;
  const reqPath = requestGraphPath(req);
  const usedAsSelectSource = (spec?.steps || []).some((step) => (step.selects || []).some((select) =>
    (req.request_id && String(select.source_request_id || "") === String(req.request_id)) ||
    (select.source_url && purePath(select.source_url) === purePath(reqPath))
  ));
  if (usedAsSelectSource) return true;
  return (spec?.request_facts?.option_sources || []).some((source: any) => {
    if (!source || typeof source !== "object") return false;
    return (req.request_id && [source.request_id, source.source_request_id].some((value) => String(value || "") === String(req.request_id))) ||
      [source.path, source.url, source.source_url].some((value) => value && purePath(String(value)) === purePath(reqPath));
  });
}
function isRequestInSteps(spec: FlowSpecData | null | undefined, req: RequestGraphEntry) {
  return capturedRequestSteps(spec, req).length > 0;
}
function capturedRequestOptions(spec: FlowSpecData | null | undefined, opts: { includeIncluded?: boolean } = {}) {
  return allCapturedRequests(spec)
    .filter((req) => opts.includeIncluded || !isRequestInSteps(spec, req))
    .map((req) => ({
      label: `#${req.sequence ?? req.request_index ?? ""} ${req.method || "GET"} ${requestDisplayPath(req)}${(req.occurrence_count || 1) > 1 ? ` · ${req.occurrence_count} 次` : ""}`,
      value: requestOptionValue(req),
    }))
    .filter((x) => x.value);
}
function confidencePercent(value?: number) {
  if (typeof value !== "number" || Number.isNaN(value) || value <= 0) return "待评估";
  return `${Math.round(value * 100)}%`;
}
function confidenceColor(value?: number) {
  if (typeof value !== "number" || Number.isNaN(value) || value <= 0) return "default";
  if (value >= 0.9) return "success";
  if (value >= 0.7) return "warning";
  return "error";
}
function inferredSchemaBusinessType(spec: Record<string, any>) {
  if (Array.isArray(spec.enum) && spec.enum.length) return "enum";
  if (spec.type === "array" && Array.isArray(spec.items?.enum) && spec.items.enum.length) return "list-enum";
  if (spec.format === "date-time") return "datetime";
  if (spec.format === "date") return "date";
  if (spec.format === "name-ref" || spec["x-options-source"] || Array.isArray(spec["x-options"])) return "enum";
  return String(spec.type || spec.format || "any");
}
function schemaBusinessType(spec: Record<string, any>) {
  return String(spec["x-dano-business-type"] || inferredSchemaBusinessType(spec));
}
function schemaWireType(spec: Record<string, any>) {
  const explicit = String(spec["x-dano-wire-type"] || "");
  if (explicit) return explicit;
  const type = String(spec.type || "any");
  const itemType = spec.items && typeof spec.items === "object" ? String(spec.items.type || "") : "";
  return type === "array" && itemType ? `${type}<${itemType}>` : type;
}
function schemaFieldRows(schema?: Record<string, any>) {
  if (!schema || typeof schema !== "object") return [];
  const props = schema.properties && typeof schema.properties === "object" ? schema.properties : schema;
  const required = new Set(Array.isArray(schema.required) ? schema.required.map(String) : []);
  return Object.entries(props || {})
    .filter(([, spec]) => spec && typeof spec === "object")
    .map(([name, spec]) => ({
      name,
      businessType: schemaBusinessType(spec as Record<string, any>),
      wireType: schemaWireType(spec as Record<string, any>),
      description: String((spec as any).description || (spec as any).title || ""),
      required: required.has(name),
    }));
}
function preferredSkillTitle(spec?: FlowSpecData | null) {
  if (!spec) return "";
  const caps = spec.capabilities || [];
  if (caps.length === 1) return (caps[0].title || spec.title || caps[0].name || "").trim();
  return (spec.title || caps.map((c) => c.title || c.name).filter(Boolean).join(" / ")).trim();
}
function jsonSchemaForParam(p: FlowParam) {
  const t = (p.type || "string").toLowerCase();
  const schema: Record<string, any> =
    t === "number" ? { type: "number" } :
    t === "boolean" ? { type: "boolean" } :
    t === "date" ? { type: "string", format: "date" } :
    t === "datetime" ? { type: "string", format: "date-time" } :
    t === "array" || t === "list-enum" ? { type: "array", items: { type: "string" } } :
    t === "object" ? { type: "object" } :
    { type: "string" };
  if (p.label || p.key) schema.description = p.label || p.key;
  const opts = enumOptionRecordList(p.enum_options || []);
  if (t === "enum" && opts.length) schema.enum = opts.map((x) => x.label);
  if (t === "list-enum" && opts.length) schema.items = { type: "string", enum: opts.map((x) => x.label) };
  return schema;
}
function enumOptionRecordList(raw: any[]) {
  const out: Array<{ label: string; value: any }> = [];
  for (const x of raw || []) {
    if (x == null) continue;
    if (typeof x === "object") {
      const label = String(x.label ?? x.text ?? x.name ?? x.value ?? "").trim();
      if (label) out.push({ label, value: x.value ?? label });
    } else {
      const label = String(x).trim();
      if (label) out.push({ label, value: label });
    }
  }
  return out;
}
function inferJsonSchema(value: any): Record<string, any> {
  if (Array.isArray(value)) return { type: "array", items: value.length ? inferJsonSchema(value[0]) : {} };
  if (value && typeof value === "object") {
    return {
      type: "object",
      properties: Object.fromEntries(Object.entries(value).slice(0, 80).map(([k, v]) => [k, inferJsonSchema(v)])),
    };
  }
  if (typeof value === "number") return { type: "number" };
  if (typeof value === "boolean") return { type: "boolean" };
  return { type: "string" };
}
function compactJson(value: any, maxLen = 160) {
  if (value == null) return "";
  const rawValue = typeof value === "string" ? value : JSON.stringify(value);
  const raw = rawValue == null ? "" : rawValue;
  return raw.length > maxLen ? `${raw.slice(0, maxLen)}...` : raw;
}

export default function PageRecorder({ tenant, subsystem, baseUrl, storageState }: {
  tenant: string; subsystem: string; baseUrl: string; storageState: string;
}) {
  const nav = useNavigate();
  const wsRef = useRef<WebSocket | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const kbRef = useRef<HTMLInputElement | null>(null);
  const consoleBufRef = useRef<any[]>([]);
  const latestFrameRef = useRef<{ seq: number; src: string; meta: RecorderFrameMeta } | null>(null);
  const frameRafRef = useRef<number | null>(null);
  const renderedFrameSeqRef = useRef(0);
  const pointerMoveRafRef = useRef<number | null>(null);
  const pendingPointerMoveRef = useRef<Record<string, unknown> | null>(null);
  const pointerGestureRef = useRef<{
    pointerId: number; nx: number; ny: number; clientX: number; clientY: number;
    button: string; buttons: number; pointerType: string; dragging: boolean;
  } | null>(null);
  const pendingClickRef = useRef<{
    timer: number; nx: number; ny: number; clientX: number; clientY: number; button: string; pointerType: string;
  } | null>(null);
  const lastInputErrorNoticeRef = useRef(0);
  const intentionalCloseRef = useRef(false);
  const wsAliveRef = useRef(false);                                // FC2 修复:跟踪 WS 存活,避免 send 失败时反复弹错
  const isComposingRef = useRef(false);                           // FH2 修复:中文输入法拼写中标记,防 onKbInput 误发中间字符

  const [phase, setPhase] = useState<"idle" | "recording" | "publishing" | "done">("idle");
  const phaseRef = useRef(phase);                                  // FC1 修复:同步最新 phase,ws.onclose 闭包不再 stale
  useEffect(() => { phaseRef.current = phase; }, [phase]);
  const [startUrl, setStartUrl] = useState("");
  const [connectionState, setConnectionState] = useState<RecorderConnectionState>("idle");
  const [reconnectedSessionNeedsCapture, setReconnectedSessionNeedsCapture] = useState(false);
  const [hasFrame, setHasFrame] = useState(false);
  const [frameMeta, setFrameMeta] = useState<RecorderFrameMeta>({});
  const hasFrameRef = useRef(false);
  useEffect(() => { hasFrameRef.current = hasFrame; }, [hasFrame]);
  const [steps, setSteps] = useState<RecStep[]>([]);
  const [reqs, setReqs] = useState<RecReq[]>([]);
  const [fields, setFields] = useState<RecField[]>([]);
  const [picked, setPicked] = useState<Record<string, { on: boolean; name: string }>>({});
  const [reqMeta, setReqMeta] = useState<{ method: string; url: string } | null>(null);
  const [cands, setCands] = useState<RecCand[]>([]);
  const [chosenIdx, setChosenIdx] = useState(0);
  const [stepSel, setStepSel] = useState<Record<number, boolean>>({});
  const [selects, setSelects] = useState<Record<string, RecSelect>>({});
  const [identity, setIdentity] = useState<Record<string, RecIdentity>>({});
  const [action, setAction] = useState(() => newRecordingActionName());
  const [title, setTitle] = useState("");
  const [result, setResult] = useState<RecResult | null>(null);
  const [recordingMode, setRecordingMode] = useState<RecordingMode>("real_submit");
  const [err, setErr] = useState("");

  const [flowSpec, setFlowSpec] = useState<FlowSpecData | null>(null);
  const flowSpecRef = useRef<FlowSpecData | null>(null);
  useEffect(() => { flowSpecRef.current = flowSpec; }, [flowSpec]);
  const [checkReport, setCheckReport] = useState<FlowCheckReport | null>(null);
  const [titleDraft, setTitleDraft] = useState("");               // FC3 修复:标题本地草稿,WS 推送不再即时覆盖编辑
  const [descDraft, setDescDraft] = useState("");                 // FC3 修复:说明本地草稿
  useEffect(() => { setTitleDraft(flowSpec?.title || ""); }, [flowSpec?.title]);
  useEffect(() => { setDescDraft(flowSpec?.business_description || ""); }, [flowSpec?.business_description]);
  // FH6 修复:JSON 面板 — 仅在 jsonDraft 未被本地编辑时才跟随 flowSpec 同步;否则用户输入会被 WS 推送覆盖
  const jsonDirtyRef = useRef(false);
  useEffect(() => {
    if (flowSpec && !jsonDirtyRef.current) {
      setJsonDraft(JSON.stringify(flowSpec, null, 2));
    }
  }, [flowSpec]);
  const [addingStep, setAddingStep] = useState(false);
  const [newStep, setNewStep] = useState({ method: "POST", path: "/api/", name: "", risk_level: "L3", role: "business_write" });
  const [newStepRequestKey, setNewStepRequestKey] = useState("");
  const [newParamRequestKey, setNewParamRequestKey] = useState("");
  const [capabilityAddValue, setCapabilityAddValue] = useState<Record<number, string>>({});
  const [capabilityAddUsage, setCapabilityAddUsage] = useState<Record<number, CapabilityUsage | "">>({});
  const pendingCapabilityMembershipRef = useRef<Array<{
    capability: string; requestId?: string; requestIndex?: number | string | null; usage: CapabilityUsage;
  }>>([]);
  const [newParam, setNewParam] = useState({ step_id: "", path: "", key: "", type: "string", category: "user_param" });
  const [newLink, setNewLink] = useState({ source_step_id: "", source_path: "", target_step_id: "", target_path: "" });
  const [editingLink, setEditingLink] = useState<Record<string, FlowLinkData>>({});
  const [bindDraft, setBindDraft] = useState<Record<string, { source_step_id?: string; source_path?: string }>>({});
  const [jsonDraft, setJsonDraft] = useState("");
  const [jsonErr, setJsonErr] = useState("");
  const [lastServerJson, setLastServerJson] = useState("");
  const [namingBusy, setNamingBusy] = useState(false);
  const [descBusy, setDescBusy] = useState(false);
  const [orchestrateBusy, setOrchestrateBusy] = useState(false);
  const [autoFixBusy, setAutoFixBusy] = useState(false);
  const [lastOperationReport, setLastOperationReport] = useState<FlowOperationReport | null>(null);
  const [expandedCapabilityKeys, setExpandedCapabilityKeys] = useState<string[]>([]);
  const [expandedCapabilitySections, setExpandedCapabilitySections] = useState<Record<number, string[]>>({});
  const [expandedCapabilitySteps, setExpandedCapabilitySteps] = useState<Record<number, string[]>>({});
  const [expandedRequestPanels, setExpandedRequestPanels] = useState<string[]>([]);
  const flowOperationRef = useRef<{
    mode: "plan" | "repair" | "replan"; previousUpdatedAt?: string; operationId: string;
  } | null>(null);
  const finalizeOperationRef = useRef<string | null>(null);
  const publishOperationRef = useRef<string | null>(null);
  const flowOperationTimerRef = useRef<number | null>(null);
  const flowMutationQueueRef = useRef<any[]>([]);
  const flowMutationInFlightRef = useRef<any | null>(null);
  const flowMutationSeqRef = useRef(0);
  const afterFlowSyncRef = useRef<(() => void) | null>(null);
  const [activeFlowTab, setActiveFlowTab] = useState("abilities");

  function acceptFlowSpec(fs: FlowSpecData) {
    const pending = pendingCapabilityMembershipRef.current;
    const edits: any[] = [];
    const remaining: typeof pending = [];
    let nextSpec = fs;
    for (const item of pending) {
      const capIdx = (nextSpec.capabilities || []).findIndex((cap, idx) => capabilityRef(cap, idx) === item.capability);
      const step = (nextSpec.steps || []).find((candidate) => {
        const meta = candidate.source_meta || {};
        return (item.requestId && String(meta.request_id || "") === item.requestId) ||
          (item.requestIndex != null && String(meta.request_index ?? "") === String(item.requestIndex));
      });
      const serverRef = capIdx >= 0 && step ? capabilityRequestRefForStep(nextSpec.capabilities?.[capIdx], step.step_id) : undefined;
      if (
        capIdx < 0 || !step
        || (item.usage !== "option_source" && !capabilityActualStepIds(nextSpec.capabilities?.[capIdx]).includes(step.step_id))
        || (item.usage === "option_source" && !serverRef)
      ) {
        remaining.push(item);
        continue;
      }
      const cap = nextSpec.capabilities![capIdx];
      const existingRef = serverRef || capabilityRequestRefForStep(cap, step.step_id);
      const requestRefs: FlowCapabilityRequestRefData[] = [
        ...(cap.request_refs || []).filter((ref) => ref.step_id !== step.step_id),
        {
          ...(existingRef || {}),
          request_id: item.requestId || existingRef?.request_id,
          request_index: item.requestIndex ?? existingRef?.request_index,
          step_id: step.step_id,
          usage: item.usage,
          origin: "manual",
          pinned: true,
          confirmed: true,
        },
      ];
      const capabilities = [...(nextSpec.capabilities || [])];
      capabilities[capIdx] = { ...cap, request_refs: requestRefs };
      nextSpec = { ...nextSpec, capabilities };
      if (existingRef?.usage !== item.usage || existingRef?.origin !== "manual" || !existingRef?.pinned) {
        edits.push({ op: "update_capability", capability_index: capIdx, field: "request_refs", value: requestRefs });
      }
    }
    pendingCapabilityMembershipRef.current = remaining;
    flowSpecRef.current = nextSpec;
    setFlowSpec(nextSpec);
    if (edits.length) send({ type: "flow_update", edits });
    const nextTitle = preferredSkillTitle(nextSpec);
    if (nextTitle && !title.trim()) setTitle(nextTitle);
  }

  function newCostlyOperationId(prefix: string) {
    const id = typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    return `${prefix}-${id}`;
  }

  function finishFlowOperation(loop?: { mode?: string; updated_at?: string }, operation?: string, operationId?: string) {
    const active = flowOperationRef.current;
    if (
      !active
      || (operationId && operationId !== active.operationId)
      || (operation && operation !== active.mode)
      || (!operation && (
        loop?.mode !== active.mode
        || !loop.updated_at
        || loop.updated_at === active.previousUpdatedAt
      ))
    ) return;
    if (flowOperationTimerRef.current != null) window.clearTimeout(flowOperationTimerRef.current);
    flowOperationTimerRef.current = null;
    flowOperationRef.current = null;
    setOrchestrateBusy(false);
    setAutoFixBusy(false);
  }

  function clearFlowOperation() {
    if (flowOperationTimerRef.current != null) window.clearTimeout(flowOperationTimerRef.current);
    flowOperationTimerRef.current = null;
    flowOperationRef.current = null;
    setOrchestrateBusy(false);
    setAutoFixBusy(false);
  }

  function armFlowOperationWatchdog(label: string) {
    if (flowOperationTimerRef.current != null) window.clearTimeout(flowOperationTimerRef.current);
    flowOperationTimerRef.current = window.setTimeout(() => {
      if (!flowOperationRef.current) return;
      // 120s is only a progress notice. The server-side LLM task remains
      // active, so showing a failure here produced the observed "先报错、后成功".
      message.warning(`${label}仍在服务端执行，完成后页面会自动更新`);
      flowOperationTimerRef.current = window.setTimeout(() => {
        if (!flowOperationRef.current) return;
        clearFlowOperation();
        message.error(`${label}超过10分钟未完成，请检查服务端连接`);
      }, 480000);
    }, 120000);
  }

  useEffect(() => () => {
    // FC4 修复:仅当 phase 处于 recording/publishing 时才关 WS(避免 StrictMode 双 mount 或组件复用时误关正在用的 WS)
    // wsRef.current 在首次 mount 时为 null(start 才会建),所以首次 cleanup 一定是 noop,无副作用
    if (phaseRef.current === "recording" || phaseRef.current === "publishing") {
      intentionalCloseRef.current = true;
      wsRef.current?.close();
    }
    if (pointerMoveRafRef.current != null) window.cancelAnimationFrame(pointerMoveRafRef.current);
    if (pendingClickRef.current) window.clearTimeout(pendingClickRef.current.timer);
  }, []);

  useEffect(() => {
    const onError = (event: ErrorEvent) => {
      consoleBufRef.current.push({
        type: "error",
        source: "window.onerror",
        text: `${event.message} (${event.filename || "?"}:${event.lineno || 0})`,
        ts: Date.now(),
      });
    };
    const onRej = (event: PromiseRejectionEvent) => {
      const msg = event.reason?.message || (typeof event.reason === "string" ? event.reason : JSON.stringify(event.reason || ""));
      consoleBufRef.current.push({ type: "error", source: "unhandledrejection", text: msg || "unknown", ts: Date.now() });
    };
    const origError = console.error;
    console.error = (...args: any[]) => {
      try {
        consoleBufRef.current.push({
          type: "error",
          source: "console.error",
          text: args.map((a) => (typeof a === "string" ? a : JSON.stringify(a))).join(" ").slice(0, 800),
          ts: Date.now(),
        });
      } catch { /* ignore */ }
      origError(...args);
    };
    window.addEventListener("error", onError);
    window.addEventListener("unhandledrejection", onRej);
    const tick = window.setInterval(() => {
      if (!consoleBufRef.current.length) return;
      if (consoleBufRef.current.length > 500) {
        const dropped = consoleBufRef.current.length - 500;
        consoleBufRef.current.splice(0, dropped);
        consoleBufRef.current.unshift({
          type: "warning",
          source: "recorder",
          text: `console logs truncated: dropped ${dropped} old entries`,
          ts: Date.now(),
        });
      }
      const entries = consoleBufRef.current.splice(0, 50);
      send({ type: "console_log_upload", entries });
    }, 5000);
    return () => {
      window.removeEventListener("error", onError);
      window.removeEventListener("unhandledrejection", onRej);
      window.clearInterval(tick);
      console.error = origError;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function sendRaw(obj: unknown) {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(obj));
      return true;
    }
    // FC2 修复:不再每次 send 失败都弹 error(高频 click 触发会刷屏)
    // 统一在 ws.onclose 里通过 wsAliveRef 标记后,首次发现时弹一次提示
    if (wsAliveRef.current) {
      wsAliveRef.current = false;
      message.warning("录制连接已断开，正在停止后续操作");
    }
    return false;
  }

  function flushFlowMutationQueue() {
    if (flowMutationInFlightRef.current || !flowMutationQueueRef.current.length) return;
    const next = flowMutationQueueRef.current.shift();
    flowMutationInFlightRef.current = next;
    if (!sendRaw(next)) {
      flowMutationInFlightRef.current = null;
      flowMutationQueueRef.current = [];
      afterFlowSyncRef.current = null;
    }
  }

  function enqueueFlowMutation(obj: any) {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return sendRaw(obj);
    const operationId = obj.operation_id || `flow-${Date.now()}-${++flowMutationSeqRef.current}`;
    flowMutationQueueRef.current.push({ ...obj, operation_id: operationId });
    flushFlowMutationQueue();
    return true;
  }

  function finishQueuedFlowMutation(operationId?: string) {
    const active = flowMutationInFlightRef.current;
    if (!active) return;
    if (operationId && active.operation_id && operationId !== active.operation_id) return;
    flowMutationInFlightRef.current = null;
    flushFlowMutationQueue();
    if (!flowMutationInFlightRef.current && !flowMutationQueueRef.current.length && afterFlowSyncRef.current) {
      const callback = afterFlowSyncRef.current;
      afterFlowSyncRef.current = null;
      callback();
    }
  }

  function failQueuedFlowMutation(operationId?: string) {
    const active = flowMutationInFlightRef.current;
    if (operationId && active?.operation_id && operationId !== active.operation_id) return;
    flowMutationInFlightRef.current = null;
    flowMutationQueueRef.current = [];
    afterFlowSyncRef.current = null;
  }

  function runAfterFlowSync(callback: () => void) {
    if (!flowMutationInFlightRef.current && !flowMutationQueueRef.current.length) {
      callback();
      return;
    }
    afterFlowSyncRef.current = callback;
    message.info("正在同步最后一次工作台修改，完成后继续");
  }

  function send(obj: any) {
    if (obj?.type === "flow_update" || obj?.type === "flow_replace") return enqueueFlowMutation(obj);
    return sendRaw(obj);
  }

  function clearFrame() {
    latestFrameRef.current = null;
    renderedFrameSeqRef.current = 0;
    if (frameRafRef.current != null) {
      window.cancelAnimationFrame(frameRafRef.current);
      frameRafRef.current = null;
    }
    if (imgRef.current) imgRef.current.removeAttribute("src");
    setHasFrame(false);
    setFrameMeta({});
  }

  function queueFrame(seq: number, data: string, meta: RecorderFrameMeta = {}) {
    if (!data) return;
    const normalizedSeq = Number(seq || 0) > 0 ? Number(seq) : renderedFrameSeqRef.current + 1;
    latestFrameRef.current = { seq: normalizedSeq, src: `data:image/jpeg;base64,${data}`, meta };
    if (frameRafRef.current != null) return;
    frameRafRef.current = window.requestAnimationFrame(() => {
      frameRafRef.current = null;
      const latest = latestFrameRef.current;
      if (!latest || latest.seq <= renderedFrameSeqRef.current) return;
      renderedFrameSeqRef.current = latest.seq;
      if (imgRef.current) imgRef.current.src = latest.src;
      setFrameMeta((current) => ({ ...current, ...Object.fromEntries(Object.entries(latest.meta).filter(([, value]) => value != null)) }));
      if (!hasFrameRef.current) setHasFrame(true);
    });
  }

  function resetEditorState() {
    flowSpecRef.current = null;
    setFlowSpec(null);
    setCheckReport(null);
    setBindDraft({});
    setEditingLink({});
    setCapabilityAddValue({});
    setCapabilityAddUsage({});
    pendingCapabilityMembershipRef.current = [];
    setJsonDraft("");
    setJsonErr("");
    setLastServerJson("");
    setActiveFlowTab("abilities");
    flowMutationInFlightRef.current = null;
    flowMutationQueueRef.current = [];
    afterFlowSyncRef.current = null;
    clearFlowOperation();
  }

  function start() {
    if (!tenant) { message.error("请先到「创建 / 进入租户」"); return; }
    if (!startUrl.trim()) { message.error("请填页面地址 start_url"); return; }
    setErr(""); setResult(null); setSteps([]); setReqs([]); clearFrame(); setFields([]); setPicked({});
    setCands([]); setSelects({}); setIdentity({}); setStepSel({}); resetEditorState();
    setAction(newRecordingActionName());
    setReconnectedSessionNeedsCapture(false);
    setConnectionState("connecting");
    openRecorderConnection();
  }

  function reconnectRecorder() {
    if (!tenant || !startUrl.trim() || connectionState === "connecting" || connectionState === "reconnecting") return;
    setErr("");
    // WebSocket 断开时后端 RecordSession 也已结束。重连不能继续使用旧请求事实、
    // FlowSpec 或交互步骤；仅保留最后画面作为视觉参考，避免新旧会话事实混合发布。
    setSteps([]); setReqs([]); setFields([]); setPicked({}); setCands([]); setSelects({}); setIdentity({}); setStepSel({});
    setResult(null); resetEditorState();
    // 新 RecordSession 的帧序号会从 1 重新开始；保留 <img> 当前 src，但重置序号门槛，
    // 否则新会话的所有帧都会因小于旧序号而被丢弃。
    latestFrameRef.current = null;
    renderedFrameSeqRef.current = 0;
    if (frameRafRef.current != null) window.cancelAnimationFrame(frameRafRef.current);
    frameRafRef.current = null;
    setReconnectedSessionNeedsCapture(true);
    setConnectionState("reconnecting");
    openRecorderConnection();
  }

  function openRecorderConnection() {
    const intercept = recordingMode === "record_only";
    intentionalCloseRef.current = false;
    wsAliveRef.current = true;                                     // FC2 修复:每次 start 重置存活标志
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/onboarding/page/record`);
    wsRef.current = ws;
    ws.onopen = () => {
      if (wsRef.current !== ws) return;
      send({
        type: "start", tenant, subsystem, start_url: startUrl.trim(),
        base_url: baseUrl.trim() || undefined,
        storage_state: storageState.trim() || undefined,
        intercept,
      });
    };
    ws.onmessage = (ev) => {
      if (wsRef.current !== ws) return;
      let m: any; try { m = JSON.parse(ev.data); } catch { return; }
      if (m.type === "started") {
        const serverAction = m.action ?? m.action_name;
        if (typeof serverAction === "string" && /^[a-zA-Z][a-zA-Z0-9_]*$/.test(serverAction)) setAction(serverAction);
        setPhase("recording");
        setConnectionState("connected");
      }
      else if (m.type === "frame") {
        queueFrame(Number(m.seq || 0), m.data, frameMetaFromMessage(m));
        // A fresh frame proves the replacement RecordSession is active. This also keeps
        // GET-only recordings analyzable after reconnect, where no write callback exists.
        setReconnectedSessionNeedsCapture(false);
      }
      else if (m.type === "step") setSteps((s) => {
        const st = m.step;
        const last = s[s.length - 1];
        // FH1 修复:覆盖规则扩展 —— 同 locator + 同 op 时覆盖(避免连续 click 同一按钮记成多步);submit 后任意步骤都不再覆盖
        if (
          last &&
          last.locator === st.locator &&
          last.op === st.op &&
          last.op !== "submit" &&
          (st.op === "fill" || st.op === "select" || st.op === "pick" || st.op === "click")
        ) {
          return [...s.slice(0, -1), st];
        }
        return [...s, st];
      });
      else if (m.type === "request") {
        setReqs((r) => [...r, m.request].slice(-40));
        // request_fields 要到 finalize 分析后才会产生，因此重连门禁必须由新会话的实时请求解锁。
        setReconnectedSessionNeedsCapture(false);
      }
      else if (m.type === "request_fields") {
        setReconnectedSessionNeedsCapture(false);
        const fs: RecField[] = m.fields || [];
        const selMap: Record<string, RecSelect> = {};
        (m.selects || []).forEach((s: RecSelect) => { selMap[s.path] = s; });
        const idMap: Record<string, RecIdentity> = {};
        (m.identity || []).forEach((i: RecIdentity) => { idMap[i.path] = i; });
        setSelects(selMap); setIdentity(idMap); setFields(fs);
        const pk: Record<string, { on: boolean; name: string }> = {};
        fs.forEach((f) => {
          const on = idMap[f.path] ? false : (selMap[f.path] ? true : !!f.suggest_param);
          pk[f.path] = { on, name: f.suggest_name || f.key };
        });
        setPicked(pk);
        setReqMeta({ method: m.method, url: m.url });
        setCands(m.candidates || []);
        setChosenIdx(m.chosen_idx ?? 0);
        setStepSel(Object.fromEntries((m.suggested_steps || []).map((i: number) => [i, true])));
        setPhase("recording");
        message.success("抓到提交请求，请核对字段和流程");
      }
      else if (m.type === "flow_spec" || m.type === "flow_spec_updated") {
        // 发布请求可能与最后一次字段更新响应交错到达。普通更新不能把发布中的
        // loading/状态提前重置，否则用户看到按钮闪退但后端仍在发布。
        if (phaseRef.current !== "publishing") setPhase("recording");
        const fs = m.full_spec || m.flow_spec;
        if (fs) {
          acceptFlowSpec(fs);
          setLastServerJson(JSON.stringify(fs));
          finishFlowOperation(fs.meta?.recording_pi_loop, m.operation, m.operation_id);
        }
        if (m.check_report) setCheckReport(m.check_report);
        if (m.operation_report) {
          const report = m.operation_report as FlowOperationReport;
          setLastOperationReport(report);
          if (report.changed) message.success(report.summary || "流程编排已更新");
          else if (report.model_errors?.length) message.error(report.summary || "模型修复失败");
          else message.info(report.summary || "检查完成，没有可自动修改的内容");
        }
        if (m.operation === "flow_update" || m.operation === "flow_replace") finishQueuedFlowMutation(m.operation_id);
      }
      else if (m.type === "step_names") {
        setNamingBusy(false);
        if (m.full_spec) { acceptFlowSpec(m.full_spec); setLastServerJson(JSON.stringify(m.full_spec)); }
        if (m.check_report) setCheckReport(m.check_report);
        message.success("步骤名称已刷新");
      }
      else if (m.type === "business_description") {
        setDescBusy(false);
        if (m.full_spec) { acceptFlowSpec(m.full_spec); setLastServerJson(JSON.stringify(m.full_spec)); }
        else if (m.description && flowSpec) {
          const next = { ...flowSpec, business_description: m.description };
          flowSpecRef.current = next;
          setFlowSpec(next);
        }
        if (m.check_report) setCheckReport(m.check_report);
        message.success("业务说明已生成");
      }
      else if (m.type === "input_error") {
        const now = Date.now();
        if (now - lastInputErrorNoticeRef.current >= 2000) {
          lastInputErrorNoticeRef.current = now;
          message.warning(m.detail || "本次页面操作未执行，请稍后重试");
        }
      }
      else if (m.type === "result") {
        publishOperationRef.current = null;
        finalizeOperationRef.current = null;
        if (m.report?.full_spec) {
          acceptFlowSpec(m.report.full_spec);
          setLastServerJson(JSON.stringify(m.report.full_spec));
        }
        setResult(m.report); setPhase("recording");
        if (m.report?.check_report) setCheckReport(m.report.check_report);
        if (m.report?.ok) {
          setFields([]); setPicked({}); setCands([]); setSelects({}); setIdentity({}); setStepSel({});
        }
      }
      else if (m.type === "error") {
        const detail = m.detail || "录制出错";
        setNamingBusy(false); setDescBusy(false); clearFlowOperation();
        if (m.full_spec) {
          acceptFlowSpec(m.full_spec);
          setLastServerJson(JSON.stringify(m.full_spec));
        }
        if (m.check_report) setCheckReport(m.check_report);
        if (m.operation === "flow_update" || m.operation === "flow_replace") failQueuedFlowMutation(m.operation_id);
        if (detail.includes("step not found") || detail.includes("link not found")) {
          message.warning("流程已变更，正在同步最新版本");
          send({ type: "refresh_flow_spec" });
        } else {
          message.error(detail);
          setErr(detail);
        }
      }
    };
    ws.onerror = () => {
      if (wsRef.current === ws) setErr("WebSocket 连接失败，当前画面和已录步骤已保留");
    };
    ws.onclose = () => {
      if (wsRef.current !== ws) return;
      wsRef.current = null;
      wsAliveRef.current = false;                                 // FC2 修复:WS 关闭,send 会自动避免刷屏
      pointerGestureRef.current = null;
      pendingPointerMoveRef.current = null;
      if (pointerMoveRafRef.current != null) window.cancelAnimationFrame(pointerMoveRafRef.current);
      pointerMoveRafRef.current = null;
      if (pendingClickRef.current) window.clearTimeout(pendingClickRef.current.timer);
      pendingClickRef.current = null;
      finalizeOperationRef.current = null;
      publishOperationRef.current = null;
      setNamingBusy(false);
      setDescBusy(false);
      clearFlowOperation();
      flowMutationInFlightRef.current = null;
      flowMutationQueueRef.current = [];
      afterFlowSyncRef.current = null;
      if (intentionalCloseRef.current) {
        setConnectionState("idle");
        return;
      }
      if (phaseRef.current === "publishing") setPhase("recording");
      setConnectionState("disconnected");
      setErr("录制连接已断开，已保留最后画面、步骤和编辑内容");
    };
  }

  function pointerButton(button: number) {
    if (button === 1) return "middle";
    if (button === 2) return "right";
    return "left";
  }
  function normalizedPoint(clientX: number, clientY: number) {
    const img = imgRef.current;
    if (!img) return null;
    const rect = img.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    return {
      nx: Math.max(0, Math.min(1, (clientX - rect.left) / rect.width)),
      ny: Math.max(0, Math.min(1, (clientY - rect.top) / rect.height)),
    };
  }
  function sendPendingPointerMove() {
    pointerMoveRafRef.current = null;
    const event = pendingPointerMoveRef.current;
    pendingPointerMoveRef.current = null;
    if (event) send({ type: "input", event });
  }
  function queuePointerMove(event: Record<string, unknown>) {
    pendingPointerMoveRef.current = event;
    if (pointerMoveRafRef.current != null) return;
    pointerMoveRafRef.current = window.requestAnimationFrame(sendPendingPointerMove);
  }
  function flushPendingRecorderClick() {
    const pending = pendingClickRef.current;
    if (!pending) return;
    window.clearTimeout(pending.timer);
    pendingClickRef.current = null;
    send({
      type: "input",
      event: {
        kind: "click", nx: pending.nx, ny: pending.ny, button: pending.button,
        pointer_type: pending.pointerType,
      },
    });
  }
  function onImgPointerDown(e: React.PointerEvent<HTMLImageElement>) {
    if (connectionState !== "connected" || e.button < 0) return;
    const point = normalizedPoint(e.clientX, e.clientY);
    if (!point) return;
    e.preventDefault();
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* pointer capture may be unavailable */ }
    pointerGestureRef.current = {
      pointerId: e.pointerId,
      ...point,
      clientX: e.clientX,
      clientY: e.clientY,
      button: pointerButton(e.button),
      buttons: e.buttons,
      pointerType: e.pointerType || "mouse",
      dragging: false,
    };
    kbRef.current?.focus({ preventScroll: true });
  }
  function onImgPointerMove(e: React.PointerEvent<HTMLImageElement>) {
    if (connectionState !== "connected") return;
    const point = normalizedPoint(e.clientX, e.clientY);
    if (!point) return;
    const gesture = pointerGestureRef.current;
    if (gesture?.pointerId === e.pointerId && !gesture.dragging) {
      const distance = Math.hypot(e.clientX - gesture.clientX, e.clientY - gesture.clientY);
      if (distance >= 5) {
        gesture.dragging = true;
        send({
          type: "input",
          event: {
            kind: "pointer_down", nx: gesture.nx, ny: gesture.ny, button: gesture.button,
            buttons: gesture.buttons, pointer_type: gesture.pointerType,
          },
        });
      }
    }
    queuePointerMove({
      kind: "pointer_move", ...point, buttons: e.buttons, pointer_type: e.pointerType || "mouse",
    });
    if (gesture?.dragging) e.preventDefault();
  }
  function dispatchRecorderClick(
    point: { nx: number; ny: number },
    e: React.PointerEvent<HTMLImageElement>,
    button: string,
  ) {
    const previous = pendingClickRef.current;
    const isDoubleClick = button === "left" && previous?.button === button
      && Math.hypot(e.clientX - previous.clientX, e.clientY - previous.clientY) <= 8;
    if (isDoubleClick && previous) {
      window.clearTimeout(previous.timer);
      pendingClickRef.current = null;
      send({
        type: "input",
        event: { kind: "dblclick", ...point, button, pointer_type: e.pointerType || "mouse" },
      });
      return;
    }
    if (previous) {
      flushPendingRecorderClick();
    }
    const pending = {
      timer: 0,
      ...point,
      clientX: e.clientX,
      clientY: e.clientY,
      button,
      pointerType: e.pointerType || "mouse",
    };
    pending.timer = window.setTimeout(() => {
      if (pendingClickRef.current !== pending) return;
      pendingClickRef.current = null;
      send({ type: "input", event: { kind: "click", ...point, button, pointer_type: pending.pointerType } });
    }, button === "left" ? 250 : 0);
    pendingClickRef.current = pending;
  }
  function onImgPointerUp(e: React.PointerEvent<HTMLImageElement>) {
    const gesture = pointerGestureRef.current;
    if (!gesture || gesture.pointerId !== e.pointerId) return;
    pointerGestureRef.current = null;
    const point = normalizedPoint(e.clientX, e.clientY) || { nx: gesture.nx, ny: gesture.ny };
    e.preventDefault();
    try { e.currentTarget.releasePointerCapture(e.pointerId); } catch { /* already released */ }
    if (gesture.dragging) {
      if (pointerMoveRafRef.current != null) {
        window.cancelAnimationFrame(pointerMoveRafRef.current);
        sendPendingPointerMove();
      }
      send({
        type: "input",
        event: {
          kind: "pointer_up", ...point, button: gesture.button, buttons: e.buttons,
          pointer_type: gesture.pointerType,
        },
      });
    } else {
      dispatchRecorderClick(point, e, gesture.button);
    }
  }
  function onImgPointerCancel(e: React.PointerEvent<HTMLImageElement>) {
    const gesture = pointerGestureRef.current;
    if (!gesture || gesture.pointerId !== e.pointerId) return;
    pointerGestureRef.current = null;
    pendingPointerMoveRef.current = null;
    if (pointerMoveRafRef.current != null) window.cancelAnimationFrame(pointerMoveRafRef.current);
    pointerMoveRafRef.current = null;
    if (gesture.dragging) {
      const point = normalizedPoint(e.clientX, e.clientY) || { nx: gesture.nx, ny: gesture.ny };
      send({ type: "input", event: { kind: "pointer_up", ...point, button: gesture.button, buttons: 0, pointer_type: gesture.pointerType } });
    }
  }
  function onImgWheel(e: React.WheelEvent<HTMLImageElement>) {
    if (connectionState !== "connected") return;
    const point = normalizedPoint(e.clientX, e.clientY);
    e.preventDefault();
    flushPendingRecorderClick();
    send({ type: "input", event: { kind: "scroll", dy: e.deltaY, dx: e.deltaX, ...(point || {}) } });
  }
  function relayKb(el: HTMLInputElement) {
    const v = el.value;
    if (v) {
      flushPendingRecorderClick();
      send({ type: "input", event: { kind: "text", text: v } });
      el.value = "";
    }
  }
  function onKbInput(e: React.FormEvent<HTMLInputElement>) {
    const ne = e.nativeEvent as { isComposing?: boolean };
    if (ne.isComposing || isComposingRef.current) return;         // FH2:原生 + ref 双保险
    relayKb(e.currentTarget);
  }
  function onKbCompositionStart(_e: React.CompositionEvent<HTMLInputElement>) {
    // FH2 修复:compositionStart 显式标记 isComposing=true;某些浏览器在 CompositionStart→Input 之间 isComposing
    // 可能短暂为 false,导致 onKbInput 误发未拼写完的中间字符(显示"拼字"而不是中文)→ ref 守门
    flushPendingRecorderClick();
    isComposingRef.current = true;
  }
  function onKbCompositionUpdate(_e: React.CompositionEvent<HTMLInputElement>) {
    isComposingRef.current = true;
  }
  function onKbCompositionEnd(e: React.CompositionEvent<HTMLInputElement>) {
    isComposingRef.current = false;
    relayKb(e.currentTarget);
  }
  function onKbKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    const key = recorderKeyName(e);
    if (key) {
      flushPendingRecorderClick();
      send({ type: "input", event: { kind: "key", key } });
      e.preventDefault();
    }
  }
  function onKbPaste(e: React.ClipboardEvent<HTMLInputElement>) {
    const text = e.clipboardData.getData("text");
    if (text) {
      flushPendingRecorderClick();
      send({ type: "input", event: { kind: "text", text } });
      e.preventDefault();
      e.currentTarget.value = "";
    }
  }

  function resetFromHere() {
    send({ type: "reset" });
    setSteps([]); setResult(null); resetEditorState();
    message.success("已清空，从现在起只录业务步骤");
  }
  function finalize() {
    if (finalizeOperationRef.current) return;
    if (connectionState !== "connected" || reconnectedSessionNeedsCapture) {
      message.warning("请先在当前录制会话中重新触发并抓取提交请求");
      return;
    }
    if (!action.trim() || badAction(action.trim())) return;
    if (!hasFrame && !steps.length && !reqs.length) { message.error("还没有可分析的页面画面或请求"); return; }
    const operationId = newCostlyOperationId("finalize");
    finalizeOperationRef.current = operationId;
    setResult(null); setPhase("publishing");
    if (!send({ type: "finalize", operation_id: operationId, action: action.trim(), title: title.trim(), success_marker: null, steps })) {
      finalizeOperationRef.current = null;
      setPhase("recording");
    }
  }
  function chooseRequest(idx: number) { setChosenIdx(idx); send({ type: "choose_request", idx }); }
  function toggleField(path: string, on: boolean) { setPicked((p) => ({ ...p, [path]: { ...p[path], on } })); }
  function renameField(path: string, name: string) { setPicked((p) => ({ ...p, [path]: { ...p[path], name } })); }
  function badAction(a: string) {
    if (!/^[a-zA-Z][a-zA-Z0-9_]*$/.test(a)) { message.error("动作名请用英文标识"); return true; }
    return false;
  }
  function payload() {
    const param_map: Record<string, string> = {};
    fields.forEach((f) => { const p = picked[f.path]; if (p?.on && p.name.trim()) param_map[f.path] = p.name.trim(); });
    const selList = Object.values(selects).filter((s) => param_map[s.path]);
    const idList = Object.values(identity);
    const checked = cands.filter((c) => stepSel[c.idx]).map((c) => c.idx);
    const step_idxs = checked.length >= 2 ? [...checked.filter((i) => i !== chosenIdx).sort((a, b) => a - b), chosenIdx] : [];
    return { param_map, selList, idList, step_idxs };
  }
  function publishRequest() {
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
    if (!action.trim() || badAction(action.trim())) return;
    runAfterFlowSync(performPublishRequest);
  }
  function performPublishRequest() {
    if (publishOperationRef.current) return;
    const { param_map, selList, idList, step_idxs } = payload();
    const currentSpec = flowSpecRef.current || flowSpec;
    if (!currentSpec) { message.error("请先生成 FlowSpec 后再发布"); return; }
    const publishTitle = title.trim() || preferredSkillTitle(currentSpec);
    const operationId = newCostlyOperationId("publish");
    publishOperationRef.current = operationId;
    setResult(null); setPhase("publishing");
    if (!send({ type: "publish_request", operation_id: operationId, action: action.trim(), title: publishTitle,
      param_map, selects: selList, identity: idList, step_idxs, use_flow_spec: true, flow_spec: currentSpec,
      expected_fingerprint: currentSpec.meta?.current_fingerprint })) {
      publishOperationRef.current = null;
      setPhase("recording");
      setResult({ ok: false, reason: "录制连接已断开，发布请求未发送" });
    }
  }
  function stopAll() {
    intentionalCloseRef.current = true;
    const ws = wsRef.current;
    const stopSent = send({ type: "stop" });
    // Let the server flush, acknowledge, and initiate the WebSocket close.  Closing
    // here immediately races the Vite proxy against queued recording frames.
    if (!stopSent) ws?.close(1000, "recording stopped");
    else if (ws) window.setTimeout(() => {
      if (wsRef.current === ws && ws.readyState < WebSocket.CLOSING) {
        ws.close(1000, "recording stop timeout");
      }
    }, 2000);
    setConnectionState("idle");
    setPhase("idle"); setResult(null); setSteps([]); clearFrame(); setFields([]); setPicked({});
    setCands([]); setSelects({}); setIdentity({}); setStepSel({}); resetEditorState();
  }

  function sendReplace(next: FlowSpecData) {
    flowSpecRef.current = next;
    setFlowSpec(next);
    send({ type: "flow_replace", flow_spec: next });
  }
  function updateFlowField(k: string, v: any) { send({ type: "flow_update", edits: [{ op: "update_flow", field: k, value: v }] }); }
  function updateStep(stepId: string, field: string, value: any) {
    patchLocalStep(stepId, { [field]: value });
    send({ type: "flow_update", edits: [{ op: "update", step_id: stepId, field, value }] });
  }
  function paramDraftKey(stepId: string, p: FlowParam) {
    return `${stepId}:${p.path || ""}:${p.key || ""}:${p.label || ""}`;
  }
  function paramEdit(stepId: string, p: FlowParam, field: string, value: any) {
    return {
      op: "update",
      step_id: stepId,
      param_path: p.path || p.key || p.label,
      param_key: p.key,
      param_label: p.label || p.key,
      field,
      value,
    };
  }
  function paramRemoveEdit(stepId: string, p: FlowParam) {
    return {
      op: "remove",
      step_id: stepId,
      param_path: p.path || p.key || p.label,
      param_key: p.key,
      param_label: p.label || p.key,
    };
  }
  function removeParam(stepId: string, p: FlowParam) {
    const edit = paramRemoveEdit(stepId, p);
    if (!send({ type: "flow_update", edits: [edit] })) return;

    // 删除立即反映到页面；服务端失败时会通过 full_spec 回滚到权威版本。
    // 同时清理依赖和选择器，避免字段卡片消失后仍残留不可见引用。
    const current = flowSpecRef.current;
    if (!current) return;
    const removedPath = stripBodyPrefix(p.path || "");
    const next: FlowSpecData = {
      ...current,
      steps: (current.steps || []).map((step) => {
        if (step.step_id !== stepId) return step;
        const sampleInputs = { ...(step.sample_inputs || {}) };
        delete sampleInputs[p.key];
        return {
          ...step,
          params: (step.params || []).filter((candidate) => !paramMatches(candidate, p)),
          selects: (step.selects || []).filter((binding) => {
            const bindingPath = stripBodyPrefix(binding.path || binding.id_path || "");
            return bindingPath !== removedPath && binding.param !== p.key;
          }),
          identity: (step.identity || []).filter((binding) =>
            stripBodyPrefix(String(binding?.path || "")) !== removedPath),
          sample_inputs: sampleInputs,
        };
      }),
      links: (current.links || []).filter((link) =>
        !(link.target_step_id === stepId && stripBodyPrefix(link.target_path || "") === removedPath)),
      capabilities: (current.capabilities || []).map((cap) => capabilityActualStepIds(cap).includes(stepId)
        ? { ...cap, confirmed: false }
        : cap),
    };
    flowSpecRef.current = next;
    setFlowSpec(next);
    setCheckReport(null);
  }
  function targetParamEdit(stepId: string, target: Record<string, any>, field: string, value: any) {
    const path = target.path || target.target_path || target.param_path || "";
    const key = target.key || target.param_name || target.current_guess || "";
    return {
      op: "update",
      step_id: stepId,
      param_path: path || key,
      param_key: key,
      param_label: target.label || key,
      field,
      value,
    };
  }
  function paramMatches(a: FlowParam, b: FlowParam) {
    const ap = stripBodyPrefix(a.path || "");
    const bp = stripBodyPrefix(b.path || "");
    if (ap && bp && ap === bp) return true;
    if (a.key && b.key && a.key === b.key) return true;
    if (a.label && b.label && a.label === b.label) return true;
    return false;
  }
  function patchLocalParam(stepId: string, p: FlowParam, updates: Record<string, any>) {
    const base = flowSpecRef.current;
    if (!base) return;
    const next: FlowSpecData = {
      ...base,
      steps: (base.steps || []).map((step) => {
        if (step.step_id !== stepId) return step;
        const oldKey = p.key;
        const newParams = (step.params || []).map((param) => {
          if (!paramMatches(param, p)) return param;
          const nextParam = { ...param, ...updates };
          if (updates.key != null && (!updates.label || nextParam.label === oldKey || !nextParam.label)) {
            nextParam.label = updates.key;
          }
          return nextParam;
        });
        const newSelects = (step.selects || []).map((sel) => {
          const sameParam = (sel.param && oldKey && sel.param === oldKey) || stripBodyPrefix(sel.path || "") === stripBodyPrefix(p.path || "");
          if (!sameParam) return sel;
          return {
            ...sel,
            ...(updates.key != null ? { param: updates.key } : {}),
            ...(updates.path != null ? { path: updates.path, id_path: sel.id_path === p.path ? updates.path : sel.id_path } : {}),
          };
        });
        const newSampleInputs = { ...(step.sample_inputs || {}) };
        if (updates.key != null && oldKey && oldKey in newSampleInputs) {
          newSampleInputs[updates.key] = newSampleInputs[oldKey];
          delete newSampleInputs[oldKey];
        }
        return { ...step, params: newParams, selects: newSelects, sample_inputs: newSampleInputs };
      }),
      capabilities: (base.capabilities || []).map((cap) => capabilityActualStepIds(cap).includes(stepId)
        ? { ...cap, confirmed: false }
        : cap),
    };
    flowSpecRef.current = next;
    setFlowSpec(next);
  }
  function patchLocalParams(stepId: string, p: FlowParam, updates: Record<string, any>) {
    patchLocalParam(stepId, p, updates);
  }
  function patchLocalStep(stepId: string, updates: Partial<FlowStepData>) {
    const base = flowSpecRef.current;
    if (!base) return;
    const next: FlowSpecData = {
      ...base,
      steps: (base.steps || []).map((step) => step.step_id === stepId ? { ...step, ...updates } : step),
      capabilities: (base.capabilities || []).map((cap) => capabilityActualStepIds(cap).includes(stepId)
        ? { ...cap, confirmed: false }
        : cap),
    };
    flowSpecRef.current = next;
    setFlowSpec(next);
  }
  function patchLocalCapability(idx: number, updates: Partial<FlowCapabilityData>, invalidateConfirmation = true) {
    const base = flowSpecRef.current;
    if (!base) return;
    const next: FlowSpecData = {
      ...base,
      capabilities: (base.capabilities || []).map((cap, capIdx) => capIdx === idx
        ? { ...cap, ...updates, ...(invalidateConfirmation ? { confirmed: false } : {}) }
        : cap),
    };
    flowSpecRef.current = next;
    setFlowSpec(next);
  }
  function updateParam(stepId: string, p: FlowParam, field: string, value: any) {
    patchLocalParam(stepId, p, { [field]: value });
    send({ type: "flow_update", edits: [paramEdit(stepId, p, field, value)] });
  }
  function updateParamType(step: FlowStepData, p: FlowParam, value: string) {
    const currentStep = flowSpecRef.current?.steps.find((item) => item.step_id === step.step_id) || step;
    const currentParam = currentStep.params.find((item) => paramMatches(item, p)) || p;
    const wasEnum = currentParam.type === "enum" || currentParam.type === "list-enum";
    const isEnum = value === "enum" || value === "list-enum";
    if (currentParam.source_kind === "api_option") {
      // 接口候选描述值从哪里来，不约束请求字段的数据类型。
      patchLocalParam(step.step_id, currentParam, { type: value });
      send({ type: "flow_update", edits: [paramEdit(step.step_id, currentParam, "type", value)] });
      return;
    }
    if (!wasEnum || isEnum) {
      patchLocalParam(step.step_id, currentParam, { type: value });
      send({ type: "flow_update", edits: [paramEdit(step.step_id, currentParam, "type", value)] });
      return;
    }

    const enumSource = ["api_option", "manual_enum", "page_enum", "form_option", "static_enum"]
      .includes(currentParam.source_kind || "");
    const sourceKind = enumSource
      ? defaultSourceForCategory(currentParam.category || "user_param")
      : (currentParam.source_kind || "unknown");
    const source = enumSource ? sourceDescriptor(sourceKind, currentParam) : currentParam.source;
    const nextSelects = (currentStep.selects || []).filter((sel) =>
      !(sel.path === currentParam.path || (!sel.path && sel.param === currentParam.key) || sel.param === currentParam.key)
    );
    const updates: Record<string, any> = {
      type: value,
      enum_options: null,
      enum_value_map: null,
      ...(enumSource ? {
        source_kind: sourceKind,
        source,
        need_human_confirm: sourceNeedsConfiguration(sourceKind, source as any),
      } : {}),
    };
    patchLocalParam(step.step_id, currentParam, updates);
    patchLocalStep(step.step_id, { selects: nextSelects });
    const edits: any[] = [
      paramEdit(step.step_id, currentParam, "type", value),
      paramEdit(step.step_id, currentParam, "enum_options", null),
      paramEdit(step.step_id, currentParam, "enum_value_map", null),
      { op: "update", step_id: step.step_id, field: "selects", value: nextSelects },
    ];
    if (enumSource) {
      edits.push(
        paramEdit(step.step_id, currentParam, "source_kind", sourceKind),
        paramEdit(step.step_id, currentParam, "source", source),
        paramEdit(step.step_id, currentParam, "need_human_confirm", sourceNeedsConfiguration(sourceKind, source as any)),
      );
    }
    send({ type: "flow_update", edits });
  }
  function updateParamCategory(stepId: string, p: FlowParam, category: string) {
    const currentSourceKind = normalizeSourceKindForUi(p.source_kind);
    const allowed = sourceOptionsForCategory(category).some((o) => o.value === currentSourceKind);
    const sourceKind = allowed ? currentSourceKind : defaultSourceForCategory(category);
    const keepExistingSource = allowed && !!p.source && (
      sourceKind !== "previous_response"
      || !!((p.source as any)?.step_id || (p.source as any)?.response_path)
    );
    const source = keepExistingSource ? p.source : sourceDescriptor(sourceKind, p, p.source as any);
    const patch: Record<string, any> = {
      category,
      source_kind: sourceKind,
      source,
      exposed_to_user: category === "user_param",
      need_human_confirm: sourceNeedsConfiguration(sourceKind, source as any),
    };
    patchLocalParams(stepId, p, patch);
    const edits: any[] = (category === "runtime_var" ? [] : ((flowSpecRef.current || flowSpec)?.links || []))
      .filter((l) => l.target_step_id === stepId && stripBodyPrefix(l.target_path) === stripBodyPrefix(p.path))
      .map((l) => ({ op: "remove", link_id: l.link_id, record_rejection: true }));
    edits.push(
      paramEdit(stepId, p, "category", category),
      paramEdit(stepId, p, "source_kind", sourceKind),
      paramEdit(stepId, p, "source", source),
      paramEdit(stepId, p, "exposed_to_user", category === "user_param"),
      paramEdit(stepId, p, "need_human_confirm", sourceNeedsConfiguration(sourceKind, source as any)),
    );
    send({ type: "flow_update", edits });
  }
  function updateParamSourceKind(stepId: string, p: FlowParam, sourceKind: string) {
    const category = p.category || "user_param";
    const currentSource = p.source as any;
    const nextSource = sourceDescriptor(sourceKind, p, currentSource);
    const needsConfiguration = sourceNeedsConfiguration(sourceKind, nextSource);
    // 只有离开“上游响应”时才移除原依赖；重新选择上游响应不能先把现有绑定删掉。
    const edits: any[] = sourceKind === "previous_response" ? [] : (flowSpec?.links || [])
      .filter((l) => l.target_step_id === stepId && stripBodyPrefix(l.target_path) === stripBodyPrefix(p.path))
      .map((l) => ({ op: "remove", link_id: l.link_id, reset_target: false }));
    edits.push(
      paramEdit(stepId, p, "source_kind", sourceKind),
      paramEdit(stepId, p, "source", nextSource),
      paramEdit(stepId, p, "exposed_to_user", category === "user_param"),
      paramEdit(stepId, p, "need_human_confirm", needsConfiguration),
      paramEdit(stepId, p, "editable", true),
    );
    patchLocalParams(stepId, p, {
      source_kind: sourceKind,
      source: nextSource,
      exposed_to_user: category === "user_param",
      need_human_confirm: needsConfiguration,
      editable: true,
    });
    send({ type: "flow_update", edits });
    if (sourceKind === "previous_response") {
      const key = paramDraftKey(stepId, p);
      setBindDraft((d) => ({
        ...d,
        [key]: d[key] || { source_step_id: (p.source as any)?.step_id || "", source_path: (p.source as any)?.response_path || "" },
      }));
      message.info("已在下方“绑定上游响应”里指定来源步骤和响应字段");
    }
  }
  function updateRuntimeSourceDetail(stepId: string, p: FlowParam, patch: Record<string, any>) {
    const source = { ...(p.source || {}), ...patch, kind: p.source_kind, path: p.path, manual: true };
    const needsConfiguration = sourceNeedsConfiguration(p.source_kind || "unknown", source);
    patchLocalParams(stepId, p, { source, need_human_confirm: needsConfiguration });
    send({ type: "flow_update", edits: [
      paramEdit(stepId, p, "source", source),
      paramEdit(stepId, p, "need_human_confirm", needsConfiguration),
    ] });
  }
  function moveStep(idx: number, dir: -1 | 1) {
    const current = flowSpecRef.current;
    if (!current) return;
    const ids = current.steps.map((s) => s.step_id);
    const j = idx + dir;
    if (j < 0 || j >= ids.length) return;
    [ids[idx], ids[j]] = [ids[j], ids[idx]];
    const byId = Object.fromEntries(current.steps.map((step) => [step.step_id, step]));
    const next = { ...current, steps: ids.map((id) => byId[id]) };
    flowSpecRef.current = next;
    setFlowSpec(next);
    send({ type: "flow_update", edits: [{ op: "reorder_steps", step_ids: ids }] });
  }
  function removeStepWithConfirm(step: FlowStepData) {
    if (!flowSpec) return;
    const links = flowSpec.links.filter((l) => l.source_step_id === step.step_id || l.target_step_id === step.step_id);
    Modal.confirm({
      title: `删除步骤 ${step.name || step.path}?`,
      content: links.length ? `该步骤关联 ${links.length} 条依赖，删除后会一并清理。` : "确认删除该步骤？",
      okText: "删除", okType: "danger", cancelText: "取消",
      onOk: () => {
        const ok = send({ type: "flow_update", edits: [{ op: "remove_step", step_id: step.step_id }] });
        if (!ok) return;
        const cur = flowSpecRef.current;
        if (cur) {
          const next = {
            ...cur,
            steps: cur.steps.filter((s) => s.step_id !== step.step_id),
            links: cur.links.filter((l) => l.source_step_id !== step.step_id && l.target_step_id !== step.step_id),
            capabilities: (cur.capabilities || []).map((cap) => {
              if (!capabilityActualStepIds(cap).includes(step.step_id)) return cap;
              return {
                ...cap,
                step_ids: (cap.step_ids || []).filter((stepId) => stepId !== step.step_id),
                confirmed: false,
              };
            }),
          };
          flowSpecRef.current = next;
          setFlowSpec(next);
        }
        message.success("已删除步骤，正在同步校验");
      },
    });
  }
  function resolveReview(reviewId: string, resolved = true) {
    send({ type: "flow_update", edits: [{ op: "resolve_review", review_id: reviewId, resolved }] });
  }
  function reviewSuggestionEdits(item: ReviewItemData) {
    const action = item.suggested_action || "";
    const tgt = item.target || {};
    const guess = item.current_guess || "";
    const edits: any[] = [];
    if (action === "confirm_link" && tgt.link_id) {
      edits.push({ op: "update", link_id: tgt.link_id, field: "confirmed", value: true });
    } else if ((action === "fix_or_remove_link" || action === "fix_link_source" || action === "fix_link_target") && tgt.link_id) {
      edits.push({ op: "remove", link_id: tgt.link_id });
    } else if (action === "confirm_request_role" && tgt.step_id) {
      edits.push({ op: "update", step_id: tgt.step_id, field: "role", value: guess || "business_write" });
    } else if (action === "hide_system_const" && tgt.step_id && tgt.path) {
      edits.push(
        targetParamEdit(tgt.step_id, tgt, "category", "system_const"),
        targetParamEdit(tgt.step_id, tgt, "exposed_to_user", false),
        { op: "resolve_review", review_id: item.id, resolved: true },
      );
    } else if (tgt.step_id && tgt.path && (action === "confirm_field_source" || action === "bind_runtime_source")) {
      const [cat, sourceKind] = guess.split("/");
      edits.push(
        targetParamEdit(tgt.step_id, tgt, "category", cat || "runtime_var"),
        ...(sourceKind ? [targetParamEdit(tgt.step_id, tgt, "source_kind", sourceKind)] : []),
        targetParamEdit(tgt.step_id, tgt, "need_human_confirm", false),
      );
    } else {
      edits.push({ op: "resolve_review", review_id: item.id, resolved: true });
    }
    if (!edits.some((e) => e.op === "resolve_review" && e.review_id === item.id)) {
      edits.push({ op: "resolve_review", review_id: item.id, resolved: true });
    }
    return edits;
  }
  function applyReviewSuggestion(item: ReviewItemData) {
    if (!flowSpec) return;
    const edits = reviewSuggestionEdits(item);
    send({ type: "flow_update", edits });
  }
  function applyLlmSuggestion(item: ReviewItemData, suggestion: NonNullable<ReviewItemData["llm_suggestions"]>[number]) {
    if (!flowSpec) return;
    const tgt = item.target || {};
    const targetStepId = suggestion.target_step_id || tgt.step_id;
    const targetPath = suggestion.target_path || tgt.path;
    if (!targetStepId || !targetPath) return;
    const edits: any[] = [];
    if (suggestion.action === "bind_previous_response" && suggestion.source_step_id && suggestion.source_path) {
      edits.push({
        op: "add",
        step_id: suggestion.source_step_id,
        link: {
          source_step_id: suggestion.source_step_id,
          source_path: suggestion.source_path,
          target_step_id: targetStepId,
          target_path: targetPath,
          confirmed: true,
          ...(typeof suggestion.confidence === "number" ? { confidence: suggestion.confidence } : {}),
          reason: suggestion.reason || "LLM 推荐并由用户确认的上游响应依赖",
        },
      });
    } else if (suggestion.action === "set_runtime_source" && suggestion.source_kind) {
      if (suggestion.source_kind === "request_header" || suggestion.source_kind === "unknown") {
        message.warning("该建议仍缺少可执行来源，请在能力卡片内补充");
        setActiveFlowTab("abilities");
        return;
      }
      const target = { ...tgt, path: targetPath };
      edits.push(
        targetParamEdit(targetStepId, target, "category", "runtime_var"),
        targetParamEdit(targetStepId, target, "source_kind", suggestion.source_kind),
        targetParamEdit(targetStepId, target, "source", { kind: suggestion.source_kind, path: targetPath }),
        targetParamEdit(targetStepId, target, "need_human_confirm", false),
      );
    } else {
      message.info("该项仍需要人工判断，请在能力卡片内确认");
      setActiveFlowTab("abilities");
      return;
    }
    edits.push({ op: "resolve_review", review_id: item.id, resolved: true });
    send({ type: "flow_update", edits });
  }
  function refreshLlmRecommendations() {
    if (!flowSpec) return;
    autoFixFlow();
  }
  function requiresManualSourceBinding(item: ReviewItemData) {
    return item.suggested_action === "bind_runtime_source" && /\/unknown$/.test(item.current_guess || "");
  }
  function bulkReview(mode: "accept" | "ignore") {
    if (!flowSpec || !reviewItems.length) return;
    const title = mode === "accept" ? "全部采纳当前判断？" : "全部忽略待确认项？";
    const content = mode === "accept"
      ? "会按系统当前判断批量确认字段分类、接口保留和依赖关系。建议先确认没有高风险项。"
      : "会把当前待确认项标记为已处理，不改变 FlowSpec 内容。";
    Modal.confirm({
      title,
      content,
      okText: mode === "accept" ? "全部采纳" : "全部忽略",
      cancelText: "取消",
      onOk: () => {
        const rawEdits = mode === "accept"
          ? reviewItems.filter((item) => !requiresManualSourceBinding(item)).flatMap((item) => reviewSuggestionEdits(item))
          : reviewItems.map((item) => ({ op: "resolve_review", review_id: item.id, resolved: true }));
        if (mode === "accept" && !rawEdits.length) {
          message.warning("当前高风险项需要先在能力卡片内手动绑定来源");
          setActiveFlowTab("abilities");
          return;
        }
        const seen = new Set<string>();
        const edits = rawEdits.filter((edit) => {
          const key = JSON.stringify(edit);
          if (seen.has(key)) return false;
          seen.add(key);
          return true;
        });
        if (edits.length) send({ type: "flow_update", edits });
      },
    });
  }
  function addStep() {
    if (!flowSpec) return;
    if (newStepRequestKey) {
      const req = findCapturedRequest(flowSpec, newStepRequestKey);
      if (!req) { message.warning("没有找到选中的捕获接口"); return; }
      send({ type: "flow_update", edits: [{ op: "add_request_step", request_index: req.request_index, request_id: req.request_id }] });
      setAddingStep(false);
      setNewStepRequestKey("");
      return;
    }
    const draft = { ...newStep, path: newStep.path.trim(), name: newStep.name.trim() };
    if (!draft.path || !draft.path.startsWith("/")) { message.warning("接口 path 需要以 / 开头"); return; }
    const step: FlowStepData = {
      step_id: "new_" + Math.random().toString(36).slice(2, 10),
      name: draft.name || fallbackStepName(draft.method, draft.path),
      method: draft.method, url: draft.path, path: draft.path, risk_level: draft.risk_level,
      params: [], selects: [], identity: [], source_meta: { role: draft.role, manual: true },
      sample_inputs: {}, headers: {}, body_source: "",
    };
    sendReplace({ ...flowSpec, steps: [...flowSpec.steps, step] });
    setAddingStep(false);
    setNewStep({ method: "POST", path: "/api/", name: "", risk_level: "L3", role: "business_write" });
    setNewStepRequestKey("");
  }
  function addCapturedRequestToFields(req?: RequestGraphEntry) {
    if (!req) { message.warning("请选择捕获接口"); return; }
    send({ type: "flow_update", edits: [{ op: "add_request_step", request_index: req.request_index, request_id: req.request_id }] });
    setNewParamRequestKey("");
    setNewStepRequestKey("");
    setActiveFlowTab("abilities");
  }
  function addParam() {
    const stepId = newParam.step_id || flowSpec?.steps?.[0]?.step_id || "";
    const path = newParam.path.trim();
    const key = newParam.key.trim();
    if (!stepId || !path || !key) { message.warning("请选择步骤并填写字段路径和参数名"); return; }
    const isEnum = newParam.type === "enum" || newParam.type === "list-enum";
    const sourceKind = defaultSourceForCategory(newParam.category);
    send({ type: "flow_update", edits: [{
      op: "add", step_id: stepId, param: {
        path, key, label: key, value: "", type: newParam.type, required: false,
        category: newParam.category, source_kind: sourceKind,
        source: sourceKind === "unknown" ? {} : { kind: sourceKind, path, manual: true },
        enum_options: isEnum ? [] : undefined,
        exposed_to_user: newParam.category === "user_param", editable: true,
        reason: "人工新增字段", need_human_confirm: false,
      },
    }] });
    setNewParam({ step_id: stepId, path: "", key: "", type: "string", category: "user_param" });
  }
  function addLink() {
    const { source_step_id, source_path, target_step_id, target_path } = newLink;
    if (!source_step_id || !target_step_id || !source_path || !target_path) { message.warning("请填写完整的来源和目标"); return; }
    send({ type: "flow_update", edits: [{ op: "add", step_id: source_step_id, link: { source_step_id, source_path, target_step_id, target_path, confirmed: false, reason: "人工新增依赖，需确认后才可发布" } }] });
    setNewLink({ source_step_id: "", source_path: "", target_step_id: "", target_path: "" });
  }
  function bindParamToPreviousResponse(step: FlowStepData, p: FlowParam) {
    if (!flowSpec) return;
    const key = paramDraftKey(step.step_id, p);
    const draft = bindDraft[key] || {};
    if (!draft.source_step_id || !draft.source_path) { message.warning("请选择来源步骤和响应字段"); return; }
    const edits: any[] = flowSpec.links
      .filter((l) => l.target_step_id === step.step_id && stripBodyPrefix(l.target_path) === stripBodyPrefix(p.path))
      .map((l) => ({ op: "remove", link_id: l.link_id, reset_target: false }));
    edits.push({
      op: "add",
      step_id: draft.source_step_id,
      link: {
        source_step_id: draft.source_step_id,
        source_path: draft.source_path,
        target_step_id: step.step_id,
        target_path: p.path,
        confirmed: true,
      },
    });
    send({ type: "flow_update", edits });
  }
  function startEditLink(link: FlowLinkData) { setEditingLink((s) => ({ ...s, [link.link_id]: { ...link } })); }
  function cancelEditLink(linkId: string) { setEditingLink((s) => { const c = { ...s }; delete c[linkId]; return c; }); }
  function saveLinkEdits(linkId: string) {
    if (!flowSpec) return;
    const edited = editingLink[linkId];
    if (!edited) return;
    const edits = [
      { op: "update", link_id: linkId, field: "source_step_id", value: edited.source_step_id },
      { op: "update", link_id: linkId, field: "source_path", value: edited.source_path },
      { op: "update", link_id: linkId, field: "target_step_id", value: edited.target_step_id },
      { op: "update", link_id: linkId, field: "target_path", value: edited.target_path },
      { op: "update", link_id: linkId, field: "confirmed", value: !!edited.confirmed },
    ];
    send({ type: "flow_update", edits });
    cancelEditLink(linkId);
  }
  function orchestrateFlow() {
    if (!flowSpecRef.current || flowOperationRef.current) return;
    if (flowMutationInFlightRef.current || flowMutationQueueRef.current.length) {
      runAfterFlowSync(orchestrateFlow);
      return;
    }
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
    const currentSpec = flowSpecRef.current;
    if (!currentSpec) return;
    flowOperationRef.current = {
      mode: "plan",
      previousUpdatedAt: currentSpec.meta?.recording_pi_loop?.updated_at,
      operationId: newCostlyOperationId("plan"),
    };
    setOrchestrateBusy(true);
    setAutoFixBusy(true);
    armFlowOperationWatchdog("能力生成");
    if (!send({ type: "orchestrate_flow", operation_id: flowOperationRef.current.operationId, flow_spec: currentSpec })) clearFlowOperation();
  }
  function performReplanFlow() {
    if (!flowSpecRef.current || flowOperationRef.current) return;
    const currentSpec = flowSpecRef.current;
    flowOperationRef.current = {
      mode: "replan",
      previousUpdatedAt: currentSpec.meta?.recording_pi_loop?.updated_at,
      operationId: newCostlyOperationId("replan"),
    };
    setOrchestrateBusy(true);
    setAutoFixBusy(true);
    armFlowOperationWatchdog("能力边界重新分析");
    if (!send({ type: "orchestrate_flow", operation_id: flowOperationRef.current.operationId, flow_spec: currentSpec, force_replan: true })) clearFlowOperation();
  }
  function replanFlow() {
    if (!flowSpecRef.current || flowOperationRef.current) return;
    Modal.confirm({
      title: "重新分析能力边界？",
      content: "将保留底层捕获接口、字段和依赖，清空当前能力层后重新拆分。适用于第一次能力拆分错误的情况。",
      okText: "重新分析",
      cancelText: "取消",
      onOk: () => {
        if (flowMutationInFlightRef.current || flowMutationQueueRef.current.length) {
          runAfterFlowSync(performReplanFlow);
          return;
        }
        performReplanFlow();
      },
    });
  }
  function autoFixFlow() {
    if (!flowSpecRef.current || flowOperationRef.current) return;
    if (flowMutationInFlightRef.current || flowMutationQueueRef.current.length) {
      runAfterFlowSync(autoFixFlow);
      return;
    }
    flowOperationRef.current = {
      mode: "repair",
      previousUpdatedAt: flowSpecRef.current.meta?.recording_pi_loop?.updated_at,
      operationId: newCostlyOperationId("repair"),
    };
    setAutoFixBusy(true);
    armFlowOperationWatchdog("自动修复");
    if (!send({ type: "auto_fix_flow", operation_id: flowOperationRef.current.operationId })) clearFlowOperation();
  }
  function addCapability() {
    const current = flowSpecRef.current;
    if (!current) return;
    const idx = (current.capabilities?.length || 0) + 1;
    const capability: FlowCapabilityData = {
      name: `capability_${idx}`,
      title: `能力 ${idx}`,
      intent: "",
      kind: "submit",
      step_ids: [],
      input_schema: { type: "object", properties: {}, required: [] },
      output_schema: { type: "object", properties: { raw: { type: "object" } } },
      output_mapping: [{ kind: "final_response", response_path: "response" }],
      confirmed: false,
      requires_human_confirm: true,
      confidence: 0.5,
    };
    const next = { ...current, capabilities: [...(current.capabilities || []), capability] };
    flowSpecRef.current = next;
    setFlowSpec(next);
    send({ type: "flow_update", edits: [{
      op: "add_capability",
      capability,
    }] });
  }
  function updateCapabilityConfirmed(idx: number, confirmed: boolean) {
    // Backend confirmation is an atomic preflight transaction. Do not paint an
    // optimistic checked state that would remain stale when the server rejects it.
    if (!confirmed) patchLocalCapability(idx, { confirmed: false }, false);
    send({ type: "flow_update", edits: [{ op: "update_capability", capability_index: idx, field: "confirmed", value: confirmed }] });
  }
  function updateCapabilityField(idx: number, field: string, value: any) {
    patchLocalCapability(idx, { [field]: value });
    send({ type: "flow_update", edits: [
      { op: "update_capability", capability_index: idx, field, value },
      { op: "update_capability", capability_index: idx, field: "confirmed", value: false },
    ] });
  }
  function removeCapability(idx: number) {
    Modal.confirm({
      title: "删除这个能力？",
      content: "只删除对外能力编排，不删除底层捕获接口和流程步骤。",
      okText: "删除", okType: "danger", cancelText: "取消",
      onOk: () => {
        const ok = send({ type: "flow_update", edits: [{ op: "remove_capability", capability_index: idx }] });
        if (!ok) return;
        const current = flowSpecRef.current;
        if (current) {
          const next = { ...current, capabilities: (current.capabilities || []).filter((_, capIdx) => capIdx !== idx) };
          flowSpecRef.current = next;
          setFlowSpec(next);
        }
        // 旧 checkReport 属于删除前的能力作用域，等待服务端按新能力重新校验。
        setCheckReport(null);
      },
    });
  }
  function addStepToCapability(idx: number, value?: string, usage?: CapabilityUsage | "") {
    if (!value || !usage) return;
    const membership = { usage, origin: "manual", pinned: true, confirmed: true };
    if (value.startsWith("step:")) {
      const stepId = value.slice(5);
      const cap = flowSpecRef.current?.capabilities?.[idx];
      const stepIds = usage === "option_source"
        ? capabilityActualStepIds(cap || {})
        : Array.from(new Set([...(capabilityActualStepIds(cap || {})), stepId]));
      const existingRef = (cap?.request_refs || []).find((ref) => ref.step_id === stepId);
      const requestRefs = [
        ...(cap?.request_refs || []).filter((ref) => ref.step_id !== stepId),
        { ...(existingRef || {}), step_id: stepId, ...membership },
      ];
      patchLocalCapability(idx, { step_ids: stepIds, request_refs: requestRefs });
      send({ type: "flow_update", edits: [
        { op: "add_capability_step", capability_index: idx, step_id: stepId, ...membership },
        { op: "update_capability", capability_index: idx, field: "request_refs", value: requestRefs },
        { op: "update_capability", capability_index: idx, field: "confirmed", value: false },
      ] });
      return;
    }
    if (value.startsWith("req:")) {
      const requestKey = value.slice(4);
      const req = findCapturedRequest(flowSpecRef.current, requestKey);
      if (!req) { message.warning("没有找到选中的捕获接口"); return; }
      const cap = flowSpecRef.current?.capabilities?.[idx];
      pendingCapabilityMembershipRef.current.push({
        capability: capabilityRef(cap || {}, idx),
        requestId: req.request_id,
        requestIndex: req.request_index,
        usage,
      });
      patchLocalCapability(idx, {});
      send({ type: "flow_update", edits: [
        { op: "add_capability_step", capability_index: idx, request_index: req?.request_index, request_id: req?.request_id, ...membership },
        { op: "update_capability", capability_index: idx, field: "confirmed", value: false },
      ] });
    }
  }
  function removeStepFromCapability(idx: number, stepId: string) {
    const cap = flowSpecRef.current?.capabilities?.[idx];
    const stepIds = capabilityActualStepIds(cap || {}).filter((id) => id !== stepId);
    patchLocalCapability(idx, { step_ids: stepIds });
    send({ type: "flow_update", edits: [
      { op: "remove_capability_step", capability_index: idx, step_id: stepId },
      { op: "update_capability", capability_index: idx, field: "confirmed", value: false },
    ] });
  }
  function moveStepInCapability(idx: number, stepIds: string[], from: number, delta: number) {
    const to = from + delta;
    if (to < 0 || to >= stepIds.length) return;
    const next = [...stepIds];
    const [item] = next.splice(from, 1);
    next.splice(to, 0, item);
    updateCapabilityField(idx, "step_ids", next);
  }
  function capabilityRef(cap: FlowCapabilityData, idx: number) {
    return cap.name || cap.capability_id || `idx:${idx}`;
  }
  function capabilityPanelKey(cap: FlowCapabilityData, idx: number) {
    // capability name 可编辑，不能作为面板 key；否则失焦保存会吞掉紧随其后的删除点击。
    return cap.capability_id || `capability-index:${idx}`;
  }
  function moveCapability(idx: number, delta: number) {
    const current = flowSpecRef.current;
    if (!current) return;
    const caps = [...(current.capabilities || [])];
    const to = idx + delta;
    if (to < 0 || to >= caps.length) return;
    const refs = caps.map(capabilityRef);
    const [item] = refs.splice(idx, 1);
    refs.splice(to, 0, item);
    const ordered = refs.map((ref) => caps.find((cap, capIdx) => capabilityRef(cap, capIdx) === ref)!).filter(Boolean);
    const next = { ...current, capabilities: ordered };
    flowSpecRef.current = next;
    setFlowSpec(next);
    send({ type: "flow_update", edits: [{ op: "reorder_capabilities", capability_refs: refs }] });
  }
  function loadJsonDraft() {
    if (!flowSpec) return;
    setJsonDraft(JSON.stringify(flowSpec, null, 2));
    setJsonErr("");
    jsonDirtyRef.current = false;                          // FH6:显式载入后清 dirty,允许后续 WS 推送自动同步
  }
  function applyJsonDraft() {
    try { setJsonErr(""); sendReplace(JSON.parse(jsonDraft)); jsonDirtyRef.current = false; }
    catch (e: any) { setJsonErr(e?.message || "JSON 解析失败"); }
  }
  function restoreServerJson() {
    if (!lastServerJson) { message.warning("没有最近的服务端版本"); return; }
    setJsonDraft(lastServerJson);
    jsonDirtyRef.current = false;
    try { sendReplace(JSON.parse(lastServerJson)); } catch { /* ignore */ }
  }

  const reviewItems = useMemo(() => {
    const list = (checkReport?.review_items?.length ? checkReport.review_items : flowSpec?.review_items) || [];
    const capStepIds = new Set<string>();
    for (const cap of (flowSpec?.capabilities || [])) {
      for (const sid of capabilityActualStepIds(cap)) capStepIds.add(sid);
    }
    return list
      .filter((i) => !i.resolved && i.severity === "high")
      .filter((i) => {
        const t = i.target || {};
        const k = t.kind;
        if (k === "flow" || k === "capability" || k === "request_role" || k === undefined) return true;
        const sid = t.step_id || t.target_step_id || t.source_step_id;
        if (!sid) return true;
        return capStepIds.has(sid);
      });
  }, [checkReport, flowSpec]);
  const stepOptions = useMemo(() => (flowSpec?.steps || []).map((s) => ({
    label: `${s.name || fallbackStepName(s.method, s.path)} · ${s.method} ${s.path || stripHost(s.url)}`,
    value: s.step_id,
  })), [flowSpec]);
  const stepById = useMemo(() => Object.fromEntries((flowSpec?.steps || []).map((s) => [s.step_id, s])), [flowSpec]);
  function stepBrief(stepId?: string) {
    const st = stepId ? stepById[stepId] : undefined;
    if (!st) return stepId || "";
    return `${st.name || fallbackStepName(st.method, st.path)} · ${st.method} ${st.path || stripHost(st.url)}`;
  }
  function groupedPublishIssues(report: FlowCheckReport | null, reviews: ReviewItemData[]) {
    const order = [
      { key: "capability", label: "能力编排", color: "geekblue" },
      { key: "interface", label: "接口步骤", color: "purple" },
      { key: "field", label: "字段配置", color: "gold" },
      { key: "dependency", label: "依赖关系", color: "cyan" },
      { key: "execution", label: "执行校验", color: "blue" },
      { key: "diagnostic", label: "页面诊断", color: "volcano" },
      { key: "flow", label: "整体流程", color: "default" },
    ];
    type OperatorIssue = {
      message: string; severity: string; source?: string; target?: Record<string, any>;
      audience?: "operator" | "internal"; actionable?: boolean; blocking?: boolean; auto_fixable?: boolean;
    };
    const isOperatorIssue = (item: OperatorIssue) => {
      if (item.audience) return item.audience === "operator" && item.actionable !== false;
      const severity = String(item.severity || "").toLowerCase();
      if (severity === "error" || severity === "high") return true;
      // Validator warnings are planner/schema diagnostics. They remain available to
      // auto-repair and logs, but an operator cannot resolve them with business input.
      if (item.source === "validator") return false;
      const kind = String(item.target?.kind || "");
      return item.source === "review" && [
        "param", "capability_enum", "link", "step", "request_role",
        "capability", "capability_relation", "flow",
      ].includes(kind);
    };
    const by: Record<string, OperatorIssue[]> = {};
    for (const [key, items] of Object.entries(report?.issue_groups || {})) {
      by[key] = (items || []).map((item) => ({
        message: item.message || "待处理问题",
        severity: item.severity || "warning",
        source: item.source,
        target: item.target,
        audience: item.audience,
        actionable: item.actionable,
        blocking: item.blocking,
        auto_fixable: item.auto_fixable,
      })).filter(isOperatorIssue);
    }
    if (!Object.keys(by).length) {
      for (const item of reviews) {
        const kind = item.target?.kind || "flow";
        const key = kind === "param" || kind === "capability_enum" ? "field"
          : kind === "link" ? "dependency"
            : kind === "step" || kind === "request_role" ? "interface"
              : kind === "capability" ? "capability" : "flow";
        by[key] = by[key] || [];
        by[key].push({ message: item.title, severity: item.severity, target: item.target });
      }
      for (const messageText of report?.errors || []) {
        by.flow = by.flow || [];
        by.flow.push({ message: messageText, severity: "error" });
      }
    }
    const out: Array<{ key: string; label: string; color: string; items: OperatorIssue[] }> = [];
    for (const item of order) {
      if (by[item.key]?.length) out.push({ ...item, items: by[item.key] });
    }
    for (const key of Object.keys(by)) {
      if (!order.some((item) => item.key === key)) out.push({ key, label: key, color: "default", items: by[key] });
    }
    return out;
  }
  function publishIssueTargetLabel(target?: Record<string, any>) {
    if (!target) return "";
    const cap = target.capability_name || target.capability_id || target.capability;
    const sid = target.target_step_id || target.step_id || target.source_step_id;
    const path = target.target_path || target.path || target.source_path;
    return [cap ? `能力 ${cap}` : "", sid ? `接口 ${stepBrief(sid)}` : "", path ? `字段 ${path}` : ""]
      .filter(Boolean).join(" · ");
  }
  function locatePublishIssue(target?: Record<string, any>) {
    if (!target) return;
    const sid = target.target_step_id || target.step_id || target.source_step_id;
    const capRef = target.capability_name || target.capability_id || target.capability
      || (flowSpec?.capabilities || []).find((cap) => capabilityActualStepIds(cap).includes(sid || ""))?.name;
    const capabilities = flowSpec?.capabilities || [];
    const capIdx = capabilities.findIndex((cap) =>
      [cap.name, cap.capability_id].filter(Boolean).includes(capRef)
      || capabilityActualStepIds(cap).includes(sid || ""));
    const isRequest = target.kind === "request_role";
    setActiveFlowTab(isRequest ? "requests" : "abilities");
    let anchor = "";
    if (isRequest) {
      setExpandedRequestPanels(["captured"]);
      anchor = `request-${domAnchorPart(target.request_index ?? target.index ?? target.request_id ?? target.path ?? sid)}`;
    } else if (capIdx >= 0) {
      const cap = capabilities[capIdx];
      const panelKey = capabilityPanelKey(cap, capIdx);
      setExpandedCapabilityKeys((keys) => Array.from(new Set([...keys, panelKey])));
      const section = target.kind === "link" ? "deps" : target.kind === "capability" || target.kind === "capability_node" ? "io" : "interfaces";
      setExpandedCapabilitySections((current) => ({
        ...current,
        [capIdx]: Array.from(new Set([...(current[capIdx] || ["interfaces"]), section])),
      }));
      if (sid) {
        setExpandedCapabilitySteps((current) => ({
          ...current,
          [capIdx]: Array.from(new Set([...(current[capIdx] || []), sid])),
        }));
      }
      if (target.link_id) anchor = `link-${domAnchorPart(target.link_id)}`;
      else if (sid && (target.target_path || target.path)) anchor = `field-${domAnchorPart(sid)}-${domAnchorPart(stripBodyPrefix(target.target_path || target.path))}`;
      else if (sid) anchor = `step-${domAnchorPart(sid)}`;
      else anchor = `capability-${domAnchorPart(cap.name || cap.capability_id || capIdx)}`;
    }
    if (!anchor && capRef) anchor = `capability-${domAnchorPart(capRef)}`;
    window.setTimeout(() => {
      const element = document.getElementById(anchor);
      if (!element) {
        message.warning("已切换到对应工作区，但错误项缺少可定位的结构化目标");
        return;
      }
      element.scrollIntoView({ behavior: "smooth", block: "center" });
      element.animate(
        [{ backgroundColor: "#fff1b8" }, { backgroundColor: "#fffbe6" }, { backgroundColor: "transparent" }],
        { duration: 2200, easing: "ease-out" },
      );
    }, 180);
  }
  function renderPublishIssue(item: ReviewItemData) {
    const tgt = item.target || {};
    const sourceStep = tgt.source_step_id ? stepBrief(tgt.source_step_id) : "";
    const targetStep = tgt.target_step_id || tgt.step_id ? stepBrief(tgt.target_step_id || tgt.step_id) : "";
    const fieldPath = tgt.target_path || tgt.path || "";
    return (
      <Space key={item.id} wrap size={4}>
        <Tag color={severityColor(item.severity)}>{item.type}</Tag>
        {targetStep && <Tag>接口 {targetStep}</Tag>}
        {fieldPath && <Tag>字段 {fieldPath}</Tag>}
        {sourceStep && <Tag>来源 {sourceStep}{tgt.source_path ? ` / ${tgt.source_path}` : ""}</Tag>}
        <Typography.Text type="danger" style={{ fontSize: 12 }}>{item.title}</Typography.Text>
      </Space>
    );
  }
  function sourcePathOptions(stepId?: string) {
    const st = stepId ? stepById[stepId] : undefined;
    return leafPaths(st?.response_json).map((p) => ({ label: p, value: p }));
  }
  function targetPathOptions(stepId?: string) {
    const st = stepId ? stepById[stepId] : undefined;
    return (st?.params || []).map((p) => ({ label: `${p.path} · ${p.key}`, value: p.path }));
  }
  function readSourceOptions() {
    const seen = new Set<string>();
    const out: Array<{ label: string; value: string }> = [];
    for (const req of allCapturedRequests(flowSpec)) {
      const value = req.url || req.path;
      if (!value || seen.has(value)) continue;
      seen.add(value);
      const state = isRequestInSteps(flowSpec, req) ? "已纳入" : "已捕获";
      out.push({
        label: `${state} · ${req.method || "GET"} ${req.path || stripHost(req.url || "")}`,
        value,
      });
    }
    return out;
  }
  function sourceStepForUrl(sourceUrl?: string) {
    const pure = purePath(sourceUrl || "");
    return (flowSpec?.steps || []).find((st) => {
      const candidates = [st.url, st.path, purePath(st.url), purePath(st.path)];
      return candidates.some((x) => x && (x === sourceUrl || purePath(x) === pure));
    });
  }
  function responseKeyOptionsForSource(sourceUrl?: string) {
    const st = sourceStepForUrl(sourceUrl);
    const sourcePath = purePath(sourceUrl || "");
    const captured = allCapturedRequests(flowSpec).find((req) => {
      const candidates = [req.url, req.path, purePath(req.url || ""), purePath(req.path || "")];
      return candidates.some((value) => value && (value === sourceUrl || purePath(value) === sourcePath));
    });
    const response = st?.response_json ?? captured?.response_json;
    const seen = new Set<string>();
    const out: Array<{ label: string; value: string }> = [];
    for (const path of leafPaths(response)) {
      const last = path.split(".").pop()?.replace(/\[\d+\]/g, "") || path;
      if (!last || seen.has(last)) continue;
      seen.add(last);
      out.push({ label: `${last} · ${path}`, value: last });
    }
    return out;
  }
  function incomingLink(stepId: string, path: string) {
    return (flowSpec?.links || []).find((l) => l.target_step_id === stepId && stripBodyPrefix(l.target_path) === stripBodyPrefix(path));
  }
  function selectBindingForParam(step: FlowStepData, p: FlowParam) {
    const selects = step.selects || [];
    return selects.find((s) => s.path === p.path) ||
      selects.find((s) => s.id_path === p.path) ||
      selects.find((s) => !s.path && s.param === p.key) ||
      selects.find((s) => s.param === p.key);
  }
  function enumOptionEdits(step: FlowStepData, p: FlowParam, options: Array<string | { label: string; value: any }>, optionMap?: Record<string, any>) {
    const nextSourceKind = p.source_kind === "api_option" ? "api_option" : "manual_enum";
    const edits: any[] = [
      paramEdit(step.step_id, p, "enum_options", options),
      paramEdit(step.step_id, p, "enum_value_map", optionMap || null),
      paramEdit(step.step_id, p, "category", "user_param"),
      paramEdit(step.step_id, p, "source_kind", nextSourceKind),
      paramEdit(step.step_id, p, "source", sourceDescriptor(nextSourceKind, p, p.source as any)),
      paramEdit(step.step_id, p, "exposed_to_user", true),
      paramEdit(step.step_id, p, "need_human_confirm", false),
    ];
    if (nextSourceKind !== "api_option" && p.type !== "enum" && p.type !== "list-enum" && options.length) {
      edits.push(paramEdit(step.step_id, p, "type", "enum"));
    }
    return edits;
  }
  function enumSourceForKind(sourceKind?: string | null) {
    if (sourceKind === "page_enum") return "dom";
    if (sourceKind === "manual_enum") return "manual";
    return "manual";
  }
  function upsertSelectBinding(step: FlowStepData, p: FlowParam, patch: Partial<FlowSelectBinding>, extraEdits: any[] = []) {
    const existing = selectBindingForParam(step, p);
    const hasExplicitIdPath = Object.prototype.hasOwnProperty.call(patch, "id_path");
    const sourceChanged = Object.prototype.hasOwnProperty.call(patch, "source_url")
      && (patch.source_url || "") !== (existing?.source_url || "");
    const currentPath = p.path || existing?.path || p.key || "";
    const nextBinding: FlowSelectBinding = {
      source_url: "",
      value_key: "",
      label_key: "",
      options: enumOptionRecordsForParam(step, p),
      count: p.enum_options?.length || 0,
      ...existing,
      ...patch,
      param: p.key,
      path: currentPath,
    };
    if (sourceChanged) {
      // 新接口必须以新响应重建候选，不能沿用首次误匹配留下的空值或旧值。
      nextBinding.options = [];
      nextBinding.option_map = null;
      nextBinding.count = 0;
      nextBinding.source_request_id = "";
      nextBinding.source_role = "";
      nextBinding.enum_source = "api";
      nextBinding.enum_confirmed = true;
      nextBinding.value_key = "";
      nextBinding.label_key = "";
    }
    if (!hasExplicitIdPath && !nextBinding.id_path && (nextBinding.source_url || p.source_kind === "api_option")) {
      nextBinding.id_path = currentPath;
    }
    if (nextBinding.options) nextBinding.count = nextBinding.options.length;
    const replaced = (step.selects || []).some((s) => s.path === p.path || (!s.path && s.param === p.key) || s === existing);
    const nextSelects = replaced
      ? (step.selects || []).map((s) => (s.path === p.path || (!s.path && s.param === p.key) || s === existing ? nextBinding : s))
      : [...(step.selects || []), nextBinding];
    const edits: any[] = [{ op: "update", step_id: step.step_id, field: "selects", value: nextSelects }];
    const paramUpdates: Record<string, any> = {};
    if (p.category !== "user_param" && p.category !== "runtime_var") {
      edits.push(paramEdit(step.step_id, p, "category", "user_param"));
      paramUpdates.category = "user_param";
    }
    const apiBacked = p.source_kind === "api_option" || !!nextBinding.source_url;
    if (!apiBacked && p.type !== "enum" && p.type !== "list-enum") {
      const nextType = nextBinding.multi ? "list-enum" : "enum";
      edits.push(paramEdit(step.step_id, p, "type", nextType));
      paramUpdates.type = nextType;
    }
    for (const edit of extraEdits) {
      if (edit?.op === "update" && edit.step_id === step.step_id && (edit.param_path || edit.param_key || edit.param_label)) {
        paramUpdates[edit.field] = edit.value;
      }
    }
    patchLocalStep(step.step_id, { selects: nextSelects });
    if (Object.keys(paramUpdates).length) patchLocalParam(step.step_id, p, paramUpdates);
    send({ type: "flow_update", edits: [...edits, ...extraEdits] });
  }
  function normalizeEnumOption(x: any): string {
    if (x == null) return "";
    if (typeof x === "string") return x;
    if (typeof x === "object" && typeof x.label === "string") return x.label;
    return String(x);
  }
  function enumOptionRecord(x: any): { label: string; value: any } | null {
    if (x == null) return null;
    if (typeof x === "object") {
      const label = String(x.label ?? x.text ?? x.name ?? x.value ?? "").trim();
      if (!label) return null;
      return { label, value: x.value ?? label };
    }
    const label = String(x).trim();
    return label ? { label, value: label } : null;
  }
  function enumOptionRecordsForParam(step: FlowStepData, p: FlowParam) {
    const sel = selectBindingForParam(step, p);
    const raw = p.enum_options?.length ? p.enum_options : sel?.options || [];
    const map = p.enum_value_map || sel?.option_map || {};
    const seen = new Set<string>();
    const out: Array<{ label: string; value: any }> = [];
    for (const item of raw || []) {
      const rec = enumOptionRecord(item);
      if (!rec || seen.has(rec.label)) continue;
      seen.add(rec.label);
      out.push({ label: rec.label, value: Object.prototype.hasOwnProperty.call(map, rec.label) ? map[rec.label] : rec.value });
    }
    return out;
  }
  function enumOptionsForParam(step: FlowStepData, p: FlowParam) {
    if (!OPTION_SOURCE_KINDS.includes(p.source_kind || "") && p.type !== "enum" && p.type !== "list-enum") return [];
    return enumOptionRecordsForParam(step, p).map((x) => x.label);
  }
  function enumOptionsTextForParam(step: FlowStepData, p: FlowParam) {
    return enumOptionRecordsForParam(step, p)
      .map((x) => String(x.value) !== String(x.label) ? `${x.label}=${String(x.value)}` : x.label)
      .join("\n");
  }
  function parseEnumOptionsText(text: string): { options: Array<{ label: string; value: any }>; optionMap: Record<string, any> | null } {
    const chunks = text.includes("\n") ? text.split(/\n/) : text.split(/[,，]/);
    const seen = new Set<string>();
    const options: Array<{ label: string; value: any }> = [];
    const optionMap: Record<string, any> = {};
    let hasMapped = false;
    for (const raw of chunks) {
      const line = raw.trim();
      if (!line) continue;
      const m = line.match(/^(.+?)(?:\s*(?:=>|=|:|：|\t)\s*)(.+)$/);
      const label = (m ? m[1] : line).trim();
      const valueRaw = (m ? m[2] : label).trim();
      if (!label || seen.has(label)) continue;
      seen.add(label);
      const value = /^-?\d+(?:\.\d+)?$/.test(valueRaw) ? Number(valueRaw) : valueRaw;
      options.push({ label, value });
      optionMap[label] = value;
      if (String(value) !== label) hasMapped = true;
    }
    return { options, optionMap: hasMapped ? optionMap : null };
  }
  function enumSourceLabel(sel?: FlowSelectBinding) {
    if (!sel) return "未绑定";
    if (sel.source_url) return "接口候选";
    if ((sel.options || []).length || sel.enum_source) return "枚举";
    return "未绑定";
  }
  function paramSourceText(step: FlowStepData, p: FlowParam, link?: FlowLinkData) {
    const sourceStep = link ? stepById[link.source_step_id] : undefined;
    const sel = selectBindingForParam(step, p);
    if (link) {
      return `实际接口返回：${sourceStep?.name || sourceStep?.path || link.source_step_id} 的 ${link.source_path}；当前默认值只是录制样例`;
    }
    if (p.source_kind === "previous_response" && p.source?.step_id) {
      return `实际接口返回：${p.source.step_name || p.source.step_id} 的 ${p.source.response_path || ""}；当前默认值只是录制样例`;
    }
    if (p.source_kind === "request_header") return `请求头来源：运行期从请求头 ${p.source?.header || ""} 获取；当前默认值只是录制样例`;
    if (p.source_kind === "user_input") return "用户输入：调用 Skill 时由用户填写；默认值来自录制样例";
    if (p.source_kind === "api_option") return `接口候选：运行期从 ${sel?.source_url || "已绑定接口"} 获取候选；默认值是录制时选中的值`;
    if (ENUM_SOURCE_KINDS.includes(p.source_kind || "")) return "枚举：候选来自录制页面、接口快照或人工维护；默认值是录制时选中的值";
    if (p.source_kind === "constant") return "固定默认值：发布后按当前值写入，通常不暴露给用户";
    if (p.source_kind === "current_user") return "当前用户：运行期从登录态/身份信息注入，不使用录制旧值";
    if (p.source_kind === "system_time") return "系统时间：运行期自动生成，不使用录制旧值";
    if (p.source_kind === "system_generated") return `系统生成值：运行期生成 ${({ uuid: "UUID", random_string: "随机字符串", random_number: "随机数字" } as Record<string, string>)[(p.source as any)?.strategy || "uuid"] || "动态值"}，不使用录制旧值`;
    if (p.source_kind === "computed") return `系统计算值：运行期根据 ${(p.source as any)?.start_field || "开始字段"} 与 ${(p.source as any)?.end_field || "结束字段"} 自动计算`;
    if (p.source_kind === "page_context") return `调用上下文：运行期从 ${(p.source as any)?.context_key || "未配置 context_key"} 注入；它不是上游接口响应`;
    return "来源未确认：需要选择用户输入、上游响应、固定值或系统来源";
  }
  function renderFlowWorkbench() {
    if (!flowSpec) return null;
    const totalParams = flowSpec.steps.reduce((n, s) => n + (s.params?.length || 0), 0);
    const capabilities = flowSpec.capabilities || [];
    const capturedTotal = allCapturedRequests(flowSpec).length;
    const visibleReviewItems = reviewItems;
    const unconfirmedCapabilities = capabilities.filter((cap) => !cap.confirmed || cap.requires_human_confirm).length;
    const publishIssueGroups = groupedPublishIssues(checkReport, visibleReviewItems);
    const hasPublishAdvice = publishIssueGroups.some((group) => group.items.length > 0);
    return (
      <Card style={{ marginTop: 16 }} styles={{ body: { paddingTop: 8 } }}>
        {checkReport && capabilities.length > 0 && (
          <Alert
            type={checkReport.passed && !hasPublishAdvice ? "success" : "warning"}
            showIcon
            style={{ marginBottom: 12 }}
            message={checkReport.passed
              ? (hasPublishAdvice ? "基础校验通过，仍有建议项" : "发布校验通过")
              : "发布校验需要处理"}
            description={
              <Space direction="vertical" size={2}>
                <Typography.Text style={{ fontSize: 12 }}>
                  Skill 参数：{checkReport.api_preview?.params?.length ? checkReport.api_preview.params.join(", ") : "无"}
                  {checkReport.dry_run ? ` · Dry-run ${checkReport.dry_run.ok ? "OK" : "需要处理"}` : ""}
                  {checkReport.dry_run?.request_count != null ? ` · ${checkReport.dry_run.request_count} 步` : ""}
                </Typography.Text>
                <Space direction="vertical" size={4}>
                  {publishIssueGroups.map((group) => (
                    <div key={group.key} style={{ display: "grid", gridTemplateColumns: "100px 1fr", gap: 8, alignItems: "start" }}>
                      <Tag color={group.color} style={{ margin: 0, textAlign: "center" }}>{group.label} {group.items.length}</Tag>
                      <Space direction="vertical" size={2}>
                        {group.items.map((item, issueIdx) => (
                          <Space key={`${group.key}-${issueIdx}`} wrap size={4}>
                            {publishIssueTargetLabel(item.target) && <Tag>{publishIssueTargetLabel(item.target)}</Tag>}
                            <Typography.Text type={item.severity === "warning" ? "secondary" : "danger"} style={{ fontSize: 12 }}>
                              {item.message}
                            </Typography.Text>
                            {item.target && <Button type="link" size="small" onClick={() => locatePublishIssue(item.target)}>定位</Button>}
                          </Space>
                        ))}
                      </Space>
                    </div>
                  ))}
                </Space>
                {hasPublishAdvice && (
                  <Button size="small" icon={<RobotOutlined />} loading={autoFixBusy} onClick={autoFixFlow}>
                    自动处理可修复项
                  </Button>
                )}
              </Space>
            }
          />
        )}
        <Tabs
          activeKey={activeFlowTab}
          onChange={setActiveFlowTab}
          destroyOnHidden={false}
          tabBarExtraContent={{
            left: (
              <Space wrap size={4} style={{ marginRight: 16 }}>
                <Typography.Text strong>编排工作台</Typography.Text>
                <Tag color="cyan">{capturedTotal} 接口</Tag>
                <Tag>{totalParams} 字段</Tag>
                {capabilities.length > 0 && <Tag color="geekblue">{capabilities.length} 能力</Tag>}
                {unconfirmedCapabilities > 0 && <Tag color="warning">{unconfirmedCapabilities} 待确认</Tag>}
                <Tag color={flowSpec.risk_level === "L4" ? "error" : "orange"}>风险 {flowSpec.risk_level}</Tag>
              </Space>
            ),
            right: (
              <Space wrap style={{ marginLeft: 12 }}>
                <Button size="small" loading={phase === "publishing"} onClick={finalize}>重新抓取</Button>
                <Button size="small" type="primary" loading={phase === "publishing"} onClick={publishRequest}>发布当前流程</Button>
              </Space>
            ),
          }}
          items={[
            { key: "abilities", label: `能力列表 ${capabilities.length || ""}`, children: renderCapabilityComposerPanel() },
            { key: "requests", label: `捕获接口 ${capturedTotal || ""}`, children: renderRequestsPanel() },
            { key: "desc", label: "整体说明", children: renderDescriptionPanel() },
            { key: "json", label: "高级 JSON", children: renderJsonPanel() },
          ]}
        />
      </Card>
    );
  }
  function renderRequestsPanel() {
    const capturedTotal = allCapturedRequests(flowSpec).length;
    return (
      <Collapse
        activeKey={expandedRequestPanels}
        onChange={(keys) => setExpandedRequestPanels((Array.isArray(keys) ? keys : [keys]).map(String))}
        bordered={false}
      >
        <Collapse.Panel header={`捕获接口 ${capturedTotal}`} key="captured">
          {renderCapturedRequestsPanel()}
        </Collapse.Panel>
      </Collapse>
    );
  }
  function renderCapturedRequestsPanel() {
    if (!flowSpec) return null;
    const rows = allCapturedRequests(flowSpec);
    if (!rows.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有捕获接口" />;
    return (
      <List
        size="small"
        rowKey={(req) => requestOptionValue(req)}
        dataSource={rows}
        renderItem={(req, idx) => {
          const capabilityNames = capturedRequestCapabilityNames(flowSpec, req);
          const fieldCandidate = !capabilityNames.length && isCapturedRequestFieldCandidate(flowSpec, req);
          return (
            <List.Item
              id={`request-${domAnchorPart(req.request_index ?? req.request_id ?? req.path ?? idx)}`}
              style={{ paddingLeft: 0, paddingRight: 0 }}
              actions={[
                <Button key="goto" size="small" onClick={() => setActiveFlowTab("abilities")}>去能力处理</Button>,
              ]}
            >
              <Space direction="vertical" size={4} style={{ width: "100%" }}>
                <Space wrap>
                  <Tag>{idx + 1}</Tag>
                  <Tag color={(req.method || "GET").toUpperCase() === "GET" ? "blue" : "green"}>{req.method || "GET"}</Tag>
                  <PathText value={req.path || stripHost(req.url || "")} maxWidth={620} />
                  {(req.occurrence_count || 1) > 1 && <Tag>{req.occurrence_count} 次</Tag>}
                  {capabilityNames.map((name) => <Tag color="success" key={name}>能力：{name}</Tag>)}
                  {!capabilityNames.length && fieldCandidate && <Tag color="cyan">仅字段候选</Tag>}
                  {!capabilityNames.length && !fieldCandidate && <Tag>仅事实</Tag>}
                  {req.role && <Tag>{req.role}</Tag>}
                  <Tag color={confidenceColor(req.confidence)}>置信度 {confidencePercent(req.confidence)}</Tag>
                  {req.response_status != null && <Tag>{req.response_status}</Tag>}
                </Space>
                {req.reason && <Typography.Text type="secondary" style={{ fontSize: 12 }}>{req.reason}</Typography.Text>}
              </Space>
            </List.Item>
          );
        }}
      />
    );
  }
  function capabilityStepSelectOptions(cap: FlowCapabilityData) {
    const existing = new Set(capabilityActualStepIds(cap));
    const allStepReqKeys = new Set((flowSpec?.steps || []).flatMap((s) => {
      const meta = s.source_meta || {};
      const keys: string[] = [];
      if (meta.request_id) keys.push(`id:${meta.request_id}`);
      if (meta.request_index != null) keys.push(`idx:${String(meta.request_index)}`);
      return keys;
    }));
    const existingReqKeys = new Set((flowSpec?.steps || [])
      .filter((s) => existing.has(s.step_id))
      .flatMap((s) => {
        const meta = s.source_meta || {};
        const keys: string[] = [];
        if (meta.request_id) keys.push(`id:${meta.request_id}`);
        if (meta.request_index != null) keys.push(`idx:${String(meta.request_index)}`);
        return keys.length ? keys : [`step:${s.step_id}`];
      }));
    const stepItems = (flowSpec?.steps || [])
      .filter((s) => !existing.has(s.step_id))
      .map((s) => ({
        label: `${s.name || fallbackStepName(s.method, s.path)} · ${s.method} ${s.path || stripHost(s.url)}`,
        value: `step:${s.step_id}`,
      }));
    const reqItems = allCapturedRequests(flowSpec)
      .filter((req) => !existingReqKeys.has(requestGraphKey(req)) && !allStepReqKeys.has(requestGraphKey(req)))
      .map((req) => ({
        label: `#${req.sequence ?? req.request_index ?? ""} ${req.method || "GET"} ${req.path || stripHost(req.url || "")}`,
        value: `req:${requestOptionValue(req)}`,
      }));
    return [...stepItems, ...reqItems];
  }
  function renderParamEditorInCapability(
    step: FlowStepData,
    p: FlowParam,
    scopedStepIds: Set<string>,
    paramIndex: number,
  ) {
    const bindKey = paramDraftKey(step.step_id, p);
    const linked = incomingLink(step.step_id, p.path);
    const currentBind = bindDraft[bindKey] || {
      source_step_id: p.source?.step_id || linked?.source_step_id,
      source_path: p.source?.response_path || linked?.source_path,
    };
    const needsManualConfirm = !!p.need_human_confirm && p.category === "runtime_var";
    const runtimeSourceComplete = !sourceNeedsConfiguration(p.source_kind || "unknown", p.source as any);
    const selectBinding = selectBindingForParam(step, p);
    const enumOptions = enumOptionsForParam(step, p);
    const enumSelectOptions = enumOptions.map((x) => ({ label: x, value: x }));
    const isApiOption = p.source_kind === "api_option";
    const isTypedEnum = p.type === "enum" || p.type === "list-enum";
    const isEnumOption = ENUM_SOURCE_KINDS.includes(p.source_kind || "") || isTypedEnum;
    const hasBindingPanel = isApiOption || isEnumOption;
    const hasRuntimePanel = !!linked || p.category === "runtime_var" || p.source_kind === "previous_response";
    const sourceSteps = (flowSpec?.steps || []).filter((s) => s.step_id !== step.step_id);
    const sourceStepOptions = [
      { label: "选择来源接口", value: "" },
      ...sourceSteps.map((s) => ({
        label: `${s.name || s.path} · ${s.method} ${s.path}`,
        value: s.step_id,
      })),
    ];
    const sourceRespOptions = [
      { label: currentBind.source_step_id ? "选择响应字段" : "先选择来源接口", value: "" },
      ...sourcePathOptions(currentBind.source_step_id),
    ];
    return (
      <List.Item
        // key 不能包含可编辑的 path/key/label。失焦保存会立即更新这些值；
        // 若 key 随之变化，组件会在 click 前被卸载，导致删除事件丢失。
        key={`${step.step_id}:param:${paramIndex}`}
        id={`field-${domAnchorPart(step.step_id)}-${domAnchorPart(p.path)}`}
        style={{ padding: "12px 0" }}
      >
        <div style={{ width: "100%", border: "1px solid #f0f0f0", borderRadius: 6, padding: 12, background: "#fff" }}>
          <Row gutter={[12, 8]} align="top">
            <Col flex="auto">
              <Space wrap size={6}>
                <Tag color={p.category === "runtime_var" ? "gold" : p.category === "system_const" ? "default" : "blue"}>{p.path}</Tag>
                <Tag>{optionLabel(CATEGORY_OPTIONS, p.category || "user_param")}</Tag>
                <Tag>{optionLabel(SOURCE_KIND_OPTIONS, normalizeSourceKindForUi(p.source_kind) || "unknown")}</Tag>
                {linked && <Tag color="cyan">依赖字段</Tag>}
                {isApiOption && <Tag color="geekblue">接口候选</Tag>}
                {isEnumOption && enumOptions.length > 0 && <Tag color="purple">枚举 {enumOptions.length}</Tag>}
                {p.page_required === true && <Tag color="red">页面必填</Tag>}
                {p.page_required === false && <Tag>页面可选</Tag>}
                {p.page_required == null && <Tag color="gold">页面必填性未确认</Tag>}
                {paramRequiredFromCaller(p)
                  ? <Tag color="volcano">调用方必填</Tag>
                  : <Tag color="green">调用方无需必填</Tag>}
                {needsManualConfirm && <Tag color="warning">待确认</Tag>}
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>{p.reason}</Typography.Text>
              </Space>
              <Typography.Text type="secondary" style={{ display: "block", marginTop: 6, fontSize: 12 }}>
                {paramSourceText(step, p, linked)}
              </Typography.Text>
            </Col>
            <Col>
              <Button
                size="small"
                danger
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => removeParam(step.step_id, p)}
              >删除字段</Button>
            </Col>
          </Row>
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
            gap: 10,
            alignItems: "end",
            marginTop: 10,
          }}>
            <FieldControl label="名称">
              <EditableText value={p.key} width="100%" onSave={(v) => v && updateParam(step.step_id, p, "key", v)} />
            </FieldControl>
            <FieldControl label="路径">
              <EditableText value={p.path} width="100%" onSave={(v) => v && updateParam(step.step_id, p, "path", v)} />
            </FieldControl>
            <FieldControl label="默认值">
              {enumOptions.length > 0 ? (
                <EnumValueInput value={String(p.value ?? "")} width="100%"
                  options={enumSelectOptions}
                  onSave={(v) => updateParam(step.step_id, p, "value", v)} />
              ) : (
                <EditableText value={String(p.value ?? "")} width="100%" onSave={(v) => updateParam(step.step_id, p, "value", v)} />
              )}
            </FieldControl>
            <FieldControl label="类型">
              <NativeSelect value={p.type} width="100%" options={PARAM_TYPE_OPTIONS}
                onChange={(v) => updateParamType(step, p, v)} />
            </FieldControl>
            <FieldControl label="分类">
              <NativeSelect value={p.category || "user_param"} width="100%" options={CATEGORY_OPTIONS}
                onChange={(v) => updateParamCategory(step.step_id, p, v)} />
            </FieldControl>
            <FieldControl label="来源">
              <NativeSelect value={normalizeSourceKindForUi(p.source_kind) || defaultSourceForCategory(p.category || "user_param")} width="100%" options={sourceSelectOptionsForParam(p)}
                onChange={(v) => updateParamSourceKind(step.step_id, p, v)} />
            </FieldControl>
            <FieldControl label="调用方必填">
              {paramExposedToCaller(p) ? (
                <Checkbox checked={!!p.required} onChange={(e) => updateParam(step.step_id, p, "required", e.target.checked)}>调用时必须提供</Checkbox>
              ) : <Typography.Text type="secondary">由流程运行期提供</Typography.Text>}
            </FieldControl>
            <FieldControl label="展示">
              {p.category === "user_param" ? (
                <Checkbox checked={p.exposed_to_user !== false} onChange={(e) => updateParam(step.step_id, p, "exposed_to_user", e.target.checked)}>暴露给调用方</Checkbox>
              ) : <Typography.Text type="secondary">不对调用方展示</Typography.Text>}
            </FieldControl>
          </div>
          {needsManualConfirm && runtimeSourceComplete && (
            <Button size="small" style={{ marginTop: 8 }} onClick={() => updateParam(step.step_id, p, "need_human_confirm", false)}>
              确认当前来源
            </Button>
          )}
          {(hasBindingPanel || hasRuntimePanel) && (
            <Collapse size="small" ghost style={{ marginTop: 10 }} defaultActiveKey={needsManualConfirm ? ["runtime"] : []}>
              {hasBindingPanel && (
                <Collapse.Panel key="binding" header={<Space><LinkOutlined />来源/枚举配置</Space>}>
                  <div style={{ background: "#fafafa", border: "1px solid #f0f0f0", borderRadius: 6, padding: 8 }}>
                    <Space direction="vertical" size={8} style={{ width: "100%" }}>
                      <Space wrap size={6}>
                        <Typography.Text strong style={{ fontSize: 12 }}>{isApiOption ? "接口候选配置" : "枚举候选配置"}</Typography.Text>
                        <Tag color={selectBinding?.source_url ? "geekblue" : "purple"}>{enumSourceLabel(selectBinding)}</Tag>
                        {enumOptions.slice(0, 8).map((x, enumIdx) => <Tag key={`${x}-${enumIdx}`}>{x}</Tag>)}
                        {enumOptions.length > 8 && <Tag>+{enumOptions.length - 8}</Tag>}
                      </Space>
                      {isApiOption && (
                        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: 8, alignItems: "end" }}>
                          <FieldControl label="来源接口">
                            <EditableComboInput
                              value={selectBinding?.source_url || ""}
                              options={readSourceOptions()}
                              placeholder="选择或输入接口地址"
                              onSave={(v) => upsertSelectBinding(step, p, { source_url: v })}
                            />
                          </FieldControl>
                          <FieldControl label="接口参数">
                            <EditableTextArea
                              rows={1}
                              value={queryToLines(selectBinding?.source_url || "")}
                              placeholder="每行一个参数，如 pageNo=1"
                              onSave={(v) => upsertSelectBinding(step, p, { source_url: mergeUrlQuery(selectBinding?.source_url || "", v) })}
                            />
                          </FieldControl>
                          <FieldControl label="值字段">
                            <EditableComboInput
                              value={selectBinding?.value_key || ""}
                              options={responseKeyOptionsForSource(selectBinding?.source_url)}
                              placeholder="如 id/userId/dictValue"
                              onSave={(v) => upsertSelectBinding(step, p, { value_key: v })}
                            />
                          </FieldControl>
                          <FieldControl label="显示字段">
                            <EditableComboInput
                              value={selectBinding?.label_key || ""}
                              options={responseKeyOptionsForSource(selectBinding?.source_url)}
                              placeholder="如 name/label/dictLabel"
                              onSave={(v) => upsertSelectBinding(step, p, { label_key: v })}
                            />
                          </FieldControl>
                          <FieldControl label="配对 ID 字段">
                            <EditableComboInput
                              value={selectBinding?.id_path || p.path || p.key || ""}
                              options={(step.params || []).map((x) => ({ label: `${x.path} · ${x.key}`, value: x.path }))}
                              placeholder="默认当前字段路径，可改为隐藏 ID 字段"
                              onSave={(v) => upsertSelectBinding(step, p, { id_path: v || null })}
                            />
                          </FieldControl>
                          <FieldControl label="多选">
                            <Checkbox checked={!!selectBinding?.multi || p.type === "list-enum"}
                              onChange={(e) => upsertSelectBinding(step, p, { multi: e.target.checked })}>
                              列表多选
                            </Checkbox>
                          </FieldControl>
                        </div>
                      )}
                      {isEnumOption && (
                        <FieldControl label="枚举候选">
                          <EditableTextArea
                            rows={3}
                            value={enumOptionsTextForParam(step, p)}
                            placeholder="每行一个候选项；提交短码时写成 病假=2"
                            onSave={(v) => {
                              const { options, optionMap } = parseEnumOptionsText(v);
                              upsertSelectBinding(
                                step,
                                p,
                                {
                                  source_url: "",
                                  value_key: "",
                                  label_key: "",
                                  options,
                                  count: options.length,
                                  option_map: optionMap,
                                  enum_source: enumSourceForKind(p.source_kind),
                                  enum_confirmed: true,
                                },
                                enumOptionEdits(step, p, options, optionMap || undefined),
                              );
                            }}
                          />
                        </FieldControl>
                      )}
                    </Space>
                  </div>
                </Collapse.Panel>
              )}
              {hasRuntimePanel && (
                <Collapse.Panel key="runtime" header={<Space><BranchesOutlined />运行期来源</Space>}>
                  <div style={{ background: "#fafafa", border: "1px solid #f0f0f0", borderRadius: 6, padding: 10 }}>
                    {p.source_kind === "previous_response" || linked ? (
                      <Space wrap>
                        <Typography.Text strong style={{ fontSize: 12 }}>上游响应</Typography.Text>
                        <NativeSelect value={currentBind.source_step_id || ""} width={300}
                          options={sourceStepOptions}
                          onChange={(v) => setBindDraft((d) => ({ ...d, [bindKey]: { ...currentBind, source_step_id: v, source_path: "" } }))} />
                        <ComboInput value={currentBind.source_path || ""} width={300}
                          options={sourceRespOptions}
                          disabled={!currentBind.source_step_id}
                          placeholder={currentBind.source_step_id ? "选择或输入响应字段，如 data.id" : "先选择来源接口"}
                          onChange={(v) => setBindDraft((d) => ({ ...d, [bindKey]: { ...currentBind, source_path: v } }))} />
                        <Button size="small" type="primary" icon={<LinkOutlined />} onClick={() => bindParamToPreviousResponse(step, p)}>绑定上游响应</Button>
                      </Space>
                    ) : p.source_kind === "page_context" ? (
                      <FieldControl label="调用上下文键">
                        <EditableText value={(p.source as any)?.context_key || p.key || ""} width={320}
                          placeholder="如 department_id；由调用方运行环境注入"
                          onSave={(v) => updateRuntimeSourceDetail(step.step_id, p, { context_key: v })} />
                      </FieldControl>
                    ) : p.source_kind === "request_header" ? (
                      <FieldControl label="请求头名称">
                        <EditableText value={(p.source as any)?.header || ""} width={320}
                          placeholder="如 Authorization / X-Tenant-Id"
                          onSave={(v) => updateRuntimeSourceDetail(step.step_id, p, { header: v })} />
                      </FieldControl>
                    ) : p.source_kind === "current_user" ? (
                      <Typography.Text type="secondary">运行期从当前登录身份注入，不依赖前置接口。</Typography.Text>
                    ) : p.source_kind === "system_time" ? (
                      <Typography.Text type="secondary">运行期按字段类型生成当前系统时间，不使用录制样例。</Typography.Text>
                    ) : p.source_kind === "system_generated" ? (
                      <FieldControl label="生成策略">
                        <NativeSelect value={(p.source as any)?.strategy || "uuid"} width={260}
                          options={[
                            { label: "UUID", value: "uuid" },
                            { label: "随机字符串", value: "random_string" },
                            { label: "随机数字", value: "random_number" },
                          ]}
                          onChange={(v) => updateRuntimeSourceDetail(step.step_id, p, { strategy: v })} />
                      </FieldControl>
                    ) : p.source_kind === "computed" ? (
                      <Typography.Text type="secondary">
                        运行期按规则 {(p.source as any)?.strategy || "未配置"}，根据 {(p.source as any)?.start_field || "开始字段"} 与 {(p.source as any)?.end_field || "结束字段"} 自动计算。
                      </Typography.Text>
                    ) : (
                      <Typography.Text type="warning">请选择明确来源；未配置来源的运行期变量不会被当成可执行字段。</Typography.Text>
                    )}
                  </div>
                </Collapse.Panel>
              )}
            </Collapse>
          )}
        </div>
      </List.Item>
    );
  }
  function renderAddFieldForStep(step: FlowStepData) {
    const isActive = newParam.step_id === step.step_id;
    return (
      <Card size="small" styles={{ body: { padding: 10 } }}>
        <Space wrap>
          <Typography.Text strong>新增字段</Typography.Text>
          <Input placeholder="字段路径" value={isActive ? newParam.path : ""} style={{ width: 180 }}
            onChange={(e) => setNewParam((s) => ({ ...s, step_id: step.step_id, path: e.target.value }))} />
          <Input placeholder="参数名" value={isActive ? newParam.key : ""} style={{ width: 160 }}
            onChange={(e) => setNewParam((s) => ({ ...s, step_id: step.step_id, key: e.target.value }))} />
          <NativeSelect value={isActive ? newParam.type : "string"} width={130} options={PARAM_TYPE_OPTIONS}
            onChange={(v) => setNewParam((s) => ({ ...s, step_id: step.step_id, type: v }))} />
          <NativeSelect value={isActive ? newParam.category : "user_param"} width={140} options={CATEGORY_OPTIONS}
            onChange={(v) => setNewParam((s) => ({ ...s, step_id: step.step_id, category: v }))} />
          <Button type="primary" onClick={() => {
            const draft = isActive ? newParam : { ...newParam, step_id: step.step_id };
            const path = draft.path.trim();
            const key = draft.key.trim();
            if (!path || !key) { message.warning("请填写字段路径和参数名"); return; }
            const isEnum = draft.type === "enum" || draft.type === "list-enum";
            const sourceKind = defaultSourceForCategory(draft.category);
            send({ type: "flow_update", edits: [{
              op: "add", step_id: step.step_id, param: {
                path, key, label: key, value: "", type: draft.type, required: false,
                category: draft.category, source_kind: sourceKind,
                source: sourceKind === "unknown" ? {} : { kind: sourceKind, path, manual: true },
                enum_options: isEnum ? [] : undefined,
                exposed_to_user: draft.category === "user_param", editable: true,
                reason: "人工新增字段", need_human_confirm: false,
              },
            }] });
            setNewParam({ step_id: step.step_id, path: "", key: "", type: "string", category: "user_param" });
          }}>添加字段</Button>
        </Space>
      </Card>
    );
  }
  function renderStepResponseFields(step: FlowStepData) {
    const leaves = leafPathValues(step.response_json);
    if (!leaves.length) return null;
    return (
      <Card size="small" title="响应字段" style={{ marginTop: 10 }}>
        <Space wrap size={4}>
          {leaves.slice(0, 60).map((leaf, idx) => (
            <Tooltip key={`${leaf.path}-${idx}`} title={leaf.value}>
              <Typography.Text code style={{ fontSize: 12 }}>{leaf.path}</Typography.Text>
            </Tooltip>
          ))}
          {leaves.length > 60 && <Tag>+{leaves.length - 60}</Tag>}
        </Space>
      </Card>
    );
  }
  function renderStepFieldsInCapability(step: FlowStepData, scopedStepIds: Set<string>) {
    return (
      <Space direction="vertical" size={10} style={{ width: "100%" }}>
        {renderAddFieldForStep(step)}
        {(step.params || []).length ? (
          <List
            size="small"
            rowKey={(_p, index) => `${step.step_id}:param:${index}`}
            dataSource={step.params || []}
            renderItem={(p, index) => renderParamEditorInCapability(step, p, scopedStepIds, index)}
          />
        ) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="这个接口没有请求入参" />
        )}
        {renderStepResponseFields(step)}
      </Space>
    );
  }
  function renderCapabilityStepWithFields(cap: FlowCapabilityData, capIdx: number, stepId: string, stepIdx: number) {
    const stepIds = capabilityActualStepIds(cap);
    const scopedStepIds = new Set(stepIds);
    const st = stepById[stepId];
    const requestRef = capabilityRequestRefForStep(cap, stepId);
    if (!st) {
      return (
        <Collapse.Panel key={stepId} header={<Typography.Text type="danger">接口不存在：{stepId}</Typography.Text>}>
          <Button danger size="small" onClick={() => removeStepFromCapability(capIdx, stepId)}>从能力移除</Button>
        </Collapse.Panel>
      );
    }
    return (
      <Collapse.Panel
        key={stepId}
        header={
          <Space wrap id={`step-${domAnchorPart(stepId)}`}>
            <Tag color="purple">接口 {stepIdx + 1}</Tag>
            <Tag color={(st.method || "GET").toUpperCase() === "GET" ? "blue" : "green"}>{st.method}</Tag>
            <Typography.Text strong>{st.name || fallbackStepName(st.method, st.path)}</Typography.Text>
            <PathText value={st.path || stripHost(st.url)} maxWidth={420} />
            <Tag color="blue">用途：{capabilityUsageLabel(requestRef?.usage)}</Tag>
            {requestRef?.pinned && <Tag color="gold">手工锁定</Tag>}
            <Tag>{st.params?.length || 0} 字段</Tag>
          </Space>
        }
        extra={
          <Space onClick={(e) => e.stopPropagation()}>
            <Tooltip title="上移"><Button size="small" icon={<UpOutlined />} disabled={stepIdx === 0} onClick={() => moveStepInCapability(capIdx, stepIds, stepIdx, -1)} /></Tooltip>
            <Tooltip title="下移"><Button size="small" icon={<DownOutlined />} disabled={stepIdx === stepIds.length - 1} onClick={() => moveStepInCapability(capIdx, stepIds, stepIdx, 1)} /></Tooltip>
            <Button size="small" danger onClick={() => removeStepFromCapability(capIdx, stepId)}>移除</Button>
          </Space>
        }
      >
        {renderStepFieldsInCapability(st, scopedStepIds)}
      </Collapse.Panel>
    );
  }
  function renderCapabilityInterfacesWithFields(cap: FlowCapabilityData, capIdx: number) {
    const stepIds = capabilityActualStepIds(cap);
    const auxiliaryRefs = (cap.request_refs || []).filter((ref) => ref.usage === "option_source" && ref.step_id && !stepIds.includes(ref.step_id));
    const addOptions = capabilityStepSelectOptions(cap);
    const fieldCount = stepIds.reduce((n, sid) => n + (stepById[sid]?.params?.length || 0), 0);
    return (
      <Space direction="vertical" size={10} style={{ width: "100%" }}>
        <Space wrap align="center">
          <Typography.Text strong>添加接口</Typography.Text>
          <NativeSelect
            value={capabilityAddValue[capIdx] || ""}
            width={460}
            options={[{ label: addOptions.length ? "选择要加入能力的接口" : "没有可添加的接口", value: "" }, ...addOptions]}
            onChange={(v) => setCapabilityAddValue((s) => ({ ...s, [capIdx]: v }))}
          />
          <NativeSelect
            value={capabilityAddUsage[capIdx] || ""}
            width={140}
            options={[{ label: "选择用途", value: "" }, ...CAPABILITY_USAGE_OPTIONS]}
            onChange={(v) => setCapabilityAddUsage((s) => ({ ...s, [capIdx]: v as CapabilityUsage | "" }))}
          />
          <Button
            size="small"
            type="primary"
            disabled={!capabilityAddValue[capIdx] || !capabilityAddUsage[capIdx]}
            onClick={() => {
              addStepToCapability(capIdx, capabilityAddValue[capIdx], capabilityAddUsage[capIdx]);
              setCapabilityAddValue((s) => ({ ...s, [capIdx]: "" }));
              setCapabilityAddUsage((s) => ({ ...s, [capIdx]: "" }));
            }}
          >
            添加接口
          </Button>
          <Tag>{stepIds.length} 执行接口 / {auxiliaryRefs.length} 候选来源 / {fieldCount} 字段</Tag>
        </Space>
        {!stepIds.length ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="未绑定接口" />
        ) : (
          <Collapse
            size="small"
            activeKey={expandedCapabilitySteps[capIdx] || []}
            onChange={(keys) => setExpandedCapabilitySteps((current) => ({
              ...current,
              [capIdx]: (Array.isArray(keys) ? keys : [keys]).map(String),
            }))}
          >
            {stepIds.map((stepId, stepIdx) => renderCapabilityStepWithFields(cap, capIdx, stepId, stepIdx))}
          </Collapse>
        )}
        {auxiliaryRefs.length > 0 && (
          <List
            size="small"
            header={<Typography.Text strong>候选来源</Typography.Text>}
            dataSource={auxiliaryRefs}
            renderItem={(ref) => {
              const st = stepById[String(ref.step_id || "")];
              return (
                <List.Item actions={[<Button key="remove" size="small" danger onClick={() => removeStepFromCapability(capIdx, String(ref.step_id || ""))}>移除</Button>]}>
                  <Space wrap>
                    <Tag color="cyan">选项来源</Tag>
                    <Typography.Text>{st?.name || ref.path || ref.step_id}</Typography.Text>
                    {st && <PathText value={st.path || stripHost(st.url)} maxWidth={420} />}
                    {ref.pinned && <Tag color="gold">手工锁定</Tag>}
                  </Space>
                </List.Item>
              );
            }}
          />
        )}
      </Space>
    );
  }
  function renderCapabilityDependencyEditor(cap: FlowCapabilityData) {
    if (!flowSpec) return null;
    const stepIds = new Set(capabilityActualStepIds(cap));
    const scopedSteps = (flowSpec.steps || []).filter((s) => stepIds.has(s.step_id));
    const scopedStepOptions = scopedSteps.map((s) => ({
      label: `${s.name || s.path} · ${s.method} ${s.path}`,
      value: s.step_id,
    }));
    const scopedLinks = (flowSpec.links || []).filter((l) => stepIds.has(l.source_step_id) && stepIds.has(l.target_step_id));
    return (
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Card size="small" title={<Space><PlusOutlined />新增依赖</Space>}>
          <Row gutter={[8, 8]} align="middle">
            <Col span={6}><NativeSelect value={newLink.source_step_id || ""} width="100%"
              options={[{ label: "选择来源接口", value: "" }, ...scopedStepOptions]}
              onChange={(v) => setNewLink((s) => ({ ...s, source_step_id: v, source_path: "" }))} /></Col>
            <Col span={6}><ComboInput value={newLink.source_path || ""} width="100%"
              options={[{ label: newLink.source_step_id ? "选择来源响应字段" : "先选择来源接口", value: "" }, ...sourcePathOptions(newLink.source_step_id)]}
              disabled={!newLink.source_step_id}
              placeholder={newLink.source_step_id ? "选择或输入来源响应字段" : "先选择来源接口"}
              onChange={(v) => setNewLink((s) => ({ ...s, source_path: v }))} /></Col>
            <Col span={5}><NativeSelect value={newLink.target_step_id || ""} width="100%"
              options={[{ label: "选择目标接口", value: "" }, ...scopedStepOptions]}
              onChange={(v) => setNewLink((s) => ({ ...s, target_step_id: v, target_path: "" }))} /></Col>
            <Col span={5}><ComboInput value={newLink.target_path || ""} width="100%"
              options={[{ label: newLink.target_step_id ? "选择目标字段" : "先选择目标接口", value: "" }, ...targetPathOptions(newLink.target_step_id)]}
              disabled={!newLink.target_step_id}
              placeholder={newLink.target_step_id ? "选择或输入目标字段" : "先选择目标接口"}
              onChange={(v) => setNewLink((s) => ({ ...s, target_path: v }))} /></Col>
            <Col span={2}><Button type="primary" block onClick={addLink}>添加</Button></Col>
          </Row>
        </Card>
        {!scopedLinks.length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="这个能力没有接口依赖" /> : (
          <List
            size="small"
            dataSource={scopedLinks}
            renderItem={(link) => {
              const sourceStep = stepById[link.source_step_id];
              const targetStep = stepById[link.target_step_id];
              return (
                <List.Item
                  id={`link-${domAnchorPart(link.link_id)}`}
                  actions={[
                    <Checkbox key="cf" checked={!!link.confirmed}
                      onChange={(e) => send({ type: "flow_update", edits: [{ op: "update", link_id: link.link_id, field: "confirmed", value: e.target.checked }] })}>已确认</Checkbox>,
                    <Button key="rm" size="small" danger onClick={() => send({ type: "flow_update", edits: [{ op: "remove", link_id: link.link_id, reset_target: true }] })}>删除</Button>,
                  ]}
                >
                  <Space wrap>
                    <Tag color="cyan">依赖</Tag>
                    <Typography.Text>{sourceStep?.name || sourceStep?.path || link.source_step_id}</Typography.Text>
                    <Typography.Text code>{link.source_path}</Typography.Text>
                    <Typography.Text>→</Typography.Text>
                    <Typography.Text>{targetStep?.name || targetStep?.path || link.target_step_id}</Typography.Text>
                    <Typography.Text code>{link.target_path}</Typography.Text>
                    {link.reason && <Typography.Text type="secondary" style={{ fontSize: 12 }}>{link.reason}</Typography.Text>}
                  </Space>
                </List.Item>
              );
            }}
          />
        )}
      </Space>
    );
  }
  function schemaRowsView(schema?: Record<string, any>, emptyText = "无") {
    const rows = schemaFieldRows(schema);
    if (!rows.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={emptyText} />;
    return (
      <List
        size="small"
        dataSource={rows}
        renderItem={(row) => (
          <List.Item>
            <Space wrap>
              <Typography.Text code>{row.name}</Typography.Text>
              <Tag color="blue">业务类型：{PARAM_TYPE_LABELS[row.businessType] || row.businessType}</Tag>
              <Tag>Wire：{row.wireType}</Tag>
              {row.required && <Tag color="red">必填</Tag>}
              {row.description && <Typography.Text type="secondary">{row.description}</Typography.Text>}
            </Space>
          </List.Item>
        )}
      />
    );
  }
  function renderCapabilityIOBusinessView(capIdx: number, inputSchema: Record<string, any>, outputSchema: Record<string, any>) {
    return (
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 12 }}>
          <Card size="small" title="调用参数">
            {schemaRowsView(inputSchema, "无调用参数")}
          </Card>
          <Card size="small" title="返回结果">
            {schemaRowsView(outputSchema, "返回最后一个接口的原始响应")}
          </Card>
        </div>
        <Collapse ghost size="small">
          <Collapse.Panel key="schema" header="编辑输入/输出 Schema">
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 12 }}>
              <FieldControl label="输入 JSON Schema">
                <EditableTextArea rows={7} value={JSON.stringify(inputSchema, null, 2)}
                  onSave={(v) => {
                    try { updateCapabilityField(capIdx, "input_schema", JSON.parse(v || "{}")); }
                    catch (e: any) { message.error(e?.message || "输入 Schema 不是合法 JSON"); }
                  }} />
              </FieldControl>
              <FieldControl label="输出 JSON Schema">
                <EditableTextArea rows={7} value={JSON.stringify(outputSchema, null, 2)}
                  onSave={(v) => {
                    try { updateCapabilityField(capIdx, "output_schema", JSON.parse(v || "{}")); }
                    catch (e: any) { message.error(e?.message || "输出 Schema 不是合法 JSON"); }
                  }} />
              </FieldControl>
            </div>
          </Collapse.Panel>
        </Collapse>
      </Space>
    );
  }
  function renderCapabilityComposerPanel() {
    if (!flowSpec) return null;
    const capabilities = flowSpec.capabilities || [];
    const capabilityRelations = flowSpec.capability_relations || [];
    const kindOptions = CAPABILITY_KIND_OPTIONS;
    return (
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Space wrap>
          <Tooltip title="基于当前能力、接口和人工修改继续规划，并同步修正字段绑定、枚举来源、依赖和接口闭包">
            <Button icon={<RobotOutlined />} type="primary" loading={orchestrateBusy || autoFixBusy} onClick={orchestrateFlow}>生成/优化能力</Button>
          </Tooltip>
          <Tooltip title="清空能力层并基于已捕获接口重新拆分；保留底层字段、接口和依赖事实">
            <Button icon={<BranchesOutlined />} loading={orchestrateBusy || autoFixBusy} onClick={replanFlow}>重新分析能力边界</Button>
          </Tooltip>
          <Button icon={<PlusOutlined />} onClick={addCapability}>新增能力</Button>
          <Button icon={<RobotOutlined />} loading={namingBusy} onClick={() => { setNamingBusy(true); send({ type: "step_naming" }); }}>命名步骤</Button>
          {flowSpec.meta?.capability_generation && <>
            <Tag color={flowSpec.meta.capability_generation.initial_completed ? "success" : "warning"}>
              {flowSpec.meta.capability_generation.initial_completed ? "首次语义生成完成" : "确定性降级结果"}
            </Tag>
            <Tag color={flowSpec.meta.capability_generation.application_cache_hit ? "green" : "blue"}>
              {flowSpec.meta.capability_generation.application_cache_hit
                ? "结果复用 · 零模型调用"
                : `模型调用 ${flowSpec.meta.capability_generation.model_calls || 0}`}
            </Tag>
            {!flowSpec.meta.capability_generation.application_cache_hit
              && !!flowSpec.meta.capability_generation.provider_cache_hits
              && <Tag color="cyan">模型前缀缓存 {Math.round((flowSpec.meta.capability_generation.model_cache_rate || 0) * 100)}%</Tag>}
            {!!flowSpec.meta.capability_generation.indexed_range_changes?.length &&
              <Tag color="cyan">识别区间字段 {flowSpec.meta.capability_generation.indexed_range_changes.length}</Tag>}
          </>}
        </Space>
        {lastOperationReport && (
          <Alert
            type={lastOperationReport.changed ? "success" : lastOperationReport.model_errors?.length ? "error" : "info"}
            showIcon
            message={lastOperationReport.summary || "编排操作完成"}
            description={
              <Space wrap size={4}>
                {lastOperationReport.cache_hit && <Tag color="green">结果复用</Tag>}
                <Tag>模型调用 {lastOperationReport.model_calls || 0}</Tag>
                <Tag color={(lastOperationReport.errors_after || 0) > 0 ? "error" : "success"}>
                  错误 {lastOperationReport.errors_before || 0} → {lastOperationReport.errors_after || 0}
                </Tag>
                <Tag>警告 {lastOperationReport.warnings_before || 0} → {lastOperationReport.warnings_after || 0}</Tag>
              </Space>
            }
          />
        )}
        {!capabilities.length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有能力编排" /> : (
          <Collapse
            size="small"
            activeKey={expandedCapabilityKeys}
            onChange={(keys) => setExpandedCapabilityKeys((Array.isArray(keys) ? keys : [keys]).map(String))}
          >
            {capabilities.map((cap, idx) => {
              const stepIds = capabilityActualStepIds(cap);
              const capSteps = stepIds.map((sid) => stepById[sid]).filter(Boolean);
              const capParams = capSteps.flatMap((st) => st.params || []);
              const derivedInputSchema = {
                type: "object",
                properties: Object.fromEntries(capParams
                  .filter(paramExposedToCaller)
                  .map((p) => [p.key || p.path, jsonSchemaForParam(p)])),
                required: capParams
                  .filter(paramRequiredFromCaller)
                  .map((p) => p.key || p.path),
              };
              const inputSchema = Object.keys(cap.input_schema?.properties || {}).length
                ? cap.input_schema
                : derivedInputSchema;
              const lastResponse = [...capSteps].reverse().find((st) => st.response_json != null)?.response_json;
              const derivedOutputSchema = lastResponse != null ? inferJsonSchema(lastResponse) : (cap.output_schema || {});
              return (
                <Collapse.Panel
                  key={capabilityPanelKey(cap, idx)}
                  header={
                    <Space wrap id={`capability-${domAnchorPart(cap.name || cap.capability_id || idx)}`}>
                      <Tag color={cap.confirmed ? "success" : "warning"}>{cap.confirmed ? "已确认" : "未确认"}</Tag>
                      <Tag color="blue">{optionLabel(kindOptions, cap.kind || "submit")}</Tag>
                      <Tag color={confidenceColor(cap.confidence)}>置信度 {confidencePercent(cap.confidence)}</Tag>
                      <Typography.Text strong>{cap.title || cap.name || `能力 ${idx + 1}`}</Typography.Text>
                      {cap.name && <Typography.Text code>{cap.name}</Typography.Text>}
                    </Space>
                  }
                  extra={
                    <Space onClick={(e) => e.stopPropagation()}>
                      <Tooltip title="能力上移"><Button size="small" icon={<UpOutlined />} disabled={idx === 0} onClick={() => moveCapability(idx, -1)} /></Tooltip>
                      <Tooltip title="能力下移"><Button size="small" icon={<DownOutlined />} disabled={idx === capabilities.length - 1} onClick={() => moveCapability(idx, 1)} /></Tooltip>
                      <Checkbox checked={!!cap.confirmed} onChange={(e) => updateCapabilityConfirmed(idx, e.target.checked)}>确认</Checkbox>
                      <Tooltip title="删除"><Button size="small" danger icon={<DeleteOutlined />}
                        onMouseDown={(e) => e.preventDefault()} onClick={() => removeCapability(idx)} /></Tooltip>
                    </Space>
                  }
                >
                  <Space direction="vertical" size={12} style={{ width: "100%" }}>
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 10 }}>
                      <FieldControl label="能力名">
                        <EditableText value={cap.name || ""} width="100%" onSave={(v) => updateCapabilityField(idx, "name", v)} />
                      </FieldControl>
                      <FieldControl label="标题">
                        <EditableText value={cap.title || ""} width="100%" onSave={(v) => updateCapabilityField(idx, "title", v)} />
                      </FieldControl>
                      <FieldControl label="类型">
                        <NativeSelect value={cap.kind || "submit"} width="100%" options={kindOptions} onChange={(v) => updateCapabilityField(idx, "kind", v)} />
                      </FieldControl>
                    </div>
                    <FieldControl label="说明">
                      <EditableTextArea rows={3} value={cap.intent || ""} onSave={(v) => updateCapabilityField(idx, "intent", v)} />
                    </FieldControl>
                    <Collapse
                      ghost
                      size="small"
                      activeKey={expandedCapabilitySections[idx] || ["interfaces"]}
                      onChange={(keys) => setExpandedCapabilitySections((current) => ({
                        ...current,
                        [idx]: (Array.isArray(keys) ? keys : [keys]).map(String),
                      }))}
                    >
                      <Collapse.Panel
                        key="interfaces"
                        header={`接口与字段 ${stepIds.length} 接口 / ${capParams.length} 字段`}
                      >
                        {renderCapabilityInterfacesWithFields(cap, idx)}
                      </Collapse.Panel>
                      <Collapse.Panel key="deps" header={`依赖 ${(flowSpec.links || []).filter((l) => stepIds.includes(l.source_step_id) && stepIds.includes(l.target_step_id)).length}`}>
                        {renderCapabilityDependencyEditor(cap)}
                      </Collapse.Panel>
                      <Collapse.Panel key="io" header="调用参数 / 返回结果">
                        {renderCapabilityIOBusinessView(idx, inputSchema, derivedOutputSchema)}
                      </Collapse.Panel>
                    </Collapse>
                  </Space>
                </Collapse.Panel>
              );
            })}
          </Collapse>
        )}
        {capabilityRelations.length > 0 && (
          <Collapse size="small" bordered={false}>
            <Collapse.Panel key="capability-relations" header={`能力关系 ${capabilityRelations.length}`}>
              <Space direction="vertical" size={8} style={{ width: "100%" }}>
                {capabilityRelations.map((relation, index) => {
                  const relationType = relation.mode || relation.type || "external_transform";
                  const owner = relation.transform_owner === "skill" ? "Skill 内部" : "调用方";
                  return (
                    <div key={relation.relation_id || `${relation.from_capability}-${relation.to_capability}-${index}`}
                      style={{ display: "grid", gridTemplateColumns: "minmax(160px, 1fr) auto minmax(160px, 1fr)", gap: 8, alignItems: "center" }}>
                      <Space wrap size={4}>
                        <Tag color="blue">{relation.from_capability || "未指定来源能力"}</Tag>
                        {relation.from_output && <Typography.Text code>{relation.from_output}</Typography.Text>}
                      </Space>
                      <Space direction="vertical" size={0} align="center">
                        <Tag color={relation.confirmed ? "success" : "warning"}>{relationType}</Tag>
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>{owner}负责</Typography.Text>
                      </Space>
                      <Space wrap size={4}>
                        <Tag color="geekblue">{relation.to_capability || "未指定目标能力"}</Tag>
                        {relation.to_input && <Typography.Text code>{relation.to_input}</Typography.Text>}
                        {relation.requires_user_confirmation && <Tag color="orange">需用户确认</Tag>}
                      </Space>
                      {relation.reason && (
                        <Typography.Text type="secondary" style={{ gridColumn: "1 / -1", fontSize: 12 }}>
                          {relation.reason}
                        </Typography.Text>
                      )}
                    </div>
                  );
                })}
              </Space>
            </Collapse.Panel>
          </Collapse>
        )}
      </Space>
    );
  }
  function renderCapabilitiesPanel() {
    if (!flowSpec) return null;
    const capabilities = flowSpec.capabilities || [];
    if (!capabilities.length) {
      return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有业务能力编排" />;
    }
    const schemaBlock = (title: string, schema?: Record<string, any>, emptyText = "未声明") => {
      const rows = schemaFieldRows(schema);
      const fallback = rows.length ? "" : compactJson(schema);
      return (
        <div style={{ minWidth: 220 }}>
          <Typography.Text strong style={{ fontSize: 12 }}>{title}</Typography.Text>
          <Space direction="vertical" size={4} style={{ width: "100%", marginTop: 6 }}>
            {rows.slice(0, 8).map((row) => (
              <Space key={`${title}-${row.name}`} size={4} wrap>
                <Typography.Text code style={{ fontSize: 12 }}>{row.name}</Typography.Text>
                <Tag color="blue">业务类型：{PARAM_TYPE_LABELS[row.businessType] || row.businessType}</Tag>
                <Tag>Wire：{row.wireType}</Tag>
                {row.required && <Tag color="red">必填</Tag>}
                {row.description && <Typography.Text type="secondary" style={{ fontSize: 12 }}>{row.description}</Typography.Text>}
              </Space>
            ))}
            {rows.length > 8 && <Typography.Text type="secondary" style={{ fontSize: 12 }}>+{rows.length - 8} 个字段</Typography.Text>}
            {!rows.length && fallback && <Typography.Text code style={{ fontSize: 12 }}>{fallback}</Typography.Text>}
            {!rows.length && !fallback && <Typography.Text type="secondary" style={{ fontSize: 12 }}>{emptyText}</Typography.Text>}
          </Space>
        </div>
      );
    };
    return (
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Alert
          type="info"
          showIcon
          message="业务能力编排"
          description="这里展示外部调用方看到的能力层；接口、字段、依赖和输入输出都应在能力卡片内维护。"
        />
        <List
          dataSource={capabilities}
          renderItem={(cap, idx) => {
            const stepIds = capabilityActualStepIds(cap);
            const mappings = cap.output_mapping || [];
            const preconditions = cap.preconditions || [];
            const capSteps = stepIds.map((stepId) => stepById[stepId]).filter(Boolean);
            const capParams = capSteps.flatMap((step) => step.params || []);
            const derivedInputSchema = {
              type: "object",
              properties: Object.fromEntries(capParams
                .filter(paramExposedToCaller)
                .map((param) => [param.key || param.path, jsonSchemaForParam(param)])),
              required: capParams
                .filter(paramRequiredFromCaller)
                .map((param) => param.key || param.path),
            };
            const lastResponse = [...capSteps].reverse().find((step) => step.response_json != null)?.response_json;
            const inputSchema = Object.keys(cap.input_schema?.properties || {}).length
              ? cap.input_schema
              : derivedInputSchema;
            const outputSchema = Object.keys(cap.output_schema?.properties || {}).length
              ? cap.output_schema
              : (lastResponse != null ? inferJsonSchema(lastResponse) : {});
            return (
              <List.Item
                style={{ paddingLeft: 0, paddingRight: 0 }}
                actions={[
                  <Checkbox
                    key="confirmed"
                    checked={!!cap.confirmed}
                    onChange={(e) => updateCapabilityConfirmed(idx, e.target.checked)}
                  >
                    已确认
                  </Checkbox>,
                ]}
              >
                <div style={{ width: "100%", border: "1px solid #f0f0f0", borderRadius: 6, padding: 12, background: "#fff" }}>
                  <Space direction="vertical" size={10} style={{ width: "100%" }}>
                    <Row gutter={[12, 8]} align="top">
                      <Col flex="auto">
                        <Space wrap size={6}>
                          <Tag color={cap.confirmed ? "success" : "warning"}>{cap.confirmed ? "已确认" : "待确认"}</Tag>
                          {cap.requires_human_confirm && <Tag color="orange">需要人工确认</Tag>}
                          {cap.kind && <Tag color="blue">{cap.kind}</Tag>}
                          <Tag color={confidenceColor(cap.confidence)}>置信度 {confidencePercent(cap.confidence)}</Tag>
                          <Typography.Text strong>{cap.title || cap.name || `能力 ${idx + 1}`}</Typography.Text>
                          {cap.name && <Typography.Text code>{cap.name}</Typography.Text>}
                        </Space>
                        {cap.intent && <Typography.Text type="secondary" style={{ display: "block", marginTop: 6, fontSize: 12 }}>{cap.intent}</Typography.Text>}
                      </Col>
                    </Row>
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 12 }}>
                      {schemaBlock("输入", inputSchema, "未声明输入")}
                      {schemaBlock("输出", outputSchema, "未声明输出")}
                      <div>
                        <Typography.Text strong style={{ fontSize: 12 }}>步骤</Typography.Text>
                        <Space direction="vertical" size={4} style={{ width: "100%", marginTop: 6 }}>
                          {stepIds.length ? stepIds.map((stepId) => {
                            const st = stepById[stepId];
                            const requestRef = capabilityRequestRefForStep(cap, stepId);
                            return (
                              <Space key={stepId} size={4} wrap>
                                <Tag>{st?.name || st?.path || stepId}</Tag>
                                {st && <PathText value={st.path || stripHost(st.url)} maxWidth={260} />}
                                <Tag color="blue">用途：{capabilityUsageLabel(requestRef?.usage)}</Tag>
                                {requestRef?.pinned && <Tag color="gold">手工锁定</Tag>}
                              </Space>
                            );
                          }) : <Typography.Text type="secondary" style={{ fontSize: 12 }}>未绑定步骤</Typography.Text>}
                        </Space>
                      </div>
                    </div>
                    {(mappings.length > 0 || preconditions.length > 0) && (
                      <Collapse ghost size="small">
                        {mappings.length > 0 && (
                          <Collapse.Panel key="mapping" header={`输出映射 ${mappings.length}`}>
                            <Space direction="vertical" size={4} style={{ width: "100%" }}>
                              {mappings.map((item, mapIdx) => (
                                <Typography.Text key={mapIdx} code style={{ fontSize: 12 }}>{compactJson(item, 240)}</Typography.Text>
                              ))}
                            </Space>
                          </Collapse.Panel>
                        )}
                        {preconditions.length > 0 && (
                          <Collapse.Panel key="preconditions" header={`前置条件 ${preconditions.length}`}>
                            <Space direction="vertical" size={4} style={{ width: "100%" }}>
                              {preconditions.map((item, preIdx) => (
                                <Typography.Text key={preIdx} code style={{ fontSize: 12 }}>{compactJson(item, 240)}</Typography.Text>
                              ))}
                            </Space>
                          </Collapse.Panel>
                        )}
                      </Collapse>
                    )}
                  </Space>
                </div>
              </List.Item>
            );
          }}
        />
      </Space>
    );
  }
  function renderReviewPanel() {
    if (!flowSpec) return null;
    if (!reviewItems.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有待确认项" />;
    const highCount = reviewItems.length;
    const llmMeta = (flowSpec.meta as any)?.llm_recommendations || {};
    const llmSuggestionCount = reviewItems.reduce((n, item) => n + (item.llm_suggestions?.length || 0), 0);
    return (
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Card size="small" style={{ background: "#fafafa" }}>
          <Row gutter={[12, 8]} align="middle">
            <Col flex="auto">
              <Space wrap>
                <Typography.Text strong>无法自动匹配字段 {reviewItems.length} 项</Typography.Text>
                <Tag color="error">{highCount} 高风险</Tag>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  第一层规则已自动处理可确定项；第二层可刷新 LLM 辅助推荐；第三层由你确认后才生效。
                </Typography.Text>
                {llmMeta.status && <Tag color={llmMeta.status === "ok" ? "blue" : "default"}>
                  智能推荐 {llmMeta.status}{llmSuggestionCount ? ` ${llmSuggestionCount} 条` : ""}
                </Tag>}
              </Space>
            </Col>
            <Col>
              <Space wrap>
                <Button icon={<RobotOutlined />} loading={autoFixBusy} onClick={refreshLlmRecommendations}>自动修复建议</Button>
                <Button type="primary" onClick={() => bulkReview("accept")}>全部采纳</Button>
                <Button onClick={() => bulkReview("ignore")}>全部忽略</Button>
              </Space>
            </Col>
          </Row>
        </Card>
        <List
          size="small"
          dataSource={reviewItems}
          renderItem={(item) => {
            const manualBind = requiresManualSourceBinding(item);
            return (
                <List.Item
                  id={`link-${domAnchorPart(link.link_id)}`}
                  actions={[
                  manualBind
                    ? <Button key="bind" size="small" type="primary" onClick={() => setActiveFlowTab("abilities")}>去能力卡片绑定</Button>
                    : <Button key="apply" size="small" type="primary" onClick={() => applyReviewSuggestion(item)}>采纳</Button>,
                  <Button key="skip" size="small" onClick={() => resolveReview(item.id, true)}>忽略</Button>,
                ]}
              >
                <Space direction="vertical" size={4} style={{ width: "100%" }}>
                  <Space wrap>
                    <Tag color={severityColor(item.severity)}>{item.severity || "medium"}</Tag>
                    <Tag>{item.type}</Tag>
                    <Typography.Text strong>{item.title}</Typography.Text>
                    {item.current_guess && <Tag>{item.current_guess}</Tag>}
                  </Space>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>{item.reason}</Typography.Text>
                  {!!item.llm_suggestions?.length && (
                    <Space direction="vertical" size={6} style={{ width: "100%" }}>
                      {item.llm_suggestions.map((s, idx) => (
                        <div key={`${item.id}-llm-${idx}`} style={{ border: "1px solid #e6f4ff", background: "#f6fbff", padding: 8, borderRadius: 6 }}>
                          <Space wrap>
                            <Tag color="blue">LLM 建议</Tag>
                            <Tag>{s.action}</Tag>
                            <Tag color={confidenceColor(s.confidence)}>置信度 {confidencePercent(s.confidence)}</Tag>
                            {s.source_step_id && <Typography.Text code>{s.source_step_id}</Typography.Text>}
                            {s.source_path && <Typography.Text code>{s.source_path}</Typography.Text>}
                            {s.source_kind && <Typography.Text code>{s.source_kind}</Typography.Text>}
                            <Button size="small" type="primary" onClick={() => applyLlmSuggestion(item, s)}>采纳此建议</Button>
                          </Space>
                          {s.reason && <div style={{ marginTop: 4 }}>
                            <Typography.Text type="secondary" style={{ fontSize: 12 }}>{s.reason}</Typography.Text>
                          </div>}
                        </div>
                      ))}
                    </Space>
                  )}
                </Space>
              </List.Item>
            );
          }}
        />
      </Space>
    );
  }
  function renderStepsPanel() {
    if (!flowSpec) return null;
    return (
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Space wrap>
          <Button icon={<PlusOutlined />} onClick={() => setAddingStep(true)}>新增步骤</Button>
          <Button icon={<ReloadOutlined />} onClick={() => send({ type: "flow_update", edits: [{ op: "dedupe_steps" }] })}>清理重复步骤</Button>
        </Space>
        {addingStep && (
          <Card size="small" title="新增步骤">
            <Space direction="vertical" size={10} style={{ width: "100%" }}>
              <Space wrap>
                <Typography.Text strong style={{ fontSize: 12 }}>从捕获接口选择</Typography.Text>
                <Select
                  allowClear
                  showSearch
                  placeholder="选择已捕获的接口"
                  notFoundContent="没有可添加的捕获接口"
                  style={{ minWidth: 520 }}
                  value={newStepRequestKey || undefined}
                  optionFilterProp="label"
                  options={allCapturedRequests(flowSpec).map((req) => ({
                    label: `${req.method || "GET"} ${req.path || stripHost(req.url || "")}`,
                    value: requestOptionValue(req),
                  })).filter((x) => x.value)}
                  onChange={(v) => setNewStepRequestKey(v || "")}
                />
                <Button type="primary" onClick={addStep} disabled={!newStepRequestKey && !newStep.path.trim()}>添加</Button>
                <Button onClick={() => { setAddingStep(false); setNewStepRequestKey(""); }}>取消</Button>
              </Space>
              <Collapse ghost size="small">
                <Collapse.Panel key="manual" header="手工新增接口">
                  <Space wrap>
                    <NativeSelect value={newStep.method} width={110} options={["GET", "POST", "PUT", "PATCH", "DELETE"].map((x) => ({ label: x, value: x }))}
                      onChange={(v) => setNewStep((s) => ({ ...s, method: v }))} />
                    <Input placeholder="/api/path" value={newStep.path} style={{ width: 260 }}
                      onChange={(e) => setNewStep((s) => ({ ...s, path: e.target.value }))} />
                    <Input placeholder="步骤名" value={newStep.name} style={{ width: 180 }}
                      onChange={(e) => setNewStep((s) => ({ ...s, name: e.target.value }))} />
                    <NativeSelect value={newStep.role} width={160} options={STEP_ROLE_OPTIONS}
                      onChange={(v) => setNewStep((s) => ({ ...s, role: v }))} />
                    <NativeSelect value={newStep.risk_level} width={90} options={RISK_OPTIONS}
                      onChange={(v) => setNewStep((s) => ({ ...s, risk_level: v }))} />
                  </Space>
                </Collapse.Panel>
              </Collapse>
            </Space>
          </Card>
        )}
        <Collapse size="small">
          {flowSpec.steps.map((step, idx) => (
            <Collapse.Panel
              key={step.step_id}
              header={
                <Space wrap style={{ minWidth: 0, maxWidth: "100%" }}>
                  <Tag color="purple">第 {idx + 1} 步</Tag>
                  <Tag color={step.method === "GET" ? "blue" : "green"}>{step.method}</Tag>
                  <Typography.Text strong style={{ maxWidth: 220 }} ellipsis={{ tooltip: step.name || fallbackStepName(step.method, step.path) }}>
                    {step.name || fallbackStepName(step.method, step.path)}
                  </Typography.Text>
                  <PathText value={step.path || stripHost(step.url)} maxWidth={420} />
                  <Tag>{step.params?.length || 0} 字段</Tag>
                </Space>
              }
              extra={
                <Space onClick={(e) => e.stopPropagation()}>
                  <Tooltip title="上移"><Button size="small" icon={<UpOutlined />} disabled={idx === 0} onClick={() => moveStep(idx, -1)} /></Tooltip>
                  <Tooltip title="下移"><Button size="small" icon={<DownOutlined />} disabled={idx === flowSpec.steps.length - 1} onClick={() => moveStep(idx, 1)} /></Tooltip>
                  <Tooltip title="删除"><Button size="small" danger icon={<DeleteOutlined />}
                    onMouseDown={(e) => e.preventDefault()} onClick={() => removeStepWithConfirm(step)} /></Tooltip>
                </Space>
              }
            >
              <Space direction="vertical" size={10} style={{ width: "100%" }}>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 10, alignItems: "end" }}>
                  <FieldControl label="名称">
                    <EditableText value={step.name || ""} width={220}
                      onSave={(v) => updateStep(step.step_id, "name", v)} />
                  </FieldControl>
                  <FieldControl label="角色">
                    <NativeSelect value={step.source_meta?.role || step.semantic_role || ""}
                      width="100%"
                      options={[{ label: "未设置", value: "" }, ...STEP_ROLE_OPTIONS]}
                      onChange={(v) => updateStep(step.step_id, "role", v)} />
                  </FieldControl>
                  <FieldControl label="风险">
                    <NativeSelect value={step.risk_level} width="100%" options={RISK_OPTIONS}
                      onChange={(v) => updateStep(step.step_id, "risk_level", v)} />
                  </FieldControl>
                </div>
                <Collapse ghost size="small">
                  <Collapse.Panel key="advanced" header="高级设置">
                    <Space direction="vertical" size={8} style={{ width: "100%" }}>
                      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 10, alignItems: "end" }}>
                        <FieldControl label="方法">
                          <NativeSelect value={step.method} width="100%"
                            options={["GET", "POST", "PUT", "PATCH", "DELETE"].map((x) => ({ label: x, value: x }))}
                            onChange={(v) => updateStep(step.step_id, "method", v)} />
                        </FieldControl>
                        <FieldControl label="Path / URL">
                          <EditableText value={step.path} width="100%"
                            onSave={(v) => {
                              if (v) {
                                updateStep(step.step_id, "path", v);
                                updateStep(step.step_id, "url", v);
                              }
                            }} />
                        </FieldControl>
                        <FieldControl label="内容类型">
                          <NativeSelect value={step.content_type || "application/json"} width="100%"
                            options={CT_OPTIONS} onChange={(v) => updateStep(step.step_id, "content_type", v)} />
                        </FieldControl>
                      </div>
                      <EditableTextArea value={step.body_source || ""}
                        placeholder="请求体模板。通常不需要手改，只有录制体缺失或需要高级修复时再改。"
                        onSave={(v) => updateStep(step.step_id, "body_source", v)} />
                      <HeadersEditor value={step.headers || {}} onChange={(h) => updateStep(step.step_id, "headers", h)} />
                    </Space>
                  </Collapse.Panel>
                </Collapse>
              </Space>
            </Collapse.Panel>
          ))}
        </Collapse>
      </Space>
    );
  }
  function renderParamsPanel() {
    if (!flowSpec) return null;
    const fieldRequestOptions = capturedRequestOptions(flowSpec);
    return (
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Card size="small" styles={{ body: { padding: 10 } }}>
          <Space wrap align="center" size={8}>
            <Typography.Text strong>从捕获接口添加字段</Typography.Text>
            <NativeSelect
              value={newParamRequestKey || ""}
              width={460}
              options={[{ label: fieldRequestOptions.length ? "选择未纳入能力字段的接口" : "没有可添加的捕获接口", value: "" }, ...fieldRequestOptions]}
              onChange={(v) => setNewParamRequestKey(v || "")}
            />
            <Button
              type="primary"
              disabled={!newParamRequestKey}
              onClick={() => addCapturedRequestToFields(findCapturedRequest(flowSpec, newParamRequestKey))}
            >
              加入能力字段
            </Button>
          </Space>
        </Card>
        <Card size="small">
          <Space wrap>
            <NativeSelect value={newParam.step_id || ""} width={320}
              options={[{ label: "选择步骤", value: "" }, ...stepOptions]}
              onChange={(v) => setNewParam((s) => ({ ...s, step_id: v }))} />
            <Input placeholder="字段路径" value={newParam.path} style={{ width: 180 }}
              onChange={(e) => setNewParam((s) => ({ ...s, path: e.target.value }))} />
            <Input placeholder="参数名" value={newParam.key} style={{ width: 160 }}
              onChange={(e) => setNewParam((s) => ({ ...s, key: e.target.value }))} />
            <NativeSelect value={newParam.type} width={120} options={PARAM_TYPE_OPTIONS}
              onChange={(v) => setNewParam((s) => ({ ...s, type: v }))} />
            <NativeSelect value={newParam.category} width={130} options={CATEGORY_OPTIONS}
              onChange={(v) => setNewParam((s) => ({ ...s, category: v }))} />
            <Button type="primary" onClick={addParam}>添加</Button>
          </Space>
        </Card>
        <Collapse size="small">
          {flowSpec.steps.map((step) => (
            <Collapse.Panel
              key={step.step_id}
              header={
                <Space wrap style={{ minWidth: 0, maxWidth: "100%" }}>
                  <ApiOutlined />
                  <Tag color={step.method === "GET" ? "blue" : "green"}>{step.method}</Tag>
                  <Typography.Text strong style={{ maxWidth: 220 }} ellipsis={{ tooltip: step.name || step.path }}>
                    {step.name || step.path}
                  </Typography.Text>
                  <PathText value={step.path} maxWidth={420} />
                  <Tag>{step.params?.length || 0} 字段</Tag>
                </Space>
              }
              extra={
                <Button
                  size="small"
                  danger
                  onClick={(e) => {
                    e.stopPropagation();
                    send({ type: "flow_update", edits: [{ op: "remove_step", step_id: step.step_id }] });
                  }}
                >
                  删除接口
                </Button>
              }
            >
            {(step.params || []).length === 0 ? (
              <Space direction="vertical" size={8} style={{ width: "100%" }}>
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="这个接口没有请求入参" />
                {step.response_json != null && (
                  <Card size="small" title="响应字段">
                    <Space wrap size={4}>
                      {leafPathValues(step.response_json).slice(0, 40).map((leaf, leafIdx) => (
                        <Tooltip key={`${leaf.path}-${leafIdx}`} title={leaf.value}>
                          <Typography.Text code style={{ fontSize: 12 }}>{leaf.path}</Typography.Text>
                        </Tooltip>
                      ))}
                      {leafPathValues(step.response_json).length > 40 && <Tag>+{leafPathValues(step.response_json).length - 40}</Tag>}
                    </Space>
                  </Card>
                )}
              </Space>
            ) : (
              <List
                size="small"
                rowKey={(p) => paramDraftKey(step.step_id, p)}
                dataSource={step.params}
                renderItem={(p) => {
                  const bindKey = paramDraftKey(step.step_id, p);
                  const linked = incomingLink(step.step_id, p.path);
                  const currentBind = bindDraft[bindKey] || {
                    source_step_id: p.source?.step_id || linked?.source_step_id,
                    source_path: p.source?.response_path || linked?.source_path,
                  };
                  const needsManualConfirm = !!p.need_human_confirm && p.category === "runtime_var" && p.source_kind === "unknown";
                  const selectBinding = selectBindingForParam(step, p);
                  const enumOptions = enumOptionsForParam(step, p);
                  const enumSelectOptions = enumOptions.map((x) => ({ label: x, value: x }));
                  const isApiOption = p.source_kind === "api_option";
                  const isTypedEnum = p.type === "enum" || p.type === "list-enum";
                  const isEnumOption = ENUM_SOURCE_KINDS.includes(p.source_kind || "") || isTypedEnum;
                  const hasBindingPanel = isApiOption || isEnumOption;
                  const hasRuntimePanel = !!linked || p.category === "runtime_var" || p.source_kind === "previous_response";
                  const sourceStepOptions = [
                    { label: "选择来源步骤", value: "" },
                    ...flowSpec.steps.filter((s) => s.step_id !== step.step_id).map((s) => ({
                      label: `${s.name || s.path} · ${s.method} ${s.path}`,
                      value: s.step_id,
                    })),
                  ];
                  const sourceRespOptions = [
                    { label: currentBind.source_step_id ? "选择响应字段" : "先选择来源步骤", value: "" },
                    ...sourcePathOptions(currentBind.source_step_id),
                  ];
                  return (
                    <List.Item style={{ padding: "12px 0" }}>
                      <div style={{ width: "100%", border: "1px solid #f0f0f0", borderRadius: 6, padding: 12, background: "#fff" }}>
                        <Row gutter={[12, 8]} align="top">
                          <Col flex="auto">
                            <Space wrap size={6}>
                              <Tag color={p.category === "runtime_var" ? "gold" : p.category === "system_const" ? "default" : "blue"}>{p.path}</Tag>
                              <Tag>{optionLabel(CATEGORY_OPTIONS, p.category || "user_param")}</Tag>
                              <Tag>{optionLabel(SOURCE_KIND_OPTIONS, normalizeSourceKindForUi(p.source_kind) || "unknown")}</Tag>
                              {linked && <Tag color="cyan">依赖字段</Tag>}
                              {isApiOption && <Tag color="geekblue">接口候选</Tag>}
                              {isEnumOption && enumOptions.length > 0 && <Tag color="purple">枚举</Tag>}
                              {p.page_required === true && <Tag color="red">页面必填</Tag>}
                              {p.page_required === false && <Tag>页面可选</Tag>}
                              {p.page_required == null && <Tag color="gold">页面必填性未确认</Tag>}
                              {paramRequiredFromCaller(p)
                                ? <Tag color="volcano">调用方必填</Tag>
                                : <Tag color="green">调用方无需必填</Tag>}
                              {needsManualConfirm && <Tag color="warning">待确认</Tag>}
                              <Typography.Text type="secondary" style={{ fontSize: 12 }}>{p.reason}</Typography.Text>
                            </Space>
                            <Typography.Text type="secondary" style={{ display: "block", marginTop: 6, fontSize: 12 }}>
                              {paramSourceText(step, p, linked)}
                            </Typography.Text>
                          </Col>
                          <Col>
                            <Space size={6} wrap>
                              <Button size="small" danger onMouseDown={(e) => e.preventDefault()}
                                onClick={() => removeParam(step.step_id, p)}>删除</Button>
                            </Space>
                          </Col>
                        </Row>
                        <div style={{
                          display: "grid",
                          gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
                          gap: 10,
                          alignItems: "end",
                          marginTop: 10,
                        }}>
                          <FieldControl label="名称">
                            <EditableText value={p.key} width="100%" onSave={(v) => v && updateParam(step.step_id, p, "key", v)} />
                          </FieldControl>
                          <FieldControl label="路径">
                            <EditableText value={p.path} width="100%" onSave={(v) => v && updateParam(step.step_id, p, "path", v)} />
                          </FieldControl>
                          <FieldControl label="默认值">
                            {enumOptions.length > 0 ? (
                              <EnumValueInput value={String(p.value ?? "")} width="100%"
                                options={enumSelectOptions}
                                onSave={(v) => updateParam(step.step_id, p, "value", v)} />
                            ) : (
                              <EditableText value={String(p.value ?? "")} width="100%" onSave={(v) => updateParam(step.step_id, p, "value", v)} />
                            )}
                          </FieldControl>
                          <FieldControl label="类型">
                            <NativeSelect value={p.type} width="100%" options={PARAM_TYPE_OPTIONS}
                              onChange={(v) => updateParamType(step, p, v)} />
                          </FieldControl>
                          <FieldControl label="分类">
                            <NativeSelect value={p.category || "user_param"} width="100%" options={CATEGORY_OPTIONS}
                              onChange={(v) => updateParamCategory(step.step_id, p, v)} />
                          </FieldControl>
                          <FieldControl label="来源">
                            <NativeSelect value={normalizeSourceKindForUi(p.source_kind) || defaultSourceForCategory(p.category || "user_param")} width="100%" options={sourceSelectOptionsForParam(p)}
                              onChange={(v) => updateParamSourceKind(step.step_id, p, v)} />
                          </FieldControl>
                          <FieldControl label="调用方必填">
                            {paramExposedToCaller(p) ? (
                              <Checkbox checked={!!p.required} onChange={(e) => updateParam(step.step_id, p, "required", e.target.checked)}>调用时必须提供</Checkbox>
                            ) : <Typography.Text type="secondary">由流程运行期提供</Typography.Text>}
                          </FieldControl>
                          <FieldControl label="展示">
                            {p.category === "user_param" ? (
                              <Checkbox checked={p.exposed_to_user !== false} onChange={(e) => updateParam(step.step_id, p, "exposed_to_user", e.target.checked)}>暴露给用户</Checkbox>
                            ) : <Typography.Text type="secondary">不对调用方展示</Typography.Text>}
                          </FieldControl>
                        </div>
                        {needsManualConfirm && <Button size="small" style={{ marginTop: 8 }} onClick={() => updateParam(step.step_id, p, "need_human_confirm", false)}>已确认</Button>}
                        {(hasBindingPanel || hasRuntimePanel) && <Collapse size="small" ghost style={{ marginTop: 10 }}
                          defaultActiveKey={needsManualConfirm ? ["runtime"] : []}>
                          {hasBindingPanel && (
                            <Collapse.Panel key="binding" header={<Space><LinkOutlined />来源绑定</Space>}>
                          <div style={{ background: "#fafafa", border: "1px solid #f0f0f0", borderRadius: 6, padding: 8 }}>
                            <Space direction="vertical" size={8} style={{ width: "100%" }}>
                              <Space wrap size={6}>
                                <Typography.Text strong style={{ fontSize: 12 }}>{isApiOption ? "接口候选配置" : "枚举候选配置"}</Typography.Text>
                                <Tag color={selectBinding?.source_url ? "geekblue" : "purple"}>{enumSourceLabel(selectBinding)}</Tag>
                                {enumOptions.slice(0, 8).map((x, enumIdx) => <Tag key={`${x}-${enumIdx}`}>{x}</Tag>)}
                                {enumOptions.length > 8 && <Tag>+{enumOptions.length - 8}</Tag>}
                              </Space>
                              {isApiOption && (
                                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 8, alignItems: "end" }}>
                                  <FieldControl label="来源接口">
                                    <EditableComboInput
                                      value={selectBinding?.source_url || ""}
                                      options={readSourceOptions()}
                                      placeholder="选择或输入接口地址"
                                      onSave={(v) => upsertSelectBinding(step, p, { source_url: v })}
                                    />
                                  </FieldControl>
                                  <FieldControl label="接口参数">
                                    <EditableTextArea
                                      rows={1}
                                      value={queryToLines(selectBinding?.source_url || "")}
                                      placeholder="每行一个参数，如 pageNo=1"
                                      onSave={(v) => upsertSelectBinding(step, p, { source_url: mergeUrlQuery(selectBinding?.source_url || "", v) })}
                                    />
                                  </FieldControl>
                                  <FieldControl label="值字段">
                                    <EditableComboInput
                                      value={selectBinding?.value_key || ""}
                                      options={responseKeyOptionsForSource(selectBinding?.source_url)}
                                      placeholder="如 id/userId/dictValue"
                                      onSave={(v) => upsertSelectBinding(step, p, { value_key: v })}
                                    />
                                  </FieldControl>
                                  <FieldControl label="显示字段">
                                    <EditableComboInput
                                      value={selectBinding?.label_key || ""}
                                      options={responseKeyOptionsForSource(selectBinding?.source_url)}
                                      placeholder="如 name/label/dictLabel"
                                      onSave={(v) => upsertSelectBinding(step, p, { label_key: v })}
                                    />
                                  </FieldControl>
                                  <FieldControl label="配对 ID 字段">
                                    <EditableComboInput
                                      value={selectBinding?.id_path || p.path || p.key || ""}
                                      options={(step.params || []).map((x) => ({ label: `${x.path} · ${x.key}`, value: x.path }))}
                                      placeholder="默认当前字段路径，可改为隐藏 ID 字段"
                                      onSave={(v) => upsertSelectBinding(step, p, { id_path: v || null })}
                                    />
                                  </FieldControl>
                                  <FieldControl label="多选">
                                    <Checkbox checked={!!selectBinding?.multi || p.type === "list-enum"}
                                      onChange={(e) => upsertSelectBinding(step, p, { multi: e.target.checked })}>
                                      列表多选
                                    </Checkbox>
                                  </FieldControl>
                                </div>
                              )}
                              {isEnumOption && (
                                <FieldControl label="枚举候选">
                                  <EditableTextArea
                                    rows={3}
                                    value={enumOptionsTextForParam(step, p)}
                                    placeholder="每行一个候选项；提交短码时写成 病假=2"
                                    onSave={(v) => {
                                      const { options, optionMap } = parseEnumOptionsText(v);
                                      upsertSelectBinding(
                                        step,
                                        p,
                                        {
                                          source_url: "",
                                          value_key: "",
                                          label_key: "",
                                          options,
                                          count: options.length,
                                          option_map: optionMap,
                                          enum_source: enumSourceForKind(p.source_kind),
                                          enum_confirmed: true,
                                        },
                                        enumOptionEdits(step, p, options, optionMap || undefined),
                                      );
                                    }}
                                  />
                                </FieldControl>
                              )}
                            </Space>
                          </div>
                            </Collapse.Panel>
                          )}
                        {hasRuntimePanel && (
                            <Collapse.Panel key="runtime" header={<Space><BranchesOutlined />运行期来源</Space>}>
                          <div style={{ background: "#fafafa", border: "1px solid #f0f0f0", borderRadius: 6, padding: 10 }}>
                            {p.source_kind === "previous_response" || linked ? (
                              <Space wrap>
                                <Typography.Text strong style={{ fontSize: 12 }}>上游响应</Typography.Text>
                                <NativeSelect value={currentBind.source_step_id || ""} width={300}
                                  options={sourceStepOptions}
                                  onChange={(v) => setBindDraft((d) => ({ ...d, [bindKey]: { ...currentBind, source_step_id: v, source_path: "" } }))} />
                                <ComboInput value={currentBind.source_path || ""} width={280}
                                  options={sourceRespOptions}
                                  disabled={!currentBind.source_step_id}
                                  placeholder={currentBind.source_step_id ? "选择或输入响应字段，如 data.id" : "先选择来源步骤"}
                                  onChange={(v) => setBindDraft((d) => ({ ...d, [bindKey]: { ...currentBind, source_path: v } }))} />
                                <Button size="small" type="primary" icon={<LinkOutlined />} onClick={() => bindParamToPreviousResponse(step, p)}>绑定上游响应</Button>
                              </Space>
                            ) : p.source_kind === "page_context" ? (
                              <FieldControl label="调用上下文键">
                                <EditableText value={(p.source as any)?.context_key || p.key || ""} width={320}
                                  onSave={(v) => updateRuntimeSourceDetail(step.step_id, p, { context_key: v })} />
                              </FieldControl>
                            ) : p.source_kind === "request_header" ? (
                              <FieldControl label="请求头名称">
                                <EditableText value={(p.source as any)?.header || ""} width={320}
                                  onSave={(v) => updateRuntimeSourceDetail(step.step_id, p, { header: v })} />
                              </FieldControl>
                            ) : p.source_kind === "system_generated" ? (
                              <FieldControl label="生成策略">
                                <NativeSelect value={(p.source as any)?.strategy || "uuid"} width={260}
                                  options={[
                                    { label: "UUID", value: "uuid" },
                                    { label: "随机字符串", value: "random_string" },
                                    { label: "随机数字", value: "random_number" },
                                  ]}
                                  onChange={(v) => updateRuntimeSourceDetail(step.step_id, p, { strategy: v })} />
                              </FieldControl>
                            ) : p.source_kind === "computed" ? (
                              <Typography.Text type="secondary">
                                运行期按规则 {(p.source as any)?.strategy || "未配置"}，根据 {(p.source as any)?.start_field || "开始字段"} 与 {(p.source as any)?.end_field || "结束字段"} 自动计算。
                              </Typography.Text>
                            ) : p.source_kind === "current_user" ? (
                              <Typography.Text type="secondary">运行期从当前登录身份注入。</Typography.Text>
                            ) : p.source_kind === "system_time" ? (
                              <Typography.Text type="secondary">运行期生成当前系统时间。</Typography.Text>
                            ) : (
                              <Typography.Text type="warning">请选择并配置明确的运行期来源。</Typography.Text>
                            )}
                          </div>
                            </Collapse.Panel>
                        )}
                        </Collapse>}
                      </div>
                    </List.Item>
                  );
                }}
              />
            )}
            </Collapse.Panel>
          ))}
        </Collapse>
      </Space>
    );
  }
  function renderLinksPanel() {
    if (!flowSpec) return null;
    return (
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Card size="small" title={<Space><PlusOutlined />新增依赖</Space>}>
          <Row gutter={[8, 8]} align="middle">
            <Col span={6}><NativeSelect value={newLink.source_step_id || ""} width="100%"
              options={[{ label: "选择来源步骤", value: "" }, ...stepOptions]}
              onChange={(v) => setNewLink((s) => ({ ...s, source_step_id: v, source_path: "" }))} /></Col>
            <Col span={6}><ComboInput value={newLink.source_path || ""} width="100%"
              options={[{ label: newLink.source_step_id ? "选择来源响应字段" : "先选择来源步骤", value: "" }, ...sourcePathOptions(newLink.source_step_id)]}
              disabled={!newLink.source_step_id}
              placeholder={newLink.source_step_id ? "选择或输入来源响应字段" : "先选择来源步骤"}
              onChange={(v) => setNewLink((s) => ({ ...s, source_path: v }))} /></Col>
            <Col span={5}><NativeSelect value={newLink.target_step_id || ""} width="100%"
              options={[{ label: "选择目标步骤", value: "" }, ...stepOptions]}
              onChange={(v) => setNewLink((s) => ({ ...s, target_step_id: v, target_path: "" }))} /></Col>
            <Col span={5}><ComboInput value={newLink.target_path || ""} width="100%"
              options={[{ label: newLink.target_step_id ? "选择目标字段" : "先选择目标步骤", value: "" }, ...targetPathOptions(newLink.target_step_id)]}
              disabled={!newLink.target_step_id}
              placeholder={newLink.target_step_id ? "选择或输入目标字段" : "先选择目标步骤"}
              onChange={(v) => setNewLink((s) => ({ ...s, target_path: v }))} /></Col>
            <Col span={2}><Button type="primary" block onClick={addLink}>添加</Button></Col>
          </Row>
        </Card>
        {!flowSpec.links?.length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有跨接口依赖" /> : (
          <List
            dataSource={flowSpec.links}
            renderItem={(link) => {
              const editing = editingLink[link.link_id];
              const sourceStep = stepById[link.source_step_id];
              const targetStep = stepById[link.target_step_id];
              return (
                <List.Item style={{ paddingLeft: 0, paddingRight: 0 }}
                  actions={editing ? [
                    <Button key="save" size="small" type="primary" icon={<SaveOutlined />} onClick={() => saveLinkEdits(link.link_id)}>保存</Button>,
                    <Button key="cancel" size="small" onClick={() => cancelEditLink(link.link_id)}>取消</Button>,
                  ] : [
                    <Button key="edit" size="small" icon={<SettingOutlined />} onClick={() => startEditLink(link)}>编辑</Button>,
                    <Checkbox key="cf" checked={!!link.confirmed}
                      onChange={(e) => send({ type: "flow_update", edits: [{ op: "update", link_id: link.link_id, field: "confirmed", value: e.target.checked }] })}>已确认</Checkbox>,
                    <Button key="rm" size="small" danger onClick={() => send({ type: "flow_update", edits: [{ op: "remove", link_id: link.link_id, reset_target: true }] })}>删除</Button>,
                  ]}
                >
                  {editing ? (
                    <Row gutter={[8, 8]} style={{ width: "100%" }} align="middle">
                      <Col span={6}><NativeSelect value={editing.source_step_id || ""} width="100%"
                        options={[{ label: "选择来源步骤", value: "" }, ...stepOptions]}
                        onChange={(v) => setEditingLink((d) => ({ ...d, [link.link_id]: { ...editing, source_step_id: v, source_path: "" } }))} /></Col>
                      <Col span={6}><ComboInput value={editing.source_path || ""} width="100%"
                        options={[{ label: "选择来源响应字段", value: "" }, ...sourcePathOptions(editing.source_step_id)]}
                        placeholder="选择或输入来源响应字段"
                        onChange={(v) => setEditingLink((d) => ({ ...d, [link.link_id]: { ...editing, source_path: v } }))} /></Col>
                      <Col span={5}><NativeSelect value={editing.target_step_id || ""} width="100%"
                        options={[{ label: "选择目标步骤", value: "" }, ...stepOptions]}
                        onChange={(v) => setEditingLink((d) => ({ ...d, [link.link_id]: { ...editing, target_step_id: v, target_path: "" } }))} /></Col>
                      <Col span={5}><ComboInput value={stripBodyPrefix(editing.target_path) || ""} width="100%"
                        options={[{ label: "选择目标字段", value: "" }, ...targetPathOptions(editing.target_step_id)]}
                        placeholder="选择或输入目标字段"
                        onChange={(v) => setEditingLink((d) => ({ ...d, [link.link_id]: { ...editing, target_path: v } }))} /></Col>
                      <Col span={2}><Checkbox checked={!!editing.confirmed}
                        onChange={(e) => setEditingLink((d) => ({ ...d, [link.link_id]: { ...editing, confirmed: e.target.checked } }))}>确认</Checkbox></Col>
                    </Row>
                  ) : (
                    <Space direction="vertical" size={4} style={{ width: "100%" }}>
                      <Space wrap>
                        <Tag color="blue">{sourceStep?.name || sourceStep?.path || link.source_step_id}</Tag>
                        <Typography.Text code>{link.source_path}</Typography.Text>
                        <BranchesOutlined />
                        <Tag color="green">{targetStep?.name || targetStep?.path || link.target_step_id}</Tag>
                        <Typography.Text code>{stripBodyPrefix(link.target_path)}</Typography.Text>
                        {link.confirmed ? <Tag color="success">已确认</Tag> : <Tag color="warning">待确认</Tag>}
                      </Space>
                      {link.reason && <Typography.Text type="secondary" style={{ fontSize: 12 }}>{link.reason}</Typography.Text>}
                    </Space>
                  )}
                </List.Item>
              );
            }}
          />
        )}
      </Space>
    );
  }
  function renderDescriptionPanel() {
    if (!flowSpec) return null;
    return (
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Space wrap align="center">
          <Typography.Text strong>最终整体说明</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            面向调用方描述整体能力、输入输出和执行边界。
          </Typography.Text>
          <Button icon={<FileTextOutlined />} type="primary" loading={descBusy} onClick={() => { setDescBusy(true); send({ type: "business_description" }); }}>
            {flowSpec.business_description ? "重新生成整体说明" : "生成整体说明"}
          </Button>
        </Space>
        <FieldControl label="最终标题">
          <Input value={titleDraft}
            onChange={(e) => setTitleDraft(e.target.value)}
            onBlur={(e) => {
              if (e.target.value.trim() !== (flowSpec?.title || "")) {
                const cur = flowSpecRef.current;
                if (cur) {
                  const next = { ...cur, title: e.target.value.trim() };
                  flowSpecRef.current = next;
                  setFlowSpec(next);
                }
                updateFlowField("title", e.target.value.trim());
              }
            }} />
        </FieldControl>
        <Input.TextArea rows={12} value={descDraft}
          onChange={(e) => setDescDraft(e.target.value)}
          onBlur={(e) => {
            if (e.target.value !== (flowSpec?.business_description || "")) {
              const cur = flowSpecRef.current;
              if (cur) {
                const next = { ...cur, business_description: e.target.value };
                flowSpecRef.current = next;
                setFlowSpec(next);
              }
              updateFlowField("business_description", e.target.value);
            }
          }}
          placeholder="生成或手写最终整体说明：包含这个 Skill 能做什么、调用方需要传什么、Skill 会执行哪些查询/提交、最终返回什么。" />
      </Space>
    );
  }
  function renderJsonPanel() {
    return (
      <Space direction="vertical" size={8} style={{ width: "100%" }}>
        <Alert type="info" showIcon message="高级模式：只在需要批量修复或复制排查时使用。常规编辑请优先用前面的步骤、字段和依赖面板。" />
        <Space wrap>
          <Button icon={<ReloadOutlined />} onClick={loadJsonDraft}>载入当前 JSON</Button>
          <Button type="primary" icon={<SaveOutlined />} onClick={applyJsonDraft} disabled={!jsonDraft.trim()}>应用</Button>
          <Button onClick={restoreServerJson}>恢复服务端版本</Button>
          {jsonErr && <Typography.Text type="danger">{jsonErr}</Typography.Text>}
        </Space>
        <Input.TextArea rows={14} value={jsonDraft}
          onChange={(e) => { jsonDirtyRef.current = true; setJsonDraft(e.target.value); }}
          style={{ fontFamily: "monospace", fontSize: 12 }} placeholder="FlowSpec JSON" />
      </Space>
    );
  }

  return (
    <ConfigProvider getPopupContainer={popupContainer}>
    <Card size="small" title="网页录制">
      {phase === "idle" && (
        <>
          <Form.Item label="业务页地址" required style={{ marginBottom: 12 }}>
            <Input value={startUrl} onChange={(e) => setStartUrl(e.target.value)}
              placeholder="https://oa.example.com/reimburse/new" onPressEnter={start} />
          </Form.Item>
          <Space align="center" wrap>
            <Button type="primary" onClick={start} loading={connectionState === "connecting"} disabled={connectionState === "connecting"}>开始录制</Button>
            <Segmented
              value={recordingMode}
              onChange={(v) => setRecordingMode(v as RecordingMode)}
              options={[
                { label: "真实提交", value: "real_submit" },
                { label: "只录制不提交", value: "record_only" },
              ]}
            />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {recordingMode === "record_only" ? "点提交只抓请求，不产生真实记录。" : "点提交会按页面原逻辑真实提交。"}
            </Typography.Text>
          </Space>
          {err && <Alert style={{ marginTop: 12 }} type="error" showIcon message={err} />}
        </>
      )}

      {(phase === "recording" || phase === "publishing") && (
        <div>
          <div style={{
            position: "sticky",
            top: 0,
            zIndex: 20,
            background: "#fff",
            border: "1px solid #f0f0f0",
            borderRadius: 6,
            padding: "8px 10px",
            marginBottom: 8,
            boxShadow: "0 2px 8px rgba(0,0,0,0.04)",
          }}>
            <Space align="center" wrap size={12}>
              <Tag color={connectionState === "connected" ? "processing" : connectionState === "reconnecting" ? "warning" : "error"}>
                {connectionState === "connected" ? (phase === "publishing" ? "发布中" : "录制中") : connectionState === "reconnecting" ? "重连中" : "已断开"}
              </Tag>
              <Button size="small" disabled={phase === "publishing" || connectionState !== "connected"} onClick={resetFromHere}>从这里开始录</Button>
              <Button size="small" onClick={stopAll} disabled={phase === "publishing"}>结束录制</Button>
              <Form.Item label="动作名" required style={{ marginBottom: 0 }}>
                <Tooltip title="每个录制会话自动生成唯一 UUID 动作名，避免与历史资产重复">
                  <Input value={action} readOnly style={{ width: 340, fontFamily: "monospace" }} />
                </Tooltip>
              </Form.Item>
              <Form.Item label="标题" style={{ marginBottom: 0 }}>
                <Input value={title} onChange={(e) => setTitle(e.target.value)} style={{ width: 180 }} />
              </Form.Item>
              <Button type="primary" loading={phase === "publishing"} disabled={connectionState !== "connected" || reconnectedSessionNeedsCapture || (!hasFrame && !steps.length && !reqs.length)} onClick={finalize}>
                停止并分析请求
              </Button>
            </Space>
          </div>
          {connectionState !== "connected" && (
            <Alert
              style={{ marginBottom: 8 }}
              type="warning"
              showIcon
              message={connectionState === "reconnecting" ? "正在重新连接录制浏览器" : "录制连接已断开，当前现场已保留"}
              description={connectionState === "disconnected" ? (
                <Space wrap>
                  <Typography.Text>断线期间最后画面、已录步骤和编辑内容不会被清空。重连会启动新的后端会话，清除旧步骤与分析结果，仅保留画面参考。</Typography.Text>
                  <Button size="small" type="primary" onClick={reconnectRecorder}>重新连接</Button>
                </Space>
              ) : undefined}
            />
          )}
          {connectionState === "connected" && reconnectedSessionNeedsCapture && (
            <Alert
              style={{ marginBottom: 8 }} type="info" showIcon
              message="已连接新录制会话"
              description="旧会话的最后画面仅作视觉参考，旧步骤和分析结果已清除。请在新页面中重新完成操作并触发提交请求。"
            />
          )}
          <div style={{ border: "1px solid #d9d9d9", borderRadius: 6, overflow: "auto", lineHeight: 0, position: "relative", background: "#f5f5f5", textAlign: "center" }}>
            <img ref={imgRef} draggable={false}
              onPointerDown={onImgPointerDown} onPointerMove={onImgPointerMove} onPointerUp={onImgPointerUp} onPointerCancel={onImgPointerCancel}
              onContextMenu={(e) => e.preventDefault()} onWheel={onImgWheel}
              onLoad={(e) => setFrameMeta((current) => ({
                ...current,
                frameWidth: current.frameWidth || e.currentTarget.naturalWidth || undefined,
                frameHeight: current.frameHeight || e.currentTarget.naturalHeight || undefined,
              }))}
              style={{
                width: frameMeta.frameWidth || "auto", maxWidth: "100%", height: "auto",
                display: hasFrame ? "block" : "none", margin: "0 auto", cursor: connectionState === "connected" ? "crosshair" : "not-allowed",
                touchAction: "none", userSelect: "none",
              }} alt="录制画面" />
            {!hasFrame && <div style={{ padding: 40, textAlign: "center", color: "#999", lineHeight: 1.6 }}>等待浏览器画面</div>}
            <input ref={kbRef} onInput={onKbInput} onKeyDown={onKbKeyDown} onPaste={onKbPaste}
              onCompositionStart={onKbCompositionStart} onCompositionUpdate={onKbCompositionUpdate} onCompositionEnd={onKbCompositionEnd}
              autoComplete="off" aria-label="录制画面键盘输入" tabIndex={-1}
              style={{ position: "absolute", left: 0, top: 0, width: 2, height: 2, opacity: 0.01, border: 0, padding: 0, pointerEvents: "none" }} />
          </div>
          {hasFrame && (frameMeta.frameWidth || frameMeta.viewportWidth) && (
            <Typography.Text type="secondary" style={{ display: "block", marginTop: 4, fontSize: 12 }}>
              画面 {frameMeta.frameWidth || "?"}×{frameMeta.frameHeight || "?"}
              {frameMeta.viewportWidth ? ` · 浏览器 ${frameMeta.viewportWidth}×${frameMeta.viewportHeight || "?"}` : ""}
              {frameMeta.deviceScaleFactor ? ` · DPR ${frameMeta.deviceScaleFactor}` : ""}
            </Typography.Text>
          )}

          {fields.length > 0 && !flowSpec && (
            <Alert
              style={{ marginTop: 12 }}
              type="warning"
              showIcon
              message="已抓到提交请求，但还没有生成 FlowSpec"
              description={
                <Space wrap>
                  {reqMeta && <Typography.Text code>{reqMeta.method} {stripHost(reqMeta.url)}</Typography.Text>}
                  <Typography.Text>请重新分析请求，发布只使用 FlowSpec 工作台中的步骤、字段、依赖和说明。</Typography.Text>
                  <Button size="small" loading={phase === "publishing"} onClick={finalize}>重新分析</Button>
                </Space>
              }
            />
          )}

          {renderFlowWorkbench()}

          {result && (
            <Alert
              style={{ marginTop: 12 }}
              type={result.ok ? "success" : "error"}
              showIcon
              message={
                <Space wrap>
                  <span>{result.ok ? `已发布：${result.action}` : `未发布：${result.reason || "需要调整"}`}</span>
                  {result.status && STATUS_META[result.status] && <Tag color={STATUS_META[result.status].color}>{STATUS_META[result.status].label}</Tag>}
                </Space>
              }
              description={
                <Space direction="vertical" size={4}>
                  {result.ok && result.api
                    ? <Typography.Text>接口 <Typography.Text code>{result.api.method} {result.api.path}</Typography.Text> · 参数 [{(result.api.params || []).join(", ")}]</Typography.Text>
                    : !result.ok ? <Typography.Text>请根据上方校验和待确认项调整后再发布。</Typography.Text> : null}
                  {result.recording_mode && (
                    <Typography.Text type="secondary">
                      录制模式：{result.recording_mode === "real_submit" ? "真实提交" : result.recording_mode === "intercepted_submit" ? "只录制不提交" : result.recording_mode}
                    </Typography.Text>
                  )}
                  {(result.clarifications || []).map((c, i) => <Typography.Text key={i} type="warning">{c}</Typography.Text>)}
                  {result.verification_basis && (
                    <Typography.Text type="secondary">验证依据：{result.verification_basis}</Typography.Text>
                  )}
                  {result.ok && (
                    <Button
                      type="primary"
                      size="small"
                      onClick={() => nav(`/skills?invoke=${encodeURIComponent(result.skill_id || `${subsystem}.${result.action || action}`)}`)}
                    >
                      直接调用
                    </Button>
                  )}
                </Space>
              }
            />
          )}
        </div>
      )}
    </Card>
    </ConfigProvider>
  );
}

function HeadersEditor({ value, onChange }: { value: Record<string, string>; onChange: (h: Record<string, string>) => void }) {
  const [newKey, setNewKey] = useState("");
  const [newVal, setNewVal] = useState("");
  const entries = Object.entries(value || {});
  return (
    <div style={{ background: "#f5f5f5", padding: 8, borderRadius: 6 }}>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>请求头</Typography.Text>
      <Space direction="vertical" size={4} style={{ width: "100%", marginTop: 6 }}>
        {entries.map(([k, v], i) => (
          <Space key={k + i} wrap>
            <Input size="small" value={k} style={{ width: 160 }} onChange={(e) => {
              const nk = e.target.value.trim(); if (!nk) return;
              const { [k]: _, ...rest } = value;
              onChange({ ...rest, [nk]: v });
            }} />
            <Input size="small" value={v} style={{ width: 260 }} onChange={(e) => onChange({ ...value, [k]: e.target.value })} />
            <Button size="small" danger onClick={() => { const { [k]: _, ...rest } = value; onChange(rest); }}>删除</Button>
          </Space>
        ))}
        <Space wrap>
          <Input size="small" placeholder="key" value={newKey} style={{ width: 160 }} onChange={(e) => setNewKey(e.target.value)} />
          <Input size="small" placeholder="value" value={newVal} style={{ width: 260 }} onChange={(e) => setNewVal(e.target.value)} />
          <Button size="small" onClick={() => {
            const k = newKey.trim();
            if (!k) return;
            // FH7 修复:不允许重复 key(连续点添加会塞空 value 重复头,后端 401)—— 大小写不敏感判断(Header 名是大小写不敏感的)
            const existingKey = Object.keys(value || {}).find((x) => x.toLowerCase() === k.toLowerCase());
            if (existingKey) {
              message.warning(`请求头 ${existingKey} 已存在,请直接编辑`);
              return;
            }
            onChange({ ...value, [k]: newVal });
            setNewKey(""); setNewVal("");
          }}>添加</Button>
        </Space>
      </Space>
    </div>
  );
}
