import { readFileSync, writeFileSync } from "node:fs";

const file = ".business-skill-studio/catalog/capabilities.json";
const caps = JSON.parse(readFileSync(file, "utf8").replace(/^\uFEFF/, ""));
const next = caps.filter(item => !String(item.transport?.pathTemplate || "").includes("/oa/car/"));
writeFileSync(file, `${JSON.stringify(next, null, 2)}\n`);
console.log(`removed ${caps.length - next.length}, kept ${next.length}`);
