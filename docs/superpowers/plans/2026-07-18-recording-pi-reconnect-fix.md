# Recording Pi Reconnect Stability Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make “生成/优化能力” reliably produce and restore capability content across recorder WebSocket replacement without exposing Pi Session ownership or half-started-session errors.

**Architecture:** Add a per-recording gateway connection lease so a replacement handler cancels and fully drains the previous handler before incrementing the resume generation. Start Pi candidates transactionally and cache them only after success. Preserve an accepted Pi submission during handler cancellation, and keep the frontend operation pending across unintentional reconnects so an interrupted plan can resume or retry with the same operation id.

**Tech Stack:** Python 3.11+, FastAPI WebSocket, asyncio, pytest/pytest-asyncio, React 18, TypeScript, Vite.

## Global Constraints

- Keep one Pi sidecar per live recorder WebSocket handler; do not create a cross-process resident Pi service.
- Do not change capability prompts, FlowSpec semantics, or release review rules.
- Do not add unbounded retry loops.
- Preserve operation-id idempotency and the server-authoritative resume draft.
- Do not expose raw Session ownership errors to the capability button during a normal reconnect handoff.

---

### Task 1: Recording connection lease and deterministic handoff

**Files:**
- Modify: `back/dano/gateway/app.py:59-125,746-790,1769-1785`
- Test: `back/tests/test_gateway_record_ws.py`

**Interfaces:**
- Produces: `_RecordingConnectionLease(task: asyncio.Task, released: asyncio.Event)`.
- Produces: `async _claim_recording_connection(key: tuple[str, str, str]) -> _RecordingConnectionLease`.
- Produces: `_release_recording_connection(key, lease) -> None`.
- Consumes: the existing `(tenant, subsystem, recording_id)` resume key.

- [ ] **Step 1: Write failing lease handoff tests**

Add an async test that starts an old owner task, claims the same key from a replacement task, and asserts the old task receives cancellation, runs its `finally`, and sets `released` before the replacement claim returns. Add a second test proving release removes only the matching lease and cannot delete a newer owner.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python -m pytest back/tests/test_gateway_record_ws.py -k "recording_connection_lease" -vv`

Expected: FAIL because `_claim_recording_connection` and `_release_recording_connection` do not exist.

- [ ] **Step 3: Implement the minimal lease registry**

Add a bounded module-level registry keyed by resume key. `_claim_recording_connection` creates the replacement lease, atomically installs it before awaiting, cancels the prior task when it is a different live task, and waits for `prior.released`. `_release_recording_connection` always sets the lease event and removes the registry entry only when the entry still points to that lease.

- [ ] **Step 4: Integrate the lease into `record_ws`**

Claim immediately after validating the start frame and building `resume_key`, before reading or incrementing `connection_generation`. In `finally`, close Pi/recorder/sender first, then release the lease in an outer cleanup path so every return, disconnect, cancellation, and exception wakes a replacement connection.

- [ ] **Step 5: Verify GREEN**

Run: `python -m pytest back/tests/test_gateway_record_ws.py -k "recording_connection_lease or started_action" -vv`

Expected: all selected tests PASS.

### Task 2: Transactional Pi startup and accepted-submission salvage

**Files:**
- Modify: `back/dano/gateway/app.py:864-876,1360-1406,1769-1785`
- Test: `back/tests/test_gateway_record_ws.py`
- Test: `back/tests/test_recording_pi_client.py`

**Interfaces:**
- Produces: `async _start_recording_pi_candidate(factory: Callable[[], RecordingPiSession]) -> RecordingPiSession`.
- Preserves: `recording_pi` remains `None` unless `candidate.start()` completed successfully.
- Consumes: `RecordingPiSession.last_submission_kind` and `current_flow_spec()`.

- [ ] **Step 1: Write failing transactional-start test**

Use two fake candidates. The first raises an ownership error from `start()` and records `close()`. The second starts successfully. Assert the helper closes the first, raises its original error, and a second call returns the new healthy candidate rather than the closed first object.

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest back/tests/test_gateway_record_ws.py -k "recording_pi_candidate" -vv`

Expected: FAIL because `_start_recording_pi_candidate` does not exist.

- [ ] **Step 3: Implement transactional startup and use it from `_ensure_recording_pi`**

Create a candidate locally, await `start()`, close it on every failed or cancelled start, and return it only after success. Assign the returned object to the handler cache after the await. Keep all ordinary Pi errors unchanged.

- [ ] **Step 4: Write failing cancellation-salvage test**

Model a handler whose Pi tool submission has set `last_submission_kind="plan"` and advanced `current_flow_spec()` while the local `pending_flow_spec` is still the prior version. Assert the cleanup helper chooses the Pi-owned newer FlowSpec only for `plan` or `repair`, and never replaces a newer local FlowSpec with an older candidate.

- [ ] **Step 5: Implement and verify submission salvage**

Before the old owner checkpoints its final resume snapshot, compare `meta.current_version` values. If Pi has an accepted plan/repair with a strictly newer version, checkpoint that version. Then run:

`python -m pytest back/tests/test_gateway_record_ws.py -k "recording_pi_candidate or accepted_submission" -vv`

Expected: all selected tests PASS.

- [ ] **Step 6: Verify existing Pi exclusivity behavior**

Run: `python -m pytest back/tests/test_recording_pi_client.py -vv`

Expected: all tests PASS, including rejection of genuinely concurrent independent scope use.

### Task 3: Frontend reconnect continuity for an active capability operation

**Files:**
- Modify: `skillfrontend/src/components/PageRecorder.tsx:1328-1370,1666-1925,2425-2445`

**Interfaces:**
- Extends: `flowOperationRef.current` with the existing mode, previous timestamp, and operation id; no new server protocol field.
- Produces: `resumeFlowOperationAfterReconnect(flowSpec: FlowSpecData | null) -> void`.
- Consumes: the existing `orchestrate_flow` / `auto_fix_flow` messages and operation-id replay behavior.

- [ ] **Step 1: Preserve the active operation on an unintentional close**

Do not call `clearFlowOperation()` from `onclose` when the close is unintentional. Keep the busy state and watchdog; intentional stop/unmount still clears it.

- [ ] **Step 2: Resume or finish after server draft restoration**

After a reconnect accepts `resumedServerSpec`, call `finishFlowOperation`. If the restored agent timestamp has not advanced, resend the original operation with the same `operation_id`. When reconnect requires `flow_replace`, defer the resend until that restore response is acknowledged. Guard with the existing ref so only one resend occurs per connection.

- [ ] **Step 3: Build the frontend**

Run: `npm run build` from `skillfrontend`.

Expected: TypeScript and Vite build exit 0.

### Task 4: End-to-end regression verification

**Files:**
- Verify: `back/dano/gateway/app.py`
- Verify: `back/dano/onboarding/recording_pi.py`
- Verify: `skillfrontend/src/components/PageRecorder.tsx`
- Verify: `back/tests/test_gateway_record_ws.py`
- Verify: `back/tests/test_recording_pi_client.py`

**Interfaces:**
- Consumes all behavior from Tasks 1-3.
- Produces no new public API.

- [ ] **Step 1: Run focused backend regression tests**

Run: `python -m pytest back/tests/test_gateway_record_ws.py back/tests/test_recording_pi_client.py -vv`

Expected: all tests PASS with zero failures.

- [ ] **Step 2: Run broader recording regression tests**

Run: `python -m pytest back/tests/test_recording_pi_agent_tools.py back/tests/test_recording_v2_scenarios.py -q`

Expected: all tests PASS with zero failures.

- [ ] **Step 3: Run the frontend production build again**

Run: `npm run build` from `skillfrontend`.

Expected: exit 0.

- [ ] **Step 4: Review the final diff**

Run: `git diff --check` and `git diff -- back/dano/gateway/app.py back/tests/test_gateway_record_ws.py back/tests/test_recording_pi_client.py skillfrontend/src/components/PageRecorder.tsx`.

Expected: no whitespace errors; changes stay limited to connection ownership, Pi startup/salvage, frontend reconnect continuity, and their regression tests.
