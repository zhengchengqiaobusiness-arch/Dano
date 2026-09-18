import { WorkerSupervisorClient } from '../../worker-supervisor-client.ts';
const client = new WorkerSupervisorClient({
  get connected() { return Boolean(process.connected); },
  on: process.on.bind(process), off: process.off.bind(process),
  send: (value, callback) => process.send(value, callback),
  disconnect: () => { if (process.connected) process.disconnect(); },
}, { startupTimeoutMs: 3000, operationTimeoutMs: 3000, maxConcurrentOperations: 8, maxMessageBytes: 4096 });
try {
  const profile = await client.profile({ user: { id: 'alice' }, folderPath: '/users/alice' }, { trustedSkillPaths: [] });
  const worker = await profile.resolveWorker('/users/alice/workspaces/default');
  const updates = [];
  const result = await worker.execute('read', { value: 'real-ipc' }, undefined, value => updates.push(value));
  const cancel = new AbortController();
  let cancelled = false;
  try { await worker.execute('bash', { wait: true }, cancel.signal, () => cancel.abort()); }
  catch (error) { cancelled = error.message === 'SUPERVISOR_CANCELLED'; }
  await profile.dispose();
  let staleRejected = false;
  try { await worker.assertIsolated(); } catch { staleRejected = true; }
  console.log(JSON.stringify({ result, updates, cancelled, staleRejected }));
} finally { client.close(); }
