import { createHash, createHmac } from "node:crypto";
import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import * as http from "node:http";
import * as os from "node:os";
import * as path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  DEFAULT_BRIDGE_CONFIG,
  type ClientMessage,
  type ServerMessage,
} from "../bridge/types.js";
import { createJwtUserContextResolver } from "../bridge/user-context.js";
import { startDanoServer, type DanoServerController } from "../server.js";
import type { ProtectedSessionTools } from "../bridge/protected-session-tools.js";

const TEST_JWT_SECRET = "test-secret-that-is-long-enough";
const controllers: DanoServerController[] = [];
const runtimeRoots: string[] = [];

afterEach(async () => {
  await Promise.all(controllers.splice(0).map(controller => controller.stop()));
  for (const root of runtimeRoots.splice(0)) {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

function signUser(userId: string, username: string): string {
  const encode = (value: unknown) =>
    Buffer.from(JSON.stringify(value)).toString("base64url");
  const unsigned = `${encode({ alg: "HS256", typ: "JWT" })}.${encode({
    sub: userId,
    name: username,
    exp: Math.floor(Date.now() / 1000) + 60,
  })}`;
  const signature = createHmac("sha256", TEST_JWT_SECRET)
    .update(unsigned)
    .digest("base64url");
  return `${unsigned}.${signature}`;
}

async function createClient(origin: string, token: string) {
  const response = await fetch(`${origin}/api/clients`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: "{}",
  });
  expect(response.status).toBe(201);
  return (await response.json()) as {
    client: { id: string };
    defaultWorkspacePath: string;
    eventsUrl: string;
    messagesUrl: string;
  };
}

type TestClient = Awaited<ReturnType<typeof createClient>>;
type TestUpload = {
  id: string;
  name: string;
  size: number;
  mimeType: string;
  path: string;
  relativePath: string;
  previewUrl: string;
};

function authenticatedServerSetup(runtimeRoot: string) {
  return {
    config: {
      ...DEFAULT_BRIDGE_CONFIG,
      host: "127.0.0.1",
      port: 0,
      upload: {
        ...DEFAULT_BRIDGE_CONFIG.upload,
        uploadDir: path.join(runtimeRoot, "uploads"),
      },
    },
    resolver: createJwtUserContextResolver({
      runtimeRootPath: runtimeRoot,
      secret: TEST_JWT_SECRET,
    }),
  };
}

async function startAuthenticatedServer(
  setup: ReturnType<typeof authenticatedServerSetup>,
): Promise<{ controller: DanoServerController; origin: string }> {
  const controller = await startDanoServer(setup.config, {
    captureSigint: false,
    userContextResolver: setup.resolver,
  });
  controllers.push(controller);
  const origin = controller.getBridgeUrl();
  if (!origin) throw new Error("Dano test server did not start");
  return { controller, origin };
}

async function uploadProjectFile(
  origin: string,
  client: TestClient,
  token: string,
  name: string,
  content: string,
): Promise<TestUpload> {
  const body = new TextEncoder().encode(content);
  const hash = createHash("sha256").update(body).digest("hex");
  const response = await fetch(
    `${origin}/api/uploads?clientId=${encodeURIComponent(client.client.id)}&name=${encodeURIComponent(name)}&mimeType=text/plain&sha256=${hash}`,
    {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body,
    },
  );
  expect(response.status).toBe(201);
  return (await response.json()) as TestUpload;
}

function waitForResponse(
  url: string,
  token: string,
  correlationId: string,
): { close(): void; ready: Promise<void>; result: Promise<ServerMessage> } {
  let request: http.ClientRequest;
  let markReady: () => void;
  const ready = new Promise<void>(resolve => {
    markReady = resolve;
  });
  const result = new Promise<ServerMessage>((resolve, reject) => {
    let buffer = "";
    const timeout = setTimeout(() => {
      request.destroy();
      reject(new Error(`Timed out waiting for ${correlationId}`));
    }, 2_000);
    request = http.get(
      url,
      { headers: { Authorization: `Bearer ${token}` } },
      response => {
        markReady();
        response.setEncoding("utf8");
        response.on("data", chunk => {
          buffer += chunk;
          let boundary = buffer.indexOf("\n\n");
          while (boundary !== -1) {
            const frame = buffer.slice(0, boundary);
            buffer = buffer.slice(boundary + 2);
            const data = frame
              .split(/\r?\n/)
              .filter(line => line.startsWith("data: "))
              .map(line => line.slice(6))
              .join("\n");
            if (data) {
              const message = JSON.parse(data) as ServerMessage;
              if (
                message.type === "response" &&
                message.payload.id === correlationId
              ) {
                clearTimeout(timeout);
                resolve(message);
                return;
              }
            }
            boundary = buffer.indexOf("\n\n");
          }
        });
        response.on("error", reject);
      },
    );
    request.on("error", reject);
  });
  return { ready, result, close: () => request.destroy() };
}

async function executeCommand(
  origin: string,
  client: Awaited<ReturnType<typeof createClient>>,
  token: string,
  payload: Extract<ClientMessage, { type: "command" }>["payload"],
): Promise<ServerMessage> {
  const correlationId = payload.id;
  if (!correlationId) throw new Error("Test commands require an id");
  const response = waitForResponse(
    `${origin}${client.eventsUrl}`,
    token,
    correlationId,
  );
  try {
    await response.ready;
    const posted = await fetch(`${origin}${client.messagesUrl}`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ type: "command", payload } satisfies ClientMessage),
    });
    expect(posted.status).toBe(202);
    return await response.result;
  } finally {
    response.close();
  }
}

describe("User runtime isolation over HTTP/SSE", () => {
  it("binds protected tools to authenticated HTTP owners and rejects a missing identity resolver", async () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "dano-http-protected-tools-"));
    runtimeRoots.push(root);
    const setup = authenticatedServerSetup(root);
    const owners: string[] = [];
    const protectedToolsForUser = vi.fn(async (context: { user: { id: string }; folderPath: string }): Promise<ProtectedSessionTools> => {
      owners.push(context.user.id);
      const agentDir = path.join(context.folderPath, "trusted-agent");
      fs.mkdirSync(agentDir, { recursive: true });
      return { agentDir, trustedSkillPaths: [], resolveWorker: async workspace => ({
        workspace, assertIsolated: async () => {},
        execute: async () => ({ content: [{ type: "text", text: "isolated fixture" }] }),
      }) };
    });
    await expect(startDanoServer(setup.config, { captureSigint: false, protectedToolsForUser }))
      .rejects.toThrow("PROTECTED_TOOLS_USER_CONTEXT_REQUIRED");
    expect(protectedToolsForUser).not.toHaveBeenCalled();
    const controller = await startDanoServer(setup.config, {
      captureSigint: false, userContextResolver: setup.resolver, protectedToolsForUser,
    });
    controllers.push(controller);
    const origin = controller.getBridgeUrl()!;
    await createClient(origin, signUser("protected-a", "Alice"));
    await createClient(origin, signUser("protected-b", "Bob"));
    expect(owners.sort()).toEqual(["protected-a", "protected-b"]);
  });

  it("assigns each authenticated User an isolated default Runtime Workspace", async () => {
    const runtimeRoot = fs.mkdtempSync(
      path.join(os.tmpdir(), "dano-user-runtime-http-"),
    );
    runtimeRoots.push(runtimeRoot);
    fs.mkdirSync(path.join(runtimeRoot, "legacy-global-workspace"));
    const controller = await startDanoServer(
      {
        ...DEFAULT_BRIDGE_CONFIG,
        host: "127.0.0.1",
        port: 0,
        upload: {
          ...DEFAULT_BRIDGE_CONFIG.upload,
          uploadDir: path.join(runtimeRoot, "uploads"),
        },
      },
      {
        cwd: path.join(runtimeRoot, "legacy-global-workspace"),
        sessionDir: path.join(runtimeRoot, "legacy-global-sessions"),
        captureSigint: false,
        userContextResolver: createJwtUserContextResolver({
          runtimeRootPath: runtimeRoot,
          secret: TEST_JWT_SECRET,
        }),
      },
    );
    controllers.push(controller);
    const origin = controller.getBridgeUrl();
    if (!origin) throw new Error("Dano test server did not start");

    const alice = await createClient(origin, signUser("alice", "Alice"));
    const bob = await createClient(origin, signUser("bob", "Bob"));
    const realRuntimeRoot = fs.realpathSync(runtimeRoot);

    expect(alice.defaultWorkspacePath).toBe(
      path.join(realRuntimeRoot, "users", "alice", "workspaces", "default"),
    );
    expect(bob.defaultWorkspacePath).toBe(
      path.join(realRuntimeRoot, "users", "bob", "workspaces", "default"),
    );
    expect(alice.defaultWorkspacePath).not.toBe(bob.defaultWorkspacePath);
  });

  it("shares Agent Sessions with the same User and rejects another User's Session path", async () => {
    const runtimeRoot = fs.mkdtempSync(
      path.join(os.tmpdir(), "dano-user-session-http-"),
    );
    runtimeRoots.push(runtimeRoot);
    const controller = await startDanoServer(
      {
        ...DEFAULT_BRIDGE_CONFIG,
        host: "127.0.0.1",
        port: 0,
        upload: {
          ...DEFAULT_BRIDGE_CONFIG.upload,
          uploadDir: path.join(runtimeRoot, "uploads"),
        },
      },
      {
        captureSigint: false,
        userContextResolver: createJwtUserContextResolver({
          runtimeRootPath: runtimeRoot,
          secret: TEST_JWT_SECRET,
        }),
      },
    );
    controllers.push(controller);
    const origin = controller.getBridgeUrl();
    if (!origin) throw new Error("Dano test server did not start");
    const aliceToken = signUser("alice-sessions", "Alice");
    const bobToken = signUser("bob-sessions", "Bob");
    const aliceFirst = await createClient(origin, aliceToken);
    const aliceSecond = await createClient(origin, aliceToken);
    const bob = await createClient(origin, bobToken);

    const named = await executeCommand(origin, aliceFirst, aliceToken, {
      id: "alice-name",
      type: "set_session_name",
      name: "Alice private session",
    });
    expect(named.payload).toMatchObject({
      command: "set_session_name",
      success: true,
    });
    const aliceState = await executeCommand(
      origin,
      aliceFirst,
      aliceToken,
      { id: "alice-state", type: "get_state" },
    );
    const aliceSessionPath = (
      aliceState.payload as { data?: { sessionFile?: string } }
    ).data?.sessionFile;
    expect(aliceSessionPath).toBeTruthy();
    fs.writeFileSync(
      aliceSessionPath!,
      `${JSON.stringify({
        type: "session",
        id: "alice-private-session",
        timestamp: "2026-08-11T00:00:00.000Z",
        cwd: aliceFirst.defaultWorkspacePath,
      })}\n`,
      "utf8",
    );
    expect(fs.existsSync(aliceSessionPath!)).toBe(true);

    const shared = await executeCommand(origin, aliceSecond, aliceToken, {
      id: "alice-switch",
      type: "switch_session",
      sessionPath: aliceSessionPath!,
    });
    expect(shared.payload).toMatchObject({
      command: "switch_session",
      success: true,
      data: { sessionPath: aliceSessionPath },
    });

    const rejected = await executeCommand(origin, bob, bobToken, {
      id: "bob-switch",
      type: "switch_session",
      sessionPath: aliceSessionPath!,
    });
    expect(rejected.payload).toMatchObject({
      command: "switch_session",
      success: false,
    });

    const deleteRejected = await executeCommand(origin, bob, bobToken, {
      id: "bob-delete",
      type: "delete_session",
      sessionPath: aliceSessionPath!,
    });
    expect(deleteRejected.payload).toMatchObject({
      command: "delete_session",
      success: false,
    });
    expect(fs.existsSync(aliceSessionPath!)).toBe(true);
  });

  it("restores only the owning User's Agent Sessions after restart", async () => {
    const runtimeRoot = fs.mkdtempSync(
      path.join(os.tmpdir(), "dano-user-restart-http-"),
    );
    runtimeRoots.push(runtimeRoot);
    const config = {
      ...DEFAULT_BRIDGE_CONFIG,
      host: "127.0.0.1",
      port: 0,
      upload: {
        ...DEFAULT_BRIDGE_CONFIG.upload,
        uploadDir: path.join(runtimeRoot, "uploads"),
      },
    };
    const resolver = createJwtUserContextResolver({
      runtimeRootPath: runtimeRoot,
      secret: TEST_JWT_SECRET,
    });
    const aliceToken = signUser("alice-restart", "Alice");
    const bobToken = signUser("bob-restart", "Bob");
    const firstController = await startDanoServer(config, {
      captureSigint: false,
      userContextResolver: resolver,
    });
    controllers.push(firstController);
    const firstOrigin = firstController.getBridgeUrl();
    if (!firstOrigin) throw new Error("Dano test server did not start");
    const firstAlice = await createClient(firstOrigin, aliceToken);
    const firstState = await executeCommand(
      firstOrigin,
      firstAlice,
      aliceToken,
      { id: "restart-state", type: "get_state" },
    );
    const storedSessionPath = (
      firstState.payload as { data?: { sessionFile?: string } }
    ).data?.sessionFile;
    expect(storedSessionPath).toBeTruthy();
    fs.writeFileSync(
      storedSessionPath!,
      `${JSON.stringify({
        type: "session",
        id: "alice-restart-session",
        timestamp: "2026-08-11T00:00:00.000Z",
        cwd: firstAlice.defaultWorkspacePath,
      })}\n`,
      "utf8",
    );
    await firstController.stop();
    controllers.splice(controllers.indexOf(firstController), 1);

    const secondController = await startDanoServer(config, {
      captureSigint: false,
      userContextResolver: resolver,
    });
    controllers.push(secondController);
    const secondOrigin = secondController.getBridgeUrl();
    if (!secondOrigin) throw new Error("Dano test server did not restart");
    const secondAlice = await createClient(secondOrigin, aliceToken);
    const bob = await createClient(secondOrigin, bobToken);
    const restored = await executeCommand(
      secondOrigin,
      secondAlice,
      aliceToken,
      {
        id: "alice-restored-list",
        type: "list_sessions",
        workspacePath: secondAlice.defaultWorkspacePath,
      },
    );
    expect(
      (
        restored.payload as {
          data?: { sessions?: Array<{ path: string }> };
        }
      ).data?.sessions,
    ).toContainEqual(expect.objectContaining({ path: storedSessionPath }));

    const rejected = await executeCommand(secondOrigin, bob, bobToken, {
      id: "bob-forged-list",
      type: "list_sessions",
      workspacePath: secondAlice.defaultWorkspacePath,
    });
    expect(rejected.payload).toMatchObject({
      command: "list_sessions",
      success: false,
    });
  });

  it("rejects another User's Runtime Workspace path", async () => {
    const runtimeRoot = fs.mkdtempSync(
      path.join(os.tmpdir(), "dano-user-workspace-http-"),
    );
    runtimeRoots.push(runtimeRoot);
    const controller = await startDanoServer(
      {
        ...DEFAULT_BRIDGE_CONFIG,
        host: "127.0.0.1",
        port: 0,
        upload: {
          ...DEFAULT_BRIDGE_CONFIG.upload,
          uploadDir: path.join(runtimeRoot, "uploads"),
        },
      },
      {
        captureSigint: false,
        userContextResolver: createJwtUserContextResolver({
          runtimeRootPath: runtimeRoot,
          secret: TEST_JWT_SECRET,
        }),
      },
    );
    controllers.push(controller);
    const origin = controller.getBridgeUrl();
    if (!origin) throw new Error("Dano test server did not start");
    const aliceToken = signUser("alice-workspace", "Alice");
    const bobToken = signUser("bob-workspace", "Bob");
    const alice = await createClient(origin, aliceToken);
    const bob = await createClient(origin, bobToken);
    fs.writeFileSync(
      path.join(alice.defaultWorkspacePath, "alice-secret.txt"),
      "Alice only",
      "utf8",
    );
    execFileSync("git", ["init", "-b", "main"], {
      cwd: alice.defaultWorkspacePath,
      stdio: "ignore",
    });

    const aliceGit = await executeCommand(origin, alice, aliceToken, {
      id: "alice-git",
      type: "list_git_branches",
    });
    expect(aliceGit.payload).toMatchObject({
      command: "list_git_branches",
      success: true,
    });
    const bobGit = await executeCommand(origin, bob, bobToken, {
      id: "bob-git",
      type: "list_git_branches",
    });
    expect(bobGit.payload).toMatchObject({
      command: "list_git_branches",
      success: false,
    });

    const listed = await executeCommand(origin, bob, bobToken, {
      id: "bob-forged-workspace-list",
      type: "list_workspace_entries",
      workspacePath: alice.defaultWorkspacePath,
      force: true,
    });
    expect(listed.payload).toMatchObject({
      command: "list_workspace_entries",
      success: false,
    });

    const read = await executeCommand(origin, bob, bobToken, {
      id: "bob-forged-workspace-read",
      type: "read_workspace_file",
      workspacePath: alice.defaultWorkspacePath,
      path: "alice-secret.txt",
    });
    expect(read.payload).toMatchObject({
      command: "read_workspace_file",
      success: false,
    });

    const created = await executeCommand(origin, bob, bobToken, {
      id: "bob-forged-workspace-session",
      type: "new_session",
      workspacePath: alice.defaultWorkspacePath,
    });
    expect(created.payload).toMatchObject({
      command: "new_session",
      success: false,
    });
  });

  it("rejects another User's Uploaded Project File reference", async () => {
    const runtimeRoot = fs.mkdtempSync(
      path.join(os.tmpdir(), "dano-user-upload-http-"),
    );
    runtimeRoots.push(runtimeRoot);
    const { origin } = await startAuthenticatedServer(
      authenticatedServerSetup(runtimeRoot),
    );
    const aliceToken = signUser("alice-upload", "Alice");
    const bobToken = signUser("bob-upload", "Bob");
    const alice = await createClient(origin, aliceToken);
    const bob = await createClient(origin, bobToken);
    const uploaded = await uploadProjectFile(
      origin,
      alice,
      aliceToken,
      "private.txt",
      "Alice private upload",
    );

    const rejected = await executeCommand(origin, bob, bobToken, {
      id: "bob-forged-upload",
      type: "prompt",
      message: "Read this file",
      files: [uploaded],
    });

    expect(rejected.payload).toMatchObject({
      command: "prompt",
      success: false,
    });
    expect(fs.existsSync(path.join(bob.defaultWorkspacePath, uploaded.relativePath))).toBe(
      false,
    );
  });

  it("restores a referenced upload only for the same User and Agent Session", async () => {
    const runtimeRoot = fs.mkdtempSync(
      path.join(os.tmpdir(), "dano-user-upload-restart-http-"),
    );
    runtimeRoots.push(runtimeRoot);
    const setup = authenticatedServerSetup(runtimeRoot);
    const aliceToken = signUser("alice-upload-restart", "Alice");
    const { controller: firstController, origin: firstOrigin } =
      await startAuthenticatedServer(setup);
    const firstAlice = await createClient(firstOrigin, aliceToken);
    const state = await executeCommand(firstOrigin, firstAlice, aliceToken, {
      id: "upload-restart-session",
      type: "new_session",
      workspacePath: firstAlice.defaultWorkspacePath,
    });
    const sessionData = (
      state.payload as {
        data?: { sessionPath?: string; sessionId?: string };
      }
    ).data;
    const sessionPath = sessionData?.sessionPath;
    expect(sessionPath).toBeTruthy();
    const uploaded = await uploadProjectFile(
      firstOrigin,
      firstAlice,
      aliceToken,
      "restart.txt",
      "survives restart",
    );
    const referenced = await executeCommand(firstOrigin, firstAlice, aliceToken, {
      id: "upload-before-restart",
      type: "steer",
      message: "Keep this project file",
      files: [uploaded],
    });
    expect(referenced.payload).toMatchObject({
      command: "steer",
      success: true,
    });
    fs.writeFileSync(
      sessionPath!,
      `${JSON.stringify({
        type: "session",
        id: sessionData?.sessionId,
        timestamp: "2026-08-11T00:00:00.000Z",
        cwd: firstAlice.defaultWorkspacePath,
      })}\n`,
      "utf8",
    );
    await firstController.stop();
    controllers.splice(controllers.indexOf(firstController), 1);

    const { origin: secondOrigin } = await startAuthenticatedServer(setup);
    const secondAlice = await createClient(secondOrigin, aliceToken);
    const switched = await executeCommand(secondOrigin, secondAlice, aliceToken, {
      id: "upload-restart-switch",
      type: "switch_session",
      sessionPath: sessionPath!,
    });
    expect(switched.payload).toMatchObject({
      command: "switch_session",
      success: true,
    });

    const restoredPreview = await fetch(`${secondOrigin}${uploaded.previewUrl}`, {
      headers: { Authorization: `Bearer ${aliceToken}` },
    });
    expect(restoredPreview.status).toBe(200);
    expect(await restoredPreview.text()).toBe("survives restart");
    const repeatedPreview = await fetch(
      `${secondOrigin}${uploaded.previewUrl}`,
      { headers: { Authorization: `Bearer ${aliceToken}` } },
    );
    expect(repeatedPreview.status).toBe(200);
    expect(await repeatedPreview.text()).toBe("survives restart");

    const restored = await executeCommand(secondOrigin, secondAlice, aliceToken, {
      id: "upload-after-restart",
      type: "steer",
      message: "Read the restored project file",
      files: [uploaded],
    });
    expect(restored.payload).toMatchObject({
      command: "steer",
      success: true,
    });
    const restoredDelete = await fetch(
      `${secondOrigin}/api/uploads/${encodeURIComponent(uploaded.id)}/orphan?clientId=${encodeURIComponent(secondAlice.client.id)}`,
      {
        method: "POST",
        headers: { Authorization: `Bearer ${aliceToken}` },
        body: "{}",
      },
    );
    expect(restoredDelete.status).toBe(202);

    const anotherSession = await executeCommand(
      secondOrigin,
      secondAlice,
      aliceToken,
      {
        id: "upload-another-session",
        type: "new_session",
        workspacePath: secondAlice.defaultWorkspacePath,
      },
    );
    expect(anotherSession.payload).toMatchObject({
      command: "new_session",
      success: true,
    });
    const wrongSession = await executeCommand(
      secondOrigin,
      secondAlice,
      aliceToken,
      {
        id: "upload-wrong-session",
        type: "steer",
        message: "Try another Agent Session",
        files: [uploaded],
      },
    );
    expect(wrongSession.payload).toMatchObject({
      command: "steer",
      success: false,
    });
  });

  it("does not let another Client of the same User preview a draft upload", async () => {
    const runtimeRoot = fs.mkdtempSync(
      path.join(os.tmpdir(), "dano-client-upload-http-"),
    );
    runtimeRoots.push(runtimeRoot);
    const { origin } = await startAuthenticatedServer(
      authenticatedServerSetup(runtimeRoot),
    );
    const token = signUser("alice-two-clients", "Alice");
    const owner = await createClient(origin, token);
    const other = await createClient(origin, token);
    const uploaded = await uploadProjectFile(
      origin,
      owner,
      token,
      "draft.txt",
      "client-bound draft",
    );
    const forgedPreviewUrl = new URL(uploaded.previewUrl, origin);
    forgedPreviewUrl.searchParams.set("clientId", other.client.id);

    const ownerPreview = await fetch(`${origin}${uploaded.previewUrl}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    const forgedPreview = await fetch(forgedPreviewUrl, {
      headers: { Authorization: `Bearer ${token}` },
    });

    expect(ownerPreview.status).toBe(200);
    expect(forgedPreview.status).toBe(403);

    const alternateWorkspace = path.join(
      path.dirname(owner.defaultWorkspacePath),
      "alternate",
    );
    fs.mkdirSync(alternateWorkspace);
    const switchedWorkspace = await executeCommand(origin, owner, token, {
      id: "upload-alternate-workspace",
      type: "new_session",
      workspacePath: alternateWorkspace,
    });
    expect(switchedWorkspace.payload).toMatchObject({
      command: "new_session",
      success: true,
    });
    const wrongWorkspace = await executeCommand(origin, owner, token, {
      id: "upload-wrong-workspace",
      type: "steer",
      message: "Try another Runtime Workspace",
      files: [uploaded],
    });
    expect(wrongWorkspace.payload).toMatchObject({
      command: "steer",
      success: false,
    });
  });

  it("isolates concurrent User uploads when identifiers are mixed", async () => {
    const runtimeRoot = fs.mkdtempSync(
      path.join(os.tmpdir(), "dano-concurrent-user-upload-http-"),
    );
    runtimeRoots.push(runtimeRoot);
    const { origin } = await startAuthenticatedServer(
      authenticatedServerSetup(runtimeRoot),
    );
    const aliceToken = signUser("alice-concurrent-upload", "Alice");
    const bobToken = signUser("bob-concurrent-upload", "Bob");
    const [alice, bob] = await Promise.all([
      createClient(origin, aliceToken),
      createClient(origin, bobToken),
    ]);
    const [aliceUpload, bobUpload] = await Promise.all([
      uploadProjectFile(origin, alice, aliceToken, "alice.txt", "alice bytes"),
      uploadProjectFile(origin, bob, bobToken, "bob.txt", "bob bytes"),
    ]);

    const mixedIdAndPath = await executeCommand(origin, bob, bobToken, {
      id: "bob-mixed-upload-ref",
      type: "prompt",
      message: "Try mixed identifiers",
      files: [{ ...aliceUpload, id: bobUpload.id }],
    });
    const forgedPreview = await fetch(
      `${origin}/api/uploads/${encodeURIComponent(aliceUpload.id)}/preview?clientId=${encodeURIComponent(bob.client.id)}`,
      { headers: { Authorization: `Bearer ${bobToken}` } },
    );
    const forgedDelete = await fetch(
      `${origin}/api/uploads/${encodeURIComponent(aliceUpload.id)}/orphan?clientId=${encodeURIComponent(bob.client.id)}`,
      {
        method: "POST",
        headers: { Authorization: `Bearer ${bobToken}` },
        body: "{}",
      },
    );

    expect(mixedIdAndPath.payload).toMatchObject({
      command: "prompt",
      success: false,
    });
    expect(forgedPreview.status).toBe(403);
    expect([403, 404]).toContain(forgedDelete.status);
    expect(fs.existsSync(aliceUpload.path)).toBe(true);
    expect(fs.existsSync(bobUpload.path)).toBe(true);
  });
});
