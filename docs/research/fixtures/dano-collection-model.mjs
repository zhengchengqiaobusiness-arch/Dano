// Run with Node 22. No browser/OpenViking delivery claim.
// Args: private models.json, private credential JSON, provider, model id.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
const require = createRequire(new URL('../../../apps/dano/package.json', import.meta.url));
const { createJiti } = require('jiti');
const { memoryCollectionModel } = await createJiti(import.meta.url).import(
  new URL('../../../apps/dano/src/bridge/memory-collection-model.ts', import.meta.url).href);
async function loadPackage(name, entry = '.') {
  const base = new URL('../../../apps/dano/node_modules/' + name + '/', import.meta.url);
  const manifest = JSON.parse(await readFile(new URL('package.json', base), 'utf8'));
  return import(new URL(manifest.exports[entry].import, base));
}
const { ModelRuntime } = await loadPackage('@earendil-works/pi-coding-agent');
const { collectionSelectionPrompt } = await loadPackage('@josephyoung/pi-openviking', './host');
const [modelsPath, credentialsPath, provider, modelId] = process.argv.slice(2);
assert(modelsPath && credentialsPath && provider && modelId);
const configured = JSON.parse(await readFile(modelsPath, 'utf8'));
const credentials = JSON.parse(await readFile(credentialsPath, 'utf8'));
const reference = configured.providers[provider].apiKey;
assert(typeof reference === 'string' && /^\$[A-Z][A-Z0-9_]*$/.test(reference));
const key = credentials[reference.slice(1)]; assert(typeof key === 'string' && key.length > 0);
process.env[reference.slice(1)] = key;
const root = await mkdtemp('/private/tmp/dano475-model-binding-');
const runtime = await ModelRuntime.create({ authPath: join(root, 'auth.json'), modelsPath,
  refreshOnCreate: false, allowModelNetwork: false });
const configuration = { model: { provider, id: modelId, maxTokens: 2048, temperature: 0, thinking: 'disabled' },
  selector: { maxInputBytes: 8192, maxFacts: 5, timeoutMs: 45000 } };
const model = runtime.getModel(provider, modelId); assert(model);
let calls = 0, usage;
const selector = memoryCollectionModel(configuration, async () => ({
  getModel: runtime.getModel.bind(runtime), getAuth: runtime.getAuth.bind(runtime),
  async completeSimple(model, context, options) {
    assert(!context.tools); assert(!JSON.stringify(context).includes(key)); calls++;
    const result = await runtime.completeSimple(model, context, options);
    usage = { input: result.usage.input, output: result.usage.output, totalTokens: result.usage.totalTokens };
    return result;
  },
}), ['SYNTHETIC_MANAGEMENT_SECRET']);
const signal = AbortSignal.timeout(45000);
assert((await selector.sensitiveValues(signal)).includes(key));
const fact = '我以后写报告固定使用简体中文，并且喜欢短句。';
const start = performance.now();
const result = JSON.parse(await selector.complete({ systemPrompt: collectionSelectionPrompt,
  data: JSON.stringify({ messages: [{ sourceId: 'm0', role: 'user', text: fact }] }), signal }));
assert(result.facts.length > 0);
assert(result.facts.every(value => value.sourceId === 'm0' && fact.includes(value.quote)));
const report = { provider, model: modelId, realModel: true, hostAdapter: true, calls,
  credentialSnapshotMatched: true, toolsAbsent: true, sourceQuotesValid: true,
  elapsedMs: Math.round(performance.now() - start), usage, browserVerified: false, remoteMemoryDeliveryTested: false };
await writeFile(join(root, 'result.json'), JSON.stringify(report, null, 2), { mode: 0o600 });
console.log(JSON.stringify({ root, ...report }));
