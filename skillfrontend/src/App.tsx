import { Link, Navigate, Route, Routes } from "react-router-dom";
import { Alert } from "antd";
import AppLayout from "./layout/AppLayout";
import Tenant from "./pages/Tenant";
import Skills from "./pages/Skills";
import SkillDetail from "./pages/SkillDetail";
import Onboard from "./pages/Onboard";
import PageOnboard from "./pages/PageOnboard";
import { getTenantKey } from "./api/client";

// 仅按租户隔离的数据页(Skill 目录)需要租户;无租户时在布局内提示而不是踢出去,
// 这样左侧菜单(「接入系统」)始终可点。运行配置已全部走后端 config.py,前端不再有配置页。
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
          「接入系统」无需租户即可使用。
        </>
      }
    />
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/tenant" element={<Tenant />} />
      <Route element={<AppLayout />}>
        <Route path="/skills" element={<RequireTenant><Skills /></RequireTenant>} />
        <Route path="/skills/:skillId" element={<RequireTenant><SkillDetail /></RequireTenant>} />
        <Route path="/onboard" element={<Onboard />} />
        <Route path="/onboard-page" element={<PageOnboard />} />
        <Route path="*" element={<Navigate to="/onboard" replace />} />
      </Route>
    </Routes>
  );
}
