import type { RpcResponse } from "@dano/types/protocol";

export const ACTIVE_SESSION_CACHE_KEY = "dano.activeSessionPath";

export function readActiveSessionCache(storage: Storage): string | null {
  return storage.getItem(ACTIVE_SESSION_CACHE_KEY);
}

export function writeActiveSessionCache(
  storage: Storage,
  sessionPath: string | null,
) {
  if (sessionPath) {
    storage.setItem(ACTIVE_SESSION_CACHE_KEY, sessionPath);
  } else {
    storage.removeItem(ACTIVE_SESSION_CACHE_KEY);
  }
}

export function createExplicitNewSessionAction(
  createSession: () => Promise<RpcResponse>,
  reportError: (error: unknown) => void,
): () => Promise<RpcResponse> {
  let pending: Promise<RpcResponse> | null = null;

  return () => {
    if (pending) return pending;

    pending = createSession()
      .then(result => {
        if (!result.success) {
          reportError(result.error);
        }
        return result;
      })
      .catch(error => {
        reportError(error);
        throw error;
      })
      .finally(() => {
        pending = null;
      });

    return pending;
  };
}
