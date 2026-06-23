import { Layout, Menu, Button, Tag, Space, Typography } from "antd";
import { AppstoreOutlined, ImportOutlined, SafetyOutlined, LogoutOutlined, GlobalOutlined } from "@ant-design/icons";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { clearTenant, TENANT_NAME } from "../api/client";

const { Header, Sider, Content } = Layout;

export default function AppLayout() {
  const nav = useNavigate();
  const loc = useLocation();
  const tenant = localStorage.getItem(TENANT_NAME) || "—";
  const selected = loc.pathname.startsWith("/onboard-page")
    ? "onboard-page"
    : loc.pathname.startsWith("/onboard") ? "onboard" : "skills";

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Sider theme="light" width={210} style={{ borderRight: "1px solid #f0f0f0" }}>
        <div style={{ padding: "16px 20px", fontSize: 16, fontWeight: 500 }}>Dano Skill 管理</div>
        <Menu
          mode="inline"
          selectedKeys={[selected]}
          onClick={(e) => {
            if (e.key === "skills") nav("/skills");
            if (e.key === "onboard") nav("/onboard");
            if (e.key === "onboard-page") nav("/onboard-page");
          }}
          items={[
            { key: "skills", icon: <AppstoreOutlined />, label: "Skill 目录" },
            { key: "onboard", icon: <ImportOutlined />, label: "接入系统(API)" },
            { key: "onboard-page", icon: <GlobalOutlined />, label: "接入页面(无 API)" },
            { key: "ops", icon: <SafetyOutlined />, label: "运维保障(P2)", disabled: true },
          ]}
        />
      </Sider>
      <Layout>
        <Header style={{ background: "#fff", borderBottom: "1px solid #f0f0f0", display: "flex", alignItems: "center", justifyContent: "space-between", padding: "0 20px" }}>
          <Typography.Text type="secondary">阶段一 接入生成 · 阶段三 运维(P0:目录 + 测试调用)</Typography.Text>
          <Space>
            <Tag color="blue">租户 {tenant}</Tag>
            <Button size="small" icon={<LogoutOutlined />} onClick={() => { clearTenant(); nav("/tenant"); }}>
              切换租户
            </Button>
          </Space>
        </Header>
        <Content style={{ padding: 20 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
