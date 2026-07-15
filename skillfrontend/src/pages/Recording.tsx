import { useState } from "react";
import { TENANT_NAME } from "../api/client";
import RecordingWorkbench from "../features/recording-v3/RecordingWorkbench";

export default function Recording() {
  const tenant = localStorage.getItem(TENANT_NAME) || "";
  const [subsystem, setSubsystem] = useState("A-OA");
  const [baseUrl, setBaseUrl] = useState("");
  const [storageState, setStorageState] = useState("");

  return (
    <div style={{ maxWidth: 1180, margin: "0 auto" }}>
      <RecordingWorkbench tenant={tenant} subsystem={subsystem} baseUrl={baseUrl} storageState={storageState} />
    </div>
  );
}
