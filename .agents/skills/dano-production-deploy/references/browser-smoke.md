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

When navigation, refresh or chat fails, check your Browser Use operation first:
verify the current browser/tab binding, inspect fresh state, use the documented
reload operation, then reopen the existing service URL in a fresh in-app tab.
Check that a previous failed navigation or stale handle is not preventing the
next action. Record the exact call, visible result and reproducibility before
opening a service investigation. A tool error alone does not establish a product
or server failure.

Investigate our own implementation and deployment only when the observations
support that boundary: browser workflow, frontend initialization/reconnect,
nginx/TLS, OAuth callback and session restoration. Tie each diagnostic to a
specific observed failure and change one variable at a time. Treat an unproven
cause as unknown; do not attribute failure to filing, providers or outside
networks from a connection error alone. Establish a proven cause, apply the
supported repair, then rerun the original failed flow and affected baseline checks.
A transient browser/network error or an `incomplete` record is a checkpoint, not
an endpoint. Continue unaffected work during any user-only step. Ask for needed
input only when a concrete access, credential or approval boundary prevents further
progress; name that boundary and the required input. Preserve TLS, authentication,
existing data and adjacent services throughout recovery. Machine success remains
recorded separately; a timer or browser-control error never triggers rollback.
Handle a confirmed product regression with a separately recorded repair/rollback.
