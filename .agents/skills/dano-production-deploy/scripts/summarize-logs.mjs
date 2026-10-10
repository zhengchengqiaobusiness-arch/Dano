#!/usr/bin/env node
import { createInterface } from "node:readline";

const timestampSource = "\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d+)?(?:Z|[+-]\\d{2}:\\d{2})";
const exactTimestamp = new RegExp(`^${timestampSource}$`);
function timestamp(value) {
  return typeof value === "string" && exactTimestamp.test(value) ? Date.parse(value) : NaN;
}
function invalidWindow() {
  console.error(JSON.stringify({ error: "INVALID_DIAGNOSTIC_WINDOW" }));
  process.exit(2);
}
const sinceText = process.env.DANO_DIAGNOSTIC_SINCE;
const sinceMs = timestamp(sinceText);
if (!sinceText || Number.isNaN(sinceMs)) {
  invalidWindow();
}

const readinessText = process.env.DANO_DIAGNOSTIC_READINESS;
const readinessMs = readinessText ? timestamp(readinessText) : undefined;
if (readinessText && (!Number.isFinite(readinessMs) || readinessMs < sinceMs)) {
  invalidWindow();
}

const configuredLimit = Number.parseInt(
  process.env.DANO_DIAGNOSTIC_MAX_LINES || "5000",
  10,
);
const maxLines =
  Number.isSafeInteger(configuredLimit) && configuredLimit > 0
    ? configuredLimit
    : 5000;

const patterns = {
  errors: /\b(error|exception|fatal|panic|failed|failure)\b/i,
  warnings: /\bwarn(?:ing)?\b/i,
  timeouts: /\b(time(?:d|out)|deadline exceeded|abort(?:ed)?)\b/i,
  health: /\b(healthcheck|unhealthy|health check)\b/i,
  permissions: /\b(eacces|eperm|permission denied|operation not permitted)\b/i,
  sandbox: /\b(bwrap|bubblewrap|heimdall|sandbox)\b/i,
  staticAssets: /(?:\/assets\/|\.(?:js|css))(?:\?|\s|$)/i,
};
const scopedLine = new RegExp(`^([a-zA-Z0-9_.-]+)\\s+\\|\\s+(${timestampSource})(?:\\s+(.*)|$)`);

function emptyAggregate() {
  return { analyzedLines: 0, categories: emptyCategories(), reasons: {},
    unclassified: { errors: 0, warnings: 0, timeouts: 0 } };
}
function windows() {
  return readinessMs === undefined ? null : {
    beforeReadiness: emptyAggregate(), afterReadiness: emptyAggregate(),
  };
}
function record(target, categories, reason) {
  target.analyzedLines += 1;
  for (const category of categories) target.categories[category] += 1;
  if (reason) target.reasons[reason] = (target.reasons[reason] || 0) + 1;
  const explained = {
    upstream_connection_refused_before_ready: "errors",
    nginx_temporary_buffer: "warnings",
  }[reason];
  for (const category of ["errors", "warnings", "timeouts"]) {
    if (categories.includes(category) && category !== explained) target.unclassified[category] += 1;
  }
}

function emptyCategories() {
  return { ...Object.fromEntries(Object.keys(patterns).map(name => [name, 0])), http5xx: 0 };
}

// nginx's combined access format: the status follows the complete request.
// Byte counts, resource names and arbitrary application numbers are not status.
function httpStatus(line) {
  const match = line.match(/^\S+ - \S+ \[[^\]]+\] "[A-Z]+ [^"]* HTTP\/\d(?:\.\d)?" ([1-5]\d\d) (?:\d+|-)(?:\s|$)/);
  return match ? Number(match[1]) : undefined;
}

function structuredLog(body) {
  try {
    const value = JSON.parse(body);
    return value && typeof value === "object" && !Array.isArray(value) ? value : undefined;
  } catch { return undefined; }
}

const summary = {
  windowStart: new Date(sinceMs).toISOString(),
  totalLines: 0,
  beforeWindowLines: 0,
  ...emptyAggregate(),
  readinessStart: readinessText ? new Date(readinessMs).toISOString() : null,
  windows: windows(),
  unscopedLines: 0,
  truncated: false,
  emptyWindow: false,
  services: Object.create(null),
};

const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });

for await (const line of lines) {
  summary.totalLines += 1;
  if (!line.trim()) continue;

  const scoped = line.match(scopedLine);
  if (!scoped) {
    summary.unscopedLines += 1;
    continue;
  }

  const timestampMs = timestamp(scoped[2]);
  if (Number.isNaN(timestampMs)) {
    summary.unscopedLines += 1;
    continue;
  }
  if (timestampMs < sinceMs) {
    summary.beforeWindowLines += 1;
    continue;
  }
  if (summary.analyzedLines >= maxLines) {
    summary.truncated = true;
    continue;
  }

  const service = scoped[1];
  const body = (scoped[3] || "").trim();
  const structured = structuredLog(body);
  const declaredStatus = structured?.statusCode ?? structured?.status ?? structured?.response?.statusCode;
  const status = structured
    ? (Number.isInteger(declaredStatus) && declaredStatus >= 100 && declaredStatus < 600 ? declaredStatus : undefined)
    : httpStatus(body);
  const severity = structured?.level;
  const text = structured ? [structured.message, structured.msg, structured.code]
    .filter(value => typeof value === "string").join(" ") : body;
  let categories = Object.entries(patterns)
    .filter(([name, pattern]) => {
      if (structured && ["trace", "debug", "info", "warn", "warning", "error", "fatal", 10, 20, 30, 40, 50, 60].includes(severity)) {
        if (name === "errors") return ["error", "fatal", 50, 60].includes(severity);
        if (name === "warnings") return ["warn", "warning", 40].includes(severity);
      }
      return (structured || status === undefined || name === "staticAssets") && pattern.test(text);
    })
    .map(([name]) => name);
  if (status >= 500) categories.push("http5xx");
  const beforeReady = readinessMs !== undefined && timestampMs < readinessMs;
  let reason = beforeReady && categories.includes("errors") &&
    /connect\(\) failed \(111: Connection refused\) while connecting to upstream/.test(body)
    ? "upstream_connection_refused_before_ready" : undefined;
  if (/^(?:commandTimeout|timeout)\s*:\s*\d+\s*,?$/.test(body)) {
    categories = categories.filter(name => name !== "timeouts");
    reason = "timeout_configuration";
  } else if (categories.includes("warnings") &&
    /an upstream response is buffered to a temporary file/.test(body)) {
    reason = "nginx_temporary_buffer";
  }
  summary.services[service] ||= { ...emptyAggregate(), windows: windows() };
  for (const target of [summary, summary.services[service]]) {
    record(target, categories, reason);
    if (target.windows) record(target.windows[beforeReady ? "beforeReadiness" : "afterReadiness"], categories, reason);
  }
}

summary.emptyWindow = summary.analyzedLines === 0;
process.stdout.write(`${JSON.stringify(summary, null, 2)}\n`);

if (summary.unscopedLines > 0 || summary.truncated) process.exitCode = 2;
