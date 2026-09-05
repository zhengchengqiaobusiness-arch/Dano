import { readFileSync } from "node:fs";
const contract = JSON.parse(readFileSync("dist/skills/oa-travel-reimburse-sk_mtoo747l_0a5943d9/references/CONTRACT.json", "utf8").replace(/^\uFEFF/, ""));
console.log("caps", contract.capabilities.map(item => `${item.role}\t${item.id}\t${item.title}\t${item.transport.pathTemplate}`).join("\n"));
for (const cap of contract.capabilities.filter(item => item.role === "primary")) {
  console.log("\n==", cap.id);
  for (const field of cap.inputForm || []) {
    console.log(`  ${field.source}\t${field.name}\t${field.label}\t${field.defaultRule || ""}\t${(field.sourceDetail || "").slice(0, 56)}`);
  }
  if (cap.bindings?.length) console.log("bindings", JSON.stringify(cap.bindings, null, 2));
}
