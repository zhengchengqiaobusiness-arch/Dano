// Long-running, multi-session Recording Pi runtime. JSONL on stdin/stdout.
import fs from "node:fs";
import path from "node:path";
import readline from "node:readline";
import { AsyncLocalStorage } from "node:async_hooks";
import { fileURLToPath } from "node:url";
import {
  createAgentSession, SessionManager, SettingsManager,
} from "@earendil-works/pi-coding-agent";
import { createModelProfile, safeSessionPath } from "./profile.mjs";
import { emit, response, safeEvent } from "./protocol.mjs";
import { configureToolBridge, recordingTools, resolveToolResult } from "./tools.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const packageRoot = path.resolve(here, "../../../..");
const sessionDir = path.resolve(process.env.DANO_PI_SESSION_DIR || path.join(packageRoot, ".pi-sessions"));
fs.mkdirSync(sessionDir, { recursive: true });
const sessions = new Map();
const toolContext = new AsyncLocalStorage();
let profile = null;
const stub = process.env.PI_STUB === "1";

configureToolBridge({ emit, getSession: () => toolContext.getStore() || "" });

function assertId(value, label) {
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(String(value || ""))) throw new Error(`invalid ${label}`);
  return String(value);
}

async function getProfile() {
  if (!profile) profile = createModelProfile();
  return profile;
}

async function openSession(message) {
  const id = assertId(message.session_id, "session_id");
  if (sessions.has(id)) return sessions.get(id);
  const recordingId = assertId(message.recording_id, "recording_id");
  const role = assertId(message.role, "role");
  if (stub) {
    const rec = { id, recordingId, role, session: null, sessionPath: "", turn: 0, queue: Promise.resolve() };
    sessions.set(id, rec);
    return rec;
  }
  const { auth, registry, model } = await getProfile();
  const candidate = safeSessionPath(message.session_path || "", sessionDir);
  const manager = candidate && fs.existsSync(candidate)
    ? SessionManager.open(candidate, sessionDir, packageRoot)
    : SessionManager.create(packageRoot, sessionDir, { id });
  const settingsManager = SettingsManager.inMemory({
    compaction: { enabled: true },
    retry: { enabled: true, maxRetries: 2, baseDelayMs: 1000 },
  });
  const { session } = await createAgentSession({
    cwd: packageRoot,
    authStorage: auth,
    modelRegistry: registry,
    model,
    sessionManager: manager,
    settingsManager,
    customTools: recordingTools,
    noTools: "builtin",
  });
  const rec = { id, recordingId, role, session, sessionPath: manager.getSessionFile() || "", turn: 0, queue: Promise.resolve() };
  session.subscribe((event) => {
    if (event?.type === "turn_start") rec.turn += 1;
    emit({ type: "event", session_id: id, recording_id: recordingId, role, turn: rec.turn, event: safeEvent(event) });
  });
  sessions.set(id, rec);
  return rec;
}

function assistantText(session) {
  const messages = session?.agent?.state?.messages || [];
  for (let i = messages.length - 1; i >= 0; i--) {
    const message = messages[i];
    if (message?.role !== "assistant") continue;
    return (message.content || []).filter((part) => part?.type === "text").map((part) => part.text || "").join("");
  }
  return "";
}

async function stubPrompt(rec, message) {
  rec.turn += 1;
  // Test-only mode validates the real JSONL/tool bridge without invoking a model.
  emit({ type: "event", session_id: rec.id, recording_id: rec.recordingId, role: rec.role, turn: rec.turn, event: { type: "turn_start" } });
  emit({ type: "event", session_id: rec.id, recording_id: rec.recordingId, role: rec.role, turn: rec.turn, event: { type: "turn_end", usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0 } } });
  return { turn: rec.turn, final_text: "PI_STUB", session_path: "", revision: message.revision };
}

async function runPrompt(rec, message) {
  if (stub) return stubPrompt(rec, message);
  return toolContext.run(rec.id, async () => {
    await rec.session.prompt(String(message.prompt || ""));
    rec.sessionPath = rec.session.sessionFile || rec.sessionPath;
    return { turn: rec.turn, final_text: assistantText(rec.session).slice(0, 100000), session_path: rec.sessionPath };
  });
}

async function handle(message) {
  if (message.type === "tool_result") {
    resolveToolResult(message);
    return;
  }
  if (message.type !== "command") return;
  const requestId = String(message.request_id || "");
  try {
    switch (message.command) {
      case "ping":
        response(requestId, true, { runtime: "recording-pi", stub });
        break;
      case "open_session": {
        const rec = await openSession(message);
        response(requestId, true, { session_id: rec.id, session_path: rec.sessionPath, role: rec.role });
        break;
      }
      case "prompt": {
        const rec = sessions.get(assertId(message.session_id, "session_id"));
        if (!rec) throw new Error("session is not open");
        const task = rec.queue.then(() => runPrompt(rec, message));
        rec.queue = task.catch(() => {});
        response(requestId, true, await task);
        break;
      }
      case "cancel": {
        const rec = sessions.get(assertId(message.session_id, "session_id"));
        if (rec?.session) await rec.session.abort();
        response(requestId, true);
        break;
      }
      case "close_session": {
        const id = assertId(message.session_id, "session_id");
        const rec = sessions.get(id);
        if (rec?.session) await rec.session.abort();
        sessions.delete(id);
        response(requestId, true);
        break;
      }
      case "shutdown":
        for (const rec of sessions.values()) if (rec.session) await rec.session.abort();
        response(requestId, true);
        setTimeout(() => process.exit(0), 10);
        break;
      default:
        throw new Error(`unknown command: ${message.command}`);
    }
  } catch (error) {
    response(requestId, false, { error: String(error?.message || error) });
  }
}

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on("line", (line) => {
  try { handle(JSON.parse(line)); }
  catch (error) { process.stderr.write(`[recording-pi] invalid input: ${String(error?.message || error)}\n`); }
});
rl.on("close", () => process.exit(0));
