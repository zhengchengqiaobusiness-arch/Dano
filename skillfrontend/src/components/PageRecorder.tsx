import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
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
  Segmented,
  Space,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import {
  BranchesOutlined,
  DeleteOutlined,
  FileTextOutlined,
  LinkOutlined,
  PictureOutlined,
  PlusOutlined,
  RobotOutlined,
  UpOutlined,
  DownOutlined,
} from "@ant-design/icons";
import { useNavigate } from "react-router-dom";

interface RecStep { op: string; locator?: string; field?: string; value?: string; required?: boolean; options?: any[] }
interface RecReq { method: string; url: string; has_body?: boolean; json?: boolean }
interface AnalysisScreenshotPayload {
  name: string;
  mime_type: "image/jpeg";
  data: string;
  width: number;
  height: number;
  byte_size: number;
}

interface AnalysisScreenshot extends AnalysisScreenshotPayload {
  id: string;
  preview_url: string;
}

interface AnalysisApplication {
  status: "applied" | "no_change" | "rejected";
  summary?: string;
  screenshot_count: number;
  model_image_count?: number;
  screenshot_names?: string[];
  changes?: Record<string, number>;
  capability_count_before?: number;
  capability_count_after?: number;
  field_count_before?: number;
  field_count_after?: number;
  proposal_gate?: { accepted?: boolean; reasons?: string[] };
  operation_id?: string;
}

interface FlowParam {
  path: string; key: string; label?: string; value: string; type: string; required: boolean; name_source?: string;
  category?: string; source_kind?: string; source?: any; reason?: string;
  exposed_to_user?: boolean; need_human_confirm?: boolean; editable?: boolean; confidence?: number;
  // 系统化:enum_options 兼容 list[string] 与 list[{label, value}];label→value 表由后端 enum_value_map 提供
  enum_options?: Array<string | { label: string; value?: any }> | null;
  enum_value_map?: Record<string, any> | null;
}
interface FlowSelectBinding {
  param?: string; path?: string; source_url?: string; value_key?: string; label_key?: string;
  source_method?: string; source_headers?: Record<string, string>; source_body?: any;
  source_content_type?: string; source_role?: string; source_request_id?: string;
  options?: Array<string | { label: string; value?: any }> | null; count?: number; multi?: boolean;
  option_map?: Record<string, any> | null;
  enum_source?: string | null; enum_confirmed?: boolean | null;
  id_path?: string | null;
  field_projections?: Record<string, string>;
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
  confirmed?: boolean;
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
  blocking?: boolean; ignorable?: boolean;
  code?: string;
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
interface RequestFactEntry {
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
    requests?: RequestFactEntry[];
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
      indexed_range_changes?: any[]; [k: string]: any;
    };
    recording_agent_session?: { mode?: "plan" | "repair"; updated_at?: string; [k: string]: any };
    last_analysis_application?: AnalysisApplication;
    versions?: Array<{ version: number; action: string; reason?: string; created_at?: string; summary?: any }>;
    current_version?: number;
    current_fingerprint?: string;
  };
}
interface FlowCheckReport {
  passed?: boolean; errors?: string[]; warnings?: string[]; suggestions?: string[];
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
    ignorable?: boolean; issue_id?: string; code?: string; review_id?: string; suggested_action?: string;
  }>>;
}
interface FlowOperationReport {
  operation?: "plan" | "repair";
  changed?: boolean;
  changes?: Record<string, number>;
  summary?: string;
  edit_errors?: string[];
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
const POINTER_MOVE_INTERVAL_MS = 20;

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

function recorderWebSocketUrl(attempt = 0) {
  const configured = String(import.meta.env.VITE_DANO_RECORDING_WS_URL || "").trim();
  if (configured) return configured;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const sameOrigin = `${proto}://${location.host}/onboarding/page/record`;
  // Prefer the backend directly during local development. If that route is not
  // reachable in the current browser environment, alternate with Vite's proxy
  // on retries instead of leaving the recording permanently disconnected.
  if (import.meta.env.DEV && location.port === "5173" && ["localhost", "127.0.0.1", "::1"].includes(location.hostname)) {
    return attempt % 2 === 0 ? "ws://127.0.0.1:8077/onboarding/page/record" : sameOrigin;
  }
  return sameOrigin;
}

const PI_RECORDING_ID_PATTERN = /^recording_[0-9a-f]{32}$/;

function piRecordingStorageKey(tenant: string, subsystem: string, startUrl: string) {
  return ["dano", "recording-pi", tenant, subsystem, startUrl]
    .map((part) => encodeURIComponent(part))
    .join(":");
}

function readPiRecordingId(storageKey: string): string | null {
  try {
    const value = window.sessionStorage.getItem(storageKey);
    return value && PI_RECORDING_ID_PATTERN.test(value) ? value : null;
  } catch {
    return null;
  }
}

function writePiRecordingId(storageKey: string, value: string) {
  if (!PI_RECORDING_ID_PATTERN.test(value)) return;
  try {
    // The opaque resume ID is tab-scoped. Server paths, session files and
    // credentials are never accepted or persisted by the browser.
    window.sessionStorage.setItem(storageKey, value);
  } catch {
    // The component ref still supports reconnects when storage is unavailable.
  }
}

function clearPiRecordingId(storageKey: string) {
  try {
    window.sessionStorage.removeItem(storageKey);
  } catch {
    // The in-memory ref is still cleared when tab storage is unavailable.
  }
}

function piRecordingIdFromMessage(messageData: any): string | null {
  const value = messageData?.pi_session?.recording_id ?? messageData?.pi_recording_id;
  return typeof value === "string" && PI_RECORDING_ID_PATTERN.test(value) ? value : null;
}
const CATEGORY_OPTIONS = [
  { label: "用户参数", value: "user_param" },
  { label: "运行期变量", value: "runtime_var" },
  { label: "系统常量", value: "system_const" },
];
// 来源按"由谁/什么注入"归类：
//   用户侧: 用户输入
//   活接口侧: api_option(运行期拉接口取)
//   枚举侧: page_enum / form_option / static_enum / manual_enum
//   上游链侧: previous_response(本能力内 step 响应)
//   系统侧: current_user / system_time / request_header / page_context / constant
const SOURCE_KIND_OPTIONS = [
  { label: "来源不明", value: "unknown" },
  { label: "用户输入", value: "user_input" },
  { label: "接口候选", value: "api_option" },
  { label: "候选关联字段", value: "selected_option_field" },
  { label: "页面枚举", value: "page_enum" },
  { label: "表单选项", value: "form_option" },
  { label: "静态枚举", value: "static_enum" },
  { label: "人工枚举", value: "manual_enum" },
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
const SOURCE_REVIEW_TYPES = new Set([
  "field_source_unknown", "field_source_incomplete", "runtime_var_missing_source", "runtime_var_source",
]);
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
function fieldEditorAnchorId(stepId: string, path: string) {
  return `field-${domAnchorPart(stepId)}-${domAnchorPart(stripBodyPrefix(path))}`;
}
function popupContainer(_node?: HTMLElement) {
  return document.body;
}
function optionLabel(options: Array<{ label: string; value: string }>, value: string) {
  return options.find((o) => o.value === value)?.label || value;
}
function normalizeSourceKindForUi(sourceKind?: string | null) {
  return sourceKind || "";
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
  const current = normalizeSourceKindForUi(p.source_kind);
  if (!current || SOURCE_KIND_OPTIONS.some((option) => option.value === current)) return SOURCE_KIND_OPTIONS;
  return [
    { label: optionLabel(SOURCE_KIND_OPTIONS, current), value: current },
    ...SOURCE_KIND_OPTIONS,
  ];
}

function typeSelectOptionsForParam(p: FlowParam) {
  if (!p.type || PARAM_TYPE_OPTIONS.some((option) => option.value === p.type)) return PARAM_TYPE_OPTIONS;
  return [
    { label: PARAM_TYPE_LABELS[p.type] || p.type, value: p.type },
    ...PARAM_TYPE_OPTIONS,
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
  const safeOptions = uniqueOptions(options);
  return (
    <select
      value={value || ""}
      disabled={disabled}
      onChange={(e) => {
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
function requestFactPath(req: RequestFactEntry) {
  return (req.path || stripHost(req.url || "") || "").split("?", 1)[0];
}
function requestFactSignature(req: RequestFactEntry) {
  return `${(req.method || "GET").toUpperCase()} ${requestFactPath(req)}`;
}
function requestFactKey(req: RequestFactEntry) {
  if (req.request_id) return `id:${req.request_id}`;
  if (req.request_index != null) return `idx:${String(req.request_index)}`;
  return `sig:${requestFactSignature(req)}`;
}
function requestQueryValues(req: RequestFactEntry) {
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
function requestBusinessFilterCount(req: RequestFactEntry) {
  return Object.entries(requestQueryValues(req)).filter(([key, value]) =>
    !isPaginationQueryKey(key) && (Array.isArray(value) ? value : [value]).some((item) => String(item ?? "").trim())
  ).length;
}
function requestQueryFieldCount(req: RequestFactEntry) {
  return Object.keys(requestQueryValues(req)).length;
}
function richerRequestFact(candidate: RequestFactEntry, current: RequestFactEntry) {
  const candidateScore = [requestBusinessFilterCount(candidate), requestQueryFieldCount(candidate), candidate.response_json != null ? 1 : 0];
  const currentScore = [requestBusinessFilterCount(current), requestQueryFieldCount(current), current.response_json != null ? 1 : 0];
  for (let idx = 0; idx < candidateScore.length; idx += 1) {
    if (candidateScore[idx] !== currentScore[idx]) return candidateScore[idx] > currentScore[idx];
  }
  return Number(candidate.sequence ?? candidate.request_index ?? 0) > Number(current.sequence ?? current.request_index ?? 0);
}
function isApiLikeRequest(req: RequestFactEntry) {
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
  const factSource = (facts?.requests || []).map((req) => {
    const key = requestFactKey(req);
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
  const source = factSource;
  const stepSigs = new Set((spec?.steps || []).map((s) => `${(s.method || "").toUpperCase()} ${purePath(s.path || s.url || "")}`));
  const stepReqKeys = new Set((spec?.steps || []).flatMap((s) => {
    const meta = s.source_meta || {};
    const out: string[] = [];
    if (meta.request_id) out.push(`id:${meta.request_id}`);
    if (meta.request_index != null) out.push(`idx:${String(meta.request_index)}`);
    return out;
  }));
  const selectedRank = (req: RequestFactEntry) => (
    req.state === "materialized" ||
    stepSigs.has(`${(req.method || "").toUpperCase()} ${purePath(req.path || req.url || "")}`) ||
    stepReqKeys.has(requestFactKey(req))
  ) ? 0 : 1;
  const sorted = source
    .filter(isApiLikeRequest)
    .filter((req, idx, arr) => arr.findIndex((x) => requestFactKey(x) === requestFactKey(req)) === idx)
    .sort((a, b) => selectedRank(a) - selectedRank(b) || requestRoleRank(a) - requestRoleRank(b) || (b.confidence ?? 0) - (a.confidence ?? 0) || Number(a.request_index ?? 0) - Number(b.request_index ?? 0));
  const grouped = new Map<string, RequestFactEntry>();
  for (const req of sorted) {
    const signature = requestFactSignature(req);
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
function requestRoleRank(req: RequestFactEntry) {
  const role = req.role || "";
  if (["submit_anchor", "business_write"].includes(role)) return 0;
  if (role === "business_get") return 1;
  if (role === "read_context") return 2;
  if (role === "read_option") return 3;
  return 9;
}
function requestOptionValue(req: RequestFactEntry) {
  return requestFactKey(req);
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
  const nodeIds = capabilityNodeStepIds(cap?.nodes);
  if (nodeIds.length) return nodeIds;
  // Read-only compatibility for a pre-P6 projection during rolling upgrades.
  // New edits never write this derived field.
  return Array.from(new Set(
    (cap?.step_ids || []).map((value) => String(value || "").trim()).filter(Boolean),
  ));
}
function capabilityRequestRefForStep(cap: FlowCapabilityData | null | undefined, stepId: string) {
  return (cap?.request_refs || []).find((ref) => ref.step_id === stepId);
}
function capabilityUsageLabel(usage?: string) {
  return optionLabel(CAPABILITY_USAGE_OPTIONS, usage || "execute");
}
function capturedRequestSteps(spec: FlowSpecData | null | undefined, req: RequestFactEntry) {
  const signature = requestFactSignature(req);
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
function capturedRequestCapabilityNames(spec: FlowSpecData | null | undefined, req: RequestFactEntry) {
  const requestStepIds = new Set(capturedRequestSteps(spec, req).map((step) => step.step_id));
  const names = (spec?.capabilities || [])
    .filter((cap) => capabilityActualStepIds(cap).some((stepId) => requestStepIds.has(stepId)))
    .map((cap) => String(cap.title || cap.name || cap.capability_id || "").trim())
    .filter(Boolean);
  return Array.from(new Set(names));
}
function isCapturedRequestFieldCandidate(spec: FlowSpecData | null | undefined, req: RequestFactEntry) {
  if (req.role === "read_option") return true;
  const reqPath = requestFactPath(req);
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
function isRequestInSteps(spec: FlowSpecData | null | undefined, req: RequestFactEntry) {
  return capturedRequestSteps(spec, req).length > 0;
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
  const out: Array<{ label: string; value?: any }> = [];
  for (const x of raw || []) {
    if (x == null) continue;
    if (typeof x === "object") {
      const label = String(x.label ?? x.text ?? x.name ?? x.value ?? "").trim();
      if (label) out.push({ label, ...(Object.prototype.hasOwnProperty.call(x, "value") ? { value: x.value } : {}) });
    } else {
      const label = String(x).trim();
      if (label) out.push({ label });
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

const RECORDING_FLOW_PROTOCOL_VERSION = 2;
const MAX_ANALYSIS_SCREENSHOTS = 4;
const MAX_ANALYSIS_SCREENSHOT_BYTES = 1_400_000;

function base64ByteSize(data: string) {
  const padding = data.endsWith("==") ? 2 : data.endsWith("=") ? 1 : 0;
  return Math.floor(data.length * 3 / 4) - padding;
}

function loadScreenshotImage(file: File): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const objectUrl = URL.createObjectURL(file);
    const image = new window.Image();
    image.onload = () => {
      URL.revokeObjectURL(objectUrl);
      resolve(image);
    };
    image.onerror = () => {
      URL.revokeObjectURL(objectUrl);
      reject(new Error("Unable to read screenshot"));
    };
    image.src = objectUrl;
  });
}

async function prepareAnalysisScreenshot(file: File): Promise<AnalysisScreenshot> {
  if (!file.type.startsWith("image/")) throw new Error("Only image files are supported");
  const image = await loadScreenshotImage(file);
  const sourceWidth = image.naturalWidth || image.width;
  const sourceHeight = image.naturalHeight || image.height;
  if (!sourceWidth || !sourceHeight) throw new Error("Screenshot has no readable dimensions");

  let scale = Math.min(1, 1800 / Math.max(sourceWidth, sourceHeight));
  let latest: AnalysisScreenshot | null = null;
  for (let attempt = 0; attempt < 5; attempt += 1) {
    const width = Math.max(1, Math.round(sourceWidth * scale));
    const height = Math.max(1, Math.round(sourceHeight * scale));
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Canvas is unavailable");
    context.fillStyle = "#fff";
    context.fillRect(0, 0, width, height);
    context.drawImage(image, 0, 0, width, height);
    const quality = Math.max(0.62, 0.92 - attempt * 0.08);
    const previewUrl = canvas.toDataURL("image/jpeg", quality);
    const data = previewUrl.slice(previewUrl.indexOf(",") + 1);
    latest = {
      id: typeof crypto.randomUUID === "function" ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`,
      name: file.name || "screenshot.jpg",
      mime_type: "image/jpeg",
      data,
      width,
      height,
      byte_size: base64ByteSize(data),
      preview_url: previewUrl,
    };
    if (latest.byte_size <= MAX_ANALYSIS_SCREENSHOT_BYTES) return latest;
    scale *= 0.78;
  }
  throw new Error(`Screenshot remains too large (${latest?.byte_size || 0} bytes)`);
}

export default function PageRecorder({ tenant, subsystem, baseUrl, storageState }: {
  tenant: string; subsystem: string; baseUrl: string; storageState: string;
}) {
  const nav = useNavigate();
  const wsRef = useRef<WebSocket | null>(null);
  const frameCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const kbRef = useRef<HTMLInputElement | null>(null);
  const consoleBufRef = useRef<any[]>([]);
  const latestFrameRef = useRef<{ seq: number; src: string; meta: RecorderFrameMeta } | null>(null);
  const frameRafRef = useRef<number | null>(null);
  const frameDecodeBusyRef = useRef(false);
  const frameDecodeGenerationRef = useRef(0);
  const renderedFrameSeqRef = useRef(0);
  const pointerMoveTimerRef = useRef<number | null>(null);
  const pendingPointerMoveRef = useRef<Record<string, unknown> | null>(null);
  const pointerGestureRef = useRef<{
    pointerId: number; nx: number; ny: number; clientX: number; clientY: number;
    button: string; buttons: number; pointerType: string; dragging: boolean; clickCount: number;
  } | null>(null);
  const lastPointerClickRef = useRef<{
    at: number; clientX: number; clientY: number; button: string; clickCount: number;
  } | null>(null);
  const lastInputErrorNoticeRef = useRef(0);
  const intentionalCloseRef = useRef(false);
  const sessionStartedRef = useRef(false);
  const connectionErrorRef = useRef("");
  const heartbeatTimerRef = useRef<number | null>(null);
  const reconnectTimerRef = useRef<number | null>(null);
  const reconnectAttemptRef = useRef(0);
  const reconnectRestoreOperationRef = useRef<string | null>(null);
  const piRecordingScopeRef = useRef("");
  const piRecordingIdRef = useRef<string | null>(null);
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
  const [action, setAction] = useState(() => newRecordingActionName());
  const actionRef = useRef(action);
  useEffect(() => { actionRef.current = action; }, [action]);
  const [title, setTitle] = useState("");
  const [result, setResult] = useState<RecResult | null>(null);
  const [recordingMode, setRecordingMode] = useState<RecordingMode>("real_submit");
  const [err, setErr] = useState("");

  const [flowSpec, setFlowSpec] = useState<FlowSpecData | null>(null);
  const flowSpecRef = useRef<FlowSpecData | null>(null);
  const serverFingerprintRef = useRef("");
  useEffect(() => { flowSpecRef.current = flowSpec; }, [flowSpec]);
  const pendingEditorScrollRef = useRef<number | null>(null);
  function preserveEditorScrollForReorder() {
    pendingEditorScrollRef.current = window.scrollY;
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
  }
  useLayoutEffect(() => {
    const scrollTop = pendingEditorScrollRef.current;
    if (scrollTop == null) return;
    pendingEditorScrollRef.current = null;
    window.scrollTo({ top: scrollTop, left: window.scrollX, behavior: "auto" });
  }, [flowSpec]);
  const [checkReport, setCheckReport] = useState<FlowCheckReport | null>(null);
  const [titleDraft, setTitleDraft] = useState("");               // FC3 修复:标题本地草稿,WS 推送不再即时覆盖编辑
  const [descDraft, setDescDraft] = useState("");                 // FC3 修复:说明本地草稿
  useEffect(() => { setTitleDraft(flowSpec?.title || ""); }, [flowSpec?.title]);
  useEffect(() => { setDescDraft(flowSpec?.business_description || ""); }, [flowSpec?.business_description]);

  // Capability-local UI state must follow the capability identity, not its
  // current array position. Index keys made expanded panels/dropdowns jump to
  // a different capability immediately after an up/down reorder.
  const [capabilityAddValue, setCapabilityAddValue] = useState<Record<string, string>>({});
  const [capabilityAddUsage, setCapabilityAddUsage] = useState<Record<string, CapabilityUsage | "">>({});
  const pendingCapabilityMembershipRef = useRef<Array<{
    capability: string; requestId?: string; requestIndex?: number | string | null; usage: CapabilityUsage;
  }>>([]);
  const [newParam, setNewParam] = useState({
    step_id: "", path: "", key: "", type: "string", category: "user_param", source_kind: "unknown",
  });
  const [newLink, setNewLink] = useState({ source_step_id: "", source_path: "", target_step_id: "", target_path: "" });
  const [bindDraft, setBindDraft] = useState<Record<string, { source_step_id?: string; source_path?: string }>>({});

  const [namingBusy, setNamingBusy] = useState(false);
  const [descBusy, setDescBusy] = useState(false);
  const [orchestrateBusy, setOrchestrateBusy] = useState(false);
  const [autoFixBusy, setAutoFixBusy] = useState(false);
  const [lastOperationReport, setLastOperationReport] = useState<FlowOperationReport | null>(null);
  const [analysisScreenshots, setAnalysisScreenshots] = useState<AnalysisScreenshot[]>([]);
  const analysisScreenshotsRef = useRef<AnalysisScreenshot[]>([]);
  useEffect(() => { analysisScreenshotsRef.current = analysisScreenshots; }, [analysisScreenshots]);
  const screenshotInputRef = useRef<HTMLInputElement>(null);
  const [analysisScreenshotBusy, setAnalysisScreenshotBusy] = useState(false);
  const [lastAnalysisEvidence, setLastAnalysisEvidence] = useState<AnalysisApplication | null>(null);
  const [expandedCapabilityKeys, setExpandedCapabilityKeys] = useState<string[]>([]);
  const [expandedCapabilitySections, setExpandedCapabilitySections] = useState<Record<number, string[]>>({});
  const [expandedCapabilitySteps, setExpandedCapabilitySteps] = useState<Record<string, string[]>>({});
  const [expandedRequestPanels, setExpandedRequestPanels] = useState<string[]>([]);
  const [expandedUnassignedSteps, setExpandedUnassignedSteps] = useState<string[]>([]);
  const [expandedCapabilityRelationKeys, setExpandedCapabilityRelationKeys] = useState<string[]>([]);
  const flowOperationRef = useRef<{
    mode: "plan" | "repair"; previousUpdatedAt?: string; operationId: string;
    analysisScreenshots: AnalysisScreenshotPayload[];
  } | null>(null);
  const finalizeOperationRef = useRef<string | null>(null);
  const publishOperationRef = useRef<string | null>(null);
  const flowOperationTimerRef = useRef<number | null>(null);
  const flowMutationQueueRef = useRef<any[]>([]);
  const flowMutationInFlightRef = useRef<any | null>(null);
  const flowMutationSeqRef = useRef(0);
  const afterFlowSyncRef = useRef<(() => void) | null>(null);
  const publishLocateTokenRef = useRef(0);
  const [activeFlowTab, setActiveFlowTab] = useState("abilities");

  function acceptFlowSpec(fs: FlowSpecData) {
    serverFingerprintRef.current = String(fs.meta?.current_fingerprint || "");
    const pending = pendingCapabilityMembershipRef.current;
    const remaining: typeof pending = [];
    for (const item of pending) {
      const capIdx = (fs.capabilities || []).findIndex(
        (cap, idx) => capabilityRef(cap, idx) === item.capability,
      );
      const step = (fs.steps || []).find((candidate) => {
        const meta = candidate.source_meta || {};
        return (item.requestId && String(meta.request_id || "") === item.requestId) ||
          (item.requestIndex != null && String(meta.request_index ?? "") === String(item.requestIndex));
      });
      const serverRef = capIdx >= 0 && step
        ? capabilityRequestRefForStep(fs.capabilities?.[capIdx], step.step_id)
        : undefined;
      if (
        capIdx < 0 || !step
        || (item.usage === "execute" && !capabilityActualStepIds(fs.capabilities?.[capIdx]).includes(step.step_id))
        || (item.usage !== "execute" && !serverRef)
      ) {
        remaining.push(item);
      }
    }
    pendingCapabilityMembershipRef.current = remaining;
    flowSpecRef.current = fs;
    setFlowSpec(fs);
    if (fs.meta?.last_analysis_application) {
      setLastAnalysisEvidence(fs.meta.last_analysis_application);
    }
    const nextTitle = preferredSkillTitle(fs);
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
    const reportStillRunning = () => {
      if (!flowOperationRef.current) return;
      message.warning(`${label}仍在服务端执行，完成后页面会自动更新`);
      // Long Pi tasks have no client-side deadline. Keep reporting progress
      // without clearing the active operation while the connection is alive.
      flowOperationTimerRef.current = window.setTimeout(reportStillRunning, 120000);
    };
    flowOperationTimerRef.current = window.setTimeout(reportStillRunning, 120000);
  }

  function resumeFlowOperationAfterReconnect(restoredSpec: FlowSpecData | null) {
    const active = flowOperationRef.current;
    const currentSpec = restoredSpec || flowSpecRef.current;
    if (!active || !currentSpec) return;
    finishFlowOperation(currentSpec.meta?.recording_agent_session);
    const pending = flowOperationRef.current;
    if (!pending) return;
    if (pending.mode === "repair") {
      send({ type: "auto_fix_flow", operation_id: pending.operationId });
      return;
    }
    send({
      type: "orchestrate_flow",
      operation_id: pending.operationId,
      analysis_screenshots: pending.analysisScreenshots,
    });
  }

  useEffect(() => () => {
    // FC4 修复:仅当 phase 处于 recording/publishing 时才关 WS(避免 StrictMode 双 mount 或组件复用时误关正在用的 WS)
    // wsRef.current 在首次 mount 时为 null(start 才会建),所以首次 cleanup 一定是 noop,无副作用
    if (phaseRef.current === "recording" || phaseRef.current === "publishing") {
      intentionalCloseRef.current = true;
      wsRef.current?.close();
    }
    if (pointerMoveTimerRef.current != null) window.clearTimeout(pointerMoveTimerRef.current);
    if (heartbeatTimerRef.current != null) window.clearInterval(heartbeatTimerRef.current);
    if (reconnectTimerRef.current != null) window.clearTimeout(reconnectTimerRef.current);
    frameDecodeGenerationRef.current += 1;
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
    const queued = flowMutationQueueRef.current.shift();
    const next = { ...queued, expected_fingerprint: serverFingerprintRef.current };
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
    if (obj?.type === "flow_update") return enqueueFlowMutation(obj);
    return sendRaw(obj);
  }

  function clearFrame() {
    frameDecodeGenerationRef.current += 1;
    frameDecodeBusyRef.current = false;
    latestFrameRef.current = null;
    renderedFrameSeqRef.current = 0;
    if (frameRafRef.current != null) {
      window.cancelAnimationFrame(frameRafRef.current);
      frameRafRef.current = null;
    }
    const canvas = frameCanvasRef.current;
    const context = canvas?.getContext("2d");
    if (canvas && context) context.clearRect(0, 0, canvas.width, canvas.height);
    setHasFrame(false);
    setFrameMeta({});
  }

  function queueFrame(seq: number, data: string, meta: RecorderFrameMeta = {}) {
    if (!data) return;
    const normalizedSeq = Number(seq || 0) > 0 ? Number(seq) : renderedFrameSeqRef.current + 1;
    latestFrameRef.current = { seq: normalizedSeq, src: `data:image/jpeg;base64,${data}`, meta };

    const scheduleDecode = () => {
      if (frameDecodeBusyRef.current || frameRafRef.current != null) return;
      frameRafRef.current = window.requestAnimationFrame(() => {
        frameRafRef.current = null;
        const frame = latestFrameRef.current;
        if (!frame || frame.seq <= renderedFrameSeqRef.current) return;

        frameDecodeBusyRef.current = true;
        const generation = frameDecodeGenerationRef.current;
        const decoder = new Image();
        decoder.decoding = "async";
        decoder.src = frame.src;
        const decoded = typeof decoder.decode === "function"
          ? decoder.decode()
          : new Promise<void>((resolve, reject) => {
              decoder.onload = () => resolve();
              decoder.onerror = () => reject(new Error("recording frame decode failed"));
            });

        decoded.then(() => {
          if (generation !== frameDecodeGenerationRef.current || frame.seq <= renderedFrameSeqRef.current) return;
          // Only expose a fully decoded, monotonically newer JPEG. Canvas keeps
          // the previous pixels until this synchronous draw, so the recording
          // surface never blanks while another JPEG is still decoding.
          const canvas = frameCanvasRef.current;
          const context = canvas?.getContext("2d", { alpha: false });
          if (!canvas || !context) return;
          const frameWidth = Math.max(1, Math.round(frame.meta.frameWidth || decoder.naturalWidth || 1));
          const frameHeight = Math.max(1, Math.round(frame.meta.frameHeight || decoder.naturalHeight || 1));
          if (canvas.width !== frameWidth) canvas.width = frameWidth;
          if (canvas.height !== frameHeight) canvas.height = frameHeight;
          context.drawImage(decoder, 0, 0, frameWidth, frameHeight);
          renderedFrameSeqRef.current = frame.seq;
          setFrameMeta((current) => {
            const updates = {
              ...Object.fromEntries(Object.entries(frame.meta).filter(([, value]) => value != null)),
              frameWidth,
              frameHeight,
            };
            if (Object.entries(updates).every(([key, value]) => current[key as keyof RecorderFrameMeta] === value)) {
              return current;
            }
            return { ...current, ...updates };
          });
          if (!hasFrameRef.current) setHasFrame(true);
        }).catch(() => {
          // A corrupt/superseded JPEG is simply skipped; the next latest frame
          // will be decoded without blanking the currently visible image.
          if (generation === frameDecodeGenerationRef.current) {
            renderedFrameSeqRef.current = Math.max(renderedFrameSeqRef.current, frame.seq);
          }
        }).finally(() => {
          if (generation !== frameDecodeGenerationRef.current) return;
          frameDecodeBusyRef.current = false;
          const latest = latestFrameRef.current;
          if (latest && latest.seq > renderedFrameSeqRef.current) scheduleDecode();
        });
      });
    };

    scheduleDecode();
  }

  function resetEditorState() {
    flowSpecRef.current = null;
    serverFingerprintRef.current = "";
    setFlowSpec(null);
    setCheckReport(null);
    setBindDraft({});
    setCapabilityAddValue({});
    setCapabilityAddUsage({});
    pendingCapabilityMembershipRef.current = [];
    analysisScreenshotsRef.current = [];
    setAnalysisScreenshots([]);
    setLastAnalysisEvidence(null);
    setActiveFlowTab("abilities");
    flowMutationInFlightRef.current = null;
    flowMutationQueueRef.current = [];
    afterFlowSyncRef.current = null;
    clearFlowOperation();
  }

  function start() {
    if (!tenant) { message.error("请先到「创建 / 进入租户」"); return; }
    if (!startUrl.trim()) { message.error("请填页面地址 start_url"); return; }
    if (reconnectTimerRef.current != null) window.clearTimeout(reconnectTimerRef.current);
    reconnectTimerRef.current = null;
    reconnectAttemptRef.current = 0;
    setErr(""); setResult(null); setSteps([]); setReqs([]); clearFrame();
    resetEditorState();
    const nextAction = newRecordingActionName();
    actionRef.current = nextAction;
    setAction(nextAction);
    // “开始录制” always creates a new logical recording.  Only the automatic
    // reconnect path may reuse the opaque server resume id; otherwise a fresh
    // run can accidentally reopen the previous run's browser/draft snapshot.
    const targetUrl = startUrl.trim();
    const piRecordingScope = piRecordingStorageKey(tenant, subsystem, targetUrl);
    clearPiRecordingId(piRecordingScope);
    piRecordingScopeRef.current = piRecordingScope;
    piRecordingIdRef.current = null;
    setReconnectedSessionNeedsCapture(false);
    setConnectionState("connecting");
    setPhase("recording");
    openRecorderConnection(false);
  }

  function resetFrameStreamForReconnect() {
    // Keep the currently painted image as a stable fallback, but invalidate all
    // in-flight decoders and the old session's sequence numbers. A replacement
    // RecordSession starts again at frame 1.
    frameDecodeGenerationRef.current += 1;
    frameDecodeBusyRef.current = false;
    latestFrameRef.current = null;
    renderedFrameSeqRef.current = 0;
    if (frameRafRef.current != null) window.cancelAnimationFrame(frameRafRef.current);
    frameRafRef.current = null;
  }

  function scheduleRecorderReconnect() {
    if (intentionalCloseRef.current || reconnectTimerRef.current != null || !tenant || !startUrl.trim()) return;
    const attempt = ++reconnectAttemptRef.current;
    const delay = Math.min(1000 * (2 ** Math.min(attempt - 1, 4)), 15000);
    setConnectionState("reconnecting");
    reconnectTimerRef.current = window.setTimeout(() => {
      reconnectTimerRef.current = null;
      if (intentionalCloseRef.current || wsRef.current) return;
      openRecorderConnection(true);
    }, delay);
  }

  function openRecorderConnection(isReconnect = false) {
    if (isReconnect) resetFrameStreamForReconnect();
    const intercept = recordingMode === "record_only";
    const targetUrl = startUrl.trim();
    const piRecordingScope = piRecordingStorageKey(tenant, subsystem, targetUrl);
    if (piRecordingScopeRef.current !== piRecordingScope) {
      piRecordingScopeRef.current = piRecordingScope;
      piRecordingIdRef.current = readPiRecordingId(piRecordingScope);
    }
    const piRecordingId = piRecordingIdRef.current;
    intentionalCloseRef.current = false;
    sessionStartedRef.current = false;
    connectionErrorRef.current = "";
    if (heartbeatTimerRef.current != null) window.clearInterval(heartbeatTimerRef.current);
    wsAliveRef.current = true;                                     // FC2 修复:每次 start 重置存活标志
    const ws = new WebSocket(recorderWebSocketUrl(reconnectAttemptRef.current));
    wsRef.current = ws;
    ws.onopen = () => {
      if (wsRef.current !== ws) return;
      send({
        type: "start", tenant, subsystem, start_url: targetUrl,
        base_url: baseUrl.trim() || undefined,
        storage_state: storageState.trim() || undefined,
        intercept,
        pi_recording_id: piRecordingId || undefined,
        resume_action: actionRef.current,
      });
      // Keep both proxy directions active. Long periods without page changes can
      // otherwise be treated as an idle WebSocket by an intermediate proxy or
      // by the backend's uvicorn default idle timeout (5min). 5s is well below
      // the lowest common idle timeout we have observed (Vite's http-proxy
      // ~60s, corporate proxies often ~30s, uvicorn default ~300s).
      heartbeatTimerRef.current = window.setInterval(() => {
        if (wsRef.current !== ws || ws.readyState !== WebSocket.OPEN) return;
        ws.send(JSON.stringify({ type: "ping", at: Date.now() }));
      }, 5000);
    };
    ws.onmessage = (ev) => {
      if (wsRef.current !== ws) return;
      let m: any; try { m = JSON.parse(ev.data); } catch { return; }
      if (m.flow_spec && m.protocol_version !== RECORDING_FLOW_PROTOCOL_VERSION) {
        const detail = `不支持的录制协议版本：${m.protocol_version ?? "missing"}`;
        connectionErrorRef.current = detail;
        setErr(detail); message.error(detail); return;
      }
      const issuedPiRecordingId = piRecordingIdFromMessage(m);
      if (issuedPiRecordingId) {
        piRecordingScopeRef.current = piRecordingScope;
        piRecordingIdRef.current = issuedPiRecordingId;
        writePiRecordingId(piRecordingScope, issuedPiRecordingId);
      }
      if (m.type === "started") {
        sessionStartedRef.current = true;
        reconnectAttemptRef.current = 0;
        setErr("");
        const serverAction = m.action ?? m.action_name;
        if (typeof serverAction === "string" && /^[a-zA-Z][a-zA-Z0-9_]*$/.test(serverAction)) {
          actionRef.current = serverAction;
          setAction(serverAction);
        }
        setPhase("recording");
        setConnectionState("connected");
        // The server draft is authoritative across a transient WebSocket
        // reconnect.  In particular, an ability plan may have completed just
        // before a 1006 close; restoring the older local empty draft here used
        // to erase that successful first result.
        const resumedServerSpec = m.resumed_server_draft && m.flow_spec ? m.flow_spec : null;
        if (resumedServerSpec) {
          acceptFlowSpec(resumedServerSpec);
          if (m.check_report) setCheckReport(m.check_report);
          reconnectRestoreOperationRef.current = null;
          setReconnectedSessionNeedsCapture(false);
          if (isReconnect) message.success("录制连接和最新能力已自动恢复");
          if (isReconnect) resumeFlowOperationAfterReconnect(resumedServerSpec);
        } else if (isReconnect && flowSpecRef.current) {
          flowMutationInFlightRef.current = null;
          flowMutationQueueRef.current = [];
          afterFlowSyncRef.current = null;
          reconnectRestoreOperationRef.current = null;
          setReconnectedSessionNeedsCapture(true);
          message.warning("服务端未找到可恢复草稿，请重新触发目标操作并分析");        } else {
          reconnectRestoreOperationRef.current = null;
          setReconnectedSessionNeedsCapture(isReconnect);
          if (isReconnect) message.success("录制连接已自动恢复");
        }
      }
      else if (m.type === "pong" || m.type === "stopped") return;
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
        // 当前会话捕获到实时请求后，才解除重连后的分析门禁。
        setReconnectedSessionNeedsCapture(false);
      }
      else if (m.type === "flow_spec") {
        const restoredAfterReconnect = !!reconnectRestoreOperationRef.current
          && m.operation_id === reconnectRestoreOperationRef.current;
        if (restoredAfterReconnect) {
          reconnectRestoreOperationRef.current = null;
          setReconnectedSessionNeedsCapture(false);
          message.success("录制连接及编辑内容已自动恢复");
        }
        if (m.operation === "finalize" && (!m.operation_id || m.operation_id === finalizeOperationRef.current)) {
          finalizeOperationRef.current = null;
          // finalize 完成:无论之前 phase 是什么,都必须清回 recording,
          // 否则 "停止并分析请求" 按钮会一直转圈 (P5 引入的守卫漏判 finalize)。
          setPhase("recording");
        }
        // 发布请求可能与最后一次字段更新响应交错到达。普通更新不能把发布中的
        // loading/状态提前重置,否则用户看到按钮闪退但后端仍在发布。
        const finalizeJustCleared = m.operation === "finalize" && !finalizeOperationRef.current;
        if (finalizeJustCleared || phaseRef.current !== "publishing") setPhase("recording");
        const fs = m.flow_spec;
        const acknowledgesActiveMutation = m.operation === "flow_update"
          && (!m.operation_id || m.operation_id === flowMutationInFlightRef.current?.operation_id);
        const hasNewerLocalMutation = acknowledgesActiveMutation && flowMutationQueueRef.current.length > 0;
        if (fs) {
          // Even when a newer optimistic edit is already queued, its patch must
          // use the fingerprint acknowledged by this server response.
          serverFingerprintRef.current = String(fs.meta?.current_fingerprint || "");
          // Every field mutation is serialized, but the user may already have made
          // a newer local edit while the previous response is in flight.  Do not
          // repaint that older snapshot over the newer draft.  The final queued
          // response contains the complete server state and is accepted normally.
          if (!hasNewerLocalMutation) acceptFlowSpec(fs);
          finishFlowOperation(fs.meta?.recording_agent_session, m.operation, m.operation_id);
        }
        if (restoredAfterReconnect && fs) resumeFlowOperationAfterReconnect(fs);
        if (m.check_report && !hasNewerLocalMutation) setCheckReport(m.check_report);
        else if (hasNewerLocalMutation) setCheckReport(null);
        if (m.operation === "plan" && (m.analysis_application || m.analysis_evidence)) {
          const application = m.analysis_application || {
            ...m.analysis_evidence,
            status: m.operation_report?.changed ? "applied" : "no_change",
            summary: m.operation_report?.summary,
            changes: m.operation_report?.changes,
          };
          setLastAnalysisEvidence(application);
        }
        // A successful server mutation produces a new validation snapshot.
        // Do not keep rendering clarifications from an older failed publish;
        // fixed or explicitly ignored warnings must disappear with that old
        // result as soon as the authoritative update is acknowledged.
        if (!hasNewerLocalMutation && ["flow_update", "plan", "repair", "step_naming", "business_description"].includes(String(m.operation || ""))) {
          setResult(null);
        }
        if (m.operation === "step_naming") {
          setNamingBusy(false);
          message.success("步骤名称已刷新");
        } else if (m.operation === "business_description") {
          setDescBusy(false);
          message.success("业务说明已生成");
        }
        if (m.operation_report) {
          const report = m.operation_report as FlowOperationReport;
          setLastOperationReport(report);
          if (report.changed) message.success(report.summary || "流程编排已更新");
          else if (report.edit_errors?.length) message.error(report.summary || "自动修复存在无效建议");
          else message.info(report.summary || "检查完成，没有可自动修改的内容");
        }
        if (m.operation === "flow_update") finishQueuedFlowMutation(m.operation_id);
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
        if (m.flow_spec) acceptFlowSpec(m.flow_spec);
        setResult(m.report); setPhase("recording");
        if (m.check_report || m.report?.check_report) {
          setCheckReport(m.check_report || m.report.check_report);
        }
      }
      else if (m.type === "error") {
        const detail = m.detail || "录制出错";
        connectionErrorRef.current = detail;
        setNamingBusy(false); setDescBusy(false); clearFlowOperation();
        publishOperationRef.current = null;
        finalizeOperationRef.current = null;
        if (m.flow_spec) acceptFlowSpec(m.flow_spec);
        if (m.check_report) setCheckReport(m.check_report);
        if (m.operation === "flow_update") failQueuedFlowMutation(m.operation_id);
        if (reconnectRestoreOperationRef.current && m.operation_id === reconnectRestoreOperationRef.current) {
          reconnectRestoreOperationRef.current = null;
          setReconnectedSessionNeedsCapture(true);
        }
        if (m.operation === "flow_update" && !m.flow_spec) {
          sendRaw({ type: "refresh_flow_spec" });
        }
        if (detail.includes("step not found") || detail.includes("link not found")) {
          message.warning("流程已变更，正在同步最新版本");
          sendRaw({ type: "refresh_flow_spec" });
        } else {
          message.error(detail);
          setErr(detail);
        }
      }
    };
    ws.onerror = () => {
      if (wsRef.current === ws && !connectionErrorRef.current) {
        connectionErrorRef.current = "WebSocket 连接失败，当前画面和已录步骤已保留";
        setErr(connectionErrorRef.current);
      }
    };
    ws.onclose = (event) => {
      if (wsRef.current !== ws) return;
      if (!intentionalCloseRef.current && event.code !== 1000 && !connectionErrorRef.current) {
        const reason = event.reason ? `：${event.reason}` : "";
        connectionErrorRef.current = `录制连接异常关闭（代码 ${event.code || 1006}）${reason}`;
      }
      const hadStarted = sessionStartedRef.current;
      sessionStartedRef.current = false;
      wsRef.current = null;
      if (heartbeatTimerRef.current != null) {
        window.clearInterval(heartbeatTimerRef.current);
        heartbeatTimerRef.current = null;
      }
      wsAliveRef.current = false;                                 // FC2 修复:WS 关闭,send 会自动避免刷屏
      pointerGestureRef.current = null;
      pendingPointerMoveRef.current = null;
      if (pointerMoveTimerRef.current != null) window.clearTimeout(pointerMoveTimerRef.current);
      pointerMoveTimerRef.current = null;
      lastPointerClickRef.current = null;
      finalizeOperationRef.current = null;
      publishOperationRef.current = null;
      setNamingBusy(false);
      setDescBusy(false);
      flowMutationInFlightRef.current = null;
      flowMutationQueueRef.current = [];
      afterFlowSyncRef.current = null;
      if (intentionalCloseRef.current) {
        setConnectionState("idle");
        clearFlowOperation();
        return;
      }
      reconnectRestoreOperationRef.current = null;
      if (phaseRef.current === "publishing") setPhase("recording");
      setErr((current) => current || connectionErrorRef.current || (hadStarted
        ? "录制连接已断开，正在自动恢复，现场和编辑内容已保留"
        : "录制服务暂时不可用，正在自动连接"));
      scheduleRecorderReconnect();
    };
  }

  function pointerButton(button: number) {
    if (button === 1) return "middle";
    if (button === 2) return "right";
    return "left";
  }
  function normalizedPoint(clientX: number, clientY: number) {
    const img = frameCanvasRef.current;
    if (!img) return null;
    const rect = img.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    return {
      nx: Math.max(0, Math.min(1, (clientX - rect.left) / rect.width)),
      ny: Math.max(0, Math.min(1, (clientY - rect.top) / rect.height)),
    };
  }
  function sendPendingPointerMove() {
    pointerMoveTimerRef.current = null;
    const event = pendingPointerMoveRef.current;
    pendingPointerMoveRef.current = null;
    if (event) send({ type: "input", event });
  }
  function queuePointerMove(event: Record<string, unknown>) {
    pendingPointerMoveRef.current = event;
    if (pointerMoveTimerRef.current != null) return;
    // Coalesce display-rate pointer events while keeping remote hover and drag
    // responsive. The backend and frame sender both drop superseded work.
    pointerMoveTimerRef.current = window.setTimeout(sendPendingPointerMove, POINTER_MOVE_INTERVAL_MS);
  }
  function onImgPointerDown(e: React.PointerEvent<HTMLCanvasElement>) {
    if (connectionState !== "connected" || e.button < 0) return;
    const point = normalizedPoint(e.clientX, e.clientY);
    if (!point) return;
    e.preventDefault();
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* pointer capture may be unavailable */ }
    const button = pointerButton(e.button);
    const previous = lastPointerClickRef.current;
    const now = performance.now();
    const clickCount = button === "left" && previous?.button === button && previous.clickCount === 1
      && now - previous.at <= 350
      && Math.hypot(e.clientX - previous.clientX, e.clientY - previous.clientY) <= 8
      ? 2
      : 1;
    pointerGestureRef.current = {
      pointerId: e.pointerId,
      ...point,
      clientX: e.clientX,
      clientY: e.clientY,
      button,
      buttons: e.buttons,
      pointerType: e.pointerType || "mouse",
      dragging: false,
      clickCount,
    };
    // Forward the press immediately. Waiting to decide between click and
    // double-click made every button feel delayed and prevented press-driven
    // lazy loaders from starting until 250 ms later.
    send({
      type: "input",
      event: {
        kind: "pointer_down", ...point, button, buttons: e.buttons,
        pointer_type: e.pointerType || "mouse", click_count: clickCount,
      },
    });
    kbRef.current?.focus({ preventScroll: true });
  }
  function onImgPointerMove(e: React.PointerEvent<HTMLCanvasElement>) {
    if (connectionState !== "connected") return;
    const point = normalizedPoint(e.clientX, e.clientY);
    if (!point) return;
    const gesture = pointerGestureRef.current;
    if (gesture?.pointerId === e.pointerId && !gesture.dragging) {
      const distance = Math.hypot(e.clientX - gesture.clientX, e.clientY - gesture.clientY);
      if (distance >= 5) {
        gesture.dragging = true;
        lastPointerClickRef.current = null;
      }
    }
    queuePointerMove({
      kind: "pointer_move", ...point, buttons: e.buttons, pointer_type: e.pointerType || "mouse",
    });
    if (gesture?.dragging) e.preventDefault();
  }
  function onImgPointerUp(e: React.PointerEvent<HTMLCanvasElement>) {
    const gesture = pointerGestureRef.current;
    if (!gesture || gesture.pointerId !== e.pointerId) return;
    pointerGestureRef.current = null;
    const point = normalizedPoint(e.clientX, e.clientY) || { nx: gesture.nx, ny: gesture.ny };
    e.preventDefault();
    try { e.currentTarget.releasePointerCapture(e.pointerId); } catch { /* already released */ }
    if (pointerMoveTimerRef.current != null) {
      window.clearTimeout(pointerMoveTimerRef.current);
      sendPendingPointerMove();
    }
    send({
      type: "input",
      event: {
        kind: "pointer_up", ...point, button: gesture.button, buttons: e.buttons,
        pointer_type: gesture.pointerType, click_count: gesture.clickCount,
      },
    });
    lastPointerClickRef.current = gesture.dragging ? null : {
      at: performance.now(), clientX: e.clientX, clientY: e.clientY,
      button: gesture.button, clickCount: gesture.clickCount,
    };
  }
  function onImgPointerCancel(e: React.PointerEvent<HTMLCanvasElement>) {
    const gesture = pointerGestureRef.current;
    if (!gesture || gesture.pointerId !== e.pointerId) return;
    pointerGestureRef.current = null;
    pendingPointerMoveRef.current = null;
    if (pointerMoveTimerRef.current != null) window.clearTimeout(pointerMoveTimerRef.current);
    pointerMoveTimerRef.current = null;
    lastPointerClickRef.current = null;
    const point = normalizedPoint(e.clientX, e.clientY) || { nx: gesture.nx, ny: gesture.ny };
    send({
      type: "input",
      event: {
        kind: "pointer_up", ...point, button: gesture.button, buttons: 0,
        pointer_type: gesture.pointerType, click_count: gesture.clickCount,
      },
    });
  }
  function onImgWheel(e: React.WheelEvent<HTMLCanvasElement>) {
    if (connectionState !== "connected") return;
    const point = normalizedPoint(e.clientX, e.clientY);
    e.preventDefault();
    send({ type: "input", event: { kind: "scroll", dy: e.deltaY, dx: e.deltaX, ...(point || {}) } });
  }
  function relayKb(el: HTMLInputElement) {
    const v = el.value;
    if (v) {
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
      send({ type: "input", event: { kind: "key", key } });
      e.preventDefault();
    }
  }
  function onKbPaste(e: React.ClipboardEvent<HTMLInputElement>) {
    const text = e.clipboardData.getData("text");
    if (text) {
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
    if (!send({ type: "finalize", operation_id: operationId, action: action.trim(), title: title.trim(), steps })) {
      finalizeOperationRef.current = null;
      setPhase("recording");
    }
  }
  function badAction(a: string) {
    if (!/^[a-zA-Z][a-zA-Z0-9_]*$/.test(a)) { message.error("动作名请用英文标识"); return true; }
    return false;
  }
  function publishRequest() {
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
    if (!action.trim() || badAction(action.trim())) return;
    runAfterFlowSync(performPublishRequest);
  }
  function performPublishRequest() {
    if (publishOperationRef.current) return;
    const currentSpec = flowSpecRef.current || flowSpec;
    if (!currentSpec) { message.error("请先生成 FlowSpec 后再发布"); return; }
    const publishTitle = title.trim() || preferredSkillTitle(currentSpec);
    const operationId = newCostlyOperationId("publish");
    publishOperationRef.current = operationId;
    // Keep the previous result until this operation receives its own reply.
    // Clearing it here made the base validator's green state look like an
    // instantaneous publish success while the backend was still reviewing.
    setPhase("publishing");
    if (!send({ type: "publish_request", operation_id: operationId, action: action.trim(), title: publishTitle,
      expected_fingerprint: currentSpec.meta?.current_fingerprint })) {
      publishOperationRef.current = null;
      setPhase("recording");
      setResult({ ok: false, reason: "录制连接已断开，发布请求未发送" });
    }
  }
  function stopAll() {
    intentionalCloseRef.current = true;
    if (reconnectTimerRef.current != null) window.clearTimeout(reconnectTimerRef.current);
    reconnectTimerRef.current = null;
    reconnectAttemptRef.current = 0;
    reconnectRestoreOperationRef.current = null;
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
    setPhase("idle"); setResult(null); setSteps([]); clearFrame();
    resetEditorState();
  }

  function updateFlowField(k: string, v: any) { send({ type: "flow_update", edits: [{ op: "update_flow", field: k, value: v }] }); }
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

    // 删除立即反映到页面；服务端响应会用权威脱敏投影确认或回滚。
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
    setCheckReport(null);
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
    setCheckReport(null);
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
    setCheckReport(null);
  }
  function updateParam(stepId: string, p: FlowParam, field: string, value: any) {
    patchLocalParam(stepId, p, { [field]: value });
    send({ type: "flow_update", edits: [paramEdit(stepId, p, field, value)] });
  }
  function updateParamType(step: FlowStepData, p: FlowParam, value: string) {
    const currentStep = flowSpecRef.current?.steps.find((item) => item.step_id === step.step_id) || step;
    const currentParam = currentStep.params.find((item) => paramMatches(item, p)) || p;
    patchLocalParam(step.step_id, currentParam, { type: value });
    send({ type: "flow_update", edits: [paramEdit(step.step_id, currentParam, "type", value)] });
  }
  function updateParamCategory(stepId: string, p: FlowParam, category: string) {
    const currentStep = flowSpecRef.current?.steps.find((item) => item.step_id === stepId);
    const current = currentStep?.params.find((item) => paramMatches(item, p)) || p;
    const updates = {
      category,
      editable: true,
    };
    patchLocalParams(stepId, current, updates);
    send({ type: "flow_update", edits: Object.entries(updates).map(([field, value]) => paramEdit(stepId, current, field, value)) });
  }
  function updateParamSourceKind(stepId: string, p: FlowParam, sourceKind: string) {
    const currentStep = flowSpecRef.current?.steps.find((item) => item.step_id === stepId);
    const current = currentStep?.params.find((item) => paramMatches(item, p)) || p;
    const currentSource = current.source as any;
    const nextSource = sourceDescriptor(sourceKind, current, currentSource);
    const needsConfiguration = sourceNeedsConfiguration(sourceKind, nextSource);
    const updates = {
      source_kind: sourceKind,
      source: nextSource,
      need_human_confirm: needsConfiguration,
      editable: true,
    };
    patchLocalParams(stepId, current, updates);
    send({ type: "flow_update", edits: Object.entries(updates).map(([field, value]) => paramEdit(stepId, current, field, value)) });
    if (sourceKind === "previous_response") {
      const key = paramDraftKey(stepId, p);
      setBindDraft((d) => ({
        ...d,
        [key]: d[key] || { source_step_id: (current.source as any)?.step_id || "", source_path: (current.source as any)?.response_path || "" },
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

  async function handleAnalysisScreenshotSelection(files: FileList | null) {
    const selected = Array.from(files || []);
    if (!selected.length) return;
    const remaining = Math.max(0, MAX_ANALYSIS_SCREENSHOTS - analysisScreenshotsRef.current.length);
    if (!remaining) {
      message.warning("\u6700\u591a\u4e0a\u4f20 4 \u5f20\u53c2\u8003\u622a\u56fe");
      if (screenshotInputRef.current) screenshotInputRef.current.value = "";
      return;
    }
    if (selected.length > remaining) message.warning(`\u672c\u6b21\u53ea\u6dfb\u52a0\u524d ${remaining} \u5f20\u622a\u56fe`);
    setAnalysisScreenshotBusy(true);
    const prepared: AnalysisScreenshot[] = [];
    try {
      for (const file of selected.slice(0, remaining)) {
        try {
          prepared.push(await prepareAnalysisScreenshot(file));
        } catch (error) {
          message.error(`${file.name}: ${error instanceof Error ? error.message : String(error)}`);
        }
      }
      if (prepared.length) {
        setAnalysisScreenshots((current) => {
          const next = [...current, ...prepared].slice(0, MAX_ANALYSIS_SCREENSHOTS);
          analysisScreenshotsRef.current = next;
          return next;
        });
        setLastAnalysisEvidence(null);
        message.success(`\u5df2\u6dfb\u52a0 ${prepared.length} \u5f20\u53c2\u8003\u622a\u56fe\uff0c\u4e0b\u6b21\u751f\u6210/\u4f18\u5316\u5c06\u91cd\u65b0\u53c2\u8003`);
      }
    } finally {
      setAnalysisScreenshotBusy(false);
      if (screenshotInputRef.current) screenshotInputRef.current.value = "";
    }
  }

  function removeAnalysisScreenshot(id: string) {
    setAnalysisScreenshots((current) => {
      const next = current.filter((item) => item.id !== id);
      analysisScreenshotsRef.current = next;
      return next;
    });
    setLastAnalysisEvidence(null);
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
    const screenshots = analysisScreenshotsRef.current.map((item) => ({
      name: item.name,
      mime_type: item.mime_type,
      data: item.data,
      width: item.width,
      height: item.height,
      byte_size: item.byte_size,
    }));
    flowOperationRef.current = {
      mode: "plan",
      previousUpdatedAt: currentSpec.meta?.recording_agent_session?.updated_at,
      operationId: newCostlyOperationId("plan"),
      analysisScreenshots: screenshots,
    };
    setOrchestrateBusy(true);
    setAutoFixBusy(true);
    armFlowOperationWatchdog("能力生成");
    if (!send({ type: "orchestrate_flow", operation_id: flowOperationRef.current.operationId, analysis_screenshots: screenshots })) clearFlowOperation();
  }
  function autoFixFlow() {
    if (!flowSpecRef.current || flowOperationRef.current) return;
    if (flowMutationInFlightRef.current || flowMutationQueueRef.current.length) {
      runAfterFlowSync(autoFixFlow);
      return;
    }
    flowOperationRef.current = {
      mode: "repair",
      previousUpdatedAt: flowSpecRef.current.meta?.recording_agent_session?.updated_at,
      operationId: newCostlyOperationId("repair"),
      analysisScreenshots: [],
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
      nodes: [],
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
    patchLocalCapability(idx, { confirmed, requires_human_confirm: false }, false);
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
        setCheckReport(null);
      },
    });
  }
  function addStepToCapability(idx: number, value?: string, usage?: CapabilityUsage | "") {
    if (!value || !usage) return;
    const membership = { usage, origin: "manual", confirmed: true };
    if (value.startsWith("step:")) {
      const stepId = value.slice(5);
      send({ type: "flow_update", edits: [
        { op: "add_capability_step", capability_index: idx, step_id: stepId, ...membership },
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
    preserveEditorScrollForReorder();
    send({ type: "flow_update", edits: [
      { op: "reorder_capability_steps", capability_index: idx, step_ids: next },
      { op: "update_capability", capability_index: idx, field: "confirmed", value: false },
    ] });
  }
  function capabilityRef(cap: FlowCapabilityData, idx: number) {
    return cap.name || cap.capability_id || `idx:${idx}`;
  }
  function capabilityPanelKey(cap: FlowCapabilityData, idx: number) {
    // Names are editable and must never participate in the normal key. Legacy
    // duplicate server IDs get an indexed quarantine key so React does not
    // reuse the wrong panel; valid IDs remain stable across every field edit.
    if (cap.capability_id) {
      const duplicateCount = (flowSpecRef.current?.capabilities || [])
        .filter((item) => item.capability_id === cap.capability_id).length;
      return duplicateCount > 1
        ? ["capability", cap.capability_id, "duplicate", idx].join(":")
        : ["capability", cap.capability_id].join(":");
    }
    return ["capability-name", cap.name || idx].join(":");
  }
  function moveCapability(idx: number, delta: number) {
    const current = flowSpecRef.current;
    if (!current) return;
    const caps = [...(current.capabilities || [])];
    const to = idx + delta;
    if (to < 0 || to >= caps.length) return;
    const ordered = [...caps];
    const [item] = ordered.splice(idx, 1);
    ordered.splice(to, 0, item);
    const refs = ordered.map(capabilityRef);
    preserveEditorScrollForReorder();
    const next = { ...current, capabilities: ordered };
    flowSpecRef.current = next;
    setFlowSpec(next);
    send({ type: "flow_update", edits: [{ op: "reorder_capabilities", capability_refs: refs }] });
  }
  const stepById = useMemo(() => Object.fromEntries((flowSpec?.steps || []).map((s) => [s.step_id, s])), [flowSpec]);
  function stepBrief(stepId?: string) {
    const st = stepId ? stepById[stepId] : undefined;
    if (!st) return stepId || "";
    return `${st.name || fallbackStepName(st.method, st.path)} · ${st.method} ${st.path || stripHost(st.url)}`;
  }
  function groupedPublishIssues(report: FlowCheckReport | null) {
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
      ignorable?: boolean; issue_id?: string; code?: string; review_id?: string; suggested_action?: string;
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
        ignorable: item.ignorable,
        issue_id: item.issue_id,
        code: item.code,
        review_id: item.review_id,
        suggested_action: item.suggested_action,
      })).filter(isOperatorIssue);
    }
    const representedReviewIds = new Set(Object.values(by).flat().map((item) => item.review_id).filter(Boolean));
    for (const review of report?.review_items || []) {
      if (review.resolved || !SOURCE_REVIEW_TYPES.has(review.type) || representedReviewIds.has(review.id)) continue;
      by.field = by.field || [];
      by.field.push({
        message: review.reason ? `${review.title}：${review.reason}` : review.title,
        severity: "warning",
        source: "review",
        target: review.target,
        audience: "operator",
        actionable: true,
        blocking: review.blocking,
        auto_fixable: false,
        ignorable: review.ignorable !== false,
        issue_id: `review:${review.id}`,
        code: review.type,
        review_id: review.id,
        suggested_action: review.suggested_action,
      });
    }
    if (!Object.keys(by).length) {
      // ReviewItems are generated workbench advice, not publish failures.
      // Reusing them as a fallback here made an accepted operator contract look
      // blocked even when deterministic publish validation had passed.
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
    const path = target.target_path || target.path || target.source_path || target.field;
    return [cap ? `能力 ${cap}` : "", sid ? `接口 ${stepBrief(sid)}` : "", path ? `字段 ${path}` : ""]
      .filter(Boolean).join(" · ");
  }
  function locatePublishIssue(target?: Record<string, any>) {
    if (!target || !Object.keys(target).length) {
      message.warning("该旧版错误项没有可定位的结构化目标，请重新校验后再定位");
      return;
    }
    const capabilities = flowSpec?.capabilities || [];
    const capabilityFields = (cap: FlowCapabilityData) => [
      ...(cap.inputs || []), ...(cap.request_fields || []), ...(cap.internal_fields || []),
      ...(cap.computed_fields || []), ...(cap.outputs || []),
    ];
    let sid = target.target_step_id || target.step_id || target.source_step_id;
    const capRef = target.capability_name || target.capability_id || target.capability
      || capabilities.find((cap) => capabilityActualStepIds(cap).includes(sid || ""))?.name;
    const capIdx = capabilities.findIndex((cap) =>
      [cap.name, cap.capability_id, cap.title, cap.kind].filter(Boolean).includes(capRef)
      || capabilityActualStepIds(cap).includes(sid || ""));
    const cap = capIdx >= 0 ? capabilities[capIdx] : undefined;
    if (!sid && target.field_id && cap) {
      sid = capabilityFields(cap).find((field) => field.field_id === target.field_id)?.step_id;
    }
    const targetPath = target.target_path || target.path || target.source_path
      || (target.field_id && cap
        ? capabilityFields(cap).find((field) => field.field_id === target.field_id)?.path
        : "");
    const isRequest = target.kind === "request_role";
    const unassignedStepId = sid && capIdx < 0 && stepById[String(sid)] ? String(sid) : "";
    setActiveFlowTab(isRequest || !!unassignedStepId ? "requests" : "abilities");
    let anchor = "";
    if (isRequest) {
      setExpandedRequestPanels(["captured"]);
      anchor = `request-${domAnchorPart(target.request_index ?? target.index ?? target.request_id ?? target.path ?? sid)}`;
    } else if (unassignedStepId) {
      setExpandedRequestPanels((keys) => Array.from(new Set([...keys, "unassigned-steps"])));
      setExpandedUnassignedSteps((keys) => Array.from(new Set([...keys, unassignedStepId])));
      anchor = targetPath
        ? fieldEditorAnchorId(unassignedStepId, targetPath)
        : `step-${domAnchorPart(unassignedStepId)}`;
    } else if (capIdx >= 0) {
      const panelKey = capabilityPanelKey(cap!, capIdx);
      setExpandedCapabilityKeys((keys) => Array.from(new Set([...keys, panelKey])));
      const section = ["link", "capability_dependency"].includes(target.kind) ? "deps"
        : ["capability_output", "capability_node", "capability_precondition"].includes(target.kind) ? "io"
          : "interfaces";
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
      else if (sid && targetPath) anchor = `field-${domAnchorPart(sid)}-${domAnchorPart(stripBodyPrefix(targetPath))}`;
      else if (sid) anchor = `step-${domAnchorPart(sid)}`;
      else anchor = `capability-${domAnchorPart(cap!.name || cap!.capability_id || capIdx)}`;
    }
    if (!anchor && capRef) anchor = `capability-${domAnchorPart(capRef)}`;
    if (!anchor && target.kind === "capability_relation" && target.relation_id) {
      setExpandedCapabilityRelationKeys(["capability-relations"]);
      anchor = `capability-relation-${domAnchorPart(target.relation_id)}`;
    }
    if (!anchor && target.kind === "flow") anchor = "flow-workbench";
    if (!anchor) {
      message.warning("该错误项缺少能力、接口或字段锚点，请重新校验生成结构化目标");
      return;
    }
    const locateToken = ++publishLocateTokenRef.current;
    const focusAnchor = (attempt = 0) => {
      if (locateToken !== publishLocateTokenRef.current) return;
      const element = document.getElementById(anchor);
      if (!element || element.getClientRects().length === 0) {
        if (attempt < 30) {
          window.setTimeout(() => focusAnchor(attempt + 1), 100);
          return;
        }
        message.warning(`没有找到该错误项对应的编辑位置（${publishIssueTargetLabel(target) || target.kind || "旧版目标"}），请重新校验`);
        return;
      }
      element.scrollIntoView({ behavior: "smooth", block: "center" });
      element.animate(
        [
          { backgroundColor: "#fff1b8", outline: "3px solid #faad14", outlineOffset: "3px" },
          { backgroundColor: "#fffbe6", outline: "2px solid #ffc53d", outlineOffset: "2px" },
          { backgroundColor: "transparent", outline: "0 solid transparent", outlineOffset: "0" },
        ],
        { duration: 2200, easing: "ease-out" },
      );
    };
    window.setTimeout(() => focusAnchor(), 180);
  }
  function publishIssueReviewId(item: { review_id?: string; issue_id?: string }) {
    if (item.review_id) return item.review_id;
    return item.issue_id?.startsWith("review:") ? item.issue_id.slice("review:".length) : "";
  }
  function ignorePublishReviewIssue(item: {
    review_id?: string; issue_id?: string; message?: string; target?: Record<string, any>;
  }) {
    const reviewId = publishIssueReviewId(item);
    if (!reviewId) {
      message.warning("该告警缺少 review_id，请重新校验后再忽略");
      return;
    }
    const queued = send({
      type: "flow_update",
      edits: [{ op: "resolve_review", review_id: reviewId, resolved: true }],
    });
    if (!queued) {
      message.warning("录制连接不可用，暂时无法保存忽略状态");
      return;
    }

    const base = flowSpecRef.current;
    if (base) {
      const next = {
        ...base,
        review_items: (base.review_items || []).map((review) => review.id === reviewId
          ? { ...review, resolved: true }
          : review),
      };
      flowSpecRef.current = next;
      setFlowSpec(next);
    }
    setCheckReport((current) => {
      if (!current) return current;
      const matchesReview = (issue: { review_id?: string; issue_id?: string }) =>
        publishIssueReviewId(issue) === reviewId;
      return {
        ...current,
        review_items: (current.review_items || []).map((review) => review.id === reviewId
          ? { ...review, resolved: true }
          : review),
        issue_groups: current.issue_groups
          ? Object.fromEntries(Object.entries(current.issue_groups).map(([key, issues]) => [
            key,
            issues.filter((issue) => !matchesReview(issue)),
          ]))
          : current.issue_groups,
      };
    });
    message.success(`已忽略告警${publishIssueTargetLabel(item.target) ? `：${publishIssueTargetLabel(item.target)}` : ""}`);
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
  function enumOptionEdits(step: FlowStepData, p: FlowParam, options: Array<string | { label: string; value?: any }>, optionMap?: Record<string, any> | null) {
    const records = options.map(enumOptionRecord).filter((item): item is { label: string; value?: any } => !!item);
    const mappingComplete = records.length > 0 && records.every((item) => item.value !== undefined);
    return [
      paramEdit(step.step_id, p, "enum_options", options),
      paramEdit(step.step_id, p, "enum_value_map", optionMap || null),
      paramEdit(step.step_id, p, "need_human_confirm", !mappingComplete),
    ];
  }
  function enumSourceForKind(sourceKind?: string | null) {
    if (sourceKind === "page_enum" || sourceKind === "form_option") return "dom";
    if (sourceKind === "static_enum" || sourceKind === "manual_enum") return "manual";
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
      // Selecting an endpoint only records the candidate source.  It is not proof
      // that label/value keys or the complete option set have been captured.
      nextBinding.enum_confirmed = false;
      nextBinding.value_key = "";
      nextBinding.label_key = "";
      nextBinding.field_projections = {};
    }
    if (!hasExplicitIdPath && !nextBinding.id_path && (nextBinding.source_url || p.source_kind === "api_option")) {
      nextBinding.id_path = currentPath;
    }
    if (nextBinding.options) nextBinding.count = nextBinding.options.length;
    const replaced = (step.selects || []).some((s) => s.path === p.path || (!s.path && s.param === p.key) || s === existing);
    const nextSelects = replaced
      ? (step.selects || []).map((s) => (s.path === p.path || (!s.path && s.param === p.key) || s === existing ? nextBinding : s))
      : [...(step.selects || []), nextBinding];
    const edits: any[] = [{ op: "upsert_select", step_id: step.step_id, binding: nextBinding }];
    const paramUpdates: Record<string, any> = {};
    for (const edit of extraEdits) {
      if (edit?.op === "update" && edit.step_id === step.step_id && (edit.param_path || edit.param_key || edit.param_label)) {
        paramUpdates[edit.field] = edit.value;
      }
    }
    patchLocalStep(step.step_id, { selects: nextSelects });
    if (Object.keys(paramUpdates).length) patchLocalParam(step.step_id, p, paramUpdates);
    send({ type: "flow_update", edits: [...edits, ...extraEdits] });
  }
  function enumOptionRecord(x: any): { label: string; value?: any } | null {
    if (x == null) return null;
    if (typeof x === "object") {
      const label = String(x.label ?? x.text ?? x.name ?? x.value ?? "").trim();
      if (!label) return null;
      return { label, ...(Object.prototype.hasOwnProperty.call(x, "value") ? { value: x.value } : {}) };
    }
    const label = String(x).trim();
    return label ? { label } : null;
  }
  function enumOptionRecordsForParam(step: FlowStepData, p: FlowParam) {
    const sel = selectBindingForParam(step, p);
    const raw = p.enum_options?.length ? p.enum_options : sel?.options || [];
    const map = p.enum_value_map || sel?.option_map || {};
    const seen = new Set<string>();
    const out: Array<{ label: string; value?: any }> = [];
    for (const item of raw || []) {
      const rec = enumOptionRecord(item);
      if (!rec || seen.has(rec.label)) continue;
      seen.add(rec.label);
      const value = Object.prototype.hasOwnProperty.call(map, rec.label) ? map[rec.label] : rec.value;
      out.push({ label: rec.label, ...(value !== undefined ? { value } : {}) });
    }
    return out;
  }
  function enumOptionsForParam(step: FlowStepData, p: FlowParam) {
    if (!OPTION_SOURCE_KINDS.includes(p.source_kind || "") && p.type !== "enum" && p.type !== "list-enum") return [];
    return enumOptionRecordsForParam(step, p).map((x) => x.label);
  }
  function enumOptionsTextForParam(step: FlowStepData, p: FlowParam) {
    return enumOptionRecordsForParam(step, p)
      .map((x) => x.value === undefined ? x.label : `${x.label}=${String(x.value)}`)
      .join("\n");
  }
  function enumMappingCompleteForParam(step: FlowStepData, p: FlowParam) {
    const records = enumOptionRecordsForParam(step, p);
    return records.length > 0 && records.every((item) => item.value !== undefined);
  }
  function parseEnumOptionsText(text: string): { options: Array<{ label: string; value?: any }>; optionMap: Record<string, any> | null; mappingComplete: boolean } {
    const chunks = text.includes("\n") ? text.split(/\n/) : text.split(/[,，]/);
    const seen = new Set<string>();
    const options: Array<{ label: string; value?: any }> = [];
    const optionMap: Record<string, any> = {};
    let hasMapped = false;
    for (const raw of chunks) {
      const line = raw.trim();
      if (!line) continue;
      const m = line.match(/^(.+?)(?:\s*(?:=>|=|:|：|\t)\s*)(.+)$/);
      const label = (m ? m[1] : line).trim();
      const valueRaw = m ? m[2].trim() : "";
      if (!label || seen.has(label)) continue;
      seen.add(label);
      if (!m) {
        // A visible label is evidence for display only.  Never invent an API value
        // by assuming value === label; that made incomplete snapshots executable.
        options.push({ label });
        continue;
      }
      const value = /^-?\d+(?:\.\d+)?$/.test(valueRaw) ? Number(valueRaw) : valueRaw;
      options.push({ label, value });
      optionMap[label] = value;
      hasMapped = true;
    }
    return { options, optionMap: hasMapped ? optionMap : null, mappingComplete: options.length > 0 && options.every((item) => item.value !== undefined) };
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
    // Before a capability contract exists there is nothing actionable to
    // locate or confirm in the capability workbench.  Initial recording
    // diagnostics remain in checkReport, but must not surface as field-source
    // warnings before the operator has generated abilities.
    const publishIssueGroups = capabilities.length > 0 ? groupedPublishIssues(checkReport) : [];
    const hasPublishAdvice = publishIssueGroups.some((group) => group.items.length > 0);
    const publishFailed = result?.ok === false;
    const publishPending = phase === "publishing" && !!publishOperationRef.current;
    const validationRefreshing = !checkReport;
    return (
      <Card style={{ marginTop: 16 }} styles={{ body: { paddingTop: 8 } }}>
        {capabilities.length > 0 && (
          <Alert
            type={publishPending || validationRefreshing ? "info" : (!publishFailed && checkReport?.passed && !hasPublishAdvice ? "success" : "warning")}
            showIcon
            style={{ marginBottom: 12 }}
            message={publishPending
              ? "正在审核并发布当前流程"
              : validationRefreshing
              ? "\u6b63\u5728\u66f4\u65b0\u53d1\u5e03\u6821\u9a8c"
              : publishFailed
              ? "发布未完成"
              : checkReport?.passed
                ? (hasPublishAdvice ? "基础校验通过，仍有建议项" : "发布校验通过")
                : "发布校验需要处理"}
            description={
              !checkReport ? (
                <Typography.Text style={{ fontSize: 12 }}>{"\u6821\u9a8c\u533a\u57df\u56fa\u5b9a\u4fdd\u7559\uff0c\u6700\u65b0\u7ed3\u679c\u8fd4\u56de\u540e\u5c06\u5728\u8fd9\u91cc\u66f4\u65b0\u3002"}</Typography.Text>
              ) : <Space direction="vertical" size={2}>
                <Typography.Text style={{ fontSize: 12 }}>
                  Skill 参数：{checkReport.api_preview?.params?.length ? checkReport.api_preview.params.join(", ") : "无"}
                  {checkReport.dry_run ? ` · Dry-run ${checkReport.dry_run.ok ? "OK" : "需要处理"}` : ""}
                  {checkReport.dry_run?.request_count != null ? ` · ${checkReport.dry_run.request_count} 步` : ""}
                </Typography.Text>
                {lastOperationReport && (
                  <Space wrap size={4}>
                    <Typography.Text style={{ fontSize: 12 }}>{lastOperationReport.summary || "编排操作完成"}</Typography.Text>
                    {!!lastOperationReport.edit_errors?.length && <Tag color="orange">跳过无效建议 {lastOperationReport.edit_errors.length}</Tag>}
                    <Tag color={(lastOperationReport.errors_after || 0) > 0 ? "error" : "success"}>
                      错误 {lastOperationReport.errors_before || 0} → {lastOperationReport.errors_after || 0}
                    </Tag>
                    <Tag>警告 {lastOperationReport.warnings_before || 0} → {lastOperationReport.warnings_after || 0}</Tag>
                  </Space>
                )}
                {result && !publishPending && (
                  <Space direction="vertical" size={2}>
                    <Space wrap size={4}>
                      <Typography.Text type={result.ok ? "success" : "danger"}>
                        {result.ok ? `已发布：${result.action}` : `未发布：${result.reason || "需要调整"}`}
                      </Typography.Text>
                      {result.status && STATUS_META[result.status] && <Tag color={STATUS_META[result.status].color}>{STATUS_META[result.status].label}</Tag>}
                    </Space>
                    {result.ok && result.api && (
                      <Typography.Text style={{ fontSize: 12 }}>
                        接口 <Typography.Text code>{result.api.method} {result.api.path}</Typography.Text> · 参数 [{(result.api.params || []).join(", ")}]
                      </Typography.Text>
                    )}
                    {result.recording_mode && (
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        录制模式：{result.recording_mode === "real_submit" ? "真实提交" : result.recording_mode === "intercepted_submit" ? "只录制不提交" : result.recording_mode}
                      </Typography.Text>
                    )}
                    {result.verification_basis && <Typography.Text type="secondary" style={{ fontSize: 12 }}>验证依据：{result.verification_basis}</Typography.Text>}
                    {(result.clarifications || []).map((item, index) => <Typography.Text key={index} type="warning" style={{ fontSize: 12 }}>{item}</Typography.Text>)}
                    {result.ok && (
                      <Button type="primary" size="small" onClick={() => nav(`/skills?invoke=${encodeURIComponent(result.skill_id || `${subsystem}.${result.action || action}`)}`)}>
                        直接调用
                      </Button>
                    )}
                  </Space>
                )}
                <Space direction="vertical" size={4}>
                  {publishIssueGroups.map((group) => (
                    <div key={group.key} style={{ display: "grid", gridTemplateColumns: "100px 1fr", gap: 8, alignItems: "start" }}>
                      <Tag color={group.color} style={{ margin: 0, textAlign: "center" }}>{group.label} {group.items.length}</Tag>
                      <Space direction="vertical" size={2}>
                        {group.items.map((item, issueIdx) => (
                          <Space key={item.issue_id || `${group.key}-${issueIdx}`} wrap size={4}>
                            {publishIssueTargetLabel(item.target) && <Tag>{publishIssueTargetLabel(item.target)}</Tag>}
                            {item.blocking === false && <Tag color="gold">不阻塞</Tag>}
                            <Typography.Text type={item.severity === "warning" ? "secondary" : "danger"} style={{ fontSize: 12 }}>
                              {item.message}
                            </Typography.Text>
                            {item.target && Object.keys(item.target).length > 0 && (
                              <Button type="link" size="small" onClick={() => locatePublishIssue(item.target)}>定位</Button>
                            )}
                            {item.ignorable === true && publishIssueReviewId(item) && (
                              <Button type="link" size="small" onClick={() => ignorePublishReviewIssue(item)}>忽略此告警</Button>
                            )}
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
    const assignedStepIds = new Set((flowSpec?.capabilities || []).flatMap((cap) => capabilityActualStepIds(cap)));
    const unassignedSteps = (flowSpec?.steps || []).filter((step) => !assignedStepIds.has(step.step_id));
    return (
      <Collapse
        activeKey={expandedRequestPanels}
        onChange={(keys) => setExpandedRequestPanels((Array.isArray(keys) ? keys : [keys]).map(String))}
        bordered={false}
      >
        <Collapse.Panel header={`捕获接口 ${capturedTotal}`} key="captured">
          {renderCapturedRequestsPanel()}
        </Collapse.Panel>
        {unassignedSteps.length > 0 && (
          <Collapse.Panel header={`未归属能力的接口与字段 ${unassignedSteps.length}`} key="unassigned-steps">
            <Collapse
              size="small"
              activeKey={expandedUnassignedSteps}
              onChange={(keys) => setExpandedUnassignedSteps((Array.isArray(keys) ? keys : [keys]).map(String))}
            >
              {unassignedSteps.map((step, index) => (
                <Collapse.Panel
                  key={step.step_id}
                  header={(
                    <Space wrap id={`step-${domAnchorPart(step.step_id)}`}>
                      <Tag color="purple">接口 {index + 1}</Tag>
                      <Tag color={(step.method || "GET").toUpperCase() === "GET" ? "blue" : "green"}>{step.method}</Tag>
                      <Typography.Text strong>{step.name || fallbackStepName(step.method, step.path)}</Typography.Text>
                      <PathText value={step.path || stripHost(step.url)} maxWidth={420} />
                      <Tag>{step.params?.length || 0} 字段</Tag>
                    </Space>
                  )}
                >
                  {renderStepFieldsInCapability(step)}
                </Collapse.Panel>
              ))}
            </Collapse>
          </Collapse.Panel>
        )}
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
        // allCapturedRequests is grouped by this signature, so it is the one
        // identity guaranteed unique even if a recorder reuses request_id.
        rowKey={(req) => requestFactSignature(req)}
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
      .filter((req) => !existingReqKeys.has(requestFactKey(req)) && !allStepReqKeys.has(requestFactKey(req)))
      .map((req) => ({
        label: `#${req.sequence ?? req.request_index ?? ""} ${req.method || "GET"} ${req.path || stripHost(req.url || "")}`,
        value: `req:${requestOptionValue(req)}`,
      }));
    return [...stepItems, ...reqItems];
  }
  function renderParamEditorInCapability(
    step: FlowStepData,
    p: FlowParam,
    paramIndex: number,
  ) {
    const bindKey = paramDraftKey(step.step_id, p);
    const linked = incomingLink(step.step_id, p.path);
    const currentBind = bindDraft[bindKey] || {
      source_step_id: p.source?.step_id || linked?.source_step_id,
      source_path: p.source?.response_path || linked?.source_path,
    };
    const linkedSourceComplete = p.source_kind === "previous_response"
      && !!linked?.source_step_id
      && !!linked?.source_path;
    const sourceConfigurationIncomplete = sourceNeedsConfiguration(p.source_kind || "unknown", p.source as any)
      && !linkedSourceComplete;
    const needsManualConfirm = !!p.need_human_confirm && p.category === "runtime_var";
    const runtimeSourceComplete = !sourceConfigurationIncomplete;
    const selectBinding = selectBindingForParam(step, p);
    const enumOptions = enumOptionsForParam(step, p);
    const enumMappingComplete = enumMappingCompleteForParam(step, p);
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
        id={fieldEditorAnchorId(step.step_id, p.path)}
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
                {isEnumOption && enumOptions.length > 0 && !enumMappingComplete && <Tag color="orange">仅有名称，值未映射</Tag>}
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
              {enumOptions.length > 0 && enumMappingComplete ? (
                <EnumValueInput value={String(p.value ?? "")} width="100%"
                  options={enumSelectOptions}
                  onSave={(v) => updateParam(step.step_id, p, "value", v)} />
              ) : (
                <EditableText value={String(p.value ?? "")} width="100%" onSave={(v) => updateParam(step.step_id, p, "value", v)} />
              )}
            </FieldControl>
            <FieldControl label="类型">
              <NativeSelect value={p.type} width="100%" options={typeSelectOptionsForParam(p)}
                onChange={(v) => updateParamType(step, p, v)} />
            </FieldControl>
            <FieldControl label="分类">
              <NativeSelect value={p.category || "user_param"} width="100%" options={CATEGORY_OPTIONS}
                onChange={(v) => updateParamCategory(step.step_id, p, v)} />
            </FieldControl>
            <FieldControl label="来源">
              <NativeSelect value={normalizeSourceKindForUi(p.source_kind) || "unknown"} width="100%" options={sourceSelectOptionsForParam(p)}
                onChange={(v) => updateParamSourceKind(step.step_id, p, v)} />
            </FieldControl>
            {paramExposedToCaller(p) && (
              <FieldControl label="必填性">
                <NativeSelect
                  value={p.required ? "required" : "optional"}
                  width="100%"
                  options={[
                    { label: "必填", value: "required" },
                    { label: "非必填", value: "optional" },
                  ]}
                  onChange={(v) => updateParam(step.step_id, p, "required", v === "required")}
                />
              </FieldControl>
            )}
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
                        {enumOptions.length > 0 && !enumMappingComplete && <Tag color="orange">未映射实际提交值</Tag>}
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
                            placeholder="每行写 名称=实际值；只有名称会保留为未映射，不会假定名称就是提交值"
                            onSave={(v) => {
                              const { options, optionMap, mappingComplete } = parseEnumOptionsText(v);
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
                                  enum_confirmed: mappingComplete,
                                },
                                enumOptionEdits(step, p, options, optionMap),
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
                    ) : p.source_kind === "selected_option_field" ? (
                      <Typography.Text type="secondary">运行期从所选候选记录的 {(p.source as any)?.response_path || "关联字段"} 自动写入。</Typography.Text>
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
          <NativeSelect value={isActive ? newParam.source_kind : "unknown"} width={140} options={SOURCE_KIND_OPTIONS}
            onChange={(v) => setNewParam((s) => ({ ...s, step_id: step.step_id, source_kind: v }))} />
          <Button type="primary" onClick={() => {
            const draft = isActive ? newParam : { ...newParam, step_id: step.step_id };
            const path = draft.path.trim();
            const key = draft.key.trim();
            if (!path || !key) { message.warning("请填写字段路径和参数名"); return; }
            const isEnum = draft.type === "enum" || draft.type === "list-enum";
            const sourceKind = draft.source_kind || "unknown";
            const param: FlowParam = {
              path, key, label: key, value: "", type: draft.type, required: false,
              category: draft.category, source_kind: sourceKind,
              enum_options: isEnum ? [] : undefined,
              exposed_to_user: draft.category === "user_param", editable: true,
              reason: "人工新增字段",
            };
            const source = sourceDescriptor(sourceKind, param);
            send({ type: "flow_update", edits: [{
              op: "add", step_id: step.step_id, param: {
                ...param,
                source,
                need_human_confirm: sourceNeedsConfiguration(sourceKind, source),
              },
            }] });
            setNewParam({
              step_id: step.step_id, path: "", key: "", type: "string", category: "user_param", source_kind: "unknown",
            });
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
  function renderStepFieldsInCapability(step: FlowStepData) {
    return (
      <Space direction="vertical" size={10} style={{ width: "100%" }}>
        {renderAddFieldForStep(step)}
        {(step.params || []).length ? (
          <List
            size="small"
            // path/key are editable. Stable positional keys prevent row remounts between blur and click.
            rowKey={(param) => `${step.step_id}:param:${(step.params || []).indexOf(param)}`}
            dataSource={step.params || []}
            renderItem={(p, index) => renderParamEditorInCapability(step, p, index)}
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
            <Tag>{st.params?.length || 0} 字段</Tag>
          </Space>
        }
        extra={
          <Space onClick={(e) => e.stopPropagation()}>
            <Tooltip title="上移"><Button size="small" icon={<UpOutlined />} disabled={stepIdx === 0}
              onMouseDown={(e) => e.preventDefault()} onClick={() => moveStepInCapability(capIdx, stepIds, stepIdx, -1)} /></Tooltip>
            <Tooltip title="下移"><Button size="small" icon={<DownOutlined />} disabled={stepIdx === stepIds.length - 1}
              onMouseDown={(e) => e.preventDefault()} onClick={() => moveStepInCapability(capIdx, stepIds, stepIdx, 1)} /></Tooltip>
            <Button size="small" danger onClick={() => removeStepFromCapability(capIdx, stepId)}>移除</Button>
          </Space>
        }
      >
        {renderStepFieldsInCapability(st)}
      </Collapse.Panel>
    );
  }
  function renderCapabilityInterfacesWithFields(cap: FlowCapabilityData, capIdx: number) {
    const capabilityUiKey = capabilityPanelKey(cap, capIdx);
    const stepIds = capabilityActualStepIds(cap);
    const auxiliaryRefs = (cap.request_refs || []).filter((ref) => ref.usage === "option_source" && ref.step_id && !stepIds.includes(ref.step_id));
    const addOptions = capabilityStepSelectOptions(cap);
    const fieldCount = stepIds.reduce((n, sid) => n + (stepById[sid]?.params?.length || 0), 0);
    return (
      <Space direction="vertical" size={10} style={{ width: "100%" }}>
        <Space wrap align="center">
          <Typography.Text strong>添加接口</Typography.Text>
          <NativeSelect
            value={capabilityAddValue[capabilityUiKey] || ""}
            width={460}
            options={[{ label: addOptions.length ? "选择要加入能力的接口" : "没有可添加的接口", value: "" }, ...addOptions]}
            onChange={(v) => setCapabilityAddValue((s) => ({ ...s, [capabilityUiKey]: v }))}
          />
          <NativeSelect
            value={capabilityAddUsage[capabilityUiKey] || ""}
            width={140}
            options={[{ label: "选择用途", value: "" }, ...CAPABILITY_USAGE_OPTIONS]}
            onChange={(v) => setCapabilityAddUsage((s) => ({ ...s, [capabilityUiKey]: v as CapabilityUsage | "" }))}
          />
          <Button
            size="small"
            type="primary"
            disabled={!capabilityAddValue[capabilityUiKey] || !capabilityAddUsage[capabilityUiKey]}
            onClick={() => {
              addStepToCapability(capIdx, capabilityAddValue[capabilityUiKey], capabilityAddUsage[capabilityUiKey]);
              setCapabilityAddValue((s) => ({ ...s, [capabilityUiKey]: "" }));
              setCapabilityAddUsage((s) => ({ ...s, [capabilityUiKey]: "" }));
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
            activeKey={expandedCapabilitySteps[capabilityUiKey] || []}
            onChange={(keys) => setExpandedCapabilitySteps((current) => ({
              ...current,
              [capabilityUiKey]: (Array.isArray(keys) ? keys : [keys]).map(String),
            }))}
          >
            {stepIds.map((stepId, stepIdx) => renderCapabilityStepWithFields(cap, capIdx, stepId, stepIdx))}
          </Collapse>
        )}
        {auxiliaryRefs.length > 0 && (
          <List
            size="small"
            header={<Typography.Text strong>候选来源</Typography.Text>}
            rowKey={(ref) => [
              capabilityUiKey,
              "option-source",
              ref.step_id || ref.request_id || auxiliaryRefs.indexOf(ref),
            ].join(":")}
            dataSource={auxiliaryRefs}
            renderItem={(ref) => {
              const st = stepById[String(ref.step_id || "")];
              return (
                <List.Item actions={[<Button key="remove" size="small" danger onClick={() => removeStepFromCapability(capIdx, String(ref.step_id || ""))}>移除</Button>]}>
                  <Space wrap>
                    <Tag color="cyan">选项来源</Tag>
                    <Typography.Text>{st?.name || ref.path || ref.step_id}</Typography.Text>
                    {st && <PathText value={st.path || stripHost(st.url)} maxWidth={420} />}
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
              <Tag color={row.required ? "red" : undefined}>{row.required ? "必填" : "非必填"}</Tag>
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
      <Space id="flow-workbench" direction="vertical" size={12} style={{ width: "100%" }}>
        <Space wrap>
          <Tooltip title="基于当前能力、接口和人工修改继续规划，并同步修正字段绑定、枚举来源、依赖和接口闭包">
            <Button icon={<RobotOutlined />} type="primary" loading={orchestrateBusy || autoFixBusy} onClick={orchestrateFlow}>生成/优化能力</Button>
          </Tooltip>
          <Button
            icon={<PictureOutlined />}
            loading={analysisScreenshotBusy}
            disabled={analysisScreenshots.length >= MAX_ANALYSIS_SCREENSHOTS || orchestrateBusy || autoFixBusy}
            onClick={() => screenshotInputRef.current?.click()}
          >{"\u4e0a\u4f20\u53c2\u8003\u622a\u56fe"}</Button>
          <input
            ref={screenshotInputRef} type="file" accept="image/png,image/jpeg,image/webp" multiple hidden
            onChange={(event) => { void handleAnalysisScreenshotSelection(event.target.files); }}
          />
          {analysisScreenshots.length > 0 && <Tag color="purple">{"\u5df2\u4e0a\u4f20"} {analysisScreenshots.length} / {MAX_ANALYSIS_SCREENSHOTS}</Tag>}
          {lastAnalysisEvidence && <Tag color={lastAnalysisEvidence.screenshot_count > 0 ? "success" : "default"}>{"\u6700\u8fd1\u5206\u6790\u5df2\u53c2\u8003"} {lastAnalysisEvidence.screenshot_count} {"\u5f20\u622a\u56fe"}</Tag>}
          <Button icon={<PlusOutlined />} onClick={addCapability}>新增能力</Button>
          <Button icon={<RobotOutlined />} loading={namingBusy} onClick={() => { setNamingBusy(true); send({ type: "step_naming" }); }}>命名步骤</Button>
          {flowSpec.meta?.capability_generation && <>
            <Tag color={flowSpec.meta.capability_generation.initial_completed ? "success" : "warning"}>
              {flowSpec.meta.capability_generation.initial_completed ? "语义规划完成" : "语义规划待补全"}
            </Tag>
            {flowSpec.meta?.recording_agent_session?.mode &&
              <Tag color="blue">Pi {flowSpec.meta.recording_agent_session.mode === "repair" ? "修复" : "规划"}</Tag>}
            {!!flowSpec.meta.capability_generation.indexed_range_changes?.length &&
              <Tag color="cyan">识别区间字段 {flowSpec.meta.capability_generation.indexed_range_changes.length}</Tag>}
          </>}
        </Space>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>{"截图可选；上传后，每次点击生成/优化都会结合截图与已录制接口重新分析。"}</Typography.Text>
        <Alert
          showIcon
          type={!lastAnalysisEvidence
            ? "info"
            : lastAnalysisEvidence.status === "applied"
              ? "success"
              : lastAnalysisEvidence.status === "rejected"
                ? "error"
                : "warning"}
          message={!lastAnalysisEvidence
            ? "能力分析结果（等待生成）"
            : (lastAnalysisEvidence.screenshot_count > 0 ? "图片增强" : "常规") + "分析" + (
              lastAnalysisEvidence.status === "applied"
                ? "已应用"
                : lastAnalysisEvidence.status === "rejected"
                  ? "未通过安全准入"
                  : "已完成，无可应用变化"
            )}
          description={(
            <Space direction="vertical" size={4}>
              <Typography.Text style={{ fontSize: 12 }}>
                {lastAnalysisEvidence?.summary || "这里固定显示最近一次生成/优化结果；重新打开页面后从流程数据恢复，只更新内容。"}
              </Typography.Text>
              {lastAnalysisEvidence && (
                <Space wrap size={4}>
                  <Tag color={
                    (lastAnalysisEvidence.model_image_count ?? 0)
                      === lastAnalysisEvidence.screenshot_count
                      ? "success"
                      : "error"
                  }>
                    图片送达 {lastAnalysisEvidence.model_image_count ?? 0} / {lastAnalysisEvidence.screenshot_count}
                  </Tag>
                  <Tag>
                    能力 {lastAnalysisEvidence.capability_count_before ?? "-"} → {lastAnalysisEvidence.capability_count_after ?? "-"}
                  </Tag>
                  <Tag>
                    字段 {lastAnalysisEvidence.field_count_before ?? "-"} → {lastAnalysisEvidence.field_count_after ?? "-"}
                  </Tag>
                  {!!lastAnalysisEvidence.changes?.capabilities && (
                    <Tag color="blue">能力变化 {lastAnalysisEvidence.changes.capabilities}</Tag>
                  )}
                  {!!lastAnalysisEvidence.changes?.fields && (
                    <Tag color="cyan">字段变化 {lastAnalysisEvidence.changes.fields}</Tag>
                  )}
                  {!!lastAnalysisEvidence.changes?.links && (
                    <Tag color="purple">关联变化 {lastAnalysisEvidence.changes.links}</Tag>
                  )}
                </Space>
              )}
            </Space>
          )}
        />
        {analysisScreenshots.length > 0 && (
          <Space wrap size={8}>
            {analysisScreenshots.map((item) => (
              <div key={item.id} style={{ position: "relative", width: 112, height: 72, border: "1px solid #d9d9d9", borderRadius: 6, overflow: "hidden", background: "#fafafa" }}>
                <img src={item.preview_url} alt={item.name} title={item.name} style={{ width: "100%", height: "100%", objectFit: "contain" }} />
                <Button type="primary" danger size="small" shape="circle" icon={<DeleteOutlined />} aria-label={"\u5220\u9664\u622a\u56fe"} onClick={() => removeAnalysisScreenshot(item.id)} style={{ position: "absolute", right: 3, top: 3, transform: "scale(.82)" }} />
              </div>
            ))}
          </Space>
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
                ? (cap.input_schema || derivedInputSchema)
                : derivedInputSchema;
              const lastResponse = [...capSteps].reverse().find((st) => st.response_json != null)?.response_json;
              const derivedOutputSchema = lastResponse != null ? inferJsonSchema(lastResponse) : (cap.output_schema || {});
              return (
                <Collapse.Panel
                  key={capabilityPanelKey(cap, idx)}
                  header={
                    <Space wrap id={`capability-${domAnchorPart(cap.name || cap.capability_id || idx)}`}>
                      <Tag color={cap.confirmed ? "success" : "default"}>{cap.confirmed ? "已采纳" : "模型建议"}</Tag>
                      <Tag color="blue">{optionLabel(kindOptions, cap.kind || "submit")}</Tag>
                      <Tag color={confidenceColor(cap.confidence)}>置信度 {confidencePercent(cap.confidence)}</Tag>
                      <Typography.Text strong>{cap.title || cap.name || `能力 ${idx + 1}`}</Typography.Text>
                      {cap.name && <Typography.Text code>{cap.name}</Typography.Text>}
                    </Space>
                  }
                  extra={
                    <Space onClick={(e) => e.stopPropagation()}>
                      <Tooltip title="能力上移"><Button size="small" icon={<UpOutlined />} disabled={idx === 0}
                        onMouseDown={(e) => e.preventDefault()} onClick={() => moveCapability(idx, -1)} /></Tooltip>
                      <Tooltip title="能力下移"><Button size="small" icon={<DownOutlined />} disabled={idx === capabilities.length - 1}
                        onMouseDown={(e) => e.preventDefault()} onClick={() => moveCapability(idx, 1)} /></Tooltip>
                      <Checkbox checked={!!cap.confirmed} onChange={(e) => updateCapabilityConfirmed(idx, e.target.checked)}>采纳当前定义</Checkbox>
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
          <Collapse size="small" bordered={false}
            activeKey={expandedCapabilityRelationKeys}
            onChange={(keys) => setExpandedCapabilityRelationKeys((Array.isArray(keys) ? keys : [keys]).map(String))}>
            <Collapse.Panel key="capability-relations" header={`能力关系 ${capabilityRelations.length}`}>
              <Space direction="vertical" size={8} style={{ width: "100%" }}>
                {capabilityRelations.map((relation, index) => {
                  const relationType = relation.mode || relation.type || "external_transform";
                  const owner = relation.transform_owner === "skill" ? "Skill 内部" : "调用方";
                  return (
                    <div key={relation.relation_id || `${relation.from_capability}-${relation.to_capability}-${index}`}
                      id={`capability-relation-${domAnchorPart(relation.relation_id || index)}`}
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
        <Alert type="info" showIcon message="服务端权威 FlowSpec 的脱敏只读投影；请在步骤、字段、能力和依赖面板中编辑。" />
        <Input.TextArea rows={14} readOnly value={flowSpec ? JSON.stringify(flowSpec, null, 2) : ""}
          style={{ fontFamily: "monospace", fontSize: 12 }} />
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
              <Tag color={connectionState === "connected" ? "processing" : (connectionState === "connecting" || connectionState === "reconnecting") ? "warning" : "error"}>
                {connectionState === "connected" ? (phase === "publishing" ? "发布中" : "录制中") : connectionState === "connecting" ? "连接中" : connectionState === "reconnecting" ? "重连中" : "已断开"}
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
          <div style={{ border: "1px solid #d9d9d9", borderRadius: 6, overflow: "auto", lineHeight: 0, position: "relative", background: "#f5f5f5", textAlign: "center" }}>
            <canvas ref={frameCanvasRef} draggable={false} role="img" aria-label="录制画面"
              onPointerDown={onImgPointerDown} onPointerMove={onImgPointerMove} onPointerUp={onImgPointerUp} onPointerCancel={onImgPointerCancel}
              onContextMenu={(e) => e.preventDefault()} onWheel={onImgWheel}
              style={{
                width: frameMeta.frameWidth || "auto", maxWidth: "100%", height: "auto",
                display: hasFrame ? "block" : "none", margin: "0 auto", cursor: connectionState === "connected" ? "crosshair" : "not-allowed",
                touchAction: "none", userSelect: "none",
              }} />
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

          {renderFlowWorkbench()}

        </div>
      )}
    </Card>
    </ConfigProvider>
  );
}
