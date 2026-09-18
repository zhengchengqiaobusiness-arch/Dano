import { describe, expect, it, vi } from "vitest";
import { MemoryIdentityService } from "../memory-identity-service.js";

const context = { user: { id: "stable-user", username: "display" }, folderPath: "/unused" };
const owner = Object.freeze({ accountId: "company", userId: "mapped-user" });
function setup(savedKey?: string) {
  const options = {
    owners: { get: vi.fn(async () => owner) },
    credentials: { read: vi.fn(async () => savedKey), write: vi.fn(async () => {}) },
    provisioner: { provision: vi.fn(async () => "issued-key"), verifyUserKey: vi.fn(async () => {}) },
    assertToolIsolation: vi.fn(async () => {}),
  };
  return { options, service: new MemoryIdentityService(options) };
}

describe("memory identity startup", () => {
  it("coalesces concurrent viewer initialization and persists before returning a connection", async () => {
    const { options, service } = setup();
    const results = await Promise.all(Array.from({ length: 8 }, () => service.connect(context)));
    expect(options.provisioner.provision).toHaveBeenCalledTimes(1);
    expect(options.credentials.write).toHaveBeenCalledExactlyOnceWith(owner, "issued-key");
    expect(results.every(result => result === results[0])).toBe(true);
    expect(Object.isFrozen(results[0])).toBe(true);
  });
  it("revalidates a persisted USER credential without using management credentials", async () => {
    const { options, service } = setup("saved-key");
    expect(await service.connect(context)).toEqual({ owner, apiKey: "saved-key" });
    expect(options.provisioner.verifyUserKey).toHaveBeenCalledExactlyOnceWith(owner, "saved-key");
    expect(options.provisioner.provision).not.toHaveBeenCalled();
    expect(options.credentials.write).not.toHaveBeenCalled();
  });
  it("refuses anonymous and unisolated callers before loading credentials", async () => {
    const { options, service } = setup();
    await expect(service.connect({ user: { id: "anonymous" }, folderPath: "/unused" })).rejects.toThrow("MEMORY_LOGIN_REQUIRED");
    options.assertToolIsolation.mockRejectedValue(new Error("worker stopped"));
    await expect(service.connect(context)).rejects.toThrow("worker stopped");
    expect(options.owners.get).not.toHaveBeenCalled();
    expect(options.credentials.read).not.toHaveBeenCalled();
    expect(options.provisioner.provision).not.toHaveBeenCalled();
  });
  it("does not fall back to management or rotate after USER authentication fails", async () => {
    const { options, service } = setup("saved-key");
    options.provisioner.verifyUserKey.mockRejectedValue(new Error("invalid USER credential"));
    await expect(service.connect(context)).rejects.toThrow("invalid USER credential");
    expect(options.provisioner.provision).not.toHaveBeenCalled();
    options.provisioner.verifyUserKey.mockResolvedValue(undefined);
    expect((await service.connect(context)).apiKey).toBe("saved-key");
  });
  it("withholds credentials if durable persistence fails and allows a subsequent recovery", async () => {
    const { options, service } = setup();
    options.credentials.write.mockRejectedValueOnce(new Error("storage unavailable"));
    await expect(service.connect(context)).rejects.toThrow("storage unavailable");
    expect((await service.connect(context)).apiKey).toBe("issued-key");
    expect(options.credentials.write).toHaveBeenCalledTimes(2);
  });
  it("withholds a prepared connection if isolation disappears during startup", async () => {
    const { options, service } = setup("saved-key");
    options.assertToolIsolation.mockResolvedValueOnce(undefined).mockRejectedValueOnce(new Error("worker stopped"));
    await expect(service.connect(context)).rejects.toThrow("worker stopped");
  });
});
