import { useEffect, useRef, useState } from "react";
import {
  Steps, Card, Form, Input, Checkbox, Button, Space, Typography,
  message, List, Tag, Alert, Divider, Radio, Upload, Empty,
} from "antd";
import { UploadOutlined } from "@ant-design/icons";
import type { UploadProps } from "antd";
import { useNavigate } from "react-router-dom";
import { TENANT_NAME } from "../api/client";
import {
  fetchSwaggerByUrl, listTemplates, templateForm, startOnboard, getJob,
  BizTemplate, FormField, OnboardJob, OnboardEvent,
} from "../api/onboarding";

// 把一个进度事件渲染成一行控制台日志:{颜色, 文本}
function logLine(e: OnboardEvent): { color: string; text: string } {
  const r = (e.iter ?? 0) + 1;
  switch (e.type) {
    case "plan": return { color: "#89b4fa", text: `计划生成 ${(e.flows || []).length} 个流程:${(e.flows || []).join(", ")}` };
    case "flow_start": return { color: "#89b4fa", text: `▶ [${e.flow}] 开始 (${(e.index ?? 0) + 1}/${e.total})${e.route === "seed" ? " · 种子(已验证)" : e.route === "reused" ? " · 复用已发布" : e.route === "read" ? " · 只读/确定性" : e.route === "llm" ? " · LLM 拆解" : ""}` };
    case "replanned": return { color: "#cba6f7", text: `  ↺ 第${e.attempt ?? 1}次重拆方案(上一版被事实核查证伪)` };
    case "coding": return { color: "#cdd6f4", text: `  第${r}轮 ${e.fixing ? "按驳回原因修复" : "编码"}中…(策略 ${e.strategy})` };
    case "coded": return { color: "#a6adc8", text: `  第${r}轮 生成代码 ${e.lines} 行` };
    case "testing": return { color: "#f9e2af", text: `  沙箱真跑中(打目标系统)…` };
    case "reviewing": return { color: "#f9e2af", text: `  三模型评审中…` };
    case "gate": return e.passed
      ? { color: "#a6e3a1", text: `  ✅ ${e.gate} 通过` }
      : { color: "#f38ba8", text: `  ❌ ${e.gate} 驳回:${e.detail || ""}` };
    case "verdict": return e.passed
      ? { color: "#a6e3a1", text: `    · ${e.role}(${e.model}) 通过` }
      : { color: "#fab387", text: `    · ${e.role}(${e.model}) 驳回:${e.detail || ""}` };
    case "rejected": return { color: "#fab387", text: `  ↩ 第${r}轮 驳回,回灌重写:${(e.reasons || []).join("; ")}` };
    case "published": return { color: "#a6e3a1", text: `  🚀 上架 → ${e.asset_id}` };
    case "exhausted": return { color: "#f38ba8", text: `  ⛔ [${e.flow}] 耗尽预算,未通过` };
    case "flow_done": return { color: e.ok ? "#a6e3a1" : "#f38ba8", text: `■ [${e.flow}] 完成 ok=${e.ok} 驳回${e.rejections}轮` };
    default: return { color: "#a6adc8", text: `${e.type} ${JSON.stringify(e)}` };
  }
}
function hhmmss(ts?: number): string {
  if (!ts) return "--:--:--";
  const d = new Date(ts * 1000);
  return [d.getHours(), d.getMinutes(), d.getSeconds()].map((n) => String(n).padStart(2, "0")).join(":");
}

// 业务模板 → 生成用的 flow 名(ASCII;function-calling 工具名不能含中文)
function flowName(t: BizTemplate): string {
  const base = (t.defKey || t.templateId).toString().replace(/[^a-zA-Z0-9_]/g, "_");
  return /[a-zA-Z]/.test(base) ? `submit_${base}` : `submit_tpl_${t.templateId}`;
}

export default function Onboard() {
  const nav = useNavigate();
  const tenant = localStorage.getItem(TENANT_NAME) || "tenant";
  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);

  const [baseUrl, setBaseUrl] = useState("https://u858758-netf-d87bf18d.westd.seetacloud.com:8443/prod-api");
  const [token, setToken] = useState("");
  const [subsystem, setSubsystem] = useState("A-OA");

  // 手动导入 swagger:上传 .json 文件 或 写 swagger 地址(生成时按它定位接口)
  const [importMode, setImportMode] = useState<"file" | "url">("file");
  const [swaggerUrl, setSwaggerUrl] = useState("https://u858758-netf-d87bf18d.westd.seetacloud.com:8443/prod-api/v3/api-docs");
  const [swagger, setSwagger] = useState<unknown>(null);
  const [swaggerLabel, setSwaggerLabel] = useState("");

  // 业务模板(查 OA 真实模板清单:请假/报销/…)
  const [templates, setTemplates] = useState<BizTemplate[]>([]);
  const [selT, setSelT] = useState<Record<string, boolean>>({});
  const [valMap, setValMap] = useState<Record<string, string>>({});
  const [formFields, setFormFields] = useState<Record<string, FormField[]>>({});

  // 勾选模板 → 自动查它的表单字段,并据此预填 values 骨架(请假样例不覆盖)
  async function onToggleTemplate(t: BizTemplate, checked: boolean) {
    setSelT((p) => ({ ...p, [t.templateId]: checked }));
    if (!checked || formFields[t.templateId]) return;
    try {
      const fields = await templateForm(baseUrl.trim(), token.trim(), t.templateId);
      setFormFields((p) => ({ ...p, [t.templateId]: fields }));
      if (fields.length) {
        setValMap((p) => {
          const cur = (p[t.templateId] || "").trim();
          if (cur && cur !== "{}") return p;            // 已有内容(如请假样例)不覆盖
          const skel: Record<string, string> = {};
          fields.forEach((f) => { skel[f.key] = ""; });
          return { ...p, [t.templateId]: JSON.stringify(skel, null, 2) };
        });
      }
    } catch { /* 查不到字段就让用户手填,不打断 */ }
  }

  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<OnboardJob | null>(null);
  const timer = useRef<number | null>(null);
  const consoleRef = useRef<HTMLDivElement | null>(null);

  // 控制台:有新日志时自动滚到底
  useEffect(() => {
    const el = consoleRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [job?.events?.length]);

  const uploadProps: UploadProps = {
    accept: ".json,application/json",
    maxCount: 1,
    showUploadList: false,
    beforeUpload: (file) => {
      const reader = new FileReader();
      reader.onload = () => {
        try {
          setSwagger(JSON.parse(String(reader.result || "")));
          setSwaggerLabel(`${file.name}(已读取)`);
          message.success("已读取 swagger 文件");
        } catch (e: any) {
          message.error("文件不是合法 JSON:" + e.message);
        }
      };
      reader.readAsText(file);
      return false;
    },
  };

  // step0 → 导入 swagger + 查 OA 真实业务模板
  async function doNext() {
    setBusy(true);
    try {
      let sw = swagger;
      if (importMode === "url") {
        if (!swaggerUrl.trim()) { message.error("请填 swagger 地址"); return; }
        sw = await fetchSwaggerByUrl(swaggerUrl.trim(), token.trim());
        setSwagger(sw); setSwaggerLabel(`${swaggerUrl.trim()}(已代取)`);
      }
      if (!sw) { message.error("请先上传 .json 文件或填 swagger 地址"); return; }
      if (!baseUrl.trim()) { message.error("请填目标系统 base_url"); return; }
      if (!token.trim()) { message.error("请填 OA token(查业务模板要用)"); return; }
      const tpls = await listTemplates(baseUrl.trim(), token.trim());
      const s: Record<string, boolean> = {}, v: Record<string, string> = {};
      tpls.forEach((t) => { s[t.templateId] = false; v[t.templateId] = "{}"; });  // 勾选时按真实表单字段生成骨架
      setTemplates(tpls); setSelT(s); setValMap(v);
      setStep(1);
    } catch (e: any) {
      message.error("查业务模板失败:" + (e?.response?.data?.detail || e.message));
    } finally {
      setBusy(false);
    }
  }

  // step1 → 生成选中的业务模板(每个模板一个复合 skill)
  async function doStart() {
    const chosen = templates.filter((t) => selT[t.templateId]);
    if (!chosen.length) { message.error("请至少选一个业务模板"); return; }
    const flows: { flow: string; actions: string[]; test_input: Record<string, unknown> }[] = [];
    for (const t of chosen) {
      let values: Record<string, unknown> = {};
      try { values = JSON.parse(valMap[t.templateId] || "{}"); }
      catch (e: any) { message.error(`「${t.name}」表单值不是合法 JSON:` + e.message); return; }
      flows.push({ flow: flowName(t), actions: [], test_input: { templateId: t.templateId, values } });
    }
    setBusy(true);
    try {
      const { job_id } = await startOnboard({
        tenant, subsystem, openapi: swagger,
        deploy: { base_url: baseUrl.trim(), auth: { kind: "token" } },
        credentials: { token: token.trim() },
        include_tags: [], flows, max_read_flows: 0,
      });
      setJobId(job_id); setJob(null); setStep(2);
    } catch (e: any) {
      message.error("启动失败:" + (e?.response?.data?.detail || e.message));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (!jobId) return;
    const tick = async () => {
      try {
        const j = await getJob(jobId);
        setJob(j);
        if (j.status !== "running" && timer.current) { window.clearInterval(timer.current); timer.current = null; }
      } catch { /* keep polling */ }
    };
    tick();
    timer.current = window.setInterval(tick, 1500);
    return () => { if (timer.current) window.clearInterval(timer.current); };
  }, [jobId]);

  const chosenCount = templates.filter((t) => selT[t.templateId]).length;

  return (
    <div style={{ maxWidth: 860, margin: "0 auto" }}>
      <Typography.Title level={4}>接入系统(导入 swagger → 选业务模板 → 生成上架)</Typography.Title>
      <Steps
        current={step}
        style={{ marginBottom: 20 }}
        items={[{ title: "导入 swagger" }, { title: "选业务模板" }, { title: "生成上架" }]}
      />

      {step === 0 && (
        <Card>
          <Form layout="vertical">
            <Form.Item label="导入方式(手动)">
              <Radio.Group value={importMode} onChange={(e) => setImportMode(e.target.value)}>
                <Radio.Button value="file">上传 .json 文件</Radio.Button>
                <Radio.Button value="url">写 swagger 地址</Radio.Button>
              </Radio.Group>
            </Form.Item>
            {importMode === "file" && (
              <Form.Item label="swagger 文件(.json)">
                <Space>
                  <Upload {...uploadProps}><Button icon={<UploadOutlined />}>选择 .json 文件</Button></Upload>
                  {swaggerLabel && <Tag color="green">{swaggerLabel}</Tag>}
                </Space>
              </Form.Item>
            )}
            {importMode === "url" && (
              <Form.Item label="swagger 地址" extra="后端代取(浏览器跨域/自签证书拉不了)">
                <Input value={swaggerUrl} onChange={(e) => setSwaggerUrl(e.target.value)} placeholder="https://.../v3/api-docs" />
              </Form.Item>
            )}
            <Divider />
            <Form.Item label="目标系统 base_url" extra="查业务模板 + 沙箱真试跑 + 调用都打这个地址">
              <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
            </Form.Item>
            <Form.Item label="OA Bearer token" extra="在网页填;用来查该 OA 的业务模板 + 沙箱真跑 + 调用">
              <Input.Password value={token} onChange={(e) => setToken(e.target.value)} />
            </Form.Item>
            <Form.Item label="子系统"><Input value={subsystem} onChange={(e) => setSubsystem(e.target.value)} style={{ width: 160 }} /></Form.Item>
            <Button type="primary" loading={busy} onClick={doNext}>导入并查业务模板</Button>
          </Form>
        </Card>
      )}

      {step === 1 && (
        <Card title={`选业务模板(查到 ${templates.length} 个,已选 ${chosenCount})`}>
          <Alert
            style={{ marginBottom: 12 }} type="info" showIcon
            message="这是从你这台 OA 查到的真实流程模板(请假/报销/…)。勾选要做成 skill 的,并按该模板表单填一组测试值(沙箱会拿它真跑);请假已带样例。"
          />
          {templates.length === 0 ? <Empty description="没查到模板" /> : (
            <List
              size="small" bordered dataSource={templates}
              style={{ maxHeight: 460, overflow: "auto" }}
              renderItem={(t) => (
                <List.Item>
                  <Space direction="vertical" size={4} style={{ width: "100%" }}>
                    <Space wrap>
                      <Checkbox checked={!!selT[t.templateId]} onChange={(e) => onToggleTemplate(t, e.target.checked)} />
                      <Typography.Text strong>{t.name}</Typography.Text>
                      {t.type && <Tag>{t.type}</Tag>}
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>templateId={t.templateId}{t.defKey ? ` · ${t.defKey}` : ""}</Typography.Text>
                    </Space>
                    {selT[t.templateId] && (
                      <>
                        {formFields[t.templateId]?.length ? (
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            该模板表单字段:{formFields[t.templateId].map((f) => (
                              <Tag key={f.key} color="gold">{f.key}{f.label && f.label !== f.key ? `(${f.label})` : ""}</Tag>
                            ))}
                          </Typography.Text>
                        ) : (
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>未取到表单字段,按该流程实际需要手填</Typography.Text>
                        )}
                        <Input.TextArea
                          value={valMap[t.templateId]}
                          onChange={(e) => setValMap({ ...valMap, [t.templateId]: e.target.value })}
                          autoSize={{ minRows: 3 }} style={{ fontFamily: "monospace" }}
                          placeholder='该模板表单的字段值,如 {"title":"...","reason":"..."}'
                        />
                      </>
                    )}
                  </Space>
                </List.Item>
              )}
            />
          )}
          <Divider />
          <Space>
            <Button onClick={() => setStep(0)}>上一步</Button>
            <Button type="primary" loading={busy} onClick={doStart}>生成选中模板(三模型真跑把关)</Button>
          </Space>
        </Card>
      )}

      {step === 2 && (
        <Card title="生成上架(三道把关 + 测试账号真跑,过了才上架)">
          {!job && <Alert type="info" message="已提交,等待后台开始…" />}
          {job && (
            <>
              <Alert
                style={{ marginBottom: 12 }}
                type={job.status === "completed" ? "success" : job.status === "failed" ? "error" : "info"}
                showIcon
                message={`状态:${job.status}`}
                description={job.error || (job.report?.published_skills ? `已上架:${job.report.published_skills.join(", ") || "(无)"}` : "生成中…(三模型评审较慢,请稍候)")}
              />
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>控制台 · 实时日志({job.events.length} 行)</Typography.Text>
                {job.status === "running" && <Tag color="processing">运行中</Tag>}
              </div>
              <div
                ref={consoleRef}
                style={{
                  background: "#0b0e14", color: "#cdd6f4", borderRadius: 6, padding: "10px 12px",
                  height: 380, overflow: "auto", fontFamily: "Consolas, 'Courier New', monospace",
                  fontSize: 12.5, lineHeight: 1.7, whiteSpace: "pre-wrap", wordBreak: "break-word",
                }}
              >
                {job.events.length === 0 && <span style={{ color: "#6c7086" }}>等待后台输出…</span>}
                {job.events.map((e, idx) => {
                  const { color, text } = logLine(e);
                  return (
                    <div key={idx}>
                      <span style={{ color: "#6c7086" }}>{hhmmss(e.ts)} </span>
                      <span style={{ color }}>{text}</span>
                    </div>
                  );
                })}
                {job.status === "running" && <span style={{ color: "#6c7086" }}>▍</span>}
              </div>
              {job.status === "completed" && (
                <Button type="primary" style={{ marginTop: 12 }} onClick={() => nav("/skills")}>去 Skill 目录调用</Button>
              )}
            </>
          )}
        </Card>
      )}
    </div>
  );
}
