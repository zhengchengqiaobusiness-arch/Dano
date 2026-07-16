export interface NativeDateInputParts {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
}

function twoDigits(value: number): string {
  return String(value).padStart(2, "0");
}

export function formatNativeDateInputValue(
  parts: NativeDateInputParts | undefined,
  includesTime: boolean,
): string {
  if (!parts) return "";
  const date = `${parts.year}-${twoDigits(parts.month)}-${twoDigits(parts.day)}`;
  return includesTime
    ? `${date}T${twoDigits(parts.hour)}:${twoDigits(parts.minute)}`
    : date;
}

export function parseNativeDateInputValue(
  value: string,
  includesTime: boolean,
): NativeDateInputParts | undefined {
  const match = includesTime
    ? value.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/)
    : value.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!match) return undefined;

  const [, yearText, monthText, dayText, hourText = "0", minuteText = "0"] = match;
  const parts = {
    year: Number(yearText),
    month: Number(monthText),
    day: Number(dayText),
    hour: Number(hourText),
    minute: Number(minuteText),
  };
  const date = new Date(parts.year, parts.month - 1, parts.day);
  if (
    date.getFullYear() !== parts.year ||
    date.getMonth() + 1 !== parts.month ||
    date.getDate() !== parts.day ||
    parts.hour < 0 ||
    parts.hour > 23 ||
    parts.minute < 0 ||
    parts.minute > 59
  ) {
    return undefined;
  }
  return parts;
}
