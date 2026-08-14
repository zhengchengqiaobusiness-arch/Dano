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
  Select,
  Space,
  Steps,
  Tabs,
  Tag,
  Typography,
  message,
} from "antd";
import { RobotOutlined, StopOutlined } from "@ant-design/icons";
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
  question?: WorkflowQuestion | null;
  release?: Record<string, unknown> | null;
  error?: string;
}

interface FlowParam {
  field_id?: string;
  path: string;
  key: string;
  label?: string;
  value?: unknown;
  default_value?: unknown;
  type?: string;
  category?: string;
  source_kind?: string;
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
}

interface FlowSpec {
  flow_id?: string;
  title?: string;
  steps?: FlowStep[];
  links?: Array<Record<string, unknown>>;
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

const TYPE_OPTIONS = [
  "string", "number", "boolean", "date", "datetime", "enum", "list-enum", "object", "array",
].map((value) => ({ value, label: value }));

const CATEGORY_OPTIONS = [
  { value: "user_param", label: "用户参数" },
  { value: "runtime_var", label: "运行期变量" },
  { value: "system_const", label: "系统常量" },
];

const SOURCE_OPTIONS = [
  ["unknown", "来源未确认"],
  ["user_input", "用户输入"],
  ["api_option", "接口候选"],
  ["selected_option_field", "候选关联字段"],
  ["page_enum", "页面枚举"],
  ["form_option", "表单选项"],
  ["static_enum", "静态枚举"],
  ["manual_enum", "人工枚举"],
  ["previous_response", "上游响应"],
  ["request_header", "请求头"],
  ["current_user", "当前用户"],
  ["system_time", "系统时间"],
  ["system_generated", "系统生成"],
  ["computed", "系统计算"],
  ["page_context", "调用上下文"],
  ["constant", "固定值"],
].map(([value, label]) => ({ value, label }));

const TERMINAL_STATUSES = new Set<WorkflowStatus>([
  "editable", "published", "cancelled", "failed",
]);

const STATUS_LABELS: Record<WorkflowStatus, string> = {
  idle: "等待开始",
  recording: "录制中",
  processing: "分析中",
  waiting_operator: "等待确认",
  editable: "能力草稿待处理",
  published: "发布完成",
  cancelled: "分析已终止",
  failed: "处理失败",
};

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
      goalText: typeof parsed.goalText === "string" ? parsed.goalText : "",
    };
  } catch {
    return { startUrl: "", goalText: "" };
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

function issueType(status: WorkflowStatus): "success" | "warning" | "error" | "info" {
  if (status === "published") return "success";
  if (status === "failed") return "error";
  if (status === "editable") return "warning";
  return "info";
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
  const [title, setTitle] = useState("");
  const [snapshot, setSnapshot] = useState<WorkflowSnapshot | null>(null);
  const [visibleStage, setVisibleStage] = useState(0);
  const [connected, setConnected] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [answer, setAnswer] = useState("");
  const [requests, setRequests] = useState<Array<Record<string, unknown>>>([]);
  const [hasFrame, setHasFrame] = useState(false);
  const [frameMeta, setFrameMeta] = useState<FrameMeta>({ width: 1280, height: 800 });
  const [pendingEdits, setPendingEdits] = useState<DraftEdit[]>([]);
  const [localValues, setLocalValues] = useState<Record<string, unknown>>({});
  const [localCapabilityStepIds, setLocalCapabilityStepIds] = useState<Record<string, string[]>>({});

  const wsRef = useRef<WebSocket | null>(null);
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
  const republishAfterRevisionRef = useRef<number | null>(null);

  const status = snapshot?.status || "idle";
  const processing = status === "processing" || status === "waiting_operator";
  const draft = snapshot?.draft || null;
  const capabilities = draft?.capabilities || [];
  const steps = draft?.steps || [];
  const capturedRequests = draft?.request_facts?.requests || requests;

  useEffect(() => {
    sessionStorage.setItem("dano.recording.setup", JSON.stringify({ startUrl, goalText }));
  }, [startUrl, goalText]);

  useEffect(() => () => {
    frameGenerationRef.current += 1;
    if (pointerTimerRef.current !== null) window.clearTimeout(pointerTimerRef.current);
    if (wheelTimerRef.current !== null) window.clearTimeout(wheelTimerRef.current);
    const socket = wsRef.current;
    wsRef.current = null;
    if (socket && socket.readyState < WebSocket.CLOSING) socket.close(1000, "page closed");
  }, []);

  function send(payload: Record<string, unknown>) {
    const socket = wsRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      message.error("录制连接不可用");
      return false;
    }
    socket.send(JSON.stringify(payload));
    return true;
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
      setFrameMeta({ width, height });
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
    setSnapshot((current) => {
      if (current && next.revision < current.revision) return current;
      return next;
    });
    actionRef.current = next.action;
    if (next.title !== undefined) setTitle(next.title);
    if (next.status === "waiting_operator") setAssistantOpen(true);
    if (TERMINAL_STATUSES.has(next.status)) setVisibleStage(2);
    else if (next.status !== "idle") setVisibleStage(1);

    const patchRevision = republishAfterRevisionRef.current;
    if (patchRevision !== null && next.revision > patchRevision && next.status === "editable") {
      republishAfterRevisionRef.current = null;
      setPendingEdits([]);
      setLocalValues({});
      setLocalCapabilityStepIds({});
      send({ type: "republish", title: next.title || title });
    }
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
    if (wsRef.current) return;
    const action = newActionName();
    actionRef.current = action;
    setConnecting(true);
    setRequests([]);
    setSnapshot(null);
    setPendingEdits([]);
    setLocalValues({});
    setLocalCapabilityStepIds({});
    setHasFrame(false);
    setTitle("");
    setVisibleStage(1);
    renderedFrameRef.current = 0;
    latestFrameRef.current = null;
    frameGenerationRef.current += 1;

    const socket = new WebSocket(recorderWebSocketUrl());
    wsRef.current = socket;
    socket.onopen = () => {
      setConnected(true);
      setConnecting(false);
      socket.send(JSON.stringify({
        type: "start",
        tenant,
        subsystem,
        start_url: startUrl.trim(),
        goal_text: goalText.trim(),
        base_url: baseUrl.trim() || undefined,
        storage_state: parseStorageState(storageState),
        resume_action: action,
      }));
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
      } else if (incoming.type === "frame") {
        queueFrame(incoming);
      } else if (incoming.type === "request" && incoming.request) {
        setRequests((current) => [...current.slice(-199), incoming.request as Record<string, unknown>]);
      } else if (incoming.type === "input_error") {
        message.warning(String(incoming.detail || "页面操作没有执行"));
      } else if (incoming.type === "error") {
        republishAfterRevisionRef.current = null;
        message.error(String(incoming.detail || "录制处理失败"));
      }
    };
    socket.onerror = () => message.error("无法连接录制服务");
    socket.onclose = () => {
      if (wsRef.current === socket) wsRef.current = null;
      setConnected(false);
      setConnecting(false);
    };
  }

  function finishRecording() {
    setAssistantOpen(true);
    send({ type: "finish", title: title.trim() });
  }

  function cancelProcessing() {
    send({ type: "cancel" });
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
    setPendingEdits((current) => [
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
    setPendingEdits((current) => [
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
    setPendingEdits((current) => [
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
    if (pendingEdits.length) {
      republishAfterRevisionRef.current = snapshot.revision;
      send({
        type: "patch_draft",
        edits: pendingEdits,
        expected_revision: snapshot.revision,
        expected_fingerprint: snapshot.draft_fingerprint,
      });
      return;
    }
    send({ type: "republish", title: title.trim() });
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
      <Card>
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          <label>
            <Text strong><Text type="danger">* </Text>业务页地址</Text>
            <Input
              value={startUrl}
              onChange={(event) => setStartUrl(event.target.value)}
              placeholder="https://example.com/business/page"
              style={{ marginTop: 8 }}
            />
          </label>
          <label>
            <Text strong><Text type="danger">* </Text>录制目标</Text>
            <Input.TextArea
              value={goalText}
              onChange={(event) => setGoalText(event.target.value)}
              placeholder="完整描述需要在页面完成的业务目标"
              autoSize={{ minRows: 3, maxRows: 5 }}
              style={{ marginTop: 8 }}
            />
          </label>
          <div style={{ display: "flex", justifyContent: "flex-end" }}>
            <Button type="primary" loading={connecting} onClick={startRecording}>开始录制</Button>
          </div>
        </Space>
      </Card>
    );
  }

  function renderRecording() {
    return (
      <div style={{ minWidth: 0 }}>
        <Card size="small" styles={{ body: { padding: 10 } }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0, flexWrap: "nowrap" }}>
            <Tag color={status === "recording" ? "processing" : processing ? "blue" : "default"}>
              {STATUS_LABELS[status]}
            </Tag>
            <Button disabled={status !== "recording"}>从这里开始录</Button>
            <Text strong style={{ whiteSpace: "nowrap" }}>动作名：</Text>
            <Input value={snapshot?.action || actionRef.current} readOnly style={{ minWidth: 230, flex: 1 }} />
            <Text strong style={{ whiteSpace: "nowrap" }}>标题：</Text>
            <Input value={title} onChange={(event) => setTitle(event.target.value)} style={{ minWidth: 150, flex: 0.6 }} />
            {status === "recording" ? (
              <Button type="primary" onClick={finishRecording}>停止并分析请求</Button>
            ) : processing ? (
              <Button danger icon={<StopOutlined />} onClick={cancelProcessing}>一键终止</Button>
            ) : null}
            <Button icon={<RobotOutlined />} onClick={() => setAssistantOpen(true)}>录制助手</Button>
          </div>
        </Card>
        <div
          style={{
            position: "relative",
            marginTop: 10,
            width: "100%",
            aspectRatio: `${frameMeta.width} / ${frameMeta.height}`,
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
          {!hasFrame ? <Empty description={connecting ? "正在连接业务页面" : "等待页面画面"} style={{ paddingTop: 150 }} /> : null}
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
        <Text type="secondary">画面 {frameMeta.width}×{frameMeta.height} · 已捕获 {snapshot?.progress.request_count || requests.length} 个请求</Text>
      </div>
    );
  }

  function renderParamEditor(step: FlowStep, param: FlowParam) {
    return (
      <div
        key={`${step.step_id}:${param.field_id || param.path}`}
        style={{
          display: "grid",
          gridTemplateColumns: "1.1fr 1.2fr 1fr 0.9fr 1.1fr 1.2fr 80px",
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
        <Input
          value={safeString(paramValue(step, param, "path"))}
          onChange={(event) => updateParam(step, param, "path", event.target.value)}
          aria-label="字段路径"
        />
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
          value={safeString(paramValue(step, param, "category") || "user_param")}
          options={CATEGORY_OPTIONS}
          onChange={(value) => updateParam(step, param, "category", value)}
          aria-label="字段分类"
        />
        <Select
          value={safeString(paramValue(step, param, "source_kind") || "unknown")}
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

  function renderCapabilities() {
    if (!capabilities.length) return <Empty description="没有生成能力" />;
    return (
      <Collapse
        items={capabilities.map((capability, index) => {
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
          return {
            key: capability.capability_id || capability.name || String(index),
            label: (
              <Space wrap>
                <Tag color={capability.confirmed ? "success" : "processing"}>{capability.confirmed ? "已确认" : "模型建议"}</Tag>
                <Tag color="blue">{capability.kind || "capability"}</Tag>
                <Text strong>{capability.title || capability.name || `能力 ${index + 1}`}</Text>
                <Text code>{capability.name}</Text>
              </Space>
            ),
            children: (
              <Space direction="vertical" size={12} style={{ width: "100%" }}>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 2fr", gap: 10 }}>
                  <Input
                    addonBefore="名称"
                    value={capabilityValue(capability, "title")}
                    onChange={(event) => updateCapability(capability, "title", event.target.value)}
                  />
                  <Input
                    addonBefore="标识"
                    value={capabilityValue(capability, "name")}
                    onChange={(event) => updateCapability(capability, "name", event.target.value)}
                  />
                  <Input
                    addonBefore="用途"
                    value={capabilityValue(capability, "intent")}
                    onChange={(event) => updateCapability(capability, "intent", event.target.value)}
                  />
                </div>
                <Space wrap>
                  <Text type="secondary">接口 {stepIds.length + auxiliaryStepIds.size}</Text>
                  <Select
                    value={undefined}
                    placeholder="添加执行接口"
                    style={{ minWidth: 260 }}
                    options={unusedSteps.map((step) => ({
                      value: step.step_id,
                      label: `${step.method || "HTTP"} ${step.path || step.url || step.name || step.step_id}`,
                    }))}
                    onChange={(stepId) => addStepToCapability(index, stepId)}
                  />
                </Space>
                {capabilitySteps.map((step, stepIndex) => (
                  <Card
                    key={step.step_id}
                    size="small"
                    title={<Space><Tag color="blue">{capabilityUsageLabel("execute")}</Tag><Tag color={step.method === "GET" ? "blue" : "green"}>{step.method}</Tag><Text>{step.name}</Text><Text code>{step.path || step.url}</Text></Space>}
                    extra={<Space>
                      <Button size="small" disabled={stepIndex === 0} onClick={() => moveStepInCapability(index, step.step_id, -1)}>上移</Button>
                      <Button size="small" disabled={stepIndex === capabilitySteps.length - 1} onClick={() => moveStepInCapability(index, step.step_id, 1)}>下移</Button>
                      <Button size="small" danger onClick={() => removeStepFromCapability(index, step.step_id)}>移除</Button>
                    </Space>}
                  >
                    <div
                      style={{
                        display: "grid",
                        gridTemplateColumns: "1.1fr 1.2fr 1fr 0.9fr 1.1fr 1.2fr 80px",
                        gap: 8,
                        color: "#8c8c8c",
                        paddingBottom: 6,
                      }}
                    >
                      <span>名称</span><span>路径</span><span>默认值</span><span>类型</span>
                      <span>分类</span><span>来源</span><span>必填性</span>
                    </div>
                    {(step.params || []).map((param) => renderParamEditor(step, param))}
                  </Card>
                ))}
                {auxiliaryRefs.map((ref) => {
                  const step = stepById.get(String(ref.step_id));
                  if (!step) return null;
                  return (
                    <Card
                      key={`${ref.usage || "auxiliary"}:${step.step_id}`}
                      size="small"
                      title={<Space><Tag color="purple">{capabilityUsageLabel(ref.usage)}</Tag><Tag>{step.method}</Tag><Text>{step.name}</Text><Text code>{step.path || step.url}</Text></Space>}
                    >
                      <Text type="secondary">该接口由能力编排引用，不作为主执行步骤重复执行。</Text>
                    </Card>
                  );
                })}
                {capability.dependencies?.length ? (
                  <Card size="small" title={`依赖 ${capability.dependencies.length}`}>
                    <pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>{JSON.stringify(capability.dependencies, null, 2)}</pre>
                  </Card>
                ) : null}
              </Space>
            ),
          };
        })}
      />
    );
  }

  function renderResult() {
    const description = (
      <Space direction="vertical" size={4}>
        <Text>{snapshot?.progress.label || STATUS_LABELS[status]}</Text>
        {snapshot?.error ? <Text type="danger">{snapshot.error}</Text> : null}
        {(snapshot?.issues || []).map((issue) => (
          <Text key={issue.issue_id} type={issue.severity === "blocking" ? "danger" : "warning"}>
            {issue.message}
          </Text>
        ))}
        {status === "published" && snapshot?.release ? (
          <Text type="success">能力已验证并发布；Skill 导出仅包含本次动作的发布结果。</Text>
        ) : null}
        {status === "published" && snapshot?.release?.lifecycle_pending ? (
          <Text type="warning">
            {String(snapshot.release.lifecycle_message || "资产已发布，生命周期登记待补偿")}
          </Text>
        ) : null}
      </Space>
    );
    return (
      <Card>
        <Alert
          showIcon
          type={issueType(status)}
          message={STATUS_LABELS[status]}
          description={description}
        />
        <div style={{ display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 12, margin: "16px 0" }}>
          {pendingEdits.length ? <Tag color="processing">待保存修改 {pendingEdits.length}</Tag> : null}
          <Button
            type="primary"
            loading={processing}
            disabled={!draft || processing}
            onClick={republish}
          >修改后再次发布</Button>
        </div>
        <Tabs
          items={[
            { key: "capabilities", label: `能力列表 ${capabilities.length}`, children: renderCapabilities() },
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
              label: "高级 JSON",
              children: <pre style={{ whiteSpace: "pre-wrap", overflow: "auto", maxHeight: "65vh" }}>{JSON.stringify(draft, null, 2)}</pre>,
            },
          ]}
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
      {(snapshot?.insights || []).length ? (
        <List
          size="small"
          bordered
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
      ) : <Empty description={status === "recording" ? "捕获到业务事实后显示分析结论" : "暂无分析结论"} />}
    </Space>
  );

  return (
    <div style={{ width: "100%", minWidth: 0, padding: "12px 18px 18px", boxSizing: "border-box" }}>
      <Steps
        current={visibleStage}
        onChange={(stage) => {
          if (stage === 0 || (stage === 1 && snapshot) || (stage === 2 && draft && TERMINAL_STATUSES.has(status))) {
            setVisibleStage(stage);
          }
        }}
        items={[{ title: "录制准备" }, { title: "页面录制" }, { title: "能力结果" }]}
        style={{ maxWidth: 980, margin: "0 auto 18px" }}
      />
      {visibleStage === 0 ? renderSetup() : visibleStage === 1 ? renderRecording() : renderResult()}
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
