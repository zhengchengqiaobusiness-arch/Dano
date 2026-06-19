import { useEffect, useState } from "react";
import { Table, Tag, Button, Space, Typography, message, Empty, Modal, Input, Alert, Popconfirm } from "antd";
import { PlayCircleOutlined, ReloadOutlined, ExportOutlined, DeleteOutlined } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import { listSkills, exportAgentSkills, deleteSkill, SkillManifest } from "../api/skills";
import InvokeDrawer from "../components/InvokeDrawer";

const EXPORT_DIR_LS = "dano.exportDir";
const DEFAULT_EXPORT_DIR = "/opt/dano/runtime-data/.agents/skills";

const RISK_COLOR: Record<string, string> = { L1: "default", L2: "default", L3: "orange", L4: "red", L5: "red" };
const INTEG_LABEL: Record<string, string> = { adapter: "代码", workflow: "复合流程", api: "接口", page: "页面" };

export default function Skills() {
  const nav = useNavigate();
  const [data, setData] = useState<SkillManifest[]>([]);
  const [loading, setLoading] = useState(false);
  const [invoke, setInvoke] = useState<SkillManifest | null>(null);
  const [exportOpen, setExportOpen] = useState(false);
  const [exportDir, setExportDir] = useState(localStorage.getItem(EXPORT_DIR_LS) || DEFAULT_EXPORT_DIR);
  const [exporting, setExporting] = useState(false);

  async function doExport() {
    if (!exportDir.trim()) { message.error("请填目标目录"); return; }
    setExporting(true);
    try {
      const r = await exportAgentSkills(exportDir.trim());
      localStorage.setItem(EXPORT_DIR_LS, exportDir.trim());
      message.success(`已导出 ${r.count} 个 skill 到 ${r.out_dir}`);
      setExportOpen(false);
    } catch (e: any) {
      message.error("导出失败:" + (e?.response?.data?.detail || e.message));
    } finally {
      setExporting(false);
    }
  }

  async function doDelete(skillId: string) {
    try {
      const r = await deleteSkill(skillId);
      message.success(`已删除 ${skillId}(${r.deleted} 条资产)`);
      load();
    } catch (e: any) {
      message.error("删除失败:" + (e?.response?.data?.detail || e.message));
    }
  }

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
        <Space>
          <Button icon={<ExportOutlined />} onClick={() => setExportOpen(true)} disabled={!data.length}>
            导出为 pi skill
          </Button>
          <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
        </Space>
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
            title: "操作", width: 260,
            render: (_, r) => (
              <Space>
                <Button size="small" type="primary" ghost icon={<PlayCircleOutlined />} onClick={() => setInvoke(r)}>测试调用</Button>
                <Button size="small" onClick={() => nav(`/skills/${encodeURIComponent(r.name)}`)}>详情</Button>
                <Popconfirm title={`删除 ${r.name}?`} description="删本租户该 skill 的全部资产版本,便于重来" okText="删除" okButtonProps={{ danger: true }} cancelText="取消" onConfirm={() => doDelete(r.name)}>
                  <Button size="small" danger icon={<DeleteOutlined />}>删除</Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />
      <InvokeDrawer skill={invoke} onClose={() => setInvoke(null)} />

      <Modal
        title="导出为 pi 文件式 skill(.agents/skills/)"
        open={exportOpen}
        onCancel={() => setExportOpen(false)}
        onOk={doExport}
        okText="导出"
        confirmLoading={exporting}
      >
        <Alert
          type="warning" showIcon style={{ marginBottom: 12 }}
          message="由 Dano 后端进程写文件,目录必须在「后端所在机器」上。生产请把后端部署在那台 Linux,这里填 pi 的 .agents/skills 绝对路径;Windows 本地后端写不进 Linux 路径。"
        />
        <Typography.Paragraph type="secondary" style={{ marginBottom: 6 }}>目标目录(pi 的 .agents/skills 绝对路径):</Typography.Paragraph>
        <Input
          value={exportDir}
          onChange={(e) => setExportDir(e.target.value)}
          placeholder="/opt/dano/runtime-data/.agents/skills"
          onPressEnter={doExport}
        />
        <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 10, marginBottom: 0 }}>
          pi 端记得设环境变量 DANO_URL、DANO_TENANT_KEY(本租户 api_key)。
        </Typography.Paragraph>
      </Modal>
    </div>
  );
}
