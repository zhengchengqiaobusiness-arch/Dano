import { getModels } from "@earendil-works/pi-ai/compat";
import {
  createAgentSession,
  ModelRuntime,
  SessionManager
} from "@earendil-works/pi-coding-agent";

const prompt = process.argv.slice(2).join(" ") ||
  "Use the business-skill-studio skill to explain the next safe step.";

const modelId = process.env.OPENAI_MODEL || "gpt-5.5";
const model = getModels("openai").find(candidate => candidate.id === modelId);
if (!model) throw new Error(`Pi built-in OpenAI model not found: ${modelId}`);

const modelRuntime = await ModelRuntime.create();
if (process.env.OPENAI_API_KEY) {
  await modelRuntime.setRuntimeApiKey("openai", process.env.OPENAI_API_KEY);
}

const { session } = await createAgentSession({
  cwd: process.cwd(),
  model,
  modelRuntime,
  sessionManager: SessionManager.inMemory(process.cwd())
});

session.subscribe(event => {
  if (event.type === "message_update" && event.assistantMessageEvent.type === "text_delta") {
    process.stdout.write(event.assistantMessageEvent.delta);
  }
});

await session.prompt(prompt);
process.stdout.write("\n");
session.dispose();
