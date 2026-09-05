/**
 * 从已采到的 get-permission-info 响应里抽出叶子页。不推断能力。
 */

import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const blobPath = process.argv[2];
const origin = process.argv[3] || "";
if (!blobPath) {
  console.error("usage: node scripts/extract-menus-from-blob.mjs <blob.bin> [origin]");
  process.exit(1);
}

function walkMenus(nodes, trail = []) {
  const pages = [];
  for (const node of Array.isArray(nodes) ? nodes : []) {
    const name = String(node.name || node.title || node.meta?.title || "").trim();
    const pathValue = String(node.path || node.component || node.url || "").trim();
    const visible = node.visible !== false && node.hidden !== true;
    const nextTrail = name ? [...trail, name] : trail;
    const children = node.children || [];
    if (children.length) {
      pages.push(...walkMenus(children, nextTrail));
      continue;
    }
    if (!visible || !nextTrail.length) continue;
    pages.push({ text: nextTrail.join(" / "), path: pathValue, origin });
  }
  return pages;
}

const raw = await readFile(blobPath);
const text = raw.toString("utf8");
const json = JSON.parse(text);
console.log(`code=${json.code} keys=${Object.keys(json.data || json).join(",")}`);
const menus = json.data?.menus || json.data || [];
const pages = walkMenus(menus);
console.log(`pages=${pages.length}`);
for (const page of pages) {
  console.log(`- ${page.text} -> ${page.path}`);
}
const out = path.join(ROOT, "data", "menus-from-blob.json");
await writeFile(out, `${JSON.stringify({ origin, pageCount: pages.length, pages }, null, 2)}\n`);
console.log(`wrote ${out}`);
