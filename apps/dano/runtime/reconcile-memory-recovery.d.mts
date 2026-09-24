export declare function checkpoint(dataRoot: string, recoveryRoot: string, outputFile: string):
  Promise<{ owners: number; journalBytes: number }>;

export declare function reconcile(configDirectory: string, dataRoot: string, recoveryRoot: string,
  checkpointFile: string, preflightOnly?: boolean): Promise<{ owners: number; events: number }>;
