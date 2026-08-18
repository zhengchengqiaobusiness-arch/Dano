import { useState } from "react";
import { Card, Form, Input, Button, Typography, Divider, message } from "antd";
import { Link, useNavigate } from "react-router-dom";
import { login, changePassword } from "../api/skills";
import { setTenant, getTenantKey } from "../api/client";

export default function Tenant() {
  const nav = useNavigate();
  const [loading, setLoading] = useState(false);
  const [pwLoading, setPwLoading] = useState(false);

  async function onLogin(v: { username: string; password: string }) {
    setLoading(true);
    try {
      const r = await login(v.username.trim(), v.password);
      setTenant(r.tenant, r.api_key);
      message.success(`已登录租户 ${r.tenant}`);
      nav("/recording");
    } catch (e: any) {
      message.error("登录失败:" + (e?.response?.data?.detail || e.message));
    } finally {
      setLoading(false);
    }
  }

  async function onChangePassword(v: { oldPassword: string; newPassword: string }) {
    setPwLoading(true);
    try {
      await changePassword(v.oldPassword, v.newPassword);
      message.success("密码已更新");
    } catch (e: any) {
      message.error("修改失败:" + (e?.response?.data?.detail || e.message));
    } finally {
      setPwLoading(false);
    }
  }

  return (
    <div style={{ maxWidth: 460, margin: "8vh auto", padding: 16 }}>
      <Typography.Title level={3} style={{ textAlign: "center" }}>Dano Skill 管理后台</Typography.Title>
      <Card title="租户登录">
        <Form layout="vertical" onFinish={onLogin}>
          <Form.Item name="username" label="用户名" rules={[{ required: true, message: "填用户名,如 acme" }]}>
            <Input placeholder="acme" autoComplete="username" />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true, message: "填密码" }]}>
            <Input.Password placeholder="密码" autoComplete="current-password" />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={loading} block>
            登录
          </Button>
        </Form>
        <Typography.Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0, textAlign: "center" }}>
          没有账号? <Link to="/tenant/register">新建租户</Link>
        </Typography.Paragraph>
      </Card>

      {getTenantKey() && (
        <Card title="修改密码" style={{ marginTop: 16 }}>
          <Form layout="vertical" onFinish={onChangePassword}>
            <Form.Item name="oldPassword" label="原密码" rules={[{ required: true, message: "填原密码" }]}>
              <Input.Password placeholder="原密码" autoComplete="current-password" />
            </Form.Item>
            <Form.Item name="newPassword" label="新密码" rules={[{ required: true, min: 8, message: "至少 8 位" }]}>
              <Input.Password placeholder="新密码(至少 8 位)" autoComplete="new-password" />
            </Form.Item>
            <Button htmlType="submit" loading={pwLoading} block>
              修改密码
            </Button>
          </Form>
          <Divider plain />
          <Typography.Paragraph type="secondary" style={{ marginTop: 0, marginBottom: 0, textAlign: "center" }}>
            已登录,可直接 <a onClick={() => nav("/recording")}>进入录制 V2</a>。
          </Typography.Paragraph>
        </Card>
      )}
    </div>
  );
}
