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
  content_type?: string; body_source?: string; headers?: Record<string, string>;
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
  request_refs?: Array<Record<string, any>>;
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
  occurrence_count?: number;
}
interface FlowSpecData {
  flow_id: string; title: string; business_description?: string;
  steps: FlowStepData[]; links: FlowLinkData[]; capabilities?: FlowCapabilityData[];
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
    request_graph?: {
      all_requests?: RequestGraphEntry[];
      selected_steps?: RequestGraphEntry[];
      candidate_reads?: RequestGraphEntry[];
      filtered_requests?: RequestGraphEntry[];
    };
    versions?: Array<{ version: number; action: string; reason?: string; created_at?: string; summary?: any }>;
    current_version?: number;
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
  }>>;
}
interface RecResult {
  ok?: boolean; action?: string; risk_level?: string; mode?: string; reason?: string;
  status?: string; warnings?: string[]; review_notes?: string[]; clarifications?: string[];
  recording_mode?: string; verification_status?: string; verification_basis?: string; skill_id?: string; asset_id?: string;
  api?: { method?: string; path?: string; params?: string[] };
  check_report?: FlowCheckReport;
}
type RecordingMode = "real_submit" | "record_only";

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
const SAFE_COMBO_KEYS = new Set(["a", "z", "y", "Enter", "Backspace"]);
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
  // { label: "枚举(页面)", value:  },
  // { label: "枚举(表单)", value:  },
  { label: "枚举(手动)", value: "manual_enum" },
  // { label: "枚举(静态)", value:  },
  { label: "上游响应", value: "previous_response" },
  { label: "请求头", value: "request_header" },
  { label: "当前用户", value: "current_user" },
  { label: "系统时间", value: "system_time" },
  { label: "页面上下文", value: "page_context" },
  { label: "固定值", value: "constant" },
];
const OPTION_SOURCE_KINDS = ["api_option", "manual_enum" ];
const ENUM_SOURCE_KINDS = [ "manual_enum"];
// 三类分类 × 各自允许的来源，避免出现"用户参数 + 固定值"这种语义不一致组合。
const SOURCE_OPTIONS_BY_CATEGORY: Record<string, Array<{ label: string; value: string }>> = {
  user_param: SOURCE_KIND_OPTIONS.filter((x) =>
    ["user_input", "api_option" , "manual_enum", ].includes(x.value)
  ),
  // 运行期变量由执行环境注入；先允许保持“待配置”，不能静默写死成上游响应。
  runtime_var: SOURCE_KIND_OPTIONS.filter((x) =>
    ["unknown", "previous_response", "page_context", "current_user", "system_time", "request_header"].includes(x.value)
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
    if (current.response_json == null && req.response_json != null) {
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
function capturedRequestSteps(spec: FlowSpecData | null | undefined, req: RequestGraphEntry) {
  const signature = requestGraphSignature(req);
  return (spec?.steps || []).filter((step) => {
    const meta = step.source_meta || {};
    return (req.request_id && String(meta.request_id || "") === String(req.request_id)) ||
      (req.request_index != null && String(meta.request_index ?? "") === String(req.request_index)) ||
      stepRequestSignature(step) === signature;
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
      label: `#${req.sequence ?? req.request_index ?? ""} ${req.method || "GET"} ${req.path || stripHost(req.url || "")}${(req.occurrence_count || 1) > 1 ? ` · ${req.occurrence_count} 次` : ""}`,
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
function schemaFieldRows(schema?: Record<string, any>) {
  if (!schema || typeof schema !== "object") return [];
  const props = schema.properties && typeof schema.properties === "object" ? schema.properties : schema;
  const required = new Set(Array.isArray(schema.required) ? schema.required.map(String) : []);
  return Object.entries(props || {})
    .filter(([, spec]) => spec && typeof spec === "object")
    .map(([name, spec]) => ({
      name,
      type: String((spec as any).type || (spec as any).format || "any"),
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
  if (t === "enum" && opts.length) schema.enum = opts.map((x) => x.value);
  if (t === "list-enum" && opts.length) schema.items = { type: "string", enum: opts.map((x) => x.value) };
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
  const latestFrameRef = useRef<{ seq: number; src: string } | null>(null);
  const frameRafRef = useRef<number | null>(null);
  const renderedFrameSeqRef = useRef(0);
  const wsAliveRef = useRef(false);                                // FC2 修复:跟踪 WS 存活,避免 send 失败时反复弹错
  const isComposingRef = useRef(false);                           // FH2 修复:中文输入法拼写中标记,防 onKbInput 误发中间字符

  const [phase, setPhase] = useState<"idle" | "recording" | "publishing" | "done">("idle");
  const phaseRef = useRef(phase);                                  // FC1 修复:同步最新 phase,ws.onclose 闭包不再 stale
  useEffect(() => { phaseRef.current = phase; }, [phase]);
  const [startUrl, setStartUrl] = useState("");
  const [hasFrame, setHasFrame] = useState(false);
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
  const [action, setAction] = useState("submit_form");
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
  const [newParam, setNewParam] = useState({ step_id: "", path: "", key: "", type: "string", category: "user_param" });
  const [newLink, setNewLink] = useState({ source_step_id: "", source_path: "", target_step_id: "", target_path: "" });
  const [editingLink, setEditingLink] = useState<Record<string, FlowLinkData>>({});
  const [bindDraft, setBindDraft] = useState<Record<string, { source_step_id?: string; source_path?: string }>>({});
  const [jsonDraft, setJsonDraft] = useState("");
  const [jsonErr, setJsonErr] = useState("");
  const [lastServerJson, setLastServerJson] = useState("");
  const [namingBusy, setNamingBusy] = useState(false);
  const [descBusy, setDescBusy] = useState(false);
  const [llmBusy, setLlmBusy] = useState(false);
  const [orchestrateBusy, setOrchestrateBusy] = useState(false);
  const [autoFixBusy, setAutoFixBusy] = useState(false);
  const [activeFlowTab, setActiveFlowTab] = useState("abilities");

  function acceptFlowSpec(fs: FlowSpecData) {
    flowSpecRef.current = fs;
    setFlowSpec(fs);
    const nextTitle = preferredSkillTitle(fs);
    if (nextTitle && !title.trim()) setTitle(nextTitle);
  }

  useEffect(() => () => {
    // FC4 修复:仅当 phase 处于 recording/publishing 时才关 WS(避免 StrictMode 双 mount 或组件复用时误关正在用的 WS)
    // wsRef.current 在首次 mount 时为 null(start 才会建),所以首次 cleanup 一定是 noop,无副作用
    if (phaseRef.current === "recording" || phaseRef.current === "publishing") {
      wsRef.current?.close();
    }
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

  function send(obj: unknown) {
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

  function clearFrame() {
    latestFrameRef.current = null;
    renderedFrameSeqRef.current = 0;
    if (frameRafRef.current != null) {
      window.cancelAnimationFrame(frameRafRef.current);
      frameRafRef.current = null;
    }
    if (imgRef.current) imgRef.current.removeAttribute("src");
    setHasFrame(false);
  }

  function queueFrame(seq: number, data: string) {
    if (!data) return;
    latestFrameRef.current = { seq: Number(seq || 0), src: `data:image/jpeg;base64,${data}` };
    if (frameRafRef.current != null) return;
    frameRafRef.current = window.requestAnimationFrame(() => {
      frameRafRef.current = null;
      const latest = latestFrameRef.current;
      if (!latest || latest.seq <= renderedFrameSeqRef.current) return;
      renderedFrameSeqRef.current = latest.seq;
      if (imgRef.current) imgRef.current.src = latest.src;
      if (!hasFrameRef.current) setHasFrame(true);
    });
  }

  function resetEditorState() {
    flowSpecRef.current = null;
    setFlowSpec(null);
    setCheckReport(null);
    setBindDraft({});
    setEditingLink({});
    setJsonDraft("");
    setJsonErr("");
    setLastServerJson("");
    setActiveFlowTab("abilities");
  }

  function start() {
    if (!tenant) { message.error("请先到「创建 / 进入租户」"); return; }
    if (!startUrl.trim()) { message.error("请填页面地址 start_url"); return; }
    const intercept = recordingMode === "record_only";
    setErr(""); setResult(null); setSteps([]); setReqs([]); clearFrame(); setFields([]); setPicked({});
    setCands([]); setSelects({}); setIdentity({}); setStepSel({}); resetEditorState();
    wsAliveRef.current = true;                                     // FC2 修复:每次 start 重置存活标志
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/onboarding/page/record`);
    wsRef.current = ws;
    ws.onopen = () => send({
      type: "start", tenant, subsystem, start_url: startUrl.trim(),
      base_url: baseUrl.trim() || undefined,
      storage_state: storageState.trim() || undefined,
      intercept,
    });
    ws.onmessage = (ev) => {
      let m: any; try { m = JSON.parse(ev.data); } catch { return; }
      if (m.type === "started") setPhase("recording");
      else if (m.type === "frame") queueFrame(Number(m.seq || 0), m.data);
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
      else if (m.type === "request") setReqs((r) => [...r, m.request].slice(-40));
      else if (m.type === "request_fields") {
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
        setLlmBusy(false); setOrchestrateBusy(false); setAutoFixBusy(false);
        setPhase("recording");
        const fs = m.full_spec || m.flow_spec;
        if (fs) {
          acceptFlowSpec(fs);
          setLastServerJson(JSON.stringify(fs));
        }
        if (m.check_report) setCheckReport(m.check_report);
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
      else if (m.type === "result") {
        setResult(m.report); setPhase("recording");
        if (m.report?.check_report) setCheckReport(m.report.check_report);
        if (m.report?.ok) {
          setFields([]); setPicked({}); setCands([]); setSelects({}); setIdentity({}); setStepSel({});
        }
      }
      else if (m.type === "error") {
        const detail = m.detail || "录制出错";
        setNamingBusy(false); setDescBusy(false); setLlmBusy(false); setOrchestrateBusy(false); setAutoFixBusy(false);
        if (detail.includes("step not found") || detail.includes("link not found")) {
          message.warning("流程已变更，正在同步最新版本");
          send({ type: "refresh_flow_spec" });
        } else {
          message.error(detail);
          setErr(detail);
        }
      }
    };
    ws.onerror = () => setErr("WebSocket 连接失败");
    ws.onclose = () => {
      wsAliveRef.current = false;                                 // FC2 修复:WS 关闭,send 会自动避免刷屏
      if (phaseRef.current === "recording" || phaseRef.current === "publishing") setPhase("idle");
    };
  }

  function onImgClick(e: React.MouseEvent<HTMLImageElement>) {
    const img = imgRef.current; if (!img) return;
    const r = img.getBoundingClientRect();
    send({ type: "input", event: { kind: "click", nx: (e.clientX - r.left) / r.width, ny: (e.clientY - r.top) / r.height } });
    kbRef.current?.focus({ preventScroll: true });
  }
  function relayKb(el: HTMLInputElement) {
    const v = el.value;
    if (v) { send({ type: "input", event: { kind: "text", text: v } }); el.value = ""; }
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
    if (key) { send({ type: "input", event: { kind: "key", key } }); e.preventDefault(); }
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
    if (!action.trim() || badAction(action.trim())) return;
    if (!steps.length && !reqs.length) { message.error("还没抓到提交请求、也没录到步骤"); return; }
    setResult(null); setPhase("publishing");
    send({ type: "finalize", action: action.trim(), title: title.trim(), success_marker: null, steps });
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
    const { param_map, selList, idList, step_idxs } = payload();
    const currentSpec = flowSpecRef.current || flowSpec;
    if (!currentSpec) { message.error("请先生成 FlowSpec 后再发布"); return; }
    const publishTitle = title.trim() || preferredSkillTitle(currentSpec);
    setResult(null); setPhase("publishing");
    send({ type: "publish_request", action: action.trim(), title: publishTitle,
      param_map, selects: selList, identity: idList, step_idxs, use_flow_spec: true, flow_spec: currentSpec });
  }
  function stopAll() {
    send({ type: "stop" }); wsRef.current?.close();
    setPhase("idle"); setResult(null); setSteps([]); clearFrame(); setFields([]); setPicked({});
    setCands([]); setSelects({}); setIdentity({}); setStepSel({}); resetEditorState();
  }

  function sendReplace(next: FlowSpecData) { send({ type: "flow_replace", flow_spec: next }); }
  function updateFlowField(k: string, v: any) { send({ type: "flow_update", edits: [{ op: "update_flow", field: k, value: v }] }); }
  function updateStep(stepId: string, field: string, value: any) {
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
    };
    flowSpecRef.current = next;
    setFlowSpec(next);
  }
  function updateParam(stepId: string, p: FlowParam, field: string, value: any) {
    patchLocalParam(stepId, p, { [field]: value });
    send({ type: "flow_update", edits: [paramEdit(stepId, p, field, value)] });
  }
  function updateParamType(step: FlowStepData, p: FlowParam, value: string) {
    // 类型只描述数据形态，不拥有分类和来源。用户把文本改成枚举、或把枚举改回文本时，
    // 不能顺带把 runtime_var/previous_response 等人工配置改掉。
    patchLocalParam(step.step_id, p, { type: value });
    send({ type: "flow_update", edits: [paramEdit(step.step_id, p, "type", value)] });
  }
  function updateParamCategory(stepId: string, p: FlowParam, category: string) {
    const currentSourceKind = normalizeSourceKindForUi(p.source_kind);
    const allowed = sourceOptionsForCategory(category).some((o) => o.value === currentSourceKind);
    const sourceKind = allowed ? currentSourceKind : defaultSourceForCategory(category);
    const keepExistingSource = allowed && !!p.source && (
      sourceKind !== "previous_response"
      || !!((p.source as any)?.step_id || (p.source as any)?.response_path)
    );
    const source = keepExistingSource
      ? p.source
      : sourceKind === "unknown" ? {}
        : sourceKind === "user_input" ? { kind: "sample", path: p.path }
          : { kind: sourceKind, path: p.path, manual: true };
    const patch: Record<string, any> = {
      category,
      source_kind: sourceKind,
      source,
      exposed_to_user: category === "user_param",
      need_human_confirm: sourceKind === "unknown",
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
      paramEdit(stepId, p, "need_human_confirm", sourceKind === "unknown"),
    );
    send({ type: "flow_update", edits });
  }
  function updateParamSourceKind(stepId: string, p: FlowParam, sourceKind: string) {
    const category = p.category || "user_param";
    const currentSource = p.source as any;
    const nextSource = sourceKind === "unknown" ? {}
      : sourceKind === "user_input" ? { kind: "sample", path: p.path }
        : sourceKind === "previous_response" && (currentSource?.step_id || currentSource?.response_path)
          ? { ...currentSource, kind: "previous_response" }
          : { kind: sourceKind, path: p.path, manual: true };
    // 只有离开“上游响应”时才移除原依赖；重新选择上游响应不能先把现有绑定删掉。
    const edits: any[] = sourceKind === "previous_response" ? [] : (flowSpec?.links || [])
      .filter((l) => l.target_step_id === stepId && stripBodyPrefix(l.target_path) === stripBodyPrefix(p.path))
      .map((l) => ({ op: "remove", link_id: l.link_id, reset_target: false }));
    edits.push(
      paramEdit(stepId, p, "source_kind", sourceKind),
      paramEdit(stepId, p, "source", nextSource),
      paramEdit(stepId, p, "exposed_to_user", category === "user_param"),
      paramEdit(stepId, p, "need_human_confirm", sourceKind === "unknown"),
      paramEdit(stepId, p, "editable", true),
    );
    patchLocalParams(stepId, p, {
      source_kind: sourceKind,
      source: nextSource,
      exposed_to_user: category === "user_param",
      need_human_confirm: sourceKind === "unknown",
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
  function moveStep(idx: number, dir: -1 | 1) {
    if (!flowSpec) return;
    const ids = flowSpec.steps.map((s) => s.step_id);
    const j = idx + dir;
    if (j < 0 || j >= ids.length) return;
    [ids[idx], ids[j]] = [ids[j], ids[idx]];
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
    setLlmBusy(true);
    send({ type: "llm_recommendations" });
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
    const currentSpec = flowSpecRef.current || flowSpec;
    if (!currentSpec) return;
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
    setOrchestrateBusy(true);
    setAutoFixBusy(true);
    send({ type: "orchestrate_flow", flow_spec: currentSpec });
  }
  function autoFixFlow() {
    if (!flowSpec) return;
    setAutoFixBusy(true);
    send({ type: "auto_fix_flow" });
  }
  function addCapability() {
    const idx = (flowSpec?.capabilities?.length || 0) + 1;
    send({ type: "flow_update", edits: [{
      op: "add_capability",
      capability: {
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
      },
    }] });
  }
  function updateCapabilityConfirmed(idx: number, confirmed: boolean) {
    send({ type: "flow_update", edits: [{ op: "update_capability", capability_index: idx, field: "confirmed", value: confirmed }] });
  }
  function updateCapabilityField(idx: number, field: string, value: any) {
    send({ type: "flow_update", edits: [{ op: "update_capability", capability_index: idx, field, value }] });
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
  function addStepToCapability(idx: number, value?: string) {
    if (!value) return;
    if (value.startsWith("step:")) {
      send({ type: "flow_update", edits: [{ op: "add_capability_step", capability_index: idx, step_id: value.slice(5) }] });
      return;
    }
    if (value.startsWith("req:")) {
      const requestKey = value.slice(4);
      const req = findCapturedRequest(flowSpec, requestKey);
      if (!req) { message.warning("没有找到选中的捕获接口"); return; }
      send({ type: "flow_update", edits: [{ op: "add_capability_step", capability_index: idx, request_index: req?.request_index, request_id: req?.request_id }] });
    }
  }
  function removeStepFromCapability(idx: number, stepId: string) {
    send({ type: "flow_update", edits: [{ op: "remove_capability_step", capability_index: idx, step_id: stepId }] });
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
  function moveCapability(idx: number, delta: number) {
    if (!flowSpec) return;
    const caps = flowSpec.capabilities || [];
    const to = idx + delta;
    if (to < 0 || to >= caps.length) return;
    const refs = caps.map(capabilityRef);
    const [item] = refs.splice(idx, 1);
    refs.splice(to, 0, item);
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
    const by: Record<string, Array<{ message: string; severity: string; target?: Record<string, any> }>> = {};
    for (const [key, items] of Object.entries(report?.issue_groups || {})) {
      by[key] = (items || []).map((item) => ({
        message: item.message || "待处理问题",
        severity: item.severity || "warning",
        target: item.target,
      }));
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
    const out: Array<{ key: string; label: string; color: string; items: Array<{ message: string; severity: string; target?: Record<string, any> }> }> = [];
    for (const item of order) {
      if (by[item.key]?.length) out.push({ ...item, items: by[item.key] });
    }
    for (const key of Object.keys(by)) {
      if (!order.some((item) => item.key === key)) out.push({ key, label: key, color: "default", items: by[key] });
    }
    return out;
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
      selects.find((s) => !s.path && s.param === p.key) ||
      selects.find((s) => s.param === p.key);
  }
  function enumOptionEdits(step: FlowStepData, p: FlowParam, options: Array<string | { label: string; value: any }>, optionMap?: Record<string, any>) {
    const edits: any[] = [
      paramEdit(step.step_id, p, "enum_options", options),
      paramEdit(step.step_id, p, "enum_value_map", optionMap || null),
    ];
    if (p.type !== "enum" && p.type !== "list-enum" && options.length) {
      edits.push(paramEdit(step.step_id, p, "type", "enum"));
    }
    if (p.category !== "user_param") {
      edits.push(paramEdit(step.step_id, p, "category", "user_param"));
    }
    if (!OPTION_SOURCE_KINDS.includes(p.source_kind || "")) {
      edits.push(paramEdit(step.step_id, p, "source_kind", "manual_enum"));
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
    if (p.type !== "enum" && p.type !== "list-enum") {
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
    if (p.source_kind === "page_context") return "页面上下文：由当前页面/应用上下文提供";
    return "来源未确认：需要选择用户输入、上游响应、固定值或系统来源";
  }
  function renderFlowWorkbench() {
    if (!flowSpec) return null;
    const totalParams = flowSpec.steps.reduce((n, s) => n + (s.params?.length || 0), 0);
    const capabilities = flowSpec.capabilities || [];
    const capturedTotal = allCapturedRequests(flowSpec).length;
    const capabilityGenerated = capabilities.length > 0 && !!flowSpec.meta?.capability_model?.status;
    const visibleReviewItems = capabilityGenerated ? reviewItems : [];
    const unconfirmedCapabilities = capabilities.filter((cap) => !cap.confirmed || cap.requires_human_confirm).length;
    const publishIssueGroups = groupedPublishIssues(checkReport, visibleReviewItems);
    const hasPublishAdvice = publishIssueGroups.some((group) => group.items.length > 0);
    return (
      <Card
        style={{ marginTop: 16 }}
        title={
          <Space wrap>
            <Typography.Text strong>编排工作台</Typography.Text>
            <Tag color="cyan">{capturedTotal} 接口</Tag>
            <Tag>{totalParams} 字段</Tag>
            {capabilities.length > 0 && <Tag color="geekblue">{capabilities.length} 能力</Tag>}
            {unconfirmedCapabilities > 0 && <Tag color="warning">{unconfirmedCapabilities} 能力待确认</Tag>}
            <Tag color={flowSpec.risk_level === "L4" ? "error" : "orange"}>风险 {flowSpec.risk_level}</Tag>
            {visibleReviewItems.length > 0 && <Tag color="error">{visibleReviewItems.length} 高风险待确认</Tag>}
          </Space>
        }
        extra={
          <Space wrap>
            <Button size="small" loading={phase === "publishing"} onClick={finalize}>重新抓取</Button>
            <Button size="small" type="primary" loading={phase === "publishing"} onClick={publishRequest}>发布当前流程</Button>
          </Space>
        }
      >
        {checkReport && capabilityGenerated && (
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
                        {group.items.slice(0, 4).map((item, issueIdx) => (
                          <Typography.Text key={`${group.key}-${issueIdx}`} type={item.severity === "warning" ? "secondary" : "danger"} style={{ fontSize: 12 }}>
                            {item.message}
                          </Typography.Text>
                        ))}
                        {group.items.length > 4 && <Typography.Text type="secondary" style={{ fontSize: 12 }}>另有 {group.items.length - 4} 项</Typography.Text>}
                      </Space>
                    </div>
                  ))}
                </Space>
              </Space>
            }
          />
        )}
        <Tabs
          activeKey={activeFlowTab}
          onChange={setActiveFlowTab}
          destroyOnHidden={false}
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
      <Collapse defaultActiveKey={[]} bordered={false}>
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
  function renderParamEditorInCapability(step: FlowStepData, p: FlowParam, scopedStepIds: Set<string>) {
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
      <List.Item key={paramDraftKey(step.step_id, p)} style={{ padding: "12px 0" }}>
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
                {needsManualConfirm && <Tag color="warning">待确认</Tag>}
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>{p.reason}</Typography.Text>
              </Space>
              <Typography.Text type="secondary" style={{ display: "block", marginTop: 6, fontSize: 12 }}>
                {paramSourceText(step, p, linked)}
              </Typography.Text>
            </Col>
            <Col>
              <Button size="small" danger onClick={() => send({ type: "flow_update", edits: [paramRemoveEdit(step.step_id, p)] })}>删除字段</Button>
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
            <FieldControl label="必填">
              <Checkbox checked={!!p.required} onChange={(e) => updateParam(step.step_id, p, "required", e.target.checked)}>必填</Checkbox>
            </FieldControl>
            <FieldControl label="展示">
              {p.category === "user_param" ? (
                <Checkbox checked={p.exposed_to_user !== false} onChange={(e) => updateParam(step.step_id, p, "exposed_to_user", e.target.checked)}>暴露给调用方</Checkbox>
              ) : <Typography.Text type="secondary">不对调用方展示</Typography.Text>}
            </FieldControl>
          </div>
          {needsManualConfirm && <Button size="small" style={{ marginTop: 8 }} onClick={() => updateParam(step.step_id, p, "need_human_confirm", false)}>已确认</Button>}
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
                              value={selectBinding?.id_path || selectBinding?.path || p.path || p.key || ""}
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
                    <Space wrap>
                      <Typography.Text strong style={{ fontSize: 12 }}>运行期来源</Typography.Text>
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
            rowKey={(p) => paramDraftKey(step.step_id, p)}
            dataSource={step.params || []}
            renderItem={(p) => renderParamEditorInCapability(step, p, scopedStepIds)}
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
          <Space wrap>
            <Tag color="purple">接口 {stepIdx + 1}</Tag>
            <Tag color={(st.method || "GET").toUpperCase() === "GET" ? "blue" : "green"}>{st.method}</Tag>
            <Typography.Text strong>{st.name || fallbackStepName(st.method, st.path)}</Typography.Text>
            <PathText value={st.path || stripHost(st.url)} maxWidth={420} />
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
          <Button
            size="small"
            type="primary"
            disabled={!capabilityAddValue[capIdx]}
            onClick={() => {
              addStepToCapability(capIdx, capabilityAddValue[capIdx]);
              setCapabilityAddValue((s) => ({ ...s, [capIdx]: "" }));
            }}
          >
            添加接口
          </Button>
          <Tag>{stepIds.length} 接口 / {fieldCount} 字段</Tag>
        </Space>
        {!stepIds.length ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="未绑定接口" />
        ) : (
          <Collapse size="small">
            {stepIds.map((stepId, stepIdx) => renderCapabilityStepWithFields(cap, capIdx, stepId, stepIdx))}
          </Collapse>
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
              <Tag>{PARAM_TYPE_LABELS[row.type] || row.type}</Tag>
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
    const kindOptions = CAPABILITY_KIND_OPTIONS;
    return (
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Space wrap>
          <Tooltip title="基于当前能力、接口和人工修改继续规划，并同步修正字段绑定、枚举来源、依赖和接口闭包">
            <Button icon={<RobotOutlined />} type="primary" loading={orchestrateBusy || autoFixBusy} onClick={orchestrateFlow}>生成/优化能力</Button>
          </Tooltip>
          <Button icon={<PlusOutlined />} onClick={addCapability}>新增能力</Button>
          <Button icon={<RobotOutlined />} loading={namingBusy} onClick={() => { setNamingBusy(true); send({ type: "step_naming" }); }}>命名步骤</Button>
        </Space>
        {!capabilities.length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有能力编排" /> : (
          <Collapse size="small">
            {capabilities.map((cap, idx) => {
              const stepIds = capabilityActualStepIds(cap);
              const capSteps = stepIds.map((sid) => stepById[sid]).filter(Boolean);
              const capParams = capSteps.flatMap((st) => st.params || []);
              const derivedInputSchema = {
                type: "object",
                properties: Object.fromEntries(capParams
                  .filter((p) => p.category === "user_param" && p.exposed_to_user !== false)
                  .map((p) => [p.key || p.path, jsonSchemaForParam(p)])),
                required: capParams
                  .filter((p) => p.category === "user_param" && p.exposed_to_user !== false && p.required)
                  .map((p) => p.key || p.path),
              };
              const lastResponse = [...capSteps].reverse().find((st) => st.response_json != null)?.response_json;
              const derivedOutputSchema = lastResponse != null ? inferJsonSchema(lastResponse) : (cap.output_schema || {});
              return (
                <Collapse.Panel
                  key={`${cap.name || idx}-${idx}`}
                  header={
                    <Space wrap>
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
                      <Tooltip title="删除"><Button size="small" danger icon={<DeleteOutlined />} onClick={() => removeCapability(idx)} /></Tooltip>
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
                    <Collapse ghost size="small" defaultActiveKey={["interfaces"]}>
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
                        {renderCapabilityIOBusinessView(idx, derivedInputSchema, derivedOutputSchema)}
                      </Collapse.Panel>
                    </Collapse>
                  </Space>
                </Collapse.Panel>
              );
            })}
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
                <Tag>{row.type}</Tag>
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
                .filter((param) => param.category === "user_param" && param.exposed_to_user !== false)
                .map((param) => [param.key || param.path, jsonSchemaForParam(param)])),
              required: capParams
                .filter((param) => param.category === "user_param" && param.exposed_to_user !== false && param.required)
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
                            return (
                              <Space key={stepId} size={4} wrap>
                                <Tag>{st?.name || st?.path || stepId}</Tag>
                                {st && <PathText value={st.path || stripHost(st.url)} maxWidth={260} />}
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
                <Button icon={<RobotOutlined />} loading={llmBusy} onClick={refreshLlmRecommendations}>刷新智能推荐</Button>
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
                  <Tooltip title="删除"><Button size="small" danger icon={<DeleteOutlined />} onClick={() => removeStepWithConfirm(step)} /></Tooltip>
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
                              {needsManualConfirm && <Tag color="warning">待确认</Tag>}
                              <Typography.Text type="secondary" style={{ fontSize: 12 }}>{p.reason}</Typography.Text>
                            </Space>
                            <Typography.Text type="secondary" style={{ display: "block", marginTop: 6, fontSize: 12 }}>
                              {paramSourceText(step, p, linked)}
                            </Typography.Text>
                          </Col>
                          <Col>
                            <Space size={6} wrap>
                              <Button size="small" danger onClick={() => send({ type: "flow_update", edits: [paramRemoveEdit(step.step_id, p)] })}>删除</Button>
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
                          <FieldControl label="必填">
                            <Checkbox checked={!!p.required} onChange={(e) => updateParam(step.step_id, p, "required", e.target.checked)}>必填</Checkbox>
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
                                      value={selectBinding?.id_path || selectBinding?.path || p.path || p.key || ""}
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
                            <Space wrap>
                              <Typography.Text strong style={{ fontSize: 12 }}>运行期来源</Typography.Text>
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
            <Button type="primary" onClick={start}>开始录制</Button>
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
              <Tag color="processing">{phase === "publishing" ? "发布中" : "录制中"}</Tag>
              <Button size="small" disabled={phase === "publishing"} onClick={resetFromHere}>从这里开始录</Button>
              <Button size="small" onClick={stopAll} disabled={phase === "publishing"}>结束录制</Button>
              <Form.Item label="动作名" required style={{ marginBottom: 0 }}>
                <Input value={action} onChange={(e) => setAction(e.target.value)} style={{ width: 190 }} />
              </Form.Item>
              <Form.Item label="标题" style={{ marginBottom: 0 }}>
                <Input value={title} onChange={(e) => setTitle(e.target.value)} style={{ width: 180 }} />
              </Form.Item>
              <Button type="primary" loading={phase === "publishing"} disabled={!steps.length && !reqs.length} onClick={finalize}>
                停止并分析请求
              </Button>
            </Space>
          </div>
          <div style={{ border: "1px solid #d9d9d9", borderRadius: 6, overflow: "hidden", lineHeight: 0, position: "relative" }}>
            <img ref={imgRef} onClick={onImgClick} draggable={false}
              onWheel={(e) => send({ type: "input", event: { kind: "scroll", dy: e.deltaY } })}
              style={{ width: "100%", display: hasFrame ? "block" : "none", cursor: "crosshair" }} alt="录制画面" />
            {!hasFrame && <div style={{ padding: 40, textAlign: "center", color: "#999", lineHeight: 1.6 }}>等待浏览器画面</div>}
            <input ref={kbRef} onInput={onKbInput} onKeyDown={onKbKeyDown} onPaste={onKbPaste}
              onCompositionStart={onKbCompositionStart} onCompositionUpdate={onKbCompositionUpdate} onCompositionEnd={onKbCompositionEnd}
              autoComplete="off" aria-hidden="true"
              style={{ position: "absolute", left: 0, top: 0, width: 1, height: 1, opacity: 0, border: 0, padding: 0 }} />
          </div>

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
