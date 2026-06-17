import { useEffect, useRef, useState } from "react";
import {
  Steps, Card, Form, Input, InputNumber, Checkbox, Button, Space, Typography,
  message, List, Tag, Alert, Divider,
} from "antd";
import { PlusOutlined, DeleteOutlined } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import { TENANT_NAME } from "../api/client";
import { fetchSwagger, preview, startOnboard, getJob, PreviewResp, OnboardJob } from "../api/onboarding";

const DEFAULT_FLOW = {
  flow: "submit_leave",
  ti: JSON.stringify({ templateId: "leave_template", values: { title: "测试请假", leaveType: "annual", leaveDays: 1, reason: "测试" } }, null, 2),
};

export default function Onboard() {
  const nav = useNavigate();
  const tenant = localStorage.getItem(TENANT_NAME) || "tenant";
  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);

  const [baseUrl, setBaseUrl] = useState("https://u858758-netf-d87bf18d.westd.seetacloud.com:8443/prod-api");
  const [token, setToken] = useState("");
  const [subsystem, setSubsystem] = useState("A-OA");
  const [swagger, setSwagger] = useState<unknown>(null);
  const [prev, setPrev] = useState<PreviewResp | null>(null);

  const [tags, setTags] = useState<string[]>([]);
  const [maxRead, setMaxRead] = useState(2);
  const [flows, setFlows] = useState([DEFAULT_FLOW]);

  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<OnboardJob | null>(null);
  const timer = useRef<number | null>(null);

  // 拉 swagger + 预览
  async function doFetch() {
    setBusy(true);
    try {
      const sw = await fetchSwagger(baseUrl.trim(), token.trim());
      setSwagger(sw);
      const p = await preview(sw);
      setPrev(p);
      setStep(1);
    } catch (e: any) {
      message.error("拉取/预览失败:" + (e?.response?.data?.detail || e.message));
    } finally {
      setBusy(false);
    }
  }

  // 启动接入 + 轮询
  async function doStart() {
    let parsedFlows;
    try {
      parsedFlows = flows.map((f) => ({ flow: f.flow.trim(), test_input: JSON.parse(f.ti || "{}") }));
    } catch (e: any) {
      message.error("写流程 test_input 不是合法 JSON:" + e.message);
      return;
    }
    setBusy(true);
    try {
      const { job_id } = await startOnboard({
        tenant, subsystem, openapi: swagger,
        deploy: { base_url: baseUrl.trim(), auth: { kind: "token" } },
        credentials: { token: token.trim() },
        include_tags: tags, flows: parsedFlows, max_read_flows: maxRead,
      });
      setJobId(job_id);
      setJob(null);
      setStep(3);
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
        if (j.status !== "running" && timer.current) {
          window.clearInterval(timer.current);
          timer.current = null;
        }
      } catch { /* keep polling */ }
    };
    tick();
    timer.current = window.setInterval(tick, 1500);
    return () => { if (timer.current) window.clearInterval(timer.current); };
  }, [jobId]);

  return (
    <div style={{ maxWidth: 820, margin: "0 auto" }}>
      <Typography.Title level={4}>接入系统(阶段一:导 swagger → 选类别 → 生成 Skill)</Typography.Title>
      <Steps
        current={step}
        style={{ marginBottom: 20 }}
        items={[{ title: "填系统" }, { title: "选类别" }, { title: "声明写流程" }, { title: "生成" }]}
      />

      {step === 0 && (
        <Card>
          <Form layout="vertical">
            <Form.Item label="目标系统 base_url"><Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} /></Form.Item>
            <Form.Item label="OA Bearer token"><Input.Password value={token} onChange={(e) => setToken(e.target.value)} placeholder="用于拉 swagger + 沙箱真试跑" /></Form.Item>
            <Form.Item label="子系统"><Input value={subsystem} onChange={(e) => setSubsystem(e.target.value)} style={{ width: 160 }} /></Form.Item>
            <Button type="primary" loading={busy} onClick={doFetch}>拉取 swagger 并预览类别</Button>
          </Form>
        </Card>
      )}

      {step === 1 && prev && (
        <Card title={`类别预览(共 ${prev.business_action_count} 个业务动作,模板 ${prev.template || "-"})`}>
          <Typography.Paragraph type="secondary">勾选要接入的类别(留空=全部)。</Typography.Paragraph>
          <Checkbox.Group value={tags} onChange={(v) => setTags(v as string[])} style={{ display: "block" }}>
            <Space direction="vertical">
              {prev.categories.map((c) => (
                <Checkbox key={c.tag} value={c.tag}>{c.tag} <Tag>{c.count}</Tag></Checkbox>
              ))}
            </Space>
          </Checkbox.Group>
          <Divider />
          <Space>
            <span>自动生成只读 adapter 上限</span>
            <InputNumber min={0} max={50} value={maxRead} onChange={(v) => setMaxRead(v ?? 0)} />
          </Space>
          <div style={{ marginTop: 16 }}>
            <Space><Button onClick={() => setStep(0)}>上一步</Button><Button type="primary" onClick={() => setStep(2)}>下一步</Button></Space>
          </div>
        </Card>
      )}

      {step === 2 && (
        <Card title="声明写流程(请假/出差等;同端点靠 templateId+字段区分)">
          <Typography.Paragraph type="secondary">读流程(GET)会自动生成,无需声明。写流程必须给 test_input(__base_url__ 后端自动注入,无需填)。</Typography.Paragraph>
          {flows.map((f, i) => (
            <Card key={i} size="small" style={{ marginBottom: 12 }}
              title={<Input value={f.flow} onChange={(e) => { const n = [...flows]; n[i] = { ...f, flow: e.target.value }; setFlows(n); }} style={{ width: 240 }} addonBefore="flow" />}
              extra={<Button size="small" danger icon={<DeleteOutlined />} onClick={() => setFlows(flows.filter((_, j) => j !== i))} />}>
              <Input.TextArea value={f.ti} onChange={(e) => { const n = [...flows]; n[i] = { ...f, ti: e.target.value }; setFlows(n); }} autoSize={{ minRows: 4 }} style={{ fontFamily: "monospace" }} />
            </Card>
          ))}
          <Button icon={<PlusOutlined />} onClick={() => setFlows([...flows, { flow: "", ti: "{}" }])}>加一条写流程</Button>
          <Divider />
          <Space>
            <Button onClick={() => setStep(1)}>上一步</Button>
            <Button type="primary" loading={busy} onClick={doStart}>开始接入(后台生成)</Button>
          </Space>
        </Card>
      )}

      {step === 3 && (
        <Card title="生成进度">
          {!job && <Alert type="info" message="已提交,等待后台开始…" />}
          {job && (
            <>
              <Alert
                style={{ marginBottom: 12 }}
                type={job.status === "completed" ? "success" : job.status === "failed" ? "error" : "info"}
                showIcon
                message={`状态:${job.status}`}
                description={job.error || (job.report?.published_skills ? `已发布:${job.report.published_skills.join(", ") || "(无)"}` : "生成中…(三模型评审较慢,请稍候)")}
              />
              <List
                size="small"
                bordered
                dataSource={job.events}
                style={{ maxHeight: 360, overflow: "auto" }}
                renderItem={(e) => (
                  <List.Item>
                    {e.type === "plan" && <span>计划生成流程:{(e.flows || []).join(", ")}</span>}
                    {e.type === "flow_start" && <span><Tag color="blue">开始</Tag>{e.flow}（{(e.index ?? 0) + 1}/{e.total}）</span>}
                    {e.type === "rejected" && <span><Tag color="orange">驳回</Tag>{e.flow} 第{(e.index ?? 0)}轮:{(e.reasons || []).join("; ")}</span>}
                    {e.type === "published" && <span><Tag color="green">发布</Tag>{e.flow} → {e.asset_id}</span>}
                    {e.type === "exhausted" && <span><Tag color="red">失败</Tag>{e.flow} 耗尽预算</span>}
                    {e.type === "flow_done" && <span><Tag color={e.ok ? "green" : "red"}>完成</Tag>{e.flow} ok={String(e.ok)} 驳回{e.rejections}轮</span>}
                  </List.Item>
                )}
              />
              {job.status === "completed" && (
                <Button type="primary" style={{ marginTop: 12 }} onClick={() => nav("/skills")}>去 Skill 目录</Button>
              )}
            </>
          )}
        </Card>
      )}
    </div>
  );
}
