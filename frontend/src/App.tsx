import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import AppLayout from "./layout/AppLayout";
import Tenant from "./pages/Tenant";
import RegisterTenant from "./pages/RegisterTenant";
import Skills from "./pages/Skills";
import Recording from "./pages/Recording";
import { getTenantKey } from "./api/client";

function RequireAuth({ children }: { children: JSX.Element }) {
  const loc = useLocation();
  if (getTenantKey()) return children;
  if (/(?:^|\/)recording\/?$/.test(loc.pathname)) return children;
  return <Navigate to="/tenant" replace />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/tenant" element={<Tenant />} />
      <Route path="/tenant/register" element={<RegisterTenant />} />
      <Route element={<RequireAuth><AppLayout /></RequireAuth>}>
        <Route path="/skills" element={<Skills />} />
        <Route path="/recording" element={<Recording />} />
      </Route>
      <Route path="*" element={<RequireAuth><Navigate to="/recording" replace /></RequireAuth>} />
    </Routes>
  );
}
