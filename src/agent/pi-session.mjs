import { mkdir, readFile } from "node:fs/promises";
import path from "node:path";
import { dataRoot, packageRoot, recordingDir } from "../paths.mjs";
import { readGoal } from "../evidence/store.mjs";
import { applyPiModelConfig } from "./pi-model.mjs";
import { installOpenAIToolCallStreamCompatibility } from "./openai-stream-compat.mjs";
import { hostTools, toolNames, wrapHostTools } from "./tools.mjs";

let startOverride = null;

export function setStartRecordingPi(fn) {
  startOverride = fn;
}

export async function startRecordingPi({ recordingId, tools, recording }) {
  if (startOverride) return startOverride({ recordingId, tools, recording });
  const host = tools || hostTools(recording);
  const {
    createAgentSession, SessionManager, AuthStorage, ModelRegistry, DefaultResourceLoader,
  } = await import("@mariozechner/pi-coding-agent");
  const agentDir = path.join(dataRoot(), "pi-agent");
  const cwd = path.join(recordingDir(recordingId), "pi-cwd");
  await mkdir(agentDir, { recursive: true });
  await mkdir(cwd, { recursive: true });
  const authStorage = AuthStorage.create(path.join(agentDir, "auth.json"));
  const modelRegistry = ModelRegistry.create(authStorage, path.join(agentDir, "models.json"));
  const resolved = applyPiModelConfig(authStorage, modelRegistry);
  installOpenAIToolCallStreamCompatibility({ baseUrl: resolved.baseUrl });
  const promptText = await readFile(path.join(packageRoot(), "src", "agent", "prompt.md"), "utf8");
  const playwrightSkill = await readFile(path.join(packageRoot(), "skill", "playwright-cli", "SKILL.md"), "utf8");
  const deriveSkill = await readFile(path.join(packageRoot(), "skill", "derive-client", "SKILL.md"), "utf8");
  const goal = await readGoal(recordingId);
  const resourceLoader = new DefaultResourceLoader({
    cwd,
    agentDir,
    systemPromptOverride: () => [promptText, playwrightSkill, deriveSkill, JSON.stringify(goal)].join("\n\n"),
  });
  const customTools = wrapHostTools(host);
  const created = await createAgentSession({
    cwd,
    agentDir,
    authStorage,
    modelRegistry,
    model: resolved.model,
    noTools: "builtin",
    tools: toolNames(),
    customTools,
    resourceLoader,
    sessionManager: SessionManager.inMemory(),
  });
  const session = created.session;
  let prompting = null;
  const queued = [];
  async function prompt(text) {
    if (recording?.finished) return;
    if (recording?.paused || prompting) {
      queued.push(text);
      return prompting;
    }
    prompting = session.prompt(text);
    try {
      await prompting;
    } finally {
      prompting = null;
      if (recording?.paused || recording?.finished) return;
      const next = queued.shift();
      if (next) await prompt(next);
    }
  }
  const goalText = `${goal.goal_text || ""}\n${goal.page_url || ""}`;
  const context = typeof host.context === "function" ? await host.context() : {};
  await prompt(`${goalText}\n${JSON.stringify({ snapshot: context.snapshot, index: context.index })}`);
  return {
    prompt: async (text) => {
      if (recording?.paused) recording.paused = false;
      if (recording?.status === "waiting_operator") recording.status = "recording";
      await prompt(text || "人已继续，从当前页面接着做");
    },
    dispose: () => session.dispose?.(),
  };
}
