import * as fs from "node:fs";

export async function ensureSafeDirectory(
  directoryPath: string,
  options: {
    recursive?: boolean;
    unsafeDirectoryError: () => Error;
  },
): Promise<void> {
  try {
    assertSafeDirectory(
      await fs.promises.lstat(directoryPath),
      options.unsafeDirectoryError,
    );
    return;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
  }

  try {
    await fs.promises.mkdir(directoryPath, {
      recursive: options.recursive ?? false,
      mode: 0o700,
    });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
  }

  assertSafeDirectory(
    await fs.promises.lstat(directoryPath),
    options.unsafeDirectoryError,
  );
}

function assertSafeDirectory(
  stats: fs.Stats,
  unsafeDirectoryError: () => Error,
): void {
  if (stats.isSymbolicLink() || !stats.isDirectory()) {
    throw unsafeDirectoryError();
  }
}
