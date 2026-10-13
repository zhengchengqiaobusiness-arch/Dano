export const AUTH_MAX_FAILURES = 5;
export const AUTH_LOCK_MAX_MINUTES = 30;

export const CLEARED = Object.freeze({ fail_count: 0, locked_until: 0 });

export function isLocked(state, now = Date.now() / 1000) {
  return Number(state?.locked_until || 0) > now;
}

export function registerFailure(state, {
  now = Date.now() / 1000,
  maxFailures = AUTH_MAX_FAILURES,
  lockMaxMinutes = AUTH_LOCK_MAX_MINUTES,
} = {}) {
  const failCount = Number(state?.fail_count || 0) + 1;
  if (failCount < maxFailures) {
    return { fail_count: failCount, locked_until: Number(state?.locked_until || 0) };
  }
  const exponent = Math.min(failCount - maxFailures, 16);
  const minutes = Math.min(2 ** exponent, lockMaxMinutes);
  return { fail_count: failCount, locked_until: now + minutes * 60 };
}
