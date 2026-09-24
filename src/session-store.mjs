import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { sessionDir } from "./paths.mjs";

export function originFromUrl(raw) {
  try {
    return new URL(String(raw || "")).origin;
  } catch {
    return "";
  }
}

function hostFile(raw) {
  const origin = originFromUrl(raw);
  let host = "unknown";
  try {
    host = new URL(origin).host.replace(/[^\w.-]+/g, "_");
  } catch {
    host = "unknown";
  }
  return path.join(sessionDir(), `${host}.json`);
}

export async function loadStorageState(targetUrl) {
  try {
    const text = await readFile(hostFile(targetUrl), "utf8");
    const parsed = JSON.parse(text);
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch (error) {
    if (error?.code === "ENOENT") return null;
    return null;
  }
}

export async function saveStorageState(targetUrl, state) {
  const file = hostFile(targetUrl);
  await mkdir(path.dirname(file), { recursive: true });
  await writeFile(file, `${JSON.stringify(state)}\n`, "utf8");
  return file;
}
