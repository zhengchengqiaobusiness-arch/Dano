import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { pathToFileURL } from "node:url";
import { once } from "node:events";
import { createConnection } from "node:net";
import {
  fauxAssistantMessage,
  fauxProvider,
  fauxToolCall,
} from "@earendil-works/pi-ai";
import {
  ModelRuntime,
  SessionManager,
  createAgentSession,
} from "@earendil-works/pi-coding-agent";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createAskUserQuestionRuntime } from "../bridge/ask-user-question.js";
import { CredentialBroker } from "../bridge/credential-broker.js";

const roots: string[] = [];
const children: ChildProcess[] = [];
const repositoryRoot = path.resolve(import.meta.dirname, "../../../..");
const gateScript = path.join(repositoryRoot, "scripts/check-provider-skill-release-gate.mjs");
const browserProducer = path.join(repositoryRoot, "scripts/provider-skill-release-gate-browser.mjs");
const gateEnvironment = { DANO_PROVIDER_ACCEPTANCE_PATH: "/acceptance/profile" };
const startupTimeoutMs = 5_000;
const captureTimeoutMs = 10_000;
const shutdownTimeoutMs = 5_000;
const commandTimeoutMs = 5_000;
const captureTestTimeoutMs = startupTimeoutMs + captureTimeoutMs + shutdownTimeoutMs + commandTimeoutMs;

afterEach(async () => {
  for (const child of children.splice(0)) {
    if (child.exitCode !== null || child.signalCode !== null) continue;
    const exit = once(child, "exit");
    child.kill("SIGTERM");
    try {
      await bounded(exit, 1_000, "cleanup SIGTERM");
    } catch {
      child.kill("SIGKILL");
      await bounded(exit, 1_000, "cleanup SIGKILL");
    }
  }
  for (const root of roots.splice(0)) {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

describe("provider Skill collector protocol with synthetic transcript", () => {
  it("installs a provider-independent Skill that holds each Turn with a public question", () => {
    const root = temporaryRoot();
    const agentDir = path.join(root, "agent");
    expect(runGate("install", { PI_CODING_AGENT_DIR: agentDir }).status).toBe(0);
    const installed = fs.readFileSync(
      path.join(agentDir, "skills/provider-broker-release-gate/SKILL.md"),
      "utf8",
    );
    expect(installed).toContain('path: "/acceptance/profile"');
    expect(installed).not.toMatch(/https?:\/\/|authorization|cookie|token/i);
    expect(installed.indexOf("`ask_user_question`")).toBeLessThan(
      installed.indexOf("`provider_request`"),
    );
    expect(installed).toContain('default: "continue"');
  });

  it("loads the installed Skill in a real Pi Turn and calls provider_request", async () => {
    const root = temporaryRoot();
    const agentDir = path.join(root, "agent");
    const workspace = path.join(root, "workspace");
    fs.mkdirSync(workspace, { recursive: true });
    expect(runGate("install", { PI_CODING_AGENT_DIR: agentDir }).status).toBe(0);
    const modelProvider = fauxProvider({ provider: "provider-skill-release-gate" });
    modelProvider.setResponses([
      fauxAssistantMessage(
        fauxToolCall("ask_user_question", {
          question: "Continue provider release gate gate-a-before?",
          inputType: "radio",
          options: [
            { id: "continue", label: "Continue" },
            { id: "stop", label: "Stop" },
          ],
          required: true,
          default: "continue",
        }, { id: "gate-question" }),
        { stopReason: "toolUse" },
      ),
      fauxAssistantMessage([
        fauxToolCall("provider_request", {
          method: "GET",
          path: gateEnvironment.DANO_PROVIDER_ACCEPTANCE_PATH,
        }),
      ], { stopReason: "toolUse" }),
      fauxAssistantMessage("gate complete"),
    ]);
    const modelRuntime = await ModelRuntime.create({
      authPath: path.join(agentDir, "auth.json"),
      modelsPath: null,
    });
    modelRuntime.registerNativeProvider(modelProvider.provider);
    await modelRuntime.setRuntimeApiKey(modelProvider.provider.id, "test-only");
    const providerFetch = vi.fn(async () => new Response("{}", { status: 200 }));
    const credentialBroker = new CredentialBroker({
      providerApiOrigin: "https://provider.test",
      readCredential: async sessionId =>
        sessionId === "login-a" ? { accessToken: "access-a" } : null,
      fetch: providerFetch as typeof fetch,
    });
    const questionRuntime = createAskUserQuestionRuntime();
    const { session } = await createAgentSession({
      cwd: workspace,
      agentDir,
      modelRuntime,
      model: modelProvider.getModel(),
      thinkingLevel: "off",
      noTools: "builtin",
      customTools: [questionRuntime.tool, credentialBroker.createTool("user-a")],
      sessionManager: SessionManager.create(workspace, path.join(root, "sessions")),
    });
    credentialBroker.observe("user-a", session);
    credentialBroker.queueAssistantTurn("user-a", session.sessionId, "login-a");
    try {
      const turn = session.prompt("/skill:provider-broker-release-gate gate-a-before");
      await waitUntil(() => questionRuntime.coordinator.state("gate-question") !== undefined);
      questionRuntime.coordinator.answer("gate-question", {
        cancelled: false,
        answer: "continue",
      });
      await turn;
      expect(providerFetch).toHaveBeenCalledOnce();
      expect(fs.readFileSync(session.sessionFile!, "utf8")).not.toContain("access-a");
    } finally {
      session.dispose();
    }
  });

  it("ships a browser producer for same-User shared-session logout sequencing", () => {
    const source = fs.readFileSync(browserProducer, "utf8");
    for (const seam of [
      "/api/auth/current",
      "/api/auth/logout",
      "/api/clients",
      "switch_session",
      "answer_question",
      "provider-broker-release-gate",
      "/preferences/theme",
    ]) expect(source).toContain(seam);
    expect(source.match(/type: "switch_session"/g)).toHaveLength(1);
    expect(source).not.toMatch(/client.?secret|access.?token|refresh.?token|cookie/i);
  });

  it("keeps the held question observable and waits for the settled transcript outcome", async () => {
    const imported = await import(
      `${pathToFileURL(browserProducer).href}?unit=${Date.now()}`
    ) as {
      createGateObserver: () => {
        observe(value: unknown): void;
        waitForQuestion(marker: string): Promise<{ id: string }>;
        waitForOutcome(marker: string, expected: string): Promise<true>;
      };
    };
    const observer = imported.createGateObserver();
    const marker = "persistent-viewer-marker";
    observer.observe(transcriptEvent("transcript_upsert", {
      role: "assistant",
      content: [{
        type: "toolCall",
        id: "held-question",
        name: "ask_user_question",
        arguments: { question: `Continue provider release gate ${marker}?` },
      }],
    }));
    for (let index = 0; index < 250; index += 1) {
      observer.observe({ type: "event", payload: { type: "session_stats", index } });
    }
    await expect(observer.waitForQuestion(marker)).resolves.toMatchObject({
      id: "held-question",
    });

    const unsettled = observer.waitForOutcome(marker, "success");
    let settled = false;
    void unsettled.then(() => { settled = true; });
    observer.observe(transcriptEvent("transcript_upsert", {
      role: "toolResult",
      toolCallId: "provider-call",
      toolName: "provider_request",
      details: { ok: true, status: 200 },
    }));
    await new Promise(resolve => setTimeout(resolve, 10));
    expect(settled).toBe(false);

    observer.observe({
      type: "event",
      payload: {
        type: "transcript_snapshot",
        messages: turnMessages(marker, "success"),
      },
    });
    await expect(unsettled).resolves.toBe(true);
  });

  it("validates the collector PASS and redacted audit contract, not real browser acceptance", async () => {
    const fixture = await completeCapture({ sameUser: true });
    expect(fixture.output()).toContain("LIVE HTTP/SSE/Pi COLLECTOR PASS:");
    expect(fixture.output()).toContain("external IAB/Chrome acceptance record");
    const raw = fs.readFileSync(fixture.evidencePath, "utf8");
    const evidence = JSON.parse(raw);
    expect(evidence.recordPurpose).toBe("redacted-audit-only-not-live-proof");
    expect(raw).not.toMatch(/raw-client|shared-session\.jsonl|https?:\/\//i);
    expect(raw).not.toMatch(/password|cookie|authorization|client.?secret|access.?token|refresh.?token/i);

    const audit = runGate(
      "audit",
      { DANO_PROVIDER_GATE_SESSION: fixture.transcriptPath },
      [fixture.evidencePath],
    );
    expect(audit.status).toBe(0);
    expect(audit.stdout).toContain("AUDIT ONLY (NOT LIVE COLLECTOR PASS)");
  }, captureTestTimeoutMs);

  it("accepts the model-compatible question arguments emitted by the live Pi transcript", async () => {
    const fixture = await completeCapture({
      sameUser: true,
      compatibleQuestionArguments: true,
    });

    expect(fixture.output()).toContain("LIVE HTTP/SSE/Pi COLLECTOR PASS:");
  }, captureTestTimeoutMs);

  it("exits after capture completion even with an unfinished HTTP connection", async () => {
    const fixture = await completeCapture({ sameUser: true, unfinishedConnection: true });
    expect(fixture.output()).toContain("COLLECTOR PASS:");
    const timing = fixture.error().split("\n").find(line => line.includes('"kind":"provider-gate-timings"'));
    expect(JSON.parse(timing!)).toMatchObject({ kind: "provider-gate-timings" });
  }, captureTestTimeoutMs);

  it("bounds capture timeout cleanup and does not emit PASS for an incomplete run", async () => {
    const root = temporaryRoot();
    const capture = await startCapture(path.join(root, "evidence.json"), 1_000);
    const result = await bounded(capture.exit, shutdownTimeoutMs, "incomplete capture exit");
    expect(result.code).not.toBe(0);
    expect(capture.error()).toContain("provider Skill capture timed out");
    expect(capture.error()).toContain('"kind":"provider-gate-timings"');
    expect(capture.output()).not.toContain("COLLECTOR PASS:");
  }, startupTimeoutMs + shutdownTimeoutMs);

  it("rejects different Users before writing a live audit record", async () => {
    const fixture = await completeCapture({ sameUser: false, expectFailure: true });
    expect(fixture.output()).not.toContain("COLLECTOR PASS:");
    expect(fixture.error()).toContain("same canonical User");
    expect(fs.existsSync(fixture.evidencePath)).toBe(false);
  }, captureTestTimeoutMs);

  it("rejects different real runner models before writing a live audit record", async () => {
    const fixture = await completeCapture({
      sameUser: true,
      differentModel: true,
      expectFailure: true,
    });
    expect(fixture.output()).not.toContain("COLLECTOR PASS:");
    expect(fixture.error()).toContain("same selected Pi model");
    expect(fs.existsSync(fixture.evidencePath)).toBe(false);
  }, captureTestTimeoutMs);

  it("does not let A logout before B's persistent viewer observed the held question", async () => {
    const fixture = await completeCapture({
      sameUser: true,
      probeEarlyLogout: true,
    });
    expect(fixture.earlyLogoutStatus).toBe(400);
    expect(fixture.output()).toContain("LIVE HTTP/SSE/Pi COLLECTOR PASS:");
  }, captureTestTimeoutMs);

  it("refuses prepare and offline verify instead of reconstructing a live collector PASS", () => {
    const root = temporaryRoot();
    const evidencePath = path.join(root, "evidence.json");
    for (const mode of ["prepare", "verify"]) {
      const result = runGate(mode, {}, [evidencePath]);
      expect(result.status).toBe(1);
      expect(result.stderr).toContain("cannot prove a live HTTP/SSE/Pi Skill collector run or browser surface provenance");
      expect(result.stdout).not.toContain("COLLECTOR PASS");
    }
  });
});

async function completeCapture(options: {
  sameUser: boolean;
  differentModel?: boolean;
  compatibleQuestionArguments?: boolean;
  probeEarlyLogout?: boolean;
  expectFailure?: boolean;
  unfinishedConnection?: boolean;
}) {
  const root = temporaryRoot();
  const evidencePath = path.join(root, "evidence.json");
  const transcriptPath = path.join(root, "shared-session.jsonl");
  const started = performance.now();
  const capture = await startCapture(evidencePath);
  const config = await collectorJson(capture.urls.a, "/config");
  writeTranscript(
    transcriptPath,
    config.markers,
    options.compatibleQuestionArguments,
  );
  await post(capture.urls.a, "/ready", {
    status: "authenticated",
    clientId: "raw-client-a",
    userId: "same-user",
    sessionPath: transcriptPath,
    preference: config.sharedPreference,
    model: {
      provider: "real-provider",
      id: options.differentModel ? "different-model" : "real-model",
    },
  });
  await post(capture.urls.b, "/ready", {
    status: "authenticated",
    clientId: "raw-client-b",
    userId: options.sameUser ? "same-user" : "different-user",
    sessionPath: transcriptPath,
    preference: config.sharedPreference,
    model: { provider: "real-provider", id: "real-model" },
  });
  await post(capture.urls.a, "/a-held", {});
  const earlyLogoutStatus = options.probeEarlyLogout
    ? await postStatus(capture.urls.a, "/a-logout", {
        logoutHttpStatus: 200,
        oldClientHttpStatus: 404,
      })
    : undefined;
  await post(capture.urls.b, "/b-observed-held", {});
  await post(capture.urls.a, "/a-logout", {
    logoutHttpStatus: 200,
    oldClientHttpStatus: 404,
  });
  const socket = options.unfinishedConnection
    ? createConnection({ host: capture.urls.a.hostname, port: Number(capture.urls.a.port) })
    : undefined;
  if (socket) {
    await once(socket, "connect");
    socket.write("GET /unfinished HTTP/1.1\r\nHost: localhost\r\n");
  }
  let result;
  try {
    await post(capture.urls.b, "/b-complete", { status: "authenticated" });
    if (options.unfinishedConnection) {
      const deadline = performance.now() + captureTimeoutMs;
      while (!capture.error().includes('"phase":"shutdown"')) {
        if (performance.now() > deadline) throw new Error("capture validation did not reach shutdown");
        await new Promise(resolve => setTimeout(resolve, 10));
      }
    }
    result = await bounded(capture.exit, options.unfinishedConnection ? shutdownTimeoutMs : captureTimeoutMs + shutdownTimeoutMs,
      "capture exit after b-complete");
  } catch (error) {
    const timing = capture.error().split("\n").find(line => line.includes('"kind":"provider-gate-timings"'));
    throw new Error(`collector completion failed after ${Math.round(performance.now() - started)}ms (startup=${capture.startupMs}ms, timing=${timing ?? "pending"}): ${error instanceof Error ? error.message : "unknown failure"}`);
  } finally {
    socket?.destroy();
  }
  if (options.expectFailure) expect(result.code).not.toBe(0);
  else expect(result.code).toBe(0);
  return { ...capture, evidencePath, transcriptPath, earlyLogoutStatus };
}

function transcriptEvent(type: string, message: unknown) {
  return { type: "event", payload: { type, message } };
}

function turnMessages(
  marker: string,
  outcome: "success" | "authentication_required",
) {
  return turnEntries(marker, outcome, [0, 1, 2, 3, 4], Date.now())
    .map(entry => entry.message);
}

async function startCapture(evidencePath: string, timeoutMs = captureTimeoutMs) {
  const started = performance.now();
  const child = spawn(
    process.execPath,
    [gateScript, "capture", evidencePath, "--port", "0", "--timeout-ms", String(timeoutMs)],
    {
      cwd: repositoryRoot,
      env: {
        ...process.env,
        ...gateEnvironment,
      },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  children.push(child);
  const exit = once(child, "close").then(([code]) => ({ code }));
  // Register before readiness; an early spawn error must not become unhandled.
  void exit.catch(() => {});
  let stdout = "";
  let stderr = "";
  child.stdout!.setEncoding("utf8");
  child.stderr!.setEncoding("utf8");
  child.stdout!.on("data", chunk => { stdout += chunk; });
  child.stderr!.on("data", chunk => { stderr += chunk; });
  const deadline = performance.now() + startupTimeoutMs;
  while (!stdout.includes("SLOT_B=")) {
    if (child.exitCode !== null || child.signalCode !== null) throw new Error("collector startup: child exited before readiness");
    if (performance.now() > deadline) throw new Error(`collector startup exceeded ${startupTimeoutMs}ms`);
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  const url = (name: "A" | "B") => new URL(
    new RegExp(`SLOT_${name}=(http[^\\s]+)`).exec(stdout)![1],
  );
  return {
    urls: { a: url("A"), b: url("B") },
    output: () => stdout,
    error: () => stderr,
    exit,
    startupMs: Math.round(performance.now() - started),
  };
}

function writeTranscript(
  transcriptPath: string,
  markers: Record<string, string>,
  compatibleQuestionArguments = false,
) {
  const now = Date.now();
  const offsets = [
    ...turnEntries(markers.aBefore, "success", [-10_000, -9_800, -9_600, -9_400, -9_200], now, compatibleQuestionArguments),
    ...turnEntries(markers.aAfter, "authentication_required", [-7_000, -6_000, 1_000, 1_200, 1_400], now, compatibleQuestionArguments),
    ...turnEntries(markers.bAfter, "success", [2_000, 2_200, 2_400, 2_600, 2_800], now, compatibleQuestionArguments),
  ];
  fs.writeFileSync(
    transcriptPath,
    `${offsets.map(value => JSON.stringify(value)).join("\n")}\n`,
  );
}

function turnEntries(marker: string, outcome: "success" | "authentication_required", offsets: number[], base: number, compatibleQuestionArguments = false) {
  const questionId = `question-${marker}`;
  const providerId = `provider-${marker}`;
  const details = outcome === "success"
    ? { ok: true, status: 200 }
    : { ok: false, error: { code: "authentication_required" } };
  const message = (value: unknown, offset: number) => ({
    type: "message",
    message: { ...(value as object), timestamp: base + offset },
  });
  return [
    message({ role: "user", content: `/skill:provider-broker-release-gate ${marker}` }, offsets[0]),
    message({ role: "assistant", content: [{ type: "toolCall", id: questionId, name: "ask_user_question", arguments: { question: `Continue provider release gate ${marker}?`, inputType: "radio", options: compatibleQuestionArguments ? JSON.stringify([{ id: "continue", label: "Continue" }, { id: "stop", label: "Stop" }]) : [{ id: "continue", label: "Continue" }, { id: "stop", label: "Stop" }], required: compatibleQuestionArguments ? "True" : true, default: "continue" } }] }, offsets[1]),
    message({ role: "toolResult", toolCallId: questionId, toolName: "ask_user_question", content: [{ type: "text", text: "answered" }], details: { status: "answered", answer: "continue" }, isError: false }, offsets[2]),
    message({ role: "assistant", content: [{ type: "toolCall", id: providerId, name: "provider_request", arguments: { method: "GET", path: gateEnvironment.DANO_PROVIDER_ACCEPTANCE_PATH } }] }, offsets[3]),
    message({ role: "toolResult", toolCallId: providerId, toolName: "provider_request", content: [{ type: "text", text: JSON.stringify(details) }], details, isError: false }, offsets[4]),
  ];
}

async function collectorJson(url: URL, route: string) {
  const response = await collectorFetch(url, route, {
    headers: { Origin: "http://localhost:5173" },
  });
  expect(response.status).toBe(200);
  return JSON.parse(response.body);
}

async function post(url: URL, route: string, body: unknown) {
  const response = await collectorFetch(url, route, {
    method: "POST",
    headers: { Origin: "http://localhost:5173", "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  expect(response.status).toBe(202);
}

async function postStatus(url: URL, route: string, body: unknown) {
  const response = await collectorFetch(url, route, {
    method: "POST",
    headers: { Origin: "http://localhost:5173", "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return response.status;
}

async function collectorFetch(url: URL, route: string, init: RequestInit) {
  const started = performance.now();
  try {
    const response = await fetch(`${url.origin}${route}${url.search}`, {
      ...init, signal: AbortSignal.timeout(commandTimeoutMs),
    });
    return { status: response.status, body: await response.text() };
  } catch {
    // Do not include the slot token or raw fetch error URL in diagnostics.
    throw new Error(`collector HTTP ${init.method ?? "GET"} ${route} failed after ${Math.round(performance.now() - started)}ms`);
  }
}

function temporaryRoot() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "dano-provider-gate-"));
  roots.push(root);
  return root;
}

function runGate(mode: string, env: Record<string, string>, args: string[] = []) {
  const started = performance.now();
  const result = spawnSync(process.execPath, [gateScript, mode, ...args], {
    cwd: repositoryRoot,
    env: { ...process.env, ...gateEnvironment, ...env },
    encoding: "utf8",
    timeout: commandTimeoutMs,
    killSignal: "SIGKILL",
  });
  if (result.error) throw new Error(`collector command ${mode} failed after ${Math.round(performance.now() - started)}ms: ${result.error.name}`);
  return result;
}

async function bounded<T>(task: Promise<T>, timeoutMs: number, stage: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([task, new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error(`${stage} exceeded ${timeoutMs}ms`)), timeoutMs);
    })]);
  } finally {
    clearTimeout(timer);
  }
}

async function waitUntil(predicate: () => boolean) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  throw new Error("condition was not reached");
}
