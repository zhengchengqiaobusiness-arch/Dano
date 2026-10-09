export interface BuildContextReceipt { targetSha: string; files: number; directory: string }
export function prepareBuildContext(directory: string, expectedSha?: string): BuildContextReceipt;
export function archiveBuildContext(source: string, parent: string, expectedSha?: string): BuildContextReceipt;
