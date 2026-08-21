import { useEffect, useState } from "react";
import { Table, Tag, Button, Space, Typography, message, Empty, Modal, Input, Alert, Popconfirm, Select } from "antd";
import { ReloadOutlined, ExportOutlined, DeleteOutlined, KeyOutlined, PauseCircleOutlined, CheckCircleOutlined } from "@ant-design/icons";
import { listSkills, exportAgentSkills, getExportDirectory, deleteSkill, freezeSkill, resumeSkill, SkillManifest, SkillExportMode } from "../api/skills";
import TokenModal from "../components/TokenModal";
import { TENANT_NAME } from "../api/client";
import { rememberExportDir, rememberedExportDir } from "../api/recording";

const RISK_COLOR: Record<string, string> = { L1: "default", L2: "default", L3: "orange", L4: "red", L5: "red" };
const INTEG_LABEL: Record<string, string> = { workflow: "复合流程", api: "接口", page: "页面" };

function fmtTime(s?: string) {
  if (!s) return "-";
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return s;
  return d.toLocaleString();
}

type Row = SkillManifest & { __group?: boolean; __ops?: number; children?: SkillManifest[] };

function groupByBusiness(skills: SkillManifest[]): Row[] {
  const groups = new Map<string, SkillManifest[]>();
  const flat: SkillManifest[] = [];
  for (const s of skills) {
    if (s.business) {
      if (!groups.has(s.business)) groups.set(s.business, []);
      groups.get(s.business)!.push(s);
    } else flat.push(s);
  }
  const rows: Row[] = [];
  for (const [biz, ops] of groups) {
    if (ops.length <= 1) { flat.push(...ops); continue; }
    const write = ops.find((o) => o.risk_level === "L3" || o.risk_level === "L4" || o.risk_level === "L5");
    const label = write?.title || ops[0].title || biz;
    rows.push({
      ...ops[0], name: `business:${biz}`, title: `${label}（${ops.length} 个操作）`,
      __group: true, __ops: ops.length, children: ops,
    });
  }
  for (const s of flat) rows.push(s as Row);
  return rows;
}

export default function Skills() {
  const [data, setData] = useState<SkillManifest[]>([]);
  const [loading, setLoading] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [exportDir, setExportDir] = useState("");
  const [exportMode, setExportMode] = useState<SkillExportMode>("package");
  const [exporting, setExporting] = useState(false);
  const [tokenSub, setTokenSub] = useState<string | null>(null);
  const tenant = localStorage.getItem(TENANT_NAME) || "";

  async function loadExportDir() {
    try {
      setExportDir(await getExportDirectory() || rememberedExportDir());
    } catch {
      setExportDir(rememberedExportDir());
    }
  }

  async function doExport() {
    const outDir = exportDir.trim();
    setExporting(true);
    try {
      const r = await exportAgentSkills(outDir, exportMode);
      if (r.out_dir) {
        rememberExportDir(r.out_dir);
        setExportDir(r.out_dir);
      }
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
      message.success(`已删除 ${skillId}(${r.deleted} 条资产,清理 ${r.removed_folders?.length || 0} 个文件夹)`);
      load();
    } catch (e: any) {
      message.error("删除失败:" + (e?.response?.data?.detail || e.message));
    }
  }

  async function doFreeze(skillId: string) {
    try {
      const r = await freezeSkill(skillId);
      message.success(`已冻结 ${skillId}(清理 ${r.removed_folders?.length || 0} 个文件夹)`);
      load();
    } catch (e: any) {
      message.error("冻结失败:" + (e?.response?.data?.detail || e.message));
    }
  }

  async function doResume(skillId: string) {
    try {
      const r = await resumeSkill(skillId);
      message.success(`已恢复 ${skillId}(${r.state})`);
      load();
    } catch (e: any) {
      message.error("恢复失败:" + (e?.response?.data?.detail || e.message));
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
  useEffect(() => {
    if (exportOpen) void loadExportDir();
  }, [exportOpen]);

  return (
    <div>
      <Table<Row>
        rowKey="name"
        loading={loading}
        dataSource={groupByBusiness(data)}
        expandable={{ defaultExpandAllRows: true }}
        locale={{ emptyText: <Empty description="本租户暂无已发布 Skill,先去接入系统生成" /> }}
        columns={[
          {
            title: "Skill",
            render: (_, r) =>
              r.__group ? (
                <div>
                  <Tag color="blue">业务剧本</Tag>
                  <span style={{ fontWeight: 600 }}>{r.title}</span>
                </div>
              ) : (
                <div>
                  <div>{r.title || r.name}{r.frozen && <Tag color="default" style={{ marginLeft: 8 }}>已冻结</Tag>}</div>
                  <div style={{ fontSize: 12, color: "#999" }}>{r.name}</div>
                </div>
              ),
          },
          { title: "类型", dataIndex: "integration", width: 110, render: (v, r) => (r.__group ? null : <Tag>{INTEG_LABEL[v] || v}</Tag>) },
          { title: "风险", dataIndex: "risk_level", width: 90, render: (v, r) => (r.__group ? null : <Tag color={RISK_COLOR[v] || "default"}>{v}</Tag>) },
          { title: "产出时间", dataIndex: "created_at", width: 180, render: (v, r) => (r.__group ? null : <Typography.Text type="secondary" style={{ fontSize: 12 }}>{fmtTime(v)}</Typography.Text>) },
          {
            title: (
              <Space size={8} wrap={false}>
                <span>操作</span>
                <Button size="small" icon={<ExportOutlined />} onClick={() => setExportOpen(true)} disabled={!data.length}>
                  导出为 pi skill
                </Button>
                <Button size="small" icon={<ReloadOutlined />} onClick={load}>刷新</Button>
              </Space>
            ),
            width: 360,
            render: (_, r) =>
              r.__group ? (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>共 {r.__ops} 个操作</Typography.Text>
              ) : (
                <Space>
                  {r.integration === "page" && (
                    <Button size="small" icon={<KeyOutlined />} onClick={() => setTokenSub(r.subsystem)}>凭证</Button>
                  )}
                  {!r.frozen && (
                    <Popconfirm title={`冻结 ${r.name}?`} description="只清理已导出的文件夹,保留数据库资产;冻结后不会再导出。" okText="冻结" cancelText="取消" onConfirm={() => doFreeze(r.name)}>
                      <Button size="small" icon={<PauseCircleOutlined />}>冻结</Button>
                    </Popconfirm>
                  )}
                  {r.frozen && (
                    <Popconfirm title={`恢复 ${r.name}?`} description="恢复后会在下次导出时重新写出文件夹。" okText="恢复" cancelText="取消" onConfirm={() => doResume(r.name)}>
                      <Button size="small" icon={<CheckCircleOutlined />}>恢复</Button>
                    </Popconfirm>
                  )}
                  <Popconfirm title={`删除 ${r.name}?`} description="删本租户该 skill 的全部资产版本,便于重来" okText="删除" okButtonProps={{ danger: true }} cancelText="取消" onConfirm={() => doDelete(r.name)}>
                    <Button size="small" danger icon={<DeleteOutlined />}>删除</Button>
                  </Popconfirm>
                </Space>
              ),
          },
        ]}
      />
      <TokenModal tenant={tenant} subsystem={tokenSub || ""} open={!!tokenSub} onClose={() => setTokenSub(null)} />

      <Modal
        title="导出为 pi 文件式 skill"
        open={exportOpen}
        onCancel={() => setExportOpen(false)}
        onOk={doExport}
        okText="导出"
        confirmLoading={exporting}
      >
        <Alert
          type="warning" showIcon style={{ marginBottom: 12 }}
          message="由 Dano 后端进程写文件,目录必须在「后端所在机器」上。Windows 本地后端写不进 Linux 路径。"
        />
        <Typography.Paragraph type="secondary" style={{ marginBottom: 6 }}>目标目录:</Typography.Paragraph>
        <Input
          value={exportDir}
          onChange={(e) => setExportDir(e.target.value)}
          placeholder="默认读取后端导出目录配置"
          onPressEnter={doExport}
        />
        <Typography.Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 6 }}>导出模式:</Typography.Paragraph>
        <Select<SkillExportMode>
          value={exportMode}
          onChange={setExportMode}
          style={{ width: "100%" }}
          options={[
            { value: "both", label: "代理包 + 自包含包" },
            { value: "package", label: "仅自包含包（直连业务 API）" },
            { value: "proxy", label: "仅代理包（调用 Dano）" },
          ]}
        />
        <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 10, marginBottom: 0 }}>
          自包含包优先读取 DANO_AUTH_HEADERS；代理包使用 DANO_URL、DANO_TENANT_KEY。
        </Typography.Paragraph>
      </Modal>
    </div>
  );
}
