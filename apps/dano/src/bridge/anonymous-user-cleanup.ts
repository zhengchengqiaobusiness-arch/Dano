import type {
  AnonymousUserContextResolver,
  AnonymousUserSweepResult,
} from "./anonymous-user-context.js";
import type { UserContext } from "./user-context.js";

export interface AnonymousUserCleanupOptions {
  readonly idleTtlMs: number;
  readonly intervalMs: number;
  readonly beginCleanup: (userId: string) => (() => void) | null;
  readonly cleanupUser: (userContext: UserContext) => Promise<void>;
  readonly retainUser?: (userContext: UserContext) => boolean;
  readonly onError?: (error: unknown) => void;
}

export class AnonymousUserCleanup {
  private interval: ReturnType<typeof setInterval> | undefined;
  private runningSweep: Promise<AnonymousUserSweepResult> | undefined;

  constructor(
    private readonly anonymousUsers: AnonymousUserContextResolver,
    private readonly options: AnonymousUserCleanupOptions,
  ) {
    assertPositiveInteger(options.idleTtlMs, "Anonymous User idle TTL");
    assertPositiveInteger(options.intervalMs, "Anonymous User cleanup interval");
  }

  async start(): Promise<void> {
    if (this.interval) return;
    await this.runSweep();
    this.interval = setInterval(() => {
      void this.runSweep();
    }, this.options.intervalMs);
    this.interval.unref?.();
  }

  sweep(): Promise<AnonymousUserSweepResult> {
    return this.runSweep();
  }

  dispose(): void {
    if (!this.interval) return;
    clearInterval(this.interval);
    this.interval = undefined;
  }

  private runSweep(): Promise<AnonymousUserSweepResult> {
    if (this.runningSweep) return this.runningSweep;
    const sweep = this.anonymousUsers
      .sweepExpired({
        idleTtlMs: this.options.idleTtlMs,
        beginCleanup: this.options.beginCleanup,
        cleanupUser: this.options.cleanupUser,
        retainUser: this.options.retainUser,
      })
      .then(result => {
        if (result.failed > 0) {
          this.options.onError?.(
            new Error(
              `${result.failed} Anonymous User cleanup operation(s) failed`,
            ),
          );
        }
        return result;
      })
      .catch(error => {
        this.options.onError?.(error);
        return { removed: 0, skipped: 0, failed: 1 };
      })
      .finally(() => {
        if (this.runningSweep === sweep) this.runningSweep = undefined;
      });
    this.runningSweep = sweep;
    return sweep;
  }
}

function assertPositiveInteger(value: number, name: string): void {
  if (!Number.isSafeInteger(value) || value <= 0) {
    throw new Error(`${name} must be a positive integer`);
  }
}
