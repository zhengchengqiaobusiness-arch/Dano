# Feature acceptance after deployment

Start only when the user explicitly requests acceptance, including an affirmative
answer to the post-deployment question. Deployment has already completed and its
lock is released. Default is no acceptance.

Read the current PR/issue and select only acceptance steps relevant to this change.
Do not automatically add unrelated model, bash, upload, OA or memory journeys.
Use the Codex in-app Browser at the existing service URL for requested UI flows.
Preserve drafts, theme, sessions, TLS, OA and runtime data. Verify loaded assets
match the deployment receipt and capture evidence for the actual changed flow.
When the change affects tools/uploads/auth/memory, exercise the corresponding
real tool, upload/identity, or memory ready/recall flow; API smoke does not replace
that explicitly requested product acceptance.

Record a separate acceptance result tied to target SHA and image ID. If browser
control fails, recover the same in-app surface and report the exact unfinished
check. Do not reopen deployment as incomplete or hold its lock. Cancellation or
silence never rolls back an otherwise healthy deployed service. A proven product
regression warrants a separately recorded fix/rollback, preserving evidence.
