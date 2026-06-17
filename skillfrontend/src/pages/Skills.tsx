import { useEffect, useState } from "react";
import { Table, Tag, Button, Space, Typography, message, Empty } from "antd";
import { PlayCircleOutlined, ReloadOutlined } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import { listSkills, SkillManifest } from "../api/skills";
import InvokeDrawer from "../components/InvokeDrawer";

const RISK_COLOR: Record<string, string> = { L1: "default", L2: "default", L3: "orange", L4: "red", L5: "red" };
const INTEG_LABEL: Record<string, string> = { adapter: "代码", workflow: "复合流程", api: "接口", page: "页面" };

export default function Skills() {
  const nav = useNavigate();
  const [data, setData] = useState<SkillManifest[]>([]);
  const [loading, setLoading] = useState(false);
  const [invoke, setInvoke] = useState<SkillManifest | null>(null);

  async function load() {
    setLoading(true);
    try {
      setData(await listSkills());
    } catch (e: any) {
      message.error("加载失败:" + (e?.response?.data?.detail || e.message));
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { load(); }, []);

  return (
    <div>
      <Space style={{ marginBottom: 16, justifyContent: "space-between", width: "100%" }}>
        <Typography.Title level={4} style={{ margin: 0 }}>Skill 目录</Typography.Title>
        <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
      </Space>
      <Table<SkillManifest>
        rowKey="name"
        loading={loading}
        dataSource={data}
        locale={{ emptyText: <Empty description="本租户暂无已发布 Skill,先去接入系统生成" /> }}
        columns={[
          {
            title: "Skill", dataIndex: "name",
            render: (_, r) => (
              <a onClick={() => nav(`/skills/${encodeURIComponent(r.name)}`)}>
                <div>{r.name}</div>
                <div style={{ fontSize: 12, color: "#999" }}>{r.title}</div>
              </a>
            ),
          },
          { title: "类型", dataIndex: "integration", width: 110, render: (v) => <Tag>{INTEG_LABEL[v] || v}</Tag> },
          { title: "风险", dataIndex: "risk_level", width: 90, render: (v) => <Tag color={RISK_COLOR[v] || "default"}>{v}</Tag> },
          { title: "需确认", dataIndex: "requires_confirmation", width: 90, render: (v) => (v ? <Tag color="orange">是</Tag> : <Tag>否</Tag>) },
          {
            title: "操作", width: 200,
            render: (_, r) => (
              <Space>
                <Button size="small" type="primary" ghost icon={<PlayCircleOutlined />} onClick={() => setInvoke(r)}>测试调用</Button>
                <Button size="small" onClick={() => nav(`/skills/${encodeURIComponent(r.name)}`)}>详情</Button>
              </Space>
            ),
          },
        ]}
      />
      <InvokeDrawer skill={invoke} onClose={() => setInvoke(null)} />
    </div>
  );
}
