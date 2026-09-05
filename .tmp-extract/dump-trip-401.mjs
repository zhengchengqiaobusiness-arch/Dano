import { readFileSync } from "node:fs";
const events = readFileSync(".business-skill-studio/recordings/rec_mtonb81e_1a5076f8/events.jsonl", "utf8")
  .split(/\n/).filter(Boolean).map(line => JSON.parse(line));
const nets = events.filter(e => e.kind === "network");
console.log("events", events.length, "nets", nets.length);
for (const event of nets) {
  const url = String(event.request?.url || "").replace("https://ruoyioffice.com", "").split("?")[0];
  const code = event.response?.body?.code;
  console.log(event.at.slice(11, 19), event.request.method, event.response?.status, code, url);
}
