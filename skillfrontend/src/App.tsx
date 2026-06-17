import { Navigate, Route, Routes } from "react-router-dom";
import AppLayout from "./layout/AppLayout";
import Tenant from "./pages/Tenant";
import Skills from "./pages/Skills";
import SkillDetail from "./pages/SkillDetail";
import Onboard from "./pages/Onboard";
import { getTenantKey } from "./api/client";

function RequireTenant({ children }: { children: JSX.Element }) {
  return getTenantKey() ? children : <Navigate to="/tenant" replace />;
}

export default function App() {
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
        <Route path="*" element={<Navigate to="/skills" replace />} />
      </Route>
    </Routes>
  );
}
