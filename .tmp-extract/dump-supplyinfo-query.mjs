import { readFileSync } from "node:fs";
const events = readFileSync(".business-skill-studio/recordings/rec_mtom949t_f8baee12/events.jsonl", "utf8")
  .split(/\n/).filter(Boolean).map(line => JSON.parse(line));
const pages = events.filter(e => e.kind === "network" && /\/oa\/supply\/page/.test(e.request?.url || ""));
for (const event of pages) {
  const url = new URL(event.request.url);
  console.log(event.at, [...url.searchParams.entries()].map(([k, v]) => `${k}=${v}`).join("&"));
}
const pageSnaps = events.filter(e => e.kind === "ui" && e.eventType === "snapshot" && (e.scope === "page" || !e.scope));
console.log("\nPAGE SNAPS", pageSnaps.length);
for (const snap of pageSnaps.slice(0, 3)) {
  const form = snap.form || snap.formFields || [];
  console.log(snap.at, "form", form.map(f => `${f.label}|${f.name}|${f.scope || ""}|${f.value || ""}`).join(" ; "));
}
const radios = events.filter(e => e.kind === "ui" && /状态/.test(`${e.label || ""}${e.text || ""}`));
console.log("\nSTATUS UI", radios.slice(0, 20).map(e => `${e.at} ${e.eventType} ${e.scope} ${e.label} ${e.text} ${e.value}`).join("\n"));
