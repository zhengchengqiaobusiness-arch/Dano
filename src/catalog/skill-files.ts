import { cp, rename, rm } from "node:fs/promises";
import path from "node:path";
import { ensureDir } from "../utils.js";

export type DirectoryMover = {
  rename: typeof rename;
  cp: typeof cp;
  rm: typeof rm;
  ensureDir: typeof ensureDir;
};

const defaultMover: DirectoryMover = { rename, cp, rm, ensureDir };

export function isRetryableFsError(error: unknown) {
  const code = error && typeof error === "object" && "code" in error ? String((error as { code?: string }).code) : "";
  return ["EXDEV", "EPERM", "EACCES", "EBUSY", "ENOTEMPTY", "EAGAIN", "EIO"].includes(code);
}

function wait(ms: number) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

export async function moveDirectory(from: string, to: string, io: DirectoryMover = defaultMover) {
  await io.ensureDir(path.dirname(to));
  let lastError: unknown;
  for (const delay of [0, 60, 160, 360, 720]) {
    if (delay) await wait(delay);
    try {
      await io.rename(from, to);
      return;
    } catch (error) {
      lastError = error;
      if (!isRetryableFsError(error)) throw error;
      if ((error as { code?: string })?.code === "EXDEV") break;
    }
  }
  await io.cp(from, to, { recursive: true, force: true });
  try {
    await io.rm(from, { recursive: true, force: true, maxRetries: 6, retryDelay: 80 });
  } catch (error) {
    if (!isRetryableFsError(error)) throw error;
    try {
      await io.rename(from, `${from}.removed-${Date.now()}`);
    } catch {
      /* trash copy is enough for a recoverable delete */
    }
  }
  if (lastError && !isRetryableFsError(lastError)) throw lastError;
}
