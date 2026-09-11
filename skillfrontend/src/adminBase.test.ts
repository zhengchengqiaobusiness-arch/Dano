import assert from "node:assert/strict";
import test from "node:test";
import { adminBasename, adminHref, routerBasename } from "./adminBase.ts";

test("生产构建的页面路由必须停在 /admin 下，不能回到站点根路径", () => {
  assert.equal(adminBasename("/admin/"), "/admin");
  assert.equal(routerBasename("/admin/"), "/admin");
  assert.equal(adminHref("/recording", "/admin/"), "/admin/recording");
  assert.equal(adminHref("/skills", "/admin/"), "/admin/skills");
  assert.equal(adminHref("/tenant", "/admin/"), "/admin/tenant");
  assert.notEqual(adminHref("/recording", "/admin/"), "/recording");
});

test("本地开发没有 /admin 前缀时，路由仍是站点内相对路径", () => {
  assert.equal(adminBasename("/"), "");
  assert.equal(routerBasename("/"), undefined);
  assert.equal(adminHref("/recording", "/"), "/recording");
});
