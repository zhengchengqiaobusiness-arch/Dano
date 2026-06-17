import { useEffect, useRef, useState } from "react";
import {
  Steps, Card, Form, Input, InputNumber, Checkbox, Button, Space, Typography,
  message, List, Tag, Alert, Divider, Radio, Upload,
} from "antd";
import { PlusOutlined, DeleteOutlined, UploadOutlined } from "@ant-design/icons";
import type { UploadProps } from "antd";
import { useNavigate } from "react-router-dom";
import { TENANT_NAME } from "../api/client";
import { fetchSwaggerByUrl, preview, startOnboard, getJob, PreviewResp, OnboardJob } from "../api/onboarding";

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

  // 手动导入 swagger:上传 .json 文件 或 写 swagger 地址
  const [importMode, setImportMode] = useState<"file" | "url">("file");
  const [swaggerUrl, setSwaggerUrl] = useState("https://u858758-netf-d87bf18d.westd.seetacloud.com:8443/prod-api/v3/api-docs");
  const [swagger, setSwagger] = useState<unknown>(null);
  const [swaggerLabel, setSwaggerLabel] = useState("");   // 已导入提示
  const [prev, setPrev] = useState<PreviewResp | null>(null);

  const [tags, setTags] = useState<string[]>([]);
  const [maxRead, setMaxRead] = useState(2);
  const [flows, setFlows] = useState([DEFAULT_FLOW]);

  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<OnboardJob | null>(null);
  const timer = useRef<number | null>(null);

  // 上传 .json 文件 → 本地解析(不经后端)
  const uploadProps: UploadProps = {
    accept: ".json,application/json",
    maxCount: 1,
    showUploadList: false,
    beforeUpload: (file) => {
      const reader = new FileReader();
      reader.onload = () => {
        try {
          const obj = JSON.parse(String(reader.result || ""));
          setSwagger(obj);
          setSwaggerLabel(`${file.name}(已读取)`);
          message.success("已读取 swagger 文件");
        } catch (e: any) {
          message.error("文件不是合法 JSON:" + e.message);
        }
      };
      reader.readAsText(file);
      return false;   // 阻止真正上传,纯前端读取
    },
  };

  // 解析导入的 swagger → 预览类别/动作
  async function doImportAndPreview() {
    setBusy(true);
    try {
      let sw = swagger;
      if (importMode === "url") {
        if (!swaggerUrl.trim()) { message.error("请填 swagger 地址"); return; }
        sw = await fetchSwaggerByUrl(swaggerUrl.trim(), token.trim());
        setSwagger(sw);
        setSwaggerLabel(`${swaggerUrl.trim()}(已代取)`);
      }
      if (!sw) { message.error("请先上传 .json 文件或填 swagger 地址"); return; }
      if (!baseUrl.trim()) { message.error("请填目标系统 base_url"); return; }
      const p = await preview(sw);
      setPrev(p);
      setStep(1);
    } catch (e: any) {
      message.error("导入/预览失败:" + (e?.response?.data?.detail || e.message));
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

  // 选中类别下的动作(用于 step1 展示"含必填"清单)
  const shownActions = (prev?.actions || []).filter(
    (a) => tags.length === 0 || a.tags.some((t) => tags.includes(t)),
  );

  return (
    <div style={{ maxWidth: 860, margin: "0 auto" }}>
      <Typography.Title level={4}>接入系统(阶段一:导入 swagger → 解析选类别 → 声明写流程 → 生成)</Typography.Title>
      <Steps
        current={step}
        style={{ marginBottom: 20 }}
        items={[{ title: "导入 swagger" }, { title: "解析·选类别" }, { title: "声明写流程" }, { title: "生成" }]}
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
              <Form.Item label="swagger 地址" extra="后端代取(浏览器跨域/自签证书拉不了);需鉴权时用下面的 token">
                <Input value={swaggerUrl} onChange={(e) => setSwaggerUrl(e.target.value)} placeholder="https://.../v3/api-docs" />
              </Form.Item>
            )}

            <Divider />
            <Form.Item label="目标系统 base_url" extra="生成时沙箱真试跑 + 之后调用都打这个地址">
              <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
            </Form.Item>
            <Form.Item label="OA Bearer token" extra="在网页填;用于代取 swagger(地址方式)+ 沙箱真试跑 + 调用">
              <Input.Password value={token} onChange={(e) => setToken(e.target.value)} />
            </Form.Item>
            <Form.Item label="子系统"><Input value={subsystem} onChange={(e) => setSubsystem(e.target.value)} style={{ width: 160 }} /></Form.Item>

            <Button type="primary" loading={busy} onClick={doImportAndPreview}>解析并预览类别</Button>
          </Form>
        </Card>
      )}

      {step === 1 && prev && (
        <Card title={`解析结果(共 ${prev.business_action_count} 个业务动作,模板 ${prev.template || "-"})`}>
          <Typography.Paragraph type="secondary">勾选要接入的类别(留空=全部);下方按勾选实时列出动作与必填字段。</Typography.Paragraph>
          <Checkbox.Group value={tags} onChange={(v) => setTags(v as string[])} style={{ display: "block" }}>
            <Space direction="vertical">
              {prev.categories.map((c) => (
                <Checkbox key={c.tag} value={c.tag}>{c.tag} <Tag>{c.count}</Tag></Checkbox>
              ))}
            </Space>
          </Checkbox.Group>

          <Divider orientation="left">动作清单（{shownActions.length}）</Divider>
          <List
            size="small"
            bordered
            dataSource={shownActions}
            style={{ maxHeight: 320, overflow: "auto" }}
            renderItem={(a) => (
              <List.Item>
                <Space direction="vertical" size={2} style={{ width: "100%" }}>
                  <Space wrap>
                    <Tag color={a.method === "GET" ? "blue" : "volcano"}>{a.method}</Tag>
                    <Typography.Text strong>{a.name}</Typography.Text>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>{a.endpoint}</Typography.Text>
                  </Space>
                  {a.summary && <Typography.Text type="secondary" style={{ fontSize: 12 }}>{a.summary}</Typography.Text>}
                  <span style={{ fontSize: 12 }}>
                    必填:{a.required.length ? a.required.map((r) => <Tag key={r} color="gold">{r}</Tag>) : <Typography.Text type="secondary">无</Typography.Text>}
                  </span>
                </Space>
              </List.Item>
            )}
          />

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
