import { readFileSync } from "node:fs";

const events = readFileSync(".business-skill-studio/recordings/rec_mtolq7b6_afd90cd7/events.jsonl", "utf8")
  .split(/\n/).filter(Boolean).map(line => JSON.parse(line));

const list = events.find(e => e.kind === "network" && e.request.url.includes("/oa/supply/page"));
const rows = list?.response?.body?.data?.list || list?.response?.body?.data;
const first = Array.isArray(rows) ? rows[0] : undefined;
console.log(JSON.stringify({
  url: list?.request.url,
  keys: first && Object.keys(first),
  first,
  printer: Array.isArray(rows) ? rows.find(r => JSON.stringify(r).includes("打印机")) : undefined,
  total: Array.isArray(rows) ? rows.length : typeof rows
}, null, 2));
