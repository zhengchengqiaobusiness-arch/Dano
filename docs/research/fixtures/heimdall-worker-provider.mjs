// Synthetic Linux acceptance fixture; see ../openviking-validation-status.md.
import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { createWorkerTools as create } from '../dano-server/bridge/heimdall-worker-tools.js';
export async function createWorkerTools({ workspace }) {
  assert.notEqual(process.getuid(), 0);
  assert.match(await readFile('/proc/self/status', 'utf8'), /^NoNewPrivs:\s+1$/m);
  assert.equal(process.env.MEMORY_SYNTHETIC_KEY, undefined);
  const provider = await create({ workspace });
  return { close: () => provider.close(), async execute(name, parameters, signal, update) {
    try { return await provider.execute(name, parameters, signal, update); }
    catch (error) {
      await writeFile(`${workspace}/provider-diagnostic.json`, JSON.stringify({ name, message: error.message }));
      throw error;
    }
  } };
}
