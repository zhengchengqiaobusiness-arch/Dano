// Real-service acceptance host. All configuration comes from protected files.
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { FileStateStore, OwnerMemoryClient, MemoryDelivery, DeliveryScheduler } from '@josephyoung/pi-openviking/host';
export async function createHost({ paths, assertToolIsolation }) {
  const config = JSON.parse(await readFile(join(paths.agentDir, 'memory-connection.json'), 'utf8'));
  if (!config.owner.accountId.startsWith('extension-test-')) throw new Error('DISPOSABLE_ACCOUNT_REQUIRED');
  const tokenizerRoot = join(paths.installationDir, 'tokenizer');
  const { Tokenizer } = await import('@huggingface/tokenizers');
  const tokenizer = new Tokenizer(JSON.parse(await readFile(join(tokenizerRoot, 'tokenizer.json'))),
    JSON.parse(await readFile(join(tokenizerRoot, 'tokenizer_config.json'))));
  const stateStore = new FileStateStore({ owner: config.owner, directory: paths.stateDir, policyVersion: 'acceptance-v1' });
  const client = new OwnerMemoryClient({ ...config, timeoutMs: 15000 });
  const policy = { maxPayloadBytes: 8192, recallTimeoutMs: 2000, recallTokenBudget: 1000,
    recallLimit: 5, minimumScore: 0.1, countTokens: (text, { model, signal }) => { signal.throwIfAborted(); if (model.provider !== 'cestc' || model.api !== 'openai-completions' || model.id !== 'qwen35') throw new Error('UNSUPPORTED_TOKENIZER_MODEL'); return tokenizer.encode(text, { add_special_tokens: false }).ids.length; } };
  const delivery = new MemoryDelivery({ store: stateStore, transport: client, maxPayloadBytes: policy.maxPayloadBytes });
  const scheduler = new DeliveryScheduler({ store: stateStore, delivery, pollIntervalMs: 500,
    initialBackoffMs: 500, maxBackoffMs: 5000, maxAttemptsPerPhase: 90, maxOperationsPerTick: 5 });
  return { memory: { owner: config.owner, stateStore, client, policy, assertToolIsolation, wakeDelivery: () => scheduler.wake() }, scheduler };
}
