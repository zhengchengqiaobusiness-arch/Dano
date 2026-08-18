import { TENANT_NAME } from "../api/client";
import PageRecorder from "../components/PageRecorder";

export default function Recording() {
  const tenant = localStorage.getItem(TENANT_NAME) || "";
  const subsystem = new URLSearchParams(window.location.search).get("subsystem") || "";
  const baseUrl = "";
  const storageState = "";

  return (
    <div style={{ width: "100%", height: "100%", minHeight: 0, overflow: "hidden", display: "flex", flexDirection: "column" }}>
      <PageRecorder tenant={tenant} subsystem={subsystem} baseUrl={baseUrl} storageState={storageState} />
    </div>
  );
}
