import { useEffect, useState } from "react";
import { Table, Tag, Button, Space, Typography, message, Empty, Modal, Input, Alert, Popconfirm, Pagination } from "antd";
import { useNavigate } from "react-router-dom";
import { ReloadOutlined, ExportOutlined, DeleteOutlined, KeyOutlined, PauseCircleOutlined, CheckCircleOutlined } from "@ant-design/icons";
import { listSkillsPage, exportAgentSkills, getExportDirectory, saveExportDirectory, deleteSkill, freezeSkill, resumeSkill, SkillManifest } from "../api/skills";
import TokenModal from "../components/TokenModal";
import { TENANT_NAME } from "../api/client";
import { rememberExportDir, rememberedExportDir } from "../api/recording";
import { notifySkillCatalogChanged, observeSkillCatalogChanges, skillDisplayId } from "../api/skillCatalog";

const RISK_COLOR: Record<string, string> = { L1: "default", L2: "default", L3: "orange", L4: "red", L5: "red" };
const INTEG_LABEL: Record<string, string> = { workflow: "复合流程", api: "接口", page: "页面" };

function fmtTime(s?: string) {
  if (!s) return "-";
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return s;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
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
  const nav = useNavigate();
  const [data, setData] = useState<SkillManifest[]>([]);
  const [loading, setLoading] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [exportDir, setExportDir] = useState("");
  const [exporting, setExporting] = useState(false);
  const [tokenSub, setTokenSub] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(8);
  const [total, setTotal] = useState(0);
  const tenant = localStorage.getItem(TENANT_NAME) || "";

  async function loadExportDir() {
    try {
      const dir = await getExportDirectory();
      if (dir) {
        rememberExportDir(dir);
        setExportDir(dir);
        return;
      }
    } catch {
      // fall back to the last local copy only when the backend is unreachable
    }
    setExportDir(rememberedExportDir());
  }

  async function persistExportDir(raw: string) {
    const next = raw.trim();
    if (!next) return;
    rememberExportDir(next);
    try {
      const saved = await saveExportDirectory(next);
      if (saved) {
        rememberExportDir(saved);
        setExportDir(saved);
      }
    } catch {
      setExportDir(next);
    }
  }

  async function doExport() {
    const outDir = exportDir.trim();
    if (outDir) await persistExportDir(outDir);
    setExporting(true);
    try {
      const r = await exportAgentSkills(outDir, tenant);
      if (r.out_dir) {
        rememberExportDir(r.out_dir);
        setExportDir(r.out_dir);
      }
      notifySkillCatalogChanged();
      if (r.errors?.length) {
        message.warning(`已导出 ${r.count} 个 skill，另有 ${r.errors.length} 条未完成`);
      } else {
        message.success(`已快速写出 ${r.count} 个 skill 到 ${r.out_dir}`);
      }
      setExportOpen(false);
      void load(page, pageSize);
    } catch (e: any) {
      message.error("导出失败:" + (e?.response?.data?.detail || e?.response?.data?.errors?.[0] || e.message));
    } finally {
      setExporting(false);
    }
  }

  async function doDelete(skill: SkillManifest) {
    try {
      const r = await deleteSkill(skill.name);
      message.success(`已删除 ${skillDisplayId(skill)}`);
      const nextPage = data.length <= 1 && page > 1 ? page - 1 : page;
      if (nextPage !== page) setPage(nextPage);
      else void load(page, pageSize);
    } catch (e: any) {
      message.error("删除失败:" + (e?.response?.data?.detail || e.message));
    }
  }

  async function doFreeze(skill: SkillManifest) {
    try {
      const r = await freezeSkill(skill.name);
      message.success(`已冻结 ${skillDisplayId(skill)}(清理 ${r.removed_folders?.length || 0} 个文件夹)`);
      void load(page, pageSize);
    } catch (e: any) {
      message.error("冻结失败:" + (e?.response?.data?.detail || e.message));
    }
  }

  async function doResume(skill: SkillManifest) {
    try {
      const r = await resumeSkill(skill.name);
      message.success(`已恢复 ${skillDisplayId(skill)}(${r.state})`);
      void load(page, pageSize);
    } catch (e: any) {
      message.error("恢复失败:" + (e?.response?.data?.detail || e.message));
    }
  }

  async function load(nextPage = page, nextSize = pageSize) {
    setLoading(true);
    try {
      const result = await listSkillsPage(nextPage, nextSize);
      setData(result.items || []);
      setTotal(Number(result.total) || 0);
      setPage(Number(result.page) || nextPage);
      setPageSize(Number(result.page_size) || nextSize);
    } catch (e: any) {
      message.error("加载失败:" + (e?.response?.data?.detail || e?.response?.data?.errors?.[0] || e.message));
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { void load(page, pageSize); }, [page, pageSize]);
  useEffect(() => {
    const refresh = () => { void load(page, pageSize); };
    const stop = observeSkillCatalogChanges(refresh);
    window.addEventListener("focus", refresh);
    return () => {
      stop();
      window.removeEventListener("focus", refresh);
    };
  }, [page, pageSize]);
  useEffect(() => {
    if (exportOpen || tokenSub) void loadExportDir();
  }, [exportOpen, tokenSub]);

  return (
    <div className="skill-catalog-page">
      <div className="skill-catalog-body">
      <Table<Row>
        rowKey="name"
        size="small"
        loading={loading}
        dataSource={groupByBusiness(data)}
        expandable={{ defaultExpandAllRows: true }}
        pagination={false}
        locale={{ emptyText: (
          <Empty
            description={(
              <Space direction="vertical" size={8}>
                <span>本租户暂无已导出 Skill，先去页面录制并产出 Skill</span>
                <Button type="link" onClick={() => nav("/recording")}>去录制页面</Button>
              </Space>
            )}
          />
        ) }}
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
                  <div style={{ fontSize: 12, color: "#999" }}>{skillDisplayId(r)}</div>
                </div>
              ),
          },
          { title: "类型", dataIndex: "integration", width: 110, render: (v, r) => (r.__group ? null : <Tag>{INTEG_LABEL[v] || v}</Tag>) },
          { title: "风险", dataIndex: "risk_level", width: 90, render: (v, r) => (r.__group ? null : <Tag color={RISK_COLOR[v] || "default"}>{v}</Tag>) },
          { title: "更新时间", dataIndex: "updated_at", width: 180, render: (_v, r) => (r.__group ? null : <Typography.Text type="secondary" style={{ fontSize: 12 }}>{fmtTime(r.updated_at || r.created_at)}</Typography.Text>) },
          {
            title: (
              <Space size={8} wrap={false}>
                <span>操作</span>
                <Button size="small" icon={<ExportOutlined />} onClick={() => setExportOpen(true)} disabled={!data.length}>
                  导出为 pi skill
                </Button>
                <Button size="small" icon={<ReloadOutlined />} onClick={() => void load(page, pageSize)}>刷新</Button>
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
                    <Popconfirm title={`冻结 ${skillDisplayId(r)}?`} description="只清理已导出的文件夹，保留目录记录；冻结后目录重导会跳过。" okText="冻结" cancelText="取消" onConfirm={() => doFreeze(r)}>
                      <Button size="small" icon={<PauseCircleOutlined />}>冻结</Button>
                    </Popconfirm>
                  )}
                  {r.frozen && (
                    <Popconfirm title={`恢复 ${skillDisplayId(r)}?`} description="恢复后下次目录导出会重新写出文件。" okText="恢复" cancelText="取消" onConfirm={() => doResume(r)}>
                      <Button size="small" icon={<CheckCircleOutlined />}>恢复</Button>
                    </Popconfirm>
                  )}
                  <Popconfirm title={`删除 ${skillDisplayId(r)}?`} description="先清理本地包，再删除本租户该 Skill 的全部资产版本" okText="删除" okButtonProps={{ danger: true }} cancelText="取消" onConfirm={() => doDelete(r)}>
                    <Button size="small" danger icon={<DeleteOutlined />}>删除</Button>
                  </Popconfirm>
                </Space>
              ),
          },
        ]}
      />
      </div>
      <div className="skill-catalog-pager">
        <Pagination
          size="small"
          current={page}
          pageSize={pageSize}
          total={total}
          showSizeChanger
          showQuickJumper
          pageSizeOptions={[8, 10, 20, 50]}
          showTotal={(count) => `共 ${count} 条`}
          onChange={(nextPage, nextSize) => {
            if (nextSize !== pageSize) {
              setPage(1);
              setPageSize(nextSize);
              return;
            }
            setPage(nextPage);
          }}
        />
      </div>
      <TokenModal tenant={tenant} subsystem={tokenSub || ""} open={!!tokenSub} onClose={() => setTokenSub(null)} outDir={exportDir || rememberedExportDir()} />

      <Modal
        title="导出为 pi 文件式 skill"
        open={exportOpen}
        onCancel={() => setExportOpen(false)}
        onOk={doExport}
        okText="导出"
        confirmLoading={exporting}
      >
        <Alert
          type="info" showIcon style={{ marginBottom: 12 }}
          message="快速原样导出：不开 Skill 4、不校验。沿用已有 Skill 4 手册，按录制合同重写合同与脚本，并写入当前 token。"
        />
        <Typography.Paragraph type="secondary" style={{ marginBottom: 6 }}>目标目录:</Typography.Paragraph>
        <Input
          value={exportDir}
          onChange={(e) => setExportDir(e.target.value)}
          onBlur={(e) => void persistExportDir(e.target.value)}
          placeholder="默认读取后端导出目录配置"
          onPressEnter={doExport}
        />
      </Modal>
    </div>
  );
}
