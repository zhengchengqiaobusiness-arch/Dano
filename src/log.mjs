export function logStamp() {
  const date = new Date();
  const part = (value, width = 2) => String(value).padStart(width, "0");
  return `${part(date.getHours())}:${part(date.getMinutes())}:${part(date.getSeconds())}.${part(date.getMilliseconds(), 3)}`;
}

export function logLine(message) {
  console.log(`${logStamp()} ${message}`);
}
