import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterAll } from "vitest";

// Tests must never discover the developer's Pi packages, credentials or skills.
const root = mkdtempSync(join(tmpdir(), "dano-test-config-"));
process.env.PI_CODING_AGENT_DIR = join(root, "agent");
afterAll(() => rmSync(root, { recursive: true, force: true }));
