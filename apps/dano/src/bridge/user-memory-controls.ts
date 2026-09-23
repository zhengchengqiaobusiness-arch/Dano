/** Browser-safe settings projection, supplied by the authenticated host runtime. */
import type { UserMemoryStatus, UserMemoryOperation, UserMemoryOperationPage, UserMemoryContent } from "../../types/memory.js";
import type { MemoryGovernanceService } from "@josephyoung/pi-openviking/host";
export type { UserMemoryStatus } from "../../types/memory.js";
export interface UserMemoryControls {
  status(): Promise<UserMemoryStatus>;
  setEnabled(enabled: boolean): Promise<void>;
  setAutomaticCollection(enabled: boolean, policyVersion?: string): Promise<void>;
  operation(id: string): Promise<UserMemoryOperation | undefined>;
  operations(cursor?: string): Promise<UserMemoryOperationPage>;
  content(id: string, index: number): Promise<UserMemoryContent | undefined>;
  governance(): MemoryGovernanceService;
  wakeGovernance(): void;
  retire(): Promise<void>;
  finalizeRetirement(): Promise<void>;
}
