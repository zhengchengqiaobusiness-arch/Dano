export function replaceMemoryUserKey(
  configRoot: string,
  dataRoot: string,
  userId: string,
  newKey: string,
): Promise<{ replaced: number }>;
