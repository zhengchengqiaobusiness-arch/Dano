import test from "node:test";
import assert from "node:assert/strict";
import { copyFile, mkdir, mkdtemp, readFile, rm } from "node:fs/promises";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import os from "node:os";
import path from "node:path";
import {
  credentialHeaders,
  materializeSkillCredentials,
  persistOriginCredentials,
  requiredCredentialOrigins,
  skillCredentialFile
} from "../src/credentials/credential-store.js";
import { SkillLibrary } from "../src/catalog/skill-library.js";
import type { CapabilityContract, EvidenceEvent } from "../src/domain.js";

const execFileAsync = promisify(execFile);

test("does not rewrite an unchanged origin credential on every request", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-skill-credential-cache-"));
  try {
    const file = await persistOriginCredentials(temporary, "https://erp.example.test/orders", {
      authorization: "Bearer unchanged"
    });
    assert.ok(file);
    const first = await readFile(file, "utf8");
    await new Promise(resolve => setTimeout(resolve, 20));
    for (let index = 0; index < 20; index += 1) {
      await persistOriginCredentials(temporary, "https://erp.example.test/orders", {
        authorization: "Bearer unchanged"
      });
    }
    assert.equal(await readFile(file, "utf8"), first);
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
});

test("extracts only authentication-related request headers", () => {
  assert.deepEqual(credentialHeaders({
    authorization: "Bearer session-token",
    cookie: "sid=logged-in",
    satoken: "custom-header-token",
    "x-tenant-id": "tenant-7",
    "proxy-authorization": "Basic proxy-only",
    "content-type": "application/json",
    accept: "application/json"
  }), {
    authorization: "Bearer session-token",
    cookie: "sid=logged-in",
    satoken: "custom-header-token",
    "x-tenant-id": "tenant-7"
  });
});

test("materializes a per-skill credential file outside the skill directory", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-skill-credentials-"));
  const dataDir = path.join(temporary, "data");
  const outputRoot = path.join(temporary, ".agents", "skills");
  const skillName = "orders-sk_test";
  try {
    await persistOriginCredentials(dataDir, "https://erp.example.test/orders", {
      authorization: "Bearer first",
      "x-tenant-id": "tenant-7",
      "content-type": "application/json"
    });
    await persistOriginCredentials(dataDir, "https://erp.example.test/orders/1", {
      authorization: "Bearer refreshed"
    });
    const file = await materializeSkillCredentials(dataDir, outputRoot, skillName, ["https://erp.example.test"]);
    assert.equal(file, skillCredentialFile(outputRoot, skillName));
    assert.equal(path.dirname(file!), path.join(temporary, ".agents", "credentials"));
    const profile = JSON.parse(await readFile(file!, "utf8"));
    assert.deepEqual(profile.origins["https://erp.example.test"], {
      authorization: "Bearer refreshed",
      "x-tenant-id": "tenant-7"
    });
    assert.equal(JSON.stringify(profile).includes("content-type"), false);
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
});

test("refuses an authenticated export when its recorded origin credential is missing", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-skill-missing-auth-"));
  try {
    await assert.rejects(
      materializeSkillCredentials(
        path.join(temporary, "data"),
        path.join(temporary, ".agents", "skills"),
        "missing-sk_test",
        ["https://erp.example.test"],
        ["https://erp.example.test"]
      ),
      /没有保存.*运行时凭据/
    );
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
});

test("the exported Python runtime reads its same-name external credential file", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-skill-python-auth-"));
  const dataDir = path.join(temporary, "data");
  const outputRoot = path.join(temporary, ".agents", "skills");
  const skillName = "orders-sk_python";
  const scriptsDir = path.join(outputRoot, skillName, "scripts");
  try {
    await persistOriginCredentials(dataDir, "https://erp.example.test/orders", {
      authorization: "Bearer python-runtime",
      "x-tenant-id": "tenant-9"
    });
    await materializeSkillCredentials(dataDir, outputRoot, skillName, ["https://erp.example.test"]);
    await mkdir(scriptsDir, { recursive: true });
    const script = path.join(scriptsDir, "execute.py");
    await copyFile(path.resolve("src", "export", "python", "execute.py"), script);
    const probe = [
      "import importlib.util, json, sys",
      "spec = importlib.util.spec_from_file_location('skill_execute', sys.argv[1])",
      "module = importlib.util.module_from_spec(spec)",
      "spec.loader.exec_module(module)",
      "print(json.dumps([module.auth_headers('https://erp.example.test/orders'), module.auth_headers('https://erp.example.test:443/orders')], ensure_ascii=False))"
    ].join("; ");
    const { stdout } = await execFileAsync("python", ["-c", probe, script], {
      env: { ...process.env, SKILL_AUTH_HEADERS: "", SKILL_AUTH_FILE: "" }
    });
    assert.deepEqual(JSON.parse(stdout), [
      { Accept: "application/json", authorization: "Bearer python-runtime", "x-tenant-id": "tenant-9" },
      { Accept: "application/json", authorization: "Bearer python-runtime", "x-tenant-id": "tenant-9" }
    ]);
    const disableProbe = probe.replace(
      "print(json.dumps([module.auth_headers('https://erp.example.test/orders'), module.auth_headers('https://erp.example.test:443/orders')], ensure_ascii=False))",
      "print(json.dumps(module.auth_headers('https://erp.example.test/orders'), ensure_ascii=False))"
    );
    const disabled = await execFileAsync("python", ["-c", disableProbe, script], {
      env: { ...process.env, SKILL_AUTH_HEADERS: "{}", SKILL_AUTH_FILE: "" }
    });
    assert.deepEqual(JSON.parse(disabled.stdout), { Accept: "application/json" });
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
});

test("managed export attaches captured credentials without writing them into the Skill", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-skill-managed-auth-"));
  const dataDir = path.join(temporary, "data");
  const outputRoot = path.join(temporary, ".agents", "skills");
  const origin = "https://erp.example.test";
  const event: EvidenceEvent = {
    id: "orders-network",
    kind: "network",
    sessionId: "orders-session",
    at: "2026-09-04T00:00:00.000Z",
    pageUrl: `${origin}/orders`,
    request: { method: "GET", url: `${origin}/orders/page`, resourceType: "xhr", headers: { authorization: "[REDACTED]" }, query: {} },
    response: { status: 200, headers: {}, body: { success: true, data: { list: [], total: 0 } } }
  };
  const capability: CapabilityContract = {
    id: "orders-page",
    kind: "atomic",
    title: "查询订单",
    description: "查询订单列表",
    operation: "query",
    confidence: 1,
    transport: { method: "GET", urlTemplate: `${origin}/orders/page`, origin, pathTemplate: "/orders/page" },
    inputSchema: { type: "object", properties: {} },
    outputSchema: { type: "object", properties: { success: { type: "boolean" }, data: { type: "object" } } },
    inputForm: [],
    evidence: [{ eventId: event.id, sessionId: event.sessionId, kind: "network", at: event.at, status: 200 }],
    sideEffect: false,
    confirmation: { required: false },
    completion: { acceptedHttpStatuses: [200], assertions: [{ path: "$.success", kind: "equals", value: true }] },
    bindings: [],
    validation: { version: 2, status: "verified", checks: [] },
    generated: { source: "heuristic", generatedAt: event.at }
  };
  try {
    await persistOriginCredentials(dataDir, `${origin}/orders/page`, { authorization: "Bearer managed-export" });
    assert.deepEqual(requiredCredentialOrigins([capability], [event]), [origin]);
    const result = await new SkillLibrary(outputRoot, dataDir).export("订单", [capability], true, [event]);
    assert.ok(result.credentialFile);
    const skillText = await readFile(path.join(result.directory, "SKILL.md"), "utf8");
    const contractText = await readFile(path.join(result.directory, "references", "CONTRACT.json"), "utf8");
    assert.equal(skillText.includes("managed-export"), false);
    assert.equal(contractText.includes("managed-export"), false);
    assert.match(await readFile(result.credentialFile!, "utf8"), /managed-export/);
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
});

test("required credentials ignore leftover evidence from another host", () => {
  const capability: CapabilityContract = {
    id: "query-dept",
    kind: "atomic",
    title: "查询部门",
    description: "查询部门",
    operation: "query",
    confidence: 1,
    transport: { method: "GET", urlTemplate: "https://a.example/system/dept/list", origin: "https://a.example", pathTemplate: "/system/dept/list" },
    inputSchema: { type: "object", properties: {} },
    outputSchema: { type: "object", properties: {} },
    inputForm: [],
    evidence: [
      { eventId: "net-a", sessionId: "a", kind: "network", at: "2026-09-05T00:00:00.000Z", status: 200 },
      { eventId: "net-b", sessionId: "b", kind: "network", at: "2026-09-05T00:00:01.000Z", status: 200 }
    ],
    sideEffect: false,
    confirmation: { required: false },
    completion: { acceptedHttpStatuses: [200] },
    bindings: [],
    validation: { version: 2, status: "verified", checks: [] },
    generated: { source: "heuristic", generatedAt: "2026-09-05T00:00:00.000Z" }
  };
  const events: EvidenceEvent[] = [{
    id: "net-a", kind: "network", sessionId: "a", at: "2026-09-05T00:00:00.000Z",
    request: { method: "GET", url: "https://a.example/system/dept/list", resourceType: "xhr", headers: { authorization: "[REDACTED]" }, query: {} },
    response: { status: 200, headers: {}, body: {} }
  }, {
    id: "net-b", kind: "network", sessionId: "b", at: "2026-09-05T00:00:01.000Z",
    request: { method: "GET", url: "http://other.example:90/system/dept/list", resourceType: "xhr", headers: { authorization: "[REDACTED]" }, query: {} },
    response: { status: 200, headers: {}, body: {} }
  }];
  assert.deepEqual(requiredCredentialOrigins([capability], events), ["https://a.example"]);
});
