const writers = new Set();

export function logStamp() {
  const date = new Date();
  const part = (value, width = 2) => String(value).padStart(width, "0");
  return `${part(date.getHours())}:${part(date.getMinutes())}:${part(date.getSeconds())}.${part(date.getMilliseconds(), 3)}`;
}

export function addLogWriter(write) {
  writers.add(write);
  return () => writers.delete(write);
}

export function logLine(message) {
  const line = `${logStamp()} ${message}`;
  console.log(line);
  for (const write of writers) {
    try {
      write(line);
    } catch {
      // A recording log sink must not break console output.
    }
  }
}
