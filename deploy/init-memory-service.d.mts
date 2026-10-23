export function initializeMemoryService(
  directory: string,
  validate: (value: unknown) => unknown,
): Promise<'MEMORY_CONFIG_INITIALIZED' | 'MEMORY_CONFIG_PRESERVED'>;
