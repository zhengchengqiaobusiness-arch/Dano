import { expect, it } from "vitest";
import { assertWorkerPrivacyEvidence, procPrivacyGroup } from "../linux-process-privacy.js";

const mount = "23 20 0:22 / /proc rw,nosuid,nodev,noexec - proc proc rw,hidepid=2,gid=1000";
const status = ["Uid:\t10001\t10001\t10001\t10001", "Gid:\t10001\t10001\t10001\t10001",
  "Groups:\t", "NoNewPrivs:\t1", "CapPrm:\t0000000000000000", "CapEff:\t0000000000000000",
  "CapAmb:\t0000000000000000"].join("\n");

it("accepts protected procfs aliases with one explicit host group", () => {
  const alias = mount.replace("/ /proc ", "/ /another-proc ");
  expect(procPrivacyGroup(mount + "\n" + alias)).toBe(1000);
  expect(procPrivacyGroup(mount.replace("hidepid=2", "hidepid=invisible"))).toBe(1000);
  expect(() => assertWorkerPrivacyEvidence(status)).not.toThrow();
});

it("rejects unprotected aliases, missing canonical mounts and inconsistent group exemptions", () => {
  for (const candidate of ["", mount.replace(" /proc ", " /alias "),
    mount.replace("hidepid=2", "hidepid=0"), mount.replace(",gid=1000", ""),
    mount.replace("gid=1000", "gid=0"), mount + ",hidepid=0",
    mount + "\n" + mount.replace(" /proc ", " /alias ").replace("gid=1000", "gid=1001"),
    mount + "\n" + mount.replace(" /proc ", " /alias ").replace("hidepid=2", "hidepid=0")]) {
    expect(() => procPrivacyGroup(candidate)).toThrow("MEMORY_PROCESS_PRIVACY_REQUIRED");
  }
});

it("rejects privileged identities, group membership, capabilities and missing kernel evidence", () => {
  for (const candidate of [status.replace("NoNewPrivs:\t1", "NoNewPrivs:\t0"),
    status.replace("Uid:\t10001\t10001\t10001\t10001", "Uid:\t0\t10001\t0\t10001"),
    status.replace("Groups:\t", "Groups:\t1000"), status.replace("Groups:\t", "Groups:\t10002"),
    status.replace("CapEff:\t0000000000000000", "CapEff:\t0000000000200000"),
    status.replace("CapPrm:\t0000000000000000", "CapPrm:\t0000000000200000"),
    status.replace("CapAmb:\t0000000000000000", "CapAmb:\t0000000000000001"),
    status.replace("Groups:\t\n", ""), ""]) {
    expect(() => assertWorkerPrivacyEvidence(candidate)).toThrow("MEMORY_PROCESS_PRIVACY_REQUIRED");
  }
});
