// Real-service acceptance host. All configuration comes from protected files.
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { FileStateStore, OwnerMemoryClient, MemoryDelivery, DeliveryScheduler } from '@josephyoung/pi-openviking/host';
export async function createHost({ paths, assertToolIsolation }) {
  const config = JSON.parse(await readFile(join(paths.agentDir, 'memory-connection.json'), 'utf8'));
  if (!config.owner.accountId.startsWith('extension-test-')) throw new Error('DISPOSABLE_ACCOUNT_REQUIRED');
  const { Tokenizer } = await import('@huggingface/tokenizers');
  const tokenizer = new Tokenizer(JSON.parse(await readFile(config.tokenizer.path)),
    JSON.parse(await readFile(config.tokenizer.configPath)));
  const stateStore = new FileStateStore({ owner: config.owner, directory: paths.stateDir, policyVersion: 'acceptance-v1' });
  const client = new OwnerMemoryClient({ owner: config.owner, baseUrl: config.baseUrl,
    apiKey: config.apiKey, timeoutMs: 15000 });
  const policy = { maxPayloadBytes: 8192, recallTimeoutMs: 2000, recallTokenBudget: 1500,
    recallLimit: 5, minimumScore: 0.1, countTokens: (text, { model, signal }) => { signal.throwIfAborted(); if (model.provider !== config.model.provider || model.api !== config.model.api || model.id !== config.model.id) throw new Error('UNSUPPORTED_TOKENIZER_MODEL'); return tokenizer.encode(text, { add_special_tokens: false }).ids.length; } };
  const delivery = new MemoryDelivery({ store: stateStore, transport: client, maxPayloadBytes: policy.maxPayloadBytes });
  const scheduler = new DeliveryScheduler({ store: stateStore, delivery, pollIntervalMs: 500,
    initialBackoffMs: 500, maxBackoffMs: 5000, maxAttemptsPerPhase: 90, maxOperationsPerTick: 5 });
  return { memory: { owner: config.owner, stateStore, client, policy, assertToolIsolation, wakeDelivery: () => scheduler.wake() }, scheduler };
}
