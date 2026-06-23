import { useEffect, useState } from "react";
import { Card, Descriptions, Tag, Button, Space, Typography, message, Spin, Table, Input } from "antd";
import { ArrowLeftOutlined, PlayCircleOutlined } from "@ant-design/icons";
import { useNavigate, useParams } from "react-router-dom";
import { getSkill, listTools, SkillManifest, FunctionTool } from "../api/skills";
import InvokeDrawer from "../components/InvokeDrawer";

export default function SkillDetail() {
  const { skillId = "" } = useParams();
  const nav = useNavigate();
  const [skill, setSkill] = useState<SkillManifest | null>(null);
  const [tool, setTool] = useState<FunctionTool | null>(null);
  const [loading, setLoading] = useState(true);
  const [invoke, setInvoke] = useState<SkillManifest | null>(null);

  useEffect(() => {
    (async () => {
      setLoading(true);
      try {
        const s = await getSkill(skillId);
        setSkill(s);
        const tools = await listTools().catch(() => []);
        setTool(tools.find((t) => t.function.name === s.name.replace(/\./g, "__")) || null);
      } catch (e: any) {
        message.error("加载失败:" + (e?.response?.data?.detail || e.message));
      } finally {
        setLoading(false);
      }
    })();
  }, [skillId]);

  if (loading) return <Spin style={{ marginTop: 80, display: "block" }} />;
  if (!skill) return <Typography.Text>未找到 {skillId}</Typography.Text>;

  const props = skill.parameters?.properties || {};
  const req = new Set(skill.parameters?.required || []);
  const rows = Object.entries(props).map(([k, v]) => ({ key: k, name: k, required: req.has(k), type: v.type || "string", desc: v.description || "" }));

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => nav("/skills")}>返回目录</Button>
        <Button type="primary" icon={<PlayCircleOutlined />} onClick={() => setInvoke(skill)}>测试调用</Button>
      </Space>

      <Card title={skill.name} style={{ marginBottom: 16 }}>
        <Descriptions column={2} size="small">
          <Descriptions.Item label="标题">{skill.title}</Descriptions.Item>
          <Descriptions.Item label="类型">{skill.integration}</Descriptions.Item>
          <Descriptions.Item label="风险">{skill.risk_level}</Descriptions.Item>
          <Descriptions.Item label="需确认">{skill.requires_confirmation ? "是" : "否"}</Descriptions.Item>
          <Descriptions.Item label="描述" span={2}>{skill.description}</Descriptions.Item>
        </Descriptions>
      </Card>

      <Card title="输入参数（function-calling parameters）" style={{ marginBottom: 16 }}>
        <Table
          size="small"
          pagination={false}
          dataSource={rows}
          columns={[
            { title: "字段", dataIndex: "name" },
            { title: "必填", dataIndex: "required", width: 80, render: (v) => (v ? <Tag color="orange">必填</Tag> : <Tag>可选</Tag>) },
            { title: "类型", dataIndex: "type", width: 100 },
            { title: "说明", dataIndex: "desc" },
          ]}
        />
      </Card>

      {skill.page && (
        <Card title="页面步骤(无 API · 流程8)" style={{ marginBottom: 16 }}>
          <Descriptions column={1} size="small" style={{ marginBottom: 12 }}>
            <Descriptions.Item label="入口页">{skill.page.start_url || "—"}</Descriptions.Item>
            <Descriptions.Item label="成功标志">
              {skill.page.success_marker ? <Tag color="green">{skill.page.success_marker}</Tag> : <Tag>无(dry/查询)</Tag>}
            </Descriptions.Item>
          </Descriptions>
          <Table
            size="small"
            pagination={false}
            dataSource={(skill.page.steps || []).map((s, i) => ({ key: i, idx: i + 1, ...s }))}
            columns={[
              { title: "#", dataIndex: "idx", width: 48 },
              { title: "操作", dataIndex: "op", width: 90, render: (v) => <Tag color={v === "submit" ? "orange" : "default"}>{v}</Tag> },
              { title: "定位(语义)", dataIndex: "locator", render: (v) => v ? <Typography.Text code style={{ fontSize: 12 }}>{v}</Typography.Text> : "—" },
              { title: "取值", dataIndex: "value_from", width: 180, render: (v) => {
                  if (!v) return "—";
                  const [kind, ...rest] = String(v).split(":");
                  const val = rest.join(":");
                  return kind === "field"
                    ? <Tag color="blue">参数 {val}</Tag>
                    : <Typography.Text type="secondary" style={{ fontSize: 12 }}>常量 {val}</Typography.Text>;
                } },
            ]}
          />
        </Card>
      )}

      {tool && (
        <Card title="function-calling tool（给聊天端 LLM 用)">
          <Typography.Paragraph type="secondary">工具名 <code>{tool.function.name}</code>（= skill_id 的点转 __);聊天端把它放进 LLM 的 tools。</Typography.Paragraph>
          <Input.TextArea readOnly value={JSON.stringify(tool, null, 2)} autoSize={{ minRows: 6, maxRows: 18 }} style={{ fontFamily: "monospace" }} />
        </Card>
      )}

      <InvokeDrawer skill={invoke} onClose={() => setInvoke(null)} />
    </div>
  );
}
