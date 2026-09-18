import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryProvisioner } from "../memory-provisioner.js";

const owner = { accountId: "company", userId: "alice" };
const admin = { auth_mode: "api_key", role: "admin", account_id: "company" };
const bound = { auth_mode: "api_key", role: "user", account_id: "company", user_id: "alice" };
const user = { user_id: "alice", role: "user", api_key: "synthetic-user-key" };
const ok = (result: unknown) => ({ status: "ok", result });
function setup(replies: unknown[]) {
  const fetcher = vi.fn(async () => {
    const next = replies.shift();
    if (next instanceof Error) throw next;
    if (next === undefined) throw new Error("Unexpected request");
    return Response.json(next);
  });
  vi.stubGlobal("fetch", fetcher);
  const client = new MemoryProvisioner({ baseUrl: "http://localhost:19337", accountId: "company",
    managementKey: "synthetic-admin-key", timeoutMs: 1000 });
  return { client, fetcher };
}
afterEach(() => vi.unstubAllGlobals());

describe("memory management provisioning", () => {
  it("reuses the stable user and verifies its key without mutating remote state", async () => {
    const { client, fetcher } = setup([admin, ok([user]), bound]);
    expect(await client.provision(owner)).toBe(user.api_key);
    const calls = fetcher.mock.calls as unknown as Array<[string, RequestInit]>;
    expect(calls.every(([, init]) => init.method === "GET" && init.redirect === "error")).toBe(true);
    expect(calls.at(-1)?.[1].headers).toEqual({ "X-API-Key": user.api_key });
  });
  it("creates only USER identities then verifies the returned binding", async () => {
    const { client, fetcher } = setup([admin, ok([]), ok({ account_id: "company", user_id: "alice", user_key: user.api_key }), bound]);
    expect(await client.provision(owner)).toBe(user.api_key);
    const calls = fetcher.mock.calls as unknown as Array<[string, RequestInit]>;
    expect(JSON.parse(calls[2]![1].body as string)).toEqual({ user_id: "alice", role: "user" });
  });
  it("reconciles a lost creation response using a read, without repeating registration", async () => {
    const { client, fetcher } = setup([admin, ok([]), new Error("lost response"), ok([user]), bound]);
    expect(await client.provision(owner)).toBe(user.api_key);
    const calls = fetcher.mock.calls as unknown as Array<[string, RequestInit]>;
    expect(calls.filter(([, init]) => init.method === "POST")).toHaveLength(1);
  });
  it("requires explicit recovery for a hashed existing key rather than rotating it", async () => {
    const { client, fetcher } = setup([admin, ok([{ user_id: "alice", role: "user", key_prefix: "prefix" }])]);
    await expect(client.provision(owner)).rejects.toThrow("MEMORY_USER_KEY_RECOVERY_REQUIRED");
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it.each([
    { ...bound, user_id: "bob" }, { ...bound, role: "admin" }, { ...bound, auth_mode: "trusted" },
  ])("rejects a mismatched data credential: %j", async identity => {
    const { client } = setup([admin, ok([user]), identity]);
    await expect(client.provision(owner)).rejects.toThrow("MEMORY_CREDENTIAL_OWNER_MISMATCH");
  });
  it("rejects foreign accounts before transport and unauthorized management identities before writes", async () => {
    const { client, fetcher } = setup([{ ...admin, account_id: "foreign" }]);
    await expect(client.provision({ ...owner, accountId: "foreign" })).rejects.toThrow("MEMORY_OWNER_MISMATCH");
    expect(fetcher).not.toHaveBeenCalled();
    await expect(client.provision(owner)).rejects.toThrow("MEMORY_MANAGEMENT_IDENTITY_INVALID");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it("does not expose upstream errors or retry an unresolved registration", async () => {
    const { client, fetcher } = setup([admin, ok([]), new Error("synthetic-admin-key"), ok([])]);
    await expect(client.provision(owner)).rejects.toThrow("MEMORY_USER_REGISTRATION_UNRESOLVED");
    expect(fetcher).toHaveBeenCalledTimes(4);
  });
});
