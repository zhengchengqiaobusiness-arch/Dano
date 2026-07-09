import { useEffect, useState } from "react";
import { Card, Descriptions, Tag, Button, Space, Typography, message, Spin, Table, Input, Popconfirm } from "antd";
import { ArrowLeftOutlined, PlayCircleOutlined, PauseCircleOutlined, CheckCircleOutlined, DeleteOutlined } from "@ant-design/icons";
import { useNavigate, useParams } from "react-router-dom";
import { deleteSkill, freezeSkill, getSkill, listTools, resumeSkill, SkillManifest, FunctionTool } from "../api/skills";
import InvokeDrawer from "../components/InvokeDrawer";

function fmtTime(s?: string) {
  if (!s) return "—";
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? s : d.toLocaleString();
}

export default function SkillDetail() {
  const { skillId = "" } = useParams();
  const nav = useNavigate();
  const [skill, setSkill] = useState<SkillManifest | null>(null);
  const [tool, setTool] = useState<FunctionTool | null>(null);
  const [loading, setLoading] = useState(true);
  const [invoke, setInvoke] = useState<SkillManifest | null>(null);

  async function load() {
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
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [skillId]);

  async function doFreeze() {
    try {
      const r = await freezeSkill(skill.name);
      message.success(`已冻结 ${skill.name}，清理 ${r.removed_folders?.length || 0} 个文件夹`);
      await load();
    } catch (e: any) {
      message.error("冻结失败:" + (e?.response?.data?.detail || e.message));
    }
  }
  async function doResume() {
    try {
      await resumeSkill(skill.name);
      message.success(`已恢复 ${skill.name}`);
      await load();
    } catch (e: any) {
      message.error("恢复失败:" + (e?.response?.data?.detail || e.message));
    }
  }
  async function doDelete() {
    try {
      const r = await deleteSkill(skill.name);
      message.success(`已删除 ${skill.name}(${r.deleted} 条资产,清理 ${r.removed_folders?.length || 0} 个文件夹)`);
      nav("/skills");
    } catch (e: any) {
      message.error("删除失败:" + (e?.response?.data?.detail || e.message));
    }
  }

  if (loading) return <Spin style={{ marginTop: 80, display: "block" }} />;
  if (!skill) return <Typography.Text>未找到 {skillId}</Typography.Text>;

  const props = skill.parameters?.properties || {};
  const req = new Set(skill.parameters?.required || []);
  const rows = Object.entries(props).map(([k, v]) => ({ key: k, name: k, required: req.has(k), type: v.type || "string", desc: v.description || "" }));

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => nav("/skills")}>返回目录</Button>
        <Button type="primary" icon={<PlayCircleOutlined />} disabled={!!skill.frozen} onClick={() => setInvoke(skill)}>测试调用</Button>
        {!skill.frozen ? (
          <Popconfirm title={`冻结 ${skill.name}?`} description="只清理已导出的文件夹,保留数据库资产;冻结后不会再导出。" okText="冻结" cancelText="取消" onConfirm={doFreeze}>
            <Button icon={<PauseCircleOutlined />}>冻结</Button>
          </Popconfirm>
        ) : (
          <Popconfirm title={`恢复 ${skill.name}?`} description="恢复后可测试调用,并会在下次导出时重新写出文件夹。" okText="恢复" cancelText="取消" onConfirm={doResume}>
            <Button icon={<CheckCircleOutlined />}>恢复</Button>
          </Popconfirm>
        )}
        <Popconfirm title={`删除 ${skill.name}?`} description="删本租户该 skill 的全部资产版本,并清理原始导出文件夹。" okText="删除" okButtonProps={{ danger: true }} cancelText="取消" onConfirm={doDelete}>
          <Button danger icon={<DeleteOutlined />}>删除</Button>
        </Popconfirm>
      </Space>

      <Card title={skill.name} style={{ marginBottom: 16 }}>
        <Descriptions column={2} size="small">
          <Descriptions.Item label="标题">{skill.title}</Descriptions.Item>
          <Descriptions.Item label="类型">{skill.integration}</Descriptions.Item>
          <Descriptions.Item label="风险">{skill.risk_level}</Descriptions.Item>
          <Descriptions.Item label="需确认">{skill.requires_confirmation ? "是" : "否"}</Descriptions.Item>
          <Descriptions.Item label="产出时间">{fmtTime(skill.created_at)}</Descriptions.Item>
          <Descriptions.Item label="状态">{skill.frozen ? <Tag>已冻结</Tag> : (skill.lifecycle_state || "已发布")}</Descriptions.Item>
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
