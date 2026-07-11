import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import {
  SessionManager,
  createAgentSession,
} from "@earendil-works/pi-coding-agent";
import { describe, expect, it, vi } from "vitest";

describe("Pi slash command invocation", () => {
  it("executes extensions and expands prompt templates and skills in a real session", async () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "dano-commands-"));
    const cwd = path.join(root, "workspace");
    const agentDir = path.join(root, "agent");
    const markerPath = path.join(root, "extension-result.txt");
    fs.mkdirSync(path.join(agentDir, "extensions"), { recursive: true });
    fs.mkdirSync(path.join(agentDir, "prompts"), { recursive: true });
    fs.mkdirSync(path.join(agentDir, "skills", "fixture-skill"), {
      recursive: true,
    });
    fs.mkdirSync(cwd, { recursive: true });

    fs.writeFileSync(
      path.join(agentDir, "extensions", "fixture.ts"),
      `import { writeFileSync } from "node:fs";
export default function (pi) {
  pi.registerCommand("fixture-extension", {
    description: "Execute a real extension handler",
    handler: async (args) => writeFileSync(${JSON.stringify(markerPath)}, args),
  });
}
`,
    );
    fs.writeFileSync(
      path.join(agentDir, "prompts", "fixture-prompt.md"),
      `---
description: Expand a real prompt template
---
PROMPT_TEMPLATE_EXPANDED $@
`,
    );
    fs.writeFileSync(
      path.join(agentDir, "skills", "fixture-skill", "SKILL.md"),
      `---
name: fixture-skill
description: Expand a real skill command.
---

SKILL_BODY_EXPANDED
`,
    );

    const fixtureModel: NonNullable<
      NonNullable<Parameters<typeof createAgentSession>[0]>["model"]
    > = {
      id: "fixture-model",
      name: "Fixture Model",
      provider: "fixture-provider",
      api: "openai-completions",
      baseUrl: "http://127.0.0.1/unused",
      reasoning: false,
      input: ["text"],
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      contextWindow: 16_000,
      maxTokens: 1_024,
    };

    const { session } = await createAgentSession({
      cwd,
      agentDir,
      model: fixtureModel,
      noTools: "all",
      sessionManager: SessionManager.inMemory(cwd),
    });

    try {
      vi.spyOn(session.modelRegistry, "hasConfiguredAuth").mockReturnValue(true);
      const agent = (session as unknown as {
        agent: { prompt(messages: unknown[]): Promise<void> };
      }).agent;
      const agentPrompt = vi
        .spyOn(agent, "prompt")
        .mockResolvedValue(undefined);

      await session.prompt("/fixture-extension browser-ok");
      expect(fs.readFileSync(markerPath, "utf8")).toBe("browser-ok");
      expect(agentPrompt).not.toHaveBeenCalled();

      await session.prompt("/fixture-prompt current changes");
      const templateMessages = agentPrompt.mock.calls[0]?.[0] as Array<{
        content: Array<{ type: string; text?: string }>;
      }>;
      expect(templateMessages[0]?.content[0]?.text).toBe(
        "PROMPT_TEMPLATE_EXPANDED current changes",
      );

      agentPrompt.mockClear();
      await session.prompt("/skill:fixture-skill repository");
      const skillMessages = agentPrompt.mock.calls[0]?.[0] as Array<{
        content: Array<{ type: string; text?: string }>;
      }>;
      const skillText = skillMessages[0]?.content[0]?.text;
      expect(skillText).toContain('<skill name="fixture-skill"');
      expect(skillText).toContain("SKILL_BODY_EXPANDED");
      expect(skillText).toContain("repository");
    } finally {
      session.dispose();
      fs.rmSync(root, { recursive: true, force: true });
    }
  });
});
