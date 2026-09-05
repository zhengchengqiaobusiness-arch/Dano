import { readFileSync } from "node:fs";
const catalog = JSON.parse(readFileSync(".business-skill-studio/catalog/capabilities.json", "utf8"));
const target = catalog.find(item => item.id === "create-post-supply-apply-bill-submit");
console.log(JSON.stringify({
  title: target.title,
  status: target.validation.status,
  checks: target.validation.checks.filter(c => !c.ok),
  fields: target.inputForm.map(f => ({
    path: f.path, name: f.name, label: f.label, source: f.source, widget: f.widget,
    defaultRule: f.defaultRule, candidates: f.candidates?.type, detail: f.sourceDetail
  })),
  bindings: target.bindings
}, null, 2));
