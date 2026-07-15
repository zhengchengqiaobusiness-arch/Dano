export interface OperationScheduler<Handle = unknown> {
  now(): number;
  setTimeout(callback: () => void, delay: number): Handle;
  clearTimeout(handle: Handle): void;
}

interface ArmedDeadline<Handle> {
  deadline: number;
  handle: Handle;
  token: symbol;
}

/**
 * Small deterministic deadline manager shared by recording operations.
 * Re-arming replaces the previous deadline and stale timer callbacks cannot
 * expire a newer attempt.
 */
export class OperationWatchdog<Operation extends string = string, Handle = unknown> {
  private readonly armed = new Map<Operation, ArmedDeadline<Handle>>();
  private readonly scheduler: OperationScheduler<Handle>;
  private readonly onTimeout: (operation: Operation) => void;

  constructor(
    scheduler: OperationScheduler<Handle>,
    onTimeout: (operation: Operation) => void,
  ) {
    this.scheduler = scheduler;
    this.onTimeout = onTimeout;
  }

  arm(operation: Operation, timeout: number): void {
    if (!Number.isFinite(timeout) || timeout <= 0) {
      throw new Error("operation timeout must be a positive finite number");
    }
    this.clear(operation);
    const token = Symbol(operation);
    const deadline = this.scheduler.now() + timeout;
    const handle = this.scheduler.setTimeout(() => {
      const current = this.armed.get(operation);
      if (!current || current.token !== token) return;
      this.armed.delete(operation);
      this.onTimeout(operation);
    }, timeout);
    this.armed.set(operation, { deadline, handle, token });
  }

  clear(operation: Operation): void {
    const current = this.armed.get(operation);
    if (!current) return;
    this.armed.delete(operation);
    this.scheduler.clearTimeout(current.handle);
  }

  clearAll(): void {
    for (const operation of [...this.armed.keys()]) this.clear(operation);
  }

  isArmed(operation: Operation): boolean {
    return this.armed.has(operation);
  }

  remaining(operation: Operation): number | undefined {
    const current = this.armed.get(operation);
    return current ? Math.max(0, current.deadline - this.scheduler.now()) : undefined;
  }
}

export const browserOperationScheduler: OperationScheduler<number> = {
  now: () => Date.now(),
  setTimeout: (callback, delay) => window.setTimeout(callback, delay),
  clearTimeout: (handle) => window.clearTimeout(handle),
};
