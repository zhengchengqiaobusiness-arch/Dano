export declare function checkpoint(dataRoot: string, recoveryRoot: string, outputFile: string):
  Promise<{ owners: number; journalBytes: number }>;

export declare function auditRetention(recoveryRoot: string): Promise<{
  owners: number; legacyInlineBodies: number; activePayloads: number;
  prunedPayloadReferences: number; needsMigration: boolean;
}>;

export declare function reconcile(configDirectory: string, dataRoot: string, recoveryRoot: string,
  checkpointFile: string, preflightOnly?: boolean): Promise<{ owners: number; events: number }>;
