import { useEffect, useRef, useState } from "react";
import { Card, Form, Input, Button, Space, Typography, Alert, Tag, List, Checkbox, Collapse, Switch, message, Select } from "antd";
import { useNavigate } from "react-router-dom";

// 方式B:网页内录制。连 WebSocket → 后端托管浏览器,画面投到这里,点击/键盘回传,实时显示捕获的步骤。
// 客户全程免安装、免命令行。

interface RecStep { op: string; locator?: string; field?: string; value?: string }
interface RecReq { method: string; url: string; has_body?: boolean; json?: boolean }
// 提交请求体拍平后的一个叶子字段(给用户勾选哪些是参数)
interface RecField { path: string; key: string; value: string; suggest_param: boolean; suggest_name: string;
  type?: string; required?: boolean; confidence?: number; confidence_tier?: string; name_source?: string;
  system_value?: boolean }
// 候选写请求(抓到多个时让用户手选用哪个)
interface RecCand { idx: number; method: string; path: string }
// P3:字段=选自某列表(选领导:名字→ID)/ 字段=当前用户·会话值(运行期重取)
interface RecSelect { path: string; source_url: string; value_key: string; label_key: string; label: string; count: number; multi?: boolean; dom_options?: boolean }
interface RecIdentity { path: string; source: string }
// Step B/C/D: FlowSpec 编辑模型(前端只消费,通过 flow_update 同步后端)
interface FlowParam {
  path: string; key: string; label?: string; value: string; type: string; required: boolean; name_source?: string;
  category?: string; source_kind?: string; reason?: string; exposed_to_user?: boolean; need_human_confirm?: boolean; editable?: boolean;
}
interface FlowStepData {
  step_id: string; name: string; method: string; url: string; path: string; risk_level: string; params: FlowParam[];
  source_meta?: { role?: string; [key: string]: any }; semantic_role?: string;
}
interface FlowLinkData { link_id: string; source_step_id: string; source_path: string; target_step_id: string; target_path: string; confirmed?: boolean; confidence?: number }
interface RequestRoleData {
  index?: number; method: string; path: string; role: string; keep: boolean; reason: string; confidence?: number;
}
interface ReviewItemData {
  id: string; type: string; severity: string; title: string; reason: string; current_guess?: string; suggested_action?: string;
  resolved?: boolean; confidence?: number; target?: { kind?: string; step_id?: string; path?: string; link_id?: string; [key: string]: any };
}
interface FlowSpecData {
  flow_id: string; title: string; steps: FlowStepData[]; links: FlowLinkData[]; risk_level: string;
  business_description?: string; review_items?: ReviewItemData[];
  meta?: {
    request_roles?: RequestRoleData[];
    versions?: Array<{ version: number; action: string; reason?: string; created_at?: string; summary?: any }>;
    current_version?: number;
  };
}
interface FlowCheckReport {
  passed?: boolean;
  errors?: string[];
  warnings?: string[];
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
  verification_plan?: { mode?: string; controllability?: string; reason?: string };
  api?: { method?: string; path?: string; params?: string[] };
  check_report?: FlowCheckReport;
}

// 录入产出状态机(后端 IngestionStatus):决定结果徽标的颜色与文案
const STATUS_META: Record<string, { color: string; label: string }> = {
  verified: { color: "success", label: "已验证 · 结构+活体" },
  partially_verified: { color: "warning", label: "部分验证 · 结构已验/活体未验" },
  needs_clarification: { color: "warning", label: "待澄清" },
  unsupported: { color: "default", label: "不支持 · 无法安全自动化" },
  rejected: { color: "error", label: "已拒绝" },
};

const KEYMAP: Record<string, string> = {
  Enter: "Enter", Backspace: "Backspace", Tab: "Tab", Delete: "Delete",
  ArrowLeft: "ArrowLeft", ArrowRight: "ArrowRight", ArrowUp: "ArrowUp", ArrowDown: "ArrowDown",
};

const CATEGORY_OPTIONS = [
  { label: "用户参数", value: "user_param" },
  { label: "运行期变量", value: "runtime_var" },
  { label: "系统常量", value: "system_const" },
];
const SOURCE_KIND_OPTIONS = [
  { label: "用户输入", value: "user_input" },
  { label: "上游响应", value: "previous_response" },
  { label: "当前用户", value: "current_user" },
  { label: "系统时间", value: "system_time" },
  { label: "页面上下文", value: "page_context" },
  { label: "固定值", value: "constant" },
  { label: "下拉/枚举", value: "form_option" },
  { label: "未知", value: "unknown" },
];
const PARAM_TYPE_OPTIONS = ["string", "number", "boolean", "datetime", "date", "array", "object", "list-enum"]
  .map((x) => ({ label: x, value: x }));
const STEP_ROLE_OPTIONS = [
  "submit_anchor", "business_write", "business_get", "read_context", "read_option", "auth", "noise",
].map((x) => ({ label: x, value: x }));

export default function PageRecorder({ tenant, subsystem, baseUrl, storageState }: {
  tenant: string; subsystem: string; baseUrl: string; storageState: string;
}) {
  const nav = useNavigate();
  const wsRef = useRef<WebSocket | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const kbRef = useRef<HTMLInputElement | null>(null);   // 隐藏输入框:接键入(含中文 IME)并回传
  const [phase, setPhase] = useState<"idle" | "recording" | "publishing" | "done">("idle");
  const [startUrl, setStartUrl] = useState("");
  const [frame, setFrame] = useState<string>("");
  const [steps, setSteps] = useState<RecStep[]>([]);
  const [reqs, setReqs] = useState<RecReq[]>([]);   // 诊断:抓到的写请求
  const [fields, setFields] = useState<RecField[]>([]);   // 提交请求体的字段表(供勾选成参数)
  const [picked, setPicked] = useState<Record<string, { on: boolean; name: string }>>({});  // path → {勾选, 参数名};必填由后端自动判定
  const [reqMeta, setReqMeta] = useState<{ method: string; url: string } | null>(null);
  const [cands, setCands] = useState<RecCand[]>([]);   // 候选写请求(可手选用哪个)
  const [chosenIdx, setChosenIdx] = useState(0);
  const [stepSel, setStepSel] = useState<Record<number, boolean>>({});   // 多步:勾入工作流的写请求 idx
  const [selects, setSelects] = useState<Record<string, RecSelect>>({});      // path → select 建议
  const [identity, setIdentity] = useState<Record<string, RecIdentity>>({});  // path → identity 建议
  const [action, setAction] = useState("submit_form");
  const [title, setTitle] = useState("");
  const [result, setResult] = useState<RecResult | null>(null);
  const [intercept, setIntercept] = useState(true);   // 拦截提交:抓到请求但不真发,录制不产生真实记录
  const [err, setErr] = useState("");
  // Step B/C/D: 多接口编排面板
  const [flowSpec, setFlowSpec] = useState<FlowSpecData | null>(null);
  const [flowSpecCollapsed, setFlowSpecCollapsed] = useState(true);
  const [checkReport, setCheckReport] = useState<FlowCheckReport | null>(null);
  const [newLink, setNewLink] = useState({ source_step_id: "", source_path: "", target_step_id: "", target_path: "" });
  const [newParam, setNewParam] = useState({ step_id: "", path: "", key: "", type: "string", category: "user_param" });
  const [jsonDraft, setJsonDraft] = useState("");
  const [jsonErr, setJsonErr] = useState("");

  useEffect(() => () => { wsRef.current?.close(); }, []);   // 卸载时断开

  function send(obj: unknown) {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
  }

  function start() {
    if (!tenant) { message.error("请先到「创建 / 进入租户」"); return; }
    if (!startUrl.trim()) { message.error("请填页面地址 start_url"); return; }
    setErr(""); setResult(null); setSteps([]); setReqs([]); setFrame(""); setFields([]); setPicked({}); setCands([]); setSelects({}); setIdentity({}); setStepSel({}); setFlowSpec(null); setCheckReport(null); setJsonDraft(""); setJsonErr("");
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/onboarding/page/record`);
    wsRef.current = ws;
    ws.onopen = () => send({
      type: "start", tenant, subsystem, start_url: startUrl.trim(),
      base_url: baseUrl.trim() || undefined,
      storage_state: storageState.trim() || undefined,
      intercept,   // 是否拦截提交(不产生真实记录)
    });
    ws.onmessage = (ev) => {
      let m: any; try { m = JSON.parse(ev.data); } catch { return; }
      if (m.type === "started") setPhase("recording");
      else if (m.type === "frame") setFrame(m.data);
      else if (m.type === "step") setSteps((s) => {
        const st = m.step;
        // 同一字段连续 fill/select(逐字符记)→ 覆盖上一条,实时列表一字段只显示一行最新值
        const last = s[s.length - 1];
        if (last && last.locator === st.locator && (st.op === "fill" || st.op === "select")) {
          return [...s.slice(0, -1), st];
        }
        return [...s, st];
      });
      else if (m.type === "request") setReqs((r) => [...r, m.request].slice(-40));   // 抓到的写请求(诊断)
      else if (m.type === "request_fields") {   // 抓到提交请求 → 列出请求体字段,让用户勾哪些是参数
        const fs: RecField[] = m.fields || [];
        const selMap: Record<string, RecSelect> = {};
        (m.selects || []).forEach((s: RecSelect) => { selMap[s.path] = s; });
        const idMap: Record<string, RecIdentity> = {};
        (m.identity || []).forEach((i: RecIdentity) => { idMap[i.path] = i; });
        setSelects(selMap); setIdentity(idMap);
        setFields(fs);
        const pk: Record<string, { on: boolean; name: string }> = {};
        fs.forEach((f) => {
          // 默认:变化字段(用户填的)勾=参数;固定字段(billType/流程号等)不勾=常量,结构上原样提交;
          // 当前用户/会话值不勾(运行期自动填)。这样"非参数字段一律原样"是结构保证,agent 改不到固定字段。
          const on = idMap[f.path] ? false : (selMap[f.path] ? true : !!f.suggest_param);
          pk[f.path] = { on, name: f.suggest_name || f.key };  // 必填由后端自动判定,前端不再手动勾
        });
        setPicked(pk);
        setReqMeta({ method: m.method, url: m.url });
        setCands(m.candidates || []);
        setChosenIdx(m.chosen_idx ?? 0);
        // 自动判出的业务流程步预勾上(用户可改);后端没给则不勾
        setStepSel(Object.fromEntries((m.suggested_steps || []).map((i: number) => [i, true])));
        setPhase("recording");
        message.success("抓到提交请求!勾选要让 agent 传值的字段 → 确认发布");
      }
      else if (m.type === "result") {   // 留在录制现场:不关浏览器、不重来
        setResult(m.report); setPhase("recording");
        if (m.report?.check_report) setCheckReport(m.report.check_report);
        if (m.report?.ok) { setFields([]); setPicked({}); setCands([]); setSelects({}); setIdentity({}); setStepSel({}); }   // 发布成功 → 收起字段表
      }
      // Step A: 后端随 request_fields 下发 FlowSpec
      else if (m.type === "flow_spec") {
        if (m.full_spec) setFlowSpec(m.full_spec);
        else if (m.flow_spec) setFlowSpec(m.flow_spec);
        if (m.check_report) setCheckReport(m.check_report);
        setFlowSpecCollapsed(false);
      }
      // Step B/C: flow_update 编辑后返回新 spec
      else if (m.type === "flow_spec_updated") {
        if (m.full_spec) setFlowSpec(m.full_spec);
        else if (m.flow_spec) setFlowSpec(m.flow_spec);
        if (m.check_report) setCheckReport(m.check_report);
        message.success("流程已更新");
      }
      // Step D2: LLM 命名返回
      else if (m.type === "step_names") {
        if (m.full_spec) setFlowSpec(m.full_spec);
        if (m.check_report) setCheckReport(m.check_report);
        const count = m.names ? Object.keys(m.names).length : (m.flow_spec?.steps?.length || 0);
        message.success(count > 0 ? `已为 ${count} 个步骤命名` : "命名完成");
      }
      // Step D3: LLM 业务说明返回
      else if (m.type === "business_description") {
        if (m.full_spec) {
          setFlowSpec(m.full_spec);
        } else if (m.description && flowSpec) {
          setFlowSpec({ ...flowSpec, business_description: m.description });
        }
        if (m.check_report) setCheckReport(m.check_report);
        message.success(m.description ? "业务说明已生成" : "LLM 暂不可用,未生成说明");
      }
      // Bug: step_id 漂移时自动同步
      else if (m.type === "error") {
        const detail = m.detail || "录制出错";
        if (detail.includes("step not found") || detail.includes("link not found")) {
          message.warning("流程已变更,正在自动同步最新版本…");
          send({ type: "refresh_flow_spec" });
          setErr("");
        } else {
          setErr(detail);
        }
      }
    };
    ws.onerror = () => setErr("WebSocket 连接失败(后端是否启动、是否支持 ws 代理?)");
    ws.onclose = () => { if (phase === "recording") setPhase("idle"); };
  }

  function onImgClick(e: React.MouseEvent<HTMLImageElement>) {
    const img = imgRef.current; if (!img) return;
    const r = img.getBoundingClientRect();
    send({ type: "input", event: { kind: "click", nx: (e.clientX - r.left) / r.width, ny: (e.clientY - r.top) / r.height } });
    kbRef.current?.focus({ preventScroll: true });   // 点完画面把焦点交给隐藏输入框(preventScroll:别把页面弹到顶部)
  }
  // 隐藏输入框接键入。中文 IME 合成中(isComposing)先不传,等 compositionend 整段传;英文走 onInput。
  function relayKb(el: HTMLInputElement) {
    const v = el.value;
    if (v) { send({ type: "input", event: { kind: "text", text: v } }); el.value = ""; }
  }
  function onKbInput(e: React.FormEvent<HTMLInputElement>) {
    if ((e.nativeEvent as { isComposing?: boolean }).isComposing) return;
    relayKb(e.currentTarget);
  }
  function onKbCompositionEnd(e: React.CompositionEvent<HTMLInputElement>) {
    relayKb(e.currentTarget);
  }
  function onKbKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (KEYMAP[e.key]) { send({ type: "input", event: { kind: "key", key: KEYMAP[e.key] } }); e.preventDefault(); }
    // 可打印字符交给 onInput(IME 安全),这里只处理 Enter/Backspace/方向键等
  }

  function resetFromHere() {
    send({ type: "reset" });
    setSteps([]); setResult(null);
    message.success("已清空,从现在起只录业务步骤(登录步骤已丢弃)");
  }
  function delStep(i: number) { setSteps((s) => s.filter((_, k) => k !== i)); }   // 删掉某一步(噪声/重复/误操作)
  function patchStep(i: number, p: Partial<RecStep>) { setSteps((s) => s.map((x, k) => (k === i ? { ...x, ...p } : x))); }
  function finalize() {
    if (!action.trim() || badAction(action.trim())) return;
    if (!steps.length && !reqs.length) { message.error("还没抓到提交请求、也没录到步骤;在画面里填表并点「提交」"); return; }
    setResult(null); setPhase("publishing");
    // 后端优先用抓到的提交请求:回 request_fields 让你勾字段;没抓到才走 DOM 步骤直接发布
    send({ type: "finalize", action: action.trim(), title: title.trim(),
           success_marker: null, steps });
  }
  function chooseRequest(idx: number) {   // 抓到多个写请求时,手选用哪个建 Skill
    setChosenIdx(idx);
    send({ type: "choose_request", idx });
  }
  function toggleField(path: string, on: boolean) {
    setPicked((p) => ({ ...p, [path]: { ...p[path], on } }));
  }
  function renameField(path: string, name: string) {
    setPicked((p) => ({ ...p, [path]: { ...p[path], name } }));
  }
  function badAction(a: string) {
    // 动作名是函数调用/工具标识,必须英文标识符;中文会导致导出目录冲突、function-call 名非法
    if (!/^[a-zA-Z][a-zA-Z0-9_]*$/.test(a)) {
      message.error("动作名请用英文标识(字母开头,如 submit_daily_report);中文写到「标题」里");
      return true;
    }
    return false;
  }
  function _payload() {
    const param_map: Record<string, string> = {};
    fields.forEach((f) => { const p = picked[f.path]; if (p?.on && p.name.trim()) param_map[f.path] = p.name.trim(); });
    const selList = Object.values(selects).filter((s) => param_map[s.path]);   // 选领导:仅作为参数的
    const idList = Object.values(identity);                                     // 当前用户:运行期重取
    // 多步(Q3):勾了 ≥2 个写请求 → 组成工作流,提交那步(chosenIdx)放最后(参数落它)
    const checked = cands.filter((c) => stepSel[c.idx]).map((c) => c.idx);
    const step_idxs = checked.length >= 2
      ? [...checked.filter((i) => i !== chosenIdx).sort((a, b) => a - b), chosenIdx] : [];
    return { param_map, selList, idList, step_idxs };
  }
  function publishRequest() {
    if (!action.trim() || badAction(action.trim())) return;
    const { param_map, selList, idList, step_idxs } = _payload();
    const useFlowSpec = !!flowSpec;
    if (!useFlowSpec && !Object.keys(param_map).length) { message.error("至少勾选一个字段作为参数"); return; }
    setResult(null); setPhase("publishing");
    // 一键发布:后端自动提炼业务 Goal + self_check + 审核 + 自动修复;必填也由后端**自动判定**
    //(默认全部必填,表单抓到 * 区分时据 * 降级可选),无需手动勾选/确认
    send({ type: "publish_request", action: action.trim(), title: title.trim(),
           param_map, selects: selList, identity: idList, step_idxs, use_flow_spec: useFlowSpec });
  }
  function stopAll() {
    send({ type: "stop" }); wsRef.current?.close();
    setPhase("idle"); setResult(null); setSteps([]); setFrame(""); setFields([]); setPicked({}); setCands([]); setSelects({}); setIdentity({}); setStepSel({}); setFlowSpec(null); setCheckReport(null); setJsonDraft(""); setJsonErr("");
  }
  function updateStepField(stepId: string, field: string, value: any) {
    send({ type: "flow_update", edits: [{ op: "update", step_id: stepId, field, value }] });
  }
  function updateParamField(stepId: string, paramPath: string, field: string, value: any) {
    send({ type: "flow_update", edits: [{ op: "update", step_id: stepId, param_path: paramPath, field, value }] });
  }
  function updateParamCategory(stepId: string, p: FlowParam, category: string) {
    const sourceKind = category === "user_param" ? "user_input" : category === "system_const" ? "constant" : (p.source_kind || "unknown");
    send({ type: "flow_update", edits: [
      { op: "update", step_id: stepId, param_path: p.path, field: "category", value: category },
      { op: "update", step_id: stepId, param_path: p.path, field: "source_kind", value: sourceKind },
      { op: "update", step_id: stepId, param_path: p.path, field: "source", value: { kind: sourceKind, path: p.path, manual: true } },
      { op: "update", step_id: stepId, param_path: p.path, field: "exposed_to_user", value: category === "user_param" },
      { op: "update", step_id: stepId, param_path: p.path, field: "need_human_confirm", value: false },
    ] });
  }
  function updateParamSourceKind(stepId: string, p: FlowParam, sourceKind: string) {
    send({ type: "flow_update", edits: [
      { op: "update", step_id: stepId, param_path: p.path, field: "source_kind", value: sourceKind },
      { op: "update", step_id: stepId, param_path: p.path, field: "source", value: sourceKind === "unknown" ? {} : { kind: sourceKind, path: p.path, manual: true } },
      { op: "update", step_id: stepId, param_path: p.path, field: "need_human_confirm", value: sourceKind === "unknown" },
    ] });
  }
  function resolveReview(reviewId: string, resolved = true) {
    send({ type: "flow_update", edits: [{ op: "resolve_review", review_id: reviewId, resolved }] });
  }
  function addParam() {
    const stepId = newParam.step_id || flowSpec?.steps?.[0]?.step_id || "";
    const path = newParam.path.trim();
    const key = newParam.key.trim();
    if (!stepId || !path || !key) {
      message.warning("请选择步骤并填写字段路径和参数名");
      return;
    }
    const sourceKind = newParam.category === "user_param" ? "user_input" : newParam.category === "system_const" ? "constant" : "unknown";
    send({ type: "flow_update", edits: [{
      op: "add",
      step_id: stepId,
      param: {
        path, key,
        label: key,
        value: "",
        type: newParam.type,
        required: false,
        category: newParam.category,
        source_kind: sourceKind,
        source: sourceKind === "unknown" ? {} : { kind: sourceKind, path, manual: true },
        exposed_to_user: newParam.category === "user_param",
        editable: true,
        reason: "人工新增字段",
        need_human_confirm: false,
      },
    }] });
    setNewParam({ step_id: stepId, path: "", key: "", type: "string", category: "user_param" });
  }
  function applyJsonDraft() {
    try {
      const parsed = JSON.parse(jsonDraft);
      setJsonErr("");
      send({ type: "flow_replace", flow_spec: parsed });
    } catch (e: any) {
      setJsonErr(e?.message || "JSON 解析失败");
    }
  }
  function loadJsonDraft() {
    if (!flowSpec) return;
    setJsonDraft(JSON.stringify(flowSpec, null, 2));
    setJsonErr("");
  }
  const allReviewItems = (checkReport?.review_items?.length ? checkReport.review_items : flowSpec?.review_items) || [];
  const reviewItems = allReviewItems.filter((i) => !i.resolved);
  const reviewColor = (severity?: string) => severity === "high" ? "error" : severity === "medium" ? "warning" : "default";

  return (
    <Card size="small" title="网页录制 → 抓提交请求 → 选字段建 Skill">
      {phase === "idle" && (
        <>
          {/* <Alert
            style={{ marginBottom: 12 }} type="info" showIcon
            message="三步:① 在画面里登录并填一遍表 → ② 点表单的「提交」(系统抓下这一下发出的请求)→ ③ 在弹出的字段表里勾选哪些当参数,确认发布。"
            description="登录态自动复用、密码不记录;无需手填 base_url / 登录态。"
          /> */}
          <Form.Item label="业务页地址 start_url" required style={{ marginBottom: 12 }}>
            <Input value={startUrl} onChange={(e) => setStartUrl(e.target.value)}
                   placeholder="https://oa.example.com/reimburse/new" onPressEnter={start} />
          </Form.Item>
          
          <div><Button type="primary" onClick={start}>开始录制</Button>   <Space style={{ marginBottom: 12 }} align="center">
            <Switch checked={intercept} onChange={setIntercept} />
            <Typography.Text>拦截提交 </Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              开:点提交只用来抓请求,系统拦下不真发(推荐)。关:会真的提交一次。
            </Typography.Text>
          </Space>  </div>
          {err && <Alert style={{ marginTop: 12 }} type="error" showIcon message={err} />}
        </>
      )}

      {(phase === "recording" || phase === "publishing") && (
        <div>
          <Space style={{ marginBottom: 8 }} wrap>
            <Tag color="processing">{phase === "publishing" ? "发布中…" : "录制中"}</Tag>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>在画面里操作;点击/键盘会传到浏览器</Typography.Text>
            <Button size="small" disabled={phase === "publishing"} onClick={resetFromHere}>从这里开始录(登录后点)</Button>
          </Space>
          <div style={{ border: "1px solid #d9d9d9", borderRadius: 6, overflow: "hidden", lineHeight: 0, position: "relative" }}>
            {frame
              ? <img ref={imgRef} src={`data:image/jpeg;base64,${frame}`} onClick={onImgClick} draggable={false}
                     onWheel={(e) => send({ type: "input", event: { kind: "scroll", dy: e.deltaY } })}
                     style={{ width: "100%", display: "block", cursor: "crosshair" }} alt="录制画面" />
              : <div style={{ padding: 40, textAlign: "center", color: "#999", lineHeight: 1.6 }}>等待浏览器画面…(若停在登录页,直接在画面里登录即可)</div>}
            {/* 隐藏输入框:点画面里的输入框后,在这里接你的键入(支持中文),整段回传到浏览器 */}
            <input ref={kbRef} onInput={onKbInput} onKeyDown={onKbKeyDown} onCompositionEnd={onKbCompositionEnd}
                   autoComplete="off" aria-hidden="true"
                   style={{ position: "absolute", left: 0, top: 0, width: 1, height: 1, opacity: 0, border: 0, padding: 0 }} />
          </div>
          <Alert type="info" showIcon style={{ marginTop: 8, marginBottom: 4 }}
            message={<span>正常填表即可:日期、下拉<b>随便点选</b>;我们抓的是你点「提交」那一下发出的<b>整条请求</b>里的最终值,不靠记录每次点击。</span>}
            description={intercept
              ? "已开启「拦截提交」:点提交只用来抓请求,不会产生真实记录。"
              : "未开启拦截:点提交会真的提交一次(产生一条真实记录)。"} />

          {/* ★ 主路径:抓到提交请求 → 勾字段建 Skill */}
          {fields.length > 0 && (
            <Card type="inner" size="small" style={{ marginTop: 12, borderColor: "#52c41a" }}
              title={<Space wrap size={4}>
                <Tag color="success">✅ 抓到提交请求</Tag>
                {reqMeta && <Typography.Text code style={{ fontSize: 12 }}>
                  {reqMeta.method} {reqMeta.url.replace(/^https?:\/\/[^/]+/, "")}</Typography.Text>}
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>勾选要让 agent 传值的字段</Typography.Text>
              </Space>}>
              <Alert type="info" showIcon style={{ marginBottom: 8 }}
                message="勾上的字段 → 变成参数(agent 调用时按需传值);没勾的(内部 ID、流程号、表单类型等)原样提交。已自动勾选像「填写内容」的字段,请核对增减。" />
              {(Object.keys(selects).length > 0 || Object.keys(identity).length > 0) && (
                <Alert type="warning" showIcon style={{ marginBottom: 8 }}
                  message={<span>
                    {Object.keys(selects).length > 0 && <span><Tag color="purple">📋 选自列表</Tag>这类字段(如选领导)agent 按<b>名字</b>传、运行期查 ID;</span>}
                    {Object.keys(identity).length > 0 && <span><Tag color="gold">🔒 当前用户</Tag>这类字段默认不作参数、运行期自动填(谁调用就是谁);</span>}
                    完整生效在后续 P4/P5。
                  </span>} />
              )}
              {cands.length > 1 && (
                <div style={{ marginBottom: 8 }}>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    抓到 {cands.length} 个写请求。点蓝色那个=参数落它(提交那步);多步业务(先起流程→再提交)勾「步骤」组成工作流:</Typography.Text>
                  <div style={{ marginTop: 4 }}>
                    {cands.map((c) => (
                      <div key={c.idx} style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 2 }}>
                        <Checkbox checked={!!stepSel[c.idx]}
                                  onChange={(e) => setStepSel((s) => ({ ...s, [c.idx]: e.target.checked }))}>
                          <Typography.Text style={{ fontSize: 11 }} type="secondary">步骤</Typography.Text>
                        </Checkbox>
                        <Tag color={c.idx === chosenIdx ? "blue" : "default"}
                             style={{ cursor: "pointer", margin: 0 }} onClick={() => chooseRequest(c.idx)}>
                          {c.method} {c.path}
                        </Tag>
                      </div>
                    ))}
                  </div>
                  {Object.values(stepSel).filter(Boolean).length >= 2 && (
                    <Typography.Text type="warning" style={{ fontSize: 11 }}>
                      多步工作流:勾选的写请求按顺序执行,step 间的 taskId 等自动串联(蓝色那步放最后)。
                      <b>多步需在「关闭拦截提交」下录制</b>(否则拿不到真实 taskId,串联会失败)。
                    </Typography.Text>
                  )}
                </div>
              )}
              <List
                size="small" style={{ maxHeight: 300, overflow: "auto" }}
                dataSource={fields}
                renderItem={(f) => {
                  const p = picked[f.path] || { on: false, name: f.key };
                  const sel = selects[f.path];
                  const idn = identity[f.path];
                  return (
                    <List.Item style={{ paddingLeft: 0, paddingRight: 0 }}>
                      <Space size={8} wrap>
                        <Checkbox checked={p.on} onChange={(e) => toggleField(f.path, e.target.checked)}>参数</Checkbox>
                        <Typography.Text code style={{ fontSize: 12 }}>{f.path}</Typography.Text>
                        {sel && <Tag color="purple" style={{ fontSize: 11 }}>
                          {sel.dom_options && !sel.source_url
                            ? <>📋 页面枚举(共{sel.count}项,从下拉里选)</>
                            : <>📋 选自列表{sel.multi ? "·多选" : ""}{sel.dom_options ? "·页面枚举" : ""} {sel.label_key}→{sel.value_key}(共{sel.count}项{sel.multi ? ",传名字列表" : ""})</>}</Tag>}
                        {idn && <Tag color="gold" style={{ fontSize: 11 }}>🔒 当前用户/会话值(运行期自动填)</Tag>}
                        {!sel && !idn && (f.suggest_param
                          ? <Tag color="blue" style={{ fontSize: 11 }}>参数·agent 传值</Tag>
                          : (f.system_value
                            ? <Tag color="gold" style={{ fontSize: 11 }}>🕒 系统值·运行期自动填</Tag>
                            : <Tag style={{ fontSize: 11 }}>固定值·原样提交</Tag>))}
                        {f.type && f.type !== "string" && <Tag style={{ fontSize: 11 }}>{f.type}</Tag>}
                        {f.name_source === "llm" && <Tag color="geekblue" style={{ fontSize: 11 }}>AI 拟名·待核</Tag>}
                        {f.confidence_tier && f.confidence_tier !== "auto" &&
                          <Tag color="orange" style={{ fontSize: 11 }}>低置信·建议确认</Tag>}
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          值={f.value === "" ? "(空)" : (f.value.length > 30 ? f.value.slice(0, 30) + "…" : f.value)}</Typography.Text>
                        {p.on && !idn && <>
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>参数名</Typography.Text>
                          <Input size="small" value={p.name} placeholder="参数名(英文/拼音)"
                                 onChange={(e) => renameField(f.path, e.target.value)} style={{ width: 150 }} />
                          {/* 必填由后端自动判定(默认全部必填,表单 * 区分时降级可选),这里只读展示,免手动勾选 */}
                          {f.required
                            ? <Tag color="red" style={{ fontSize: 11 }}>必填(自动)</Tag>
                            : <Tag style={{ fontSize: 11 }}>可选(自动)</Tag>}
                        </>}
                      </Space>
                    </List.Item>
                  );
                }}
              />
              <Space style={{ marginTop: 10 }} wrap>
                <Form.Item label="动作名" required style={{ marginBottom: 0 }}>
                  <Input value={action} onChange={(e) => setAction(e.target.value)} placeholder="submit_leave" style={{ width: 180 }} />
                </Form.Item>
                <Form.Item label="标题" style={{ marginBottom: 0 }}>
                  <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="提交请假" style={{ width: 160 }} />
                </Form.Item>
                <Button type="primary" loading={phase === "publishing"} onClick={publishRequest}>
                  确认发布(AI 自动提炼目标 + 审核 + 修复)
                </Button>
              </Space>
            </Card>
          )}

          {/* 还没抓到请求时:动作名 + 抓取按钮 */}
          {!fields.length && (
            <>
              <Space size="large" wrap style={{ marginTop: 12 }}>
                <Form.Item label="Skill 动作名(英文)" required style={{ marginBottom: 0 }}>
                  <Input value={action} onChange={(e) => setAction(e.target.value)} placeholder="submit_leave" style={{ width: 200 }} />
                </Form.Item>
                <Form.Item label="标题(中文)" style={{ marginBottom: 0 }}>
                  <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="提交请假" style={{ width: 180 }} />
                </Form.Item>
              </Space>
              <Space style={{ marginTop: 12 }} wrap>
                <Button type="primary" loading={phase === "publishing"} disabled={!steps.length && !reqs.length} onClick={finalize}>
                  {result && !result.ok ? "改完重新发布" : "停止并发布(抓提交请求)"}
                </Button>
                <Button onClick={stopAll} disabled={phase === "publishing"}>结束录制</Button>
              </Space>
              <Typography.Text type="secondary" style={{ fontSize: 12, display: "block", marginTop: 6 }}>
                在画面里填好表、点过「提交」后按这个 → 弹出字段勾选表,选好字段再确认发布。
              </Typography.Text>
            </>
          )}
          {fields.length > 0 && (
            <Space style={{ marginTop: 12 }} wrap>
              <Button loading={phase === "publishing"} onClick={finalize}>重新抓取提交请求</Button>
              <Button onClick={stopAll} disabled={phase === "publishing"}>结束录制</Button>
            </Space>
          )}

          {/* Step B/C/D: 多接口编排面板(只展示编排关系,字段编辑在上面) */}
          {flowSpec && (
            <Collapse
              style={{ marginTop: 16 }}
              activeKey={flowSpecCollapsed ? [] : ["flow-editor"]}
              onChange={(keys) => setFlowSpecCollapsed(keys.length === 0)}
            >
              <Collapse.Panel
                key="flow-editor"
                header={
                  <Space>
                    <Typography.Text strong>多接口编排</Typography.Text>
                    <Tag color="blue">{flowSpec.steps?.length || 0} 步</Tag>
                    <Tag color="orange">风险: {flowSpec.risk_level}</Tag>
                  </Space>
                }
              >
                {/* LLM 命名 + 业务说明 */}
                <Space style={{ marginBottom: 12 }} wrap>
                  <Button size="small" onClick={() => {
                    send({ type: "step_naming" });
                    message.info("正在让 AI 给每个步骤起业务名…");
                  }}>🤖 AI 命名步骤</Button>
                  <Button size="small" onClick={() => {
                    send({ type: "business_description" });
                    message.info("正在生成业务流程说明…");
                  }}>📝 生成业务说明</Button>
                </Space>
                {flowSpec.business_description && (
                  <Alert type="info" showIcon style={{ marginBottom: 12, fontSize: 12 }}
                    message="业务说明" description={flowSpec.business_description} />
                )}
                <Card size="small" style={{ marginBottom: 12 }} title="流程标题与说明">
                  <Space direction="vertical" size={8} style={{ width: "100%" }}>
                    <Input
                      size="small"
                      placeholder="流程标题"
                      defaultValue={flowSpec.title}
                      onBlur={(e) => {
                        const value = e.currentTarget.value.trim();
                        if (value && value !== flowSpec.title) {
                          send({ type: "flow_update", edits: [{ op: "update_flow", field: "title", value }] });
                        }
                      }}
                      onPressEnter={(e) => e.currentTarget.blur()}
                    />
                    <Input.TextArea
                      rows={5}
                      placeholder="业务说明，可先点击生成业务说明后再人工修正"
                      value={flowSpec.business_description || ""}
                      onChange={(e) => setFlowSpec({ ...flowSpec, business_description: e.target.value })}
                      onBlur={(e) => send({ type: "flow_update", edits: [{
                        op: "update_flow", field: "business_description", value: e.currentTarget.value,
                      }] })}
                    />
                  </Space>
                </Card>
                {checkReport && (
                  <Alert
                    type={checkReport.passed ? "success" : "warning"}
                    showIcon
                    style={{ marginBottom: 12, fontSize: 12 }}
                    message={checkReport.passed ? "FlowSpec 发布校验通过" : "FlowSpec 发布校验需要处理"}
                    description={
                      <Space direction="vertical" size={2}>
                        {(checkReport.api_preview?.params || []).length > 0 && (
                          <Typography.Text style={{ fontSize: 12 }}>
                            Skill 参数: {(checkReport.api_preview?.params || []).join(", ")}
                          </Typography.Text>
                        )}
                        {checkReport.dry_run && (
                          <Typography.Text
                            type={checkReport.dry_run.ok ? "success" : "warning"}
                            style={{ fontSize: 12 }}
                          >
                            Dry-run: {checkReport.dry_run.ok ? "请求可构造" : "需要处理"}
                            {typeof checkReport.dry_run.request_count === "number"
                              ? ` · ${checkReport.dry_run.request_count} 步` : ""}
                            {checkReport.dry_run.fact_check?.configured
                              ? ` · fact_check ${checkReport.dry_run.fact_check.passed ? "完整" : "不完整"}`
                              : " · 未配置 fact_check"}
                          </Typography.Text>
                        )}
                        {(checkReport.dry_run?.missing_params || []).map((x, i) =>
                          <Typography.Text key={"dm" + i} type="warning" style={{ fontSize: 12 }}>Dry-run 缺少参数: {x}</Typography.Text>)}
                        {(checkReport.dry_run?.self_check || []).map((x, i) =>
                          <Typography.Text key={"ds" + i} type="danger" style={{ fontSize: 12 }}>Self-check: {x}</Typography.Text>)}
                        {(checkReport.dry_run?.build_errors || []).map((x, i) =>
                          <Typography.Text key={"db" + i} type="danger" style={{ fontSize: 12 }}>Build: {x}</Typography.Text>)}
                        {checkReport.dry_run?.fact_check?.reason && (
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            Fact-check: {checkReport.dry_run.fact_check.reason}
                          </Typography.Text>
                        )}
                        {(checkReport.errors || []).map((x, i) =>
                          <Typography.Text key={"fe" + i} type="danger" style={{ fontSize: 12 }}>{x}</Typography.Text>)}
                        {(checkReport.warnings || []).map((x, i) =>
                          <Typography.Text key={"fw" + i} type="warning" style={{ fontSize: 12 }}>{x}</Typography.Text>)}
                      </Space>
                    }
                  />
                )}
                {reviewItems.length > 0 && (
                  <Card size="small" style={{ marginBottom: 12 }} title={
                    <Space>
                      <Typography.Text strong>待确认项</Typography.Text>
                      <Tag color="error">{reviewItems.filter((i) => i.severity === "high").length} 高</Tag>
                      <Tag color="warning">{reviewItems.filter((i) => i.severity === "medium").length} 中</Tag>
                      <Tag>{reviewItems.filter((i) => i.severity === "low").length} 低</Tag>
                    </Space>
                  }>
                    <List
                      size="small"
                      dataSource={reviewItems.slice(0, 50)}
                      renderItem={(item) => (
                        <List.Item style={{ paddingLeft: 0, paddingRight: 0 }}>
                          <Space direction="vertical" size={2} style={{ width: "100%" }}>
                            <Space size={6} wrap>
                              <Tag color={reviewColor(item.severity)}>{item.severity || "medium"}</Tag>
                              <Tag>{item.type}</Tag>
                              <Typography.Text strong style={{ fontSize: 12 }}>{item.title}</Typography.Text>
                              {item.current_guess && <Tag>{item.current_guess}</Tag>}
                              {typeof item.confidence === "number" && item.confidence > 0 && (
                                <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                                  置信度 {Math.round(item.confidence * 100)}%
                                </Typography.Text>
                              )}
                            </Space>
                            <Space size={6} wrap>
                              {(item.target?.path || item.target?.link_id || item.target?.step_id) && (
                                <Typography.Text code style={{ fontSize: 11 }}>
                                  {item.target?.path || item.target?.link_id || item.target?.step_id}
                                </Typography.Text>
                              )}
                              <Typography.Text type="secondary" style={{ fontSize: 11 }}>{item.reason}</Typography.Text>
                              <Button size="small" onClick={() => resolveReview(item.id, true)}>确认/忽略</Button>
                            </Space>
                          </Space>
                        </List.Item>
                      )}
                    />
                  </Card>
                )}
                {(flowSpec.meta?.versions || []).length > 0 && (
                  <Card size="small" style={{ marginBottom: 12 }} title={
                    <Space>
                      <Typography.Text strong>版本历史</Typography.Text>
                      <Tag>v{flowSpec.meta?.current_version || flowSpec.meta?.versions?.length}</Tag>
                    </Space>
                  }>
                    <List
                      size="small"
                      dataSource={(flowSpec.meta?.versions || []).slice().reverse().slice(0, 8)}
                      renderItem={(v) => (
                        <List.Item style={{ paddingLeft: 0, paddingRight: 0 }}>
                          <Space size={6} wrap>
                            <Tag color={v.version === flowSpec.meta?.current_version ? "blue" : "default"}>v{v.version}</Tag>
                            <Tag>{v.action}</Tag>
                            {v.summary?.steps !== undefined && <Typography.Text type="secondary" style={{ fontSize: 11 }}>{v.summary.steps} 步</Typography.Text>}
                            {v.summary?.links !== undefined && <Typography.Text type="secondary" style={{ fontSize: 11 }}>{v.summary.links} 链接</Typography.Text>}
                            {v.reason && <Typography.Text type="secondary" style={{ fontSize: 11 }}>{v.reason}</Typography.Text>}
                          </Space>
                        </List.Item>
                      )}
                    />
                  </Card>
                )}
                {(flowSpec.meta?.request_roles || []).length > 0 && (
                  <Card size="small" style={{ marginBottom: 12 }} title={
                    <Space>
                      <Typography.Text strong>接口筛选</Typography.Text>
                      <Tag>{flowSpec.meta?.request_roles?.length || 0} 条</Tag>
                    </Space>
                  }>
                    <List
                      size="small"
                      dataSource={(flowSpec.meta?.request_roles || []).slice(0, 40)}
                      renderItem={(r) => (
                        <List.Item style={{ paddingLeft: 0, paddingRight: 0 }}>
                          <Space size={6} wrap>
                            <Tag color={r.keep ? "success" : "default"}>{r.keep ? "保留" : "过滤"}</Tag>
                            <Tag>{r.role}</Tag>
                            <Tag>{r.method}</Tag>
                            <Typography.Text code style={{ fontSize: 11 }}>{r.path}</Typography.Text>
                            {typeof r.confidence === "number" && (
                              <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                                置信度 {Math.round(r.confidence * 100)}%
                              </Typography.Text>
                            )}
                            <Typography.Text type="secondary" style={{ fontSize: 11 }}>{r.reason}</Typography.Text>
                          </Space>
                        </List.Item>
                      )}
                    />
                  </Card>
                )}

                {/* 步骤列表(含重排按钮) */}
                <Card size="small" style={{ marginBottom: 12 }} title="步骤列表">
                  <List
                    size="small" dataSource={flowSpec.steps}
                    renderItem={(step, stepIdx) => (
                      <List.Item
                        actions={[
                          <Button key="up" size="small" disabled={stepIdx === 0} title="上移"
                            onClick={() => {
                              if (!flowSpec.steps || stepIdx <= 0) return;
                              const ids = flowSpec.steps.map((s) => s.step_id);
                              [ids[stepIdx - 1], ids[stepIdx]] = [ids[stepIdx], ids[stepIdx - 1]];
                              send({ type: "flow_update", edits: [{ op: "reorder_steps", step_ids: ids }] });
                            }}>↑</Button>,
                          <Button key="down" size="small" disabled={stepIdx === flowSpec.steps.length - 1} title="下移"
                            onClick={() => {
                              if (!flowSpec.steps || stepIdx >= flowSpec.steps.length - 1) return;
                              const ids = flowSpec.steps.map((s) => s.step_id);
                              [ids[stepIdx], ids[stepIdx + 1]] = [ids[stepIdx + 1], ids[stepIdx]];
                              send({ type: "flow_update", edits: [{ op: "reorder_steps", step_ids: ids }] });
                            }}>↓</Button>,
                          <Button key="remove-step" size="small" danger title="删除步骤"
                            onClick={() => send({ type: "flow_update", edits: [{ op: "remove_step", step_id: step.step_id }] })}
                          >删除</Button>,
                        ]}
                      >
                        <Space direction="vertical" size={4} style={{ width: "100%" }}>
                          <Space size={8} wrap>
                            <Tag color="purple">第 {stepIdx + 1} 步</Tag>
                            <Tag>{step.method}</Tag>
                            <Typography.Text code style={{ fontSize: 11 }}>{step.path}</Typography.Text>
                            <Tag>{step.risk_level}</Tag>
                            {step.name && <Typography.Text strong style={{ fontSize: 11 }}>· {step.name}</Typography.Text>}
                            <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                              {step.params?.length || 0} 个参数
                            </Typography.Text>
                          </Space>
                          <Space size={6} wrap>
                            <Input
                              size="small"
                              placeholder="步骤名"
                              defaultValue={step.name}
                              style={{ width: 180 }}
                              onBlur={(e) => {
                                const value = e.currentTarget.value.trim();
                                if (value !== (step.name || "")) updateStepField(step.step_id, "name", value);
                              }}
                              onPressEnter={(e) => e.currentTarget.blur()}
                            />
                            <Select
                              size="small"
                              placeholder="角色"
                              value={step.source_meta?.role || step.semantic_role || undefined}
                              style={{ width: 150 }}
                              options={STEP_ROLE_OPTIONS}
                              onChange={(value) => updateStepField(step.step_id, "role", value)}
                            />
                          </Space>
                          {(step.params || []).length > 0 && (
                            <List
                              size="small"
                              dataSource={(step.params || []).slice(0, 12)}
                              renderItem={(p) => (
                                <List.Item style={{ padding: "6px 0" }}>
                                  <Space direction="vertical" size={4} style={{ width: "100%" }}>
                                    <Space size={6} wrap>
                                      <Tag
                                        color={p.category === "runtime_var" ? "gold" : p.category === "system_const" ? "default" : "blue"}
                                        style={{ fontSize: 11 }}
                                        title={p.reason || p.path}
                                      >
                                        {p.path}
                                      </Tag>
                                      {p.need_human_confirm && <Tag color="warning">待确认</Tag>}
                                      <Input
                                        size="small"
                                        placeholder="参数名"
                                        defaultValue={p.key}
                                        style={{ width: 140 }}
                                        onBlur={(e) => {
                                          const value = e.currentTarget.value.trim();
                                          if (value && value !== p.key) updateParamField(step.step_id, p.path, "key", value);
                                        }}
                                        onPressEnter={(e) => e.currentTarget.blur()}
                                      />
                                      <Select
                                        size="small"
                                        value={p.type}
                                        style={{ width: 112 }}
                                        options={PARAM_TYPE_OPTIONS}
                                        onChange={(value) => updateParamField(step.step_id, p.path, "type", value)}
                                      />
                                      <Select
                                        size="small"
                                        value={p.category || "user_param"}
                                        style={{ width: 126 }}
                                        options={CATEGORY_OPTIONS}
                                        onChange={(value) => updateParamCategory(step.step_id, p, value)}
                                      />
                                      <Select
                                        size="small"
                                        value={p.source_kind || "unknown"}
                                        style={{ width: 126 }}
                                        options={SOURCE_KIND_OPTIONS}
                                        onChange={(value) => updateParamSourceKind(step.step_id, p, value)}
                                      />
                                    </Space>
                                    <Space size={10} wrap>
                                      <Checkbox
                                        checked={!!p.required}
                                        onChange={(e) => updateParamField(step.step_id, p.path, "required", e.target.checked)}
                                      >
                                        <Typography.Text style={{ fontSize: 11 }}>必填</Typography.Text>
                                      </Checkbox>
                                      <Checkbox
                                        checked={p.exposed_to_user !== false}
                                        onChange={(e) => updateParamField(step.step_id, p.path, "exposed_to_user", e.target.checked)}
                                      >
                                        <Typography.Text style={{ fontSize: 11 }}>暴露给用户</Typography.Text>
                                      </Checkbox>
                                      {p.need_human_confirm && (
                                        <Button size="small" onClick={() => updateParamField(step.step_id, p.path, "need_human_confirm", false)}>
                                          确认字段
                                        </Button>
                                      )}
                                      <Button
                                        size="small"
                                        danger
                                        onClick={() => send({ type: "flow_update", edits: [{
                                          op: "remove", step_id: step.step_id, param_path: p.path,
                                        }] })}
                                      >
                                        删除字段
                                      </Button>
                                      {p.reason && (
                                        <Typography.Text type="secondary" style={{ fontSize: 11 }}>{p.reason}</Typography.Text>
                                      )}
                                    </Space>
                                  </Space>
                                </List.Item>
                              )}
                            />
                          )}
                          {(step.params || []).length > 12 && (
                            <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                              还有 {(step.params || []).length - 12} 个字段
                            </Typography.Text>
                          )}
                        </Space>
                      </List.Item>
                    )}
                  />
                </Card>

                <Card size="small" style={{ marginBottom: 12 }} title="新增参数">
                  <Space size={8} wrap>
                    <Select
                      size="small"
                      placeholder="步骤"
                      value={newParam.step_id || undefined}
                      style={{ width: 220 }}
                      options={(flowSpec.steps || []).map((s) => ({ label: `${s.method} ${s.path}`, value: s.step_id }))}
                      onChange={(value) => setNewParam((s) => ({ ...s, step_id: value }))}
                    />
                    <Input
                      size="small"
                      placeholder="字段路径"
                      value={newParam.path}
                      style={{ width: 160 }}
                      onChange={(e) => setNewParam((s) => ({ ...s, path: e.target.value }))}
                    />
                    <Input
                      size="small"
                      placeholder="参数名"
                      value={newParam.key}
                      style={{ width: 140 }}
                      onChange={(e) => setNewParam((s) => ({ ...s, key: e.target.value }))}
                    />
                    <Select
                      size="small"
                      value={newParam.type}
                      style={{ width: 112 }}
                      options={PARAM_TYPE_OPTIONS}
                      onChange={(value) => setNewParam((s) => ({ ...s, type: value }))}
                    />
                    <Select
                      size="small"
                      value={newParam.category}
                      style={{ width: 126 }}
                      options={CATEGORY_OPTIONS}
                      onChange={(value) => setNewParam((s) => ({ ...s, category: value }))}
                    />
                    <Button size="small" type="primary" onClick={addParam}>添加参数</Button>
                  </Space>
                </Card>

                {/* 步骤间链接 */}
                {flowSpec.steps && flowSpec.steps.length >= 2 && (
                  <Card size="small" title={
                    <Space>
                      <Typography.Text strong>步骤间数据流</Typography.Text>
                      <Tag>{flowSpec.links?.length || 0} 条链接</Tag>
                    </Space>
                  }>
                    {(!flowSpec.links || flowSpec.links.length === 0) && (
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        暂无自动探测的串联关系(如 taskId)。可手动添加。
                      </Typography.Text>
                    )}
                    <List
                      size="small" dataSource={flowSpec.links || []}
                      renderItem={(link) => {
                        const sourceStep = flowSpec.steps!.find((s) => s.step_id === link.source_step_id);
                        const targetStep = flowSpec.steps!.find((s) => s.step_id === link.target_step_id);
                        return (
                          <List.Item actions={[
                            <Checkbox key="confirmed" checked={link.confirmed || false}
                              onChange={(e) => send({ type: "flow_update", edits: [{
                                op: "update", link_id: link.link_id,
                                field: "confirmed", value: e.target.checked,
                              }] })}
                            >
                              <Typography.Text style={{ fontSize: 11 }}>人工确认</Typography.Text>
                            </Checkbox>,
                            <Button key="del" size="small" danger
                              onClick={() => send({ type: "flow_update", edits: [{
                                op: "remove", link_id: link.link_id,
                              }] })}
                            >删除</Button>,
                          ]}>
                            <Space size={4} wrap>
                              <Tag color="blue">{sourceStep?.path || link.source_step_id}</Tag>
                              <Typography.Text code style={{ fontSize: 11 }}>{link.source_path}</Typography.Text>
                              <Typography.Text>→</Typography.Text>
                              <Tag color="green">{targetStep?.path || link.target_step_id}</Tag>
                              <Typography.Text code style={{ fontSize: 11 }}>{link.target_path}</Typography.Text>
                              {link.confirmed ?
                                <Tag color="success" style={{ fontSize: 11 }}>✓ 已确认</Tag> :
                                <Tag color="warning" style={{ fontSize: 11 }}>待确认</Tag>}
                            </Space>
                          </List.Item>
                        );
                      }}
                    />

                    {/* 添加新链接 */}
                    <div style={{ marginTop: 12, padding: 8, border: "1px dashed #d9d9d9", borderRadius: 4 }}>
                      <Typography.Text strong style={{ fontSize: 12 }}>+ 添加新链接</Typography.Text>
                      <div style={{ marginTop: 8, display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr auto", gap: 8, alignItems: "center" }}>
                        <Select size="small" placeholder="源步骤"
                          value={newLink.source_step_id || undefined}
                          onChange={(value) => setNewLink((s) => ({ ...s, source_step_id: value }))}
                          options={flowSpec.steps.map((s) => ({ label: `${s.method} ${s.path}`, value: s.step_id }))}
                        />
                        <Input size="small" placeholder="源字段路径 (如 data.id)"
                          value={newLink.source_path}
                          onChange={(e) => setNewLink((s) => ({ ...s, source_path: e.target.value }))}
                        />
                        <Select size="small" placeholder="目标步骤"
                          value={newLink.target_step_id || undefined}
                          onChange={(value) => setNewLink((s) => ({ ...s, target_step_id: value }))}
                          options={flowSpec.steps.map((s) => ({ label: `${s.method} ${s.path}`, value: s.step_id }))}
                        />
                        <Input size="small" placeholder="目标字段路径 (如 body.id)"
                          value={newLink.target_path}
                          onChange={(e) => setNewLink((s) => ({ ...s, target_path: e.target.value }))}
                        />
                        <Button size="small" type="primary" onClick={() => {
                          if (!newLink.source_step_id || !newLink.target_step_id
                              || !newLink.source_path || !newLink.target_path) {
                            message.warning("请填写源步骤/源路径/目标步骤/目标路径");
                            return;
                          }
                          send({ type: "flow_update", edits: [{
                            op: "add", step_id: newLink.source_step_id,
                            link: {
                              source_step_id: newLink.source_step_id,
                              source_path: newLink.source_path,
                              target_step_id: newLink.target_step_id,
                              target_path: newLink.target_path,
                            },
                          }] });
                          setNewLink({ source_step_id: "", source_path: "", target_step_id: "", target_path: "" });
                        }}>添加</Button>
                      </div>
                    </div>
                  </Card>
                )}

                <Card size="small" style={{ marginTop: 12 }} title="FlowSpec JSON">
                  <Space direction="vertical" size={8} style={{ width: "100%" }}>
                    <Space size={8} wrap>
                      <Button size="small" onClick={loadJsonDraft}>载入当前 JSON</Button>
                      <Button size="small" type="primary" onClick={applyJsonDraft} disabled={!jsonDraft.trim()}>
                        应用 JSON
                      </Button>
                      {jsonErr && <Typography.Text type="danger" style={{ fontSize: 12 }}>{jsonErr}</Typography.Text>}
                    </Space>
                    <Input.TextArea
                      rows={10}
                      value={jsonDraft}
                      onChange={(e) => setJsonDraft(e.target.value)}
                      placeholder="FlowSpec JSON"
                      style={{ fontFamily: "monospace", fontSize: 11 }}
                    />
                  </Space>
                </Card>
              </Collapse.Panel>
            </Collapse>
          )}

          {result && (
            <Alert
              style={{ marginTop: 12 }} type={result.ok ? "success" : "error"} showIcon
              message={
                <Space size={8} wrap>
                  <span>{result.ok ? `已发布:${result.action}` : `未发布(${result.reason || "见原因"})`}</span>
                  {result.status && STATUS_META[result.status] &&
                    <Tag color={STATUS_META[result.status].color}>{STATUS_META[result.status].label}</Tag>}
                </Space>
              }
              description={
                <Space direction="vertical" size={2}>
                  {result.ok && result.api
                    ? <span>抓到接口 <Typography.Text code>{result.api.method} {result.api.path}</Typography.Text> ·
                        参数 [{(result.api.params || []).join(", ")}] —— agent 可传这些值调用。</span>
                    : result.ok
                      ? <span>风险 {result.risk_level} · 回放 {result.mode} —— 浏览器还开着,可继续录下一个或结束。</span>
                      : <span>删掉不对的步骤(或调整),再点「改完重新发布」。浏览器没关,现场还在。</span>}
                  {result.status === "partially_verified" &&
                    <Typography.Text type="warning" style={{ fontSize: 12 }}>
                      仅结构已验、未真跑活体;在可逆沙箱配置登录态后可升为「已验证」。</Typography.Text>}
                  {(result.warnings || []).map((w, i) =>
                    <Typography.Text key={"w" + i} type="warning" style={{ fontSize: 12 }}>⚠ {w}</Typography.Text>)}
                  {(result.review_notes || []).map((n, i) =>
                    <Typography.Text key={"r" + i} type="secondary" style={{ fontSize: 12 }}>AI 顾问:{n}</Typography.Text>)}
                  {(result.clarifications || []).length > 0 && (
                    <div style={{ marginTop: 6, padding: "6px 10px", border: "1px solid #ffd591",
                                  borderRadius: 6, background: "#fffbe6" }}>
                      <Typography.Text strong style={{ fontSize: 12 }}>
                        请补充/确认以下 {result.clarifications!.length} 项即可(AI 已自动修复其余问题,<b>无需重录</b>):
                      </Typography.Text>
                      <ol style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                        {result.clarifications!.map((c, i) =>
                          <li key={"c" + i}><Typography.Text style={{ fontSize: 12 }}>{c}</Typography.Text></li>)}
                      </ol>
                    </div>
                  )}
                  {result.ok && <Button type="primary" size="small" style={{ marginTop: 4 }} onClick={() => nav("/skills")}>去 Skill 目录调用</Button>}
                </Space>
              }
            />
          )}
        </div>
      )}
    </Card>
  );
}
