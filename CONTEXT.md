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

**Runtime Workspace**:
The single project folder Dano gives to Pi for one Dano session. Dano may know the owning user and session, but Pi only sees this folder as its current project.
_Avoid_: chat workspace, user workspace, project folder

**Uploaded Project File**:
A user-selected file that Dano stores inside the current Runtime Workspace and presents to Pi as a project file path reference. The browser may show the user's original filename, but Pi consumes the workspace-relative path.
_Avoid_: image attachment, temporary upload blob, base64 file payload
