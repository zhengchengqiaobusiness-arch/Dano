/**
 * 从 get-permission-info / getRouters 拼出可打开的叶子路由。不推断能力。
 */

import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const blobPath = process.argv[2];
const origin = process.argv[3] || "";
const mode = process.argv[4] || "yudao";
if (!blobPath) {
  console.error("usage: node scripts/extract-full-routes.mjs <json-or-bin> <origin> [yudao|ruoyi]");
  process.exit(1);
}

function joinPath(parent, child) {
  const left = String(parent || "").trim();
  const right = String(child || "").trim();
  if (!right) return left;
  if (/^https?:\/\//i.test(right) || right.startsWith("/")) return right;
  if (!left) return `/${right}`.replace(/\/+/g, "/");
  return `${left.replace(/\/$/, "")}/${right}`.replace(/\/+/g, "/");
}

function queryString(node) {
  const raw = node.query || node.meta?.query || "";
  if (!raw) return "";
  if (typeof raw === "object") {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(raw)) params.set(key, String(value));
    const text = params.toString();
    return text ? `?${text}` : "";
  }
  const text = String(raw).trim();
  if (!text) return "";
  try {
    const parsed = JSON.parse(text);
    if (parsed && typeof parsed === "object") {
      const params = new URLSearchParams();
      for (const [key, value] of Object.entries(parsed)) params.set(key, String(value));
      const qs = params.toString();
      return qs ? `?${qs}` : "";
    }
  } catch {
    /* 已是 querystring */
  }
  return text.startsWith("?") ? text : `?${text}`;
}

function walk(nodes, parentPath = "", trail = []) {
  const pages = [];
  for (const node of Array.isArray(nodes) ? nodes : []) {
    const name = String(node.meta?.title || node.name || node.title || node.menuName || "").trim();
    const nodePath = String(node.path || node.component || node.url || "").trim();
    const fullPath = joinPath(parentPath, nodePath);
    const nextTrail = name ? [...trail, name] : trail;
    const children = node.children || [];
    const hidden = node.hidden === true || node.visible === false;
    if (children.length) {
      pages.push(...walk(children, fullPath, nextTrail));
      continue;
    }
    if (hidden || !nextTrail.length) continue;
    const qs = queryString(node);
    const pathWithQs = `${fullPath}${qs}`;
    pages.push({
      text: nextTrail.join(" / "),
      route: pathWithQs,
      url: origin
        ? `${origin.replace(/\/$/, "")}${pathWithQs.startsWith("/") ? pathWithQs : `/${pathWithQs}`}`
        : pathWithQs,
    });
  }
  return pages;
}

const raw = await readFile(blobPath);
let json;
try {
  json = JSON.parse(raw.toString("utf8"));
} catch {
  json = JSON.parse(raw.toString("utf8").replace(/^\uFEFF/, ""));
}
const menus = json.data?.menus || (Array.isArray(json.data) ? json.data : json.menus || []);
const pages = walk(menus);
const out = path.join(ROOT, "data", `routes-${mode}-${new URL(origin || "http://local").host.replace(/[^\w.-]+/g, "_")}.json`);
await writeFile(out, `${JSON.stringify({ origin, mode, pageCount: pages.length, pages }, null, 2)}\n`);
console.log(`pages=${pages.length} wrote ${out}`);
for (const page of pages.slice(0, 40)) {
  console.log(`- ${page.text} -> ${page.url}`);
}
