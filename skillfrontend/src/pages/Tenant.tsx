import { useState } from "react";
import { Card, Form, Input, Button, Typography, Divider, message, Space } from "antd";
import { useNavigate } from "react-router-dom";
import { createTenant } from "../api/skills";
import { setTenant, getTenantKey } from "../api/client";

export default function Tenant() {
  const nav = useNavigate();
  const [loading, setLoading] = useState(false);

  async function onCreate(v: { tenant: string }) {
    setLoading(true);
    try {
      const r = await createTenant(v.tenant.trim());
      setTenant(r.tenant, r.api_key);
      message.success(`租户 ${r.tenant} 已就绪`);
      nav("/skills");
    } catch (e: any) {
      message.error("建租户失败:" + (e?.response?.data?.detail || e.message));
    } finally {
      setLoading(false);
    }
  }

  function onUseKey(v: { name: string; key: string }) {
    setTenant(v.name.trim() || "tenant", v.key.trim());
    nav("/skills");
  }

  return (
    <div style={{ maxWidth: 460, margin: "8vh auto", padding: 16 }}>
      <Typography.Title level={3} style={{ textAlign: "center" }}>Dano Skill 管理后台</Typography.Title>
      <Card title="新建 / 进入租户">
        <Form layout="vertical" onFinish={onCreate}>
          <Form.Item name="tenant" label="租户名" rules={[{ required: true, message: "填租户名,如 acme" }]}>
            <Input placeholder="acme" />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={loading} block>
            创建并进入(POST /tenants 拿 key)
          </Button>
        </Form>
        <Divider plain>或 已有 api_key 直接进入</Divider>
        <Form layout="vertical" onFinish={onUseKey}>
          <Space.Compact style={{ width: "100%" }}>
            <Form.Item name="name" noStyle>
              <Input placeholder="租户名(显示用)" style={{ width: "35%" }} />
            </Form.Item>
            <Form.Item name="key" noStyle rules={[{ required: true }]}>
              <Input placeholder="X-Tenant-Key" style={{ width: "65%" }} />
            </Form.Item>
          </Space.Compact>
          <Button htmlType="submit" block style={{ marginTop: 12 }}>用此 key 进入</Button>
        </Form>
        {getTenantKey() && (
          <Typography.Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0 }}>
            已有登录态,可直接 <a onClick={() => nav("/skills")}>进入目录</a>。
          </Typography.Paragraph>
        )}
      </Card>
    </div>
  );
}
