import assert from "node:assert/strict";
import test from "node:test";
import { shouldRouteToPiCheck } from "./piCheckRoute.ts";

test("目录与 token 的代理前缀比 /v1 更具体", () => {
  assert.equal("/v1/skills".startsWith("/v1/skills"), true);
  assert.equal("/v1/skills/export".startsWith("/v1/skills"), true);
  assert.equal("/v1/settings/token".startsWith("/v1/settings/token"), true);
  assert.equal("/v1/recording-results".startsWith("/v1/skills"), false);
});

test("出包、目录、token 走 Pi_check", () => {
  assert.equal(shouldRouteToPiCheck("/v1/skills", "GET"), true);
  assert.equal(shouldRouteToPiCheck("/v1/skills?page=1", "GET"), true);
  assert.equal(shouldRouteToPiCheck("/v1/skills/export", "POST"), true);
  assert.equal(shouldRouteToPiCheck("/v1/skills/oa.rec_1/freeze", "POST"), true);
  assert.equal(shouldRouteToPiCheck("/v1/settings/token", "POST"), true);
  assert.equal(shouldRouteToPiCheck("/v1/recording-results/uuid-1/export-skill", "POST"), true);
  assert.equal(shouldRouteToPiCheck("/v1/recording-results/rec_1/draft", "PUT"), true);
  assert.equal(shouldRouteToPiCheck("/v1/pi-recordings", "GET"), true);
  assert.equal(shouldRouteToPiCheck("/v1/recording-results/rec_1", "GET"), true);
  assert.equal(shouldRouteToPiCheck("/skills", "GET"), true);
  assert.equal(shouldRouteToPiCheck("/recording-results/uuid-1/export-skill", "POST"), true);
});

test("录制历史和登录不走 Pi_check", () => {
  assert.equal(shouldRouteToPiCheck("/v1/recording-results", "GET"), false);
  assert.equal(shouldRouteToPiCheck("/v1/recording-results/uuid-1", "GET"), false);
  assert.equal(shouldRouteToPiCheck("/v1/recording-results/uuid-1/export-skill", "GET"), true);
  assert.equal(shouldRouteToPiCheck("/v1/pi-recordings/rec_1/export-skill", "POST"), true);
  assert.equal(shouldRouteToPiCheck("/tenants", "POST"), false);
  assert.equal(shouldRouteToPiCheck("/export/directory", "GET"), false);
});
