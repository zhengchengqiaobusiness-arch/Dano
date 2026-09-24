import { Navigate, Route, Routes } from "react-router-dom";
import AppLayout from "./layout/AppLayout";
import Skills from "./pages/Skills";
import Recording from "./pages/Recording";

export default function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route path="/skills" element={<Skills />} />
        <Route path="/recording" element={<Recording />} />
      </Route>
      <Route path="*" element={<Navigate to="/recording" replace />} />
    </Routes>
  );
}
