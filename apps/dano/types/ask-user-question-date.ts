import { format, isMatch, parse } from "date-fns";

const FORMAT_REFERENCE_DATE = new Date(2026, 6, 3, 9, 30, 0, 0);
const DATE_PARTS = [/y/, /M/, /d/];
const TIME_PARTS = [/H/, /m/];
const AMBIGUOUS_HOUR_PARTS = /[hKk]/;
const OUT_OF_SCOPE_TIME_PARTS = /[sSaXxOzZ]/;

export function validateAskUserQuestionDateFormat(value: unknown): string | null {
  if (typeof value !== "string" || !value.trim()) {
    return "dateFormat is required for inputType:\"date\" and must be a non-empty string such as \"yyyy-MM-dd\" or \"yyyy-MM-dd HH:mm\".";
  }
  const formatString = value.trim();
  if (!DATE_PARTS.every(part => part.test(formatString))) {
    return "dateFormat must include year, month, and day tokens, for example \"yyyy-MM-dd\".";
  }
  const hasTime = TIME_PARTS.some(part => part.test(formatString));
  if (AMBIGUOUS_HOUR_PARTS.test(formatString)) {
    return "dateFormat time formats must use 24-hour H/HH tokens; 12-hour h/K/k tokens are not supported.";
  }
  if (hasTime && !TIME_PARTS.every(part => part.test(formatString))) {
    return "dateFormat time formats must use 24-hour hour and minute tokens, for example \"yyyy-MM-dd HH:mm\".";
  }
  if (OUT_OF_SCOPE_TIME_PARTS.test(formatString)) {
    return "dateFormat supports date-only or date-time-to-minute formats; seconds and time zones are not supported.";
  }
  try {
    format(FORMAT_REFERENCE_DATE, formatString);
    return null;
  } catch (cause) {
    const message = cause instanceof Error ? cause.message : String(cause);
    return `dateFormat is not supported: ${message}`;
  }
}

export function isAskUserQuestionDateTimeFormat(formatString: string): boolean {
  return TIME_PARTS.every(part => part.test(formatString));
}

export function parseAskUserQuestionDateValue(
  value: string,
  formatString: string,
): Date | null {
  if (!isMatch(value, formatString)) return null;
  const parsed = parse(value, formatString, FORMAT_REFERENCE_DATE);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

export function formatAskUserQuestionDateValue(
  value: Date,
  formatString: string,
): string {
  return format(value, formatString);
}
