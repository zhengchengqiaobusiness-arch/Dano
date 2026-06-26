/**
 * Network utility functions for the bridge.
 *
 * Provides helpers for enumerating LAN IP addresses from
 * the host's network interfaces.
 */

import * as os from "node:os";

/**
 * Get all LAN-facing IPv4 addresses on this host.
 *
 * Filters out:
 * - Internal/loopback addresses (127.x.x.x)
 * - Link-local addresses (169.254.x.x)
 * - IPv6 addresses
 *
 * @returns Array of IPv4 address strings
 */
export function getLanIps(): string[] {
  const interfaces = os.networkInterfaces();
  const ips: string[] = [];

  for (const entries of Object.values(interfaces)) {
    if (!entries) continue;
    for (const entry of entries) {
      if (entry.family !== "IPv4") continue;
      if (entry.internal) continue;
      const addr = entry.address;
      // Skip link-local (169.254.x.x) and loopback (127.x.x.x — already filtered by internal)
      if (addr.startsWith("169.254.")) continue;
      ips.push(addr);
    }
  }

  return ips;
}

/**
 * Check if an IPv4 address falls within the Tailscale CGNAT range.
 *
 * Tailscale assigns IPs from 100.64.0.0/10 (100.64.0.0 – 100.127.255.255).
 *
 * @param addr IPv4 address string
 * @returns true if the address is in the Tailscale range
 */
export function isTailscaleIp(addr: string): boolean {
  const parts = addr.split(".").map(Number);
  if (parts.length !== 4) return false;
  if (parts.some(isNaN)) return false;
  // 100.64.0.0/10 = 100.01000000.0.0 to 100.01111111.255.255
  return parts[0] === 100 && (parts[1] & 0b11000000) === 0b01000000;
}
