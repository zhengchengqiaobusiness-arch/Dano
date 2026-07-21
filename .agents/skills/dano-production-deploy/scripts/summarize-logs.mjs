#!/usr/bin/env node
import { createInterface } from "node:readline";

const sinceText = process.env.DANO_DIAGNOSTIC_SINCE;
const sinceMs = Date.parse(sinceText || "");
if (!sinceText || Number.isNaN(sinceMs)) {
  throw new Error("DANO_DIAGNOSTIC_SINCE must be a valid RFC3339 timestamp");
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
  http5xx: /\b(?:http(?:\/\d(?:\.\d)?)?\s*)?5\d\d\b/i,
  staticAssets: /(?:\/assets\/|\.(?:js|css))(?:\?|\s|$)/i,
};
const timestampPattern =
  /\b(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))\b/;
const servicePattern = /^([a-zA-Z0-9_.-]+)\s+\|\s+/;

function emptyCategories() {
  return Object.fromEntries(Object.keys(patterns).map(name => [name, 0]));
}

const summary = {
  windowStart: new Date(sinceMs).toISOString(),
  totalLines: 0,
  beforeWindowLines: 0,
  analyzedLines: 0,
  unscopedLines: 0,
  truncated: false,
  emptyWindow: false,
  categories: emptyCategories(),
  services: {},
};

const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });

for await (const line of lines) {
  summary.totalLines += 1;
  if (!line.trim()) continue;

  const timestampMatch = line.match(timestampPattern);
  const serviceMatch = line.match(servicePattern);
  if (!timestampMatch || !serviceMatch) {
    summary.unscopedLines += 1;
    continue;
  }

  const timestampMs = Date.parse(timestampMatch[1]);
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

  const service = serviceMatch[1];
  summary.analyzedLines += 1;
  summary.services[service] ||= {
    analyzedLines: 0,
    categories: emptyCategories(),
  };
  summary.services[service].analyzedLines += 1;

  for (const [name, pattern] of Object.entries(patterns)) {
    if (!pattern.test(line)) continue;
    summary.categories[name] += 1;
    summary.services[service].categories[name] += 1;
  }
}

summary.emptyWindow = summary.analyzedLines === 0;
process.stdout.write(`${JSON.stringify(summary, null, 2)}\n`);

if (summary.unscopedLines > 0 || summary.truncated) process.exitCode = 2;
