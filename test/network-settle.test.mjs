import assert from "node:assert/strict";
import test from "node:test";
import { actionAccepts, actionSettled, framesRelated, pageOpenedFor, sealedUntil } from "../src/browser/network.mjs";

const started = 1000;

test("a click inside a frame keeps that frame and its outer page, not a sibling frame", () => {
  const frame = (url, parent = null) => ({ url: () => url, parentFrame: () => parent });
  const main = frame("http://app/");
  const child = frame("http://app/inner", main);
  const sibling = frame("http://app/other", main);
  assert.equal(framesRelated(child, main), true);
  assert.equal(framesRelated(main, child), false);
  assert.equal(framesRelated(child, child), true);
  assert.equal(framesRelated(child, sibling), false);
});

test("two embedded pages with the same address stay separate", () => {
  const frame = (url, parent, guid) => ({ url: () => url, parentFrame: () => parent, _guid: guid });
  const main = frame("http://app/", null, "main");
  const left = frame("http://app/embed", main, "left");
  const right = frame("http://app/embed", main, "right");
  assert.equal(framesRelated(left, right), false);
  assert.equal(framesRelated(left, left), true);
});

test("a window opened by this click keeps its requests, an already open window does not", () => {
  const opener = { id: "main" };
  const clicked = { page: () => opener };
  const popup = { opener: () => opener };
  const other = { opener: () => ({ id: "else" }) };
  const earlier = { opener: () => opener };
  const action = { open: true, armed: 4000, frames: [clicked] };
  const pages = [
    { page: popup, at: 4100 },
    { page: other, at: 4100 },
    { page: earlier, at: 1000 },
  ];
  const frameOf = (page) => ({ page: () => page });
  assert.equal(pageOpenedFor(action, frameOf(popup), pages), true);
  assert.equal(pageOpenedFor(action, frameOf(other), pages), false);
  assert.equal(pageOpenedFor(action, frameOf(earlier), pages), false);
});

test("an action stays open briefly so a request that starts after the click is still its request", () => {
  assert.equal(actionSettled({ now: 1399, started, rows: [] }), false);
  assert.equal(actionSettled({ now: 1400, started, rows: [] }), true);
});

test("an action waits out a request that is still running, then a quiet gap", () => {
  const running = [{ started_at: 1100, ended_at: 0 }];
  assert.equal(actionSettled({ now: 2000, started, rows: running }), false);
  const finished = [{ started_at: 1100, ended_at: 1200 }];
  assert.equal(actionSettled({ now: 1599, started, rows: finished }), false);
  assert.equal(actionSettled({ now: 1600, started, rows: finished }), true);
});

test("the wait ends at the cap even when a request has not finished", () => {
  assert.equal(actionSettled({ now: 2500, started, rows: [{ started_at: 1100, ended_at: 0 }] }), true);
});

test("a request after the action window is not that action, even if the snapshot is still open", () => {
  const action = { open: true, started: 1000 };
  assert.equal(actionAccepts(action, 1000), true);
  assert.equal(actionAccepts(action, 2500), true);
  assert.equal(actionAccepts(action, 2501), false);
  assert.equal(actionAccepts(action, 999), false);
  assert.equal(actionAccepts({ ...action, open: false }, 1100), false);
});

test("a snapshot keeps the 1500ms after the click and does not leave the window open", () => {
  assert.equal(sealedUntil({ started: 1000, armed: 4000 }, 4400), 5500);
  assert.equal(sealedUntil({ started: 1000 }, 4400), 2500);
});

test("the window starts at the click, not at the earlier name check", () => {
  const clicking = { open: true, started: 1000, armed: 4000 };
  assert.equal(actionAccepts(clicking, 2000), false);
  assert.equal(actionAccepts(clicking, 9000), true);
  const sealed = { ...clicking, acceptUntil: 9500 };
  assert.equal(actionAccepts(sealed, 9500), true);
  assert.equal(actionAccepts(sealed, 12000), false);
});
