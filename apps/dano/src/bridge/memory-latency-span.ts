import { AsyncLocalStorage } from "node:async_hooks";

type Span = { stage: string; startedAt: string; durationMs: number; outcome: "ok" | "error" };
const scope = new AsyncLocalStorage<(span: Span) => void>();

/** Only the explicitly armed extension handler installs an observer. Arguments,
 * results and error messages never enter diagnostics. */
export function withMemoryLatencyObserver<T>(observe: (span: Span) => void, work: () => T): T {
  return scope.run(observe, work);
}

export async function measureMemoryStage<T>(stage: string, work: () => T | Promise<T>): Promise<T> {
  const observe = scope.getStore();
  if (!observe) return work();
  const start = performance.now();
  let outcome: Span["outcome"] = "ok";
  try { return await work(); }
  catch (error) { outcome = "error"; throw error; }
  finally {
    try { observe({ stage, startedAt: new Date(performance.timeOrigin + start).toISOString(), durationMs: performance.now() - start, outcome }); }
    catch { /* Diagnostic observers cannot affect execution. */ }
  }
}

/** Bind to the original receiver so private fields and method semantics survive. */
export function measureMemoryMethods<T extends object>(target: T, stages: Partial<Record<keyof T, string>>): T {
  return new Proxy(target, {
    get(object, key) {
      const value: unknown = Reflect.get(object, key, object);
      if (typeof value !== "function") return value;
      const stage = stages[key as keyof T];
      return stage ? (...args: unknown[]) => measureMemoryStage(stage, () => Reflect.apply(value, object, args))
        : value.bind(object);
    },
  });
}
