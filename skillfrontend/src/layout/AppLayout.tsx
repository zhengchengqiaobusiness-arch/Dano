import { ConfigProvider, Layout } from "antd";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { clearTenant, TENANT_NAME } from "../api/client";
import { STUDIO_THEME } from "../studioTheme";
import StudioHeader from "./StudioHeader";

const { Content } = Layout;

export default function AppLayout() {
  const nav = useNavigate();
  const loc = useLocation();
  const tenant = localStorage.getItem(TENANT_NAME) || "—";
  const selected = loc.pathname.startsWith("/recording")
    ? "recording"
    : loc.pathname.startsWith("/onboard") ? "onboard" : "skills";

  return (
    <ConfigProvider theme={STUDIO_THEME}>
      <div className="studio-root">
        {selected !== "recording" ? (
          <StudioHeader
            current={selected === "skills" ? 3 : -1}
            keepRecording
            keepResult
            onChange={(next) => {
              if (next === 3) nav("/skills");
              else nav("/recording");
            }}
            tenant={tenant}
            onSwitchTenant={() => { clearTenant(); nav("/tenant"); }}
          />
        ) : null}
        <Content
          className={
            selected === "recording"
              ? "studio-app-content is-recording"
              : selected === "skills"
                ? "studio-app-content is-catalog"
                : "studio-app-content"
          }
        >
          <Outlet />
        </Content>
      </div>
    </ConfigProvider>
  );
}
