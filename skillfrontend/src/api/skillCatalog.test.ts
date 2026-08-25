import assert from "node:assert/strict";
import test from "node:test";
import {
  notifySkillCatalogChanged,
  observeSkillCatalogChanges,
  skillDisplayId,
} from "./skillCatalog.ts";

test("catalog displays the action id instead of the canonical runtime id", () => {
  assert.equal(skillDisplayId({
    name: "admin-dianshixinxi-com-90.action_8a01bc7d87ef4680b2b259147e3d3322",
    action: "action_8a01bc7d87ef4680b2b259147e3d3322",
  }), "action_8a01bc7d87ef4680b2b259147e3d3322");
});

test("a completed export notifies the mounted catalog to reload", () => {
  const target = new EventTarget();
  let refreshes = 0;
  const stop = observeSkillCatalogChanges(() => { refreshes += 1; }, target);

  notifySkillCatalogChanged(target);
  stop();
  notifySkillCatalogChanged(target);

  assert.equal(refreshes, 1);
});
