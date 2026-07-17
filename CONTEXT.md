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
A transient AI helper for ask_user_question input and editor fields that rewrites or generates the field value without submitting the answer or creating a normal Dano conversation turn.
_Avoid_: detached session, user prompt, field workflow, preview candidate

**Submitted Form**:
A completed, read-only grouped form identified by an opaque `formId` that reuses its source tool-call ID. Submission ends the original interactive question; a later confirmation flow in the same Assistant Turn may reference the form without reopening it. Transcript identity may outlive that Turn, but confirmation eligibility does not.
_Avoid_: pending confirmation, editable form, separate form identifier

**Assistant Turn**:
The server-owned execution that starts from one user message and may contain multiple sequential model responses and tool calls. Submitted Forms are eligible for confirmation only within the Assistant Turn that created them.
_Avoid_: one assistant message, one tool call, browser streaming state

**Form Interaction**:
The server-owned confirmation lifecycle for one or more Submitted Forms. Its append-only snapshots live in the existing session JSONL and reduce to `awaiting_confirmation`, `revising`, `confirmed`, `cancelled`, or `interrupted`; the browser only renders the projected state, Form Revisions, and allowed actions.
_Avoid_: frontend confirmation state, global streaming state, reconstructed form relationship

**Form Revision**:
The next editable revision of one Submitted Form inside a revising Form Interaction. It preserves the form's `formId`, increments its per-form revision, and starts from the latest complete submitted answer; all revisions in the interaction are submitted as one set before confirmation resumes.
_Avoid_: new form identity, unsaved draft, reopened Submitted Form

**Runtime Workspace**:
The single project folder Dano gives to Pi for one Dano session. Dano may know the owning user and session, but Pi only sees this folder as its current project.
_Avoid_: chat workspace, user workspace, project folder

**Browser Date Value**:
The value submitted by an `ask_user_question` date control after the frontend date component applies its configured format. Dano returns this user answer to the model as submitted and does not normalize it in the Dano Bridge.
_Avoid_: native date value, backend-normalized date, bridge date

**Date Format**:
A required model-provided argument on an `ask_user_question` date field that configures the frontend date control's display and output format.
_Avoid_: model-only metadata, backend date parser, business date conversion

**Uploaded Project File**:
A user-selected file that Dano stores inside the current Runtime Workspace and presents to Pi as a project file path reference. The browser may show the user's original filename, but Pi consumes the workspace-relative path.
_Avoid_: image attachment, temporary upload blob, base64 file payload
