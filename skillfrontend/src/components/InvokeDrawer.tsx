import { useEffect, useState } from "react";
import { Drawer, Button, Checkbox, Input, Typography, Tag, Alert, Descriptions, Space, message } from "antd";
import { invokeSkill, SkillManifest, TaskOutcome, JSONSchema } from "../api/skills";

const STATE_COLOR: Record<string, string> = {
  completed: "success", failed: "error", rejected: "error",
  cancelled: "warning", needs_input: "warning",
};

function skeleton(p: JSONSchema): Record<string, unknown> {
  const o: Record<string, unknown> = {};
  for (const k of Object.keys(p?.properties || {})) o[k] = "";
  return o;
}

export default function InvokeDrawer({ skill, onClose }: { skill: SkillManifest | null; onClose: () => void }) {
  const [text, setText] = useState("{}");
  const [confirm, setConfirm] = useState(false);
  const [running, setRunning] = useState(false);
  const [out, setOut] = useState<TaskOutcome | null>(null);

  useEffect(() => {
    if (skill) {
      setText(JSON.stringify(skeleton(skill.parameters), null, 2));
      setConfirm(skill.requires_confirmation);
      setOut(null);
    }
  }, [skill]);

  async function run() {
    if (!skill) return;
    let input: Record<string, unknown>;
    try {
      input = JSON.parse(text || "{}");
    } catch (e: any) {
      message.error("输入不是合法 JSON:" + e.message);
      return;
    }
    setRunning(true);
    setOut(null);
    try {
      setOut(await invokeSkill(skill.name, input, confirm));
    } catch (e: any) {
      message.error("调用失败:" + (e?.response?.data?.detail || e.message));
    } finally {
      setRunning(false);
    }
  }

  const req = skill?.parameters?.required || [];

  return (
    <Drawer title={skill ? `测试调用 · ${skill.name}` : ""} width={560} open={!!skill} onClose={onClose} destroyOnClose>
      {skill && (
        <>
          <Descriptions size="small" column={1} style={{ marginBottom: 12 }}>
            <Descriptions.Item label="风险">
              <Tag color={skill.risk_level >= "L3" ? "orange" : "default"}>{skill.risk_level}</Tag>
              {skill.requires_confirmation && <Tag color="orange">写操作需确认</Tag>}
            </Descriptions.Item>
            <Descriptions.Item label="必填字段">{req.length ? req.join(", ") : "无"}</Descriptions.Item>
          </Descriptions>

          <Typography.Text type="secondary">input（业务字段;__base_url__ 和凭证后端注入,无需填）</Typography.Text>
          <Input.TextArea value={text} onChange={(e) => setText(e.target.value)} autoSize={{ minRows: 8, maxRows: 18 }} style={{ fontFamily: "monospace", marginTop: 6 }} />

          <Space style={{ marginTop: 12 }}>
            <Checkbox checked={confirm} onChange={(e) => setConfirm(e.target.checked)}>confirm（L3 写操作必须勾）</Checkbox>
            <Button type="primary" loading={running} onClick={run}>调用</Button>
          </Space>

          {out && (
            <div style={{ marginTop: 16 }}>
              <Alert
                type={(STATE_COLOR[out.state] as any) || "info"}
                showIcon
                message={<span>state: <b>{out.state}</b></span>}
                description={out.message}
              />
              <Typography.Text type="secondary" style={{ display: "block", marginTop: 12 }}>返回（structured_output）</Typography.Text>
              <Input.TextArea
                readOnly
                value={JSON.stringify(out.exec_result?.structured_output ?? null, null, 2)}
                autoSize={{ minRows: 4, maxRows: 14 }}
                style={{ fontFamily: "monospace", marginTop: 6 }}
              />
              {out.audit && (out.audit as any).fact_check && (
                <Alert style={{ marginTop: 10 }} type="info" message="事实核查证据" description={<pre style={{ margin: 0, fontSize: 12 }}>{JSON.stringify((out.audit as any).fact_check, null, 2)}</pre>} />
              )}
            </div>
          )}
        </>
      )}
    </Drawer>
  );
}
