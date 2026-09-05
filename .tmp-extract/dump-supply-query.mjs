import { readFileSync } from "node:fs";
const catalog = JSON.parse(readFileSync(".business-skill-studio/catalog/capabilities.json", "utf8"));
const hits = catalog.filter(item =>
  /\/oa\/supply/.test(item.transport?.pathTemplate || "") || /supply/.test(item.id)
);
console.log(hits.map(item => ({
  id: item.id,
  op: item.operation,
  title: item.title,
  path: item.transport?.pathTemplate,
  status: item.validation?.status,
  failed: (item.validation?.checks || []).filter(c => !c.ok).map(c => `${c.name}:${c.detail}`)
})));
