# Host memory token counting

## Implemented boundary

`MemoryTokenizers` loads an administrator-provided binding from the exact
`provider`, `api` and model `id` to local tokenizer/config files and their SHA-256
hashes. It uses `@huggingface/tokenizers@0.2.0`, pinned in the Dano dependency lock.
No runtime download, model-name inference or character-count fallback occurs.
The tokenizer API follows the [official Tokenizers.js documentation](https://github.com/huggingface/tokenizers.js).

Each configured model has a worker thread. Counting does not block the HTTP
host event loop. Each worker accepts one outstanding request; saturation rejects
recall rather than building a queue. Cancellation rejects the caller promptly
while retaining the occupied slot until the worker replies. Input byte limits,
asset byte limits and startup deadlines are required configuration. Pending
counts retain the process; idle workers do not. Closing rejects pending work
and waits for worker termination.

The worker checks regular files, final-component symlinks and expected hashes
before constructing the tokenizer. Only token counts return to the caller.
Text and token arrays are not logged or persisted by this service. Empty model
registries and unknown models have no implicit fallback.

## Executed evidence — 2026-09-18

- Server type check and `build:server` succeeded.
- Real-worker tests cover model binding, hashes, symlinks, asset and input limits,
  concurrent saturation, cancellation, idempotent shutdown and standalone
  process liveness.
- The built `dist/server/bridge/memory-tokenizer.js` loaded these existing fixed
  [DeepSeek-V4-Flash tokenizer assets](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash/tree/60d8d70770c6776ff598c94bb586a859a38244f1):
  - `tokenizer.json`: `8f9f37ca37fdc4f5fd36d5cf4d3b0e8392edb4e894fd10cc0d70b4957c8633cf`
  - `tokenizer_config.json`: `6ac8c8dc065ed118161d02dd532749ae3f52c243deac27872134fae2f50d8547`
- All 24 multilingual, Unicode, whitespace and repeated-text inputs matched
  counts freshly computed by Rust `tokenizers@0.23.2`, with special tokens off.
- An initial built-process check exited with unsettled top-level await because
  the worker was unreferenced during counting. Pending requests now reference
  the worker; response/cancellation returns it to unreferenced state. The real
  built-process rerun passed and a child-process regression covers this case.

## Outstanding release gates

The protected host now starts this module from private configuration and closes
the shared workers after user-runtime disposal completes.
The extension's model-aware asynchronous callback is now published in `0.1.1`
and Dano pins that exact registry artifact. The real model's tokenizer binding,
recall behavior and deployment acceptance remain required before production use.

Local tokenizer parity proves the specified files' counts. A provider alias or
display name does not prove which model/tokenizer a remote proxy actually uses.
Deployment must explicitly bind a verified model contract, and the full recall
budget/latency/cost evaluation remains outstanding. These checks do not satisfy
#465 AC-13 or the real-browser memory acceptance gate.
