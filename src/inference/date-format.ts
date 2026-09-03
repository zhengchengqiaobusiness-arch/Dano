export const BUSINESS_TZ_OFFSET_MS = 8 * 60 * 60 * 1000;
const DATE_ONLY = /^(\d{4}-\d{2}-\d{2})$/;
const DATE_TIME = /^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})/;

function pad(value: number) {
  return String(value).padStart(2, "0");
}

export function isDateInput(value: unknown): value is string {
  return typeof value === "string" && /^\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}:\d{2})?$/.test(value.trim());
}

export function recordedClock(value: unknown) {
  const match = String(value ?? "").trim().replace("T", " ").match(/^\d{4}-\d{2}-\d{2} (\d{2}:\d{2}:\d{2})/);
  return match?.[1];
}

export function dateToMillis(value: string) {
  const raw = value.trim().replace("T", " ");
  const withClock = raw.length === 10 ? `${raw} 00:00:00` : raw.slice(0, 19);
  const [day, clock = "00:00:00"] = withClock.split(" ");
  const [year, month, date] = day.split("-").map(Number);
  const [hour, minute, second] = clock.split(":").map(Number);
  return Date.UTC(year, (month || 1) - 1, date || 1, hour || 0, minute || 0, second || 0) - BUSINESS_TZ_OFFSET_MS;
}

export function normalizeDateString(value: string, clock?: string) {
  const raw = value.trim().replace("T", " ");
  if (DATE_ONLY.test(raw)) {
    const suffix = clock && /^\d{2}:\d{2}:\d{2}$/.test(clock) ? clock : "00:00:00";
    return `${raw} ${suffix}`;
  }
  return value;
}

export function dateDay(value: unknown) {
  if (typeof value === "string") {
    const match = value.trim().match(DATE_TIME) || value.trim().match(DATE_ONLY);
    return match?.[1];
  }
  if (typeof value === "number" && Number.isFinite(value) && value > 10_000_000_000) {
    const shifted = new Date(value + BUSINESS_TZ_OFFSET_MS);
    if (Number.isNaN(shifted.getTime())) return undefined;
    return `${shifted.getUTCFullYear()}-${pad(shifted.getUTCMonth() + 1)}-${pad(shifted.getUTCDate())}`;
  }
  return undefined;
}

export function clockFromEpoch(value: unknown) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 10_000_000_000) return undefined;
  const shifted = new Date(value + BUSINESS_TZ_OFFSET_MS);
  if (Number.isNaN(shifted.getTime())) return undefined;
  return `${pad(shifted.getUTCHours())}:${pad(shifted.getUTCMinutes())}:${pad(shifted.getUTCSeconds())}`;
}
