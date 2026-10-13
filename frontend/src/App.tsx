import { Navigate, Route, Routes } from "react-router-dom";
import AppLayout from "./layout/AppLayout";
import Tenant from "./pages/Tenant";
import Skills from "./pages/Skills";
import Recording from "./pages/Recording";
import { getTenantKey } from "./api/client";

function RequireAuth({ children }: { children: JSX.Element }) {
  if (getTenantKey()) return children;
  return <Navigate to="/tenant" replace />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/tenant" element={<Tenant />} />
      <Route path="/tenant/register" element={<Navigate to="/tenant" replace />} />
      <Route element={<RequireAuth><AppLayout /></RequireAuth>}>
        <Route path="/skills" element={<Skills />} />
        <Route path="/recording" element={<Recording />} />
      </Route>
      <Route path="*" element={<RequireAuth><Navigate to="/recording" replace /></RequireAuth>} />
    </Routes>
  );
}
