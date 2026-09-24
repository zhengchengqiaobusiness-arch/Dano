import assert from "node:assert/strict";
import test from "node:test";
import {
  applyExportedCatalog,
  mergeRecordingHistory,
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

test("Pi_check 已交能力覆盖同 recording_id 的网关历史", () => {
  const merged = mergeRecordingHistory(
    [{ id: "uuid-1", recording_id: "rec_quit", title: "旧" }],
    [{ id: "rec_quit", recording_id: "rec_quit", title: "离职申请", capability_count: 6 }],
  );
  assert.equal(merged.length, 1);
  assert.equal(merged[0].id, "rec_quit");
  assert.equal(merged[0].recording_id, "rec_quit");
});

test("exported catalog overlays the same recording row", () => {
  const overlaid = applyExportedCatalog(
    { id: "uuid-1", recording_id: "rec_quit", skill_lifecycle: "ready_to_export" },
    [{ name: "oa.rec_quit", recording_id: "rec_quit", title: "离职申请", version: 2, export_path: "E:/out/quit" }],
  );
  assert.equal(overlaid.skill_id, "oa.rec_quit");
  assert.equal(overlaid.skill_lifecycle, "exported");
  assert.equal(overlaid.skill_export_status, "exported");
  assert.equal(overlaid.skill_version, 2);
  assert.equal(overlaid.skill_export_title, "离职申请");
});
