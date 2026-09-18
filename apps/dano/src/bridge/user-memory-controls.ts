/** Browser-safe settings projection, supplied by the authenticated host runtime. */
import type { UserMemoryStatus, UserMemoryOperation, UserMemoryOperationPage } from "../../types/memory.js";
export type { UserMemoryStatus } from "../../types/memory.js";
export interface UserMemoryControls {
  status(): Promise<UserMemoryStatus>;
  setEnabled(enabled: boolean): Promise<void>;
  operation(id: string): Promise<UserMemoryOperation | undefined>;
  operations(cursor?: string): Promise<UserMemoryOperationPage>;
}
