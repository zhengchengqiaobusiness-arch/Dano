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
  CheckCircleOutlined,
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
  options?: string[] | null; count?: number; multi?: boolean;
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
interface FlowSpecData {
  flow_id: string; title: string; business_description?: string;
  steps: FlowStepData[]; links: FlowLinkData[];
  risk_level: string; review_items?: ReviewItemData[];
  meta?: {
    request_roles?: RequestRoleData[];
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
const SOURCE_KIND_OPTIONS = [
  { label: "用户输入", value: "user_input" },
  { label: "上游响应", value: "previous_response" },
  { label: "请求头", value: "request_header" },
  { label: "当前用户", value: "current_user" },
  { label: "系统时间", value: "system_time" },
  { label: "页面上下文", value: "page_context" },
  { label: "固定值", value: "constant" },
  { label: "接口候选", value: "api_option" },
  { label: "页面枚举", value: "page_enum" },
  { label: "固定枚举", value: "static_enum" },
  { label: "手工枚举", value: "manual_enum" },
  { label: "未知", value: "unknown" },
];
const OPTION_SOURCE_KINDS = ["api_option", "page_enum", "static_enum", "manual_enum", "form_option"];
const ENUM_SOURCE_KINDS = ["page_enum", "static_enum", "manual_enum", "form_option"];
const SOURCE_OPTIONS_BY_CATEGORY: Record<string, Array<{ label: string; value: string }>> = {
  user_param: SOURCE_KIND_OPTIONS.filter((x) => ["user_input", ...OPTION_SOURCE_KINDS].includes(x.value)),
  runtime_var: SOURCE_KIND_OPTIONS.filter((x) => ["previous_response", "request_header", "current_user", "system_time", "page_context", "api_option", "unknown"].includes(x.value)),
  system_const: SOURCE_KIND_OPTIONS.filter((x) => ["constant", "page_context"].includes(x.value)),
};
const PARAM_TYPE_OPTIONS = ["string", "number", "boolean", "datetime", "date", "enum", "array", "object", "list-enum"]
  .map((x) => ({ label: x, value: x }));
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
  return sourceKind === "form_option" ? "static_enum" : (sourceKind || "");
}
function sourceOptionsForCategory(category?: string) {
  return SOURCE_OPTIONS_BY_CATEGORY[category || "user_param"] || SOURCE_KIND_OPTIONS;
}
function defaultSourceForCategory(category: string, current?: string) {
  const options = sourceOptionsForCategory(category);
  const normalized = normalizeSourceKindForUi(current);
  if (normalized && options.some((x) => x.value === normalized)) return normalized;
  return options[0]?.value || "unknown";
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
      {options.map((opt) => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
    </select>
  );
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
        {options.filter((opt) => opt.value).map((opt) => (
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
        {options.map((opt) => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
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
        {options.map((opt) => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
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
  const autoResolvedReviewKeyRef = useRef("");
  const autoDedupedStepKeyRef = useRef("");
  const autoLinkedRuntimeKeyRef = useRef("");
  const autoLlmRecommendKeyRef = useRef("");
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
  const [activeFlowTab, setActiveFlowTab] = useState("review");

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
    setFlowSpec(null);
    setCheckReport(null);
    setBindDraft({});
    setEditingLink({});
    setJsonDraft("");
    setJsonErr("");
    setLastServerJson("");
    setActiveFlowTab("review");
    autoResolvedReviewKeyRef.current = "";
    autoDedupedStepKeyRef.current = "";
    autoLinkedRuntimeKeyRef.current = "";
    autoLlmRecommendKeyRef.current = "";
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
        setLlmBusy(false);
        const fs = m.full_spec || m.flow_spec;
        if (fs) {
          setFlowSpec(fs);
          setLastServerJson(JSON.stringify(fs));
          autoDedupeReadSteps(fs);
          autoLinkUnmatchedRuntimeFields(fs);
          autoResolveNonHighReviews(fs, m.check_report);
          autoRefreshLlmRecommendations(fs, m.check_report);
        }
        if (m.check_report) setCheckReport(m.check_report);
      }
      else if (m.type === "step_names") {
        setNamingBusy(false);
        if (m.full_spec) { setFlowSpec(m.full_spec); setLastServerJson(JSON.stringify(m.full_spec)); }
        if (m.check_report) setCheckReport(m.check_report);
        message.success("步骤名称已刷新");
      }
      else if (m.type === "business_description") {
        setDescBusy(false);
        if (m.full_spec) { setFlowSpec(m.full_spec); setLastServerJson(JSON.stringify(m.full_spec)); }
        else if (m.description && flowSpec) setFlowSpec({ ...flowSpec, business_description: m.description });
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
        setNamingBusy(false); setDescBusy(false); setLlmBusy(false);
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
    if (!action.trim() || badAction(action.trim())) return;
    const { param_map, selList, idList, step_idxs } = payload();
    if (!flowSpec) { message.error("请先生成 FlowSpec 后再发布"); return; }
    setResult(null); setPhase("publishing");
    send({ type: "publish_request", action: action.trim(), title: title.trim(),
      param_map, selects: selList, identity: idList, step_idxs, use_flow_spec: true });
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
  function updateParam(stepId: string, p: FlowParam, field: string, value: any) {
    send({ type: "flow_update", edits: [{ op: "update", step_id: stepId, param_path: p.path, field, value }] });
  }
  function updateParamCategory(stepId: string, p: FlowParam, category: string) {
    const sourceKind = defaultSourceForCategory(category, p.source_kind);
    send({ type: "flow_update", edits: [
      { op: "update", step_id: stepId, param_path: p.path, field: "category", value: category },
      { op: "update", step_id: stepId, param_path: p.path, field: "source_kind", value: sourceKind },
      { op: "update", step_id: stepId, param_path: p.path, field: "source", value: sourceKind === "unknown" ? {} : { kind: sourceKind, path: p.path, manual: true } },
      { op: "update", step_id: stepId, param_path: p.path, field: "exposed_to_user", value: category === "user_param" },
      { op: "update", step_id: stepId, param_path: p.path, field: "need_human_confirm", value: false },
    ] });
  }
  function updateParamSourceKind(stepId: string, p: FlowParam, sourceKind: string) {
    const category = sourceKind === "constant"
      ? "system_const"
      : sourceKind === "user_input" || (OPTION_SOURCE_KINDS.includes(sourceKind) && p.category !== "runtime_var")
        ? "user_param"
        : "runtime_var";
    send({ type: "flow_update", edits: [
      { op: "update", step_id: stepId, param_path: p.path, field: "category", value: category },
      { op: "update", step_id: stepId, param_path: p.path, field: "source_kind", value: sourceKind },
      { op: "update", step_id: stepId, param_path: p.path, field: "source", value: sourceKind === "unknown" ? {} : { kind: sourceKind, path: p.path, manual: true } },
      { op: "update", step_id: stepId, param_path: p.path, field: "exposed_to_user", value: category === "user_param" },
      { op: "update", step_id: stepId, param_path: p.path, field: "need_human_confirm", value: sourceKind === "unknown" },
    ] });
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
        setFlowSpec((cur) => {
          if (!cur) return cur;
          return {
            ...cur,
            steps: cur.steps.filter((s) => s.step_id !== step.step_id),
            links: cur.links.filter((l) => l.source_step_id !== step.step_id && l.target_step_id !== step.step_id),
          };
        });
        message.success("已删除步骤，正在同步校验");
      },
    });
  }
  function resolveReview(reviewId: string, resolved = true) {
    send({ type: "flow_update", edits: [{ op: "resolve_review", review_id: reviewId, resolved }] });
  }
  function autoResolveNonHighReviews(spec: FlowSpecData, report?: FlowCheckReport) {
    const list = (report?.review_items?.length ? report.review_items : spec.review_items) || [];
    const nonHigh = list.filter((item) => !item.resolved && item.severity !== "high");
    if (!nonHigh.length) return;
    const version = spec.meta?.current_version ?? 0;
    // FH4 修复:key 增加 report.review_items.length(spec 路径下)与 list 来源,避免后端增删 review 后 id 集合不变但内容变了仍被认为是同一 key
    const source = report?.review_items?.length ? "report" : "spec";
    const key = `${spec.flow_id}:${version}:${source}:${list.length}:${nonHigh.map((item) => item.id).sort().join("|")}`;
    if (autoResolvedReviewKeyRef.current === key) return;
    autoResolvedReviewKeyRef.current = key;
    send({ type: "flow_update", edits: [{ op: "resolve_reviews", exclude_severities: ["high"], resolved: true }] });
  }
  function autoDedupeReadSteps(spec: FlowSpecData) {
    const seen = new Set<string>();
    const duplicates: string[] = [];
    for (const step of spec.steps || []) {
      const method = (step.method || "GET").toUpperCase();
      const role = step.source_meta?.role || step.semantic_role || "";
      if (["POST", "PUT", "PATCH", "DELETE"].includes(method)) continue;
      if (role && !["business_get", "read_context", "read_option"].includes(role)) continue;
      const key = `${method}:${purePath(step.path || step.url)}`;
      if (seen.has(key)) duplicates.push(step.step_id);
      seen.add(key);
    }
    if (!duplicates.length) return;
    const version = spec.meta?.current_version ?? 0;
    const key = `${spec.flow_id}:${version}:${duplicates.join("|")}`;
    if (autoDedupedStepKeyRef.current === key) return;
    autoDedupedStepKeyRef.current = key;
    send({ type: "flow_update", edits: [{ op: "dedupe_steps" }] });
  }
  function autoLinkUnmatchedRuntimeFields(spec: FlowSpecData) {
    const boring = new Set(["", "0", "1", "true", "false", "200", "ok", "success", "null", "none"]);
    const edits: any[] = [];
    const existingTargets = new Set((spec.links || []).map((l) => `${l.target_step_id}:${stripBodyPrefix(l.target_path)}`));
    const stepIndex = new Map((spec.steps || []).map((s, i) => [s.step_id, i]));

    for (const targetStep of spec.steps || []) {
      const targetIdx = stepIndex.get(targetStep.step_id) ?? 0;
      for (const p of targetStep.params || []) {
        if (p.category !== "runtime_var" || p.source_kind !== "unknown") continue;
        if (existingTargets.has(`${targetStep.step_id}:${stripBodyPrefix(p.path)}`)) continue;
        const value = String(p.value ?? "").trim();
        if (!value || boring.has(value.toLowerCase())) continue;

        const matches: Array<{ step: FlowStepData; path: string }> = [];
        for (const sourceStep of spec.steps || []) {
          const sourceIdx = stepIndex.get(sourceStep.step_id) ?? 0;
          if (sourceIdx >= targetIdx) continue;
          for (const leaf of leafPathValues(sourceStep.response_json)) {
            if (leaf.value === value) matches.push({ step: sourceStep, path: leaf.path });
          }
        }
        if (matches.length !== 1) continue;
        edits.push({
          op: "add",
          step_id: matches[0].step.step_id,
          link: {
            source_step_id: matches[0].step.step_id,
            source_path: matches[0].path,
            target_step_id: targetStep.step_id,
            target_path: p.path,
            confirmed: true,
            reason: "加载 FlowSpec 时按录制值唯一匹配到上游响应，自动建立运行期依赖",
          },
        });
      }
    }
    if (!edits.length) return;
    const version = spec.meta?.current_version ?? 0;
    const key = `${spec.flow_id}:${version}:${edits.map((e) => `${e.link.source_step_id}:${e.link.source_path}->${e.link.target_step_id}:${e.link.target_path}`).join("|")}`;
    if (autoLinkedRuntimeKeyRef.current === key) return;
    autoLinkedRuntimeKeyRef.current = key;
    send({ type: "flow_update", edits });
  }
  function autoRefreshLlmRecommendations(spec: FlowSpecData, report?: FlowCheckReport) {
    const list = (report?.review_items?.length ? report.review_items : spec.review_items) || [];
    const high = list.filter((item) => !item.resolved && item.severity === "high");
    if (!high.length) return;
    if (high.some((item) => item.llm_suggestions?.length)) return;
    const version = spec.meta?.current_version ?? 0;
    const key = `${spec.flow_id}:${version}:${high.map((item) => item.id).sort().join("|")}`;
    if (autoLlmRecommendKeyRef.current === key) return;
    autoLlmRecommendKeyRef.current = key;
    setLlmBusy(true);
    send({ type: "llm_recommendations" });
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
        { op: "update", step_id: tgt.step_id, param_path: tgt.path, field: "category", value: "system_const" },
        { op: "update", step_id: tgt.step_id, param_path: tgt.path, field: "exposed_to_user", value: false },
        { op: "resolve_review", review_id: item.id, resolved: true },
      );
    } else if (tgt.step_id && tgt.path && (action === "confirm_field_source" || action === "bind_runtime_source")) {
      const [cat, sourceKind] = guess.split("/");
      edits.push(
        { op: "update", step_id: tgt.step_id, param_path: tgt.path, field: "category", value: cat || "runtime_var" },
        ...(sourceKind ? [{ op: "update", step_id: tgt.step_id, param_path: tgt.path, field: "source_kind", value: sourceKind }] : []),
        { op: "update", step_id: tgt.step_id, param_path: tgt.path, field: "need_human_confirm", value: false },
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
          confidence: suggestion.confidence || 0,
          reason: suggestion.reason || "LLM 推荐并由用户确认的上游响应依赖",
        },
      });
    } else if (suggestion.action === "set_runtime_source" && suggestion.source_kind) {
      if (suggestion.source_kind === "request_header" || suggestion.source_kind === "unknown") {
        message.warning("该建议仍缺少可执行来源，请在字段页手动补充");
        setActiveFlowTab("params");
        return;
      }
      edits.push(
        { op: "update", step_id: targetStepId, param_path: targetPath, field: "category", value: "runtime_var" },
        { op: "update", step_id: targetStepId, param_path: targetPath, field: "source_kind", value: suggestion.source_kind },
        { op: "update", step_id: targetStepId, param_path: targetPath, field: "source", value: { kind: suggestion.source_kind, path: targetPath } },
        { op: "update", step_id: targetStepId, param_path: targetPath, field: "need_human_confirm", value: false },
      );
    } else {
      message.info("该项仍需要人工判断，请在字段页手动确认");
      setActiveFlowTab("params");
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
          message.warning("当前高风险项需要先到字段页手动绑定来源");
          setActiveFlowTab("params");
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
    send({ type: "flow_update", edits: [{ op: "add", step_id: source_step_id, link: { source_step_id, source_path, target_step_id, target_path } }] });
    setNewLink({ source_step_id: "", source_path: "", target_step_id: "", target_path: "" });
  }
  function bindParamToPreviousResponse(step: FlowStepData, p: FlowParam) {
    if (!flowSpec) return;
    const key = `${step.step_id}:${p.path}`;
    const draft = bindDraft[key] || {};
    if (!draft.source_step_id || !draft.source_path) { message.warning("请选择来源步骤和响应字段"); return; }
    const edits: any[] = flowSpec.links
      .filter((l) => l.target_step_id === step.step_id && stripBodyPrefix(l.target_path) === stripBodyPrefix(p.path))
      .map((l) => ({ op: "remove", link_id: l.link_id }));
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
    return list.filter((i) => !i.resolved && i.severity === "high");
  }, [checkReport, flowSpec]);
  const stepOptions = useMemo(() => (flowSpec?.steps || []).map((s) => ({
    label: `${s.name || fallbackStepName(s.method, s.path)} · ${s.method} ${s.path || stripHost(s.url)}`,
    value: s.step_id,
  })), [flowSpec]);
  const stepById = useMemo(() => Object.fromEntries((flowSpec?.steps || []).map((s) => [s.step_id, s])), [flowSpec]);
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
    for (const st of flowSpec?.steps || []) {
      if (st.response_json == null) continue;
      const value = st.url || st.path;
      if (!value || seen.has(value)) continue;
      seen.add(value);
      out.push({ label: `${st.name || st.path} · ${st.method} ${st.path || stripHost(st.url)}`, value });
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
    const seen = new Set<string>();
    const out: Array<{ label: string; value: string }> = [];
    for (const path of leafPaths(st?.response_json)) {
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
    return (step.selects || []).find((s) => s.path === p.path || s.param === p.key);
  }
  function enumOptionEdits(step: FlowStepData, p: FlowParam, options: string[]) {
    const edits: any[] = [
      { op: "update", step_id: step.step_id, param_path: p.path, field: "enum_options", value: options },
    ];
    if (p.type !== "enum" && p.type !== "list-enum" && options.length) {
      edits.push({ op: "update", step_id: step.step_id, param_path: p.path, field: "type", value: "enum" });
    }
    if (p.category !== "user_param") {
      edits.push({ op: "update", step_id: step.step_id, param_path: p.path, field: "category", value: "user_param" });
    }
    return edits;
  }
  function enumSourceForKind(sourceKind?: string | null) {
    if (sourceKind === "page_enum") return "dom";
    if (sourceKind === "manual_enum") return "manual";
    return null;
  }
  function upsertSelectBinding(step: FlowStepData, p: FlowParam, patch: Partial<FlowSelectBinding>, extraEdits: any[] = []) {
    const existing = selectBindingForParam(step, p);
    const nextBinding: FlowSelectBinding = {
      param: p.key,
      path: p.path,
      source_url: "",
      value_key: "",
      label_key: "",
      options: (p.enum_options || []).map(normalizeEnumOption),
      count: p.enum_options?.length || 0,
      ...existing,
      ...patch,
    };
    if (nextBinding.options) nextBinding.count = nextBinding.options.length;
    const replaced = (step.selects || []).some((s) => s.path === p.path || s.param === p.key);
    const nextSelects = replaced
      ? (step.selects || []).map((s) => (s.path === p.path || s.param === p.key ? nextBinding : s))
      : [...(step.selects || []), nextBinding];
    const edits: any[] = [{ op: "update", step_id: step.step_id, field: "selects", value: nextSelects }];
    if (p.category !== "user_param" && p.category !== "runtime_var") {
      edits.push({ op: "update", step_id: step.step_id, param_path: p.path, field: "category", value: "user_param" });
    }
    if (p.type !== "enum" && p.type !== "list-enum") {
      edits.push({ op: "update", step_id: step.step_id, param_path: p.path, field: "type", value: nextBinding.multi ? "list-enum" : "enum" });
    }
    send({ type: "flow_update", edits: [...edits, ...extraEdits] });
  }
  function normalizeEnumOption(x: any): string {
    if (x == null) return "";
    if (typeof x === "string") return x;
    if (typeof x === "object" && typeof x.label === "string") return x.label;
    return String(x);
  }
  function enumOptionsForParam(step: FlowStepData, p: FlowParam) {
    if (!OPTION_SOURCE_KINDS.includes(p.source_kind || "")) return [];
    const sel = selectBindingForParam(step, p);
    const raw = p.enum_options?.length ? p.enum_options : sel?.options || [];
    return Array.from(new Set((raw || []).map(normalizeEnumOption).filter(Boolean)));
  }
  function enumSourceLabel(sel?: FlowSelectBinding) {
    if (!sel) return "未绑定";
    if (sel.enum_source === "dom") return "页面真实枚举";
    if (sel.enum_source === "manual") return "手工枚举";
    if (sel.source_url) return "接口候选";
    if ((sel.options || []).length) return "固定枚举";
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
    if (p.source_kind === "page_enum") return "页面枚举：候选来自录制页面真实下拉快照；默认值是录制时选中的值";
    if (p.source_kind === "manual_enum") return "手工枚举：候选由人工维护；默认值是录制时选中的值";
    if (p.source_kind === "static_enum" || p.source_kind === "form_option") return "固定枚举：候选来自固定快照；默认值是录制时选中的值";
    if (p.source_kind === "constant") return "固定默认值：发布后按当前值写入，通常不暴露给用户";
    if (p.source_kind === "current_user") return "当前用户：运行期从登录态/身份信息注入，不使用录制旧值";
    if (p.source_kind === "system_time") return "系统时间：运行期自动生成，不使用录制旧值";
    if (p.source_kind === "page_context") return "页面上下文：由当前页面/应用上下文提供";
    return "来源未确认：需要选择用户输入、上游响应、固定值或系统来源";
  }
  function renderFlowWorkbench() {
    if (!flowSpec) return null;
    const totalParams = flowSpec.steps.reduce((n, s) => n + (s.params?.length || 0), 0);
    return (
      <Card
        style={{ marginTop: 16 }}
        title={
          <Space wrap>
            <Typography.Text strong>FlowSpec 工作台</Typography.Text>
            <Tag color="blue">{flowSpec.steps.length} 步</Tag>
            <Tag>{totalParams} 字段</Tag>
            <Tag color={flowSpec.risk_level === "L4" ? "error" : "orange"}>风险 {flowSpec.risk_level}</Tag>
            {reviewItems.length > 0 && <Tag color="error">{reviewItems.length} 高风险待确认</Tag>}
          </Space>
        }
        extra={
          <Space wrap>
            <Button size="small" icon={<RobotOutlined />} loading={namingBusy}
              onClick={() => { setNamingBusy(true); send({ type: "step_naming" }); }}>命名步骤</Button>
            <Button size="small" icon={<FileTextOutlined />} loading={descBusy}
              onClick={() => { setDescBusy(true); send({ type: "business_description" }); }}>
              {flowSpec.business_description ? "重新生成说明" : "生成说明"}
            </Button>
            <Button size="small" type="primary" loading={phase === "publishing"} onClick={publishRequest}>发布当前流程</Button>
          </Space>
        }
      >
        {checkReport && (
          <Alert
            type={checkReport.passed ? "success" : "warning"}
            showIcon
            style={{ marginBottom: 12 }}
            message={checkReport.passed ? "发布校验通过" : "发布校验需要处理"}
            description={
              <Space direction="vertical" size={2}>
                <Typography.Text style={{ fontSize: 12 }}>
                  Skill 参数：{checkReport.api_preview?.params?.length ? checkReport.api_preview.params.join(", ") : "无"}
                  {checkReport.dry_run ? ` · Dry-run ${checkReport.dry_run.ok ? "OK" : "需要处理"}` : ""}
                  {checkReport.dry_run?.request_count != null ? ` · ${checkReport.dry_run.request_count} 步` : ""}
                </Typography.Text>
                {(checkReport.errors || []).slice(0, 4).map((x, i) =>
                  <Typography.Text key={i} type="danger" style={{ fontSize: 12 }}>{x}</Typography.Text>)}
              </Space>
            }
          />
        )}
        <Tabs
          activeKey={activeFlowTab}
          onChange={setActiveFlowTab}
          destroyOnHidden={false}
          items={[
            { key: "review", label: `待确认 ${reviewItems.length || ""}`, children: renderReviewPanel() },
            { key: "steps", label: "步骤", children: renderStepsPanel() },
            { key: "params", label: "字段", children: renderParamsPanel() },
            { key: "links", label: "依赖", children: renderLinksPanel() },
            { key: "desc", label: "说明", children: renderDescriptionPanel() },
            { key: "json", label: "JSON", children: renderJsonPanel() },
          ]}
        />
      </Card>
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
                    ? <Button key="bind" size="small" type="primary" onClick={() => setActiveFlowTab("params")}>去字段页绑定</Button>
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
                            {typeof s.confidence === "number" && <Tag>{Math.round(s.confidence * 100)}%</Tag>}
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
              <Button type="primary" onClick={addStep}>保存</Button>
              <Button onClick={() => setAddingStep(false)}>取消</Button>
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
    return (
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Card size="small" title="新增字段">
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
            >
            {(step.params || []).length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="这个步骤还没有字段" /> : (
              <List
                size="small"
                dataSource={step.params}
                renderItem={(p) => {
                  const bindKey = `${step.step_id}:${p.path}`;
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
                  const isEnumOption = ENUM_SOURCE_KINDS.includes(p.source_kind || "");
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
                              {p.source_kind === "page_enum" && <Tag color="purple">页面枚举</Tag>}
                              {p.source_kind === "manual_enum" && <Tag color="purple">手工枚举</Tag>}
                              {(p.source_kind === "static_enum" || p.source_kind === "form_option") && enumOptions.length > 0 && <Tag color="purple">固定枚举</Tag>}
                              {needsManualConfirm && <Tag color="warning">待确认</Tag>}
                              <Typography.Text type="secondary" style={{ fontSize: 12 }}>{p.reason}</Typography.Text>
                            </Space>
                            <Typography.Text type="secondary" style={{ display: "block", marginTop: 6, fontSize: 12 }}>
                              {paramSourceText(step, p, linked)}
                            </Typography.Text>
                          </Col>
                          <Col><Button size="small" danger onClick={() => send({ type: "flow_update", edits: [{ op: "remove", step_id: step.step_id, param_path: p.path }] })}>删除</Button></Col>
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
                              onChange={(v) => updateParam(step.step_id, p, "type", v)} />
                          </FieldControl>
                          <FieldControl label="分类">
                            <NativeSelect value={p.category || "user_param"} width="100%" options={CATEGORY_OPTIONS}
                              onChange={(v) => updateParamCategory(step.step_id, p, v)} />
                          </FieldControl>
                          <FieldControl label="来源">
                            <NativeSelect value={normalizeSourceKindForUi(p.source_kind) || defaultSourceForCategory(p.category || "user_param")} width="100%" options={sourceOptionsForCategory(p.category)}
                              onChange={(v) => updateParamSourceKind(step.step_id, p, v)} />
                          </FieldControl>
                          <FieldControl label="必填">
                            <Checkbox checked={!!p.required} onChange={(e) => updateParam(step.step_id, p, "required", e.target.checked)}>必填</Checkbox>
                          </FieldControl>
                          <FieldControl label="展示">
                            <Checkbox checked={p.exposed_to_user !== false} onChange={(e) => updateParam(step.step_id, p, "exposed_to_user", e.target.checked)}>暴露给用户</Checkbox>
                          </FieldControl>
                        </div>
                        {needsManualConfirm && <Button size="small" style={{ marginTop: 8 }} onClick={() => updateParam(step.step_id, p, "need_human_confirm", false)}>已确认</Button>}
                        {(hasBindingPanel || hasRuntimePanel) && <Collapse size="small" ghost style={{ marginTop: 10 }}
                          defaultActiveKey={needsManualConfirm ? ["runtime"] : []}>
                          {hasBindingPanel && (
                            <Collapse.Panel key="binding" header={<Space><LinkOutlined />来源绑定</Space>}>
                          <div style={{ background: "#fafafa", border: "1px solid #f0f0f0", borderRadius: 6, padding: 10 }}>
                            <Space direction="vertical" size={8} style={{ width: "100%" }}>
                              <Space wrap size={6}>
                                <Typography.Text strong style={{ fontSize: 12 }}>{isApiOption ? "接口候选配置" : "枚举候选配置"}</Typography.Text>
                                <Tag color={selectBinding?.source_url ? "geekblue" : "purple"}>{enumSourceLabel(selectBinding)}</Tag>
                                {enumOptions.slice(0, 8).map((x) => <Tag key={x}>{x}</Tag>)}
                                {enumOptions.length > 8 && <Tag>+{enumOptions.length - 8}</Tag>}
                              </Space>
                              {isApiOption && (
                                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 10 }}>
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
                                      rows={2}
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
                                      value={selectBinding?.id_path || ""}
                                      options={(step.params || []).map((x) => ({ label: `${x.path} · ${x.key}`, value: x.path }))}
                                      placeholder="可选，如 yyxtid"
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
                                    value={enumOptions.join("\n")}
                                    placeholder="每行一个候选项，也支持逗号分隔"
                                    onSave={(v) => {
                                      const options = Array.from(new Set(v.split(/[\n,，]/).map((x) => x.trim()).filter(Boolean)));
                                      upsertSelectBinding(
                                        step,
                                        p,
                                        {
                                          source_url: "",
                                          value_key: "",
                                          label_key: "",
                                          options,
                                          count: options.length,
                                          option_map: null,
                                          enum_source: enumSourceForKind(p.source_kind),
                                          enum_confirmed: true,
                                        },
                                        enumOptionEdits(step, p, options),
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
                    <Button key="rm" size="small" danger onClick={() => send({ type: "flow_update", edits: [{ op: "remove", link_id: link.link_id }] })}>删除</Button>,
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
        <FieldControl label="流程标题">
          <Input value={titleDraft}
            onChange={(e) => setTitleDraft(e.target.value)}
            onBlur={(e) => {
              if (e.target.value.trim() !== (flowSpec?.title || "")) {
                setFlowSpec((cur) => cur ? { ...cur, title: e.target.value.trim() } : cur);
                updateFlowField("title", e.target.value.trim());
              }
            }} />
        </FieldControl>
        <Input.TextArea rows={12} value={descDraft}
          onChange={(e) => setDescDraft(e.target.value)}
          onBlur={(e) => {
            if (e.target.value !== (flowSpec?.business_description || "")) {
              setFlowSpec((cur) => cur ? { ...cur, business_description: e.target.value } : cur);
              updateFlowField("business_description", e.target.value);
            }
          }}
          placeholder="生成或手写业务说明" />
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
          <Space style={{ marginBottom: 8 }} wrap>
            <Tag color="processing">{phase === "publishing" ? "发布中" : "录制中"}</Tag>
            <Button size="small" disabled={phase === "publishing"} onClick={resetFromHere}>从这里开始录</Button>
            <Button size="small" onClick={stopAll} disabled={phase === "publishing"}>结束录制</Button>
          </Space>
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

          {fields.length > 0 && flowSpec && (
            <Card type="inner" size="small" style={{ marginTop: 12, borderColor: "#52c41a" }}>
              <Row gutter={[12, 8]} align="middle">
                <Col flex="auto">
                  <Space wrap>
                    <CheckCircleOutlined />
                    <Typography.Text strong>已抓到提交请求</Typography.Text>
                    {reqMeta && <Typography.Text code>{reqMeta.method} {stripHost(reqMeta.url)}</Typography.Text>}
                    <Tag color="blue">已生成 FlowSpec</Tag>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      字段、依赖和确认项请在下方工作台编辑。
                    </Typography.Text>
                  </Space>
                </Col>
                <Col>
                  <Space wrap>
                    <Button loading={phase === "publishing"} onClick={finalize}>重新抓取</Button>
                  </Space>
                </Col>
              </Row>
            </Card>
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

          {!fields.length && (
            <Card size="small" style={{ marginTop: 12 }}>
              <Space size="large" wrap>
                <Form.Item label="动作名" required style={{ marginBottom: 0 }}>
                  <Input value={action} onChange={(e) => setAction(e.target.value)} style={{ width: 200 }} />
                </Form.Item>
                <Form.Item label="标题" style={{ marginBottom: 0 }}>
                  <Input value={title} onChange={(e) => setTitle(e.target.value)} style={{ width: 180 }} />
                </Form.Item>
                <Button type="primary" loading={phase === "publishing"} disabled={!steps.length && !reqs.length} onClick={finalize}>
                  停止并分析请求
                </Button>
              </Space>
            </Card>
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
