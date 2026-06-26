import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { createAgentSessionServices } from "@earendil-works/pi-coding-agent";
import { afterEach, describe, expect, it } from "vitest";
import { createHeadlessUIContext } from "../headless-ui-context.js";

describe("Heimdall extension", () => {
  const tempDirs: string[] = [];

  afterEach(() => {
    for (const path of tempDirs.splice(0)) {
      rmSync(path, { recursive: true, force: true });
    }
  });

  it("supports extension status formatting in headless mode", () => {
    expect(createHeadlessUIContext().theme.fg("accent", "guarded")).toBe(
      "guarded",
    );
  });

  it("loads the guarded bash tool from the pinned package", async () => {
    const cwd = mkdtempSync(join(tmpdir(), "dano-heimdall-"));
    tempDirs.push(cwd);
    const services = await createAgentSessionServices({
      cwd,
      agentDir: join(cwd, ".pi-agent"),
      resourceLoaderOptions: {
        additionalExtensionPaths: [
          fileURLToPath(
            import.meta.resolve(
              "@josephyoung/pi-heimdall/extensions/heimdall.ts",
            ),
          ),
        ],
        noContextFiles: true,
        noPromptTemplates: true,
        noSkills: true,
        noThemes: true,
      },
    });
    const loaded = services.resourceLoader.getExtensions();
    const heimdall = loaded.extensions.find(extension =>
      extension.path.includes("pi-heimdall/extensions/heimdall.ts"),
    );

    expect(loaded.errors).toEqual([]);
    expect(heimdall?.tools.get("bash")?.definition.label).toBe(
      "bash (heimdall sandbox)",
    );
  });
});
