import { readFileSync } from "node:fs";
const caps = JSON.parse(readFileSync(".business-skill-studio/catalog/capabilities.json", "utf8").replace(/^\uFEFF/, ""));
for (const id of ["query-get-supply-page", "create-post-supply-create", "query-get-dept-company-list"]) {
  const cap = caps.find(item => item.id === id);
  console.log("\n==", id, cap?.title);
  for (const field of cap?.inputForm || []) {
    console.log(`  ${field.source}\t${field.name}\t${field.label}\t${field.widget || ""}\t${field.defaultRule || ""}\t${(field.sourceDetail || "").slice(0, 40)}`);
  }
}
