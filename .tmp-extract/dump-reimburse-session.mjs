import { readFileSync, existsSync } from "node:fs";

function summarize(id) {
  const dir = `.business-skill-studio/recordings/${id}`;
  const session = JSON.parse(readFileSync(`${dir}/session.json`, "utf8").replace(/^\uFEFF/, ""));
  const events = readFileSync(`${dir}/events.jsonl`, "utf8").trim().split("\n").filter(Boolean).map(line => JSON.parse(line));
  const nets = events.filter(event => event.kind === "network");
  const writes = nets.filter(event => /submit|create|save|update/i.test(event.request?.url || "") && event.request?.method !== "GET");
  const pages = [...new Set(events.map(event => event.pageUrl).filter(Boolean))];
  console.log("\n==", id);
  console.log("startUrl", session.startUrl);
  console.log("expected", session.expectedOperations, "coverage", session.completeFieldCoverage);
  console.log("events", events.length, "network", nets.length);
  console.log("pages", pages);
  for (const event of writes) {
    const code = event.response?.body?.code;
    console.log("WRITE", event.request.method, event.request.url, "http", event.response?.status, "code", code);
    if (event.request.body && typeof event.request.body === "object") {
      console.log("  keys", Object.keys(event.request.body));
    }
  }
  const queries = nets.filter(event => /\/page(?:\?|$)/i.test(event.request?.url || "") && event.request?.method === "GET");
  for (const event of queries.slice(0, 8)) {
    console.log("QUERY", event.request.url.split("?")[0], "code", event.response?.body?.code);
  }
  const lastUi = [...events].reverse().find(event => event.kind === "ui");
  console.log("lastUi", lastUi?.label || lastUi?.text, lastUi?.pageUrl);
}

for (const id of process.argv.slice(2)) summarize(id);
