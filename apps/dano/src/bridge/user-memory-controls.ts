/** Browser-safe settings projection, supplied by the authenticated host runtime. */
export interface UserMemoryStatus {
  enabled: boolean;
  automaticCollection: boolean;
  effectiveAt: string;
  policyVersion: string;
  revision: number;
}
export interface UserMemoryControls {
  status(): Promise<UserMemoryStatus>;
  setEnabled(enabled: boolean): Promise<void>;
}
