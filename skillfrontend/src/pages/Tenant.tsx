import { useState } from "react";
import { Alert, Button, Card, Divider, Form, Input, Modal, Space, Tag, Typography, message } from "antd";
import { Link, useNavigate } from "react-router-dom";
import {
  changePassword,
  login,
  loginTotp,
  regenerateBackupCodes,
  totpActivate,
  totpDisable,
  totpSetup,
  type TotpSetup,
} from "../api/skills";
import { setTenant, getTenantKey } from "../api/client";

/** 后端错误提示;网络层异常时退回 message。 */
function detailOf(e: any): string {
  return e?.response?.data?.detail || e?.message || "未知错误";
}

export default function Tenant() {
  const nav = useNavigate();
  const [loading, setLoading] = useState(false);
  const [pwLoading, setPwLoading] = useState(false);
  const [totpLoading, setTotpLoading] = useState(false);

  // 两步登录:密码通过后拿到 challenge,切到验证码面板
  const [challenge, setChallenge] = useState("");
  // 绑定流程:setup 拿到二维码后进入待激活状态
  const [setup, setSetup] = useState<TotpSetup | null>(null);
  const [bound, setBound] = useState(false);
  const [backupCodes, setBackupCodes] = useState<string[]>([]);
  // 「重新生成备用码」与「关闭两步验证」共用同一张表单(都要密码 + 验证码)
  const [manageForm] = Form.useForm<{ password: string; code: string }>();

  async function onLogin(v: { username: string; password: string }) {
    setLoading(true);
    try {
      const r = await login(v.username.trim(), v.password);
      if (r.need_totp) {
        setChallenge(r.challenge);
        message.info("请输入 Authenticator 上的验证码");
        return;
      }
      setTenant(r.tenant, r.api_key);
      message.success(`已登录租户 ${r.tenant}`);
      nav("/recording");
    } catch (e: any) {
      message.error("登录失败:" + detailOf(e));
    } finally {
      setLoading(false);
    }
  }

  async function onVerifyTotp(v: { code: string }) {
    setLoading(true);
    try {
      const r = await loginTotp(challenge, v.code.trim());
      setTenant(r.tenant, r.api_key);
      message.success(`已登录租户 ${r.tenant}`);
      nav("/recording");
    } catch (e: any) {
      message.error("验证失败:" + detailOf(e));
      setChallenge("");   // challenge 可能已作废,退回密码步重来
    } finally {
      setLoading(false);
    }
  }

  async function onChangePassword(v: {
    oldPassword: string;
    newPassword: string;
    code?: string;
  }) {
    setPwLoading(true);
    try {
      await changePassword(v.oldPassword, v.newPassword, (v.code || "").trim());
      message.success("密码已更新");
    } catch (e: any) {
      message.error("修改失败:" + detailOf(e));
    } finally {
      setPwLoading(false);
    }
  }

  async function onTotpSetup() {
    setTotpLoading(true);
    try {
      setSetup(await totpSetup());
    } catch (e: any) {
      if (e?.response?.status === 409) setBound(true);   // 本会话之外已经绑过
      message.error("无法开启:" + detailOf(e));
    } finally {
      setTotpLoading(false);
    }
  }

  async function onTotpActivate(v: { code: string }) {
    setTotpLoading(true);
    try {
      const codes = await totpActivate(v.code.trim());
      setSetup(null);
      setBound(true);
      setBackupCodes(codes);
    } catch (e: any) {
      message.error("激活失败:" + detailOf(e));
    } finally {
      setTotpLoading(false);
    }
  }

  async function onTotpDisable(v: { password: string; code: string }) {
    setTotpLoading(true);
    try {
      await totpDisable(v.password, v.code.trim());
      setBound(false);
      message.success("两步验证已关闭");
    } catch (e: any) {
      message.error("关闭失败:" + detailOf(e));
    } finally {
      setTotpLoading(false);
    }
  }

  async function onRegenerate(v: { password: string; code: string }) {
    setTotpLoading(true);
    try {
      setBackupCodes(await regenerateBackupCodes(v.password, v.code.trim()));
    } catch (e: any) {
      message.error("生成失败:" + detailOf(e));
    } finally {
      setTotpLoading(false);
    }
  }

  return (
    <div style={{ maxWidth: 460, margin: "8vh auto", padding: 16 }}>
      <Typography.Title level={3} style={{ textAlign: "center" }}>Dano Skill 管理后台</Typography.Title>

      {!challenge ? (
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
      ) : (
        <Card title="两步验证">
          <Form layout="vertical" onFinish={onVerifyTotp}>
            <Form.Item
              name="code"
              label="验证码"
              extra="Authenticator 上的 6 位码;也可填一个备用码(XXXXX-XXXXX)"
              rules={[{ required: true, message: "填验证码" }]}
            >
              <Input placeholder="123456" autoComplete="one-time-code" maxLength={11} autoFocus />
            </Form.Item>
            <Button type="primary" htmlType="submit" loading={loading} block>
              验证并登录
            </Button>
          </Form>
          <Typography.Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0, textAlign: "center" }}>
            <a onClick={() => setChallenge("")}>返回重新输入密码</a>
          </Typography.Paragraph>
        </Card>
      )}

      {getTenantKey() && (
        <>
          <Card title="修改密码" style={{ marginTop: 16 }}>
            <Form layout="vertical" onFinish={onChangePassword}>
              <Form.Item name="oldPassword" label="原密码" rules={[{ required: true, message: "填原密码" }]}>
                <Input.Password placeholder="原密码" autoComplete="current-password" />
              </Form.Item>
              <Form.Item name="newPassword" label="新密码" rules={[{ required: true, min: 12, message: "至少 12 位" }]}>
                <Input.Password placeholder="新密码(至少 12 位)" autoComplete="new-password" />
              </Form.Item>
              <Form.Item name="code" label="验证码" extra="已开启两步验证时必填">
                <Input placeholder="123456" autoComplete="one-time-code" maxLength={11} />
              </Form.Item>
              <Button htmlType="submit" loading={pwLoading} block>
                修改密码
              </Button>
            </Form>
          </Card>

          <Card
            title="两步验证"
            style={{ marginTop: 16 }}
            extra={bound ? <Tag color="green">已开启</Tag> : <Tag>未开启</Tag>}
          >
            {bound ? (
              <>
                <Typography.Paragraph type="secondary">
                  登录时除密码外还需 Authenticator 上的 6 位码。关闭或重新生成备用码都需要密码 + 验证码。
                </Typography.Paragraph>
                <Form form={manageForm} layout="vertical" onFinish={onRegenerate}>
                  <Form.Item name="password" label="密码" rules={[{ required: true, message: "填密码" }]}>
                    <Input.Password placeholder="当前密码" autoComplete="current-password" />
                  </Form.Item>
                  <Form.Item name="code" label="验证码" rules={[{ required: true, message: "填验证码" }]}>
                    <Input placeholder="123456" autoComplete="one-time-code" maxLength={11} />
                  </Form.Item>
                  <Space>
                    <Button htmlType="submit" loading={totpLoading}>重新生成备用码</Button>
                    <Button
                      danger
                      loading={totpLoading}
                      onClick={async () => {
                        const values = await manageForm.validateFields();
                        await onTotpDisable(values);
                        manageForm.resetFields();
                      }}
                    >
                      关闭两步验证
                    </Button>
                  </Space>
                </Form>
              </>
            ) : setup ? (
              <>
                <Typography.Paragraph>
                  用 Authenticator 扫描二维码,或手动输入密钥,然后填入 6 位码完成绑定。
                </Typography.Paragraph>
                <div style={{ textAlign: "center", marginBottom: 12 }}>
                  <img src={setup.qr_svg_data_uri} width={180} height={180} alt="TOTP 绑定二维码" />
                </div>
                <Typography.Paragraph copyable={{ text: setup.secret }} code style={{ wordBreak: "break-all" }}>
                  {setup.secret}
                </Typography.Paragraph>
                <Form layout="vertical" onFinish={onTotpActivate}>
                  <Form.Item name="code" label="验证码" rules={[{ required: true, message: "填 6 位验证码" }]}>
                    <Input placeholder="123456" autoComplete="one-time-code" maxLength={6} />
                  </Form.Item>
                  <Space>
                    <Button type="primary" htmlType="submit" loading={totpLoading}>确认绑定</Button>
                    <Button onClick={() => setSetup(null)}>取消</Button>
                  </Space>
                </Form>
              </>
            ) : (
              <>
                <Typography.Paragraph type="secondary">
                  开启后,登录需要密码 + Authenticator 验证码两步。
                </Typography.Paragraph>
                <Button type="primary" loading={totpLoading} onClick={onTotpSetup} block>
                  开启两步验证
                </Button>
              </>
            )}
          </Card>

          <Divider plain />
          <Typography.Paragraph type="secondary" style={{ textAlign: "center" }}>
            已登录,可直接 <a onClick={() => nav("/recording")}>进入录制 V2</a>。
          </Typography.Paragraph>
        </>
      )}

      <Modal
        open={backupCodes.length > 0}
        title="请保存备用码"
        onCancel={() => setBackupCodes([])}
        onOk={() => setBackupCodes([])}
        okText="我已保存"
        cancelButtonProps={{ style: { display: "none" } }}
      >
        <Alert
          type="warning"
          showIcon
          message="这些码只显示这一次,关闭后无法再查看"
          description="每个码只能用一次,可在手机丢失时代替验证码登录。"
          style={{ marginBottom: 12 }}
        />
        <Typography.Paragraph copyable={{ text: backupCodes.join("\n") }}>
          {backupCodes.map((c) => (
            <div key={c}><code>{c}</code></div>
          ))}
        </Typography.Paragraph>
      </Modal>
    </div>
  );
}
