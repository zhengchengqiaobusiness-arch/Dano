import {
  Alert,
  Button,
  Card,
  Checkbox,
  Collapse,
  Drawer,
  Empty,
  Input,
  List,
  Modal,
  Radio,
  Select,
  Space,
  Spin,
  Steps,
  Switch,
  Table,
  Tabs,
  Tag,
  Timeline,
  Tooltip,
  Typography,
  message,
} from "antd";
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  CodeOutlined,
  CopyOutlined,
  DeleteOutlined,
  ExclamationCircleOutlined,
  LoadingOutlined,
  MessageOutlined,
  PlayCircleOutlined,
  RobotOutlined,
  StopOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type {
  FormEvent,
  KeyboardEvent,
  PointerEvent,
  WheelEvent,
} from "react";
import {
  deleteRecordingResult,
  exportRecordingSkill,
  getRecordingResult,
  listRecordingResults,
  rememberExportDir,
  rememberedExportDir,
} from "../api/recording";
import type {
  RecordingResultDetail,
  RecordingResultSummary,
  SkillExportOutcome,
} from "../api/recording";

const { Text, Title } = Typography;

type WorkflowStatus =
  | "idle"
  | "recording"
  | "processing"
  | "waiting_operator"
  | "editable"
  | "published"
  | "cancelled"
  | "failed";

interface WorkflowIssue {
  issue_id: string;
  code: string;
  message: string;
  severity?: string;
  resolver?: string;
  target?: Record<string, string>;
}

interface WorkflowQuestion {
  question_id: string;
  issue_id: string;
  text: string;
  options?: string[];
  context_ref?: string;
}

interface ThoughtChunk {
  kind: "text" | "thinking" | "tool" | string;
  text: string;
  tool?: string;
  phase?: string;
  args?: string;
  result?: string;
  ok?: boolean;
}

interface WorkflowActivity {
  sequence: number;
  step: string;
  round?: number;
  status: "pending" | "running" | "resolved" | "blocked" | "waiting_operator" | string;
  label: string;
  issue_id?: string;
  code?: string;
  target?: Record<string, string>;
}

interface WorkflowSnapshot {
  run_id: string;
  action: string;
  title?: string;
  revision: number;
  status: WorkflowStatus;
  progress: {
    step: string;
    label: string;
    round?: number;
    request_count?: number;
  };
  capture_frozen?: boolean;
  draft?: FlowSpec | null;
  draft_fingerprint?: string;
  check_report?: Record<string, unknown>;
  issues?: WorkflowIssue[];
  insights?: Array<Record<string, unknown>>;
  activity?: WorkflowActivity[];
  question?: WorkflowQuestion | null;
  release?: Record<string, unknown> | null;
  error?: string;
  stage_seven_attempt_id?: string;
  machine_verification_status?: string;
}

interface FlowParam {
  field_id?: string;
  path: string;
  key: string;
  label?: string;
  value?: unknown;
  default_value?: unknown;
  type?: string;
  source_kind?: string;
  source?: Record<string, unknown>;
  exposed_to_user?: boolean;
  required?: boolean;
  reason?: string;
  enum_options?: unknown[];
}

interface FlowStep {
  step_id: string;
  name?: string;
  method?: string;
  path?: string;
  url?: string;
  params?: FlowParam[];
}

interface FlowCapability {
  capability_id?: string;
  name?: string;
  title?: string;
  intent?: string;
  kind?: string;
  confidence?: number;
  confirmed?: boolean;
  request_refs?: Array<{
    step_id?: string;
    usage?: string;
    [key: string]: unknown;
  }>;
  step_ids?: string[];
  nodes?: Array<Record<string, unknown>>;
  dependencies?: Array<Record<string, unknown>>;
  input_schema?: Record<string, unknown>;
  output_schema?: Record<string, unknown>;
  requires_human_confirm?: boolean;
}

interface FlowLink {
  source_step_id?: string;
  source_path?: string;
  target_step_id?: string;
  target_path?: string;
  confirmed?: boolean;
  reason?: string;
  source?: Record<string, unknown>;
  target?: Record<string, unknown>;
  [key: string]: unknown;
}

interface FlowSpec {
  flow_id?: string;
  title?: string;
  steps?: FlowStep[];
  links?: FlowLink[];
  capabilities?: FlowCapability[];
  request_facts?: {
    requests?: Array<Record<string, unknown>>;
  };
  meta?: Record<string, unknown>;
}

interface PageRecorderProps {
  tenant: string;
  subsystem: string;
  baseUrl: string;
  storageState: string;
}

interface FrameMeta {
  width: number;
  height: number;
}

interface DraftEdit {
  op: string;
  actor: "user";
  [key: string]: unknown;
}

const TYPE_LABELS: Record<string, string> = {
  string: "文本",
  number: "数字",
  boolean: "是/否",
  date: "日期",
  datetime: "日期时间",
  enum: "单选",
  "list-enum": "多选",
  object: "对象",
  array: "列表",
};

const TYPE_OPTIONS = Object.entries(TYPE_LABELS).map(([value, label]) => ({ value, label }));

const SOURCE_OPTIONS = [
  ["caller_input", "调用方输入"],
  ["user_input", "调用方输入"],
  ["selected_record_identity", "客户选择记录"],
  ["api_option", "实时接口取值"],
  ["page_enum", "页面枚举"],
  ["static_enum", "固定选项"],
  ["form_option", "页面选项"],
  ["constant", "固定值"],
  ["page_default", "页面预填，可修改"],
  ["page_rule", "前端页面规则"],
  ["selected_option_field", "所选记录自动带入"],
  ["session", "登录会话"],
  ["current_user", "当前登录用户"],
  ["context", "调用上下文"],
  ["page_context", "页面上下文"],
  ["response_binding", "上游响应"],
  ["previous_response", "上游接口响应"],
  ["computed", "自动计算"],
  ["generated", "运行时生成"],
  ["unknown", "未知"],
].map(([value, label]) => ({ value, label }));

function sourceKindLabel(value?: string) {
  const labels: Record<string, string> = {
    caller_input: "调用方输入",
    user_input: "调用方输入",
    selected_record_identity: "客户选择记录",
    record_identity: "客户选择记录",
    api_option: "实时接口取值",
    page_enum: "页面枚举",
    static_enum: "固定选项",
    manual_enum: "人工确认选项",
    form_option: "页面选项",
    constant: "固定值",
    page_default: "页面预填，可修改",
    page_rule: "前端页面规则",
    session: "登录会话",
    current_user: "当前登录用户",
    storage: "登录存储",
    cookie: "登录 Cookie",
    context: "调用上下文",
    page_context: "页面上下文",
    response_binding: "上游接口响应",
    previous_response: "上游接口响应",
    dynamic_structure: "上游接口动态结构",
    selected_option_field: "所选记录自动带入",
    computed: "自动计算",
    generated: "运行时生成",
    system_generated: "运行时生成",
    system_time: "系统时间",
    unknown: "未知",
  };
  return labels[value || ""] || value || "未知";
}

function paramSourceLabel(param: FlowParam) {
  const source = asRecord(param.source);
  const sourceKind = safeString(source.kind);
  if (sourceKind === "selected_record_identity" || sourceKind === "record_identity") {
    return "客户选择记录";
  }
  if (
    paramIsCallerInput(param)
    && (
      ["response_binding", "previous_response", "dynamic_structure"].includes(param.source_kind || "")
      || sourceKind === "previous_response"
    )
  ) {
    return "上游默认值，可修改";
  }
  if (param.source_kind === "page_default") {
    return paramIsCallerInput(param) ? "页面预填，可修改" : "页面预填";
  }
  if (param.source_kind === "selected_option_field") {
    return paramIsCallerInput(param) ? "所选记录带入，可修改" : "所选记录自动带入";
  }
  if (param.source_kind === "computed") {
    return paramIsCallerInput(param) ? "自动计算，可修改" : "自动计算";
  }
  if (param.source_kind === "unknown") {
    return "未知";
  }
  if (param.source_kind === "constant") {
    const kind = safeString(source.kind);
    if (kind === "recorded_control_default") return "页面只读默认值";
    if (kind === "empty_field") return "未知";
    if (["option_query_filter", "query_constant", "recorded_command_state"].includes(kind)) {
      return "未知";
    }
  }
  return sourceKindLabel(param.source_kind);
}

function constantValueCaption(param: FlowParam) {
  const kind = safeString(asRecord(param.source).kind);
  if (kind === "recorded_control_default") return "录制页面默认值";
  if (kind === "empty_field") return "接口空值";
  return "固定值";
}

const CALLER_SOURCE_KINDS = new Set([
  "caller_input", "user_input", "api_option", "page_enum", "static_enum", "manual_enum", "form_option",
  "page_default",
]);

function paramIsCallerInput(param: FlowParam) {
  if (typeof param.exposed_to_user === "boolean") return param.exposed_to_user;
  return CALLER_SOURCE_KINDS.has(param.source_kind || "caller_input");
}

function looksPaginationField(field: { key?: string; path?: string } | null | undefined) {
  const raw = `${field?.key || ""}.${field?.path || ""}`.toLowerCase().replace(/[^a-z0-9]+/g, "");
  return /(?:pageno|pagenum|pagesize|pageindex|currentpage|limit|offset)$/.test(raw);
}

function paramSourceTagColor(param: FlowParam) {
  const kind = param.source_kind || "";
  if (["api_option", "page_enum", "static_enum", "manual_enum", "form_option"].includes(kind)) {
    return "purple";
  }
  if (["previous_response", "response_binding", "dynamic_structure"].includes(kind)) {
    return "cyan";
  }
  if (kind === "page_default") return "geekblue";
  if (["selected_record_identity", "record_identity", "selected_option_field"].includes(kind)) {
    return "blue";
  }
  if (["session", "current_user", "storage", "cookie", "context", "page_context"].includes(kind)) {
    return "gold";
  }
  if (kind === "constant") return "default";
  if (kind === "unknown" || paramSourceLabel(param) === "未知") return "#cf1322";
  if (paramIsCallerInput(param)) return "blue";
  return "default";
}

function paramTypeLabel(param: FlowParam) {
  if (param.source_kind === "api_option") return param.type === "list-enum" ? "实时接口多选" : "实时接口选项";
  return TYPE_LABELS[param.type || "string"] || param.type || TYPE_LABELS.string;
}

function apiOptionSourceSummary(param: FlowParam) {
  if (param.source_kind !== "api_option") return "";
  const source = asRecord(param.source);
  const sourceUrl = safeString(source.source_url);
  if (!sourceUrl) return "实时调用取值接口，调用方选择显示值，Skill 自动提交接口值";
  const method = safeString(source.source_method) || "GET";
  const labelKey = safeString(source.label_key) || "显示值";
  const valueKey = safeString(source.value_key) || "接口值";
  return `取值接口：${method} ${sourceUrl}；调用方选择 ${labelKey}，Skill 自动提交 ${valueKey}`;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function stepDisplayPath(step: FlowStep) {
  return String(step.path || step.url || "").split("?")[0];
}

function paramDisplayName(param: FlowParam) {
  return String(param.label || param.key || param.path || "未命名字段");
}

function fmtHistoryTime(value?: string) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

const RESULT_STATUS_BOX_STYLE = { width: "100%", height: 420, boxSizing: "border-box" as const };
const REPLAY_SKIP_HINTS = ["跳过回放取证", "仍无法登录", "录制会话登录态已过期", "请刷新凭证后重新点"];

const DEFAULT_RECORDING_GOAL_TEMPLATE = "请将我接下来在页面中实际完成的每项业务操作分别生成一个可调用能力。";

const STATUS_LABELS: Record<WorkflowStatus, string> = {
  idle: "等待开始",
  recording: "录制中",
  processing: "分析中",
  waiting_operator: "等待确认",
  editable: "能力草稿待处理",
  published: "能力已验证，Skill 未产出",
  cancelled: "分析已终止",
  failed: "处理失败",
};

const SKILL_LIFECYCLE_LABELS: Record<string, { label: string; color: string }> = {
  stage_six_done: { label: "阶段1—6已完成", color: "default" },
  verifying: { label: "阶段7验证中", color: "processing" },
  verified_not_exported: { label: "能力已验证，Skill 未产出", color: "blue" },
  generating: { label: "Skill 生成中", color: "processing" },
  exported: { label: "Skill 已导出", color: "success" },
  export_failed: { label: "Skill 导出失败", color: "error" },
  needs_reexport: { label: "能力已修改，Skill 需要重新产出", color: "warning" },
};

const ACTIVITY_STATUS: Record<string, { label: string; color?: string }> = {
  pending: { label: "发现了" },
  running: { label: "思考中", color: "processing" },
  resolved: { label: "已解决", color: "success" },
  blocked: { label: "未解决", color: "error" },
  waiting_operator: { label: "需要确认", color: "warning" },
};

function capabilityTitleOf(item: WorkflowActivity) {
  const titled = String(item.target?.capability_title || "").trim();
  if (titled) return titled;
  const named = item.label.match(/能力[「"]([^」"]+)[」"]/);
  if (named) return named[1];
  if (item.label.includes("整体流程")) return "整体流程";
  return "";
}

function activityDisplay(item: { status: string; label: string }) {
  if (REPLAY_SKIP_HINTS.some((hint) => item.label.includes(hint))) {
    return { label: "已跳过回放取证", color: "warning" as const };
  }
  if (item.label.startsWith("发现了")) return { label: "发现了" };
  if (item.label.startsWith("我觉得") || item.label.startsWith("准备")) {
    return { label: "准备处理", color: "processing" as const };
  }
  if (item.label.startsWith("本轮结果")) return { label: "本轮结果", color: "processing" as const };
  if (item.label.startsWith("已经处理好")) return { label: "已解决", color: "success" as const };
  return ACTIVITY_STATUS[item.status] || { label: item.status || "处理" };
}

function preflightOf(snapshot: WorkflowSnapshot | null | undefined) {
  const draft = snapshot?.draft as Record<string, unknown> | null | undefined;
  const meta = (draft?.meta || {}) as Record<string, unknown>;
  const run = (meta.verification_run || {}) as Record<string, unknown>;
  return (run.preflight || {}) as Record<string, unknown>;
}

function looksReplaySkipped(snapshot: WorkflowSnapshot | null | undefined) {
  if (!snapshot) return false;
  const preflight = preflightOf(snapshot);
  if (preflight.skip_replay === true) return true;
  const text = `${snapshot.progress.label || ""} ${(snapshot.issues || []).map((issue) => issue.message || "").join(" ")}`;
  return REPLAY_SKIP_HINTS.some((hint) => text.includes(hint));
}

function isAuthNoiseActivity(item: WorkflowActivity) {
  if (item.code === "replay_auth") return true;
  return REPLAY_SKIP_HINTS.some((hint) => item.label.includes(hint));
}

function dedupeAnalysisActivities(activities: WorkflowActivity[]) {
  const seen = new Set<string>();
  const next: WorkflowActivity[] = [];
  for (const item of activities) {
    const key = `${item.status}\0${item.label}`;
    if (seen.has(key)) continue;
    seen.add(key);
    next.push(item);
  }
  return next;
}

function analysisStatusView(status: WorkflowStatus, cancelling: boolean, _snapshot: WorkflowSnapshot | null) {
  if (cancelling && status !== "cancelled") return { color: "warning" as const, label: "正在终止" };
  if (status === "cancelled") return { color: "default" as const, label: "已终止" };
  if (status === "published") return { color: "success" as const, label: "能力已验证，Skill 未产出" };
  if (status === "failed") return { color: "error" as const, label: "失败" };
  if (status === "waiting_operator") return { color: "warning" as const, label: "需要确认" };
  if (status === "processing") return { color: "processing" as const, label: "分析中" };
  if (status === "editable") return { color: "default" as const, label: "能力草稿待处理" };
  return { color: "default" as const, label: STATUS_LABELS[status] };
}

function isStageSevenProgress(progress?: { step?: string; round?: number } | null) {
  const step = String(progress?.step || "");
  return (Number(progress?.round || 0) > 0) || ["verifying", "resolving"].includes(step);
}

function pageStage(status: WorkflowStatus, resumeOnly = false, _verificationLive = false) {
  if (resumeOnly) return 2;
  if (status === "idle") return 0;
  if (["recording", "processing", "waiting_operator"].includes(status)) return 1;
  return 2;
}

function recorderWebSocketUrl() {
  const configured = String(import.meta.env.VITE_DANO_RECORDING_WS_URL || "").trim();
  if (configured) return configured;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  if (
    import.meta.env.DEV
    && location.port === "5173"
    && ["localhost", "127.0.0.1", "::1"].includes(location.hostname)
  ) {
    return "ws://127.0.0.1:8077/onboarding/page/record";
  }
  return `${proto}://${location.host}/onboarding/page/record`;
}

function newActionName() {
  const value = typeof crypto?.randomUUID === "function"
    ? crypto.randomUUID().replaceAll("-", "")
    : `${Date.now().toString(16)}${Math.random().toString(16).slice(2).padEnd(20, "0")}`.slice(0, 32);
  return `action_${value.toLowerCase()}`;
}

function readSetupDraft() {
  try {
    const parsed = JSON.parse(sessionStorage.getItem("dano.recording.setup") || "{}");
    return {
      startUrl: typeof parsed.startUrl === "string" ? parsed.startUrl : "",
      goalText: typeof parsed.goalText === "string" && parsed.goalText.trim()
        ? parsed.goalText
        : DEFAULT_RECORDING_GOAL_TEMPLATE,
      title: typeof parsed.title === "string" ? parsed.title : "",
      machineVerification: parsed.machineVerification === true,
    };
  } catch {
    return { startUrl: "", goalText: DEFAULT_RECORDING_GOAL_TEMPLATE, title: "", machineVerification: false };
  }
}

function parseStorageState(value: string): Record<string, unknown> | undefined {
  if (!value.trim()) return undefined;
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" ? parsed : undefined;
  } catch {
    return undefined;
  }
}

function safeString(value: unknown) {
  if (value === null || value === undefined) return "";
  return typeof value === "string" ? value : JSON.stringify(value);
}

function schemaFieldNames(schema?: Record<string, unknown> | null) {
  const properties = schema?.properties;
  if (!properties || typeof properties !== "object") return [];
  return Object.keys(properties as Record<string, unknown>);
}

function capabilityIsWrite(capability: FlowCapability) {
  const kind = String(capability.kind || "").toLowerCase();
  return Boolean(capability.requires_human_confirm) || [
    "submit", "delete", "withdraw", "approve", "reject", "submit_batch", "create", "update", "edit",
  ].includes(kind);
}

function historyLifecycleView(item: RecordingResultSummary) {
  const key = String(item.skill_lifecycle || "");
  if (SKILL_LIFECYCLE_LABELS[key]) return SKILL_LIFECYCLE_LABELS[key];
  if (item.skill_needs_reexport) return SKILL_LIFECYCLE_LABELS.needs_reexport;
  if (item.published) return SKILL_LIFECYCLE_LABELS.exported;
  if (item.machine_verification_status === "verified") return SKILL_LIFECYCLE_LABELS.verified_not_exported;
  if (["running", "waiting_operator"].includes(String(item.machine_verification_status || ""))) {
    return SKILL_LIFECYCLE_LABELS.verifying;
  }
  return SKILL_LIFECYCLE_LABELS.stage_six_done;
}

function releaseUsedMachineVerification(release?: Record<string, unknown> | null) {
  const candidate = release?.release;
  if (!candidate || typeof candidate !== "object") return false;
  const verification = (candidate as Record<string, unknown>).machine_verification;
  return Boolean(
    verification
    && typeof verification === "object"
    && (verification as Record<string, unknown>).enabled === true,
  );
}

export default function PageRecorder({
  tenant,
  subsystem,
  baseUrl,
  storageState,
}: PageRecorderProps) {
  const setup = useMemo(readSetupDraft, []);
  const [startUrl, setStartUrl] = useState(setup.startUrl);
  const [goalText, setGoalText] = useState(setup.goalText);
  const [machineVerification, setMachineVerification] = useState(setup.machineVerification);
  const [title, setTitle] = useState(setup.title);
  const [snapshot, setSnapshot] = useState<WorkflowSnapshot | null>(null);
  const [connected, setConnected] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [answer, setAnswer] = useState("");
  const [hasFrame, setHasFrame] = useState(false);
  const [finishRequested, setFinishRequested] = useState(false);
  const [frameMeta, setFrameMeta] = useState<FrameMeta>({ width: 1280, height: 800 });
  const [pendingEdits, setPendingEdits] = useState<DraftEdit[]>([]);
  const [localValues, setLocalValues] = useState<Record<string, unknown>>({});
  const [localCapabilityStepIds, setLocalCapabilityStepIds] = useState<Record<string, string[]>>({});
  const [editingResult, setEditingResult] = useState(false);
  const [viewStage, setViewStage] = useState(0);
  const [keepRecording, setKeepRecording] = useState(false);
  const [keepResult, setKeepResult] = useState(false);
  const [resumeOnly, setResumeOnly] = useState(false);
  const [thoughts, setThoughts] = useState<ThoughtChunk[]>([]);
  const [expandedTools, setExpandedTools] = useState<Record<number, boolean>>({});
  const [cancelling, setCancelling] = useState(false);
  const [history, setHistory] = useState<RecordingResultSummary[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [activeResultId, setActiveResultId] = useState("");
  const [deletingId, setDeletingId] = useState("");
  const [openingId, setOpeningId] = useState("");
  const [analysisRequested, setAnalysisRequested] = useState(false);
  const reachedStageRef = useRef(0);
  const verificationLogRef = useRef<HTMLDivElement | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const snapshotRef = useRef<WorkflowSnapshot | null>(null);
  const actionRef = useRef("");
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const keyboardRef = useRef<HTMLInputElement | null>(null);
  const latestFrameRef = useRef<{ seq: number; src: string; meta: FrameMeta } | null>(null);
  const renderedFrameRef = useRef(0);
  const decodingFrameRef = useRef(false);
  const frameGenerationRef = useRef(0);
  const pointerRef = useRef<{ pointerId: number; button: string } | null>(null);
  const pointerMoveRef = useRef<Record<string, unknown> | null>(null);
  const pointerTimerRef = useRef<number | null>(null);
  const wheelRef = useRef<Record<string, number> | null>(null);
  const wheelTimerRef = useRef<number | null>(null);
  const composingRef = useRef(false);
  const lastBackspaceRef = useRef(0);
  const pendingEditsRef = useRef<DraftEdit[]>([]);
  const patchInFlightRef = useRef<{ revision: number; edits: DraftEdit[] } | null>(null);
  const republishRequestedRef = useRef(false);
  const reconnectTimerRef = useRef<number | null>(null);
  const reconnectAttemptRef = useRef(0);
  const closingRef = useRef(false);
  const finishRequestedRef = useRef(false);
  const resumeOnlyRef = useRef(false);
  const cancellingRef = useRef(false);
  const machineVerificationRef = useRef(setup.machineVerification);
  const socketInitRef = useRef<Record<string, unknown> | null>(null);
  const stageSevenAttemptIdRef = useRef("");
  const activeResultIdRef = useRef("");
  const deletingIdRef = useRef("");
  const acceptNextSnapshotRef = useRef(false);
  const [stageSevenOpen, setStageSevenOpen] = useState(false);
  const [skillExportOpen, setSkillExportOpen] = useState(false);
  const [skillExporting, setSkillExporting] = useState(false);
  const [skillExportProgress, setSkillExportProgress] = useState("");
  const [skillExportOutcome, setSkillExportOutcome] = useState<SkillExportOutcome | null>(null);
  const [skillClarifications, setSkillClarifications] = useState<string[]>([]);
  const [skillExportErrors, setSkillExportErrors] = useState<string[]>([]);
  const [skillTitle, setSkillTitle] = useState("");
  const [skillDescription, setSkillDescription] = useState("");
  const [skillPlanningMode, setSkillPlanningMode] = useState<"dynamic" | "fixed">("dynamic");
  const [skillExamples, setSkillExamples] = useState("");
  const [skillSuccess, setSkillSuccess] = useState("");
  const [skillForbidden, setSkillForbidden] = useState("");
  const [skillOutDir, setSkillOutDir] = useState(rememberedExportDir);
  const [resultMeta, setResultMeta] = useState<RecordingResultDetail | null>(null);

  const status = snapshot?.status || "idle";
  const processing = status === "processing" || status === "waiting_operator";
  const draft = snapshot?.draft || null;
  const canRetryPublish = Boolean(draft) && ["editable", "failed", "cancelled"].includes(status);
  const capabilities = draft?.capabilities || [];
  const steps = draft?.steps || [];
  const capturedRequests = draft?.request_facts?.requests || [];
  const runBusy = (connecting || processing) && !cancelling && status !== "cancelled";
  const analysisSessionLive = analysisRequested
    || status === "waiting_operator"
    || isStageSevenProgress(snapshot?.progress)
    || stageSevenOpen;
  const analysisMode = analysisSessionLive;
  const reachedStage = pageStage(status, resumeOnly, analysisSessionLive);
  const stageSevenStatus = String(
    snapshot?.machine_verification_status
    || resultMeta?.machine_verification_status
    || resultMeta?.stage_seven?.status
    || "",
  );
  const stageSevenFingerprint = String(
    resultMeta?.stage_seven_fingerprint
    || resultMeta?.stage_seven?.working_fingerprint
    || "",
  );
  const currentDraftFingerprint = String(
    snapshot?.draft_fingerprint
    || resultMeta?.draft_fingerprint
    || "",
  );
  const stageSevenVerified = stageSevenStatus === "verified";
  const fingerprintMatches = !stageSevenFingerprint || !currentDraftFingerprint
    || stageSevenFingerprint === currentDraftFingerprint;
  const canProduceSkill = Boolean(
    (activeResultId || history.find((row) => row.action === (snapshot?.action || ""))?.id)
    && capabilities.length
    && stageSevenVerified
    && fingerprintMatches
    && !pendingEdits.length
    && !patchInFlightRef.current
    && !processing
    && !connecting
    && !cancelling
    && !skillExporting,
  );
  const verificationButtonLabel = (
    stageSevenStatus === "stale"
    || stageSevenStatus === "running"
    || stageSevenStatus === "waiting_operator"
    || (stageSevenVerified && !fingerprintMatches)
    || Boolean(resultMeta?.skill_needs_reexport && !stageSevenVerified)
  ) ? "继续验证" : "开始机器验证";

  useEffect(() => {
    sessionStorage.setItem("dano.recording.setup", JSON.stringify({
      startUrl,
      goalText,
      title,
      machineVerification,
    }));
  }, [startUrl, goalText, title, machineVerification]);

  useEffect(() => {
    if (resumeOnly) {
      if (reachedStage >= 2) setKeepResult(true);
    } else {
      if (reachedStage >= 1) setKeepRecording(true);
      if (reachedStage >= 2) setKeepResult(true);
    }
    if (reachedStage > reachedStageRef.current) {
      setViewStage(reachedStage);
      if (reachedStage >= 2 && !resumeOnly) setAssistantOpen(true);
    }
    reachedStageRef.current = reachedStage;
  }, [reachedStage, resumeOnly]);

  function appendThought(chunk: ThoughtChunk) {
    setThoughts((current) => {
      const last = current[current.length - 1];
      if (last && last.kind === chunk.kind && (chunk.kind === "text" || chunk.kind === "thinking")) {
        const text = String(chunk.text || "");
        if (!text) return current;
        return [...current.slice(0, -1), { ...last, text: last.text + text }];
      }
      if (chunk.kind === "tool" && last?.kind === "tool" && last.tool && last.tool === chunk.tool) {
        return [...current.slice(0, -1), {
          ...last,
          ...chunk,
          text: chunk.text || last.text,
          args: chunk.args || last.args,
          result: chunk.result || last.result,
          ok: chunk.phase === "end" ? chunk.ok : last.ok,
        }];
      }
      if (chunk.kind === "tool" || String(chunk.text || "")) {
        return [...current, chunk];
      }
      return current;
    });
  }

  function upsertHistory(row: RecordingResultSummary) {
    setHistory((current) => {
      const next = current.filter((item) => item.id !== row.id && item.action !== row.action);
      return [row, ...next];
    });
  }

  useEffect(() => {
    if (!tenant) {
      setHistory([]);
      return;
    }
    let cancelled = false;
    setHistoryLoading(true);
    listRecordingResults(subsystem).then((rows) => {
      if (!cancelled) setHistory(rows);
    }).catch(() => {
      if (!cancelled) setHistory([]);
    }).finally(() => {
      if (!cancelled) setHistoryLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [tenant, subsystem]);

  useEffect(() => {
    if (!["editable", "published"].includes(status) || processing) return;
    const resultId = activeResultId || history.find((row) => row.action === (snapshot?.action || ""))?.id;
    if (!resultId) return;
    void refreshResultMeta(resultId);
  }, [status, snapshot?.machine_verification_status, snapshot?.draft_fingerprint]);

  useEffect(() => {
    if (analysisRequested || isStageSevenProgress(snapshot?.progress) || status === "waiting_operator") {
      setStageSevenOpen(true);
    }
  }, [analysisRequested, snapshot?.progress, status]);

  useEffect(() => {
    if (["published", "editable", "failed", "cancelled"].includes(status)) {
      cancellingRef.current = false;
      setCancelling(false);
      setConnecting(false);
      if (status === "cancelled") {
        setAnalysisRequested(false);
        closeRecordingSocket();
      }
    }
  }, [status]);

  useEffect(() => {
    if (!connected) return undefined;
    if (!["recording", "processing", "waiting_operator"].includes(status)) return undefined;
    const timer = window.setInterval(() => {
      const socket = wsRef.current;
      if (!socket || socket.readyState !== WebSocket.OPEN) return;
      socket.send(JSON.stringify({ type: "ping" }));
    }, 15000);
    return () => window.clearInterval(timer);
  }, [connected, status]);

  useEffect(() => {
    const box = verificationLogRef.current;
    if (!box) return;
    box.scrollTop = box.scrollHeight;
  }, [snapshot?.activity, snapshot?.progress.label, snapshot?.insights, snapshot?.progress.round, thoughts]);

  useEffect(() => {
    closingRef.current = false;
    return () => {
      closingRef.current = true;
      frameGenerationRef.current += 1;
      if (pointerTimerRef.current !== null) window.clearTimeout(pointerTimerRef.current);
      if (wheelTimerRef.current !== null) window.clearTimeout(wheelTimerRef.current);
      if (reconnectTimerRef.current !== null) window.clearTimeout(reconnectTimerRef.current);
      const socket = wsRef.current;
      wsRef.current = null;
      if (socket && socket.readyState < WebSocket.CLOSING) socket.close(1000, "page closed");
    };
  }, []);

  function stopReconnect() {
    if (reconnectTimerRef.current !== null) {
      window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
  }

  function closeRecordingSocket() {
    stopReconnect();
    const socket = wsRef.current;
    wsRef.current = null;
    setConnected(false);
    setConnecting(false);
    if (socket && socket.readyState < WebSocket.CLOSING) socket.close(1000, "client stop");
  }

  function enterCapabilityResults() {
    setKeepResult(true);
    setViewStage(2);
    setAssistantOpen(true);
  }

  function canAutoReconnectRecording() {
    const status = snapshotRef.current?.status;
    const initType = socketInitRef.current?.type;
    if (["published", "editable", "failed", "cancelled"].includes(status || "")) {
      return false;
    }
    if (initType === "start") {
      return ["recording", "processing", "waiting_operator"].includes(status || "recording");
    }
    if (initType === "resume_verification") {
      return ["processing", "waiting_operator"].includes(status || "");
    }
    return false;
  }

  function send(payload: Record<string, unknown>) {
    const socket = wsRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      message.error("录制连接不可用");
      return false;
    }
    socket.send(JSON.stringify(payload));
    return true;
  }

  function editIdentity(edit: DraftEdit) {
    return [
      edit.op,
      edit.capability_id || edit.capability_name || edit.capability_index || "",
      edit.step_id || "",
      edit.param_path || "",
      edit.field || "",
    ].join("\u0000");
  }

  function replacePendingEdits(update: (current: DraftEdit[]) => DraftEdit[]) {
    const next = update(pendingEditsRef.current);
    pendingEditsRef.current = next;
    setPendingEdits(next);
  }

  function mergePendingEdits(older: DraftEdit[], newer: DraftEdit[]) {
    const merged = new Map<string, DraftEdit>();
    [...older, ...newer].forEach((edit) => merged.set(editIdentity(edit), edit));
    return Array.from(merged.values());
  }

  function localValueKeyForEdit(edit: DraftEdit) {
    if (edit.op === "update" && edit.step_id && edit.param_path && edit.field) {
      return editKey(String(edit.step_id), String(edit.param_path), String(edit.field));
    }
    if (edit.op === "update_capability" && edit.field) {
      const reference = String(edit.capability_id || edit.capability_name || "");
      return `capability\u0000${reference}\u0000${String(edit.field)}`;
    }
    return "";
  }

  function clearAcknowledgedLocalValues(edits: DraftEdit[]) {
    setLocalValues((current) => {
      const next = { ...current };
      edits.forEach((edit) => {
        const key = localValueKeyForEdit(edit);
        if (key && Object.is(next[key], edit.value)) delete next[key];
      });
      return next;
    });
    if (!pendingEditsRef.current.some((edit) => [
      "add_capability_step", "remove_capability_step", "reorder_capability_steps",
    ].includes(edit.op))) {
      setLocalCapabilityStepIds({});
    }
  }

  function flushDraftEdits() {
    const current = snapshotRef.current;
    if (!current?.draft || patchInFlightRef.current) return false;
    if (!["editable", "published", "failed", "cancelled"].includes(current.status)) return false;
    const edits = pendingEditsRef.current;
    if (!edits.length) return false;
    pendingEditsRef.current = [];
    setPendingEdits([]);
    patchInFlightRef.current = { revision: current.revision, edits };
    const sent = send({
      type: "patch_draft",
      edits,
      expected_revision: current.revision,
      expected_fingerprint: current.draft_fingerprint,
    });
    if (!sent) {
      patchInFlightRef.current = null;
      replacePendingEdits((queued) => mergePendingEdits(edits, queued));
    }
    return sent;
  }

  function scheduleFrameDecode() {
    if (decodingFrameRef.current) return;
    const frame = latestFrameRef.current;
    if (!frame || frame.seq <= renderedFrameRef.current) return;
    decodingFrameRef.current = true;
    const generation = frameGenerationRef.current;
    const image = new Image();
    image.src = frame.src;
    const decoded = typeof image.decode === "function"
      ? image.decode()
      : new Promise<void>((resolve, reject) => {
        image.onload = () => resolve();
        image.onerror = () => reject(new Error("frame decode failed"));
      });
    decoded.then(() => {
      if (generation !== frameGenerationRef.current || frame.seq <= renderedFrameRef.current) return;
      const canvas = canvasRef.current;
      const context = canvas?.getContext("2d", { alpha: false });
      if (!canvas || !context) return;
      const width = Math.max(1, frame.meta.width || image.naturalWidth || 1280);
      const height = Math.max(1, frame.meta.height || image.naturalHeight || 800);
      if (canvas.width !== width) canvas.width = width;
      if (canvas.height !== height) canvas.height = height;
      context.drawImage(image, 0, 0, width, height);
      renderedFrameRef.current = frame.seq;
      setFrameMeta((current) => current.width === width && current.height === height
        ? current
        : { width, height });
      setHasFrame(true);
    }).catch(() => undefined).finally(() => {
      if (generation !== frameGenerationRef.current) return;
      decodingFrameRef.current = false;
      if ((latestFrameRef.current?.seq || 0) > renderedFrameRef.current) scheduleFrameDecode();
    });
  }

  function queueFrame(messageData: Record<string, unknown>) {
    const data = String(messageData.data || "");
    if (!data) return;
    const seq = Number(messageData.seq || renderedFrameRef.current + 1);
    const frame = (messageData.frame || messageData.frame_meta || {}) as Record<string, unknown>;
    const width = Number(messageData.frame_width || messageData.width || frame.width || 1280);
    const height = Number(messageData.frame_height || messageData.height || frame.height || 800);
    latestFrameRef.current = {
      seq,
      src: `data:image/jpeg;base64,${data}`,
      meta: { width, height },
    };
    scheduleFrameDecode();
  }

  function receiveSnapshot(next: WorkflowSnapshot) {
    const current = snapshotRef.current;
    if (current && next.revision < current.revision && !acceptNextSnapshotRef.current) return;
    acceptNextSnapshotRef.current = false;
    snapshotRef.current = next;
    setSnapshot(next);
    actionRef.current = next.action;
    if (next.stage_seven_attempt_id) {
      stageSevenAttemptIdRef.current = next.stage_seven_attempt_id;
    }
    const init = socketInitRef.current;
    if (init?.type === "resume_verification") {
      socketInitRef.current = {
        ...init,
        restart: false,
        reset_stage_seven: false,
        attempt_id: next.stage_seven_attempt_id || stageSevenAttemptIdRef.current || init.attempt_id,
        result_id: init.result_id || activeResultIdRef.current,
      };
    }
    if (next.title !== undefined) setTitle(next.title);
    if (
      ["published", "editable", "failed", "cancelled"].includes(next.status)
      && !resumeOnlyRef.current
    ) {
      enterCapabilityResults();
    } else if (next.status === "waiting_operator") {
      setAssistantOpen(true);
    }
    if (next.status === "published") setEditingResult(false);
    if (finishRequestedRef.current && next.status !== "recording") {
      finishRequestedRef.current = false;
      setFinishRequested(false);
    }

    const inFlight = patchInFlightRef.current;
    if (inFlight && next.revision > inFlight.revision) {
      patchInFlightRef.current = null;
      clearAcknowledgedLocalValues(inFlight.edits);
      if (pendingEditsRef.current.length) {
        window.setTimeout(flushDraftEdits, 0);
      } else if (republishRequestedRef.current) {
        republishRequestedRef.current = false;
        send({ type: "republish",
          title: next.title || title,
          machine_verification: machineVerificationRef.current,
        });
      }
    }
  }

  function openRecordingSocket(action: string) {
    if (closingRef.current) {
      setConnecting(false);
      return;
    }
    if (wsRef.current) {
      const leftover = wsRef.current;
      wsRef.current = null;
      if (leftover.readyState < WebSocket.CLOSING) leftover.close(1000, "replace");
    }
    setConnecting(true);
    const socket = new WebSocket(recorderWebSocketUrl());
    wsRef.current = socket;
    socket.onopen = () => {
      reconnectAttemptRef.current = 0;
      setConnected(true);
      setConnecting(false);
      const init = socketInitRef.current;
      if (!init) return;
      socket.send(JSON.stringify(init));
      if (init.type === "resume_verification") {
        socketInitRef.current = {
          ...init,
          restart: false,
          reset_stage_seven: false,
        };
      }
      // A disconnected finish command is safe to repeat: the authoritative
      // workflow deduplicates it and returns the current snapshot.
      if (init.type === "start" && finishRequestedRef.current) {
        socket.send(JSON.stringify({
          type: "finish",
          title: title.trim(),
          machine_verification: machineVerificationRef.current,
        }));
      }
    };
    socket.onmessage = (event) => {
      let incoming: Record<string, unknown>;
      try {
        incoming = JSON.parse(String(event.data));
      } catch {
        return;
      }
      if (incoming.type === "snapshot" && incoming.snapshot) {
        receiveSnapshot(incoming.snapshot as WorkflowSnapshot);
      } else if (incoming.type === "thought") {
        appendThought({
          kind: String(incoming.kind || "text"),
          text: String(incoming.text || ""),
          tool: incoming.tool ? String(incoming.tool) : undefined,
          phase: incoming.phase ? String(incoming.phase) : undefined,
          args: incoming.args ? String(incoming.args) : undefined,
          result: incoming.result ? String(incoming.result) : undefined,
          ok: typeof incoming.ok === "boolean" ? incoming.ok : undefined,
        });
      } else if (incoming.type === "recording_result_saved" && incoming.result) {
        const row = incoming.result as RecordingResultSummary;
        upsertHistory(row);
        if (row.action === actionRef.current) {
          activeResultIdRef.current = row.id;
          setActiveResultId(row.id);
        }
      } else if (incoming.type === "frame") {
        queueFrame(incoming);
      } else if (incoming.type === "input_error") {
        message.warning(String(incoming.detail || "页面操作没有执行"));
      } else if (incoming.type === "error") {
        const inFlight = patchInFlightRef.current;
        if (inFlight) {
          patchInFlightRef.current = null;
          replacePendingEdits((queued) => mergePendingEdits(inFlight.edits, queued));
          republishRequestedRef.current = false;
          send({ type: "ping" });
          message.warning("修改尚未保存，已请求最新草稿，请核对后再次发布");
        } else {
          finishRequestedRef.current = false;
          setFinishRequested(false);
          const detail = String(incoming.detail || "录制处理失败");
          if (snapshotRef.current?.status === "processing") {
            const failed: WorkflowSnapshot = {
              ...snapshotRef.current,
              status: "failed",
              error: detail,
              progress: {
                step: "ready",
                label: "处理失败，草稿已保留",
                round: snapshotRef.current.progress.round || 0,
              },
            };
            snapshotRef.current = failed;
            setSnapshot(failed);
            if (!resumeOnlyRef.current) enterCapabilityResults();
          }
          message.error(detail);
        }
      }
    };
    // onclose owns reconnects; a transient transport error is not a workflow failure.
    socket.onerror = () => undefined;
    socket.onclose = () => {
      if (wsRef.current !== socket) return;
      wsRef.current = null;
      setConnected(false);
      setConnecting(false);
      if (cancellingRef.current) {
        applyCancelledSnapshot();
        return;
      }
      if (closingRef.current || !canAutoReconnectRecording()) {
        const resumeDisconnected = socketInitRef.current?.type === "resume_verification"
          && snapshotRef.current?.status === "processing";
        const neverStarted = acceptNextSnapshotRef.current
          && snapshotRef.current?.status === "processing"
          && snapshotRef.current.progress.label === "正在启动机器验证";
        if (resumeDisconnected || neverStarted) {
          const failed: WorkflowSnapshot = {
            ...snapshotRef.current,
            status: "failed",
            error: "分析连接已断开",
            progress: { step: "ready", label: "分析连接已断开，请重新继续分析", round: 0 },
          };
          snapshotRef.current = failed;
          setSnapshot(failed);
          acceptNextSnapshotRef.current = false;
        }
        return;
      }
      reconnectAttemptRef.current += 1;
      const delay = Math.min(5000, 500 * (2 ** Math.min(4, reconnectAttemptRef.current)));
      reconnectTimerRef.current = window.setTimeout(() => {
        reconnectTimerRef.current = null;
        if (!canAutoReconnectRecording()) return;
        openRecordingSocket(actionRef.current);
      }, delay);
    };
  }

  function startRecording() {
    if (!tenant) {
      message.error("请先选择租户");
      return;
    }
    if (!startUrl.trim() || !goalText.trim()) {
      message.error("请填写业务页地址和录制目标");
      return;
    }
    const previous = wsRef.current;
    if (previous && previous.readyState === WebSocket.OPEN) {
      try {
        previous.send(JSON.stringify({ type: "cancel" }));
      } catch {
        // A new recording must still start even if the old cancel frame is lost.
      }
    }
    cancellingRef.current = false;
    setCancelling(false);
    closeRecordingSocket();
    const action = newActionName();
    actionRef.current = action;
    activeResultIdRef.current = "";
    setActiveResultId("");
    resumeOnlyRef.current = false;
    setResumeOnly(false);
    setAnalysisRequested(false);
    setStageSevenOpen(false);
    acceptNextSnapshotRef.current = true;
    setKeepResult(false);
    setKeepRecording(true);
    setViewStage(1);
    reachedStageRef.current = 1;
    setConnecting(true);
    snapshotRef.current = null;
    setSnapshot(null);
    setThoughts([]);
    setExpandedTools({});
    patchInFlightRef.current = null;
    republishRequestedRef.current = false;
    finishRequestedRef.current = false;
    setFinishRequested(false);
    setPendingEdits([]);
    setLocalValues({});
    setLocalCapabilityStepIds({});
    setEditingResult(false);
    setHasFrame(false);
    renderedFrameRef.current = 0;
    latestFrameRef.current = null;
    frameGenerationRef.current += 1;
    socketInitRef.current = {
      type: "start",
      tenant,
      subsystem,
      title: title.trim(),
      start_url: startUrl.trim(),
      goal_text: goalText.trim(),
      base_url: baseUrl.trim() || undefined,
      storage_state: parseStorageState(storageState),
      resume_action: action,
      machine_verification: machineVerificationRef.current,
    };

    openRecordingSocket(action);
  }

  function applyViewedDraft(item: RecordingResultSummary, draft: FlowSpec | null, detail?: RecordingResultDetail | null) {
    const stageStatus = String(
      detail?.machine_verification_status
      || detail?.stage_seven?.status
      || item.machine_verification_status
      || "",
    );
    const next: WorkflowSnapshot = {
      run_id: "",
      action: item.action,
      title: item.title,
      revision: 0,
      status: "editable",
      progress: {
        step: "ready",
        label: stageStatus === "verified"
          ? (item.skill_lifecycle === "exported" ? "Skill 已导出" : "能力已验证，Skill 未产出")
          : "已打开录制结果，尚未开始机器验证",
      },
      draft,
      draft_fingerprint: detail?.draft_fingerprint,
      machine_verification_status: stageStatus,
      capture_frozen: true,
    };
    snapshotRef.current = next;
    setSnapshot(next);
    if (item.title) setTitle(item.title);
    if (detail) setResultMeta(detail);
  }

  async function openResult(item: RecordingResultSummary) {
    if (!tenant) {
      message.error("请先选择租户");
      return;
    }
    if (deletingIdRef.current || openingId) return;
    cancellingRef.current = false;
    setCancelling(false);
    setAnalysisRequested(false);
    setStageSevenOpen(true);
    closeRecordingSocket();
    setOpeningId(item.id);
    actionRef.current = item.action;
    activeResultIdRef.current = item.id;
    setActiveResultId(item.id);
    resumeOnlyRef.current = true;
    setResumeOnly(true);
    machineVerificationRef.current = true;
    setMachineVerification(true);
    setKeepRecording(false);
    setKeepResult(true);
    setViewStage(2);
    reachedStageRef.current = 2;
    setThoughts([]);
    setExpandedTools({});
    pendingEditsRef.current = [];
    setEditingResult(false);
    socketInitRef.current = null;
    try {
      const detail = await getRecordingResult(item.id);
      applyViewedDraft(item, (detail.draft || null) as FlowSpec | null, detail);
    } catch {
      message.error("打开录制结果失败");
      setKeepResult(false);
      setViewStage(0);
      resumeOnlyRef.current = false;
      setResumeOnly(false);
    } finally {
      setOpeningId("");
    }
  }

  function currentResultId() {
    return activeResultIdRef.current
      || history.find((row) => row.action === actionRef.current)?.id
      || "";
  }

  async function startAnalysis(item?: RecordingResultSummary) {
    if (!tenant) {
      message.error("请先选择租户");
      return;
    }
    if (cancellingRef.current) return;
    const socketLive = Boolean(wsRef.current && wsRef.current.readyState === WebSocket.OPEN);
    if (processing && socketLive) return;
    if (item) {
      actionRef.current = item.action;
      activeResultIdRef.current = item.id;
      setActiveResultId(item.id);
      resumeOnlyRef.current = true;
      setResumeOnly(true);
      machineVerificationRef.current = true;
      setMachineVerification(true);
      setKeepRecording(false);
      setKeepResult(true);
      setViewStage(2);
      reachedStageRef.current = 2;
    }
    const resultId = item?.id || currentResultId();
    if (!resultId) {
      message.warning("没有可分析的录制结果，请先停止录制或从历史打开");
      return;
    }
    activeResultIdRef.current = resultId;
    setActiveResultId(resultId);
    closeRecordingSocket();
    acceptNextSnapshotRef.current = true;
    setStageSevenOpen(true);
    let draft = snapshotRef.current?.draft || null;
    const starting: WorkflowSnapshot = {
      run_id: "",
      action: item?.action || actionRef.current,
      title: item?.title || title,
      revision: 0,
      status: "processing",
      progress: { step: "verifying", label: "正在启动机器验证", round: 0 },
      draft,
      capture_frozen: true,
      activity: [],
      issues: [],
    };
    snapshotRef.current = starting;
    setSnapshot(starting);
    setAnalysisRequested(true);
    setThoughts([]);
    setExpandedTools({});
    setConnecting(true);
    socketInitRef.current = {
      type: "resume_verification",
      result_id: resultId,
      tenant,
      subsystem,
      restart: false,
      reset_stage_seven: false,
      attempt_id: stageSevenAttemptIdRef.current || undefined,
    };
    openRecordingSocket(actionRef.current);
    if (draft) return;
    try {
      const detail = await getRecordingResult(resultId);
      const current = snapshotRef.current;
      if (current?.status === "processing" && !current.draft && detail.draft) {
        const next = { ...current, draft: detail.draft as FlowSpec };
        snapshotRef.current = next;
        setSnapshot(next);
      }
      if (detail.title) setTitle(detail.title);
    } catch {
      // The websocket snapshot can still populate the draft.
    }
  }

  async function removeResult(item: RecordingResultSummary) {
    if (deletingIdRef.current) return;
    if (activeResultIdRef.current === item.id && runBusy) {
      message.warning("请先终止分析再删除");
      return;
    }
    if (!window.confirm(`删除「${item.title || item.action}」的录制结果？已发布 Skill 不会被删除。`)) {
      return;
    }
    deletingIdRef.current = item.id;
    setDeletingId(item.id);
    try {
      await deleteRecordingResult(item.id);
      setHistory((current) => current.filter((row) => row.id !== item.id));
      if (activeResultIdRef.current === item.id) {
        closeRecordingSocket();
        activeResultIdRef.current = "";
        setActiveResultId("");
        resumeOnlyRef.current = false;
        setResumeOnly(false);
        setKeepResult(false);
        setViewStage(0);
        snapshotRef.current = null;
        setSnapshot(null);
      }
    } catch {
      message.error("删除录制结果失败");
    } finally {
      deletingIdRef.current = "";
      setDeletingId("");
    }
  }

  function historyPublishLabel(item: RecordingResultSummary) {
    return historyLifecycleView(item);
  }

  async function refreshResultMeta(resultId = currentResultId()) {
    if (!resultId) return;
    try {
      const detail = await getRecordingResult(resultId);
      setResultMeta(detail);
      upsertHistory({
        id: detail.id,
        action: detail.action,
        title: detail.title,
        goal_summary: detail.goal_summary,
        capability_count: detail.capability_count,
        request_count: detail.request_count,
        created_at: detail.created_at,
        published: detail.published,
        machine_verification_ran: detail.machine_verification_ran,
        machine_verification_status: detail.machine_verification_status,
        stage_seven_fingerprint: detail.stage_seven_fingerprint,
        skill_id: detail.skill_id,
        skill_version: detail.skill_version,
        skill_export_status: detail.skill_export_status,
        skill_export_path: detail.skill_export_path,
        skill_lifecycle: detail.skill_lifecycle,
        skill_needs_reexport: detail.skill_needs_reexport,
      });
    } catch {
      // keep the last known meta
    }
  }

  function openSkillExport() {
    if (!canProduceSkill) return;
    setSkillTitle((title || snapshot?.title || "").trim());
    setSkillDescription("");
    setSkillPlanningMode("dynamic");
    setSkillExamples("");
    setSkillSuccess("");
    setSkillForbidden("");
    setSkillOutDir(rememberedExportDir());
    setSkillExportOutcome(null);
    setSkillClarifications([]);
    setSkillExportErrors([]);
    setSkillExportProgress("");
    setSkillExportOpen(true);
  }

  async function submitSkillExport() {
    const resultId = currentResultId();
    if (!resultId) {
      message.error("没有可导出的录制结果");
      return;
    }
    const description = skillDescription.trim();
    if (!skillTitle.trim()) {
      message.error("请填写 Skill 显示名称");
      return;
    }
    if (!description) {
      message.error("请填写业务描述");
      return;
    }
    if (skillExporting) return;
    setSkillExporting(true);
    setSkillExportProgress("正在规划并导出 Skill…");
    setSkillClarifications([]);
    setSkillExportErrors([]);
    if (resultMeta) {
      setResultMeta({ ...resultMeta, skill_lifecycle: "generating", skill_export_status: "generating" });
    }
    const historyRow = history.find((row) => row.id === resultId);
    if (historyRow) {
      upsertHistory({ ...historyRow, skill_lifecycle: "generating", skill_export_status: "generating" });
    }
    try {
      const outDir = skillOutDir.trim();
      if (outDir) rememberExportDir(outDir);
      const outcome = await exportRecordingSkill(resultId, {
        title: skillTitle.trim(),
        business_description: description,
        planning_mode: skillPlanningMode,
        example_requests: skillExamples.split(/\r?\n/).map((item) => item.trim()).filter(Boolean),
        success_criteria: skillSuccess.trim(),
        forbidden_actions: skillForbidden.trim(),
        out_dir: outDir,
      });
      if (outcome.status === "needs_clarification") {
        setSkillClarifications(outcome.clarification_questions || []);
        setSkillExportProgress("");
        message.warning("规划需要补充说明，请根据问题修改业务描述后再导出");
        await refreshResultMeta(resultId);
        return;
      }
      if (outcome.status !== "exported") {
        setSkillExportErrors(outcome.errors || ["Skill 导出失败"]);
        setSkillExportProgress("");
        message.error("Skill 导出失败");
        await refreshResultMeta(resultId);
        return;
      }
      setSkillExportOutcome(outcome);
      setSkillExportProgress("");
      if (outcome.export_path) rememberExportDir(outcome.export_path.replace(/[\\/][^\\/]+$/, "") || outDir);
      await refreshResultMeta(resultId);
      message.success(outcome.idempotent ? "已返回现有 Skill 导出结果" : "Skill 已导出");
    } catch (error) {
      const detail = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setSkillExportErrors([typeof detail === "string" ? detail : "Skill 导出失败"]);
      setSkillExportProgress("");
      message.error(typeof detail === "string" ? detail : "Skill 导出失败");
      await refreshResultMeta(resultId);
    } finally {
      setSkillExporting(false);
    }
  }

  async function copySkillDir(pathValue?: string) {
    const target = String(pathValue || skillExportOutcome?.export_path || "").trim();
    if (!target) {
      message.warning("没有可打开的 Skill 目录");
      return;
    }
    try {
      await navigator.clipboard.writeText(target);
      message.success("Skill 目录已复制，请在资源管理器中打开");
    } catch {
      message.info(target);
    }
  }

  function finishRecording() {
    if (finishRequestedRef.current || status !== "recording") return;
    finishRequestedRef.current = true;
    setFinishRequested(true);
    if (!send({ type: "finish",
      title: title.trim(),
      machine_verification: machineVerificationRef.current,
    })) {
      finishRequestedRef.current = false;
      setFinishRequested(false);
    }
  }

  function applyCancelledSnapshot() {
    cancellingRef.current = false;
    setCancelling(false);
    setAnalysisRequested(false);
    setConnecting(false);
    if (snapshotRef.current && snapshotRef.current.status !== "cancelled") {
      const cancelled: WorkflowSnapshot = {
        ...snapshotRef.current,
        status: "cancelled",
        error: "",
        progress: {
          step: "ready",
          label: "当前分析已终止，草稿已保留",
          round: snapshotRef.current.progress.round || 0,
        },
      };
      snapshotRef.current = cancelled;
      setSnapshot(cancelled);
    }
  }

  function cancelProcessing() {
    if (cancellingRef.current || status === "cancelled") return;
    cancellingRef.current = true;
    const socket = wsRef.current;
    if (socket && socket.readyState === WebSocket.OPEN) {
      try {
        socket.send(JSON.stringify({ type: "cancel" }));
      } catch {
        // Local UI still stops even if the cancel frame cannot be written.
      }
    }
    closeRecordingSocket();
    applyCancelledSnapshot();
  }

  function answerQuestion(value?: string) {
    const question = snapshot?.question;
    const finalAnswer = String(value ?? answer).trim();
    if (!question || !finalAnswer) return;
    if (send({ type: "answer", question_id: question.question_id, answer: finalAnswer })) {
      setAnswer("");
    }
  }

  function editKey(stepId: string, path: string, field: string) {
    return `${stepId}\u0000${path}\u0000${field}`;
  }

  function paramValue(step: FlowStep, param: FlowParam, field: keyof FlowParam) {
    const key = editKey(step.step_id, param.path, String(field));
    return Object.prototype.hasOwnProperty.call(localValues, key)
      ? localValues[key]
      : param[field];
  }

  function updateParam(step: FlowStep, param: FlowParam, field: keyof FlowParam, value: unknown) {
    const key = editKey(step.step_id, param.path, String(field));
    setLocalValues((current) => ({ ...current, [key]: value }));
    replacePendingEdits((current) => [
      ...current.filter((edit) => !(
        edit.op === "update"
        && edit.step_id === step.step_id
        && edit.param_path === param.path
        && edit.field === field
      )),
      {
        op: "update",
        actor: "user",
        step_id: step.step_id,
        param_path: param.path,
        field,
        value,
      },
    ]);
  }

  function updateCapability(capability: FlowCapability, field: "name" | "title" | "intent", value: string) {
    const reference = String(capability.capability_id || capability.name || "");
    const key = `capability\u0000${reference}\u0000${field}`;
    setLocalValues((current) => ({ ...current, [key]: value }));
    replacePendingEdits((current) => [
      ...current.filter((edit) => !(
        edit.op === "update_capability"
        && (edit.capability_id === capability.capability_id || edit.capability_name === capability.name)
        && edit.field === field
      )),
      {
        op: "update_capability",
        actor: "user",
        capability_id: capability.capability_id,
        capability_name: capability.name,
        field,
        value,
      },
    ]);
  }

  function capabilityValue(capability: FlowCapability, field: "name" | "title" | "intent") {
    const reference = String(capability.capability_id || capability.name || "");
    const key = `capability\u0000${reference}\u0000${field}`;
    return Object.prototype.hasOwnProperty.call(localValues, key)
      ? String(localValues[key] || "")
      : String(capability[field] || "");
  }

  function capabilityReference(capability: FlowCapability, index: number) {
    return {
      capability_id: capability.capability_id,
      capability_name: capability.name,
      capability_index: index,
    };
  }

  function capabilityKey(capability: FlowCapability, index: number) {
    return String(capability.capability_id || capability.name || index);
  }

  function serverCapabilityExecuteStepIds(capability: FlowCapability) {
    return Array.from(new Set([
      ...(capability.step_ids || []),
      ...(capability.request_refs || [])
        .filter((ref) => (ref.usage || "execute") === "execute")
        .map((ref) => String(ref.step_id || "")),
    ].filter(Boolean)));
  }

  function capabilityExecuteStepIds(capability: FlowCapability, index: number) {
    const key = capabilityKey(capability, index);
    if (Object.prototype.hasOwnProperty.call(localCapabilityStepIds, key)) {
      return localCapabilityStepIds[key];
    }
    return serverCapabilityExecuteStepIds(capability);
  }

  function replaceCapabilityMembershipEdits(
    capability: FlowCapability,
    index: number,
    nextStepIds: string[],
  ) {
    const reference = capabilityReference(capability, index);
    const originalStepIds = serverCapabilityExecuteStepIds(capability);
    const targetsCapability = (edit: DraftEdit) => (
      (Boolean(capability.capability_id) && edit.capability_id === capability.capability_id)
      || (Boolean(capability.name) && edit.capability_name === capability.name)
      || edit.capability_index === index
    );
    const membershipOps = new Set([
      "add_capability_step", "remove_capability_step", "reorder_capability_steps",
    ]);
    const edits: DraftEdit[] = [
      ...originalStepIds
        .filter((stepId) => !nextStepIds.includes(stepId))
        .map((stepId) => ({
          op: "remove_capability_step", actor: "user" as const, ...reference, step_id: stepId,
        })),
      ...nextStepIds
        .filter((stepId) => !originalStepIds.includes(stepId))
        .map((stepId) => ({
          op: "add_capability_step", actor: "user" as const, ...reference,
          step_id: stepId, usage: "execute", origin: "manual", confirmed: true,
        })),
      {
        op: "reorder_capability_steps", actor: "user", ...reference, step_ids: nextStepIds,
      },
    ];
    setLocalCapabilityStepIds((current) => ({
      ...current,
      [capabilityKey(capability, index)]: nextStepIds,
    }));
    replacePendingEdits((current) => [
      ...current.filter((edit) => !(membershipOps.has(edit.op) && targetsCapability(edit))),
      ...edits,
    ]);
  }

  function addStepToCapability(index: number, stepId?: string) {
    const capability = capabilities[index];
    if (!capability || !stepId) return;
    const current = capabilityExecuteStepIds(capability, index);
    if (current.includes(stepId)) return;
    replaceCapabilityMembershipEdits(capability, index, [...current, stepId]);
  }

  function removeStepFromCapability(index: number, stepId: string) {
    const capability = capabilities[index];
    if (!capability) return;
    replaceCapabilityMembershipEdits(
      capability,
      index,
      capabilityExecuteStepIds(capability, index).filter((item) => item !== stepId),
    );
  }

  function moveStepInCapability(index: number, stepId: string, delta: number) {
    const capability = capabilities[index];
    if (!capability) return;
    const current = capabilityExecuteStepIds(capability, index);
    const from = current.indexOf(stepId);
    const to = from + delta;
    if (from < 0 || to < 0 || to >= current.length) return;
    const next = [...current];
    next.splice(from, 1);
    next.splice(to, 0, stepId);
    replaceCapabilityMembershipEdits(capability, index, next);
  }

  function capabilityUsageLabel(usage?: string) {
    return ({
      execute: "执行", preflight: "前置", option_source: "选项源", fact_check: "校验",
    } as Record<string, string>)[usage || "execute"] || usage || "关联";
  }

  function republish() {
    if (!snapshot || !draft || processing) return;
    if (pendingEditsRef.current.length || patchInFlightRef.current) {
      republishRequestedRef.current = true;
      flushDraftEdits();
      return;
    }
    const payload = {
      type: "republish",
      title: title.trim(),
      machine_verification: machineVerificationRef.current,
    };
    if (send(payload)) return;
    republishRequestedRef.current = true;
    startAnalysis();
  }

  function requestPublish() {
    if (status === "recording") {
      finishRecording();
      return;
    }
    republish();
  }

  function cancelResultEditing() {
    if (patchInFlightRef.current) return;
    pendingEditsRef.current = [];
    setPendingEdits([]);
    setLocalValues({});
    setLocalCapabilityStepIds({});
    republishRequestedRef.current = false;
    setEditingResult(false);
  }

  function normalizedPoint(clientX: number, clientY: number) {
    const canvas = canvasRef.current;
    if (!canvas) return null;
    const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    return {
      nx: Math.max(0, Math.min(1, (clientX - rect.left) / rect.width)),
      ny: Math.max(0, Math.min(1, (clientY - rect.top) / rect.height)),
    };
  }

  function pointerButton(button: number) {
    return button === 1 ? "middle" : button === 2 ? "right" : "left";
  }

  function onPointerDown(event: PointerEvent<HTMLCanvasElement>) {
    if (!connected || status !== "recording") return;
    const point = normalizedPoint(event.clientX, event.clientY);
    if (!point) return;
    event.preventDefault();
    const button = pointerButton(event.button);
    pointerRef.current = { pointerId: event.pointerId, button };
    try { event.currentTarget.setPointerCapture(event.pointerId); } catch { /* no-op */ }
    send({ type: "input", event: { kind: "pointer_down", ...point, button, buttons: event.buttons } });
    keyboardRef.current?.focus({ preventScroll: true });
  }

  function onPointerMove(event: PointerEvent<HTMLCanvasElement>) {
    if (!connected || status !== "recording") return;
    const point = normalizedPoint(event.clientX, event.clientY);
    if (!point) return;
    pointerMoveRef.current = { kind: "pointer_move", ...point, buttons: event.buttons };
    if (pointerTimerRef.current !== null) return;
    pointerTimerRef.current = window.setTimeout(() => {
      pointerTimerRef.current = null;
      const move = pointerMoveRef.current;
      pointerMoveRef.current = null;
      if (move) send({ type: "input", event: move });
    }, 50);
  }

  function onPointerUp(event: PointerEvent<HTMLCanvasElement>) {
    const pointer = pointerRef.current;
    if (!pointer || pointer.pointerId !== event.pointerId) return;
    pointerRef.current = null;
    const point = normalizedPoint(event.clientX, event.clientY);
    if (!point) return;
    event.preventDefault();
    send({
      type: "input",
      event: { kind: "pointer_up", ...point, button: pointer.button, buttons: event.buttons },
    });
  }

  function onWheel(event: WheelEvent<HTMLCanvasElement>) {
    if (!connected || status !== "recording") return;
    event.preventDefault();
    const point = normalizedPoint(event.clientX, event.clientY) || {};
    const current = wheelRef.current || { dx: 0, dy: 0 };
    wheelRef.current = {
      dx: current.dx + event.deltaX,
      dy: current.dy + event.deltaY,
      ...point,
    };
    if (wheelTimerRef.current !== null) return;
    wheelTimerRef.current = window.setTimeout(() => {
      wheelTimerRef.current = null;
      const wheel = wheelRef.current;
      wheelRef.current = null;
      if (wheel) send({ type: "input", event: { kind: "scroll", ...wheel } });
    }, 50);
  }

  function relayText(element: HTMLInputElement) {
    if (!element.value) return;
    send({ type: "input", event: { kind: "text", text: element.value } });
    element.value = "";
  }

  function onKeyboardInput(event: FormEvent<HTMLInputElement>) {
    if (composingRef.current) return;
    relayText(event.currentTarget);
  }

  function onKeyboardDown(event: KeyboardEvent<HTMLInputElement>) {
    const simpleKeys: Record<string, string> = {
      Backspace: "Backspace", Delete: "Delete", Enter: "Enter", Tab: "Tab",
      Escape: "Escape", ArrowUp: "ArrowUp", ArrowDown: "ArrowDown",
      ArrowLeft: "ArrowLeft", ArrowRight: "ArrowRight", Home: "Home", End: "End",
      PageUp: "PageUp", PageDown: "PageDown",
    };
    const key = simpleKeys[event.key];
    if (!key) return;
    if (key === "Backspace") lastBackspaceRef.current = performance.now();
    send({ type: "input", event: { kind: "key", key } });
    event.preventDefault();
  }

  function onBeforeInput(event: FormEvent<HTMLInputElement>) {
    const inputEvent = event.nativeEvent as InputEvent;
    if (inputEvent.inputType !== "deleteContentBackward") return;
    if (performance.now() - lastBackspaceRef.current > 80) {
      send({ type: "input", event: { kind: "key", key: "Backspace" } });
    }
    event.preventDefault();
  }

  function renderSetup() {
    return (
      <div>
      <Card>
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0, flexWrap: "nowrap" }}>
            <Text strong style={{ whiteSpace: "nowrap" }}><Text type="danger">* </Text>业务地址</Text>
            <Input
              value={startUrl}
              onChange={(event) => setStartUrl(event.target.value)}
              placeholder="https://example.com/business/page"
              style={{ flex: 1.4, minWidth: 160 }}
            />
            <Text strong style={{ whiteSpace: "nowrap" }}>Skill 名称</Text>
            <Input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="例如：请假申请"
              style={{ flex: 0.8, minWidth: 120 }}
            />
            {status !== "recording" ? (
              <Button
                type="primary"
                loading={connecting && !resumeOnly}
                onClick={startRecording}
                style={{ flexShrink: 0 }}
              >
                开始录制
              </Button>
            ) : null}
          </div>
          <div style={{ display: "flex", alignItems: "flex-start", gap: 10, width: "100%", minWidth: 0 }}>
            <Text strong style={{ whiteSpace: "nowrap", paddingTop: 5 }}><Text type="danger">* </Text>录制目标</Text>
            <div style={{ flex: 1, minWidth: 0 }}>
              <Input.TextArea
                value={goalText}
                onChange={(event) => setGoalText(event.target.value)}
                placeholder="查询记录、保存草稿并提交申请"
                autoSize={{ minRows: 2, maxRows: 5 }}
                style={{ width: "100%" }}
              />
              <div style={{ marginTop: 4 }}>
                <Text type="secondary">系统只根据实际录制且有完整证据的业务操作生成能力。</Text>
              </div>
            </div>
          </div>
        </div>
      </Card>
      <Card title="历史录制结果" size="small" style={{ marginTop: 12 }}>
        <Table<RecordingResultSummary>
          rowKey="id"
          size="small"
          loading={historyLoading}
          pagination={false}
          dataSource={history}
          locale={{ emptyText: <Empty description="还没有保存的录制结果" /> }}
          columns={[
            {
              title: "Skill",
              render: (_, item) => {
                const lifecycle = historyPublishLabel(item);
                return (
                  <div>
                    <div>
                      {(item.title || "").trim() || (item.goal_summary || "").trim() || "未命名录制"}
                      <Tag color={lifecycle.color as "success"} style={{ marginLeft: 8 }}>{lifecycle.label}</Tag>
                    </div>
                    <div style={{ fontSize: 12, color: "#999" }}>{item.action}</div>
                  </div>
                );
              },
            },
            {
              title: "能力",
              width: 80,
              render: (_, item) => item.capability_count,
            },
            {
              title: "请求",
              width: 80,
              render: (_, item) => item.request_count,
            },
            {
              title: "产出时间",
              width: 180,
              render: (_, item) => (
                <Text type="secondary" style={{ fontSize: 12 }}>{fmtHistoryTime(item.created_at)}</Text>
              ),
            },
            {
              title: "操作",
              width: 160,
              render: (_, item) => (
                <Space>
                  <Button
                    size="small"
                    loading={openingId === item.id}
                    onClick={() => openResult(item)}
                  >继续分析</Button>
                  <Button
                    size="small"
                    danger
                    icon={<DeleteOutlined />}
                    disabled={deletingId === item.id}
                    loading={deletingId === item.id}
                    onClick={() => removeResult(item)}
                  >删除</Button>
                </Space>
              ),
            },
          ]}
        />
      </Card>
      </div>
    );
  }

  function renderRecording() {
    return (
      <div style={{ minWidth: 0, flex: 1, minHeight: 0, height: "100%", display: "flex", flexDirection: "column" }}>
        <Card size="small" styles={{ body: { padding: 10 } }} style={{ flexShrink: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0, flexWrap: "nowrap" }}>
            <Tag color={status === "recording" ? "processing" : processing ? "blue" : "default"}>
              {status === "processing" && snapshot?.progress.label
                ? snapshot.progress.label
                : STATUS_LABELS[status]}
            </Tag>
            <Space size={6} style={{ whiteSpace: "nowrap" }}>
              <Switch
                size="small"
                checked={machineVerification}
                disabled={processing || (status !== "recording" && !canRetryPublish)}
                onChange={(checked) => {
                  machineVerificationRef.current = checked;
                  setMachineVerification(checked);
                  if (status === "recording") {
                    send({ type: "set_analysis_mode", machine_verification: checked });
                  }
                }}
              />
              <Text>编译并进行机器验证</Text>
            </Space>
            {status === "recording" || finishRequested || processing ? (
              <Button
                type="primary"
                loading={finishRequested || status === "processing"}
                disabled={processing || (status !== "recording" && !canRetryPublish)}
                onClick={requestPublish}
              >
                结束录制并分析
              </Button>
            ) : (
              <Button
                type="primary"
                loading={connecting}
                onClick={startRecording}
              >
                {connecting ? "正在连接" : "重新连接"}
              </Button>
            )}
            {processing || cancelling ? (
              <Button danger icon={<StopOutlined />} loading={cancelling} onClick={cancelProcessing}>一键终止</Button>
            ) : null}
            <Button icon={<RobotOutlined />} onClick={() => setAssistantOpen(true)}>录制助手</Button>
          </div>
        </Card>
        <div
          style={{
            flex: 1,
            minHeight: 0,
            marginTop: 10,
            width: "100%",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
        <div
          style={{
            containerType: "size",
            width: "100%",
            height: "100%",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
        <div
          style={{
            position: "relative",
            width: `min(100%, calc(100cqh * ${frameMeta.width} / ${frameMeta.height}))`,
            height: `min(100%, calc(100cqw * ${frameMeta.height} / ${frameMeta.width}))`,
            overflow: "hidden",
            border: "1px solid #d9d9d9",
            borderRadius: 8,
            background: "#eef1f5",
          }}
        >
          <canvas
            ref={canvasRef}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerCancel={() => { pointerRef.current = null; }}
            onWheel={onWheel}
            onContextMenu={(event) => event.preventDefault()}
            style={{
              display: hasFrame ? "block" : "none",
              width: "100%",
              height: "100%",
              touchAction: "none",
              cursor: status === "recording" ? "default" : "not-allowed",
            }}
          />
          {!hasFrame ? (
            <Empty
              description={connecting ? "正在连接业务页面" : "等待页面画面"}
              style={{ height: "100%", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", margin: 0 }}
            />
          ) : null}
          <input
            ref={keyboardRef}
            onInput={onKeyboardInput}
            onKeyDown={onKeyboardDown}
            onBeforeInput={onBeforeInput}
            onCompositionStart={() => { composingRef.current = true; }}
            onCompositionEnd={(event) => {
              composingRef.current = false;
              relayText(event.currentTarget);
            }}
            onPaste={(event) => {
              const text = event.clipboardData.getData("text");
              if (text) send({ type: "input", event: { kind: "text", text } });
              event.preventDefault();
            }}
            aria-label="录制页面键盘输入"
            style={{ position: "absolute", width: 1, height: 1, opacity: 0, left: 0, top: 0 }}
          />
        </div>
        </div>
        </div>
      </div>
    );
  }

  function renderParamEditor(step: FlowStep, param: FlowParam) {
    return (
      <div
        key={`${step.step_id}:${param.field_id || param.path}`}
        style={{
          display: "grid",
          gridTemplateColumns: "1.1fr 1.2fr 1fr 0.9fr 1.2fr 80px",
          gap: 8,
          alignItems: "center",
          padding: "8px 0",
          borderTop: "1px solid #f0f0f0",
        }}
      >
        <Input
          value={safeString(paramValue(step, param, "label") || param.key)}
          onChange={(event) => updateParam(step, param, "label", event.target.value)}
          aria-label="字段名称"
        />
        <Text code ellipsis={{ tooltip: safeString(param.path) }}>{safeString(param.path)}</Text>
        <Input
          value={safeString(paramValue(step, param, "value") ?? param.default_value)}
          onChange={(event) => updateParam(step, param, "value", event.target.value)}
          aria-label="默认值"
        />
        <Select
          value={safeString(paramValue(step, param, "type") || "string")}
          options={TYPE_OPTIONS}
          onChange={(value) => updateParam(step, param, "type", value)}
          aria-label="字段类型"
        />
        <Select
          value={safeString(paramValue(step, param, "source_kind") || "caller_input")}
          options={SOURCE_OPTIONS}
          onChange={(value) => updateParam(step, param, "source_kind", value)}
          aria-label="字段来源"
        />
        <Checkbox
          checked={Boolean(paramValue(step, param, "required"))}
          onChange={(event) => updateParam(step, param, "required", event.target.checked)}
        >必填</Checkbox>
      </div>
    );
  }

  function capabilityAllSteps(capability: FlowCapability, index: number) {
    const ids = Array.from(new Set([
      ...capabilityExecuteStepIds(capability, index),
      ...(capability.request_refs || []).map((ref) => String(ref.step_id || "")),
    ].filter(Boolean)));
    const stepById = new Map(steps.map((step) => [step.step_id, step]));
    return ids.map((stepId) => stepById.get(stepId)).filter(Boolean) as FlowStep[];
  }

  function capabilityOrchestration(capability: FlowCapability) {
    const usageRank: Record<string, number> = {
      option_source: 0, preflight: 1, execute: 2, fact_check: 3,
    };
    const stepById = new Map(steps.map((step) => [step.step_id, step]));
    const refs = [...(capability.request_refs || [])].sort((left, right) => {
      const rank = (usageRank[left.usage || "execute"] ?? 9) - (usageRank[right.usage || "execute"] ?? 9);
      if (rank !== 0) return rank;
      return Number(left.sequence || 0) - Number(right.sequence || 0);
    });
    return refs.map((ref) => {
      const step = stepById.get(String(ref.step_id || ""));
      return {
        ref,
        step: step || {
          step_id: String(ref.step_id || ref.request_id || ref.path || ""),
          name: String(ref.path || ref.request_id || "关联请求"),
          method: String(ref.method || ""),
          path: String(ref.path || ""),
        } as FlowStep,
      };
    });
  }

  function capabilityParams(capabilitySteps: FlowStep[]) {
    const unique = new Map<string, { step: FlowStep; param: FlowParam }>();
    capabilitySteps.forEach((step) => (step.params || []).forEach((param) => {
      const key = String(param.field_id || `${step.step_id}:${param.path}`);
      if (!unique.has(key)) unique.set(key, { step, param });
    }));
    return Array.from(unique.values());
  }

  function capabilityPublicParams(capability: FlowCapability, capabilitySteps: FlowStep[]) {
    const inputSchema = asRecord(capability.input_schema);
    if (!Object.prototype.hasOwnProperty.call(inputSchema, "properties")) return null;
    const properties = asRecord(inputSchema.properties);
    const required = new Set(
      Array.isArray(inputSchema.required) ? inputSchema.required.map((value) => String(value)) : [],
    );
    const anchor = capabilitySteps.at(-1) || {
      step_id: String(capability.capability_id || capability.name || "capability"),
      name: String(capability.title || capability.name || "能力输入"),
    };
    return Object.entries(properties).flatMap(([key, rawSchema]) => {
      const schema = asRecord(rawSchema);
      const optionSource = asRecord(schema["x-dano-option-source"]);
      const externalSource = asRecord(schema["x-dano-external-source"]);
      const sourceCapability = safeString(schema["x-dano-source-capability"]);
      const businessType = safeString(schema["x-dano-business-type"]);
      const format = safeString(schema.format);
      const snapshot = schema["x-options-snapshot"];
      const enumValues = schema.enum;
      let type = safeString(schema.type) || "string";
      if (businessType === "single_enum") type = "enum";
      else if (businessType === "multi_enum") type = "list-enum";
      else if (format === "date") type = "date";
      else if (format === "date-time") type = "datetime";
      const flowPath = safeString(schema["x-flow-path"]) || key;
      if (looksPaginationField({ key, path: flowPath })) return [];
      const matched = capabilitySteps
        .flatMap((step) => (step.params || []).map((param) => ({ step, param })))
        .find(({ param }) => (
          safeString(param.key) === key
          || safeString(param.path) === flowPath
          || safeString(param.path).endsWith(`.${key}`)
        ));
      if (matched) {
        if (looksPaginationField(matched.param) || matched.param.exposed_to_user === false) {
          return [];
        }
        return [{
          step: matched.step,
          param: {
            ...matched.param,
            key,
            path: flowPath || matched.param.path,
            label: safeString(schema.label || schema.title) || matched.param.label || key,
            type: type || matched.param.type,
            required: typeof matched.param.required === "boolean"
              ? matched.param.required
              : required.has(key),
            exposed_to_user: matched.param.exposed_to_user !== false,
          } satisfies FlowParam,
        }];
      }
      const hasOptionSource = Boolean(optionSource.source_url);
      const hasUpstreamSource = Boolean(sourceCapability || externalSource.step_id);
      const sourceKind = hasOptionSource
        ? "api_option"
        : hasUpstreamSource ? "previous_response" : Array.isArray(enumValues) ? "static_enum" : "user_input";
      return [{
        step: anchor,
        param: {
          path: flowPath,
          key,
          label: safeString(schema.label || schema.title) || key,
          type,
          source_kind: sourceKind,
          source: optionSource,
          exposed_to_user: true,
          required: required.has(key),
          reason: safeString(schema.description),
          enum_options: Array.isArray(snapshot)
            ? snapshot
            : Array.isArray(enumValues) ? enumValues : undefined,
        } satisfies FlowParam,
      }];
    });
  }

  function enumPreview(param: FlowParam) {
    const values = (param.enum_options || []).slice(0, 4).map((option) => {
      if (option && typeof option === "object") {
        const record = option as Record<string, unknown>;
        return String(record.label || record.name || record.value || "");
      }
      return String(option);
    }).filter(Boolean);
    return values.join("、");
  }

  function renderFieldSummary(
    titleText: string,
    entries: Array<{ step: FlowStep; param: FlowParam }>,
    emptyText: string,
  ) {
    return (
      <Card size="small" title={`${titleText} ${entries.length}`}>
        {entries.length ? (
          <List
            size="small"
            dataSource={entries}
            renderItem={({ step, param }) => {
              const fixedValue = param.value !== undefined ? param.value : param.default_value;
              const preview = enumPreview(param);
              return (
                <List.Item>
                  <div style={{ width: "100%", minWidth: 0 }}>
                    <Space wrap size={[6, 4]}>
                      <Text strong>{paramDisplayName(param)}</Text>
                      <Tag>{paramTypeLabel(param)}</Tag>
                      {param.required ? <Tag color="error">必填</Tag> : <Tag>可选</Tag>}
                      <Tag
                        color={paramSourceTagColor(param)}
                        style={paramSourceLabel(param) === "未知" ? {
                          color: "#fff",
                          background: "#cf1322",
                          borderColor: "#a8071a",
                          fontWeight: 600,
                        } : undefined}
                      >
                        {paramSourceLabel(param)}
                      </Tag>
                    </Space>
                    {param.source_kind === "constant" && fixedValue !== undefined ? (
                      <div><Text type="secondary">{constantValueCaption(param)}：{safeString(fixedValue)}</Text></div>
                    ) : null}
                    {apiOptionSourceSummary(param) ? (
                      <div><Text type="secondary">{apiOptionSourceSummary(param)}</Text></div>
                    ) : null}
                    {preview ? (
                      <div>
                        <Text type="secondary">
                          {param.source_kind === "api_option" ? "录制时样例" : "可选值"}：{preview}
                        </Text>
                      </div>
                    ) : null}
                    {param.reason ? <div><Text type="secondary">{param.reason}</Text></div> : null}
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {step.name || step.step_id}
                    </Text>
                  </div>
                </List.Item>
              );
            }}
          />
        ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={emptyText} />}
      </Card>
    );
  }

  function linkStepId(link: FlowLink, side: "source" | "target") {
    const nested = asRecord(link[side]);
    return String(link[`${side}_step_id`] || nested.step_id || "");
  }

  function linkPath(link: FlowLink, side: "source" | "target") {
    const nested = asRecord(link[side]);
    return String(link[`${side}_path`] || nested.path || "");
  }

  function renderCapabilityResult(capability: FlowCapability, index: number) {
    const capabilitySteps = capabilityAllSteps(capability, index);
    const executeIds = capabilityExecuteStepIds(capability, index);
    const allStepIds = new Set(capabilitySteps.map((step) => step.step_id));
    const params = capabilityParams(capabilitySteps);
    const publicParams = capabilityPublicParams(capability, capabilitySteps);
    const callerInputs = publicParams ?? params.filter(({ param }) => paramIsCallerInput(param));
    const publicKeys = new Set(callerInputs.flatMap(({ param }) => [param.key, param.path]));
    const automaticInputs = params.filter(({ param }) => (
      !paramIsCallerInput(param)
      && !publicKeys.has(param.key)
      && !publicKeys.has(param.path)
    ));
    const stepById = new Map(steps.map((step) => [step.step_id, step]));
    const links: FlowLink[] = [
      ...(draft?.links || []).filter((link) => (
        allStepIds.has(linkStepId(link, "source")) && allStepIds.has(linkStepId(link, "target"))
      )),
      ...((capability.dependencies || []) as FlowLink[]),
    ];
    const evidence = params.filter(({ param }) => Boolean(param.reason));
    const orchestration = capabilityOrchestration(capability);
    return (
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        {capability.intent ? <Text>{capability.intent}</Text> : null}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 12 }}>
          {renderFieldSummary("调用方提供", callerInputs, "调用方无需提供业务参数")}
          {renderFieldSummary("系统自动处理", automaticInputs, "没有自动注入字段")}
        </div>
        <Card size="small" title={`执行编排 ${orchestration.length}`}>
          {orchestration.length ? (
            <List
              size="small"
              dataSource={orchestration}
              renderItem={({ step, ref }, stepIndex) => {
                const usage = ref.usage || (executeIds.includes(step.step_id) ? "execute" : undefined);
                return (
                  <List.Item>
                    <Space align="start" style={{ width: "100%" }}>
                      <Tag color="blue">{stepIndex + 1}</Tag>
                      <div style={{ minWidth: 0 }}>
                        <Space wrap>
                          <Tag color={step.method === "GET" ? "blue" : "green"}>{step.method || "HTTP"}</Tag>
                          <Tag color={usage === "execute" ? "processing" : "purple"}>{capabilityUsageLabel(usage)}</Tag>
                          <Text strong>{step.name || `步骤 ${stepIndex + 1}`}</Text>
                        </Space>
                        <div><Text code>{stepDisplayPath(step)}</Text></div>
                      </div>
                    </Space>
                  </List.Item>
                );
              }}
            />
          ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有执行接口" />}
          {links.length ? (
            <div style={{ borderTop: "1px solid #f0f0f0", paddingTop: 10 }}>
              <Text strong>参数传递</Text>
              <List
                size="small"
                dataSource={links}
                renderItem={(link, linkIndex) => {
                  const sourceId = linkStepId(link, "source");
                  const targetId = linkStepId(link, "target");
                  return (
                    <List.Item key={`${sourceId}:${targetId}:${linkIndex}`}>
                      <Space wrap>
                        <Text>{stepById.get(sourceId)?.name || sourceId || "上游响应"}</Text>
                        <Text code>{linkPath(link, "source") || "返回值"}</Text>
                        <Text>→</Text>
                        <Text>{stepById.get(targetId)?.name || targetId || "下游请求"}</Text>
                        <Text code>{linkPath(link, "target") || "请求字段"}</Text>
                        {link.confirmed ? <Tag color="success">已确认</Tag> : null}
                      </Space>
                    </List.Item>
                  );
                }}
              />
            </div>
          ) : null}
        </Card>
        {(evidence.length || links.some((link) => link.reason)) ? (
          <Collapse
            ghost
            items={[{
              key: "evidence",
              label: `识别依据 ${evidence.length + links.filter((link) => link.reason).length}`,
              children: (
                <List
                  size="small"
                  dataSource={[
                    ...evidence.map(({ param }) => `${paramDisplayName(param)}：${param.reason}`),
                    ...links.filter((link) => link.reason).map((link) => String(link.reason)),
                  ]}
                  renderItem={(item) => <List.Item><Text type="secondary">{item}</Text></List.Item>}
                />
              ),
            }]}
          />
        ) : null}
      </Space>
    );
  }

  function renderCapabilityEditor(capability: FlowCapability, index: number) {
    const stepIds = capabilityExecuteStepIds(capability, index);
    const auxiliaryRefs = (capability.request_refs || []).filter(
      (ref) => ref.usage !== "execute" && ref.step_id && !stepIds.includes(ref.step_id),
    );
    const auxiliaryStepIds = new Set(auxiliaryRefs.map((ref) => String(ref.step_id)));
    const stepById = new Map(steps.map((step) => [step.step_id, step]));
    const capabilitySteps = stepIds.map((stepId) => stepById.get(stepId)).filter(Boolean) as FlowStep[];
    const unusedSteps = steps.filter(
      (step) => !stepIds.includes(step.step_id) && !auxiliaryStepIds.has(step.step_id),
    );
    return (
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 2fr", gap: 10 }}>
          <label>
            <Text type="secondary">名称</Text>
            <Input
              value={capabilityValue(capability, "title")}
              onChange={(event) => updateCapability(capability, "title", event.target.value)}
            />
          </label>
          <label>
            <Text type="secondary">用途</Text>
            <Input
              value={capabilityValue(capability, "intent")}
              onChange={(event) => updateCapability(capability, "intent", event.target.value)}
            />
          </label>
        </div>
        <Space wrap>
          <Text type="secondary">能力 ID</Text><Text code>{capability.capability_id || capability.name}</Text>
          <Text type="secondary">接口 {stepIds.length + auxiliaryStepIds.size}</Text>
          <Select
            value={undefined}
            placeholder="添加执行接口"
            style={{ minWidth: 260 }}
            options={unusedSteps.map((step) => ({
              value: step.step_id,
              label: `${step.method || "HTTP"} ${stepDisplayPath(step) || step.name || step.step_id}`,
            }))}
            onChange={(stepId) => addStepToCapability(index, stepId)}
          />
        </Space>
        {capabilitySteps.map((step, stepIndex) => (
          <Card
            key={step.step_id}
            size="small"
            title={<Space><Tag color="blue">执行</Tag><Tag color={step.method === "GET" ? "blue" : "green"}>{step.method}</Tag><Text>{step.name}</Text><Text code>{stepDisplayPath(step)}</Text></Space>}
            extra={<Space>
              <Button size="small" disabled={stepIndex === 0} onClick={() => moveStepInCapability(index, step.step_id, -1)}>上移</Button>
              <Button size="small" disabled={stepIndex === capabilitySteps.length - 1} onClick={() => moveStepInCapability(index, step.step_id, 1)}>下移</Button>
              <Button size="small" danger onClick={() => removeStepFromCapability(index, step.step_id)}>移除</Button>
            </Space>}
          >
            <div style={{ display: "grid", gridTemplateColumns: "1.1fr 1.2fr 1fr 0.9fr 1.2fr 80px", gap: 8, color: "#8c8c8c", paddingBottom: 6 }}>
              <span>名称</span><span>路径（只读）</span><span>默认值</span><span>类型</span><span>来源</span><span>必填性</span>
            </div>
            {(step.params || []).map((param) => renderParamEditor(step, param))}
          </Card>
        ))}
        {auxiliaryRefs.map((ref) => {
          const step = stepById.get(String(ref.step_id));
          if (!step) return null;
          return (
            <Card key={`${ref.usage || "auxiliary"}:${step.step_id}`} size="small">
              <Space wrap>
                <Tag color="purple">{capabilityUsageLabel(ref.usage)}</Tag>
                <Tag>{step.method}</Tag><Text>{step.name}</Text><Text code>{stepDisplayPath(step)}</Text>
              </Space>
            </Card>
          );
        })}
      </Space>
    );
  }

  function renderCapabilities() {
    if (openingId) {
      return (
        <div style={{ textAlign: "center", padding: "48px 0" }}>
          <Spin />
          <div style={{ marginTop: 12 }}>
            <Text type="secondary">正在打开录制结果…</Text>
          </div>
        </div>
      );
    }
    if (!capabilities.length) {
      return <Empty description={processing ? "正在载入能力草稿…" : "没有生成能力"} />;
    }
    return (
      <Collapse
        defaultActiveKey={[]}
        items={capabilities.map((capability, index) => {
          return {
            key: capability.capability_id || capability.name || String(index),
            label: (
              <Space wrap>
                <Tag color={stageSevenVerified || status === "published" || capability.confirmed ? "success" : "processing"}>
                  {stageSevenVerified || status === "published" ? "已验证" : capability.confirmed ? "已确认" : "分析结果"}
                </Tag>
                <Tag color="blue">{capability.kind || "capability"}</Tag>
                <Text strong>{capability.title || capability.name || `能力 ${index + 1}`}</Text>
                <Text code>{capability.capability_id || capability.name}</Text>
              </Space>
            ),
            children: editingResult
              ? renderCapabilityEditor(capability, index)
              : renderCapabilityResult(capability, index),
          };
        })}
      />
    );
  }

  function renderOperatorQuestion() {
    const question = snapshot?.question;
    if (!question) return null;
    return (
      <Alert
        showIcon
        type="warning"
        style={{ marginBottom: 8 }}
        message="需要你确认"
        description={(
          <Space direction="vertical" style={{ width: "100%" }}>
            <Text>{question.text}</Text>
            {question.options?.length ? (
              <Space wrap>
                {question.options.map((option) => (
                  <Button key={option} type="primary" onClick={() => answerQuestion(option)}>{option}</Button>
                ))}
              </Space>
            ) : (
              <Space.Compact style={{ width: "100%" }}>
                <Input
                  value={answer}
                  onChange={(event) => setAnswer(event.target.value)}
                  onPressEnter={() => answerQuestion()}
                  placeholder="输入答复后回车，或点回复并继续"
                />
                <Button type="primary" onClick={() => answerQuestion()}>回复并继续</Button>
              </Space.Compact>
            )}
          </Space>
        )}
      />
    );
  }

  function renderThoughtBlock(item: ThoughtChunk, index: number) {
    const isToolStart = item.kind === "tool" && item.phase !== "end";
    const isToolEnd = item.kind === "tool" && item.phase === "end";
    const expanded = Boolean(expandedTools[index]);
    const toggleExpand = () => setExpandedTools((prev) => ({ ...prev, [index]: !prev[index] }));

    // ── tool call row ─────────────────────────────────────────────────────────
    if (item.kind === "tool") {
      const isPending = isToolStart;
      const succeeded = isToolEnd && item.ok !== false;
      const failed = isToolEnd && item.ok === false;
      const statusIcon = isPending
        ? <LoadingOutlined style={{ color: "#1677ff", fontSize: 13 }} spin />
        : succeeded
          ? <CheckCircleOutlined style={{ color: "#52c41a", fontSize: 13 }} />
          : <CloseCircleOutlined style={{ color: "#ff4d4f", fontSize: 13 }} />;
      const toolLabel = item.tool || "工具";

      return (
        <div key={`thought-${index}`} style={{ marginLeft: 2 }}>
          <button
            type="button"
            onClick={toggleExpand}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              width: "100%",
              background: "none",
              border: "none",
              cursor: "pointer",
              padding: "3px 0",
              textAlign: "left",
            }}
          >
            <CodeOutlined style={{ color: "#8c8c8c", fontSize: 12, flexShrink: 0 }} />
            <Text code style={{ fontSize: 12 }}>{toolLabel}</Text>
            {statusIcon}
            {isPending && <Text type="secondary" style={{ fontSize: 11 }}>运行中…</Text>}
            {!isPending && (
              <Text type="secondary" style={{ fontSize: 11, marginLeft: 2 }}>
                {expanded ? "▲" : "▼"} 详情
              </Text>
            )}
          </button>
          {expanded && (
            <div style={{
              marginTop: 4,
              marginLeft: 18,
              background: "#fafafa",
              border: "1px solid #f0f0f0",
              borderRadius: 6,
              padding: "8px 10px",
              fontSize: 12,
            }}>
              {item.args ? (
                <div style={{ marginBottom: item.result ? 8 : 0 }}>
                  <Text type="secondary" style={{ fontSize: 11, display: "block", marginBottom: 2 }}>请求参数</Text>
                  <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: 12, lineHeight: 1.5, maxHeight: 180, overflow: "auto" }}>{item.args}</pre>
                </div>
              ) : null}
              {item.result ? (
                <div>
                  <Text type="secondary" style={{ fontSize: 11, display: "block", marginBottom: 2 }}>返回结果</Text>
                  <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: 12, lineHeight: 1.5, maxHeight: 180, overflow: "auto" }}>{item.result}</pre>
                </div>
              ) : isToolStart ? (
                <Text type="secondary" style={{ fontSize: 11 }}>等待返回…</Text>
              ) : null}
            </div>
          )}
        </div>
      );
    }

    // ── thinking ──────────────────────────────────────────────────────────────
    if (item.kind === "thinking") {
      return (
        <div key={`thought-${index}`} style={{ display: "flex", gap: 6, alignItems: "flex-start" }}>
          <RobotOutlined style={{ color: "#d9d9d9", fontSize: 12, marginTop: 3, flexShrink: 0 }} />
          <Text type="secondary" style={{ fontSize: 12, fontStyle: "italic", lineHeight: 1.6, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
            {item.text}
          </Text>
        </div>
      );
    }

    // ── text (model commentary) ───────────────────────────────────────────────
    return (
      <div key={`thought-${index}`} style={{ display: "flex", gap: 6, alignItems: "flex-start" }}>
        <MessageOutlined style={{ color: "#1677ff", fontSize: 12, marginTop: 3, flexShrink: 0 }} />
        <Text style={{ fontSize: 13, lineHeight: 1.65, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
          {item.text}
        </Text>
      </div>
    );
  }

  function renderAnalysisActions(size: "small" | "middle" = "small") {
    if (processing || connecting || cancelling) {
      if (!analysisSessionLive) return null;
      return (
        <Button
          danger
          size={size}
          icon={<StopOutlined />}
          loading={cancelling}
          disabled={!processing && !connecting && !cancelling}
          onClick={cancelProcessing}
        >终止分析</Button>
      );
    }
    if (!draft) return null;
    return (
      <Button
        type="primary"
        size={size}
        icon={<PlayCircleOutlined />}
        onClick={() => startAnalysis()}
      >{verificationButtonLabel}</Button>
    );
  }

  function renderVerificationLog() {
    const activities: WorkflowActivity[] = dedupeAnalysisActivities(snapshot?.activity || []);
    const replaySkipped = looksReplaySkipped(snapshot);

    type TLEntry =
      | { kind: "capability_header"; capability: string }
      | { kind: "activity"; item: WorkflowActivity; idx: number }
      | { kind: "thought"; item: ThoughtChunk; idx: number };

    const entries: TLEntry[] = [];
    let lastCapability = "";
    activities.forEach((item, idx) => {
      if (isAuthNoiseActivity(item) || item.label.includes("发现能力「整体流程」")) return;
      const capability = capabilityTitleOf(item);
      if (capability === "整体流程") {
        entries.push({ kind: "activity", item, idx });
        return;
      }
      if (capability && capability !== lastCapability) {
        entries.push({ kind: "capability_header", capability });
        lastCapability = capability;
      }
      entries.push({ kind: "activity", item, idx });
    });

    for (let i = 0; i < thoughts.length; i++) {
      entries.push({ kind: "thought", item: thoughts[i], idx: i });
    }

    const statusView = analysisStatusView(status, cancelling, snapshot);
    const statusColor = statusView.color;
    const statusLabel = statusView.label;

    // ── Map entries to Ant Timeline items ────────────────────────────────────
    const tlItems = entries.map((entry, ei): NonNullable<React.ComponentProps<typeof Timeline>["items"]>[number] => {
      if (entry.kind === "capability_header") {
        return {
          key: `capability-${entry.capability}-${ei}`,
          dot: (
            <div style={{
              minWidth: 8,
              height: 8,
              borderRadius: "50%",
              background: "#1677ff",
              marginTop: 6,
            }} />
          ),
          children: (
            <Text strong style={{ fontSize: 13 }}>
              {entry.capability}
            </Text>
          ),
        };
      }

      if (entry.kind === "activity") {
        const act = entry.item;
        const display = activityDisplay(act);
        const isResolved = act.status === "resolved" || display.color === "success";
        const isBlocked = act.status === "blocked" || display.color === "error";
        const isWaiting = act.status === "waiting_operator";
        const isRunning = act.status === "running" && display.color === "processing";

        const dotIcon = isResolved
          ? <CheckCircleOutlined style={{ color: "#52c41a", fontSize: 14 }} />
          : isBlocked
            ? <CloseCircleOutlined style={{ color: "#ff4d4f", fontSize: 14 }} />
            : isWaiting
              ? <ExclamationCircleOutlined style={{ color: "#faad14", fontSize: 14 }} />
              : isRunning
                ? <LoadingOutlined style={{ color: "#1677ff", fontSize: 14 }} spin />
                : <ClockCircleOutlined style={{ color: "#8c8c8c", fontSize: 14 }} />;

        return {
          key: `act-${act.sequence}-${ei}`,
          dot: dotIcon,
          children: (
            <div style={{ paddingBottom: 2 }}>
              <Text style={{
                fontSize: 13,
                lineHeight: 1.6,
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                color: isBlocked ? "#cf1322" : isResolved ? "#389e0d" : isWaiting ? "#d46b08" : undefined,
              }}>
                {act.label}
              </Text>
            </div>
          ),
        };
      }

      // thought entry
      const thought = entry.item;
      if (thought.kind === "tool") {
        const isToolStart = thought.phase !== "end";
        const succeeded = !isToolStart && thought.ok !== false;
        const failed = !isToolStart && thought.ok === false;
        const toolIdx = entry.idx;
        const expanded = Boolean(expandedTools[toolIdx]);
        const toggleExpand = () => setExpandedTools((prev) => ({ ...prev, [toolIdx]: !prev[toolIdx] }));

        const dotIcon = isToolStart
          ? <LoadingOutlined style={{ color: "#1677ff", fontSize: 13 }} spin />
          : succeeded
            ? <CheckCircleOutlined style={{ color: "#52c41a", fontSize: 13 }} />
            : <CloseCircleOutlined style={{ color: "#ff4d4f", fontSize: 13 }} />;

        return {
          key: `thought-${toolIdx}-${ei}`,
          dot: dotIcon,
          children: (
            <div>
              <button
                type="button"
                onClick={toggleExpand}
                style={{ background: "none", border: "none", cursor: "pointer", padding: 0, display: "flex", alignItems: "center", gap: 6 }}
              >
                <Text code style={{ fontSize: 12 }}>{thought.tool || "tool"}</Text>
                {isToolStart && <Text type="secondary" style={{ fontSize: 11 }}>运行中…</Text>}
                {!isToolStart && (
                  <Text type="secondary" style={{ fontSize: 11 }}>{expanded ? "▲" : "▼"} 详情</Text>
                )}
              </button>
              {expanded && (
                <div style={{ marginTop: 4, background: "#fafafa", border: "1px solid #f0f0f0", borderRadius: 6, padding: "8px 10px" }}>
                  {thought.args ? (
                    <div style={{ marginBottom: thought.result ? 8 : 0 }}>
                      <Text type="secondary" style={{ fontSize: 11, display: "block", marginBottom: 2 }}>请求参数</Text>
                      <pre style={{ margin: 0, fontSize: 12, lineHeight: 1.5, whiteSpace: "pre-wrap", wordBreak: "break-word", maxHeight: 180, overflow: "auto" }}>{thought.args}</pre>
                    </div>
                  ) : null}
                  {thought.result ? (
                    <div>
                      <Text type="secondary" style={{ fontSize: 11, display: "block", marginBottom: 2 }}>返回结果</Text>
                      <pre style={{ margin: 0, fontSize: 12, lineHeight: 1.5, whiteSpace: "pre-wrap", wordBreak: "break-word", maxHeight: 180, overflow: "auto" }}>{thought.result}</pre>
                    </div>
                  ) : isToolStart ? (
                    <Text type="secondary" style={{ fontSize: 11 }}>等待返回…</Text>
                  ) : null}
                </div>
              )}
            </div>
          ),
        };
      }

      if (thought.kind === "thinking") {
        return {
          key: `thought-${entry.idx}-${ei}`,
          dot: <RobotOutlined style={{ color: "#d9d9d9", fontSize: 12 }} />,
          children: (
            <Text type="secondary" style={{ fontSize: 12, fontStyle: "italic", lineHeight: 1.6, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
              {thought.text}
            </Text>
          ),
        };
      }

      return {
        key: `thought-${entry.idx}-${ei}`,
        dot: <MessageOutlined style={{ color: "#1677ff", fontSize: 12 }} />,
        children: (
          <Text style={{ fontSize: 13, lineHeight: 1.65, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
            {thought.text}
          </Text>
        ),
      };
    });

    // Trailing pulse while processing
    if (processing && !cancelling) {
      tlItems.push({
        key: "pulse",
        dot: <LoadingOutlined style={{ color: "#1677ff", fontSize: 13 }} spin />,
        children: (
          <Text type="secondary" style={{ fontSize: 12 }}>
            {snapshot?.progress.label || "分析中…"}
          </Text>
        ),
      });
    }

    const isEmpty = activities.length === 0 && entries.length === 0 && !processing && !connecting;

    return (
      <Card
        size="small"
        title={(
          <Space size={6}>
            <ThunderboltOutlined style={{ color: processing ? "#1677ff" : "#8c8c8c" }} />
            <Text style={{ fontWeight: 500 }}>实时分析模式</Text>
            <Tag color={statusColor} style={{ fontSize: 11 }}>{statusLabel}</Tag>
            {snapshot?.progress.round && processing
              ? <Text type="secondary" style={{ fontSize: 11 }}>第 {snapshot.progress.round} 轮</Text>
              : null}
          </Space>
        )}
        extra={renderAnalysisActions("small")}
        style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column" }}
        styles={{
          body: {
            flex: 1,
            minHeight: 0,
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
            padding: "8px 12px",
          },
        }}
      >
        {status === "cancelled" ? (
          <Alert
            showIcon
            type="warning"
            style={{ marginBottom: 8 }}
            message="分析已终止"
            description="草稿已保留，确认后可继续机器验证。"
          />
        ) : null}
        {replaySkipped && status !== "cancelled" ? (
          <Alert
            showIcon
            type="warning"
            style={{ marginBottom: 8 }}
            message="已跳过回放取证"
            description="录制浏览器登录态已过期，不影响已产出的 Skill。分析会继续处理能力；运行期调用使用独立凭证。"
          />
        ) : null}
        {status === "failed" && snapshot?.error ? (
          <Alert
            showIcon
            type="error"
            style={{ marginBottom: 8 }}
            message="分析失败"
            description={snapshot.error}
          />
        ) : null}
        {renderOperatorQuestion()}
        <div ref={verificationLogRef} style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
          {isEmpty && status !== "cancelled" && !replaySkipped ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={
                connecting || runBusy
                  ? "正在连接…"
                  : "尚未开始机器验证，确认能力后点击开始机器验证"
              }
              style={{ marginTop: 24 }}
            />
          ) : (
            <>
              {tlItems.length ? (
                <Timeline
                  style={{ paddingTop: 8 }}
                  items={tlItems}
                />
              ) : status === "cancelled" ? null : processing ? (
                <Text type="secondary" style={{ display: "block", padding: "8px 0 4px" }}>
                  {snapshot?.progress.label || "正在启动机器验证"}
                </Text>
              ) : null}
            </>
          )}
        </div>
      </Card>
    );
  }

  function renderResult() {
    return (
      <Card>
        {analysisMode ? (
          <div style={RESULT_STATUS_BOX_STYLE}>
            {renderVerificationLog()}
          </div>
        ) : null}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, margin: "16px 0" }}>
          <Space>
            <Text strong>{editingResult ? "修改能力结果" : `能力结果 ${openingId ? "…" : capabilities.length}`}</Text>
            {editingResult ? <Text type="secondary">仅修改识别错误的内容，字段路径和能力标识保持稳定</Text> : null}
            {pendingEdits.length ? <Tag color="processing">待保存修改 {pendingEdits.length}</Tag> : null}
          </Space>
          {editingResult ? (
            <Space>
              <Button disabled={Boolean(patchInFlightRef.current) || processing} onClick={cancelResultEditing}>取消修改</Button>
              <Button
                type="primary"
                loading={processing || Boolean(patchInFlightRef.current)}
                disabled={!draft || processing || (status === "published" && !pendingEdits.length)}
                onClick={republish}
                >保存并重新验证</Button>
            </Space>
          ) : (
            <Space>
              {!stageSevenVerified || !fingerprintMatches ? (
                draft && !analysisMode && !processing && !connecting && !cancelling ? (
                  <Button
                    type="primary"
                    icon={<PlayCircleOutlined />}
                    onClick={() => startAnalysis()}
                  >{verificationButtonLabel}</Button>
                ) : null
              ) : (
                <>
                  <Button
                    disabled={!draft || processing || skillExporting}
                    onClick={() => setEditingResult(true)}
                  >修改能力结果</Button>
                  <Button
                    type="primary"
                    disabled={!canProduceSkill}
                    onClick={openSkillExport}
                  >产出 Skill</Button>
                </>
              )}
            </Space>
          )}
        </div>
        {renderCapabilities()}
        {resultMeta?.skill_lifecycle === "exported" || skillExportOutcome?.status === "exported" ? (
          <Alert
            style={{ marginTop: 16 }}
            type="success"
            showIcon
            message="Skill 已导出"
            description={
              <Space direction="vertical" size={4}>
                <Text>Skill ID：{skillExportOutcome?.skill_id || resultMeta?.skill_id || "—"}</Text>
                <Text>导出路径：{skillExportOutcome?.export_path || resultMeta?.skill_export_path || "—"}</Text>
                <Text>版本：{skillExportOutcome?.version || resultMeta?.skill_version || "—"}</Text>
                <Space>
                  <Button size="small" icon={<CopyOutlined />} onClick={() => copySkillDir(skillExportOutcome?.export_path || resultMeta?.skill_export_path)}>打开 Skill 目录</Button>
                  <Button size="small" onClick={openSkillExport} disabled={!canProduceSkill}>重新产出 Skill</Button>
                </Space>
              </Space>
            }
          />
        ) : null}
        <Collapse
          style={{ marginTop: 16 }}
          items={[{
            key: "technical",
            label: "技术详情",
            children: (
              <Tabs
                items={[
                  {
                    key: "requests",
                    label: `捕获接口 ${capturedRequests.length}`,
                    children: capturedRequests.length ? (
                      <List
                        bordered
                        dataSource={capturedRequests}
                        renderItem={(item, index) => (
                          <List.Item>
                            <Space><Tag>{String(item.method || "")}</Tag><Text>{String(item.path || item.url || `请求 ${index + 1}`)}</Text></Space>
                          </List.Item>
                        )}
                      />
                    ) : <Empty description="没有捕获接口" />,
                  },
                  {
                    key: "json",
                    label: "FlowSpec JSON",
                    children: <pre style={{ whiteSpace: "pre-wrap", overflow: "auto", maxHeight: "65vh" }}>{JSON.stringify(draft, null, 2)}</pre>,
                  },
                ]}
              />
            ),
          }]}
        />
      </Card>
    );
  }

  const assistantBody = (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      <Alert
        showIcon
        type={status === "failed" ? "error" : status === "waiting_operator" ? "warning" : "info"}
        message={snapshot?.progress.label || STATUS_LABELS[status]}
        description={processing && snapshot?.progress.round
          ? `自动处理第 ${snapshot.progress.round} 轮`
          : undefined}
      />
      {snapshot?.question ? (
        <Card size="small" title="需要你确认">
          <Space direction="vertical" style={{ width: "100%" }}>
            <Text>{snapshot.question.text}</Text>
            {snapshot.question.options?.length ? (
              <Space wrap>
                {snapshot.question.options.map((option) => (
                  <Button key={option} onClick={() => answerQuestion(option)}>{option}</Button>
                ))}
              </Space>
            ) : (
              <Space.Compact style={{ width: "100%" }}>
                <Input value={answer} onChange={(event) => setAnswer(event.target.value)} onPressEnter={() => answerQuestion()} />
                <Button type="primary" onClick={() => answerQuestion()}>继续处理</Button>
              </Space.Compact>
            )}
          </Space>
        </Card>
      ) : null}
      {(snapshot?.activity || []).length ? (
        <Card size="small" title="处理进展">
          <Timeline
            items={dedupeAnalysisActivities(snapshot?.activity || []).map((item, index) => {
              const display = activityDisplay(item);
              return {
                key: `assist-act-${item.sequence}-${index}`,
                color: display.color === "error" ? "red" : display.color === "success" ? "green" : display.color === "warning" ? "orange" : "blue",
                children: <Text>{item.label}</Text>,
              };
            })}
          />
        </Card>
      ) : null}
      {(snapshot?.insights || []).length ? (
        <Card size="small" title="实时分析候选" styles={{ body: { padding: 0 } }}>
          <List
            size="small"
            dataSource={snapshot?.insights || []}
            renderItem={(item) => (
              <List.Item>
                <Space align="start">
                  <Tag>{String(item.kind || "分析")}</Tag>
                  <Text>{String(item.text || item.reason || JSON.stringify(item))}</Text>
                </Space>
              </List.Item>
            )}
          />
        </Card>
      ) : (snapshot?.activity || []).length || snapshot?.question
        ? null
        : <Empty description={status === "recording" ? "捕获到业务事实后显示分析结论" : "暂无分析结论"} />}
    </Space>
  );

  return (
    <div style={{ width: "100%", height: "100%", minWidth: 0, minHeight: 0, padding: "8px 12px", boxSizing: "border-box", display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <Steps
        current={viewStage}
        onChange={(next) => {
          if (next <= (keepResult ? 2 : keepRecording ? 1 : 0)) setViewStage(next);
        }}
        items={[
          {
            title: "录制准备",
            status: viewStage === 0 ? "process" : "finish",
          },
          {
            title: "页面录制",
            disabled: !keepRecording,
            status: viewStage === 1 ? "process" : keepRecording ? "finish" : "wait",
          },
          {
            title: "能力结果",
            disabled: !keepResult,
            status: viewStage === 2 ? "process" : keepResult ? "finish" : "wait",
          },
        ]}
        style={{ maxWidth: 980, margin: "0 auto 12px", flexShrink: 0 }}
      />
      <div style={{ display: viewStage === 0 ? "block" : "none", flex: 1, minHeight: 0, overflow: "auto" }}>{renderSetup()}</div>
      {keepRecording ? <div style={{ display: viewStage === 1 ? "flex" : "none", flexDirection: "column", flex: 1, minHeight: 0, overflow: "hidden" }}>{renderRecording()}</div> : null}
      {keepResult ? <div style={{ display: viewStage === 2 ? "block" : "none", flex: 1, minHeight: 0, overflow: "auto" }}>{renderResult()}</div> : null}
      <Modal
        title={skillExportOutcome?.status === "exported" ? "Skill 已导出" : "配置并导出 Skill"}
        open={skillExportOpen}
        onCancel={() => {
          if (!skillExporting) setSkillExportOpen(false);
        }}
        width={760}
        destroyOnClose={false}
        footer={
          skillExportOutcome?.status === "exported" ? (
            <Space>
              <Button icon={<CopyOutlined />} onClick={() => copySkillDir()}>打开 Skill 目录</Button>
              <Button onClick={() => { setSkillExportOutcome(null); setSkillClarifications([]); setSkillExportErrors([]); }}>重新产出 Skill</Button>
              <Button type="primary" onClick={() => setSkillExportOpen(false)}>完成</Button>
            </Space>
          ) : (
            <Space>
              <Button disabled={skillExporting} onClick={() => setSkillExportOpen(false)}>取消</Button>
              <Button type="primary" loading={skillExporting} disabled={skillExporting} onClick={() => void submitSkillExport()}>导出 Skill</Button>
            </Space>
          )
        }
      >
        {skillExportOutcome?.status === "exported" ? (
          <Space direction="vertical" size={8} style={{ width: "100%" }}>
            <Text>Skill ID：{skillExportOutcome.skill_id || "—"}</Text>
            <Text>Skill 名称：{skillExportOutcome.skill_name || skillTitle || "—"}</Text>
            <Text>规划方式：{skillExportOutcome.planning_mode === "fixed" ? "固定业务步骤" : "按用户需求动态选择"}</Text>
            <Text>使用的能力：{(skillExportOutcome.used_capabilities || []).map((item) => String(item.title || item.name || item.capability_id || "")).filter(Boolean).join("、") || "—"}</Text>
            <div>
              <Text>未使用的能力：</Text>
              {(skillExportOutcome.unused_capabilities || []).length ? (
                <List
                  size="small"
                  dataSource={skillExportOutcome.unused_capabilities || []}
                  renderItem={(item) => (
                    <List.Item>
                      <Text>{String(item.title || item.name || item.capability_id || "")}</Text>
                      <Text type="secondary">{String(item.reason || "")}</Text>
                    </List.Item>
                  )}
                />
              ) : <Text type="secondary">无</Text>}
            </div>
            <div>
              <Text>有效调用路线：</Text>
              <List
                size="small"
                dataSource={skillExportOutcome.routes || []}
                renderItem={(route) => (
                  <List.Item>
                    <Space direction="vertical" size={0}>
                      <Text>{String(route.name || route.route_id || "")}</Text>
                      <Text type="secondary">{Array.isArray(route.capability_sequence) ? route.capability_sequence.join(" → ") : ""}</Text>
                    </Space>
                  </List.Item>
                )}
              />
            </div>
            <Text>导出路径：{skillExportOutcome.export_path || "—"}</Text>
            <Text>Skill 版本：{skillExportOutcome.version || 1}</Text>
          </Space>
        ) : (
          <Space direction="vertical" size={12} style={{ width: "100%" }}>
            {skillExporting ? <Alert type="info" showIcon message={skillExportProgress || "正在规划和导出 Skill…"} /> : null}
            {skillClarifications.length ? (
              <Alert
                type="warning"
                showIcon
                message="需要补充说明"
                description={(
                  <List size="small" dataSource={skillClarifications} renderItem={(item) => <List.Item>{item}</List.Item>} />
                )}
              />
            ) : null}
            {skillExportErrors.length ? (
              <Alert
                type="error"
                showIcon
                message="导出失败"
                description={(
                  <List size="small" dataSource={skillExportErrors} renderItem={(item) => <List.Item>{item}</List.Item>} />
                )}
              />
            ) : null}
            <div>
              <Text strong>Skill 显示名称</Text>
              <Input
                style={{ marginTop: 6 }}
                value={skillTitle}
                onChange={(event) => setSkillTitle(event.target.value)}
                placeholder="例如：请假办理"
                disabled={skillExporting}
              />
            </div>
            <div>
              <Text strong>业务描述</Text>
              <Input.TextArea
                style={{ marginTop: 6 }}
                value={skillDescription}
                onChange={(event) => setSkillDescription(event.target.value)}
                autoSize={{ minRows: 6, maxRows: 12 }}
                disabled={skillExporting}
                placeholder="请描述这个页面在业务上用来做什么、用户通常会提出什么要求、哪些操作需要组合、什么结果代表完成。例如：用户可以查询待办记录，也可以查询后选择一条记录进行提交；如果用户只要求查询，不要执行提交。"
              />
            </div>
            <div>
              <Text strong>规划方式</Text>
              <Radio.Group
                style={{ display: "block", marginTop: 8 }}
                value={skillPlanningMode}
                onChange={(event) => setSkillPlanningMode(event.target.value)}
                disabled={skillExporting}
              >
                <Space direction="vertical">
                  <Radio value="dynamic">
                    <Space direction="vertical" size={0}>
                      <Text>按用户需求动态选择（推荐）</Text>
                      <Text type="secondary">Skill 在实际使用时，根据用户请求从有效能力组合中选择最少且足够的能力。适用于页面具有查询、选项、提交等多种能力，用户每次需求不同的情况。</Text>
                    </Space>
                  </Radio>
                  <Radio value="fixed">
                    <Space direction="vertical" size={0}>
                      <Text>固定业务步骤</Text>
                      <Text type="secondary">根据当前业务描述生成一条确定的主要调用顺序。适用于该页面始终按照固定步骤完成同一业务的情况。</Text>
                    </Space>
                  </Radio>
                </Space>
              </Radio.Group>
            </div>
            <Collapse
              items={[{
                key: "more",
                label: "更多设置",
                children: (
                  <Space direction="vertical" size={12} style={{ width: "100%" }}>
                    <div>
                      <Text>用户请求示例，一行一个</Text>
                      <Input.TextArea
                        style={{ marginTop: 6 }}
                        value={skillExamples}
                        onChange={(event) => setSkillExamples(event.target.value)}
                        autoSize={{ minRows: 3, maxRows: 8 }}
                        disabled={skillExporting}
                      />
                    </div>
                    <div>
                      <Text>成功条件</Text>
                      <Input.TextArea
                        style={{ marginTop: 6 }}
                        value={skillSuccess}
                        onChange={(event) => setSkillSuccess(event.target.value)}
                        autoSize={{ minRows: 2, maxRows: 5 }}
                        disabled={skillExporting}
                      />
                    </div>
                    <div>
                      <Text>禁止或限制的操作</Text>
                      <Input.TextArea
                        style={{ marginTop: 6 }}
                        value={skillForbidden}
                        onChange={(event) => setSkillForbidden(event.target.value)}
                        autoSize={{ minRows: 2, maxRows: 5 }}
                        disabled={skillExporting}
                      />
                    </div>
                    <div>
                      <Text>导出目录</Text>
                      <Input
                        style={{ marginTop: 6 }}
                        value={skillOutDir}
                        onChange={(event) => setSkillOutDir(event.target.value)}
                        placeholder="默认使用系统已记忆的导出目录"
                        disabled={skillExporting}
                      />
                    </div>
                  </Space>
                ),
              }]}
            />
            <div>
              <Text strong>已验证能力</Text>
              <Table
                size="small"
                style={{ marginTop: 8 }}
                pagination={false}
                rowKey={(row) => String(row.capability_id || row.name)}
                dataSource={capabilities}
                columns={[
                  { title: "能力名称", render: (_, cap) => cap.title || cap.name || cap.capability_id },
                  { title: "类型", width: 110, render: (_, cap) => cap.kind || "—" },
                  { title: "输入字段", render: (_, cap) => schemaFieldNames(cap.input_schema).join("、") || "—" },
                  { title: "输出字段", render: (_, cap) => schemaFieldNames(cap.output_schema).join("、") || "—" },
                  { title: "写操作", width: 80, render: (_, cap) => (capabilityIsWrite(cap) ? "是" : "否") },
                  { title: "需确认", width: 80, render: (_, cap) => (cap.requires_human_confirm || capabilityIsWrite(cap) ? "是" : "否") },
                  { title: "验证状态", width: 90, render: () => (stageSevenVerified ? "已验证" : stageSevenStatus || "未验证") },
                ]}
              />
            </div>
          </Space>
        )}
      </Modal>
      <Drawer
        title="录制助手"
        placement="right"
        width={440}
        open={assistantOpen}
        onClose={() => setAssistantOpen(false)}
        destroyOnClose={false}
      >
        {assistantBody}
      </Drawer>
    </div>
  );
}
