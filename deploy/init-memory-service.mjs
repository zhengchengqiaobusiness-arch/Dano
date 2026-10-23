#!/usr/bin/env node
import { constants } from 'node:fs';
import { open, lstat, realpath, readFile, readdir, link, unlink } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { randomUUID } from 'node:crypto';

const fail = () => new Error('MEMORY_CONFIG_INITIALIZATION_FAILED');
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
function merge(base, input) {
  if (!object(input)) throw fail();
  const result = { ...base };
  for (const [key, value] of Object.entries(input)) {
    if (!Object.hasOwn(base, key)) throw fail();
    result[key] = object(base[key]) && object(value) ? merge(base[key], value) : value;
  }
  return result;
}
async function finishPublication(path, file) {
  let stat = await file.stat();
  if (stat.nlink !== 2) return;
  const directory = dirname(path);
  for (const name of await readdir(directory)) {
    if (!/^\.memory-service\.[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.tmp$/.test(name)) continue;
    const temporary = join(directory, name);
    const candidate = await lstat(temporary).catch(error => { if (error.code !== 'ENOENT') throw error; });
    // Recover only this publisher's second name for the very same private inode.
    if (candidate?.isFile() && candidate.dev === stat.dev && candidate.ino === stat.ino) {
      await unlink(temporary).catch(error => { if (error.code !== 'ENOENT') throw error; });
      break;
    }
  }
  stat = await file.stat();
  if (stat.nlink !== 1) throw fail();
}
async function privateJson(path, recoverPublication = false) {
  const file = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    let stat = await file.stat();
    if (!stat.isFile() || stat.uid !== process.getuid() || (stat.mode & 0o077) || stat.size > 1048576) throw fail();
    if (recoverPublication) { await finishPublication(path, file); stat = await file.stat(); }
    if (stat.nlink !== 1) throw fail();
    return JSON.parse(await file.readFile('utf8'));
  } finally { await file.close(); }
}

/** Initializes only a missing file. Existing settings and credentials are never merged or rewritten. */
export async function initializeMemoryService(directory, validate) {
  try {
    if (await realpath(directory) !== resolve(directory)) throw fail();
    const stat = await lstat(directory);
    if (!stat.isDirectory() || stat.uid !== process.getuid() || (stat.mode & 0o077)) throw fail();
    const target = join(directory, 'memory-service.json');
    try {
      const existing = await privateJson(target, true);
      validate(existing);
      return 'MEMORY_CONFIG_PRESERVED';
    } catch (error) { if (error.code !== 'ENOENT') throw error; }
    const defaults = JSON.parse(await readFile(new URL('./memory-service.defaults.json', import.meta.url), 'utf8'));
    const input = await privateJson(join(directory, 'memory-service.inputs.json'));
    const config = merge(defaults, input);
    validate(config); // Missing credentials/assets fail before publishing anything.
    const temporary = join(directory, `.memory-service.${randomUUID()}.tmp`);
    const file = await open(temporary, 'wx', 0o600);
    try {
      try {
        await file.writeFile(JSON.stringify(config, null, 2) + '\n');
        await file.sync();
      } finally { await file.close(); }
      // Atomic no-replace publication: a concurrent initializer cannot overwrite a winner.
      await link(temporary, target);
    } catch (error) {
      if (error.code !== 'EEXIST') throw error;
      validate(await privateJson(target, true));
      return 'MEMORY_CONFIG_PRESERVED';
    } finally {
      await unlink(temporary).catch(error => { if (error.code !== 'ENOENT') throw error; });
    }
    return 'MEMORY_CONFIG_INITIALIZED';
  } catch { throw fail(); }
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  try {
    if (process.argv.length !== 3) throw fail();
    const { parseMemoryHostConfig } = await import('../dist/server/bridge/memory-host-config.js');
    console.log(await initializeMemoryService(process.argv[2], parseMemoryHostConfig));
  } catch { console.error('MEMORY_CONFIG_INITIALIZATION_FAILED'); process.exitCode = 1; }
}
