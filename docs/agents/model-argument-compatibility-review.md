# Model argument compatibility review checklist

Use this checklist whenever a model-facing collection- or object-shaped
parameter is added or changed. Canonical schemas and prompt guidance describe
what models should emit; the runtime compatibility boundary and executable
evidence determine what Dano can safely accept.

## Required automated evidence

- [ ] Add or update a sanitized captured model-deviation fixture and its
  canonical equivalent. Do not include secrets, user answers, or unavailable
  identifiers from a real session.
- [ ] Exercise the canonical collection or object and every supported safe JSON
  string encoding, including harmless surrounding whitespace.
- [ ] Exercise supported aliases independently and together with the canonical
  field, preserving deterministic precedence, order, and stable deduplication.
- [ ] Cover malformed or ambiguous values, empty and non-string items, duplicate
  items, unknown targets, partial-valid input, all-invalid input, and fallback.
- [ ] Cross boolean-compatible control flags with native and JSON-string target
  collections when the parameters participate in the same operation.
- [ ] Assert external behavior: selected targets and order, ignored-reason
  classes, whether fallback was attempted, terminal result, and the canonical
  Bridge projection consumed by the browser.
- [ ] Prove isolation and leakage boundaries, including unavailable and
  cross-Assistant-Turn targets. Raw compatibility-only values must not cross the
  browser protocol or appear in errors with sensitive identifiers or answers.
- [ ] Run the focused compatibility matrix, the relevant Coordinator and
  RPC/JSONL tests, the full test suite, checks, and the complete build.

For `ask_user_question` request fields, the executable sources are:

- `apps/dano/src/bridge/__tests__/fixtures/ask-user-question-request-model-deviations.json`
- `apps/dano/src/bridge/__tests__/ask-user-question-request-compatibility.test.ts`

For `ask_user_question` confirmation targets, the executable sources are:

- `apps/dano/src/bridge/__tests__/fixtures/ask-user-question-model-deviations.json`
- `apps/dano/src/bridge/__tests__/ask-user-question-confirmation-compatibility.test.ts`
- the Coordinator and RPC/JSONL regressions in the neighboring test files

## Reviewer judgement

The following decisions cannot be proved by a checklist alone:

- [ ] Is the proposed recovery unambiguous, or could the same input reasonably
  name a different target, field, or answer?
- [ ] Is parsing intentionally bounded? Nested or recursive decoding needs an
  explicit product requirement and its own ambiguity analysis.
- [ ] Does partial recovery avoid incorrect submission, field mapping, data
  loss, or confirmation of a different operation?
- [ ] Does fallback preserve the user's likely intent after every explicit
  target is filtered, rather than hiding a materially wrong target selection?
- [ ] Is compatibility normalized once at the server boundary, with no duplicate
  raw-argument parsing in the browser, RPC adapter, or persistence projector?
- [ ] Does the change preserve the canonical protocol, domain lifecycle, and
  security boundary even though runtime admission becomes broader?

Record the judgement and its rationale in the originating issue or pull request.
