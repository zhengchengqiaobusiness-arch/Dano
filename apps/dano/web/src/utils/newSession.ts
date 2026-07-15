export const ACTIVE_SESSION_CACHE_KEY = "dano.activeSessionPath";

type NewSessionResult = {
  success: boolean;
  error?: string;
};

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

export function createSingleFlightNewSession<Result extends NewSessionResult>(
  createSession: (workspacePath?: string) => Promise<Result>,
  reportError: (message: string) => void,
  fallbackError: () => string,
): (workspacePath?: string) => Promise<Result> {
  let pending: Promise<Result> | null = null;

  return (workspacePath?: string) => {
    if (pending) return pending;

    pending = createSession(workspacePath)
      .then(result => {
        if (!result.success) {
          reportError(result.error?.trim() || fallbackError());
        }
        return result;
      })
      .catch(error => {
        reportError(
          error instanceof Error && error.message.trim()
            ? error.message
            : fallbackError(),
        );
        throw error;
      })
      .finally(() => {
        pending = null;
      });

    return pending;
  };
}
