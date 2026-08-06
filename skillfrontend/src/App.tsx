import { Navigate, Route, Routes } from "react-router-dom";
import AppLayout from "./layout/AppLayout";
import Tenant from "./pages/Tenant";
import RegisterTenant from "./pages/RegisterTenant";
import Skills from "./pages/Skills";
import SkillDetail from "./pages/SkillDetail";
import Onboard from "./pages/Onboard";
import Recording from "./pages/Recording";
import { getTenantKey } from "./api/client";

// 强制登录墙:后台所有页面(含接入系统/录制)都必须先登录租户账号;
// 未登录(无 api_key)一律重定向到 /tenant 登录页。
function RequireAuth({ children }: { children: JSX.Element }) {
  if (getTenantKey()) return children;
  return <Navigate to="/tenant" replace />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/tenant" element={<Tenant />} />
      <Route path="/tenant/register" element={<RegisterTenant />} />
      <Route element={<RequireAuth><AppLayout /></RequireAuth>}>
        <Route path="/skills" element={<Skills />} />
        <Route path="/skills/:skillId" element={<SkillDetail />} />
        <Route path="/onboard" element={<Onboard />} />
        <Route path="/recording" element={<Recording />} />
      </Route>
      <Route path="*" element={<RequireAuth><Navigate to="/onboard" replace /></RequireAuth>} />
    </Routes>
  );
}
