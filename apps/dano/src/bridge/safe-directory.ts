import * as fs from "node:fs";

export async function ensureSafeDirectory(
  directoryPath: string,
  options: {
    recursive?: boolean;
    ownerPrivate?: boolean;
    unsafeDirectoryError: () => Error;
  },
): Promise<void> {
  try {
    assertSafeDirectory(
      await fs.promises.lstat(directoryPath),
      options.unsafeDirectoryError,
    );
    if (options.ownerPrivate) await protectOwnerDirectory(directoryPath, options.unsafeDirectoryError);
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
  if (options.ownerPrivate) await protectOwnerDirectory(directoryPath, options.unsafeDirectoryError);
}

/** Tighten only the opened owner directory, without following links or walking contents. */
async function protectOwnerDirectory(directoryPath: string, unsafe: () => Error): Promise<void> {
  const handle = await fs.promises.open(directoryPath, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
  try {
    const stats = await handle.stat();
    if (!stats.isDirectory() || stats.uid !== process.getuid?.()) throw unsafe();
    if ((stats.mode & 0o777) !== 0o700) await handle.chmod(0o700);
  } finally { await handle.close(); }
}

function assertSafeDirectory(
  stats: fs.Stats,
  unsafeDirectoryError: () => Error,
): void {
  if (stats.isSymbolicLink() || !stats.isDirectory()) {
    throw unsafeDirectoryError();
  }
}
