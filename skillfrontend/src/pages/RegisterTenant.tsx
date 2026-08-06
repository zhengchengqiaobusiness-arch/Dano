import { useState } from "react";
import { Card, Form, Input, Button, Typography, message } from "antd";
import { Link, useNavigate } from "react-router-dom";
import { createTenantWithPassword } from "../api/skills";
import { setTenant } from "../api/client";

export default function RegisterTenant() {
  const nav = useNavigate();
  const [loading, setLoading] = useState(false);

  async function onCreate(v: { tenant: string; username: string; password: string }) {
    setLoading(true);
    try {
      const r = await createTenantWithPassword(v.tenant.trim(), v.username.trim(), v.password);
      setTenant(r.tenant, r.api_key);
      message.success(`租户 ${r.tenant} 已就绪`);
      nav("/skills");
    } catch (e: any) {
      message.error("建租户失败:" + (e?.response?.data?.detail || e.message));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ maxWidth: 460, margin: "8vh auto", padding: 16 }}>
      <Typography.Title level={3} style={{ textAlign: "center" }}>Dano Skill 管理后台</Typography.Title>
      <Card title="新建租户">
        <Form layout="vertical" onFinish={onCreate}>
          <Form.Item name="tenant" label="租户名" rules={[{ required: true, message: "填租户名,如 acme" }]}>
            <Input placeholder="acme" />
          </Form.Item>
          <Form.Item name="username" label="登录用户名" rules={[{ required: true, message: "填登录用户名" }]}>
            <Input placeholder="acme" autoComplete="username" />
          </Form.Item>
          <Form.Item name="password" label="初始密码" rules={[{ required: true, min: 8, message: "至少 8 位" }]}>
            <Input.Password placeholder="初始密码(至少 8 位)" autoComplete="new-password" />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={loading} block>
            创建并进入
          </Button>
        </Form>
        <Typography.Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0, textAlign: "center" }}>
          已有账号? <Link to="/tenant">返回登录</Link>
        </Typography.Paragraph>
      </Card>
    </div>
  );
}
