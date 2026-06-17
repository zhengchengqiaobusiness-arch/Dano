// Dano pi 桥入口 · Python 经 spawn 启动本进程。
// 协议(JSONL):stdin 收 start_run / cancel_run;stdout 只输出 JSONL 事件;日志一律走 stderr。
// 两种模式:
//   PI_STUB=1  → 确定性桥验证:直接走工具 execute(回调 Python),不调 LLM(Phase 0 无 key 也能验)。
//   否则       → 真实:createAgentSession,pi 自主选工具(需 LLM 凭证)。
import readline from "node:readline";
import { customTools } from "./tools.mjs";

const emit = (o) => process.stdout.write(JSON.stringify(o) + "\n"); // 只此一处写 stdout
const log = (...a) => process.stderr.write("[run_pi] " + a.join(" ") + "\n");
const BACK_DIR = new URL("..", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");

function readStartRun() {
  return new Promise((resolve) => {
    const rl = readline.createInterface({ input: process.stdin });
    rl.on("line", (line) => {
      line = line.trim();
      if (!line) return;
      let msg;
      try { msg = JSON.parse(line); } catch { log("非法 JSON,忽略:", line.slice(0, 80)); return; }
      if (msg.type === "start_run") { resolve({ msg, rl }); }
      // cancel_run 等在 main 里再挂监听
    });
    rl.on("close", () => resolve(null));
  });
}

async function realRun(start) {
  const pi = await import("@earendil-works/pi-coding-agent");
  const { ModelRegistry, AuthStorage, createAgentSession, SessionManager, DefaultResourceLoader } = pi;

  // 解析模型:用 pi **内置** provider(自带 API 适配器 + baseUrl),只把 key 设进 authStorage。
  // key 仅经 env,不落代码。DeepSeek 用内置 'deepseek' provider + 'deepseek-v4-flash'。
  const auth = AuthStorage.inMemory();
  const apiKey = process.env.DANO_PI_API_KEY;
  const provider = process.env.DANO_PI_PROVIDER || "deepseek";
  const modelId = process.env.DANO_PI_MODEL || "deepseek-v4-flash";
  if (apiKey) auth.setRuntimeApiKey(provider, apiKey);
  const registry = ModelRegistry.create(auth);
  let model = registry.find(provider, modelId);
  if (!model) model = registry.getAvailable?.()[0];
  if (!model || (registry.hasConfiguredAuth && !registry.hasConfiguredAuth(model))) {
    return { status: "failed", error: "no_model_or_credentials" };
  }

  // skills:DefaultResourceLoader 需 cwd + agentDir;skill 经 additionalSkillPaths 加入。
  let resourceLoader, skillsLoaded = false;
  try {
    const skillsDir = new URL("./skills", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
    resourceLoader = new DefaultResourceLoader({
      cwd: BACK_DIR, agentDir: `${BACK_DIR}/.pi-agent`,
      additionalSkillPaths: [skillsDir],
      noExtensions: true, noThemes: true, noPromptTemplates: true, noContextFiles: true,
    });
    skillsLoaded = true;
  } catch (e) { log("resourceLoader 构造失败(跳过 skills):", e.message); }

  const { session } = await createAgentSession({
    model, authStorage: auth, modelRegistry: registry,   // ← 用我的 auth/registry(带 key)
    customTools, noTools: "builtin",
    ...(resourceLoader ? { resourceLoader } : {}),
    sessionManager: SessionManager.inMemory ? SessionManager.inMemory() : undefined,
  });

  let toolEvents = 0;
  try {
    session.subscribe((ev) => {
      const t = (ev && ev.type) || "";
      if (/tool/i.test(t)) { toolEvents++; emit({ type: "agent_event", run_id: start.run_id, event: t }); }
    });
  } catch (e) { log("subscribe 失败:", e.message); }

  await session.prompt(start.prompt);

  // 取最后一条 assistant 文本作为最终输出
  let finalText = "";
  try {
    const msgs = (session.agent && session.agent.state && session.agent.state.messages) || [];
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i] && msgs[i].role === "assistant") {
        const c = msgs[i].content;
        finalText = Array.isArray(c) ? c.map((x) => x.text || "").join("") : String(c || "");
        if (finalText) break;
      }
    }
  } catch {}
  return { status: "completed", skills_loaded: skillsLoaded, tool_events: toolEvents,
           final_text: finalText.slice(0, 600) };
}

async function stubRun(start) {
  // 直接调工具 execute → 走真实的 Node→Python HTTP 回调路径(验证桥,不需 LLM)
  const tc = "tc-stub-1";
  emit({ type: "tool_call_started", run_id: start.run_id, tool_call_id: tc, tool: "parse_spec" });
  const params = (start.context && start.context.system_instance_id)
    ? { system_instance_id: start.context.system_instance_id }
    : { system_instance_id: "a-oa" };
  const res = await customTools[0].execute(tc, params, undefined, undefined, {});
  emit({ type: "tool_call_finished", run_id: start.run_id, tool_call_id: tc, status: "ok" });
  return { status: "completed", stub: true, tool_result: res };
}

async function main() {
  const got = await readStartRun();
  if (!got) { log("stdin 关闭,无 start_run"); process.exit(0); }
  const start = got.msg;
  const runId = start.run_id;

  // 取消监听
  got.rl.on("line", (line) => {
    try { const m = JSON.parse(line); if (m.type === "cancel_run" && m.run_id === runId) { emit({ type: "run_completed", run_id: runId, status: "cancelled" }); process.exit(0); } } catch {}
  });
  // 超时
  const timeoutMs = ((start.budget && start.budget.timeout_s) || 600) * 1000;
  const timer = setTimeout(() => { emit({ type: "run_completed", run_id: runId, status: "failed", error: "timeout" }); process.exit(0); }, timeoutMs);
  timer.unref?.();

  emit({ type: "run_started", run_id: runId });
  let result;
  try {
    result = process.env.PI_STUB === "1" ? await stubRun(start) : await realRun(start);
  } catch (e) {
    result = { status: "failed", error: String((e && e.message) || e) };
  }
  clearTimeout(timer);
  emit({ type: "run_completed", run_id: runId, ...result });
  process.exit(0);
}

main();
