import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuthenticatedUserContext } from "../user-context.js";
import {
  readThemeColorPreference,
  saveThemeColorPreference,
} from "../user-preferences.js";

const temporaryRoots: string[] = [];

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>(done => {
    resolve = done;
  });
  return { promise, resolve };
}

async function userContext(userId: string): Promise<AuthenticatedUserContext> {
  const runtimeRoot = await fs.promises.mkdtemp(
    path.join(os.tmpdir(), "dano-user-preferences-"),
  );
  temporaryRoots.push(runtimeRoot);
  const folderPath = path.join(runtimeRoot, "users", userId);
  await fs.promises.mkdir(folderPath, { recursive: true });
  return {
    user: { id: userId, username: userId },
    folderPath,
  };
}

afterEach(async () => {
  vi.restoreAllMocks();
  await Promise.all(
    temporaryRoots.splice(0).map(root =>
      fs.promises.rm(root, { recursive: true, force: true }),
    ),
  );
});

describe("Theme Color User Preference", () => {
  it("serializes concurrent saves for the same User", async () => {
    const context = await userContext("same-user");
    const firstRenameStarted = deferred();
    const releaseFirstRename = deferred();
    const rename = fs.promises.rename.bind(fs.promises);
    let renameCount = 0;
    vi.spyOn(fs.promises, "rename").mockImplementation(async (...args) => {
      renameCount += 1;
      if (renameCount === 1) {
        firstRenameStarted.resolve();
        await releaseFirstRename.promise;
      }
      return rename(...args);
    });

    const first = saveThemeColorPreference(context, {
      accentColorPreset: "blue",
    });
    await firstRenameStarted.promise;
    const second = saveThemeColorPreference(context, {
      accentColorPreset: "purple",
    });
    const secondCompletedFirst = await Promise.race([
      second.then(() => true),
      new Promise<false>(resolve => setTimeout(() => resolve(false), 30)),
    ]);

    expect(secondCompletedFirst).toBe(false);
    expect(renameCount).toBe(1);
    releaseFirstRename.resolve();
    await Promise.all([first, second]);
    await expect(readThemeColorPreference(context)).resolves.toEqual({
      accentColorPreset: "purple",
    });
  });

  it("does not block saves belonging to different Users", async () => {
    const firstUser = await userContext("first-user");
    const secondUser = await userContext("second-user");
    const firstRenameStarted = deferred();
    const releaseFirstRename = deferred();
    const rename = fs.promises.rename.bind(fs.promises);
    vi.spyOn(fs.promises, "rename").mockImplementation(async (...args) => {
      if (String(args[1]).startsWith(firstUser.folderPath)) {
        firstRenameStarted.resolve();
        await releaseFirstRename.promise;
      }
      return rename(...args);
    });

    const first = saveThemeColorPreference(firstUser, {
      accentColorPreset: "blue",
    });
    await firstRenameStarted.promise;
    await saveThemeColorPreference(secondUser, {
      accentColorPreset: "pink",
    });

    await expect(readThemeColorPreference(secondUser)).resolves.toEqual({
      accentColorPreset: "pink",
    });
    releaseFirstRename.resolve();
    await first;
  });
});
