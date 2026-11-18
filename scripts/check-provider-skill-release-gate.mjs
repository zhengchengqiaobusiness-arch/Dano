#!/usr/bin/env node
import { createHash, randomBytes, randomUUID } from "node:crypto";
import {
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { join, resolve } from "node:path";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { isDeepStrictEqual } from "node:util";

const appRequire = createRequire(
  new URL("../apps/dano/package.json", import.meta.url),
);
const jiti = appRequire("jiti").createJiti(import.meta.url);
let normalizeAskUserQuestionCardRequest;

const root = resolve(import.meta.dirname, "..");
const fixtureDir = join(root, "scripts/fixtures/provider-broker-release-gate");
const skillName = "provider-broker-release-gate";
const mode = process.argv[2];
const SHA256 = /^[0-9a-f]{64}$/;
const FORBIDDEN_EVIDENCE =
  /https?:\/\/|password|client.?secret|access.?token|refresh.?token|cookie|authorization|login.?session|private.?payload/i;

try {
  if (mode === "install") installSkill();
  else if (mode === "remove") removeSkill();
  else if (mode === "capture") await captureGate(requiredArgument(3));
  else if (mode === "audit") auditGate(requiredArgument(3));
  else if (mode === "prepare" || mode === "verify") {
    throw new Error(
      "offline evidence cannot prove a live HTTP/SSE/Pi Skill collector run or browser surface provenance; use capture and attach an external IAB/Chrome acceptance record",
    );
  }
  else {
    throw new Error(
      "usage: check-provider-skill-release-gate.mjs <install|remove|capture|audit> [evidence.json]",
    );
  }
} catch (error) {
  console.error(error instanceof Error ? error.message : "release gate failed");
  process.exitCode = 1;
}

function installSkill() {
  const agentDir = requiredEnvironment("PI_CODING_AGENT_DIR");
  const providerPath = configuredProviderPath();
  const targetDir = join(agentDir, "skills", skillName);
  const targetPath = join(targetDir, "SKILL.md");
  const rendered = readFileSync(join(fixtureDir, "SKILL.md"), "utf8")
    .replace("{{PROVIDER_REQUEST_PATH}}", escapeYamlString(providerPath));
  if (rendered.includes("{{")) {
    throw new Error("provider Skill fixture contains an unresolved value");
  }

  mkdirSync(targetDir, { recursive: true });
  const stagingPath = join(targetDir, `.SKILL.md.${process.pid}.tmp`);
  writeFileSync(stagingPath, rendered, { mode: 0o600 });
  renameSync(stagingPath, targetPath);
  console.log(`installed ${skillName}`);
}

function removeSkill() {
  const agentDir = requiredEnvironment("PI_CODING_AGENT_DIR");
  rmSync(join(agentDir, "skills", skillName), {
    recursive: true,
    force: true,
  });
  console.log(`removed ${skillName}`);
}

function newCaptureRecord() {
  return {
    schemaVersion: 2,
    runId: randomUUID(),
    preparedAt: new Date().toISOString(),
    completedAt: null,
    recordPurpose: "redacted-audit-only-not-live-proof",
    capture: {
      expectedBrowserContexts: {
        a: "codex-in-app-browser",
        b: "chrome",
      },
      callbackMode: "single-shared-dano-callback",
      seam: "Dano HTTP/SSE and Pi transcript",
    },
    markers: {
      aBefore: marker("a-before"),
      aAfter: marker("a-after"),
      bAfter: marker("b-after"),
      sharedPreference: "yellow",
    },
    observations: {
      identity: {
        aStatus: null,
        bStatus: null,
        aClientFingerprint: null,
        aAfterClientFingerprint: null,
        bClientFingerprint: null,
        aUserIdFingerprint: null,
        bUserIdFingerprint: null,
        aPreference: null,
        bPreference: null,
      },
      sharedRuntime: {
        aSessionFingerprint: null,
        bSessionFingerprint: null,
        bSwitchStatus: null,
      },
      expectedAnswerSurfaces: {
        aBeforeBrowser: null,
        aAfterBrowser: null,
        bAfterBrowser: null,
      },
      sequence: emptySequence(),
    },
  };
}

async function captureGate(evidencePath) {
  if (existsSync(evidencePath)) {
    throw new Error("provider Skill audit record already exists");
  }
  const options = captureOptions(process.argv.slice(4));
  const started = performance.now();
  const timings = {};
  const browserSource = readFileSync(
    join(root, "scripts/provider-skill-release-gate-browser.mjs"),
    "utf8",
  );
  const evidence = newCaptureRecord();
  const run = {
    tokens: {
      a: randomBytes(32).toString("hex"),
      b: randomBytes(32).toString("hex"),
    },
    raw: { a: null, b: null },
    aHeld: false,
    bObservedHeld: false,
    aLogout: null,
    bComplete: false,
  };
  let finish;
  let fail;
  const completion = new Promise((resolveCompletion, rejectCompletion) => {
    finish = resolveCompletion;
    fail = rejectCompletion;
  });
  let finalizing = false;
  const connections = new Set();
  const server = createServer(async (request, response) => {
    try {
      const url = new URL(request.url ?? "/", `http://${request.headers.host}`);
      if (request.method === "OPTIONS") {
        cors(response, options.origin);
        response.statusCode = 204;
        response.end();
        return;
      }
      if (request.headers.origin !== options.origin) {
        json(response, 403, { error: "unexpected browser origin" }, options.origin);
        return;
      }
      const slot = captureSlot(url, run.tokens);
      if (!slot) {
        json(response, 403, { error: "invalid capture slot" }, options.origin);
        return;
      }
      if (request.method === "GET" && url.pathname === "/gate.mjs") {
        cors(response, options.origin);
        response.statusCode = 200;
        response.setHeader("Content-Type", "text/javascript; charset=utf-8");
        response.setHeader("Cache-Control", "no-store");
        response.end(browserSource);
        return;
      }
      if (request.method === "GET" && url.pathname === "/config") {
        json(response, 200, {
          slot,
          markers: evidence.markers,
          sharedPreference: evidence.markers.sharedPreference,
        }, options.origin);
        return;
      }
      if (request.method === "POST" && url.pathname === "/ready") {
        if (run.raw[slot]) throw new Error(`${slot} ready phase already captured`);
        run.raw[slot] = validateSkillReady(await requestJson(request), slot);
        json(response, 202, { status: "ready" }, options.origin);
        return;
      }
      if (request.method === "GET" && url.pathname === "/peer") {
        const peer = run.raw[slot === "a" ? "b" : "a"];
        if (!peer) {
          json(response, 425, { status: "waiting" }, options.origin);
          return;
        }
        json(response, 200, {
          clientId: peer.clientId,
          sessionPath: peer.sessionPath,
        }, options.origin);
        return;
      }
      if (request.method === "POST" && url.pathname === "/a-held") {
        if (slot !== "a" || !run.raw.a || !run.raw.b) {
          throw new Error("A held phase requires both ready browser slots");
        }
        run.aHeld = true;
        json(response, 202, { status: "held" }, options.origin);
        return;
      }
      if (request.method === "POST" && url.pathname === "/b-observed-held") {
        if (slot !== "b" || !run.aHeld || !run.raw.b) {
          throw new Error("B held observation requires its persistent shared-session viewer");
        }
        run.bObservedHeld = true;
        json(response, 202, { status: "observed" }, options.origin);
        return;
      }
      if (request.method === "POST" && url.pathname === "/a-logout") {
        if (slot !== "a" || !run.aHeld || !run.bObservedHeld) {
          throw new Error("A cannot logout before B observes the held question");
        }
        run.aLogout = validateLogout(await requestJson(request));
        json(response, 202, { status: "logged-out" }, options.origin);
        return;
      }
      if (request.method === "GET" && url.pathname === "/state") {
        json(response, 200, {
          bothReady: Boolean(run.raw.a && run.raw.b),
          aHeld: run.aHeld,
          bObservedHeld: run.bObservedHeld,
          aLoggedOut: Boolean(run.aLogout),
        }, options.origin);
        return;
      }
      if (request.method === "POST" && url.pathname === "/b-complete") {
        if (slot !== "b" || !run.aLogout) throw new Error("A logout is incomplete");
        const completed = await requestJson(request);
        if (completed.status !== "authenticated") {
          throw new Error("B must remain authenticated after A logout");
        }
        run.bComplete = true;
        json(response, 202, { status: "complete" }, options.origin);
        if (!finalizing) {
          finalizing = true;
          setImmediate(() => {
            const validating = performance.now();
            try {
              finalizeSkillCapture(evidencePath, evidence, run);
              finish();
            } catch (error) {
              fail(error);
            } finally {
              timings.validationMs = Math.round(performance.now() - validating);
            }
          });
        }
        return;
      }
      json(response, 404, { error: "capture route was not found" }, options.origin);
    } catch (error) {
      json(
        response,
        400,
        { error: error instanceof Error ? error.message : "capture failed" },
        options.origin,
      );
    }
  });
  server.on("connection", socket => {
    connections.add(socket);
    socket.once("close", () => connections.delete(socket));
  });
  server.on("error", fail);
  await new Promise((resolveListen, rejectListen) => {
    server.once("error", rejectListen);
    server.listen(options.port, "127.0.0.1", resolveListen);
  });
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("capture server failed");
  const collector = `http://127.0.0.1:${address.port}`;
  console.log(`SLOT_A=${collector}/gate.mjs?slot=a&token=${run.tokens.a}`);
  console.log(`SLOT_B=${collector}/gate.mjs?slot=b&token=${run.tokens.b}`);
  const timeout = setTimeout(() => {
    fail(new Error("provider Skill capture timed out"));
  }, options.timeoutMs);
  try {
    await completion;
  } finally {
    clearTimeout(timeout);
    timings.captureMs = Math.round(performance.now() - started);
    const closing = performance.now();
    console.error(JSON.stringify({ kind: "provider-gate-phase", phase: "shutdown" }));
    // Allow the final response to drain, then bound lingering HTTP connections.
    const forceClose = setTimeout(() => {
      // Also close sockets that have not finished sending their HTTP headers.
      for (const socket of connections) socket.destroy();
    }, 1_000);
    try {
      await new Promise((resolveClose, rejectClose) => server.close(error => error ? rejectClose(error) : resolveClose()));
    } finally {
      clearTimeout(forceClose);
      timings.shutdownMs = Math.round(performance.now() - closing);
      console.error(JSON.stringify({ kind: "provider-gate-timings", ...timings }));
    }
  }
  console.log(
    "LIVE HTTP/SSE/Pi COLLECTOR PASS: active collector verified same User/two Login Sessions, shared Agent Session, A logout authentication_required, and unaffected B success from the live Pi transcript; browser surface provenance requires an external IAB/Chrome acceptance record",
  );
  console.log(`Wrote redacted audit record: ${evidencePath}`);
}

function captureOptions(argv) {
  const options = { origin: "http://localhost:5173", port: 0, timeoutMs: 10 * 60_000 };
  const fields = new Map([
    ["--origin", "origin"],
    ["--port", "port"],
    ["--timeout-ms", "timeoutMs"],
  ]);
  for (let index = 0; index < argv.length; index += 2) {
    const field = fields.get(argv[index]);
    const value = argv[index + 1];
    if (!field || value === undefined) throw new Error(`invalid capture option ${argv[index]}`);
    options[field] = field === "origin" ? new URL(value).origin : Number(value);
  }
  if (!Number.isInteger(options.port) || options.port < 0) throw new Error("--port is invalid");
  if (!Number.isInteger(options.timeoutMs) || options.timeoutMs < 1000) {
    throw new Error("--timeout-ms is invalid");
  }
  return options;
}

function captureSlot(url, tokens) {
  const slot = url.searchParams.get("slot");
  const token = url.searchParams.get("token");
  return (slot === "a" || slot === "b") && token === tokens[slot] ? slot : null;
}

function validateSkillReady(value, slot) {
  if (!record(value)) throw new Error("ready capture must be an object");
  for (const field of ["status", "clientId", "userId", "sessionPath", "preference"] ) {
    if (typeof value[field] !== "string" || !value[field]) {
      throw new Error(`ready.${field} is required`);
    }
  }
  if (value.status !== "authenticated") throw new Error(`${slot} must be authenticated`);
  if (
    !record(value.model) ||
    typeof value.model.provider !== "string" ||
    !value.model.provider ||
    typeof value.model.id !== "string" ||
    !value.model.id
  ) {
    throw new Error(`ready.model must identify slot ${slot}'s selected Pi model`);
  }
  return { ...value, model: { provider: value.model.provider, id: value.model.id } };
}

function validateLogout(value) {
  if (
    !record(value) ||
    value.logoutHttpStatus !== 200 ||
    value.oldClientHttpStatus !== 404
  ) {
    throw new Error("A logout must revoke its old Browser Client");
  }
  return { ...value, observedAt: new Date().toISOString() };
}

function finalizeSkillCapture(evidencePath, evidence, run) {
  const a = run.raw.a;
  const b = run.raw.b;
  if (!a || !b || !run.bObservedHeld || !run.aLogout || !run.bComplete) {
    throw new Error("capture incomplete");
  }
  if (a.userId !== b.userId) throw new Error("A and B are not the same canonical User");
  if (a.clientId === b.clientId) throw new Error("A and B reused one Browser Client");
  if (a.model.provider !== b.model.provider || a.model.id !== b.model.id) {
    throw new Error("A and B did not use the same selected Pi model");
  }
  if (resolve(a.sessionPath) !== resolve(b.sessionPath)) {
    throw new Error("A and B did not share one Agent Session");
  }
  const providerPath = configuredProviderPath();
  const proofs = [
    ["aBefore", evidence.markers.aBefore, "success"],
    ["aAfter", evidence.markers.aAfter, "authentication_required"],
    ["bAfter", evidence.markers.bAfter, "success"],
  ].map(([field, markerValue, expected]) => ({
    field,
    proof: verifyTranscript(a.sessionPath, markerValue, expected, providerPath),
  }));
  if (proofs.some(({ proof }, index) => proof.outcome !== ["success", "authentication_required", "success"][index])) {
    throw new Error("live Pi transcript did not contain the required Skill outcomes");
  }
  const [aBefore, aAfter, bAfter] = proofs.map(item => item.proof);
  const logoutAt = run.aLogout.observedAt;
  if (!(Date.parse(aAfter.questionCallAt) < Date.parse(logoutAt) && Date.parse(logoutAt) < Date.parse(aAfter.questionResultAt))) {
    throw new Error("A logout was not between held question presentation and answer");
  }
  evidence.completedAt = new Date().toISOString();
  evidence.observations.identity = {
    aStatus: a.status,
    bStatus: b.status,
    aClientFingerprint: sha256(a.clientId),
    aAfterClientFingerprint: sha256(a.clientId),
    bClientFingerprint: sha256(b.clientId),
    aUserIdFingerprint: sha256(a.userId),
    bUserIdFingerprint: sha256(b.userId),
    aPreference: a.preference,
    bPreference: b.preference,
  };
  evidence.observations.sharedRuntime = {
    aSessionFingerprint: sha256(resolve(a.sessionPath)),
    bSessionFingerprint: sha256(resolve(b.sessionPath)),
    bSwitchStatus: "succeeded",
  };
  evidence.observations.expectedAnswerSurfaces = {
    aBeforeBrowser: "codex-in-app-browser",
    aAfterBrowser: "chrome",
    bAfterBrowser: "chrome",
  };
  evidence.observations.sequence = {
    aBeforeAcceptedAt: aBefore.userAt,
    aBeforeQuestionCallAt: aBefore.questionCallAt,
    aBeforeQuestionAnsweredAt: aBefore.questionResultAt,
    aBeforeResultAt: aBefore.providerResultAt,
    aAfterAcceptedAt: aAfter.userAt,
    aAfterQuestionCallAt: aAfter.questionCallAt,
    logoutAt,
    logoutHttpStatus: run.aLogout.logoutHttpStatus,
    aOldClientHttpStatus: run.aLogout.oldClientHttpStatus,
    bAfterLogoutStatus: b.status,
    aAfterQuestionAnsweredAt: aAfter.questionResultAt,
    aAfterResultAt: aAfter.providerResultAt,
    bAfterAcceptedAt: bAfter.userAt,
    bAfterQuestionCallAt: bAfter.questionCallAt,
    bAfterQuestionAnsweredAt: bAfter.questionResultAt,
    bAfterResultAt: bAfter.providerResultAt,
  };
  const raw = `${JSON.stringify(evidence, null, 2)}\n`;
  const errors = verifyEvidence(evidence, raw, providerPath, a.sessionPath);
  if (errors.length > 0) throw new Error(errors.join("\n"));
  writeFileSync(evidencePath, raw, { encoding: "utf8", flag: "wx", mode: 0o600 });
}

async function requestJson(request) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > 64 * 1024) throw new Error("capture request is too large");
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function cors(response, origin) {
  response.setHeader("Access-Control-Allow-Origin", origin);
  response.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  response.setHeader("Access-Control-Allow-Headers", "Content-Type");
  response.setHeader("Vary", "Origin");
}

function json(response, status, value, origin) {
  cors(response, origin);
  response.statusCode = status;
  response.setHeader("Content-Type", "application/json; charset=utf-8");
  response.setHeader("Cache-Control", "no-store");
  response.end(`${JSON.stringify(value)}\n`);
}

function emptySequence() {
  return {
    aBeforeAcceptedAt: null,
    aBeforeQuestionCallAt: null,
    aBeforeQuestionAnsweredAt: null,
    aBeforeResultAt: null,
    aAfterAcceptedAt: null,
    aAfterQuestionCallAt: null,
    logoutAt: null,
    logoutHttpStatus: null,
    aOldClientHttpStatus: null,
    bAfterLogoutStatus: null,
    aAfterQuestionAnsweredAt: null,
    aAfterResultAt: null,
    bAfterAcceptedAt: null,
    bAfterQuestionCallAt: null,
    bAfterQuestionAnsweredAt: null,
    bAfterResultAt: null,
  };
}

function auditGate(evidencePath) {
  const raw = readFileSync(evidencePath, "utf8");
  const evidence = JSON.parse(raw);
  const errors = verifyEvidence(
    evidence,
    raw,
    configuredProviderPath(),
    requiredEnvironment("DANO_PROVIDER_GATE_SESSION"),
  );
  if (errors.length > 0) throw new Error(errors.join("\n"));
  console.log(
    "AUDIT ONLY (NOT LIVE COLLECTOR PASS): redacted Skill record and Pi transcript structure are valid, but offline files cannot prove a live run or browser surface provenance",
  );
}

function verifyEvidence(value, raw, providerPath, sessionPath) {
  const errors = [];
  if (!record(value)) return ["evidence must be a JSON object"];
  if (FORBIDDEN_EVIDENCE.test(raw)) {
    errors.push("evidence contains forbidden provider or credential data");
  }
  equal(value.schemaVersion, 2, "schemaVersion", errors);
  equal(
    value.recordPurpose,
    "redacted-audit-only-not-live-proof",
    "recordPurpose",
    errors,
  );
  match(value.runId, /^[0-9a-f-]{36}$/i, "runId", errors);
  isoDate(value.preparedAt, "preparedAt", errors);
  isoDate(value.completedAt, "completedAt", errors);
  equal(
    value.capture?.expectedBrowserContexts?.a,
    "codex-in-app-browser",
    "capture.expectedBrowserContexts.a",
    errors,
  );
  equal(
    value.capture?.expectedBrowserContexts?.b,
    "chrome",
    "capture.expectedBrowserContexts.b",
    errors,
  );
  equal(
    value.capture?.callbackMode,
    "single-shared-dano-callback",
    "capture.callbackMode",
    errors,
  );
  equal(
    value.capture?.seam,
    "Dano HTTP/SSE and Pi transcript",
    "capture.seam",
    errors,
  );

  const markers = value.markers;
  for (const field of ["aBefore", "aAfter", "bAfter"]) {
    if (!validMarker(markers?.[field])) errors.push(`markers.${field} is invalid`);
  }
  if (new Set([markers?.aBefore, markers?.aAfter, markers?.bAfter]).size !== 3) {
    errors.push("Skill markers must be distinct");
  }
  match(
    markers?.sharedPreference,
    /^yellow$/,
    "markers.sharedPreference",
    errors,
  );

  const identity = value.observations?.identity;
  equal(identity?.aStatus, "authenticated", "identity.aStatus", errors);
  equal(identity?.bStatus, "authenticated", "identity.bStatus", errors);
  match(identity?.aClientFingerprint, SHA256, "identity.aClientFingerprint", errors);
  match(
    identity?.aAfterClientFingerprint,
    SHA256,
    "identity.aAfterClientFingerprint",
    errors,
  );
  match(identity?.bClientFingerprint, SHA256, "identity.bClientFingerprint", errors);
  equal(
    identity?.aClientFingerprint,
    identity?.aAfterClientFingerprint,
    "A-before and A-after must originate from the same Browser Client",
    errors,
  );
  if (identity?.aClientFingerprint === identity?.bClientFingerprint) {
    errors.push("A and B must use different Browser Clients");
  }
  match(
    identity?.aUserIdFingerprint,
    SHA256,
    "identity.aUserIdFingerprint",
    errors,
  );
  match(
    identity?.bUserIdFingerprint,
    SHA256,
    "identity.bUserIdFingerprint",
    errors,
  );
  equal(
    identity?.aUserIdFingerprint,
    identity?.bUserIdFingerprint,
    "A and B must resolve to the same canonical User ID fingerprint",
    errors,
  );
  equal(identity?.aPreference, markers?.sharedPreference, "identity.aPreference", errors);
  equal(identity?.bPreference, markers?.sharedPreference, "identity.bPreference", errors);

  const runtime = value.observations?.sharedRuntime;
  match(runtime?.aSessionFingerprint, SHA256, "sharedRuntime.aSessionFingerprint", errors);
  match(runtime?.bSessionFingerprint, SHA256, "sharedRuntime.bSessionFingerprint", errors);
  equal(
    runtime?.aSessionFingerprint,
    runtime?.bSessionFingerprint,
    "A and B must observe the same Agent Session fingerprint",
    errors,
  );
  equal(runtime?.bSwitchStatus, "succeeded", "sharedRuntime.bSwitchStatus", errors);

  const expectedAnswerSurfaces = value.observations?.expectedAnswerSurfaces;
  equal(
    expectedAnswerSurfaces?.aBeforeBrowser,
    "codex-in-app-browser",
    "expectedAnswerSurfaces.aBeforeBrowser",
    errors,
  );
  equal(
    expectedAnswerSurfaces?.aAfterBrowser,
    "chrome",
    "expectedAnswerSurfaces.aAfterBrowser",
    errors,
  );
  equal(
    expectedAnswerSurfaces?.bAfterBrowser,
    "chrome",
    "expectedAnswerSurfaces.bAfterBrowser",
    errors,
  );

  verifySequence(value.observations?.sequence, errors);
  equal(
    sha256(resolve(sessionPath)),
    runtime?.aSessionFingerprint,
    "DANO_PROVIDER_GATE_SESSION must be the shared Agent Session",
    errors,
  );
  const phaseProofs = [];
  for (const [field, markerField, expected] of [
    ["aBefore", "aBefore", "success"],
    ["aAfter", "aAfter", "authentication_required"],
    ["bAfter", "bAfter", "success"],
  ]) {
    const proof = verifyTranscript(
      sessionPath,
      markers?.[markerField],
      expected,
      providerPath,
    );
    if (proof.outcome !== expected) {
      errors.push(`${field} transcript phase produced ${proof.outcome}`);
    } else {
      phaseProofs.push({ field, proof });
    }
  }
  verifyTranscriptTimeline(phaseProofs, value.observations?.sequence, errors);
  return errors;
}

function verifyTranscriptTimeline(phases, sequence, errors) {
  if (phases.length !== 3) return;
  const [aBefore, aAfter, bAfter] = phases.map(phase => phase.proof);
  if (!(aBefore.startIndex < aAfter.startIndex && aAfter.startIndex < bAfter.startIndex)) {
    errors.push("transcript phases must be ordered A-before, held A-after, B-after");
  }
  for (const [actual, field] of [
    [aBefore.userAt, "aBeforeAcceptedAt"],
    [aBefore.questionCallAt, "aBeforeQuestionCallAt"],
    [aBefore.questionResultAt, "aBeforeQuestionAnsweredAt"],
    [aBefore.providerResultAt, "aBeforeResultAt"],
    [aAfter.userAt, "aAfterAcceptedAt"],
    [aAfter.questionCallAt, "aAfterQuestionCallAt"],
    [aAfter.questionResultAt, "aAfterQuestionAnsweredAt"],
    [aAfter.providerResultAt, "aAfterResultAt"],
    [bAfter.userAt, "bAfterAcceptedAt"],
    [bAfter.questionCallAt, "bAfterQuestionCallAt"],
    [bAfter.questionResultAt, "bAfterQuestionAnsweredAt"],
    [bAfter.providerResultAt, "bAfterResultAt"],
  ]) {
    equal(actual, sequence?.[field], `sequence.${field} must match Pi transcript`, errors);
  }
  const logout = Date.parse(sequence?.logoutAt ?? "");
  const presented = Date.parse(aAfter.questionCallAt);
  const answered = Date.parse(aAfter.questionResultAt);
  if (!(presented < logout && logout < answered)) {
    errors.push("logoutAt must be after A's question call and before B answers it");
  }
}

function verifySequence(sequence, errors) {
  const timeFields = [
    "aBeforeAcceptedAt",
    "aBeforeQuestionCallAt",
    "aBeforeQuestionAnsweredAt",
    "aBeforeResultAt",
    "aAfterAcceptedAt",
    "aAfterQuestionCallAt",
    "logoutAt",
    "aAfterQuestionAnsweredAt",
    "aAfterResultAt",
    "bAfterAcceptedAt",
    "bAfterQuestionCallAt",
    "bAfterQuestionAnsweredAt",
    "bAfterResultAt",
  ];
  for (const field of timeFields) isoDate(sequence?.[field], `sequence.${field}`, errors);
  const timestamps = timeFields.map(field => Date.parse(sequence?.[field] ?? ""));
  if (
    timestamps.every(Number.isFinite) &&
    timestamps.some((value, index) => index > 0 && value <= timestamps[index - 1])
  ) {
    errors.push("release-gate timestamps must be monotonic");
  }
  equal(sequence?.logoutHttpStatus, 200, "sequence.logoutHttpStatus", errors);
  equal(sequence?.aOldClientHttpStatus, 404, "sequence.aOldClientHttpStatus", errors);
  equal(
    sequence?.bAfterLogoutStatus,
    "authenticated",
    "sequence.bAfterLogoutStatus",
    errors,
  );
}

function verifyTranscript(sessionPath, markerValue, expected, providerPath) {
  const files = transcriptFiles(sessionPath);
  if (files.length !== 1) return { outcome: "invalid" };
  for (const file of files) {
    const entries = readEntries(file);
    const start = entries.findIndex(entry => {
      const message = messageOf(entry);
      const text = textOf(message?.content);
      return (
        message?.role === "user" &&
        text.includes(markerValue) &&
        (text.includes(`skill name="${skillName}"`) ||
          text.includes(`/skill:${skillName}`))
      );
    });
    if (start < 0) continue;
    const nextUser = entries.findIndex(
      (entry, index) => index > start && messageOf(entry)?.role === "user",
    );
    const entriesForTurn = entries.slice(
      start + 1,
      nextUser < 0 ? entries.length : nextUser,
    );
    const execution = turnExecution(entriesForTurn, markerValue, providerPath);
    if (!execution) return { outcome: "invalid" };
    const proof = {
      outcome: providerOutcome(execution.providerResult),
      startIndex: start,
      userAt: isoTimestamp(messageOf(entries[start])),
      questionCallAt: execution.questionCallAt,
      questionResultAt: execution.questionResultAt,
      providerResultAt: execution.providerResultAt,
    };
    return Object.values(proof).some(value => value === null)
      ? { outcome: "invalid" }
      : proof;
  }
  return { outcome: "missing_skill_invocation" };
}

function turnExecution(entries, markerValue, providerPath) {
  const calls = entries.flatMap((entry, index) =>
    toolCalls(messageOf(entry)).map(call => ({ call, index })),
  );
  const allowedCalls = calls.filter(({ call }) =>
    call.name === "read"
      ? typeof (call.arguments ?? call.args)?.path === "string" &&
        (call.arguments ?? call.args).path.endsWith(
          `/skills/${skillName}/SKILL.md`,
        )
      : call.name === "ask_user_question" || call.name === "provider_request",
  );
  if (allowedCalls.length !== calls.length) return null;
  const relevantCalls = calls.filter(
    ({ call }) =>
      call.name === "ask_user_question" || call.name === "provider_request",
  );
  if (
    relevantCalls.length !== 2 ||
    relevantCalls[0]?.call.name !== "ask_user_question" ||
    relevantCalls[1]?.call.name !== "provider_request"
  ) return null;
  const questionCall = relevantCalls[0].call;
  const questionArguments = questionCall.arguments ?? questionCall.args;
  const questionCard = normalizeQuestionCard(questionArguments);
  if (
    !record(questionArguments) ||
    Object.keys(questionArguments).length !== 5 ||
    !isDeepStrictEqual(questionCard, {
      batch: false,
      id: "answer",
      kind: "single",
      question: `Continue provider release gate ${markerValue}?`,
      options: [
        { id: "continue", label: "Continue" },
        { id: "stop", label: "Stop" },
      ],
      required: true,
      default: "continue",
    })
  ) return null;
  const providerArguments =
    relevantCalls[1].call.arguments ?? relevantCalls[1].call.args;
  if (
    !record(providerArguments) ||
    Object.keys(providerArguments).length !== 2 ||
    providerArguments.method !== "GET" ||
    providerArguments.path !== providerPath
  ) return null;

  const questionResult = matchingResult(
    entries,
    questionCall.id,
    "ask_user_question",
  );
  const providerResult = matchingResult(
    entries,
    relevantCalls[1].call.id,
    "provider_request",
  );
  if (
    !questionResult ||
    questionResult.index <= relevantCalls[0].index ||
    questionResult.index >= relevantCalls[1].index ||
    questionResult.message.isError === true ||
    questionResult.message.details?.status !== "answered" ||
    questionResult.message.details?.answer !== "continue" ||
    !providerResult ||
    providerResult.index <= relevantCalls[1].index
  ) return null;
  return {
    providerResult: providerResult.message,
    questionCallAt: isoTimestamp(messageOf(entries[relevantCalls[0].index])),
    questionResultAt: isoTimestamp(questionResult.message),
    providerResultAt: isoTimestamp(providerResult.message),
  };
}

function normalizeQuestionCard(argumentsValue) {
  normalizeAskUserQuestionCardRequest ??= jiti(
    "../apps/dano/src/bridge/ask-user-question.ts",
  ).normalizeAskUserQuestionCardRequest;
  return normalizeAskUserQuestionCardRequest(argumentsValue);
}

function isoTimestamp(message) {
  const value = message?.timestamp;
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return new Date(value).toISOString();
}

function transcriptFiles(sessionPath) {
  if (!existsSync(sessionPath)) return [];
  return !statSync(sessionPath).isDirectory() && sessionPath.endsWith(".jsonl")
    ? [sessionPath]
    : [];
}

function readEntries(file) {
  return readFileSync(file, "utf8")
    .split(/\r?\n/)
    .filter(Boolean)
    .flatMap(line => {
      try {
        return [JSON.parse(line)];
      } catch {
        return [];
      }
    });
}

function toolCalls(message) {
  if (!message) return [];
  if (message.type === "toolCall") return [message];
  if (message.role !== "assistant" || !Array.isArray(message.content)) return [];
  return message.content.filter(block => block?.type === "toolCall");
}

function matchingResult(entries, callId, toolName) {
  if (typeof callId !== "string") return null;
  const matches = [];
  for (const [index, entry] of entries.entries()) {
    const message = messageOf(entry);
    if (
      message?.role === "toolResult" &&
      message.toolCallId === callId &&
      message.toolName === toolName
    ) matches.push({ message, index });
  }
  return matches.length === 1 ? matches[0] : null;
}

function providerOutcome(message) {
  const details = record(message.details)
    ? message.details
    : parseObject(textOf(message.content));
  if (
    details?.ok === true &&
    Number.isInteger(details.status) &&
    details.status >= 200 &&
    details.status < 300
  ) return "success";
  return details?.ok === false &&
    details.error?.code === "authentication_required"
    ? "authentication_required"
    : "unexpected_result";
}

function parseObject(value) {
  try {
    const parsed = JSON.parse(value);
    return record(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function messageOf(entry) {
  return entry?.type === "message" && entry.message ? entry.message : entry;
}

function textOf(value) {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(textOf).join("\n");
  if (record(value)) return Object.values(value).map(textOf).join("\n");
  return "";
}

function configuredProviderPath() {
  return relativeProviderPath(requiredEnvironment("DANO_PROVIDER_ACCEPTANCE_PATH"));
}

function relativeProviderPath(value) {
  if (!value.startsWith("/") || value.startsWith("//")) {
    throw new Error("DANO_PROVIDER_ACCEPTANCE_PATH must be a relative provider path");
  }
  const parsed = new URL(value, "https://provider.invalid");
  if (parsed.origin !== "https://provider.invalid") {
    throw new Error("DANO_PROVIDER_ACCEPTANCE_PATH must stay on the provider origin");
  }
  return `${parsed.pathname}${parsed.search}`;
}

function marker(phase) {
  return `dano424-${phase}-${randomBytes(10).toString("hex")}`;
}

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function validMarker(value) {
  return typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(value);
}

function record(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function equal(actual, expected, path, errors) {
  if (actual !== expected) errors.push(`${path} must equal ${JSON.stringify(expected)}`);
}

function match(value, pattern, path, errors) {
  if (typeof value !== "string" || !pattern.test(value)) {
    errors.push(`${path} has an invalid value`);
  }
}

function isoDate(value, path, errors) {
  if (typeof value !== "string" || Number.isNaN(Date.parse(value))) {
    errors.push(`${path} must be an ISO date`);
  }
}

function escapeYamlString(value) {
  return value.replaceAll("\\", "\\\\").replaceAll('"', '\\"');
}

function requiredArgument(index) {
  const value = process.argv[index]?.trim();
  if (!value) throw new Error("evidence path is required");
  return value;
}

function requiredEnvironment(name) {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`${name} is required`);
  return value;
}
