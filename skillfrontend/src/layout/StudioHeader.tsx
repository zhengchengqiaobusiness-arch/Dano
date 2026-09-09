import { Button, Steps, Tag } from "antd";
import { LogoutOutlined } from "@ant-design/icons";

export default function StudioHeader({
  current,
  keepRecording = false,
  keepResult = false,
  onChange,
  tenant,
  onSwitchTenant,
}: {
  current: number;
  keepRecording?: boolean;
  keepResult?: boolean;
  onChange: (next: number) => void;
  tenant?: string;
  onSwitchTenant?: () => void;
}) {
  return (
    <header className="studio-header">
      <div className="studio-brand">
        <div className="studio-logo" aria-hidden>π</div>
        <div>
          <div className="studio-brand-title">Dano Skill</div>
          <div className="studio-brand-sub">真实操作 · 可执行 Skill</div>
        </div>
      </div>
      <div className="studio-header-steps">
        <Steps
          size="small"
          current={current}
          onChange={onChange}
          items={[
            {
              title: "录制准备",
              status: current === 0 ? "process" : current > 0 ? "finish" : "wait",
            },
            {
              title: "页面录制",
              disabled: !keepRecording,
              status: current === 1 ? "process" : keepRecording ? "finish" : "wait",
            },
            {
              title: "能力结果",
              disabled: !keepResult,
              status: current === 2 ? "process" : keepResult ? "finish" : "wait",
            },
            {
              title: "Skill 目录",
              status: current === 3 ? "process" : "wait",
            },
          ]}
        />
      </div>
      <div className="studio-header-right">
        {tenant ? <Tag color="cyan">租户 {tenant}</Tag> : null}
        {onSwitchTenant ? (
          <Button size="small" type="text" icon={<LogoutOutlined />} onClick={onSwitchTenant}>切换租户</Button>
        ) : null}
      </div>
    </header>
  );
}
