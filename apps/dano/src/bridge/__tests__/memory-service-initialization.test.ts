import { afterEach, expect, it } from 'vitest';
import { chmod, mkdtemp, readFile, realpath, rm, stat, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { initializeMemoryService } from '../../../../../deploy/init-memory-service.mjs';
import { parseMemoryHostConfig } from '../memory-host-config.js';
const roots: string[] = [];
afterEach(async () => { await Promise.all(roots.splice(0).map(path => rm(path, { recursive: true, force: true }))); });
const inputs = () => ({ accountId: 'synthetic-account', managementKey: 'synthetic-management', encryptionKey: 'ab'.repeat(32),
  encryptionKeyVersion: 'v1', memoryModel: {
    tokenizer: { path: '/etc/dano-memory/tokenizer.json', sha256: 'a'.repeat(64) },
    config: { path: '/etc/dano-memory/tokenizer_config.json', sha256: 'b'.repeat(64) },
    extraction: { apiBase: 'https://model.example.test/v1', apiKey: 'synthetic-provider-key' },
  } });
async function setup(input: unknown = inputs()) {
  const dir = await realpath(await mkdtemp(join(tmpdir(), 'memory-init-'))); roots.push(dir);
  await writeFile(join(dir, 'memory-service.inputs.json'), JSON.stringify(input), { mode: 0o600 });
  return dir;
}
it('initializes a complete version-2 config from the sole defaults and private inputs', async () => {
  const dir = await setup();
  expect(await initializeMemoryService(dir, parseMemoryHostConfig)).toBe('MEMORY_CONFIG_INITIALIZED');
  const path = join(dir, 'memory-service.json'), raw = JSON.parse(await readFile(path, 'utf8'));
  expect(raw.policy).toEqual({ maxPayloadBytes: 10240, recallTimeoutMs: 10000, recallTokenBudget: 15000, recallLimit: 10, minimumScore: 0.5 });
  expect(raw.memoryModel.id).toBe('mimo-v2.6-flash');
  expect(raw).not.toHaveProperty('tokenizers');
  expect(parseMemoryHostConfig(raw).collection?.model.id).toBe(raw.memoryModel.id);
  expect((await stat(path)).mode & 0o777).toBe(0o600);
});
it('preserves existing settings, credentials, bytes and inode without reading bootstrap inputs', async () => {
  const dir = await setup(); await initializeMemoryService(dir, parseMemoryHostConfig);
  const path = join(dir, 'memory-service.json'), config = JSON.parse(await readFile(path, 'utf8'));
  config.policy.recallLimit = 2; config.policy.recallTokenBudget = 3333;
  const bytes = JSON.stringify(config) + '\n\n'; await writeFile(path, bytes);
  await rm(join(dir, 'memory-service.inputs.json'));
  const before = await stat(path);
  expect(await initializeMemoryService(dir, parseMemoryHostConfig)).toBe('MEMORY_CONFIG_PRESERVED');
  expect(await readFile(path, 'utf8')).toBe(bytes);
  expect((await stat(path)).ino).toBe(before.ino);
});
it('fails without publishing when required inputs or schema are invalid, without leaking values', async () => {
  for (const input of [{}, { ...inputs(), accidentalSecret: 'do-not-print-me' }]) {
    const dir = await setup(input);
    await expect(initializeMemoryService(dir, parseMemoryHostConfig)).rejects.toThrow(/^MEMORY_CONFIG_INITIALIZATION_FAILED$/);
    await expect(stat(join(dir, 'memory-service.json'))).rejects.toMatchObject({ code: 'ENOENT' });
  }
});
it('does not repair or overwrite corrupt existing files', async () => {
  const dir = await setup(), path = join(dir, 'memory-service.json');
  await writeFile(path, 'corrupt-private-config', { mode: 0o600 });
  await expect(initializeMemoryService(dir, parseMemoryHostConfig)).rejects.toThrow('MEMORY_CONFIG_INITIALIZATION_FAILED');
  expect(await readFile(path, 'utf8')).toBe('corrupt-private-config');
});
it('rejects unsafe bootstrap permissions and symlink destinations', async () => {
  const dir = await setup(), seed = join(dir, 'memory-service.inputs.json');
  await chmod(seed, 0o644);
  await expect(initializeMemoryService(dir, parseMemoryHostConfig)).rejects.toThrow('MEMORY_CONFIG_INITIALIZATION_FAILED');
  await chmod(seed, 0o600);
  await symlink(seed, join(dir, 'memory-service.json'));
  await expect(initializeMemoryService(dir, parseMemoryHostConfig)).rejects.toThrow('MEMORY_CONFIG_INITIALIZATION_FAILED');
  expect(JSON.parse(await readFile(seed, 'utf8'))).toEqual(inputs());
});
it('allows explicit deployment overrides only for initialization', async () => {
  const dir = await setup({ ...inputs(), policy: { recallTimeoutMs: 35000 } });
  await initializeMemoryService(dir, parseMemoryHostConfig);
  const raw = JSON.parse(await readFile(join(dir, 'memory-service.json'), 'utf8'));
  expect(raw.policy.recallTimeoutMs).toBe(35000); expect(raw.policy.recallLimit).toBe(10);
});
