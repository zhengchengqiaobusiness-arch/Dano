import { describe, it, expect } from "vitest";
import { isTailscaleIp, getLanIps } from "../network.js";

describe("isTailscaleIp", () => {
  it("returns true for Tailscale CGNAT range start (100.64.0.1)", () => {
    expect(isTailscaleIp("100.64.0.1")).toBe(true);
  });

  it("returns true for Tailscale CGNAT range end (100.127.255.255)", () => {
    expect(isTailscaleIp("100.127.255.255")).toBe(true);
  });

  it("returns true for middle of Tailscale range (100.100.100.100)", () => {
    expect(isTailscaleIp("100.100.100.100")).toBe(true);
  });

  it("returns false for private 192.168.x.x", () => {
    expect(isTailscaleIp("192.168.1.1")).toBe(false);
  });

  it("returns false for private 10.x.x.x", () => {
    expect(isTailscaleIp("10.0.0.1")).toBe(false);
  });

  it("returns false for 100.0.0.1 (below Tailscale range)", () => {
    expect(isTailscaleIp("100.0.0.1")).toBe(false);
  });

  it("returns false for 100.128.0.1 (above Tailscale range)", () => {
    expect(isTailscaleIp("100.128.0.1")).toBe(false);
  });

  it("returns false for 100.63.255.255 (below Tailscale range)", () => {
    expect(isTailscaleIp("100.63.255.255")).toBe(false);
  });

  it("returns false for localhost", () => {
    expect(isTailscaleIp("127.0.0.1")).toBe(false);
  });

  it("returns false for invalid input (not enough octets)", () => {
    expect(isTailscaleIp("100.64.1")).toBe(false);
  });

  it("returns false for empty string", () => {
    expect(isTailscaleIp("")).toBe(false);
  });

  it("returns false for non-numeric octets", () => {
    expect(isTailscaleIp("100.abc.0.1")).toBe(false);
  });
});

describe("getLanIps", () => {
  it("returns an array", () => {
    const result = getLanIps();
    expect(Array.isArray(result)).toBe(true);
  });

  it("only returns valid IPv4 addresses", () => {
    const result = getLanIps();
    for (const ip of result) {
      expect(ip).toMatch(/^\d+\.\d+\.\d+\.\d+$/);
    }
  });
});
