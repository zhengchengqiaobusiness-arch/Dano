import { Link, Navigate, Route, Routes } from "react-router-dom";
import { Alert } from "antd";
import AppLayout from "./layout/AppLayout";
import Tenant from "./pages/Tenant";
import Skills from "./pages/Skills";
import SkillDetail from "./pages/SkillDetail";
import Onboard from "./pages/Onboard";
import Settings from "./pages/Settings";
import { getTenantKey } from "./api/client";
import { reapplyIfSaved } from "./api/settings";
import { useEffect } from "react";

// 仅按租户隔离的数据页(Skill 目录)需要租户;无租户时在布局内提示而不是踢出去,
// 这样左侧菜单(含「运行配置」「接入系统」)始终可点——运行配置是全局的,不该被租户挡住。
function RequireTenant({ children }: { children: JSX.Element }) {
  if (getTenantKey()) return children;
  return (
    <Alert
      type="warning"
      showIcon
      message="还没进入租户"
      description={
        <>
          Skill 目录按租户隔离,请先到 <Link to="/tenant">创建 / 进入租户</Link>。
          「运行配置」「接入系统」无需租户即可使用。
        </>
      }
    />
  );
}

export default function App() {
  useEffect(() => { reapplyIfSaved(); }, []);   // 启动时把本地保存的密钥/凭证重发给后端
  return (
    <Routes>
      <Route path="/tenant" element={<Tenant />} />
      <Route element={<AppLayout />}>
        <Route path="/skills" element={<RequireTenant><Skills /></RequireTenant>} />
        <Route path="/skills/:skillId" element={<RequireTenant><SkillDetail /></RequireTenant>} />
        <Route path="/onboard" element={<Onboard />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/settings" replace />} />
      </Route>
    </Routes>
  );
}
