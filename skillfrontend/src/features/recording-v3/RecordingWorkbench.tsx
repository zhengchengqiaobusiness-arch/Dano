import PageRecorder from "../../components/PageRecorder";
import { useRecordingSession } from "./hooks/useRecordingSession";

export interface RecordingWorkbenchProps {
  tenant: string;
  subsystem: string;
  baseUrl: string;
  storageState: string;
}

function ScopedRecordingWorkbench({ tenant, subsystem, baseUrl, storageState }: RecordingWorkbenchProps) {
  const recordingSession = useRecordingSession(tenant, subsystem);
  return (
    <PageRecorder
      tenant={tenant}
      subsystem={subsystem}
      baseUrl={baseUrl}
      storageState={storageState}
      recordingSession={recordingSession}
    />
  );
}

export default function RecordingWorkbench(props: RecordingWorkbenchProps) {
  // A tenant/subsystem pair owns exactly one persisted-session namespace. A
  // keyed scope prevents React from carrying the previous pair's reducer and
  // resume credentials into the next recorder while leaving the DOM unchanged.
  return <ScopedRecordingWorkbench key={JSON.stringify([props.tenant, props.subsystem])} {...props} />;
}
