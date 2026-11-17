# Baseline browser smoke

Run after machine deployment releases its lock. Use the Codex in-app Browser at
the profile's existing service URL and reuse the existing OA client, TLS and
browser tab. This baseline is part of the deployment workflow; full feature
acceptance remains optional.

1. Bind the run to the machine receipt's target SHA, image ID and asset list.
   Check the loaded page's JS/CSS references against that list. Preserve drafts,
   theme and existing sessions; use a new chat for synthetic prompts.
2. Send a short, unique, non-sensitive arithmetic or factual prompt through the
   visible composer. Require an actual nonempty correct assistant answer,
   generation completion and a usable composer. Record elapsed time and capture
   evidence. Do not require a particular model wording or language for this gate.
3. Exercise the visible login action and existing OA authorization/callback flow.
   Require return to Dano and the menu's authenticated state. An already logged-in
   menu alone is not login-flow evidence. Preserve an existing authenticated
   session; use an additional in-app tab/session when available, otherwise ask
   before logging the user out. Never clear shared browser storage to force login.
   User credentials, MFA, CAPTCHA or browser security interstitials may require
   user input; keep that step pending and do not claim login passed.
4. In a new logged-in chat, send another short synthetic prompt. Require a
   correct nonempty answer and normal completion, then reload the page and check
   authenticated state is retained. Reuse the current configured model; tools,
   images, business submissions and memory writes are outside this baseline.
5. Save a separate browser-smoke result with target SHA, image ID, URL, login/chat
   outcomes, timestamps, screenshots and any concrete blocker. Record `passed`
   only when every baseline check passed; otherwise record `incomplete` or
   `failed`. Report it alongside the machine receipt, whose pending field is the
   original handoff state. Keep credentials and cookies out of evidence.

Recover a browser-control interruption on the same in-app surface. A browser or
provider blocker does not undo successful machine deployment. Diagnose a proven
product failure and handle repair/rollback as a separate operation.
