import { useState } from "react";
import {
  Steps, Card, Form, Input, Button, Space, Typography, message, Table,
  Tag, Alert, Divider, Checkbox, Collapse, List, Segmented, Spin, Select,
} from "antd";
import { useNavigate } from "react-router-dom";
import { TENANT_NAME } from "../api/client";
import {
  scoutPage, onboardPage, onboardPagePi, startPageAgent, getJob,
  PageScoutResp, PageStep, PageOnboardReport, PagePiReport, OnboardJob, OnboardEvent,
} from "../api/onboarding";
import PageRecorder from "../components/PageRecorder";

// 一个可编辑的字段行(由侦察出的输入步派生)
interface FieldRow {
  key: string;
  op: string;          // fill / select / upload
  locator: string;     // 语义定位(只读)
  field: string;       // 绑定的业务字段名(可改 → 成为 Skill 参数)
  required: boolean;   // 是否必填(可勾)
  sample: string;      // 回放用测试值
}

export default function PageOnboard() {
  const nav = useNavigate();
  const tenant = localStorage.getItem(TENANT_NAME) || "";
  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);
  const [genMode, setGenMode] = useState<"manual" | "pi" | "record" | "agent">("record");

  // ── 页面直驱(Agent 自主)模式 ──
  const [goal, setGoal] = useState("");                    // 一句话业务目标(默认空)
  const [token, setToken] = useState("");                  // 登录 token(默认空;治登录:注入会话免登录)
  const [agentAction, setAgentAction] = useState("");      // 导出 skill 名(手填;空则后端随机唯一)
  const [agentJob, setAgentJob] = useState<OnboardJob | null>(null);

  // ── 接入目标 / 登录 ──(subsystem = OA 系统实例,固定几个;不同业务用不同「动作名」区分)
  const [subsystem, setSubsystem] = useState("A-OA");
  const [baseUrl, setBaseUrl] = useState("");
  // 默认填本机登录态文件(页面直驱免登录);token/goal 保持空,按需填
  const [storageState, setStorageState] = useState(
    "E:\\python\\try\\Dano\\back\\.dano-sessions\\123123123__A-OA.json");

  // ── 各模式自己的输入 ──
  const [startUrl, setStartUrl] = useState("");
  const [actionHint, setActionHint] = useState("");        // pi 模式
  const [piResult, setPiResult] = useState<PagePiReport | null>(null);
  const [scout, setScout] = useState<PageScoutResp | null>(null);
  const [rows, setRows] = useState<FieldRow[]>([]);
  const [action, setAction] = useState("submit_form");
  const [title, setTitle] = useState("");
  const [successMarker, setSuccessMarker] = useState("");
  const [report, setReport] = useState<PageOnboardReport | null>(null);

  function deploy(): Record<string, string> { return baseUrl.trim() ? { base_url: baseUrl.trim() } : {}; }
  function creds(): Record<string, string> {
    const c: Record<string, string> = {};
    if (storageState.trim()) c.storage_state = storageState.trim();
    if (token.trim()) c.token = token.trim();   // 预置登录态:注入 cookie+localStorage 免登录
    return c;
  }

  async function doScout() {
    if (!tenant) { message.error("请先到「创建 / 进入租户」"); return; }
    if (!startUrl.trim()) { message.error("请填表单页地址"); return; }
    setBusy(true);
    try {
      const sc = await scoutPage({ tenant, subsystem, start_url: startUrl.trim(), deploy: deploy(), credentials: creds() });
      setScout(sc);
      const fieldSteps = sc.suggested_steps.filter((s) => s.op !== "submit" && s.op !== "goto");
      setRows(fieldSteps.map((s, i) => ({
        key: String(i), op: s.op, locator: s.locator || "", field: s.field || "", required: !!s.required, sample: "",
      })));
      if (!fieldSteps.length) message.warning("页面未发现可填字段,请确认地址/登录态(需登录的页面用「网页录制」)");
      setStep(1);
    } catch (e: any) {
      message.error("侦察失败:" + (e?.response?.data?.detail || e.message));
    } finally { setBusy(false); }
  }

  function patchRow(key: string, p: Partial<FieldRow>) {
    setRows((rs) => rs.map((r) => (r.key === key ? { ...r, ...p } : r)));
  }

  async function doOnboard() {
    if (!action.trim()) { message.error("请填 Skill 动作名(英文,如 submit_reimburse)"); return; }
    const steps: PageStep[] = rows.map((r) => ({ op: r.op, locator: r.locator, field: r.field.trim() || undefined, required: r.required }));
    if (scout?.submit_locator) steps.push({ op: "submit", locator: scout.submit_locator });
    const sample: Record<string, unknown> = {};
    rows.forEach((r) => { if (r.field.trim() && r.sample !== "") sample[r.field.trim()] = r.sample; });
    setBusy(true);
    try {
      const rep = await onboardPage({
        tenant, subsystem, start_url: startUrl.trim(), action: action.trim(),
        title: title.trim(), success_marker: successMarker.trim() || null,
        deploy: deploy(), credentials: creds(), sample_inputs: sample,
        steps, dom_fingerprint: scout?.dom_fingerprint || "",
      });
      setReport(rep);
      setStep(2);
    } catch (e: any) {
      message.error("生成失败:" + (e?.response?.data?.detail || e.message));
    } finally { setBusy(false); }
  }

  async function doPi() {
    if (!tenant) { message.error("请先到「创建 / 进入租户」"); return; }
    if (!startUrl.trim()) { message.error("请填页面地址"); return; }
    setBusy(true);
    setPiResult(null);
    try {
      const r = await onboardPagePi({ tenant, subsystem, start_url: startUrl.trim(), action_hint: actionHint.trim(), deploy: deploy(), credentials: creds() });
      setPiResult(r);
    } catch (e: any) {
      message.error("pi 接入失败:" + (e?.response?.data?.detail || e.message));
    } finally { setBusy(false); }
  }

  async function doAgent() {
    if (!tenant) { message.error("请先到「创建 / 进入租户」"); return; }
    if (!startUrl.trim()) { message.error("请填页面地址 start_url"); return; }
    if (!goal.trim()) { message.error("请填业务目标(如:提交一张请假单)"); return; }
    setBusy(true);
    setAgentJob(null);
    try {
      const { job_id } = await startPageAgent({
        tenant, subsystem, start_url: startUrl.trim(), goal: goal.trim(),
        action: agentAction.trim(),          // 空则后端生成唯一随机 skill 名
        deploy: deploy(), credentials: creds(),
      });
      let done = false;
      while (!done) {
        await new Promise((r) => setTimeout(r, 1500));
        const job = await getJob(job_id);
        setAgentJob(job);
        if (job.status === "completed" || job.status === "failed") done = true;
      }
    } catch (e: any) {
      message.error("页面直驱接入失败:" + (e?.response?.data?.detail || e.message));
    } finally { setBusy(false); }
  }

  // 进度事件 → 一句人话(看 Agent 在干什么)
  function agentStepText(ev: OnboardEvent): string {
    const tool = ev.tool || "";
    if (tool === "page_session_open")
      return ev.at_login ? "⚠ 打开页面 —— 当前停在登录页(登录态无效)"
                         : `打开页面会话(发现 ${ev.fields ?? 0} 个字段)`;
    if (tool === "page_observe")
      return ev.errors && ev.errors.length
        ? `观察页面 · 校验报错:${ev.errors.join("; ")}`
        : `观察页面(${ev.fields ?? 0} 个字段)`;
    if (tool === "page_act")
      return `操作:${ev.op || "?"}${ev.field ? " " + ev.field : ""}${ev.dry ? "(dry)" : ""}` +
             `${ev.ok === false ? " ✗ 未命中元素" : ""}`;
    if (tool === "page_crystallize") return "固化成功轨迹为 Skill";
    if (tool === "sandbox_replay") return "回放验证";
    if (tool === "request_review") return "三模型评审";
    if (tool === "publish_asset") return "发布";
    if (ev.event) return "模型调用工具…";          // pi 原始事件兜底(无细节)
    return ev.note || ev.type || "…";
  }

  // 把事件流映射成流水线阶段(可视化:操作页面→生成候选→Self Check→三维审核→发布)
  const PIPELINE = ["操作页面", "生成 Skill 候选", "确定性 Self Check", "成果验收/漏洞/合规", "发布"];
  function agentStage(): { current: number; status: "process" | "finish" | "error" } {
    const evs = agentJob?.events || [];
    let cur = 0;
    for (const e of evs) {
      if (e.tool === "page_crystallize") cur = 1;
      else if (e.tool === "sandbox_replay") cur = 2;
      else if (e.tool === "request_review") cur = 3;
      else if (e.tool === "publish_asset") cur = 4;
      else if (e.tool && e.tool.indexOf("page_") === 0) cur = 0;
    }
    let status: "process" | "finish" | "error" = "process";
    if (agentJob?.status === "failed") status = "error";
    if (agentJob?.report?.published_skills?.length) { cur = 4; status = "finish"; }
    return { current: cur, status };
  }
  const REVIEW_LABEL: Record<string, string> = {
    acceptance: "成果验收", security: "漏洞检测", compliance: "合规审核",
  };

  const hasSubmit = !!scout?.submit_locator;
  const wide = { maxWidth: 1180, margin: "0 auto" };

  return (
    <div style={wide}>
      {/* <Typography.Title level={4} style={{ marginBottom: 4 }}>接入页面型系统(无 API · 流程8)</Typography.Title> */}
      {/* <Typography.Paragraph type="secondary" style={{ marginBottom: 16 }}>
        给没有开放接口的系统(老 OA / 报销页)生成「页面型 Skill」。<b>推荐「网页录制」</b>:在托管浏览器里走一遍、点提交,
        系统抓下提交请求,你勾选哪些字段当参数即可。<b>密码不经过这里</b>;需登录的系统在画面里登一次,登录态自动复用。
      </Typography.Paragraph> */}

      {/* <Space style={{ marginBottom: 12 }} wrap align="center">
        <Form.Item label="子系统" style={{ marginBottom: 0 }} tooltip="Skill 归类用的业务分组,如 假勤 / 报销 / 会议室;随便填个分类即可">
          <Input value={subsystem} onChange={(e) => setSubsystem(e.target.value)} style={{ width: 160 }} />
        </Form.Item>
      </Space> */}

      <Segmented
        value={genMode} block
        onChange={(v) => setGenMode(v as "manual" | "pi" | "record" | "agent")}
        style={{ marginBottom: 16 }}
        options={[
          { label: "网页录制(推荐 · 免安装 · 需登录的系统用它)", value: "record" },
          { label: "页面直驱(Agent 自主操作 · 给目标即可)", value: "agent" },
          // { label: "逐步确认", value: "manual" },
          // { label: "pi 自动接入", value: "pi" },
        ]}
      />

      {genMode !== "record" && (
        <Collapse ghost style={{ marginBottom: 12 }} items={[{
          key: "adv", label: "高级(系统基址 base_url / 登录态文件,可选)",
          children: (
            <Space size="large" wrap align="start">
              <Form.Item label="系统基址 base_url" style={{ marginBottom: 0 }} extra="start_url 为相对路径时拼接">
                <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} style={{ width: 360 }} />
              </Form.Item>
              <Form.Item label="登录态 storage_state 路径" style={{ marginBottom: 0 }} extra="可填 Playwright storageState JSON 路径">
                <Input value={storageState} onChange={(e) => setStorageState(e.target.value)}
                       placeholder="/opt/dano/secrets/oa-storage.json" style={{ width: 360 }} />
              </Form.Item>
            </Space>
          ),
        }]} />
      )}

      {genMode === "manual" && (
        <>
          <Steps current={step} size="small" style={{ marginBottom: 16 }}
                 items={[{ title: "填表单页地址" }, { title: "确认字段映射" }, { title: "回放并发布" }]} />

          {step === 0 && (
            <Card size="small">
              <Form.Item label="表单页地址 start_url" required style={{ marginBottom: 12 }}
                         extra="要接入的业务表单页(绝对 URL;或相对系统基址)">
                <Input value={startUrl} onChange={(e) => setStartUrl(e.target.value)} onPressEnter={doScout}
                       placeholder="https://oa.example.com/reimburse/new" />
              </Form.Item>
              <Button type="primary" loading={busy} onClick={doScout}>侦察页面</Button>
            </Card>
          )}

          {step === 1 && (
            <Card size="small" title="确认字段映射(改字段名 = Skill 参数;勾必填;填测试值用于回放)">
              <div style={{ marginBottom: 12 }}>
                提交按钮:{hasSubmit
                  ? <><Tag color="orange">{scout?.submit_locator}</Tag><Typography.Text type="secondary"> 写页面 · L3 · 需三模型评审</Typography.Text></>
                  : <Tag>未发现(作为查询页面 · L1)</Tag>}
              </div>
              <Table<FieldRow>
                rowKey="key" size="small" pagination={false} dataSource={rows}
                locale={{ emptyText: "未发现可填字段" }}
                columns={[
                  { title: "操作", dataIndex: "op", width: 70, render: (v) => <Tag>{v}</Tag> },
                  { title: "定位(语义)", dataIndex: "locator", ellipsis: true, render: (v) => <Typography.Text code style={{ fontSize: 12 }}>{v}</Typography.Text> },
                  { title: "字段名(Skill 参数)", dataIndex: "field", width: 180, render: (_, r) => <Input size="small" value={r.field} onChange={(e) => patchRow(r.key, { field: e.target.value })} /> },
                  { title: "必填", dataIndex: "required", width: 56, render: (_, r) => <Checkbox checked={r.required} onChange={(e) => patchRow(r.key, { required: e.target.checked })} /> },
                  { title: "测试值(回放)", dataIndex: "sample", width: 150, render: (_, r) => <Input size="small" value={r.sample} onChange={(e) => patchRow(r.key, { sample: e.target.value })} /> },
                ]}
              />
              <Divider style={{ margin: "12px 0" }} />
              <Space size="large" wrap>
                <Form.Item label="Skill 动作名(英文)" required style={{ marginBottom: 8 }}>
                  <Input value={action} onChange={(e) => setAction(e.target.value)} placeholder="submit_reimburse" style={{ width: 200 }} />
                </Form.Item>
                <Form.Item label="标题(中文)" style={{ marginBottom: 8 }}>
                  <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="提交报销" style={{ width: 180 }} />
                </Form.Item>
                <Form.Item label="成功标志" style={{ marginBottom: 8 }} tooltip="提交成功后出现的元素/文本,如 text=保存成功;dry 回放可留空">
                  <Input value={successMarker} onChange={(e) => setSuccessMarker(e.target.value)} placeholder="text=保存成功" style={{ width: 180 }} />
                </Form.Item>
              </Space>
              <div>
                <Space>
                  <Button onClick={() => setStep(0)}>上一步</Button>
                  <Button type="primary" loading={busy} onClick={doOnboard}>生成并发布</Button>
                </Space>
              </div>
            </Card>
          )}

          {step === 2 && report && (
            <Card size="small" title="回放并发布结果">
              <Alert
                style={{ marginBottom: 12 }} type={report.ok ? "success" : "error"} showIcon
                message={report.ok ? "已发布页面型 Skill" : `未发布(卡在:${report.stage || "?"})`}
                description={report.ok
                  ? <Space direction="vertical" size={2}><span>动作:{report.action} · 风险 {report.risk_level} · 回放 {report.mode}</span><span>资产:{report.asset_id}</span></Space>
                  : (report.reason || "见下方评审意见")}
              />
              {report.verdicts && report.verdicts.length > 0 && (
                <List size="small" bordered header="三模型评审意见" dataSource={report.verdicts}
                  renderItem={(v) => (
                    <List.Item>
                      <Space direction="vertical" size={2} style={{ width: "100%" }}>
                        <Space>{v.passed ? <Tag color="green">通过</Tag> : <Tag color="red">驳回</Tag>}<Typography.Text strong>{v.role}</Typography.Text><Typography.Text type="secondary" style={{ fontSize: 12 }}>{v.model}</Typography.Text></Space>
                        {v.reasons?.map((r, i) => (<Typography.Text key={i} type="secondary" style={{ fontSize: 12 }}>· {r}</Typography.Text>))}
                      </Space>
                    </List.Item>
                  )}
                />
              )}
              <Divider style={{ margin: "12px 0" }} />
              <Space>
                {report.ok ? <Button type="primary" onClick={() => nav("/skills")}>去 Skill 目录调用</Button>
                  : <Button type="primary" onClick={() => setStep(1)}>返回修改</Button>}
                <Button onClick={() => { setStep(0); setScout(null); setReport(null); }}>接入下一个</Button>
              </Space>
            </Card>
          )}
        </>
      )}

      {genMode === "pi" && (
        <Card size="small">
          <Space size="large" wrap align="end" style={{ marginBottom: 4 }}>
            <Form.Item label="页面地址 start_url" required style={{ marginBottom: 8 }}>
              <Input value={startUrl} onChange={(e) => setStartUrl(e.target.value)} placeholder="https://oa.example.com/reimburse/new" style={{ width: 360 }} />
            </Form.Item>
            <Form.Item label="建议动作名(可选,英文)" style={{ marginBottom: 8 }} extra="留空则由 pi 自行命名">
              <Input value={actionHint} onChange={(e) => setActionHint(e.target.value)} placeholder="submit_reimburse" style={{ width: 200 }} />
            </Form.Item>
          </Space>
          <div>
            <Button type="primary" loading={busy} onClick={doPi}>pi 自动接入</Button>
            <Typography.Text type="secondary" style={{ marginLeft: 12, fontSize: 12 }}>pi 真实打开页面、逐步自主决策,约 1–3 分钟。</Typography.Text>
          </div>
          {busy && (
            <div style={{ marginTop: 16, textAlign: "center" }}>
              <Spin tip="pi 正在自主接入(侦察 / 建体 / 回放 / 评审 / 发布)…"><div style={{ height: 40 }} /></Spin>
            </div>
          )}
          {piResult && !busy && (
            <Alert
              style={{ marginTop: 16 }} type={piResult.published_skills?.length ? "success" : "warning"} showIcon
              message={piResult.published_skills?.length ? `pi 已发布:${piResult.published_skills.join(", ")}` : `未发布(pi 状态:${piResult.pi_status || "?"})`}
              description={
                <Space direction="vertical" size={2}>
                  <span>pi 状态:{piResult.pi_status} · 工具调用 {piResult.tool_events ?? 0} 次</span>
                  {piResult.final_text && (
                    <div style={{ maxHeight: 200, overflow: "auto", whiteSpace: "pre-wrap",
                                  background: "#fafafa", border: "1px solid #f0f0f0", borderRadius: 4,
                                  padding: "6px 10px", fontSize: 12, lineHeight: 1.6 }}>
                      {piResult.final_text}
                    </div>
                  )}
                  {piResult.error && <Typography.Text type="danger">{piResult.error}</Typography.Text>}
                  {!!piResult.published_skills?.length && <Button type="primary" size="small" style={{ marginTop: 6 }} onClick={() => nav("/skills")}>去 Skill 目录调用</Button>}
                </Space>
              }
            />
          )}
        </Card>
      )}

      {genMode === "agent" && (
        <Card size="small">
          <Typography.Paragraph type="secondary" style={{ marginBottom: 12 }}>
            给一个<b>页面地址</b> + 一句<b>业务目标</b> + 测试登录态,<b>Agent 自己打开页面、理解、操作、跑通</b>,
            把它自己跑通的成功轨迹固化为可调用的 Skill。<b>无需录人</b>;首跑默认 dry(只填不真提交)。
          </Typography.Paragraph>
          <Space size="large" wrap align="end" style={{ marginBottom: 4 }}>
            <Form.Item label="页面地址 start_url" required style={{ marginBottom: 8 }}>
              <Input value={startUrl} onChange={(e) => setStartUrl(e.target.value)}
                     placeholder="https://oa.example.com/leave/new" style={{ width: 360 }} />
            </Form.Item>
            <Form.Item label="Skill 名称(可选)" style={{ marginBottom: 8 }}
                       tooltip="导出的 Skill 名,英文/拼音;留空则系统生成唯一随机名">
              <Input value={agentAction} onChange={(e) => setAgentAction(e.target.value)}
                     placeholder="留空=随机唯一" style={{ width: 220 }} />
            </Form.Item>
          </Space>
          {/* 业务目标 goal 与 登录 token 同一排,均默认空 */}
          <Space size="large" wrap align="end" style={{ marginBottom: 8 }}>
            <Form.Item label="业务目标 goal" required style={{ marginBottom: 8 }}
                       tooltip="一句话说清要 Agent 达成什么,如:提交一张请假单 / 新建一条报销">
              <Input value={goal} onChange={(e) => setGoal(e.target.value)}
                     placeholder="" style={{ width: 320 }} />
            </Form.Item>
            <Form.Item label="登录 token" style={{ marginBottom: 8 }}
                       tooltip="需登录的系统必填:粘贴一个有效登录 token,系统在打开页面前注入(cookie+localStorage),免登录。RSA/验证码登录的系统改用高级里的 storage_state。">
              <Input.Password value={token} onChange={(e) => setToken(e.target.value)}
                              placeholder="" style={{ width: 360 }} />
            </Form.Item>
          </Space>
          <div>
            <Button type="primary" loading={busy} onClick={doAgent}>Agent 自主接入</Button>
            <Typography.Text type="secondary" style={{ marginLeft: 12, fontSize: 12 }}>
              Agent 真实打开页面、逐步操作并自纠错,约 1–5 分钟;下方实时展示操作步骤。
            </Typography.Text>
          </div>

          {agentJob && (() => {
            const st = agentStage();
            const lastReview = [...(agentJob.events || [])].reverse().find((e) => e.reviews);
            return (
              <Card size="small" type="inner" title="接入流水线" style={{ marginTop: 16 }}>
                <Steps size="small" current={st.current} status={st.status}
                       items={PIPELINE.map((t) => ({ title: t }))} />
                {lastReview?.reviews && (
                  <div style={{ marginTop: 12 }}>
                    <Space size={8} wrap>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>三维审核:</Typography.Text>
                      {lastReview.reviews.map((r) => (
                        <Tag key={r.role} color={r.passed ? "green" : "red"}>
                          {REVIEW_LABEL[r.role] || r.role} {r.passed ? "✓" : "✗"}
                        </Tag>
                      ))}
                      {lastReview.all_passed === false &&
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>(有阻断项 → 受限修复后重审)</Typography.Text>}
                    </Space>
                  </div>
                )}
              </Card>
            );
          })()}

          {(() => {
            const evs = agentJob?.events || [];
            const atLogin = evs.some((e) => e.at_login);
            const shotEv = [...evs].reverse().find((e) => e.screenshot);
            return (
              <>
                {atLogin && (
                  <Alert style={{ marginTop: 16 }} type="error" showIcon
                    message="Agent 打开页面后停在登录页 —— 登录态无效"
                    description="该系统需要登录。请在上方「登录 token」填一个有效 token(或在高级里填 storage_state)后重试,否则 Agent 无法操作页面。" />
                )}
                {shotEv?.screenshot && (
                  <Card size="small" type="inner" title="当前页面(Agent 实时所见)" style={{ marginTop: 16 }}>
                    <img src={shotEv.screenshot} alt="page" style={{ maxWidth: "100%", border: "1px solid #f0f0f0", borderRadius: 4 }} />
                  </Card>
                )}
              </>
            );
          })()}

          {agentJob && (agentJob.events.length > 0 || agentJob.status === "running") && (
            <Card size="small" type="inner" title="Agent 操作过程(实时)" style={{ marginTop: 16 }}>
              <List
                size="small" dataSource={agentJob.events} locale={{ emptyText: "等待 Agent 启动…" }}
                style={{ maxHeight: 280, overflow: "auto" }}
                renderItem={(ev) => (
                  <List.Item style={{ padding: "4px 0" }}>
                    <Space size={6}>
                      <Tag color={ev.errors && ev.errors.length ? "red" : ev.tool ? "blue" : "default"}>
                        {ev.tool || ev.event || ev.phase || ev.type}
                      </Tag>
                      <Typography.Text style={{ fontSize: 13 }}>{agentStepText(ev)}</Typography.Text>
                    </Space>
                  </List.Item>
                )}
              />
              {agentJob.status === "running" && (
                <div style={{ textAlign: "center", marginTop: 8 }}>
                  <Spin size="small" /> <Typography.Text type="secondary" style={{ fontSize: 12 }}>进行中…</Typography.Text>
                </div>
              )}
            </Card>
          )}

          {agentJob?.report && (
            <Alert
              style={{ marginTop: 16 }} showIcon
              type={agentJob.report.published_skills?.length ? "success" : "warning"}
              message={agentJob.report.published_skills?.length
                ? `Agent 已发布:${agentJob.report.published_skills.join(", ")}`
                : `未发布(状态:${agentJob.report.status || agentJob.status})`}
              description={
                <Space direction="vertical" size={2}>
                  {!!agentJob.report.published_skills?.length &&
                    <Button type="primary" size="small" style={{ marginTop: 6 }}
                            onClick={() => nav("/skills")}>去 Skill 目录调用</Button>}
                </Space>
              }
            />
          )}
          {agentJob?.status === "failed" && agentJob.error && (
            <Alert style={{ marginTop: 12 }} type="error" showIcon message="接入失败" description={agentJob.error} />
          )}
        </Card>
      )}

      {genMode === "record" && (
        <PageRecorder tenant={tenant} subsystem={subsystem} baseUrl={baseUrl} storageState={storageState} />
      )}
    </div>
  );
}
