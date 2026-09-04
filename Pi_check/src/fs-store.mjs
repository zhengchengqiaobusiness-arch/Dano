/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 *
 * 文件系统只保存事实、PI 草稿、PI 最终结果和独立回执。
 * 禁止改写 PI 结果对象中的任何值。
 */

import { appendFile, mkdir, readFile, rename, rm, stat, writeFile } from "node:fs/promises";
import path from "node:path";

function safeId(value) {
  const text = String(value ?? "");
  if (!/^[a-zA-Z0-9_-]+$/.test(text)) throw new Error("invalid recording id");
  return text;
}

async function atomicWrite(filePath, text) {
  await mkdir(path.dirname(filePath), { recursive: true });
  const temporary = `${filePath}.${process.pid}.${Date.now()}.tmp`;
  await writeFile(temporary, text, "utf8");
  try {
    await rename(temporary, filePath);
  } catch (error) {
    if (error?.code !== "EPERM" && error?.code !== "EEXIST") throw error;
    await writeFile(filePath, text, "utf8");
    await rm(temporary, { force: true });
  }
}

async function atomicJson(filePath, value) {
  await atomicWrite(filePath, `${JSON.stringify(value, null, 2)}\n`);
}

export class RecordingFiles {
  constructor(root) {
    this.root = path.resolve(root);
    this.#queues = new Map();
  }

  #queues;

  async #serialized(recordingId, work) {
    const key = String(recordingId);
    const previous = this.#queues.get(key) || Promise.resolve();
    let release;
    const next = new Promise((resolve) => {
      release = resolve;
    });
    this.#queues.set(key, previous.then(() => next));
    await previous;
    try {
      return await work();
    } finally {
      release();
    }
  }

  directory(recordingId) {
    return path.join(this.root, safeId(recordingId));
  }

  resultPath(recordingId) {
    return path.join(this.directory(recordingId), "pi-result.json");
  }

  receiptPath(recordingId) {
    return path.join(this.directory(recordingId), "receipt.json");
  }

  draftPath(recordingId) {
    return path.join(this.directory(recordingId), "pi-draft.json");
  }

  async initialize(recordingId, manifest) {
    const directory = this.directory(recordingId);
    await mkdir(path.join(directory, "blobs"), { recursive: true });
    await atomicJson(path.join(directory, "manifest.json"), manifest);
    return directory;
  }

  async writeManifest(recordingId, manifest) {
    await this.#serialized(recordingId, () => (
      atomicJson(path.join(this.directory(recordingId), "manifest.json"), manifest)
    ));
  }

  async appendEvidence(recordingId, event) {
    await this.#serialized(recordingId, () => appendFile(
      path.join(this.directory(recordingId), "evidence.jsonl"),
      `${JSON.stringify(event)}\n`,
      "utf8",
    ));
  }

  async readEvidence(recordingId) {
    try {
      const text = await readFile(path.join(this.directory(recordingId), "evidence.jsonl"), "utf8");
      return text.split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line));
    } catch (error) {
      if (error?.code === "ENOENT") return [];
      throw error;
    }
  }

  async writeBlob(recordingId, blobId, bytes) {
    const filePath = path.join(this.directory(recordingId), "blobs", `${safeId(blobId)}.bin`);
    await writeFile(filePath, bytes);
    return filePath;
  }

  async readBlob(recordingId, blobId) {
    return readFile(path.join(this.directory(recordingId), "blobs", `${safeId(blobId)}.bin`));
  }

  async hasPiResult(recordingId) {
    try {
      await stat(this.resultPath(recordingId));
      return true;
    } catch (error) {
      if (error?.code === "ENOENT") return false;
      throw error;
    }
  }

  async writePiResult(recordingId, result) {
    if (await this.hasPiResult(recordingId)) {
      throw new Error("同一录制只允许一个最终结果");
    }
    await atomicJson(this.resultPath(recordingId), result);
  }

  async readPiResult(recordingId) {
    try {
      return JSON.parse(await readFile(this.resultPath(recordingId), "utf8"));
    } catch (error) {
      if (error?.code === "ENOENT") return null;
      throw error;
    }
  }

  async deletePiResult(recordingId) {
    await rm(this.resultPath(recordingId), { force: true });
    await rm(this.receiptPath(recordingId), { force: true });
  }

  async writeReceipt(recordingId, receipt) {
    await atomicJson(this.receiptPath(recordingId), receipt);
  }

  async readReceipt(recordingId) {
    try {
      return JSON.parse(await readFile(this.receiptPath(recordingId), "utf8"));
    } catch (error) {
      if (error?.code === "ENOENT") return null;
      throw error;
    }
  }

  async writeDraft(recordingId, draft) {
    await atomicJson(this.draftPath(recordingId), draft);
  }

  async readDraft(recordingId) {
    try {
      return JSON.parse(await readFile(this.draftPath(recordingId), "utf8"));
    } catch (error) {
      if (error?.code === "ENOENT") return null;
      throw error;
    }
  }
}
