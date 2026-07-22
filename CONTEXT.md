# Dano

Dano is an enterprise assistant that turns user requests into controlled actions in connected business systems.

## Language

**curl Tool**:
A native network capability for the Dano Agent, which does not expose a shell. It forwards curl arguments and returns the curl process result.
_Avoid_: REST runtime, HTTP client, policy engine

**Dano Bridge**:
The internal HTTP/SSE and RPC subsystem inside the Dano server that connects browser clients to runtime session capabilities. It is a source-module boundary, not an independent workspace package or separate service.
_Avoid_: bridge workspace package, separate bridge service

**Field Assist**:
A transient AI helper for ask_user_question input and editor fields that rewrites or generates the field value without submitting the answer or creating a normal Dano conversation turn. A text field may expose or omit Field Assist independently; exposure is a presentation choice, not an authorization boundary.
_Avoid_: detached session, user prompt, field workflow, preview candidate

**Grouped Form**:
An actionable multi-field question that collects a titled set of related answers in one card. Answering it produces a Submitted Form; it is not itself a Submitted Form while the user can still edit it.
_Avoid_: ordinary single question, Submitted Form before submission, confirmation lifecycle

**Submitted Form**:
A completed, read-only grouped form identified by an opaque `formId` that reuses its source tool-call ID. Submission ends the original interactive question; a later confirmation flow in the same Assistant Turn may reference the form without reopening it. Transcript identity may outlive that Turn, but confirmation eligibility does not.
_Avoid_: pending confirmation, editable form, separate form identifier

**Assistant Turn**:
The server-owned execution that starts from one user message and may contain multiple sequential model responses and tool calls. Submitted Forms are eligible for confirmation only within the Assistant Turn that created them.
_Avoid_: one assistant message, one tool call, browser streaming state

**Activity Trail**:
A durable, chronological account of meaningful work within an Assistant Turn that preserves each tool call as its own row and communicates unresolved setbacks without technical errors in the collapsed row. Multiple safe detail items within one call may be summarized by that row, but separate calls never merge or share expansion state. It remains visible with lower emphasis after the final answer; its summary and normal expanded details may identify work through safe object names, domains, and counts. For Bash source, details may additionally identify executable basenames available from the parsed AST, never directories or arguments. This concise presentation is not an exhaustive execution trace; parsing failure or the absence of an identifiable command uses a generic localized script detail. Normal details never expose tool names, complete paths or URLs, full commands, scripts, code, raw output, or other implementation details. When an unresolved failure has no reliable user-facing explanation, its expanded details may show the original failure information instead of inventing a classification.
_Avoid_: tool log, tool-call list, process summary, technical trace

**Form Interaction**:
The server-owned confirmation lifecycle for one or more Submitted Forms. Its append-only snapshots live in the existing session JSONL and reduce to `awaiting_confirmation`, `revising`, `confirmed`, `cancelled`, or `interrupted`; the browser only renders the projected state, Form Revisions, and allowed actions.
_Avoid_: frontend confirmation state, global streaming state, reconstructed form relationship

**Confirmation Card**:
The browser presentation that summarizes one or more Submitted Forms for confirmation. It may appear before its Form Interaction has been presented, but it exposes actions only from the server-projected Form Interaction and never owns confirmation state itself.
_Avoid_: Form Interaction, automatically transformed Submitted Form, frontend-owned confirmation state

Form Interaction mutations use the projected interaction revision as an optimistic concurrency token. A stale client receives the latest authoritative projection without changing JSONL; the first persisted terminal state is immutable, and safe duplicate revision requests do not append contradictory snapshots.

On process or stored-session recovery, any persisted open Form Interaction is terminalized as `interrupted`; the server never recreates the missing model tool call. Live updates, reconnects, transcript pages, and tree replay all attach state through the same structured-entry projector. Legacy JSONL without structured Form Interaction entries stays read-only and is not migrated or inferred from error text.

**Form Revision**:
The next editable revision of one Submitted Form inside a revising Form Interaction. It preserves the form's `formId`, increments its per-form revision, and starts from the latest complete submitted answer; all revisions in the interaction are submitted as one set before confirmation resumes.
A Form Revision remains the same editable form rather than becoming a distinct card type: it uses the same field presentation as a Grouped Form, while its revision heading, current answers, and revision actions communicate that the form is being modified.
_Avoid_: new form identity, unsaved draft, reopened Submitted Form

**Runtime Workspace**:
The single project folder Dano gives to Pi for one Dano session. Dano may know the owning user and session, but Pi only sees this folder as its current project.
_Avoid_: chat workspace, user workspace, project folder

**User**:
The stable person identity established from a server-verified token. A User is independent of browser clients, sessions, and Runtime Workspaces; none of those identifiers may stand in for a User.
_Avoid_: client, session owner inferred from clientId, anonymous identity

**User Context**:
The server-owned request context produced after verifying a User token. It carries the authenticated User and the safely resolved User Folder; browser-provided identity fields never create or change it.
_Avoid_: client context, browser identity, session context

**User Folder**:
The persistent directory under the Dano runtime users root that is mapped from one verified User ID. It holds user-owned data such as preferences and must remain isolated from other User Folders and Runtime Workspaces.
_Avoid_: Runtime Workspace, session directory, client directory

**Browser Date Value**:
The value submitted by an `ask_user_question` date control after the frontend date component applies its configured format. Dano returns this user answer to the model as submitted and does not normalize it in the Dano Bridge.
_Avoid_: native date value, backend-normalized date, bridge date

**Date Format**:
A required model-provided argument on an `ask_user_question` date field that configures the frontend date control's display and output format.
_Avoid_: model-only metadata, backend date parser, business date conversion

**Uploaded Project File**:
A user-selected file that Dano stores inside the current Runtime Workspace and presents to Pi as a project file path reference. The browser may show the user's original filename, but Pi consumes the workspace-relative path.
_Avoid_: image attachment, temporary upload blob, base64 file payload
