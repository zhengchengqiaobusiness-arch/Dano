import { mkdir, readFile, writeFile, appendFile } from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";

export async function ensureDir(dir: string) {
  await mkdir(dir, { recursive: true });
}

export function id(prefix: string) {
  return `${prefix}_${Date.now().toString(36)}_${crypto.randomBytes(4).toString("hex")}`;
}

export async function writeJson(file: string, value: unknown) {
  await ensureDir(path.dirname(file));
  await writeFile(file, JSON.stringify(value, null, 2) + "\n", "utf8");
}

export async function readJson<T>(file: string, fallback: T): Promise<T> {
  try {
    return JSON.parse(await readFile(file, "utf8")) as T;
  } catch (error: any) {
    if (error?.code === "ENOENT") return fallback;
    throw error;
  }
}

export async function appendJsonl(file: string, value: unknown) {
  await ensureDir(path.dirname(file));
  await appendFile(file, JSON.stringify(value) + "\n", "utf8");
}

export async function readJsonl<T>(file: string): Promise<T[]> {
  try {
    const text = await readFile(file, "utf8");
    return text
      .split(/\r?\n/)
      .filter(Boolean)
      .map(line => JSON.parse(line) as T);
  } catch (error: any) {
    if (error?.code === "ENOENT") return [];
    throw error;
  }
}

export function slugify(text: string) {
  const slug = text
    .normalize("NFKD")
    .toLowerCase()
    .replace(/[^\p{Letter}\p{Number}]+/gu, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 56);
  return slug || `capability-${Date.now().toString(36)}`;
}

export function getByPath(value: any, jsonPath: string): unknown {
  if (jsonPath === "$") return value;
  const literal = jsonPath.replace(/^\$\./, "");
  if (!literal.includes(".")) return value?.[literal];
  const parts = literal
    .split(".")
    .filter(Boolean)
    .flatMap(part => {
      const matches = [...part.matchAll(/([^\[\]]+)|\[(\d+)\]/g)];
      return matches.map(m => (m[1] ?? Number(m[2])));
    });
  let current = value;
  for (const part of parts) {
    if (current == null) return undefined;
    current = current[part as any];
  }
  return current;
}

function pathSegments(jsonPath: string): Array<string | number> {
  return [...jsonPath.replace(/^\$\./, "").matchAll(/([^.\[\]]+)|\[(\d+)\]/g)]
    .map(match => (match[1] ?? Number(match[2])));
}

export function setByPath(target: Record<string, unknown>, jsonPath: string, value: unknown) {
  const literal = jsonPath.replace(/^\$\./, "");
  if (!literal.includes(".")) {
    target[literal] = value;
    return;
  }
  const parts = pathSegments(jsonPath);
  if (!parts.length) return;
  let cursor: any = target;
  for (let index = 0; index < parts.length; index++) {
    const key = parts[index]!;
    if (index === parts.length - 1) {
      cursor[key] = value;
      return;
    }
    const next = parts[index + 1];
    if (cursor[key] == null) cursor[key] = typeof next === "number" ? [] : {};
    cursor = cursor[key];
  }
}
