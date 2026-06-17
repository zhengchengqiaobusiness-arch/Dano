import { Navigate, Route, Routes } from "react-router-dom";
import AppLayout from "./layout/AppLayout";
import Tenant from "./pages/Tenant";
import Skills from "./pages/Skills";
import SkillDetail from "./pages/SkillDetail";
import Onboard from "./pages/Onboard";
import Settings from "./pages/Settings";
import { getTenantKey } from "./api/client";
import { reapplyIfSaved } from "./api/settings";
import { useEffect } from "react";

function RequireTenant({ children }: { children: JSX.Element }) {
  return getTenantKey() ? children : <Navigate to="/tenant" replace />;
}

export default function App() {
  useEffect(() => { reapplyIfSaved(); }, []);   // 启动时把本地保存的密钥/凭证重发给后端
  return (
    <Routes>
      <Route path="/tenant" element={<Tenant />} />
      <Route
        element={
          <RequireTenant>
            <AppLayout />
          </RequireTenant>
        }
      >
        <Route path="/skills" element={<Skills />} />
        <Route path="/skills/:skillId" element={<SkillDetail />} />
        <Route path="/onboard" element={<Onboard />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/skills" replace />} />
      </Route>
    </Routes>
  );
}
