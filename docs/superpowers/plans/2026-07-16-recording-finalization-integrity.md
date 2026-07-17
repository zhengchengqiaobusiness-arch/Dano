# Recording Finalization Integrity Implementation Plan

> **For Codex:** Execute this plan in the current `playwright_v3` workspace. Preserve the existing UI structure and all unrelated dirty-worktree changes.

**Goal:** Make a real recording finalize into a canonical, executable workbench instead of leaving a preliminary preview with missing identity, ownership, provider, resolver, risk, confirmation, and Pi state.

**Root cause:** For a user action that emits multiple business terminal requests but cannot be proven independently splittable, `plan_capabilities` selects only the last request. A submit POST followed by a refresh GET therefore becomes a read-only query capability; the POST remains unowned, final validation rejects it for missing L3/confirmation policy, no revision is committed, and Pi never starts. The client then keeps displaying the preliminary `meta.preview=true` skeleton after the failure.

**Constraints:** Stay on `playwright_v3`; do not add or rearrange UI controls; do not mutate recorded evidence or user decisions; do not weaken validation; preserve independently splittable capability behavior.

---

## Task 1: Lock the planner regression with failing tests

**Files:**
- Modify: `Playwright/tests/test_inference_graph_planner.py`

1. Change the existing unsplit multi-result expectation so one command owns all of its ordered terminal requests.
2. Add a real-shape regression: submit POST followed by page-refresh GET must produce one capability containing both requests, with submit operation, L3 risk, explicit confirmation, and no unbound business request.
3. Run the two focused tests and confirm they fail against the current planner for the expected reason.

## Task 2: Preserve the complete unsplittable command

**Files:**
- Modify: `Playwright/src/dano_recording/capability_planner.py`
- Test: `Playwright/tests/test_inference_graph_planner.py`

1. For independently splittable terminals, retain the existing one-terminal-per-capability behavior.
2. For an unsplittable action, include every terminal request plus all proven dependencies in one ordered execution chain.
3. Derive the public operation from the last mutating request when the chain ends in a read refresh; derive risk and confirmation from the full chain.
4. Run the focused tests, the complete planner test module, and contract projection tests.

## Task 3: Never present a failed preliminary preview as a completed draft

**Files:**
- Modify: `skillfrontend/tests/workbenchRegression.test.ts`
- Modify: `skillfrontend/src/components/PageRecorder.tsx`

1. Add a failing source-contract regression proving an `analysis_failed` event clears only an uncommitted `meta.preview=true` workbench.
2. In the existing error handler, clear the preview and its check report when finalization/reanalysis fails; preserve any committed or operator-edited workbench.
3. Do not add controls or change layout/styles.
4. Run frontend contract tests and production build.

## Task 4: Prove the full canonical projection on the recorded fixture

**Files:**
- Modify only if a second independently reproduced defect is found in canonical projection/provider/resolver code.

1. Recompile the latest captured fact set read-only through final contract integration.
2. Assert every runtime step and capability has canonical UUID ownership and request refs.
3. Assert wire bindings have runtime providers, sensitive runtime fields have trusted resolvers, mutation requests have L3+ confirmation, and no business request is orphaned.
4. Re-run the finalization/recording application tests and Pi lifecycle tests.

## Task 5: Full verification and review

1. Run all Playwright Python tests relevant to compilation, recording application, persistence, identity, executability, and Pi.
2. Run all frontend tests and `npm run build`.
3. Run launcher/backend regressions without stopping user-owned services.
4. Review the diff for UI changes, validation weakening, secrets, generated artifacts, and unrelated edits.
5. Report exact verification evidence and any remaining advisory that truly requires user/Pi confirmation.
