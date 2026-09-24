import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

export function packageRoot() {
  return ROOT;
}

export function dataRoot() {
  return process.env.CABP_DATA || path.join(ROOT, "data");
}

export function recordingDir(id) {
  return path.join(dataRoot(), "recordings", id);
}

export function skillDir(skillId) {
  return path.join(dataRoot(), "skills", skillId);
}

export function tokenDir() {
  return path.join(dataRoot(), "tokens");
}

export function sessionDir() {
  return path.join(dataRoot(), "sessions");
}

export function docDir() {
  return path.join(ROOT, "doc");
}

export function skillSourceDir() {
  return path.join(ROOT, "skill");
}

export function webDir() {
  return path.join(ROOT, "web");
}
