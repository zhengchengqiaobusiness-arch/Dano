import { useEffect, useState } from "react";
import { Modal, Input, Button, Space, Typography, Tag, message, Empty, Alert, Descriptions } from "antd";
import { ReloadOutlined, SaveOutlined } from "@ant-design/icons";
import { getRuntimeToken, saveRuntimeToken, RuntimeToken } from "../api/skills";
import { rememberedExportDir } from "../api/recording";

// 录制型 skill 运行期鉴权。token 按 (tenant, subsystem) 存在 Pi_check 本机仓库。
// 保存后立刻回写已导出包的 auth.local.json；下次导出也用这一份。
export default function TokenModal({
  tenant, subsystem, open, onClose, outDir = "",
}: { tenant: string; subsystem: string; open: boolean; onClose: () => void; outDir?: string }) {
  const [rec, setRec] = useState<RuntimeToken | null>(null);
  const [loading, setLoading] = useState(false);
  const [token, setToken] = useState("");
  const [headerName, setHeaderName] = useState("Authorization");
  const [prefix, setPrefix] = useState("Bearer ");
  const [saving, setSaving] = useState(false);

  async function load() {
    setLoading(true);
    try {
      setRec(await getRuntimeToken(tenant, subsystem));
    } catch (e: any) {
      message.error("查询失败:" + (e?.response?.data?.detail || e.message));
    } finally {
      setLoading(false);
    }
  }

  // 打开 / 切换子系统时拉取(默认打码)
  useEffect(() => {
    if (open) { setToken(""); load(); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, subsystem]);

  async function save() {
    if (!token.trim()) { message.error("粘贴新 token 再保存"); return; }
    setSaving(true);
    try {
      const saved = await saveRuntimeToken({
        tenant, subsystem, token: token.trim(),
        header_name: headerName.trim() || "Authorization",
        token_prefix: prefix,   // 允许空前缀(有些系统直接放裸 token)
        out_dir: (outDir || rememberedExportDir()).trim(),
      });
      const n = saved.updated_packages?.length || 0;
      message.success(n ? `已更新 token，并回写 ${n} 个已导出包` : "已更新 token，下次导出会写入最新凭证");
      setToken("");
      await load();
    } catch (e: any) {
      message.error("保存失败:" + (e?.response?.data?.detail || e.message));
    } finally {
      setSaving(false);
    }
  }

  const headers = rec?.headers || {};
  const headerKeys = Object.keys(headers);

  return (
    <Modal
      title={<>运行期 Token · <Tag color="blue">{subsystem}</Tag></>}
      open={open}
      onCancel={onClose}
      footer={<Button onClick={onClose}>关闭</Button>}
      width={620}
    >
      <Alert
        type="info" showIcon style={{ marginBottom: 14 }}
        message="这里保存的 token 是正式凭证：立刻回写已导出包的 auth.local.json，之后导出也用这一份。过期报 401 时粘贴新 token 即可，不必重录。"
      />

      <Space style={{ marginBottom: 8, justifyContent: "space-between", width: "100%" }}>
        <Typography.Text strong>当前 token</Typography.Text>
        <Space>
          <Button size="small" icon={<ReloadOutlined />} onClick={() => load()} loading={loading}>刷新</Button>
        </Space>
      </Space>

      {headerKeys.length ? (
        <Descriptions bordered size="small" column={1} style={{ marginBottom: 6 }}>
          {headerKeys.map((k) => (
            <Descriptions.Item key={k} label={k}>
              <Typography.Text style={{ wordBreak: "break-all", fontFamily: "monospace" }}>
                {headers[k]}
              </Typography.Text>
            </Descriptions.Item>
          ))}
        </Descriptions>
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="还没有存过 token(录制时会自动抓,或在下面手动填一份)" style={{ margin: "12px 0" }} />
      )}
      {rec?.has_token && (
        <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
          来源:{rec.source?.startsWith("scheduled:") ? "定时登录刷新" : rec.source === "manual" ? "手动刷新" : "录制自动抓"} · 更新时间:{rec.updated_at ? new Date(rec.updated_at).toLocaleString() : "-"}
        </Typography.Paragraph>
      )}

      <Typography.Text strong style={{ display: "block", margin: "14px 0 8px" }}>更新 / 刷新 token</Typography.Text>
      <Input.TextArea
        value={token}
        onChange={(e) => setToken(e.target.value)}
        placeholder="粘贴新 token(只填 token 本身,如 4d6f9993...;系统会按下面的头名+前缀拼好)"
        autoSize={{ minRows: 2, maxRows: 4 }}
        style={{ marginBottom: 8, fontFamily: "monospace" }}
      />
      <Space wrap style={{ marginBottom: 12 }}>
        <span>
          <Typography.Text type="secondary" style={{ fontSize: 12, marginRight: 6 }}>头名称</Typography.Text>
          <Input size="small" value={headerName} onChange={(e) => setHeaderName(e.target.value)} style={{ width: 150 }} />
        </span>
        <span>
          <Typography.Text type="secondary" style={{ fontSize: 12, marginRight: 6 }}>前缀</Typography.Text>
          <Input size="small" value={prefix} onChange={(e) => setPrefix(e.target.value)} style={{ width: 110 }} placeholder="Bearer " />
        </span>
        <Button type="primary" icon={<SaveOutlined />} onClick={save} loading={saving}>保存并生效</Button>
      </Space>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 0 }}>
        只更新这一个头(默认 Authorization),其它头(如 Tenant-Id)保留不变。
      </Typography.Paragraph>
    </Modal>
  );
}
