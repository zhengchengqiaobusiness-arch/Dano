export declare function checkpoint(dataRoot: string, recoveryRoot: string, outputFile: string):
  Promise<{ owners: number; journalBytes: number }>;

export declare function auditRetention(recoveryRoot: string): Promise<{
  owners: number; legacyInlineBodies: number; activePayloads: number;
  prunedPayloadReferences: number; needsMigration: boolean;
}>;

export declare function migrateLegacyRecovery(configDirectory: string, dataRoot: string,
  sourceRoot: string, targetRoot: string, outputCheckpoint: string): Promise<{
    owners: number; journalBytes: number; legacyInlineBodies: number;
    retiredBodies: number; activePayloads: number;
  }>;

export declare function reconcile(configDirectory: string, dataRoot: string, recoveryRoot: string,
  checkpointFile: string, preflightOnly?: boolean): Promise<{
    owners: number; events: number; remoteWriterChecks?: number;
  }>;
