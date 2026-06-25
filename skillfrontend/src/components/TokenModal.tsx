import { useEffect, useState } from "react";
import { Modal, Input, Button, Space, Typography, Switch, Tag, message, Empty, Alert, Descriptions } from "antd";
import { ReloadOutlined, SaveOutlined } from "@ant-design/icons";
import { getRuntimeToken, putRuntimeToken, RuntimeToken } from "../api/skills";

// 页面型 skill 运行期鉴权 token 的查看 / 刷新。token 按 (tenant, subsystem) 存在后端 PG。
// 过期(401 账号未登录)时,粘贴一份新 token 保存即可恢复,无需重录整条流程。
export default function TokenModal({
  tenant, subsystem, open, onClose,
}: { tenant: string; subsystem: string; open: boolean; onClose: () => void }) {
  const [rec, setRec] = useState<RuntimeToken | null>(null);
  const [loading, setLoading] = useState(false);
  const [reveal, setReveal] = useState(false);
  const [token, setToken] = useState("");
  const [headerName, setHeaderName] = useState("Authorization");
  const [prefix, setPrefix] = useState("Bearer ");
  const [saving, setSaving] = useState(false);

  async function load(revealNow = reveal) {
    setLoading(true);
    try {
      setRec(await getRuntimeToken(tenant, subsystem, revealNow));
    } catch (e: any) {
      message.error("查询失败:" + (e?.response?.data?.detail || e.message));
    } finally {
      setLoading(false);
    }
  }

  // 打开 / 切换子系统时拉取(默认打码)
  useEffect(() => {
    if (open) { setReveal(false); setToken(""); load(false); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, subsystem]);

  async function onToggleReveal(v: boolean) {
    setReveal(v);
    await load(v);   // 明文需重新向后端取(打码在后端做)
  }

  async function save() {
    if (!token.trim()) { message.error("粘贴新 token 再保存"); return; }
    setSaving(true);
    try {
      await putRuntimeToken({
        tenant, subsystem, token: token.trim(),
        header_name: headerName.trim() || "Authorization",
        token_prefix: prefix,   // 允许空前缀(有些系统直接放裸 token)
      });
      message.success("已更新 token,运行期立即生效(无需重录)");
      setToken("");
      await load(reveal);
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
        message="页面型 skill 运行期靠这组鉴权头调用目标系统。token 过期会报「账号未登录(401)」——这里粘贴一份新 token 保存即可恢复,不用重录。"
      />

      <Space style={{ marginBottom: 8, justifyContent: "space-between", width: "100%" }}>
        <Typography.Text strong>当前 token</Typography.Text>
        <Space>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>显示明文</Typography.Text>
          <Switch size="small" checked={reveal} onChange={onToggleReveal} loading={loading} />
          <Button size="small" icon={<ReloadOutlined />} onClick={() => load()} loading={loading}>刷新</Button>
        </Space>
      </Space>

      {headerKeys.length ? (
        <Descriptions bordered size="small" column={1} style={{ marginBottom: 6 }}>
          {headerKeys.map((k) => (
            <Descriptions.Item key={k} label={k}>
              <Typography.Text copyable={reveal} style={{ wordBreak: "break-all", fontFamily: "monospace" }}>
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
          来源:{rec.source === "manual" ? "手动刷新" : "录制自动抓"} · 更新时间:{rec.updated_at ? new Date(rec.updated_at).toLocaleString() : "-"}
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
