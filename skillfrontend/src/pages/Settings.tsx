import { useEffect, useState } from "react";
import {
  Card, Form, Input, Switch, Button, Space, Typography, message, Tag, Divider, Alert,
} from "antd";
import { PlusOutlined, DeleteOutlined } from "@ant-design/icons";
import { applyRuntime, getRuntime, loadSaved, saveLocal, RuntimeStatus } from "../api/settings";

interface CredRow { tenant: string; token: string }

export default function Settings() {
  const saved = loadSaved();
  const [piKey, setPiKey] = useState(saved.pi_api_key || "");
  const [piBase, setPiBase] = useState(saved.pi_base_url || "https://api.siliconflow.cn/v1");
  const [piModel, setPiModel] = useState(saved.pi_model || "deepseek-ai/DeepSeek-V3.2");
  const [insecure, setInsecure] = useState(saved.insecure_tls ?? true);
  const [creds, setCreds] = useState<CredRow[]>(
    Object.entries(saved.runtime_credentials || {}).map(([k, v]) => ({
      tenant: k.replace(/\/oa$/, ""),
      token: (v as { token: string }).token,
    })),
  );
  const [status, setStatus] = useState<RuntimeStatus | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getRuntime().then(setStatus).catch(() => setStatus(null));
  }, []);

  async function onSave() {
    const runtime_credentials: Record<string, { token: string }> = {};
    for (const c of creds) {
      if (c.tenant.trim() && c.token.trim()) runtime_credentials[`${c.tenant.trim()}/oa`] = { token: c.token.trim() };
    }
    const cfg = {
      pi_api_key: piKey.trim() || undefined,
      pi_base_url: piBase.trim(),
      pi_model: piModel.trim(),
      insecure_tls: insecure,
      runtime_credentials,
    };
    setBusy(true);
    try {
      const st = await applyRuntime(cfg);
      saveLocal(cfg);
      setStatus(st);
      message.success("已应用到后端(本地已保存,重启后自动重发)");
    } catch (e: any) {
      message.error("应用失败:" + (e?.response?.data?.detail || e.message));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ maxWidth: 720, margin: "0 auto" }}>
      <Typography.Title level={4}>运行配置</Typography.Title>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="密钥/凭证在此填写,提交后存入后端进程内存(不写文件、不进 bat);本地浏览器保存一份,后端重启后自动重发。"
      />

      <Card title="模型(编码 + 三模型评审,OpenAI 兼容)" style={{ marginBottom: 16 }}>
        <Form layout="vertical">
          <Form.Item label="API Key（SiliconFlow / OpenAI 兼容)" required>
            <Input.Password value={piKey} onChange={(e) => setPiKey(e.target.value)} placeholder="sk-..." />
          </Form.Item>
          <Form.Item label="Base URL"><Input value={piBase} onChange={(e) => setPiBase(e.target.value)} /></Form.Item>
          <Form.Item label="编码模型"><Input value={piModel} onChange={(e) => setPiModel(e.target.value)} /></Form.Item>
          <Form.Item label="忽略 TLS 证书校验(目标系统自签证书时开)">
            <Switch checked={insecure} onChange={setInsecure} />
          </Form.Item>
        </Form>
      </Card>

      <Card title="调用期 OA 凭证(测试调用写流程时后端取它打 OA)">
        <Typography.Paragraph type="secondary">按租户配 token(键 = 租户名,映射到 “租户/oa”)。</Typography.Paragraph>
        {creds.map((c, i) => (
          <Space key={i} style={{ display: "flex", marginBottom: 8 }}>
            <Input placeholder="租户名,如 codegen-oa" value={c.tenant} style={{ width: 200 }}
              onChange={(e) => { const n = [...creds]; n[i] = { ...c, tenant: e.target.value }; setCreds(n); }} />
            <Input.Password placeholder="OA Bearer token" value={c.token} style={{ width: 340 }}
              onChange={(e) => { const n = [...creds]; n[i] = { ...c, token: e.target.value }; setCreds(n); }} />
            <Button danger icon={<DeleteOutlined />} onClick={() => setCreds(creds.filter((_, j) => j !== i))} />
          </Space>
        ))}
        <Button icon={<PlusOutlined />} onClick={() => setCreds([...creds, { tenant: "", token: "" }])}>加一条</Button>
      </Card>

      <Divider />
      <Space>
        <Button type="primary" loading={busy} onClick={onSave}>保存并应用</Button>
        {status && (
          <span>
            后端当前:模型 key {status.pi_key_set ? <Tag color="green">已配</Tag> : <Tag color="red">未配</Tag>}
            · {status.pi_model || "-"} · TLS校验{status.insecure_tls ? "关" : "开"}
            · 凭证 {(status.runtime_credential_keys || []).length ? (status.runtime_credential_keys || []).join(",") : "无"}
          </span>
        )}
      </Space>
    </div>
  );
}
