import { readFileSync } from "node:fs";

const events = readFileSync(".business-skill-studio/recordings/rec_mtolq7b6_afd90cd7/events.jsonl", "utf8")
  .split(/\n/).filter(Boolean).map(line => JSON.parse(line));

for (const event of events) {
  if (event.kind === "ui") {
    const formLabels = (event.form || []).map(f => `${f.label}=${f.value}`).join("; ");
    console.log([
      event.at.slice(11, 19),
      event.eventType,
      event.scope || "",
      event.label || event.text || "",
      event.selector || "",
      formLabels
    ].join(" | "));
  } else if (event.kind === "network") {
    const url = event.request.url.replace("https://ruoyioffice.com", "");
    if (/supply/.test(url)) {
      const body = event.request.body;
      const items = body?.items;
      console.log([
        event.at.slice(11, 19),
        event.request.method,
        url.split("?")[0],
        items ? JSON.stringify(items) : ""
      ].join(" | "));
    }
  }
}
