import test from "node:test";
import assert from "node:assert/strict";
import extension from "../.pi/extensions/business-skill-studio.js";

test("only a successful export terminates the current Pi turn", () => {
  const hooks = new Map<string, Function>();
  extension({ registerProvider() {}, registerTool() {}, on(name: string, handler: Function) { hooks.set(name, handler); } } as any);
  const handler = hooks.get("turn_end");
  assert.ok(handler);
  let aborted = 0;
  const context = { abort() { aborted++; } };
  handler({ toolResults: [{ toolName: "business_skill_export", isError: true }] }, context);
  handler({ toolResults: [{ toolName: "business_browser_control", isError: false }] }, context);
  assert.equal(aborted, 0);
  handler({ toolResults: [{ toolName: "business_skill_export", isError: false }] }, context);
  assert.equal(aborted, 1);
});
