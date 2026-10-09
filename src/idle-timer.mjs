export const HUMAN_STEER_IDLE_MS = 2500;
export const PREVIEW_DETACH_MS = 600000;

export function armIdleTimer(holder, key, waitMs, fire) {
  clearTimeout(holder[key]);
  const timer = setTimeout(() => {
    holder[key] = null;
    fire();
  }, waitMs);
  if (typeof timer.unref === "function") timer.unref();
  holder[key] = timer;
}
