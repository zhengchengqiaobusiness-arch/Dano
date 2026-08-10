import { useState } from "react";
import { Form, Input } from "antd";
import { TENANT_NAME } from "../api/client";
import PageRecorder from "../components/PageRecorder";

export default function Recording() {
  const tenant = localStorage.getItem(TENANT_NAME) || "";
  const [subsystem, setSubsystem] = useState(() => new URLSearchParams(window.location.search).get("subsystem") || "");
  const baseUrl = "";
  const storageState = "";

  return (
    <div style={{ maxWidth: 1180, margin: "0 auto" }}>
      <Form.Item label="业务系统标识" required>
        <Input value={subsystem} onChange={(event) => setSubsystem(event.target.value)}
          placeholder="例如 oa、crm 或目标系统实例名" />
      </Form.Item>
      <PageRecorder tenant={tenant} subsystem={subsystem} baseUrl={baseUrl} storageState={storageState} />
    </div>
  );
}
