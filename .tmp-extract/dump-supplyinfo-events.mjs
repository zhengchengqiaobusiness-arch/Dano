import { readFileSync } from "node:fs";
const events = readFileSync(".business-skill-studio/recordings/rec_mtom949t_f8baee12/events.jsonl", "utf8")
  .split(/\n/).filter(Boolean).map(line => JSON.parse(line));
const counts = new Map();
for (const event of events) {
  const key = event.kind === "ui"
    ? `ui ${event.eventType} ${event.scope || ""} ${event.label || event.text || ""}`
    : `${event.request.method} ${event.request.url.replace("https://ruoyioffice.com", "").split("?")[0]}`;
  counts.set(key, (counts.get(key) || 0) + 1);
}
console.log([...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 25).map(([k, n]) => `${n}\t${k}`).join("\n"));
