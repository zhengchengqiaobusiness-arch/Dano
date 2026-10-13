import assert from "node:assert/strict";
import test from "node:test";
import { AUTH_LOCK_MAX_MINUTES, AUTH_MAX_FAILURES, CLEARED, isLocked, registerFailure } from "../src/auth/throttle.mjs";

const NOW = 1_700_000_000;

test("未达阈值不锁定", () => {
  let state = CLEARED;
  for (let i = 0; i < AUTH_MAX_FAILURES - 1; i += 1) {
    state = registerFailure(state, { now: NOW });
  }
  assert.equal(state.fail_count, AUTH_MAX_FAILURES - 1);
  assert.equal(isLocked(state, NOW), false);
});

test("第五次失败锁一分钟，过后放行", () => {
  let state = CLEARED;
  for (let i = 0; i < AUTH_MAX_FAILURES; i += 1) {
    state = registerFailure(state, { now: NOW });
  }
  assert.equal(isLocked(state, NOW), true);
  assert.equal(state.locked_until, NOW + 60);
  assert.equal(isLocked(state, NOW + 61), false);
});

test("锁定时长按失败次数翻倍并封顶", () => {
  let state = CLEARED;
  for (let i = 0; i < AUTH_MAX_FAILURES; i += 1) {
    state = registerFailure(state, { now: NOW });
  }
  assert.equal(state.locked_until - NOW, 60);
  state = registerFailure(state, { now: NOW });
  assert.equal(state.locked_until - NOW, 120);
  state = registerFailure(state, { now: NOW });
  assert.equal(state.locked_until - NOW, 240);
  state = registerFailure({ fail_count: 40, locked_until: 0 }, {
    now: NOW,
    lockMaxMinutes: AUTH_LOCK_MAX_MINUTES,
  });
  assert.equal(state.locked_until - NOW, AUTH_LOCK_MAX_MINUTES * 60);
});

test("阈值可配，成功清零后未锁定", () => {
  let state = registerFailure(CLEARED, { now: NOW, maxFailures: 2 });
  assert.equal(isLocked(state, NOW), false);
  state = registerFailure(state, { now: NOW, maxFailures: 2 });
  assert.equal(isLocked(state, NOW), true);
  assert.equal(CLEARED.fail_count, 0);
  assert.equal(isLocked(CLEARED, NOW), false);
});
